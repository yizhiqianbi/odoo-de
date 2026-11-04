# Run inside `odoo-bin shell`: demo cases for the AP and HR playbooks next to the ten trade orders.
# AP: four vendor bills from the three factories, one exact match, one price variance, one over-billed quantity,
# one duplicate of an already posted bill. HR: two job positions and three applicants.
from markupsafe import Markup

admin = env.ref("base.user_admin")
henv = env(user=admin)
zhaomin = env["res.users"].search([("login", "=", "zhaomin")])
chenjing = env["res.users"].search([("login", "=", "chenjing")])
lina = env["res.users"].search([("login", "=", "lina")])
Partner = henv["res.partner"]
cn = env.ref("base.cn")


def partner(name, **vals):
    return Partner.search([("name", "=", name)], limit=1) or Partner.create({"name": name, **vals})


def product(code):
    return henv["product.product"].search([("default_code", "=", code)], limit=1)


# vendors and the person on their side
VENDORS = {}
for key, company, contact, function in [
    ("mingda", "宁波明达家具厂", "周丽", "财务"),
    ("huayi", "佛山华艺金属制品", "黄海涛", "业务经理"),
    ("haina", "青岛海纳户外用品", "刘洋", "财务主管"),
]:
    vendor = partner(company, is_company=True, country_id=cn.id)
    vendor.write({"lang": "zh_CN", "supplier_rank": max(vendor.supplier_rank, 1)})
    partner(contact, parent_id=vendor.id, function=function, lang="zh_CN", email=f"{key}@example.invalid")
    VENDORS[key] = vendor


def receive(po, quantities=None):
    for picking in po.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
        for move in picking.move_ids:
            move.quantity = (quantities or {}).get(move.product_id.default_code, move.product_uom_qty)
            move.picked = True
        picking.with_context(skip_backorder=True).button_validate()


def bill(po, ref, date, changes=None):
    po.with_user(zhaomin).action_create_invoice()
    move = po.invoice_ids.sorted("id")[-1]
    vals = {"ref": ref, "invoice_date": date, "invoice_user_id": zhaomin.id}
    move.write(vals)
    for line in move.invoice_line_ids:
        line.write((changes or {}).get(line.product_id.default_code, {}))
    return move


def purchase(vendor, origin, lines, date):
    po = henv["purchase.order"].create({
        "partner_id": VENDORS[vendor].id, "origin": origin, "date_order": date, "user_id": zhaomin.id,
        "order_line": [(0, 0, {"product_id": product(code).id, "product_qty": qty, "price_unit": price, "tax_ids": [(5, 0, 0)]})
                       for code, qty, price in lines],
    })
    po.button_confirm()
    return po


AP = [
    # vendor, sale order it serves, PO lines, received, vendor's invoice no., bill changes
    ("mingda", "S00025", [("FC-200", 500, 12.8), ("FT-120", 200, 31.5)], None, "MD-INV-2611", None),
    ("huayi", "S00027", [("ST-60", 1200, 14.2)], None, "HY-2026-0331", {"ST-60": {"price_unit": 14.8}}),
    ("haina", "S00028", [("PU-90", 500, 21.5)], {"PU-90": 400}, "HN-INV-0588", {"PU-90": {"quantity": 500}}),
]
bills = henv["account.move"]
Move = henv["account.move"]
for vendor, origin, lines, received, ref, changes in AP:
    if Move.search([("ref", "=", ref), ("move_type", "=", "in_invoice")], limit=1):
        continue
    po = purchase(vendor, origin, lines, "2026-10-20")
    receive(po, received)
    bills |= bill(po, ref, "2026-10-30", changes)

if not Move.search([("ref", "=", "MD-INV-2598"), ("move_type", "=", "in_invoice")], limit=1):
    po = purchase("mingda", "S00029", [("HC-30", 300, 26.0)], "2026-10-08")
    receive(po)
    first = bill(po, "MD-INV-2598", "2026-10-15")
    first.action_post()
    again = henv["account.move"].create({
        "move_type": "in_invoice", "partner_id": VENDORS["mingda"].id, "ref": "MD-INV-2598",
        "invoice_date": "2026-10-31", "invoice_user_id": zhaomin.id,
        "invoice_line_ids": [(0, 0, {"product_id": product("HC-30").id, "quantity": 300, "price_unit": 26.0,
                                     "purchase_line_id": po.order_line.id, "tax_ids": [(5, 0, 0)]})],
    })
    bills |= again

for move in bills:
    move.with_user(zhaomin).action_work_start()
    print("bill", move.partner_id.name, move.ref, move.amount_total, move.work_current_step_id.code)

# HR: positions, recruiter and interviewers, applicants
recruiter = env["hr.employee"].search([("user_id", "=", chenjing.id)], limit=1)
Job = henv["hr.job"]
JOBS = {}
for name, interviewers, description in [
    ("外贸业务员", lina, "负责欧美客户的出口订单跟进：报价、PO 核对、信用证、订舱和单据。要求：英语读写流利（六级或以上），"
                     "两年以上外贸或跟单经验，熟悉 FOB/CIF 和 L/C 流程，能独立和客户邮件沟通。"),
    ("应付会计", zhaomin, "负责供应商发票审核、三单匹配、付款计划和对账。要求：会计或财务相关专业本科，三年以上应付经验，"
                       "熟悉增值税发票和 ERP 系统，细心。"),
]:
    job = Job.search([("name", "=", name)], limit=1) or Job.create({"name": name})
    job.write({"recruiter_id": recruiter.id, "interviewer_ids": [(6, 0, interviewers.ids)],
               "description": Markup("<p>%s</p>") % description, "no_of_recruitment": 1})
    JOBS[name] = job

Applicant = henv["hr.applicant"]
for name, job, email, expected, proposed, notes in [
    ("张伟", "外贸业务员", "zhangwei@example.invalid", 12000, 11000,
     "宁波大学国际贸易本科，英语六级。3 年外贸业务员经验（家具出口，客户在德国和北欧），独立处理过信用证和订舱。"
     "期望月薪 12000，一个月内可到岗。"),
    ("刘芳", "外贸业务员", "liufang@example.invalid", 8000, 0,
     "应届毕业生，商务英语专业，英语专八。在外贸公司实习 6 个月，做过报价单和单据整理，没有独立跟单经验。"
     "期望月薪 8000，随时可到岗。"),
    ("孙磊", "应付会计", "sunlei@example.invalid", 15000, 14000,
     "财务管理本科，中级会计师。5 年制造业应付会计经验，负责过 200 家供应商的对账和付款计划，用过 SAP 和金蝶。"
     "期望月薪 15000，需要提前一个月离职。"),
]:
    if Applicant.search([("partner_name", "=", name)], limit=1):
        continue
    contact = partner(name, email=email, lang="zh_CN")
    applicant = Applicant.create({
        "partner_id": contact.id, "partner_name": name, "email_from": email, "job_id": JOBS[job].id,
        "salary_expected": expected, "salary_proposed": proposed or False,
        "interviewer_ids": [(6, 0, JOBS[job].interviewer_ids.ids)], "applicant_notes": Markup("<p>%s</p>") % notes,
    })
    applicant.with_user(chenjing).action_work_start()
    print("applicant", name, job, applicant.recruiter_id.name, applicant.work_current_step_id.code)

env.cr.commit()
