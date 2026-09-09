# Contributing to OrigenLab

This repository is a **monorepo** with four apps:

- [`apps/web/`](apps/web/) — Astro marketing site (Node.js).
- [`apps/email-pipeline/`](apps/email-pipeline/) — Python email/leads/reporting pipeline (`uv`, `pytest`, optional ML groups).
- [`apps/api/`](apps/api/) — read-only operator FastAPI on **:8001** (GET only).
- [`apps/dashboard/`](apps/dashboard/) — read-only operator React UI on **:5173** (**Today** → `apps/api`).

Start with the map: [`docs/DOCUMENTATION_MAP.md`](docs/DOCUMENTATION_MAP.md) and monorepo context: [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md).

## Operator stack (read-only)

| Component | Port / default | Notes |
|-----------|----------------|-------|
| **`apps/api`** | **8001** | Active HTTP API. Legacy email-pipeline API on **:8000** was **removed** (API-3 Phase 6). |
| **`apps/dashboard`** | **5173** | Active UI; no write/send from the browser. |
| **SQLite** | `ORIGENLAB_SQLITE_PATH` | Default backend for API + pipeline; **send/outreach truth**. |
| **Postgres mirror** | optional | Read-only reporting mirror; sync via email-pipeline scripts only when explicitly approved. |
| **Supabase** | `supabase/` | **V2 durable system of record** (accepted 2026-09-05) — not a mirror. Local schema foundation only; no hosted project exists. See [`docs/README.md`](docs/README.md); build state in [`docs/STATUS.md`](docs/STATUS.md). |

Agent pointers: root [`AGENTS.md`](AGENTS.md), [`apps/api/AGENTS.md`](apps/api/AGENTS.md), [`apps/dashboard/AGENTS.md`](apps/dashboard/AGENTS.md).

## Safe validation (casual testing)

For dashboard/API checks without production risk:

- Follow [`apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md) — Mode 1 (SQLite) or Mode 2 (**disposable** Postgres on e.g. `:5433`).
- API smoke: `cd apps/api && uv run python scripts/dashboard_v1_http_smoke.py --expect-backend sqlite`
- Dashboard smoke: `cd apps/dashboard && npm run smoke:sqlite` (and `npm run smoke:contacts` when rows have email)

**Do not** use casual validation to mutate **Gmail**, **production SQLite**, or **production/scratch Postgres**. Mirror sync and ingest/send scripts live in **email-pipeline** and require explicit approval — see [`apps/email-pipeline/docs/CRUD_SAFETY.md`](apps/email-pipeline/docs/CRUD_SAFETY.md).

### Active operator stack

For PRs touching email-pipeline, API, or dashboard:

```bash
./scripts/validate-active-stack.sh
```

This runs each app’s validate script safely (no apply/send/purge/Alembic). It does not include the public web app. The script fails fast if the tree is dirty before validation and also checks that validation did not dirty tracked files; if `apps/api/uv.lock` drifts, inspect the diff before restoring.

### Local full check

From the repo root:

```bash
./scripts/check-all.sh
```

This runs web, email-pipeline, API, and dashboard gates. It is intentionally heavier than app-specific checks. Use `./scripts/validate-active-stack.sh` when you only need the active operator stack. It does not run Gmail/Postgres/send/purge/apply workflows.

## Local setup

### Website (`apps/web`)

- Use Node 20 (see [`apps/web/.nvmrc`](apps/web/.nvmrc)).
- From `apps/web/`: `npm ci`, then `npm run check` and `npm run build` when you change site code.

### Email pipeline (`apps/email-pipeline`)

- Python 3.12 with [`uv`](https://docs.astral.sh/uv/).
- From `apps/email-pipeline/`: `uv sync --group dev --group data-tools --group lab` (matches CI for pandas/xlrd tests and Tatiana lab deps).
- Copy [`apps/email-pipeline/.env.example`](apps/email-pipeline/.env.example) to `.env` and point paths at a **directory outside the repo** for real data (see [`apps/email-pipeline/docs/DATA_LOCATIONS.md`](apps/email-pipeline/docs/DATA_LOCATIONS.md)).

## What must not be committed

Do not commit:

- `.env` or any file containing secrets
- PST/mbox archives, SQLite/DB files, JSONL exports, report outputs, caches, virtualenvs, or build artifacts (`dist/`, `.astro/`, `node_modules/`, `.venv/`)

When in doubt, check [`.gitignore`](.gitignore) and the app-level `.gitignore` files.

## Pull requests

Use the repository root PR template ([`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)) and mark which area you touched.

## Security

See [`SECURITY.md`](SECURITY.md) (repo root) and [`apps/email-pipeline/docs/SECURITY.md`](apps/email-pipeline/docs/SECURITY.md) for data-handling expectations.

## Licensing

By contributing, you agree your contributions are licensed under the same terms as this project (see [`LICENSE`](LICENSE)).
