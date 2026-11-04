# Run inside `odoo-bin shell`: the trade SOP's own bot user, kept apart from the AP bot so their groups never add up.
import datetime
import os

LOGIN = "de_trade_bot"
KEY_FILE = "/home/hubin/odoo-de/secrets/trade_bot_api_key"

agent_group = env.ref("trade_sop.group_trade_agent")
Users = env["res.users"].with_context(no_reset_password=True)
bot = Users.search([("login", "=", LOGIN)], limit=1) or Users.create({
    "name": "数字员工 · 跟单",
    "login": LOGIN,
    "email": "de-trade-bot@example.invalid",
    "lang": "zh_CN",
})
bot.group_ids = [(6, 0, [env.ref("base.group_user").id, agent_group.id])]

ap_bot = Users.search([("login", "=", "de_ap_bot")], limit=1)
ap_bot.group_ids = [(3, agent_group.id), (3, env.ref("stock.group_stock_user").id)]

env["res.users.apikeys"].sudo().search([("user_id", "=", bot.id), ("name", "=", "trade-sop-local")]).unlink()
expiration = datetime.datetime.now() + datetime.timedelta(days=30)
key = env["res.users.apikeys"].with_user(bot)._generate("rpc", "trade-sop-local", expiration)
env.cr.commit()

fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(key)
print(f"trade bot uid={bot.id} groups={bot.group_ids.mapped('name')} key expires {expiration:%Y-%m-%d}")
print(f"ap bot groups={ap_bot.group_ids.mapped('name')}")
