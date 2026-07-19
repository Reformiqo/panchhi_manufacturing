"""C-03 / C-04 — Multi-item Job Card + operation-level SFG receipt.

THE KEY BUILD (FRD sheet 1, req #3): on Job Card submit, auto-post a
Manufacture Stock Entry that consumes THIS operation's inputs from WIP
and receives THIS operation's output into stock, one finished row per
variant, carrying the operation's actual operating cost. Every
operation becomes a costing boundary; the Work Order never holds cost.

Gated on `custom_is_multi_variant` (fetched from the Work Order).
Additive design (FRD sheet A): the standard `total_completed_qty` is
populated from the variant roll-up so stock Job Card reporting keeps
working.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.manufacturing.doctype.job_card.job_card import JobCard


class MultiItemJobCard(JobCard):
	# ------------------------------------------------------------------
	# C-03 — roll-up engine
	# ------------------------------------------------------------------
	def validate(self):
		super().validate()
		if cint(self.custom_is_multi_variant):
			self._roll_up_variant_items()

	def _roll_up_variant_items(self):
		total = 0.0
		for d in self.get("custom_items") or []:
			d.pending_qty = max(
				0.0, flt(d.planned_qty) - flt(d.completed_qty) - flt(d.rejected_qty)
			)
			if flt(d.completed_qty) <= 0:
				d.status = "Pending"
			elif d.pending_qty > 0:
				d.status = "In Process"
			else:
				d.status = "Completed"
			total += flt(d.completed_qty)
		# Standard field ← variant roll-up, so stock reports stay right.
		if self.get("custom_items"):
			self.total_completed_qty = flt(total, self.precision("total_completed_qty"))

	# ------------------------------------------------------------------
	# C-04 — SFG receipt on completion
	# ------------------------------------------------------------------
	def on_submit(self):
		super().on_submit()
		if cint(self.custom_is_multi_variant) and not self.get("custom_sfg_stock_entry"):
			self._post_sfg_receipt()

	def on_cancel(self):
		se_name = self.get("custom_sfg_stock_entry")
		if se_name and frappe.db.get_value("Stock Entry", se_name, "docstatus") == 1:
			frappe.get_doc("Stock Entry", se_name).cancel()
			self.db_set("custom_sfg_stock_entry", None, update_modified=False)
		super().on_cancel()

	def _post_sfg_receipt(self):
		completed_rows = [
			d for d in (self.get("custom_items") or []) if flt(d.completed_qty) > 0
		]
		if not completed_rows:
			frappe.throw(
				_(
					"No variant row has a Completed Qty — nothing to receive into stock. "
					"Enter completed quantities in the Variant Items table before submitting."
				),
				title=_("Nothing To Receive"),
			)

		wo = frappe.get_doc("Work Order", self.work_order)
		op_row = self._matching_operation_row(wo)

		se = frappe.new_doc("Stock Entry")
		se.purpose = se.stock_entry_type = "Manufacture"
		se.company = wo.company
		se.work_order = wo.name
		se.remarks = _("SFG receipt for Job Card {0} (Operation: {1})").format(
			self.name, self.operation
		)

		wip = self.wip_warehouse or (op_row and op_row.wip_warehouse) or wo.wip_warehouse
		# Output lands where the operation row says; else the WO's FG store.
		# (Must differ from the consumption warehouse — ERPNext rejects
		# same source+target per row once it back-fills defaults.)
		target = (op_row and op_row.fg_warehouse) or wo.fg_warehouse or wip

		total_completed = sum(flt(d.completed_qty) for d in completed_rows)
		self._append_consumption_rows(se, wo, op_row, wip, total_completed)

		for d in completed_rows:
			output_item = d.output_item or self._resolve_output_item(op_row, d.item_code)
			se.append(
				"items",
				{
					"item_code": output_item,
					"qty": flt(d.completed_qty),
					"t_warehouse": target,
					"is_finished_item": 1,
					"uom": frappe.db.get_value("Item", output_item, "stock_uom"),
				},
			)

		se.fg_completed_qty = total_completed
		self._append_operating_cost(se)

		se.flags.ignore_permissions = True
		se.insert()
		se.submit()
		self.db_set("custom_sfg_stock_entry", se.name, update_modified=False)
		frappe.msgprint(
			_("Stock Entry {0} posted — operation output received into {1}.").format(
				frappe.utils.get_link_to_form("Stock Entry", se.name), target
			),
			alert=True,
			indicator="green",
		)

	def _matching_operation_row(self, wo):
		for op in wo.operations:
			if self.get("operation_id") and op.name == self.operation_id:
				return op
		for op in wo.operations:
			if op.operation == self.operation:
				return op
		return None

	def _append_consumption_rows(self, se, wo, op_row, wip, total_completed):
		"""Consume this operation's share of required items, proportional
		to the quantity completed on this Job Card."""
		share = (flt(total_completed) / flt(wo.qty)) if flt(wo.qty) else 0
		for r in wo.required_items:
			belongs = (
				(op_row and r.get("operation_row_id") and str(r.operation_row_id) == str(op_row.idx))
				or (r.get("operation") and r.operation == self.operation)
			)
			if not belongs:
				continue
			qty = flt(r.required_qty) * share
			if qty <= 0:
				continue
			se.append(
				"items",
				{
					"item_code": r.item_code,
					"qty": qty,
					"s_warehouse": wip or r.source_warehouse,
					"uom": r.stock_uom,
				},
			)

	def _resolve_output_item(self, op_row, variant_item: str) -> str:
		"""Which item does THIS variant become at THIS operation?

		1. Operation has no finished_good (last operation) → the variant
		   itself is the output.
		2. finished_good is a template → the matching variant of that
		   template (same attribute values as the WO variant).
		3. finished_good is a plain item → use it as-is.
		"""
		op_fg = op_row and op_row.finished_good
		if not op_fg:
			return variant_item
		if not frappe.db.get_value("Item", op_fg, "has_variants"):
			return op_fg

		wanted = {
			a.attribute: a.attribute_value
			for a in frappe.get_all(
				"Item Variant Attribute",
				filters={"parent": variant_item},
				fields=["attribute", "attribute_value"],
			)
		}
		for candidate in frappe.get_all(
			"Item", filters={"variant_of": op_fg}, pluck="name"
		):
			attrs = {
				a.attribute: a.attribute_value
				for a in frappe.get_all(
					"Item Variant Attribute",
					filters={"parent": candidate},
					fields=["attribute", "attribute_value"],
				)
			}
			if wanted and all(attrs.get(k) == v for k, v in wanted.items()):
				return candidate

		frappe.throw(
			_(
				"Could not resolve an output item for variant {0} at operation {1} "
				"(Finished Good {2} is a template with no matching variant). Set the "
				"Output Item explicitly on the Job Card row."
			).format(variant_item, self.operation, op_fg),
			title=_("Output Item Unresolved"),
		)

	def _append_operating_cost(self, se):
		"""Actual operating cost: logged minutes x workstation hour rate,
		as an Additional Cost absorbed into the SFG valuation."""
		minutes = sum(flt(t.time_in_mins) for t in (self.get("time_logs") or []))
		hour_rate = flt(
			frappe.db.get_value("Workstation", self.workstation, "hour_rate")
			if self.workstation
			else 0
		)
		amount = flt((minutes / 60.0) * hour_rate, 2)
		if amount <= 0:
			return
		account = frappe.db.get_value(
			"Company", se.company, "default_operating_cost_account"
		)
		if not account:
			frappe.msgprint(
				_(
					"Operating cost {0} NOT added: set Default Operating Cost Account "
					"on Company {1} to absorb labour into SFG valuation."
				).format(amount, se.company),
				indicator="orange",
			)
			return
		se.append(
			"additional_costs",
			{
				"expense_account": account,
				"description": _("Actual operating cost — Job Card {0}").format(self.name),
				"amount": amount,
			},
		)
