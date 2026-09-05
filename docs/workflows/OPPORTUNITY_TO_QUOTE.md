# Opportunity → quote

## Business purpose
Produce a customer quotation for a durable sales opportunity, track its
review/approval lifecycle, and record its outcome — without letting the
quote's outcome silently change the opportunity's own outcome.

## Actor
Sales operator (creates, reviews, approves, sends, closes). No autonomous
step sends anything to a customer — sending is always an operator action.

## Trigger
A durable `commercial.sales_opportunity` exists and is ready to be quoted
(from the Lead → Opportunity workflow, or a manually-created opportunity).

## Required information
The opportunity to quote, a document number, a quote number (or an existing
Drive folder to adopt — see below).

## Inputs
Either: nothing but the opportunity (system generates a new quote + Drive
workspace), or an already-existing Drive "Pendientes" folder the operator
wants to adopt as a quote's system of record.

## Human steps
1. Operator creates a quote from an opportunity, or adopts an existing Drive
   folder as a quote (`adopt_drive_folder`).
2. Operator edits the actual quote content in the Drive-provisioned Google
   Sheet (content editing happens in Drive, not the CRM).
3. Operator submits the quote for review, requests adjustments, approves,
   and confirms it was sent — each a distinct action.
4. Operator eventually closes the quote as won or null.

## System steps
1. On create: allocate a quote number (transactional number-series
   allocator) and provision a Drive folder + copied template sheet, or skip
   both entirely if adopting an existing folder.
2. Drive provisioning uses a lease-fenced optimistic-concurrency mechanism
   (a `version` token plus a server-owned 300-second lease) so two
   concurrent attempts can never create duplicate Drive artifacts; it
   finds-before-creates by a stamped `quote_id` property on the Drive
   objects, so a retry after partial failure reuses prior artifacts.
3. Every workflow transition (submit-for-review, request-adjustments,
   approve, confirm-send, close) is a distinct, idempotent command against
   one lifecycle field, `customer_quote_revision.status`.
4. Closing the quote never touches the parent `commercial.sales_opportunity`
   — that is a deliberate, separate operator action in Pipeline.

## Decisions / gates
- **Submit for review / request adjustments / approve / confirm send** —
  each a distinct, explicit operator action; the dashboard collapses the
  first three into one "Revisión" lane for display, but the underlying
  states remain distinct.
- **Close (won/null)** — reachable only from "sent"; requires an explicit
  outcome choice, no default.
- **Adopt vs. generate** — adopting an existing Drive folder explicitly
  skips both the number-series allocator and any Drive mutation; the system
  never fabricates a serial/issue-year for an adopted quote.

## Outputs
A `commercial.customer_quote` row with a resolved lifecycle outcome, and
(for generated quotes) a live Drive folder + sheet; for adopted quotes, a
CRM record pointing at a pre-existing Drive folder.

## Documents generated
The Google Sheet quote document itself (Drive-authoritative content); the
CRM stores only safe references (folder/sheet IDs, provisioning state), not
the content.

## Communications generated
None automated — "confirm send" records that a human sent the quote outside
the system (email, in person, etc.); the system does not send it.

## Statuses visible to the operator
Cotizaciones board (kanban over `customer_quote_revision.status`), plus a
read-only Drive-intake evidence resolver ("Incorporar al CRM") for folders
discovered in Drive with no CRM identity yet.

## Completion condition
The quote reaches `closed_won` or `closed_null`.

## Exceptions
- Drive provisioning failure leaves the quote in `failed`, retriable via a
  dedicated retry command — the quote itself is never rolled back, and
  nothing in Drive is ever deleted as compensation.
- A lease expiring and being reclaimed by a newer attempt makes an older
  attempt's terminal write a no-op, not a corrupting overwrite.
- Ambiguous Drive-folder-to-CRM-identity matches (2+ candidate
  organizations/contacts/opportunities) are never auto-resolved — the
  intake-resolution service returns a candidate list, not a guess, and the
  operator must choose explicitly.

## Evidence that should be retained
`commercial.customer_quote_event` (append-only: created, submitted,
adjustments-requested, approved, send-confirmed, closed, Drive-provisioning
requested/ready/failed).

## Data that must be durable
`commercial.customer_quote`, `_revision`, `_drive_workspace` (references and
provisioning state only), `_number_series` allocator state, `_event`.

## Data that may be inferred / rebuilt
Nothing in this workflow is rebuildable — the whole aggregate is durable
human commercial truth by design.

## Unresolved questions
- None specific to this workflow's own mechanics — it is one of the
  architecturally strongest parts of the system (see
  `docs/refoundation/REFOUNDATION_PLAN.md`'s "must not be redesigned"
  list). The open questions live one level up, in how a quote's supplier
  cost side (RFQ/offer) will eventually connect — not yet built.
