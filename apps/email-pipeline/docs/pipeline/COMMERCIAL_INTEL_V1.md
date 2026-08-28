# Commercial Intelligence V1

Status: canonical  
Owner: email-pipeline-maintainers

This layer targets **client discovery/commercial intelligence**, not generic email tagging.

## Scope

V1 adds a safe additive slice on top of historical email data:

- Rebuildable signal facts and rollups (derived from `emails` history).
- Durable human-facing candidate state (org/contact/opportunity).
- Explainable reasons, confidence, suppression, and review status.

Raw archive tables remain untouched:

- `emails`
- `attachments`
- `attachment_extracts`

## Ownership and table contract

Rebuildable tables:

- `commercial_email_signal_fact`
- `commercial_org_signal_rollup`
- `commercial_contact_signal_rollup`
- `commercial_opportunity_fact`
- `v_commercial_candidate_queue` (view over durable candidates)

Durable tables:

- `organization_candidate`
- `contact_candidate`
- `opportunity_candidate`
- `candidate_review_event`
- `candidate_manual_override`

Schema owner: `src/origenlab_email_pipeline/commercial/commercial_intel_schema.py`  
Build owner: `scripts/commercial/build_commercial_intel_v1.py`

## Processing contract (incremental correctness)

Watermark key: `pipeline_kv.commercial_v1_last_email_id`

- Watermark is used for performance only.
- Correctness is preserved by:
  - deleting/rebuilding facts for selected email ids on each run
  - idempotent upserts for candidate tables
  - rollup recomputation from facts
  - explicit reconciliation checks

This keeps runs safe under append/rerun and mixed ingest semantics.

## Business rules (v1 deterministic)

Positive signals:

- quote/cotizacion intent
- procurement/purchase intent
- technical inquiry in lab/equipment context
- equipment relevance

Suppressions:

- vendor/supplier-like
- invoice/payment-heavy
- logistics-heavy
- noise sender/system mail
- existing-client-likely (best effort domain set)

Every signal stores reason code, reason text, confidence/strength, and email evidence linkage.

## Candidate lifecycle

Candidate statuses:

- `new`
- `needs_review`
- `approved`
- `rejected`
- `suppressed`
- `snoozed` (operational deferral; preserved across builder reruns like `approved` / `rejected`)

Manual/human decisions are tracked in:

- `candidate_review_event`
- `candidate_manual_override`

The queue view `v_commercial_candidate_queue` is the primary read surface for operations. It includes `org_domain`, `display_name`, `candidate_type` (organizations only), `rationale_text`, and a single `reason_summary` line (rationale + suppression flags when present).

## Operational commands

From `apps/email-pipeline`:

```bash
# Build/update v1 layer (incremental by watermark + optional reprocess window)
uv run python scripts/commercial/build_commercial_intel_v1.py

# Full rebuild of rebuildable signal layer, then durable sync
uv run python scripts/commercial/build_commercial_intel_v1.py --rebuild

# Audit/reconciliation summary
uv run python scripts/commercial/audit_commercial_intel_v1.py

# Export queue slice (CSV/JSON) for inspection or external review
uv run python scripts/commercial/export_commercial_candidate_queue.py \
  --out reports/out/commercial_queue_sample.csv \
  --review-status needs_review --min-confidence 0.5 --limit 200

# Minimal review CLI (durable override + audit row + immediate status update)
uv run python scripts/commercial/review_commercial_candidate.py \
  --entity-kind organization --entity-key example.com --action approve --note "validated"
```

**Streamlit UI removed (2026-06-04).** Use the CLIs above and [`review_commercial_candidate.py`](../../scripts/commercial/review_commercial_candidate.py) for review writes on a **writable** SQLite file. Optional RW env: `ORIGENLAB_OPERATOR_COMMERCIAL_REVIEW_RW=1` (legacy alias: `ORIGENLAB_STREAMLIT_COMMERCIAL_REVIEW_RW=1` when the operator helper is wired). Active read UI: [`apps/dashboard`](../../../dashboard/README.md) + Postgres mirror via [`apps/api`](../../../api/README.md).

## Non-goals for v1

- Perfect entity resolution of participants.
- Full CRM/workflow system.
- Opaque ML scoring.
- Strong existing-client master-data guarantees.

