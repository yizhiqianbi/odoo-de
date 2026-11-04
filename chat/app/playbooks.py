"""The playbooks the chat serves. What a case contains and what may be said comes from Odoo; this only says which bot
works each playbook and what people in the chat are called there."""
from . import config

PLAYBOOKS = {
    "trade": {"model": "sale.order", "key": config.BOT_KEY_FILE, "name": "外贸跟单",
              "owner": "业务员", "party": "顾客", "ai": "AI 业务员", "ai_external": "Sales assistant (AI)"},
    "ap": {"model": "account.move", "key": config.HOME / "keys" / "bot_ap.key", "name": "应付发票审核",
           "owner": "会计", "party": "供应商", "ai": "AI 应付专员", "ai_external": "AI 应付专员"},
    "hr": {"model": "hr.applicant", "key": config.HOME / "keys" / "bot_hr.key", "name": "招聘候选人跟进",
           "owner": "招聘负责人", "party": "候选人", "ai": "AI 招聘专员", "ai_external": "AI 招聘专员"},
}


def labels(code):
    p = PLAYBOOKS.get(code) or PLAYBOOKS["trade"]
    return {k: p[k] for k in ("name", "owner", "party", "ai", "ai_external")}
