from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from odoo.addons.work_case.models.guards import refuse_agent

BEIJING = ZoneInfo("Asia/Shanghai")


class HrApplicant(models.Model):
    _name = "hr.applicant"
    _inherit = ["hr.applicant", "work.case.mixin"]

    hr_interview_slot = fields.Char(string="约定面试时间", copy=False)
    hr_feedback_summary = fields.Text(string="面试反馈汇总", copy=False)
    hr_onboarding_note = fields.Text(string="入职准备", copy=False)

    def create_employee_from_applicant(self):
        refuse_agent(self.env, _("创建员工"))
        return super().create_employee_from_applicant()

    def archive_applicant(self):
        refuse_agent(self.env, _("拒绝候选人"))
        return super().archive_applicant()

    # what the HR playbook shows the digital employee, the recruiter and the candidate

    def _work_context_record(self):
        return {
            "name": self.partner_name or self.display_name, "job": self.job_id.name or "",
            "department": self.department_id.name or "", "stage": self.stage_id.name or "",
            "recruiter": self.recruiter_id.name or "", "email": self.email_from or "",
            "phone": self.partner_phone or "", "degree": self.type_id.name or "",
            "availability": fields.Date.to_string(self.availability) or "",
            "applied_on": fields.Datetime.to_string(self.create_date),
            "interviewers": self.interviewer_ids.mapped("name"),
            "salary_expected": self.salary_expected, "salary_proposed": self.salary_proposed,
            "currency": self.currency_id.name,
            "hr_interview_slot": self.hr_interview_slot or "", "hr_feedback_summary": self.hr_feedback_summary or "",
            "hr_onboarding_note": self.hr_onboarding_note or "",
        }

    def _work_context_details(self):
        return {
            "notes": html2plaintext(self.applicant_notes or "")[:4000],
            "job_description": html2plaintext(self.job_id.description or "")[:3000],
            "meetings": [{"name": m.name, "start": fields.Datetime.to_string(m.start),
                          "attendees": m.partner_ids.mapped("name")} for m in self.meeting_ids],
            "attachments": self.attachment_ids.mapped("name"),
        }

    def _work_facts(self):
        facts = [("候选人", self.partner_name), ("职位", self.job_id.name), ("阶段", self.stage_id.name),
                 ("招聘负责人", self.recruiter_id.name), ("期望薪资", self.salary_expected and f"{self.salary_expected:,.0f}"),
                 ("拟定薪资", self.salary_proposed and f"{self.salary_proposed:,.0f}"), ("可到岗", self.availability),
                 ("面试时间", self.hr_interview_slot), ("面试官", ", ".join(self.interviewer_ids.mapped("name")))]
        return [{"label": k, "value": str(v)} for k, v in facts if v]

    def _work_external(self):
        return {"title": f"应聘：{self.job_id.name or self.display_name}",
                "subtitle": "，".join(x for x in (self.recruiter_id.name and f"对接人 {self.recruiter_id.name}",
                                                  self.hr_interview_slot and f"面试 {self.hr_interview_slot}") if x)}

    def _work_confidential(self):
        """Who else is applying stays inside the company, and so does the pay we may offer until the offer is approved."""
        others = self.sudo().search([("job_id", "=", self.job_id.id), ("id", "!=", self.id)]) if self.job_id else []
        values = [a.partner_name for a in others if a.partner_name]
        offer = self.work_step_ids.filtered(lambda s: s.code == "offer")
        if self.salary_proposed and offer.state != "done":
            values += [f"{self.salary_proposed:,.0f}", f"{self.salary_proposed:.0f}"]
        return values

    def _work_identity(self):
        tokens = [self.partner_name, self.partner_id.name, self.email_from]
        return {"tokens": [t for t in tokens if t], "figures": [self.salary_expected] if self.salary_expected else []}

    # Odoo actions the digital employee may draft for the recruiter

    def _work_actions(self):
        return super()._work_actions() + [{
            "name": "interview_event",
            "description": "在 Odoo 日历里建面试会议并邀请面试官和候选人（确认后创建）",
            "params": "start 北京时间 YYYY-MM-DD HH:MM, duration_minutes 分钟（默认 60）, location 地点或线上链接",
        }]

    def _work_check_interview_event(self, params):
        try:
            start = datetime.strptime((params.get("start") or "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
        except ValueError:
            raise UserError(_("start 要写成北京时间 YYYY-MM-DD HH:MM。"))
        if start < datetime.now(BEIJING):
            raise UserError(_("面试时间已经过去了。"))
        minutes = int(params.get("duration_minutes") or 60)
        if not 15 <= minutes <= 240:
            raise UserError(_("面试时长要在 15 到 240 分钟之间。"))
        if not self.interviewer_ids:
            raise UserError(_("这个职位还没有面试官，请招聘负责人先指定。"))
        location = (params.get("location") or "").strip()
        clean = {"start": start.strftime("%Y-%m-%d %H:%M"), "duration_minutes": minutes, "location": location}
        preview = _("面试：%(name)s · %(job)s\n时间：%(start)s 起 %(minutes)s 分钟（北京时间）\n地点：%(where)s\n邀请：%(who)s",
                    name=self.partner_name, job=self.job_id.name, start=clean["start"], minutes=minutes,
                    where=location or _("未填"), who="、".join(self.interviewer_ids.mapped("name") + [self.partner_name]))
        return clean, preview

    def _work_do_interview_event(self, params, proposal):
        start = datetime.strptime(params["start"], "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING).astimezone(timezone.utc)
        start = start.replace(tzinfo=None)
        partners = self.interviewer_ids.partner_id | self.partner_id
        # invitations queue with the rest of the outgoing mail instead of being sent inside the confirming request
        event = self.env["calendar.event"].with_context(no_mail_to_attendees=True).create({
            "name": _("面试：%(name)s · %(job)s", name=self.partner_name, job=self.job_id.name),
            "start": start, "stop": start + timedelta(minutes=params["duration_minutes"]),
            "applicant_id": self.id, "partner_ids": [(6, 0, partners.ids)], "user_id": self._work_owner().id,
            "location": params["location"] or False,
        })
        self.sudo().hr_interview_slot = _("%s（北京时间）", params["start"])
        event.attendee_ids.filtered(lambda a: a.partner_id != self.env.user.partner_id)._notify_attendees(
            self.env.ref("calendar.calendar_template_meeting_invitation", raise_if_not_found=False), force_send=False)
        return {"text": _("已在日历建好面试 %s，邀请了 %s 人", params["start"], len(partners)),
                "model": "calendar.event", "id": event.id}
