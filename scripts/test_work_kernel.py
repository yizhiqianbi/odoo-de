"""Checks the work_case kernel over JSON-2 with each scenario bot's own key: what it may read, and what it is refused.

Run as root: venv/bin/python scripts/test_work_kernel.py
"""
import json
import sys

import httpx

KEYS = "/home/tradechat/keys"
URL = "http://127.0.0.1:8069/json/2"


def client(name):
    key = open(f"{KEYS}/{name}.key").read().strip()
    return httpx.Client(base_url=URL, timeout=60,
                        headers={"Authorization": f"bearer {key}", "X-Odoo-Database": "de", "User-Agent": "work-kernel-test"})


def call(http, model, method, **body):
    res = http.post(f"/{model}/{method}", json=body)
    if res.status_code >= 400:
        return False, (res.json().get("message") if res.headers.get("content-type", "").startswith("application/json")
                       else res.text)[:160]
    return True, res.json()


failures = 0


def expect(label, ok, want):
    global failures
    mark = "OK " if ok == want else "BAD"
    failures += ok != want
    print(f"{mark} {label}")


ap, hr, trade = client("bot_ap"), client("bot_hr"), httpx.Client(
    base_url=URL, timeout=60, headers={"Authorization": f"bearer {open('/home/tradechat/bot_api_key').read().strip()}",
                                       "X-Odoo-Database": "de"})

ok, cases = call(ap, "account.move", "work_agent_cases")
expect("AP bot lists its cases", ok, True)
bills = {c["name"]: c for c in cases} if ok else {}
print("   ", [(c["party"], c["owner_login"], c["contact"], c["contact_lang"], c["step"]) for c in cases])
bill_id = cases[0]["id"]
ok, ctx = call(ap, "account.move", "work_agent_context", ids=[bill_id])
expect("AP bot reads a bill's context", ok, True)
if ok:
    print("   ", json.dumps({k: ctx[k] for k in ("facts", "external", "confidential", "identity")}, ensure_ascii=False)[:600])
    print("   ", json.dumps(ctx["details"]["lines"], ensure_ascii=False)[:300])
expect("AP bot cannot post a bill", call(ap, "account.move", "action_post", ids=[bill_id])[0], False)
expect("AP bot cannot write a bill directly", call(ap, "account.move", "write", ids=[bill_id], vals={"ref": "X"})[0], False)
ok, steps = call(ap, "work.step", "search", domain=[["res_model", "=", "account.move"], ["res_id", "=", bill_id]])
expect("AP bot cannot see steps outside its methods", ok and bool(steps), False)
expect("AP bot cannot read sale orders", call(ap, "sale.order", "search_read", domain=[], fields=["name"], limit=1)[0], False)
expect("AP bot cannot read applicants", call(ap, "hr.applicant", "search_read", domain=[], fields=["partner_name"], limit=1)[0], False)
expect("AP bot cannot fill a field outside the playbook",
       call(ap, "account.move", "work_agent_report", ids=[bill_id], step_code="intake", summary="x", values={"ref": "Y"})[0], False)
expect("AP bot cannot close a step that is not current",
       call(ap, "account.move", "work_agent_complete", ids=[bill_id], step_code="approve", summary="x")[0], False)

ok, cases = call(hr, "hr.applicant", "work_agent_cases")
expect("HR bot lists its cases", ok, True)
print("   ", [(c["name"], c["owner_login"], c["contact_lang"], c["step"]) for c in cases] if ok else cases)
if ok:
    ok, ctx = call(hr, "hr.applicant", "work_agent_context", ids=[cases[0]["id"]])
    expect("HR bot reads an applicant's context", ok, True)
    if ok:
        print("   ", json.dumps({k: ctx[k] for k in ("facts", "confidential")}, ensure_ascii=False)[:400])
    expect("HR bot cannot refuse an applicant", call(hr, "hr.applicant", "archive_applicant", ids=[cases[0]["id"]])[0], False)
expect("HR bot cannot read bills", call(hr, "account.move", "work_agent_cases")[0], False)

ok, cases = call(trade, "sale.order", "work_agent_cases")
expect("trade bot lists its cases", ok, True)
order = next(c for c in cases if c["name"].startswith("S00027"))
ok, ctx = call(trade, "sale.order", "sop_agent_context", ids=[order["id"]])
expect("trade bot still reads through the old sop_agent_context", ok and "order" in ctx and "lines" in ctx, True)
ok, ctx = call(trade, "sale.order", "work_agent_context", ids=[order["id"]])
print("   ", json.dumps({k: ctx[k] for k in ("external", "confidential")}, ensure_ascii=False)[:400] if ok else ctx)
expect("trade bot cannot approve a step", call(trade, "work.step", "action_approve", ids=[1])[0], False)
expect("trade bot cannot read bills", call(trade, "account.move", "work_agent_cases")[0], False)
sys.exit(1 if failures else 0)
