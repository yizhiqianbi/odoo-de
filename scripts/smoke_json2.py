"""Smoke test of the JSON-2 API as the digital employee's bot user."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8069/json/2"
KEY = open("/home/hubin/odoo-de/secrets/bot_api_key").read().strip()


def call(model, method, **body):
    req = urllib.request.Request(
        f"{BASE}/{model}/{method}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"bearer {KEY}", "Content-Type": "application/json; charset=utf-8",
                 "X-Odoo-Database": "de", "User-Agent": "de-smoke"},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


status, ctx = call("res.users", "context_get")
print("whoami", status, ctx.get("uid"), ctx.get("lang"))

status, pos = call("purchase.order", "search_read", domain=[], fields=["name", "partner_id", "state", "amount_total", "invoice_status"], limit=5)
print("purchase orders", status, [(p["name"], p["partner_id"][1], p["state"], p["invoice_status"]) for p in pos])

status, bills = call("account.move", "search_read", domain=[("move_type", "=", "in_invoice")],
                     fields=["name", "partner_id", "state", "amount_total", "ref"], limit=5)
print("vendor bills", status, [(b["name"], b["partner_id"] and b["partner_id"][1], b["state"], b["amount_total"]) for b in bills])

status, err = call("res.users", "write", ids=[2], vals={"name": "hijack"})
print("write admin user (expect refusal)", status, err.get("name"))

status, err = call("ir.config_parameter", "set_param", key="x", value="y")
print("set system param (expect refusal)", status, err.get("name"))
