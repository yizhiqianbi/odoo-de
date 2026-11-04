"""The digital employee in Discuss: one Odoo user every internal person chats with privately.

Decisions reach the person who makes them as cards with buttons; the buttons run as that person. What people write
there is fetched by the chat service, which answers as their assistant agent through the methods below."""
import logging
import threading

import requests
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext, html_sanitize

from .work import lines

_logger = logging.getLogger(__name__)
CARD_MODELS = ("work.step", "work.proposal")
PING_URL = "http://127.0.0.1:21047/api/assistant/ping"


def _ping(url):
    try:
        requests.post(url, timeout=3)
    except requests.RequestException as e:
        _logger.info("assistant ping to %s failed: %s", url, e)


class MailMessage(models.Model):
    _inherit = "mail.message"

    work_ref = fields.Char(index="btree_not_null", readonly=True,
                           help="model,id this message is about: a decision card for work.step and work.proposal, else the case.")

    def _store_message_fields(self, res, **kwargs):
        super()._store_message_fields(res, **kwargs)
        res.attr("work_ref", predicate=lambda m: bool(m.work_ref))


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    def _get_message_create_valid_field_names(self):
        names = super()._get_message_create_valid_field_names()
        if self.env.user == self.env["work.assistant"]._user():
            names = names | {"work_ref"}
        return names

    def _message_post_after_hook(self, message):
        res = super()._message_post_after_hook(message)
        assistant = self.env["work.assistant"]._partner()
        if (self.channel_type == "chat" and message.message_type == "comment" and message.author_id != assistant
                and assistant in self.sudo().channel_member_ids.partner_id):
            url = self.env["ir.config_parameter"].sudo().get_param("work_case.assistant_ping_url", PING_URL)
            self.env.cr.postcommit.add(lambda: threading.Thread(target=_ping, args=(url,), daemon=True).start())
        return res


class WorkStepCard(models.Model):
    _inherit = "work.step"

    card_message_id = fields.Many2one("mail.message", readonly=True, copy=False, index="btree_not_null", ondelete="set null")


class WorkProposalCard(models.Model):
    _inherit = "work.proposal"

    card_message_id = fields.Many2one("mail.message", readonly=True, copy=False, index="btree_not_null", ondelete="set null")


class WorkAssistant(models.AbstractModel):
    _name = "work.assistant"
    _description = "讨论里的数字员工"

    def _user(self):
        return self.env.ref("work_case.user_assistant").sudo()

    def _partner(self):
        return self._user().partner_id

    def _check_caller(self):
        if self.env.user != self._user():
            raise AccessError(_("只有数字员工自己能调用这个接口。"))

    def _person(self, login):
        user = self.env["res.users"].sudo().search([("login", "=", login), ("share", "=", False)], limit=1)
        if not user or user == self._user():
            raise UserError(_("没有这个内部用户：%s", login))
        return user

    def _chat(self, user):
        """The private chat between the digital employee and one person, created and shown to them on first use."""
        channel = self.env["discuss.channel"].with_user(self._user())._get_or_create_chat([user.partner_id.id])
        member = channel.sudo().channel_member_ids.filtered(lambda m: m.partner_id == user.partner_id)
        if member.unpin_dt:
            member.unpin_dt = False
        return channel

    def _say(self, user, body, ref=False):
        """Post as the digital employee in its chat with `user`; `body` is plain text or Markup."""
        if not user or user.share or user == self._user():
            return self.env["mail.message"]
        channel = self._chat(user)
        if not isinstance(body, Markup):
            body = Markup("<br/>").join((body or "").splitlines())
        return channel.with_user(self._user()).message_post(
            body=body, message_type="comment", subtype_xmlid="mail.mt_comment",
            author_id=self._partner().id, work_ref=ref or False)

    def _post_card(self, item):
        """A card for a step waiting on a decision or a drafted action waiting for confirmation, in its owner's chat."""
        owner = item.owner_id
        case = item.record_name
        if item._name == "work.step":
            body = Markup("<b>%s</b> · %s<br/>%s") % (case, item.name, item.question or "")
        elif item.action == "send_message":
            body = Markup("<b>%s</b> · %s") % (case, _("有一条对外消息等你批准"))
        else:
            body = Markup("<b>%s</b> · %s") % (case, _("起草了「%s」，等你确认", item.action_name))
        message = self._say(owner, body, ref=f"{item._name},{item.id}")
        if message:
            item.sudo().card_message_id = message
        return message

    def _tell(self, record, text, who=""):
        """A line from a case's digital employee to the case owner, tagged with the case so a reply can find it."""
        owner = record._work_owner()
        head = Markup("<b>%s</b>%s") % (record.display_name, Markup(" · %s") % who if who else "")
        body = head + Markup("<br/>") + Markup("<br/>").join((text or "").splitlines())
        return self._say(owner, body, ref=f"{record._name},{record.id}")

    # used by the chat service, as the digital employee

    @api.model
    def inbox(self, after_id=0, limit=50):
        """What internal people wrote to the digital employee after message `after_id`, oldest first."""
        self._check_caller()
        partner = self._partner()
        channels = self.env["discuss.channel"].sudo().search([
            ("channel_type", "=", "chat"), ("channel_member_ids.partner_id", "=", partner.id)])
        if not after_id:
            last = self.env["mail.message"].sudo().search([("model", "=", "discuss.channel"), ("res_id", "in", channels.ids)],
                                                         order="id desc", limit=1)
            return {"messages": [], "last_id": last.id}
        found = self.env["mail.message"].sudo().search([
            ("model", "=", "discuss.channel"), ("res_id", "in", channels.ids), ("id", ">", after_id),
            ("message_type", "=", "comment"), ("author_id", "!=", partner.id)], order="id", limit=limit)
        result = []
        for m in found:
            user = m.author_id.user_ids.filtered(lambda u: not u.share)[:1]
            if not user:
                continue
            parent = m.parent_id
            result.append({
                "id": m.id, "login": user.login, "name": user.name, "text": html2plaintext(m.body or "").strip(),
                "attachments": m.attachment_ids.mapped("name"),
                "reply_to": parent and {"text": html2plaintext(parent.body or "").strip()[:500], "ref": parent.work_ref or ""},
            })
        return {"messages": result, "last_id": found[-1:].id or after_id}

    @api.model
    def history(self, login, limit=16):
        """The last messages of the chat with one person, oldest first."""
        self._check_caller()
        channel = self._chat(self._person(login))
        found = self.env["mail.message"].sudo().search([
            ("model", "=", "discuss.channel"), ("res_id", "=", channel.id), ("message_type", "=", "comment")],
            order="id desc", limit=limit)
        partner = self._partner()
        return [{"id": m.id, "me": m.author_id == partner, "date": fields.Datetime.to_string(m.date),
                 "text": html2plaintext(m.body or "").strip()[:1500], "ref": m.work_ref or ""} for m in reversed(found)]

    @api.model
    def say(self, login, html, ref=False):
        """Post in the chat with one person; the html is sanitized like any message body."""
        self._check_caller()
        if ref and ref.split(",")[0] in CARD_MODELS:
            raise UserError(_("卡片只能由 post_card 发。"))
        message = self._say(self._person(login), Markup(html_sanitize(html or "")), ref=ref)
        return {"id": message.id}

    @api.model
    def typing(self, login, on=True):
        self._check_caller()
        channel = self._chat(self._person(login))
        member = channel.sudo().channel_member_ids.filtered(lambda m: m.partner_id == self._partner())
        member._notify_typing(bool(on))
        return True

    @api.model
    def post_card(self, login, model, res_id):
        """Bring a waiting item back to the bottom of the chat, for its owner only."""
        self._check_caller()
        if model not in CARD_MODELS:
            raise UserError(_("只有流程决定和起草的动作有卡片。"))
        item = self.env[model].sudo().browse(int(res_id)).exists()
        user = self._person(login)
        if not item or item.owner_id != user:
            raise UserError(_("这件事不归 %s 决定。", user.name))
        if (item._name == "work.step" and item.state != "waiting") or (item._name == "work.proposal" and item.state != "pending"):
            raise UserError(_("这件事已经处理过了。"))
        return {"id": self._post_card(item).id}

    # used by the card, as the person looking at it

    @api.model
    def card_data(self, message_id):
        """What a card shows, read with the rights of the person looking at it."""
        message = self.env["mail.message"].browse(int(message_id))
        message.check_access("read")
        model, _sep, res_id = (message.sudo().work_ref or "").partition(",")
        if model not in CARD_MODELS:
            return {}
        item = self.env[model].browse(int(res_id)).exists()
        if not item:
            return {"gone": True}
        item.check_access("read")
        base = {"model": model, "id": item.id, "playbook": item.playbook_id.name, "case": item.record_name,
                "res_model": item.res_model, "res_id": item.res_id, "mine": item.owner_id == self.env.user,
                "owner": item.owner_id.name, "decided_by": item.decided_by.name or "",
                "decided_at": fields.Datetime.to_string(item.decided_at) if item.decided_at else "",
                "superseded": bool(item.card_message_id) and item.card_message_id.id != message.id}
        if model == "work.step":
            return {**base, "kind": "decision", "title": item.name, "question": item.question or "",
                    "options": lines(item.options), "recommendation": item.recommendation or "",
                    "summary": item.agent_summary or "", "open": item.state == "waiting",
                    "state": item.state, "result": item.decision or ""}
        return {**base, "kind": "message" if item.action == "send_message" else "proposal", "title": item.action_name,
                "question": item.summary or "", "preview": item.preview or "", "open": item.state == "pending",
                "state": item.state, "result": item.result or "",
                "link": item.result_model and f"/odoo/{item.result_model}/{item.result_id}" or ""}

    # setup

    @api.model
    def _backfill(self):
        """Cards for everything already waiting, and a first hello to everyone who decides something."""
        hello = _("你好，我是数字员工。要你拍板的事我会发到这里，点卡片上的按钮就行。\n"
                  "也可以直接问我：「我今天有什么要处理的」「S00027 卡在哪」「这周还有哪些发票没对上」，"
                  "或者交代事情，比如「让 Hamburg Home 那边催一下定金」。")
        steps = self.env["work.step"].sudo().search([("state", "!=", "done")])
        for owner in steps.mapped("owner_id").filtered(lambda u: not u.share):
            if not self.env["mail.message"].sudo().search_count([
                    ("model", "=", "discuss.channel"), ("res_id", "=", self._chat(owner).id),
                    ("author_id", "=", self._partner().id)]):
                self._say(owner, hello)
        posted = 0
        for model in CARD_MODELS:
            domain = [("state", "=", "waiting" if model == "work.step" else "pending"), ("card_message_id", "=", False)]
            for item in self.env[model].sudo().search(domain, order="id"):
                if item.owner_id and not item.owner_id.share and self._post_card(item):
                    posted += 1
        return posted
