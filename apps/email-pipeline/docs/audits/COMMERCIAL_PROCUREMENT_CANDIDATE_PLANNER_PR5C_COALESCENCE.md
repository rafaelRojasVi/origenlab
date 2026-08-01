# Commercial procurement candidate planner — PR5C coalescence / lifecycle slice

**Status:** Draft PR checkpoint (coalescence + lifecycle only; identity/lifecycle correction)
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

### Plane A (PR4)

PR4 is the canonical persisted Plane A contract. For verified PR4
`tender_key_kind` values (`codigo_externo`, `codigo_licitacion`,
`numero_adquisicion`), preserve the PR4 `canonical_tender_key` after bounded
whitespace/case normalization. **Do not** require the stricter PR5B Mercado
Público `CodigoExterno` cross-source regex.

Separate:

1. **Plane A canonical identity** — persisted PR4 key + original
   `tender_key_kind`;
2. **Cross-source join eligibility** — whether the key matches exact
   `mercado_publico_codigo_externo` shape for Plane B joins.

Non-shape-compatible verified PR4 keys remain valid **`pr4_only`** tenders.
They are never silently discarded and never falsely relabelled as
`mercado_publico_codigo_externo` for kind preservation (except when
cross-source eligible, coalesced identity uses the MP kind so PR4-only → both
keeps a stable `coalesced_tender_id`).

Corrupt PR4 rows become typed unresolved evidence:

- `pr4_canonical_key_missing`
- `pr4_tender_key_kind_unsupported`
- `pr4_canonical_identity_corrupt`

**Reconciliation:** `PR4 total = PR4 coalesced + PR4 unresolved` (no silent drop).

### Plane B (acquisition)

A live observation enters coalescence only when:

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

## 3. Stable coalesced tender ID

`coalesced_tender_id` derives only from:

- ID algorithm/version (`coalesced_tender_id_v1`);
- `canonical_tender_key`;
- `tender_key_kind`.

It does **not** include evidence refs, snapshot IDs, acquisition timestamps,
selected fields, lifecycle, or as-of. Adding/removing/refreshing evidence for
the same identity must not change the ID.

## 4. Field precedence (no global best source)

See emitted `FIELD_PRECEDENCE_MATRIX.json`. High→low for detailed fields:

`ticket_detail` → `ticket_summary` → `ocds_release` → `ocds_record` → `pr4` →
`ocds_lista_index`

Status **code and name are selected atomically** from the same evidence ref.
Internally inconsistent code/name pairs (e.g. code `8` + Publicada) emit
`status_conflict`. ChileCompra legal-citation suffixes on names (e.g.
`Desierta (o art. …)`) match the expected family via prefix.

Timestamps are parsed through the documented policy and compared as normalized
UTC instants before conflict. Reason codes distinguish
`close_timestamp_conflict` vs `publication_timestamp_conflict` under parent
`date_conflict`.

Lista-index stubs cannot override status/dates/buyer/title. Package
`creationDate` is never tender publication. File mtime / build time are never
acquisition provenance.

## 5. Freshness and lifecycle

- CLI `--as-of-utc` (timezone-aware) is the sole wall for semantic decisions.
- `freshness_threshold_hours` must be `> 0`.
- Acquisition timestamps: `AcquisitionPage.acquired_at_utc` only (aware UTC;
  not future relative to as-of).
- Naive ChileCompra tender timestamps: America/Santiago (PR5A/equipment policy).

### `active_open` field provenance

Requires:

1. selected status evidence from an acquisition evidence ref;
2. that status evidence acquisition timestamp within freshness threshold;
3. selected close evidence from an acquisition evidence ref;
4. that close evidence acquisition timestamp within freshness threshold;
5. both refs source-capable for their fields;
6. no unresolved status conflict;
7. no unresolved close-date conflict;
8. close strictly after as-of.

Status and close may come from different live refs when both are independently
current. A fresh lista stub or buyer-only live ref cannot freshen PR4 lifecycle
fields.

Provenance outputs: `lifecycle_status_evidence_ref_id`,
`lifecycle_close_evidence_ref_id`, `lifecycle_publication_evidence_ref_id`,
`lifecycle_evidence_currentness_class`.

Close-date conflict → never `active_open` (`status_unknown` +
`authoritative_close_date_conflict`). Publication-date conflict blocks
`future_scheduled` when publication timing is required. Buyer conflict does not
alone alter lifecycle.

Terminal PR4 historical statuses may remain awarded/closed/cancelled without a
fresh live snapshot; they remain historical evidence, not currently verified.

Closing-soon buckets only for `active_open`: `lt_24h`, `d1_to_d3`, `d4_to_d7`,
`gt_7d`; else `not_applicable`.

## 6. Report output safety

Before any output file is created, the destination must resolve under the
repository’s ignored `apps/email-pipeline/reports/out/` tree. Path traversal and
symlink escape are rejected. Direct calls to `write_plan_outputs` /
`write_walkthrough` cannot bypass this check. Do not modify `.gitignore`.

## 7. Fingerprints

| Algorithm | Covers |
|-----------|--------|
| `candidate_input_source_fp_v1` | PR4 deps + acquisition snapshot digests + accepted/unresolved identities |
| `candidate_build_plan_fp_v1` | input FP + as-of + freshness hours + planner/lifecycle/coalescence versions |
| `candidate_semantic_digest_v1` | order-independent coalesced / evidence / unresolved / conflicts |

## 8. CLI

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

## 9. Production-derived checkpoint (corrected)

**Checkpoint composition:** production PR4 read-only data **+** committed
live-derived sanitized acquisition fixtures. **Not** a fresh production
acquisition run.

SQLite opened `mode=ro` + `query_only=ON`. No DDL/DML. No network.

### Former vs corrected PR4 accounting

| Metric | Former (flawed) | Corrected |
|--------|-----------------|-----------|
| PR4 signals | 16448 | 16448 |
| PR4 coalesced | 14690 | **16448** |
| PR4 silently skipped | **1758** | **0** |
| PR4 typed unresolved | n/a | **0** |

Redacted gap diagnosis (all 16448): `tender_key_kind=codigo_externo`, nonempty,
3 hyphen parts. **14690** match exact MP `CodigoExterno` regex; **1758** fail
only because suffix digit length is **3** (`letters_1_digits_3`), not the
stricter `\d{2}`. They are valid Plane A `pr4_only` tenders
(`tender_key_kind=codigo_externo`), not unresolved.

### Corrected production-derived counts

| Input / output | Value |
|----------------|-------|
| Acquisition snapshots | same prior ticket detail + OCDS lista sanitized fixtures |
| Coalesced tenders | **16448** |
| PR4 unresolved | **0** |
| Live unresolved | **1** (lista OCID-only) |
| Conflicts | **3** (status=1, close date=1, buyer=1) |
| `candidate_source_kind` | pr4=16447, both=1, live_snapshot=0 |
| Lifecycle | awarded=6584, cancelled=2655, closed=7208, status_conflict=1 |
| active_open | 0 |
| no_silent_drop | true |

Fingerprints (changed with corrected semantics — expected):

- input: `5915fe6c4633b0a31a775547c047f1807dd59887dded9f4425669f088145f947`
- build-plan: `66b2e7707e7f0b2ced464ea93ebf3c9ec1c5afcd5d5acb6efde223a1be6dc529`
- semantic: `6afde87d765784405b86acab2cd5eda61bd20d584f8292d66d8a3874a3e8af8d`

Former fingerprints (obsolete): input `6a2a873b…` · build `780d3057…` ·
semantic `82da570c…`.

## 10. Walkthrough cases

| Case | Intent |
|------|--------|
| A | PR4-only historical (production-derived, redacted) |
| B | Live-only Ticket via sanitized fixture + PR5B parser |
| C | Two-plane agreement — `synthetic_overlap_through_production_code_path` unless real overlap |
| D | Status/date/buyer conflict — synthetic through production path when needed |
| E | OCDS/lista without MP canonical → unresolved, not a candidate |

## 11. Next boundary

Still within **PR5C**: deterministic product relevance classification.

Not started: **PR5D** persistence, **PR5E** scheduling, **PR6** contacts,
**PR7** API/dashboard.
