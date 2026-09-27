# OrigenLab — Dashboard (React)

The operator CRM. One shell, one navigation, one Google Workspace sign-in, over the V2
durable core. It talks only to **`apps/api`**, and only through GET:

| Route | Use |
|-------|-----|
| `GET /auth/session` · `GET /auth/google/login` · `POST /auth/logout` | Sign-in state, Google Workspace login, sign-out |
| `GET /v2/workspace/*` | Every CRM section (Resumen, Oportunidades, Organizaciones, Personas, Proveedores, Archivo Drive, Marketing, Revisión) |
| `GET /v2/contacts` · `GET /v2/organizations` | Personas and Organizaciones search |

The browser does not open a database, CSV files, or `apps/email-pipeline` modules. In
production every request passes the `apps/dashboard-proxy` method+path allowlist. Canonical
V2 architecture: [`../../docs/README.md`](../../docs/README.md); what is built and deployed:
[`../../docs/STATUS.md`](../../docs/STATUS.md).

## What the operator sees

```
App.tsx → pages/DashboardApp.tsx → components/auth/AuthGate.tsx (one Google Workspace sign-in)
  → crm/CrmApp.tsx (the one shell and navigation; crm/shellRoute.ts reads the hash)
  → crm/pages/: Resumen, Oportunidades, Organizaciones, Personas, Proveedores,
    Archivo Drive, Marketing, Revisión — read-only over `/v2/workspace/*`
```

- Routes are `#/crm/<section>[/<case uuid>]`. `#/`, an empty hash and unknown hashes open
  `#/crm/resumen`.
- **Old bookmarks keep working.** Hashes of the earlier operator panel (`#/cotizaciones`,
  `#/ventas`, `#/contactos`, `#/archivo`, …) are redirected to the CRM section that covers the
  same ground, or to Resumen when none does (`LEGACY_REDIRECTS` in `crm/shellRoute.ts`). A
  bookmark that selected a case — `#/ventas?opportunity=<uuid>`, `#/casos?id=<uuid>` — opens
  that case in Oportunidades when it exists in the CRM, and the list when it does not. V1
  `sales_…` ids name no V2 case and open the list.
- **Roles.** The API decides what each operator sees. Only `sales` and `admin` read contact
  addresses as recorded; for any other role (`viewer`) the API masks them; the dashboard says so and searches by name instead (`crm/redaction.ts`).
- **Honest labels.** An imported case still in `quoting` shows «Cotización enviada ·
  histórico»; next steps are marked *sugerencia*; Drive links carry «registro local» because
  they come from an archive ledger, not a live Drive read.

The earlier operator panel (Today, Ventas, Cotizaciones, Catálogo, Licitaciones, the V2
consoles) was removed on 2026-09-26; git history holds it. The V1 API routes it read are
untouched.

## Run locally

**Terminal 1 — API** (`apps/api`), against a loopback V2 database. Sign-in needs either
Google Workspace login or the development header login — see
[`apps/api/docs/PRODUCTION_AUTH.md`](../api/docs/PRODUCTION_AUTH.md#google-workspace-login-dashboard-v2-boundary):

```bash
cd apps/api
uv sync
export ORIGENLAB_V2_DATABASE_URL="postgresql://…@127.0.0.1:…/origenlab_clean"  # loopback only
export ORIGENLAB_DEV_LOGIN_ENABLED=true   # local only; refused when ORIGENLAB_ENV=production
uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001 --reload
```

**Terminal 2 — Dashboard**:

```bash
cd apps/dashboard
npm install
ORIGENLAB_DEV_OPERATOR_EMAIL="dev-operator@origenlab.cl" npm run dev -- --host 127.0.0.1
```

Open [http://localhost:5173](http://localhost:5173). `ORIGENLAB_DEV_API_TARGET` moves the
proxy target when the API runs on another port.

### Environment

| Mode | `VITE_ORIGENLAB_API_BASE_URL` | Behavior |
|------|-------------------------------|----------|
| **`npm run dev`** | **Leave unset** | Same-origin requests; Vite proxies `/auth`, `/v2` (and the V1 paths) to the API |
| **`npm run build`** / production | **Required** | The same-origin Worker, e.g. `https://dashboard.origenlab.cl/api` ([`apps/dashboard-proxy`](../dashboard-proxy/README.md)) |

Production builds **throw at runtime** if `VITE_ORIGENLAB_API_BASE_URL` is missing (no
silent localhost fallback). No token goes into a `VITE_*` variable — they are embedded in the
bundle. See [docs/PRODUCTION_API_AUTH.md](docs/PRODUCTION_API_AUTH.md). Restart `npm run dev`
after changing `.env`.

### Development operator identity

`ORIGENLAB_DEV_OPERATOR_EMAIL` is read by `vite.config.ts` in the Node process and injected as
`X-OriginLab-Operator-Email` on proxied requests (`vite.devOperatorProxy.ts`, unit tested).
It is off when unset, only active for `vite dev`, and never reaches client code because it is
not `VITE_`-prefixed. The API honours the header only with `ORIGENLAB_DEV_LOGIN_ENABLED=true`
against a loopback database. `VITE_ORIGENLAB_API_BASE_URL` must stay unset while relying on
it — an absolute API URL bypasses the Vite proxy and the injection with it. Never commit a real
address as a default.

## Write scope and safety boundaries

- **The dashboard writes nothing.** The only non-GET request is `POST /auth/logout`, which
  clears the session cookie. Actions that would change the CRM are shown disabled until their
  command is approved and routed through the proxy.
- The dashboard does **not** send email, create Gmail drafts, or touch Drive.
- **No raw email bodies** or filesystem paths in the UI.
- The browser never sets `X-OriginLab-Operator-Email`; the proxy (production) or the Vite dev
  proxy (local) does.

## Tests and build

```bash
cd apps/dashboard
npm run validate  # tests + build — run before opening or merging dashboard PRs
npm test
npm run build
```

Safety tests enforce: `App.tsx` → `DashboardApp` → `AuthGate` → `CrmApp` only; production
host allowlist; no DB/pipeline imports; the only mutating request is the `/auth/logout` POST
to exactly that path, and no source sets the operator identity header
(`noWritePolicy.test.ts`, `dashboard0Safety.test.ts`, `crm/crm.test.tsx`). CI:
[`.github/workflows/dashboard.yml`](../../.github/workflows/dashboard.yml).

The `smoke*` and `freeze:*` scripts exercise the **V1 API** (`/health`, `/operator/*`,
`/cases/warm`, `/contacts/{email}`, `/mirror/*`) on :8001, not the CRM; see
[docs/V1_FREEZE_OPERATOR_HANDOFF.md](docs/V1_FREEZE_OPERATOR_HANDOFF.md) and
[docs/BACKEND_MATRIX_VALIDATION.md](docs/BACKEND_MATRIX_VALIDATION.md).
