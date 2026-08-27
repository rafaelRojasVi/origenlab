# API-3 Phase 6 — Legacy :8000 API removal complete

**Date:** 2026-05  
**Status:** Complete

---

## Removed

| Artifact                                         | Notes                                                                                |
| ------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `apps/email-pipeline/src/origenlab_api/`         | Entire legacy FastAPI tree (18 modules)                                              |
| `apps/email-pipeline/tests/test_api_*.py`        | Legacy-only HTTP tests (slice1, classification, commercial, meta, cors, deprecation) |
| `apps/dashboard/scripts/legacy-smoke.mjs`        | Deprecated smoke                                                                     |
| `npm run smoke:legacy`                           | Removed from `package.json`                                                          |
| RUNBOOK `:8000` uvicorn + deprecated curl blocks | Operators use `:8001` `/mirror/*` only                                               |
| Streamlit `:8000` path branch                    | `api_preview_paths()` always uses `/mirror/*`                                        |
| Dual-server parity (`--legacy-base`)             | `mirror_parity_smoke.py` is mirror-only                                              |

---

## Replacement

| Need                      | Use                                                                                                                        |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Postgres mirror reporting | `apps/api` on **:8001**, `GET /mirror/*`                                                                                   |
| Operator Dashboard Today  | Unchanged: `/health`, `/operator/status`, `/cases/warm`, `/opportunities/equipment`, `/contacts/{email}`, `/emails/recent` |
| Shared SQL                | `origenlab_email_pipeline.postgres_dashboard_api`                                                                          |
| Smoke                     | `npm run smoke:mirror`, v1 freeze checklist                                                                                |

---

## Legacy dashboard UI

The previously parked pre-v1 dashboard under `apps/dashboard/src/legacy/` was subsequently removed during repository simplification. The active operator UI remains `apps/dashboard` backed by `apps/api`.

---

## Related

- [API-3_PHASE6B_STABILIZATION.md](./API-3_PHASE6B_STABILIZATION.md) — post-removal architecture
- [archive/api3/](./archive/api3/README.md) — historical phases 1–5B
