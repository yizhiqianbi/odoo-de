# Run inside `odoo-bin shell`: creates the digital employee's bot user and writes a fresh API key to secrets/bot_api_key.
import datetime
import os

LOGIN = "de_ap_bot"
KEY_FILE = "/home/hubin/odoo-de/secrets/bot_api_key"

groups = [
    env.ref("base.group_user"),
    env.ref("account.group_account_invoice"),
    env.ref("purchase.group_purchase_user"),
]
Users = env["res.users"].with_context(no_reset_password=True)
bot = Users.search([("login", "=", LOGIN)], limit=1)
if not bot:
    bot = Users.create({
        "name": "数字员工 · 应付",
        "login": LOGIN,
        "email": "de-ap-bot@example.invalid",
        "group_ids": [(6, 0, [g.id for g in groups])],
        "lang": "zh_CN",
    })

keys = env["res.users.apikeys"].sudo().search([("user_id", "=", bot.id), ("name", "=", "superagent-local")])
keys.unlink()
expiration = datetime.datetime.now() + datetime.timedelta(days=30)
key = env["res.users.apikeys"].with_user(bot)._generate("rpc", "superagent-local", expiration)
env.cr.commit()

fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(key)
print(f"bot uid={bot.id} partner={bot.partner_id.id} key expires {expiration:%Y-%m-%d}")
