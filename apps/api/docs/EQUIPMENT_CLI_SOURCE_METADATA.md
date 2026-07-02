# Equipment API source metadata for CLI clients

`GET /opportunities/equipment` is a read-only operator endpoint. It must be safe for dashboard and future CLI use: clients should not infer business truth from filenames or local paths.

## Production contract

In production, `ORIGENLAB_API_BACKEND=postgres` is required. The endpoint reads `api.v_equipment_opportunity_current` through `PostgresEquipmentOpportunityRepository`; it does not open `active/current` CSV files.

```json
{
  "meta": {
    "data_source": "postgres_mirror",
    "read_only": true,
    "source_kind": "csv_artifact",
    "artifact_basename": "equipment_first_operator_queue_20260702.csv",
    "canonical_reason": "manifest_canonical",
    "source_path": "equipment_first_operator_queue_20260702.csv"
  },
  "items": []
}
```

`source_kind`, `artifact_basename`, and `canonical_reason` are semantic provenance fields copied from the Postgres read model. Prefer these over `source_path` in CLI output.

## Local/CI fallback

The SQLite backend is local-dev only and may load the canonical active-current CSV queue directly. That fallback now exposes the same metadata shape:

```json
{
  "meta": {
    "data_source": "active_current_csv",
    "read_only": true,
    "source_kind": "csv_artifact",
    "artifact_basename": "equipment_first_operator_queue_20260702.csv",
    "canonical_reason": "active_current_csv_fallback"
  },
  "items": []
}
```

## CLI guidance

For a future CLI:

- branch on `meta.data_source` to distinguish production Postgres mirror from local CSV fallback;
- display `meta.artifact_basename` only as provenance/audit context;
- never treat `source_path` or a CSV filename as the opportunity identity;
- use `item.opportunity_key` as the cross-source correlation id;
- show `meta.canonical_reason` when explaining why a source was selected;
- keep all commands read-only unless they explicitly call email-pipeline CLIs outside the API.

CSV remains an input/audit artifact during the bridge. The public production API contract is the typed Postgres read model, not the CSV file.
