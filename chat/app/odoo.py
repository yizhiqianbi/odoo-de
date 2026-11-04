"""Odoo JSON-2 client. Each identity has its own key; what it may do is decided inside Odoo, not here."""
import httpx

from . import config


class OdooError(Exception):
    pass


class Odoo:
    def __init__(self, key_file, agent):
        self.key = open(key_file).read().strip()
        self.http = httpx.AsyncClient(base_url=f"{config.ODOO_URL}/json/2", timeout=60, headers={
            "Authorization": f"bearer {self.key}", "X-Odoo-Database": config.ODOO_DB, "User-Agent": agent})

    async def call(self, model, method, **body):
        try:
            res = await self.http.post(f"/{model}/{method}", json=body)
        except httpx.TransportError as e:
            raise OdooError(f"连不上 Odoo：{e.__class__.__name__}") from e
        if res.status_code >= 400:
            try:
                message = res.json().get("message")
            except ValueError:
                message = res.text[:300]
            raise OdooError(message or f"Odoo HTTP {res.status_code}")
        return res.json()

    async def case(self, group, method, **body):
        return await self.call(group["res_model"], method, ids=[group["res_id"]], **body)


class Bot(Odoo):
    """One playbook's digital employee: read-only on its records except through the guarded work_agent_* methods."""

    def __init__(self, key_file):
        super().__init__(key_file, "work-chat-ai")

    async def cases(self, model):
        return await self.call(model, "work_agent_cases")

    async def context(self, group):
        return await self.case(group, "work_agent_context")

    async def log_chat(self, group, lines):
        return await self.case(group, "work_agent_log_chat", lines=lines)


class Sales(Odoo):
    """One person who decides, with their own key; used only for decisions they click in the chat."""

    def __init__(self, key_file):
        super().__init__(key_file, "work-chat-person")

    async def decide(self, group, step_code, approve, choice=None, note=""):
        ids = await self.call("work.step", "search", domain=[
            ["res_model", "=", group["res_model"]], ["res_id", "=", group["res_id"]], ["code", "=", step_code]])
        if not ids:
            raise OdooError(f"找不到 {group['order_name']} 的步骤 {step_code}")
        await self.call("work.step", "write", ids=ids, vals={"human_note": note or False})
        if approve:
            return await self.call("work.step", "action_approve", ids=ids, choice=choice or None)
        return await self.call("work.step", "action_reject", ids=ids)

    async def proposal(self, proposal_id, approve, note=""):
        await self.call("work.proposal", "write", ids=[proposal_id], vals={"human_note": note or False})
        return await self.call("work.proposal", "action_confirm" if approve else "action_reject", ids=[proposal_id])

    async def desk(self):
        return await self.call("work.playbook", "workbench_data")


class Assistant(Odoo):
    """The digital employee in Odoo Discuss: it only reads its private chats and posts in them."""

    def __init__(self, key_file):
        super().__init__(key_file, "work-chat-assistant")

    async def inbox(self, after_id):
        return await self.call("work.assistant", "inbox", after_id=after_id)

    async def history(self, login, limit=16):
        return await self.call("work.assistant", "history", login=login, limit=limit)

    async def say(self, login, html, ref=False):
        return await self.call("work.assistant", "say", login=login, html=html, ref=ref)

    async def typing(self, login, on=True):
        return await self.call("work.assistant", "typing", login=login, on=on)

    async def post_card(self, login, model, res_id):
        return await self.call("work.assistant", "post_card", login=login, model=model, res_id=res_id)
