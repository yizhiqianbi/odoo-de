from datetime import datetime, time, timedelta

from odoo import api, fields, models

from .work import lines


class WorkPlaybook(models.Model):
    _inherit = "work.playbook"

    @api.model
    def workbench_data(self):
        """Everything the workbench shows one person, read with that person's rights:
        what waits on them, each playbook as a board, the digital employees' record, and the latest ledger lines."""
        user = self.env.user
        Step, Proposal, Ledger = self.env["work.step"], self.env["work.proposal"], self.env["work.action"]
        playbooks = self.search([]).filtered(lambda p: not p.group_id or p.group_id in user.all_group_ids)
        local_now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        start_of_day = fields.Datetime.to_string(datetime.combine(local_now.date(), time.min)
                                                 - (local_now.utcoffset() or timedelta()))

        waiting = []
        for step in Step.search([("state", "=", "waiting"), ("playbook_id", "in", playbooks.ids)]):
            if step.owner_id != user:
                continue
            waiting.append({
                "kind": "decision", "id": step.id, "playbook": step.playbook_id.name, "res_model": step.res_model,
                "res_id": step.res_id, "case": step.record_name, "title": step.name, "question": step.question or "",
                "options": lines(step.options), "recommendation": step.recommendation or "",
                "summary": step.agent_summary or "", "at": fields.Datetime.to_string(step.write_date),
            })
        for proposal in Proposal.search([("state", "=", "pending"), ("playbook_id", "in", playbooks.ids)]):
            if proposal.owner_id != user:
                continue
            waiting.append({
                "kind": "message" if proposal.action == "send_message" else "proposal", "id": proposal.id,
                "playbook": proposal.playbook_id.name, "res_model": proposal.res_model, "res_id": proposal.res_id,
                "case": proposal.record_name, "title": proposal.action_name, "question": proposal.summary,
                "preview": proposal.preview or "", "at": fields.Datetime.to_string(proposal.create_date),
            })
        waiting.sort(key=lambda w: w["at"])

        boards, roster, mine = [], [], []
        for playbook in playbooks:
            if playbook.model not in self.env:
                continue
            Model = self.env[playbook.model]
            records = Model.search([("work_playbook_id", "=", playbook.id)])
            stages = [{"id": s.id, "name": s.short_name or s.name, "human": s.gate != "agent", "cases": []}
                      for s in playbook.stage_ids]
            by_stage = {s["id"]: s for s in stages}
            done = []
            for record in records:
                current = record.work_current_step_id
                item = {"res_model": record._name, "res_id": record.id, "name": record.display_name,
                        "state": current.state if current else "done", "step": current.name if current else ""}
                owner = record._work_owner()
                if owner == user:
                    mine.append({**item, "playbook": playbook.name, "party": record._work_party().display_name,
                                 "facts": record._work_facts()[:3]})
                if current and current.stage_id.id in by_stage:
                    by_stage[current.stage_id.id]["cases"].append(item)
                else:
                    done.append(item)
            boards.append({"id": playbook.id, "name": playbook.name, "party": playbook.party_label or "",
                           "stages": stages, "done": done, "total": len(records)})

            agent_lines = Ledger.search([("playbook_id", "=", playbook.id), ("human", "=", False)])
            today = agent_lines.filtered(lambda a: fields.Datetime.to_string(a.create_date) >= start_of_day)
            last = agent_lines[:1]
            states = [r.work_current_step_id.state for r in records if r.work_current_step_id]
            roster.append({
                "playbook": playbook.name, "actor": last.actor or "", "cases": len(records),
                "waiting": states.count("waiting"), "blocked": states.count("blocked"),
                "today": len(today), "refused_today": len(today.filtered(lambda a: not a.ok)),
                "total": len(agent_lines), "refused": len(agent_lines.filtered(lambda a: not a.ok)),
                "tiers": {r: len(agent_lines.filtered(lambda a, r=r: a.risk == r)) for r in ("R0", "R1", "R2", "R3")},
                "last": last.what or "", "last_case": last.record_name or "",
                "last_at": fields.Datetime.to_string(last.create_date) if last else "",
            })

        recent = Ledger.search([("playbook_id", "in", playbooks.ids)], limit=40)
        today_lines = Ledger.search_count([("playbook_id", "in", playbooks.ids), ("human", "=", False),
                                           ("create_date", ">=", start_of_day)])
        refused_today = Ledger.search_count([("playbook_id", "in", playbooks.ids), ("human", "=", False), ("ok", "=", False),
                                             ("create_date", ">=", start_of_day)])
        return {
            "user": user.name, "waiting": waiting, "mine": mine, "boards": boards, "roster": roster,
            "today": today_lines, "refused_today": refused_today,
            "ledger": [{"id": a.id, "at": fields.Datetime.to_string(a.create_date), "actor": a.actor, "human": a.human,
                        "what": a.what, "detail": a.detail or "", "error": a.error or "", "risk": a.risk, "ok": a.ok,
                        "case": a.record_name, "res_model": a.res_model, "res_id": a.res_id,
                        "link_model": a.link_model or "", "link_id": a.link_id} for a in recent],
        }
