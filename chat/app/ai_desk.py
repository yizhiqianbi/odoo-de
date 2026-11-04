"""One person's digital employee in Odoo Discuss: their assistant agent, answering them in a private chat.

It reads Odoo with that person's own key, so it sees exactly what they may see. It changes nothing in Odoo itself:
decisions are buttons on cards, and work on a case is handed to that case's agent through the protocol."""
import json
import logging
from datetime import timedelta, timezone

from . import agents, config, llm
from .odoo import OdooError
from .store import now

log = logging.getLogger(__name__)
BEIJING = timezone(timedelta(hours=8))

ERP_MODELS = {
    "sale.order": "销售订单：name 单号, partner_id 客户, client_order_ref 客户单号, amount_total, currency_id, state, date_order, commitment_date, user_id 业务员",
    "crm.lead": "线索和商机：name, partner_id, contact_name, expected_revenue, probability, stage_id, user_id, type(lead/opportunity), date_deadline",
    "res.partner": "客户、供应商、联系人：name, email, phone, country_id, is_company, parent_id",
    "product.product": "产品：default_code, name, list_price, qty_available, uom_id",
    "stock.quant": "库存：product_id, location_id, quantity, reserved_quantity",
    "account.move": "发票和账单：move_type(out_invoice 客户发票, in_invoice 供应商账单, in_refund 供应商红字), name, ref, partner_id, "
                    "amount_total, amount_residual, currency_id, state, payment_state, invoice_date, invoice_date_due",
    "account.payment": "收付款：partner_id, amount, currency_id, date, payment_type, state",
    "purchase.order": "采购单：name, partner_id, amount_total, state, date_planned",
    "hr.applicant": "应聘者：partner_name, job_id, stage_id, user_id, email_from",
    "hr.job": "职位：name, no_of_recruitment, user_id",
    "hr.employee": "员工：name, job_title, department_id, parent_id, work_email",
    "mail.activity": "待办：summary, res_name, res_model, date_deadline, user_id, activity_type_id",
    "calendar.event": "日程：name, start, stop, partner_ids, location",
}

TOOLS = [
    {"type": "function", "function": {
        "name": "my_desk", "description": "他现在要拍板的事（流程决定、起草的 Odoo 动作、待批准的对外消息）和他负责的事项进度。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "show_card", "description": "把一件等他拍板的事重新发成卡片，放到对话最下面，他在卡片上点按钮决定。",
        "parameters": {"type": "object", "required": ["model", "id"], "properties": {
            "model": {"type": "string", "enum": ["work.step", "work.proposal"]}, "id": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "erp_search", "description": "用他的权限查 Odoo 记录。可查的模型：" + "；".join(f"{k}（{v}）" for k, v in ERP_MODELS.items()),
        "parameters": {"type": "object", "required": ["model"], "properties": {
            "model": {"type": "string", "enum": list(ERP_MODELS)},
            "domain": {"type": "array", "description": "Odoo domain，比如 [[\"state\",\"=\",\"sale\"],[\"amount_total\",\">\",10000]]",
                       "items": {}},
            "fields": {"type": "array", "items": {"type": "string"}},
            "order": {"type": "string", "description": "比如 date_order desc"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30}}}}},
    {"type": "function", "function": {
        "name": "erp_count", "description": "用他的权限统计 Odoo 记录：总数，可按一个字段分组，可加总一个数字字段。",
        "parameters": {"type": "object", "required": ["model"], "properties": {
            "model": {"type": "string", "enum": list(ERP_MODELS)},
            "domain": {"type": "array", "items": {}},
            "group_by": {"type": "string"}, "sum": {"type": "string"}}}}},
]

SYSTEM = """你是 {owner} 的数字员工，在 Odoo「讨论」里和 {owner} 一对一聊天。他是做决定的人，活由你和各个事项的数字员工去干。

你能做的：
- my_desk：看他现在要拍板的事和他负责的事项。问「今天有什么要处理」「我还有什么没批」时先用它。
- show_card：把要他拍板的事重新发成卡片（他在卡片上点批准、驳回或确认）。列待办时最多重发 3 张最要紧的。
  决定只能在卡片上点按钮，你不能代他批准；他在聊天里回「同意」「批了」，就把对应的卡片发给他点。
- erp_search / erp_count：用他自己的权限查 Odoo。没权限会报错，那就如实说他没有这个权限，不要换别的办法绕。
- read_resource：读你被授权的资料，比如一个事项的要点（customer_overview）和群聊（customer_chat）。
- task_agent / ask_agent：他交代一个事项的事（催对方、问对方、改说法），派给那个事项的 agent（key 见下面的组织信息），
  派完告诉他「已交代给某某事项的数字员工，有结果再告诉你」。不要说已经办成了。
- answer：回复别的 agent 发给你的信封。
{report_rule}

说话方式：中文，简短直接，先给结论。可以用「- 」列点、用 **加粗**。提到单据时写成 [S00027](/odoo/sale.order/27) 这样的链接，
链接只能用工具结果里的 url。不用表格、不用标题。数字、单号、日期、人名只能来自工具结果或对话记录，查不到就说查不到。
你不能改 Odoo、不能对外发消息，也没有联系工厂、货代、银行的工具；需要外面的人动手，就请他安排。

你在 agent 组织里的位置（来自授权表）：
{org}

{rules}"""

REPORT_RULES = {
    True: "这次是他在对话里找你：最后直接写给他的回复，它会原样发到对话里。没什么要说的也要回一句。",
    False: "这次不是他在找你（是别的 agent 回复了你，或者有事要你处理）：真有他需要知道的结果，用 report_to_human 发给他，"
           "一次最多一条；没有就不发。最后用一句话（不发给他）说明这次做了什么。",
}


def plain(value):
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], str):
        return value[1]
    if isinstance(value, list):
        return f"{len(value)} 条" if value and all(isinstance(v, int) for v in value) else value
    return None if value is False else value


def history_text(rows, name):
    lines = []
    for r in rows:
        who = "你" if r["me"] else name
        card = "（卡片）" if r["ref"].startswith(("work.step,", "work.proposal,")) else ""
        lines.append(f"{who}{card}：{r['text'][:800]}")
    return "\n".join(lines) or "（还没有聊过）"


def desk_summary(data):
    return {
        "waiting": [{"model": "work.step" if w["kind"] == "decision" else "work.proposal", "id": w["id"],
                     "kind": {"decision": "流程决定", "proposal": "Odoo 动作", "message": "对外消息"}[w["kind"]],
                     "case": w["case"], "playbook": w["playbook"], "title": w["title"], "question": w["question"][:300],
                     "url": f"/odoo/{w['res_model']}/{w['res_id']}"} for w in data["waiting"]],
        "my_cases": [{"case": m["name"], "playbook": m["playbook"], "party": m["party"], "step": m["step"],
                      "state": m["state"], "url": f"/odoo/{m['res_model']}/{m['res_id']}"} for m in data["mine"]],
        "digital_employees_today": data["today"], "refused_today": data["refused_today"],
    }


async def execute(svc, user, login, name, args):
    person = svc.sales_client(user)
    try:
        if name == "my_desk":
            return desk_summary(await person.desk())
        if name == "show_card":
            return await svc.desk.client.post_card(login, args["model"], int(args["id"]))
        if name in ("erp_search", "erp_count"):
            model = args["model"]
            if model not in ERP_MODELS:
                return {"error": f"不能查 {model}"}
            domain = args.get("domain") or []
            if name == "erp_search":
                fields = list(dict.fromkeys(["display_name", *(args.get("fields") or [])]))
                rows = await person.call(model, "search_read", domain=domain, fields=fields, order=args.get("order") or None,
                                         limit=max(1, min(int(args.get("limit") or 10), 30)))
                total = await person.call(model, "search_count", domain=domain)
                return {"total": total, "records": [
                    {**{k: plain(v) for k, v in r.items() if k != "id"}, "url": f"/odoo/{model}/{r['id']}"} for r in rows]}
            group_by, total_field = args.get("group_by"), args.get("sum")
            fields = [f for f in (group_by, total_field) if f] or ["id"]
            rows = await person.call(model, "search_read", domain=domain, fields=fields, limit=5000)
            groups = {}
            for r in rows:
                key = str(plain(r.get(group_by)) if group_by else "全部")
                g = groups.setdefault(key, {"count": 0, **({"sum": 0} if total_field else {})})
                g["count"] += 1
                if total_field:
                    g["sum"] += r.get(total_field) or 0
            return {"total": len(rows), "groups": groups, "capped": len(rows) >= 5000}
    except OdooError as e:
        return {"error": str(e)}
    except (KeyError, TypeError, ValueError) as e:
        return {"error": f"参数不对：{e}"}
    return None


async def run(svc, agent, reasons):
    me = agent["key"]
    user = svc.store.user(agent["owner_user_id"])
    login = svc.desk.login_of(user)
    incoming = svc.desk.take(me)
    talking = bool(incoming)
    try:
        history = await svc.desk.client.history(login, limit=16) if svc.desk.client else []
    except OdooError as e:
        log.warning("could not read the Discuss chat of %s: %s", login, e)
        history = []
    if talking:
        await svc.desk.typing(user, True)
    principal = f"agent:{me}"
    system = SYSTEM.format(owner=user["name"], report_rule=REPORT_RULES[talking], org=agents.org_text(svc, agent),
                           rules=agents.PROTOCOL_RULES)
    prompt = "\n\n".join([
        f"现在时间：{now().astimezone(BEIJING):%Y-%m-%d %H:%M}（北京时间）",
        "这次为什么叫你：\n" + "\n".join(f"- {r}" for r in reasons),
        f"你和 {user['name']} 在讨论里的最近对话（最后几条是新的）：\n" + history_text(history, user["name"]),
        "待你处理的信封：\n" + agents.envelope_text(svc.store.open_envelopes(principal), me),
        "和你有关的最近 agent 往来：\n" + agents.envelope_text(svc.store.envelopes(principal, limit=10), me),
    ])
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    tools = TOOLS + agents.PROTOCOL_TOOLS + ([] if talking else [agents.REPORT_TOOL])
    actions = []
    try:
        for _ in range(config.AI_MAX_ROUNDS):
            reply = await llm.chat(messages, tools=tools)
            calls = reply.get("tool_calls") or []
            messages.append({"role": "assistant", "content": reply.get("content") or "", **({"tool_calls": calls} if calls else {})})
            if not calls:
                text = (reply.get("content") or "").strip()
                if talking:
                    await svc.desk.say(user, text or "收到。")
                return {"summary": text[:200], "actions": actions}
            for call in calls:
                tool = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                reported = any(a["tool"] == "report_to_human" and a["result"] == {"sent": True} for a in actions)
                if tool == "report_to_human" and reported:
                    result = {"error": "这次已经汇报过一条了，把要说的合并进下次汇报。"}
                else:
                    result = await execute(svc, user, login, tool, args)
                    if result is None:
                        result = await agents.execute_protocol(svc, me, tool, args)
                    if result is None:
                        result = {"error": f"没有这个工具：{tool}"}
                actions.append({"tool": tool, "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)[:12000]})
        if talking:
            await svc.desk.say(user, "这件事我查了好几轮还没理清，先停一下。你可以换个说法，或者说得具体一点。")
        return {"summary": "达到单次处理的轮数上限，先停下。", "actions": actions}
    except Exception:
        if talking:
            await svc.desk.say(user, "我这边出了点问题，没能处理这条。稍后再试一次。")
        raise
    finally:
        if talking:
            await svc.desk.typing(user, False)
