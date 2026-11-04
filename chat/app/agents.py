"""Protocol tools shared by every agent, and the runner for the root and assistant agents.

Case agents run in ai_case with these tools added; the root, the assistants and the advisors have no group of their
own and work only through the protocol: reading resources, asking and tasking other agents, and reporting to people.
"""
import json
import logging

from . import config, llm
from .protocol import LEVELS, RESOURCES, Denied

log = logging.getLogger(__name__)

PROTOCOL_TOOLS = [
    {"type": "function", "function": {
        "name": "read_resource",
        "description": "读取数据。资源：" + "；".join(f"{k}（{LEVELS[v[0]]}{'，要指定 customer' if v[1] else ''}）：{v[2]}"
                                             for k, v in RESOURCES.items()),
        "parameters": {"type": "object", "required": ["resource"], "properties": {
            "resource": {"type": "string", "enum": list(RESOURCES)}, "customer": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "ask_agent", "description": "向另一个 agent 提问。对方回答后会再叫你。",
        "parameters": {"type": "object", "required": ["to", "question"], "properties": {
            "to": {"type": "string", "description": "agent 的 key"}, "question": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "task_agent", "description": "给另一个 agent 派一个任务，比如让事项 agent 去群里问外部联系人。",
        "parameters": {"type": "object", "required": ["to", "instruction"], "properties": {
            "to": {"type": "string"}, "instruction": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "answer", "description": "回复一个发给你的信封（提问或任务），写回答或任务结果。",
        "parameters": {"type": "object", "required": ["envelope_id", "text"], "properties": {
            "envelope_id": {"type": "integer"}, "text": {"type": "string"}}}}},
]
REPORT_TOOL = {"type": "function", "function": {
    "name": "report_to_human", "description": "给负责你的人发一条汇报（中文）。",
    "parameters": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}}}


async def execute_protocol(svc, key, name, args):
    """Runs one protocol tool for agent `key`; a refusal comes back as an error the agent can read."""
    try:
        if name == "read_resource":
            return await svc.read_resource(key, args["resource"], args.get("customer") or None)
        if name == "ask_agent":
            env = await svc.send_envelope(key, "ask", args["to"], args["question"])
            return {"sent": True, "envelope_id": env["id"], "note": "对方回答后会再叫你，这次不用等。"}
        if name == "task_agent":
            env = await svc.send_envelope(key, "task", args["to"], args["instruction"])
            return {"sent": True, "envelope_id": env["id"]}
        if name == "answer":
            await svc.answer_envelope(key, int(args["envelope_id"]), args["text"])
            return {"sent": True}
        if name == "report_to_human":
            await svc.report(key, args["text"])
            return {"sent": True}
    except Denied as e:
        return {"error": f"被协议拒绝：{e}", "denied": True}
    except (KeyError, TypeError, ValueError) as e:
        return {"error": f"参数不对：{e}"}
    return None


def envelope_text(envelopes, me):
    lines = []
    for e in envelopes:
        verb = {"ask": "提问", "task": "任务", "answer": "回答", "report": "汇报", "read": "读取"}.get(e["verb"], e["verb"])
        state = {"open": "待处理", "answered": "已回复", "denied": "被拒绝", "delivered": "已送达"}.get(e["status"], e["status"])
        reply = f"（回复 #{e['reply_to']}）" if e["reply_to"] else ""
        reason = f" 拒绝原因：{e['reason']}" if e["reason"] else ""
        lines.append(f"#{e['id']} {e['sender']} → {e['recipient']} {verb}{reply} [{state}]{reason}：{e['body'][:600]}")
    return "\n".join(lines) or "（无）"


def org_text(svc, agent):
    """The agent's place in the organisation, as the grant table currently says."""
    me = agent["key"]
    can_ask = sorted(g["target"].removeprefix("agent:") for g in svc.store.grants(me, "ask"))
    can_task = sorted(g["target"].removeprefix("agent:") for g in svc.store.grants(me, "task"))
    asked_by = sorted({g["subject"] for g in svc.store.grants(verb="ask") if g["target"] in (f"agent:{me}", "agent:*")})
    scope = sorted(svc.policy.scope(f"agent:{me}"))
    customers = []
    for c in scope:
        group = svc.store.group_by_customer(c)
        ctx = svc.ctx.get(group["id"]) if group else None
        if ctx:
            step = ctx.get("current_step") or {}
            customers.append(f"- 事项 {c}（{ctx['playbook']['name']}）：{ctx['case']['party']}，{ctx['case']['name']}，"
                             f"当前「{step.get('name', '已完成')}」（{step.get('state', '')}），agent customer:{c}")
    advisors = [f"{a['key']}（{a['name']}）" for a in svc.store.agents() if a["kind"] == "advisor" and a["key"] != me]
    return "\n".join([
        f"- 你的 key：{me}，信息级别上限 {agent['clearance']}（{LEVELS[agent['clearance']]}）",
        f"- 你可以提问的 agent：{', '.join(can_ask) or '无'}",
        f"- 你可以派任务的 agent：{', '.join(can_task) or '无'}",
        f"- 可以向你提问的 agent：{', '.join(asked_by) or '无'}",
        f"- 可以咨询的顾问：{', '.join(advisors) or '无'}（要先有对它的 ask 授权）",
        "- 你能看到的事项：\n" + ("\n".join(customers) or "（无）"),
    ])


PROTOCOL_RULES = """信息分级：0 公开，1 内部（比如工厂负荷、付款计划），2 客户机密（价格、联系人、聊天、信用证、账单、候选人），3 公司机密（成本）。
协议由服务端强制执行：没有授权的提问、派任务、读取会被拒绝；发给别人的内容如果涉及对方无权知道的客户或机密，也会被拒绝。
被拒绝时不要换个说法绕过：要么只给对方有权知道的内容（比如汇总数字、不点名的客户），要么直接告诉对方没有权限。"""

MANAGER = """你是 {name}。{role}

你在 agent 组织里的位置（来自授权表）：
{org}

{rules}

工作方式：
1. 先处理「待你处理的信封」：提问要用 answer 回答；任务要去做，做完用 answer 回复结果。
2. 只做信封里要你做的事。回答一个提问时只答所问，不要顺手盘查别的客户、不要主动给别的 agent 派新任务；
   只有人（管理员或业务员）派给你的任务要求你统筹时，才去扩大范围。
3. 需要数据就 read_resource（customer 参数只写数字编号）；需要下级去执行（比如去群里问顾客）就 task_agent，写清楚要做什么。
4. 回答别的 agent 时，只给它需要并且有权知道的东西。事项 agent 只能知道自己事项的事，别的客户、供应商、候选人一律不点名、不给价格。
5. 你和所有 agent 都没有联系工厂、船务、货代、银行、律所、税局的工具，也不能改 Odoo。需要外面的人动手时，汇报给{human}，
   并如实告诉问你的 agent「已报给人，还没有人去做」。绝不能说「已联系工厂」「已安排订舱」这类没发生的事。
6. 真有需要人知道或拍板的事，才用 report_to_human 汇报给{human}，先写结论；一次处理最多汇报一条。
7. 没有要做的事就直接结束。
最后用一句中文（80 字以内，不用 markdown）说明这次做了什么。"""

ROLES = {
    "root": ("公司 agent 组织的 root，直接对管理员负责，统筹所有助理、顾问和事项 agent，盯全局风险和资源冲突。", "管理员"),
    "assistant": ("{owner} 的助理 agent，负责他名下的事项：盯进度、排优先级、"
                  "回答事项 agent 的问题、协调事项之间的资源冲突（比如几个客户抢同一家工厂的产能）。", "{owner}"),
}
# advisors answer questions from any agent granted to ask them; they give an opinion, never act, and say when a
# real lawyer or tax adviser must look at it
ADVISORS = {
    "advisor:legal": ("法务顾问", "公司的法务顾问 agent。别的 agent 遇到合同和法律问题会来问你：外贸合同和 P/I 条款、信用证软条款和不符点、"
                      "索赔和违约责任、供应商对账争议、录用通知和劳动合同（试用期、竞业、薪资条款）、个人信息保护。"
                      "你按中国法律和国际贸易惯例（UCP600、Incoterms 2020）给出明确、可执行的意见：风险是什么、建议怎么做、"
                      "需要对方提供或修改什么。只给意见，不替公司做决定；金额大、可能诉讼、或你没有把握时，明确说要请执业律师复核。"),
    "advisor:tax": ("财税顾问", "公司的财税顾问 agent。别的 agent 遇到财税问题会来问你：增值税专用发票和普通发票、发票作废和红字发票、"
                    "进项抵扣、出口退税和报关单证、付款条件和账期、员工薪资个税和社保。"
                    "你按中国现行财税规定给出明确、可执行的意见；只给意见，不替公司做决定；没有把握时说要请注册会计师或税务师复核。"),
}


async def run_manager(svc, agent, reasons):
    me = agent["key"]
    owner = svc.store.user(agent["owner_user_id"])["name"] if agent["owner_user_id"] else ""
    role, human = (ADVISORS[me][1], "管理员") if agent["kind"] == "advisor" else ROLES[agent["kind"]]
    system = MANAGER.format(name=agent["name"], role=role.format(owner=owner), org=org_text(svc, agent),
                            rules=PROTOCOL_RULES, human=human.format(owner=owner))
    principal = f"agent:{me}"
    user = "\n\n".join([
        "这次为什么叫你：\n" + "\n".join(f"- {r}" for r in reasons),
        "待你处理的信封：\n" + envelope_text(svc.store.open_envelopes(principal), me),
        "和你有关的最近往来：\n" + envelope_text(svc.store.envelopes(principal, limit=20), me),
    ])
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    tools = PROTOCOL_TOOLS + [REPORT_TOOL]
    actions = []
    for _ in range(config.AI_MAX_ROUNDS):
        reply = await llm.chat(messages, tools=tools)
        calls = reply.get("tool_calls") or []
        messages.append({"role": "assistant", "content": reply.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            return {"summary": (reply.get("content") or "").strip(), "actions": actions}
        for call in calls:
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            reported = any(a["tool"] == "report_to_human" and a["result"] == {"sent": True} for a in actions)
            if call["function"]["name"] == "report_to_human" and reported:
                result = {"error": "这次已经汇报过一条了，把要说的合并进下次汇报。"}
            else:
                result = await execute_protocol(svc, me, call["function"]["name"], args)
            if result is None:
                result = {"error": f"没有这个工具：{call['function']['name']}"}
            actions.append({"tool": call["function"]["name"], "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
    return {"summary": "达到单次处理的轮数上限，先停下。", "actions": actions}
