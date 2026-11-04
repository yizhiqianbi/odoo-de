"""Drives the group chat over HTTP as the salesperson and the customer, and prints what happens.

usage: chat/venv/bin/python scripts/chat_e2e.py STEP
"""
import os
import sys
import time

import httpx

BASE = os.environ.get("CHAT_URL", "http://127.0.0.1:21047")
http = httpx.Client(base_url=BASE, timeout=30)


def login(username):
    token = http.post("/api/login", json={"username": username}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


SALES, CUSTOMER = login("mitchell"), login("anna")
GID = http.get("/api/groups", headers=SALES).json()[0]["id"]


def detail(who):
    return http.get(f"/api/groups/{GID}", headers=who).json()


def wait_ai(timeout=180):
    time.sleep(3)
    end = time.time() + timeout
    while time.time() < end:
        status = (detail(SALES)["panel"] or {}).get("status") or {}
        if not status.get("busy"):
            time.sleep(2)
            status = (detail(SALES)["panel"] or {}).get("status") or {}
            if not status.get("busy"):
                return status
        time.sleep(2)
    return {"text": "timeout"}


def show(since):
    for m in detail(SALES)["messages"]:
        if m["id"] > since:
            tag = "内部" if m["audience"] == "internal" else "群"
            extra = f" [{m['status']}] {m['meta'].get('reasons', '')}" if m["kind"] in ("draft", "decision") else ""
            print(f"#{m['id']} {tag} {m['author']} {m['kind']}{extra}\n    {m['body']}")


def last_id():
    ms = detail(SALES)["messages"]
    return ms[-1]["id"] if ms else 0


def check_customer_view():
    data = detail(CUSTOMER)
    leaked = [m["id"] for m in data["messages"] if m["audience"] != "all" or "meta" in m]
    assert not leaked and data["panel"] is None, f"customer sees internal data: {leaked}"
    print(f"顾客视图：{len(data['messages'])} 条群消息，没有内部内容")


step = sys.argv[1]
start = last_id()
if step == "customer":
    text = sys.argv[2]
    http.post(f"/api/groups/{GID}/messages", json={"body": text}, headers=CUSTOMER).raise_for_status()
elif step == "decide":
    card = next(m for m in detail(SALES)["messages"] if m["kind"] == "decision" and m["status"] == "waiting")
    body = {"approve": True, "choice": card["meta"]["recommendation"], "note": sys.argv[2] if len(sys.argv) > 2 else ""}
    print("决定：", card["meta"]["step_name"], "->", body["choice"][:60])
    http.post(f"/api/groups/{GID}/decisions/{card['id']}", json=body, headers=SALES).raise_for_status()
elif step == "send-draft":
    draft = next(m for m in detail(SALES)["messages"] if m["kind"] == "draft" and m["status"] == "pending")
    http.post(f"/api/groups/{GID}/drafts/{draft['id']}", json={"send": True}, headers=SALES).raise_for_status()
elif step == "reject-draft":
    draft = next(m for m in detail(SALES)["messages"] if m["kind"] == "draft" and m["status"] == "pending")
    http.post(f"/api/groups/{GID}/drafts/{draft['id']}", json={"send": False, "note": sys.argv[2]},
              headers=SALES).raise_for_status()
elif step == "tell-ai":
    http.post(f"/api/groups/{GID}/messages", json={"body": sys.argv[2], "internal": True}, headers=SALES).raise_for_status()
elif step == "settings":
    http.post(f"/api/groups/{GID}/settings", json={sys.argv[2]: int(sys.argv[3])}, headers=SALES).raise_for_status()
if step != "show":
    status = wait_ai()
    print("AI：", status.get("text"))
show(0 if step == "show" else start)
check_customer_view()
panel = detail(SALES)["panel"]
print("当前步骤：", panel["current"], "| 待跟进：", [(f["id"], f["what"]) for f in panel["followups"]])
