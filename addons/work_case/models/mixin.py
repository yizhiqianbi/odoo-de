import json

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from .guards import is_agent, refuse_agent
from .work import STEP_STATES, lines

MAX_OPTIONS = 3
CHAT_TAG = "[群聊]"


def plain(value):
    """A field value as JSON: records by name, dates as ISO strings."""
    if isinstance(value, models.BaseModel):
        return ", ".join(value.mapped("display_name"))
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value if value is not False else ""


class WorkCaseMixin(models.AbstractModel):
    """Makes a business model workable by digital employees: playbook steps, the agent methods, and the hooks a
    scenario module overrides to say what the agent sees, what the outside party sees, and what stays confidential.

    The agent methods are the only way a digital employee changes anything: each checks the caller, works on the
    current step only, and writes with sudo, since the agent's own access to the record is read-only.
    """
    _name = "work.case.mixin"
    _description = "数字员工事项"

    work_playbook_id = fields.Many2one("work.playbook", string="Playbook", copy=False, readonly=True)
    work_step_ids = fields.One2many("work.step", "res_id", string="数字员工步骤",
                                    domain=lambda self: [("res_model", "=", self._name)])
    work_current_step_id = fields.Many2one("work.step", compute="_compute_work_current_step", string="当前步骤")
    work_state = fields.Selection(STEP_STATES, compute="_compute_work_current_step", string="当前步骤状态")
    work_chat_frame = fields.Html(compute="_compute_work_chat_frame", sanitize=False, string="数字员工群")

    def _compute_work_chat_frame(self):
        for record in self:
            record.work_chat_frame = record.work_step_ids and Markup(
                '<iframe class="o_work_chat_frame" title="数字员工群" src="/?case=%s:%s&amp;frame=1"'
                ' style="width:100%%;height:72vh;border:1px solid #c9d3da;border-radius:6px"></iframe>'
            ) % (record._name, record.id)

    @api.depends("work_step_ids.state")
    def _compute_work_current_step(self):
        for record in self:
            current = record.work_step_ids.filtered(lambda s: s.state != "done").sorted("sequence")[:1]
            record.work_current_step_id = current
            record.work_state = current.state if current else ("done" if record.work_step_ids else False)

    def write(self, vals):
        if not self.env.su:
            refuse_agent(self.env, _("直接修改业务记录"))
        return super().write(vals)

    def action_work_start(self):
        for record in self.filtered(lambda r: not r.work_step_ids):
            playbook = record.work_playbook_id or self.env["work.playbook"].search([("model", "=", record._name)], limit=1)
            if not playbook.stage_ids:
                raise UserError(_("%s 还没有可用的 Playbook。", record._description))
            record.sudo().work_playbook_id = playbook
            self.env["work.step"].sudo().create([
                {"stage_id": stage.id, "res_model": record._name, "res_id": record.id} for stage in playbook.stage_ids])
            record.message_post(body=_("数字员工流程「%(name)s」已启动，共 %(n)s 步。", name=playbook.name,
                                       n=len(playbook.stage_ids)), subtype_xmlid="mail.mt_note")
        return True

    # hooks for scenario modules

    def _work_path(self, path):
        value = self
        for name in (path or "").split("."):
            value = value[name] if name else value
        return value

    def _work_owner(self):
        self.ensure_one()
        owner = self._work_path(self.work_playbook_id.owner_field or "user_id")
        return owner[:1] or self.env.ref("base.user_admin")

    def _work_party(self):
        self.ensure_one()
        return self._work_path(self.work_playbook_id.party_field or "partner_id")[:1]

    def _work_party_lang(self):
        """The language to talk to the outside party in. Only Chinese may be installed, so a partner's lang says little;
        a company abroad is addressed in English."""
        party = self._work_party()
        country = party.commercial_partner_id.country_id.code
        if country and country not in ("CN", "TW", "HK", "MO", "SG"):
            return "en_US"
        return party.lang or "zh_CN"

    def _work_context_record(self):
        return {"name": self.display_name}

    def _work_context_details(self):
        return {}

    def _work_facts(self):
        return []

    def _work_external(self):
        return {"title": self.display_name, "subtitle": ""}

    def _work_confidential(self):
        return []

    def _work_actions(self):
        """Odoo actions the digital employee may draft on this case; each runs only when a person confirms it.
        A scenario adds its own with _work_check_<name>(params) -> (params, preview) and _work_do_<name>(params, proposal)."""
        return [{"name": "send_email", "description": "给外部联系人发一封邮件（Odoo 发出，留在记录的沟通记录里）",
                 "params": "subject 标题, body 正文"}]

    def _work_check_send_email(self, params):
        subject, body = (params.get("subject") or "").strip(), (params.get("body") or "").strip()
        if not subject or not body:
            raise UserError(_("邮件需要标题和正文。"))
        lowered = f"{subject}\n{body}".lower()
        leaked = [c for c in self._work_confidential() if c and str(c).lower() in lowered]
        if leaked:
            raise UserError(_("邮件里有不能对外的信息：%s", "、".join(leaked)))
        party = self._work_party()
        contact = party.child_ids[:1] or party
        if not contact.email:
            raise UserError(_("%s 没有邮箱。", contact.display_name))
        return {"subject": subject, "body": body, "partner_id": contact.id}, \
            _("收件人：%(to)s <%(email)s>\n标题：%(subject)s\n\n%(body)s", to=contact.name, email=contact.email,
              subject=subject, body=body)

    # an outgoing chat message the review gate held back: the chat sends it once a person approves it here or there

    def _work_check_send_message(self, params):
        text = (params.get("text") or "").strip()
        if not text:
            raise UserError(_("消息不能为空。"))
        reasons = "；".join(params.get("reasons") or [])
        return {"text": text, "draft_id": params.get("draft_id"), "reasons": params.get("reasons") or []}, \
            text + (_("\n\n没有直接发出：%s", reasons) if reasons else "")

    def _work_do_send_message(self, params, proposal):
        return {"text": _("已批准，消息会由群聊发出")}

    def _work_do_send_email(self, params, proposal):
        message = self.message_post(
            subject=params["subject"], body=Markup("<br/>").join(params["body"].splitlines()),
            partner_ids=[params["partner_id"]], message_type="comment", subtype_xmlid="mail.mt_comment")
        return {"text": _("邮件已发给 %s", self.env["res.partner"].browse(params["partner_id"]).name),
                "model": "mail.message", "id": message.id}

    def _work_identity(self):
        party = self._work_party()
        return {"tokens": [t for t in (party.commercial_company_name or party.name, party.name, self.display_name) if t],
                "figures": []}

    # the digital employee's methods

    def _agent_case(self):
        if not is_agent(self.env):
            raise AccessError(_("这些方法只给数字员工调用。"))
        self.ensure_one()
        self.check_access("read")
        record = self.sudo()
        if not record.work_step_ids:
            raise UserError(_("%s 还没有启动数字员工流程。", record.display_name))
        return record

    def _agent_step(self, step_code):
        record = self._agent_case()
        current = record.work_current_step_id
        if not current:
            raise UserError(_("%s 的流程已全部完成。", record.display_name))
        if current.code != step_code:
            raise UserError(_("当前步骤是 %(code)s（%(name)s），不能操作 %(asked)s。",
                              code=current.code, name=current.name, asked=step_code))
        if current.state == "waiting":
            raise UserError(_("步骤 %s 正在等人决定，人决定之前不能再改。", current.name))
        return record, current

    def _agent_note(self, step, title, text):
        self.message_post(
            body=Markup("<b>[数字员工] %s · %s</b><br/>%s") % (step.name, title, Markup("<br/>").join((text or "").splitlines())),
            subtype_xmlid="mail.mt_note",
        )

    def _work_step_json(self, step):
        return {"code": step.code, "name": step.name, "short_name": step.stage_id.short_name or step.name,
                "state": step.state, "needs_human": step.needs_human, "summary": step.agent_summary or "",
                "question": step.question or "", "options": lines(step.options),
                "recommendation": step.recommendation or "", "decision": step.decision or "",
                "human_note": step.human_note or ""}

    @api.model
    def work_agent_cases(self):
        """Every record of this model with a playbook started: who decides, who the outside party is, where it stands."""
        if not is_agent(self.env):
            raise AccessError(_("这些方法只给数字员工调用。"))
        ids = self.env["work.step"].sudo().search([("res_model", "=", self._name)]).mapped("res_id")
        readable = self.search([("id", "in", sorted(set(ids)))], order="id")
        result = []
        for record in readable.sudo():
            owner, party = record._work_owner(), record._work_party()
            contact = party.child_ids[:1] or party
            step = record.work_current_step_id
            result.append({
                "model": record._name, "id": record.id, "name": record.display_name,
                "playbook": record.work_playbook_id.code, "owner_login": owner.login, "owner": owner.name,
                "party_id": party.id, "party": party.display_name, "contact": contact.name,
                "contact_lang": contact.lang or party.lang or "en_US",
                "step": step.code or None, "state": step.state or "done",
            })
        return result

    def work_agent_context(self):
        """Everything the digital employee needs about one case, read in one transaction."""
        record = self._agent_case()
        playbook = record.work_playbook_id
        Message = self.env["mail.message"].sudo()
        thread = [("model", "=", record._name), ("res_id", "=", record.id)]
        messages = Message.search(thread + [("message_type", "in", ("email", "comment")), ("body", "not ilike", CHAT_TAG)],
                                  order="id desc", limit=20)
        chat = Message.search(thread + [("body", "ilike", CHAT_TAG)], order="id desc", limit=30)
        owner, party = record._work_owner(), record._work_party()
        current = record.work_current_step_id
        external = record._work_external()
        external["steps"] = [{"code": s.code, "name": s.stage_id.external_name or s.name, "done": s.state == "done"}
                             for s in record.work_step_ids]
        return {
            "case": {"model": record._name, "id": record.id, "name": record.display_name, "playbook": playbook.code,
                     "owner": owner.name, "owner_login": owner.login, "party": party.display_name,
                     "party_lang": record._work_party_lang()},
            "playbook": {"code": playbook.code, "name": playbook.name, "role": playbook.role or "",
                         "external_rules": playbook.external_rules or "", "party_label": playbook.party_label or "",
                         "agent_fields": lines(playbook.agent_fields),
                         "field_choices": {f: [v for v, _label in record._fields[f]._description_selection(self.env)]
                                           for f in lines(playbook.agent_fields)
                                           if f in record._fields and record._fields[f].type == "selection"}},
            "record": record._work_context_record(),
            "details": record._work_context_details(),
            "facts": record._work_facts(),
            "external": external,
            "confidential": [c for c in record._work_confidential() if c],
            "identity": record._work_identity(),
            "steps": [record._work_step_json(s) for s in record.work_step_ids],
            "actions": record._work_actions(),
            "proposals": [p._json() for p in self.env["work.proposal"].sudo().search(
                [("res_model", "=", record._name), ("res_id", "=", record.id)], limit=40)],
            "current_step": current and {
                **record._work_step_json(current),
                "goal": current.stage_id.goal or "", "agent_task": current.stage_id.agent_task or "",
                "human_question": current.stage_id.human_question or "",
            } or None,
            "messages": [{
                "id": m.id, "date": fields.Datetime.to_string(m.date),
                "author": m.author_id.display_name or m.email_from or "", "internal": bool(m.subtype_id.internal),
                "text": html2plaintext(m.body or "")[:3000], "attachments": m.attachment_ids.mapped("name"),
            } for m in reversed(messages)],
            "chat": [html2plaintext(m.body or "").replace(CHAT_TAG, "", 1).strip() for m in reversed(chat)],
        }

    def work_agent_log_chat(self, lines):
        """Keep the case's group chat on the record as internal notes: lines are {"author", "text"}."""
        record = self._agent_case()
        for line in lines or []:
            record.message_post(
                body=Markup("%s <b>%s</b>：%s") % (CHAT_TAG, line.get("author", ""),
                                                  Markup("<br/>").join((line.get("text") or "").splitlines())),
                subtype_xmlid="mail.mt_note",
            )
        return {"logged": len(lines or [])}

    def work_agent_report(self, step_code, summary, values=None):
        """Record what the digital employee found on the current step, and fill in fields the playbook allows."""
        record, step = self._agent_step(step_code)
        values = values or {}
        refused = sorted(set(values) - set(lines(record.work_playbook_id.agent_fields)))
        if refused:
            raise UserError(_("数字员工不能填写这些字段：%s", ", ".join(refused)))
        if values:
            record.write(values)
        step.write({"state": "working", "agent_summary": summary})
        record._agent_note(step, _("进展"), summary)
        return {"step": step.code, "state": step.state, "written": sorted(values)}

    def work_agent_complete(self, step_code, summary):
        """Close a step the digital employee may close itself; a step that needs a person is refused."""
        record, step = self._agent_step(step_code)
        if step.needs_human:
            raise UserError(_("步骤 %s 需要人来拍板，请用 work_agent_request_decision 提出问题。", step.name))
        step.write({"state": "done", "agent_summary": summary, "decision": _("数字员工完成"),
                    "decided_at": fields.Datetime.now()})
        record._agent_note(step, _("已完成"), summary)
        return {"step": step.code, "state": "done", "next": record.work_current_step_id.code or None}

    def work_agent_request_decision(self, step_code, question, options, recommendation, summary=""):
        """Ask the owner one concrete question, with at most three options and a recommendation."""
        record, step = self._agent_step(step_code)
        options = [o.strip() for o in options or [] if o and o.strip()]
        if not question or not options or len(options) > MAX_OPTIONS:
            raise UserError(_("需要一个问题和 1 到 %s 个选项。", MAX_OPTIONS))
        if recommendation not in options:
            raise UserError(_("推荐必须是选项之一。"))
        owner = record._work_owner()
        step._close_activity(_("问题已更新"))
        activity = record.activity_schedule(
            "mail.mail_activity_data_todo", summary=_("待决定：%s", step.name),
            note=Markup("%s<br/>%s %s") % (question, _("推荐："), recommendation), user_id=owner.id)
        step.write({
            "state": "waiting", "question": question, "options": "\n".join(options),
            "recommendation": recommendation, "agent_summary": summary or step.agent_summary,
            "human_note": False, "decision": False, "activity_id": activity.id,
        })
        text = "%s\n%s\n%s %s" % (summary, question, _("选项："), " / ".join(options)) if summary else question
        record._agent_note(step, _("请 %s 决定", owner.name), "%s\n%s %s" % (text, _("推荐："), recommendation))
        self.env["work.assistant"]._post_card(step)
        return {"step": step.code, "state": "waiting", "asked": owner.name}

    def work_agent_block(self, step_code, reason):
        """The step cannot go on until a person acts outside the playbook."""
        record, step = self._agent_step(step_code)
        owner = record._work_owner()
        step._close_activity(_("已受阻"))
        activity = record.activity_schedule("mail.mail_activity_data_todo", summary=_("受阻：%s", step.name),
                                            note=reason, user_id=owner.id)
        step.write({"state": "blocked", "agent_summary": reason, "activity_id": activity.id})
        record._agent_note(step, _("受阻"), reason)
        self.env["work.assistant"]._tell(record, "%s\n%s" % (_("「%s」受阻了：", step.name), reason))
        return {"step": step.code, "state": "blocked"}

    def work_agent_tell_owner(self, text, who=""):
        """The case's digital employee says something to the owner only; it arrives in their chat with the digital employee."""
        record = self._agent_case()
        message = self.env["work.assistant"]._tell(record, text, who)
        return {"sent": bool(message)}

    def work_agent_log_actions(self, entries):
        """The digital employee's tool calls on this case, for the action ledger: entries are
        {"tool", "risk", "ok", "what", "detail", "error", "args", "result"}."""
        record = self._agent_case()
        Ledger = self.env["work.action"]
        for e in entries or []:
            if e.get("tool", "").startswith("human_"):
                continue
            Ledger._log(record, actor=e.get("actor") or self.env.user.name, tool=e.get("tool") or "?",
                        risk=e.get("risk") if e.get("risk") in ("R0", "R1", "R2", "R3") else "R1", ok=bool(e.get("ok", True)),
                        what=e.get("what") or e.get("tool") or "?", detail=e.get("detail") or "", error=e.get("error") or "",
                        args=e.get("args"), result=e.get("result"))
        return {"logged": len(entries or [])}

    def work_agent_hold_message(self, text, draft_id, reasons=None, summary=""):
        """A message to the outside party that the review gate held: it waits here for the owner like any drafted action."""
        record = self._agent_case()
        params, preview = record._work_check_send_message({"text": text, "draft_id": draft_id, "reasons": reasons})
        proposal = self.env["work.proposal"].sudo().create({
            "res_model": record._name, "res_id": record.id, "playbook_id": record.work_playbook_id.id,
            "action": "send_message", "action_name": _("对外发消息"),
            "summary": summary or _("AI 想对%s说这条，等你批准", record.work_playbook_id.party_label or _("对方")),
            "params": json.dumps(params, ensure_ascii=False), "preview": preview,
        })
        owner = record._work_owner()
        proposal.activity_id = record.activity_schedule(
            "mail.mail_activity_data_todo", summary=_("待批准：对外消息"), note=text[:500], user_id=owner.id)
        self.env["work.assistant"]._post_card(proposal)
        return {"proposal": proposal.id}

    def work_agent_withdraw_message(self, proposal_id, reason=""):
        """The digital employee takes back a held message, or the chat closed it: the waiting card goes away."""
        record = self._agent_case()
        proposal = self.env["work.proposal"].sudo().search([
            ("id", "=", proposal_id), ("res_model", "=", record._name), ("res_id", "=", record.id),
            ("action", "=", "send_message"), ("state", "=", "pending")])
        if proposal:
            proposal._close_activity(_("已撤回"))
            proposal.write({"state": "rejected", "result": _("撤回：%s", reason or _("群聊里已处理"))})
        return {"withdrawn": bool(proposal)}

    def work_agent_propose(self, action, summary, params=None):
        """Draft an Odoo action for the owner to confirm. Parameters are checked now, so the card shows what will run."""
        record = self._agent_case()
        if action not in {a["name"] for a in record._work_actions()}:
            raise UserError(_("这个事项没有动作 %s。", action))
        params, preview = getattr(record, f"_work_check_{action}")(params or {})
        name = next(a["description"] for a in record._work_actions() if a["name"] == action).split("（")[0]
        Proposal = self.env["work.proposal"].sudo()
        if Proposal.search_count([("res_model", "=", record._name), ("res_id", "=", record.id),
                                  ("action", "=", action), ("state", "=", "pending")]):
            raise UserError(_("已经有一个「%s」在等确认，等人处理完再起草。", name))
        owner = record._work_owner()
        proposal = Proposal.create({
            "res_model": record._name, "res_id": record.id, "playbook_id": record.work_playbook_id.id,
            "action": action, "action_name": name, "summary": summary, "params": json.dumps(params, ensure_ascii=False),
            "preview": preview,
        })
        proposal.activity_id = record.activity_schedule(
            "mail.mail_activity_data_todo", summary=_("待确认：%s", name), note=summary, user_id=owner.id)
        record._agent_note(record.work_current_step_id or record.work_step_ids[-1:], _("起草了「%s」，请 %s 确认", name, owner.name),
                           "%s\n\n%s" % (summary, preview))
        self.env["work.assistant"]._post_card(proposal)
        return {"proposal": proposal.id, "state": "pending", "asked": owner.name, "preview": preview}
