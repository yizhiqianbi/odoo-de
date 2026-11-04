# Run inside `odoo-bin shell` with OUT_DIR set: the digital employee in Discuss gets its key, everyone who decides
# lands on Discuss when they sign in, and everything already waiting is sent to its owner as a card.
import datetime
import os

OUT_DIR = os.environ["OUT_DIR"]
NAME = "trade-chat"

assistant = env.ref("work_case.user_assistant")
assistant.group_ids = [(4, env.ref("base.group_user_regular").id)]
env["res.users.apikeys"].sudo().search([("user_id", "=", assistant.id), ("name", "=", NAME)]).unlink()
key = env["res.users.apikeys"].with_user(assistant)._generate("rpc", NAME, datetime.datetime.now() + datetime.timedelta(days=30))
path = os.path.join(OUT_DIR, "keys", "assistant.key")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(key)
print("assistant key for", assistant.login)

discuss = env.ref("mail.action_discuss")
people = env["res.users"].search([("share", "=", False), ("id", "!=", assistant.id),
                                  ("login", "in", ["admin", "lina", "wangqiang", "zhaomin", "chenjing"])])
people.write({"action_id": discuss.id})
print("home is Discuss for", ", ".join(people.mapped("login")))

print("cards posted:", env["work.assistant"]._backfill())
env.cr.commit()
