# Target Commercial Architecture

Status: canonical (direction)
Last reviewed: 2026-08-28

Where the commercial platform is going, and the rules every new slice must
follow. The current state is in `CURRENT_SYSTEM_TRUTH.md`.

## Shape

A **modular monolith**: one monorepo, one API application, one operator
dashboard, one durable Postgres boundary, one ingestion/intelligence
application. No microservices, no event bus, no CQRS infrastructure, no
workflow engine, no second database.

```text
EXTERNAL SOURCES  (Gmail / ChileCompra / Drive / manual / spreadsheets)
        |
        v
ADAPTERS + INGESTION            apps/email-pipeline
        |
        v
MACHINE INTELLIGENCE / EVIDENCE  rebuildable read models + mirrors
        |            (candidates, suggestions, evidence, provenance)
        v
DURABLE HUMAN COMMERCIAL CORE    PostgreSQL commercial.*
        |
        v
APPLICATION LAYER                apps/api (commands, validation, lifecycle)
        |
        v
TRUST BOUNDARY                   apps/dashboard-proxy (allowlist, identity)
        |
        v
OPERATOR UI                      apps/dashboard (presentation only)
```

**Machine systems propose; the durable CRM records human commercial truth.**
No rebuildable projection may own durable operator decisions.

## Durable commercial core (target)

```text
commercial.organization
        +-- commercial.contact (organization_id)
        +-- roles: customer | supplier | prospect | partner
        +-- organization_source / contact_source  (provenance links)

commercial.sales_opportunity  (organization_id, primary_crm_contact_id)
        +-- commercial.task
        +-- commercial.activity
        +-- commercial.sales_opportunity_event (append-only)
        |
        +-- FUTURE commercial.customer_quote
        |       +-- quote revisions (immutable per revision)
        |       +-- quote lines
        |       +-- document references
        +-- FUTURE commercial.supplier_offer
                +-- supplier organization_id
                +-- document references
```

Gmail threads, ChileCompra tenders, Drive documents, historical deals, and
catalog evidence attach as **provenance/context** (logical IDs toward
rebuildable systems), never as competing workflows.

## Ownership rules

| Layer | Owns | Must never own |
|---|---|---|
| email-pipeline | ingestion, extraction, OCR, classification, semantic suggestions, evidence, rebuildable projections, mirror jobs | CRM state, quote lifecycle, supplier selection, human tasks as truth, UI workflow state |
| Postgres `commercial.*` durable tables | organizations, contacts, sales opportunities, tasks, activities, events, (future) quotes + supplier offers + document refs | Gmail/ChileCompra internals |
| apps/api | commands, validation, lifecycle rules, reads, concurrency, idempotency, operator identity, transactions | presentation |
| apps/dashboard | interaction, presentation, forms, Kanban, review | business decisions, canonical state, hard-coded business data |
| apps/dashboard-proxy | browser/API trust boundary, route/method allowlist, identity forwarding | business logic; never weakened for convenience |
| apps/web | public site | anything operator/CRM |

## Invariants for every new slice

1. **One source of truth per business concept.** CRM opportunity state →
   `commercial.sales_opportunity`. Organization identity →
   `commercial.organization`. Machine opportunity → PR3 projection. Never a
   second status field for the same concept.
2. **One write path per aggregate:** dashboard → proxy → API route →
   service → repository → Postgres transaction → append-only event. No
   hidden second writers, no ad-hoc SQL from UI or scripts against durable
   tables.
3. **Projections can be rebuilt; human decisions must survive** a full
   rebuild of every mirror and read model.
4. **Dependencies point inward.** Durable core never imports
   Gmail/ChileCompra/Drive specifics; adapters attach provenance IDs.
5. **No speculative abstraction.** Explicit domain tables, explicit
   commands. Add generic machinery only when the product demonstrably
   needs it.
6. Migrations: additive, fail-closed for human data, never rewrite shipped
   history. Least-privilege RO/RW grants per surface.

## Supplier identity direction

Canonical supplier identity = `commercial.organization` with a supplier
role. Everything else is evidence to reconcile into it:

- supplier workbook (imported spreadsheet) — evidence
- `marketing_supplier_domains` — deterministic classification rules
- warm-case supplier categories — Gmail evidence
- `catalog.supplier_offer` / price snapshots — catalog evidence
- PR2 identity accounts — machine identity candidates

The dashboard must not define suppliers (the former hard-coded
`SUPPLIER_GROUP_DEFINITIONS` list is removed; grouping is derived from
evidence). No new supplier identity universe may be created.

## Machine → human bridge (opportunities)

- PR3 proposes opportunities (`o_*`) with evidence and canonical-stage
  suggestions.
- Operators confirm/reject via `opportunity_operator_state`
  (pre-promotion triage only).
- Confirmation should lead to **promotion** into
  `commercial.sales_opportunity` (`sales_*`), where the human lifecycle
  (stage, tasks, activities, future quotes) lives.
- `opportunity_operator_state.manual_stage` is transitional: once the
  promote-at-confirm flow is standard, post-promotion stage lives only on
  the sales opportunity, and `manual_stage` stops being written for
  promoted records (column stays; shipped migration history is not
  rewritten).

## Next implementation slice (after this reset)

`Pipeline -> Quote -> Supplier Offer`, built directly on the durable core:

1. Sales-opportunity list/detail UI on the durable read routes that already
   exist (promote + stage transition buttons; Kanban by stage).
2. `commercial.customer_quote` + revisions + lines (new migration,
   append-only revisions), commands in apps/api, proxy allowlist entries.
3. `commercial.supplier_offer` referencing supplier
   `commercial.organization` rows (create-on-confirm from evidence).
4. Organization/contact API routes (read + confirm/merge commands) so
   evidence reconciliation has a UI path.

Nothing in this list requires new infrastructure — only new tables,
commands, routes, and UI on the existing single write path.
