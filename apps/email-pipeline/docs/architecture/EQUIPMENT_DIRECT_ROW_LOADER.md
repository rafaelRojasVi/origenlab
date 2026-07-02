# Equipment direct-row read model loader

Status: phase 1 implementation for retiring the CSV bridge.

The existing production API already reads `api.v_equipment_opportunity_current` from Postgres. The remaining CSV dependency is upstream: the DB-2 equipment loader historically re-opened `equipment_first_operator_queue_*.csv` and inserted those rows into `commercial.equipment_opportunity*`.

This phase adds `origenlab_email_pipeline.equipment_opportunity_read_model_loader`, which accepts already-normalized equipment rows in memory and writes them directly to the typed Postgres read model tables.

## What this changes

- Adds `preview_direct_rows_load(...)` and `apply_direct_rows_load(...)`.
- Uses `source_input: typed_rows` in summaries.
- Stores semantic provenance with `source_kind: typed_read_model`.
- Reuses the existing `commercial.equipment_opportunity_source` and `commercial.equipment_opportunity` tables.
- Preserves canonical-source semantics: the direct source is promoted as the current equipment read model source.

## What this does not change yet

- It does not remove CSV exports.
- It does not remove the legacy CSV loader.
- It does not automatically change the cron schedule.
- It does not alter API/dashboard routes.

## Why this is the correct next step

The old bridge was:

```text
equipment pipeline -> CSV artifact -> CSV loader -> Postgres read model -> API/dashboard/CLI
```

The target flow is:

```text
equipment pipeline -> typed rows -> Postgres read model -> API/dashboard/CLI
                                -> CSV export/audit artifact
```

This PR implements the direct typed-row loader needed for that target while keeping the existing CSV path available as a rollback/export path.

## Safety

The direct loader writes only Postgres read-model tables. It does not touch Gmail, SQLite, sending, drafts, archives, NDR handling, ChileCompra network calls, or dashboard/API mutation endpoints.
