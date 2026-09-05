# Lead → opportunity

## Business purpose
Turn a machine-discovered prospect (from Gmail history, ChileCompra tender
activity, or lead research) into a durable, human-owned sales opportunity
worth pursuing.

## Actor
Sales operator (confirms/promotes); the machine intelligence pipeline
proposes candidates automatically.

## Trigger
A new prospect signal appears — a tender match, a Gmail thread with
commercial intent, or a lead-research batch result.

## Required information
Enough evidence to identify an organization and, ideally, a contact — a
domain, a tender buyer name, or an email thread.

## Inputs
Gmail archive evidence, ChileCompra tender data, lead-research batch output.

## Human steps
1. Operator reviews the machine-proposed opportunity (Negocios intake
   cockpit) or a lead (Prospectos).
2. Operator confirms or rejects the candidate.
3. On confirm, operator promotes it into a durable sales opportunity
   (`POST /operations/sales-opportunities/promote`).

## System steps
1. PR3 builder continuously (re)constructs `commercial_opportunity` rows
   from Gmail/tender evidence (full rebuild, not incremental).
2. On promotion, the system snapshots identity: resolves-or-creates a
   `commercial.organization`/`commercial.contact` conservatively (never
   fabricates identity from insufficient evidence — see decision below).
3. A durable `commercial.sales_opportunity` row is created with a logical
   (non-FK) pointer back to the source PR3 opportunity.

## Decisions / gates
- **Confirm/reject** at the Negocios intake stage — soft, reversible, no
  durable effect until promotion.
- **Identity resolution at promotion** — hard gate in spirit, but a
  deliberately soft failure mode: if evidence is insufficient or malformed,
  `organization_id`/`primary_crm_contact_id` are left `NULL` rather than
  guessed, and promotion still proceeds. This is a considered design choice,
  not a bug.

## Outputs
A durable `commercial.sales_opportunity` row, owned by the confirming
operator, independent of any future PR3 rebuild.

## Documents generated
None at this stage (quotes are a separate, later workflow).

## Communications generated
None — this workflow never sends anything.

## Statuses visible to the operator
Negocios intake cockpit shows candidate/confirmed/rejected; Pipeline shows
the promoted opportunity's stage once it exists.

## Completion condition
A `commercial.sales_opportunity` row exists, independent of the PR3 source
row's continued existence.

## Exceptions
- Duplicate promotion attempts are rejected (`ON CONFLICT (source_kind,
  source_opportunity_id) DO NOTHING`, surfaced as a conflict, not a silent
  no-op).
- If a promoted opportunity's source tender later changes (new close date,
  addendum), nothing today re-notifies or re-links the durable opportunity —
  a known, documented gap (see `docs/architecture/
  COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`), not fixed by this workflow.

## Evidence that should be retained
The PR3 source row and its evidence chain, even after promotion (never
FK'd, but the logical pointer should remain resolvable for provenance).

## Data that must be durable
`commercial.sales_opportunity`, its `_event` audit trail, and the resolved
organization/contact identity once set.

## Data that may be inferred / rebuilt
The PR3 `commercial_opportunity`/`commercial.opportunity` mirror in its
entirety — a full rebuild must never affect an already-promoted opportunity.

## Unresolved questions
- What cadence does `commercial_identity` actually rebuild on in production?
  Not established — affects how large the "new contact invisible until next
  rebuild" staleness window really is.
- Should `commercial.warm_case*` (a parallel, half-wired Gmail-evidence path)
  feed this same promotion flow, or stay a separate/retired surface? See
  `docs/refoundation/REFOUNDATION_PLAN.md`'s decision register.
