# Commercial Opportunity Stage Read Model — PR3

Status: implementation  
Owner: email-pipeline-maintainers  
Date: 2026-07-29  
Branch: `feat/commercial-opportunity-stage-pr3`

Related: [`COMMERCIAL_TRUTH_AUDIT_PR1.md`](COMMERCIAL_TRUTH_AUDIT_PR1.md) · [`COMMERCIAL_IDENTITY_READ_MODEL_PR2.md`](COMMERCIAL_IDENTITY_READ_MODEL_PR2.md) · [`COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md`](../../../docs/commercial/COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md) · [`CRUD_SAFETY.md`](../CRUD_SAFETY.md)

**Identity does not prove an opportunity.**  
**Historical commercial evidence does not prove a current stage.**  
**A current stage requires dated, typed, defensible evidence.**  
**Cumulative counts never prove fulfillment or purchase pending.**  
**Tender evidence remains a separate procurement dimension.**  
**PR3 does not generate operator next actions.**

---

## 1. Problem and scope

PR1 showed overloaded action buckets and undated cumulative counts cannot prove current commercial stage. PR2 added a rebuildable account/contact identity spine. PR3 adds a **deterministic, rebuildable opportunity-stage read model** over:

- PR2 identity resolution (in-memory on dry-run; fingerprint-gated on apply)
- explicit `commercial_deal*` ledger evidence
- dated documents/payments/events
- typed signals only when a real business-event timestamp can be recovered
- historical mart counts conservatively as `commercial_history`

PR3 is **not** a dashboard redesign and **not** an autonomous CRM.

### In scope

- Pure opportunity/event resolver
- Canonical stage taxonomy + source-status mappings
- Deterministic opportunity/event/evidence/conflict IDs
- PR2 identity linkage + fingerprint compatibility gate
- Rebuildable `commercial_opportunity_*` tables
- Dry-run-default CLI (`--sqlite-path` required)
- Synthetic tests + documentation

### Out of scope

- Gmail refresh/mutation, sends, drafts
- Production SQLite apply (this task validates on fixtures only)
- Postgres / Alembic / API / dashboard
- Mutations to existing commercial-deal rows
- Classification / `commercial_action_bucket` / suppression / outreach changes
- Tender linking, product-interest, outreach batches, next-action tasks
- ChatGPT/MCP, deploy/systemd/cron/Cloudflare

---

## 2. Source inventory

| Source | Role | Notes |
|--------|------|-------|
| `commercial_deal` | Explicit opportunities (1 per `deal_key`) | Ledger evidence; not duplicated |
| `commercial_deal_event` | Dated stage refinements | Missing `event_at` → conflict, timestamp stays missing |
| `commercial_deal_document` | Typed docs with `issued_at` | Supplier docs refine fulfillment only |
| `commercial_deal_payment` | Dated payments | Inbound → won; outbound → fulfillment |
| `commercial_deal_evidence` / field evidence | Provenance pointers | Not body text |
| `opportunity_signals` | Evidence candidate **only** with recovered email event time | Production columns: `id`, `signal_type`, `entity_kind`, `entity_key`, `email_id`, `attachment_id`, `score`, `details_json`, `created_at`. `entity_kind=contact` → email key; `organization` is not an email. `created_at` is mart stamp — never stage evidence. Schema mismatches fail closed. |
| `contact_master` lifetime counts | `commercial_history` only when typed commercial evidence exists | Production columns include quote/invoice/purchase/business-doc counts + `first_seen_at`/`last_seen_at`. Generic `total`/`inbound`/`outbound` alone never create history. |
| `emails` | Optional recover of signal event time | Read-only |
| PR2 identity sources / resolution | Account/contact linkage | Not reimplemented |

**Deliberately separate:** warm-case models, ChileCompra equipment, action buckets, lead_account_* clustering.

**Reused:** PR2 `resolve_identity` + stable IDs + fingerprint; PR1 stage vocabulary; deal ledger enums.

**Not replaced:** `commercial_deal*` remains the operator ledger. PR3 is an additive read model.

---

## 3. Identity dependency / fingerprint contract

Production may lack persisted `commercial_identity_*` (PR2 validated dry-run only).

| Mode | Behavior |
|------|----------|
| Dry-run | Load PR2 source assertions → `resolve_identity` in memory → link opportunities → **no writes** |
| Apply | Fail closed unless persisted PR2 `schema_version` + `identity_fingerprint_algorithm_version` + `identity_fingerprint` match the in-memory resolution |

Fingerprint algorithm: **`identity_fp_v2`** — canonical JSON over linkage-relevant account/contact fields (ids, domains, aliases, status, confidence, account_link_method) plus evidence/conflict IDs. Order-independent. Stored by PR2 apply. Missing/stale/mismatched snapshots raise `IdentitySnapshotError`.

Apply also recomputes an **`opportunity_source_fp_v1`** source fingerprint inside the write transaction; identity or source drift between plan and apply raises `StaleBuildPlanError` and preserves the prior dataset.

---

## 4. Opportunity kinds

| `record_kind` | When |
|---------------|------|
| `explicit_opportunity` | Each valid `commercial_deal.deal_key` → exactly one opportunity |
| `evidence_candidate` | Dated typed non-deal evidence with stable id + recoverable event time |
| `commercial_history` | Lifetime cumulative counts only |

Ambiguous/unresolved identity **retains** the explicit opportunity, nulls unsafe links, emits identity conflicts, marks review required.

Supplier-only evidence may refine an existing client deal’s fulfillment stage; it must not create a standalone client opportunity.

---

## 5. Stage taxonomy and deal_status mapping

Canonical stages: `qualifying`, `quote_requested`, `quote_preparing`, `quote_sent`, `technical_review`, `purchase_pending`, `won`, `fulfillment`, `post_sale`, `lost`, `commercial_history`, `unknown`.

| `commercial_deal.deal_status` | Canonical stage | Terminal? |
|------------------------------|-----------------|-----------|
| `draft` | `quote_preparing` | no |
| `quoted` | `quote_sent` | no |
| `client_po_received` | `purchase_pending` | no |
| `client_invoiced` | `purchase_pending` | no |
| `client_paid` | `won` | lifecycle yes (may refine to fulfillment) |
| `supplier_po_sent` / `supplier_invoiced` / `supplier_paid` | `fulfillment` | no |
| `logistics_pending` / `in_transit` | `fulfillment` | no |
| `delivered` | `post_sale` | hard terminal |
| `cancelled` | `lost` | hard terminal |
| `needs_review` | `unknown` | no |
| `closed` | `unknown` unless supporting dated payment/delivery/cancel evidence | no (alone) |

**`closed` with support:** `closed` / `deal_closed` rows stay provenance only. Dated supporting evidence selects stage (`client_payment_received` / inbound payment → `won`; `delivered` → `post_sale`; `deal_cancelled` → `lost`). `stage_evidence_id` points at that supporting evidence — never the generic closed status. Closed provenance must not manufacture `stage_regression_prevented` against its own support. Unsupported closed → `unknown` + `closed_without_supporting_evidence` + `needs_review`.

**Hard terminals** (`lost`, `post_sale`) cannot be overwritten by older quote/active stages. Contradictory hard terminals at any timestamps emit `conflicting_terminal_events` and require review; displayed stage is the latest hard-terminal **UTC instant** (raw ISO strings are never compared lexicographically).

**Undated terminal policy (conservative):** undated `client_paid` / `delivered` / `cancelled` cannot prove a definitive terminal stage → `canonical_stage=unknown`, `stage_is_terminal=false`, `stage_is_current=false`, `confidence=unavailable`, conflict `undated_terminal_unproven`. Undated unsupported `closed` uses `closed_without_supporting_evidence` instead.

`won` may still be refined to fulfillment by later supplier/logistics evidence.

---

## 6. Evidence precedence

Chronological compatible progression (UTC instants — not raw ISO string order):

1. Detect hard-terminal contradictions explicitly (`conflicting_terminal_events` / same-time conflicts). Displayed hard terminal is the latest UTC instant; input order does not matter.
2. For compatible nonterminal progression, walk dated evidence in UTC event chronology; later legitimate advances refine older stages.
3. Operator confirmation strengthens evidence and breaks ties; it does not outrank every later lifecycle advance.
4. Later lower-stage events emit `stage_regression_prevented` and do not regress the opportunity.
5. Deterministic stable source keys break otherwise equal ties.
6. Original source timestamp strings are preserved on stored records. Date-only values order as UTC midnight of that day without inventing sub-day precision. Malformed timestamps emit `malformed_event_timestamp` and never become current stage evidence.

Document mapping notes:
- `supplier_proforma` alone does not advance to fulfillment; may refine fulfillment only after client PO/payment/commitment evidence.
- `payment_voucher` / `payment_confirmation` documents never establish won; only inbound payments / `client_payment_received` (or equivalent direction-aware evidence) do.
- Outbound payments refine fulfillment, never client won.

Build time never becomes stage evidence time. Every selected `stage_evidence_id` has exactly one matching `commercial_opportunity_evidence` row (provenance invariant).

---

## 7. Currentness and terminality

- Dated nonterminal explicit deal status/events may be `stage_is_current=true`.
- Hard/lifecycle terminals are not “current open” (`stage_is_current=false`).
- Evidence candidates are never automatically current.
- Undated evidence is never current.
- Cumulative counts are never current.
- No arbitrary inactivity threshold changes canonical stage.

---

## 8. Conflict reason codes

Includes: `opportunity_identity_unresolved`, `opportunity_identity_ambiguous`, `deal_contact_account_mismatch`, `duplicate_deal_key_evidence`, `source_event_missing_timestamp`, `malformed_event_timestamp`, `conflicting_terminal_events`, `stage_regression_prevented`, `same_timestamp_stage_conflict`, `unsupported_source_stage`, `undated_signal_history_only`, `undated_terminal_unproven`, `closed_without_supporting_evidence`, `deal_status_without_usable_timestamp`, `stale_or_missing_identity_snapshot`, `stale_build_plan`, `supplier_only_not_client_opportunity`, `source_schema_incompatible`.

**Review status** is derived centrally after resolution: any attached conflict in `REVIEW_REQUIRING_CONFLICT_REASONS`, plus `deal_status=needs_review`, forces `review_status=needs_review`. No opportunity may be `ok` while carrying a review-requiring attached conflict.

Conflicts carry stable subject keys + source pointers — never email bodies, extracts, credentials, or unnecessary PII.

---

## 9. Stable IDs

| Entity | Material |
|--------|----------|
| Opportunity (deal) | `v1\|opportunity\|commercial_deal\|{deal_key}` |
| Opportunity (other) | `v1\|opportunity\|{source_kind}\|{source_key}` |
| Event / evidence / conflict | Deterministic SHA prefixes `oe_` / `ox_` / `oc_` |

Autoincrement deal `id` is **not** the canonical opportunity id.

---

## 10. Schema and transaction contract

Tables: `commercial_opportunity`, `commercial_opportunity_event`, `commercial_opportunity_evidence`, `commercial_opportunity_conflict`, `commercial_opportunity_build_meta`.

**Contract B:** additive `CREATE TABLE IF NOT EXISTS` may remain after first-run DDL; DELETE+INSERT data replacement is atomic with `PRAGMA foreign_keys=ON`. Rollback preserves the previous opportunity dataset. Does not mutate `commercial_deal*` or `commercial_identity_*`.

---

## 11. CLI safety

```bash
uv run python scripts/commercial/build_commercial_opportunity_read_model.py \
  --sqlite-path /explicit/path.sqlite \
  --run-context local_fixture
```

- Required `--sqlite-path` (no production fallback)
- Default dry-run; explicit `--apply`
- `--run-context` / `--json-summary`
- Apply refuses absent/stale identity snapshot
- Expected operator-contract failures print concise `error:` lines to stderr (no traceback) and exit:
  - `2` — path / run-context / mode (`CommercialIdentityPathError`)
  - `3` — missing/mismatched identity snapshot (`IdentitySnapshotError`)
  - `4` — incompatible source schema (`SourceSchemaError`)
  - `5` — stale build plan / source race (`StaleBuildPlanError`)

**Do not run production apply or production opportunity dry-run without separate operator authorization after merge.**

---

## 12. Metrics

Exact metrics (definitions embedded in output):  
`source_deals_inspected`, `source_events_inspected`, `source_documents_inspected`, `source_payments_inspected`, `source_signals_inspected`, `canonical_opportunity_count`, `explicit_deal_opportunity_count`, `evidence_candidate_count`, `commercial_history_count`, `current_opportunity_count`, `terminal_opportunity_count`, `linked_account_count`, `linked_contact_count`, `unresolved_identity_count`, `opportunity_conflict_count`, `missing_event_timestamp_count`, `undated_signal_history_count`, stage/confidence/conflict distributions, `identity_fingerprint_match_status`, and inference flags (`opportunity_stage_fields_inferred=true`; next-action/tender/product-interest=`false`).

Fixture results must not be described as production metrics. Run-context labels are metadata only.

---

## 13. Boundaries with later work

| Concern | Owner |
|---------|-------|
| Tender ↔ account | PR4 |
| Product interest / batch readiness | later |
| Next-action tasks | later |
| API / dashboard | later |
| Identity matching rules | PR2 only |

---

## 14. Known limitations

- Does not reopen cancelled deals from newer quote events.
- Mart `opportunity_signals.created_at` is never business event time.
- Supplier-only non-deal planes do not mint client opportunities.
- No automatic follow-up dates or inactivity thresholds.
