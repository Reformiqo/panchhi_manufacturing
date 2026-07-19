"""C-07 — Style Recipe: the BOM-less prefill for multi-variant Work Orders.

Optional, never blocking (FRD sheet 1 req #4): a Work Order can always
be typed by hand. The recipe is the anti-fatigue answer to R-06 —
expected to carry ~90% of production within a quarter.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class StyleRecipe(Document):
	def validate(self):
		ops = {d.operation for d in self.operations}
		for m in self.materials:
			if m.operation not in ops:
				frappe.throw(
					_("Materials row {0}: operation {1} is not in the Operations table.").format(
						m.idx, m.operation
					)
				)
		# Every op except the last needs an SFG output — same rule the
		# Work Order enforces (L-03), caught earlier here.
		for d in (self.operations or [])[:-1]:
			if not d.finished_good:
				frappe.throw(
					_(
						"Operation {0} needs a Semi Finished Good (only the last "
						"operation may leave it empty)."
					).format(d.operation)
				)


@frappe.whitelist()
def get_recipe_details(style_item: str, qty: float = 0):
	"""Rows for the Work Order form: operations + required items scaled
	by qty, + the style's existing variants for the variant-table prefill."""
	recipe_name = frappe.db.get_value(
		"Style Recipe", {"style_item": style_item, "enabled": 1}, "name"
	)
	if not recipe_name:
		frappe.throw(
			_("No enabled Style Recipe found for {0}.").format(style_item),
			title=_("Style Recipe"),
		)
	recipe = frappe.get_doc("Style Recipe", recipe_name)
	qty = flt(qty)

	operations = [
		{
			"operation": d.operation,
			"workstation": d.workstation,
			"time_in_mins": flt(d.time_in_mins),
			"is_subcontracted": d.is_subcontracted,
			"finished_good": d.finished_good,
			"wip_warehouse": d.wip_warehouse,
			"fg_warehouse": d.fg_warehouse,
			"status": "Pending",
		}
		for d in recipe.operations
	]
	required_items = [
		{
			"item_code": m.item_code,
			"item_name": m.item_name,
			"required_qty": flt(m.qty_per_unit) * qty if qty else flt(m.qty_per_unit),
			"operation": m.operation,
			"source_warehouse": m.source_warehouse,
		}
		for m in recipe.materials
	]
	variants = frappe.get_all(
		"Item",
		filters={"variant_of": style_item, "disabled": 0},
		fields=["name as item_code", "item_name"],
		order_by="name",
	)
	return {
		"recipe": recipe_name,
		"operations": operations,
		"required_items": required_items,
		"variants": variants,
	}
