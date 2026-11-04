{
    "name": "数字员工内核",
    "summary": "Playbooks worked by digital employees on any business record, with human decision gates",
    "version": "20.0.1.2.0",
    "category": "Productivity",
    "author": "odoo-de",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "security/security.xml",
        "security/ir.access.csv",
        "data/assistant.xml",
        "views/work_views.xml",
        "views/ledger_views.xml",
        "views/workbench_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "work_case/static/src/**/*",
        ],
    },
    "installable": True,
}
