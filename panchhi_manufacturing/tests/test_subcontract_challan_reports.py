"""C-05 subcontracting + C-08 Rule 45 challan + C-10 reports, END TO END.

Everything here failed the "run the actual flow" bar that unit tests miss.
The 2026-07-24 audit drove this exact chain in a console and it surfaced
SIX real bugs no existing test caught, because no test had a subcontracted
operation OR rendered a document:

  1. OperationSequenceError — a subcontracted op (no Job Card) permanently
     blocked every downstream in-house Job Card.
  2. "Job Card not found for the operation" — the SFG-receipt Stock Entry
     demanded a Job Card for the subcontracted op too.
  3. make_subcontracting_order didn't check is_sub_contracted_item, so the
     user hit ERPNext's terse "must be a subcontracted item".
  4. validate_items demanded a Purchase Order Item link on a PO-less SCO.
  5. calculate_supplied_items_qty_and_amount loaded a BOM that doesn't exist.
  6. supplier_warehouse defaulted to the WIP warehouse → "Reserve Warehouse
     must differ from Supplier Warehouse", plus missing mandatory fields.

This module seeds ONE realistic scenario — a subcontracted Stitching op
(with a trim input) then an in-house Finishing op that produces the
variants — and asserts the whole chain: SCO creation, dispatch, challan,
Style Progress, Actual Cost by Style.
"""
import re

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from panchhi_manufacturing.overrides.subcontracting_order import make_subcontracting_order


class TestSubcontractChallanReports(IntegrationTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.run_id = frappe.generate_hash(length=6).upper()
		cls.company = "Panchhi Fashion"
		cls.abbr = frappe.db.get_value("Company", cls.company, "abbr")
		hsn = frappe.db.get_value("GST HSN Code", {}, "name")

		frappe.db.set_single_value(
			"Manufacturing Settings",
			"allow_editing_of_items_and_quantities_in_work_order",
			1,
		)
		frappe.db.set_single_value(
			"Buying Settings",
			"backflush_raw_materials_of_subcontract_based_on",
			"Material Transferred for Subcontract",
		)

		root = frappe.db.get_value(
			"Warehouse",
			{"company": cls.company, "is_group": 1, "parent_warehouse": ""},
			"name",
		) or frappe.db.get_value("Warehouse", {"company": cls.company, "is_group": 1}, "name")

		def wh(name):
			full = f"{name}-{cls.run_id} - {cls.abbr}"
			if not frappe.db.exists("Warehouse", full):
				frappe.get_doc(
					{
						"doctype": "Warehouse",
						"warehouse_name": f"{name}-{cls.run_id}",
						"company": cls.company,
						"parent_warehouse": root,
					}
				).insert(ignore_permissions=True)
			return full

		cls.wip = wh("PMSC-WIP")
		cls.fg = wh("PMSC-FG")
		cls.sup = wh("PMSC-SUP")  # job worker's warehouse — distinct from WIP

		def item(code, **kw):
			if not frappe.db.exists("Item", code):
				frappe.get_doc(
					{
						"doctype": "Item",
						"item_code": code,
						"item_name": code,
						"item_group": "All Item Groups",
						"stock_uom": "Nos",
						"is_stock_item": 1,
						"valuation_rate": 10,
						"gst_hsn_code": hsn,
						**kw,
					}
				).insert(ignore_permissions=True)
			return code

		cls.style = item(f"PMSC-STYLE-{cls.run_id}")
		cls.pink = item(f"PMSC-PINK-{cls.run_id}")
		cls.raw = item(f"PMSC-RAW-{cls.run_id}")
		cls.trim = item(f"PMSC-TRIM-{cls.run_id}")
		cls.sfg = item(f"PMSC-SFG-{cls.run_id}", is_sub_contracted_item=1)
		cls.sfg_plain = item(f"PMSC-SFG-PLAIN-{cls.run_id}")  # NOT subcontract-enabled
		cls.service = item(f"PMSC-SVC-{cls.run_id}", is_stock_item=0)

		cls.supplier = frappe.db.get_value("Supplier", {}, "name")
		cls.ws = frappe.db.get_value("Workstation", {}, "name")
		for op in (f"PMSC Stitch {cls.run_id}", f"PMSC Finish {cls.run_id}"):
			if not frappe.db.exists("Operation", op):
				frappe.get_doc({"doctype": "Operation", "name": op}).insert(ignore_permissions=True)
		cls.op_stitch = f"PMSC Stitch {cls.run_id}"
		cls.op_finish = f"PMSC Finish {cls.run_id}"

		rec = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Receipt",
				"stock_entry_type": "Material Receipt",
				"company": cls.company,
				"items": [
					{"item_code": cls.raw, "qty": 100, "t_warehouse": cls.wip, "basic_rate": 10},
					{"item_code": cls.trim, "qty": 100, "t_warehouse": cls.wip, "basic_rate": 3},
				],
			}
		)
		rec.insert(ignore_permissions=True)
		rec.submit()

		cls.wo = cls._build_submitted_wo()

	@classmethod
	def _build_submitted_wo(cls):
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": cls.company,
				"production_item": cls.style,
				"custom_is_multi_variant": 1,
				"custom_variants": [{"item_code": cls.pink, "qty": 5}],
				"qty": 1,
				"fg_warehouse": cls.fg,
				"wip_warehouse": cls.wip,
				"skip_transfer": 1,
				"transfer_material_against": "Work Order",
				"planned_start_date": nowdate(),
				"operations": [
					{
						"operation": cls.op_stitch,
						"workstation": cls.ws,
						"time_in_mins": 30,
						"is_subcontracted": 1,
						"finished_good": cls.sfg,
						"wip_warehouse": cls.wip,
						"fg_warehouse": cls.wip,
					},
					{"operation": cls.op_finish, "workstation": cls.ws, "time_in_mins": 40},
				],
				"required_items": [
					{
						"item_code": cls.trim,
						"required_qty": 5,
						"operation": cls.op_stitch,
						"source_warehouse": cls.wip,
					},
					{
						"item_code": cls.raw,
						"required_qty": 5,
						"operation": cls.op_finish,
						"source_warehouse": cls.wip,
					},
				],
			}
		)
		wo.insert(ignore_permissions=True)
		wo.submit()
		return wo.name

	# ------------------------------------------------------------------
	# The in-house Job Card must not be blocked by the subcontracted op
	# (bugs 1 & 2 — sequence + ops-completed)
	# ------------------------------------------------------------------
	def test_inhouse_job_card_completes_despite_upstream_subcontract(self):
		jc_name = frappe.db.get_value(
			"Job Card", {"work_order": self.wo, "operation": self.op_finish}, "name"
		)
		self.assertTrue(jc_name, "in-house Finishing Job Card must be created")
		jc = frappe.get_doc("Job Card", jc_name)
		# No Job Card for the subcontracted stitch op.
		self.assertFalse(
			frappe.db.exists("Job Card", {"work_order": self.wo, "operation": self.op_stitch}),
			"subcontracted operation must NOT get a Job Card",
		)
		for d in jc.custom_items:
			d.completed_qty = d.planned_qty
		jc.time_logs = []
		jc.append(
			"time_logs",
			{
				"from_time": frappe.utils.now_datetime(),
				"to_time": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=40),
				"time_in_mins": 40,
				"completed_qty": 5,
			},
		)
		jc.save(ignore_permissions=True)
		jc.submit()  # would OperationSequenceError before the fix
		jc.reload()
		self.assertEqual(jc.status, "Completed")
		# And the SFG-receipt Stock Entry posted (would throw "Job Card not
		# found for the operation <subcontracted op>" before the fix).
		self.assertTrue(jc.custom_sfg_stock_entry, "SFG receipt must post")
		se = frappe.get_doc("Stock Entry", jc.custom_sfg_stock_entry)
		self.assertEqual(se.docstatus, 1)
		fg = {d.item_code: flt(d.qty) for d in se.items if d.is_finished_item}
		self.assertEqual(fg.get(self.pink), 5.0)

	# ------------------------------------------------------------------
	# C-05 — WO-driven SCO: no PO, supplied items from the WO operation
	# ------------------------------------------------------------------
	def test_wo_driven_sco_creates_submits_and_dispatches(self):
		wo = frappe.get_doc("Work Order", self.wo)
		op = next(o for o in wo.operations if o.operation == self.op_stitch)
		sco_name = make_subcontracting_order(
			work_order=wo.name,
			operation_row=op.name,
			service_item=self.service,
			supplier=self.supplier,
			service_rate=4,
			supplier_warehouse=self.sup,
		)
		sco = frappe.get_doc("Subcontracting Order", sco_name)
		self.assertFalse(sco.get("purchase_order"), "WO-driven SCO carries no PO")
		self.assertTrue(sco.custom_work_order)
		# supplied items come from the WO operation's inputs (the trim), NOT a BOM
		self.assertEqual([s.rm_item_code for s in sco.supplied_items], [self.trim])
		self.assertEqual(flt(sco.supplied_items[0].required_qty), 5.0)

		sco.submit()  # touches PO-based paths — must survive with no PO
		self.assertEqual(sco.docstatus, 1)

		# Dispatch the raw materials to the job worker → the SE the Rule 45
		# challan accompanies.
		from erpnext.controllers.subcontracting_controller import make_rm_stock_entry

		disp = frappe.get_doc(make_rm_stock_entry(subcontract_order=sco.name))
		disp.insert(ignore_permissions=True)
		disp.submit()
		self.assertEqual(disp.purpose, "Send to Subcontractor")
		self.assertEqual(disp.supplier, self.supplier)
		disp_items = {d.item_code: d for d in disp.items}
		self.assertIn(self.trim, disp_items)
		self.assertEqual(disp_items[self.trim].t_warehouse, self.sup)

		# C-08 — the challan must name the job worker, cite the SCO, and
		# list ONLY the dispatched trim.
		html = frappe.get_print(
			"Stock Entry", disp.name, print_format="Rule 45 Job Work Challan", no_letterhead=1
		)
		text = re.sub(r"<style.*?</style>", " ", html, flags=re.DOTALL)
		text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
		self.assertIn("Rule 45", text)
		self.assertIn("Section 143", text)
		self.assertIn(self.supplier, text, "Job Worker must be named")
		self.assertIn(sco.name, text, "challan must cite the SCO")
		self.assertIn(self.trim, text, "dispatched trim must be listed")
		self.assertNotIn(self.pink, text, "finished variant must NOT be on the challan")

	# ------------------------------------------------------------------
	# make_subcontracting_order guards (bugs 3 & 6)
	# ------------------------------------------------------------------
	def test_sco_rejects_non_subcontract_enabled_sfg(self):
		"""A finished good without is_sub_contracted_item gets a clear
		message, not ERPNext's terse one."""
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": self.company,
				"production_item": self.style,
				"custom_is_multi_variant": 1,
				"custom_variants": [{"item_code": self.pink, "qty": 2}],
				"qty": 1,
				"fg_warehouse": self.fg,
				"wip_warehouse": self.wip,
				"skip_transfer": 1,
				"transfer_material_against": "Work Order",
				"planned_start_date": nowdate(),
				"operations": [
					{
						"operation": self.op_stitch,
						"workstation": self.ws,
						"time_in_mins": 10,
						"is_subcontracted": 1,
						"finished_good": self.sfg_plain,  # NOT subcontract-enabled
						"wip_warehouse": self.wip,
						"fg_warehouse": self.wip,
					},
					{"operation": self.op_finish, "workstation": self.ws, "time_in_mins": 10},
				],
			}
		)
		wo.insert(ignore_permissions=True)
		wo.submit()
		op = next(o for o in wo.operations if o.operation == self.op_stitch)
		with self.assertRaises(frappe.ValidationError) as cm:
			make_subcontracting_order(
				work_order=wo.name,
				operation_row=op.name,
				service_item=self.service,
				supplier=self.supplier,
				supplier_warehouse=self.sup,
			)
		self.assertIn("Sub-contracted Item", str(cm.exception))

	def test_sco_rejects_supplier_warehouse_equal_to_wip(self):
		"""supplier_warehouse must differ from the WIP reserve warehouse."""
		wo = frappe.get_doc("Work Order", self.wo)
		op = next(o for o in wo.operations if o.operation == self.op_stitch)
		with self.assertRaises(frappe.ValidationError) as cm:
			make_subcontracting_order(
				work_order=wo.name,
				operation_row=op.name,
				service_item=self.service,
				supplier=self.supplier,
				supplier_warehouse=self.wip,  # same as reserve → must be rejected
			)
		self.assertIn("Supplier", str(cm.exception))

	# ------------------------------------------------------------------
	# C-10 reports — seeded so an EMPTY result fails (the contract-test trap)
	# ------------------------------------------------------------------
	def test_reports_show_real_seeded_production(self):
		from panchhi_manufacturing.panchhi_manufacturing.report.style_progress import (
			style_progress,
		)
		from panchhi_manufacturing.panchhi_manufacturing.report.actual_cost_by_style import (
			actual_cost_by_style,
		)

		# Ensure production exists (this test may run before the JC test).
		jc_name = frappe.db.get_value(
			"Job Card", {"work_order": self.wo, "operation": self.op_finish}, "name"
		)
		jc = frappe.get_doc("Job Card", jc_name)
		if jc.docstatus == 0:
			for d in jc.custom_items:
				d.completed_qty = d.planned_qty
			jc.time_logs = []
			jc.append(
				"time_logs",
				{
					"from_time": frappe.utils.now_datetime(),
					"to_time": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=40),
					"time_in_mins": 40,
					"completed_qty": 5,
				},
			)
			jc.save(ignore_permissions=True)
			jc.submit()

		_, sp_rows = style_progress.execute({"work_order": self.wo, "include_completed": 1})
		ops = {r["operation"]: r for r in sp_rows}
		self.assertIn(self.op_stitch, ops)
		self.assertEqual(ops[self.op_stitch]["mode"], "Subcontract")
		self.assertIn(self.op_finish, ops)
		self.assertEqual(ops[self.op_finish]["mode"], "In-house")
		self.assertTrue(
			ops[self.op_finish]["sfg_receipt"], "in-house op must show its SFG receipt"
		)

		_, ac_rows = actual_cost_by_style.execute({"work_order": self.wo})
		by_variant = {r["variant"]: r for r in ac_rows}
		self.assertIn(self.pink, by_variant)
		self.assertGreater(
			flt(by_variant[self.pink]["absorbed_cost"]),
			0,
			"Actual Cost by Style must show non-zero absorbed cost for produced variant",
		)
