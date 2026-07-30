# Commercial Procurement Link Read Model — PR4

Status: audit/design checkpoint (no production persistence yet)  
Owner: email-pipeline-maintainers  
Date: 2026-07-30  
Branch: `feat/commercial-procurement-linking-pr4`  
Starting main: `d3ce0ae366a680f0ce77d9cecca1b7a10b37d99a`

Related:

- [`COMMERCIAL_TRUTH_AUDIT_PR1.md`](COMMERCIAL_TRUTH_AUDIT_PR1.md)
- [`COMMERCIAL_IDENTITY_READ_MODEL_PR2.md`](COMMERCIAL_IDENTITY_READ_MODEL_PR2.md)
- [`COMMERCIAL_OPPORTUNITY_STAGE_READ_MODEL_PR3.md`](COMMERCIAL_OPPORTUNITY_STAGE_READ_MODEL_PR3.md)
- [`CHILECOMPRA_EQUIPMENT_REFRESH.md`](../operator/CHILECOMPRA_EQUIPMENT_REFRESH.md)
- [`LEAD_PIPELINE.md`](../leads/LEAD_PIPELINE.md)
- [`SCHEMA_OWNERSHIP.md`](../pipeline/SCHEMA_OWNERSHIP.md)
- [`CRUD_SAFETY.md`](../CRUD_SAFETY.md)

**PR4 models PROCUREMENT CONTEXT, not CRM opportunity stage.**

Hard invariants:

- A tender does not prove an existing commercial relationship.
- A tender does not create or advance a PR3 opportunity.
- A closed tender remains procurement/market history.
- A tender buyer may link to a PR2 account; the tender must not mutate that account.
- Suppression/outreach state is not identity and is not copied into PR4.
- Consumer-email domains never establish institutional membership.
- Marketplace URLs (`mercadopublico.cl` / `chilecompra.cl`) are not buyer institution domains.
- Build time is never substituted for missing tender dates.
- Ambiguity becomes a conflict/review row, not an automatic merge.
- No row becomes ready-to-contact merely because a tender exists.

Terminology:

| Term | Meaning |
|------|---------|
| procurement signal | Canonical tender/procurement observation |
| account link | Optional link to `commercial_identity_account` |
| procurement context | `none` / `tender_watch` / `tender_active` / `historical_tender` / `unknown` |
| enrichment queue | Missing/ambiguous fields requiring human research |
| evidence | Exact source pointers for every link or refusal |

---

## 1. Current lineage (three lanes)

```text
Lane A (SQLite leads):
  ChileCompra file → external_leads_raw → lead_master (tender_buyer)
  → lead_account_* (public-buyer rollup; NOT PR2)

Lane B (equipment operator path):
  Mercado Público API / Licitacion CSV → equipment_first_* artifacts
  → Postgres commercial.equipment_opportunity* → API/dashboard Tenders
  (does not write SQLite; ICP-filtered; not the full historical corpus)

Lane C (research presentation):
  DeepSearch → lead_research_prospect (public_tender_review)
  → commercial_action_buckets.tender_opportunity
  → PR1 tender_account_links.csv (audit overlay only)
```

There is **no** dedicated SQLite `tenders` table today.

---

## 2. Canonical-source decision

| Rank | Source | Role |
|------|--------|------|
| **Primary** | `external_leads_raw` + `lead_master` where `source_name=chilecompra`, coalesced by `CodigoExterno` (from `raw_json`) | Full file corpus in SQLite; tender key + buyer name; status/dates in `raw_json` |
| **Secondary** | ChileCompra API / equipment-first CSV → Postgres equipment opportunities | Strong status/date fields for **active equipment** tenders; not SQLite identity truth |
| **Excluded as canonical** | `lead_research_prospect` (`public_tender_review`) | No tender id column; research/presentation |
| **Excluded** | `lead_account_*` | Separate lead rollup; PR4 links to **PR2** accounts |
| **Isolation only** | `commercial_opportunity_*` | Prove PR4 does not mutate/reinterpret PR3 stages |
| **Link target only** | `commercial_identity_*` (PR2) | Never mutated by PR4 |

**Do not combine all planes blindly.** Coalesce line items → one procurement signal per `(source_system, canonical_tender_key)` before linking.

Audit tooling (read-only):

```bash
cd apps/email-pipeline
uv run python scripts/qa/audit_commercial_procurement_link.py \
  --sqlite-path "$DB" \
  --output-dir reports/out/active/current/commercial_procurement_link_audit_2026-07-30
```

Requires explicit `--sqlite-path` + `--output-dir`. Opens SQLite with `mode=ro` and `PRAGMA query_only=ON`.

---

## 3. Production validation checkpoint (2026-07-30)

Dated read-only measurement against canonical production SQLite after Gate A/B persistence. **Checkpoint only** — not a shipped metric contract.

| Metric | Value |
|--------|------:|
| ChileCompra line rows (`lead_master`) | 17643 |
| Coalesced tender keys | 17643 |
| Multi-line tenders (same `CodigoExterno`) | 0 |
| Buyer-name variants on same tender | 0 |
| Missing tender id lines | 0 |
| Missing buyer name lines | 0 |
| Missing status lines | 1195 |
| Missing date lines | 1195 |
| Consumer-email observations | 0 |
| Marketplace-domain observations | 0 |
| Procurement context: `historical_tender` | 16448 |
| Procurement context: `unknown` | 1195 |
| Procurement context: `tender_active` / `tender_watch` | 0 / 0 |
| Route A exact institutional domain | 12 |
| Route C exact alias | 94 |
| Route F no match | 17536 |
| Route I name/domain conflict | 1 |
| Auto-link-allowed signals (policy) | 99 |
| Unique auto-linkable PR2 accounts | 9 |
| Enrichment queue candidates | 17544 |
| Persisted PR2 identity fingerprint | `identity_fp_v2` / `6907341d…` (matched) |
| Procurement source fingerprint | `procurement_source_fp_v1` / `7fd9c556…` |

Notes:

- This corpus behaves as **one row ≈ one tender key** (no multi-line coalesce observed). Coalesce-by-`CodigoExterno` remains mandatory for general ChileCompra CSVs that emit multiple lines per tender.
- Almost all structured rows are **historical**; status/date gaps map to `unknown`, never to `tender_active`.
- Most buyers do not yet match PR2 commercial identity (mart/deal-heavy). That is expected and drives the enrichment queue — not automatic outreach.

Gitignored artifacts: `reports/out/active/current/commercial_procurement_link_audit_2026-07-30/`.

---

## 4. Schema contract (proposed — not applied)

Namespace: `commercial_procurement_*`  
`schema_version`: `commercial_procurement_v1`  
`build_contract`: `procurement_account_link_read_model_v1`  
Transaction: **`B_schema_additive_data_atomic`**

Prefer **separate** signal and account-link tables.

| Table | Purpose |
|-------|---------|
| `commercial_procurement_signal` | One canonical tender observation |
| `commercial_procurement_account_link` | Optional PR2 account link + route/confidence |
| `commercial_procurement_evidence` | Source pointers (no bodies) |
| `commercial_procurement_conflict` | Ambiguity / policy refusals |
| `commercial_procurement_enrichment_queue` | Human research queue (not send-ready) |
| `commercial_procurement_build_meta` | Schema, fingerprints, run_context, metrics |

Stable IDs (evaluate; never autoincrement semantics):

- signal: `v1|procurement|{source_system}|{canonical_tender_key}` → `p_` + sha256[:32]
- link/evidence/conflict/queue: deterministic hashes of subject ids + reason + source pointers

**Non-relationship to PR3:** no writes to `commercial_opportunity_*`; procurement_context is independent of `canonical_stage`.

**Relationship to PR2:** soft FK to `commercial_identity_account.account_id` for links only; apply requires matching persisted `identity_fp_v2`.

Full column contract: see package `schema_contract.py` / audit `proposed_schema.md`.

---

## 5. Matching precedence (no fuzzy / LLM)

1. Refuse consumer / internal / marketplace domains (`H`).
2. Name vs institutional-domain conflict → no link (`I`).
3. Exact institutional buyer domain, unique + compatible name → high (`A`).
4. Explicit contact email institutional domain, unique → high (`E`).
5. Exact unique alias → medium (`C`); weak/generic public-unit names → review only.
6. Exact unique canonical name → medium (`B`); weak names → review.
7. Unique compatible name across alias∪canonical → medium (`D`).
8. Multiple accounts → ambiguous (`G`).
9. Else unlinked + enrichment (`F`).

Never: substring-only auto-link; merge distinct institutional domains by name; region-alone identity; attach consumer emails to institutions.

---

## 6. Consumer / internal / marketplace policy

Reuse PR2 helpers:

- `CONSUMER_EMAIL_DOMAINS` / `is_consumer_domain`
- `INTERNAL_DOMAINS` (`origenlab.cl`, `labdelivery.cl`)
- Marketplace: `mercadopublico.cl`, `chilecompra.cl` (+ www/api hosts)

Marketplace domains are stripped during buyer-domain sanitization (same spirit as `normalize_chilecompra`).

---

## 7. Procurement-status policy

Structured fields only (`CodigoEstado` / `Estado` / `FechaCierre` / publication dates from `raw_json` or API):

| Context | When |
|---------|------|
| `historical_tender` | Inactive codes `{6,7,8,18,19}` or closed-like names; or close date in the past |
| `tender_active` | Publicada (`5`) **and** parsed close date ≥ today |
| `tender_watch` | Publicada without close date; or future close without publicada |
| `unknown` | Missing/malformed status+dates |

Line-item text may describe product relevance but **cannot** establish lifecycle status.  
`lead_master.status` is a **workflow** field (`nuevo`, …) — not ChileCompra lifecycle.

---

## 8. Confidence and reason codes

Confidence: `high` | `medium` | `low` | `none`.

Enrichment/conflict reasons (non-exhaustive):

- `buyer_account_not_found`
- `buyer_name_ambiguous`
- `buyer_domain_missing`
- `buyer_domain_conflicts_with_name`
- `buyer_contact_missing`
- `consumer_email_link_withheld`
- `marketplace_domain_ignored`
- `internal_domain_refused`
- `tender_identifier_missing`
- `tender_status_unknown`
- `tender_dates_missing_or_malformed`
- `duplicate_source_records_need_review`
- `weak_generic_public_unit_name`
- `line_items_coalesced_to_tender`

Queue priority uses evidence completeness (active/watch context, domain/email presence, multi-line), **not** sales scoring. No next-action / send-readiness fields.

---

## 9. Fingerprint and apply gates (proposed)

| Gate | Algorithm / rule |
|------|------------------|
| Procurement source FP | `procurement_source_fp_v1` over raw keys, lead keys, coalesced tender keys |
| PR2 identity FP | Require persisted `identity_fp_v2` match (same spirit as PR3) |
| Stale plan | Recompute source + identity FPs inside write txn; mismatch → refuse/rollback |
| CLI | Explicit `--sqlite-path`; dry-run default; `--apply`; `--run-context` |
| Exit codes (proposed) | 2 path/context; 3 identity snapshot; 4 source schema; 5 stale plan |
| Immutability | Assert `commercial_deal*`, `commercial_identity_*` data, `commercial_opportunity_*` unchanged by PR4 apply |
| PR3 | Must not require PR3 to link; must verify no stage mutation/reinterpretation |

---

## 10. Validation metrics (future apply)

- Signal count == coalesced tender keys
- Link/conflict/queue counts match dry-run plan
- FK check empty
- Source + identity fingerprints recorded in build_meta
- Explicit redacted samples for each route
- No PR3 stage distribution drift
- No Gmail/Postgres/suppression/outreach/classification/deal mutation

---

## 11. Known limitations

- SQLite chilecompra corpus may lack active publicada+future-close rows (checkpoint: zero active/watch).
- Equipment API lane has better live status/dates but is ICP-filtered and outside SQLite.
- PR2 identity coverage of public buyers is currently sparse → large enrichment queue.
- `lead_master` workflow status must not be confused with procurement context.
- Research prospects lack tender identifiers.

---

## 12. Explicit exclusions

- API / dashboard / Postgres publication changes
- Send path / ready-to-contact
- PR5 product-interest
- Fuzzy or LLM matching
- Production `--apply` of `commercial_procurement_*` in this checkpoint

---

## 13. Implementation plan (next commits / PRs)

1. ~~Read-only source audit + synthetic tests + this contract~~ (this checkpoint)
2. Pure resolver + stable IDs + fingerprint module (no production apply)
3. SQLite DDL + persist (dry-run default CLI)
4. Focused + full tests; operator dry-run on production
5. Separate Gate authorization for first `--apply`
6. Optional later: join equipment API plane as secondary evidence without replacing SQLite corpus

Commit sequence for this checkpoint:

1. Audit tooling for procurement/account-link sources
2. Synthetic tests for audit invariants
3. This design document

---

## 14. Safety confirmation (checkpoint)

- Production SQLite opened read-only only
- No PR2/PR3 `--apply`
- No mutation of identity/opportunity/deal/suppression/outreach/classification
- No Gmail/Postgres/dashboard behaviour change
- No PR5 / deploy / systemd / cron / Cloudflare changes
