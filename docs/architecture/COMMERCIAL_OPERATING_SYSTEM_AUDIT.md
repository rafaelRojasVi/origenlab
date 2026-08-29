# Commercial Operating System Audit

Status: historical / point-in-time audit and evidence record (see status note below)
Performed: 2026-08-29, branch `feat/sales-opportunity-realdata-preview-v1` (base `feat/sales-opportunity-ui-v1` @ `1e46a82`, forked from `main` @ `190fae0`)
Method: read-only investigation of real sources (real SQLite at `/home/rafael/data/origenlab-email/sqlite/emails.sqlite`, ~62GB with live WAL; local repo/git state) plus read/write against the disposable local preview Postgres only (`localhost:55432`). **No production system was written to, migrated, or mutated to produce this document.**

`docs/architecture/CURRENT_SYSTEM_TRUTH.md` is authoritative for CURRENT repository truth. This
document remains a valuable historical/point-in-time audit and evidence record explaining how that
state was reached; where the two disagree, trust `CURRENT_SYSTEM_TRUTH.md`. This document is not
retrospectively rewritten to match later code — instead, findings that were later resolved or
corrected are annotated in place (see "Status note (post-merge corrections)" immediately below and
the inline `>` callouts on Executive summary items 1 and 2).

## Status note (post-merge corrections)

The branch chain this audit describes (Pipeline UI, real-data preview, CRM-4A reconciliation, the
warm-case category contract fix) has since merged to `main` through PRs #521–#526. The findings
below were re-examined by a later whole-repository audit against `main` and are annotated inline
where the original finding no longer matches current code:

- **Executive summary #1** (CRM-4A schema and zero writer) — **RESOLVED** by PR #522.
- **Executive summary #2, `commercial.warm_case*` half** (orphaned, no reader) — **CORRECTED**: the
  read path is live under the Postgres API backend; the writer is opt-in and not part of the
  documented scheduled cron. This is a half-wired/ownership question, not "no reader." No product
  decision has been made on scheduling vs. retiring it.
- **Executive summary #2, `commercial.equipment_opportunity*` half** (no route reads it) —
  **CORRECTED**: the route and read model are still wired end to end; only the primary UI content
  has been removed, leaving a limited count/signal consumer. It has not been retired.
- **"This branch, 40 commits ahead of `main`"** (here and in Phase 9) — the branch chain has since
  merged; see `CURRENT_SYSTEM_TRUTH.md` for current deployment topology. Historical branch/commit
  references below are left as originally written.

## Executive summary

The backend domain model is fundamentally sound and mostly not what needs replacing. The durable
CRM core (`commercial.sales_opportunity` + task/activity/event, optimistic concurrency, idempotent
commands) works correctly end to end. The frontend already has a working, tested, real
Kanban/workspace Pipeline (this branch, 40 commits ahead of `main`) — the "stale dashboard" problem
described going into this audit is **the deployed production dashboard**, not the code in this
repository. The real, worth-fixing problems are narrower and more specific than "rebuild
everything":

1. **CRM-4A (`commercial.organization`/`commercial.contact`) has a schema and zero writer.**
   Promoted sales opportunities have `organization_id`/`primary_crm_contact_id` permanently `NULL`.
   This is the single largest structural gap in the domain model.

   > **RESOLVED by PR #522** (merged to `main`). Sales-opportunity promotion now performs
   > conservative, provenance-based CRM-4A reconciliation in the same transaction — see
   > `docs/architecture/CURRENT_SYSTEM_TRUTH.md` ("Write paths") for current behavior.
2. **Two orphaned/duplicate machine surfaces exist** that look durable but aren't wired to anything:
   `commercial.warm_case*` (Postgres, has a promotion writer that's never scheduled, never read by
   the API) and `commercial.equipment_opportunity*` (DB-1-era CSV batch table, no route reads it).
   Both are dead weight, same shape as the "CRM-4A schema-only" pattern.

   > **CORRECTED.** A later whole-repository audit against `main` found both halves of this
   > finding inaccurate as originally stated:
   > - `commercial.warm_case*`: the Postgres read path IS live under the Postgres API backend
   >   (the production configuration). The writer/promotion job is opt-in and not part of the
   >   documented scheduled cron — a half-wired/ownership question (likely empty or stale in
   >   production), not simply "no reader." No decision has been made on scheduling the writer
   >   vs. retiring the Postgres path.
   > - `commercial.equipment_opportunity*`: the route and read model are still wired end to end;
   >   only the primary UI content has been removed, leaving a limited Today/System count/signal
   >   consumer. It has not been retired.
3. **The "9,577 PR3 opportunities" figure is misleading.** 9,576 of 9,577 are
   `record_kind='commercial_history'` — identity-touch reconstructions with no product/title
   content. Exactly **one** row (`o_254ee22e1f2e2c9ab7f7ef9706729d78`, CEAF/SERVA, linked to the
   only `commercial_deal` row in the whole database) is a genuine, title-bearing, real opportunity.
   This drives the real-data hydration design in Phase 4 (see below) — hydrating "opportunities"
   from the other ~9,576 rows would mean inventing subject matter that doesn't exist in the source
   data, which the task explicitly forbids.
4. **The new-supplier-visibility gap has a precise root cause**: a hand-curated, frozen 90-domain
   Python literal (`SUPPLIER_VENDOR_DOMAINS`) gates whether a domain is even eligible for
   supplier-role classification; a brand-new supplier's first email is invisible to the operator
   Suppliers view unless its content happens to match narrow quote/RFQ text heuristics.
5. **Deployment drift is not a bug.** It's unmerged/undeployed branch work plus a manual,
   non-automated `wrangler deploy` step for the Cloudflare Worker proxy. See Phase 9 below.
6. **No durable customer-quote or supplier-RFQ/offer structures exist yet.** This is by design per
   `TARGET_COMMERCIAL_ARCHITECTURE.md` ("FUTURE commercial.customer_quote" / "FUTURE
   commercial.supplier_offer") — Quotes must stay read-only/evidence-based in the UI, not fabricated.
7. **Documentation drift found and partially fixed in this pass**: `apps/dashboard/README.md` still
   describes the dashboard as "App.tsx → TodayPage only" / "GET only, no write/send/draft/archive
   actions," which was true before this branch's Pipeline work (durable stage transitions, task/
   activity writes, PR3→CRM promotion are all real writes today). `CURRENT_SYSTEM_TRUTH.md`'s
   dashboard-sections table predates the Pipeline page entirely.

## Method and safety

- Five parallel read-only audit passes (Gmail/identity, commercial/CRM core, quotations/suppliers,
  ChileCompra/tenders, other-ops+deployment) were run against the real SQLite using the existing
  `connect_sqlite_readonly` pattern (`apps/email-pipeline/src/origenlab_email_pipeline/qa/commercial_truth_audit/readonly.py`:
  URI `mode=ro` + `PRAGMA query_only=ON`), never a raw read-write connection.
- No production Postgres was contacted at any point (no reachable production DSN was used; where one
  might be configured, it was not connected to).
- The disposable local preview Postgres (`localhost:55432`, container `origenlab-ui-preview-pg`,
  db `origenlab_ui_preview`) was inspected read/write only to confirm its identity and schema
  version (`alembic_version = 20260827_0038`, matching repo head) and to confirm its existing 6
  organizations / 8 sales opportunities are 100% synthetic fixtures (`created_by='preview'`,
  `organization_id` values like `demo-uach`, `demo-pr3-004`). That fixture data was **not** the
  target of any write in this audit pass; see Phase 4 plan below for how it will be isolated.
- A prior session apparently started a "PR3 freshness probe" at `~/.claude/jobs/9b33656b/tmp/
  probe_pr3_freshness.py`. That path does not exist on this machine — it was rebuilt from scratch
  (read-only) rather than assumed to exist, per the standing instruction to verify before trusting
  claimed prior state.

## Domain matrix

Columns: NAME · LAYER · NATURE (durable/rebuildable/evidence/legacy/UI-only) · ROW COUNT (real
SQLite unless noted) · SOURCE OF TRUTH · WRITER · READERS · KEEP/MERGE/DEPRECATE · NOTES.

### Gmail, identity, warm cases

| Name | Nature | Rows | Writer | Readers | Verdict | Notes |
|---|---|---|---|---|---|---|
| `emails` (SQLite) | Evidence, ground truth | not scanned (huge) | Gmail IMAP ingest only | everything downstream | KEEP | Never write outside the ingest script |
| `commercial_identity_account` | Rebuildable | 10,727 | `commercial_identity/builder.py` | contacts/identity consumers | KEEP | Deterministic resolver |
| `commercial_identity_account_alias` | Rebuildable | 10,485 | same | same | KEEP | Domain/name variants per account |
| `commercial_identity_account_domain` | Rebuildable | 10,710 | same | domain→org linking | KEEP | `link_method` records how a domain attached |
| `commercial_identity_contact` | Rebuildable | 27,317 | same | `/contacts/{email}` (indirect) | KEEP | Closest thing today to "is this a known contact," but rebuildable, not durable |
| `commercial_identity_evidence` | Evidence | 94,435 | same | provenance trail | KEEP | Good raw material for a provenance-summary read-model field |
| `commercial_identity_conflict` | Evidence | 5,068 | same | **none found** | KEEP but SURFACE | 5,068 unresolved identity conflicts with no operator-facing view anywhere |
| `cases_review_queue` (virtual) | Rebuildable, computed live | n/a | computed per-request from `emails`+classification | `GET /cases/warm` (Bandeja) | KEEP | The freshest of the two warm-case paths |
| `commercial.warm_case*` (Postgres) | Looks durable, is orphaned | not queried (no prod access) | `warm_case_promotion.py` — **manual CLI only, never scheduled** | **none — no API route reads it** | DEPRECATE or wire up | Same shape as CRM-4A: schema + writer that runs, but nothing downstream consumes it |
| `SUPPLIER_VENDOR_DOMAINS` literal | Legacy/evidence | ~90 domains, frozen | hand-edited by engineers | supplier role classification | MERGE into a maintained/derived source | Root cause of the new-supplier gap (see below) |

### Commercial / CRM core

| Name | Nature | Rows | Writer | Readers | Verdict | Notes |
|---|---|---|---|---|---|---|
| `commercial_opportunity` (SQLite, PR3) | Rebuildable mart | 9,577 (verified) | PR3 builder, full-replace each run | mirror sync, `/opportunities/commercial` | KEEP, reframe as "opportunity/evidence mart" | 9,576/9,577 are `commercial_history`; only 1 is `explicit_opportunity` |
| `commercial.opportunity` (Postgres mirror) | Rebuildable mirror | mirrors SQLite 1:1 | mirror sync job | `/mirror/*`, `/opportunities/commercial` | KEEP | Denormalized `contact_display_email`/`account_display_domain` are explicitly "not identity truth" |
| `commercial.sales_opportunity` | **DURABLE** | 8 in preview (all synthetic) | `CommercialOperationsService.promote_sales_opportunity` / `transition_sales_opportunity_stage` | dashboard Pipeline | KEEP — canonical durable identity | No FK to PR3 by design (rebuilds can't cascade-delete human state) |
| `commercial.opportunity_operator_state` | **DURABLE** | n/a in preview | confirm/reject via `/operations/*` | Negocios intake cockpit | KEEP, but see overlap note | Pre-promotion triage — a different lifecycle from `sales_opportunity`, not a duplicate to merge away |
| `commercial.task` / `commercial.activity` | **DURABLE** | 0 in preview (fixtures had none) | `/operations/*` create/complete/cancel | Hoy queue, opportunity workspace | KEEP | Pure human-authored concepts — no machine source to backfill from |
| `commercial.deal` | Historical evidence mirror | 1 real row in SQLite source | opt-in mirror sync | Negocios historical ledger | KEEP as evidence, rename in IA to "historical deal ledger" | The one row (CEAF/SERVA) is the richest real commercial record in the system |
| `commercial.organization` / `commercial.contact` (+`*_source`) | **Durable schema, zero writer (CRM-4A)** | 6 orgs in preview, all synthetic | **none — grepped whole repo, zero writers outside test fixtures** | none | KEEP schema; **build the writer next**, not UI work | `promote_sales_opportunity` never touches `organization_id`/`primary_crm_contact_id` — confirmed by reading the method body |
| `commercial.equipment_opportunity`(+`_source`,`_status_event`) | Legacy, DB-1 era | not queried (DDL-only migration, "no data migration" per its own docstring) | CSV batch loader | **none found** | DEPRECATE (pending full-repo grep before deletion) | Superseded by PR3; parallel "candidate opportunity" concept |

### Quotations & suppliers

| Name | Nature | Rows | Writer | Readers | Verdict | Notes |
|---|---|---|---|---|---|---|
| Durable `customer_quote`/`quote_revision` | **Does not exist** | 0 | — | — | BUILD LATER, do not fabricate | Zero hits for `customer_quote`/`quote_revision`/`quotation_number` anywhere in migrations or schema code |
| `client_quote_number` (on `commercial.deal`) | Evidence | free-text string, 1 real row | manual/heuristic extraction | deal ledger | KEEP as evidence | Closest thing to a "quote id" in the whole system, and it's a loose string |
| Supplier RFQ / Supplier Offer entities | **Does not exist** | 0 | — | — | BUILD LATER | "Offers" only exist informally as classified email threads |
| `commercial.organization` (supplier role) | Durable, unused by UI | n/a | none | **SuppliersPage does not read this table at all** | EXTEND | Intended canonical supplier identity home, bypassed entirely today |
| `SupplierEntityGroups` (dashboard) | UI-only | n/a | derived per-render from warm cases | operator | KEEP pattern, REPLACE identity source | The component's own code comment says canonical identity should be `commercial.organization` |

**New-supplier-gap root cause** (traced to exact lines): the Suppliers view only shows warm cases
whose `category` is `supplier_quote_received`/`supplier_followup`/`supplier_reply`
(`apps/dashboard/src/lib/warmCaseSectionFilters.ts:4-8,24-29`), assigned by
`infer_warm_case_role_category()` (`apps/email-pipeline/src/origenlab_email_pipeline/
warm_case_role_classification.py:214`), which for the domain-based branch checks
`is_supplier_vendor_domain()` (`warm_case_sender_rules.py:897`) against **`SUPPLIER_VENDOR_DOMAINS`**
— a hand-curated `frozenset` of ~90 domains (`warm_case_sender_rules.py:105`), dated to a
"2026-08-24 directional activation audit," never derived from `commercial.organization`, never
updated automatically. A brand-new supplier's first email is invisible unless its content happens to
match `looks_like_supplier_quote_response()`/`looks_like_supplier_followup_thread()`/
`looks_like_supplier_marketing_thread()` text heuristics. **Target fix direction (not implemented
tonight — this is a classification-pipeline change, not a UI change)**: classify supplier-shaped
evidence into a durable/derived candidate table keyed by domain regardless of the static allowlist,
surface as an "unresolved supplier candidate," let a human confirm/promote into
`commercial.organization`.

### ChileCompra / tenders

| Name | Nature | Rows | Writer | Readers | Verdict | Notes |
|---|---|---|---|---|---|---|
| `commercial_procurement_signal` | Rebuildable | 16,448 | ChileCompra acquisition pipeline | candidate planner | KEEP | One row per acquired tender observation |
| `commercial_procurement_evidence` | Evidence | 203,348 | anexo acquisition/extraction | planner, annex preview/import | KEEP | Largest single contributor to the 62GB SQLite footprint |
| `commercial_procurement_conflict` | Evidence/QA | 1,196 | planner | QA/audit scripts | KEEP | e.g. `codigo_licitacion` vs `numero_adquisicion` mismatches |
| `commercial_procurement_enrichment_candidate` | Rebuildable | 16,406 | planner | `/operator/procurement/queues/{name}` | KEEP | Operator-facing candidate before/without promotion |
| `commercial_procurement_account_resolution` | Rebuildable identity | 16,448 | planner | institution routes | KEEP | ~1:1 with signals |
| `apps/api routes/institutions.py` (`/operator/procurement/*`) | Read/write boundary | n/a | code | TendersPage | KEEP | **No promote-tender route here** — only annex import writes |
| `apps/api routes/operations.py` `/sales-opportunities/promote` | Durable write | n/a | code | dashboard Pipeline | KEEP | **This is where tender→CRM promotion actually happens**, indirectly, by `source_opportunity_id` |

**Tender → opportunity promotion today**: exists only indirectly. There is no "promote tender"
action; `POST /sales-opportunities/promote` promotes a PR3 machine opportunity by
`source_opportunity_id`. Tender-origin opportunities carry `codigo_licitacion` as a denormalized
label, not a first-class FK. **Real gap**: if a promoted opportunity's source tender later changes
(new close date, addendum), nothing re-notifies or re-links the durable `sales_opportunity` — it can
silently drift from the live tender state. Worth naming in the target IA; not fixed tonight (a
pipeline-side change, not a UI change).

### Other operations (payments/logistics/catalog/prospecting)

| Name | Nature | Rows | Verdict | Notes |
|---|---|---|---|---|
| `catalog_product` (+alias/category/offer/snapshot/link) | Rebuildable evidence | 9 products, 2 supplier offers, 2 price snapshots | KEEP as evidence | Real but very thin — not yet operationally significant |
| `commercial_deal_payment` | Evidence, historical | 2 rows | KEEP as evidence, do not treat as durable payments truth | **This is the entire "payments" concept in the system** |
| (logistics/imports) | — | 0 tables found | **DEPRECATE the standalone nav section** | "Pagos y logística" is a client-side filter over `/cases/warm`, not backed by any table |
| `lead_research_prospect` | Rebuildable evidence | 81 | KEEP as evidence | Real but small; never gate sends on `classification` alone (pipeline `AGENTS.md` rule) |

## Documentation drift found (and why)

- `apps/dashboard/README.md` describes the app as read-only/GET-only with `App.tsx → TodayPage.tsx`
  as the only mounted route. Current code has 11 pages (Today, Inbox/Bandeja, Pipeline, Deals,
  Prospectos, Catálogo, Suppliers, Tenders, Payments/Logistics, Contacts, System) and real durable
  writes (stage transitions, task/activity CRUD, PR3→CRM promotion) via the Pipeline page — all
  built and tested across 40 commits on this branch chain. The README predates that work.
- `CURRENT_SYSTEM_TRUTH.md`'s "Dashboard sections and their data" table has no Pipeline row at all.
- Recommendation: update both docs once this branch lands on `main` (not done in this pass, to avoid
  editing docs out from under work that hasn't merged yet — but flagged here so it isn't lost).

## Phase 2 — domain model resolution

Target invariant confirmed against real evidence: **one durable human `SALES_OPPORTUNITY` identity**
(`commercial.sales_opportunity`), everything else is source evidence, machine context, or
candidate/pre-CRM discovery. Concretely, resolving each named overlap from the brief:

| Concept | Resolution |
|---|---|
| "Negocios" (Deals page, PR3 intake cockpit) | **KEEP, reframe.** This is the pre-promotion triage stage (`opportunity_operator_state`, confirm/reject), architecturally distinct from Pipeline (post-promotion durable pursuit). Not legacy cruft — it is the "candidate intake" the target model calls for. Rename in IA to make the two-stage flow explicit (see Phase 6). |
| Commercial deals mirror (`commercial.deal`) | KEEP as historical evidence, surfaced from the Opportunity/Organization workspace, not a competing "deal" concept. |
| PR3 opportunities / machine opportunities | Same table (`commercial_opportunity`/`commercial.opportunity`) — rebuildable machine projection. Reframe internally as "opportunity/evidence mart," since 99.99% of rows are historical reconstructions, not live candidates. |
| `sales_opportunity` | The one durable identity. Everything above links to it by logical ID, never FK. |
| Gmail warm cases | Evidence layer feeding Negocios/Bandeja/Suppliers/Customers — two duplicate implementations found (live SQLite queue vs. orphaned Postgres mirror); consolidate onto the live queue, deprecate the orphaned Postgres path. |
| Tender opportunities | A `source_kind` on the same PR3/`sales_opportunity` objects, not a separate opportunity concept. Tender-specific state (`codigo_licitacion`, close date) is evidence attached by logical reference. |
| Prospect opportunities | `lead_research_prospect`/`lead_intel.*` — pre-CRM discovery evidence, promotes into the same `sales_opportunity` path once a human acts on it (no separate promotion mechanism found; same `/sales-opportunities/promote` applies once a PR3 row exists for it). |

## Phase 3 — event flow summary (condensed; full traces in the five audit-pass scratch files this
session produced)

| # | Flow | Durable write today? | Where it goes stale/lost |
|---|---|---|---|
| 1-3 | Customer emails (new/known/reply) | No | Rebuild-lag window between Gmail ingest and next `commercial_identity` rebuild (cadence unverified); replies don't auto-link to an existing `sales_opportunity` |
| 4-5 | Supplier emails (new/new address) | No | New-supplier-gap (see above); cross-domain re-contact from an existing supplier surfaces as a new low-confidence identity unless the resolver's alias/domain match fires |
| 6-8 | Supplier quote / customer quote request / customer reply to quote | No | No structured quote entity exists at all — "did we quote this, which revision" is unanswerable from durable data |
| 9-10 | New tender / tender changes | No | A promoted opportunity does not re-sync if its source tender changes after promotion — silent drift |
| 11 | Machine discovers a prospect | No (rebuildable only, by design) | No route to durable state until a human promotes (flow 12) |
| 12 | Human promotes candidate to CRM | **Yes** (`sales_opportunity` + event) | `organization_id`/`primary_crm_contact_id` left NULL forever — CRM-4A gap |
| 13 | Human changes CRM stage | **Yes** (versioned, idempotent) | Solid — no gap found |

## Phase 6 — target operator IA (decision)

Given how much of the target IA already exists as working pages, the decision is **consolidate and
rename, not rebuild**:

```
TODAY                 — unified actionable work (unchanged concept, already correct: "Today owns no
                         business state")
COMMERCIAL
  Pipeline             — durable sales-opportunity board (exists, KEEP as-is; add real-data + org
                         enrichment from this audit)
  Detected Opportunities (rename of "Negocios"/Deals) — PR3 intake/triage feeding Pipeline via
                         promotion; keep the historical-deal-ledger panel here as evidence
  Customers / Institutions (rename of ContactsPage's institution-grouping view) — canonical
                         organization/contact view once CRM-4A gets its writer
  Quotes               — NOT built tonight; if added, read-only/evidence view only (no durable
                         quote entity exists)
SOURCING
  Suppliers            — KEEP page, EXTEND identity source (read `commercial.organization` +
                         broader `commercial_identity_*`, not just the frozen domain allowlist)
  Purchases / Imports  — fold into Opportunity/Organization workspace; no durable domain exists to
                         justify a standalone nav item (see "Pagos y logística" finding)
EXTERNAL OPPORTUNITIES
  Tenders              — KEEP (genuinely useful ChileCompra processing); clarify tender↔opportunity
                         relationship, flag drift-after-promotion
  Prospecting / Discovery (rename of "Prospectos") — KEEP as machine evidence
CATALOG                — KEEP as-is (thin but real)
SYSTEM / ADMIN          — KEEP; this is where "Solo lectura", proxy/backend status belongs — never
                         in primary operator workflows
```

`InboxTriagePage` (Bandeja de revisión) stays separate from Negocios/Detected Opportunities — it
reads a different evidence stream (`/cases/warm`, general commercial signal triage) from PR3
opportunity intake, and merging them would conflate two real, distinct machine surfaces.

## Phase 9 — deployment drift diagnosis (root cause, not executed)

> **Status: this branch chain has since merged to `main`** (PRs #521-#526). The diagnosis below
> describes the drift as it stood at audit time; see `CURRENT_SYSTEM_TRUTH.md` for current
> deployment topology.

**Root cause: unreleased branch work, not a bug.** `git merge-base main <this branch>` = `190fae0`,
exactly main's current tip — all 40 commits on this branch chain (including the dashboard-proxy
allowlist entries for `/operations/work-queue` and `/opportunities/commercial`) are unmerged. Even
after merging, the Cloudflare Worker will not auto-update: `apps/dashboard-proxy` has no CI deploy
step; `wrangler deploy` is a documented **manual** command
(`apps/dashboard-proxy/README.md:77`) with no automation. This explains all four observed symptoms
(old nav, stale "Solo lectura" messaging, both 403s, old Negocios-only view).

**Reconciliation plan (not executed):**
1. Merge `feat/sales-opportunity-ui-v1` → `feat/sales-opportunity-realdata-preview-v1` chain into
   `main` through normal review.
2. Confirm which branch Render's `origenlab-api`/`origenlab-dashboard` services actually watch
   (not inspectable from this read-only checkout — check Render's own dashboard).
3. Manually run `cd apps/dashboard-proxy && npx wrangler deploy` — this has no CI automation today
   and is the likely reason prior allowlist changes never reached production either.
4. Verify: hit `/api/operations/work-queue` and `/api/opportunities/commercial` post-deploy, confirm
   no more `path_not_allowed`.
5. Process gap worth fixing later (not tonight): add a CI deploy step for `dashboard-proxy`, and
   embed a build/version marker in the dashboard bundle so "what's actually deployed" is verifiable
   without guessing from symptoms.

## Phase 10 — cleanup / retirement lists

| Item | Verdict | Rationale |
|---|---|---|
| `commercial.warm_case*` (Postgres mirror + promotion job) | DEPRECATE (pending confirmation nothing else depends on it) | Orphaned — writer never scheduled, no reader |
| `commercial.equipment_opportunity*` | DEPRECATE (pending full-repo grep before deletion) | DB-1-era, DDL-only migration, no route reads it |
| "Pagos y logística" as a standalone nav section | FOLD into Opportunity/Organization workspace | No durable backend concept exists |
| `SUPPLIER_VENDOR_DOMAINS` frozen literal | REPLACE with derived/maintained source | Root cause of new-supplier gap |
| `apps/dashboard/README.md` "read-only/GET-only" framing | DEPRECATE the claim, not the file | Stale relative to Pipeline's real writes |
| `commercial.opportunity_operator_state` | KEEP, do not merge into `sales_opportunity` | Distinct pre-promotion lifecycle, not a duplicate |
| CRM-4A (`commercial.organization`/`commercial.contact`) | KEEP schema, BUILD the writer | Not a deletion candidate — it's the actual next increment |
| Durable `customer_quote`/supplier RFQ/offer entities | DO NOT BUILD tonight | No evidence of readiness; `TARGET_COMMERCIAL_ARCHITECTURE.md` marks these "FUTURE" |

## Open questions for the next PR (not blocking, not answered by this audit)

1. Should `commercial.warm_case*` be deleted outright, or repurposed as the actual backing store for
   Bandeja de revisión (giving operator actions like snooze/close somewhere durable to live)? This
   audit recommends (a) delete, on the grounds that a live-computed proposal surface is more correct
   per the core principle, but a full caller search was not exhaustive.
2. What cadence does `commercial_identity` actually rebuild on in production? Not found in this pass
   (affects how large the "new contact invisible" staleness window really is).
3. Who should own writing the CRM-4A reconciliation service, and should it run at promotion time
   (synchronous) or as an idempotent backfill job (asynchronous)? This audit's real-data hydration
   script (see commit history) demonstrates the target shape in the disposable preview only — it is
   explicitly not a production writer.
