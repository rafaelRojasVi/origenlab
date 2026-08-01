# Commercial procurement acquisition — PR5B

**Status:** Acquisition-contract correction pass (draft)
**Branch:** `feat/commercial-procurement-acquisition-pr5b`
**Base:** `9a78aebd06a8065e249284ae54fabdd46ff8069c` (PR5A merge)

This document does **not** authorize authenticated Mercado Público requests,
production `--apply`, PR5 candidate planning, relevance classification,
account/contact resolution, scheduling changes, or PR5C.

## 1. Objective

Establish a versioned, source-neutral **acquisition snapshot contract** and
deterministic parsers for:

| Lane | Endpoint kind | Role |
|------|---------------|------|
| A | `ticket_licitaciones_summary` | Active discovery (`estado=activas`) |
| B | `ticket_licitacion_detail` | Code detail + items |
| C | `ocds_lista_agno_mes_range` | Official OCDS monthly range (≤1000/page) |

Bulk official downloads remain the PR4 historical/backfill lane.

## 2. Existing operational flow (unchanged)

```
ticket API
    ↓
summary keyword prefilter
    ↓
bounded detail lookups / detail cache
    ↓
equipment-first classifier
    ↓
CSV / manifest publication
    ↓
typed equipment opportunity publication
```

That flow is a **consumer** of source data. It is **not** the PR5B acquisition
snapshot contract. Auto-refresh is **not** routed through PR5B in this PR.

Compatibility adapter maps acquisition tender/line observations → existing
`CHILECOMPRA_NORMALIZED_FIELDS` without equipment classification.

## 3. Package

`src/origenlab_email_pipeline/commercial_procurement_acquisition/`

Contract versions:

- `commercial_procurement_acquisition_v1`
- `procurement_acquisition_parser_v1`
- `acquisition_run_v1`
- fingerprint algorithms: `acquisition_source_fingerprint_v1`,
  `acquisition_normalized_semantic_digest_v1`,
  `acquisition_run_source_fingerprint_v1`
- raw digest: `sha256_canonical_json_v1`

Grains:

- **AcquisitionSnapshot** — one sanitized query scope
- **AcquisitionRun** — composite of child snapshots / detail attempts
- Response page → Source observation → Tender / Line observation

### Identity (correction)

| Concept | Meaning |
|---------|---------|
| `source_native_tender_key` | Source-qualified (e.g. `ticket_api:codigo_externo:…`, `ocds:ocid|release|tender|kind`) |
| `canonical_tender_key_candidate` | Source-neutral normalized Mercado Público CodigoExterno **without** `ticket:` / `ocds-tender:` prefixes |
| `canonical_candidate_kind` / `reason` | Why a candidate was / was not emitted |

OCDS emits a Mercado Público canonical candidate only when `tender.id` matches
the documented CodigoExterno shape (or an explicit mapping exists). Ocid-only
releases remain unresolved.

### Records policy B (OCDS)

Historical releases preferred. `compiledRelease` emitted only when its
`(ocid, release.id)` is not already among historical releases. Provenance
retains `record_id`, `release_kind`, tags, procurement method, classifications,
and related processes as evidence only (not PR5 eligibility).

### Ticket detail statuses

`complete` | `malformed_response` | `detail_empty` | `detail_multiple_results` |
`detail_code_mismatch` | `source_total_mismatch`

### Monthly OCDS statuses

`complete` | `terminal_empty_page` | `incomplete_range` | `duplicate_page` |
`overlapping_range` | `source_total_mismatch` | `malformed_response` |
`partial_page_failure`

Single-page empty responses use neutral `empty_page` (not terminal).

Cross-source coalescence is **deferred to PR5C**.

## 4. Fixture origin vs completeness

Committed fixtures use `fixture_origin=synthetic_official_shape`.

`fixture_origin` is **not** mixed into page/snapshot completeness. Content
completeness statuses describe parse/assembly outcome only.

## 5. Data walkthrough (Cases A–E)

Generated (gitignored) report example:

`reports/out/pr5b_walkthrough_correction/`

### Case A — Ticket summary

Source-native vs canonical tender identity; malformed payloads fingerprint
actual JSON input (not error text).

### Case B — Ticket detail

Code-detail query identity; multiple stable line IDs; compatibility adapter
equals existing normalizer on locked fields.

### Case C — OCDS

Records policy B provenance; procurementMethod; additional classifications;
related processes; monthly page/range assembler.

### Case D — AcquisitionRun

One summary child + detail A success + detail B failed attempt with its **own**
`build_ticket_detail_query(tender_code=B)` query ID. Partial run completeness;
successful evidence retained.

### Case E — Cross-source canonical candidate

Parser-emitted equal candidates: `9999-1-le26` from Ticket CodigoExterno and
OCDS `tender.id`. Source-native keys remain distinct. **No coalescence.**

### Fingerprint values (walkthrough fixtures — correction pass)

| Case | Fingerprint key | Value |
|------|-----------------|-------|
| A | source | `985b3ea0834198c9c008e4026e814d3807981a40d6bd8a9b67b6003e2ee33cdd` |
| A | semantic | `038fd66b7dbd71af84b6283ab71489810dc1ae9e86d127f48114c2c27d55a010` |
| A | malformed_raw | `da4030d94d7e021763a73475c7e0f434f7ce361882886e3c02ca576b275047d5` |
| B | source | `c09e386252bfc97b591b62b515b0669995b916b07ea6ac03e497e2adceb07065` |
| B | semantic | `9fc4a53a1308c240518da5b82fd5a93902889c91e1421447dd67d12981ffe0e9` |
| C | monthly_source | `dba6adcead5268cdbd7aafe83aadfec2e153441e6310ef288a5d430d6581e572` |
| C | monthly_semantic | `c879be2b7b595084ce28001238a24b92bd99989bf52a222f09880c3c29485e35` |
| C | records_source | `08ccedd2361724cc77eec7dd3a22e6d07516d5ca07a23c36230b02fe6a18a9f4` |
| C | records_semantic | `045eaa041f7086f3643d80c2b811c0717a76669e0d58bb091632eae465d46008` |
| C | single_source | `82c94c9b222bd990014da9622e808ed5040ccae3c60c8e2d00bbaf4d068b0884` |
| D | run_source | `d77de0bde939a35934afff0144e2b79c36539491b09997e2f12077929b83587e` |
| D | summary_source | `985b3ea0834198c9c008e4026e814d3807981a40d6bd8a9b67b6003e2ee33cdd` |
| D | detail_a_source | `c09e386252bfc97b591b62b515b0669995b916b07ea6ac03e497e2adceb07065` |
| E | shared candidate | `9999-1-le26` (parser-equal; coalesced=false) |

## 6. Safety

- `authenticated_request_performed=false`
- `ticket_configured` (boolean only via shared helper)
- `ticket_used_for_request=false`
- `ticket_persisted=false`
- `ticket_logged=false`
- No `ticket_value_accessed` field (removed as inaccurate)
- CLI rejects `--network` / `--apply` / `--ticket`
- `AcquisitionQuery.extra` removed from v1
- No production SQLite mutation; no PR2/PR3/PR4 mutation
- No Gmail / Postgres / dashboard / outreach / scheduler changes
- Reports remain gitignored

## 7. PR5C boundary

PR5C may consume these snapshots for deterministic candidate planning and
cross-source coalescence. PR5B does **not** emit relevance, account, contact,
or outreach outcomes.

## 8. CLI

```bash
uv run python scripts/commercial/build_commercial_procurement_acquisition_snapshot.py \
  --source-kind walkthrough \
  --out-dir reports/out/pr5b_walkthrough_correction/
```
