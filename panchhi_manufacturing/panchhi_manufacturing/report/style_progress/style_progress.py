"""C-10 — Style Progress.

One row per operation of every open multi-variant Work Order: how it
executes (Job Card vs Subcontracting Order), what has completed, and
whether the operation's SFG receipt actually posted (the R-05 audit —
a completed Job Card with no receipt Stock Entry is a defect).
"""
import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{"label": _("Work Order"), "fieldname": "work_order", "fieldtype": "Link",
		 "options": "Work Order", "width": 160},
		{"label": _("Style"), "fieldname": "style", "fieldtype": "Link",
		 "options": "Item", "width": 140},
		{"label": _("Operation"), "fieldname": "operation", "fieldtype": "Data", "width": 130},
		{"label": _("Mode"), "fieldname": "mode", "fieldtype": "Data", "width": 110},
		{"label": _("Output SFG"), "fieldname": "finished_good", "fieldtype": "Link",
		 "options": "Item", "width": 160},
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link",
		 "options": "doc_type", "width": 160},
		{"label": _("Doc Type"), "fieldname": "doc_type", "fieldtype": "Data", "hidden": 1},
		{"label": _("Completed Qty"), "fieldname": "completed_qty", "fieldtype": "Float", "width": 110},
		{"label": _("SFG Receipt"), "fieldname": "sfg_receipt", "fieldtype": "Link",
		 "options": "Stock Entry", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]

	wo_filters = {"custom_is_multi_variant": 1, "docstatus": 1}
	if filters.get("company"):
		wo_filters["company"] = filters.company
	if filters.get("work_order"):
		wo_filters["name"] = filters.work_order
	if not filters.get("include_completed"):
		wo_filters["status"] = ("!=", "Completed")

	rows = []
	for wo in frappe.get_all(
		"Work Order", filters=wo_filters, fields=["name", "production_item"],
		order_by="planned_start_date desc", limit=500,
	):
		ops = frappe.get_all(
			"Work Order Operation",
			filters={"parent": wo.name},
			fields=["name", "operation", "is_subcontracted", "finished_good"],
			order_by="idx",
		)
		for op in ops:
			row = {
				"work_order": wo.name,
				"style": wo.production_item,
				"operation": op.operation,
				"finished_good": op.finished_good,
				"mode": _("Subcontract") if op.is_subcontracted else _("In-house"),
			}
			if op.is_subcontracted:
				sco = frappe.db.get_value(
					"Subcontracting Order",
					{"custom_work_order": wo.name, "custom_operation": op.name, "docstatus": ("<", 2)},
					["name", "status"],
					as_dict=True,
				)
				row.update(
					{
						"doc_type": "Subcontracting Order",
						"document": sco and sco.name,
						"status": (sco and sco.status) or _("Not Created"),
					}
				)
			else:
				jc = frappe.db.get_value(
					"Job Card",
					{"work_order": wo.name, "operation": op.operation, "docstatus": ("<", 2)},
					["name", "status", "total_completed_qty", "custom_sfg_stock_entry"],
					as_dict=True,
					order_by="creation desc",
				)
				row.update(
					{
						"doc_type": "Job Card",
						"document": jc and jc.name,
						"completed_qty": flt(jc and jc.total_completed_qty),
						"sfg_receipt": jc and jc.custom_sfg_stock_entry,
						"status": (jc and jc.status) or _("Not Created"),
					}
				)
				# R-05 audit: completed but nothing received.
				if jc and jc.status == "Completed" and not jc.custom_sfg_stock_entry:
					row["status"] = _("Completed — NO SFG RECEIPT")
			rows.append(row)
	return columns, rows
