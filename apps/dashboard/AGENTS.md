# Dashboard — agent instructions

**Policy overlay for `apps/dashboard/`.** UI behavior and freeze validation live in [`README.md`](README.md) and [`docs/V1_FREEZE_OPERATOR_HANDOFF.md`](docs/V1_FREEZE_OPERATOR_HANDOFF.md).

## Read first

1. Root [`AGENTS.md`](../../AGENTS.md) — monorepo operator stack rules
2. [`docs/PROJECT_CONTEXT.md`](../../docs/PROJECT_CONTEXT.md) — architecture entrypoint
3. [`docs/V1_FREEZE_OPERATOR_HANDOFF.md`](docs/V1_FREEZE_OPERATOR_HANDOFF.md) — run modes, smokes, forbidden UI actions
4. API contracts: [`../api/README.md`](../api/README.md)

## Hard rules

| Rule | Detail |
|------|--------|
| **Read-only UI** | **Today** page only in active code; no send, draft, archive, mark-contacted, or status writes. |
| **API target** | `apps/api` on **:8001** — operator routes only, not `/mirror/*` on Today. |
| **Legacy UI** | Pre-v1 dashboard UI removed; extend active Dashboard/API paths instead of restoring it. |
| **No data mutation** | Browser must not open SQLite, Postgres, CSV paths, or email-pipeline mutation scripts. |

## Smokes

From `apps/dashboard/`: `npm run smoke:sqlite` · `npm run smoke:contacts` · `npm test`
