# Operator API — agent instructions

**Policy overlay for `apps/api/`.** Factual contracts live in code, [`README.md`](README.md), and monorepo docs — do not duplicate full architecture here.

## Read first

1. Root [`AGENTS.md`](../../AGENTS.md) — monorepo operator stack rules
2. [`docs/PROJECT_CONTEXT.md`](../../docs/PROJECT_CONTEXT.md) — what each app owns
3. [`README.md`](README.md) — routes, env, smoke commands
4. Dashboard freeze: [`../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](../dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)

## Hard rules

| Rule | Detail |
|------|--------|
| **Active API** | This app on port **8001** only. Legacy email-pipeline FastAPI on **:8000** was **removed** (API-3 Phase 6). |
| **GET only** | No write/send/ingest/migrate endpoints. |
| **Production auth** | `ORIGENLAB_API_AUTH_TOKEN` required when `ORIGENLAB_ENV=production`; public routes: `/health` + `OPTIONS` only — see [docs/PRODUCTION_AUTH.md](docs/PRODUCTION_AUTH.md). |
| **No pipeline writes** | Do not import or invoke Gmail ingest, DNR refresh, mirror sync, or send scripts from this package. |
| **Mirror ≠ send truth** | Postgres / future Supabase mirror responses are not outbound approval. |

## Tests

From `apps/api/`: `uv run pytest tests -q` · `uv run python scripts/dashboard_v1_http_smoke.py --expect-backend sqlite`
