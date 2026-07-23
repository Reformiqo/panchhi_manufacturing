"""Form-layout blast radius — the class of bug unit tests never see.

A Frappe Section Break owns every field up to the NEXT Section Break, so
a `depends_on` on OUR section break silently hides STOCK fields that
happen to follow it. Found live on 2026-07-23: an unchecked Work Order
rendered with no Company, Qty To Manufacture, BOM No, Sales Order or
Project, because `custom_variants_section` was never closed.

Every one of the 35 behavioural tests passed while the form was
unusable — they assert on documents, never on layout. These assert on
layout.
"""
import frappe
from frappe.tests import IntegrationTestCase

# Our gated section breaks, and the stock field that must stay visible
# on a doc where the gate is OFF.
GATED_SECTIONS = {
	"Work Order": [
		("custom_variants_section", "custom_variants_end_section"),
	],
	"Job Card": [
		("custom_variant_items_section", "custom_variant_items_end_section"),
	],
}


class TestFormLayout(IntegrationTestCase):

	def test_every_gated_section_break_is_closed(self):
		"""A depends_on Section Break must be followed by a closing
		Section Break before any STOCK (non-custom_) field."""
		for doctype, pairs in GATED_SECTIONS.items():
			meta = frappe.get_meta(doctype)
			names = [f.fieldname for f in meta.fields]
			for opener, closer in pairs:
				with self.subTest(doctype=doctype, section=opener):
					self.assertIn(opener, names, f"{doctype}.{opener} missing")
					self.assertIn(closer, names, f"{doctype}.{closer} missing")

					start, end = names.index(opener), names.index(closer)
					self.assertLess(start, end, "closer must follow opener")

					swallowed = [
						fn
						for fn in names[start + 1 : end]
						if not fn.startswith("custom_")
					]
					self.assertEqual(
						swallowed,
						[],
						f"{doctype}.{opener} is gated by depends_on and would "
						f"HIDE these stock fields when the gate is off: {swallowed}. "
						f"Close the section earlier.",
					)

	def test_closing_breaks_are_never_gated(self):
		"""The closing break must always render — a depends_on on it
		would re-open the same hole."""
		for doctype, pairs in GATED_SECTIONS.items():
			meta = frappe.get_meta(doctype)
			for _opener, closer in pairs:
				df = meta.get_field(closer)
				with self.subTest(doctype=doctype, field=closer):
					self.assertFalse(
						df.depends_on,
						f"{doctype}.{closer} closes a gated section and must "
						f"not itself be conditional",
					)
					self.assertFalse(df.hidden, f"{doctype}.{closer} must not be hidden")

	def test_stock_work_order_keeps_its_core_fields_visible(self):
		"""The reported symptom, asserted directly: on a Work Order with
		the multi-variant gate OFF, stock's own fields must not fall
		inside any hidden section."""
		meta = frappe.get_meta("Work Order")
		names = [f.fieldname for f in meta.fields]

		# Walk the fields tracking which section is active, exactly as the
		# form renderer does, and collect what a gate-off doc would hide.
		hidden_now = False
		hidden_fields = []
		for f in meta.fields:
			if f.fieldtype == "Section Break":
				hidden_now = "custom_is_multi_variant" in (f.depends_on or "")
			elif hidden_now:
				hidden_fields.append(f.fieldname)

		for critical in (
			"company",
			"qty",
			"bom_no",
			"sales_order",
			"production_plan",
			"project",
		):
			with self.subTest(field=critical):
				self.assertIn(critical, names, f"Work Order.{critical} vanished")
				self.assertNotIn(
					critical,
					hidden_fields,
					f"Work Order.{critical} is hidden on a NON-multi-variant "
					f"Work Order — stock ERPNext behaviour is broken",
				)
