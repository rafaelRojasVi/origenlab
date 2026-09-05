# OrigenLab repository / platform re-foundation — plan and decision register

Status: canonical
Owner: project-maintainers
Last reviewed: 2026-09-03, verified against `main` @ `774cc36cf4dee2b90ff043e4307544573787b229` (Alembic head `20260902_0046`)
Branch: `docs/origenlab-refoundation-v1` (documentation-only)

## Why this exists

OrigenLab grew organically for months into many individually strong systems
— Gmail ingestion, SQLite marts, lead/supplier intelligence, outbound
campaigns, ChileCompra procurement intelligence, Postgres mirrors, a durable
CRM, quotes, Google Drive integration, a product catalogue, and operator
tooling — but the repository, documentation hierarchy, data authority, and
runtime model had become hard to reason about as a whole. **The goal of this
effort is not to rewrite working functionality.** It is to make the
repository comprehensible enough that future work — a connected commercial
platform where CRM, marketing, and communications interoperate through
stable identity and explicit authority boundaries — can be designed
deliberately instead of inferred from accumulated code and databases.

This document is the re-foundation effort's own scope record and decision
register — not an architecture-truth document itself. For architecture
truth, see [`../architecture/CURRENT_SYSTEM_TRUTH.md`](../architecture/CURRENT_SYSTEM_TRUTH.md)
(current) and [`../architecture/TARGET_COMMERCIAL_ARCHITECTURE.md`](../architecture/TARGET_COMMERCIAL_ARCHITECTURE.md)
(direction).

## What this phase produced

A documentation-only checkpoint, no code/migration/data changes:

| Deliverable | File |
|---|---|
| SQLite/database forensic register | [`../data/SQLITE_REGISTER.md`](../data/SQLITE_REGISTER.md) |
| Data authority map (per business concept) | [`../data/DATA_AUTHORITY_MAP.md`](../data/DATA_AUTHORITY_MAP.md) |
| System inventory (per app/module) | [`SYSTEM_INVENTORY.md`](SYSTEM_INVENTORY.md) |
| Non-technical workflow template + 3 worked examples | [`../templates/WORKFLOW_TEMPLATE.md`](../templates/WORKFLOW_TEMPLATE.md), [`../workflows/`](../workflows/) |
| This plan / decision register | this file |
| Documentation authority map, amended | [`../DOCUMENTATION_MAP.md`](../DOCUMENTATION_MAP.md) |
| Root router, amended | [`../../CLAUDE.md`](../../CLAUDE.md) |
| Current-truth doc, corrected drift | [`../architecture/CURRENT_SYSTEM_TRUTH.md`](../architecture/CURRENT_SYSTEM_TRUTH.md) |

Explicitly out of scope for this pass (deferred, not forgotten — see "Next
slice" below): mapping every one of the ~140 tracked `.md` files in the
repository (only canonical/architecture-level docs were audited); a fourth
and fifth workflow (tender→opportunity, supplier RFQ→offer); any code,
migration, or database change; a full deep SQLite integrity audit (the
existing `audit_sqlite_deep.py` tooling is the right mechanism for that, on
an explicit operator decision, not something this pass ran ad hoc).

## Principles preserved (not reinvented)

Confirmed present in the codebase and kept, not redesigned:

- Machine systems propose; the durable CRM records human commercial truth.
- Rebuildable projections never own a durable human decision.
- One source of truth and one controlled write path per durable aggregate.
- Append-only evidence/events where they already exist.
- Explicit provenance (logical pointers between rebuildable and durable
  layers, never a durable FK into a rebuildable projection).
- Idempotent external effects (Gmail two-phase send, Drive lease-fenced
  provisioning — different mechanisms, same intent).
- Fail-closed ambiguous identity matching — confirmed **twice** in the
  codebase independently: `promote_sales_opportunity`'s org-first
  conservative resolution, and the customer-quote intake-resolution
  service's closed confidence vocabulary that never auto-picks among
  multiple candidates.
- No silent machine advancement of commercial state (a quote closing never
  touches its parent opportunity; a PR3 rebuild never touches a promoted
  opportunity).

The "Platt Commercial Platform" reference architecture that motivated the
original architecture-convergence framing of this work is a lessons-learned
reference, not a specification — nothing here imports its schema names
(`app`, `comms`, `integration`, etc.) mechanically.

## What must not be redesigned (architecturally strong, confirmed by this pass)

- The durable CRM core: `commercial.organization`/`contact` →
  `sales_opportunity` → `task`/`activity` → `customer_quote` (+revision,
  Drive workspace, events, number series). Idempotent commands, optimistic
  concurrency, append-only events, and (as of CRM-Q2/Q2B) a real multi-state
  quote workflow with decoupled closure — all uniformly applied.
- The `promote_sales_opportunity` and customer-quote intake-resolution
  evidence→signal patterns — both real, working precedents for "evidence
  must not silently become CRM truth," not aspirational designs.
- The Gmail two-phase send-safety design and the Drive lease-fenced
  provisioning design — different mechanisms, each already fit for its one
  provider today.

## Decision register (unresolved, tracked here — not answered by this pass)

Each entry: current state, evidence, dependency/blocker, and why it's
unresolved rather than decided.

| # | Question | Evidence | Status |
|---|---|---|---|
| 1 | Should `supplier_master` bridge to `commercial.organization` (the direction `docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md` already states)? | No bridge exists; `SuppliersPage` doesn't read either table today — it reads warm-case domain grouping gated by a frozen ~90-domain literal (`SUPPLIER_VENDOR_DOMAINS`), which is the documented root cause of a "new supplier invisible" gap | Open — direction stated, not built |
| 2 | Is the Postgres `outbound.*` suppression/outreach-state mirror (migration `0004`) meant to be kept fresh, or retired? | Route (`/mirror/outbound`) is real and correctly wired; its only writer is the `EXPERIMENTAL_PARKED` break-glass script, not daily runtime | Open — "half-wired," an ownership question, not a bug |
| 3 | Should `commercial.warm_case*` be scheduled, repurposed, or retired? | Postgres read path is live under the Postgres API backend; production runs the SQLite backend by default; writer is opt-in CLI, never scheduled | Open — same "half-wired" shape as #2, independently |
| 4 | Should a tender-opportunity link be first-class (FK + change notification), given promoted opportunities silently drift from live tender state today? | Confirmed gap, documented in `docs/architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`, not fixed since | Open |
| 5 | What is the smallest correct shape for a campaign-reply → CRM signal? | Two real existing precedents of different weight (see `../workflows/CAMPAIGN_TO_REPLY.md`); brief's fixed constraint: a reply must never silently create/advance an opportunity | Open — explicitly deferred design work |
| 6 | Should the campaign-recipient lifecycle (already durable-*shaped*) move into durable Postgres? | Currently SQLite-only, CLI-only, no dashboard surface | Open — cost of an unforced migration vs. value of unifying with durable truth |
| 7 | Is a first-class, provider-neutral communications domain justified now, or only once a second provider (e.g. WhatsApp) exists? | One provider (Gmail) today; two different existing outbox-style mechanisms (Gmail two-phase, Drive lease-fencing) with different semantics | Open — do not assume a second provider automatically crosses this threshold |
| 8 | Does `EXPERIMENTAL_PARKED.md`'s "Postgres/API is parked" framing need a scope correction, given it predates every durable-CRM migration and root `AGENTS.md` cites it without that scope distinction? | Doc dated 2026-05-19; `sales_opportunity` (0035) through intake-resolution (0046) all postdate it; the doc does not mention `apps/api`'s command layer at all | Open — a maintainer decision on whether to narrow the doc's scope language, not something this pass should silently fix |
| 9 | What should happen to the four undocumented-provenance SQLite backup files and the one 0-byte failed backup found in this pass? | See [`../data/SQLITE_REGISTER.md`](../data/SQLITE_REGISTER.md) — none recommended for deletion; several need operator confirmation of intent/retention window | Open — operator judgment call, not a technical one |
| 10 | Is `commercial.equipment_opportunity*` (DB-1 era) safe to retire? | Route and read model still wired end to end (limited count/signal consumer); a full-repo caller grep was not exhaustive as of the last audit | Open — needs a caller search before any deletion recommendation, per the repo's own evidence standard (`docs/architecture/COMMERCIAL_RESET_LEDGER.md`) |

## Reversible vs. expensive-later decisions

- **Reversible, low-cost to defer:** #5, #6, #7 above (campaign/communications
  design) — nothing currently depends on an answer; the workflow docs
  already record the open questions precisely enough to pick this up later
  without re-deriving context.
- **Reversible, but visible to operators if left too long:** #1, #2, #3, #8 —
  each is a "half-wired" surface that already confuses anyone reading the
  code cold; documenting them (done, this pass) buys time, but doesn't fix
  the confusion for a new contributor who doesn't read docs first.
- **Gets more expensive the longer it waits:** #10 (`equipment_opportunity*`)
  and the SQLite backup disposition in #9 — not because they're urgent, but
  because the evidence needed to decide them safely (caller graphs, operator
  memory of why a file exists) decays over time.
- **Already effectively locked in, correctly:** the durable CRM/quote
  architecture (see "must not be redesigned" above) and the
  `commercial.organization`/`commercial.contact` FK structure — moving these
  now would cost more than any schema-ownership symmetry argument is worth,
  since `sales_opportunity`, `customer_quote`, and the intake-resolution
  service all already depend on them directly.

## Explicit non-goals (this phase and near-term)

- No CRM/quote redesign — inventory and preserve, per "must not be
  redesigned" above.
- No code, migration, or production data change.
- No database consolidation or deletion (see the SQLite register's evidence
  standard: no deletion recommendation without caller/provenance evidence).
- No mechanical adoption of Platt's schema names or boundaries.
- No mapping of every one of the ~140 tracked docs in this pass — canonical/
  architecture-level docs only.

## Recommended next slice

In priority order, each independently startable:

1. **Doc-authority audit, extended scope** — map the remaining app-internal
   operational/audit/generated docs (the ~70 files under
   `apps/email-pipeline/docs/` not yet covered, `apps/web/docs/audits/`,
   etc.) into the KEEP/RENAME/AMEND/MERGE/SUPERSEDE framework already
   established in `../DOCUMENTATION_MAP.md`. Low risk, mechanical, high
   value for anyone who has to find something later.
2. **Resolve decision-register items #1–#3 and #8** — these are
   documentation/ownership decisions, not implementation work; each needs a
   maintainer/operator judgment call recorded, not new code.
3. **A full SQLite deep audit**, using the repository's own existing
   `audit_sqlite_deep.py --light-only` (safe on production) and
   `--confirm-offline-copy` (on a verified separate-storage copy) tooling —
   an explicit operator decision, not something a documentation pass should
   trigger itself.
4. **Design work for decision-register items #5–#7** (campaign↔CRM
   connection, communications domain) — this is genuine design work, not a
   documentation task; it should follow this repository's own precedent
   (small, evidence-driven, incremental) rather than a big-bang schema
   redesign, and should produce its own plan before any migration is
   proposed.
