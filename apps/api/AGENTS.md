# Operator API — agent instructions

**Policy overlay for `apps/api/`.** Factual contracts live in code, [`README.md`](README.md), and monorepo docs — do not duplicate full architecture here.

## Read first

1. Root [`AGENTS.md`](../../AGENTS.md) — monorepo operator stack rules
2. [`docs/STATUS.md`](../../docs/STATUS.md) — what is actually built and deployed
3. [`docs/PROJECT_CONTEXT.md`](../../docs/PROJECT_CONTEXT.md) — what each app owns (**V1 reference**; for V2 work read [`docs/README.md`](../../docs/README.md) instead)
4. [`README.md`](README.md) — routes, env, smoke commands
5. Dashboard freeze: [`../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)

## Hard rules

| Rule                    | Detail                                                                                                                                                                   |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Active API**          | This app on port **8001** only. Legacy email-pipeline FastAPI on **:8000** was **removed** (API-3 Phase 6). Removal enforcement: `apps/api/scripts/api3_phase6_grep_gate.sh` (run in CI). |
| **Narrow write surface** | GET-only except the explicitly allowlisted `POST /operations/*` durable CRM commands and the tender annex preview/import (see `CURRENT_SYSTEM_TRUTH.md`). No send/ingest/migrate endpoints — ever. |
| **Production auth**     | `ORIGENLAB_API_AUTH_TOKEN` required when `ORIGENLAB_ENV=production`; public routes: `/health` + `OPTIONS` only — see [docs/PRODUCTION_AUTH.md](docs/PRODUCTION_AUTH.md). |
| **No pipeline writes**  | Do not import or invoke Gmail ingest, DNR refresh, mirror sync, or send scripts from this package.                                                                       |
| **Mirror ≠ send truth** | Postgres mirror responses are not outbound approval. Supabase is **not** a mirror — it is the V2 durable system of record ([`docs/README.md`](../../docs/README.md)). |

## Tests

From the repo root: `make test-api` (after `make db-up`; see [root `AGENTS.md` → *Testing*](../../AGENTS.md)) · one file: `scripts/run_pytest.sh api tests/test_x.py` · V1 smoke: `uv run python scripts/dashboard_v1_http_smoke.py --expect-backend sqlite` (from `apps/api/`) · production: `make smoke`
