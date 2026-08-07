# COMMERCIAL_PROCUREMENT_PROSPECT_RECOGNITION_PR5E2

**Status:** hardened (read-only recognition corrections)
**Planner version:** `procurement_institution_prospect_planner_v2`
**Recognition layer:** `procurement_prospect_recognition_pr5e2_v1`
**Contract:** `institution_prospect_contract_v2`
**Stacks on:** PR #436 / PR5E.1 (itself on PR #434 / PR5B.2)

## Purpose

Correct commercial judgment **before** any PR5F persistence.

> The system identifies public evidence of requested or used equipment.
> It does not discover an institution’s unexpressed needs and does not
> authorize contact or outreach.

## Pipeline

```text
tender and line evidence
→ lifecycle projection (immutable temporal contract)
→ line/clause commercial claims
→ catalog capability match (from catalog_seed_v1.json)
→ procurement-event family (conservative ladder)
→ canonical buyer identity
→ review-only institution cluster
→ account/contact overlay
→ institution profile
→ separate operator queues (stated grains)
```

## Hardening contracts (post-89b6a8a)

### Immutable temporal provenance
- Never clamp or rewrite `acquired_at` / observation stamps to `as_of`.
- Evidence acquired after `evaluation_as_of_utc` is `future_observation_excluded`.
- Terminal overrides open only with real observation chronology; otherwise fail closed.

### Source population
- Exact set reconciliation required (not counts alone).
- First PR5E.2 run’s `16643 → 16644` drift was tender `1499-163-lp26`, admitted only because acquisition stamps were clamped from `20:12Z` onto `as_of 02:00Z` (look-ahead).

### Event families
- Exact buyer+title alone does **not** confirm the same procurement event.
- Confirmed join requires reissue/replacement markers or shared stable project/BIP IDs.
- Near matches → `retender_review_required` with independent-event bounds.
- No institution-specific normalization in production source.

### Line-driven recognition
- Classification uses full `ProductTextUnit` text, not matched-span reconstruction.
- Clause splits preserve mixed purchase + maintenance claims.
- Valid maintenance/rental/consumable signals are assigned, not auto-review.

### Catalog
- Capability derived from `data/catalog/catalog_seed_v1.json`.
- Includes reactor; maps catalog `sonicator` ↔ taxonomy `ultrasonic_processor`.
- Incubator/microscope/etc. are not silently treated as verified supply capability.

### Fixtures
- Reviewed adjudication fixture is test/audit-only; normal planning does not load `tests/fixtures`.

## Operator queue grains

| Queue | Grain |
|-------|--------|
| current_opportunity | institution + tender + category/claim |
| historical_prospect | institution + category + commercial signal |
| institution_match_review | profile \| review_cluster subject |
| contact_gap | prospect-prioritized profiles only |
| line_evidence_review | genuine ambiguity only |
| retender_review | unresolved relationship component |

Empty CSVs emit deterministic headers. Authorization flags are always explicit `false`.

## CLI

Prefer temporally eligible acquisition snapshots (`acquired_at ≤ as_of`). Do not rewrite stamps.

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_commercial_procurement_institution_prospect_plan.py \
  --sqlite-path "$ORIGENLAB_SQLITE_PATH" \
  --acquisition-snapshot-json <eligible_snapshot.json> \
  --as-of-utc 2026-08-06T02:00:00Z \
  --allow-stale-feed \
  --out-dir reports/out/active/current/commercial_procurement_institution_prospects_pr5e2_<UTC> \
  --run-context production_dry_run
```
