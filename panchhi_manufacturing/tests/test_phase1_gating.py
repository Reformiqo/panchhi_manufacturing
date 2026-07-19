"""THE REGRESSION SUITE (FRD sheet A, mitigation row 2 / T-02).

Run after every ERPNext upgrade, forever. Pins the blast-radius rule:
with `custom_is_multi_variant` OFF (and no `custom_work_order` on the
SCO), every override defers to stock ERPNext.
"""
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase


class TestPhase1Gating(IntegrationTestCase):

	def test_stock_entry_defers_to_super_when_flag_off(self):
		se = frappe.get_doc({"doctype": "Stock Entry", "purpose": "Manufacture"})
		se.work_order = None
		with mock.patch(
			"erpnext.stock.doctype.stock_entry.stock_entry.StockEntry.validate_finished_goods"
		) as core:
			se.validate_finished_goods()
		core.assert_called_once()

	def test_stock_entry_defers_when_wo_not_multi_variant(self):
		se = frappe.get_doc({"doctype": "Stock Entry", "purpose": "Manufacture"})
		se.work_order = "WO-FAKE"
		with mock.patch.object(frappe.db, "get_value", return_value=0), mock.patch(
			"erpnext.stock.doctype.stock_entry.stock_entry.StockEntry.validate_finished_goods"
		) as core:
			se.validate_finished_goods()
		core.assert_called_once()

	def test_work_order_create_job_card_defers_when_flag_off(self):
		wo = frappe.get_doc({"doctype": "Work Order", "custom_is_multi_variant": 0})
		with mock.patch(
			"erpnext.manufacturing.doctype.work_order.work_order.WorkOrder.create_job_card"
		) as core:
			wo.create_job_card()
		core.assert_called_once()

	def test_work_order_validate_production_item_defers_when_flag_off(self):
		wo = frappe.get_doc({"doctype": "Work Order", "custom_is_multi_variant": 0})
		with mock.patch(
			"erpnext.manufacturing.doctype.work_order.work_order.WorkOrder.validate_production_item"
		) as core:
			wo.validate_production_item()
		core.assert_called_once()

	def test_job_card_no_sfg_receipt_when_flag_off(self):
		jc = frappe.get_doc({"doctype": "Job Card", "custom_is_multi_variant": 0})
		with mock.patch(
			"erpnext.manufacturing.doctype.job_card.job_card.JobCard.on_submit"
		) as core, mock.patch.object(jc, "_post_sfg_receipt") as poster:
			jc.on_submit()
		core.assert_called_once()
		poster.assert_not_called()

	def test_sco_defers_when_not_wo_driven(self):
		sco = frappe.get_doc({"doctype": "Subcontracting Order"})
		with mock.patch(
			"erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order."
			"SubcontractingOrder.validate_purchase_order_for_subcontracting"
		) as core:
			sco.validate_purchase_order_for_subcontracting()
		core.assert_called_once()

	def test_se_hook_noops_without_work_order(self):
		from panchhi_manufacturing.events.stock_entry import update_variant_produced_qty

		doc = frappe._dict(work_order=None, purpose="Manufacture")
		# Must return silently, touching nothing.
		self.assertIsNone(update_variant_produced_qty(doc, "on_submit"))

	def test_multi_variant_wo_requires_variant_rows(self):
		wo = frappe.get_doc(
			{"doctype": "Work Order", "custom_is_multi_variant": 1, "custom_variants": []}
		)
		with self.assertRaises(frappe.ValidationError):
			wo._validate_variants()

	def test_multi_variant_wo_rejects_duplicate_variants(self):
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"custom_is_multi_variant": 1,
				"custom_variants": [
					{"item_code": "X", "qty": 5},
					{"item_code": "X", "qty": 3},
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			wo._validate_variants()

	def test_multi_variant_wo_qty_is_variant_sum(self):
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"custom_is_multi_variant": 1,
				"custom_variants": [
					{"item_code": "A", "qty": 100},
					{"item_code": "B", "qty": 80},
					{"item_code": "C", "qty": 120},
				],
			}
		)
		wo._validate_variants()
		self.assertEqual(wo.qty, 300)

	def test_jc_rollup_sets_standard_total_completed_qty(self):
		jc = frappe.get_doc(
			{
				"doctype": "Job Card",
				"custom_is_multi_variant": 1,
				"custom_items": [
					{"item_code": "A", "planned_qty": 100, "completed_qty": 98, "rejected_qty": 2},
					{"item_code": "B", "planned_qty": 80, "completed_qty": 80},
					{"item_code": "C", "planned_qty": 120, "completed_qty": 0},
				],
			}
		)
		jc._roll_up_variant_items()
		self.assertEqual(jc.total_completed_qty, 178)
		self.assertEqual(jc.custom_items[0].status, "Completed")  # 98 + 2 rejected = plan
		self.assertEqual(jc.custom_items[1].status, "Completed")
		self.assertEqual(jc.custom_items[2].status, "Pending")
		self.assertEqual(jc.custom_items[2].pending_qty, 120)

	def test_c13_suppresses_wo_level_operating_cost(self):
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Manufacture",
				"work_order": "WO-FAKE",
				"additional_costs": [
					{"expense_account": "X", "description": "Operating Cost as per Work Order / BOM", "amount": 100},
					{"expense_account": "X", "description": "Freight", "amount": 50},
				],
			}
		)
		with mock.patch.object(frappe.db, "get_value", return_value=1):
			se._suppress_whole_wo_operating_cost()
		descriptions = [d.description for d in se.additional_costs]
		self.assertEqual(descriptions, ["Freight"])
