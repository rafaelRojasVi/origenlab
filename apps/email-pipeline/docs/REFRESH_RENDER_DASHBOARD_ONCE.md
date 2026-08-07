# Refresh Render dashboard once (manual operator runbook)

**Purpose:** After new Gmail mail arrives, update the **Render Postgres read-model mirror** so the static dashboard at `dashboard.origenlab.cl` shows fresh warm cases and equipment rows.

**Truth model:** Local SQLite (`~/data/origenlab-email/sqlite/emails.sqlite`) remains authoritative. Render Postgres is a **read-only mirror** for the dashboard API. The dashboard SPA is read-only (GET-only API).

---

## Safety scope

| Allowed | Forbidden in this workflow |
|---------|----------------------------|
| Read-only Gmail IMAP ingest into local SQLite (optional) | Gmail sends or label/folder mutation |
| Incremental `build_business_mart.py` (no `--rebuild`) | `build_business_mart.py --rebuild` |
| Incremental `build_commercial_intel_v1.py` (no `--rebuild`) | `build_commercial_intel_v1.py --rebuild` |
| `sync_dashboard_postgres_mirror.py` → **Render Postgres only** | Outreach-state writes, `mark_outreach_state`, sends |
| Read-only verify queries | SQLite upload/copy, SQLite backup, destructive SQL |
| Operator opens dashboard and clicks **Refresh** | Cron automation, dashboard/API writes |

---

## Prerequisites

1. **Local `.env`** (recommended): `cp apps/email-pipeline/.env.example apps/email-pipeline/.env` and set paths + Render URL once. Ops scripts load it automatically (gitignored; never commit).
2. **Local SQLite** at `~/data/origenlab-email/sqlite/emails.sqlite` (or `ORIGENLAB_SQLITE_PATH` in `.env`).
3. **Render external Postgres URL** as `ORIGENLAB_CLOUD_POSTGRES_URL` in `.env` (Render → Postgres → **External Database URL**, not Internal). Render often gives `postgresql://…`; the sync script normalizes to `postgresql+psycopg://…` for Alembic. Logs show `host/db` only.
4. **Gmail OAuth** (only if `RUN_GMAIL_INGEST=1`): set `ORIGENLAB_GMAIL_*` in `.env` per [`docs/ingest/WORKSPACE_GMAIL_IMAP.md`](ingest/WORKSPACE_GMAIL_IMAP.md).
5. Alembic head already applied on Render Postgres (first-time: see [Phase 1 cloud read path](PHASE1_CLOUD_READ_PATH.md)).

---

## One command (recommended)

From repo root (with `apps/email-pipeline/.env` filled in — no manual `export` needed):

```bash
# Mirror-only (no new Gmail fetch):
bash apps/email-pipeline/scripts/ops/refresh_render_dashboard_once.sh

# With Gmail ingest for new messages (read-only IMAP, last 14 days):
RUN_GMAIL_INGEST=1 GMAIL_SINCE_DAYS=14 \
  bash apps/email-pipeline/scripts/ops/refresh_render_dashboard_once.sh

# Dashboard mirror + commercial.deal mirror (read-only SQLite → Postgres commercial.deal):
RUN_COMMERCIAL_DEAL_MIRROR=1 \
  bash apps/email-pipeline/scripts/ops/refresh_render_dashboard_once.sh

# Full operator refresh (fast dashboard + deals + catalog + lead research + outbound sidecars):
DASHBOARD_FAST=1 RUN_GMAIL_INGEST=1 RUN_COMMERCIAL_DEAL_MIRROR=1 RUN_CATALOG_MIRROR=1 RUN_LEAD_RESEARCH_MIRROR=1 \
  bash apps/email-pipeline/scripts/ops/refresh_render_dashboard_once.sh

# Post-campaign refresh (recommended after outreach + bounce sync):
# See "Post-campaign dashboard refresh order" below.
DASHBOARD_FAST=1 RUN_GMAIL_INGEST=1 RUN_LEAD_RESEARCH_MIRROR=1 \
  bash apps/email-pipeline/scripts/ops/refresh_render_dashboard_once.sh
```

Example `apps/email-pipeline/.env` (secrets stay local):

```bash
ORIGENLAB_SQLITE_PATH=/home/you/data/origenlab-email/sqlite/emails.sqlite
ORIGENLAB_CLOUD_POSTGRES_URL=postgresql://admin_origenlab:YOUR_PASSWORD@dpg-d88eqqbtqb8s738acu90-a.oregon-postgres.render.com/origenlab_dashboard_prod
ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON=/home/you/secrets/google-oauth-desktop.json
ORIGENLAB_GMAIL_WORKSPACE_USER=contacto@origenlab.cl
ORIGENLAB_GMAIL_TOKEN_JSON=/home/you/data/origenlab-email/secrets/gmail_workspace_token.json
```

**Final step (human):** open https://dashboard.origenlab.cl and click **Refresh**.

---

## What the script does

| Step | Command / action | Mutates |
|------|------------------|---------|
| 1 | Preflight: SQLite file exists; cloud Postgres URL set | — |
| 2 | Optional Gmail ingest (`RUN_GMAIL_INGEST=1`) | SQLite `emails` only |
| 3 | `build_business_mart.py` (incremental, **no** `--rebuild`) | SQLite mart tables |
| 4 | `build_commercial_intel_v1.py` (incremental, **no** `--rebuild`) | SQLite commercial_* tables |
| 5 | `sync_dashboard_mirror_to_cloud.sh` | Render Postgres mirror |
| 6 | `verify_dashboard_postgres_mirror.py --assert-render-dashboard` | — (read-only) |
| 7 (opt-in) | `RUN_COMMERCIAL_DEAL_MIRROR=1`: commercial deal dry-run → sync → `verify_commercial_deals_postgres_mirror.py --scan-jsonb` | Postgres `commercial.deal` only |
| 8 (opt-in) | `RUN_CATALOG_MIRROR=1`: `build_catalog_sqlite.py` → catalog sync dry-run → sync → `verify_catalog_postgres_mirror.py --scan-text` | SQLite `catalog_*` + Postgres `catalog.*` |
| 9 (opt-in) | `RUN_LEAD_RESEARCH_MIRROR=1`: build lead research SQLite → sync → `verify_lead_research_postgres_mirror.py --scan-text` | SQLite `lead_research_*` + Postgres `lead_intel.*` |
| 10 (default on) | `RUN_OUTBOUND_SIDECAR_MIRROR=1`: `sqlite_outbound_sidecars_to_postgres.py --replace` → `verify_outbound_sidecar_postgres_mirror.py` | Postgres `outbound.*` only |

**`RUN_OUTBOUND_SIDECAR_MIRROR` defaults to `1`.** Set `RUN_OUTBOUND_SIDECAR_MIRROR=0` only when skipping outbound mirror (not recommended after campaigns — `DASHBOARD_FAST=1` uses `--only canonical` and does **not** refresh suppression counts without this step).

Gmail ingest flags (when enabled):

```bash
uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py \
  --folder INBOX \
  --skip-duplicate-message-id \
  --since-days 14

uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py \
  --folder "[Gmail]/Enviados" \
  --skip-duplicate-message-id \
  --since-days 14
```

- **`--skip-duplicate-message-id`** — skip rows already in SQLite (safe re-run).
- **`--since-days`** — bounds IMAP search (default 14). Increase if mail is older.
- **No `--replace-source`** — never deletes existing folder rows.
- Sent folder name is locale-dependent; discover with `--list-folders` if Enviados fails.

**Not run:** `refresh_outbound_safety_memory.py`, `mark_outreach_state`, any send script, mart/commercial `--rebuild`, commercial ledger promotion/apply, Render deploy.

### Commercial deal mirror (opt-in, `RUN_COMMERCIAL_DEAL_MIRROR=1`)

Runs **only after** step 6 (dashboard mirror verify) succeeds. Does **not** use `GET /mirror/commercial/purchase-events` or dashboard purchase-event tables.

| Sub-step | Command | Notes |
|----------|---------|--------|
| Dry-run | `sync_commercial_deals_postgres_mirror.py --dry-run` | No Postgres writes |
| Sync | `sync_commercial_deals_postgres_mirror.py` | Read-only SQLite; writes `commercial.deal` |
| Verify | `verify_commercial_deals_postgres_mirror.py --scan-jsonb` | JSON: `/tmp/commercial_deals_mirror_verify.json` |

If commercial verify fails, the script exits non-zero with:

`Commercial deal mirror verify failed. Dashboard normal mirror may still be fresh, but commercial deal data should not be trusted.`

Warm cases / equipment in the dashboard may still be current; do not trust the **Commercial deals** table until verify passes.

### Catalog mirror (opt-in, `RUN_CATALOG_MIRROR=1`)

Runs **only after** step 6 (dashboard mirror verify) and **after** the commercial deal block when that flag is also set. Does **not** run during default refresh.

| Sub-step | Command | Notes |
|----------|---------|--------|
| Alembic | `alembic upgrade head` | Ensures `catalog.*` tables exist on Render Postgres |
| Build | `build_catalog_sqlite.py` | Writes/updates SQLite `catalog_*` from seed (opt-in only) |
| Dry-run | `sync_catalog_postgres_mirror.py --dry-run` | No Postgres writes |
| Sync | `sync_catalog_postgres_mirror.py` | Read-only SQLite; writes `catalog.*` |
| Verify | `verify_catalog_postgres_mirror.py --scan-text` | JSON: `/tmp/catalog_postgres_mirror_verify.json` |

If catalog verify fails, the script exits non-zero with:

`Catalog mirror verify failed. Dashboard mirror may still be fresh, but Catálogo / catalog API data should not be trusted.`

Summary prints Postgres counts when verify passes: `products`, `supplier_offers`, `price_snapshots`, `commercial_history`.

### Outbound sidecar mirror (default on, `RUN_OUTBOUND_SIDECAR_MIRROR=1`)

Runs **after** dashboard mirror verify and **after** commercial / catalog / lead research blocks when those flags are set. Required when `DASHBOARD_FAST=1` (`--only canonical` skips outbound sidecars in the main sync).

| Sub-step | Command | Notes |
|----------|---------|--------|
| Sync | `sqlite_outbound_sidecars_to_postgres.py --replace` | Reloads `outbound.contact_email_suppression`, domain suppression, outreach state |
| Verify | `verify_outbound_sidecar_postgres_mirror.py` | JSON: `/tmp/outbound_sidecar_mirror_verify.json` |

When `RUN_LEAD_RESEARCH_MIRROR=1`, verify also checks lead_intel **blocked** and **net_new_safe** segment counts.

If outbound sidecar verify fails, the script exits non-zero with:

`Outbound sidecar mirror verify failed. Dashboard may show PRECAUCIÓN / stale suppressions.`

Warm cases / equipment may still be current; suppression and net-new KPIs on Today are **not trustworthy** until verify passes.

---

## Post-campaign dashboard refresh order

After a campaign send and bounce sync, run **before** opening the operator dashboard:

| Step | Action | Mutates |
|------|--------|---------|
| 1 | Gmail Sent + INBOX ingest (`RUN_GMAIL_INGEST=1` or manual IMAP) | SQLite |
| 2 | Bounce suppression sync (`sync_outreach_batch_from_ingested_bounces.py --apply`) | SQLite suppressions |
| 3 | Contacted universe audit (`audit_contacted_universe.py`) | CSV exports |
| 4 | Lead research rebuild (`RUN_LEAD_RESEARCH_MIRROR=1` or `build_lead_research_sqlite.py`) | SQLite lead_research |
| 5 | Dashboard mirror refresh (`refresh_render_dashboard_once.sh`, `DASHBOARD_FAST=1`) | Postgres mart/reporting |
| 6 | Outbound sidecar sync (automatic when `RUN_OUTBOUND_SIDECAR_MIRROR=1`, default) | Postgres `outbound.*` |
| 7 | Verify Postgres (`verify_outbound_sidecar_postgres_mirror.py`; included in script) | — |
| 8 | Dashboard hard refresh (browser) | — |

**One command** (steps 1, 4–7 when flags set; run steps 2–3 manually first if not already done):

```bash
DASHBOARD_FAST=1 RUN_GMAIL_INGEST=1 RUN_LEAD_RESEARCH_MIRROR=1 \
  bash apps/email-pipeline/scripts/ops/refresh_render_dashboard_once.sh
```

Expected operator dashboard status after green verify: **LISTO** (`api.v_operator_status.verdict = READY`, `outbound_readiness_json.verdict = mirror_ok`).

---

## Verify expectations (fail-closed)

After sync, assertions require:

| Check | Expected |
|-------|----------|
| `archive.emails` | `0` (mirror does not load full archive bodies) |
| `api.v_warm_case` | `> 0` |
| `api.v_equipment_opportunity_current` | `9` after direct ChileCompra refresh or explicit legacy/backfill equipment reload (override: `ORIGENLAB_EXPECT_EQUIPMENT_COUNT`) |
| `reporting.dashboard_sync_run` latest | `status = success`, `finished_at` set |

JSON artifact: `/tmp/render_dashboard_mirror_verify.json`

### Commercial deal mirror (when `RUN_COMMERCIAL_DEAL_MIRROR=1`)

| Check | Expected |
|-------|----------|
| `commercial.deal` row count | Matches SQLite `commercial_deal` count |
| JSONB columns | No forbidden keys (`--scan-jsonb`) |

JSON artifact: `/tmp/commercial_deals_mirror_verify.json`

### Catalog mirror (when `RUN_CATALOG_MIRROR=1`)

| Check | Expected |
|-------|----------|
| SQLite vs Postgres row counts | Match per table (`products`, `supplier_offers`, etc.) |
| Text columns | No forbidden terms (`--scan-text`) |

JSON artifact: `/tmp/catalog_postgres_mirror_verify.json`

### Outbound sidecar mirror (default on, `RUN_OUTBOUND_SIDECAR_MIRROR=1`)

| Check | Expected |
|-------|----------|
| `outbound.contact_email_suppression` row count | Matches SQLite `contact_email_suppression` |
| Bounce suppressions (`bounce_%` reason codes) | Matches SQLite |
| Contacted sidecar distinct emails | Matches SQLite (suppression ∪ outreach contacted/replied/snoozed) |
| Lead blocked / net_new_safe (when lead mirror ran) | Matches SQLite `lead_research_prospect` segments |

JSON artifact: `/tmp/outbound_sidecar_mirror_verify.json`

---

## Manual step-by-step (equivalent)

```bash
set -eo pipefail
cd apps/email-pipeline
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
export ORIGENLAB_CLOUD_POSTGRES_URL='postgresql+psycopg://…'

# Optional ingest
uv sync --group gmail --group dev
uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --folder INBOX --skip-duplicate-message-id --since-days 14
uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --folder "[Gmail]/Enviados" --skip-duplicate-message-id --since-days 14

# Incremental derived layers (NOT --rebuild)
uv sync --group dev
uv run python scripts/mart/build_business_mart.py
uv run python scripts/commercial/build_commercial_intel_v1.py

# Mirror + verify
bash scripts/ops/sync_dashboard_mirror_to_cloud.sh
uv run python scripts/qa/verify_dashboard_postgres_mirror.py \
  --assert-render-dashboard \
  --expect-equipment-count 9
```

---

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| `Refusing Postgres dashboard mirror sync: mart table(s) … empty` | Run incremental `build_business_mart.py` once; if mart was never built, operator must run **one-time** `--rebuild` outside this workflow. |
| Gmail folder select fails | `uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --list-folders` and set `ORIGENLAB_GMAIL_SENT_FOLDER`. |
| Equipment count ≠ 9 | Set `ORIGENLAB_EXPECT_EQUIPMENT_COUNT` to current baseline after operator review. |
| Dashboard stale after green verify | Open dashboard and click **Refresh** (browser cache / SPA poll). |
| **PRECAUCIÓN** / stale suppressions after campaign | `DASHBOARD_FAST=1` skips outbound in main sync — ensure `RUN_OUTBOUND_SIDECAR_MIRROR=1` (default) or run `sqlite_outbound_sidecars_to_postgres.py --replace` manually |
| Suppression count mismatch (e.g. SQLite 114 vs Postgres 96) | Run outbound sidecar sync + `verify_outbound_sidecar_postgres_mirror.py`; check `/tmp/outbound_sidecar_mirror_verify.json` |

---

## Related docs

- [Phase 0 local Postgres mirror proof](PHASE0_LOCAL_POSTGRES_MIRROR.md)
- [Phase 1 cloud read path](PHASE1_CLOUD_READ_PATH.md)
- [RUNBOOK — canonical dashboard refresh chain](RUNBOOK.md#canonical-dashboard-refresh-chain) (includes full mart `--rebuild` — **not** used here)
