{
    "name": "OrigenLab — Comercial",
    "version": "19.0.1.0.0",
    "summary": "Plan de pagos del cliente sobre la cotización",
    "description": """
Concentra en la cotización lo que Odoo reparte en varios registros.

**Plan de pagos.** El caso habitual es 50% de anticipo y 50% contra entrega,
pero puede terminar en N pagos distintos, y **cada pago se factura por separado**.
Odoo no modela eso de fábrica:

- Los *términos de pago* generan **una** factura con varios vencimientos, no
  varias facturas. No sirven para este proceso.
- Los *anticipos* sí generan una factura por cada uno, pero no dejan registrado
  el plan acordado: no hay dónde ver "qué se pactó" frente a "qué se facturó".

Este módulo agrega ese plan a la cotización: N hitos con porcentaje y
disparador, la validación de que sumen 100%, y el enlace a la factura que
cubrió cada hito.
""",
    "author": "OrigenLab",
    "category": "Sales",
    "license": "LGPL-3",
    "depends": ["sale_management", "account"],
    "data": [
        "security/ir.model.access.csv",
        "wizards/dhl_freight_wizard_views.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
    "application": False,
}
