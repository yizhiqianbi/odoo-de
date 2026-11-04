"""Agent interaction protocol: who may ask, task or read what. The server enforces it on every envelope.

Principals are agents ("agent:<key>") and people ("user:<id>").
Every piece of information has a level: 0 公开, 1 内部, 2 客户机密, 3 公司机密.

Grants are rows an admin can edit, each (subject agent, verb, target, max_level):
    ask  agent:<key> | agent:*    may send that agent a question
    task agent:<key> | agent:*    may hand that agent a task
    read customer:<key>           may read that customer's resources up to max_level
    read *                        may read every customer's and company-wide resources up to max_level
An agent's clearance caps every grant it holds. Replies need no grant: an agent may answer what it was sent.

Before delivery every envelope body is checked against its recipient: it may not identify a customer outside the
recipient's scope, nor carry company secrets to a recipient cleared below level 3. Anything that fails a check is
denied and logged; nothing is escalated.
"""
import re

from . import gate

LEVELS = {0: "公开", 1: "内部", 2: "客户机密", 3: "公司机密"}
# name: (level, per customer, description)
RESOURCES = {
    "catalog": (0, False, "产品目录和标准价"),
    "job_openings": (0, False, "在招职位和职位要求"),
    "factory_load": (1, False, "各工厂按交货月份的未出运数量和订单数，不含客户名"),
    "payment_plan": (1, False, "已排进付款计划的供应商账单：计划付款日、到期日、金额"),
    "pipeline": (1, False, "各职位每个招聘步骤的人数，不含候选人姓名"),
    "customer_overview": (2, True, "一个事项（订单、账单、候选人）的要点、流程进度、待人决定的事"),
    "customer_chat": (2, True, "一个事项群里最近的消息"),
    "vendor_ledger": (2, True, "和这张账单同一供应商的所有账单：金额、匹配结果、进度"),
    "costs": (3, False, "产品成本"),
}
LEGAL_SUFFIX = re.compile(r"[\s,]+(gmbh|ab|sarl|inc\.?|co\.?,?\s*ltd\.?|ltd\.?|llc|pty\s+ltd|s\.r\.l\.|ltda\.?)$", re.I)
SEND_LIMIT = (15, 600)


class Denied(Exception):
    pass


def short_name(company):
    return LEGAL_SUFFIX.sub("", company or "").strip()


class Policy:
    """Answers permission questions from the grant table and the facts the service knows about each customer."""

    def __init__(self, store, facts):
        self.store = store
        self.facts = facts  # () -> {"customers": {key: {"tokens": set, "figures": set}}, "public": set, "secret": set}

    # grants

    def agent(self, key):
        agent = self.store.agent(key)
        if not agent or not agent["enabled"]:
            raise Denied(f"没有这个 agent，或它已被停用：{key}")
        return agent

    def may_send(self, sender, verb, recipient):
        if verb not in ("ask", "task"):
            raise Denied(f"未知的动作：{verb}")
        self.agent(sender)
        self.agent(recipient)
        targets = {f"agent:{recipient}", "agent:*"}
        if not any(g["target"] in targets for g in self.store.grants(sender, verb)):
            raise Denied(f"{sender} 没有向 {recipient} {'提问' if verb == 'ask' else '派任务'}的授权")
        count, seconds = SEND_LIMIT
        if self.store.envelopes_sent_since(f"agent:{sender}", seconds) >= count:
            raise Denied(f"{sender} 发得太频繁了，{seconds // 60} 分钟内最多 {count} 个")

    def read_level(self, key, customer=None):
        """The highest level `key` may read for that customer, or for company-wide resources; -1 if none."""
        agent = self.agent(key)
        targets = {"*"} | ({f"customer:{customer}"} if customer else set())
        levels = [g["max_level"] for g in self.store.grants(key, "read") if g["target"] in targets]
        return min(max(levels), agent["clearance"]) if levels else -1

    def check_read(self, key, resource, customer=None):
        if resource not in RESOURCES:
            raise Denied(f"没有这个资源：{resource}")
        level, per_customer, _ = RESOURCES[resource]
        if per_customer and not customer:
            raise Denied(f"{resource} 要指定客户")
        allowed = self.read_level(key, customer if per_customer else None)
        if allowed < level:
            target = f"客户 {customer} 的 " if per_customer else ""
            raise Denied(f"{key} 读不了{target}{resource}：需要级别 {level}（{LEVELS[level]}），"
                         f"授权只到 {allowed if allowed >= 0 else '无'}")
        return level

    # scope: which customers a principal may hear about

    def scope(self, principal):
        kind, _, ident = principal.partition(":")
        if kind == "user":
            user = self.store.user(int(ident))
            if user["role"] == "admin":
                return set(self.facts()["customers"])
            return {g["customer_key"] for g in self.store.groups_for(user)}
        customers = set(self.facts()["customers"])
        return {c for c in customers if self.read_level(ident, c) >= 2}

    def clearance(self, principal):
        kind, _, ident = principal.partition(":")
        if kind == "user":
            return {"admin": 3, "sales": 2}.get(self.store.user(int(ident))["role"], 0)
        return self.agent(ident)["clearance"]

    def check_body(self, recipient, text):
        """Refuse a body that identifies customers outside the recipient's scope or leaks secrets above its clearance."""
        facts = self.facts()
        scope = self.scope(recipient)
        allowed_figures = set(facts["public"])
        for key in scope:
            allowed_figures |= facts["customers"].get(key, {}).get("figures", set())
        lowered = text.lower()
        found = gate.figures(text)
        for key, fact in facts["customers"].items():
            if key in scope:
                continue
            names = [t for t in fact["tokens"] if t and t.lower() in lowered]
            numbers = sorted((found & fact["figures"]) - allowed_figures)
            if names or numbers:
                shown = names + [n.split(" ", 1)[1] for n in numbers]
                raise Denied(f"内容涉及接收方无权知道的客户信息（{'、'.join(shown[:4])}）")
        if self.clearance(recipient) < 3:
            leaked = sorted((found & facts["secret"]) - allowed_figures)
            if leaked:
                raise Denied(f"内容含公司机密（成本数字：{'、'.join(n.split(' ', 1)[1] for n in leaked)}）")
