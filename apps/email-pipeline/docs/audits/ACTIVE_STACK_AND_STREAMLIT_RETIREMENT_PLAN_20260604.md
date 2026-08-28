# Active stack and Streamlit retirement plan — 2026-06-04

Status: canonical (operator stack alignment)  
Owner: email-pipeline-maintainers  
Supersedes nothing — complements [`STREAMLIT_RETIREMENT_AUDIT_20260602.md`](STREAMLIT_RETIREMENT_AUDIT_20260602.md)

---

## Active stack

| Layer | Path | Role |
|-------|------|------|
| **Operator UI** | [`apps/dashboard`](../../../dashboard/README.md) | React operator dashboard (**:5173**). Read-only; **GET-only** to API. |
| **Backend / read API** | [`apps/api`](../../../api/README.md) | FastAPI on **:8001** — operator routes + **`GET /mirror/*`** Postgres mirror reporting. |
| **Dashboard read model** | Postgres mirror (sync via `uv run origenlab mirror-dashboard`) | Populated from SQLite by [`scripts/sync/sync_dashboard_postgres_mirror.py`](../../scripts/sync/sync_dashboard_postgres_mirror.py). **Not** send/outbound truth. |
| **Operational truth** | `apps/email-pipeline` SQLite + Gmail Sent ingest | Ingest, mart, commercial intel, outbound safety, DNR, equipment-first queues, QA reports. **No UI** in this package. |

**Daily operator path (2026-06-04):**

1. `cd apps/email-pipeline` → `uv run origenlab status` / safety / digest CLIs on SQLite.  
2. Optional mirror refresh → `uv run origenlab mirror-dashboard` (dry-run default; `--apply` only with approval).  
3. `cd apps/api` → API **:8001**.  
4. `cd apps/dashboard` → React UI **:5173** polling `apps/api`.

See monorepo [`docs/PROJECT_CONTEXT.md`](../../../../docs/PROJECT_CONTEXT.md), root [`AGENTS.md`](../../../../AGENTS.md), and [`docs/RUNBOOK.md`](../RUNBOOK.md#m-eprun-dashboard-optional).

---

## Legacy / retired stack (Streamlit)

**Product decision:** Streamlit is **not** the operator UI anymore. Do **not** add Streamlit features. Do **not** treat Streamlit as source of truth for sends, DNR, or mirror state.

| Artifact | Path / note |
|----------|-------------|
| ~~Streamlit app~~ | ~~`apps/business_mart_app.py`~~ **removed** 2026-06-04 |
| ~~Streamlit renderers~~ | ~~`streamlit_prioridad_pages.py`~~, ~~`streamlit_prioridad_handoffs.py`~~, ~~`streamlit_page_status.py`~~ **removed** 2026-06-04 |
| Tatiana draft review | `tatiana_copilot/draft_review_helpers.py` — borrador export; legacy artifact dir suffix unchanged |
| Neutral read helpers | `read/today_workspace.py`, `read/leads_browse.py`, `read/suppliers_browse.py` — **keep**; session keys on `today_workspace` |
| LAN launcher | ~~`scripts/tools/run_streamlit_lan.sh`~~ **removed** |
| Docker UI image | ~~`Dockerfile`~~, ~~`docker-compose.yml`~~ **removed**. Active mirror dev: [`docker-compose.dashboard-postgres.yml`](../../docker-compose.dashboard-postgres.yml) |
| Dependency group | `pyproject.toml` → **`data-tools`** (pandas/xlrd); **`streamlit` removed** from deps (2026-06-04) |
| Renamed tests | `test_today_workspace_read.py`, `test_tatiana_draft_review_helpers.py`, etc. — not the product UI |
| Streamlit operator docs | `docs/pipeline/STREAMLIT_DATA_FRESHNESS.md`, historical RUNBOOK sections — migrate over time |

**Already removed (Phase 5E–5G + 2026-06-04 UI):** browse/today/copy shims, `streamlit_api_preview`, `streamlit_canonical_dashboard_sql`, **`business_mart_app.py`**, **`streamlit_prioridad_*`**, **`streamlit_page_status`**. See `tests/test_read_module_shim_parity.py` (guard test; removed with the read/ package in the 2026-08 commercial platform reset) and [`STREAMLIT_LAUNCH_SURFACE_REMOVAL_PLAN_20260604.md`](STREAMLIT_LAUNCH_SURFACE_REMOVAL_PLAN_20260604.md).

---

## Decision

- **Active UI:** `apps/dashboard` (React).  
- **Active read API:** `apps/api` + Postgres mirror.  
- **email-pipeline:** batch jobs, CLIs (`origenlab`), SQLite truth, reports — **not** a UI package.  
- **Streamlit:** removed 2026-06-04; historical audit/design references and backward-compatible aliases remain where explicitly documented.
- **Prefer:** API/dashboard parity, `origenlab` CLIs, and CSV/JSON under `reports/out/`; do not add new Streamlit pages.

---

## Retirement phases

| Phase | Goal | This PR |
|-------|------|---------|
| **0** | Mark legacy/parked; document active stack | **Yes** — this doc + surgical README/RUNBOOK/APP_CONTEXT/AGENTS notes |
| **1** | Remove Streamlit as *primary* path in top-level docs | **Partial** — README/RUNBOOK pointers; full RUNBOOK dedup deferred |
| **2** | API/dashboard parity checklist per Streamlit page | Documented below — implementation in follow-up PRs |
| **3** | Delete Streamlit Python UI (`business_mart_app`, `streamlit_prioridad_*`, `streamlit_page_status`) | **Done** (2026-06-04) — see launch-surface plan |
| **4** | Drop `streamlit` dep; rename `ui` → `data-tools` | **Done** (2026-06-04) |

---

## Phase 2 — parity checklist (API + dashboard)

Use before deleting Streamlit pages. Status from [`STREAMLIT_RETIREMENT_AUDIT_20260602.md`](STREAMLIT_RETIREMENT_AUDIT_20260602.md) §3.2 (2026-06-04).

| Streamlit capability | Replacement target | Parity |
|---------------------|-------------------|--------|
| Inicio / KPIs | `GET /operator/status`, Today dashboard | Partial |
| Actividad contacto Gmail | `GET /emails/recent` (API) | Dashboard wiring TBD |
| Casos para revisar | `GET /cases/*`, `cases_review_queue` | Partial |
| Contacts / orgs browse | `GET /mirror/contacts`, `/mirror/organizations` | Read partial; RW only CLI/Streamlit |
| Equipment / opportunities | `/opportunities/equipment`, mirror commercial | Partial |
| Outbound / suppressions | `/mirror/outbound/*` + SQLite CLIs | Read partial |
| Salud de datos | — | **Missing** in React |
| Cola outreach marketing | `export_next_marketing_recipients.py` | CLI only |
| Borrador comercial | Tatiana scripts / `draft_review_helpers` | CLI/library export (legacy `reports/out/*_streamlit_borrador_comercial/`) |
| Candidatos comerciales | — | **Missing** (RW was Streamlit-only) |
| Sidecar writes (suppression, lead research, commercial review) | CLIs + env-gated Streamlit | **API is GET-only** — CLIs remain truth for writes |

**Write policy:** Retiring Streamlit without new audited write APIs means **all sidecar mutations stay on CLIs** (`refresh-safety`, suppression imports, commercial intel scripts). See [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md).

---

## Candidate files — remove now / later / needs parity

| Category | Path | Status | Notes |
|----------|------|--------|-------|
| App | ~~`apps/business_mart_app.py`~~ | **Removed** | 2026-06-04 — no `apps/api`/`apps/dashboard` imports |
| UI modules | ~~`streamlit_prioridad_pages.py`~~, ~~`streamlit_prioridad_handoffs.py`~~, ~~`streamlit_page_status.py`~~ | **Removed** | Handoff keys on `read/today_workspace.py` |
| Tatiana helper | `tatiana_copilot/draft_review_helpers.py` | **Keep** | Renamed from `streamlit_draft_helpers` |
| Read helpers | `read/today_workspace.py`, `read/leads_browse.py`, `read/suppliers_browse.py` | **Keep** | API/library |
| Docker Streamlit | ~~`Dockerfile`~~, ~~`docker-compose.yml`~~ | **Removed** | Dashboard stack uses `docker-compose.dashboard-postgres.yml` |
| Deps | `data-tools` (pandas/xlrd) | **Keep** | No `streamlit` package |
| Tests | UI-only tests (`test_business_mart_app_ux`, etc.) | **Removed** | Read-module tests remain |
| Already gone | browse/today/copy shims, `streamlit_api_preview`, canonical SQL shim | **Done** | Phase 5E–5G |

---

## What stays in email-pipeline (not retirement targets)

- `uv run origenlab` operator CLI ([`OPERATOR_COMMAND_SURFACE.md`](../OPERATOR_COMMAND_SURFACE.md))  
- Ingest / mart / commercial / safety scripts ([`SCRIPT_MAP.md`](../SCRIPT_MAP.md))  
- SQLite schema modules, outbound gate, equipment-first builders  
- Postgres **sync** scripts (parked for daily ops but required for dashboard mirror)  
- Shared utilities — [`SHARED_UTILITY_CONTRACTS.md`](../SHARED_UTILITY_CONTRACTS.md)  
- Root/core facade layout — [`MODULE_FACADE_AUDIT_20260604.md`](MODULE_FACADE_AUDIT_20260604.md), [`ROOT_MISC_MODULE_CLASSIFICATION_20260604.md`](ROOT_MISC_MODULE_CLASSIFICATION_20260604.md)

---

## Non-goals (this initiative)

- **No** runtime changes to Gmail, Postgres sync, send, mirror, purge, or `--apply` in retirement PRs.
- **No** runtime behavior changes (Gmail, Postgres sync, send, purge, mirror, `--apply`).  
- **No** import migrations or `core/` facade churn for Streamlit retirement.  
- **No** generic developer-doc expansion unrelated to retirement (prefer this plan + parity checklist).  
- **No** re-adding the `streamlit` package without an approved UI surface.

---

## Launch surfaces (detail)

See [`STREAMLIT_LAUNCH_SURFACE_REMOVAL_PLAN_20260604.md`](STREAMLIT_LAUNCH_SURFACE_REMOVAL_PLAN_20260604.md) — per-path grep evidence, active/legacy, and PR 1 vs PR 3+ delete list.

## Follow-up PR ideas (ordered)

1. **docs:** RUNBOOK — demote `#m-eprun-docker-streamlit` to “legacy”; dashboard section first in operator map. **(PR 1 — done in launch-surface batch)**
2. **docs:** `EXPERIMENTAL_PARKED.md` — Streamlit row → legacy/parked; React = active UI.  
3. **api/dashboard:** Wire `GET /emails/recent`, expand cases/contacts parity (read-only).  
4. **operator env:** `ORIGENLAB_OPERATOR_*` with `ORIGENLAB_STREAMLIT_*` aliases (contact suppression, leads research, Sent preflight) — **done** 2026-06-04.

---

## Verification

```bash
cd apps/email-pipeline
uv run pytest tests/test_active_stack_docs.py tests/test_module_facade_audit.py tests/test_operator_cli.py -q
uv run origenlab audit-facades -- --fail-on-manual-review
```

---

## References

- [`STREAMLIT_RETIREMENT_AUDIT_20260602.md`](STREAMLIT_RETIREMENT_AUDIT_20260602.md) — inventory + parity detail  
- [`apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](../../../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)  
- [`apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md`](../../../api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md)
