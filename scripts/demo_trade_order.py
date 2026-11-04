# Run inside `odoo-bin shell`: one export order paid by L/C, with the customer's PO and the L/C notice in its chatter.
# The L/C deliberately disagrees with the contract in several places, for the digital employee to find.
from markupsafe import Markup

admin = env.ref("base.user_admin")
henv = env(user=admin)
Partner = henv["res.partner"]
customer = Partner.search([("name", "=", "Hamburg Home GmbH")], limit=1) or Partner.create({
    "name": "Hamburg Home GmbH", "is_company": True, "email": "purchasing@hamburg-home.example",
    "country_id": env.ref("base.de").id, "city": "Hamburg",
})
factory = Partner.search([("name", "=", "宁波明达家具厂")], limit=1) or Partner.create({
    "name": "宁波明达家具厂", "is_company": True, "email": "sales@mingda-furniture.example",
    "country_id": env.ref("base.cn").id, "city": "宁波",
})
Product = henv["product.product"]
chair = Product.search([("default_code", "=", "FC-200")], limit=1) or Product.create({
    "name": "Folding Chair FC-200 (steel frame, beech seat)", "default_code": "FC-200",
    "list_price": 18.5, "type": "consu", "taxes_id": [(5, 0, 0)],
})
table = Product.search([("default_code", "=", "FT-120")], limit=1) or Product.create({
    "name": "Folding Table FT-120 (120x60 cm)", "default_code": "FT-120",
    "list_price": 46.0, "type": "consu", "taxes_id": [(5, 0, 0)],
})

order = henv["sale.order"].create({
    "partner_id": customer.id,
    "user_id": admin.id,
    "client_order_ref": "HH-PO-77812",
    "commitment_date": "2026-12-20 08:00:00",
    "incoterm": env.ref("account.incoterm_FOB").id,
    "incoterm_location": "Ningbo",
    "trade_payment_method": "lc",
    "factory_id": factory.id,
    "order_line": [
        (0, 0, {"product_id": chair.id, "product_uom_qty": 2000, "price_unit": 18.5, "tax_ids": [(5, 0, 0)]}),
        (0, 0, {"product_id": table.id, "product_uom_qty": 500, "price_unit": 46.0, "tax_ids": [(5, 0, 0)]}),
    ],
})
order.action_sop_start()
sample = order.sop_step_ids.filtered(lambda s: s.code == "sample")
sample.human_note = "客户 10/20 邮件确认样品，椅面颜色按 2 号色板"
sample.action_approve()

order.message_post(
    body=Markup(
        "Dear Sir,<br/>Please find our purchase order <b>HH-PO-77812</b>:<br/>"
        "1. Folding Chair FC-200, 2,000 pcs @ USD 18.50 = USD 37,000.00<br/>"
        "2. Folding Table FT-120, 500 pcs @ USD 46.00 = USD 23,000.00<br/>"
        "Total USD 60,000.00, FOB Ningbo.<br/>"
        "Shipment: not later than Dec 20, 2026. Destination: Hamburg, Germany.<br/>"
        "Packing: chairs 4 pcs per export carton, tables 1 pc per carton, 5-ply master cartons.<br/>"
        "Shipping marks: HH / HH-PO-77812 / HAMBURG / C/NO. 1-UP<br/>"
        "Payment: irrevocable L/C at sight.<br/>Best regards, Anna Weber, Hamburg Home GmbH"),
    message_type="email", subtype_xmlid="mail.mt_comment", author_id=customer.id,
    subject="PO HH-PO-77812",
)
order.message_post(
    body=Markup(
        "Dear Sir,<br/>We have opened the L/C through Deutsche Bank AG, Hamburg. Main terms:<br/>"
        "L/C No.: DEUT-LC-2026-0815, irrevocable, available by payment at sight<br/>"
        "Beneficiary: YourCompany<br/>Amount: USD 58,000.00<br/>"
        "Latest date of shipment: 2026-12-10; Expiry: 2026-12-25 in China; presentation within 21 days after shipment<br/>"
        "Port of loading: Ningbo; Port of discharge: Rotterdam<br/>"
        "Partial shipment: prohibited; Transhipment: allowed<br/>"
        "Documents required: full set clean on board B/L; commercial invoice in 3 originals; packing list; "
        "GSP Certificate of Origin Form A; inspection certificate issued and signed by the applicant.<br/>"
        "Best regards, Anna Weber"),
    message_type="email", subtype_xmlid="mail.mt_comment", author_id=customer.id,
    subject="L/C DEUT-LC-2026-0815 for HH-PO-77812",
)
env.cr.commit()
print(f"order={order.name} total={order.amount_total} {order.currency_id.name} current_step={order.sop_current_step_id.code}")
