"""Every way around the SOP gates the bot could try over JSON-2 must be refused."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8069/json/2"
KEY = open("/home/hubin/odoo-de/secrets/trade_bot_api_key").read().strip()


def call(model, method, **body):
    req = urllib.request.Request(f"{BASE}/{model}/{method}", data=json.dumps(body).encode(), headers={
        "Authorization": f"bearer {KEY}", "Content-Type": "application/json", "X-Odoo-Database": "de"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


# Every attempt targets something refused whatever stage the live orders are at, so a guard that is missing
# shows up as a failure here instead of quietly moving a real order on.
cases = call("sale.order", "work_agent_cases")[1]
case = next(c for c in cases if c["state"] not in ("waiting", "done") and c["step"])
order = [case["id"]]
ctx = call("sale.order", "work_agent_context", ids=order)[1]
codes = [s["code"] for s in ctx["steps"]]
later = codes[codes.index(case["step"]) + 1]
human = next((c for c in cases if c["state"] not in ("waiting", "done")
              and call("sale.order", "work_agent_context", ids=[c["id"]])[1]["current_step"]["needs_human"]), None)
step = call("work.step", "search", domain=[("res_model", "=", "sale.order"), ("res_id", "in", order), ("code", "=", case["step"])])[1] or [1]
attempts = {
    "直接确认销售订单": ("sale.order", "action_confirm", {"ids": order}),
    "直接改订单金额": ("sale.order", "write", {"ids": order, "vals": {"note": "x"}}),
    "直接把步骤改成已完成": ("work.step", "write", {"ids": step, "vals": {"state": "done"}}),
    "冒充人批准步骤": ("work.step", "action_approve", {"ids": step}),
    "给客户发消息": ("sale.order", "message_post", {"ids": order, "body": "hi", "message_type": "comment",
                                              "subtype_xmlid": "mail.mt_comment"}),
    "用备注通知客户": ("sale.order", "message_post", {"ids": order, "body": "hi", "message_type": "comment",
                                               "subtype_xmlid": "mail.mt_note", "partner_ids": [case["party_id"]]}),
    f"跳步完成「{later}」": ("sale.order", "sop_agent_complete", {"ids": order, "step_code": later, "summary": "x"}),
    "填写不允许的字段": ("sale.order", "sop_agent_report", {"ids": order, "step_code": case["step"], "summary": "x",
                                                    "values": {"amount_total": 1}}),
}
if human:
    attempts[f"自己完成需人拍板的步骤（{human['name']} {human['step']}）"] = (
        "sale.order", "sop_agent_complete", {"ids": [human["id"]], "step_code": human["step"], "summary": "x"})
failed = 0
for label, (model, method, body) in attempts.items():
    status, result = call(model, method, **body)
    refused = status >= 400
    failed += not refused
    print(f"{'拒绝' if refused else '!! 放行'}  {label}: {status} {result.get('message', result) if refused else result}"[:200])
print("全部被拒绝" if not failed else f"{failed} 项没有被拒绝")
