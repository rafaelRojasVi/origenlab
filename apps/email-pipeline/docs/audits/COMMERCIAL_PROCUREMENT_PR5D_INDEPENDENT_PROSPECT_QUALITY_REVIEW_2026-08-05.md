# COMMERCIAL_PROCUREMENT_PR5D_INDEPENDENT_PROSPECT_QUALITY_REVIEW_2026-08-05

**Document type:** durable aggregate-only audit of an external independent PR5D
prospect-quality review  
**Relation:** documentation context for draft PR #434 (live-feed bridge); does
**not** change classifiers, bridge code, or gold labels

This note separates:

1. **Verified repository facts** (fingerprints, planner identity, dry-run
   aggregates already produced by OrigenLab planners).
2. **Findings from the external independent review** (human dispositions on a
   diagnostic title-only sample).
3. **Limitations and inferences** (what the sample cannot claim).
4. **PR #434 live-feed results** (a different evidence question).

---

## 1. Verified review metadata

External review bundle filename (not committed; machine paths omitted):

```text
OrigenLab_PR5D_prospect_quality_review_bundle.zip
```

| Field | Value |
|-------|-------|
| ZIP SHA-256 | `e52e65d508447065b80e177b0e13ef21a38edab442f3a0830cb63798ad82e3cc` |
| Review schema | `origenlab_pr5d_independent_blind_review_summary_v1` |
| Review status / label status | `reviewed_not_gold` |
| Reviewed at | `2026-08-05T22:30:40Z` |
| Source as-of | `2026-08-01T19:00:30Z` |
| Planner | `procurement_product_relevance_planner_v1` |
| Blind packet SHA-256 | `c590f435926d41885b49e43ee4912497b8b09b63c5f4241638c131ddac9dfee5` |
| Aggregate summary SHA-256 | `ac290aa03a982e10c2b55f2c5e62c17346e86157ab0122040d3d67460bfe4f9d` |
| Input fingerprint | `3c5b4b619ac0765faa45c56b4aec879136104ef0e9471e36f82d4a2a7a01b2b6` |
| Semantic digest | `00d1446fcd23b7774e22c5fc7a6e56ed06fcdaaf504d3c61762d128cc6020b2a` |

Bundle files inspected read-only (via archive listing / stream; not extracted
into the repository):

- `analysis/pr5d_review_bundle/README.md`
- `analysis/pr5d_review_bundle/pr5d_independent_review_summary.json`
- `analysis/pr5d_review_bundle/source_aggregate_summary.json`
- `analysis/pr5d_review_bundle/blind_review_label_map.json`

Row-level CSV/JSON/notebook/PNG artifacts in the ZIP were **not** committed.

---

## 2. Review protocol (external review)

From `review_protocol` / README / summary counts:

- **200** unique review records (`unique_record_ids=200`).
- Sample role: **diagnostic-stratified** (not a representative production sample).
- Evidence available to the reviewer: **title-only**.
- Model predictions were **not** viewed while labeling (`predictions_seen=false`).
- The **sealed scoring manifest was not accessed**
  (`sealed_scoring_manifest_accessed=false`).
- Labels are independent **`reviewed_not_gold`** labels — **not** accepted gold.
- **No representative holdout** existed
  (`representative_holdout_available=false` / holdout proposed count 0).
- Therefore this sample **cannot** estimate production-wide precision, recall,
  F1, lead yield, or opportunity rate.

Data-quality status recorded in the bundle:
`usable_for_diagnostic_error_analysis_only`.

Bundle decision verdict (external recommendation, not an automatic repo gate):
`PAUSE_PR5F_IMPLEMENTATION_FOR_RELEVANCE_VALIDATION`.

---

## 3. Independent review results (aggregate)

### Disposition

| Disposition | Count |
|-------------|------:|
| Clear equipment opportunities | 4 |
| Manual review requiring line evidence | 4 |
| Clear negative or ineligible | 192 |
| **Total** | **200** |

### Human class distribution

| Human class | Count |
|-------------|------:|
| `strong_equipment_class` | 4 |
| `ambiguous` | 3 |
| `laboratory_context_only` | 1 |
| `consumable_or_reagent` | 15 |
| `non_laboratory_false_positive` | 3 |
| `rental_or_comodato` | 22 |
| `service_or_maintenance_only` | 10 |
| `unrelated` | 142 |
| **Total** | **200** |

### Four clear title-level equipment matches

1. `ADQUISICIÓN DE MICROSCOPIO PARA EL SERVICIO DE NEUROCIRUGÍA DEL HOSPITAL DIPRECA`
2. `ADQUISICIÓN DE CENTRIFUGA DE ALTA VELOCIDAD PARA LABORATORIO DE ANÁLISIS EN ACUICULTURA FINANCIADO POR LEASING FINANCIERO`
3. `SC 6618 - Adquisición de Centrifugas UOH`
4. `ADQUISICIÓN DE MICROSCOPIO INVERTIDO - UTALCA`

**None was action-ready from that packet.** The title-only review packet lacked
line specifications, buyer resolution, lifecycle/currentness, value, supplier
fit, verified contacts, and authorization evidence.

---

## 4. Associated production dry-run context (separate)

These are **planner aggregate counts** from the PR5D production dry-run context
bound into the review summary — **not** accuracy metrics and **not** inferred
from the 200-row human sample:

```text
PR5D decisions:                 16,448
Strong or compatible decisions:     12
Abstention/manual-review decisions: 14,812
```

Do **not** infer classifier accuracy, precision, recall, F1, or lead yield from
these counts.

---

## 5. Relation to PR #434 (different questions)

| Evidence | Purpose |
|----------|---------|
| PR5D 200-row independent review | Diagnostic review of **title-level** relevance errors on a stratified sample |
| PR #434 live-feed packet | **Current-source** lifecycle, line evidence, buyer/org resolution, and operator bucketing via the equipment detail-cache bridge |

### PR #434 already-verified live results

From
[`COMMERCIAL_PROCUREMENT_LIVE_FEED_BRIDGE_PR5B2.md`](COMMERCIAL_PROCUREMENT_LIVE_FEED_BRIDGE_PR5B2.md)
(production-derived read-only run):

```text
Live-backed review population: 193
current_opportunity: 6
needs_review: 160
historical_market_signal: 14
rejected: 13
actionable: 0
outreach authorized: 0
```

**No title↔opportunity overlap is claimed** between the four PR5D clear matches
and the six PR #434 `current_opportunity` rows unless stable tender identity is
explicitly joined and verified. Their percentages must **not** be compared as if
drawn from the same population.

---

## 6. Documented conclusions

- The PR5D independent review is a useful **frozen diagnostic baseline**.
- PR #434 solves the missing **live-evidence bridge** but does **not** validate
  classifier accuracy against sealed predictions.
- A separate, **explicitly authorized** calibration task should compare the
  independent labels with the sealed predictions (without converting them to
  gold in this PR).
- A **representative holdout** is still required before publishing precision,
  recall, F1, or production lead-yield claims.
- Keep concepts separate: product fit ≠ current opportunity ≠ actionable lead ≠
  contact authorization ≠ outreach authorization.
- **PR5F persistence, PR5G adjudication, dashboard/API wiring, and outreach
  remain outside PR #434.**

---

## 7. Explicit non-goals of this documentation follow-up

- No ZIP / CSV / row-level / notebook / PNG commit.
- No sealed scoring manifest access.
- No promotion of reviewed labels to gold.
- No PR5D rule tuning from this sample.
- No production dry-run rerun; no code/test/database/Gmail/network changes.
