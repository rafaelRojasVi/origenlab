from odoo import fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    origenlab_cost_ids = fields.One2many(
        comodel_name="origenlab.quote.cost",
        inverse_name="sale_line_id",
        string="Costos asociados",
        readonly=True,
        help="Componentes de costo que financian esta línea. Se enlazan solos "
        "desde la pestaña Costeo.",
    )
    origenlab_cost_amount = fields.Monetary(
        string="Costo",
        compute="_compute_origenlab_cost_amount",
        currency_field="company_currency_id",
        store=False,
        help="Suma en CLP de los costos enlazados a esta línea.",
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Divisa de la compañía",
        readonly=True,
    )

    def _compute_origenlab_cost_amount(self):
        for line in self:
            line.origenlab_cost_amount = sum(
                line.origenlab_cost_ids.mapped("amount_company")
            )
