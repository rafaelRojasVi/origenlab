from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

from ..models.dhl_rates import DANGEROUS_GOODS, quote_freight, zone_for_country

# Parámetro de sistema: DHL lo fija cada mes y no viene en la guía de tarifas.
FUEL_PARAM = "origenlab.dhl_fuel_surcharge_pct"


class OrigenlabDhlFreightWizard(models.TransientModel):
    _name = "origenlab.dhl.freight.wizard"
    _description = "Calculadora de flete DHL Express Import"

    order_id = fields.Many2one("sale.order", string="Cotización", required=True)
    cost_line_id = fields.Many2one(
        "origenlab.quote.cost",
        string="Línea de costo a completar",
        domain="[('order_id', '=', order_id), ('kind', '=', 'freight')]",
    )

    country_id = fields.Many2one(
        "res.country",
        string="País de origen",
        help="Desde dónde despacha el proveedor. Sirve para proponer la zona.",
    )
    zone = fields.Selection(
        selection=[(str(n), "Zona %s" % n) for n in range(1, 7)],
        string="Zona DHL",
        required=True,
        default="4",
        help="España, Alemania e Italia son zona 4; EE.UU. zona 3; China zona 5.",
    )

    pieces = fields.Integer(string="Piezas", default=1, required=True)
    actual_kg = fields.Float(string="Peso real total (kg)", required=True)
    length_cm = fields.Float(string="Largo por pieza (cm)")
    width_cm = fields.Float(string="Ancho por pieza (cm)")
    height_cm = fields.Float(string="Alto por pieza (cm)")

    dangerous = fields.Selection(
        selection=DANGEROUS_GOODS,
        string="Mercancías peligrosas",
        default="none",
        required=True,
        help="Los reactivos entran aquí. Cuál aplica lo decide el proveedor al "
        "declarar la mercancía: entre exentas y completamente reguladas hay 7×.",
    )
    remote_delivery = fields.Boolean(string="Entrega en área remota")
    formal_clearance = fields.Boolean(string="Liberación formal de aduana")
    fuel_pct = fields.Float(
        string="Recargo por combustible (%)",
        help="DHL lo fija cada mes y no viene en la guía. Sin él, el estimado "
        "queda corto del orden de 20-30%.",
    )

    volumetric_kg = fields.Float(string="Peso volumétrico (kg)", compute="_compute_quote")
    chargeable_kg = fields.Float(string="Peso facturable (kg)", compute="_compute_quote")
    base_usd = fields.Float(string="Tarifa base (USD)", compute="_compute_quote")
    extras_usd = fields.Float(string="Recargos (USD)", compute="_compute_quote")
    fuel_usd = fields.Float(string="Combustible (USD)", compute="_compute_quote")
    total_usd = fields.Float(string="Flete estimado (USD)", compute="_compute_quote")
    breakdown = fields.Html(string="Detalle del cálculo", compute="_compute_quote", sanitize=False)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        stored = self.env["ir.config_parameter"].sudo().get_param(FUEL_PARAM)
        if stored:
            values.setdefault("fuel_pct", float(stored))
        return values

    @api.onchange("country_id")
    def _onchange_country_id(self):
        for wizard in self:
            if not wizard.country_id:
                continue
            zone = zone_for_country(wizard.country_id.name)
            if zone:
                wizard.zone = str(zone)

    @api.depends(
        "zone", "pieces", "actual_kg", "length_cm", "width_cm", "height_cm",
        "dangerous", "remote_delivery", "formal_clearance", "fuel_pct",
    )
    def _compute_quote(self):
        for wizard in self:
            if not wizard.zone or wizard.actual_kg <= 0:
                wizard.volumetric_kg = wizard.chargeable_kg = 0.0
                wizard.base_usd = wizard.extras_usd = wizard.fuel_usd = 0.0
                wizard.total_usd = 0.0
                wizard.breakdown = _("Indica al menos la zona y el peso real.")
                continue

            result = quote_freight(
                zone=int(wizard.zone),
                actual_kg=wizard.actual_kg,
                length_cm=wizard.length_cm,
                width_cm=wizard.width_cm,
                height_cm=wizard.height_cm,
                pieces=wizard.pieces,
                dangerous=wizard.dangerous,
                remote_delivery=wizard.remote_delivery,
                formal_clearance=wizard.formal_clearance,
                fuel_pct=wizard.fuel_pct,
            )
            wizard.volumetric_kg = result["volumetric_kg"]
            wizard.chargeable_kg = result["chargeable_kg"]
            wizard.base_usd = result["base_usd"]
            wizard.extras_usd = result["extras_usd"]
            wizard.fuel_usd = result["fuel_usd"]
            wizard.total_usd = result["total_usd"]

            def usd(amount):
                return formatLang(self.env, amount, digits=2)

            rows = [(_("Tarifa base — zona %s") % wizard.zone, result["base_usd"], "")]
            rows += [(label, amount, "") for label, amount in result["extras"]]
            if wizard.fuel_pct:
                rows.append(
                    (_("Combustible (%s%%)") % formatLang(self.env, wizard.fuel_pct, digits=1),
                     result["fuel_usd"], "")
                )

            body = Markup("").join(
                Markup(
                    '<tr><td class="ps-0">%s</td>'
                    '<td class="text-end pe-0" style="white-space:nowrap">%s USD</td></tr>'
                ) % (label, usd(amount))
                for label, amount, _extra in rows
            )
            total = Markup(
                '<tr class="border-top fw-bold"><td class="ps-0">%s</td>'
                '<td class="text-end pe-0" style="white-space:nowrap">%s USD</td></tr>'
            ) % (_("Flete estimado"), usd(result["total_usd"]))

            peso = Markup('<div class="text-muted small mb-2">%s</div>') % (
                _("Peso facturable %(cobrado)s kg — real %(real)s, volumétrico %(vol)s. "
                  "Se cobra el mayor de los dos.")
                % {
                    "cobrado": formatLang(self.env, result["chargeable_kg"], digits=1),
                    "real": formatLang(self.env, wizard.actual_kg, digits=1),
                    "vol": formatLang(self.env, result["volumetric_kg"], digits=1),
                }
            )
            excluye = Markup('<div class="text-muted small mt-2">%s</div>') % (
                _("No incluye %s.") % ", ".join(result["excludes"])
            )
            wizard.breakdown = (
                peso
                + Markup('<table class="table table-sm mb-0" style="max-width:32rem">%s%s</table>')
                % (body, total)
                + excluye
            )

    def action_apply(self):
        """Escribir el estimado en la línea de flete, con su supuesto."""
        self.ensure_one()
        if self.total_usd <= 0:
            raise UserError(_("No hay nada que aplicar: el flete estimado es cero."))

        usd = self.env["res.currency"].search([("name", "=", "USD")], limit=1)
        if not usd:
            raise UserError(
                _("La divisa USD no está activa. Las tarifas DHL están en USD.")
            )

        line = self.cost_line_id or self.order_id.origenlab_cost_line_ids.filtered(
            lambda cost: cost.kind == "freight"
        )[:1]
        note = _("DHL zona %(zone)s, %(kg).1f kg facturables%(fuel)s") % {
            "zone": self.zone,
            "kg": self.chargeable_kg,
            "fuel": _(", combustible %.1f%%") % self.fuel_pct if self.fuel_pct else _(", SIN combustible"),
        }
        values = {"amount": self.total_usd, "currency_id": usd.id, "note": note}
        if line:
            line.write(values)
        else:
            self.env["origenlab.quote.cost"].create(
                dict(values, order_id=self.order_id.id, kind="freight", name=_("Flete DHL"))
            )

        if self.fuel_pct:
            self.env["ir.config_parameter"].sudo().set_param(FUEL_PARAM, str(self.fuel_pct))
        return {"type": "ir.actions.act_window_close"}
