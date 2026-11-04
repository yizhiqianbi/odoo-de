"""What a digital employee may never do, even under sudo. Modules owning other actions with outside effect
(confirming orders, posting entries, validating transfers) refuse them with `refuse_agent` in their own models."""
from odoo import _, models
from odoo.exceptions import AccessError

AGENT_GROUP = "work_case.group_agent"


def is_agent(env):
    return env.user.has_group(AGENT_GROUP)


def refuse_agent(env, action):
    if is_agent(env):
        raise AccessError(_("数字员工不能执行「%s」，这一步需要人来决定。", action))


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def message_post(self, **kwargs):
        if is_agent(self.env):
            note = self.env.ref("mail.mt_note")
            is_note = kwargs.get("subtype_xmlid") == "mail.mt_note" or kwargs.get("subtype_id") == note.id
            if kwargs.get("message_type", "notification") == "comment" and not is_note:
                raise AccessError(_("数字员工只能写内部备注；对外发送消息需要人来决定。"))
            partners = self.env["res.partner"].sudo().browse(kwargs.get("partner_ids") or [])
            if any(not partner.user_ids.filtered(lambda u: not u.share) for partner in partners):
                raise AccessError(_("数字员工不能通知公司外部的联系人。"))
        return super().message_post(**kwargs)
