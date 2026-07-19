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
# DocType JS (C-01 WO variants UI, C-03 JC multi-item UI — added per phase)
# --------------------------------------------------------------------------
# doctype_js = {
#     "Work Order": "public/js/work_order.js",
#     "Job Card": "public/js/job_card.js",
# }

# --------------------------------------------------------------------------
# Document Events (C-02 WO submit branching, C-04 JC completion SFG receipt
# — added per phase)
# --------------------------------------------------------------------------
# doc_events = {}

# --------------------------------------------------------------------------
# DocType Class Overrides — THE FORK (C-06). Every override is gated on
# Work Order.custom_is_multi_variant; unmarked documents behave as stock
# ERPNext, byte for byte. DO NOT wire these before Q-01 client sign-off
# (FRD sheet 'A. Core Overrides').
# --------------------------------------------------------------------------
# override_doctype_class = {
#     "Work Order": "panchhi_manufacturing.overrides.work_order.MultiVariantWorkOrder",
#     "Stock Entry": "panchhi_manufacturing.overrides.stock_entry.MultiVariantStockEntry",
#     "Subcontracting Order": "panchhi_manufacturing.overrides.subcontracting_order.BOMLessSubcontractingOrder",
#     "Job Card": "panchhi_manufacturing.overrides.job_card.MultiItemJobCard",
# }
