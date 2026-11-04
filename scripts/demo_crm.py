# Run inside `odoo-bin shell`: a small furniture-export pipeline for the workbench, idempotent by lead name.
import datetime

Lead = env["crm.lead"]
Partner = env["res.partner"]
Users = env["res.users"]
today = datetime.date.today()
stages = env["crm.stage"].search([("is_won", "=", False)], order="sequence")
print("stages:", stages.mapped("name"))
by_login = {u.login: u for u in Users.search([("login", "in", ["admin", "lina", "wangqiang"])])}
print("users:", {k: v.name for k, v in by_login.items()})
tag = {name: env["crm.tag"].search([("name", "=", name)], limit=1) or env["crm.tag"].create({"name": name})
       for name in ["广交会", "老客户复购", "官网询盘", "LinkedIn", "大客户"]}


def company(name, country, email):
    return Partner.search([("name", "=", name)], limit=1) or Partner.create({
        "name": name, "is_company": True, "country_id": env.ref(f"base.{country}").id, "email": email})


rows = [
    # name, partner, contact, email, revenue, probability, stage index, owner, tags, days to deadline, type
    ("Hamburg Home 2027 春季折叠椅补单", company("Hamburg Home GmbH", "de", "buying@hamburghome.example"), "Anna Weber", None, 48000, 60, 2, "admin", ["老客户复购"], 20, "opportunity"),
    ("Nordic Living 户外餐桌新系列", company("Nordic Living AB", "se", "erik@nordicliving.example"), "Erik Lindqvist", None, 72000, 30, 1, "lina", ["老客户复购", "大客户"], 35, "opportunity"),
    ("Lyon Café Group 咖啡馆桌椅", company("Lyon Café Group", "fr", "achats@lyoncafe.example"), "Julien Martin", None, 26500, 20, 0, "lina", ["广交会"], 40, "opportunity"),
    ("Texas Outdoor 露营折叠桌", company("Texas Outdoor Co.", "us", "sourcing@texasoutdoor.example"), "Mike Johnson", None, 91000, 45, 1, "wangqiang", ["官网询盘", "大客户"], 25, "opportunity"),
    ("Osaka Interior 和室矮桌", company("Osaka Interior Co., Ltd.", "jp", "kenji@osaka-interior.example"), "Kenji Tanaka", None, 33000, 80, 2, "wangqiang", ["老客户复购"], 10, "opportunity"),
    ("Melbourne Patio 阳台套装", company("Melbourne Patio Pty Ltd", "au", "hello@melbpatio.example"), "Chloe Smith", None, 18000, 10, 0, "admin", ["LinkedIn"], 50, "opportunity"),
    ("Dubai Majlis 酒店宴会椅", company("Dubai Majlis Trading LLC", "ae", "omar@majlis.example"), "Omar Al-Farsi", None, 120000, 70, 2, "admin", ["大客户"], 15, "opportunity"),
    ("Vienna Garden 询价：铝制花园椅", None, "Lukas Huber", "lukas@viennagarden.example", 0, 0, 0, "lina", ["官网询盘"], 0, "lead"),
    ("Warsaw Office 询价：折叠会议桌", None, "Piotr Nowak", "piotr@warsawoffice.example", 0, 0, 0, "wangqiang", ["LinkedIn"], 0, "lead"),
    ("Madrid Terraza 广交会名片", None, "Lucía García", "lucia@madridterraza.example", 0, 0, 0, "admin", ["广交会"], 0, "lead"),
]
for name, partner, contact, email, revenue, prob, stage, owner, tags, days, kind in rows:
    vals = {"name": name, "type": kind, "contact_name": contact, "user_id": by_login[owner].id,
            "tag_ids": [(6, 0, [tag[t].id for t in tags])], "email_from": email or (partner and partner.email)}
    if partner:
        vals["partner_id"] = partner.id
    if kind == "opportunity":
        vals.update(expected_revenue=revenue, probability=prob)
        vals["stage_id"] = stages[min(stage, len(stages) - 1)].id
        vals["date_deadline"] = today + datetime.timedelta(days=days)
    lead = Lead.search([("name", "=", name)], limit=1)
    lead.write(vals) if lead else Lead.create(vals)
env.cr.commit()
print("opportunities:", Lead.search_count([("type", "=", "opportunity")]), "leads:", Lead.search_count([("type", "=", "lead")]))
