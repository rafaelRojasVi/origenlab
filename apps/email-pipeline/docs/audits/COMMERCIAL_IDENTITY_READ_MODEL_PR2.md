# Commercial Identity Read Model — PR2

Status: draft implementation (identity infrastructure only)  
Owner: email-pipeline-maintainers  
Date: 2026-07-28  
Branch: `feat/commercial-identity-read-model-pr2`

Related: [`COMMERCIAL_TRUTH_AUDIT_PR1.md`](COMMERCIAL_TRUTH_AUDIT_PR1.md) · [`LEAD_ACCOUNT_LAYER.md`](../leads/LEAD_ACCOUNT_LAYER.md) · [`SCHEMA_CLASSIFICATION_MODEL.md`](../pipeline/SCHEMA_CLASSIFICATION_MODEL.md) · [`CRUD_SAFETY.md`](../CRUD_SAFETY.md)

**Identity does not imply relationship stage.**  
**Relationship history does not imply an active opportunity.**  
**Suppression does not imply identity.**  
**A shared consumer email domain never proves account membership.**

---

## 1. Problem established by PR1

PR1 showed that prospect/mart/deal planes already contain institutional names, domains, and emails, but there is **no rebuildable canonical identity layer**. Action buckets overload history; consumer domains are unsafe for auto-join; conflicting org names on the same domain or email must not be silently merged.

PR2 builds that identity layer only.

---

## 2. Scope

### In scope

- Deterministic account/contact identity resolver
- Rebuildable SQLite read-model tables (`commercial_identity_*`)
- Evidence / provenance rows
- Conflict / review queue
- Dry-run default CLI with explicit `--sqlite-path` + `--apply`
- Synthetic tests + documentation

### Out of scope (later PRs)

- Opportunity stage / quote / PO / fulfilment / won-lost (PR3)
- Next-action generation
- Tender ↔ account commercial linking (PR4)
- Product-interest / batch readiness (PR5)
- API / dashboard / Postgres mirror / Alembic
- Gmail mutation, sends, suppressions, outreach state, classifications, `commercial_action_bucket`

---

## 3. Source inventory (read-only evidence)

| Source | Role in PR2 | Origin label |
|--------|-------------|--------------|
| `contact_master` | Email + display name + org guess + seen timestamps | `business_mart` (or OrigenLab/Labdelivery when `emails.source_file` can label the address) |
| `organization_master` | Institutional domain + org name guess | `business_mart` |
| `lead_research_prospect` | Research identity assertions | `research` |
| `commercial_deal` (`client_*`) | Deal-party identity fields | `commercial_deal` |
| `emails` | Optional origin labeling via `classify_email_source` | `origenlab_gmail` / `labdelivery_archive` |

**Not used as identity evidence:** `contact_email_suppression`, `contact_domain_suppression`, `outreach_contact_state` (safety / lifecycle memory only).

**Not reused as this CRM identity layer:** `lead_account_*` remains the **lead-master rollup** for public-buyer clustering. PR2 is a separate commercial identity read model spanning mart + research + deals.

---

## 4. Schema / read-model contract

Rebuildable tables (DELETE + INSERT on `--apply`):

| Table | Purpose |
|-------|---------|
| `commercial_identity_account` | Canonical account (`account_id`, names, primary domain, confidence/status, first/last evidence) |
| `commercial_identity_account_alias` | Observed name aliases |
| `commercial_identity_account_domain` | Institutional domains linked to an account |
| `commercial_identity_contact` | Canonical contact (`contact_id`, normalized email, optional account link) |
| `commercial_identity_evidence` | Provenance pointers (source table/id, origin plane, reason, confidence, evidence_at) |
| `commercial_identity_conflict` | Review queue with deterministic reason codes |
| `commercial_identity_build_meta` | Schema version + last build metrics JSON |

No opportunity-stage, next-action, won/lost, or fulfilment columns exist on these tables.

---

## 5. Matching precedence

1. **Exact normalized email** — strongest automatic contact key (case + trim; existing `normalize_valid_email` / export helpers). No Gmail-dot or plus-address transforms.
2. **Institutional domain** — may create/link accounts and link contacts when org evidence is compatible.
3. **Compatible org name** — may link a contact to a name-keyed account **only when exactly one** compatible candidate account exists for that normalized name.
4. **Never** merge solely on vague name similarity, and **never** merge distinct institutional domains solely because normalized org names match.
5. Ambiguity → **conflict / needs_review / ambiguous**, not a silent confident merge.

### Internal-domain policy

Reuse `INTERNAL_DOMAINS` (`origenlab.cl`, `labdelivery.cl`).

- These domains **must not** become external commercial accounts via institutional-domain resolution.
- Contacts on those domains are represented explicitly as **internal actors** (`identity_status=internal_actor`).
- They remain unlinked to commercial accounts and are **not** classified as customer institutions.
- Reason code: `internal_domain_not_commercial_account`.

### Multi-domain / competing-domain policy

Distinguish:

- normalized **email domain**
- explicit source **`domain_raw`**
- candidate **account domains**

Incompatible domain evidence for the same exact email withholds the account link and emits `exact_email_competing_domains` and/or `contact_competing_account_links` with real source evidence pointers.

### Consumer-email organization evidence

A consumer-domain email **never** proves account membership. Explicit organization-name evidence may still create a **low-confidence name-only account**, but the consumer contact is **not** attached. A review conflict (`consumer_email_org_link_withheld`) describes the withheld link.

---

## 6. Consumer-domain policy

Uses PR1 `CONSUMER_EMAIL_DOMAINS` plus `proton.me` (and `protonmail.com`).

Domains such as `gmail.com`, `googlemail.com`, `outlook.com`, `hotmail.com`, `live.com`, `yahoo.com`, `icloud.com`, `proton.me`, `protonmail.com`, … **never** establish institutional account membership by domain alone.

---

## 7. Stable-ID contract

| Entity | Key material | Form |
|--------|--------------|------|
| Contact | `v1\|contact\|{normalized_email}` | `c_` + sha256 hex[:32] |
| Account (domain) | `v1\|account\|domain\|{domain}` | `a_` + sha256 hex[:32] |
| Account (name-only) | `v1\|account\|name\|{normalized_name}` | `a_` + sha256 hex[:32] |
| Evidence / conflict | Deterministic payloads of source pointers / reason + subject keys | `e_` / `x_` + sha256 hex[:32] |

IDs are independent of input ordering and stable across rebuilds with unchanged evidence. Autoincrement is **not** the identity contract.

---

## 8. Evidence and confidence

- `evidence_at` is the **observed** source timestamp when present.
- Missing timestamps stay **missing** — build time is never substituted as evidence time.
- Alias rows track **alias-specific** `evidence_count`, `first_evidence_at`, and `last_evidence_at` (not domain-wide ranges copied onto every alias).
- Confidence labels: `high` / `medium` / `low` / `none`.
- Status labels: `resolved` / `needs_review` / `ambiguous` / `unlinked` / `internal_actor`.
- Origins remain distinguishable: OrigenLab Gmail, Labdelivery archive, research, business mart, commercial deal.
- Research-only rows are never labeled as customers (PR2 has no customer flag; research origin is explicit on evidence).
- Research roles come from `lead_research_prospect.role_title` (canonical), with legacy `role` / `title` fallback for older fixtures.

---

## 9. Conflict handling

Deterministic reason codes include:

- `exact_email_conflicting_organizations`
- `institutional_domain_conflicting_organizations`
- `exact_email_competing_domains`
- `contact_competing_account_links`
- `ambiguous_name_account_candidates`
- `consumer_domain_auto_link_refused`
- `consumer_email_org_link_withheld`
- `internal_domain_not_commercial_account`

Every conflict stores subject keys JSON + evidence pointers JSON for human review.

---

## 10. Rebuild behavior and mutation safety

CLI: `scripts/commercial/build_commercial_identity_read_model.py`

- **Requires** `--sqlite-path` (no `ORIGENLAB_SQLITE_PATH` fallback).
- **Default dry-run** — prints planned row counts; performs **no writes**.
- **Transaction contract B** (`B_schema_additive_data_atomic`):
  - Additive schema (`CREATE TABLE IF NOT EXISTS` via `executescript`) may remain after a first-run failure because SQLite `executescript` auto-commits DDL.
  - Prior read-model **data** is never partially replaced: `PRAGMA foreign_keys=ON`, then `BEGIN` → DELETE+INSERT → `COMMIT`, or full **rollback** of the data replacement on failure.
- Do **not** run `--apply` against production SQLite without explicit operator approval.

Package: `origenlab_email_pipeline.commercial_identity`.

---

## 11. Validation metrics (definitions)

Emitted in dry-run/apply summaries (fixture/local unless a production run is explicitly authorized):

| Metric | Definition / denominator |
|--------|--------------------------|
| `source_identity_rows_inspected` | Source assertions loaded |
| `canonical_account_count` | Distinct `account_id` |
| `canonical_contact_count` | Distinct valid-email contacts |
| `contacts_linked_to_accounts` | Contacts with `account_id` |
| `unlinked_contacts` | Contacts without account link |
| `institutional_domain_links` | Links via institutional domain |
| `consumer_domain_auto_link_refusals` | **Distinct canonical contacts** whose consumer/public email domain refused institutional auto-link (incremented once during contact construction; not during source aggregation) |
| `account_conflicts` / `contact_conflicts` | Conflict rows by class |
| `records_without_usable_email` | Missing/invalid email **assertion rows** |
| `records_without_usable_organization_identity` | Assertion rows without usable org name and without institutional domain |
| `*_origin_source_assertion_rows` | Assertion-row tallies by origin (may overlap across different rows for the same person) |
| `canonical_contacts_with_*_origin` | Distinct contacts with at least one evidence origin of that plane |
| `canonical_contacts_research_only` | Distinct contacts whose **only** origin is research |

Label in metrics: orchestrator-supplied `--run-context` (`synthetic_fixture` | `local_fixture` | `production_dry_run` | `production_apply`). Default CLI value is `local_fixture`. **Run context is metadata, not commercial evidence** — the pure resolver never guesses environment. Metric definitions are also embedded in `metrics["metric_definitions"]`.

Build meta also stores `identity_fingerprint` (order-independent SHA-256 over stable account/contact/evidence/conflict IDs) so PR3 apply can fail closed on missing/stale identity snapshots.

---

## 12. Known limitations

- Does not parse free-text bodies for new emails beyond mart/deal/research fields.
- Does not fuzzy-merge similar institution names (by design).
- Origin labeling from `emails` is best-effort and sender-based.
- `lead_account_*` is not replaced; operators must not confuse the two layers.
- Suppression/outreach state is not copied into this model.
- Internal actors are represented but never treated as external commercial accounts.

---

## 13. Boundary with PR3

PR3 may consume `commercial_identity_*` as the account/contact spine for **opportunity stage** evidence. PR2 must not invent stage, next action, or tender relevance.

PR3 **dry-run** re-resolves identity in memory from the same source assertions (no dependency on persisted identity tables). PR3 **apply** requires a persisted PR2 snapshot whose `schema_version` and `identity_fingerprint` match the in-memory resolution used for the opportunity build.

---

## CLI examples

```bash
cd apps/email-pipeline

# Dry-run (default)
uv run python scripts/commercial/build_commercial_identity_read_model.py \
  --sqlite-path /explicit/path/to/emails.sqlite

# Apply only to an approved non-production fixture
uv run python scripts/commercial/build_commercial_identity_read_model.py \
  --sqlite-path /tmp/fixture.sqlite --apply --json-summary
```
