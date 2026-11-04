from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.work_case.models.guards import refuse_agent

MATCH = [("matched", "三单一致"), ("variance", "有差异"), ("no_po", "无采购单")]


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "work.case.mixin"]

    ap_match_status = fields.Selection(MATCH, string="三单匹配", copy=False)
    ap_variance_note = fields.Text(string="差异说明", copy=False)
    ap_payment_date = fields.Date(string="计划付款日", copy=False)

    def action_post(self):
        refuse_agent(self.env, _("过账"))
        return super().action_post()

    def button_cancel(self):
        refuse_agent(self.env, _("作废账单"))
        return super().button_cancel()

    def action_register_payment(self):
        refuse_agent(self.env, _("登记付款"))
        return super().action_register_payment()

    # what the AP playbook shows the digital employee, the accountant and the vendor

    def _ap_lines(self):
        return self.invoice_line_ids.filtered(lambda l: l.display_type == "product")

    def _ap_orders(self):
        return self._ap_lines().purchase_line_id.order_id

    def _work_context_record(self):
        return {
            "name": self.name or "", "state": self.state, "vendor": self.partner_id.display_name,
            "vendor_ref": self.ref or "", "invoice_date": fields.Date.to_string(self.invoice_date) or "",
            "due_date": fields.Date.to_string(self.invoice_date_due) or "",
            "payment_term": self.invoice_payment_term_id.name or "", "currency": self.currency_id.name,
            "amount_untaxed": self.amount_untaxed, "amount_total": self.amount_total,
            "payment_state": self.payment_state, "accountant": self.invoice_user_id.name or "",
            "ap_match_status": self.ap_match_status or "", "ap_variance_note": self.ap_variance_note or "",
            "ap_payment_date": fields.Date.to_string(self.ap_payment_date) or "",
        }

    def _work_context_details(self):
        lines = [{
            "product": line.product_id.display_name, "code": line.product_id.default_code or "",
            "billed_qty": line.quantity, "billed_price": line.price_unit, "subtotal": line.price_subtotal,
            "po": line.purchase_line_id.order_id.name or "",
            "ordered_qty": line.purchase_line_id.product_qty, "received_qty": line.purchase_line_id.qty_received,
            "billed_on_other_bills_qty": line.purchase_line_id.qty_invoiced - line.quantity if line.purchase_line_id else 0,
            "po_price": line.purchase_line_id.price_unit,
        } for line in self._ap_lines()]
        duplicates = self.search([
            ("id", "!=", self.id), ("move_type", "=", self.move_type), ("state", "!=", "cancel"),
            ("partner_id.commercial_partner_id", "=", self.partner_id.commercial_partner_id.id),
            ("ref", "=", self.ref),
        ]) if self.ref else self.browse()
        return {
            "lines": lines,
            "purchase_orders": [{
                "name": po.name, "state": po.state, "amount_total": po.amount_total, "origin": po.origin or "",
                "receipts": [{"name": p.name, "state": p.state, "done": fields.Datetime.to_string(p.date_done) or ""}
                             for p in po.picking_ids],
            } for po in self._ap_orders()],
            "same_ref_bills": [{"name": m.name or "", "state": m.state, "amount_total": m.amount_total,
                                "payment_state": m.payment_state} for m in duplicates],
        }

    def _work_facts(self):
        status = dict(MATCH).get(self.ap_match_status, "")
        facts = [("账单", self.name if self.name and self.name != "/" else ""), ("供应商", self.partner_id.display_name),
                 ("供应商发票号", self.ref), ("金额", f"{self.currency_id.name} {self.amount_total:,.2f}"),
                 ("采购单", ", ".join(self._ap_orders().mapped("name"))), ("三单匹配", status),
                 ("到期日", self.invoice_date_due), ("计划付款", self.ap_payment_date)]
        facts += [(f"{line.quantity:g}×", f"{line.product_id.display_name}，{line.price_unit}") for line in self._ap_lines()]
        return [{"label": k, "value": str(v)} for k, v in facts if v]

    def _work_external(self):
        orders = ", ".join(self._ap_orders().mapped("name"))
        return {"title": f"发票 {self.ref or self.name}",
                "subtitle": "，".join(x for x in (orders and f"采购单 {orders}", f"{self.currency_id.name} {self.amount_total:,.2f}") if x)}

    def _work_confidential(self):
        """Whom the goods are for: the vendor must not learn our customers or what we sell to them for."""
        origins = {o.strip() for po in self._ap_orders() for o in (po.origin or "").split(",") if o.strip()}
        if not origins or "sale.order" not in self.env:
            return []
        sales = self.env["sale.order"].sudo().search([("name", "in", list(origins))])
        return [name for so in sales for name in (so.partner_id.commercial_partner_id.name, so.client_order_ref)]

    def _work_identity(self):
        vendor = self.partner_id.commercial_partner_id
        tokens = [vendor.name, *vendor.child_ids.mapped("name"), self.ref, *self._ap_orders().mapped("name")]
        figures = [self.amount_total, self.amount_untaxed] + self._ap_lines().mapped("price_unit")
        return {"tokens": [t for t in tokens if t], "figures": figures}

    def _work_approve_post_bill(self, step):
        if self.state == "draft":
            self.action_post()

    # Odoo actions the digital employee may draft for the accountant

    def _work_actions(self):
        return super()._work_actions() + [{
            "name": "cancel_bill",
            "description": "作废这张草稿账单，用于重复发票或要供应商重开发票（确认后作废，流程结束）",
            "params": "reason 原因",
        }, {
            "name": "vendor_credit",
            "description": "按差异向供应商要一张红字发票：在 Odoo 里起一张关联这张账单的草稿退款单，过账仍由会计在 Odoo 里做。只用于已过账的账单",
            "params": "lines 列表，每项 code 产品编码、qty 数量、price_unit 每件退多少；reason 原因",
        }]

    def _work_check_cancel_bill(self, params):
        reason = (params.get("reason") or "").strip()
        if not reason:
            raise UserError(_("作废要写原因。"))
        if self.state != "draft":
            raise UserError(_("只能作废草稿账单，这张已经是 %s。", self.state))
        preview = _("作废草稿账单：%(vendor)s 发票号 %(ref)s，%(amount)s\n原因：%(reason)s",
                    vendor=self.partner_id.name, ref=self.ref or "", reason=reason,
                    amount=f"{self.currency_id.name} {self.amount_total:,.2f}")
        return {"reason": reason}, preview

    def _work_do_cancel_bill(self, params, proposal):
        self.button_cancel()
        self.sudo().ap_variance_note = "\n".join(x for x in (self.ap_variance_note, _("已作废：%s", params["reason"])) if x)
        self.work_step_ids.filtered(lambda s: s.state != "done").sudo().write(
            {"state": "done", "decision": _("账单已作废"), "decided_by": self.env.user.id, "decided_at": fields.Datetime.now()})
        return {"text": _("账单已作废，流程结束"), "model": "account.move", "id": self.id}

    def _work_check_vendor_credit(self, params):
        reason = (params.get("reason") or "").strip()
        if not reason:
            raise UserError(_("红字发票要写原因。"))
        if self.state != "posted" or self.move_type != "in_invoice":
            raise UserError(_("只能对已过账的供应商账单开红字发票；草稿账单请直接改或作废。"))
        by_code = {line.product_id.default_code: line for line in self._ap_lines()}
        wanted, preview, total = [], [], 0.0
        for item in params.get("lines") or []:
            line = by_code.get(item.get("code"))
            if not line:
                raise UserError(_("这张账单上没有产品 %s。", item.get("code")))
            qty, price = float(item.get("qty") or 0), float(item.get("price_unit") or 0)
            if not 0 < qty <= line.quantity or not 0 < price <= line.price_unit:
                raise UserError(_("%s 的数量或单价超出了原账单。", item.get("code")))
            wanted.append({"line_id": line.id, "qty": qty, "price_unit": price})
            total += qty * price
            preview.append(f"{item['code']}：{qty:g} × {price:g}")
        if not wanted:
            raise UserError(_("至少要有一行。"))
        if total > self.amount_untaxed:
            raise UserError(_("退款合计超过了原账单金额。"))
        open_credits = self.search([("reversed_entry_id", "=", self.id), ("state", "=", "draft")])
        if open_credits:
            raise UserError(_("这张账单已经有一张草稿红字发票 %s。", open_credits[0].name))
        text = _("给 %(vendor)s 的发票 %(ref)s 起一张草稿红字发票，合计 %(cur)s %(total)s（未税）\n%(lines)s\n原因：%(reason)s",
                 vendor=self.partner_id.name, ref=self.ref or self.name, cur=self.currency_id.name,
                 total=f"{total:,.2f}", lines="\n".join(preview), reason=reason)
        return {"lines": wanted, "reason": reason}, text

    def _work_do_vendor_credit(self, params, proposal):
        Line = self.env["account.move.line"]
        credit = self.create({
            "move_type": "in_refund", "partner_id": self.partner_id.id, "currency_id": self.currency_id.id,
            "reversed_entry_id": self.id, "invoice_origin": self.name, "ref": _("%s 差异：%s", self.ref or self.name, params["reason"]),
            "invoice_date": fields.Date.context_today(self),
            "invoice_line_ids": [(0, 0, {
                "product_id": line.product_id.id, "name": line.name, "quantity": item["qty"], "price_unit": item["price_unit"],
                "tax_ids": [(6, 0, line.tax_ids.ids)], "purchase_line_id": line.purchase_line_id.id,
            }) for item in params["lines"] for line in [Line.browse(item["line_id"])]],
        })
        self.message_post(body=_("已起草红字发票 %s，等会计过账", credit.display_name), subtype_xmlid="mail.mt_note")
        return {"text": _("草稿红字发票 %s，合计 %s %s", credit.display_name, credit.currency_id.name, f"{credit.amount_total:,.2f}"),
                "model": "account.move", "id": credit.id}
