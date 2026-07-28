"""BOM-driven grouped multi-variant Work Order (2026-07-28 client report).

Panchhi maintains a full default BOM per colour/size variant (operations +
materials) instead of a Style Recipe. A grouped multi-variant Work Order must
therefore fetch its route from those BOMs, NOT come out empty:

  1. `fetch_production_details` populates the grouped WO from the variants'
     default BOMs — merged required items + the shared operation route.
  2. The WO (several SFG-less, in-house operations) SUBMITS — the SFG-per-
     operation rule applies only to the operation-as-transaction model.
  3. One Job Card per operation is created.
  4. Completing an INTERMEDIATE operation's Job Card posts NO Stock Entry
     (labour only) — the anti-double-production guard.
  5. Completing the LAST operation's Job Card posts exactly ONE Manufacture
     Stock Entry that receives each variant ONCE (not N× the operations) and
     consumes the merged materials once.

Creates its own PMBOM-* masters with a unique run suffix each run.
"""
import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate


class TestBomDrivenWorkOrder(IntegrationTestCase):

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
		cls.wip_wh = cls._make_warehouse("PMBOM-WIP", root_wh)
		cls.fg_wh = cls._make_warehouse("PMBOM-FG", root_wh)

		cls.style = cls._make_item(f"PMBOM-STYLE-{cls.run_id}")
		cls.pink = cls._make_item(f"PMBOM-PINK-{cls.run_id}")
		cls.white = cls._make_item(f"PMBOM-WHITE-{cls.run_id}")
		cls.raw = cls._make_item(f"PMBOM-RAW-{cls.run_id}")

		cls.op1 = cls._make_operation("PMBOM Cutting")
		cls.op2 = cls._make_operation("PMBOM Stitching")
		cls.workstation = frappe.db.get_value("Workstation", {}, "name")

		# A default BOM per variant: two in-house operations, one raw item at
		# 1 unit per finished piece — SFG-less (a plain BOM, no per-op SFG).
		cls.bom_pink = cls._make_bom(cls.pink)
		cls.bom_white = cls._make_bom(cls.white)

		# Raw stock so the final Manufacture entry has something to consume.
		receipt = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Receipt",
				"stock_entry_type": "Material Receipt",
				"company": cls.company,
				"items": [
					{"item_code": cls.raw, "qty": 500, "t_warehouse": cls.wip_wh, "basic_rate": 10}
				],
			}
		)
		receipt.insert(ignore_permissions=True)
		receipt.submit()

	# ------------------------------------------------------------------ seed
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
				frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": "61091000"}).insert(
					ignore_permissions=True
				)
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

	@classmethod
	def _make_operation(cls, name):
		if not frappe.db.exists("Operation", name):
			frappe.get_doc({"doctype": "Operation", "name": name}).insert(ignore_permissions=True)
		return name

	@classmethod
	def _make_bom(cls, fg_item):
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": fg_item,
				"company": cls.company,
				"quantity": 1,
				"with_operations": 1,
				"is_active": 1,
				"is_default": 1,
				"items": [{"item_code": cls.raw, "qty": 1, "source_warehouse": cls.wip_wh}],
				"operations": [
					{"operation": cls.op1, "workstation": cls.workstation, "time_in_mins": 5},
					{"operation": cls.op2, "workstation": cls.workstation, "time_in_mins": 5},
				],
			}
		)
		bom.insert(ignore_permissions=True)
		bom.submit()
		return bom.name

	# ------------------------------------------------------------------ test
	def _draft_grouped_wo(self):
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
				"source_warehouse": self.wip_wh,
				"skip_transfer": 1,
				"transfer_material_against": "Work Order",
				"planned_start_date": nowdate(),
			}
		)
		wo.insert(ignore_permissions=True)
		return wo

	def test_fetch_from_variant_boms_populates_route(self):
		"""Step 1 — the reported bug: empty grouped WO gets its operations +
		merged required items from the variants' default BOMs."""
		from panchhi_manufacturing.overrides.work_order import fetch_production_details

		wo = self._draft_grouped_wo()
		self.assertEqual(len(wo.operations), 0)
		self.assertEqual(len(wo.required_items), 0)

		fetch_production_details(wo.name)
		wo.reload()

		# two shared operations, SFG-less + in-house
		self.assertEqual([o.operation for o in wo.operations], [self.op1, self.op2])
		self.assertTrue(all(not o.finished_good and not o.is_subcontracted for o in wo.operations))

		# raw merged across both variant BOMs: 10 + 8 = 18
		req = {r.item_code: flt(r.required_qty) for r in wo.required_items}
		self.assertEqual(req, {self.raw: 18})

	def test_bom_driven_flow_produces_each_variant_once(self):
		"""Steps 2-5 — submit, one Job Card per op, intermediate op posts no
		SE, final op posts exactly one SE producing each variant once."""
		from panchhi_manufacturing.overrides.work_order import fetch_production_details

		wo = self._draft_grouped_wo()
		fetch_production_details(wo.name)
		wo.reload()
		self.assertEqual(wo.qty, 18)  # Σ variants
		wo.submit()  # SFG-per-op rule relaxed for a pure BOM route

		# ---- one Job Card per operation --------------------------------
		jc_names = frappe.get_all(
			"Job Card",
			filters={"work_order": wo.name, "docstatus": 0},
			order_by="sequence_id, creation",
			pluck="name",
		)
		self.assertEqual(len(jc_names), 2)
		jcs = [frappe.get_doc("Job Card", n) for n in jc_names]
		jcs.sort(key=lambda j: (j.get("sequence_id") or 0))
		first_jc, last_jc = jcs[0], jcs[-1]

		def _complete(jc):
			for d in jc.custom_items:
				d.completed_qty = d.planned_qty
			jc.time_logs = []
			jc.append(
				"time_logs",
				{
					"from_time": frappe.utils.now_datetime(),
					"to_time": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=30),
					"time_in_mins": 30,
					"completed_qty": 18,
				},
			)
			jc.save(ignore_permissions=True)
			jc.submit()
			jc.reload()

		# ---- intermediate operation: labour only, NO stock entry --------
		_complete(first_jc)
		self.assertFalse(
			first_jc.get("custom_sfg_stock_entry"),
			"Intermediate BOM operation must NOT post a Manufacture entry",
		)

		# ---- final operation: exactly ONE Manufacture SE ----------------
		_complete(last_jc)
		self.assertTrue(last_jc.get("custom_sfg_stock_entry"), "Final op did not post the SE")

		ses = frappe.get_all(
			"Stock Entry",
			filters={"work_order": wo.name, "purpose": "Manufacture", "docstatus": 1},
			pluck="name",
		)
		self.assertEqual(len(ses), 1, "Expected exactly ONE Manufacture Stock Entry")

		se = frappe.get_doc("Stock Entry", ses[0])
		fg_rows = {d.item_code: flt(d.qty) for d in se.items if d.is_finished_item}
		# each variant produced ONCE — 10 & 8, NOT 20 & 16
		self.assertEqual(fg_rows, {self.pink: 10, self.white: 8})
		consumed = {d.item_code: flt(d.qty) for d in se.items if not d.is_finished_item}
		self.assertEqual(consumed, {self.raw: 18})

		# ---- per-variant produced qty + WO completion -------------------
		wo.reload()
		produced = {d.item_code: flt(d.produced_qty) for d in wo.custom_variants}
		self.assertEqual(produced, {self.pink: 10, self.white: 8})
		self.assertEqual(flt(wo.produced_qty), 18)
		self.assertEqual(wo.status, "Completed")
