# Phase 0 — Local Postgres mirror proof-of-life

**Status:** operator procedure  
**Scope:** Disposable **local** Postgres only (`127.0.0.1:5433`). No cloud deploy, no Gmail ingest, no mart rebuild, no 128GB SQLite copy.

## Prerequisites

```bash
cd apps/email-pipeline
uv sync --group dev   # includes postgres (psycopg, alembic)
export ORIGENLAB_SQLITE_PATH="$HOME/data/origenlab-email/sqlite/emails.sqlite"
```

## 1. Start local Postgres

```bash
docker compose -f docker-compose.dashboard-postgres.yml up -d
export ORIGENLAB_POSTGRES_URL='postgresql+psycopg://origenlab:origenlab@127.0.0.1:5433/origenlab_dashboard_local'
```

One-off alternative:

```bash
docker run -d --name origenlab-dashboard-local-pg \
  -e POSTGRES_USER=origenlab -e POSTGRES_PASSWORD=origenlab \
  -e POSTGRES_DB=origenlab_dashboard_local \
  -p 127.0.0.1:5433:5432 postgres:16
```

## 2. Migrate

```bash
uv run alembic -c alembic.ini upgrade head
```

## 3. Mirror sync (read-only SQLite → Postgres)

Uses **existing** mart state; does not ingest Gmail or `--rebuild` marts.

```bash
uv run origenlab mirror-dashboard --live --apply \
  --operator phase0-local \
  --reason "Phase 0 local postgres mirror proof-of-life"
```

## 4. Verify

```bash
uv run python scripts/qa/verify_dashboard_postgres_mirror.py
```

Expect `archive.emails` count **0** on a lightweight mirror (mart + outbound sidecars + commercial tables only; no full archive replica). Warm cases via `api.v_warm_case` and `commercial.warm_case`.

**Equipment canonical source (PHASE W1, 2026-08):** direct Postgres publication is legacy/manual-backfill opt-in — `uv run origenlab auto-refresh-chilecompra-equipment --once --apply --publish-read-model` when Postgres is configured; the flag defaults `false` and the tracked cron wrapper explicitly disables it, so this table family is frozen for observation. Use `mirror-dashboard --live -- --include-equipment-opportunities` only for an explicit legacy/backfill CSV reload of the resolved `active/current` equipment queue.

## 5. Next: Phase 1 cloud

See [PHASE1_CLOUD_READ_PATH.md](PHASE1_CLOUD_READ_PATH.md) — cloud Postgres + API + static dashboard; manual sync via `scripts/ops/sync_dashboard_mirror_to_cloud.sh`.

## 6. API + dashboard smokes

```bash
cd ../api
uv sync --group dev
export ORIGENLAB_API_BACKEND=postgres
export ORIGENLAB_POSTGRES_URL="$ORIGENLAB_POSTGRES_URL"
uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001

# other terminal
cd ../dashboard
EXPECT_BACKEND=postgres npm run smoke:postgres
EXPECT_BACKEND=postgres npm run smoke:contacts
```

## Teardown

```bash
docker compose -f docker-compose.dashboard-postgres.yml down
# optional: docker compose -f docker-compose.dashboard-postgres.yml down -v
```
