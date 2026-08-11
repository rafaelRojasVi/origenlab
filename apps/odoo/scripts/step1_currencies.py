from odoo import fields  # noqa: E402 (odoo shell namespace)

"""Paso 1 (1.2) — activar divisas de proveedor y fijar tipo de cambio contra CLP.

Idempotente: re-ejecutarlo solo actualiza la tasa del día.
Tasas: mindicador.cl (dólar/euro observado, Banco Central de Chile).
Se ejecuta con:  docker compose run --rm -T odoo odoo shell -d origenlab --no-http
"""

RATES = {
    # divisa: CLP por 1 unidad de la divisa, fecha
    "USD": (913.86, "2026-08-07"),
    "EUR": (1053.08, "2026-08-07"),
}

Currency = env["res.currency"].with_context(active_test=False)
Rate = env["res.currency.rate"]
company = env.company

print("COMPANY_CURRENCY:", company.currency_id.name)

for name, (clp_per_unit, date) in RATES.items():
    currency = Currency.search([("name", "=", name)], limit=1)
    if not currency:
        print(f"MISSING_CURRENCY: {name}")
        continue
    if not currency.active:
        currency.active = True
        print(f"ACTIVATED: {name}")
    else:
        print(f"ALREADY_ACTIVE: {name}")

    existing = Rate.search(
        [
            ("currency_id", "=", currency.id),
            ("name", "=", date),
            ("company_id", "=", company.id),
        ],
        limit=1,
    )
    # Ojo con la dirección: en Odoo 19 `rate` y `company_rate` son unidades de la
    # divisa por 1 CLP. Lo que queremos fijar es "cuántos CLP vale 1 EUR/USD",
    # que es `inverse_company_rate`. Escribir `company_rate` invierte la tasa y
    # deja márgenes silenciosamente erróneos.
    values = {"inverse_company_rate": clp_per_unit}
    if existing:
        existing.write(values)
        print(f"RATE_UPDATED: 1 {name} = {clp_per_unit} CLP ({date})")
    else:
        Rate.create(
            {
                "currency_id": currency.id,
                "name": date,
                "company_id": company.id,
                **values,
            }
        )
        print(f"RATE_CREATED: 1 {name} = {clp_per_unit} CLP ({date})")

env.cr.commit()

# Verificación: convertir 1000 de cada divisa a CLP con la tasa recién fijada.
for name in RATES:
    currency = Currency.search([("name", "=", name)], limit=1)
    converted = currency._convert(1000, company.currency_id, company, fields.Date.today())
    print(f"CHECK: 1000 {name} -> {converted:,.0f} CLP")

print("ACTIVE_NOW:", env["res.currency"].search([("active", "=", True)]).mapped("name"))
