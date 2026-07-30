# Commercial Procurement Link Read Model — PR4

Status: audit/design correction checkpoint (no production persistence yet)
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
- Suppression, outreach, classification, and deal state are not identity and are not copied into PR4.
- Consumer-email domains never establish institutional membership.
- Marketplace URLs (`mercadopublico.cl` / `chilecompra.cl`) are not buyer institution domains.
- Build time is never substituted for missing tender dates; `--as-of-date` is comparison-only.
- Ambiguity becomes a conflict/review row, not an automatic merge.
- No row becomes ready-to-contact merely because a tender exists.
- Only **verified tender-level** identifiers form canonical procurement signals.

Terminology:

| Term | Meaning |
|------|---------|
| procurement signal | Canonical verified tender/procurement observation |
| account link | Optional link to `commercial_identity_account` |
| procurement context | `none` / `tender_watch` / `tender_active` / `historical_tender` / `unknown` |
| enrichment candidate | Rebuildable research hint (not mutable operator lifecycle) |
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
| **Primary** | `external_leads_raw` + `lead_master` where `source_name=chilecompra`, coalesced by **verified** tender-level keys | Full file corpus in SQLite |
| **Secondary** | ChileCompra API / equipment-first CSV → Postgres equipment opportunities | Strong status/date fields for **active equipment** tenders |
| **Excluded as canonical grain** | Line-level `Codigo` / `Correlativo` / `source_record_id` fallback | `unresolved_tender_key` until proven tender-level |
| **Excluded** | `lead_research_prospect` (`public_tender_review`) | No tender id column |
| **Excluded** | `lead_account_*` | Separate lead rollup; PR4 links to **PR2** |
| **Isolation only** | `commercial_opportunity_*` | Prove PR4 does not mutate/reinterpret PR3 |
| **Link target only** | `commercial_identity_*` (PR2) | Never mutated by PR4 |

Verified tender-level key names (precedence):

1. `CodigoExterno` / `codigo_externo` → `codigo_externo`
2. `CodigoLicitacion` / `codigo_licitacion` → `codigo_licitacion`
3. `Número de Adquisición` / `Numero de Adquisicion` / `numero_adquisicion` → `numero_adquisicion`

Do **not** hardcode raw↔lead as 1:1; measure matched / raw_only / lead_only.

Audit tooling (read-only):

```bash
cd apps/email-pipeline
uv run python scripts/qa/audit_commercial_procurement_link.py \
  --sqlite-path "$DB" \
  --output-dir reports/out/active/current/commercial_procurement_link_audit_2026-07-30 \
  --as-of-date 2026-07-30
```

Requires explicit `--sqlite-path`, `--output-dir`, and `--as-of-date`. Opens SQLite with `mode=ro` and `PRAGMA query_only=ON`.

---

## 3. Production validation checkpoint (2026-07-30, corrected)

Dated read-only measurement against canonical production SQLite after Gate A/B persistence. **Checkpoint only** — not a shipped metric contract.

| Metric | Value |
|--------|------:|
| `raw_chilecompra_rows` | 17643 |
| `lead_chilecompra_rows` | 17643 |
| `matched_raw_lead_rows` | 17643 |
| `raw_only_rows` / `lead_only_rows` | 0 / 0 |
| `duplicate_raw_source_keys` / `duplicate_lead_source_keys` | 0 / 0 |
| `null_or_invalid_raw_json_rows` | 0 |
| Raw key presence: `CodigoExterno` | 16448 |
| Raw key presence: `Codigo` / `Correlativo` | 16448 / 16448 |
| Raw key presence: `source_record_id_fallback` | 1195 |
| `tender_key_kind`: `codigo_externo` | 16448 |
| `tender_key_kind`: `unresolved_tender_key` | 1195 |
| `verified_tender_level_key_rows` | 16448 |
| `unresolved_tender_key_rows` | 1195 |
| `coalesced_verified_tenders` | **16448** |
| `multi_line_verified_tenders` | 0 |
| Procurement context: `historical_tender` | 16448 |
| Procurement context: active / watch / unknown (verified) | 0 / 0 / 0 |
| Route A / C / F / I | 8 / 41 / 16398 / 1 |
| Auto-link-allowed signals | 42 |
| Unique auto-linkable PR2 accounts | 8 |
| `account_not_linked_total` | 16406 |
| `data_quality_conflict_total` | 1196 |
| `enrichment_candidate_total` | 16406 |
| `operator_queue_eligible_total` | **0** |
| Persisted PR2 identity fingerprint | `identity_fp_v2` / `6907341d…` |
| Source fingerprint (`procurement_source_fp_v1`) | `84c9c6e7…` |
| Build-plan fingerprint (`procurement_build_plan_fp_v1`) | `f29d945b…` |
| As-of date | `2026-07-30` (UTC calendar date) |

Notes:

- The earlier headline “17,643 coalesced tenders” was **incorrect** as tender grain: 1,195 rows lack a verified tender-level key and are `unresolved_tender_key`.
- This corpus has **one verified key ≈ one row** (no multi-line coalesce observed). Coalesce-by-verified-key remains mandatory for multi-line ChileCompra formats.
- All verified rows in this checkpoint are **historical**; unresolved rows are data-quality conflicts, not active tenders.
- Operator-queue eligible is **0** because unmatched historical tenders are market history, not immediate tasks.

Gitignored artifacts: `reports/out/active/current/commercial_procurement_link_audit_2026-07-30/`.

---

## 4. Schema contract (proposed — not applied)

Namespace: `commercial_procurement_*`
`schema_version`: `commercial_procurement_v1`
`build_contract`: `procurement_account_link_read_model_v1`
`resolver`: `procurement_resolver_v2`
Transaction: **`B_schema_additive_data_atomic`**

Prefer **separate** signal and account-link tables.

| Table | Purpose |
|-------|---------|
| `commercial_procurement_signal` | One verified tender observation |
| `commercial_procurement_account_link` | Optional PR2 account link + route/confidence |
| `commercial_procurement_evidence` | Source pointers (no bodies); multiple per signal |
| `commercial_procurement_conflict` | Ambiguity / policy refusals / line conflicts |
| `commercial_procurement_enrichment_candidate` | Rebuildable research candidates (**Option B**) |
| `commercial_procurement_build_meta` | Schema, source FP, build-plan FP, as_of_date, metrics |

**Option B (chosen):** PR4 stays fully rebuildable. No mutable `open` / `in_progress` / `resolved` / `dismissed` lifecycle in PR4. Durable operator review events deferred to a later PR if needed.

Production tables store usable `procurement_id`, `account_id`, and safe institution names. Redacted tokens are for audit CSV/JSON only.

Stable IDs (evaluate; never autoincrement semantics):

- signal: `v1|procurement|{source_system}|{canonical_tender_key}` → `p_` + sha256[:32]
- link/evidence/conflict/candidate: deterministic hashes of subject ids + reason + source pointers

**Non-relationship to PR3:** no writes to `commercial_opportunity_*`; procurement_context is independent of `canonical_stage`.

**Relationship to PR2:** soft FK to `commercial_identity_account.account_id` for links only; apply requires matching persisted `identity_fp_v2`.

Full column contract: see package `schema_contract.py` / audit `proposed_schema.md`.

---

## 5. Matching precedence (no fuzzy / LLM)

1. Strip refused consumer / internal / marketplace domains from institutional evidence; record auxiliary reasons (e.g. `consumer_email_ignored_for_account_identity`). They do **not** block an independently valid exact institutional domain or compatible exact-name match.
2. Name vs institutional-domain conflict → no link (`I`).
3. Exact institutional buyer domain, unique → high (`A`). Multiple domain accounts → ambiguity (`G`).
4. Explicit contact email institutional domain, unique → high (`E`).
5. Evaluate full **alias ∪ canonical** candidate set before name routes:
   - multiple accounts → ambiguous (`G`) — including alias account A + canonical account B
   - unique alias hit → medium (`C`); weak/generic public-unit names → review only
   - unique canonical hit → medium (`B`); weak names → review
6. Route **D removed** — unreachable once alias∪canonical is evaluated as one set.
7. Route `H` only when no stronger independent institutional evidence remains.
8. Else unlinked (`F`) with enrichment candidates.

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
| `historical_tender` | Inactive codes `{6,7,8,18,19}` or closed-like names; or close date before `as_of_date` |
| `tender_active` | Publicada (`5`) **and** parsed close date ≥ `as_of_date` |
| `tender_watch` | Publicada without close date; or future close without publicada |
| `unknown` | Missing/malformed status+dates |

`--as-of-date YYYY-MM-DD` is resolved once (UTC calendar date) and passed through all status classification. Build wall-clock is never a substitute for missing tender dates.

Line-item text may describe product relevance but **cannot** establish lifecycle status.
`lead_master.status` is a **workflow** field (`nuevo`, …) — not ChileCompra lifecycle.

---

## 8. Confidence, enrichment, and reason codes

Confidence: `high` | `medium` | `low` | `none`.

Separate metrics:

- `account_not_linked_total`
- `data_quality_conflict_total`
- `enrichment_candidate_total` (multiple reasons per signal allowed)
- `operator_queue_eligible_total` (conservative)

Operator eligibility (proposed):

- active/watch procurement; or
- unresolved but recent structured procurement; or
- explicit ambiguity on an otherwise relevant (non-historical-unmatched) signal

Historical unmatched signals remain market history (`operator_queue_eligible=0`), not immediate tasks.

Enrichment/conflict reasons (non-exhaustive):

- `buyer_account_not_found`
- `buyer_name_ambiguous`
- `buyer_domain_missing`
- `buyer_domain_conflicts_with_name`
- `buyer_contact_missing`
- `consumer_email_link_withheld`
- `consumer_email_ignored_for_account_identity`
- `marketplace_domain_ignored`
- `internal_domain_refused`
- `internal_domain_ignored_for_account_identity`
- `tender_identifier_missing`
- `tender_key_unresolved_line_or_fallback`
- `tender_status_unknown`
- `tender_dates_missing_or_malformed`
- `duplicate_source_records_need_review`
- `line_field_conflict_across_tender_lines`
- `weak_generic_public_unit_name`
- `line_items_coalesced_to_tender`

Priority uses evidence completeness only. No next-action / send-readiness fields.

---

## 9. Fingerprint and apply gates (proposed)

| Gate | Algorithm / rule |
|------|------------------|
| Procurement source FP | `procurement_source_fp_v1` over semantic verified-signal fields only (no `lead_id`, no build timestamps, order-independent) |
| Build-plan FP | `procurement_build_plan_fp_v1` = source FP + identity FP + `as_of_date` + resolver version |
| PR2 identity FP | Require persisted `identity_fp_v2` match |
| Stale plan | Recompute FPs inside write txn; mismatch → refuse/rollback |
| CLI | Explicit `--sqlite-path`; `--as-of-date`; dry-run default; `--apply`; `--run-context` |
| Exit codes (proposed) | 2 path/context; 3 identity snapshot; 4 source schema; 5 stale plan |
| Immutability | Assert deals, identity data, opportunity stages unchanged by PR4 apply |
| PR3 | Must not require PR3 to link; must verify no stage mutation/reinterpretation |

Source fingerprint includes: source system, verified tender key/kind, constituent source IDs, buyer raw/norm, institutional domain, contact email/domain where used, region, title, status code/name, raw+parsed dates, first/last seen, procurement context/reason, line count, material conflict inputs.

---

## 10. Validation metrics (future apply)

- Signal count == coalesced **verified** tender keys
- Unresolved keys recorded as conflicts, not signals
- Link/conflict/candidate counts match dry-run plan
- FK check empty
- Source + build-plan + identity fingerprints recorded in build_meta
- Explicit redacted samples for each route
- No PR3 stage distribution drift
- No Gmail, Postgres, suppression, outreach, classification, or deal mutation

---

## 11. Known limitations

- SQLite chilecompra corpus may lack active publicada+future-close rows (checkpoint: zero active/watch among verified).
- 1,195 rows lack verified tender-level keys in this corpus.
- Equipment API lane has better live status/dates but is ICP-filtered and outside SQLite.
- PR2 identity coverage of public buyers is sparse → large enrichment-candidate count; operator-eligible remains conservative.
- `lead_master` workflow status must not be confused with procurement context.
- Research prospects lack tender identifiers.

---

## 12. Explicit exclusions

- API / dashboard / Postgres publication changes
- Send path / ready-to-contact
- PR5 product-interest
- Fuzzy or LLM matching
- Production `--apply` of `commercial_procurement_*` in this checkpoint
- Mutable operator lifecycle state in rebuildable enrichment tables

---

## 13. Implementation plan (next commits / PRs)

1. ~~Read-only source audit + synthetic tests + initial contract~~
2. ~~Corrected tender-key grain, linking, fingerprints, Option B queue contract~~ (this checkpoint)
3. Pure resolver + stable IDs (still no production apply)
4. SQLite DDL + persist (dry-run default CLI)
5. Focused + full tests; operator dry-run on production
6. Separate Gate authorization for first `--apply`
7. Optional later: join equipment API plane as secondary evidence without replacing SQLite corpus

---

## 14. Safety confirmation (checkpoint)

- Production SQLite opened read-only only
- No PR2 or PR3 `--apply`
- No mutation of identity, opportunity, deal, suppression, outreach, or classification records
- No Gmail, Postgres, or dashboard behaviour change
- No PR5 / deploy / systemd / cron / Cloudflare changes

---

## 15. CI note (facade audit)

The `supplier_workbook` root/core pair exists on clean main and on this branch as a correctly classified `root_implementation_with_subpackage_facade` finding — it is **not** the PR failure. The PR-only failure was basename collision of `readonly.py` / `redaction.py` under `qa/` with the PR1 commercial truth audit package. Renamed to `sqlite_readonly.py` / `output_redaction.py` (distinct ownership; no supplier allowlist).
