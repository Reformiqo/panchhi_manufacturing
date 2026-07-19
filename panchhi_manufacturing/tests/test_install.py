"""Phase 0 — app skeleton contract.

Pins the wiring every later phase depends on:
  - app installed on the site
  - hooks resolve (after_migrate path importable + callable)
  - setup.after_migrate is idempotent with empty specs (Phase 0 state)
  - CF/PS are code-first: no Custom Field / Property Setter fixture
    blocks in hooks (the detox fixture-churn lesson, enforced by test)
"""
import frappe
from frappe.tests import IntegrationTestCase


class TestPanchhiManufacturingInstall(IntegrationTestCase):

	def test_app_installed(self):
		self.assertIn("panchhi_manufacturing", frappe.get_installed_apps())

	def test_after_migrate_hook_resolves_and_runs(self):
		hooks = frappe.get_hooks("after_migrate", app_name="panchhi_manufacturing")
		self.assertIn("panchhi_manufacturing.setup.after_migrate", hooks)
		# Phase 0: empty specs — must run clean (idempotence smoke).
		frappe.get_attr("panchhi_manufacturing.setup.after_migrate")()

	def test_no_cf_or_ps_fixtures(self):
		from panchhi_manufacturing import hooks

		for fx in hooks.fixtures:
			dt = fx["dt"] if isinstance(fx, dict) else fx
			self.assertNotIn(
				dt,
				("Custom Field", "Property Setter"),
				"CF/PS must stay code-first in setup.py — never fixtures.",
			)

	def test_overrides_package_importable(self):
		import panchhi_manufacturing.overrides  # noqa: F401
