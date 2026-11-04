import json

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

from .guards import refuse_agent

STATES = [("pending", "待确认"), ("done", "已执行"), ("rejected", "已驳回"), ("failed", "执行失败")]


class WorkProposal(models.Model):
    """An Odoo action the digital employee drafted on a case. It runs only when a person confirms it, as that person."""
    _name = "work.proposal"
    _description = "数字员工起草的动作"
    _order = "id desc"

    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference(required=True, index=True, model_field="res_model")
    record_name = fields.Char(compute="_compute_record_name", string="事项")
    playbook_id = fields.Many2one("work.playbook", required=True, index=True)
    action = fields.Char(required=True, string="动作")
    action_name = fields.Char(required=True, string="动作名称")
    summary = fields.Text(required=True, string="数字员工的说明")
    params = fields.Text(required=True, default="{}", string="参数")
    preview = fields.Text(string="执行内容")
    state = fields.Selection(STATES, default="pending", required=True, string="状态")
    human_note = fields.Char(string="人的备注")
    result = fields.Char(readonly=True, string="结果")
    result_model = fields.Char(readonly=True)
    result_id = fields.Many2oneReference(readonly=True, model_field="result_model")
    decided_by = fields.Many2one("res.users", readonly=True)
    decided_at = fields.Datetime(readonly=True)
    activity_id = fields.Many2one("mail.activity", readonly=True)
    owner_id = fields.Many2one("res.users", compute="_compute_owner", search="_search_owner", string="负责人")

    def write(self, vals):
        if not self.env.su:
            refuse_agent(self.env, _("直接修改起草的动作"))
        return super().write(vals)

    def _record(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id)

    def _compute_record_name(self):
        for proposal in self:
            record = proposal.sudo()._record()
            proposal.record_name = record.display_name if record.exists() else ""

    def _compute_owner(self):
        for proposal in self:
            proposal.owner_id = proposal.sudo()._record()._work_owner()

    def _search_owner(self, operator, value):
        if operator not in ("=", "in"):
            raise UserError(_("负责人只支持「等于」筛选。"))
        users = value if operator == "in" else [value]
        ids = self.sudo().search([("state", "=", "pending")]).filtered(lambda p: p.owner_id.id in users).ids
        return [("id", "in", ids)]

    def _close_activity(self, feedback):
        for proposal in self.filtered("activity_id"):
            if proposal.activity_id.exists():
                proposal.activity_id.sudo().action_feedback(feedback=feedback)
            proposal.sudo().activity_id = False

    def _json(self):
        return {"id": self.id, "action": self.action, "name": self.action_name, "summary": self.summary,
                "preview": self.preview or "", "state": self.state, "result": self.result or "",
                "human_note": self.human_note or "", "decided_by": self.decided_by.name or "",
                "link": self.result_model and f"/odoo/{self.result_model}/{self.result_id}"}

    def action_confirm(self):
        """A person confirms: the action runs now, as that person, with the parameters checked when it was drafted."""
        refuse_agent(self.env, _("确认起草的动作"))
        for proposal in self:
            if proposal.state != "pending":
                raise UserError(_("这个动作已经处理过了。"))
            record = proposal._record()
            record.check_access("write")
            run = getattr(record, f"_work_do_{proposal.action}", None)
            if not run:
                raise UserError(_("动作 %s 没有实现。", proposal.action))
            outcome = run(json.loads(proposal.params), proposal) or {}
            proposal._close_activity(_("已确认：%s", proposal.action_name))
            proposal.sudo().write({
                "state": "done", "result": outcome.get("text") or _("已执行"), "result_model": outcome.get("model"),
                "result_id": outcome.get("id") or 0, "decided_by": self.env.user.id, "decided_at": fields.Datetime.now(),
            })
            self.env["work.action"]._log(
                record, actor=self.env.user.name, human=True, tool="human_confirm", risk="R3",
                what=_("确认执行：%s", proposal.action_name), detail=proposal.result,
                link=outcome.get("model") and (outcome["model"], outcome.get("id") or 0),
                args={"proposal": proposal.id, "note": proposal.human_note or ""})
            body = Markup("<b>%s</b> %s<br/>%s") % (proposal.action_name, _("已确认执行"), proposal.result)
            if proposal.human_note:
                body += Markup("<br/>%s %s") % (_("备注："), proposal.human_note)
            record.message_post(body=body, subtype_xmlid="mail.mt_note")
        return True

    def action_reject(self):
        refuse_agent(self.env, _("驳回起草的动作"))
        for proposal in self:
            if proposal.state != "pending":
                raise UserError(_("这个动作已经处理过了。"))
            if not proposal.human_note:
                raise UserError(_("驳回前请在「人的备注」里写清楚为什么。"))
            proposal._close_activity(_("已驳回：%s", proposal.human_note))
            proposal.sudo().write({"state": "rejected", "result": _("驳回：%s", proposal.human_note),
                                   "decided_by": self.env.user.id, "decided_at": fields.Datetime.now()})
            self.env["work.action"]._log(
                proposal._record(), actor=self.env.user.name, human=True, tool="human_confirm", risk="R3",
                what=_("驳回动作：%s", proposal.action_name), detail=proposal.human_note,
                args={"proposal": proposal.id, "note": proposal.human_note})
            proposal._record().message_post(
                body=Markup("<b>%s</b> %s<br/>%s") % (proposal.action_name, _("已驳回"), proposal.human_note),
                subtype_xmlid="mail.mt_note")
        return True

    def action_open_record(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self.res_model, "res_id": self.res_id,
                "view_mode": "form", "target": "current"}

    def action_open_result(self):
        self.ensure_one()
        if not self.result_model:
            return False
        return {"type": "ir.actions.act_window", "res_model": self.result_model, "res_id": self.result_id,
                "view_mode": "form", "target": "current"}
