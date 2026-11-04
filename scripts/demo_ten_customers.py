# Run inside `odoo-bin shell`: nine more export customers next to Hamburg Home, each with one order at a different SOP step.
# Several orders share a factory, so the factory's load is something agents need to coordinate on.
from markupsafe import Markup

admin = env.ref("base.user_admin")
henv = env(user=admin)
Users = env["res.users"].with_context(no_reset_password=True)
sales_groups = [env.ref("base.group_user").id, env.ref("sales_team.group_sale_salesman_all_leads").id]


def salesperson(login, name):
    user = Users.search([("login", "=", login)], limit=1) or Users.create(
        {"name": name, "login": login, "email": f"{login}@example.invalid", "lang": "zh_CN"})
    user.group_ids = [(4, g) for g in sales_groups]
    return user


SALES = {"admin": admin, "lina": salesperson("lina", "李娜"), "wangqiang": salesperson("wangqiang", "王强")}

Partner = henv["res.partner"]


def partner(name, **vals):
    return Partner.search([("name", "=", name)], limit=1) or Partner.create({"name": name, **vals})


FACTORIES = {
    "mingda": partner("宁波明达家具厂", is_company=True, country_id=env.ref("base.cn").id, city="宁波"),
    "huayi": partner("佛山华艺金属制品", is_company=True, country_id=env.ref("base.cn").id, city="佛山"),
    "haina": partner("青岛海纳户外用品", is_company=True, country_id=env.ref("base.cn").id, city="青岛"),
}

Product = henv["product.product"]
PRODUCTS = {}
for code, name, price, cost in [
    ("FC-200", "Folding Chair FC-200 (steel frame, beech seat)", 18.5, 12.8),
    ("FT-120", "Folding Table FT-120 (120x60 cm)", 46.0, 31.5),
    ("OB-150", "Outdoor Bench OB-150 (acacia, 150 cm)", 65.0, 44.0),
    ("PU-90", "Parasol PU-90 (2.7 m, crank tilt)", 32.0, 21.5),
    ("HC-30", "Hammock Chair HC-30 (cotton rope)", 39.0, 26.0),
    ("ST-60", "Side Table ST-60 (powder-coated steel)", 22.0, 14.2),
]:
    product = Product.search([("default_code", "=", code)], limit=1) or Product.create(
        {"name": name, "default_code": code, "list_price": price, "type": "consu", "taxes_id": [(5, 0, 0)]})
    product.standard_price = cost
    PRODUCTS[code] = product

# company, country, city, contact, payment, incoterm, place, factory, lines, delivery, current step, salesperson, extra fields, customer's latest note
CUSTOMERS = [
    ("Nordic Living AB", "se", "Stockholm", "Erik Lindqvist", "tt", "FOB", "Ningbo", "mingda",
     [("FC-200", 3000, 17.9), ("ST-60", 800, 21.0)], "2026-12-15", "progress", "admin", {},
     "Erik here. How is production going? Could you send a few photos of the first finished chairs?"),
    ("Maison Claire SARL", "fr", "Lyon", "Camille Dubois", "tt", "CIF", "Le Havre", "huayi",
     [("ST-60", 1200, 22.0), ("PU-90", 400, 33.5)], "2027-01-25", "po", "admin", {},
     "Bonjour, please find our PO MC-2026-114 attached: 1,200 side tables ST-60 and 400 parasols PU-90, CIF Le Havre, "
     "delivery end of January 2027, 30% deposit by T/T and balance against B/L copy. Packing: 2 side tables per carton."),
    ("Brooklyn Loft Supply Inc.", "us", "New York", "Jason Miller", "lc", "FOB", "Ningbo", "mingda",
     [("FT-120", 900, 45.0), ("FC-200", 1800, 18.2)], "2026-12-18", "payment", "admin",
     {"lc_no": "CITI-NY-88231", "lc_expiry": "2027-01-10", "lc_latest_shipment": "2026-12-18"},
     "Our L/C CITI-NY-88231 was issued by Citibank yesterday, amount USD 73,260.00, latest shipment Dec 18, 2026, "
     "partial shipment allowed. Please check and confirm."),
    ("Osaka Interior Co., Ltd.", "jp", "Osaka", "Kenji Tanaka", "tt", "CIF", "Osaka", "haina",
     [("HC-30", 600, 40.0)], "2026-12-08", "inspection", "lina", {},
     "Tanaka desu. Our QC staff can join the final inspection on Nov 10. Is the goods ready by then?"),
    ("Dubai Majlis Trading LLC", "ae", "Dubai", "Omar Al-Farsi", "tt", "CFR", "Jebel Ali", "huayi",
     [("PU-90", 1500, 31.0), ("ST-60", 1000, 21.5)], "2026-11-28", "booking", "lina", {},
     "Please book the earliest vessel to Jebel Ali. We need the goods before the winter season sales."),
    ("Sydney Garden Co. Pty Ltd", "au", "Sydney", "Olivia Brown", "lc", "FOB", "Qingdao", "haina",
     [("OB-150", 500, 64.0), ("HC-30", 300, 39.5)], "2026-11-20", "loading", "lina",
     {"lc_no": "ANZ-SYD-55102", "lc_expiry": "2026-12-15", "lc_latest_shipment": "2026-11-25",
      "vessel": "COSCO SHIPPING ARIES", "voyage": "026S", "etd": "2026-11-18", "eta": "2026-12-01"},
     "Has the container been loaded? Please send me the container and seal numbers once done."),
    ("Milano Casa S.r.l.", "it", "Milan", "Giulia Rossi", "tt", "FOB", "Ningbo", "mingda",
     [("OB-150", 400, 66.0), ("FC-200", 2400, 18.5)], "2027-01-20", "sample", "lina", {},
     "Ciao, we received your catalogue. Could you send us samples of OB-150 and FC-200 in walnut colour?"),
    ("Toronto Patio Ltd.", "ca", "Toronto", "Liam Wilson", "tt", "FOB", "Ningbo", "mingda",
     [("FT-120", 700, 46.0), ("PU-90", 600, 32.0)], "2026-12-12", "packing", "wangqiang", {},
     "Please make sure the shipping marks follow our new artwork: TP / PO-7781 / TORONTO / C/NO. 1-UP."),
    ("São Paulo Móveis Ltda.", "br", "São Paulo", "Ana Souza", "lc", "CFR", "Santos", "huayi",
     [("ST-60", 1500, 21.0), ("FC-200", 2000, 18.0)], "2026-11-10", "documents", "wangqiang",
     {"lc_no": "ITAU-SP-20931", "lc_expiry": "2026-12-05", "lc_latest_shipment": "2026-11-15",
      "vessel": "MSC ANNA", "voyage": "FA644W", "etd": "2026-11-08", "eta": "2026-12-12",
      "container_no": "MSCU 4471205", "seal_no": "ML-2290811", "bl_no": "MEDUFA644812"},
     "We need the full set of documents by courier within the L/C presentation period. Please send the drafts first."),
]

for (company, country, city, contact, payment, incoterm, place, factory, lines, delivery, target, sales_login,
     extra, note) in CUSTOMERS:
    customer = partner(company, is_company=True, country_id=env.ref(f"base.{country}").id, city=city,
                       email=f"info@{company.split()[0].lower()}.example")
    person = partner(contact, parent_id=customer.id, email=f"{contact.split()[0].lower()}@{company.split()[0].lower()}.example")
    ref = f"{company.split()[0][:2].upper()}-PO-{abs(hash(company)) % 90000 + 10000}"
    if henv["sale.order"].search([("partner_id", "=", customer.id)], limit=1):
        continue
    order = henv["sale.order"].create({
        "partner_id": customer.id,
        "user_id": SALES[sales_login].id,
        "client_order_ref": ref,
        "commitment_date": f"{delivery} 08:00:00",
        "incoterm": env.ref(f"account.incoterm_{incoterm}").id,
        "incoterm_location": place,
        "trade_payment_method": payment,
        "factory_id": FACTORIES[factory].id,
        "order_line": [(0, 0, {"product_id": PRODUCTS[code].id, "product_uom_qty": qty, "price_unit": price,
                               "tax_ids": [(5, 0, 0)]}) for code, qty, price in lines],
    })
    order.action_sop_start()
    for step in order.sop_step_ids.sorted("sequence"):
        if step.code == target:
            break
        step.human_note = "演示数据：此前已完成"
        step.action_approve()
    if extra:
        order.write(extra)
    order.message_post(body=Markup("%s<br/>%s, %s") % (note, person.name, company), message_type="email",
                       subtype_xmlid="mail.mt_comment", author_id=person.id, subject=f"{ref}")
    print(f"{order.name} {company} {payment} -> {order.sop_current_step_id.code} ({SALES[sales_login].name})")

env.cr.commit()
