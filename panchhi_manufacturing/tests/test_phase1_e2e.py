"""Phase 1 — LIVE end-to-end on real masters (FRD sheet 3, compressed).

One style, two variants, one in-house operation, no BOM:
  1. raw material received into WIP
  2. multi-variant Work Order (BOM-less) submitted → Job Card created
     with the variant plan stamped on it
  3. variant completed quantities entered, Job Card submitted
  4. → auto-posted Manufacture Stock Entry consumes raw from WIP and
     receives BOTH variant outputs into stock (THE KEY BUILD, C-04)
  5. → per-variant produced_qty rolls up; Work Order completes (C-06)

Creates its own PMTEST-* masters with a unique run suffix each run.
"""
import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate


class TestPhase1EndToEnd(IntegrationTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.run_id = frappe.generate_hash(length=6).upper()
		cls.company = "Panchhi Fashion"
		cls.abbr = frappe.db.get_value("Company", cls.company, "abbr")

		frappe.db.set_single_value(
			"Manufacturing Settings", "allow_editing_of_items_and_quantities_in_work_order", 1
		)

		root_wh = frappe.db.get_value(
			"Warehouse", {"company": cls.company, "is_group": 1, "parent_warehouse": ""}, "name"
		) or frappe.db.get_value("Warehouse", {"company": cls.company, "is_group": 1}, "name")
		cls.wip_wh = cls._make_warehouse("PMTEST-WIP", root_wh)
		cls.fg_wh = cls._make_warehouse("PMTEST-FG", root_wh)

		cls.style = cls._make_item(f"PMTEST-STYLE-{cls.run_id}")
		cls.pink = cls._make_item(f"PMTEST-FG-PINK-{cls.run_id}")
		cls.white = cls._make_item(f"PMTEST-FG-WHITE-{cls.run_id}")
		cls.raw = cls._make_item(f"PMTEST-RAW-{cls.run_id}")

		if not frappe.db.exists("Operation", "PMTEST Cutting"):
			frappe.get_doc({"doctype": "Operation", "name": "PMTEST Cutting"}).insert(
				ignore_permissions=True
			)
		cls.workstation = frappe.db.get_value("Workstation", {}, "name")

		# Raw stock into WIP so the auto-SE has something to consume.
		receipt = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Receipt",
				"stock_entry_type": "Material Receipt",
				"company": cls.company,
				"items": [
					{
						"item_code": cls.raw,
						"qty": 500,
						"t_warehouse": cls.wip_wh,
						"basic_rate": 10,
					}
				],
			}
		)
		receipt.insert(ignore_permissions=True)
		receipt.submit()

	@classmethod
	def _make_warehouse(cls, wh_name, parent):
		full = f"{wh_name} - {cls.abbr}"
		if not frappe.db.exists("Warehouse", full):
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": wh_name,
					"company": cls.company,
					"parent_warehouse": parent,
				}
			).insert(ignore_permissions=True)
		return full

	@classmethod
	def _hsn_code(cls):
		if not getattr(cls, "_hsn", None):
			cls._hsn = frappe.db.get_value("GST HSN Code", {}, "name")
			if not cls._hsn:
				frappe.get_doc(
					{"doctype": "GST HSN Code", "hsn_code": "61091000"}
				).insert(ignore_permissions=True)
				cls._hsn = "61091000"
		return cls._hsn

	@classmethod
	def _make_item(cls, code):
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
					"gst_hsn_code": cls._hsn_code(),
				}
			).insert(ignore_permissions=True)
		return code

	def test_full_flow(self):
		# ---- 2. multi-variant BOM-less Work Order -----------------------
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": self.company,
				"production_item": self.style,
				"custom_is_multi_variant": 1,
				"custom_variants": [
					{"item_code": self.pink, "qty": 10},
					{"item_code": self.white, "qty": 8},
				],
				"qty": 1,  # overwritten by the variant roll-up
				"fg_warehouse": self.fg_wh,
				"wip_warehouse": self.wip_wh,
				"skip_transfer": 1,
				"transfer_material_against": "Work Order",
				"planned_start_date": nowdate(),
				"operations": [
					{
						"operation": "PMTEST Cutting",
						"workstation": self.workstation,
						"time_in_mins": 60,
						"status": "Pending",
					}
				],
				"required_items": [
					{
						"item_code": self.raw,
						"required_qty": 18,
						"operation": "PMTEST Cutting",
						"source_warehouse": self.wip_wh,
					}
				],
			}
		)
		wo.insert(ignore_permissions=True)
		self.assertEqual(wo.qty, 18)  # Σ variants
		wo.submit()

		# ---- Job Card created with the variant plan stamped -------------
		jc_names = frappe.get_all(
			"Job Card", filters={"work_order": wo.name, "docstatus": 0}, pluck="name"
		)
		self.assertEqual(len(jc_names), 1)
		jc = frappe.get_doc("Job Card", jc_names[0])
		self.assertTrue(jc.custom_is_multi_variant)
		self.assertEqual(
			{d.item_code: d.planned_qty for d in jc.custom_items},
			{self.pink: 10, self.white: 8},
		)

		# ---- 3. complete per-variant and submit -------------------------
		for d in jc.custom_items:
			d.completed_qty = d.planned_qty
		jc.time_logs = []
		jc.append(
			"time_logs",
			{
				"from_time": frappe.utils.now_datetime(),
				"to_time": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=60),
				"time_in_mins": 60,
				"completed_qty": 18,
			},
		)
		jc.save(ignore_permissions=True)
		self.assertEqual(jc.total_completed_qty, 18)  # standard field ← roll-up
		jc.submit()

		# ---- 4. THE KEY BUILD: auto-posted SFG receipt ------------------
		jc.reload()
		self.assertTrue(jc.custom_sfg_stock_entry, "C-04 SE was not auto-posted")
		se = frappe.get_doc("Stock Entry", jc.custom_sfg_stock_entry)
		self.assertEqual(se.docstatus, 1)
		fg_rows = {d.item_code: d for d in se.items if d.is_finished_item}
		self.assertEqual(set(fg_rows), {self.pink, self.white})
		self.assertEqual(flt(fg_rows[self.pink].qty), 10)
		self.assertEqual(flt(fg_rows[self.white].qty), 8)
		consumed = [d for d in se.items if not d.is_finished_item]
		self.assertTrue(consumed and consumed[0].item_code == self.raw)
		self.assertEqual(flt(consumed[0].qty), 18)

		# stock actually landed
		from erpnext.stock.utils import get_stock_balance

		self.assertEqual(get_stock_balance(self.pink, fg_rows[self.pink].t_warehouse), 10)
		self.assertEqual(get_stock_balance(self.white, fg_rows[self.white].t_warehouse), 8)

		# ---- 5. per-variant produced qty + WO completion ----------------
		wo.reload()
		produced = {d.item_code: flt(d.produced_qty) for d in wo.custom_variants}
		self.assertEqual(produced, {self.pink: 10, self.white: 8})
		self.assertEqual(flt(wo.produced_qty), 18)
		self.assertEqual(wo.status, "Completed")

		# ---- cancel unwinds ---------------------------------------------
		jc.cancel()
		wo.reload()
		self.assertEqual(flt(wo.produced_qty), 0)
		self.assertNotEqual(wo.status, "Completed")
