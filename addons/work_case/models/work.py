from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

from .guards import refuse_agent

STEP_STATES = [
    ("todo", "待开始"),
    ("working", "进行中"),
    ("waiting", "待人工决定"),
    ("blocked", "受阻"),
    ("done", "已完成"),
]


def lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


class WorkPlaybook(models.Model):
    """A process a digital employee works on one kind of record: who it is, who decides, what it may touch."""
    _name = "work.playbook"
    _description = "数字员工 Playbook"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    model = fields.Char(required=True, help="The business model this playbook works on, such as sale.order.")
    active = fields.Boolean(default=True)
    role = fields.Text(string="岗位卡", help="Who the digital employee is on this playbook, whom it serves, its tone.")
    external_rules = fields.Text(string="对外沟通规则", help="What may and may never be said to the outside party.")
    party_label = fields.Char(string="外部联系人叫法", default="客户", translate=True)
    owner_field = fields.Char(string="负责人字段", default="user_id", help="Path to the deciding res.users, e.g. recruiter_id.user_id.")
    party_field = fields.Char(string="外部联系人字段", default="partner_id", help="Path to the outside res.partner.")
    agent_fields = fields.Text(string="agent 可填写的字段", help="One field name per line.")
    group_id = fields.Many2one("res.groups", string="处理人权限组",
                               help="Only members see and decide this playbook's steps. Empty means every internal user.")
    stage_ids = fields.One2many("work.stage", "playbook_id", string="步骤")

    _code_unique = models.Constraint("UNIQUE(code)", "Playbook 代码必须唯一。")

    @api.constrains("model")
    def _check_model(self):
        for playbook in self:
            if playbook.model not in self.env or not hasattr(self.env[playbook.model], "work_agent_context"):
                raise ValidationError(_("模型 %s 没有接入数字员工内核。", playbook.model))


class WorkStage(models.Model):
    """One step of a playbook: what it achieves, what the digital employee does, and who closes it."""
    _name = "work.stage"
    _description = "数字员工 Playbook 步骤"
    _order = "playbook_id, sequence, id"

    playbook_id = fields.Many2one("work.playbook", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(required=True)
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    short_name = fields.Char(string="短名", translate=True)
    external_name = fields.Char(string="对外名称", help="What the outside party sees for this step, in their language.")
    goal = fields.Text(string="目标", translate=True)
    agent_task = fields.Text(string="数字员工的工作", help="Instructions the digital employee reads for this step.")
    human_question = fields.Char(string="人要决定什么")
    gate = fields.Selection([
        ("human", "人工拍板"),
        ("agent", "数字员工完成"),
        ("condition", "符合条件时人工拍板"),
    ], required=True, default="human", string="关卡")
    gate_domain = fields.Char(string="需要人拍板的条件", default="[]",
                              help="Domain on the business record; the step needs a person when the record matches.")
    on_approve = fields.Char(string="批准时执行",
                             help="Name of a hook: approving calls _work_approve_<name>(step) on the record, as the approver.")

    _code_unique = models.Constraint("UNIQUE(playbook_id, code)", "同一个 Playbook 里步骤代码必须唯一。")


class WorkStep(models.Model):
    """One playbook step of one record. The digital employee changes it only through the record's work_agent_* methods."""
    _name = "work.step"
    _description = "数字员工步骤"
    _order = "res_model, res_id, sequence, id"

    stage_id = fields.Many2one("work.stage", required=True, ondelete="restrict")
    playbook_id = fields.Many2one(related="stage_id.playbook_id", store=True, index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference(required=True, index=True, model_field="res_model")
    record_name = fields.Char(compute="_compute_record_name", string="事项")
    sequence = fields.Integer(related="stage_id.sequence", store=True)
    code = fields.Char(related="stage_id.code", store=True)
    name = fields.Char(related="stage_id.name")
    state = fields.Selection(STEP_STATES, default="todo", required=True, string="状态")
    needs_human = fields.Boolean(compute="_compute_needs_human", string="需人工拍板")
    agent_summary = fields.Text(string="数字员工的结论")
    question = fields.Char(string="待决定的问题")
    options = fields.Text(string="选项", help="One option per line.")
    recommendation = fields.Char(string="推荐")
    human_note = fields.Char(string="人的备注")
    decision = fields.Char(string="决定", readonly=True)
    decided_by = fields.Many2one("res.users", readonly=True)
    decided_at = fields.Datetime(readonly=True)
    activity_id = fields.Many2one("mail.activity", readonly=True)
    owner_id = fields.Many2one("res.users", compute="_compute_owner", search="_search_owner", string="负责人")

    def write(self, vals):
        if not self.env.su:
            refuse_agent(self.env, _("直接修改流程步骤"))
        return super().write(vals)

    def _record(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id)

    def _compute_record_name(self):
        for step in self:
            record = step.sudo()._record()
            step.record_name = record.display_name if record.exists() else ""

    def _compute_owner(self):
        for step in self:
            step.owner_id = step.sudo()._record()._work_owner()

    def _search_owner(self, operator, value):
        if operator not in ("=", "in"):
            raise UserError(_("负责人只支持「等于」筛选。"))
        users = value if operator == "in" else [value]
        ids = self.sudo().search([("state", "!=", "done")]).filtered(lambda s: s.owner_id.id in users).ids
        return [("id", "in", ids)]

    def _compute_needs_human(self):
        for step in self:
            gate = step.stage_id.gate
            if gate == "condition":
                record = step.sudo()._record()
                step.needs_human = bool(record.filtered_domain(safe_eval(step.stage_id.gate_domain or "[]")))
            else:
                step.needs_human = gate == "human"

    def _close_activity(self, feedback):
        for step in self.filtered("activity_id"):
            if step.activity_id.exists():
                step.activity_id.sudo().action_feedback(feedback=feedback)
            step.activity_id = False

    def _check_current(self):
        record = self._record()
        current = record.work_current_step_id
        if self != current:
            raise UserError(_("只能决定当前步骤「%s」。", current.name or _("无")))
        record.check_access("write")
        return record

    def action_approve(self, choice=None):
        """A person accepts the step, as recommended unless another of its options is chosen."""
        refuse_agent(self.env, _("批准流程步骤"))
        for step in self:
            if step.state == "done":
                continue
            record = step._check_current()
            if choice and choice not in lines(step.options):
                raise UserError(_("只能从这一步给出的选项里选。"))
            if step.stage_id.on_approve:
                hook = getattr(record, f"_work_approve_{step.stage_id.on_approve}", None)
                if not hook:
                    raise UserError(_("批准钩子 %s 没有实现。", step.stage_id.on_approve))
                hook(step)
            decision = choice or step.recommendation or _("批准")
            step._close_activity(_("已批准：%s", decision))
            step.write({"state": "done", "decision": decision, "decided_by": self.env.user.id,
                        "decided_at": fields.Datetime.now()})
            self.env["work.action"]._log(record, actor=self.env.user.name, human=True, tool="human_decide",
                                         what=_("批准：%s", step.name), detail=_("%s 已批准：%s", self.env.user.name, decision),
                                         args={"step": step.code, "choice": choice, "note": step.human_note or ""})
            body = Markup("<b>%s</b> %s<br/>%s") % (step.name, _("已批准"), decision)
            if step.human_note:
                body += Markup("<br/>%s %s") % (_("备注："), step.human_note)
            record.message_post(body=body, subtype_xmlid="mail.mt_note")
        return True

    def action_reject(self):
        """A person sends the step back to the digital employee; the note says what to change."""
        refuse_agent(self.env, _("驳回流程步骤"))
        for step in self:
            if step.state == "done":
                continue
            record = step._check_current()
            if not step.human_note:
                raise UserError(_("驳回前请在「人的备注」里写清楚要怎么改。"))
            step._close_activity(_("已驳回：%s", step.human_note))
            step.write({"state": "working", "decision": _("驳回"), "decided_by": self.env.user.id,
                        "decided_at": fields.Datetime.now()})
            self.env["work.action"]._log(record, actor=self.env.user.name, human=True, tool="human_decide",
                                         what=_("驳回：%s", step.name), detail=step.human_note,
                                         args={"step": step.code, "note": step.human_note})
            record.message_post(body=Markup("<b>%s</b> %s<br/>%s") % (step.name, _("已驳回"), step.human_note),
                                subtype_xmlid="mail.mt_note")
        return True

    def action_open_record(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self.res_model, "res_id": self.res_id,
                "view_mode": "form", "target": "current"}
