# Commercial Truth Audit — PR1

Status: committed technical audit (evidence-gathering)  
Owner: email-pipeline-maintainers  
Date: 2026-07-28  
Branch: `audit/commercial-truth-pr1`

Related: [`SCHEMA_CLASSIFICATION_MODEL.md`](../pipeline/SCHEMA_CLASSIFICATION_MODEL.md) · [`COMMERCIAL_INTEL_V1.md`](../pipeline/COMMERCIAL_INTEL_V1.md) · [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md)

**This PR does not redesign the dashboard, add CRM schema, connect ChatGPT, send email, or change production classifications.**

---

## 1. How the current system works

### Lineage (source → evidence → overlay → classification → API → UI/export)

```text
DeepSearch / research CSVs / presentacion merges
        │
        ▼
lead_research_builder ──► lead_research_prospect (+ evidence, block_reason, batch)
        │
        ├─ operational sidecars (exact-email safety + outreach memory)
        │     contact_email_suppression
        │     contact_domain_suppression
        │     outreach_contact_state
        │
        ▼
lead_research_operational_overlay  (display/mirror overlay; not send approval)
        │
        ▼
commercial_action_buckets
  ready_to_contact | needs_email_enrichment | tender_opportunity
  review_history | already_contacted | blocked
        │
        ├─ Postgres lead_intel mirror loaders
        ├─ apps/api mirror lead routes (GET-only)
        └─ apps/dashboard Prospectos filters + CSV export queues

Parallel evidence planes (not the same as action buckets):

emails (canonical OrigenLab Gmail vs legacy Labdelivery mbox/PST)
  → business mart: contact_master / organization_master
  → commercial intel v1: signal facts / rollups / opportunity facts
  → commercial_deal / commercial_purchase_events (when present)
  → Chilecompra / equipment-first tender CSVs (+ public_tender_review prospects)
```

### Important field contract

| Field / store | Source of truth? | Observed vs inferred | Builder / refresh | Manual override? | Used for |
| --- | --- | --- | --- | --- | --- |
| `emails` | Yes (message evidence) | Observed | IMAP / mbox ingest | No | Evidence |
| `contact_email_suppression` | Yes (safety) | Observed (NDR/operator) | NDR tools / CRUD | Yes (operator) | Safety |
| `contact_domain_suppression` | Yes (safety) | Observed | Operator tools | Yes | Safety |
| `outreach_contact_state` | Yes (lifecycle memory) | Observed / backfill | Outreach sync | Limited | Safety / anti-repeat |
| `lead_research_prospect.classification` | No (rebuildable queue) | Inferred at import + overlay | Research builders + overlay | Via rebuild/overlay | Presentation / workflow |
| `commercial_action_bucket` | No (derived) | Inferred | `commercial_action_buckets.py` | No | Prioritization / UI |
| `contact_master` quote/invoice/purchase counts | Derived mart | Inferred from mail/docs | `build_business_mart.py` | No | Presentation / audit evidence |
| Commercial intel rollups | Derived | Inferred signals | `build_commercial_intel_v1.py` | Candidate review tables | Discovery |
| Chilecompra queues | CSV/API publish artifacts | Observed tenders + enrichment | equipment-first / chilecompra publish | Operator CSV | Tender review |
| Postgres / dashboard | Read model | Mirror of SQLite overlays | Mirror refresh scripts | No | Presentation |

**Golden rule (unchanged):** never gate sends on `lead_research_prospect.classification` alone.

---

## 2. What it does well

- Strong **exact-email** anti-repeat and bounce/suppression safety when operators refresh sidecars.
- Clear separation in docs between evidence, safety, and UI opinions (`SCHEMA_CLASSIFICATION_MODEL.md`).
- Operational overlay correctly elevates exact suppressions and contacted outreach state for Prospectos display.
- Tender rows can be held in `tender_opportunity` without forcing cold outreach.
- Commercial intel v1 and business mart already store many of the signals needed for later stage models (quote / procurement / technical / invoice).

---

## 3. Where evidence is lost

- **`already_contacted` collapses many commercial realities** whenever `gmail_sent_count > 0` or `gmail_received_count > 0` (see `derive_commercial_action_bucket`).
- Quote / purchase / fulfilment evidence in `contact_master` and commercial intel is **not** used by the action-bucket layer.
- Labdelivery archive rows share the `emails` table but are easy to treat as generic “history” rather than recoverable customer relationships.
- Product interest is fragmented across `product_angle`, `likely_need`, `top_equipment_tags`, campaign labels — not a durable account interest object.
- Tender ↔ account linkage is mostly prospect/CSV coincidence, not a first-class account link with confidence.
- Open inbound threads often keep generic next-action text (“esperar respuesta” / “inspeccionar historial”) with no stage-specific task.

---

## 4. Which classifications are overloaded

| Bucket / class | Overloaded with |
| --- | --- |
| `already_contacted` | Campaign recipients, unanswered first touches, qualified inquiries, quotes, purchase-pending, customers, fulfilment |
| `review_history` | Same-domain caution, legacy contacts, active-case holds, ambiguous research |
| `classification` values like `old_gmail_prospect_review` | Safety + history + workflow mixed |
| Dashboard “Prospectos” queues | Prioritization presentation mistaken for CRM stage truth |

Audit-only candidate split (not implemented in production):

- `audit_relationship_state` — customer/knowledge relationship (tender does **not** overwrite this)
- `audit_commercial_stage` — current stage only when dated/explicit; else `customer_history` / `commercial_history` / `unknown`
- `audit_procurement_context` — `none` / `tender_watch` / `tender_active` / `historical_tender`
- `audit_safety_state`
- `audit_already_contacted_breakdown`
- Stage evidence fields: `stage_evidence_type`, `stage_evidence_at`, `stage_evidence_source`, `stage_confidence`, `stage_is_current`

---

## 4b. Methodology hardening (post-review)

Corrected so headlines do not overstate evidence:

1. **Duplicates** — `duplicate_occurrence_count = sum(max(count-1,0))` over **valid** emails only; missing emails are never duplicates.
2. **Procurement vs relationship/stage** — tender evidence sets `audit_procurement_context` only.
3. **Lifetime counts ≠ current fulfilment** — `quote_email_count` / `invoice_email_count` / `purchase_email_count` are historical; current fulfilment requires dated `commercial_deal` status or explicit recent logistics evidence.
4. **Multi-deal selection** — exact-email before institutional domain; active before terminal; newest timestamp; stable ID tie-break; consumer-domain fallback refused.
5. **Batch readiness** — `safe_ready_with_explicit_interest` intersection; `provisional_batch_ready_flag` uses that intersection only.
6. **Cohort ≠ campaign** — `prospect_source_batch_quality.csv` is prospect-source cohort quality; `sent_campaign_quality.csv` is the Sent-folder report. Do not publish cohort duplicate rates as campaign recipient rates.
7. **Metric confidence** — every headline in `summary.json` carries `observed` / `derived_*` / `heuristic` / `unavailable` plus definition/denominator.
8. **Output path** — `--output-dir` must be under gitignored `reports/out/` unless `--allow-output-outside-report-root`.

---

## 5. Does the dashboard represent Tatiana’s workflow?

Partially for **outreach safety triage**, poorly for **commercial case management**.

Tatiana’s real questions look like:

1. Who is safe to email?
2. Who do we already know (OrigenLab / Labdelivery)?
3. What is the live opportunity stage?
4. What is the next human action?
5. Which accounts fit a product batch (e.g. centrifuges) and why?

Today’s Prospectos buckets answer (1) reasonably and (2)/(3)/(4)/(5) only weakly by overloading history into `already_contacted` / `review_history`.

---

## 6. Can current data create trustworthy product-specific batches?

**Sometimes, with low trust unless human-reviewed.**

Signals exist (`product_angle`, `likely_need`, mart equipment tags, campaign labels, tender text), but:

- many campaign recipients lack an explicit product-fit reason;
- interest is not versioned with evidence date/confidence at account level;
- consumer emails cannot safely join to institutions by domain;
- safety must still be re-checked from suppression/outreach sidecars, not from Prospectos classification.

The audit CLI emits `product_interest_inventory.csv` and `batch_readiness.csv` to quantify this without inventing interest (`unknown` when insufficient).

---

## 7. Data-quality and bounce risks

Measured by the audit CLI (local production runs stay gitignored):

- **Prospect-source cohort** duplicate rate of **valid** email rows (missing emails excluded).
- Cohort rows currently bounced/suppressed (current state — **not** proven “suppressed before send”).
- Missing product-interest provenance.
- Actual Sent-folder campaign quality in `sent_campaign_quality.csv` (subject+month grouping; no hard-coded subject allowlist).

This PR **does not** apply NDRs or change suppression state.

**Do not** treat prospect cohort duplicate rates as Gmail campaign recipient duplicate rates.

---

## 8. Labdelivery archive value

Recoverable value is real but mixed:

- historical quotation / invoice / purchase counts in the mart;
- legacy mailbox tier (`contacto@labdelivery` source paths);
- dormant customer candidates vs generic historic addresses.

The audit distinguishes relationship candidates (`labdelivery_relationships.csv`) and flags addresses now bounced/suppressed. It does **not** auto-promote archive addresses into outreach queues.

---

## 9. Tender integration gaps

- `public_tender_review` → `tender_opportunity` is a coarse hold bucket.
- Institution/contact enrichment is uneven (many tender rows lack usable email).
- Links to Gmail/Labdelivery history are opportunistic.
- Closed tenders as market intelligence are not modeled separately from active opportunities.

Audit output: `tender_account_links.csv` with confidence + reason codes; no auto ready-to-contact promotion.

---

## 10. Recommended future architecture (proposal only)

Keep safety tables authoritative. Add **separate read models** (later PRs):

1. **Account** — institution identity, aliases, domains (consumer domains excluded from auto-join).
2. **Contact** — people/emails linked to accounts with role + email quality.
3. **Opportunity** — commercial stage with evidence pointers (quote/PO/invoice/tender).
4. **Procurement signal** — Mercado Público / Chilecompra linked to accounts with confidence.
5. **Next-action task** — operator queue item distinct from safety state.
6. Dimensions: relationship × commercial-stage × **procurement context** × safety × product-interest (replace overloaded single bucket).
7. Optional read-only MCP/ChatGPT over the audit/read models — never send.
8. Revised dashboard queues fed by those dimensions — not by “any Gmail history ⇒ already_contacted”.

### Suggested later PR sequence (stop after PR1)

| PR | Scope |
| --- | --- |
| **PR1 (this)** | Read-only commercial truth audit + lineage docs + synthetic tests |
| PR2 | Account/contact identity read model (no send changes) |
| PR3 | Opportunity stage model from existing mart/intel/deal evidence |
| PR4 | Tender↔account linking with confidence + enrichment queue |
| PR5 | Product-interest evidence object + batch builder (human review gate) |
| PR6 | Dashboard queue redesign consuming the new dimensions (still GET-only) |
| PR7 | Optional read-only assistant/MCP over the commercial read model |

---

## Audit CLI

```bash
cd apps/email-pipeline
uv run python scripts/qa/audit_commercial_truth.py \
  --sqlite-path /explicit/path/to/emails.sqlite \
  --output-dir reports/out/active/current/commercial_truth_audit
```

`--sqlite-path` and `--output-dir` are **required**. There is no silent fallback to `ORIGENLAB_SQLITE_PATH`.
`--output-dir` must resolve under gitignored `reports/out/` unless `--allow-output-outside-report-root` is set.

Outputs (emails redacted in CSVs):

- `summary.json`, `audit_report.md` (metrics include confidence + definitions)
- `source_inventory.csv`, `source_overlap.csv`
- `account_identity_conflicts.csv`, `contact_identity_conflicts.csv`
- `classification_distribution.csv`, `classification_conflicts.csv`
- `already_contacted_breakdown.csv`, `opportunity_stage_candidates.csv`
- `open_thread_without_next_action.csv`
- `bounce_leakage.csv`
- `prospect_source_batch_quality.csv` (prospect cohorts — **not** Sent campaigns)
- `sent_campaign_quality.csv` (canonical OrigenLab Sent + NDR correlation)
- `product_interest_inventory.csv`, `batch_readiness.csv`
- `labdelivery_relationships.csv`, `tender_account_links.csv`
- `operator_review_sample.csv`

Production-derived reports under `reports/out/` remain **gitignored**.

---

## Safety confirmation for this PR

- No Gmail send/draft/label/archive/delete
- No production SQLite mutation
- No production Postgres mutation / migrations
- No dashboard behaviour change
- No classification builder change
- No suppression/NDR apply
- No deployment / systemd / cron / Cloudflare changes
