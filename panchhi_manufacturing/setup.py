"""Code-first Custom Fields + Property Setters for Panchhi Manufacturing.

Runs on after_install AND after_migrate (hooks.py), so every deploy
converges the schema to what this module declares — the "setting keeps
resetting" class of bug can't happen, and there is exactly ONE source of
truth (this file), never Customize Form, never fixtures.

Phase map (FRD v3.0, sheet 'B. Build Plan'):
  Phase 0  — scaffold only. CUSTOM_FIELDS / PROPERTY_SETTERS empty.
  C-01     — Work Order: custom_is_multi_variant + custom_variants table
             + bom_no optional PS.
  C-02     — Work Order Operation: execution_type / output_sfg_item /
             subcontract fields.
  C-03     — Job Card: custom_items table + roll-up fields.
  C-05     — Subcontracting Order: custom_work_order / custom_operation
             + Subcontracting Order Item.bom optional PS.
Each phase appends to the dicts below in its own commit.
"""
from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Panchhi Manufacturing"

# {doctype: [field_def, ...]} — consumed by create_custom_fields (idempotent).
CUSTOM_FIELDS: dict[str, list[dict]] = {
	# ---- C-01: multi-variant Work Order --------------------------------
	"Work Order": [
		{
			"fieldname": "custom_is_multi_variant",
			"fieldtype": "Check",
			"label": "Is Multi Variant",
			"insert_after": "production_item",
			"description": "One Work Order for every colour/size variant of a style. "
			"The style template stays in Production Item; the real deliverables "
			"live in the Variants table. Gates every Panchhi override — "
			"unchecked Work Orders behave as stock ERPNext.",
		},
		{
			"fieldname": "custom_variants_section",
			"fieldtype": "Section Break",
			"label": "Variants",
			"insert_after": "custom_is_multi_variant",
			"depends_on": "eval:doc.custom_is_multi_variant",
		},
		{
			"fieldname": "custom_variants",
			"fieldtype": "Table",
			"label": "Variant Items",
			"options": "Panchhi WO Variant",
			"insert_after": "custom_variants_section",
			"depends_on": "eval:doc.custom_is_multi_variant",
		},
		{
			# MUST close the section. A Section Break owns every field up to
			# the NEXT Section Break, so without this the "Variants" break
			# swallows the rest of stock's Production Item section — and its
			# depends_on then hides Company / Qty To Manufacture / BOM No /
			# Sales Order / Project on every UNCHECKED Work Order. That is
			# the exact blast radius the FRD forbids. No depends_on here:
			# this break must always render so the stock fields come back.
			"fieldname": "custom_variants_end_section",
			"fieldtype": "Section Break",
			"insert_after": "custom_variants",
		},
	],
	# ---- C-03: multi-item Job Card -------------------------------------
	"Job Card": [
		{
			"fieldname": "custom_is_multi_variant",
			"fieldtype": "Check",
			"label": "Is Multi Variant",
			"insert_after": "work_order",
			"read_only": 1,
			"fetch_from": "work_order.custom_is_multi_variant",
		},
		{
			"fieldname": "custom_variant_items_section",
			"fieldtype": "Section Break",
			"label": "Variant Items",
			"insert_after": "custom_is_multi_variant",
			"depends_on": "eval:doc.custom_is_multi_variant",
		},
		{
			"fieldname": "custom_items",
			"fieldtype": "Table",
			"label": "Variant Items",
			"options": "Panchhi JC Variant Item",
			"insert_after": "custom_variant_items_section",
			"depends_on": "eval:doc.custom_is_multi_variant",
		},
		{
			"fieldname": "custom_sfg_stock_entry",
			"fieldtype": "Link",
			"label": "SFG Receipt Stock Entry",
			"options": "Stock Entry",
			"insert_after": "custom_items",
			"read_only": 1,
			"no_copy": 1,
			"description": "Auto-posted on Job Card submit: receives this "
			"operation's output into stock, per variant (C-04).",
		},
		{
			# Closes the section — see custom_variants_end_section on Work
			# Order. Without it the gated break hides stock's bom_no and
			# is_subcontracted on every non-multi-variant Job Card.
			"fieldname": "custom_variant_items_end_section",
			"fieldtype": "Section Break",
			"insert_after": "custom_sfg_stock_entry",
		},
	],
	# ---- C-05: WO-driven Subcontracting Order --------------------------
	"Subcontracting Order": [
		{
			"fieldname": "custom_work_order",
			"fieldtype": "Link",
			"label": "Work Order",
			"options": "Work Order",
			"insert_after": "purchase_order",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_operation",
			"fieldtype": "Data",
			"label": "Work Order Operation",
			"insert_after": "custom_work_order",
			"read_only": 1,
			"no_copy": 1,
		},
	],
}

# (doctype, fieldname, property, value, property_type) — upserted each migrate.
PROPERTY_SETTERS: list[tuple[str, str | None, str, str, str]] = [
	# C-01 — BOM optional for in-house manufacture. WO.validate already
	# guards every BOM code path behind `if self.bom_no`, so relaxing the
	# field is sufficient.
	("Work Order", "bom_no", "reqd", "0", "Check"),
	# C-05 — BOM absent from subcontracting when WO-driven.
	("Subcontracting Order Item", "bom", "reqd", "0", "Check"),
	# C-05 — WO-driven SCO has no Purchase Order behind it.
	("Subcontracting Order", "purchase_order", "reqd", "0", "Check"),
]


def after_migrate():
	"""Converge Custom Fields + Property Setters to this module's spec."""
	if CUSTOM_FIELDS:
		create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
		_stamp_module(CUSTOM_FIELDS)
	for doctype, fieldname, prop, value, property_type in PROPERTY_SETTERS:
		frappe.make_property_setter(
			{
				"doctype": doctype,
				"fieldname": fieldname,
				"property": prop,
				"value": value,
				"property_type": property_type,
			},
			validate_fields_for_doctype=False,
		)
	frappe.db.commit()


def _stamp_module(field_map: dict[str, list[dict]]) -> None:
	"""Tag our Custom Fields with this app's module so they are
	attributable (and never picked up by another app's fixture export)."""
	for doctype, fields in field_map.items():
		for df in fields:
			name = f"{doctype}-{df['fieldname']}"
			if frappe.db.exists("Custom Field", name):
				frappe.db.set_value(
					"Custom Field", name, "module", MODULE, update_modified=False
				)
