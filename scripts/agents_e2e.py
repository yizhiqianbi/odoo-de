"""Drives one multi-agent scenario through the API and prints the envelopes and group messages it produces.

usage: chat/venv/bin/python scripts/agents_e2e.py customer USERNAME "message"     # a customer writes in their group
       chat/venv/bin/python scripts/agents_e2e.py task AGENT_KEY "instruction"   # the admin tasks an agent
       chat/venv/bin/python scripts/agents_e2e.py assistant USERNAME "instruction"  # a salesperson tasks their assistant
       chat/venv/bin/python scripts/agents_e2e.py watch ORDER N                     # just watch, replaying the last N items
"""
import os
import sys
import time

import httpx

BASE = os.environ.get("CHAT_URL", "http://127.0.0.1:21047")
WAIT = int(os.environ.get("WAIT", "240"))
http = httpx.Client(base_url=BASE, timeout=30, trust_env=False)


def login(username):
    return {"Authorization": "Bearer " + http.post("/api/login", json={"username": username}).json()["token"]}


ADMIN = login("admin")
mode, who, text = sys.argv[1:4]
overview = http.get("/api/admin/overview", headers=ADMIN).json()
names = overview["names"]
start = max([e["id"] for e in overview["envelopes"]] or [0])
watch = None

if mode == "customer":
    me = login(who)
    group = http.get("/api/groups", headers=me).json()[0]
    sales = login(next(g for g in overview["groups"] if g["order_name"] == group["order_name"])["sales"][0])
    last = max([m["id"] for m in http.get(f"/api/groups/{group['id']}", headers=sales).json()["messages"]] or [0])
    watch = (sales, group["id"], last)
    http.post(f"/api/groups/{group['id']}/messages", headers=me, json={"body": text}).raise_for_status()
elif mode == "watch":
    group = next(g for g in overview["groups"] if g["order_name"] == who)
    sales = login(group["sales"][0])
    gid = next(g["id"] for g in http.get("/api/groups", headers=sales).json() if g["order_name"] == who)
    start -= int(text)
    watch = (sales, gid, max(m["id"] for m in http.get(f"/api/groups/{gid}", headers=sales).json()["messages"]) - int(text))
elif mode == "task":
    http.post("/api/admin/task", headers=ADMIN, json={"agent": who, "body": text}).raise_for_status()
else:
    me = login(who)
    http.post("/api/assistant", headers=me, json={"agent": f"assistant:{who}", "body": text}).raise_for_status()

seen_env, seen_msg, quiet = start, watch[2] if watch else 0, 0
deadline = time.time() + WAIT
while time.time() < deadline:
    time.sleep(6)
    data = http.get("/api/admin/overview", headers=ADMIN).json()
    names.update(data["names"])
    busy = any((a.get("status") or {}).get("busy") for a in data["agents"])
    new = [e for e in data["envelopes"] if e["id"] > seen_env]
    for e in new:
        target = e["recipient"]
        if target.startswith("resource:"):
            target = "资源 " + target[9:]
        print(f"#{e['id']} {names.get(e['sender'], e['sender'])} → {names.get(target, target)} [{e['verb']}/{e['status']}]"
              + (f" 拒绝：{e['reason']}" if e["reason"] else "") + (f"\n    {e['body'][:500]}" if e["body"] else ""))
        seen_env = e["id"]
    if watch:
        sales, gid, _ = watch
        for m in http.get(f"/api/groups/{gid}", headers=sales).json()["messages"]:
            if m["id"] > seen_msg:
                print(f"  [群 {m['author']}/{m['audience']}/{m['kind']}{'/' + m['status'] if m['status'] else ''}] {m['body'][:400]}")
                seen_msg = m["id"]
    quiet = 0 if (new or busy) else quiet + 1
    if quiet >= 4:
        break
print("done")
