"""Phase 4 — Production Plan → ONE multi-variant Work Order.

Replicates the client's exact scenario (MFG-PP-2026-00002): a plan whose
assembly rows are three colour variants of one style template must create
a single Work Order with all three in its Variants table — plus gating
(non-variant rows keep stock behaviour) and per-variant produced-qty
sync back to the plan rows.
"""
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate


class TestPhase4ProductionPlan(IntegrationTestCase):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.run_id = frappe.generate_hash(length=6).upper()
		cls.company = "Panchhi Fashion"
		cls.abbr = frappe.db.get_value("Company", cls.company, "abbr")
		cls.fg_wh = f"PMTEST-FG - {cls.abbr}"
		cls.wip_wh = f"PMTEST-WIP - {cls.abbr}"
		hsn = frappe.db.get_value("GST HSN Code", {}, "name")

		root_wh = frappe.db.get_value(
			"Warehouse", {"company": cls.company, "is_group": 1, "parent_warehouse": ""}, "name"
		) or frappe.db.get_value("Warehouse", {"company": cls.company, "is_group": 1}, "name")
		for wh_name, full in (("PMTEST-FG", cls.fg_wh), ("PMTEST-WIP", cls.wip_wh)):
			if not frappe.db.exists("Warehouse", full):
				frappe.get_doc({
					"doctype": "Warehouse", "warehouse_name": wh_name,
					"company": cls.company, "parent_warehouse": root_wh,
				}).insert(ignore_permissions=True)

		def make_item(code, **kw):
			if not frappe.db.exists("Item", code):
				frappe.get_doc({
					"doctype": "Item", "item_code": code, "item_name": code,
					"item_group": "All Item Groups", "stock_uom": "Nos",
					"is_stock_item": 1, "valuation_rate": 5, "gst_hsn_code": hsn,
					"default_material_request_type": "Manufacture", **kw,
				}).insert(ignore_permissions=True)
			return code

		# Style template + attribute so real ERPNext variants can hang off it
		attr = "PM4 Colour"
		if not frappe.db.exists("Item Attribute", attr):
			frappe.get_doc({
				"doctype": "Item Attribute", "attribute_name": attr,
				"item_attribute_values": [
					{"attribute_value": c, "abbr": c[:3].upper() + cls.run_id}
					for c in ("Cream", "Sky", "Rose")
				],
			}).insert(ignore_permissions=True)
		cls.template = make_item(
			f"PM4-STYLE-{cls.run_id}", has_variants=1,
			attributes=[{"attribute": attr}],
		)
		cls.variants = []
		for colour in ("Cream", "Sky", "Rose"):
			code = f"PM4-STYLE-{cls.run_id}-{colour}"
			if not frappe.db.exists("Item", code):
				frappe.get_doc({
					"doctype": "Item", "item_code": code, "item_name": code,
					"item_group": "All Item Groups", "stock_uom": "Nos",
					"is_stock_item": 1, "valuation_rate": 5, "gst_hsn_code": hsn,
					"variant_of": cls.template,
					"attributes": [{"attribute": attr, "attribute_value": colour}],
				}).insert(ignore_permissions=True)
			cls.variants.append(code)

		cls.raw = make_item(f"PM4-RAW-{cls.run_id}")
		cls.plain = make_item(f"PM4-PLAIN-{cls.run_id}")  # NOT a variant

		def make_bom(item):
			existing = frappe.db.get_value("BOM", {"item": item, "docstatus": 1}, "name")
			if existing:
				return existing
			bom = frappe.get_doc({
				"doctype": "BOM", "item": item, "quantity": 1, "company": cls.company,
				"items": [{"item_code": cls.raw, "qty": 1, "rate": 5}],
			})
			bom.insert(ignore_permissions=True)
			bom.submit()
			return bom.name

		cls.boms = {v: make_bom(v) for v in cls.variants}
		cls.plain_bom = make_bom(cls.plain)

	def _make_plan(self, rows):
		plan = frappe.get_doc({
			"doctype": "Production Plan",
			"company": self.company,
			"posting_date": nowdate(),
			"po_items": [
				{
					"item_code": code, "bom_no": bom, "planned_qty": qty,
					"warehouse": self.fg_wh, "planned_start_date": nowdate(),
					"stock_uom": "Nos",
				}
				for code, bom, qty in rows
			],
		})
		plan.insert(ignore_permissions=True)
		plan.submit()
		self.addCleanup(self._cleanup_plan, plan.name)
		return plan

	def _cleanup_plan(self, name):
		try:
			for wo in frappe.get_all("Work Order", filters={"production_plan": name}, pluck="name"):
				doc = frappe.get_doc("Work Order", wo)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc("Work Order", wo, force=True)
			plan = frappe.get_doc("Production Plan", name)
			if plan.docstatus == 1:
				plan.cancel()
			frappe.db.commit()
		except Exception:
			pass

	# ------------------------------------------------------------------
	# THE requirement — one WO for all variants of one style
	# ------------------------------------------------------------------
	def test_three_variant_rows_make_one_work_order(self):
		plan = self._make_plan([
			(self.variants[0], self.boms[self.variants[0]], 1),
			(self.variants[1], self.boms[self.variants[1]], 1),
			(self.variants[2], self.boms[self.variants[2]], 1),
		])
		plan.make_work_order()

		wos = frappe.get_all(
			"Work Order", filters={"production_plan": plan.name},
			fields=["name", "production_item", "qty", "custom_is_multi_variant"],
		)
		self.assertEqual(len(wos), 1, "exactly ONE Work Order must be created")
		wo = frappe.get_doc("Work Order", wos[0].name)
		self.assertTrue(wo.custom_is_multi_variant)
		self.assertEqual(wo.production_item, self.template)
		self.assertEqual(flt(wo.qty), 3)
		self.assertEqual(
			{d.item_code: flt(d.qty) for d in wo.custom_variants},
			{v: 1.0 for v in self.variants},
		)
		self.assertEqual(wo.production_plan, plan.name)

	# ------------------------------------------------------------------
	# Gating — non-variant items keep stock behaviour
	# ------------------------------------------------------------------
	def test_non_variant_rows_keep_stock_behaviour(self):
		plan = self._make_plan([(self.plain, self.plain_bom, 2)])
		plan.make_work_order()
		wos = frappe.get_all(
			"Work Order", filters={"production_plan": plan.name},
			fields=["production_item", "custom_is_multi_variant", "bom_no"],
		)
		self.assertEqual(len(wos), 1)
		self.assertEqual(wos[0].production_item, self.plain)
		self.assertFalse(wos[0].custom_is_multi_variant)
		self.assertEqual(wos[0].bom_no, self.plain_bom)  # stock path kept the BOM

	def test_single_variant_row_keeps_stock_behaviour(self):
		plan = self._make_plan([(self.variants[0], self.boms[self.variants[0]], 2)])
		plan.make_work_order()
		wos = frappe.get_all(
			"Work Order", filters={"production_plan": plan.name},
			fields=["production_item", "custom_is_multi_variant"],
		)
		self.assertEqual(len(wos), 1)
		self.assertEqual(wos[0].production_item, self.variants[0])
		self.assertFalse(wos[0].custom_is_multi_variant)

	def test_mixed_plan_groups_variants_and_passes_through_the_rest(self):
		plan = self._make_plan([
			(self.variants[0], self.boms[self.variants[0]], 1),
			(self.variants[1], self.boms[self.variants[1]], 1),
			(self.plain, self.plain_bom, 2),
		])
		plan.make_work_order()
		wos = frappe.get_all(
			"Work Order", filters={"production_plan": plan.name},
			fields=["production_item", "custom_is_multi_variant"],
		)
		self.assertEqual(len(wos), 2)
		by_item = {w.production_item: w for w in wos}
		self.assertIn(self.template, by_item)
		self.assertTrue(by_item[self.template].custom_is_multi_variant)
		self.assertIn(self.plain, by_item)
		self.assertFalse(by_item[self.plain].custom_is_multi_variant)

	# ------------------------------------------------------------------
	# Produced-qty sync — plan rows track their own variant
	# ------------------------------------------------------------------
	def test_variant_production_updates_matching_plan_rows(self):
		"""Produce two of the three variants via REAL Manufacture Stock
		Entries and assert each plan row tracks its own variant."""
		plan = self._make_plan([
			(self.variants[0], self.boms[self.variants[0]], 2),
			(self.variants[1], self.boms[self.variants[1]], 1),
			(self.variants[2], self.boms[self.variants[2]], 1),
		])
		plan.make_work_order()
		wo_name = frappe.get_all(
			"Work Order", filters={"production_plan": plan.name}, pluck="name"
		)[0]
		wo = frappe.get_doc("Work Order", wo_name)
		wo.skip_transfer = 1
		wo.wip_warehouse = self.wip_wh
		wo.fg_warehouse = self.fg_wh
		wo.flags.ignore_validate_update_after_submit = True
		wo.submit()

		# Raw stock so the Manufacture entry can consume something.
		receipt = frappe.get_doc({
			"doctype": "Stock Entry", "purpose": "Material Receipt",
			"stock_entry_type": "Material Receipt", "company": self.company,
			"items": [{"item_code": self.raw, "qty": 50, "t_warehouse": self.wip_wh, "basic_rate": 5}],
		})
		receipt.insert(ignore_permissions=True)
		receipt.submit()

		se = frappe.get_doc({
			"doctype": "Stock Entry", "purpose": "Manufacture",
			"stock_entry_type": "Manufacture", "company": self.company,
			"work_order": wo.name,
			"items": [
				{"item_code": self.raw, "qty": 3, "s_warehouse": self.wip_wh},
				{"item_code": self.variants[0], "qty": 2, "t_warehouse": self.fg_wh, "is_finished_item": 1},
				{"item_code": self.variants[1], "qty": 1, "t_warehouse": self.fg_wh, "is_finished_item": 1},
			],
		})
		se.insert(ignore_permissions=True)
		se.submit()

		plan.reload()
		produced = {d.item_code: flt(d.produced_qty) for d in plan.po_items}
		self.assertEqual(produced[self.variants[0]], 2)
		self.assertEqual(produced[self.variants[1]], 1)
		self.assertEqual(produced[self.variants[2]], 0)
		pending = {d.item_code: flt(d.pending_qty) for d in plan.po_items}
		self.assertEqual(pending[self.variants[0]], 0)
		self.assertEqual(pending[self.variants[2]], 1)

		# IDEMPOTENCE: produced qty is DERIVED from the ledger, so
		# re-running the recompute (repost, amend, duplicate hook) must
		# not double-count.
		wo.reload()
		wo.update_variant_produced_qty()
		wo.reload()
		self.assertEqual(
			{d.item_code: flt(d.produced_qty) for d in wo.custom_variants},
			{self.variants[0]: 2.0, self.variants[1]: 1.0, self.variants[2]: 0.0},
			"recompute must be idempotent, never incremental",
		)
		self.assertEqual(flt(wo.produced_qty), 3)

		# CANCEL unwinds — same derived path, no sign juggling.
		se.cancel()
		wo.reload()
		self.assertEqual(flt(wo.produced_qty), 0)
		plan.reload()
		self.assertEqual(
			{d.item_code: flt(d.produced_qty) for d in plan.po_items},
			{v: 0.0 for v in self.variants},
		)
