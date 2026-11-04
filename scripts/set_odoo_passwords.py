# Gives the human Odoo logins random passwords before the web UI is reachable from the network.
# OUT=secrets/odoo_logins.txt odoo-bin shell -c odoo.conf -d de --no-http < scripts/set_odoo_passwords.py
import os
import secrets

lines = []
for login in ("admin", "lina", "wangqiang"):
    user = env["res.users"].search([("login", "=", login)])
    password = secrets.token_urlsafe(12)
    user.password = password
    lines.append(f"{login}\t{password}\t{user.name}")
env.cr.commit()
fd = os.open(os.environ["OUT"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("\n".join(lines) + "\n")
print("passwords written for", [line.split("\t")[0] for line in lines])
