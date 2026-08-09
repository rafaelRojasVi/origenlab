# ANEXO-P1 — Shadow prospect integration (measurement only)

**Status:** draft measurement lane  
**Does not:** mutate production queues, persist annex-derived prospect state, authorize contact/outreach, start PR5F, or widen catalog capability.

## Purpose

Answer:

> If annex-backed PR5D evidence were considered by the PR5E.2 institution
> prospect planner, what prospects / institution signals / queue memberships
> WOULD change?

Two parallel in-memory views:

| View | PR5D evidence |
|------|----------------|
| CURRENT | baseline API/item only |
| SHADOW | baseline + annex segments |

PR5C coalesced tenders and a frozen PR5E contact overlay are shared. The only
intentional variable is product evidence.

## Architecture

```
ANEXO-E2 recognize helpers
  → ProductRelevancePlanResult (current) + (shadow)
build_institution_prospects_from_plans ×2
  → queue / profile / history diff
gitignored shadow packet only
```

Package: `origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects`  
CLI: `scripts/commercial/build_anexo_shadow_prospect_comparison.py`

## Safety invariants

Every summary carries:

- `contact_authorization = false`
- `outreach_authorization = false`
- `production_queue_mutated = false`
- `persisted = false`
- `pr5f_started = false`
- `annex_production_integration = false`
- `shadow_results_are_not_production = true`

CLI rejects `--apply/--send/--persist/--network/--ticket/--gmail/--postgres/--outreach`.

## Capability gating

Claim-level capability uses the catalog seed API
(`match_catalog_status` / `verified_catalog_equipment_classes`). Never inferred
from tender text. Multi-equipment tenders are scored per class
(`in_scope` / `out_of_scope` / `unclear`).

## Commercial intent

Shadow opportunities preserve H1 semantics: accessory, maintenance,
method/exam, and supplier-required equipment do not become sellable-equipment
prospects.

## Pilot

Frozen-52 digest:

`fec235f7845186f50298276ef19af5b335491409a249765300f5733d07b599a5`

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_anexo_shadow_prospect_comparison.py \
  --anexo-evidence-dir reports/out/active/current/chilecompra_anexo_evidence_e2_deferred52 \
  --out-dir reports/out/active/current/anexo_p1_shadow_prospect \
  --json-summary
```

Artifacts (gitignored): `summary.json`, `tender_deltas.jsonl`,
`institution_deltas.jsonl`, `queue_deltas.jsonl`, `shadow_opportunities.csv`,
`walkthrough.md`.
