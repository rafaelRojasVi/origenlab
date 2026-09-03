{
    "name": "OrigenLab — Importaciones",
    "version": "19.0.1.0.0",
    "summary": "Embarque, aduana y nacionalización sobre la orden de compra",
    "description": """
Añade a la orden de compra los datos de importación que Odoo no trae de fábrica:
zarpe/arribo, BL/AWB, agente de aduana, N° DIN y fecha de nacionalización.

Los costos de internación (flete, seguro, aranceles, gastos de agente) se
registran con **Costos en destino** (`stock_landed_costs`), no aquí: este módulo
solo aporta la trazabilidad documental y de fechas del embarque.
""",
    "author": "OrigenLab",
    "category": "Inventory/Purchase",
    "license": "LGPL-3",
    "depends": ["purchase", "stock_landed_costs"],
    "data": [
        "views/purchase_order_views.xml",
    ],
    "installable": True,
    "application": False,
}
