"""Phase 2 — Style Recipe (C-07) + replacement reports (C-10)."""
import frappe
from frappe.tests import IntegrationTestCase


def _make_items(suffix):
	made = []
	hsn = frappe.db.get_value("GST HSN Code", {}, "name")
	for code in (f"PMR-STYLE-{suffix}", f"PMR-SFG-{suffix}", f"PMR-RAW-{suffix}"):
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": code,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"valuation_rate": 5,
					"gst_hsn_code": hsn,
				}
			).insert(ignore_permissions=True)
		made.append(code)
	return made


class TestPhase2RecipeAndReports(IntegrationTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.suffix = frappe.generate_hash(length=6).upper()
		cls.style, cls.sfg, cls.raw = _make_items(cls.suffix)
		if not frappe.db.exists("Operation", "PMR Op A"):
			frappe.get_doc({"doctype": "Operation", "name": "PMR Op A"}).insert(
				ignore_permissions=True
			)
		if not frappe.db.exists("Operation", "PMR Op B"):
			frappe.get_doc({"doctype": "Operation", "name": "PMR Op B"}).insert(
				ignore_permissions=True
			)

	def _recipe(self, **overrides):
		doc = frappe.get_doc(
			{
				"doctype": "Style Recipe",
				"recipe_name": f"PMR Recipe {self.suffix}-{frappe.generate_hash(length=4)}",
				"style_item": self.style,
				"operations": [
					{"operation": "PMR Op A", "time_in_mins": 30, "finished_good": self.sfg},
					{"operation": "PMR Op B", "time_in_mins": 15},
				],
				"materials": [
					{"operation": "PMR Op A", "item_code": self.raw, "qty_per_unit": 2},
				],
				**overrides,
			}
		)
		return doc

	# ---- C-07 validation --------------------------------------------
	def test_recipe_inserts_and_get_recipe_details_scales(self):
		self._recipe().insert(ignore_permissions=True)
		from panchhi_manufacturing.panchhi_manufacturing.doctype.style_recipe.style_recipe import (
			get_recipe_details,
		)

		out = get_recipe_details(self.style, qty=18)
		self.assertEqual(len(out["operations"]), 2)
		self.assertEqual(out["operations"][0]["finished_good"], self.sfg)
		self.assertEqual(out["required_items"][0]["required_qty"], 36)  # 2/unit x 18
		self.assertIsInstance(out["variants"], list)

	def test_recipe_rejects_material_for_unknown_operation(self):
		doc = self._recipe(
			materials=[{"operation": "PMR Op B", "item_code": self.raw, "qty_per_unit": 1}]
		)
		doc.materials[0].operation = "PMR Op A"  # valid; now break it
		doc.materials[0].operation = "Folding"  # an op not in the table
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_recipe_requires_sfg_on_non_last_operations(self):
		doc = self._recipe()
		doc.operations[0].finished_good = None  # first of two — must throw
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_get_recipe_details_throws_when_no_recipe(self):
		from panchhi_manufacturing.panchhi_manufacturing.doctype.style_recipe.style_recipe import (
			get_recipe_details,
		)

		with self.assertRaises(frappe.ValidationError):
			get_recipe_details(self.raw)  # no recipe for the raw item

	# ---- C-10 reports smoke -----------------------------------------
	def test_reports_registered_and_execute(self):
		from panchhi_manufacturing.panchhi_manufacturing.report.actual_cost_by_style import (
			actual_cost_by_style,
		)
		from panchhi_manufacturing.panchhi_manufacturing.report.goods_with_job_workers import (
			goods_with_job_workers,
		)
		from panchhi_manufacturing.panchhi_manufacturing.report.style_progress import (
			style_progress,
		)
		from panchhi_manufacturing.panchhi_manufacturing.report.variant_work_order_summary import (
			variant_work_order_summary,
		)

		for report in (
			"Variant Work Order Summary",
			"Style Progress",
			"Goods with Job Workers",
			"Actual Cost by Style",
		):
			self.assertTrue(frappe.db.exists("Report", report), f"{report} not registered")

		for mod in (
			variant_work_order_summary,
			style_progress,
			goods_with_job_workers,
			actual_cost_by_style,
		):
			cols, rows = mod.execute({})
			self.assertTrue(len(cols) >= 4)
			self.assertIsInstance(rows, list)
