"""Creates the demo people, one group per case each playbook has started in Odoo, and the agent organisation.

Safe to rerun: existing users, groups, members and agents are kept, and default grants are only written for new
agents, so an admin's edits to the grant table survive restarts. Login is by choosing an identity, without passwords.
"""
import asyncio
import secrets
import unicodedata

from app.odoo import Bot, OdooError
from app.playbooks import PLAYBOOKS
from app.protocol import short_name
from app.store import Store, stamp
from app import config

# the people who decide; chat username -> name. Odoo logins map to these, except Odoo's admin, who is mitchell here.
STAFF = [("mitchell", "Mitchell Admin"), ("lina", "李娜"), ("wangqiang", "王强"), ("zhaomin", "赵敏"), ("chenjing", "陈静")]
LOGIN_TO_USERNAME = config.LOGIN_TO_USERNAME
# which salespeople look after which trade order, by position in the order list (many to many)
ASSIGN = {"mitchell": [0, 1, 2, 3], "lina": [3, 4, 5, 6, 7], "wangqiang": [7, 8, 9, 0]}
TITLES = {"S00025": "Hamburg Home 折叠桌椅"}
PINYIN = {"周丽": "zhouli", "黄海涛": "huanghaitao", "刘洋": "liuyang", "张伟": "zhangwei", "刘芳": "liufang", "孙磊": "sunlei"}
ADVISORS = [("advisor:legal", "法务顾问"), ("advisor:tax", "财税顾问")]


def username(name):
    if name in PINYIN:
        return PINYIN[name]
    if name == "Anna Weber":
        return "anna"
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return ascii_name.split()[0].lower()


def user_id(store, login, name, role):
    user = store.user_by_username(login)
    return user["id"] if user else store.add_user(login, name, role, secrets.token_urlsafe(16))


def default_agent(store, key, name, kind, clearance, grants, owner=None, customer=None):
    if store.agent(key):
        return False
    store.add_agent(key, name, kind, clearance, owner, customer)
    for verb, target, level in grants:
        store.grant(key, verb, target, level)
    return True


async def cases():
    """Every started case of every playbook, with what the chat needs to name its group."""
    found = []
    for code, playbook in PLAYBOOKS.items():
        if not playbook["key"].exists():
            continue
        bot = Bot(playbook["key"])
        try:
            rows = await bot.cases(playbook["model"])
        except OdooError as e:
            print(f"skip {code}: {e}")
            continue
        for row in rows:
            ctx = await bot.call(playbook["model"], "work_agent_context", ids=[row["id"]])
            found.append({**row, "playbook": code, "ctx": ctx})
    return found


def title_and_name(case):
    ctx = case["ctx"]
    if case["playbook"] == "trade":
        order = case["name"].split(" ")[0]
        return TITLES.get(order) or short_name(case["party"]), order
    if case["playbook"] == "ap":
        return f"{case['party']} {ctx['external']['title']}", f"ap-{case['id']}"
    return f"{case['name']} · {ctx['record']['job']}", f"hr-{case['id']}"


def main():
    store = Store(config.DB_PATH)
    admin = user_id(store, "admin", "管理员", "admin")
    staff = {login: user_id(store, login, name, "sales") for login, name in STAFF}
    found = asyncio.run(cases())
    trade = sorted((c for c in found if c["playbook"] == "trade"), key=lambda c: c["name"])

    for case in found:
        title, name = title_and_name(case)
        customer = user_id(store, username(case["contact"]), case["contact"], "customer")
        if case["playbook"] == "trade":
            index = trade.index(case)
            team = [staff[s] for s, picks in ASSIGN.items() if index in picks] or [staff["mitchell"]]
            key = str(case["party_id"])
        else:
            team = [staff.get(LOGIN_TO_USERNAME.get(case["owner_login"], case["owner_login"]), staff["mitchell"])]
            key = name
        group = store.one("select * from groups where order_name = ?", name)
        if not group:
            gid = store.x("insert into groups(order_name, title, sales_id, customer_id, customer_key, chase_minutes, "
                          "playbook, res_model, res_id, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          name, title, team[0], customer, key, 240, case["playbook"], case["model"], case["id"], stamp())
            group = store.group(gid)
        elif not group["res_id"]:
            store.update_group(group["id"], playbook=case["playbook"], res_model=case["model"], res_id=case["id"],
                               customer_key=group["customer_key"] or key)
        for member in team:
            store.add_member(group["id"], member)

    advisor_asks = [("ask", f"agent:{key}", 2) for key, _ in ADVISORS]
    default_agent(store, "root", "Root 总控", "root", 3,
                  [("read", "*", 3), ("ask", "agent:*", 3), ("task", "agent:*", 3)], owner=admin)
    for login, name in STAFF:
        mine = [g["customer_key"] for g in store.groups_for(store.user(staff[login]))]
        default_agent(store, f"assistant:{login}", f"{name} 的助理", "assistant", 2,
                      [("ask", "agent:root", 2), ("read", "*", 1)] + advisor_asks
                      + [(verb, f"agent:customer:{c}", 2) for c in mine for verb in ("ask", "task")]
                      + [("read", f"customer:{c}", 2) for c in mine], owner=staff[login])
    for group in store.all_groups():
        c = group["customer_key"]
        owners = [s["username"] for s in store.salespeople(group) if s["role"] == "sales"]
        default_agent(store, f"customer:{c}", f"{group['title']} agent", "customer", 2,
                      [("ask", f"agent:assistant:{p}", 2) for p in owners] + advisor_asks
                      + [("read", f"customer:{c}", 2), ("read", "*", 0)], customer=c)
    for key, name in ADVISORS:
        if default_agent(store, key, name, "advisor", 2, [("read", "*", 2)]):
            for agent in store.agents():
                if agent["kind"] in ("assistant", "customer"):
                    store.grant(agent["key"], "ask", f"agent:{key}", 2)

    for user in store.users():
        groups = [g["order_name"] for g in store.groups_for(user)] if user["role"] != "admin" else ["全部"]
        print(f"{user['role']:8} {user['username']:12} {user['name']:22} {', '.join(groups)}")
    print("agents:", len(store.agents()), "grants:", len(store.grants()))


main()
