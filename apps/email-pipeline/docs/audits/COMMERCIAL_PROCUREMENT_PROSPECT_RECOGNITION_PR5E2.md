# COMMERCIAL_PROCUREMENT_PROSPECT_RECOGNITION_PR5E2

**Status:** implemented (read-only recognition corrections)  
**Planner version:** `procurement_institution_prospect_planner_v2`  
**Recognition layer:** `procurement_prospect_recognition_pr5e2_v1`  
**Contract:** `institution_prospect_contract_v2`  
**Stacks on:** PR #436 / PR5E.1 institution prospect map (itself stacked on PR #434 / PR5B.2)

## Purpose

Correct commercial judgment before any PR5F persistence.

The system answers:

> What laboratory-equipment demand has this institution publicly demonstrated,
> was it purchase, maintenance, rental or consumables, is anything genuinely
> open now, and does OrigenLab already have a safely resolved account/contact?

It recognizes **only public procurement evidence**. It does **not** discover
unexpressed needs and does **not** authorize contact or outreach.

## Pipeline

```text
tender and line evidence
→ lifecycle projection (precedence)
→ commercial signal interpretation (line-scoped)
→ procurement-event family
→ canonical buyer identity
→ review-only institution cluster
→ account/contact overlay
→ institution profile
→ separate operator queues
```

## Root causes corrected

| Defect | Cause | Fix |
|--------|-------|-----|
| Lifecycle downgrade (`active_open` → `status_unknown`) | PR5C fail-closes open status when acquisition stamps are after pinned `as_of` / provenance gate fails; PR5E.1 only counted `active_open` | Lifecycle precedence: known open values precede `status_unknown`; acquired_at clamped to `as_of` on replay |
| Lexical false positives | Substring `centrifuga` / bare `incubadora` / bare `balanza` | Line-scoped vetoes: centrifugal pump, business incubator, anthropometric context, microscope covers |
| Mixed tenders erased | Title-only ambiguous aggregates buried catalog lines | Line evidence retained; disposition keeps catalog-class lines |
| False recurrence | Raw tender count ≥2 | Event families: exact buyer+object fingerprint; near-match → `retender_review_required` / `recurrence_not_established` |
| Institution fragmentation | Strict identity digests (correct) without review cluster | Review-only clusters; no auto-merge; no sibling contact authorization |

## Lifecycle precedence

1. Preserve `status_conflict`.
2. Authoritative terminal status overrides open/unknown.
3. Preserve known non-weak lifecycles.
4. Restore `active_open` when values are Publicada + future close and source was `status_unknown`.
5. Lifecycle remains independent of commercial relevance / catalog fit.

## Commercial signals (unchanged vocabulary)

`equipment_purchase_signal` · `installed_base_signal` · `rental_or_comodato_signal` ·
`consumable_or_reagent_signal` · `review_required_signal` · `excluded_unrelated`

Three independent axes: `prospect_strength` · `opportunity_urgency` · `contact_readiness`.

## Operator queues (separate)

- `current_opportunity_queue.csv` — active + catalog_fit / possible_fit only
- `historical_prospect_queue.csv` — closed/historical purchase, maintenance, rental
- `institution_match_review_queue.csv` — unresolved / ambiguous / fragmented clusters
- `contact_gap_queue.csv` — exact gap reason; never outreach auth
- `line_evidence_review_queue.csv`
- `retender_review_queue.csv`

## Reviewed adjudication fixture

`tests/fixtures/commercial_procurement_pr5e2_reviewed_adjudication.json`

Provenance: **`analyst_reviewed_provisional`** — not gold truth; not classifier output.
Expected distribution: 8 / 3 / 8 / 6 / 6 / 5 (total 36).

## CLI

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_commercial_procurement_institution_prospect_plan.py \
  --sqlite-path "$ORIGENLAB_SQLITE_PATH" \
  --detail-cache-dir reports/out/active/current/chilecompra_detail_cache \
  --equipment-manifest reports/out/active/current/equipment_first_operator_queue_chilecompra_api_YYYYMMDD.manifest.json \
  --refresh-state reports/out/active/current/chilecompra_equipment_auto_refresh_state.json \
  --as-of-utc 2026-08-06T02:00:00Z \
  --allow-stale-feed \
  --out-dir reports/out/active/current/commercial_procurement_institution_prospects_pr5e2_<UTC> \
  --run-context production_dry_run
```

Contact and outreach authorization remain **false**. Profiles are **not persisted**.
