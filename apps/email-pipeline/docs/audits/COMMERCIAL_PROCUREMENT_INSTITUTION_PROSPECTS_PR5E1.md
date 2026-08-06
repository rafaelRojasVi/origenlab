# COMMERCIAL_PROCUREMENT_INSTITUTION_PROSPECTS_PR5E1

**Status:** implemented (read-only planner)  
**Planner version:** `procurement_institution_prospect_planner_v1`  
**Contract:** `institution_prospect_contract_v1`  
**Stacks on:** PR #434 / PR5B.2 live-feed bridge

## Pipeline

```text
tender evidence (PR5C live_snapshot / both / pr4)
→ procurement buyer identity (deterministic)
→ existing OrigenLab account comparison (PR5E, unchanged)
→ existing contact comparison (PR5E, unchanged)
→ accumulated equipment history (PR5D classes + dates)
→ institution-level prospect profile
```

## Separations

| Claim | Truth |
|-------|-------|
| closed tender | ≠ current opportunity |
| closed relevant tender | = historical buying-intent evidence |
| account match | ≠ usable contact |
| usable contact | ≠ outreach authorization |

Contact and outreach authorization remain **false**. Profiles are **not persisted** (`not_persisted=True`).

**Follow-on:** PR5E.2 recognition corrections —
[`COMMERCIAL_PROCUREMENT_PROSPECT_RECOGNITION_PR5E2.md`](COMMERCIAL_PROCUREMENT_PROSPECT_RECOGNITION_PR5E2.md).

## What already existed (PR5B.2 audit)

Verified from the fixed PR5B.2 packet
`commercial_procurement_live_feed_review_pr5b2_20260806T021500Z`:

| Metric | Value |
|--------|------:|
| Live-backed tenders | 193 |
| organization linked | 2 |
| organization unlinked | 191 |
| contact_resolution_deferred | 191 |
| no_contact_found | 2 |
| selected contacts | 0 |
| PR5C live_snapshot / pr4 / coalesced | 193 / 16448 / 16641 |

Root cause of 191 unlinked live buyers: `organization_reason_code=buyer_domain_missing`.
Ticket `buyer_source_id` is CodigoOrganismo/RUT (not a domain); PR5E fail-closes
name-only live linking. The two linked rows are
`INSTITUTO DE SALUD PUBLICA DE CHILE` (`exact_unique_alias`) with
`internal_search_exhausted_empty` / no selected contact.

PR5B.2 exports **tender-level** review rows only. This planner adds the missing
**institution-level** equipment-demand profile — including unlinked buyers.

## Institution identity rules

Priority:

1. PR5E linked `account_id` (OrigenLab account)
2. Typed ChileCompra `buyer_source_id` (not an `account_id`)
3. Strict normalized buyer name when identifiers are absent and no conflict
4. Otherwise unresolved — still retained as a profile (never silently dropped)

Conflicting ChileCompra buyer IDs under the same normalized name become
identity-review cases; units are not merged by similar names. No fuzzy / LLM /
web automatic account links.

## Commercial evidence signals

| Signal | Meaning |
|--------|---------|
| `equipment_purchase_signal` | strong/compatible/exact catalog relevance |
| `installed_base_signal` | service/maintenance (not a purchase) |
| `rental_or_comodato_signal` | rental demand (not ownership) |
| `consumable_or_reagent_signal` | must not inflate purchase history |
| `review_required_signal` | ambiguous / lab-context |
| `excluded_unrelated` | unrelated / non-lab FP |

Demand recurrence: `1` tender → `observed_once`; `2+` → `repeated_observed_demand`.

## Three independent axes

- `prospect_strength` — equipment fit, frequency, recency, repeated demand
- `opportunity_urgency` — open tender / closing / current lifecycle
- `contact_readiness` — linked account + contact quality/status

A strong historical prospect may have zero urgency. An open tender may have poor
contact readiness. Neither erases the other.

## CLI

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_commercial_procurement_institution_prospect_plan.py \
  --sqlite-path "$ORIGENLAB_SQLITE_PATH" \
  --detail-cache-dir reports/out/active/current/chilecompra_detail_cache \
  --equipment-manifest reports/out/active/current/equipment_first_operator_queue_chilecompra_api_YYYYMMDD.manifest.json \
  --refresh-state reports/out/active/current/chilecompra_equipment_auto_refresh_state.json \
  --as-of-utc 2026-08-06T02:00:00Z \
  --out-dir reports/out/active/current/commercial_procurement_institution_prospects_pr5e1_<UTC> \
  --run-context production_dry_run \
  --json-summary
```

Forbidden: `--apply --persist --network --ticket --gmail --postgres --outreach --send --schedule --label`.

## Artifacts (gitignored)

- `summary.json` (aggregate / redacted)
- `institution_prospect_packet.json`
- `institution_prospect.csv`
- `institution_match_review_queue.csv`
- `contact_gap_queue.csv`
- `source_reconciliation.json`
- `walkthrough.md`

## Out of scope

PR5F persistence, CRM account creation, automatic uncertain linking, dashboard/API,
Gmail, outreach, classifier gold promotion.
