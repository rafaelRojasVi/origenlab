# PR5D-H3 — Canonical equipment recognition coverage

## Purpose

Separate **canonical equipment recognition** from **OrigenLab catalog capability**.

PR5D already models both layers. H3 only closes high-confidence recognition gaps
so shadow prospecting (ANEXO-P1) can distinguish:

- real equipment outside the recorded catalog
- from no recognized equipment / incomplete evidence

H3 does **not** expand catalog capability, mutate queues, authorize contact, or
start PR5F.

## Versioning

| constant | value |
|----------|--------|
| `PRODUCT_RELEVANCE_RULES_VERSION` | `procurement_product_relevance_rules_v4` |
| `PRODUCT_RELEVANCE_TAXONOMY_VERSION` | `procurement_product_relevance_taxonomy_v1` (unchanged) |
| aggregation policy | `aggregation_policy_v3` (unchanged) |

`autoclave` already existed in `CANONICAL_EQUIPMENT_CLASSES`.

## Implemented in this phase

### Autoclave

- Detector: `\bautoclaves?\b` on normalized text
- Accessory veto: headed `repuesto/accesorio para autoclave` → consumable (not complete equipment)
- Existing H1 gates still apply: service/maintenance, supplier-required, purchase overrides

Central contract for `986278-12-LE26`:

| layer | result |
|-------|--------|
| recognition | `autoclave` |
| catalog | `outside_recorded_catalog` |
| sellable current opportunity queue | **no** |
| coverage debt (`needs_ocr`) | **retained** |

## Explicitly not implemented

| class | reason |
|-------|--------|
| `chromatography_hplc` | corpus HPLC spans are method language |
| `pipette` | tips / Pasteur / serological / graduated labware |
| `lyophilizer` | `liofilizado` adjective on reagents/calibrators |
| `reactor` | catalog-verified commercially important, but no deferred-52 gold instrument hits |
| `osmometer` | commercially important; no deferred-52 gold hits → requires more gold |
| `spectrophotometer` | no deferred-52 instrument hits; avoid method confusion |
| `ph_meter` / `plate_reader` / `oven_muffle` / `titrator` | no safe gold yet |

## Review packet (gitignored)

```text
reports/out/active/current/pr5d_h3_recognition_coverage/
  coverage_matrix.json
  coverage_matrix.csv
  positive_examples.jsonl
  negative_controls.jsonl
  frozen52_delta.json
  walkthrough.md
```

Build:

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_pr5d_h3_recognition_coverage.py
```

## Safety

- No catalog seed changes
- No capability expansion
- No annex production integration
- No queue mutation / authorization / persistence / PR5F
