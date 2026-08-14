# Commercial procurement product relevance — PR5D

**Status:** Draft implementation slice  
**Branch:** `feat/commercial-procurement-product-relevance-pr5d`  
**Question answered:** Does this procurement describe something OrigenLab can plausibly supply?

## Scope

```text
PR5C coalesced procurement
        ↓
Product-text evidence (adapter)
        ↓
Normalized evidence units
        ↓
Deterministic product classification
        ↓
Reason-coded relevance decision
        ↓
Evaluation / human-review queue (proposed labels ≠ gold)
```

## Exclusions

Contact Master, account research, lead persistence, outreach eligibility, Gmail, Ticket/OCDS network acquisition, scheduling, API/dashboard/Postgres, email sending, LLM/embeddings/ML downloads, infrastructure.

## Field-sufficiency conclusion

PR5C exposes **title-level** product text only (`title_raw` / `title_selected`). Meaningful relevance requires line-level `product` / `description` / `category` from PR5B snapshots. PR5D’s adapter re-reads immutable `AcquisitionSnapshot` artifacts by `snapshot_id` + `observation_id` **without** changing PR5C identity or lifecycle.

Lossless adapter feasible without PR5C contract change: **yes**.

### Reconciliation

```text
coalesced tenders = tenders with relevance decisions

all extracted product-text units = linked evidence units + typed unresolved evidence units
```

Empty product text → `ambiguous` / insufficient evidence — **never** silent `unrelated`.

## Taxonomy

Reuses `commercial_procurement_live_relevance/taxonomy.py` + PR5D extensions.

Gap fills (class-level): `tablet_hardness_tester`, `dissolution_apparatus`, `sedimentation_settlometer`.

Commercial capability seeds (UP200St, PTB 311E, Nalgene settlometer, …) remain `proposed_seed_not_verified` — **not** exact catalog aliases until sanitized evidence exists.

## Current rule and aggregation semantics

Current constants:

- `PRODUCT_RELEVANCE_RULES_VERSION = "procurement_product_relevance_rules_v3"`
- `aggregation_policy_spec()["version"] = "aggregation_policy_v3"`

Rules v3 fail closed on precision findings from annex review: accessory and
replacement lines, service/maintenance context, diagnostic method/exam wording,
bare `agitador`, and supplier-required equipment do not become buyer equipment
acquisitions.

Aggregation v3 treats independent single-class strong units with distinct
classes as legitimate multi-equipment evidence
(`multiple_distinct_equipment_classes_combined`), while a single strong unit with
competing classes remains `mixed_requires_review` with
`conflicting_canonical_equipment_classes`. Negative evidence plus empty
abstentions keeps the most-specific negative
(`negative_survives_empty_abstentions`); negative evidence plus
equipment-bearing abstention still requires review.

## CLI

```bash
uv run python scripts/commercial/build_commercial_procurement_product_relevance_plan.py \
  --sqlite-path … \
  --acquisition-snapshot-json … \
  --as-of-utc … \
  --out-dir reports/out/active/current/commercial_procurement_product_relevance_pr5d_<UTC>
```

Forbidden: `--apply --persist --network --ticket --gmail --postgres --outreach --schedule`.

## Evaluation

- **Contract fixtures:** tracked synthetic cases (explicitly labelled).
- **Real review corpus:** gitignored labeling queue via **per-stratum stable-hash quotas** with documented exhaustion + redistribution; proposed ≠ gold.
- **Blind packet** (human-facing) excludes predicted class / prediction-derived stratum; **sealed scoring manifest** joins by `record_id`.
- Metric eligibility requires `reviewed|gold`, non-empty `review_source`, `independently_reviewed=True`, non-synthetic, valid gold class.
- Metrics only over independently labelled `reviewed`/`gold` records. Zero eligible labels ⇒ no precision/recall/F1.

## Contract corrections (audit pass)

- Shareable walkthrough uses redacted DTOs only (no operational ID prefixes / `text_raw`).
- Taxonomy classes live only in PR5A `taxonomy.py` (no PR5D duplicate append).
- Title-only absence of keyword ⇒ `ambiguous` + abstain (not silent `unrelated`).
- Plan + walkthrough publish as one atomic staged bundle.
- Reconciliation uses independent extraction-attempt IDs.

## Roadmap note

Authoritative sequence: PR5D product relevance → PR5E contact → PR5F persistence → PR5G adjudication. See corrected §11 in `COMMERCIAL_PROCUREMENT_LIVE_RELEVANCE_PR5.md`.
