"""Actions with outside effect in sales, purchase, stock and accounting that only a person may take."""
from odoo import _, models

from odoo.addons.work_case.models.guards import refuse_agent


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        refuse_agent(self.env, _("确认销售订单"))
        return super().action_confirm()


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def button_confirm(self):
        refuse_agent(self.env, _("确认采购单"))
        return super().button_confirm()


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        refuse_agent(self.env, _("验证出入库"))
        return super().button_validate()


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_post(self):
        refuse_agent(self.env, _("过账"))
        return super().action_post()
