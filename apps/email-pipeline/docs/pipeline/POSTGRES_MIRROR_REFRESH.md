# Postgres mirror refresh (operator workflow)

Status: canonical (operator contract)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-07

Related: [`DAILY_CORE.md`](DAILY_CORE.md) · [`RUNBOOK.md`](../RUNBOOK.md) · [`OPERATOR_COMMAND_SURFACE.md`](../OPERATOR_COMMAND_SURFACE.md) · [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md)

This document is the **operator recipe** for refreshing the **Postgres dashboard mirror** after SQLite operational truth has been updated. It is separate from daily core and from outbound send procedures.

---

## Mirror run lifecycle status

`reporting.dashboard_sync_run` and `ops.pipeline_kv` (`dashboard_postgres_mirror_last_sync`) share one lifecycle per apply invocation:

| Status | Meaning |
|--------|---------|
| **`running`** | Invocation accepted; selected loaders are still executing. `finished_at` is null. |
| **`success`** | **Every** selected loader and final count verification completed. Terminal. |
| **`failed`** | Invocation terminated before complete publication. Terminal. |

Invariants:

- **One history row per invocation** when `reporting.dashboard_sync_run` exists (insert `running`, then compare-and-set the same `id` with `WHERE status = 'running' RETURNING id`).
- Terminal publication **fails closed** if the expected running row is missing, already terminal, or the sync id is stale — KV is not updated and no replacement history row is inserted.
- Mid-run detail refresh also requires a still-`running` row before rewriting KV (so terminal KV cannot regress to `running`).
- **`ops.pipeline_kv` reflects the same current lifecycle** (same `sync_run_id`, status, timestamps, and details) when both stores exist.
- A **success watermark is terminal**, not an intermediate milestone after outbound/mart only.
- Dry-run writes **no** run row and **no** KV lifecycle update.
- Persisted / CLI `error_message` values are sanitized (no raw Postgres URLs, passwords, tokens, or absolute SQLite paths).

Compatibility when tables are missing:

| Reporting table | KV table | Behavior |
|-----------------|----------|----------|
| present | present / absent | Require durable `running` row; CAS to terminal; update KV only if present and CAS succeeded. |
| absent at start and finish | present | Explicit KV-only mode (`sync_run_id` may be `None`); no invented history row. |
| absent | absent | No-op lifecycle publication (supported compatibility). |
| topology changes mid-run | — | Fail closed; do not synthesize history or claim consistent terminal publication. |

This lifecycle PR does **not** make loader publication globally atomic across every dashboard dataset. Outbound may commit before mart, and later in-process loaders commit separately.

## Transactional publication (outbound + mart loaders)

Each base migrate loader publishes its **selected target set** in **one** PostgreSQL transaction:

| Loader | Atomic visibility unit |
|--------|------------------------|
| **Outbound** | All three targets (`contact_email_suppression`, `contact_domain_suppression`, `outreach_contact_state`) become visible together at one commit. |
| **Mart** | All **selected** mart targets (`--tables archive\|canonical\|all`) become visible together at one commit. |

Within that transaction:

1. Acquire `SHARE ROW EXCLUSIVE` locks on selected targets (serializes competing replace writers; ordinary `SELECT` readers remain allowed).
2. Optionally `DELETE` selected targets (`--replace`).
3. Load every selected table (batched inserts **without** intermediate commits).
4. Repair sequences / run final validation **before** commit.
5. **Commit once** on success; **rollback** on any conversion, insert, sequence, or validation failure so the prior committed publication remains unchanged.

**Residual boundary:** the overall `mirror-dashboard` refresh is **not** one database-wide transaction. This removes empty/partial publication *inside* each base loader; it does **not** provide a generation-wide snapshot across outbound + mart + later in-process stages.

Missing SQLite outbound sidecar source tables remain warnings (logical zero rows). In `--replace` mode that still publishes an empty Postgres target for that source as part of the same atomic outbound set.

---

## Purpose

Give operators a single, copy-paste workflow for:

1. Refreshing **SQLite + reports** (daily core).
2. Loading **Postgres connection env** from the local `.env` file.
3. **Dry-running** then **applying** the mirror sync.
4. **Smoking** the local or live API/dashboard read paths.

---

## Mental model

| Layer | Role |
|-------|------|
| **`daily-core --apply`** | Refreshes **SQLite** operational truth and safety exports under `reports/out/`. **Never** runs Postgres mirror. |
| **`mirror-dashboard --apply`** | Copies refreshed SQLite-side state into the **Postgres mirror** for reporting and the React dashboard (core loaders only). |
| **`mirror-dashboard --live --apply`** | Same as above **plus** warm cases, commercial deals, and operator snapshots — the dashboard people actually see. Equipment opportunities are direct-published by the ChileCompra refresh path. |
| **Dashboard / API (`apps/api` :8001)** | **Read-only visibility** over SQLite (operator routes) and Postgres (`/mirror/*`). |
| **Postgres mirror** | **Not send approval.** LISTO / READY / mirror success does **not** mean an outbound batch may be sent. |

```
Gmail + pipeline scripts
  ↓ daily-core --apply
SQLite + reports/out  (operational truth)
  ↓ mirror-dashboard --live --apply
Postgres mirror  (read-only reporting)
  ↓ apps/api /mirror/*
React dashboard (read-only)
```

---

## When to run it

Run mirror refresh when:

- You need the **React dashboard** or **`/mirror/*` API** to reflect a recent **daily-core** (or equivalent SQLite refresh).
- Operator UI counts (Prospectos, Negocios, Catálogo, sync timestamps) look **stale** compared to SQLite/`operator_status.py`.
- After a deliberate SQLite refresh on the same machine that holds the Postgres URL in `.env`.

**Do not** run mirror apply as a substitute for daily core, send readiness checks, or post-send safety loops.

---

## Before you mirror

1. **Refresh SQLite truth first** — mirror copies what SQLite and sidecars already contain; it does not ingest Gmail or rebuild marts by itself.
2. **Confirm Postgres URL is configured** — one of `ORIGENLAB_POSTGRES_URL`, `ALEMBIC_DATABASE_URL`, or `ORIGENLAB_CLOUD_POSTGRES_URL` must be set (typically in `apps/email-pipeline/.env`, **uncommitted**).
3. **Default mirror is dry-run** — `mirror-dashboard` without `--apply` prints the plan and does **not** write Postgres.
4. **Schema migrations are separate** — use `mirror-dashboard --alembic --apply` **only** when an explicit schema migration is required (see [Alembic / schema note](#alembic--schema-note)).

Recommended order:

```bash
cd apps/email-pipeline

# 1 — SQLite + reports (no Postgres)
uv run origenlab daily-core --apply
```

Equivalent long form: `uv run origenlab refresh-dashboard --apply --no-mirror`. See [`DAILY_CORE.md`](DAILY_CORE.md).

---

## Local shell / env setup

CLI subprocesses **do not** automatically load `apps/email-pipeline/.env`. If Postgres URL variables live only in that file, the mirror command will fail until the shell exports them.

**Load `.env` in the current shell** (from `apps/email-pipeline/`):

```bash
cd apps/email-pipeline

set -a
source .env
set +a
```

- **`set -a`** — export every variable assigned while sourcing.
- **`source .env`** — read local env file (must stay **gitignored**; never commit real URLs or passwords).
- **`set +a`** — stop auto-export.

Verify (values redacted — only check that the variable is **non-empty**):

```bash
# Example — do not paste real URLs into tickets or docs
test -n "${ORIGENLAB_POSTGRES_URL:-${ALEMBIC_DATABASE_URL:-${ORIGENLAB_CLOUD_POSTGRES_URL:-}}}" && echo "Postgres URL present"
```

**Never paste real database URLs into docs, commits, or chat.**

---

## Live dashboard refresh (preferred)

For the **live React dashboard** and deployed API counts (warm cases, commercial deals, operator snapshots), use the **`--live`** preset instead of remembering passthrough flags. Equipment opportunities are refreshed by the direct ChileCompra path: `uv run origenlab auto-refresh-chilecompra-equipment --once --apply`.

**`--live`** includes:

- warm cases (`--include-warm-cases`) with the same default window as **Hoy**: **14 days / 100 warm cases** (`--warm-days 14 --warm-limit 100`)
- stale generated **`warm_queue_promotion`** cases missing from the current snapshot are **closed** in Postgres (`--close-missing-warm-cases`) so `api.v_warm_case` matches the live dashboard; this does **not** delete emails and does **not** approve sends
- precise warm-case **role categories** are stored in `commercial.warm_case.role_category` and exposed via `api.v_warm_case` (legacy `category` remains for CHECK compatibility)
- commercial deals (`--include-commercial-deals`)
- operator snapshots (`--include-operator-snapshots`)

Override the warm-case window via passthrough when needed:

```bash
uv run origenlab mirror-dashboard --live -- --warm-days 30 --warm-limit 200
```

**`mirror-dashboard --apply`** without **`--live`** refreshes **core mirror pieces only** (mart, outbound, canonical, purchase events). Use that when you intentionally skip optional dashboard loaders.

`--include-equipment-opportunities` is no longer part of the normal live mirror loop. Pass it after `--` only for explicit legacy/backfill CSV reloads, for example:

```bash
uv run origenlab mirror-dashboard --live --apply \
  --operator rafael \
  --reason "Legacy equipment CSV backfill" \
  -- --include-equipment-opportunities
```

**Daily core intentionally never includes mirror.**

If local SQLite and live Postgres **Hoy** warm-case counts still differ after `mirror-dashboard --live --apply`, export both `/cases/warm` responses and run the [warm-case parity audit](#warm-case-parity-audit-sqlite-vs-postgres-caseswarm) (diagnostic only). Remaining gaps after role-category promotion usually mean real row differences or classification edge cases, not legacy category flattening (`supplier_reply` vs `supplier_quote_received`).

### Dry-run

```bash
cd apps/email-pipeline

set -a
source .env
set +a

uv run origenlab mirror-dashboard --live
```

### Apply

```bash
cd apps/email-pipeline

set -a
source .env
set +a

uv run origenlab mirror-dashboard --live --apply --operator rafael --reason "Daily live dashboard refresh"
```

`--live --apply` requires **`--operator`** (or **`--updated-by`**) and **`--reason`** for optional-loader audit. Dry-run does not.

With the optional **`ol-mirror`** shell helper (see below):

```bash
ol-mirror --live
ol-mirror --live --apply --operator rafael --reason "Daily live dashboard refresh"
```

---

## Dry-run (core mirror only)

After sourcing `.env`:

```bash
cd apps/email-pipeline

uv run origenlab mirror-dashboard
```

This is the **safe default** for core mirror: plan only, **no Postgres writes**. For the live dashboard preset, use **`mirror-dashboard --live`** (see above). Review output for missing URL, empty mart, or sync scope errors before applying.

---

## Apply (core mirror only)

When dry-run looks correct and Postgres target is intentional:

```bash
cd apps/email-pipeline

set -a
source .env
set +a

uv run origenlab mirror-dashboard --apply
```

Writes **core** Postgres mirror loaders only. For the live dashboard people actually see, prefer **`mirror-dashboard --live --apply`** (above).

Successful apply typically reports:

- `status: success`
- `dry_run: False`
- a **`sync_run_id`**
- row counts for canonical / archive / outbound / commercial loads (exact keys depend on sync scope)

**Mirror success is not send approval.**

---

## Smoke checks

### Local API (`apps/api` on :8001)

Start the API with the same Postgres URL the mirror used, then:

```bash
curl -sS 'http://127.0.0.1:8001/mirror/dashboard/summary' | uv run python -m json.tool
curl -sS 'http://127.0.0.1:8001/mirror/dashboard/summary?scope=archive' | uv run python -m json.tool
curl -sS 'http://127.0.0.1:8001/mirror/meta/dashboard-sync' | uv run python -m json.tool
```

Optional: open the React dashboard (`apps/dashboard`, `npm run dev`) and confirm Hoy / Prospectos / Negocios / Catálogo reflect recent mirror data.

### Live operator status (read-only)

```bash
curl -sS 'https://api.origenlab.cl/operator/status?max_staleness_days=14' | python -m json.tool
```

This checks deployed operator visibility; it does **not** approve sends.

---

## Optional shell helper (`~/.zshrc` only)

To avoid forgetting `source .env`, you may add a **local-only** zsh function in **`~/.zshrc`** (not in this repo):

```bash
# Local operator helper — adjust clone path; do NOT commit to the repo
ol-mirror() (
  cd /path/to/origenlab/apps/email-pipeline || return
  set -a
  source .env
  set +a
  uv run origenlab mirror-dashboard "$@"
)
```

Usage:

```bash
ol-mirror              # core dry-run
ol-mirror --apply      # core mirror write
ol-mirror --live       # live dashboard dry-run
ol-mirror --live --apply --operator rafael --reason "Daily live dashboard refresh"
```

Replace `/path/to/origenlab` with your clone location. Keep `.env` uncommitted.

---

## What it does not do

`mirror-dashboard` (with or without `--apply`):

- **Does not send emails.**
- **Does not purge data.**
- **Does not apply NDR suppressions.**
- **Does not replace daily core** — run `daily-core --apply` first when SQLite truth is stale.
- **Does not approve outbound sends.**

**Daily core intentionally never includes mirror.** Use this doc for the mirror step after daily core.

---

## Alembic / schema note

- **Normal mirror refresh:** `mirror-dashboard` / `mirror-dashboard --apply` — **no Alembic**.
- **Schema drift / new migrations:** `mirror-dashboard --alembic --apply` runs `alembic upgrade head` **then** mirror apply. Use **only** when an explicit schema migration is required and ops has approved target Postgres.
- Do **not** treat Alembic as part of daily core or routine mirror refresh.

---

## Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Mirror fails: no Postgres URL | `.env` not sourced in shell | `set -a; source .env; set +a` from `apps/email-pipeline/` |
| `ORIGENLAB_CLOUD_POSTGRES_URL` in `.env` but CLI sees nothing | Ran mirror from another directory / fresh shell | `cd apps/email-pipeline` and source `.env` before `uv run origenlab mirror-dashboard` |
| Mirror aborts: empty mart | SQLite mart not rebuilt | Run `daily-core --apply` (includes mart rebuild) before mirror |
| Dashboard stale but SQLite fresh | Skipped mirror apply | Dry-run then `mirror-dashboard --apply` after sourcing env |
| API mirror routes 503 / empty | API missing Postgres URL or mirror never applied | Align API env with pipeline `.env`; re-run apply smoke curls above |
| Confused daily vs mirror | Wrong doc section | Daily truth: [`DAILY_CORE.md`](DAILY_CORE.md); mirror: this file |
| Live Hoy warm-case counts differ from local SQLite after `mirror-dashboard --live --apply` | Warm-case promotion/classification parity gap between SQLite `/cases/warm` and Postgres mirror `/cases/warm` | Export both API responses and run the read-only parity audit below (diagnostic only; **not send approval**) |

### Warm-case parity audit (SQLite vs Postgres `/cases/warm`)

If mirror apply succeeded but **Hoy** client/supplier/logistics counts still differ between local SQLite and live Postgres dashboards, compare exported warm-case payloads:

```bash
cd apps/email-pipeline

# Export JSON manually from each backend (do not commit responses):
# curl -sS 'http://127.0.0.1:8001/cases/warm' > /tmp/warm_sqlite.json
# curl -sS 'https://api.origenlab.cl/cases/warm' > /tmp/warm_postgres.json

uv run python scripts/qa/audit_warm_case_parity.py \
  --sqlite-json /tmp/warm_sqlite.json \
  --postgres-json /tmp/warm_postgres.json \
  --out-dir reports/out/active/current/warm_case_parity_audit
```

The audit reports row/category count deltas, category mismatches for the same contact+subject, and SQLite-only vs Postgres-only rows. **Diagnostic only** — it does not mutate data or approve sends.

---

## Quick reference

```bash
cd apps/email-pipeline

# 1 — SQLite operational truth (no mirror)
uv run origenlab daily-core --apply

# 2 — load local Postgres URL from gitignored .env
set -a
source .env
set +a

# 3 — dry-run live dashboard mirror (preferred)
uv run origenlab mirror-dashboard --live

# 4 — apply live dashboard mirror
uv run origenlab mirror-dashboard --live --apply --operator rafael --reason "Daily live dashboard refresh"

# (core only, no optional loaders)
# uv run origenlab mirror-dashboard --apply

# 5 — local smoke (API must be running on :8001)
curl -sS 'http://127.0.0.1:8001/mirror/meta/dashboard-sync' | uv run python -m json.tool
```
