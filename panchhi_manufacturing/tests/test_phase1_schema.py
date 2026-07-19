"""Phase 1 — schema + wiring contract.

Everything later phases assume: custom fields exist, property setters
applied, child doctypes migrated, override classes actually resolve.
"""
import frappe
from frappe.tests import IntegrationTestCase


class TestPhase1Schema(IntegrationTestCase):

	def test_work_order_custom_fields(self):
		meta = frappe.get_meta("Work Order")
		self.assertTrue(meta.has_field("custom_is_multi_variant"))
		self.assertTrue(meta.has_field("custom_variants"))
		self.assertEqual(meta.get_field("custom_variants").options, "Panchhi WO Variant")

	def test_job_card_custom_fields(self):
		meta = frappe.get_meta("Job Card")
		self.assertTrue(meta.has_field("custom_is_multi_variant"))
		self.assertTrue(meta.has_field("custom_items"))
		self.assertTrue(meta.has_field("custom_sfg_stock_entry"))
		self.assertEqual(meta.get_field("custom_items").options, "Panchhi JC Variant Item")

	def test_sco_custom_fields(self):
		meta = frappe.get_meta("Subcontracting Order")
		self.assertTrue(meta.has_field("custom_work_order"))
		self.assertTrue(meta.has_field("custom_operation"))

	def test_child_doctypes_exist(self):
		self.assertTrue(frappe.db.exists("DocType", "Panchhi WO Variant"))
		self.assertTrue(frappe.db.exists("DocType", "Panchhi JC Variant Item"))

	def test_property_setters_applied(self):
		"""bom_no / SCO bom / SCO purchase_order all optional (C-01, C-05)."""
		self.assertFalse(frappe.get_meta("Work Order").get_field("bom_no").reqd)
		self.assertFalse(frappe.get_meta("Subcontracting Order Item").get_field("bom").reqd)
		self.assertFalse(frappe.get_meta("Subcontracting Order").get_field("purchase_order").reqd)

	def test_override_classes_resolve(self):
		from panchhi_manufacturing.overrides.job_card import MultiItemJobCard
		from panchhi_manufacturing.overrides.stock_entry import MultiVariantStockEntry
		from panchhi_manufacturing.overrides.subcontracting_order import (
			PanchhiSubcontractingOrder,
		)
		from panchhi_manufacturing.overrides.work_order import MultiVariantWorkOrder

		self.assertTrue(issubclass(MultiVariantWorkOrder, frappe.get_doc({"doctype": "Work Order"}).__class__.__mro__[-3] or object))
		# The registry must hand back OUR classes.
		self.assertIs(frappe.get_doc({"doctype": "Work Order"}).__class__, MultiVariantWorkOrder)
		self.assertIs(frappe.get_doc({"doctype": "Stock Entry"}).__class__, MultiVariantStockEntry)
		self.assertIs(frappe.get_doc({"doctype": "Job Card"}).__class__, MultiItemJobCard)
		self.assertIs(
			frappe.get_doc({"doctype": "Subcontracting Order"}).__class__,
			PanchhiSubcontractingOrder,
		)

	def test_stock_entry_doc_events_wired(self):
		hooks = frappe.get_hooks("doc_events", app_name="panchhi_manufacturing")
		se = hooks.get("Stock Entry", {})
		target = "panchhi_manufacturing.events.stock_entry.update_variant_produced_qty"
		self.assertIn(target, se.get("on_submit", []))
		self.assertIn(target, se.get("on_cancel", []))
