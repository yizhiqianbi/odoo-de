"""SQLite store for the group chat. The case itself lives in Odoo; this keeps people, groups, messages and follow-ups."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

SCHEMA = """
create table if not exists users(
    id integer primary key, username text unique not null, name text not null,
    role text not null check (role in ('sales', 'customer')), salt text not null, pw_hash text not null);
create table if not exists tokens(token text primary key, user_id integer not null, created_at text not null);
create table if not exists groups(
    id integer primary key, order_name text unique not null, title text not null,
    sales_id integer not null, customer_id integer not null,
    ai_auto integer not null default 1, sim_customer integer not null default 0,
    chase_minutes integer not null default 5, odoo_fp text not null default '', created_at text not null);
-- audience 'all' is the group; 'internal' is only the salesperson and the AI.
-- kind: text | draft (AI message held for approval) | decision (an Odoo SOP step waiting for the salesperson)
create table if not exists messages(
    id integer primary key, group_id integer not null, author text not null check (author in ('sales', 'ai', 'customer')),
    audience text not null check (audience in ('all', 'internal')), kind text not null default 'text',
    body text not null, meta text not null default '{}', status text not null default '', created_at text not null);
create index if not exists messages_group on messages(group_id, id);
create table if not exists followups(
    id integer primary key, group_id integer not null, due_at text not null, what text not null,
    status text not null default 'open', result text not null default '', created_at text not null);
-- salespeople in a group, many to many; groups.sales_id is the order's main salesperson in Odoo
create table if not exists group_members(group_id integer not null, user_id integer not null, primary key (group_id, user_id));
-- the agent organisation; kind: root | assistant (one per salesperson) | customer (one per customer)
create table if not exists agents(
    key text primary key, name text not null, kind text not null, owner_user_id integer,
    customer_key text, clearance integer not null, enabled integer not null default 1, created_at text not null);
create table if not exists grants(
    id integer primary key, subject text not null, verb text not null check (verb in ('ask', 'task', 'read')),
    target text not null, max_level integer not null default 3, source text not null default 'default',
    created_at text not null, unique (subject, verb, target));
-- every agent interaction, delivered or denied; sender/recipient are "agent:<key>", "user:<id>" or "resource:<name>"
create table if not exists envelopes(
    id integer primary key, sender text not null, recipient text not null, verb text not null, body text not null,
    status text not null, reason text not null default '', reply_to integer, level integer, created_at text not null);
create index if not exists envelopes_recipient on envelopes(recipient, status);
-- every tool call an agent made on a case, for the action ledger; risk is R0 read .. R3 Odoo action a person confirms
create table if not exists actions(
    id integer primary key, group_id integer not null, agent text not null, tool text not null, risk text not null,
    args text not null, result text not null, ok integer not null, created_at text not null);
create index if not exists actions_group on actions(group_id, id);
"""

USERS_V2 = """
create table users_v2(
    id integer primary key, username text unique not null, name text not null,
    role text not null check (role in ('admin', 'sales', 'customer')), salt text not null, pw_hash text not null);
insert into users_v2 select * from users;
drop table users;
alter table users_v2 rename to users;
"""


def now():
    return datetime.now(timezone.utc)


def stamp(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def parse(ts):
    return datetime.fromisoformat(ts)


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        if "'admin'" not in self.one("select sql from sqlite_master where name = 'users'")["sql"]:
            self.db.executescript("begin;" + USERS_V2 + "commit;")
        columns = {c["name"] for c in self.q("pragma table_info(groups)")}
        if "customer_key" not in columns:
            self.x("alter table groups add column customer_key text not null default ''")
        # a group is about one case: a record in Odoo worked by one playbook
        if "playbook" not in columns:
            self.x("alter table groups add column playbook text not null default 'trade'")
            self.x("alter table groups add column res_model text not null default 'sale.order'")
            self.x("alter table groups add column res_id integer not null default 0")

    def q(self, sql, *args):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args)]

    def one(self, sql, *args):
        rows = self.q(sql, *args)
        return rows[0] if rows else None

    def x(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).lastrowid

    # people

    def add_user(self, username, name, role, password):
        salt = secrets.token_hex(16)
        return self.x("insert into users(username, name, role, salt, pw_hash) values (?, ?, ?, ?, ?)",
                      username, name, role, salt, hash_password(password, salt))

    def login(self, username, password):
        user = self.one("select * from users where username = ?", username)
        if not user or not hmac.compare_digest(user["pw_hash"], hash_password(password, user["salt"])):
            return None
        return self.issue_token(user["id"])

    def user_by_username(self, username):
        return self.one("select id, username, name, role from users where username = ?", username)

    def issue_token(self, user_id):
        token = secrets.token_urlsafe(32)
        self.x("insert into tokens(token, user_id, created_at) values (?, ?, ?)", token, user_id, stamp())
        return token

    def user_by_token(self, token):
        return self.one("select u.id, u.username, u.name, u.role from tokens t join users u on u.id = t.user_id "
                        "where t.token = ?", token or "")

    def user(self, user_id):
        return self.one("select id, username, name, role from users where id = ?", user_id)

    # groups

    def users(self):
        return self.q("select id, username, name, role from users order by case role when 'admin' then 0 "
                      "when 'sales' then 1 else 2 end, id")

    def groups_for(self, user):
        if user["role"] == "admin":
            return self.all_groups()
        return self.q("select * from groups where customer_id = ? or sales_id = ? or id in "
                      "(select group_id from group_members where user_id = ?) order by id",
                      user["id"], user["id"], user["id"])

    def members(self, group):
        ids = {group["customer_id"], group["sales_id"]}
        ids |= {r["user_id"] for r in self.q("select user_id from group_members where group_id = ?", group["id"])}
        return ids

    def salespeople(self, group):
        return [self.user(i) for i in sorted(self.members(group) - {group["customer_id"]})]

    def add_member(self, group_id, user_id):
        self.x("insert or ignore into group_members(group_id, user_id) values (?, ?)", group_id, user_id)

    def group_by_customer(self, customer_key):
        return self.one("select * from groups where customer_key = ? order by id limit 1", customer_key)

    def group(self, group_id):
        return self.one("select * from groups where id = ?", group_id)

    def all_groups(self):
        return self.q("select * from groups order by id")

    def update_group(self, group_id, **vals):
        for key, value in vals.items():
            self.x(f"update groups set {key} = ? where id = ?", value, group_id)

    # messages

    @staticmethod
    def _msg(row):
        if row:
            row["meta"] = json.loads(row["meta"])
        return row

    def add_message(self, group_id, author, body, audience="all", kind="text", meta=None, status=""):
        mid = self.x("insert into messages(group_id, author, audience, kind, body, meta, status, created_at) "
                     "values (?, ?, ?, ?, ?, ?, ?, ?)",
                     group_id, author, audience, kind, body, json.dumps(meta or {}, ensure_ascii=False), status, stamp())
        return self.message(mid)

    def message(self, mid):
        return self._msg(self.one("select * from messages where id = ?", mid))

    def update_message(self, mid, **vals):
        if "meta" in vals:
            vals["meta"] = json.dumps(vals["meta"], ensure_ascii=False)
        for key, value in vals.items():
            self.x(f"update messages set {key} = ? where id = ?", value, mid)
        return self.message(mid)

    def messages(self, group_id, internal=True, limit=300):
        rows = self.q("select * from messages where group_id = ? and (audience = 'all' or ?) order by id desc limit ?",
                      group_id, 1 if internal else 0, limit)
        return [self._msg(row) for row in reversed(rows)]

    def count_since(self, group_id, author, seconds, audience="all"):
        since = stamp(now() - timedelta(seconds=seconds))
        return self.one("select count(*) n from messages where group_id = ? and author = ? and audience = ? "
                        "and kind = 'text' and created_at >= ?", group_id, author, audience, since)["n"]

    # follow-ups the AI keeps for itself

    def add_followup(self, group_id, minutes, what):
        fid = self.x("insert into followups(group_id, due_at, what, created_at) values (?, ?, ?, ?)",
                     group_id, stamp(now() + timedelta(minutes=minutes)), what, stamp())
        return self.one("select * from followups where id = ?", fid)

    def followups(self, group_id, status="open"):
        return self.q("select * from followups where group_id = ? and status = ? order by due_at", group_id, status)

    def due_followups(self, group_id):
        return self.q("select * from followups where group_id = ? and status = 'open' and due_at <= ? order by due_at",
                      group_id, stamp())

    def close_followup(self, group_id, fid, status, result=""):
        self.x("update followups set status = ?, result = ? where id = ? and group_id = ?", status, result, fid, group_id)

    # agents, grants, envelopes

    def add_agent(self, key, name, kind, clearance, owner_user_id=None, customer_key=None):
        self.x("insert or ignore into agents(key, name, kind, owner_user_id, customer_key, clearance, created_at) "
               "values (?, ?, ?, ?, ?, ?, ?)", key, name, kind, owner_user_id, customer_key, clearance, stamp())

    def agent(self, key):
        return self.one("select * from agents where key = ?", key)

    def agents(self):
        return self.q("select * from agents order by case kind when 'root' then 0 when 'assistant' then 1 else 2 end, key")

    def update_agent(self, key, **vals):
        for k, v in vals.items():
            self.x(f"update agents set {k} = ? where key = ?", v, key)

    def grant(self, subject, verb, target, max_level, source="default"):
        self.x("insert into grants(subject, verb, target, max_level, source, created_at) values (?, ?, ?, ?, ?, ?) "
               "on conflict(subject, verb, target) do update set max_level = excluded.max_level, source = excluded.source",
               subject, verb, target, max_level, source, stamp())

    def grants(self, subject=None, verb=None):
        sql, args = "select * from grants where 1 = 1", []
        if subject:
            sql, args = sql + " and subject = ?", args + [subject]
        if verb:
            sql, args = sql + " and verb = ?", args + [verb]
        return self.q(sql + " order by subject, verb, target", *args)

    def revoke(self, grant_id):
        self.x("delete from grants where id = ?", grant_id)

    def add_envelope(self, sender, recipient, verb, body, status, reason="", reply_to=None, level=None):
        eid = self.x("insert into envelopes(sender, recipient, verb, body, status, reason, reply_to, level, created_at) "
                     "values (?, ?, ?, ?, ?, ?, ?, ?, ?)", sender, recipient, verb, body, status, reason, reply_to, level,
                     stamp())
        return self.envelope(eid)

    def envelope(self, eid):
        return self.one("select * from envelopes where id = ?", eid)

    def set_envelope_status(self, eid, status):
        self.x("update envelopes set status = ? where id = ?", status, eid)

    def envelopes(self, principal=None, limit=200):
        if principal:
            rows = self.q("select * from envelopes where sender = ? or recipient = ? order by id desc limit ?",
                          principal, principal, limit)
        else:
            rows = self.q("select * from envelopes order by id desc limit ?", limit)
        return list(reversed(rows))

    def open_envelopes(self, recipient):
        return self.q("select * from envelopes where recipient = ? and status = 'open' order by id", recipient)

    def envelopes_sent_since(self, sender, seconds):
        since = stamp(now() - timedelta(seconds=seconds))
        return self.one("select count(*) n from envelopes where sender = ? and created_at >= ? and verb in ('ask', 'task')",
                        sender, since)["n"]

    # the action ledger

    def add_action(self, group_id, agent, tool, risk, args, result, ok):
        aid = self.x("insert into actions(group_id, agent, tool, risk, args, result, ok, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                     group_id, agent, tool, risk, json.dumps(args, ensure_ascii=False),
                     json.dumps(result, ensure_ascii=False), int(ok), stamp())
        return self.action(aid)

    @staticmethod
    def _action(row):
        if row:
            row["args"], row["result"] = json.loads(row["args"]), json.loads(row["result"])
        return row

    def action(self, aid):
        return self._action(self.one("select * from actions where id = ?", aid))

    def actions(self, group_ids, limit=300):
        if not group_ids:
            return []
        marks = ",".join("?" * len(group_ids))
        rows = self.q(f"select * from actions where group_id in ({marks}) order by id desc limit ?", *group_ids, limit)
        return [self._action(r) for r in rows]
