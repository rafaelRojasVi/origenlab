# Commercial Procurement Link Read Model — PR4

Status: persistence implemented (fixture/apply gated); **no production --apply executed**
Owner: email-pipeline-maintainers
Date: 2026-07-30
Branch: `feat/commercial-procurement-persistence-pr4`
Starting merge (planner): `159e469188e492d25d0a6f625fcb1d6f4a67f53b`

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
| account resolution | One deterministic row per signal: linked / unlinked / ambiguous / refused |
| procurement context | `none` / `tender_watch` / `tender_active` / `historical_tender` / `unknown` (build-plan derived) |
| enrichment candidate | Rebuildable research hint (not mutable operator lifecycle) |
| evidence | Exact source pointers for every resolution or refusal |

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

## 3. Production validation checkpoint (2026-07-30, final design hardening)

Dated read-only measurement against canonical production SQLite after Gate A/B persistence. **Checkpoint only** — not a shipped metric contract. Fingerprints below supersede earlier checkpoint hashes (algorithms had not shipped).

| Metric | Value |
|--------|------:|
| `raw_chilecompra_rows` | 17643 |
| `lead_chilecompra_rows` | 17643 |
| `matched_raw_lead_rows` | 17643 |
| `raw_only_rows` / `lead_only_rows` | 0 / 0 |
| `tender_key_kind`: `codigo_externo` | 16448 |
| `tender_key_kind`: `unresolved_tender_key` | 1195 |
| `coalesced_verified_tenders` | **16448** |
| `multi_line_verified_tenders` | 0 |
| Procurement context: `historical_tender` | 16448 |
| Route A / C / F / I | 8 / 41 / 16398 / 1 |
| Resolution linked / unlinked / ambiguous | 42 / 16405 / 1 |
| Auto-link-allowed signals | 42 |
| Unique linked PR2 accounts | 8 |
| `account_not_linked_total` | 16406 |
| `data_quality_conflict_total` | 1196 |
| `enrichment_candidate_total` | 16406 |
| `operator_queue_eligible_total` | **0** |
| Source FP line count (all outcomes) | 17643 |
| Verified component sha256 | `c3389e3a…` |
| Unresolved component sha256 | `14ca9804…` |
| Persisted PR2 identity fingerprint | `identity_fp_v2` / `6907341d…` |
| Source fingerprint (`procurement_source_fp_v1`) | `77a43838a58fdfe4089e2a187a8b04d223e9185ed2bb9b6d385e74a026543541` |
| Build-plan fingerprint (`procurement_build_plan_fp_v1`) | `afcbea179949ee4653e08040daaef1457a65ffca99af534f016bd67cd0c9be67` |
| Resolver | `procurement_resolver_v3` |
| As-of date | `2026-07-30` (UTC calendar date) |

Notes:

- Source fingerprint hashes **all** source-line outcomes (verified + unresolved + raw-only + lead-only + malformed), not only coalesced signals.
- Derived `procurement_context` and account-resolution results are build-plan concerns (`as_of_date` + resolver version + identity FP).
- Unresolved-key conflicts carry direct provenance (`source_system` + `source_record_id` + `subject_key`); conflict ID = hash of `v1|procurement-conflict|…`.
- Operator-queue eligible remains **0** for unmatched historical tenders.

Gitignored artifacts: `reports/out/active/current/commercial_procurement_link_audit_2026-07-30/`.

---

## 4. Schema contract (proposed — not applied)

Namespace: `commercial_procurement_*`
`schema_version`: `commercial_procurement_v1`
`build_contract`: `procurement_account_resolution_read_model_v1`
`resolver`: `procurement_resolver_v3`
Transaction: **`B_schema_additive_data_atomic`**

Prefer **separate** signal and account-resolution tables.

| Table | Purpose |
|-------|---------|
| `commercial_procurement_signal` | One verified tender observation |
| `commercial_procurement_account_resolution` | One resolution row per signal (`linked`/`unlinked`/`ambiguous`/`refused`) |
| `commercial_procurement_evidence` | Source pointers; supports subjects without `procurement_id` |
| `commercial_procurement_conflict` | Ambiguity / policy / line / unresolved-key conflicts |
| `commercial_procurement_enrichment_candidate` | Rebuildable research candidates (Option B) |
| `commercial_procurement_build_meta` | Schema, source FP, build-plan FP, as_of_date, identity FP |

**Resolution CHECK semantics:**

- `linked` ⇒ `account_id IS NOT NULL`, `auto_link_allowed=1`, route ∈ {A,B,C,E}
- `unlinked` / `ambiguous` / `refused` ⇒ `account_id IS NULL`, `auto_link_allowed=0`
- Ambiguity candidates live in `candidate_account_ids_json`, never as a selected `account_id`

**PR2 reference:** logical account reference (not physical SQLite FK). Physical FKs remain required within `commercial_procurement_*`. Independent PR2 DELETE+INSERT rebuildability is the reason — see `schema_contract.py` / `PR2_LOGICAL_REFERENCE_NOTE`.

Apply-time validation: identity schema exists; `identity_fp_v2` matches; every linked `account_id` exists in `commercial_identity_account`; rechecked inside the write transaction; identity FP recorded in build_meta.

Stable IDs (never autoincrement semantics):

- signal: `v1|procurement|{source_system}|{canonical_tender_key}`
- resolution / evidence / candidate: deterministic hashes of subject ids + reason + pointers
- unresolved conflict: `v1|procurement-conflict|{source_system}|{source_record_id}|{reason_code}`

**Non-relationship to PR3:** no writes to `commercial_opportunity_*`.

Full column contract: package `schema_contract.py` / audit `proposed_schema.md`.

---

## 5. Matching precedence (no fuzzy / LLM)

1. Strip refused consumer / internal / marketplace domains from institutional evidence; record auxiliary reasons (e.g. `consumer_email_ignored_for_account_identity`). They do **not** block an independently valid exact institutional domain or compatible exact-name match.
2. Name vs institutional-domain conflict → no link (`I`).
3. Exact institutional buyer domain, unique → high (`A`). Multiple domain accounts → ambiguity (`G`).
4. Explicit contact email institutional domain, unique → high (`E`).
5. Exact unique alias → medium (`C`) → `linked` when auto-allowed; weak names → `unlinked` review
6. Exact unique canonical name → medium (`B`) → same
7. Route **D removed**
8. Route `H` → `refused` only when no stronger institutional evidence remains
9. Else `unlinked` (`F`) with enrichment candidates

Routes A/B/C/E map to `linked` only when `auto_link_allowed`. F→unlinked, G/I→ambiguous, H→refused.

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

## 9. Fingerprint and apply gates

Three payload levels:

| Level | Contents |
|-------|----------|
| A. Source-line semantic payload | One row per joined ChileCompra source outcome (verified, unresolved, raw-only, lead-only, malformed). Includes **field-level provenance markers** (`external_leads_raw` / `lead_master` / `both_equal` / `absent` / `conflict`). Excludes `lead_id`, build timestamps, `as_of_date`, identity FP, derived `procurement_context`, resolver version. Contact emails enter as hashes only. |
| B. `procurement_source_fp_v1` | Order-independent hash of **all** Level-A payloads (component hashes for verified vs unresolved). |
| C. `procurement_build_plan_fp_v1` | Source FP + persisted `identity_fp_v2` + `as_of_date` + resolver version (`procurement_resolver_v4`). |

| Gate | Rule |
|------|------|
| Schema | Tables absent → additive first-run; present+compatible → continue; present+incompatible → refuse (exit 4); no silent ALTER |
| Stale plan | Expected approvals vs preflight; preflight vs live `BEGIN IMMEDIATE` plan; readback semantic digest vs live — mismatch → exit 5, rollback, **no DELETE** until checks pass |
| Production apply | `--run-context production_apply` + `--apply` + four `--expected-*` values from an approved dry-run |
| CLI | Explicit `--sqlite-path`; `--as-of-date`; dry-run default; `--apply`; `--run-context` |
| Immutability | Assert deals, identity data, opportunity stages, lead/raw sources unchanged by PR4 apply |
| PR3 | Must not require PR3 to link; must verify no stage mutation |
| Transaction | `B_schema_additive_data_atomic`: schema ensure outside data txn; `foreign_keys=ON`; `BEGIN IMMEDIATE`; clear children-first; insert parents-first; validate; commit |

Exit codes: `0` success; `2` path/mode; `3` identity; `4` schema; `5` stale plan; `6` unsafe invocation; `7` plan/persistence validation.

Resolver: `procurement_resolver_v4` (field provenance corrections vs v3).

---

## 10. Validation metrics (apply)

- Signal count == coalesced **verified** tender keys
- Unresolved keys recorded as conflicts, not signals
- Link/conflict/candidate counts match dry-run plan
- FK check empty; every linked/candidate account exists in PR2 (logical, no physical FK)
- Source + build-plan + identity fingerprints + semantic/materialization digests in build_meta
- No PR3 stage distribution drift
- No Gmail, Postgres, suppression, outreach, classification, or deal mutation
- **No production `--apply` in this PR** — production checkpoint is dry-run only

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
- **First production `--apply` of `commercial_procurement_*`** (separate authorization)
- Mutable operator lifecycle state in rebuildable enrichment tables

---

## 13. Implementation plan

1. ~~Read-only source audit + synthetic tests + initial contract~~
2. ~~Corrected tender-key grain, linking, fingerprints, Option B queue~~
3. ~~Final design hardening: source-line FP, account_resolution, unresolved provenance~~
4. ~~Pure resolver + stable IDs (planner dry-run; PR #418)~~
5. ~~SQLite DDL + persist (dry-run default CLI; this PR)~~
6. ~~Focused + full tests; operator dry-run on production~~
7. Separate Gate authorization for first production `--apply`

---

## 14. Safety confirmation (this PR)

- Production SQLite opened **read-only** for the dry-run checkpoint only
- No production `--apply`; no `commercial_procurement_*` tables created in production by this PR
- No PR2 or PR3 `--apply`
- No mutation of identity, opportunity, deal, suppression, outreach, or classification records
- No Gmail, Postgres, or dashboard behaviour change
- No PR5 / deploy / systemd / cron / Cloudflare changes

---

## 15. CI note (facade audit)

The `supplier_workbook` root/core pair exists on clean main and on this branch as a correctly classified `root_implementation_with_subpackage_facade` finding — it is **not** the PR failure. The PR-only failure was basename collision of `readonly.py` / `redaction.py` under `qa/` with the PR1 commercial truth audit package. Renamed to `sqlite_readonly.py` / `output_redaction.py` (distinct ownership; no supplier allowlist).
