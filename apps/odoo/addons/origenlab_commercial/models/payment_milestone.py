from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Tolerancia al comparar la suma de porcentajes con 100. Con 3 pagos de 33,33%
# la suma da 99,99: eso es un plan válido, no un error de captura.
PERCENTAGE_TOLERANCE = 0.05


class OrigenlabPaymentMilestone(models.Model):
    _name = "origenlab.payment.milestone"
    _description = "Hito de pago del cliente"
    _order = "order_id, sequence, id"

    order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Cotización",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Concepto",
        required=True,
        help="Cómo se le nombra al cliente. Ej: «Anticipo», «Contra entrega».",
    )
    percentage = fields.Float(
        string="% del total",
        required=True,
        digits=(5, 2),
        help="Porcentaje del total de la cotización que cubre este pago.",
    )
    trigger = fields.Selection(
        selection=[
            ("on_order", "A la firma / orden de compra"),
            ("on_shipment", "Al embarque"),
            ("on_delivery", "Contra entrega"),
            ("days_after", "A N días de la factura"),
        ],
        string="Se cobra",
        required=True,
        default="on_order",
    )
    days_after = fields.Integer(
        string="Días",
        help="Solo aplica cuando se cobra a N días de la factura.",
    )
    payment_date = fields.Date(
        string="Fecha de pago",
        help="Fecha comprometida con el cliente para este pago. Obligatoria "
        "antes de confirmar el pedido.",
    )

    currency_id = fields.Many2one(
        related="order_id.currency_id",
        string="Divisa",
        readonly=True,
    )
    amount_planned = fields.Monetary(
        string="Monto planificado",
        compute="_compute_amount_planned",
        currency_field="currency_id",
        store=False,
        help="Calculado sobre el total de la cotización. Cambia si cambia la "
        "cotización: es el plan, no lo facturado.",
    )

    invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="Factura",
        domain="[('move_type', '=', 'out_invoice')]",
        help="Factura que cubrió este hito. Cada pago se factura por separado.",
    )
    invoice_status = fields.Selection(
        selection=[
            ("pending", "Por facturar"),
            ("draft", "Factura en borrador"),
            ("invoiced", "Facturado"),
            ("paid", "Pagado"),
        ],
        string="Estado",
        compute="_compute_invoice_status",
        store=False,
    )

    @api.depends("percentage", "order_id.amount_total")
    def _compute_amount_planned(self):
        for milestone in self:
            total = milestone.order_id.amount_total or 0.0
            milestone.amount_planned = total * (milestone.percentage or 0.0) / 100.0

    @api.depends("invoice_id", "invoice_id.state", "invoice_id.payment_state")
    def _compute_invoice_status(self):
        for milestone in self:
            invoice = milestone.invoice_id
            if not invoice:
                milestone.invoice_status = "pending"
            elif invoice.state == "draft":
                milestone.invoice_status = "draft"
            elif invoice.payment_state in ("paid", "in_payment", "reversed"):
                milestone.invoice_status = "paid"
            else:
                milestone.invoice_status = "invoiced"

    @api.constrains("percentage")
    def _check_percentage_range(self):
        for milestone in self:
            if not 0 < milestone.percentage <= 100:
                raise ValidationError(
                    _("El porcentaje de «%s» debe estar entre 0 y 100.") % milestone.name
                )

    @api.constrains("trigger", "days_after")
    def _check_days_after(self):
        for milestone in self:
            if milestone.trigger == "days_after" and milestone.days_after <= 0:
                raise ValidationError(
                    _("«%s» se cobra a N días: indica cuántos.") % milestone.name
                )
