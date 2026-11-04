# Run inside `odoo-bin shell` with OUT_DIR set: Odoo keys for the group chat service, one per identity so each can be revoked alone.
# The AI salesperson uses the trade bot (guarded); the human salesperson's key is used only for actions the salesperson clicks.
import datetime
import os

OUT_DIR = os.environ["OUT_DIR"]
NAME = "trade-chat"
expiration = datetime.datetime.now() + datetime.timedelta(days=30)


def issue(user, filename):
    env["res.users.apikeys"].sudo().search([("user_id", "=", user.id), ("name", "=", NAME)]).unlink()
    key = env["res.users.apikeys"].with_user(user)._generate("rpc", NAME, expiration)
    fd = os.open(os.path.join(OUT_DIR, filename), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return user.login


bot = env["res.users"].search([("login", "=", "de_trade_bot")], limit=1)
# Each salesperson clicks decisions with their own key; files are keys/<chat username>.key.
os.makedirs(os.path.join(OUT_DIR, "keys"), mode=0o700, exist_ok=True)
for chat_name, login in (("mitchell", "admin"), ("lina", "lina"), ("wangqiang", "wangqiang")):
    user = env["res.users"].search([("login", "=", login)], limit=1)
    print("salesperson key for", issue(user, f"keys/{chat_name}.key"))
customer = env["res.partner"].search([("name", "=", "Hamburg Home GmbH")], limit=1)
anna = env["res.partner"].search([("name", "=", "Anna Weber"), ("parent_id", "=", customer.id)], limit=1) or \
    env["res.partner"].create({"name": "Anna Weber", "parent_id": customer.id, "function": "Purchasing Manager",
                               "email": "anna.weber@hamburg-home.example", "lang": "en_US"})
# one key per user and name: a second key for the same person here would revoke their first
print("keys for", issue(bot, "bot_api_key"), f"expire {expiration:%Y-%m-%d}")
print("customer contact", anna.display_name)
env.cr.commit()
