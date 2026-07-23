"""C-06 (half) + C-13 — Stock Entry against a Multi-Variant Work Order.

THE FORK, part 1 of 2 (FRD sheet A). Gated on the Work Order's
`custom_is_multi_variant`; unmarked Work Orders hit stock ERPNext
byte-for-byte via the early `super()` return.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.stock.doctype.stock_entry.stock_entry import (
	FinishedGoodError,
	StockEntry,
)


class MultiVariantStockEntry(StockEntry):
	def _multi_variant_wo(self):
		if not self.work_order:
			return None
		if cint(
			frappe.db.get_value("Work Order", self.work_order, "custom_is_multi_variant")
		):
			return self.work_order
		return None

	def validate_finished_goods(self):
		"""Stock rule: one FG per Work Order, item must equal
		production_item. Multi-variant rule: any variant of the plan OR
		any operation's SFG output (or a variant thereof) is a valid
		finished item, several per entry, each capped by its own plan."""
		wo = self._multi_variant_wo()
		if not wo:
			return super().validate_finished_goods()

		allowed, variant_caps = self._allowed_finished_items(wo)
		finished = []
		for d in self.get("items"):
			if not d.is_finished_item:
				continue
			if d.item_code not in allowed:
				frappe.throw(
					_(
						"Finished Item {0} is neither a Variant of Work Order {1} nor an "
						"operation output SFG."
					).format(d.item_code, wo),
					exc=FinishedGoodError,
				)
			finished.append(d)

		if not finished:
			frappe.throw(
				msg=_("There must be atleast 1 Finished Good in this Stock Entry"),
				title=_("Missing Finished Good"),
				exc=FinishedGoodError,
			)

		allowance = flt(
			frappe.db.get_single_value(
				"Manufacturing Settings", "overproduction_percentage_for_work_order"
			)
		)
		for d in finished:
			if d.item_code not in variant_caps:
				continue  # intermediate SFG — capped only by the WO total below
			cap = variant_caps[d.item_code]
			already = flt(
				frappe.db.get_value(
					"Panchhi WO Variant",
					{"parent": wo, "item_code": d.item_code},
					"produced_qty",
				)
			)
			allowed_qty = cap + (allowance / 100.0) * cap
			if already + flt(d.qty) > allowed_qty:
				frappe.throw(
					_(
						"Variant {0}: producing {1} would exceed the planned {2} "
						"(+{3}% allowance) on Work Order {4}."
					).format(d.item_code, flt(d.qty), cap, allowance, wo)
				)

	def _allowed_finished_items(self, wo: str):
		variants = frappe.get_all(
			"Panchhi WO Variant", filters={"parent": wo}, fields=["item_code", "qty"]
		)
		variant_caps = {v.item_code: flt(v.qty) for v in variants}
		allowed = set(variant_caps)

		op_fgs = [
			fg
			for fg in frappe.get_all(
				"Work Order Operation", filters={"parent": wo}, pluck="finished_good"
			)
			if fg
		]
		allowed.update(op_fgs)
		if op_fgs:
			# variants OF an operation SFG template (SFG-CUT → SFG-CUT-PNK …)
			allowed.update(
				frappe.get_all(
					"Item", filters={"variant_of": ("in", op_fgs)}, pluck="name"
				)
			)
		# explicit per-variant outputs typed on this WO's Job Cards
		jc_names = frappe.get_all("Job Card", filters={"work_order": wo}, pluck="name")
		if jc_names:
			allowed.update(
				o
				for o in frappe.get_all(
					"Panchhi JC Variant Item",
					filters={"parent": ("in", jc_names)},
					pluck="output_item",
				)
				if o
			)
		return allowed, variant_caps

	def validate(self):
		# fg_completed_qty needs handling on BOTH sides of super():
		#
		#  BEFORE — stock's validate_work_order() throws "For Quantity
		#    (Manufactured Qty) is mandatory" when it is empty. On a
		#    multi-variant entry the answer is simply the sum of the
		#    finished rows, so derive it rather than making the user
		#    retype what the grid already says.
		#  AFTER  — stock's validate() then zeroes it whenever from_bom
		#    is unset, and BOM-less manufacture is the whole point of
		#    this app (C-01/C-04). Restore the same derived figure.
		is_mv_manufacture = self._multi_variant_wo() and self.purpose == "Manufacture"
		if is_mv_manufacture and not flt(self.fg_completed_qty):
			self.fg_completed_qty = self._finished_qty()

		super().validate()

		if is_mv_manufacture and not self.from_bom:
			self.fg_completed_qty = self._finished_qty()
		self._suppress_whole_wo_operating_cost()

	def _finished_qty(self):
		return sum(flt(d.qty) for d in self.get("items") if d.is_finished_item)

	def _suppress_whole_wo_operating_cost(self):
		"""C-13 — every operation's SE already carries that operation's
		actual cost (posted by its Job Card). ERPNext's whole-WO operating
		cost injection on a final Manufacture entry would count the same
		labour twice. Drop those rows on multi-variant WOs."""
		if not self._multi_variant_wo():
			return
		if self.purpose != "Manufacture" or self.get("job_card"):
			return
		kept = [
			d
			for d in (self.get("additional_costs") or [])
			if "operating cost" not in (d.description or "").lower()
		]
		if len(kept) != len(self.get("additional_costs") or []):
			self.set("additional_costs", kept)
