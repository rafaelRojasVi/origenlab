# Production dashboard smoke checklist (Phase 9E)

Read-only operator checklist after **refresh + deploy**. Does not send email, mutate Gmail, write SQLite/Postgres, or deploy.

## When to run

- After `refresh_render_dashboard_once.sh` (especially with `RUN_CATALOG_MIRROR=1` and `RUN_COMMERCIAL_DEAL_MIRROR=1`)
- After Render deploy of **apps/api** (`:8001`) and **apps/dashboard**
- Before handing the dashboard to operators for the day

## Prerequisites

- Deployed **operator API** base URL (production or staging), e.g. `https://api.origenlab.cl` or local `http://127.0.0.1:8001`
- Deployed **dashboard** URL loads (browser sanity: Today, Negocio, Catálogo open without console errors)
- No secrets in chat logs — smoke output must stay PASS/FAIL + counts only

## Automated smoke (recommended)

Two complementary checks:

| Smoke type | What it validates | When to use |
|------------|-------------------|-------------|
| **Origin API smoke** | `https://api.origenlab.cl` directly — Access + bearer token on FastAPI origin | After API deploy, mirror refresh, auth secret rotation |
| **Dashboard Worker proxy smoke** | `https://dashboard.origenlab.cl/api/*` — the **actual browser production path** (Access → Worker → API) | After Worker deploy, CORS/auth hardening, dashboard cutover |

From `apps/email-pipeline` for origin API smoke. Use **curl** (or equivalent) for Worker proxy smoke — see below.

### A. Origin API smoke (direct `api.origenlab.cl`)

Validates FastAPI origin auth and mirror data. Does **not** prove the dashboard browser path.

#### Local API (no Cloudflare Access)

Start `apps/api` on port 8001, then:

```bash
cd apps/email-pipeline
uv run python scripts/qa/smoke_dashboard_api_readiness.py \
  --api-base http://127.0.0.1:8001
```

No service token or API auth headers are sent unless you set the env vars below.

#### Production origin API (Cloudflare Access + bearer token)

`https://api.origenlab.cl` may use **Cloudflare Access** (edge) and **`ORIGENLAB_API_AUTH_TOKEN`** (origin) when `ORIGENLAB_ENV=production`. CORS is not auth.

Configure **both** when applicable:

```bash
export CF_ACCESS_CLIENT_ID='your-client-id'
export CF_ACCESS_CLIENT_SECRET='your-client-secret'
export ORIGENLAB_API_AUTH_TOKEN='your-api-token'   # from secret store only
uv run python scripts/qa/smoke_dashboard_api_readiness.py \
  --api-base https://api.origenlab.cl
```

When `ORIGENLAB_API_AUTH_TOKEN` is set, the smoke script sends `X-OriginLab-API-Key` on every GET (never printed). Cloudflare Access headers are unchanged.

Without tokens, smoke may return **HTTP 403** (Access) or **401** (API bearer). See [`apps/api/docs/PRODUCTION_AUTH.md`](../../apps/api/docs/PRODUCTION_AUTH.md).

**Two layers — do not conflate:**

| Symptom | Layer | Meaning |
|---------|-------|---------|
| **302 / 403** (HTML or Access page) | **Cloudflare Access** (edge) | Missing/invalid Access session or service token |
| **401** JSON (`error.code`: `unauthorized`) | **API origin** (`ORIGENLAB_API_AUTH_TOKEN`) | Request reached the API but no bearer/API-key header |

The production **dashboard browser** calls same-origin `/api/*` via the Worker proxy; it does **not** send the API token. UI 401s with a passing **origin** CLI smoke (tokens in env) mean Worker secrets or route allowlist — see [`apps/dashboard/docs/PRODUCTION_API_AUTH.md`](../../apps/dashboard/docs/PRODUCTION_API_AUTH.md) and [`apps/dashboard-proxy/README.md`](../../apps/dashboard-proxy/README.md).

### B. Dashboard Worker proxy smoke (canonical browser path)

**Base:** `https://dashboard.origenlab.cl/api`

This is the production path operators use in the browser after Cloudflare Access login. Run after Worker deploy or CORS/auth changes.

**Headers (curl example — use service token values from secret store, never commit):**

```http
CF-Access-Client-Id: <service-token-client-id>
CF-Access-Client-Secret: <service-token-client-secret>
Origin: https://dashboard.origenlab.cl
Accept: application/json
```

Map proxy-specific env names if present: `CF_ACCESS_CLIENT_ID=${CF_ACCESS_CLIENT_ID_PROXY:-$CF_ACCESS_CLIENT_ID}` (same for secret).

**Routes checked (GET only):**

| Route |
|-------|
| `/health` |
| `/operator/status?max_staleness_days=14` |
| `/operator/automation-status?cooldown-seconds=60` |
| `/mirror/catalog/products?limit=100` |
| `/mirror/leads/summary` |
| `/mirror/leads/prospects?limit=20&include_blocked=false` |
| `/mirror/audits/gmail-interactions` |
| `/mirror/commercial/deals?limit=20` |

**Expected per route (2026-07-06 production smoke):**

| Check | Expected |
|-------|----------|
| HTTP status | **200** |
| `content-type` | `application/json` |
| `access-control-allow-origin` | `https://dashboard.origenlab.cl` (never `*`) |
| `x-originlab-proxy` | `dashboard-proxy` |
| `x-originlab-upstream-status` | `200` |
| Body | Valid JSON (do not log bodies in operator chat) |

**Example (one route):**

```bash
curl -sS -D - -o /dev/null \
  -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
  -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
  -H "Origin: https://dashboard.origenlab.cl" \
  -H "Accept: application/json" \
  "https://dashboard.origenlab.cl/api/health"
```

**Failure signals:**

| Symptom | Likely cause |
|---------|----------------|
| **502** + `upstream_redirect_blocked` | Worker missing/invalid `CF_ACCESS_*` — upstream Access redirect not converted for browser |
| **401** JSON | Worker missing `ORIGENLAB_API_AUTH_TOKEN` |
| **403** `path_not_allowed` | Route not on Worker allowlist |
| CORS error in browser but curl 200 | Upstream cookie/CORS leak — verify Worker #345–#349 deployed |
| **200** origin smoke, browser fails | Run this Worker proxy smoke; check dashboard `VITE_ORIGENLAB_API_BASE_URL=https://dashboard.origenlab.cl/api` |

Do **not** print `ORIGENLAB_API_AUTH_TOKEN`, `CF_ACCESS_*` values, or `CF_Authorization` cookies in reports.

Alternate env names for **origin** smoke: `ORIGENLAB_CF_ACCESS_CLIENT_ID` / `ORIGENLAB_CF_ACCESS_CLIENT_SECRET` (same values as `CF_ACCESS_*`).

Optional CLI overrides (prefer env for production):

```bash
uv run python scripts/qa/smoke_dashboard_api_readiness.py \
  --api-base https://api.origenlab.cl \
  --cf-access-client-id "$CF_ACCESS_CLIENT_ID" \
  --cf-access-client-secret "$CF_ACCESS_CLIENT_SECRET"
```

If smoke returns **HTTP 403** without Cloudflare tokens, or **401** without `ORIGENLAB_API_AUTH_TOKEN`, the script prints guidance — configure the missing layer or run against local API.

### Machine-readable report (no secrets in output)

```bash
uv run python scripts/qa/smoke_dashboard_api_readiness.py \
  --api-base https://api.origenlab.cl \
  --json-out /tmp/dashboard_smoke_report.json
```

Exit code **0** = PASS, **1** = FAIL.

### What the script checks (GET only)

| Route | Expectation |
|-------|-------------|
| `GET /health` | HTTP 200, `ok: true`, service name present |
| `GET /operator/status` | HTTP 200, `verdict` present, **no** `sqlite_path` in JSON |
| `GET /mirror/commercial/deals` | HTTP 200, `read_only`, `data_source: postgres_mirror`, `total >= 1` |
| `GET /mirror/catalog/products` | HTTP 200, `read_only`, `data_source: postgres_mirror`, `total >= 9` |
| `GET /mirror/catalog/products/serva-blueslick-250ml` | `commercial_history` includes EUR **117.00** and CLP **695000** |
| `GET /mirror/catalog/products/serva-temed-25ml` | `commercial_history` includes EUR **31.00** and CLP **545000** |
| `GET /cases/warm` | HTTP 200 if reachable; `meta.read_only`, known `data_source` |
| `GET /operator/procurement/status` | HTTP 200 if reachable; `meta.read_only`, `meta.data_source == institution_prospect_read_model` |

### Safety scan (all JSON bodies)

Must **not** expose populated values for keys such as: `gmail_url`, `source_file`, `source_path`, `body`, `email_body`, `full_text`, `transfer_id`, `operation_id`.

Must **not** contain forbidden substrings (banking, Gmail URLs, RUT markers, etc.) or known prose artifacts (`montoes`, `Monto112`, `decotizar`, `enelectroforesis`, `oportunida de s`, …).

The script does **not** print response bodies, DB paths, Postgres URLs, or credentials.

## Manual UI checklist (5 minutes)

1. **Today** — Operator status card shows a verdict (not blank); warm-case table loads or shows an explicit empty state (not a generic error).
2. **Negocio** — At least one commercial deal row; CEAF×SERVA lines visible on highlight cards when applicable; Spanish status labels (no raw `margin_ok` in UI).
3. **Catálogo** — Product list ≥ 9 rows; open **BlueSlick 250 ml** and **TEMED 25 ml** drawers — commercial history shows CLP/EUR reference amounts (no email bodies, no bank fields).
4. **Equipamiento** — Table or “Fuente de licitaciones no disponible” (distinguish from “zero opportunities”).
5. **Sistema** — Copy mentions canonical Gmail scope vs full archive; no local file paths in the page.

## If smoke fails

| Symptom | Likely fix |
|---------|------------|
| Catalog `total < 9` | Re-run refresh with `RUN_CATALOG_MIRROR=1`; verify Postgres catalog mirror |
| SERVA history amounts missing | Re-run `build_catalog_sqlite.py` + catalog sync; check seed rows |
| Commercial `total < 1` | Re-run with `RUN_COMMERCIAL_DEAL_MIRROR=1` |
| Forbidden key / prose artifact | Fix mirror redaction or catalog builder; do not patch dashboard to hide leaks |
| `sqlite_path` on `/operator/status` | API config leak — fix operator status serializer before go-live |
| Equipment `meta.source_path` set | API must not expose CSV paths to browser |
| HTTP 403 on all routes | Set `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`, or use local `:8001` |
| HTTP 401 on private routes (CLI smoke) | Set `ORIGENLAB_API_AUTH_TOKEN` in smoke env (see API auth doc) |
| HTTP 401 in dashboard UI but **origin** CLI smoke PASS | Run **Worker proxy smoke** (section B); check Worker `ORIGENLAB_API_AUTH_TOKEN` (401 JSON) or `CF_ACCESS_*` (502 redirect blocked); do **not** add `VITE_*` auth token |

## Related

- Refresh runbook: [`apps/email-pipeline/docs/REFRESH_RENDER_DASHBOARD_ONCE.md`](../../apps/email-pipeline/docs/REFRESH_RENDER_DASHBOARD_ONCE.md)
- Dashboard production API auth: [`apps/dashboard/docs/PRODUCTION_API_AUTH.md`](../../apps/dashboard/docs/PRODUCTION_API_AUTH.md)
- Dashboard read-only Worker proxy: [`apps/dashboard-proxy/README.md`](../../apps/dashboard-proxy/README.md)
- API mirror smoke (narrower): [`apps/api/scripts/mirror_parity_smoke.py`](../../apps/api/scripts/mirror_parity_smoke.py)

## Tests

```bash
cd apps/email-pipeline
uv run pytest tests/test_smoke_dashboard_api_readiness.py -q
```
