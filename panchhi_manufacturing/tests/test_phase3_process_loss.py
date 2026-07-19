"""Phase 3 / C-12 — rejects roll into STANDARD process loss + costing.

Live flow with rejects: variant A completed 8 / rejected 2, variant B
completed 8 (plan 10 + 8 = 18):
  - JC standard total_completed_qty = 16, standard process_loss_qty = 2
    (native set_process_loss derives it — no fork needed)
  - JC submits cleanly (completed + loss == for_quantity)
  - auto-SE consumes material for the PROCESSED 18 (rejects consumed
    fabric too) but receives only the 16 good pieces — the loss cost is
    absorbed into good-unit valuation
  - WO variant produced: A=8 (not 10), B=8; WO stays In Process
"""
import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate


class TestPhase3ProcessLoss(IntegrationTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.run_id = frappe.generate_hash(length=6).upper()
		cls.company = "Panchhi Fashion"
		cls.abbr = frappe.db.get_value("Company", cls.company, "abbr")
		root_wh = frappe.db.get_value(
			"Warehouse", {"company": cls.company, "is_group": 1, "parent_warehouse": ""}, "name"
		) or frappe.db.get_value("Warehouse", {"company": cls.company, "is_group": 1}, "name")
		cls.wip_wh = f"PMTEST-WIP - {cls.abbr}"
		cls.fg_wh = f"PMTEST-FG - {cls.abbr}"
		for wh_name, full in (("PMTEST-WIP", cls.wip_wh), ("PMTEST-FG", cls.fg_wh)):
			if not frappe.db.exists("Warehouse", full):
				frappe.get_doc(
					{
						"doctype": "Warehouse",
						"warehouse_name": wh_name,
						"company": cls.company,
						"parent_warehouse": root_wh,
					}
				).insert(ignore_permissions=True)
		hsn = frappe.db.get_value("GST HSN Code", {}, "name")
		cls.items = {}
		for key in ("STYLE", "A", "B", "RAW"):
			code = f"PM3-{key}-{cls.run_id}"
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
				}
			).insert(ignore_permissions=True)
			cls.items[key] = code

		if not frappe.db.exists("Operation", "PMTEST Cutting"):
			frappe.get_doc({"doctype": "Operation", "name": "PMTEST Cutting"}).insert(
				ignore_permissions=True
			)

		receipt = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Receipt",
				"stock_entry_type": "Material Receipt",
				"company": cls.company,
				"items": [
					{"item_code": cls.items["RAW"], "qty": 100, "t_warehouse": cls.wip_wh, "basic_rate": 10}
				],
			}
		)
		receipt.insert(ignore_permissions=True)
		receipt.submit()

	def test_rejects_flow(self):
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": self.company,
				"production_item": self.items["STYLE"],
				"custom_is_multi_variant": 1,
				"custom_variants": [
					{"item_code": self.items["A"], "qty": 10},
					{"item_code": self.items["B"], "qty": 8},
				],
				"qty": 1,
				"fg_warehouse": self.fg_wh,
				"wip_warehouse": self.wip_wh,
				"skip_transfer": 1,
				"transfer_material_against": "Work Order",
				"planned_start_date": nowdate(),
				"operations": [
					{
						"operation": "PMTEST Cutting",
						"workstation": frappe.db.get_value("Workstation", {}, "name"),
						"time_in_mins": 60,
						"status": "Pending",
					}
				],
				"required_items": [
					{
						"item_code": self.items["RAW"],
						"required_qty": 18,
						"operation": "PMTEST Cutting",
						"source_warehouse": self.wip_wh,
					}
				],
			}
		)
		wo.insert(ignore_permissions=True)
		wo.submit()

		jc = frappe.get_doc(
			"Job Card",
			frappe.get_all("Job Card", filters={"work_order": wo.name, "docstatus": 0}, pluck="name")[0],
		)
		for d in jc.custom_items:
			if d.item_code == self.items["A"]:
				d.completed_qty, d.rejected_qty = 8, 2
			else:
				d.completed_qty = 8
		jc.append(
			"time_logs",
			{
				"from_time": frappe.utils.now_datetime(),
				"to_time": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=30),
				"time_in_mins": 30,
				"completed_qty": 16,
			},
		)
		jc.save(ignore_permissions=True)

		# C-12: standard fields carry the roll-up + native process loss.
		self.assertEqual(flt(jc.total_completed_qty), 16)
		self.assertEqual(flt(jc.process_loss_qty), 2)

		jc.submit()
		jc.reload()
		self.assertTrue(jc.custom_sfg_stock_entry)
		se = frappe.get_doc("Stock Entry", jc.custom_sfg_stock_entry)

		# Received only the good 16; consumed material for all 18 processed.
		fg = {d.item_code: flt(d.qty) for d in se.items if d.is_finished_item}
		self.assertEqual(fg, {self.items["A"]: 8, self.items["B"]: 8})
		raw_rows = [d for d in se.items if not d.is_finished_item]
		self.assertEqual(flt(raw_rows[0].qty), 18)

		wo.reload()
		produced = {d.item_code: flt(d.produced_qty) for d in wo.custom_variants}
		self.assertEqual(produced, {self.items["A"]: 8, self.items["B"]: 8})
		self.assertEqual(flt(wo.produced_qty), 16)
		# Loss-adjusted completion: variant A is 8 good + 2 process loss
		# = 10 planned. The rejects will never be produced — standard
		# process-loss semantics (C-12) close the order.
		self.assertEqual(wo.status, "Completed")
