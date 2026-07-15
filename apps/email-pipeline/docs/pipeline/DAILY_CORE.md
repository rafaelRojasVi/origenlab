# Daily core workflow (canonical)

Status: canonical (operator contract)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-05

Related: [`OPERATOR_COMMAND_SURFACE.md`](../OPERATOR_COMMAND_SURFACE.md) · [`RUNBOOK.md`](../RUNBOOK.md) · [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md) · post-send: [`POST_SEND_SAFE_LOOP.md`](POST_SEND_SAFE_LOOP.md) · future split: [`DAILY_CORE_FAST_REFRESH_SPLIT.md`](DAILY_CORE_FAST_REFRESH_SPLIT.md) · mart optimization: [`EMAIL_MART_FEATURES_DESIGN.md`](EMAIL_MART_FEATURES_DESIGN.md)

This document defines the **daily operating layer** for `apps/email-pipeline`: what operators run to refresh SQLite operational truth and safety exports **without** sending mail, purging data, or requiring Postgres.

---

## Canonical daily command

From `apps/email-pipeline/`:

```bash
uv run origenlab daily-core --apply
```

This is the **daily core CLI alias**. It runs the eight steps below against **SQLite** and writes **reports** under `reports/out/`. It **never** runs Postgres mirror sync.

`refresh-dashboard --apply --no-mirror` is similar but still uses the legacy email-body mart scan; **daily-core** uses the faster feature-backed mart path (see step 2–3 below).

---

## Plan-only (safe default)

Inspect the workflow before any writes:

```bash
uv run origenlab daily-core
```

Plan-only mode prints the eight-step list and exits **without** Gmail ingest, feature refresh, mart rebuild, commercial intel refresh, or other mutating steps. The plan shows **eight steps** (no mirror).

---

## Daily core steps (in order)

When `daily-core --apply` is used, the orchestrator runs:

| # | Step | CLI subcommand | Notes |
|---|------|----------------|-------|
| 1 | Gmail ingest | `gmail-ingest` | INBOX + Sent (`[Gmail]/Enviados`); updates SQLite `emails` |
| 2 | Email mart features | `build-email-mart-features --missing-only --apply` | Inserts feature rows for newly ingested emails only |
| 3 | Business mart rebuild | `build-mart -- --rebuild --use-email-mart-features` | Break-glass: deletes and rebuilds mart tables from precomputed features |
| 4 | Commercial intel | `build-commercial-intel` | Incremental refresh of SQLite `commercial_*` |
| 5 | Outbound safety exports | `refresh-safety` | Anti-repeat / DNR export chain to `reports/out` |
| 6 | NDR review batches | `ndr-review` | **Read-only** NDR review queue — **does not apply suppressions** |
| 7 | Post-send digest | `post-send-digest` | Report artifacts after contacted-universe audit inputs |
| 8 | Operator status | `status` | READY / CAUTION / BLOCKED snapshot |

With `--skip-ingest`, steps 2–3 still run (missing-only feature refresh is cheap when current).

Optional flags (same contract, different shape): `--skip-ingest`, `--since-days N` — see `uv run origenlab daily-core --help`.

---

## Operational truth contract

| Topic | Source of truth |
|-------|-----------------|
| Ingested mail + Sent history | **SQLite** (`emails`), populated by Gmail ingest (Sent folder in SQLite) |
| Anti-repeat / contacted memory | **SQLite** + safety exports from `refresh-safety` |
| Send / already-contacted decisions | **SQLite + Gmail Sent history inside SQLite** — not Postgres, not dashboard UI |

**Postgres mirror** and **deployed dashboard / API status** are **read-only visibility** for reporting and operator UI. They are **not send approval**. LISTO / READY on a dashboard does **not** mean an outbound batch may be sent.

---

## Daily core safety boundaries

The daily core workflow (`daily-core --apply`):

- **Does not send emails.**
- **Does not purge data.**
- **Does not apply NDR suppressions** (`ndr-review` builds review batches only).
- **Does not run Alembic.**
- **Does not require Postgres** (mirror is never part of `daily-core`).
- **Does not approve outbound sends** — use separate campaign / export / send procedures after readiness checks.

Mart rebuild (`build-mart --rebuild --use-email-mart-features`) **does** mutate SQLite mart tables. That is expected for daily core; it is not Gmail send and not Postgres mirror.

---

## Run manifest (apply only)

When `daily-core --apply` completes (success or first-step failure), the CLI writes:

`reports/out/active/current/daily_core_run_manifest.json`

This file is **operator visibility / evidence only**. It records which daily-core steps ran and their exit codes. It is **not send approval**, does **not** replace SQLite or Gmail Sent operational truth, and is **separate** from the existing campaign workspace index at `reports/out/active/current/manifest.json`.

Plan-only (`uv run origenlab daily-core`) and `--help` do **not** write this file.

After a **successful** applied daily-core run, the CLI may also write aggregate-only SQLite storage evidence (`sqlite_storage_snapshot.json` / `sqlite_storage_history.json`) under `active/current`. That telemetry is **non-critical operator evidence**, not a ninth workflow step, and failures there must not change daily-core’s return code. See [`../SQLITE_STORAGE_MAINTENANCE.md`](../SQLITE_STORAGE_MAINTENANCE.md).

`uv run origenlab status` includes a read-only summary of this manifest when present (visibility only — **not send approval**).

---

## Optional mirror (separate step)

Postgres reporting visibility is **outside** daily core. **Operator recipe (env, dry-run, apply, smoke):** [`POSTGRES_MIRROR_REFRESH.md`](POSTGRES_MIRROR_REFRESH.md).

Run mirror only when a Postgres URL is configured and reporting visibility is needed — **after** `daily-core --apply` when SQLite truth is stale:

```bash
uv run origenlab mirror-dashboard --apply
```

Requires `ORIGENLAB_POSTGRES_URL`, `ALEMBIC_DATABASE_URL`, or `ORIGENLAB_CLOUD_POSTGRES_URL`. Default `mirror-dashboard` (no `--apply`) is dry-run. Schema migrations (`alembic upgrade head`) are **not** part of daily core — use `mirror-dashboard --alembic --apply` only with explicit ops approval.

**Do not** treat mirror success as permission to send.

---

## Future: fast refresh vs daily core

Gmail ingest is fast (~18s). **Mart rebuild** now uses precomputed `email_mart_features` (~3s feature scan vs ~368s legacy body scan after full backfill). Per-email automation should still **not** call full `daily-core --apply` on every message.

A separate **operator-fast-refresh** / **email-event-refresh** workflow is planned (not implemented). It would ingest recent Gmail changes and refresh recent operator views **without** full mart rebuild. `daily-core` remains the canonical full SQLite truth job.

See [`DAILY_CORE_FAST_REFRESH_SPLIT.md`](DAILY_CORE_FAST_REFRESH_SPLIT.md) for timing evidence and the three-lane model. Feature design and parity proof: [`EMAIL_MART_FEATURES_DESIGN.md`](EMAIL_MART_FEATURES_DESIGN.md).

---

## What daily core is not

- **Not** per-email automation — see [`DAILY_CORE_FAST_REFRESH_SPLIT.md`](DAILY_CORE_FAST_REFRESH_SPLIT.md).
- **Not** the full post-send loop — see [`POST_SEND_SAFE_LOOP.md`](POST_SEND_SAFE_LOOP.md) after sends.
- **Not** outbound campaign export or send — see [`RUNBOOK.md`](../RUNBOOK.md) daily outbound sections.
- **Not** a substitute for `check-readiness` before building send batches.
- **Not** institution grouping or cleanup planners — see `plan_function_surface.py` / `plan_import_surface.py` (read-only audits).

---

## Quick reference

```bash
cd apps/email-pipeline

# Plan only — no mutations (eight steps, no mirror)
uv run origenlab daily-core

# Daily core — SQLite + reports, no Postgres mirror
uv run origenlab daily-core --apply

# Optional mirror (separate; Postgres required)
uv run origenlab mirror-dashboard --apply
```
