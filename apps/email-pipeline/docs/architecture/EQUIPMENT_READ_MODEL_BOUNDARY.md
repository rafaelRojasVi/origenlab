# Equipment opportunities — read-model boundary

**Status:** active architecture contract (2026-06)  
**Audience:** API, dashboard, and email-pipeline maintainers  

**Related:**

- [`apps/api/README.md`](../../../api/README.md) — operator API backends and production env
- [`docs/runbooks/EQUIPMENT_READ_MODEL_RUNBOOK.md`](../runbooks/EQUIPMENT_READ_MODEL_RUNBOOK.md) — operator verification checklist (migrations, audits, remote API)
- [`apps/api/docs/API_RESPONSE_CONTRACT.md`](../../../api/docs/API_RESPONSE_CONTRACT.md) — API route inventory (the legacy `GET /opportunities/equipment` HTTP route documented below is retired; see its changelog entry)

---

## Strategic split

| Layer | Role |
|-------|------|
| **SQLite + local filesystem** | Operational truth: ingest, outbound safety, operator artifacts under `reports/out/active/current` |
| **Postgres (`api.*` views)** | Typed **read model** for API and dashboard in production |
| **CSV queue files** | Export, audit, and operator artifacts — **not** the public HTTP contract |

**`GET /opportunities/equipment` (the apps/api HTTP route) is retired** — the dashboard's actionable-opportunity summary now sources from `GET /operator/procurement/status` (W1) instead. This does **not** change the writer/read-model boundary below: the ChileCompra-direct and legacy-CSV-bridge pipelines still populate `commercial.equipment_opportunity*` and `api.v_equipment_opportunity_current` on schedule, and that data remains available for direct SQL/audit access. The SQLite/active-current CSV path was always **dev/local only** and remains so.

---

## Current live path

```
ChileCompra API/detail builder rows
        │
        ▼
normalized equipment rows in memory
        │
        ▼
commercial.equipment_opportunity_source
commercial.equipment_opportunity
        │
        ▼
api.v_equipment_opportunity_current   (one row per opportunity_key)
        │
        ▼
available for direct SQL / audit access
(no apps/api HTTP route currently reads this view — retired)
```

`auto-refresh-chilecompra-equipment --once --apply` also writes the active/current CSV and manifest artifacts for audit, debugging, and compatibility. With Postgres configured, the typed rows are the normal writer path for this read model (`source_kind=typed_read_model`, `canonical_reason=chilecompra_api_direct_rows`). See [`EQUIPMENT_DIRECT_ROW_LOADER.md`](EQUIPMENT_DIRECT_ROW_LOADER.md).

---

## Legacy CSV bridge (backfill only)

```
equipment_first_operator_queue_*.csv  (active/current artifact)
        │
        ▼
load_equipment_opportunity_mirror.py
        │
        ▼
commercial.equipment_opportunity_source
commercial.equipment_opportunity
        │
        ▼
api.v_equipment_opportunity   (canonical source base rows)
        │
        ▼
api.v_equipment_opportunity_current   (one row per opportunity_key)
        │
        ▼
available for direct SQL / audit access
(no apps/api HTTP route currently reads this view — retired)
```

CSV remains a valid **input** only for explicit legacy/backfill reloads, e.g. `mirror-dashboard --live -- --include-equipment-opportunities`. The API does **not** open that CSV in production.

---

## Target final state

```
domain pipeline (ingest, enrichment, scoring)
        │
        ▼
typed Postgres read model (commercial.* / api.*)
        │
        ├──► apps/api + dashboard (query views)
        │
        └──► CSV export / audit artifact (generated from read model, not identity source)
```

- **Identity** for an equipment opportunity row: **`opportunity_key`** (`equipment:<source_slug>:<codigo_licitacion>`) for cross-source correlation; `codigo_licitacion` plus ChileCompra/detail fields remain display semantics — not the queue CSV filename.
- **`meta.source_path` / `source_path_info`** in API responses: audit/metadata about which mirror artifact row came from — basename-only in JSON; not a stable business key.

---

## Stable opportunity key

`commercial.equipment_opportunity.opportunity_key` is the **cross-source correlation key** for equipment opportunities:

| Property | Rule |
|----------|------|
| Format | `equipment:<source_slug>:<codigo_licitacion_lower>` |
| `source_slug` | From row/`extra_json` `source` when present (normalized); else `equipment_queue` |
| Uniqueness | **Not** unique yet — bridge snapshots may repeat the same opportunity across `source_id` loads |
| Provenance | `source_id`, `csv_path`, and `source_path` remain load/artifact provenance, not business identity |

`api.v_equipment_opportunity` exposes `opportunity_key` additively on each row.

---

## Correlation audit before uniqueness

`opportunity_key` is **indexed but intentionally not unique** during the direct-publish / legacy-backfill phase. The same stable key may appear on multiple `commercial.equipment_opportunity` rows when a new source load re-ingests the same `codigo_licitacion` or when canonical and non-canonical sources both contain the opportunity.

**Audit views** (read-only; no public HTTP endpoint yet):

| View | Schema | Purpose |
|------|--------|---------|
| `v_equipment_opportunity_key_audit` | `commercial` | Correlation report grouped by `opportunity_key` |
| `v_equipment_opportunity_key_audit` | `api` | Same data for API-role / tooling queries |

Columns include `row_count`, `source_count`, `canonical_row_count`, `has_canonical`, sync/close time ranges, sample buyer/title/category, and distinct `source_artifacts` / `canonical_reasons`.

CLI: `scripts/audit_equipment_opportunity_keys.py` lists repeated keys (`row_count > 1`) from `api.v_equipment_opportunity_key_audit`.

Use this evidence before introducing snapshot/history tables or a uniqueness constraint on `opportunity_key`. Repeated keys are **expected** until the bridge loader is retired.

---

## Current read model

| View | Role |
|------|------|
| `api.v_equipment_opportunity` | **Base** read model: rows from the current canonical `equipment_opportunity_source` load |
| `api.v_equipment_opportunity_current` | **Current** API/dashboard view: canonical rows deduplicated to **one row per `opportunity_key`** |

Selection rules for `api.v_equipment_opportunity_current`:

1. Include only rows where `is_canonical_source = TRUE` (from the base view).
2. When multiple canonical rows share an `opportunity_key`, keep one row via `row_number()` ordered by `priority_rank`, `close_at`, `synced_at`, `opportunity_id`.

Historical or stale keys that appear only on non-canonical sources remain visible in **`v_equipment_opportunity_key_audit`**, not in the current API view. **`opportunity_key` is still not unique** at the table level; current selection is view-level read-model logic only.

No apps/api HTTP route currently queries `api.v_equipment_opportunity_current` — see the retirement note under **Strategic split** above.

---

## Source artifact metadata

`commercial.equipment_opportunity_source` stores both **internal path provenance** and **semantic source metadata**:

| Column | Role |
|--------|------|
| `csv_path`, `manifest_path`, `file_sha256`, `file_mtime` | Internal provenance for bridge loads and audit (paths remain in Postgres, not in public API JSON) |
| `source_kind` | Kind of source artifact (`typed_read_model` for live ChileCompra direct rows; `csv_artifact` for legacy bridge loads) |
| `artifact_basename` | Safe basename only — no parent directories |
| `canonical_reason` | Why this source was promoted (`chilecompra_api_direct_rows`, `manifest_canonical`, `resolved_active_current_queue`, etc.) |

`api.v_equipment_opportunity` exposes `source_kind`, `artifact_basename`, and `canonical_reason` alongside legacy `source_path` for compatibility.

**Contract guidance:** future API and read-model work should prefer `source_kind` + `artifact_basename` + `canonical_reason` over raw `csv_path` / filesystem paths. `source_path` in HTTP responses remains basename-redacted audit metadata, not business identity.

---

## Enforcement

Historically, `apps/api` had a dedicated Postgres/SQLite repository and route (`GET /opportunities/equipment`) enforcing "production must read `api.v_equipment_opportunity_current`, never fall back to CSV." That HTTP route and its repository were retired (dashboard now uses `GET /operator/procurement/status` / W1), so this enforcement no longer applies to `apps/api` — it has no equipment-specific code path left to enforce it on. The view and the writer pipelines above are unaffected and continue to run on schedule.

---

## Out of scope for this boundary doc

- Removing CSV generation from the email-pipeline (still required for operator audit and mirror ingest).
- Large schema migrations or retiring the bridge loader (follow-up work once read model is sole writer).
