from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from odoo.addons.work_case.models.guards import is_agent

# Order fields the digital employee may fill in, as listed in the trade playbook's agent_fields.
AGENT_FIELDS = (
    "destination_port", "shipping_marks", "lc_no", "lc_expiry", "lc_latest_shipment",
    "vessel", "voyage", "etd", "eta", "container_no", "seal_no", "bl_no",
)


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "work.case.mixin"]

    factory_id = fields.Many2one("res.partner", string="工厂")
    trade_payment_method = fields.Selection([("tt", "T/T 电汇"), ("lc", "L/C 信用证")], string="付款方式")
    destination_port = fields.Char(string="目的港")
    shipping_marks = fields.Text(string="唛头")
    lc_no = fields.Char(string="信用证号")
    lc_expiry = fields.Date(string="信用证有效期")
    lc_latest_shipment = fields.Date(string="最迟装运期")
    vessel = fields.Char(string="船名")
    voyage = fields.Char(string="航次")
    etd = fields.Date(string="ETD")
    eta = fields.Date(string="ETA")
    container_no = fields.Char(string="柜号")
    seal_no = fields.Char(string="封号")
    bl_no = fields.Char(string="提单号")

    # what the trade playbook shows the digital employee, the salesperson and the customer

    def _work_context_record(self):
        return {
            "name": self.name,
            "state": self.state,
            "customer": self.partner_id.display_name,
            "customer_ref": self.client_order_ref or "",
            "salesperson": self.user_id.name or "",
            "currency": self.currency_id.name,
            "amount_total": self.amount_total,
            "date_order": fields.Datetime.to_string(self.date_order),
            "commitment_date": fields.Datetime.to_string(self.commitment_date) if self.commitment_date else "",
            "incoterm": self.incoterm.code or "",
            "incoterm_location": self.incoterm_location or "",
            "payment_term": self.payment_term_id.name or "",
            "trade_payment_method": self.trade_payment_method or "",
            "factory": self.factory_id.display_name or "",
            "note": html2plaintext(self.note or ""),
            **{f: (fields.Date.to_string(self[f]) if self._fields[f].type == "date" else self[f]) or "" for f in AGENT_FIELDS},
        }

    def _work_lines(self):
        return [{
            "product": line.product_id.display_name, "code": line.product_id.default_code or "",
            "description": line.name, "qty": line.product_uom_qty, "uom": line.product_uom_id.name,
            "price_unit": line.price_unit, "subtotal": line.price_subtotal,
        } for line in self.order_line.filtered(lambda l: not l.display_type)]

    def _work_context_details(self):
        return {
            "lines": self._work_lines(),
            "invoices": [{
                "name": move.name or "", "state": move.state, "payment_state": move.payment_state,
                "amount_total": move.amount_total, "amount_residual": move.amount_residual,
            } for move in self.invoice_ids.filtered(lambda m: m.state != "cancel")],
            "deliveries": [{
                "name": picking.name, "state": picking.state,
                "scheduled": fields.Datetime.to_string(picking.scheduled_date),
            } for picking in self.picking_ids],
        }

    def _work_facts(self):
        amount = f"{self.currency_id.name} {self.amount_total:,.2f}"
        facts = [("订单", self.name), ("客户", self.partner_id.display_name), ("客户 PO", self.client_order_ref),
                 ("金额", amount), ("条款", f"{self.incoterm.code or ''} {self.incoterm_location or ''}".strip()),
                 ("交期", fields.Date.to_string(self.commitment_date.date()) if self.commitment_date else ""),
                 ("工厂", self.factory_id.display_name), ("目的港", self.destination_port),
                 ("信用证", self.lc_no), ("最迟装运", self.lc_latest_shipment), ("有效期", self.lc_expiry),
                 ("船名", self.vessel), ("ETD", self.etd)]
        facts += [(f"{line['qty']:g}×", f"{line['product']}，{line['price_unit']}") for line in self._work_lines()]
        return [{"label": k, "value": str(v)} for k, v in facts if v]

    def _work_external(self):
        goods = ", ".join(f"{line['qty']:,.0f} × {line['code'] or line['product']}" for line in self._work_lines())
        place = f"{self.incoterm.code or ''} {self.incoterm_location or ''}".strip()
        return {"title": f"Order {self.client_order_ref or self.name}", "subtitle": ", ".join(x for x in (place, goods) if x)}

    def _work_confidential(self):
        return [self.factory_id.name]

    def _work_identity(self):
        contacts = self.partner_id.commercial_partner_id.child_ids.mapped("name")
        tokens = [self.partner_id.commercial_partner_id.name, *contacts, self.client_order_ref, self.name,
                  self.lc_no, self.bl_no, self.container_no]
        figures = [self.amount_total] + [line.price_unit for line in self.order_line]
        return {"tokens": [t for t in tokens if t], "figures": figures}

    def _work_approve_confirm_order(self, step):
        if self.state in ("draft", "sent"):
            self.action_confirm()

    # Odoo actions the digital employee may draft for the salesperson

    def _work_actions(self):
        return super()._work_actions() + [{
            "name": "factory_po",
            "description": "按生产指令给工厂下采购单（单价按公司成本价自动带出，确认后下达）",
            "params": "lines 列表 [{code 产品编号, qty 数量}], date_planned 要求工厂交货日 YYYY-MM-DD",
        }]

    def _work_check_factory_po(self, params):
        if not self.factory_id:
            raise UserError(_("订单还没有指定工厂。"))
        ordered = {}
        for line in self.order_line.filtered(lambda l: l.product_id):
            ordered[line.product_id.default_code] = ordered.get(line.product_id.default_code, 0) + line.product_uom_qty
        lines = []
        for line in params.get("lines") or []:
            code, qty = str(line.get("code") or "").strip(), float(line.get("qty") or 0)
            if code not in ordered:
                raise UserError(_("%s 不在这个订单里。", code))
            if not 0 < qty <= ordered[code]:
                raise UserError(_("%(code)s 的数量要在 0 到订单数量 %(max)s 之间。", code=code, max=ordered[code]))
            lines.append({"code": code, "qty": qty})
        if not lines:
            raise UserError(_("采购单至少要有一行。"))
        planned = fields.Date.to_date(params.get("date_planned") or False)
        if not planned:
            raise UserError(_("要写工厂交货日 date_planned。"))
        existing = self.env["purchase.order"].sudo().search(
            [("origin", "=", self.name), ("partner_id", "=", self.factory_id.id), ("state", "!=", "cancel")], limit=1)
        if existing:
            raise UserError(_("这个订单已经有给工厂的采购单 %s 了。", existing.name))
        preview = _("向 %(factory)s 下采购单（来源 %(order)s），要求 %(date)s 前交货：\n%(lines)s\n单价按公司成本价带出。",
                    factory=self.factory_id.name, order=self.name, date=planned,
                    lines="\n".join(f"{l['code']} × {l['qty']:,.0f}" for l in lines))
        return {"lines": lines, "date_planned": fields.Date.to_string(planned)}, preview

    def _work_do_factory_po(self, params, proposal):
        Product = self.env["product.product"]
        po = self.env["purchase.order"].create({
            "partner_id": self.factory_id.id, "origin": self.name, "user_id": self.user_id.id,
            "date_planned": params["date_planned"],
            "order_line": [(0, 0, {
                "product_id": (product := Product.search([("default_code", "=", line["code"])], limit=1)).id,
                "product_qty": line["qty"], "price_unit": product.standard_price, "date_planned": params["date_planned"],
                "tax_ids": [(5, 0, 0)],
            }) for line in params["lines"]],
        })
        po.button_confirm()
        return {"text": _("采购单 %s 已下达给 %s", po.name, self.factory_id.name), "model": "purchase.order", "id": po.id}

    # the earlier sop_agent_* names, kept for the DSH batch agent's `sop` tool

    def sop_agent_context(self):
        ctx = self.work_agent_context()
        return {"order": ctx["record"], **ctx["details"], "steps": ctx["steps"], "current_step": ctx["current_step"],
                "messages": ctx["messages"], "chat": ctx["chat"]}

    def sop_agent_log_chat(self, lines):
        return self.work_agent_log_chat(lines)

    def sop_agent_report(self, step_code, summary, values=None):
        return self.work_agent_report(step_code, summary, values)

    def sop_agent_complete(self, step_code, summary):
        return self.work_agent_complete(step_code, summary)

    def sop_agent_request_decision(self, step_code, question, options, recommendation, summary=""):
        return self.work_agent_request_decision(step_code, question, options, recommendation, summary)

    def sop_agent_block(self, step_code, reason):
        return self.work_agent_block(step_code, reason)

    @api.model
    def sop_agent_open_orders(self):
        if not is_agent(self.env):
            raise AccessError(_("这些方法只给数字员工调用。"))
        return [{"name": c["name"], "customer": c["party"], "step": c["step"], "state": c["state"]}
                for c in self.work_agent_cases() if c["step"] and c["state"] != "waiting"]
