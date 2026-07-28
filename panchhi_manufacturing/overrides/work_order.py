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
		outputs are the variant items themselves).

		This applies ONLY to the operation-as-transaction model — i.e. when
		the route actually uses per-operation SFG receipts (some operation
		names an SFG, or a stage is subcontracted). A pure BOM-driven route
		(every operation SFG-less and in-house, e.g. fetched from the
		variants' default BOMs) is a flat manufacture with a single finished
		receipt on the last operation, so the rule does not apply."""
		operations = self.get("operations") or []
		uses_sfg_model = any(d.finished_good or cint(d.is_subcontracted) for d in operations)
		if not uses_sfg_model:
			return
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

	def validate_qty(self):
		"""Stock caps WO.qty against the ONE linked Production Plan row —
		a multi-variant WO's qty spans SEVERAL plan rows, so that check
		misfires ('Cannot produce more than 2 for <style>' on a 4-piece
		three-variant plan). Run the stock validator with the plan link
		masked (keeps qty>0 / whole-number / subcontract checks), then
		apply the plan cap PER VARIANT against its own row."""
		if not cint(self.custom_is_multi_variant):
			return super().validate_qty()

		plan_item = self.production_plan_item
		try:
			self.production_plan_item = None
			super().validate_qty()
		finally:
			self.production_plan_item = plan_item

		if not (self.production_plan and plan_item):
			return

		allowance_pct = flt(
			frappe.db.get_single_value(
				"Manufacturing Settings", "overproduction_percentage_for_work_order"
			)
		)
		rows = {
			r.item_code: r
			for r in frappe.get_all(
				"Production Plan Item",
				filters={"parent": self.production_plan},
				fields=["item_code", "planned_qty", "ordered_qty"],
			)
		}
		for v in self.custom_variants:
			row = rows.get(v.item_code)
			if not row:
				continue
			max_qty = (
				flt(row.planned_qty) * (1 + allowance_pct / 100.0) - flt(row.ordered_qty)
			)
			if flt(v.qty) > max_qty:
				from erpnext.manufacturing.doctype.work_order.work_order import (
					OverProductionError,
				)

				frappe.throw(
					_("Variant {0}: cannot produce more than {1} against Production Plan {2}.").format(
						v.item_code, max_qty, self.production_plan
					),
					OverProductionError,
				)

	def update_production_plan_status(self):
		"""Stock pushes THIS Work Order's total produced qty into the ONE
		linked plan row — wrong for a multi-variant WO that serves several
		plan rows. Distribute per-variant instead, reusing the plan's own
		row updater so pending qty + plan status recompute stock-style."""
		if not cint(self.custom_is_multi_variant) or not self.production_plan:
			return super().update_production_plan_status()

		plan = frappe.get_doc("Production Plan", self.production_plan)
		rows_by_item = {}
		for row in plan.po_items:
			rows_by_item.setdefault(row.item_code, row.name)
		for v in self.custom_variants:
			row_name = rows_by_item.get(v.item_code)
			if row_name:
				plan.run_method(
					"update_produced_pending_qty", flt(v.produced_qty), row_name
				)

	def update_variant_produced_qty(self, produced_by_item: dict[str, float] | None = None):
		"""Recompute per-variant produced qty from the ledger, roll up the
		scalar, and re-derive status.

		DERIVED, never incremented: sum the finished rows of every
		submitted Manufacture Stock Entry against this Work Order. An
		incremental `+=` double-counts whenever the hook fires more than
		once for the same entry (repost, amend, a manual resync), and
		drifts permanently once it does. Deriving is idempotent and
		self-healing — re-running it can only converge on the truth.

		`produced_by_item` is accepted for signature compatibility with
		the doc_events hook but is deliberately ignored.
		"""
		produced = dict(
			frappe.db.sql(
				"""
				SELECT sed.item_code, SUM(sed.qty)
				FROM `tabStock Entry Detail` sed
				JOIN `tabStock Entry` se ON se.name = sed.parent
				WHERE se.docstatus = 1
				  AND se.purpose = 'Manufacture'
				  AND se.work_order = %s
				  AND sed.is_finished_item = 1
				GROUP BY sed.item_code
				""",
				self.name,
			)
			or []
		)

		total = 0.0
		for v in self.custom_variants:
			qty = flt(produced.get(v.item_code))
			if flt(v.produced_qty) != qty:
				v.db_set("produced_qty", qty, update_modified=False)
			total += qty
		self.db_set("produced_qty", total, update_modified=False)
		self.reload()
		self.update_status()
		# Stock wires plan sync through update_work_order_qty, which the
		# multi-variant flow bypasses — trigger it here so plan rows track
		# their own variant's produced qty.
		if self.production_plan:
			self.update_production_plan_status()


# ----------------------------------------------------------------------
# BOM-driven prefill (client maintains per-variant BOMs, not Style Recipes)
# ----------------------------------------------------------------------
def get_default_bom(item_code: str) -> str | None:
	"""The item's default active BOM, else any active BOM."""
	if not item_code:
		return None
	return frappe.db.get_value(
		"BOM", {"item": item_code, "is_active": 1, "is_default": 1}, "name"
	) or frappe.db.get_value(
		"BOM", {"item": item_code, "is_active": 1}, "name"
	)


def build_production_lines_from_variant_boms(
	company, variants, total_qty, wip_warehouse=None, source_warehouse=None
):
	"""Aggregate each variant's DEFAULT BOM into Work-Order-shaped lines.

	Panchhi maintains a full BOM per colour/size variant (operations +
	materials) rather than a Style Recipe, so a grouped multi-variant Work
	Order derives its route from those BOMs:

	  * Required Items — every variant's default BOM exploded to raw
	    materials, scaled to that variant's planned qty via ERPNext's own
	    ``get_bom_items_as_dict`` (identical division-by-bom.quantity as a
	    single-item Work Order), then merged across variants by
	    (item_code, source_warehouse).
	  * Operations — variants of one style share a production route, so the
	    operations come from a representative variant BOM (the first one
	    that carries operations), time scaled to the grouped total qty.
	    They are SFG-less and in-house: a flat manufacture whose single
	    finished receipt happens on the last operation's Job Card.

	Returns ``None`` when NO variant has a BOM (caller keeps the WO empty
	for the planner to fill by hand, exactly as before).
	"""
	from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict

	merged: dict[tuple, dict] = {}
	representative_bom = None
	found_any = False

	for v in variants or []:
		item_code = v.get("item_code") if isinstance(v, dict) else v.item_code
		vqty = flt(v.get("qty") if isinstance(v, dict) else v.qty)
		bom = get_default_bom(item_code)
		if not bom:
			continue
		found_any = True
		if representative_bom is None and frappe.get_cached_value("BOM", bom, "with_operations"):
			representative_bom = bom
		if vqty <= 0:
			continue
		for it in get_bom_items_as_dict(bom, company, qty=vqty, fetch_exploded=1).values():
			key = (it.get("item_code"), it.get("source_warehouse"))
			row = merged.get(key)
			if not row:
				row = merged[key] = {
					"item_code": it.get("item_code"),
					"item_name": it.get("item_name"),
					"required_qty": 0.0,
					"stock_uom": it.get("stock_uom"),
					"source_warehouse": it.get("source_warehouse") or source_warehouse or wip_warehouse,
				}
			row["required_qty"] += flt(it.get("qty"))

	if not found_any:
		return None

	operations = []
	if representative_bom:
		bom_qty = flt(frappe.get_cached_value("BOM", representative_bom, "quantity")) or 1.0
		scale = flt(total_qty) / bom_qty if bom_qty else 1.0
		for op in frappe.get_all(
			"BOM Operation",
			filters={"parent": representative_bom},
			fields=["operation", "workstation", "description", "time_in_mins", "sequence_id", "idx"],
			order_by="idx",
		):
			operations.append(
				{
					"operation": op.operation,
					"workstation": op.workstation,
					"description": op.description,
					"time_in_mins": flt(op.time_in_mins) * scale,
					"sequence_id": op.sequence_id or op.idx,
				}
			)

	return {
		"operations": operations,
		"required_items": list(merged.values()),
		"representative_bom": representative_bom,
	}


@frappe.whitelist()
def fetch_production_details(work_order: str):
	"""(Re)build a multi-variant Work Order's operations + required items.

	Style Recipe first (backward compatible); otherwise derive from the
	variants' default BOMs. Lets a planner populate an already-created
	grouped Work Order that came out empty, without recreating it.
	"""
	wo = frappe.get_doc("Work Order", work_order)
	if not cint(wo.custom_is_multi_variant):
		frappe.throw(_("{0} is not a Multi-Variant Work Order.").format(wo.name))
	if wo.docstatus != 0:
		frappe.throw(_("Operations & materials can only be fetched on a draft Work Order."))
	if not wo.get("custom_variants"):
		frappe.throw(_("Add at least one row to the Variants table first."))

	total_qty = sum(flt(v.qty) for v in wo.custom_variants)

	if frappe.db.exists("Style Recipe", {"style_item": wo.production_item, "enabled": 1}):
		from panchhi_manufacturing.panchhi_manufacturing.doctype.style_recipe.style_recipe import (
			get_recipe_details,
		)

		details = get_recipe_details(wo.production_item, qty=total_qty)
		operations, required_items = details["operations"], details["required_items"]
		source = _("Style Recipe")
	else:
		lines = build_production_lines_from_variant_boms(
			wo.company,
			wo.custom_variants,
			total_qty,
			wip_warehouse=wo.wip_warehouse,
			source_warehouse=wo.source_warehouse,
		)
		if not lines:
			frappe.throw(
				_(
					"No enabled Style Recipe for {0}, and none of its variants has a default "
					"BOM. Create a BOM for the variant items (or a Style Recipe for the style) "
					"and try again."
				).format(wo.production_item),
				title=_("Nothing To Fetch"),
			)
		operations, required_items = lines["operations"], lines["required_items"]
		source = _("variant BOMs")

	wo.set("operations", [])
	wo.set("required_items", [])
	for op in operations:
		wo.append("operations", op)
	for rm in required_items:
		wo.append("required_items", rm)
	wo.save(ignore_permissions=True)
	frappe.msgprint(
		_("Fetched {0} operation(s) and {1} material line(s) from {2}.").format(
			len(operations), len(required_items), source
		),
		alert=True,
		indicator="green",
	)
	return wo.name
