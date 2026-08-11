from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Los equipos se cotizan en euros (Ortoalresa y el resto de proveedores
# europeos). Dejarlo en CLP era la trampa: teclear 7400 sin cambiar la divisa
# dejaba el costo 1.000× abajo y el margen mentía sin avisar.
# Para cambiarlo basta editar esta constante.
SUPPLIER_DEFAULT_CURRENCY = "EUR"


class OrigenlabQuoteCost(models.Model):
    _name = "origenlab.quote.cost"
    _description = "Componente de costo de la cotización"
    _order = "order_id, sequence, id"

    order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Cotización",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Concepto", required=True)
    kind = fields.Selection(
        # Para agregar un componente nuevo (garantía extendida, instalación,
        # certificación…) basta con sumar una opción aquí: el cálculo, la
        # conversión de divisa y el total no cambian.
        selection=[
            ("supplier", "Equipo (proveedor)"),
            ("freight", "Flete internacional"),
            ("customs", "Aduana e internación"),
            ("insurance", "Seguro"),
            ("local", "Transporte nacional"),
            ("other", "Otro"),
        ],
        string="Tipo",
        required=True,
        default="other",
    )

    # Solo los componentes de tipo "equipo" se venden como tal y por eso llevan
    # producto: son los que aparecen en las líneas del pedido. El flete o la
    # aduana son costos del negocio, no artículos que el cliente compra.
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Producto",
        help="El equipo del catálogo. Al indicarlo se crea y enlaza su línea "
        "en el pedido.",
    )
    quantity = fields.Float(
        string="Cantidad",
        default=1.0,
        digits="Product Unit of Measure",
    )
    sale_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Línea del pedido",
        readonly=True,
        ondelete="set null",
        copy=False,
        help="Línea que este costo financia. Se crea sola al elegir producto.",
    )

    amount = fields.Monetary(
        string="Monto",
        required=True,
        currency_field="currency_id",
        help="En la divisa en que lo cotizó el proveedor o el transportista.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Divisa",
        required=True,
        default=lambda self: self.env.company.currency_id,
        help="EUR y USD son los casos habituales. Debe tener tipo de cambio "
        "cargado o se convertirá a 1:1 sin avisar.",
    )
    company_currency_id = fields.Many2one(
        related="order_id.company_id.currency_id",
        string="Divisa de la compañía",
        readonly=True,
    )
    amount_company = fields.Monetary(
        string="Monto en CLP",
        compute="_compute_amount_company",
        currency_field="company_currency_id",
        store=False,
        help="Convertido con el tipo de cambio de la fecha de la cotización.",
    )
    note = fields.Char(
        string="Supuesto",
        help="De dónde sale el número: cotización del proveedor, estimado DHL, "
        "referencia anterior. Un estimado sin supuesto no se puede revisar.",
    )

    @api.depends("amount", "currency_id", "order_id.date_order", "order_id.company_id")
    def _compute_amount_company(self):
        for cost in self:
            company = cost.order_id.company_id or self.env.company
            target = company.currency_id
            date = (cost.order_id.date_order or fields.Datetime.now()).date()
            if not cost.currency_id or cost.currency_id == target:
                cost.amount_company = cost.amount
            else:
                cost.amount_company = cost.currency_id._convert(
                    cost.amount, target, company, date
                )

    @api.constrains("amount")
    def _check_amount(self):
        for cost in self:
            if cost.amount < 0:
                raise ValidationError(
                    _("El costo «%s» no puede ser negativo.") % cost.name
                )

    @api.model
    def _origenlab_supplier_currency(self):
        """Divisa por defecto del equipo, con vuelta atrás segura.

        Si EUR estuviera inactiva o sin tipo de cambio, se convertiría 1:1 sin
        avisar — que es justo el error que esto viene a evitar. En ese caso es
        preferible caer a la divisa de la compañía, que al menos no miente.
        """
        currency = self.env["res.currency"].search(
            [("name", "=", SUPPLIER_DEFAULT_CURRENCY), ("active", "=", True)], limit=1
        )
        return currency or self.env.company.currency_id

    @api.onchange("kind")
    def _onchange_kind_currency(self):
        """Al marcar una línea como equipo, proponer EUR.

        Solo si la divisa sigue siendo la de la compañía, es decir intacta: una
        divisa elegida a mano nunca se pisa.
        """
        for cost in self:
            if cost.kind != "supplier":
                continue
            if cost.currency_id and cost.currency_id != self.env.company.currency_id:
                continue
            cost.currency_id = self._origenlab_supplier_currency()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        """Nombrar el costo como el equipo, sin pisar un nombre ya escrito."""
        for cost in self:
            if not cost.product_id:
                continue
            if not cost.name or cost.name in ("Equipo (proveedor)", "Envío"):
                cost.name = cost.product_id.display_name

    # --- Enlace con la línea del pedido --------------------------------------
    def _origenlab_sync_sale_line(self):
        """Crear o alinear la línea del pedido de cada equipo.

        Se mantiene el enlace en un solo sentido: el costo manda sobre producto
        y cantidad. El precio de venta NO se toca aquí — se fija en el paso 5
        (precio sugerido) o a mano, y sobrescribirlo desde el costo borraría
        una decisión comercial.
        """
        SaleLine = self.env["sale.order.line"]
        for cost in self:
            if cost.kind != "supplier" or not cost.product_id:
                continue
            quantity = cost.quantity or 1.0
            if cost.sale_line_id:
                values = {}
                if cost.sale_line_id.product_id != cost.product_id:
                    values["product_id"] = cost.product_id.id
                if cost.sale_line_id.product_uom_qty != quantity:
                    values["product_uom_qty"] = quantity
                if values:
                    cost.sale_line_id.write(values)
                continue
            cost.sale_line_id = SaleLine.create(
                {
                    "order_id": cost.order_id.id,
                    "product_id": cost.product_id.id,
                    "product_uom_qty": quantity,
                }
            )

    @api.model_create_multi
    def create(self, vals_list):
        costs = super().create(vals_list)
        costs._origenlab_sync_sale_line()
        return costs

    def write(self, vals):
        result = super().write(vals)
        if {"product_id", "quantity", "kind"} & set(vals):
            self._origenlab_sync_sale_line()
        return result
