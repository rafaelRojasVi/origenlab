# Commercial procurement candidate planner — PR5C coalescence / lifecycle slice

**Status:** Draft PR checkpoint (coalescence + lifecycle only)
**Milestone:** **PR5C** — deterministic candidate planner
**This slice:** source coalescence and lifecycle (not a separate roadmap PR id)
**Branch:** `feat/commercial-procurement-coalescence-lifecycle-pr5c`
**Base SHA:** `9c78f8a0948e08466f8c09f51ed0ee16b13b2946` (PR5B.1 merge #423)

This document does **not** authorize product relevance, account/contact resolution,
persistence / `--apply`, scheduling, Ticket/OCDS acquisition, or PR5D+.

## 1. Objective

Consume:

| Plane | Input |
|-------|--------|
| **A** | Persisted PR4 `commercial_procurement_*` (read-only) |
| **B** | One or more serialized PR5B `AcquisitionSnapshot` JSON files |

Produce validated canonical tender identities, deterministic coalescence,
selected-field provenance, freshness/currentness, lifecycle, closing-soon
buckets, bounded conflicts, unresolved evidence, fingerprints, and a redacted
walkthrough.

**Explicitly not in this slice:** product/equipment relevance, taxonomy,
keyword/AI classification, account/contact resolution, outreach outcomes,
persistence, network acquisition.

## 2. Canonical identity

A Plane B observation enters coalescence only when:

- `canonical_tender_key_candidate` is non-null;
- `canonical_candidate_kind == mercado_publico_codigo_externo`;
- the value passes `is_mercado_publico_codigo_shape` after
  `normalize_mercado_publico_codigo`.

Never used as canonical keys: OCID alone, release id, line id, source record id,
title, buyer name, fuzzy similarity.

Rejected observations become `UnresolvedProcurementEvidence` with bounded
reasons (`live_canonical_candidate_missing`, `…_malformed`,
`ocds_ocid_only_unresolved`, `source_native_identity_not_canonical`,
`unsupported_candidate_kind`, `incomplete_or_failed_page`).

## 3. Field precedence (no global best source)

See emitted `FIELD_PRECEDENCE_MATRIX.json`. High→low for detailed fields:

`ticket_detail` → `ticket_summary` → `ocds_release` → `ocds_record` → `pr4` →
`ocds_lista_index`

Lista-index stubs cannot override status/dates/buyer/title. Package
`creationDate` is never tender publication. File mtime / build time are never
acquisition provenance. Contradictions become conflicts.

## 4. Freshness and lifecycle

- CLI `--as-of-utc` (timezone-aware) is the sole wall for semantic decisions.
- Acquisition timestamps: `AcquisitionPage.acquired_at_utc` only (aware UTC;
  not future relative to as-of).
- Naive ChileCompra tender timestamps: America/Santiago (PR5A/equipment policy).
- `active_open` requires `current_authoritative_snapshot`, open/publicada status,
  and close **strictly after** as-of.
- Closing-soon buckets only for `active_open`: `lt_24h`, `d1_to_d3`, `d4_to_d7`,
  `gt_7d`; else `not_applicable`.

## 5. Fingerprints

| Algorithm | Covers |
|-----------|--------|
| `candidate_input_source_fp_v1` | PR4 deps + acquisition snapshot digests + accepted/unresolved identities |
| `candidate_build_plan_fp_v1` | input FP + as-of + freshness hours + planner/lifecycle/coalescence versions |
| `candidate_semantic_digest_v1` | order-independent coalesced / evidence / unresolved / conflicts |

## 6. CLI

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_commercial_procurement_candidate_plan.py \
  --sqlite-path /path/to/emails.sqlite \
  --acquisition-snapshot-json snap1.json \
  --acquisition-snapshot-json snap2.json \
  --as-of-utc 2026-08-01T19:00:30Z \
  --freshness-threshold-hours 48 \
  --out-dir reports/out/.../candidate_plan/ \
  --run-context production_dry_run
```

Rejects `--apply`, `--persist`, `--network`, `--ticket`, `--gmail`,
`--postgres`, `--outreach`, `--schedule`.

## 7. Production read-only checkpoint (2026-08-01)

SQLite opened `mode=ro` + `query_only=ON`. No DDL/DML. No network.

| Input | Value |
|-------|-------|
| PR4 signals | 16448 |
| PR4 accepted into coalescence | 14690 |
| Acquisition snapshots | ticket detail + OCDS lista (sanitized fixtures via PR5B path) |
| Coalesced tenders | 14690 |
| Unresolved | 1 (lista OCID-only) |
| Conflicts | 4 |
| `candidate_source_kind` | pr4=14689, both=1, live_snapshot=0 |
| Lifecycle | awarded=5709, cancelled=2261, closed=6719, status_conflict=1 |
| active_open | 0 |

Fingerprints:

- input: `6a2a873bfce993ccd12db86cc1a4fda4c25388482a32668b0253e9e3c4aa4e71`
- build-plan: `780d3057d3f3b6e2df265c19ff73770e2555c8c8b03de5ae3e4d4ecd6fb102d3`
- semantic: `82da570c75ef6187fe47c10907705a5d63938fa683073b33637d272edfba0b38`

## 8. Walkthrough cases

| Case | Intent |
|------|--------|
| A | PR4-only historical (production-derived, redacted) |
| B | Live-only Ticket via sanitized fixture + PR5B parser |
| C | Two-plane agreement — `synthetic_overlap_through_production_code_path` unless real overlap |
| D | Status/date/buyer conflict — synthetic through production path when needed |
| E | OCDS/lista without MP canonical → unresolved, not a candidate |

## 9. Next boundary

Still within **PR5C**: deterministic product relevance classification.

Not started: **PR5D** persistence, **PR5E** scheduling, **PR6** contacts,
**PR7** API/dashboard.
