"""C-01 / C-02 — Multi-variant Work Order (route header).

Gated on `custom_is_multi_variant`. Flag off ⇒ every method defers to
stock ERPNext unchanged (FRD sheet A blast-radius rule).

v16.6 already gives us per-operation SFG natively (Work Order Operation.
finished_good / wip_warehouse / fg_warehouse / is_subcontracted and
WO.track_semi_finished_goods), so this layer adds ONLY what v16.6
lacks: several deliverable variants on one Work Order, and Job Cards /
SCOs that carry all of them.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.manufacturing.doctype.work_order.work_order import (
	WorkOrder,
	split_qty_based_on_batch_size,
)


class MultiVariantWorkOrder(WorkOrder):
	def validate(self):
		if cint(self.custom_is_multi_variant):
			self._validate_variants()
			self._validate_operation_outputs()
		super().validate()

	def validate_production_item(self):
		"""Stock ERPNext refuses an Item Template as production_item.
		FRD: the multi-variant WO's production_item IS the style
		template — the real deliverables are the variant rows, each
		validated to be a concrete (non-template) item."""
		if cint(self.custom_is_multi_variant):
			from erpnext.stock.doctype.item.item import validate_end_of_life

			if self.production_item:
				validate_end_of_life(self.production_item)
			for d in self.get("custom_variants") or []:
				if frappe.get_cached_value("Item", d.item_code, "has_variants"):
					frappe.throw(
						_("Row {0}: Variant {1} is itself a template — pick concrete variants.").format(
							d.idx, d.item_code
						)
					)
			return
		return super().validate_production_item()

	def _validate_variants(self):
		rows = self.get("custom_variants") or []
		if not rows:
			frappe.throw(
				_("Multi-Variant Work Order needs at least one row in the Variants table."),
				title=_("Variants Missing"),
			)
		seen = set()
		total = 0.0
		for d in rows:
			if d.item_code in seen:
				frappe.throw(
					_("Variant {0} appears more than once in the Variants table.").format(d.item_code)
				)
			seen.add(d.item_code)
			if flt(d.qty) <= 0:
				frappe.throw(
					_("Row {0}: Variant {1} needs a Planned Qty greater than zero.").format(
						d.idx, d.item_code
					)
				)
			total += flt(d.qty)
		# The Work Order's scalar qty is the roll-up of the variant plan —
		# keeps every stock-ERPNext qty validation consistent.
		self.qty = total

	def _validate_operation_outputs(self):
		"""FRD L-03 / R-05 — an operation without an output SFG would
		complete without receiving anything into stock. Every operation
		except the LAST must name its finished_good (the last operation's
		outputs are the variant items themselves)."""
		operations = self.get("operations") or []
		for d in operations[:-1]:
			if not d.finished_good:
				frappe.throw(
					_(
						"Row {0}: Operation {1} has no Semi Finished Good. Every operation "
						"of a Multi-Variant Work Order except the last must name the SFG "
						"it receives into stock."
					).format(d.idx, d.operation),
					title=_("Output SFG Missing"),
				)

	def create_job_card(self):
		"""Native loop, minus subcontracted operations (those get a
		Subcontracting Order instead — C-02 branching), plus variant rows
		stamped onto every created Job Card (C-03)."""
		if not cint(self.custom_is_multi_variant):
			return super().create_job_card()

		manufacturing_settings_doc = frappe.get_doc("Manufacturing Settings")
		enable_capacity_planning = not cint(manufacturing_settings_doc.disable_capacity_planning)
		plan_days = cint(manufacturing_settings_doc.capacity_planning_for_days) or 30

		for idx, row in enumerate(self.operations):
			if cint(row.is_subcontracted):
				continue  # C-02: subcontracted ops transact through an SCO, never a JC
			qty = self.qty
			while qty > 0:
				qty = split_qty_based_on_batch_size(self, row, qty)
				if row.job_card_qty > 0:
					self.prepare_data_for_job_card(row, idx, plan_days, enable_capacity_planning)

		planned_end_date = self.operations and self.operations[-1].planned_end_time
		if planned_end_date:
			self.db_set("planned_end_date", planned_end_date)

		self._stamp_variants_on_job_cards()

	def _stamp_variants_on_job_cards(self):
		"""Copy the variant plan onto every draft Job Card of this WO."""
		for jc_name in frappe.get_all(
			"Job Card", filters={"work_order": self.name, "docstatus": 0}, pluck="name"
		):
			jc = frappe.get_doc("Job Card", jc_name)
			if jc.get("custom_items"):
				continue
			jc.custom_is_multi_variant = 1
			for v in self.custom_variants:
				jc.append(
					"custom_items",
					{
						"item_code": v.item_code,
						"item_name": v.item_name,
						"planned_qty": flt(v.qty),
						"pending_qty": flt(v.qty),
						"status": "Pending",
					},
				)
			jc.save(ignore_permissions=True)

	def update_variant_produced_qty(self, produced_by_item: dict[str, float]):
		"""Called from the Stock Entry submit/cancel hook: write per-variant
		produced qty, roll up the scalar, and re-derive status."""
		total = 0.0
		for v in self.custom_variants:
			if v.item_code in produced_by_item:
				v.db_set(
					"produced_qty",
					max(0.0, flt(v.produced_qty) + flt(produced_by_item[v.item_code])),
					update_modified=False,
				)
			total += flt(
				frappe.db.get_value("Panchhi WO Variant", v.name, "produced_qty")
			)
		self.db_set("produced_qty", total, update_modified=False)
		self.reload()
		self.update_status()
