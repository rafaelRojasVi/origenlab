# OrigenLab — Odoo 19 local evaluation

Stack de evaluación local, dentro del monorepo en `apps/odoo/`.

> **Este repositorio es público.** Los secretos viven solo en `.env` y en
> `config/odoo.conf`, ambos en `.gitignore`. No escribas contraseñas en el
> README, en `config/odoo.conf.example` ni en ningún archivo versionado.
> El stack escucha solo en `127.0.0.1`: nada queda expuesto a la red local.

## Puesta en marcha

```bash
cd apps/odoo
cp .env.example .env          # rellena DB_PASSWORD y ODOO_MASTER_PASSWORD
./scripts/bootstrap_config.sh # genera config/odoo.conf desde la plantilla
docker compose up -d
```

## Acceso

| Item | Valor |
|---|---|
| Odoo | http://localhost:8069 |
| Master password (gestor de BD) | `ODOO_MASTER_PASSWORD` en `.env` |
| Postgres (host) | `127.0.0.1:5434`, usuario `odoo`, contraseña en `.env` |

## Proceso comercial

Las notas dictadas paso a paso y lo implementado para cada una están en
[`docs/PROCESS_NOTES.md`](docs/PROCESS_NOTES.md). Ese archivo manda sobre la
configuración de Odoo, no al revés.

## First run — create the database

Open http://localhost:8069. On the wizard:

| Field | Use |
|---|---|
| Master Password | the value above |
| Database Name | `origenlab` |
| Email | `contacto@origenlab.cl` |
| Password | your choice (this is your Odoo login) |
| Language | **Español (CL)** |
| Country | **Chile** ← *important: this auto-loads the `l10n_cl` chart of accounts and IVA taxes* |
| Demo data | **check it** for the trial — it gives you sample products/customers to click through, but you must recreate the DB before real data |

## Apps installed

`crm`, `sale_management`, `account`, `l10n_cl`, `purchase`, `stock`,
`stock_landed_costs`, `origenlab_import`.

## Costos de importación (aduana, flete, seguro)

Odoo's feature for this is **Costos en destino** (`stock_landed_costs`): you
receive the equipment, then add customs/freight/insurance as separate cost
documents that get **distributed across the received units**. The result is a
real landed unit cost, so your margin reflects what the equipment actually cost
you in Valdivia, not the EUR invoice from Ortoalresa.

**Prerequisite already applied:** the `Goods` product category was switched from
*precio estándar* to **FIFO + valoración en tiempo real**. Landed costs cannot be
applied to standard-cost products — this was the blocker. FIFO (rather than AVCO)
suits you because each unit you import for a specific deal keeps its own cost.

**Cost products already created** (Inventario → Operaciones → Costos en destino):

| Producto | Reparto |
|---|---|
| Flete internacional | por valor |
| Seguro de carga | por valor |
| Arancel aduanero | por valor |
| IVA de internación (crédito) | por valor |
| Honorarios agente de aduana | equitativo |
| Gastos portuarios y almacenaje | equitativo |
| Transporte nacional a destino | por cantidad |

Change the split method per document if a given shipment splits differently.

## Fechas de embarque y aduana

Odoo has nothing native for this, so `addons/origenlab_import/` adds an
**"Importación"** tab to every purchase order:

- N° BL / AWB, ETD (zarpe), ETA (arribo), días en tránsito (computed)
- Agente de aduana, N° DIN, fecha de nacionalización, notas

BL and DIN are searchable from the RFQ list. ETA earlier than ETD is rejected.

To change the fields, edit `addons/origenlab_import/` then:

```bash
cd ~/odoo-trial && docker compose stop odoo
docker compose run --rm odoo odoo -d origenlab -u origenlab_import --stop-after-init
docker compose start odoo
```

## Pagos del cliente en varias fechas

Three native mechanisms — use them together:

1. **Anticipo (down payment)** — on a confirmed sale order, *Crear factura* →
   *Anticipo (porcentaje)*. Invoices e.g. 40% now; the balance stays on the order
   for later. This is the right tool for "abono para iniciar la importación".
2. **Términos de pago** — a single invoice with several due dates. Created for
   you: **"50% anticipo / 50% a 30 días"** (Contabilidad → Configuración →
   Términos de pago). Add more to match how you really sell.
3. **Pagos parciales** — register a payment *smaller* than the invoice total;
   Odoo marks it *parcialmente pagado* and tracks the saldo. Repeat as the client
   pays. Each payment keeps its own date and reference.

Use (1) for the deposit, (3) for irregular real-world instalments, (2) only when
the schedule is agreed up front.

## Known limits (worth knowing before you commit)

- **No single "deal" screen.** The client sale order, the supplier PO, the landed
  costs and the payment schedule are four linked records, navigated via smart
  buttons. Odoo will not show them on one page without a custom module.
- **Landed costs need stocked goods.** They attach to a *receipt*. If you ever
  drop-ship straight from Ortoalresa to the client, there's no receipt to attach
  to and the import costs won't fold into that unit's cost.
- **Currency.** Enable *Ajustes → Multi-divisa* and set an EUR rate before
  testing, or the supplier cost and the client price won't reconcile.

## What to actually test (your real cycle, not a feature tour)

This is the evaluation that matters — it maps to `docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md`:

1. Create a customer with a **RUT** and región. Does the RUT field validate?
2. Create product `Digicen 22 R` with a **EUR cost** and a **CLP sale price**. Check multi-currency handling — enable *Ajustes → Multi-divisa* first.
3. From an opportunity, generate a **cotización** with 2 line items → look at the margin column (cost vs price).
4. From that quote, raise a **solicitud de presupuesto** to a supplier, enter the EUR cost + lead time, confirm, and check the margin updates on the sale order.
5. Print the quote PDF. Judge whether the template can carry OrigenLab branding (teal `#0f766e`, Plus Jakarta Sans) without a developer.
6. Check the §2.2 gates: can you mark delivery/payment/validity/IVA/warranty as *explicitly unconfirmed* rather than silently blank?

Step 6 is the one most likely to disappoint — Odoo will happily print a quote with empty terms. If so, that's a template customisation, not a blocker.

## Known caveat: DTE / SII e-invoicing

Certified Chilean electronic invoicing (`l10n_cl_edi`) is an **Odoo Enterprise** module. This Community trial gives you the chart of accounts and IVA taxes, but **not** legal DTE emission. Options if you adopt:

- Odoo Community for commercial truth + a cheap DTE provider (Haulmer ~CLP 10k/mes) for the fiscal document, or
- Odoo Standard/Enterprise with Chile localization pricing.

Decide this *after* the trial confirms the commercial cycle fits.

## Operating the stack

```bash
cd ~/odoo-trial
docker compose ps                 # status
docker compose logs -f odoo       # tail logs
docker compose stop               # stop, keep data
docker compose start              # resume
docker compose down               # remove containers, KEEP volumes
docker compose down -v            # DESTROY the database and filestore
```

Data lives in the named volumes `origenlab-odoo-trial_odoo-db-data` and `_odoo-web-data`.

## Notes

- `config/odoo.conf` is `0644` because the container's `odoo` user (uid 100) must read it and your uid is 1000. It holds the master password — fine for a local single-user machine, but do not copy this file to a shared host.
- `list_db = True` exposes the database manager. Acceptable on `127.0.0.1`; turn it off before any networked deployment.
- Odoo logs a warning that `/mnt/extra-addons` is "invalid" while `addons/` is empty. Harmless — it becomes valid once a custom module is dropped in.
- The `workers = 0` setting runs Odoo single-process (low RAM, fine for one operator). Raise it if you promote this to a real deployment.
