# Run inside `odoo-bin shell` with OUT_DIR set: one bot per playbook and the AP / HR people who decide,
# each with its own key, so a scenario's bot never adds up the rights of another scenario's bot.
# OUT_DIR=/home/tradechat LOGINS=secrets/odoo_logins.txt odoo-bin shell -c odoo.conf -d de --no-http < scripts/setup_work_bots.py
import datetime
import os
import secrets

OUT_DIR = os.environ["OUT_DIR"]
LOGINS = os.environ["LOGINS"]
NAME = "work-chat"
expiration = datetime.datetime.now() + datetime.timedelta(days=30)
Users = env["res.users"].with_context(no_reset_password=True)
base = env.ref("base.group_user")


def user(login, name, groups, email):
    record = Users.search([("login", "=", login)], limit=1) or Users.create(
        {"name": name, "login": login, "email": email, "lang": "zh_CN"})
    record.group_ids = [(6, 0, [base.id] + [env.ref(g).id for g in groups])]
    return record


def issue(record, filename):
    env["res.users.apikeys"].sudo().search([("user_id", "=", record.id), ("name", "=", NAME)]).unlink()
    key = env["res.users.apikeys"].with_user(record)._generate("rpc", NAME, expiration)
    fd = os.open(os.path.join(OUT_DIR, "keys", filename), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)


bots = {
    "bot_ap.key": user("de_ap_bot", "数字员工 · 应付", ["work_ap.group_ap_agent"], "de-ap-bot@example.invalid"),
    "bot_hr.key": user("de_hr_bot", "数字员工 · 招聘", ["work_hr.group_hr_agent"], "de-hr-bot@example.invalid"),
}
people = {
    "zhaomin": user("zhaomin", "赵敏", ["account.group_account_invoice", "purchase.group_purchase_user"],
                    "zhaomin@example.invalid"),
    "chenjing": user("chenjing", "陈静", ["hr_recruitment.group_hr_recruitment_user"], "chenjing@example.invalid"),
}
recruiter = people["chenjing"]
if not env["hr.employee"].search([("user_id", "=", recruiter.id)], limit=1):
    env["hr.employee"].create({"name": recruiter.name, "user_id": recruiter.id, "work_email": recruiter.email,
                               "job_title": "招聘主管"})

os.makedirs(os.path.join(OUT_DIR, "keys"), mode=0o700, exist_ok=True)
for filename, record in {**bots, **{f"{k}.key": v for k, v in people.items()}}.items():
    issue(record, filename)

kept = []
if os.path.exists(LOGINS):
    kept = [line for line in open(LOGINS).read().splitlines() if line.split("\t")[0] not in people]
for login, record in people.items():
    password = secrets.token_urlsafe(12)
    record.password = password
    kept.append(f"{login}\t{password}\t{record.name}")
env.cr.commit()
fd = os.open(LOGINS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("\n".join(kept) + "\n")

for record in (*bots.values(), *people.values()):
    print(f"{record.login:10} uid={record.id} groups={sorted(record.group_ids.mapped('name'))}")
print(f"keys expire {expiration:%Y-%m-%d}; passwords for {sorted(people)} written to {LOGINS}")
