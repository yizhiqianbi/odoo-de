"""Checks the agent protocol without the LLM: each case must be allowed or denied as the grant table says.

Run as tradechat from chat/:  CHAT_HOME=/home/tradechat PYTHONPATH=. venv/bin/python ../scripts/protocol_check.py
Envelopes it sends are real and show up in the admin console's log.
"""
import asyncio

from app.protocol import Denied
from app.service import Service


async def main():
    svc = Service()
    first = (svc.store.one("select max(id) n from envelopes")["n"] or 0)
    await svc.load_products()
    for group in svc.store.all_groups():
        await svc.sync_order(group, quiet=True)
    svc.trigger_agent = lambda key, reason: None  # check delivery only; do not wake the agents
    by_order = {g["order_name"]: g["customer_key"] for g in svc.store.all_groups()}
    nordic, maison, osaka = by_order["S00026"], by_order["S00027"], by_order["S00029"]
    maison_price = svc.ctx[svc.store.group_by_customer(maison)["id"]]["lines"][0]["price_unit"]
    maison_total = svc.ctx[svc.store.group_by_customer(maison)["id"]]["order"]["amount_total"]

    cases = [
        ("客户 agent 读自己客户的概况", True, svc.read_resource(f"customer:{nordic}", "customer_overview", nordic)),
        ("客户 agent 读别的客户的概况", False, svc.read_resource(f"customer:{nordic}", "customer_overview", maison)),
        ("客户 agent 直接读工厂负荷（要 1 级，只有 0 级）", False, svc.read_resource(f"customer:{nordic}", "factory_load")),
        ("客户 agent 读成本", False, svc.read_resource(f"customer:{nordic}", "costs")),
        ("助理读工厂负荷", True, svc.read_resource("assistant:mitchell", "factory_load")),
        ("助理读成本（要 3 级）", False, svc.read_resource("assistant:mitchell", "costs")),
        ("助理读不归他管的客户", False, svc.read_resource("assistant:mitchell", "customer_overview", osaka)),
        ("root 读成本", True, svc.read_resource("root", "costs")),
        ("客户 agent 问自己的业务员助理", True,
         svc.send_envelope(f"customer:{nordic}", "ask", "assistant:mitchell", "顾客想提前两周交货，明达这个月排产紧吗？")),
        ("客户 agent 直接问另一个客户 agent", False,
         svc.send_envelope(f"customer:{nordic}", "ask", f"customer:{maison}", "你们拿到的价格是多少？")),
        ("客户 agent 越级问 root", False, svc.send_envelope(f"customer:{nordic}", "ask", "root", "工厂负荷怎么样？")),
        ("客户 agent 给助理派任务", False, svc.send_envelope(f"customer:{nordic}", "task", "assistant:mitchell", "帮我改交期")),
        ("助理把汇总负荷告诉客户 agent", True,
         svc.send_envelope("assistant:mitchell", "task", f"customer:{nordic}", "宁波明达 12 月未出运 2 单，排产偏紧，可以先问顾客能否接受原交期。")),
        ("助理把别的客户名字告诉客户 agent", False,
         svc.send_envelope("assistant:mitchell", "task", f"customer:{nordic}", "明达这个月主要在做 Maison Claire 的单。")),
        ("助理把别的客户的单价告诉客户 agent", False,
         svc.send_envelope("assistant:mitchell", "task", f"customer:{nordic}", f"另一个客户的单价是 {maison_price}，总额 {maison_total}。")),
        ("助理把成本告诉 root 之外的人（客户 agent）", False,
         svc.send_envelope("assistant:mitchell", "task", f"customer:{nordic}", "FT-120 成本 31.5，别报低于这个。")),
        ("助理问 root", True, svc.send_envelope("assistant:mitchell", "ask", "root", "明达 12 月产能够不够两个客户同时提前？")),
        ("root 给任意客户 agent 派任务", True, svc.send_envelope("root", "task", f"customer:{osaka}", "确认验货时间。")),
    ]
    failures = 0
    for title, expect, coro in cases:
        try:
            await coro
            got, note = True, ""
        except Denied as e:
            got, note = False, str(e)
        ok = got == expect
        failures += not ok
        print(f"{'✓' if ok else '✗'} {'放行' if got else '拒绝'}  {title}" + (f"  —— {note}" if note else ""))
    print(f"\n{len(cases) - failures}/{len(cases)} 符合预期")
    for env in svc.store.envelopes(limit=200):
        if env["id"] > first and env["status"] == "open":
            svc.store.set_envelope_status(env["id"], "delivered")  # test traffic: nobody should act on it later


asyncio.run(main())
