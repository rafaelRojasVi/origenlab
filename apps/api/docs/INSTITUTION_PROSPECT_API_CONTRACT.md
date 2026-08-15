# Institution Prospect API contract (W1)

Frontend handoff for `apps/dashboard/src/api/institutionIntel/adapter.ts`. All
routes are **read-only**, backed by the published
`commercial_procurement_institution_prospects` planner bundle (see
[`API_RESPONSE_CONTRACT.md`](API_RESPONSE_CONTRACT.md) for the repo-wide
response envelope conventions this domain follows).

The frontend does not need to understand PR5C/PR5D/PR5E internals — everything
below is the stable surface. Raw status/reason tokens are returned exactly as
the planner emits them (snake_case English); **the frontend is responsible for
Spanish translation**, not the API.

## Routes

| Method | Path                                          | Purpose                                  |
| ------ | --------------------------------------------- | ----------------------------------------- |
| GET    | `/operator/procurement/status`                | Feed health + queue size counts           |
| GET    | `/operator/procurement/institutions`          | Paginated institution profile list        |
| GET    | `/operator/procurement/institutions/{institution_id}` | One institution profile (404 if genuinely absent on a healthy feed) |
| GET    | `/operator/procurement/queues/{queue_name}`   | One operator queue, paginated + filtered  |

`queue_name` is a strict enum (invalid values → `422`):

```
current_opportunity | historical_prospect | contact_gap
institution_match_review | line_evidence_review | retender_review
```

These are the API's route-facing short names; they map 1:1 onto the planner's
on-disk `*_queue` files but the raw filenames are never accepted or exposed.

## Query parameters

`institutions`:

| Param            | Type   | Default | Notes                                  |
| ----------------- | ------ | ------- | --------------------------------------- |
| `limit`           | int    | 50      | `1..500`, else `422`                    |
| `offset`          | int    | 0       | `>=0`                                   |
| `institution_id`  | string | —       | exact match                             |
| `q`               | string | —       | substring search: display_name / normalized_name / institution_id |

`queues/{queue_name}`:

| Param                    | Type   | Default | Notes                                             |
| ------------------------- | ------ | ------- | --------------------------------------------------- |
| `limit`                   | int    | 50      | `1..500`, else `422`                                |
| `offset`                  | int    | 0       | `>=0`                                                |
| `institution_id`          | string | —       | exact match                                          |
| `tender_code`             | string | —       | **case-insensitive** exact match (queue CSVs store lowercase codes, e.g. `745712-19-lp26`) |
| `equipment_category`      | string | —       | case-insensitive exact match                         |
| `commercial_signal_type`  | string | —       | case-insensitive exact match                         |
| `q`                       | string | —       | substring search: display_name / institution_id / tender_code |

A filter parameter that names a field the given queue doesn't have is simply a
no-op (never a 400/422) — "filtering selects existing rows only," it never
changes queue membership.

These parameter names are the ones W1 owns/finalizes per the frontend's
placeholder pagination controls; nothing else in the dashboard's generic
pagination UI needs to change to wire against this.

## Pagination envelope

Every list endpoint (`institutions`, `queues/{queue_name}`) returns:

```json
{
  "meta": { "...": "see Feed meta below" },
  "limit": 50,
  "offset": 0,
  "total": 14758,
  "count": 5,
  "items": [ "..." ]
}
```

- `total` = total matching rows after filters, before pagination.
- `count` = rows actually returned on this page (`len(items)`).
- Ordering is deterministic (institutions: sorted by `institution_id`; queue
  rows: whatever stable order the planner already sorted them in when
  published — see `queues.build_operator_queues`'s `_sort_rows`).

## Feed meta (`InstitutionProspectMeta`) — availability semantics

Every response (including `status`) carries a `meta` object:

```json
{
  "data_source": "institution_prospect_read_model",
  "read_only": true,
  "contract_version": "institution_prospect_contract_v4",
  "supported_contract_version": true,
  "planner_version": "procurement_institution_prospect_planner_v4",
  "recognition_layer_version": "procurement_prospect_recognition_pr5e2_v1",
  "as_of_utc": "2026-08-14T17:01:11Z",
  "run_context": "production_dry_run",
  "generated_at_utc": "2026-08-14T21:38:11+00:00",
  "source_digest": "1c42c27ae6f1dedbfbea5c9f2b06db7609464ec6ff5732674d34bd82a0fea2a5",
  "source_path": "institution_prospects_validation",
  "source_path_info": { "redacted": true, "basename": "institution_prospects_validation", "kind": "directory" },
  "source_kind": "institution_prospect_bundle",
  "artifact_basename": "institution_prospects_validation",
  "canonical_reason": "institution_prospect_read_model",
  "reduced_mode": false,
  "stale": false,
  "note": "",
  "not_persisted": true,
  "contact_authorization": false,
  "outreach_authorization": false
}
```

This is the **required distinction #2 from the frontend contract** — four
states, all driven by `meta.reduced_mode` + `meta.canonical_reason` +
`meta.stale`, never by an empty `items` array alone:

| State                          | `reduced_mode` | `canonical_reason`                        | `items`/`total` |
| ------------------------------- | -------------- | ------------------------------------------ | ---------------- |
| Unavailable / not published yet | `true`         | `missing_institution_prospect_packet`      | `[]` / `0`        |
| Unavailable — malformed bundle  | `true`         | `malformed_institution_prospect_packet`    | `[]` / `0`        |
| Unavailable — contract mismatch | `true`         | `unsupported_contract_version`             | `[]` / `0`        |
| Available, genuinely empty      | `false`        | `institution_prospect_read_model`          | `[]` / `0`        |
| Available with data             | `false`        | `institution_prospect_read_model`          | populated         |
| Available but stale             | `false`        | `institution_prospect_read_model`          | populated, **`stale: true`** (as_of_utc older than 48h) |

`note` is a free-text human explanation only set when `reduced_mode` is true.
`source_path`/`artifact_basename` are always redacted to a basename (never a
full filesystem path) via the same convention as `/opportunities/equipment`.

`contact_authorization` / `outreach_authorization` are **always `false`** at
every level (meta, institution item, queue row) — this API never grants
outreach permission; that remains a separate, unimplemented authorization
step. Frontend contract requirement #6 (verified contacts are rare) is a
data-truth property, not something this API alters.

## Institution list / detail item shape

`InstitutionProfileItem` — a near-verbatim projection of the planner's
profile dict (nothing is re-typed away or dropped):

```json
{
  "institution_id": "c1b951651a203b49d8a7e0fd03120b75a6271083488a7ad608b363d2a9766a4f",
  "identity": {
    "institution_id": "c1b951651a203b49d8a7e0fd03120b75a6271083488a7ad608b363d2a9766a4f",
    "identity_kind": "origenlab_account",
    "display_name": "INSTITUTO DE SALUD PUBLICA DE CHILE",
    "normalized_name": "instituto de salud publica de chile",
    "chilecompra_buyer_source_id": "7177",
    "account_id": "a_1c0889fb92f425fa7873591f5a7903bb",
    "account_resolution_status": "linked",
    "account_resolution_source": "pr4_linked_consistent",
    "account_resolution_reason": "pr4_linked_account_carried_forward",
    "linked_account_present": true,
    "identity_review_required": false,
    "aliases": ["INSTITUTO DE SALUD PUBLICA DE CHILE", "instituto de salud publica de chile"],
    "provenance": [ "...tender/identifier provenance entries..." ]
  },
  "account_contact_overlay": {
    "account_resolution_status": "linked",
    "account_resolution_source": "pr4_linked_consistent",
    "account_resolution_reason": "pr4_linked_account_carried_forward",
    "linked_account_present": true,
    "contact_resolution_status": "no_contact_found",
    "known_contact_count": 0,
    "suitable_contact_count": 0,
    "verified_contact_count": 0,
    "blocked_or_safety_unknown_count": 0,
    "selected_contact_present": false,
    "selected_contact_id": null,
    "contact_gap_status": "linked_account_no_contact",
    "contact_next_action": "none",
    "contact_authorization": false,
    "outreach_authorization": false
  },
  "axes": {
    "prospect_strength": { "axis": "prospect_strength", "band": "medium", "score": 4, "reason_codes": ["has_equipment_purchase_evidence", "open_purchase_tender_present", "recent_observation"] },
    "opportunity_urgency": { "axis": "opportunity_urgency", "band": "high", "score": 7, "reason_codes": ["projected_lifecycle_precedence_applied", "open_tender_present", "current_opportunity_like_evidence", "closing_soon"] },
    "contact_readiness": { "axis": "contact_readiness", "band": "low", "score": 1, "reason_codes": ["linked_account", "linked_account_no_contact"] }
  },
  "equipment_history": [ "...accumulated equipment-history entries..." ],
  "current_opportunities": [ "...tender-level dicts, see below..." ],
  "historical_signals": [ "...closed/non-open tender-level dicts..." ],
  "counts": {
    "tender_count": 26, "open_tender_count": 1, "historical_tender_count": 25,
    "equipment_purchase_tender_count": 1, "equipment_category_count": 1,
    "repeated_equipment_category_count": 0, "procurement_event_family_count": 26,
    "retender_review_tender_count": 0
  },
  "operator_next_action": "research_new_contact",
  "contact_authorization": false,
  "outreach_authorization": false,
  "not_persisted": true
}
```

**Requirement #1 (three independent axes)** is structural here: `axes` always
has exactly `prospect_strength`, `opportunity_urgency`, `contact_readiness` as
separate `{band, score, reason_codes}` objects. The API never combines them
into a single score — do not average/sum them client-side either.

**Requirement #5 (no institution↔contact fuzzy matching)**: `account_contact_overlay`
is exactly the existing `ContactOverlay` — `contact_gap_status`,
`known_contact_count`, `suitable_contact_count`, `verified_contact_count`,
`contact_next_action`, plus both authorization flags. No canonical
institution→contact identity mapping exists yet in the planner, so none is
invented here; `selected_contact_id`/`selected_contact_present` reflect only
what the planner already resolved (usually `null`/`false`, matching
requirement #6 — this is real, not a placeholder gap).

Each entry in `current_opportunities` / `historical_signals` carries (at
least): `tender_code`, `coalesced_tender_id`, `canonical_equipment_category`,
`procurement_method`, `procurement_method_details`,
`procurement_eligibility_status`, `lifecycle_class`, `reason_codes`,
`close_timestamp`, `publication_timestamp` — this is where SAG/ISP's raw
method/eligibility tokens live for a profile-level view (vs. the flatter
queue-row view below).

## `/status` response shape

```json
{
  "meta": { "...as above..." },
  "counts": { "...full planner counts dict, e.g. commercial_signal_counts, review_disposition_counts, projected_lifecycle_counts, etc..." },
  "operator_queue_sizes": {
    "current_opportunity_queue": 3,
    "historical_prospect_queue": 764,
    "institution_match_review_queue": 64,
    "contact_gap_queue": 547,
    "line_evidence_review_queue": 14758,
    "retender_review_queue": 684
  },
  "summary_ok": true
}
```

`operator_queue_sizes` and `counts` come straight from the planner's own
`summary.json` (falling back to counting the loaded queue rows if `summary.json`
itself is absent from the bundle) — the API never recomputes them.

## Queue row shape

Row shape genuinely varies by queue (see
`commercial_procurement_institution_prospects.queues.EMPTY_QUEUE_HEADERS` for
the authoritative per-queue field list); rows are returned as decoded JSON
objects, not re-declared as six parallel schemas, so a field the planner adds
is never silently dropped ahead of a contract update. Real
`current_opportunity` row (SAG, `745712-19-LP26`, balance category):

```json
{
  "institution_id": "8f031dec655d2a131b0dbdf9685d10e12e208aecc5b88d83d4c019a1b5461026",
  "display_name": "SERVICIO AGRICOLA Y GANADERO",
  "tender_code": "745712-19-lp26",
  "coalesced_tender_id": "coalesced_tender_805ad917bdefacd150bd5fa5",
  "equipment_category": "balance",
  "lifecycle_class": "active_open",
  "review_disposition": "catalog_fit_candidate",
  "commercial_signal_type": "equipment_purchase_signal",
  "catalog_fit_status": "catalog_fit_candidate",
  "catalog_match_status": "catalog_equipment_class_needs_confirmation",
  "opportunity_urgency_band": "high",
  "prospect_strength_band": "high",
  "line_evidence_unit_count": "9",
  "closing_soon_bucket": "gt_7d",
  "publication_timestamp": "2026-08-04T11:23:31.767",
  "close_timestamp": "2026-08-24T19:00:00",
  "reason_codes": ["verified_catalog_class_and_purchase_signal"],
  "eligibility_reason_codes": [
    "newest_acquisition_age_hours=0.0964",
    "current_open_with_future_close",
    "status_provenance=current_authoritative_snapshot",
    "close_provenance=current_authoritative_snapshot",
    "preserve_known_lifecycle"
  ],
  "queue": "current_opportunity_queue",
  "contact_authorization": false,
  "outreach_authorization": false,
  "queue_row_id": "6cedc739a8ea98244c5d2f3931a97fdc"
}
```

Note `tender_code` is lowercase in the published CSV (`745712-19-lp26`); the
`tender_code` query filter matches case-insensitively so a caller can pass the
human-displayed uppercase form.

**ISP `1093303-5-CO26`** (`GET /operator/procurement/queues/current_opportunity?tender_code=1093303-5-CO26`)
returns `total: 0, items: []` — the tender is restricted
(`procurement_eligibility_status: restricted_invitation_unconfirmed`) and is
correctly absent from the actionable queue, while still visible via
`GET /operator/procurement/institutions/{isp_institution_id}` →
`current_opportunities[].procurement_method == "CO"`,
`procurement_eligibility_status == "restricted_invitation_unconfirmed"`. This
is the concrete "visible in profile, excluded from actionable queue" case
requirement #2/#8 describe.

## Evidence/provenance projection (requirement #4)

No dedicated `/evidence` endpoint exists in W1. Provenance is carried inline
today via `identity.provenance` (tender/identifier evidence entries — source
kind + tender/identifier key, no raw document bodies) and per-opportunity
`reason_codes`/`eligibility_reason_codes` (machine-readable tokens, not
excerpts). No Gmail body text, portal tokens, or secrets are ever included —
`path_redaction` strips absolute filesystem paths from `meta`, and nothing in
the planner output includes raw email content. If/when annex (T1)
excerpt+locator evidence is exposed, it should follow the same
`{excerpt, document/source identity, locator}` shape requirement #4 specifies,
added as new, additive, optional fields — not built in this PR (see next
section).

## T1 / ANEXO status (requirement #8)

`enable_annex_opportunity_evidence` is untouched by this PR — still defaults
off, no annex acquisition was triggered. Nothing in this contract currently
exposes annex-derived fields (the validated bundle has none), so there is
nothing to mark `unavailable` vs `available` yet; the meta/queue-row shapes
above have room to add such fields additively later without a breaking
change, per the frontend's `AvailabilityBlock` semantics.

## Example: `/status` meta as returned in read-only smoke validation

```json
{
  "reduced_mode": false,
  "contract_version": "institution_prospect_contract_v4",
  "as_of_utc": "2026-08-14T17:01:11Z",
  "operator_queue_sizes": {
    "current_opportunity_queue": 3,
    "historical_prospect_queue": 764,
    "institution_match_review_queue": 64,
    "contact_gap_queue": 547,
    "line_evidence_review_queue": 14758,
    "retender_review_queue": 684
  }
}
```

`current_opportunity_queue` = 3 rows across 2 unique tenders
(`4291-46-le26`, `745712-19-lp26`) — SAG contributes 2 rows (balance +
centrifuge), matching the PR3 lifecycle-hotfix cached-live validation exactly.
