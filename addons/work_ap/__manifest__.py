{
    "name": "应付发票审核",
    "summary": "Vendor bills worked by a digital employee: three-way match, approval, payment plan",
    "version": "20.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "odoo-de",
    "license": "LGPL-3",
    "depends": ["work_case", "account", "purchase", "purchase_stock"],
    "data": [
        "security/security.xml",
        "security/ir.access.csv",
        "data/ap_playbook_data.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
}
