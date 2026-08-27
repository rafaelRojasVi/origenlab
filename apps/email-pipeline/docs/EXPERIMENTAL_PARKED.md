# Experimental / parked features (email-pipeline)

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-05-19 (RUNBOOK dedup: [dashboard section](RUNBOOK.md#m-eprun-dashboard-optional))

**Purpose:** Single index for infrastructure and pilots that are **built but not on the daily SQLite outbound path**. Agents and operators should read this **before** touching Postgres, the HTTP dashboard, Tatiana/ML tooling, or old campaign pilots.

**Companion:** [`SCRIPT_MAP.md`](SCRIPT_MAP.md) · [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) · [`audits/REDUCTION_SHORTLIST_20260607.md`](audits/REDUCTION_SHORTLIST_20260607.md) · [`AGENTS.md`](../AGENTS.md) · [`reports/out/active/current/manifest.json`](../reports/out/active/current/manifest.json) (`postgres_status` / `api_status`: `parked`)

---

## What is **not** required for daily ops

The following are **optional**. None are needed for:

- Gmail Workspace ingest → SQLite (`emails`, Sent truth)
- Anti-repeat / DNR refresh (`refresh_outbound_safety_memory.py` chain)
- Equipment-first tender queues (`build_equipment_first_*`)
- Outbound send safety (shared gate, `mark_sent_batch_contacted`, suppressions)

| Area | Paths | Notes |
|------|-------|-------|
| **Postgres migration** | `alembic/`, `scripts/migrate/sqlite_*_to_postgres.py` | `--replace` can TRUNCATE/DELETE target tables. **Scratch Postgres only** until explicitly promoted. |
| **Dashboard mirror** | `scripts/sync/sync_dashboard_postgres_mirror.py` | Loads Postgres mirror from SQLite; **not** send/export truth. |
| **Dashboard stack wrapper** | `scripts/ops/refresh_operational_dashboard_stack.py` | Mart + mirror orchestration; **DASHBOARD_ONLY**; Gmail ingest off by default. |
| **HTTP mirror API** | [`apps/api`](../../api/README.md) (`GET /mirror/*` on :8001) | Legacy `apps/email-pipeline/src/origenlab_api` on :8000 **removed** (API-3 Phase 6) — see [`apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md`](../../api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md). |
| **React dashboard** | [`apps/dashboard`](../../dashboard/README.md) | **Active operator UI** — polls `apps/api` (operator + mirror read paths). Obsolete pre-v1 dashboard client removed. |
| **Tatiana pilots** | `scripts/tatiana/*`, `src/origenlab_email_pipeline/tatiana_copilot/` | Drafting / eval; **lab boundary — not daily outbound** — see [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md). |
| **ML exploration** | `scripts/ml/*`, `scripts/reports/build_ml_report.py` | Embeddings / clustering experiments; lab report path. |
| **Dataset / cohort tools** | `scripts/dataset/*` | Tatiana cohort exports and label review CLIs. |
| **Old campaign pilots** | `scripts/leads/campaigns/*` | e.g. DR50 reconciliation, ready8 patches — niche, not current equipment-first policy. |

**Streamlit Python UI** (`business_mart_app.py` and related UI modules) **removed** (2026-06-04). Active operator UI: `apps/dashboard` + `apps/api`. Retirement plan: [`audits/ACTIVE_STACK_AND_STREAMLIT_RETIREMENT_PLAN_20260604.md`](audits/ACTIVE_STACK_AND_STREAMLIT_RETIREMENT_PLAN_20260604.md).

---

## Hard rules (agents + operators)

1. **Do not run** `alembic upgrade`, `sqlite_*_to_postgres.py --replace`, or `sync_dashboard_postgres_mirror.py` **without explicit approval**.
2. **Do not run** `refresh_operational_dashboard_stack.py` (or the full dashboard refresh chain in [`RUNBOOK.md`](RUNBOOK.md)) **without explicit approval**.
3. **Do not assume** Postgres or API is installed, migrated, or running for send/export decisions — use **SQLite** + `operator_status.py`.
4. **Do not use** stale legacy artifacts (`buyer_opportunity_crosscheck_*`, `tender_buyer_outreach_queue_*`) for current operator work — see manifest `legacy_do_not_use`. Legacy `build_buyer_opportunity_queue.py` was removed in Phase 5C; use `build_equipment_first_*`.

---

## File banners (Phase 1)

Scripts in the parked paths above carry top-of-file comments:

- **`# EXPERIMENTAL_PARKED`** — migrate loaders, mirror sync
- **`# EXPERIMENTAL_PARKED / DASHBOARD_ONLY`** — `refresh_operational_dashboard_stack.py`

Tatiana/ML/dataset/campaign files are listed here **without** mass header edits (avoid risky churn). Treat entire directories as parked per [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) and reduction planning in [`audits/REDUCTION_SHORTLIST_20260607.md`](audits/REDUCTION_SHORTLIST_20260607.md).

---

## When you *might* use the parked stack

Only with **explicit operator approval**. **Daily work** stays in [RUNBOOK — Daily outbound](RUNBOOK.md#m-eprun-daily-outbound) (`operator_status`, Gmail Sent ingest, `refresh_outbound_safety_memory`) — **not** this stack.

**Commands (single chain — do not duplicate in cheat sheet):** [RUNBOOK — Optional dashboard preview stack](RUNBOOK.md#m-eprun-dashboard-optional) · [canonical dashboard refresh chain](RUNBOOK.md#canonical-dashboard-refresh-chain).

**Planned wrapper UX:** [`dashboard_stack_simplification_design_20260519.md`](../reports/out/active/current/dashboard_stack_simplification_design_20260519.md) (`--mode sqlite-status|postgres-mirror|full-dashboard`). **Today:** `refresh_operational_dashboard_stack.py` remains **experimental/dashboard-only** — can mutate SQLite mart + Postgres mirror by default; prefer explicit RUNBOOK steps until modes ship.

Checklist:

1. Confirm SQLite + canonical Gmail ingest are current (or run ingest in dashboard chain).
2. Trial migrations on **scratch** Postgres (`ORIGENLAB_POSTGRES_URL`) only with approval.
3. Rebuild mart if needed (`build_business_mart.py` — break-glass).
4. Mirror sync → start API → React dev server.
5. Treat API/React as **read-only** preview — not outbound safety truth.

---

## Related audits

- [`audits/POSTGRES_API_PIPELINE_MESS_AUDIT.md`](audits/POSTGRES_API_PIPELINE_MESS_AUDIT.md)
- [`reports/out/active/current/code_quality_simplification_audit_20260519.md`](../reports/out/active/current/code_quality_simplification_audit_20260519.md)
