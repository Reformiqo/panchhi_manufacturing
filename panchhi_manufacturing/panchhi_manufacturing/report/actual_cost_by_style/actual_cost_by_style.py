"""C-10 — Actual Cost by Style.

Absorbed actual cost per variant of every multi-variant Work Order,
read from the stock ledger: the incoming value of each finished row
(materials consumed + operating cost + job-work charges, because every
operation's Stock Entry / Subcontracting Receipt rolls its cost into
the next SFG's valuation — the Operation-as-Transaction promise).
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
		{"label": _("Produced Qty"), "fieldname": "produced_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Absorbed Cost"), "fieldname": "absorbed_cost", "fieldtype": "Currency", "width": 140},
		{"label": _("Cost / Unit"), "fieldname": "unit_cost", "fieldtype": "Currency", "width": 120},
	]

	wo_conditions = ["wo.custom_is_multi_variant = 1", "wo.docstatus = 1"]
	params = {}
	if filters.get("company"):
		wo_conditions.append("wo.company = %(company)s")
		params["company"] = filters.company
	if filters.get("work_order"):
		wo_conditions.append("wo.name = %(wo)s")
		params["wo"] = filters.work_order
	if filters.get("style"):
		wo_conditions.append("wo.production_item = %(style)s")
		params["style"] = filters.style

	rows = frappe.db.sql(
		f"""
		SELECT wo.name AS work_order, wo.production_item AS style,
		       v.item_code AS variant, v.produced_qty,
		       COALESCE(SUM(sle.stock_value_difference), 0) AS absorbed_cost
		FROM `tabWork Order` wo
		JOIN `tabPanchhi WO Variant` v ON v.parent = wo.name
		LEFT JOIN `tabStock Entry` se
		       ON se.work_order = wo.name AND se.docstatus = 1
		LEFT JOIN `tabStock Ledger Entry` sle
		       ON sle.voucher_type = 'Stock Entry'
		      AND sle.voucher_no = se.name
		      AND sle.item_code = v.item_code
		      AND sle.actual_qty > 0
		      AND sle.is_cancelled = 0
		WHERE {' AND '.join(wo_conditions)}
		GROUP BY wo.name, v.item_code
		ORDER BY wo.name, v.idx
		""",
		params,
		as_dict=True,
	)
	for r in rows:
		r["unit_cost"] = (flt(r.absorbed_cost) / flt(r.produced_qty)) if flt(r.produced_qty) else 0
	return columns, rows
