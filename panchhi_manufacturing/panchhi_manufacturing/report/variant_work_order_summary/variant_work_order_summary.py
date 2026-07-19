"""C-10 — Variant Work Order Summary.

Replacement for the stock Work Order Summary, which shows only the
style template on multi-variant Work Orders. One row per WO variant.
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
		 "options": "Item", "width": 150},
		{"label": _("Variant"), "fieldname": "variant", "fieldtype": "Link",
		 "options": "Item", "width": 180},
		{"label": _("Planned Qty"), "fieldname": "planned_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Produced Qty"), "fieldname": "produced_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 110},
		{"label": _("% Complete"), "fieldname": "pct", "fieldtype": "Percent", "width": 100},
		{"label": _("WO Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Planned Start"), "fieldname": "planned_start_date", "fieldtype": "Date", "width": 110},
	]

	conditions = ["wo.custom_is_multi_variant = 1", "wo.docstatus < 2"]
	params = {}
	if filters.get("company"):
		conditions.append("wo.company = %(company)s")
		params["company"] = filters.company
	if filters.get("status"):
		conditions.append("wo.status = %(status)s")
		params["status"] = filters.status
	if filters.get("style"):
		conditions.append("wo.production_item = %(style)s")
		params["style"] = filters.style
	if filters.get("from_date"):
		conditions.append("wo.planned_start_date >= %(from_date)s")
		params["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("wo.planned_start_date <= %(to_date)s")
		params["to_date"] = filters.to_date

	rows = frappe.db.sql(
		f"""
		SELECT wo.name AS work_order, wo.production_item AS style,
		       wo.status, wo.planned_start_date,
		       v.item_code AS variant, v.qty AS planned_qty,
		       v.produced_qty
		FROM `tabWork Order` wo
		JOIN `tabPanchhi WO Variant` v ON v.parent = wo.name
		WHERE {' AND '.join(conditions)}
		ORDER BY wo.planned_start_date DESC, wo.name, v.idx
		""",
		params,
		as_dict=True,
	)
	for r in rows:
		r["pending_qty"] = max(0.0, flt(r.planned_qty) - flt(r.produced_qty))
		r["pct"] = (flt(r.produced_qty) / flt(r.planned_qty) * 100) if flt(r.planned_qty) else 0
	return columns, rows
