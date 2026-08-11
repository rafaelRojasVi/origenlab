from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    origenlab_bl_number = fields.Char(
        string="N° BL / AWB",
        tracking=True,
        help="Documento de transporte: Bill of Lading (marítimo) o Air Waybill (aéreo).",
    )
    origenlab_etd = fields.Date(
        string="ETD (zarpe)",
        tracking=True,
        help="Fecha estimada de zarpe desde el puerto de origen.",
    )
    origenlab_eta = fields.Date(
        string="ETA (arribo)",
        tracking=True,
        help="Fecha estimada de arribo a puerto chileno.",
    )
    origenlab_customs_agent_id = fields.Many2one(
        comodel_name="res.partner",
        string="Agente de aduana",
        help="Agente que tramita la internación. Sus honorarios se cargan como "
        "costo en destino, no en esta orden.",
    )
    origenlab_customs_entry = fields.Char(
        string="N° DIN",
        tracking=True,
        help="Declaración de Ingreso emitida por el Servicio Nacional de Aduanas.",
    )
    origenlab_nationalization_date = fields.Date(
        string="Fecha de nacionalización",
        tracking=True,
        help="Fecha en que la mercancía queda legalmente disponible en Chile.",
    )
    origenlab_import_notes = fields.Text(string="Notas de importación")

    # --- Entrega final y confidencialidad del precio de compra ---------------
    # El equipo puede despacharse a nuestra dirección o directo a la del cliente.
    # En el segundo caso hay que pedir en aduana que retiren las etiquetas con
    # precios y costos: si se olvida, el cliente ve lo que pagamos al proveedor
    # y eso no se puede deshacer.
    origenlab_final_delivery = fields.Selection(
        selection=[
            ("own", "A nuestra dirección"),
            ("client", "Directo al cliente"),
        ],
        string="Entrega final",
        default="own",
        tracking=True,
        help="Dónde entrega DHL físicamente. Si va directo al cliente, el "
        "retiro de etiquetas de precio pasa a ser obligatorio.",
    )
    origenlab_final_delivery_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Dirección de entrega final",
        tracking=True,
        help="Dirección del cliente cuando el despacho va directo.",
    )
    origenlab_label_removal_state = fields.Selection(
        selection=[
            ("pending", "Pendiente"),
            ("requested", "Solicitado a DHL"),
            ("confirmed", "Confirmado por DHL"),
            ("not_applicable", "No aplica (decisión explícita)"),
        ],
        string="Retiro de etiquetas de precio",
        default="pending",
        required=True,
        tracking=True,
        help="Se tramita en aduana. Debe quedar confirmado —o descartado "
        "explícitamente— antes de nacionalizar si el envío va al cliente.",
    )
    origenlab_label_removal_contact = fields.Char(
        string="Contacto en DHL",
        help="Quién en DHL tomó la solicitud de retiro de etiquetas.",
    )
    origenlab_label_removal_date = fields.Date(
        string="Fecha de solicitud",
        tracking=True,
    )

    @api.constrains(
        "origenlab_nationalization_date",
        "origenlab_final_delivery",
        "origenlab_label_removal_state",
    )
    def _check_origenlab_label_removal(self):
        """No dejar nacionalizar un envío al cliente con las etiquetas sin resolver.

        Se permite seguir marcando "No aplica", pero como decisión explícita:
        la regla del negocio es que nada quede en blanco por descuido
        (BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md §2.2).
        """
        for order in self:
            if not order.origenlab_nationalization_date:
                continue
            if order.origenlab_final_delivery != "client":
                continue
            if order.origenlab_label_removal_state in ("confirmed", "not_applicable"):
                continue
            raise ValidationError(
                _(
                    "El envío %s va directo al cliente. Antes de registrar la "
                    "nacionalización, el retiro de etiquetas de precio debe estar "
                    "en «Confirmado por DHL», o marcado «No aplica» de forma "
                    "explícita. De lo contrario el cliente puede ver el precio "
                    "pagado al proveedor."
                )
                % (order.name or "")
            )

    origenlab_transit_days = fields.Integer(
        string="Días en tránsito",
        compute="_compute_origenlab_transit_days",
        store=True,
        help="Días entre zarpe y arribo. Útil para estimar plazos en cotizaciones "
        "futuras del mismo proveedor.",
    )

    @api.depends("origenlab_etd", "origenlab_eta")
    def _compute_origenlab_transit_days(self):
        for order in self:
            if order.origenlab_etd and order.origenlab_eta:
                order.origenlab_transit_days = (
                    order.origenlab_eta - order.origenlab_etd
                ).days
            else:
                order.origenlab_transit_days = 0

    @api.constrains("origenlab_etd", "origenlab_eta")
    def _check_origenlab_shipment_dates(self):
        for order in self:
            if (
                order.origenlab_etd
                and order.origenlab_eta
                and order.origenlab_eta < order.origenlab_etd
            ):
                raise ValidationError(
                    _("El arribo (ETA) no puede ser anterior al zarpe (ETD).")
                )
