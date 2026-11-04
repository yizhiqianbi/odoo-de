{
    "name": "外贸跟单 SOP",
    "summary": "Export order follow-up worked by a digital employee: the trade playbook on the work_case kernel",
    "version": "20.0.2.0.0",
    "category": "Sales/Sales",
    "author": "odoo-de",
    "license": "LGPL-3",
    "depends": ["work_case", "sale_management", "purchase", "stock", "account"],
    "data": [
        "security/security.xml",
        "security/ir.access.csv",
        "data/trade_playbook_data.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
}
