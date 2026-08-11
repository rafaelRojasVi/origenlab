from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .payment_milestone import PERCENTAGE_TOLERANCE

# El caso por defecto que describe la operación: mitad al inicio para poder
# lanzar la importación, mitad cuando el equipo llega.
# Cuánto puede desviarse el margen real del objetivo antes de marcarlo. Medio
# punto porcentual absorbe el redondeo del precio sin dar falsas alarmas.
MARGIN_TOLERANCE_PP = 0.5

DEFAULT_PLAN = [
    {"name": "Anticipo", "percentage": 50.0, "trigger": "on_order", "sequence": 10},
    {"name": "Contra entrega", "percentage": 50.0, "trigger": "on_delivery", "sequence": 20},
]


class SaleOrder(models.Model):
    _inherit = "sale.order"

    origenlab_payment_milestone_ids = fields.One2many(
        comodel_name="origenlab.payment.milestone",
        inverse_name="order_id",
        string="Plan de pagos",
        copy=True,
        compute="_compute_origenlab_payment_milestone_ids",
        store=True,
        readonly=False,
        help="Se llena solo con el plan 50/50 en cuanto la cotización tiene "
        "total. Editable: cualquier cambio manual queda y no se pisa.",
    )
    origenlab_payment_plan_pct = fields.Float(
        string="% cubierto por el plan",
        compute="_compute_origenlab_payment_plan",
        digits=(5, 2),
        store=False,
    )
    origenlab_payment_plan_balanced = fields.Boolean(
        compute="_compute_origenlab_payment_plan",
        store=False,
        help="Verdadero cuando los hitos suman 100%.",
    )

    # --- Costeo: de lo que pagamos al precio que cobramos --------------------
    def _origenlab_default_cost_lines(self):
        """Toda cotización arranca con equipo y envío: son los dos que siempre hay.

        Van en 0 y a completar. Aparecen para que no se olviden, no porque
        valgan cero: una cotización sin flete cargado infla el margen.
        """
        currency = self.env.company.currency_id
        supplier_currency = self.env["origenlab.quote.cost"]._origenlab_supplier_currency()
        return [
            fields.Command.create(
                {
                    "kind": "supplier",
                    "name": "Equipo (proveedor)",
                    "sequence": 10,
                    "amount": 0.0,
                    "currency_id": supplier_currency.id,
                }
            ),
            fields.Command.create(
                {
                    "kind": "freight",
                    "name": "Envío",
                    "sequence": 20,
                    "amount": 0.0,
                    "currency_id": currency.id,
                }
            ),
        ]

    origenlab_cost_line_ids = fields.One2many(
        comodel_name="origenlab.quote.cost",
        inverse_name="order_id",
        string="Costos del negocio",
        copy=True,
        default=_origenlab_default_cost_lines,
    )
    origenlab_cost_total = fields.Monetary(
        string="Costo total",
        compute="_compute_origenlab_costing",
        currency_field="company_currency_id",
        store=False,
    )
    origenlab_margin_pct = fields.Float(
        string="Margen objetivo (%)",
        default=30.0,
        digits=(5, 2),
        help="Sobre el costo total. El precio sugerido es costo × (1 + margen).",
    )
    origenlab_price_suggested = fields.Monetary(
        string="Precio sugerido",
        compute="_compute_origenlab_costing",
        currency_field="company_currency_id",
        store=False,
        help="Sugerencia, no un precio aplicado. El precio al cliente se fija "
        "a mano en las líneas.",
    )
    origenlab_margin_real_pct = fields.Float(
        string="Margen real (%)",
        compute="_compute_origenlab_costing",
        digits=(5, 2),
        store=False,
        help="Con el precio que realmente está en las líneas hoy.",
    )
    origenlab_margin_amount = fields.Monetary(
        string="Ganancia",
        compute="_compute_origenlab_costing",
        currency_field="company_currency_id",
        store=False,
        help="Precio neto menos costo total: lo que efectivamente deja el "
        "negocio, en pesos. Queda en cero mientras no haya costos cargados, "
        "porque sin costo el «margen» sería la venta completa.",
    )
    origenlab_margin_gap_pct = fields.Float(
        string="Desvío vs objetivo (pp)",
        compute="_compute_origenlab_costing",
        digits=(5, 2),
        store=False,
        help="Margen real menos margen objetivo, en puntos porcentuales.",
    )
    origenlab_margin_state = fields.Selection(
        selection=[
            ("no_cost", "Sin costear"),
            ("no_price", "Sin precio"),
            ("loss", "Bajo el costo"),
            ("below", "Bajo el objetivo"),
            ("on_target", "En el objetivo"),
            ("above", "Sobre el objetivo"),
        ],
        string="Estado del margen",
        compute="_compute_origenlab_costing",
        store=False,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Divisa de la compañía",
        readonly=True,
    )

    @api.depends(
        "origenlab_cost_line_ids.amount_company",
        "origenlab_margin_pct",
        "amount_untaxed",
    )
    def _compute_origenlab_costing(self):
        for order in self:
            cost = sum(order.origenlab_cost_line_ids.mapped("amount_company"))
            order.origenlab_cost_total = cost
            order.origenlab_price_suggested = cost * (
                1.0 + (order.origenlab_margin_pct or 0.0) / 100.0
            )
            # Margen real contra el precio que hoy está en las líneas, neto de
            # impuestos. Sin costos cargados no significa nada, así que 0.
            if cost > 0 and order.amount_untaxed:
                real = (order.amount_untaxed - cost) / cost * 100.0
                order.origenlab_margin_amount = order.amount_untaxed - cost
            else:
                real = 0.0
                order.origenlab_margin_amount = 0.0
            order.origenlab_margin_real_pct = real

            gap = real - (order.origenlab_margin_pct or 0.0)
            order.origenlab_margin_gap_pct = gap if cost > 0 else 0.0

            # El punto es que un precio fuera del margen objetivo se vea, no que
            # se corrija solo: puede haber una razón comercial para venderlo así.
            if cost <= 0:
                order.origenlab_margin_state = "no_cost"
            elif not order.amount_untaxed:
                order.origenlab_margin_state = "no_price"
            elif real < 0:
                order.origenlab_margin_state = "loss"
            elif abs(gap) <= MARGIN_TOLERANCE_PP:
                order.origenlab_margin_state = "on_target"
            elif gap < 0:
                order.origenlab_margin_state = "below"
            else:
                order.origenlab_margin_state = "above"

    def action_origenlab_apply_suggested_price(self):
        """Llevar el precio sugerido a la línea, cuando hay una sola.

        El caso por defecto del negocio es un equipo grande, es decir una línea.
        Con varias no se puede repartir sin inventar un criterio, así que se
        avisa en vez de adivinar.
        """
        for order in self:
            lines = order.order_line.filtered(lambda line: not line.display_type)
            if len(lines) != 1:
                raise ValidationError(
                    _(
                        "%(order)s tiene %(count)s líneas. El precio sugerido "
                        "solo se aplica cuando hay una: con varias habría que "
                        "repartirlo y no hay criterio para hacerlo por ti. "
                        "Usa el sugerido (%(price)s) como referencia y fija los "
                        "precios a mano."
                    )
                    % {
                        "order": order.name or "",
                        "count": len(lines),
                        "price": order.origenlab_price_suggested,
                    }
                )
            line = lines[0]
            quantity = line.product_uom_qty or 1.0
            line.price_unit = order.origenlab_price_suggested / quantity
        return True

    @api.depends("amount_total")
    def _compute_origenlab_payment_milestone_ids(self):
        """Sembrar el plan 50/50 en cuanto la cotización tiene precio final.

        Solo siembra cuando no hay plan: un plan editado a mano nunca se pisa,
        aunque después cambie el total. Si lo borras entero, vuelve el 50/50.
        """
        for order in self:
            if order.origenlab_payment_milestone_ids:
                continue
            if (order.amount_total or 0.0) <= 0:
                continue
            order.origenlab_payment_milestone_ids = [
                fields.Command.create(values) for values in DEFAULT_PLAN
            ]

    @api.depends("origenlab_payment_milestone_ids.percentage")
    def _compute_origenlab_payment_plan(self):
        for order in self:
            total = sum(order.origenlab_payment_milestone_ids.mapped("percentage"))
            order.origenlab_payment_plan_pct = total
            order.origenlab_payment_plan_balanced = (
                bool(order.origenlab_payment_milestone_ids)
                and abs(total - 100.0) <= PERCENTAGE_TOLERANCE
            )

    @api.constrains("origenlab_payment_milestone_ids")
    def _check_origenlab_payment_plan(self):
        """Un plan que no suma 100% deja plata sin facturar y nadie lo nota."""
        for order in self:
            milestones = order.origenlab_payment_milestone_ids
            if not milestones:
                continue
            total = sum(milestones.mapped("percentage"))
            if abs(total - 100.0) > PERCENTAGE_TOLERANCE:
                raise ValidationError(
                    _(
                        "El plan de pagos de %(order)s suma %(total).2f%%, no 100%%. "
                        "Ajusta los hitos: si no, queda saldo sin facturar."
                    )
                    % {"order": order.name or "", "total": total}
                )

    def action_origenlab_open_dhl_calculator(self):
        """Abrir la calculadora de flete, apuntando a la línea de flete si existe."""
        self.ensure_one()
        freight = self.origenlab_cost_line_ids.filtered(lambda cost: cost.kind == "freight")
        return {
            "type": "ir.actions.act_window",
            "name": _("Calcular flete DHL"),
            "res_model": "origenlab.dhl.freight.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_order_id": self.id,
                "default_cost_line_id": freight[:1].id or False,
            },
        }

    def action_confirm(self):
        """No confirmar un negocio con pagos sin fecha.

        La fecha se pide al confirmar, no al cotizar: mientras la cotización
        está en borrador puede no conocerse aún la fecha de entrega. Pero un
        pedido confirmado sin fechas de cobro no se puede seguir.
        """
        for order in self:
            undated = order.origenlab_payment_milestone_ids.filtered(
                lambda milestone: not milestone.payment_date
            )
            if undated:
                raise ValidationError(
                    _(
                        "Falta la fecha de pago en: %(hitos)s.\n"
                        "Cada pago del plan necesita su fecha antes de confirmar %(order)s."
                    )
                    % {
                        "hitos": ", ".join(undated.mapped("name")),
                        "order": order.name or "",
                    }
                )
        return super().action_confirm()

    def action_origenlab_load_default_payment_plan(self):
        """Cargar el plan habitual 50/50, sin pisar uno ya definido."""
        for order in self:
            if order.origenlab_payment_milestone_ids:
                raise ValidationError(
                    _(
                        "%s ya tiene un plan de pagos. Bórralo primero si quieres "
                        "volver al 50/50 por defecto."
                    )
                    % (order.name or "")
                )
            order.origenlab_payment_milestone_ids = [
                fields.Command.create(values) for values in DEFAULT_PLAN
            ]
        return True
