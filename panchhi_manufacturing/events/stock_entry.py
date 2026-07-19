"""C-06 (half 2) — per-variant produced qty + status roll-up.

Additive doc_events hook (NOT a fork): after any Stock Entry submits or
cancels against a multi-variant Work Order, recompute each variant's
produced_qty from the entry's finished rows, roll the scalar
`produced_qty` up as the sum of variants, and let the Work Order
re-derive its status (Completed only when every variant is complete —
because the scalar equals the variant sum, the stock status math holds).

Intermediate SFG receipts (operation outputs that are NOT in the
variant plan) deliberately do NOT move produced_qty — only final
variant output counts as production.
"""
from __future__ import annotations

import frappe
from frappe.utils import cint, flt


def update_variant_produced_qty(doc, method=None):
	if not doc.work_order or doc.purpose != "Manufacture":
		return
	if not cint(
		frappe.db.get_value("Work Order", doc.work_order, "custom_is_multi_variant")
	):
		return

	variant_items = set(
		frappe.get_all(
			"Panchhi WO Variant", filters={"parent": doc.work_order}, pluck="item_code"
		)
	)
	sign = -1 if method == "on_cancel" else 1
	produced: dict[str, float] = {}
	for d in doc.get("items"):
		if d.is_finished_item and d.item_code in variant_items:
			produced[d.item_code] = produced.get(d.item_code, 0.0) + sign * flt(d.qty)

	if not produced:
		return

	wo = frappe.get_doc("Work Order", doc.work_order)
	wo.update_variant_produced_qty(produced)
