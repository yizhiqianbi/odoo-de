{
    "name": "招聘候选人跟进",
    "summary": "Applicants worked by a digital employee: screening, interview scheduling, feedback, offer, onboarding",
    "version": "20.0.1.0.0",
    "category": "Human Resources/Recruitment",
    "author": "odoo-de",
    "license": "LGPL-3",
    "depends": ["work_case", "hr_recruitment"],
    "data": [
        "security/security.xml",
        "security/ir.access.csv",
        "data/hr_playbook_data.xml",
        "views/hr_applicant_views.xml",
    ],
    "installable": True,
}
