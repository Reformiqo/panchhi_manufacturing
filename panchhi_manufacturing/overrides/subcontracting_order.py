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
def make_subcontracting_order(work_order: str, operation_row: str, service_item: str, supplier: str | None = None, service_rate: float = 0):
	"""Create a DRAFT WO-driven SCO for one subcontracted operation.

	`operation_row` is the Work Order Operation child-row name;
	`service_item` is the job-work service (e.g. 'Stitching Charges').
	"""
	wo = frappe.get_doc("Work Order", work_order)
	op = next((o for o in wo.operations if o.name == operation_row), None)
	if not op:
		frappe.throw(_("Operation row {0} not found on Work Order {1}").format(operation_row, work_order))
	if not op.finished_good:
		frappe.throw(
			_("Operation {0} has no Semi Finished Good to receive — set it on the Work Order first.").format(op.operation)
		)

	sco = frappe.new_doc("Subcontracting Order")
	sco.company = wo.company
	sco.custom_work_order = wo.name
	sco.custom_operation = op.name
	sco.supplier = supplier
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
			"warehouse": op.fg_warehouse or wo.wip_warehouse,
		},
	)
	sco.flags.ignore_permissions = True
	sco.insert()
	return sco.name
