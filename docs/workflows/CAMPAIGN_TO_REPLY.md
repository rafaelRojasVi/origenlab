# Campaign → reply

**This workflow describes current behavior only. Unlike the other two
workflow docs, it is deliberately left with open design questions rather
than a designed target — connecting campaign replies to CRM state is future
work, explicitly out of scope for this documentation pass. See
[`../refoundation/REFOUNDATION_PLAN.md`](../refoundation/REFOUNDATION_PLAN.md).**

## Business purpose
Send outbound marketing/prospecting emails to a curated recipient list,
safely (no duplicate sends, no sending to suppressed contacts), and know
whether each recipient replied, bounced, or went quiet.

## Actor
Sales/marketing operator, via CLI only — there is no dashboard UI for any
part of this workflow today (confirmed by a repository-wide grep: every
"campaign" reference in `apps/dashboard/src` is prospect-classification
metadata, never a send action).

## Trigger
An operator decides to run a campaign and initializes one via
`outbound_campaign_cli.py init`.

## Required information
A recipient candidate list (from mart/lead evidence), sourced and filtered
before any send.

## Inputs
Candidate emails, sourced via `candidates add`, filtered by the shared
`candidate_export_gate.py` safety gate (suppression lists, Sent-history
preflight, manual holds).

## Human steps
1. Operator initializes a campaign, adds candidates, selects a batch.
2. Operator reviews the batch (`batch show`) before sending.
3. Operator runs the live send (`send --live`).
4. Operator runs reconciliation afterward to resolve `in_flight` attempts
   against Sent-folder/bounce evidence.
5. Operator manually reviews reply/bounce state per recipient — nothing
   downstream of this is automated today.

## System steps
1. Shared gate (`candidate_export_gate.py`) filters candidates against
   suppression, manual-hold, and Sent-history-preflight rules — fails closed
   if Sent history is missing/mismatched/unparsable.
2. Two-phase send safety: an `outbound_send_attempt` row is written as
   `in_flight` *before* the Gmail API call, then updated to
   `accepted`/`failed` after — a crash between the two leaves the row
   `in_flight`, and the pre-send check refuses to retry until reconciled.
3. Reconciliation matches by normalized recipient email (not by Message-ID)
   against Sent-folder and bounce evidence; bounce evidence takes
   precedence.

## Decisions / gates
- **Candidate eligibility** — the shared export gate, evaluated before every
  send lane (archive-first and lead-based both use it).
- **Sent-history preflight** — fails closed (exit code 3) if Sent history is
  missing, folder-mismatched, or unparsable; override requires an explicit,
  audited flag and should be rare.
- **Reconciliation** — never auto-guesses an ambiguous `in_flight` outcome;
  it stays `in_flight` for explicit operator action if no Sent/bounce
  evidence resolves it.

## Outputs
Per-recipient lifecycle state (`candidate → selected/reserved → sent →
blocked/bounced/replied/inactive`) and an append-only send-attempt ledger.

## Documents generated
None — CSV/JSON exports exist but are export-only, never operational state.

## Communications generated
The campaign email itself, sent via Gmail, subject to every gate above.

## Statuses visible to the operator
CLI output only (`status`, `contact-status`, `batch show`) — **no dashboard
visibility of any kind**.

## Completion condition
Not formally defined — a campaign can be `active`/`paused`/`completed`/
`archived`, but nothing marks "done" automatically; the operator decides.

## Exceptions
- A recipient's reply is inferred purely from `state='replied'` on the
  recipient row and `outreach_contact_state` — there is no dedicated "reply
  evidence" table, no reply content capture, and no connection to CRM
  activity of any kind.
- A bounce sets `contact_email_suppression`, which affects future campaigns
  but has no relationship to any durable CRM record for that contact.

## Evidence that should be retained
The append-only `outbound_send_attempt` ledger; Sent-folder/bounce evidence
in the `emails` archive.

## Data that must be durable
Recipient lifecycle state and the send-attempt ledger are operationally
durable today, but live entirely in SQLite — not in the same platform as
`commercial.*` durable CRM truth. Whether that split is a problem worth
fixing is an open question, not a stated defect.

## Data that may be inferred / rebuilt
None of this is designed as rebuildable — it is closer in spirit to durable
operational truth than to a machine projection, just not on the Postgres
CRM platform.

## Unresolved questions (the actual point of this document)
1. **A campaign reply must never silently create or advance a
   `sales_opportunity`.** This constraint is fixed by the brief that started
   this re-foundation effort; the workflow below it — what a reply *should*
   become — is not decided.
2. Two real existing patterns could serve as a template for turning a reply
   into something CRM-visible: `promote_sales_opportunity`'s heavier
   snapshot-and-create pattern (see `LEAD_TO_OPPORTUNITY.md`), or the
   customer-quote intake-resolution service's much lighter advisory,
   never-writes, closed-confidence-vocabulary pattern (see
   `OPPORTUNITY_TO_QUOTE.md`). Which one — or something smaller than both —
   fits a campaign reply has not been decided, and should be argued from
   requirements, not defaulted to either existing shape.
3. Should campaign recipients resolve to canonical `commercial.organization`/
   `commercial.contact` identity at all, or stay keyed by normalized email
   string indefinitely? Today it's the latter.
4. Is a first-class, provider-neutral communications domain
   (conversation/message/delivery-event/contact-point) justified by current
   requirements (one provider, Gmail), or only once a second provider
   (e.g. WhatsApp) is actually introduced? Not decided either way.
5. Should the campaign-recipient lifecycle ever move into durable Postgres,
   given it's already durable-*shaped* operationally? Not decided — the
   cost of a migration nothing currently demands has to be weighed against
   the value of unifying it with the rest of durable truth.
