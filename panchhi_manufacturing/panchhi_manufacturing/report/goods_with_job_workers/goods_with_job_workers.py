"""C-10 — Goods with Job Workers.

Current stock resting in job-worker warehouses (your material, their
premises — the Rule 45 exposure). Defaults to every warehouse whose
name contains 'job work'; pass a warehouse filter to scope to one
job worker's subtree.
"""
import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{"label": _("Warehouse (Job Worker)"), "fieldname": "warehouse", "fieldtype": "Link",
		 "options": "Warehouse", "width": 220},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link",
		 "options": "Item", "width": 200},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{"label": _("Qty"), "fieldname": "actual_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Stock UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 90},
		{"label": _("Value"), "fieldname": "stock_value", "fieldtype": "Currency", "width": 130},
	]

	if filters.get("warehouse"):
		lft, rgt = frappe.db.get_value("Warehouse", filters.warehouse, ["lft", "rgt"])
		warehouses = frappe.get_all(
			"Warehouse", filters={"lft": (">=", lft), "rgt": ("<=", rgt), "is_group": 0},
			pluck="name",
		)
	else:
		warehouses = frappe.get_all(
			"Warehouse",
			filters={"warehouse_name": ("like", "%job work%"), "is_group": 0},
			pluck="name",
		)
	if not warehouses:
		return columns, []

	rows = frappe.db.sql(
		"""
		SELECT b.warehouse, b.item_code, i.item_name, i.stock_uom,
		       b.actual_qty, b.stock_value
		FROM `tabBin` b
		JOIN `tabItem` i ON i.name = b.item_code
		WHERE b.warehouse IN %(whs)s AND b.actual_qty != 0
		ORDER BY b.warehouse, b.item_code
		""",
		{"whs": tuple(warehouses)},
		as_dict=True,
	)
	total = {"warehouse": _("TOTAL"), "stock_value": sum(flt(r.stock_value) for r in rows)}
	if rows:
		rows.append(total)
	return columns, rows
