import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .guards import refuse_agent

RISKS = [("R0", "只读"), ("R1", "内部记录"), ("R2", "对外说话"), ("R3", "Odoo 动作")]


class WorkAction(models.Model):
    """The action ledger: every call a digital employee made on a case, and every decision a person took on its work.
    Lines are written by the kernel itself and never edited."""
    _name = "work.action"
    _description = "数字员工动作账本"
    _order = "id desc"
    _rec_name = "what"

    res_model = fields.Char(required=True, index=True, string="模型")
    res_id = fields.Many2oneReference(required=True, index=True, model_field="res_model", string="记录")
    record_name = fields.Char(string="事项", readonly=True)
    playbook_id = fields.Many2one("work.playbook", required=True, index=True, string="场景")
    actor = fields.Char(required=True, string="谁", help="The digital employee's name, or the person's.")
    user_id = fields.Many2one("res.users", string="Odoo 用户", index=True,
                              help="The bot account for a digital employee, the person for a human decision.")
    human = fields.Boolean(string="人做的", index=True)
    tool = fields.Char(required=True, string="工具")
    risk = fields.Selection(RISKS, required=True, default="R1", string="风险", index=True)
    ok = fields.Boolean(default=True, string="完成", index=True)
    what = fields.Char(required=True, string="做了什么")
    detail = fields.Text(string="细节")
    error = fields.Text(string="被拒原因")
    link_model = fields.Char(string="产生的单据模型")
    link_id = fields.Many2oneReference(model_field="link_model", string="产生的单据")
    args = fields.Text(string="参数", default="{}")
    result = fields.Text(string="返回", default="{}")

    def write(self, vals):
        raise UserError(_("账本只能追加，不能修改。"))

    def unlink(self):
        if not self.env.is_superuser():
            raise UserError(_("账本只能追加，不能删除。"))
        return super().unlink()

    @api.model
    def _log(self, record, *, actor, tool, what, risk="R1", ok=True, detail="", error="", human=False,
             link=None, args=None, result=None):
        return self.sudo().create({
            "res_model": record._name, "res_id": record.id, "record_name": record.display_name,
            "playbook_id": record.work_playbook_id.id, "actor": actor, "user_id": self.env.user.id, "human": human,
            "tool": tool, "risk": risk, "ok": ok, "what": what[:250], "detail": detail or "", "error": error or "",
            "link_model": link and link[0], "link_id": link and link[1] or 0,
            "args": json.dumps(args or {}, ensure_ascii=False), "result": json.dumps(result or {}, ensure_ascii=False),
        })

    def action_open_record(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self.res_model, "res_id": self.res_id,
                "view_mode": "form", "target": "current"}

    def action_open_link(self):
        self.ensure_one()
        if not self.link_model:
            return False
        return {"type": "ir.actions.act_window", "res_model": self.link_model, "res_id": self.link_id,
                "view_mode": "form", "target": "current"}
