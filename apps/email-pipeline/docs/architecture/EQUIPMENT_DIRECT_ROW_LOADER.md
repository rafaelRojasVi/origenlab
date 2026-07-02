# Equipment direct-row read model loader

Status: phase 2 wiring for retiring the CSV bridge.

The production API reads `api.v_equipment_opportunity_current` from Postgres. The remaining CSV dependency was upstream: the DB-2 equipment mirror historically re-opened `equipment_first_operator_queue_*.csv` and inserted those rows into `commercial.equipment_opportunity*` with `source_kind=csv_artifact`.

`origenlab_email_pipeline.equipment_opportunity_read_model_loader` accepts normalized equipment rows in memory and writes them directly to the typed Postgres read-model tables.

## Current behavior

The compatibility entrypoint `equipment_opportunity_mirror.preview_load/apply_load` still accepts the canonical active/current queue artifact, but now defaults to the typed-row writer:

```text
equipment_first_operator_queue_*.csv export
  -> load rows in memory
  -> apply_direct_rows_load(...)
  -> commercial.equipment_opportunity_source / commercial.equipment_opportunity
  -> api.v_equipment_opportunity_current
```

The resulting source metadata is explicit:

```json
{
  "source_input": "typed_rows",
  "source_kind": "typed_read_model",
  "artifact_basename": "equipment_first_operator_queue_20260702.csv",
  "canonical_reason": "typed_rows_from_manifest_canonical",
  "legacy_csv_loader_used": false
}
```

The exported CSV remains as an audit/export artifact and backward-compatible input. It is no longer the default SQL writer semantics for the DB-2 equipment mirror.

## Legacy fallback

The old CSV SQL loader is still available for rollback/tests:

```python
preview_load(active_current, use_direct_rows=False)
apply_load(pg_url, active_current, updated_by="op", reason="rollback", use_direct_rows=False)
```

That path keeps `source_kind=csv_artifact`.

## Target flow

The end-state remains:

```text
equipment pipeline -> typed rows -> Postgres read model -> API/dashboard/CLI
                                -> CSV export/audit artifact
```

This PR wires the mirror entrypoint to the typed-row writer while preserving the CSV export and legacy fallback. A later cleanup can pass the ChileCompra builder rows directly without opening the CSV export at all.

## Safety

The direct loader writes only Postgres read-model tables when the existing mirror/apply command explicitly runs. It does not touch Gmail, SQLite, sending, drafts, archives, NDR handling, ChileCompra network calls, or dashboard/API mutation endpoints.
