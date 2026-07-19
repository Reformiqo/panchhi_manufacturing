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
CUSTOM_FIELDS: dict[str, list[dict]] = {}

# (doctype, fieldname, property, value, property_type) — upserted each migrate.
PROPERTY_SETTERS: list[tuple[str, str | None, str, str, str]] = []


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
