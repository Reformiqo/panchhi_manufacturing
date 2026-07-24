"""C-05 — Subcontracting Order driven from a Work Order operation, BOM-free.

The standard SCO explodes the FG item's BOM for Supplied Items and
demands a Purchase Order. When the SCO carries `custom_work_order` +
`custom_operation`, both requirements are bypassed: Supplied Items come
from THAT operation's inputs on the Work Order, and no PO is needed.
Standard subcontracting (no custom fields) is untouched — every
override starts with an early return.

Set Buying Settings → Backflush Raw Materials of Subcontract Based On =
"Material Transferred for Subcontract" so the Subcontracting Receipt
never consults a BOM either (FRD R-04 — validated here on submit).
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
	SubcontractingOrder,
)


class PanchhiSubcontractingOrder(SubcontractingOrder):
	def _wo_driven(self) -> bool:
		return bool(self.get("custom_work_order"))

	def validate_purchase_order_for_subcontracting(self):
		if self._wo_driven():
			return  # no PO behind a WO-driven SCO
		return super().validate_purchase_order_for_subcontracting()

	def validate_items(self):
		"""The stock controller demands each finished-good row be linked to
		a Purchase Order Item and caps its qty against the PO's pending
		subcontract qty. A WO-driven SCO has no PO, so those gates don't
		apply — keep the checks that DO matter (finished goods must be
		stock items AND subcontracted items) and set the row amount."""
		if not self._wo_driven():
			return super().validate_items()
		for item in self.items:
			is_stock_item, is_sub = frappe.get_value(
				"Item", item.item_code, ["is_stock_item", "is_sub_contracted_item"]
			)
			if not is_stock_item:
				frappe.throw(
					_("Row {0}: Item {1} must be a stock item.").format(item.idx, item.item_name)
				)
			if not item.get("is_scrap_item") and not is_sub:
				frappe.throw(
					_("Row {0}: Item {1} must be a subcontracted item.").format(
						item.idx, item.item_name
					)
				)
			item.amount = flt(item.qty) * flt(item.rate)

	def calculate_supplied_items_qty_and_amount(self):
		"""Stock derives each finished good's raw-material cost from its
		BOM. A WO-driven SCO has no BOM — derive it instead from the
		supplied items we mapped off the Work Order operation."""
		if not self._wo_driven():
			return super().calculate_supplied_items_qty_and_amount()
		rm_total = sum(flt(s.amount) for s in self.get("supplied_items") or [])
		for item in self.get("items"):
			item.rm_cost_per_qty = (
				flt(rm_total / flt(item.qty), item.precision("rm_cost_per_qty"))
				if flt(item.qty)
				else 0
			)

	def create_raw_materials_supplied_or_received(self, raw_material_table="supplied_items"):
		if self._wo_driven() and raw_material_table == "supplied_items":
			return self._set_supplied_items_from_work_order()
		return super().create_raw_materials_supplied_or_received(raw_material_table)

	def _set_supplied_items_from_work_order(self):
		"""Supplied Items = the linked operation's inputs on the Work
		Order (SFG from the previous operation + trims), NOT a BOM."""
		if self.get("supplied_items"):
			return  # user- or system-populated already; don't clobber
		wo = frappe.get_doc("Work Order", self.custom_work_order)
		op_row = None
		for op in wo.operations:
			if op.operation == self.custom_operation or op.name == self.custom_operation:
				op_row = op
				break
		for r in wo.required_items:
			belongs = (
				(op_row and r.get("operation_row_id") and str(r.operation_row_id) == str(op_row.idx))
				or (r.get("operation") and r.operation in (self.custom_operation, op_row and op_row.operation))
			)
			if not belongs:
				continue
			self.append(
				"supplied_items",
				{
					"main_item_code": (op_row and op_row.finished_good) or wo.production_item,
					"rm_item_code": r.item_code,
					"required_qty": flt(r.required_qty),
					"supplied_qty": 0,
					"rate": flt(r.rate),
					"amount": flt(r.rate) * flt(r.required_qty),
					"stock_uom": r.stock_uom,
					"reserve_warehouse": r.source_warehouse or wo.wip_warehouse,
				},
			)

	def on_submit(self):
		if self._wo_driven():
			self._validate_backflush_setting()
		super().on_submit()

	def _validate_backflush_setting(self):
		"""FRD R-04 — with Backflush = 'BOM', the Subcontracting Receipt
		goes looking for a BOM that does not exist and fails downstream.
		Fail HERE with a clear message instead."""
		backflush = frappe.db.get_single_value(
			"Buying Settings", "backflush_raw_materials_of_subcontract_based_on"
		)
		if backflush != "Material Transferred for Subcontract":
			frappe.throw(
				_(
					"Buying Settings → 'Backflush Raw Materials of Subcontract Based On' "
					"must be 'Material Transferred for Subcontract' for a BOM-less "
					"Work-Order-driven Subcontracting Order (currently: {0})."
				).format(backflush or _("not set")),
				title=_("Backflush Setting"),
			)


@frappe.whitelist()
def make_subcontracting_order(work_order: str, operation_row: str, service_item: str, supplier: str | None = None, service_rate: float = 0, supplier_warehouse: str | None = None):
	"""Create a DRAFT WO-driven SCO for one subcontracted operation.

	`operation_row` is the Work Order Operation child-row name;
	`service_item` is the job-work service (e.g. 'Stitching Charges').
	`supplier_warehouse` is the job worker's warehouse — it MUST differ
	from the warehouse the raw materials are reserved in, or ERPNext
	rejects the Send-to-Subcontractor transfer.
	"""
	wo = frappe.get_doc("Work Order", work_order)
	op = next((o for o in wo.operations if o.name == operation_row), None)
	if not op:
		frappe.throw(_("Operation row {0} not found on Work Order {1}").format(operation_row, work_order))
	if not op.finished_good:
		frappe.throw(
			_("Operation {0} has no Semi Finished Good to receive — set it on the Work Order first.").format(op.operation)
		)
	if not frappe.db.get_value("Item", op.finished_good, "is_sub_contracted_item"):
		frappe.throw(
			_(
				"Semi Finished Good {0} must have 'Is Sub-contracted Item' ticked on its "
				"Item master before it can be received from a job worker on a Subcontracting "
				"Order. Enable it on the Item first."
			).format(op.finished_good),
			title=_("Item Not Subcontract-Enabled"),
		)

	reserve_warehouse = op.wip_warehouse or wo.wip_warehouse
	supplier_warehouse = (
		supplier_warehouse
		or frappe.db.get_value("Buying Settings", None, "supplier_warehouse")
		or frappe.db.get_value("Warehouse", {"company": wo.company, "warehouse_name": "Goods In Transit"}, "name")
	)
	if not supplier_warehouse or supplier_warehouse == reserve_warehouse:
		frappe.throw(
			_(
				"A Supplier (job worker) Warehouse distinct from the WIP warehouse {0} is "
				"required to dispatch raw materials on a Subcontracting Order. Pass one, or "
				"set a default Supplier Warehouse in Buying Settings."
			).format(reserve_warehouse),
			title=_("Supplier Warehouse Required"),
		)
	cost_center = frappe.db.get_value("Company", wo.company, "cost_center")
	if wo.get("project"):
		cost_center = frappe.db.get_value("Project", wo.project, "cost_center") or cost_center
	fg_uom = frappe.db.get_value("Item", op.finished_good, "stock_uom")

	sco = frappe.new_doc("Subcontracting Order")
	sco.company = wo.company
	sco.custom_work_order = wo.name
	sco.custom_operation = op.name
	sco.supplier = supplier
	sco.supplier_warehouse = supplier_warehouse
	sco.cost_center = cost_center
	sco.transaction_date = frappe.utils.today()
	qty = flt(wo.qty)
	sco.append(
		"service_items",
		{
			"item_code": service_item,
			"item_name": frappe.db.get_value("Item", service_item, "item_name"),
			"qty": qty,
			"rate": flt(service_rate),
			"amount": qty * flt(service_rate),
			"fg_item": op.finished_good,
			"fg_item_qty": qty,
		},
	)
	sco.append(
		"items",
		{
			"item_code": op.finished_good,
			"item_name": frappe.db.get_value("Item", op.finished_good, "item_name"),
			"qty": qty,
			"rate": flt(service_rate),
			"stock_uom": fg_uom,
			"warehouse": op.fg_warehouse or wo.wip_warehouse,
			"cost_center": cost_center,
			# Core validate_service_items does qty * subcontracting_conversion_factor;
			# without an explicit factor it is None → TypeError.
			"conversion_factor": 1,
			"subcontracting_conversion_factor": 1,
		},
	)
	sco.flags.ignore_permissions = True
	sco.insert()
	return sco.name
