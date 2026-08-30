# Equipment opportunities — Postgres read model (operator runbook)

**Status:** canonical operator workflow (2026-07)
**Audience:** operators and developers verifying equipment mirror + API read path  
**Related:** [`../architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md`](../architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md) · [`../pipeline/POSTGRES_MIRROR_REFRESH.md`](../pipeline/POSTGRES_MIRROR_REFRESH.md) · [`../../../api/README.md`](../../../api/README.md)

---

## Purpose

Give operators a single checklist to verify that:

1. Postgres schema/migrations for the equipment read model are at head (`20260617_0030`).
2. `api.v_equipment_opportunity_current` has **one row per `opportunity_key`**.
3. Repeated keys in the bridge layer are understood (audit) but do not leak into the current API view.

This runbook is **read-only verification**. It does not approve sends, mutate Gmail, or change SQLite operational truth.

**`GET /opportunities/equipment` (the apps/api HTTP route) is retired** — the dashboard's actionable-opportunity summary now sources from `GET /operator/procurement/status` (W1) instead. §1–§3 below (migrations, key audit, direct-SQL view check) remain the way to verify this read model's health; §4–§5 (which used to curl the now-retired route) have been updated accordingly.

**PHASE W1 (2026-08):** the writer below is legacy/manual-backfill opt-in (`--publish-read-model`, default `false`); the tracked cron wrapper explicitly disables it, so this read model is frozen at its last writer run for an observation period rather than updated on schedule.

---

## Data flow

```text
ChileCompra API/detail builder rows
        │
        ▼
auto-refresh-chilecompra-equipment --once --apply
        │
        ▼
commercial.equipment_opportunity_source   (source load + artifact metadata)
commercial.equipment_opportunity          (rows; opportunity_key indexed, not unique)
        │
        ▼
api.v_equipment_opportunity               (canonical base rows from current source)
api.v_equipment_opportunity_key_audit     (correlation: repeated keys across loads)
        │
        ▼
api.v_equipment_opportunity_current       (one row per opportunity_key — read-model truth)
        │
        ▼
available for direct SQL / audit access
(no apps/api HTTP route currently reads this view — retired; see §4)
```

The command also writes `equipment_first_operator_queue_*.csv` and the canonical dashboard CSV under `reports/out/active/current` for audit/debugging. Those CSV artifacts are no longer the normal live writer bridge; `mirror-dashboard --live -- --include-equipment-opportunities` is reserved for explicit legacy/backfill CSV reloads.

---

## Source/provenance vs current API truth

| Layer | What it is | Used by public API? |
|-------|------------|---------------------|
| CSV queue file under `reports/out/active/current` | Operator artifact + legacy/backfill mirror input | **No** (dev/SQLite fallback only) |
| `commercial.equipment_opportunity_source` | Load provenance (`csv_path`, `file_sha256`, `source_kind`, `artifact_basename`, `canonical_reason`) | Indirectly via views |
| `commercial.equipment_opportunity` | All mirrored rows; same `opportunity_key` may repeat across `source_id` loads | **No** (base table) |
| `api.v_equipment_opportunity` | Canonical-source rows from the current load | Internal base view |
| `api.v_equipment_opportunity_key_audit` | Repeated-key diagnostic (`row_count > 1`) | **No** (operator CLI only) |
| **`api.v_equipment_opportunity_current`** | **Current** deduplicated read model | **No** — HTTP route retired; direct SQL/audit access only (§3) |

**Identity for correlation:** `opportunity_key` (`equipment:<source_slug>:<codigo_licitacion_lower>`).  
**Provenance fields** (`source_id`, internal `csv_path`, `source_path` in Postgres) must not appear as raw filesystem paths in public JSON.

---

## Required environment variables

Load from `apps/email-pipeline/.env` when working locally (`set -a && source .env && set +a`). **Never paste values into chat, tickets, or CI logs.**

| Variable | Used for |
|----------|----------|
| `ORIGENLAB_POSTGRES_URL` | Mirror loaders, audits, direct `psycopg` checks |
| `ALEMBIC_DATABASE_URL` | Alembic migrations (`alembic upgrade head`) — usually same target as `ORIGENLAB_POSTGRES_URL` |
| `CF_ACCESS_CLIENT_ID` | Remote API audit / manual curl behind Cloudflare Access |
| `CF_ACCESS_CLIENT_SECRET` | Remote API audit / manual curl behind Cloudflare Access |
| `ORIGENLAB_API_AUTH_TOKEN` | Production private-route smoke / manual curl (`X-OriginLab-API-Key`) |
| `ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS` | Per-request timeout for `remote_response_audit.py` (default `30`; use `90` on cold Render) |
| `ORIGENLAB_REMOTE_AUDIT_RETRIES` | Network retries only (default `2`) |
| `ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS` | Sleep between network retries (default `2.0`) |

Production API also requires `ORIGENLAB_API_BACKEND=postgres` and `ORIGENLAB_ENV=production` on Render — see [`../../../api/README.md`](../../../api/README.md).

---

## 1. Confirm migrations at head

```bash
cd apps/email-pipeline
export ALEMBIC_DATABASE_URL="$ORIGENLAB_POSTGRES_URL"

uv run alembic current
uv run alembic heads
uv run alembic upgrade head
```

**Healthy:** `alembic current` shows revision **`20260617_0030`** (or later head that includes `api.v_equipment_opportunity_current`).

Key migrations on this path:

| Revision | Adds |
|----------|------|
| `20260617_0025` | Source artifact metadata on `equipment_opportunity_source` |
| `20260617_0026` | `source_kind`, `artifact_basename`, `canonical_reason` on base view |
| `20260617_0027` | `opportunity_key` column + index |
| `20260617_0028` | `opportunity_key` on `api.v_equipment_opportunity` |
| `20260617_0029` | `api.v_equipment_opportunity_key_audit` |
| `20260617_0030` | **`api.v_equipment_opportunity_current`** |

---

## 2. Audit repeated keys (bridge layer)

Repeated `opportunity_key` values are **expected** while the CSV bridge is active. This audit lists keys with `row_count > 1` in the correlation view — it does **not** mean the current API is wrong.

```bash
cd apps/email-pipeline
uv run python scripts/audit_equipment_opportunity_keys.py --limit 25
```

**Healthy signals:**

- **No rows printed** — no repeated keys in audit view (fine).
- **Rows with `canonical_row_count >= 1`** — bridge history; current API still dedupes via `api.v_equipment_opportunity_current`.
- **`canonical_row_count = 0`** — stale/non-canonical only; should **not** appear in `api.v_equipment_opportunity_current` (§3).

---

## 3. Verify current view in Postgres

`psycopg.connect()` expects `postgresql://`, not SQLAlchemy's `postgresql+psycopg://`. Normalize the URL in one-off scripts:

```bash
cd apps/email-pipeline
uv run python - <<'PY'
import os
import psycopg

raw = os.environ["ORIGENLAB_POSTGRES_URL"]
url = raw.replace("postgresql+psycopg://", "postgresql://", 1)

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(*) as total_rows,
                   count(distinct opportunity_key) as distinct_keys
            from api.v_equipment_opportunity_current
            """
        )
        total_rows, distinct_keys = cur.fetchone()
        print(f"total_rows={total_rows} distinct_keys={distinct_keys}")
        if total_rows != distinct_keys:
            raise SystemExit("UNHEALTHY: current view has duplicate opportunity_key")
        print("ok: one row per opportunity_key")
PY
```

**Healthy:** `total_rows == distinct_keys`.

---

## 4. Production API smoke (origin-token aware)

```bash
cd apps/email-pipeline
CF_ACCESS_CLIENT_ID=... \
CF_ACCESS_CLIENT_SECRET=... \
ORIGENLAB_API_AUTH_TOKEN=... \
  uv run python scripts/qa/smoke_dashboard_api_readiness.py \
  --api-base https://api.origenlab.cl
```

The smoke script sends Cloudflare Access service-token headers when provided and sends `X-OriginLab-API-Key` when `ORIGENLAB_API_AUTH_TOKEN` is set. It does not print secrets or response bodies.

**Note:** this smoke script no longer checks the equipment read model over HTTP — `GET /opportunities/equipment` is retired, and the script's procurement check now validates `GET /operator/procurement/status` (W1) instead. For the equipment read model specifically, use §1–§3 above (migrations, key audit, direct-SQL view check).

Optional narrower response contract audit (Cloudflare Access only):

```bash
cd apps/api
ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS=90 \
  CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \
  uv run python scripts/remote_response_audit.py
```

`remote_response_audit.py` retries **network** failures (`TimeoutError`, `URLError`, `OSError`) only. Contract failures are **not** retried. Current limitation: it does **not** send `ORIGENLAB_API_AUTH_TOKEN`, so use it only when the target does not require origin token auth on private routes, or after an origin-token-aware smoke has already passed. This script also no longer has an equipment-specific check (route retired); it validates the general response envelope on the current route surface.

Skips with exit `0` when Cloudflare credentials are unset (CI without secrets).

---

## 5. Manual verification — direct SQL only

There is no HTTP route left to manually curl for this read model — `GET /opportunities/equipment` is retired. Use the direct-SQL check in §3 as the manual verification path. If you need to eyeball individual rows:

```bash
cd apps/email-pipeline
uv run python - <<'PY'
import os
import psycopg

raw = os.environ["ORIGENLAB_POSTGRES_URL"]
url = raw.replace("postgresql+psycopg://", "postgresql://", 1)

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "select opportunity_key, buyer, close_at from api.v_equipment_opportunity_current "
            "order by close_at nulls last limit 10"
        )
        for row in cur.fetchall():
            print(row)
PY
```

Inspect:

- each `opportunity_key` is a non-empty string, unique across the printed rows
- no raw filesystem paths in the output (this query does not select `source_path`)

---

## Expected healthy signals (summary)

| Check | Expected |
|-------|----------|
| `api.v_equipment_opportunity_current` | `count(*) == count(distinct opportunity_key)` |
| View rows | each has non-empty `opportunity_key` |
| Key audit CLI | no rows, or repeated keys with canonical history only |
| Current view | stale keys with `canonical_row_count = 0` in audit **absent** from the current view |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `UndefinedTable: api.v_equipment_opportunity_key_audit` | Migrations not applied | `uv run alembic upgrade head` (see §1) |
| `InvalidTableDefinition: cannot change name of view column "source_id"` on upgrade | Migration `0028` inserted `opportunity_key` before existing view columns | Ensure `20260617_0028` **appends** `opportunity_key` after `canonical_reason`; re-run migrations from fixed revision |
| `psycopg` error: `invalid connection option` / missing `=` after `postgresql+psycopg://` | SQLAlchemy URL passed directly to `psycopg.connect` | Replace scheme: `.replace("postgresql+psycopg://", "postgresql://", 1)` |
| `remote_response_audit.py` timeout on `/health` | Cold Render instance | `ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS=90` and/or rely on default network retries |
| Audit shows repeated keys | Bridge re-ingest of same `codigo_licitacion` | **Okay** during CSV bridge — verify current view still dedupes (§3) |
| Repeated key with `canonical_row_count = 0` | Non-canonical stale rows only | Should **not** appear in `api.v_equipment_opportunity_current`; if it does, inspect view definition and canonical flags |

---

## Security

- **Never** paste `ORIGENLAB_POSTGRES_URL`, `ALEMBIC_DATABASE_URL`, or Cloudflare service token values into chat, screenshots, or public logs.
- If a database URL or Access secret is exposed, **rotate** Render Postgres credentials and regenerate the Cloudflare Access service token.
- Public API responses must keep filesystem paths **basename-only** with `source_path_info.redacted == true` — see [`../../../api/docs/API_RESPONSE_CONTRACT.md`](../../../api/docs/API_RESPONSE_CONTRACT.md).

---

## Related docs

- Architecture contract: [`../architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md`](../architecture/EQUIPMENT_READ_MODEL_BOUNDARY.md)
- Mirror refresh (loads data into Postgres): [`../pipeline/POSTGRES_MIRROR_REFRESH.md`](../pipeline/POSTGRES_MIRROR_REFRESH.md)
- API response shapes: [`../../../api/docs/API_RESPONSE_CONTRACT.md`](../../../api/docs/API_RESPONSE_CONTRACT.md)
