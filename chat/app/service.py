"""Ties the group chats to their cases in Odoo and to the agents, carries the agent protocol, and runs the follow-up loop.

A group is about one case: a record in Odoo worked by one playbook. Each playbook has its own bot, so one scenario's
digital employee never holds another scenario's rights."""
import asyncio
import json
import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

from . import agents, ai_case, ai_customer, ai_desk, config, gate
from .desk import Desk
from .errors import ChatError
from .hub import Hub
from .odoo import Bot, OdooError, Sales
from .playbooks import PLAYBOOKS, labels
from .protocol import Denied, Policy, short_name
from .store import Store, now, parse

log = logging.getLogger(__name__)
STATE = {"todo": "待开始", "working": "进行中", "waiting": "待人工决定", "blocked": "受阻", "done": "已完成"}
SHIPPED = {"loading", "advice", "documents", "archive"}
MAX_SILENCE_CHASES = 2
RUN_LIMIT = (6, 600)  # runs per agent per window, so agents waking each other cannot spin
RUN_LIMIT_ASSISTANT = (30, 600)  # assistants also answer people chatting with them


def case_key(value):
    """Agents write "customer:67", "customer 67", "67" or "ap-12"; the key is what follows the prefix."""
    if not value:
        return None
    return re.sub(r"^\s*customer\s*[:：]?\s*", "", str(value).strip(), flags=re.I) or None


def distinctive(token):
    """Long enough to identify someone: four letters, or two CJK characters for Chinese names."""
    return bool(token) and (len(token) >= 4 or (len(token) >= 2 and re.search(r"[\u4e00-\u9fff]", token)))


def party_lang(ctx):
    lang = (ctx.get("case") or {}).get("party_lang") or ""
    return "zh" if lang.startswith("zh") else "en"


def describe_action(tool, args, result):
    """A ledger line in words: what the digital employee did, and the detail that matters."""
    r = result if isinstance(result, dict) else {}
    if tool == "send_to_group":
        if r.get("sent"):
            return "对外发出一条消息", args.get("text", "")
        return "对外消息转成草稿，等人批准", "；".join(r.get("held_because") or []) or args.get("text", "")
    if tool == "work_report":
        fields = "，".join(f"{k}={v}" for k, v in (args.get("values") or {}).items())
        return f"记录进展：{args.get('step_code', '')}", "；".join(x for x in (args.get("summary"), fields and f"写入 {fields}") if x)
    simple = {
        "withdraw_draft": ("撤回草稿", "reason"), "tell_owner": ("给负责人留言", "text"),
        "work_complete": (f"完成步骤：{args.get('step_code', '')}", "summary"),
        "work_ask": (f"请人决定：{args.get('step_code', '')}", "question"),
        "work_block": (f"标记受阻：{args.get('step_code', '')}", "reason"),
        "work_propose": (f"起草 Odoo 动作：{args.get('action', '')}", "summary"),
        "read_resource": (f"查阅资料：{args.get('resource', '')}", "customer"),
        "ask_agent": (f"咨询 {args.get('to') or args.get('agent') or ''}", "question"),
        "schedule_followup": (f"记一条待跟进，{args.get('minutes', '')} 分钟后", "what"),
        "close_followup": ("关掉一条待跟进", "result"),
    }
    if tool in simple:
        what, key = simple[tool]
        detail = r.get("preview") if tool == "work_propose" and r.get("preview") else args.get(key) or ""
        return what, str(detail)
    return tool, json.dumps(args, ensure_ascii=False)[:500]


def fingerprint(ctx):
    steps = [[s["code"], s["name"], s["state"], s["decision"], s["question"]] for s in ctx["steps"]]
    return json.dumps({"order": ctx["record"].get("state", ""), "steps": steps}, ensure_ascii=False)


def describe_changes(old_fp, ctx):
    old = {s[0]: s for s in json.loads(old_fp)["steps"]}
    changes = []
    for s in ctx["steps"]:
        before = old.get(s["code"])
        if not before or before[2] != s["state"] or before[3] != s["decision"]:
            was = STATE.get(before[2], before[2]) if before else "无"
            changes.append(f"「{s['name']}」{was} → {STATE.get(s['state'], s['state'])}"
                           + (f"，决定：{s['decision']}" if s["decision"] else ""))
    return "；".join(changes) or "记录状态有变化"


class Service:
    def __init__(self):
        self.store = Store(config.DB_PATH)
        self.hub = Hub()
        self.bots = {code: Bot(p["key"]) for code, p in PLAYBOOKS.items() if p["key"].exists()}
        self.sales_keys = {}
        self.policy = Policy(self.store, self.facts)
        self.ctx = {}
        self.products = []
        self.status = {}
        self.pending = defaultdict(list)
        self.runs = {}
        self.ran = defaultdict(deque)
        self.sims = {}
        self.chases = {}
        self.desk = Desk(self)

    # people and delivery

    def bot(self, group):
        return self.bots[group["playbook"]]

    def sales_client(self, user):
        if user["username"] not in self.sales_keys:
            self.sales_keys[user["username"]] = Sales(config.HOME / "keys" / f"{user['username']}.key")
        return self.sales_keys[user["username"]]

    def admins(self):
        return {u["id"] for u in self.store.users() if u["role"] == "admin"}

    async def send_group(self, group, event, internal=False):
        await self.hub.send(self.store.members(group), {**event, "group_id": group["id"]}, internal=internal)

    # messages

    async def post(self, group, author, body, audience="all", kind="text", status="", meta=None):
        message = self.store.add_message(group["id"], author, body, audience, kind, meta, status)
        await self.send_group(group, {"type": "message", "message": message}, internal=audience == "internal")
        if audience == "all" and kind == "text":
            asyncio.create_task(self._mirror(group, author, body, meta))
            if author == "customer":
                self.chases.pop(group["id"], None)
            else:
                self.schedule_sim(group)
        return message

    def log_action(self, group, agent, tool, args, result):
        from .ai_case import RISK
        ok = not (isinstance(result, dict) and result.get("error"))
        action = self.store.add_action(group["id"], agent, tool, RISK.get(tool, "R1"), args, result, ok)
        asyncio.create_task(self.hub.send(self.store.members(group) | self.admins(),
                                          {"type": "action", "action": action, "group_id": group["id"]}, internal=True))
        if not tool.startswith("human_"):
            asyncio.create_task(self._ledger(group, action))

    async def _ledger(self, group, action):
        """The same line in Odoo's action ledger, worded for people; Odoo logs people's own decisions itself."""
        what, detail = describe_action(action["tool"], action["args"] or {}, action["result"] or {})
        result = action["result"] if isinstance(action["result"], dict) else {"value": action["result"]}
        entry = {"actor": labels(group["playbook"])["ai"], "tool": action["tool"], "risk": action["risk"], "ok": bool(action["ok"]),
                 "what": what, "detail": "" if not action["ok"] else detail,
                 "error": (result.get("error") or "") if not action["ok"] else "",
                 "args": action["args"], "result": result}
        if not action["ok"] and detail:
            entry["detail"] = f"它想做的：{detail}"
        try:
            await self.bot(group).case(group, "work_agent_log_actions", entries=[entry])
        except OdooError as e:
            log.warning("could not write the ledger for %s: %s", group["order_name"], e)

    async def hold_in_odoo(self, group, draft):
        """A held outgoing message also waits in Odoo, so the owner can approve it from the workbench."""
        meta = draft["meta"] or {}
        try:
            res = await self.bot(group).case(group, "work_agent_hold_message", text=draft["body"], draft_id=draft["id"],
                                             reasons=meta.get("reasons") or [])
            meta = {**meta, "proposal": res["proposal"]}
        except OdooError as e:
            log.warning("could not hold draft #%s in Odoo: %s", draft["id"], e)
            meta = {**meta, "hold_error": str(e)}
        return self.store.update_message(draft["id"], meta=meta)

    async def _settle_in_odoo(self, group, user, draft, approve, note=""):
        pid = (draft["meta"] or {}).get("proposal")
        if not pid:
            return
        try:
            if user:
                await self.sales_client(user).proposal(pid, approve, note or ("" if approve else "在群聊里选择不发"))
            else:
                await self.bot(group).case(group, "work_agent_withdraw_message", proposal_id=pid, reason=note)
        except OdooError as e:
            log.warning("draft #%s: Odoo proposal %s not settled: %s", draft["id"], pid, e)

    async def update(self, group, message):
        await self.send_group(group, {"type": "message_update", "message": message},
                              internal=message["audience"] == "internal")

    async def _mirror(self, group, author, body, meta):
        names = labels(group["playbook"])
        name = {"customer": self.store.user(group["customer_id"])["name"], "ai": names["ai"]}.get(author)
        if author == "sales":
            name = (meta or {}).get("by") or names["owner"]
        try:
            await self.bot(group).log_chat(group, [{"author": name, "text": body}])
        except OdooError as e:
            log.warning("could not log chat to Odoo: %s", e)

    async def customer_says(self, group, text, simulated=False):
        await self.post(group, "customer", text, meta={"simulated": True} if simulated else None)
        self.trigger(group["id"], f"{labels(group['playbook'])['party']}在群里说：{text}")

    async def sales_says(self, group, user, text, internal):
        await self.post(group, "sales", text, audience="internal" if internal else "all", meta={"by": user["name"]})
        names = labels(group["playbook"])
        if internal:
            self.trigger(group["id"], f"{names['owner']} {user['name']} 只对你说：{text}", explicit=True)
        else:
            self.trigger(group["id"], f"{names['owner']} {user['name']} 在群里对{names['party']}说：{text}"
                                      "（通常你不用接话，除非有事要记录或跟进）")

    # running agents

    def trigger(self, group_id, reason, explicit=False):
        group = self.store.group(group_id)
        if not explicit and not group["ai_auto"]:
            return
        self.trigger_agent(f"customer:{group['customer_key']}", reason)

    def trigger_agent(self, key, reason):
        agent = self.store.agent(key)
        if not agent or not agent["enabled"]:
            return
        self.pending[key].append(reason)
        task = self.runs.get(key)
        if not task or task.done():
            self.runs[key] = asyncio.create_task(self._run_loop(key))

    async def _run_loop(self, key):
        await asyncio.sleep(1.5)
        agent = self.store.agent(key)
        count, window = RUN_LIMIT_ASSISTANT if agent and agent["kind"] == "assistant" else RUN_LIMIT
        while self.pending.get(key):
            ran = self.ran[key]
            while ran and time.monotonic() - ran[0] > window:
                ran.popleft()
            if len(ran) >= count:
                wait = window - (time.monotonic() - ran[0])
                log.warning("%s ran %d times in %ds; waiting %.0fs", key, count, window, wait)
                await asyncio.sleep(wait)
                continue
            ran.append(time.monotonic())
            reasons = self.pending.pop(key)
            agent = self.store.agent(key)
            group = self.store.group_by_customer(agent["customer_key"]) if agent["kind"] == "customer" else None
            await self.set_status(key, group, {"busy": True, "text": "；".join(reasons)[:160]})
            summary = ""
            try:
                if group:
                    result = await ai_case.run(self, group, reasons)
                elif agent["kind"] == "assistant":
                    result = await ai_desk.run(self, agent, reasons)
                else:
                    result = await agents.run_manager(self, agent, reasons)
                summary = result["summary"]
                log.info("%s ran: %s | %s", key, reasons, [(a["tool"], a["result"]) for a in result["actions"]])
            except Exception as e:
                log.exception("agent %s failed", key)
                summary = f"这次处理出错了：{e}"
            if group:
                await self.sync_case(group, quiet=True)
            await self.set_status(key, group, {"busy": False, "text": summary, "at": now().isoformat(timespec="seconds")})

    async def set_status(self, key, group, status):
        self.status[key] = status
        if group:
            await self.send_group(group, {"type": "status", "status": status}, internal=True)
        await self.push_agent_event({"type": "agent_status", "agent": key, "status": status}, key)

    # the agent protocol

    async def push_agent_event(self, event, *principals):
        users = set(self.admins())
        for p in principals:
            if p.startswith("user:"):
                users.add(int(p.split(":", 1)[1]))
            elif p.startswith("agent:assistant:") or p.startswith("assistant:"):
                agent = self.store.agent(p.removeprefix("agent:"))
                users.add(agent and agent["owner_user_id"])
        await self.hub.send(users, event)

    def principal_name(self, principal):
        kind, _, ident = principal.partition(":")
        found = self.store.agent(ident) if kind == "agent" else self.store.user(int(ident)) if kind == "user" else None
        return found["name"] if found else principal

    async def _deliver(self, envelope):
        names = {p: self.principal_name(p) for p in (envelope["sender"], envelope["recipient"])}
        await self.push_agent_event({"type": "envelope", "envelope": envelope, "names": names},
                                    envelope["sender"], envelope["recipient"])
        if envelope["status"] != "denied" and envelope["recipient"].startswith("agent:"):
            sender = envelope["sender"]
            verb = {"ask": "提问", "task": "任务", "answer": "回复"}[envelope["verb"]]
            self.trigger_agent(envelope["recipient"].split(":", 1)[1],
                               f"收到 {sender} 的{verb}（信封 #{envelope['id']}）：{envelope['body'][:300]}")

    async def _refuse(self, sender, recipient, verb, body, reason, reply_to=None):
        envelope = self.store.add_envelope(sender, recipient, verb, body, "denied", reason, reply_to)
        await self._deliver(envelope)
        raise Denied(reason)

    async def send_envelope(self, sender_key, verb, recipient_key, body):
        sender, recipient = f"agent:{sender_key}", f"agent:{recipient_key}"
        try:
            self.policy.may_send(sender_key, verb, recipient_key)
            self.policy.check_body(recipient, body)
        except Denied as e:
            await self._refuse(sender, recipient, verb, body, str(e))
        envelope = self.store.add_envelope(sender, recipient, verb, body, "open")
        await self._deliver(envelope)
        return envelope

    async def answer_envelope(self, key, envelope_id, text):
        me = f"agent:{key}"
        original = self.store.envelope(envelope_id)
        if not original or original["recipient"] != me or original["status"] != "open":
            raise Denied(f"信封 #{envelope_id} 不是发给你的待处理信封")
        try:
            self.policy.check_body(original["sender"], text)
        except Denied as e:
            await self._refuse(me, original["sender"], "answer", text, str(e), envelope_id)
        self.store.set_envelope_status(envelope_id, "answered")
        envelope = self.store.add_envelope(me, original["sender"], "answer", text, "delivered", reply_to=envelope_id)
        await self._deliver(envelope)

    async def report(self, key, text):
        agent = self.store.agent(key)
        targets = [agent["owner_user_id"]] if agent["kind"] == "assistant" else sorted(self.admins())
        if agent["kind"] == "customer" or not all(targets):
            raise Denied("事项 agent 请用 tell_owner 在群里给负责人留言")
        for user_id in targets:
            recipient = f"user:{user_id}"
            try:
                self.policy.check_body(recipient, text)
            except Denied as e:
                await self._refuse(f"agent:{key}", recipient, "report", text, str(e))
            await self._deliver(self.store.add_envelope(f"agent:{key}", recipient, "report", text, "delivered"))
            await self.desk.say(self.store.user(user_id), text if agent["kind"] == "assistant" else f"【{agent['name']}】{text}")

    async def human_to_agent(self, user, key, text):
        agent = self.store.agent(key)
        if not agent:
            raise ChatError("没有这个 agent")
        allowed = user["role"] == "admin" or (agent["kind"] == "assistant" and agent["owner_user_id"] == user["id"])
        if not allowed:
            raise ChatError("你只能给自己的助理 agent 下任务")
        envelope = self.store.add_envelope(f"user:{user['id']}", f"agent:{key}", "task", text, "open")
        await self._deliver(envelope)
        return envelope

    async def read_resource(self, key, resource, customer=None):
        customer = case_key(customer)
        target = f"resource:{resource}" + (f"@{customer}" if customer else "")
        try:
            level = self.policy.check_read(key, resource, customer)
        except Denied as e:
            await self._refuse(f"agent:{key}", target, "read", "", str(e))
        data = self.resource_data(resource, customer)
        await self._deliver(self.store.add_envelope(f"agent:{key}", target, "read", "", "delivered", level=level))
        return data

    def cases(self, playbook):
        return [(group, self.ctx[group["id"]]) for group in self.store.all_groups()
                if group["playbook"] == playbook and group["id"] in self.ctx]

    def resource_data(self, resource, customer=None):
        if resource == "catalog":
            return [{k: p[k] for k in ("code", "name", "list_price")} for p in self.products]
        if resource == "costs":
            return [{k: p[k] for k in ("code", "name", "cost")} for p in self.products]
        if resource == "factory_load":
            load = defaultdict(lambda: {"qty": 0, "orders": 0})
            for _, ctx in self.cases("trade"):
                step = (ctx.get("current_step") or {}).get("code")
                if not step or step in SHIPPED:
                    continue
                record = ctx["record"]
                cell = load[(record.get("factory") or "未指定", (record.get("commitment_date") or "")[:7] or "未定")]
                cell["qty"] += sum(line["qty"] for line in ctx["details"].get("lines", []))
                cell["orders"] += 1
            return [{"factory": f, "delivery_month": m, **v} for (f, m), v in sorted(load.items())]
        if resource == "payment_plan":
            return sorted(({"payment_date": c["record"]["ap_payment_date"], "due_date": c["record"]["due_date"],
                            "amount": c["record"]["amount_total"], "currency": c["record"]["currency"]}
                           for _, c in self.cases("ap") if c["record"].get("ap_payment_date")),
                          key=lambda r: r["payment_date"])
        if resource == "job_openings":
            jobs = {}
            for _, c in self.cases("hr"):
                jobs.setdefault(c["record"]["job"], c["details"].get("job_description", ""))
            return [{"job": job, "requirements": text} for job, text in sorted(jobs.items())]
        if resource == "pipeline":
            counts = defaultdict(lambda: defaultdict(int))
            for _, c in self.cases("hr"):
                counts[c["record"]["job"]][(c.get("current_step") or {}).get("name", "已完成")] += 1
            return [{"job": job, "by_step": dict(steps)} for job, steps in sorted(counts.items())]
        group = self.store.group_by_customer(customer)
        if not group:
            raise Denied(f"没有事项 {customer}")
        if resource == "customer_chat":
            return [{"from": m["author"], "internal": m["audience"] == "internal", "kind": m["kind"], "text": m["body"][:500]}
                    for m in self.store.messages(group["id"], limit=20)]
        ctx = self.ctx.get(group["id"]) or {}
        if resource == "vendor_ledger":
            if group["playbook"] != "ap":
                raise Denied("vendor_ledger 只适用于应付账单")
            party = ctx.get("case", {}).get("party")
            return [{"bill": c["record"]["vendor_ref"], "amount": c["record"]["amount_total"],
                     "match": c["record"]["ap_match_status"], "step": (c.get("current_step") or {}).get("name", "已完成"),
                     "payment_state": c["record"]["payment_state"]}
                    for _, c in self.cases("ap") if c["case"]["party"] == party]
        return {
            "case": ctx.get("case", {}).get("name"), "playbook": group["playbook"],
            "facts": ctx.get("facts", []),
            "current_step": ctx.get("current_step") and {k: ctx["current_step"][k] for k in ("code", "name", "state")},
            "waiting": [{"step": s["name"], "question": s["question"]} for s in ctx.get("steps", []) if s["state"] == "waiting"],
            "followups": [f["what"] for f in self.store.followups(group["id"])],
            "owners": [s["name"] for s in self.store.salespeople(group)],
        }

    def facts(self):
        """What identifies each case's outside party, plus public and secret figures, for the protocol's content checks."""
        cases = {}
        for group in self.store.all_groups():
            ctx = self.ctx.get(group["id"])
            if not ctx:
                continue
            identity = ctx.get("identity") or {}
            tokens = set(identity.get("tokens", [])) | {self.store.user(group["customer_id"])["name"]}
            tokens |= {short_name(t) for t in tokens}
            cases[group["customer_key"]] = {"tokens": {t for t in tokens if distinctive(t)},
                                            "figures": gate.figures(" ".join(str(v) for v in identity.get("figures", [])))}
        return {"customers": cases,
                "public": gate.figures(" ".join(str(p["list_price"]) for p in self.products)),
                "secret": gate.figures(" ".join(str(p["cost"]) for p in self.products))}

    async def load_products(self):
        if "trade" not in self.bots:
            return
        try:
            lines = await self.bots["trade"].call("sale.order.line", "search_read", fields=["product_id"],
                                        domain=[["order_id.work_playbook_id.code", "=", "trade"], ["product_id", "!=", False]])
            ids = sorted({l["product_id"][0] if isinstance(l["product_id"], list) else l["product_id"] for l in lines})
            rows = await self.bots["trade"].call("product.product", "read", ids=ids,
                                       fields=["default_code", "name", "list_price", "standard_price"])
            self.products = [{"code": r["default_code"], "name": r["name"], "list_price": r["list_price"],
                              "cost": r["standard_price"]} for r in rows]
        except OdooError as e:
            log.warning("could not load products: %s", e)

    # the simulated customer

    def schedule_sim(self, group):
        if not group["sim_customer"]:
            return
        task = self.sims.get(group["id"])
        if task and not task.done():
            task.cancel()
        self.sims[group["id"]] = asyncio.create_task(self._sim(group["id"]))

    async def _sim(self, group_id):
        await asyncio.sleep(config.SIM_REPLY_DELAY)
        group = self.store.group(group_id)
        count, seconds = config.SIM_REPLY_LIMIT
        if not group["sim_customer"] or self.store.count_since(group_id, "customer", seconds) >= count:
            return
        messages = self.store.messages(group_id, internal=False, limit=40)
        if not messages or messages[-1]["author"] == "customer":
            return
        ctx = self.ctx.get(group_id) or {}
        try:
            text = await ai_customer.reply(messages, self.store.user(group["customer_id"])["name"], group["playbook"], ctx)
        except RuntimeError as e:
            log.warning("simulated customer failed: %s", e)
            return
        if text:
            await self.customer_says(group, text, simulated=True)

    # decisions the salesperson makes in the chat

    def _own(self, group, message_id, kind, status):
        message = self.store.message(message_id)
        if not message or message["group_id"] != group["id"] or message["kind"] != kind:
            raise ChatError("找不到这条消息")
        if message["status"] != status:
            raise ChatError("这条已经处理过了")
        return message

    async def send_draft(self, group, user, draft_id, body=None):
        draft = self._own(group, draft_id, "draft", "pending")
        await self._settle_in_odoo(group, user, draft, True)
        text = (body or draft["body"]).strip()
        await self.update(group, self.store.update_message(
            draft_id, status="sent", meta={**draft["meta"], "edited": text != draft["body"], "by": user["name"]}))
        await self.post(group, "ai", text, meta={"approved_draft": draft_id, "by": user["name"]})

    async def reject_draft(self, group, user, draft_id, note=""):
        draft = self._own(group, draft_id, "draft", "pending")
        await self._settle_in_odoo(group, user, draft, False, note)
        await self.update(group, self.store.update_message(
            draft_id, status="rejected", meta={**draft["meta"], "note": note, "by": user["name"]}))
        if note:
            self.trigger(group["id"], f"{labels(group['playbook'])['owner']} {user['name']} 没有批准你的草稿 #{draft_id}，意见：{note}",
                         explicit=True)

    async def withdraw_draft(self, group, draft_id, reason=""):
        draft = self._own(group, draft_id, "draft", "pending")
        await self._settle_in_odoo(group, None, draft, False, reason)
        await self.update(group, self.store.update_message(
            draft_id, status="withdrawn", meta={**draft["meta"], "note": reason}))

    async def decide(self, group, user, card_id, approve, choice=None, note=""):
        card = self._own(group, card_id, "decision", "waiting")
        meta = card["meta"]
        if not approve and not note:
            raise ChatError("驳回时请写一句原因，AI 会照着改")
        try:
            await self.sales_client(user).decide(group, meta["step"], approve, choice, note)
        except OdooError as e:
            raise ChatError(str(e)) from e
        chosen = choice or meta.get("recommendation") or "批准"
        result = f"{user['name']} 已批准：{chosen}" if approve else f"{user['name']} 已驳回：{note}"
        await self.update(group, self.store.update_message(
            card_id, status="approved" if approve else "rejected", meta={**meta, "result": result, "note": note}))
        self.log_action(group, f"user:{user['id']}", "human_decide",
                        {"step_name": meta["step_name"], "approve": approve, "choice": choice, "note": note}, {"result": result})
        await self.sync_case(group, quiet=True)
        self.trigger(group["id"], f"「{meta['step_name']}」有了决定：{result}" + (f"；备注：{note}" if note and approve else ""),
                     explicit=True)

    async def confirm_proposal(self, group, user, card_id, approve, note=""):
        card = self._own(group, card_id, "proposal", "waiting")
        meta = card["meta"]
        if not approve and not note:
            raise ChatError("驳回时请写一句原因，AI 会照着改")
        try:
            await self.sales_client(user).proposal(meta["proposal"], approve, note)
        except OdooError as e:
            raise ChatError(str(e)) from e
        await self.sync_case(group, quiet=True)
        card = self.store.message(card_id)
        result = card["meta"].get("result") or (f"{user['name']} 已确认" if approve else f"{user['name']} 已驳回：{note}")
        self.log_action(group, f"user:{user['id']}", "human_confirm",
                        {"action_name": meta["action_name"], "approve": approve, "note": note},
                        {"result": result, "link": card["meta"].get("link"), "state": card["status"],
                         **({"error": result} if card["status"] == "failed" else {})})
        self.trigger(group["id"], f"「{meta['action_name']}」{'已确认执行' if approve else '被驳回'}：{result}", explicit=True)

    # Odoo state

    async def sync_case(self, group, quiet=False):
        try:
            ctx = await self.bot(group).context(group)
        except (OdooError, KeyError) as e:
            log.warning("could not read %s: %s", group["order_name"], e)
            return
        self.ctx[group["id"]] = ctx
        group = self.store.group(group["id"])
        await self._sync_cards(group, ctx["steps"])
        await self._sync_proposals(group, ctx.get("proposals", []))
        fp = fingerprint(ctx)
        if fp != group["odoo_fp"]:
            self.store.update_group(group["id"], odoo_fp=fp)
            if group["odoo_fp"] and not quiet:
                self.trigger(group["id"], f"Odoo 里的{labels(group['playbook'])['name']}流程变了："
                             + describe_changes(group["odoo_fp"], ctx))
        await self.push_panel(group)

    async def _sync_cards(self, group, steps):
        """One decision card per playbook step waiting in Odoo, whoever asked; cards close when Odoo moves on."""
        by_code = {s["code"]: s for s in steps}
        open_cards = [m for m in self.store.messages(group["id"], limit=500)
                      if m["kind"] == "decision" and m["status"] == "waiting"]
        still_open = set()
        for card in open_cards:
            step = by_code.get(card["meta"]["step"])
            if step and step["state"] == "waiting" and step["question"] == card["meta"]["question"]:
                still_open.add(step["code"])
                continue
            result = (step and step["decision"]) or (step and STATE.get(step["state"])) or "已关闭"
            await self.update(group, self.store.update_message(
                card["id"], status="closed", meta={**card["meta"], "result": f"已在 Odoo 处理：{result}"}))
        for step in steps:
            if step["state"] == "waiting" and step["code"] not in still_open:
                await self.post(group, "ai", step["question"], audience="internal", kind="decision", status="waiting",
                                meta={"step": step["code"], "step_name": step["name"], "question": step["question"],
                                      "options": step["options"], "recommendation": step["recommendation"],
                                      "summary": step["summary"]})

    async def _sync_held_messages(self, group, proposals, messages):
        """Held outgoing messages decided in Odoo are sent or dropped here; held ones Odoo has not seen yet go there."""
        drafts = {m["meta"].get("proposal"): m for m in messages if m["kind"] == "draft" and m["meta"].get("proposal")}
        owner = labels(group["playbook"])["owner"]
        for p in proposals:
            draft = drafts.get(p["id"])
            if p["action"] != "send_message" or not draft or draft["status"] != "pending" or p["state"] == "pending":
                continue
            by = p.get("decided_by") or owner
            if p["state"] == "done":
                await self.update(group, self.store.update_message(draft["id"], status="sent", meta={**draft["meta"], "by": by}))
                await self.post(group, "ai", draft["body"], meta={"approved_draft": draft["id"], "by": by})
            elif (p.get("result") or "").startswith("撤回") or (p.get("result") or "").startswith("数字员工撤回"):
                await self.update(group, self.store.update_message(draft["id"], status="withdrawn", meta=draft["meta"]))
            else:
                note = p.get("human_note") or ""
                await self.update(group, self.store.update_message(
                    draft["id"], status="rejected", meta={**draft["meta"], "note": note, "by": by}))
                self.trigger(group["id"], f"{owner} {by} 在 Odoo 里没有批准你的草稿 #{draft['id']}" + (f"，意见：{note}" if note else ""),
                             explicit=True)
        for m in messages:
            if m["kind"] == "draft" and m["status"] == "pending" and not m["meta"].get("proposal") and not m["meta"].get("hold_error"):
                await self.hold_in_odoo(group, m)

    async def _sync_proposals(self, group, proposals):
        """One card per drafted Odoo action waiting for confirmation; it closes with Odoo's result."""
        messages = self.store.messages(group["id"], limit=500)
        await self._sync_held_messages(group, proposals, messages)
        proposals = [p for p in proposals if p["action"] != "send_message"]
        by_id = {p["id"]: p for p in proposals}
        cards = {m["meta"]["proposal"]: m for m in messages if m["kind"] == "proposal"}
        for pid, card in cards.items():
            proposal = by_id.get(pid)
            if card["status"] == "waiting" and proposal and proposal["state"] != "pending":
                await self.update(group, self.store.update_message(card["id"], status=proposal["state"], meta={
                    **card["meta"], "result": proposal["result"], "link": proposal["link"]}))
        for proposal in proposals:
            if proposal["state"] == "pending" and proposal["id"] not in cards:
                await self.post(group, "ai", proposal["summary"], audience="internal", kind="proposal", status="waiting",
                                meta={"proposal": proposal["id"], "action": proposal["action"],
                                      "action_name": proposal["name"], "summary": proposal["summary"],
                                      "preview": proposal["preview"]})

    # what people see beside the chat

    def panel(self, group):
        ctx = self.ctx.get(group["id"]) or {}
        record = ctx.get("record") or {}
        marks = [record.get("customer_ref") or record.get("name"), (record.get("destination_port") or "").upper()] \
            if group["playbook"] == "trade" else [ctx.get("external", {}).get("title")]
        return {
            "playbook": group["playbook"], "labels": labels(group["playbook"]),
            "case": ctx.get("case", {}).get("name") or group["order_name"],
            "marks": [m for m in marks if m],
            "facts": ctx.get("facts", []),
            "steps": [{"code": s["code"], "name": s["name"], "short_name": s["short_name"], "state": s["state"],
                       "needs_human": s["needs_human"]} for s in ctx.get("steps", [])],
            "current": (ctx.get("current_step") or {}).get("code"),
            "followups": self.store.followups(group["id"]),
            "settings": {k: group[k] for k in ("ai_auto", "sim_customer", "chase_minutes")},
            "status": self.status.get(f"customer:{group['customer_key']}"),
            "salespeople": [s["name"] for s in self.store.salespeople(group)],
        }

    def customer_header(self, group):
        """What the outside party sees on top of the chat, in their language."""
        ctx = self.ctx.get(group["id"]) or {}
        external = ctx.get("external") or {}
        return {"title": external.get("title") or "", "subtitle": external.get("subtitle") or "",
                "steps": [{"code": s["code"], "name": s["name"], "state": "done" if s["done"] else "todo"}
                          for s in external.get("steps", [])],
                "current": (ctx.get("current_step") or {}).get("code"),
                "lang": party_lang(ctx), "ai": labels(group["playbook"])["ai_external"]}

    def group_summary(self, group):
        ctx = self.ctx.get(group["id"]) or {}
        step = ctx.get("current_step") or {}
        waiting = sum(1 for m in self.store.messages(group["id"], limit=300)
                      if (m["kind"] in ("decision", "proposal") and m["status"] == "waiting")
                      or (m["kind"] == "draft" and m["status"] == "pending"))
        return {"step": step.get("name"), "state": step.get("state"), "todo": waiting}

    async def push_panel(self, group):
        group = self.store.group(group["id"])
        await self.send_group(group, {"type": "panel", "panel": self.panel(group), "summary": self.group_summary(group)},
                              internal=True)

    # proactive follow-up

    async def tick(self):
        for group in self.store.all_groups():
            await self.sync_case(group)
            group = self.store.group(group["id"])
            if not group["ai_auto"]:
                continue
            for followup in self.store.due_followups(group["id"]):
                self.store.close_followup(group["id"], followup["id"], "fired", "到期，已交给 AI 处理")
                self.trigger(group["id"], f"待跟进 #{followup['id']} 到期了：{followup['what']}")
            self._chase_silence(group)

    def _chase_silence(self, group):
        messages = self.store.messages(group["id"], internal=False, limit=1)
        last = messages[-1] if messages else None
        if not last or last["author"] == "customer":
            return
        if now() - parse(last["created_at"]) < timedelta(minutes=group["chase_minutes"]):
            return
        chased = self.chases.setdefault(group["id"], {"last": None, "count": 0})
        if chased["last"] == last["id"] or chased["count"] >= MAX_SILENCE_CHASES:
            return
        chased.update(last=last["id"], count=chased["count"] + 1)
        party = labels(group["playbook"])["party"]
        self.trigger(group["id"], f"{party}已经 {group['chase_minutes']} 分钟没有回复群里最后一条消息（#{last['id']}）。"
                     f"如果那条消息需要{party}回应，就礼貌地追问一次；不需要就别打扰。")

    async def loop(self):
        asyncio.create_task(self.desk.loop())
        await self.load_products()
        for group in self.store.all_groups():
            await self.sync_case(group, quiet=True)
        ticks = 0
        while True:
            await asyncio.sleep(config.TICK_SECONDS)
            ticks += 1
            try:
                if ticks % 30 == 0:
                    await self.load_products()
                await self.tick()
            except Exception:
                log.exception("follow-up tick failed")
