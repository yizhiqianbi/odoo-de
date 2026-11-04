"""Odoo Discuss as the front door for internal people: what they write to the digital employee there wakes their
assistant agent, and what the agents have to tell them is posted back there."""
import asyncio
import html
import logging
import re
from collections import defaultdict

from . import config
from .odoo import Assistant, OdooError

log = logging.getLogger(__name__)


def to_html(text):
    """The model's plain text, with bold, bullets and links into Odoo, as a message body."""
    lines = []
    for line in (text or "").strip().splitlines():
        s = html.escape(line.rstrip(), quote=False)
        heading = re.match(r"^\s*#+\s*(.*)$", s)
        if heading:
            s = f"<b>{heading.group(1)}</b>"
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"\[([^\]]+)\]\((/odoo/[\w./,-]+)\)", r'<a href="\2">\1</a>', s)
        s = re.sub(r"^\s*[-*]\s+", "• ", s)
        lines.append(s)
    return "<p>" + "<br/>".join(lines) + "</p>"


class Desk:
    def __init__(self, svc):
        self.svc = svc
        self.client = Assistant(config.ASSISTANT_KEY_FILE) if config.ASSISTANT_KEY_FILE.exists() else None
        self.unanswered = defaultdict(list)
        self.wake = asyncio.Event()
        self.last_file = config.HOME / "desk_last_id"

    def login_of(self, user):
        return config.USERNAME_TO_LOGIN.get(user["username"], user["username"])

    def user_of(self, login):
        return self.svc.store.user_by_username(config.LOGIN_TO_USERNAME.get(login, login))

    def take(self, agent_key):
        return self.unanswered.pop(agent_key, [])

    async def say(self, user, text, ref=False):
        if not self.client or not text:
            return
        try:
            await self.client.say(self.login_of(user), to_html(text), ref)
        except OdooError as e:
            log.warning("could not post to %s in Discuss: %s", user["username"], e)

    async def typing(self, user, on):
        if self.client:
            try:
                await self.client.typing(self.login_of(user), on)
            except OdooError:
                pass

    async def loop(self):
        if not self.client:
            log.warning("no assistant key at %s; Discuss is not connected", config.ASSISTANT_KEY_FILE)
            return
        last = int(self.last_file.read_text()) if self.last_file.exists() else 0
        while True:
            try:
                res = await self.client.inbox(last)
            except OdooError as e:
                log.warning("Discuss inbox: %s", e)
                await asyncio.sleep(10)
                continue
            for message in res["messages"]:
                await self.receive(message)
            if res["last_id"] != last:
                last = res["last_id"]
                self.last_file.write_text(str(last))
            try:
                await asyncio.wait_for(self.wake.wait(), config.DESK_POLL_SECONDS)
            except TimeoutError:
                pass
            self.wake.clear()

    async def receive(self, message):
        user = self.user_of(message["login"])
        agent = user and self.svc.store.agent(f"assistant:{user['username']}")
        if not agent or not agent["enabled"]:
            try:
                await self.client.say(message["login"], to_html("你还没有开通数字员工助理，请找管理员开通。"))
            except OdooError as e:
                log.warning("could not answer %s: %s", message["login"], e)
            return
        self.unanswered[agent["key"]].append(message)
        text = message["text"]
        if message["attachments"]:
            text += f"（附件：{'、'.join(message['attachments'])}）"
        if message.get("reply_to"):
            text += f"（这是在回复：{message['reply_to']['text'][:300]}）"
        self.svc.trigger_agent(agent["key"], f"{message['name']} 在讨论里对你说：{text}")
