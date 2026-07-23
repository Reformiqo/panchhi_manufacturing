"""Production Plan → ONE multi-variant Work Order per style.

Client requirement (22-07-2026, Momodou/Nainsi walkthrough on
MFG-PP-2026-00002): a Production Plan whose assembly items are colour
variants of the same style (6186-CREAM & PINK / 6186 -SKY / 6186 -PINK,
all `variant_of` = 6186) must create ONE Work Order carrying all three
in its Variants table — not one Work Order per row.

Gating / blast radius:
  * Only plan rows whose Item has `variant_of` set participate, and only
    when TWO OR MORE rows share the same template. Everything else —
    single-variant rows, non-variant items, sub-assembly rows,
    subcontracted POs, material requests — flows through stock ERPNext
    unchanged.
  * The grouped Work Order is the FRD's route header: production_item =
    the style template, custom_is_multi_variant = 1, BOM-less. If a
    Style Recipe exists for the template it is applied automatically
    (operations + scaled materials); otherwise the planner types
    operations by hand before submitting.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.manufacturing.doctype.production_plan.production_plan import (
	ProductionPlan,
	set_default_warehouses,
)


class PanchhiProductionPlan(ProductionPlan):
	def make_work_order_for_finished_goods(self, wo_list, default_warehouses):
		items_data = self.get_production_items()

		groups: dict[str, list[dict]] = {}
		passthrough: list[dict] = []
		for item in items_data.values():
			template = frappe.get_cached_value("Item", item["production_item"], "variant_of")
			if template:
				groups.setdefault(template, []).append(item)
			else:
				passthrough.append(item)

		for template, items in list(groups.items()):
			if len(items) < 2:
				# A single variant of a style — stock behaviour.
				passthrough.extend(items)
				continue
			work_order = self.create_multi_variant_work_order(
				template, items, default_warehouses
			)
			if work_order:
				wo_list.append(work_order)

		# Stock path, verbatim, for everything ungrouped.
		for item in passthrough:
			if self.sub_assembly_items:
				item["use_multi_level_bom"] = 0
			set_default_warehouses(item, default_warehouses)
			work_order = self.create_work_order(item)
			if work_order:
				wo_list.append(work_order)

	def create_multi_variant_work_order(self, template, items, default_warehouses):
		"""One route-header Work Order for all variants of `template`."""
		total_qty = sum(flt(d.get("qty")) for d in items)
		if total_qty <= 0:
			return None

		first = items[0]
		wo = frappe.new_doc("Work Order")
		wo.company = self.company
		wo.production_item = template
		wo.custom_is_multi_variant = 1
		wo.qty = total_qty
		wo.stock_uom = frappe.get_cached_value("Item", template, "stock_uom")
		wo.use_multi_level_bom = 0
		wo.transfer_material_against = "Work Order"
		wo.production_plan = self.name
		# Work Order carries a single production_plan_item link — anchor
		# it on the first row; per-row produced tracking is distributed
		# variant-wise by MultiVariantWorkOrder.update_production_plan_status.
		wo.production_plan_item = first.get("production_plan_item")
		wo.planned_start_date = first.get("planned_start_date") or self.posting_date
		wo.project = first.get("project") or self.project

		warehouse_seed = dict(first)
		set_default_warehouses(warehouse_seed, default_warehouses)
		wo.fg_warehouse = first.get("fg_warehouse") or warehouse_seed.get("fg_warehouse")
		wo.wip_warehouse = warehouse_seed.get("wip_warehouse")
		wo.scrap_warehouse = warehouse_seed.get("scrap_warehouse")
		if not wo.source_warehouse:
			wo.source_warehouse = wo.fg_warehouse

		for d in items:
			wo.append(
				"custom_variants",
				{"item_code": d["production_item"], "qty": flt(d.get("qty"))},
			)

		self._apply_style_recipe(wo, template, total_qty)

		wo.reserve_stock = self.reserve_stock
		try:
			# ignore_mandatory only — stock's create_work_order parity. NOT
			# ignore_validate: that would skip validate() wholesale, and with
			# it _validate_variants (duplicate rows, qty <= 0) and the qty
			# roll-up. A bad grouping would then sit silently in the draft
			# until the planner hit Submit. Verified 2026-07-23 that the
			# insert succeeds on the client's own plan without the flag.
			wo.flags.ignore_mandatory = True
			wo.insert()
			return wo.name
		except Exception:
			frappe.log_error(
				title="Panchhi PP multi-variant WO", message=frappe.get_traceback()
			)
			frappe.throw(
				_("Could not create the multi-variant Work Order for style {0} — see Error Log.").format(
					template
				)
			)

	def _apply_style_recipe(self, wo, template, total_qty):
		"""Prefill operations + materials when a Style Recipe exists.
		Optional by design (FRD C-07) — silently skipped otherwise."""
		if not frappe.db.exists(
			"Style Recipe", {"style_item": template, "enabled": 1}
		):
			return
		from panchhi_manufacturing.panchhi_manufacturing.doctype.style_recipe.style_recipe import (
			get_recipe_details,
		)

		details = get_recipe_details(template, qty=total_qty)
		for op in details["operations"]:
			wo.append("operations", op)
		for rm in details["required_items"]:
			wo.append("required_items", rm)
