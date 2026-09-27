# OrigenLab — Dashboard (React)

The operator CRM. One shell, one navigation, one Google Workspace sign-in, over the V2
durable core. It talks only to **`apps/api`**, and — apart from sign-out and the Marketing commands — only through GET:

| Route | Use |
|-------|-----|
| `GET /auth/session` · `GET /auth/google/login` · `POST /auth/logout` | Sign-in state, Google Workspace login, sign-out |
| `GET /v2/workspace/*` | Every CRM section (Resumen, Oportunidades, Organizaciones, Personas, Proveedores, Archivo Drive, Marketing, Revisión) |
| `GET /v2/contacts` · `GET /v2/organizations` | Personas and Organizaciones search |
| `POST /v2/commands/create-campaign-draft` · `POST /v2/commands/save-campaign-draft` | Marketing: create and save a campaign **draft**; mounted only with `ORIGENLAB_V2_CAMPAIGN_DRAFTS_ENABLED`, refused by the production proxy |
| `POST /v2/commands/freeze-campaign-audience` | Marketing: freeze a draft's audience into an immutable recipient snapshot, sent only from the final confirmation screen; mounted only with `ORIGENLAB_V2_AUDIENCE_FREEZE_ENABLED`, refused by the production proxy. Sends nothing |
| `POST /v2/commands/set-campaign-planning` | Marketing: set, change or clear an unsent campaign's internal planned day («Planificación interna · no programa el envío»); sales/admin; mounted only with `ORIGENLAB_V2_CAMPAIGN_PLANNING_ENABLED`. Schedules and sends nothing |
| `POST /v2/commands/block-campaign` · `POST /v2/commands/unblock-campaign` | Marketing: place or lift a campaign safety block (one campaign or every campaign), each with a mandatory reason and its own confirmation; **admin only**; mounted only with `ORIGENLAB_V2_CAMPAIGN_BLOCKS_ENABLED`. A block only refuses; lifting starts nothing |

Marketing «Bajas» is read-only (`GET /v2/workspace/marketing/suppressions`): which addresses answered «BAJA» (or the legacy «REMOVER»), since when, which «BAJA» replies from an unproven sender are held for review — each blocking its exact address — and which frozen recipients that refuses today. It states that Gmail replies are not synchronized automatically; the dashboard has no unsubscribe, review, re-subscribe, Gmail or Send action.

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

### Marketing

- **Campañas.** Each card shows a thumbnail of the stored HTML, or «Contenido no importado» when
  the campaign has none. Opening one shows subject, preheader, HTML and a desktop/mobile
  preview. Previews are sanitized (`crm/marketing/emailPreview.ts`), rendered under a CSP that
  allows images only from `https://origenlab.cl`, inside `<iframe sandbox="">`: no script runs
  and no remote image or tracking pixel is requested.
- **Drafts say where they live.** The editor always shows one of: *sin guardar* (only in this
  tab), *guardado* (table, database and version), *cambios sin guardar*, or *sólo lectura*.
  Save is disabled when the API reports drafts are not enabled. Templates
  (`crm/marketing/emailTemplates.ts`) use only the website catalogue's verified images.
- **Audiencias por equipo.** Filter by family, brand, model, institution, basis and whether the
  interest is recorded in the CRM or only in evidence. Every interest shows its basis, source
  and date; no evidence reads «Sin información». Eligibility is separate: suppliers,
  suppressed and invalid destinations cannot be selected, and the recipient list is
  deduplicated by address (`crm/marketing/audienceSelection.ts`). The selection is not saved.
- **Congelar audiencia** (`crm/marketing/AudienceFreeze.tsx`), from a saved, unchanged draft:
  criteria → review → final confirmation → freeze. The API computes everything
  (`/freeze-preview`); the screen shows coverage per canonical line («Sin información» where
  there is no evidence), every exclusion reason, and the ambiguous identities an operator must
  decide with a note. The confirmation shows content and policy versions and fingerprints and
  needs an explicit acknowledgement. **There is no Send button**, and a BAJA blocker is shown on
  every freeze and snapshot screen: unsubscribe processing does not exist. A frozen campaign
  opens read-only; «Nueva versión» starts a new draft to freeze separately.
- **Resumen y Calendario** (`crm/marketing/MarketingOverview.tsx`, `CampaignCalendar.tsx`,
  `calendar.ts`). On top: «Último envío: hace N días», «Próxima campaña: en N días» and counts of
  sent, draft, frozen and planned campaigns — no opens, clicks or replies, none are imported. The
  calendar puts every event on a recorded date, displayed in America/Santiago: each real send
  batch (a campaign sent over two days shows two), the freeze, the last draft save, and the
  internal planned day. Month grid on desktop, agenda list on phones; status and line filters;
  imported V1 campaigns are marked.
- **Historial de campaña** (`crm/marketing/CampaignDetail.tsx`). The frozen content the campaign
  was sent with — never the editable draft — in desktop, mobile and read-only raw HTML, under the
  same sandbox and sanitizer; «HTML enviado no archivado» when the record has none. The planning
  panel («Planificación interna · no programa el envío») is editable by sales/admin only, where
  `authoring.planning_enabled` says the API mounts the command.
- **Bloqueos de seguridad** (`crm/marketing/CampaignHolds.tsx`, `GET …/marketing/campaign-blocks`).
  A banner on the Marketing section shows every hold that is not about one campaign — a block on
  every campaign, and V1 holds such as the September wave-2 incident hold — and each campaign's
  history has a «Bloqueo de seguridad» panel; cards of a held campaign read «Bloqueada». A viewer
  sees status only (no reason, no operator); sales also sees why; only an admin, where the API
  reports `may_decide`, blocks or lifts, with a mandatory reason and a confirmation that says the
  decision sends and rewrites nothing and never expires.

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

The V1 Gmail → mirror refresh chain still runs on the API side even though this shell no
longer displays the mirror: email-pipeline RUNBOOK anchor
[`m-eprun-dashboard-gmail-to-react`](../email-pipeline/docs/RUNBOOK.md#m-eprun-dashboard-gmail-to-react)
— ingest, then `sync_dashboard_postgres_mirror.py`, then check freshness with
`GET /mirror/meta/dashboard-sync` and `GET /mirror/classification/summary` on :8001. A
«Failed to fetch» in `npm run dev` almost always means `VITE_ORIGENLAB_API_BASE_URL` is set
and bypasses the Vite proxy; unset it and restart.
