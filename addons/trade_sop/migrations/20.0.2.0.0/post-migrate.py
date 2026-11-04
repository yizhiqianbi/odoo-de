"""Move the 20.0.1 trade.sop.step rows onto the work_case kernel as work.step rows of the trade playbook."""
import logging

from odoo import SUPERUSER_ID, api

log = logging.getLogger(__name__)

COLUMNS = ("order_id", "code", "state", "agent_summary", "question", "options", "recommendation", "human_note",
           "decision", "decided_by", "decided_at", "activity_id")


def migrate(cr, version):
    cr.execute("SELECT to_regclass('trade_sop_step')")
    if not cr.fetchone()[0]:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    playbook = env.ref("trade_sop.playbook_trade")
    stages = {stage.code: stage for stage in playbook.stage_ids}
    cr.execute(f"SELECT {', '.join(COLUMNS)} FROM trade_sop_step ORDER BY order_id, sequence, id")
    rows = [dict(zip(COLUMNS, row)) for row in cr.fetchall()]
    existing = set(env["work.step"].search([("res_model", "=", "sale.order")]).mapped(lambda s: (s.res_id, s.code)))
    values = []
    for row in rows:
        stage = stages.get(row["code"])
        if not stage or (row["order_id"], row["code"]) in existing:
            continue
        values.append({
            "stage_id": stage.id, "res_model": "sale.order", "res_id": row["order_id"],
            **{k: row[k] for k in COLUMNS[2:] if row[k] is not None},
        })
    env["work.step"].create(values)
    orders = env["sale.order"].browse(sorted({v["res_id"] for v in values}))
    orders.write({"work_playbook_id": playbook.id})
    log.info("moved %s trade steps of %s orders onto the work_case kernel", len(values), len(orders))
