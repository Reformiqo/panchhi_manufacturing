app_name = "panchhi_manufacturing"
app_title = "Panchhi Manufacturing"
app_publisher = "Reformiqo Business Services Pvt. Ltd."
app_description = "Multi-Variant Work Order, Multi-Item Job Card, Operation-Level SFG Receipt & BOM-Bypassed Subcontracting (FRD v3.0)"
app_email = "info@reformiqo.com"
app_license = "mit"

required_apps = ["frappe", "erpnext"]

# --------------------------------------------------------------------------
# Install / Migrate
#
# Custom Fields + Property Setters are CODE-FIRST: upserted by
# panchhi_manufacturing.setup on every migrate. No CF/PS fixtures — one
# source of truth, no fixture churn. (Lesson from detox_waste_management,
# where fixture-exported CFs from other apps leaked into the app fixture
# and blocked cloud migrates.)
# --------------------------------------------------------------------------
after_install = "panchhi_manufacturing.setup.after_migrate"
after_migrate = "panchhi_manufacturing.setup.after_migrate"

# --------------------------------------------------------------------------
# Fixtures — Client Scripts only (DB-resident, module-scoped). CF/PS are
# code-first in setup.py, never exported here.
# --------------------------------------------------------------------------
fixtures = [
    {
        "dt": "Client Script",
        "filters": [["module", "=", "Panchhi Manufacturing"]],
    },
]

# --------------------------------------------------------------------------
# Document Events — additive roll-up of per-variant produced qty (C-06
# half 2, done as a hook rather than a fork so the blast radius stays
# minimal).
# --------------------------------------------------------------------------
doc_events = {
    "Stock Entry": {
        "on_submit": "panchhi_manufacturing.events.stock_entry.update_variant_produced_qty",
        "on_cancel": "panchhi_manufacturing.events.stock_entry.update_variant_produced_qty",
    },
}

# --------------------------------------------------------------------------
# DocType Class Overrides — THE FORK (FRD sheet 'A. Core Overrides').
# Every override is gated on Work Order.custom_is_multi_variant (or the
# SCO's custom_work_order); unmarked documents behave as stock ERPNext,
# byte for byte. Regression suite: tests/test_phase1_gating.py — run it
# after every ERPNext upgrade.
# --------------------------------------------------------------------------
override_doctype_class = {
    "Work Order": "panchhi_manufacturing.overrides.work_order.MultiVariantWorkOrder",
    "Stock Entry": "panchhi_manufacturing.overrides.stock_entry.MultiVariantStockEntry",
    "Job Card": "panchhi_manufacturing.overrides.job_card.MultiItemJobCard",
    "Subcontracting Order": "panchhi_manufacturing.overrides.subcontracting_order.PanchhiSubcontractingOrder",
}
