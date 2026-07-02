# Equipment direct-row read model loader

Status: phase 3 direct publish path.

The production API reads `api.v_equipment_opportunity_current` from Postgres. The CSV queue is now an export/audit artifact and compatibility fallback, not the normal writer bridge for live ChileCompra equipment opportunities.

## Current live behavior

`auto-refresh-chilecompra-equipment --once --apply` now has the live path:

```text
ChileCompra API/detail builder rows
  -> normalized published equipment rows in memory
  -> apply_chilecompra_equipment_read_model(...)
  -> commercial.equipment_opportunity_source / commercial.equipment_opportunity
  -> api.v_equipment_opportunity_current
  -> API/dashboard/CLI
```

It still writes:

```text
ChileCompra API/detail builder rows
  -> API queue CSV
  -> canonical dashboard CSV export
  -> active/current manifest provenance
```

Those CSV files are retained for audit, debugging, and legacy/backfill compatibility.

The resulting source metadata is explicit:

```json
{
  "source_input": "typed_rows",
  "source_kind": "typed_read_model",
  "artifact_basename": "equipment_first_operator_queue_20260702.csv",
  "canonical_reason": "chilecompra_api_direct_rows"
}
```

## Live mirror behavior

`mirror-dashboard --live` no longer includes `--include-equipment-opportunities` by default. This prevents the live mirror loop from reopening the CSV export after `auto-refresh-chilecompra-equipment` already published the typed read model directly.

Warm cases, commercial deals, and operator snapshots still run through the live mirror loop.

## Legacy fallback

The old equipment CSV mirror path remains available when explicitly requested:

```bash
uv run origenlab mirror-dashboard --live --apply \
  --operator rafael \
  --reason legacy_equipment_backfill \
  -- --include-equipment-opportunities
```

At Python level, the rollback path is still:

```python
preview_load(active_current, use_direct_rows=False)
apply_load(pg_url, active_current, updated_by="op", reason="rollback", use_direct_rows=False)
```

That path keeps `source_kind=csv_artifact`.

## Target flow

The target flow is now implemented for the live ChileCompra refresh:

```text
equipment pipeline -> typed rows -> Postgres read model -> API/dashboard/CLI
                                -> CSV export/audit artifact
```

## Safety

The direct publisher writes only Postgres read-model tables when the existing ChileCompra auto-refresh command explicitly runs with `--apply` and Postgres is configured. It does not touch Gmail, SQLite, sending, drafts, archives, NDR handling, or dashboard/API mutation endpoints.
