// Panchhi Manufacturing — Work Order UI (C-02 / C-07).
//
//   Apply Style Recipe   draft + multi-variant: prefill operations,
//                        required items (scaled by variant qty) and the
//                        style's variants.
//   Create Subcontracting Order
//                        submitted + multi-variant: pick a subcontracted
//                        operation, service item, supplier, rate → draft
//                        WO-driven, BOM-less SCO (C-05).

frappe.ui.form.on("Work Order", {
    refresh(frm) {
        if (!frm.doc.custom_is_multi_variant) return;

        if (frm.doc.docstatus === 0 && frm.doc.production_item) {
            frm.add_custom_button(
                __("Fetch Operations & Materials"),
                () => panchhi.fetch_production_details(frm),
                __("Panchhi")
            );
        }

        if (frm.doc.docstatus === 1) {
            const sub_ops = (frm.doc.operations || []).filter((o) => o.is_subcontracted);
            if (sub_ops.length) {
                frm.add_custom_button(
                    __("Create Subcontracting Order"),
                    () => panchhi.make_sco_dialog(frm, sub_ops),
                    __("Panchhi")
                );
            }
        }
    },
});

window.panchhi = window.panchhi || {};

// Populate the grouped Work Order's operations + required items. The server
// uses the style's Style Recipe when one exists, otherwise derives the route
// from the variants' default BOMs (Panchhi maintains BOMs, not recipes). Runs
// server-side and saves, so an already-created empty draft can be filled in
// place — needs the Work Order saved first (it reads the persisted variants).
panchhi.fetch_production_details = function (frm) {
    if (frm.is_new()) {
        frappe.msgprint(
            __("Save the Work Order first, then fetch operations & materials.")
        );
        return;
    }
    if (!(frm.doc.custom_variants || []).length) {
        frappe.msgprint(__("Add at least one row to the Variants table first."));
        return;
    }
    frappe.call({
        method: "panchhi_manufacturing.overrides.work_order.fetch_production_details",
        args: { work_order: frm.doc.name },
        freeze: true,
        freeze_message: __("Fetching operations & materials…"),
        callback(r) {
            if (r.message) frm.reload_doc();
        },
    });
};

panchhi.make_sco_dialog = function (frm, sub_ops) {
    const d = new frappe.ui.Dialog({
        title: __("Create Subcontracting Order"),
        fields: [
            {
                fieldname: "operation_row",
                label: __("Operation"),
                fieldtype: "Select",
                reqd: 1,
                options: sub_ops.map((o) => ({
                    value: o.name,
                    label: `${o.operation} → ${o.finished_good || __("(no SFG)")}`,
                })),
            },
            {
                fieldname: "service_item",
                label: __("Service Item (job-work charge)"),
                fieldtype: "Link",
                options: "Item",
                reqd: 1,
                get_query: () => ({ filters: { is_stock_item: 0 } }),
            },
            {
                fieldname: "supplier",
                label: __("Job Worker (Supplier)"),
                fieldtype: "Link",
                options: "Supplier",
            },
            {
                // The job worker's warehouse. MUST differ from the WIP
                // warehouse the raw materials are reserved in, or ERPNext
                // rejects the Send-to-Subcontractor transfer — the server
                // guard in make_subcontracting_order throws otherwise.
                fieldname: "supplier_warehouse",
                label: __("Job Worker Warehouse"),
                fieldtype: "Link",
                options: "Warehouse",
                reqd: 1,
                get_query: () => ({
                    filters: {
                        company: frm.doc.company,
                        is_group: 0,
                        name: ["!=", frm.doc.wip_warehouse],
                    },
                }),
            },
            {
                fieldname: "service_rate",
                label: __("Rate per Unit"),
                fieldtype: "Currency",
                default: 0,
            },
        ],
        primary_action_label: __("Create"),
        primary_action(values) {
            frappe.call({
                method:
                    "panchhi_manufacturing.overrides.subcontracting_order.make_subcontracting_order",
                args: {
                    work_order: frm.doc.name,
                    operation_row: values.operation_row,
                    service_item: values.service_item,
                    supplier: values.supplier,
                    service_rate: values.service_rate,
                    supplier_warehouse: values.supplier_warehouse,
                },
                callback(r) {
                    d.hide();
                    if (r.message) {
                        frappe.set_route("Form", "Subcontracting Order", r.message);
                    }
                },
            });
        },
    });
    d.show();
};
