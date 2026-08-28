# Shared utility contracts — email-pipeline

Status: canonical for developers  
Owner: email-pipeline-maintainers

## Purpose

Five small modules live at the **package root** (`origenlab_email_pipeline/<name>.py`). They are **intentional shared utilities**, not “misc unknown” leftovers:

- Safe for **tests**, **scripts**, and **mart/commercial helpers** to import directly.
- **No** Streamlit, Gmail, Postgres, OpenAI, or production SQLite paths inside these modules.
- **Characterization tests** lock observable contracts before any future move or `core/` facade.

## Contract table

| Module | Responsibility | Stable contract |
|--------|----------------|-----------------|
| [`timeutil.py`](../src/origenlab_email_pipeline/timeutil.py) | UTC timestamps for pipeline metadata | `now_iso()` → `YYYY-MM-DDTHH:MM:SSZ` (no fractional seconds, suffix `Z`) |
| [`freshness_dates.py`](../src/origenlab_email_pipeline/freshness_dates.py) | Derived mart timeline date filtering | `email_date_iso_for_mart_timeline(date_iso, *, slack_days=2, today=None)` — empty → `None`; unparseable non-empty → pass through; parsed date strictly after `today + slack_days` → `None`; negative slack → `0`; slack `> 3660` → default `2` |
| [`contact_export_queries.py`](../src/origenlab_email_pipeline/contact_export_queries.py) | Shared `contact_master` export/audit SQL | `sql_contact_master_marketing_export_candidates()` and `sql_contact_master_candidate_audit_contacts()` share the same `FROM`/`WHERE`/`ORDER`/`LIMIT ?` tail; column tuples `CONTACT_MASTER_*_COLUMN_NAMES` match SQLite `cursor.description` order |
| [`pipeline_meta_schema.py`](../src/origenlab_email_pipeline/pipeline_meta_schema.py) | Additive SQLite metadata DDL | `ensure_pipeline_meta_tables(conn)` idempotent; creates `pipeline_run` + `pipeline_kv` with documented columns |
| [`pipeline_run_recorder.py`](../src/origenlab_email_pipeline/pipeline_run_recorder.py) | Run audit + KV store | `start_run` / `finish_run` / `set_kv` / `get_kv` on shared DB file (mutates SQLite when scripts run — not for read-only operator snapshots) |

## Usage notes

- **`timeutil`**: use for any new pipeline row timestamp; do not invent alternate formats.
- **`freshness_dates`**: only affects **derived** mart bounds; raw `emails.date_iso` is never rewritten.
- **`contact_export_queries`**: if you change ranking or filters, update **both** SQL builders and run contract tests; scripts `export_marketing_from_contact_master` and `export_candidate_audit` must stay aligned.
- **`pipeline_meta_schema` / `pipeline_run_recorder`**: call `start_run` at script start and `finish_run` on success; use `set_kv` for cross-run checkpoints (e.g. mart build stamps). Tests use `:memory:` SQLite only.

## Tests

```bash
cd apps/email-pipeline
uv run pytest tests/test_shared_utility_contracts.py -q
```

Related existing tests (not replaced):

- `tests/test_timeutil_contract.py` — minimal `now_iso` pattern
- `tests/test_freshness_dates.py` — additional mart date scenarios
- `tests/test_contact_export_queries.py` — SQL execution + ordering on sample rows

Full shared-utility + operator CLI smoke:

```bash
uv run pytest tests/test_shared_utility_contracts.py tests/test_module_facade_audit.py tests/test_operator_cli.py -q
```

## Future refactor rule

Before moving any of these modules under `core/` or a subpackage:

1. **Keep or extend** `tests/test_shared_utility_contracts.py` (and siblings above).
2. Run an **import/script/docs audit** (`rg`, `SCRIPT_MAP.md`, monorepo `apps/api` if applicable).
3. Add a **facade shim** only when a stable `core.*` import path is required — do not delete the root implementation in the same PR.
4. **Do not delete** a utility because fan-in looks low; script entrypoints and KV recording are often single-caller by design.

## Non-goals

- No Gmail ingest/send, Postgres mirror, `--apply`, or `refresh-dashboard` orchestration in these modules.
- No dependency on production `ORIGENLAB_SQLITE_PATH` in contract tests.
