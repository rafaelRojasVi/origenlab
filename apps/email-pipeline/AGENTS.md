# Email-pipeline — agent instructions

**Policy overlay for `apps/email-pipeline/`.** Factual behavior lives in code and canonical docs below. If this file conflicts with **`RUNBOOK.md`** or **`SCRIPT_MAP.md`**, follow the canonical doc.

## Read before acting (in order)

1. **[`reports/out/active/current/manifest.json`](reports/out/active/current/manifest.json)** — canonical vs stale files, known warnings, parked Postgres/API.
2. **[`reports/out/active/current/README_ACTIVE_CURRENT.md`](reports/out/active/current/README_ACTIVE_CURRENT.md)** — operator handoff for `active/current/`.
3. **[`docs/SCRIPT_MAP.md`](docs/SCRIPT_MAP.md)** — which scripts are daily, break-glass, or lab.
4. **[`docs/EXPERIMENTAL_PARKED.md`](docs/EXPERIMENTAL_PARKED.md)** — **read before** Postgres migrate/mirror, FastAPI/React dashboard, Tatiana/ML/dataset, or old campaign pilots.
5. **[`docs/RUNBOOK.md`](docs/RUNBOOK.md)** — procedures (ingest, DNR, equipment-first, campaigns).
6. **[`docs/CRUD_SAFETY.md`](docs/CRUD_SAFETY.md)** — mutation rules and `--apply` policy.
7. **Operator status (read-only):**

   ```bash
   cd apps/email-pipeline
   uv run python scripts/qa/operator_status.py
   ```

8. **Classification layers & send gates:** [`docs/pipeline/SCHEMA_CLASSIFICATION_MODEL.md`](docs/pipeline/SCHEMA_CLASSIFICATION_MODEL.md) — evidence vs safety vs workflow; never gate sends on `lead_research_prospect.classification` alone.
9. **Safety checkpoint (pause marker):** [`docs/pipeline/CURRENT_SAFETY_CHECKPOINT.md`](docs/pipeline/CURRENT_SAFETY_CHECKPOINT.md) — safe loop before outreach; golden rules; what not to build next.
10. **Post-send safe loop:** [`docs/pipeline/POST_SEND_SAFE_LOOP.md`](docs/pipeline/POST_SEND_SAFE_LOOP.md) — after Sent/NDR changes; targeted NDR `--emails-file` / `--only-code`; broad `--apply` is break-glass.
11. Optional context: [`docs/OUTBOUND_SOURCE_OF_TRUTH.md`](docs/OUTBOUND_SOURCE_OF_TRUTH.md), [`reports/out/active/current/code_quality_simplification_audit_20260519.md`](reports/out/active/current/code_quality_simplification_audit_20260519.md).

## Hard rules (non-negotiable)

| Rule                              | Detail                                                                                                                                                                                                                               |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **No email sending**              | Do not run `send_inline_html_email_via_gmail_api.py` or any send path unless the user explicitly orders a send test.                                                                                                                 |
| **No Gmail mutation**             | Do not create drafts, labels, or API writes. **Read-only IMAP ingest** (`05_workspace_gmail_imap_to_sqlite.py`) is allowed when the user asks to refresh Sent truth.                                                                 |
| **No Postgres migrations**        | Do not run `alembic upgrade`, `sqlite_*_to_postgres.py --replace`, or `sync_dashboard_postgres_mirror.py` unless explicitly approved. Postgres is **parked** for daily ops.                                                          |
| **No `--apply` without approval** | Imports, backfills, purges, suppression writes, and archive moves require explicit user consent. Default to dry-run / read-only.                                                                                                     |
| **No invented contacts**          | Do not add buyer emails, DeepSearch rows, or marketing contacts without evidence.                                                                                                                                                    |
| **Equipment-first tenders**       | Use `equipment_first_operator_queue_*.csv` and aligned `buyer_opportunity_ab_queue_*.csv`.                                                                                                                                           |
| **LEGACY scripts**                | **Do not use for current operator work:** `buyer_opportunity_crosscheck_*`, `tender_buyer_outreach_queue_*` — use `build_equipment_first_*` builders instead. Legacy `build_buyer_opportunity_queue.py` was **removed in Phase 5C**. |
| **Parked stack**                  | Read [`docs/EXPERIMENTAL_PARKED.md`](docs/EXPERIMENTAL_PARKED.md) before Postgres/API/Tatiana/ML; not required for ingest, DNR, equipment-first queues, or send safety.                                                              |
| **No file deletes**               | Unless the user explicitly requests deletion.                                                                                                                                                                                        |
| **Tests for behavior changes**    | If you change code (not docs-only), run targeted pytest; see workspace rule *Testing — definition of done*.                                                                                                                          |

## Runtime truth

- **SQLite** (`ORIGENLAB_SQLITE_PATH` / settings) + **Gmail Sent in `emails`** = outbound safety truth.
- **Active operator UI** = [`apps/dashboard`](../../dashboard/README.md) + [`apps/api`](../../api/README.md) (Postgres mirror read model). Not Streamlit.
- **Postgres mirror sync** = parked for *daily outbound* but required when using the React dashboard; not send approval.
- **Streamlit Python UI** = **removed** (2026-06-04). Do not reintroduce; active UI is **`apps/dashboard`** — see [`docs/audits/ACTIVE_STACK_AND_STREAMLIT_RETIREMENT_PLAN_20260604.md`](docs/audits/ACTIVE_STACK_AND_STREAMLIT_RETIREMENT_PLAN_20260604.md).

## Safe default workflow

```bash
cd apps/email-pipeline
uv run python scripts/qa/operator_status.py          # read-only verdict
uv run python scripts/qa/check_outbound_readiness.py # read-only DNR/Sent preflight
# Mutations only with explicit approval:
# uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --folder "[Gmail]/Enviados"
# uv run python scripts/qa/refresh_outbound_safety_memory.py
```

## Makefile shortcuts (optional)

From `apps/email-pipeline/`: `make doctor`, `make safety-refresh`, `make equipment-queue` (optional `DATE_SUFFIX=YYYYMMDD`), `make audit` — see [`Makefile`](Makefile).

## Monorepo pointer

[`../../docs/PROJECT_CONTEXT.md`](../../docs/PROJECT_CONTEXT.md)
