# OrigenLab V2 — state machines and operator workflows

**Purpose.** What actually happens, step by step, and what each step is
allowed to change.

**This document owns:** every state vocabulary and transition table; the
twelve operator workflows; the send predicate and its three enforcement
points; the dispatch linearization limit; the quotation arithmetic and
supersession rules.

**It does not own:** entity definitions ([`DOMAIN.md`](DOMAIN.md)), authority
and retention ([`DATA.md`](DATA.md)), infrastructure
([`ARCHITECTURE.md`](ARCHITECTURE.md)), runbooks
([`OPERATIONS.md`](OPERATIONS.md)).

Everything here is **[V2 DECISION]** and **[PLANNED]**. No command described
below exists yet.

## 0. Conventions

Every workflow step is described as **actor · command · preconditions · state
change · durable evidence · failure behaviour**. Three rules apply everywhere:

1. **Every state change is a command.** Commands are `POST` routes on FastAPI,
   carry a trusted operator identity and an idempotency key, and take an
   expected version where concurrent edits are possible.
2. **Every state change writes exactly one `crm.domain_event`** in the same
   transaction. No event, no transition.
3. **Failure is refusal, not repair.** A command that cannot satisfy its
   preconditions returns the specific rule that failed and changes nothing.

## 1. State vocabularies

### 1.1 Opportunity stage

`lead → qualifying → qualified → quoting → negotiating → {won, lost}`, plus
`abandoned`.

| From | Allowed to |
|---|---|
| `lead` | `qualifying`, `abandoned`, `lost` |
| `qualifying` | `qualified`, `lead`, `abandoned`, `lost` |
| `qualified` | `quoting`, `qualifying`, `abandoned`, `lost` |
| `quoting` | `negotiating`, `qualified`, `abandoned`, `lost` |
| `negotiating` | `won`, `lost`, `quoting`, `abandoned` |
| `won`, `lost`, `abandoned` | nothing — terminal |

`won ⇔ (won_quote_id, won_revision_no)` set. `won`/`lost`/`abandoned` ⇔
`closed_at` set. Reopening is a **new** opportunity that references the old
one; a terminal stage is never revived. Only the API role may update `stage`.

### 1.2 Quote revision status

`draft → in_review → approved → sent`; `in_review → draft`;
`{draft, in_review, approved} → void`.

- At most one revision per quote in `{draft, in_review}`.
- Once `approved` or `sent`, a trigger rejects **any** line or price change.
- `sent` requires `pdf_sha256` and exactly one of `sent_attempt_id` (a V2
  send) or `sent_message_id` (an operator-linked Gmail message whose
  attachment hash matches).
- **Supersession is a fact, not a status.** When a newer revision is approved,
  the previous `approved` or `sent` revision gains `superseded_by_revision_no`
  and `superseded_at`. **A sent revision stays `sent` forever.**

### 1.3 Campaign status

`draft → audience_frozen → approved → active ⇄ paused → completed →
archived`; any non-terminal → `cancelled`.

### 1.4 Campaign recipient state

`snapshotted → {excluded, reserved}`;
`reserved → {sent, excluded, failed, snapshotted, needs_review}`;
`needs_review → {sent, snapshotted, failed}`;
`sent → {bounced, replied, unsubscribed}`.

`excluded` carries `exclusion_reason ∈ {block, prior_contact, cooldown,
policy_supplier, policy_no_channel, precheck_block, precheck_switch}`.

### 1.5 Send attempt — two independent vocabularies

**Submission state** (did Gmail take the message?):
`reserved, skipped, dispatching, accepted, rejected, ambiguous, not_dispatched`.

| From | Allowed to |
|---|---|
| `reserved` | `skipped`, `dispatching` |
| `dispatching` | `accepted`, `rejected`, `ambiguous`, `not_dispatched` |
| `ambiguous` | `accepted`, `not_dispatched` |

**Delivery state** (what happened afterwards):
`n/a, pending, sent_copy_confirmed, bounced, complained`, with
`bounce_class ∈ {hard, soft}`.

`delivery_state ≠ 'n/a' ⇔ submission_state = 'accepted'`.
`pending → {sent_copy_confirmed, bounced, complained}`;
`sent_copy_confirmed → {bounced, complained}`.

**An accepted message that later bounces stays `accepted`.** Only
`delivery_state` becomes `bounced`. Gmail accepted the submission; a bounce
does not erase that fact. **Submission state, delivery state and
campaign-recipient state are three separate things and are never collapsed.**

### 1.6 Contact control kinds

| Kind | Meaning | Expires | Campaign-overridable |
|---|---|---|---|
| `block` | permanent refusal to send — bounce, complaint, unsubscribe, operator decision | never | **never** |
| `prior_contact` | the permanent fact that this address was contacted at least once | **never** | only by an explicit, approved, per-recipient campaign override |
| `cooldown` | a dated pause, `until_at` in the future | yes, at `until_at` | **never** |

`prior_contact` rows are never deleted and never expire. The 8,580 Wave 1A
historical addresses are permanent prior-contact facts
([`DATA.md`](DATA.md) §7).

## 2. The send predicate

`outbound.dispatch_allowed(attempt_or_recipient)` is one SQL function — a
read-only, `SECURITY INVOKER` predicate that grants no privilege of its own
([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2). It is
**purpose-scoped**: it takes `purpose ∈ {marketing, transactional}` and is true
only when **every clause applicable to that purpose** holds.

| # | Clause | Applies to |
|---|---|---|
| 1 | `send_control` has the flag for this purpose set (`marketing_enabled` or `transactional_enabled`) | both |
| 2 | The campaign is `active` and approved | marketing only |
| 3 | The mailbox is the production sender and is authorized | both |
| 4 | No `block` exists on the address **or** its domain | both |
| 5 | No `prior_contact` exists, **or** the recipient carries an approved recontact override | marketing only |
| 6 | No `cooldown` with `until_at > now()` exists | marketing only |
| 7 | Campaign policy admits the recipient (supplier policy, channel policy) | marketing only |

Clauses 5, 6 and 7 do not apply to a transactional send: a quotation the
customer asked for is not cold outreach (W5). **A `block` always applies.**
"The complete applicable predicate" below means every clause in this table
whose "applies to" column covers the attempt's purpose — never a subset of it.

**Three enforcement points, one predicate:**

The two SQL steps are `SECURITY DEFINER` functions on the closed list in
[`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2: `EXECUTE` belongs to
`origenlab_worker` alone, the worker holds no direct DML on
`outbound.send_attempt`, and each function validates its caller and arguments
before it writes.

| # | Where | What it does |
|---|---|---|
| 1 | `reserve_attempts` (SQL, privileged) | evaluates the predicate over `snapshotted` recipients under `FOR UPDATE SKIP LOCKED`; creates `reserved` attempts |
| 2 | `begin_dispatch(attempt_id)` (SQL, privileged) | re-evaluates the **complete applicable** predicate and, in the same transaction, sets `dispatching`, `dispatch_started_at`, a lease, and a minted RFC 822 id. Failure sets `skipped` and releases the recipient |
| 3 | `send_one(attempt_id)` (worker) | the only code holding a Gmail client. It **re-evaluates the complete applicable predicate** — kill switch, campaign state, mailbox authorization, block, prior-contact override, cooldown and policy — immediately before calling the provider, and refuses unless the row it just moved to `dispatching` is younger than 5 seconds **(impl)** |

The Gmail client class cannot be constructed without a `dispatching` attempt
id. **There is one sender path. No break-glass bypass, no direct-send script
and no second sender enters V2.**

### 2.1 Dispatch linearization — the honest limit

The linearization point is **the moment the Gmail API request begins**.

- A block, an unsubscribe or a kill-switch flip committed **before** that
  moment is caught by enforcement point 3 and the message is never sent.
- A block committed **after** that moment cannot recall the in-progress
  message. It applies to every later attempt.
- The window between the final check and the provider call is small but
  **non-zero**. It cannot be closed by any database mechanism, because the
  provider call is outside the transaction.

**Do not claim a zero-race guarantee.** The guarantee is: at most one in-flight
message per address at any time (the partial unique index over
`reserved`/`dispatching`/`ambiguous`), a complete predicate re-check
immediately before the call, and a recorded outcome for every attempt.

### 2.2 Budget accounting

Campaign budget counts **every attempt that reserves capacity or might have
reached Gmail**: `reserved`, `dispatching`, `accepted`, `ambiguous`. Capacity
is released only by `skipped`, `rejected` and `not_dispatched`.
`reserve_attempts` takes `SELECT … FROM outbound.campaign WHERE id = $1 FOR
UPDATE`, so concurrent workers serialize on the campaign row and
`n = LEAST(requested, max_sends − consumed)`.

## 3. Workflows

### W1 — Evidence → promotion

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | worker · ingest | source reachable | `evidence.source_record` inserted (`dedupe_key` unique) | source record | duplicate key → no-op |
| 2 | worker · extract | source record present | `evidence.assertion` rows, unresolved | assertions | unparseable → record quarantined |
| 3 | operator · `promote_assertion` | assertion unresolved; exactly one candidate subject **or** an explicit subject supplied | canonical `crm` row created or linked; assertion resolved | domain event; `origin_source_record_id` on the new row | more than one candidate and none supplied → refused, ambiguity recorded, **nothing created** |
| 4 | operator · `quarantine_source_record` | contradiction or unresolvable subject | record flagged | domain event | — |

Nothing here runs on a timer. A pending assertion is never counted or sent to.

### W2 — Prospect → lead → qualified opportunity

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `set_relationship` | organization exists | `organization_relationship(prospect)` opened | event | overlapping same role → refused |
| 2 | operator · `create_opportunity` | ≥1 target; target consistency ([`DOMAIN.md`](DOMAIN.md) §3.3) | opportunity at `stage = lead` | event | inconsistent targets → refused with the failing rule |
| 3 | operator · `advance_stage(qualifying)` | transition allowed | stage changed | event | — |
| 4 | operator · `advance_stage(qualified)` | **`organization_id` is set** | stage changed | event | no organization → refused |
| 5 | operator · `advance_stage(abandoned\|lost)` | non-terminal | `closed_at` set | event | — |

An opportunity may live at `lead` with only a person or only a contact point.
It may not reach `qualified`, and no quote may exist, without an organization.

### W3 — Opportunity → quotation → approval → send → outcome

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `create_quote` | opportunity has an organization and is non-terminal | `crm.quote` with a unique number | event | terminal stage or no organization → refused |
| 2 | operator · `create_revision` | no open revision for this quote | revision `draft`, lines copied from the previous revision if any | event | second open revision → refused |
| 3 | operator · `edit_lines` | revision `draft` | lines changed | — | revision `approved`/`sent` → trigger rejects |
| 4 | operator · `submit_for_review` | `draft`; ≥1 priced line | `in_review` | event | — |
| 5 | approver · `approve_revision` | `in_review`; every derived total recomputes to the stored value | `approved`; FX and totals frozen; the previous approved/sent revision gains `superseded_by_revision_no` | event | any total mismatch → **approval refused** |
| 6 | worker · `render_pdf` | `approved` | `pdf_sha256` written through `crm.record_quote_pdf`, which can write no other column | Storage object + hash | render failure → retry; status unchanged |
| 7 | operator · `send_quote` | `approved`; `pdf_sha256` present; `transactional_enabled`; no `block` on the address | transactional send (W5); on acceptance `sent` with `sent_attempt_id` | attempt row, event | any precondition false → refused |
| 7' | operator · `link_sent_message` | `approved`; a Gmail message whose attachment hash equals `pdf_sha256` | `sent` with `sent_message_id` | event | hash mismatch → refused |
| 8 | operator · `close_opportunity(won, quote, revision_no)` | revision `sent`; opportunity `negotiating` | opportunity `won`; `closed_at` set | event | quote not sent → refused |

**The final sent PDF, its SHA-256 and its sending evidence are immutable.**
Google Drive remains a working workspace for drafting; **it is never the
authority for content already sent**. A later revision never rewrites an
earlier one.

#### W3a — Quotation arithmetic

- **Customer-facing currency lives on the revision**: `quote_currency`,
  `price_decimals` (0 for CLP, 2 for USD/EUR), `tax_rate` (IVA, default 0.19),
  `rounding_rule = half_up`, and stored `subtotal`, `discount_total`,
  `tax_base`, `tax_total`, `grand_total`, `totals_computed_at`.
- **Every cost-bearing line carries its own supplier cost currency and FX
  snapshot**: `cost_currency`, `unit_cost`, `fx_rate` (cost → quote currency,
  1 when equal), `fx_as_of`, `fx_source ∈ {bcentral, supplier_quote, manual}`.
- `line_kind ∈ {item, logistics, fee, discount}`. **Margin and markup are
  distinct**: `margin_mode ∈ {margin, markup, none}` with `margin_pct`.
- **All arithmetic is `numeric` with documented rounding. Binary floating
  point is never used for money, FX or totals.**

```text
unit_cost_qc        = unit_cost × fx_rate
allocated_cost_qc   = Σ logistics lines allocated to this item
landed_unit_cost_qc = unit_cost_qc + allocated_cost_qc / qty
unit_price_qc       = round(landed / (1 − margin_pct))   [margin]
                    = round(landed × (1 + markup_pct))   [markup]
line_total_qc       = round(unit_price_qc × qty)
subtotal            = Σ line totals (discounts negative)
tax_total           = round(tax_base × tax_rate)
grand_total         = subtotal + tax_total
```

Allocated logistics lines price at 0; unallocated logistics, fees and
discounts price on their own.

- **A quote revision may have zero or one principal item** — at most one
  `item` line with `is_principal`, enforced by a partial unique index.
- **Logistics may be allocated only to an `item` line.**
  `allocated_to_line_no` must reference an `item` line in the same revision.
- **A principal item becomes mandatory only when a logistics line is allocated
  to it** — that is, when a logistics line relies on the default allocation
  target. Approval fails if such a line exists and no principal is set. A
  revision with no allocated logistics needs no principal.
- `approve_revision` computes and stores every derived column in one SQL
  function, and a check trigger recomputes from the stored inputs and rejects
  the approval on any difference. Both run `SECURITY INVOKER`, as the
  operator's own command inside the FastAPI transaction.

### W4 — Campaign: draft → frozen audience → approval → sending → reply

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `create_campaign` | — | `draft` with budget and policy | event | — |
| 2 | operator · `freeze_audience` | `draft` | recipients inserted `snapshotted` or `excluded` with a reason; campaign `audience_frozen` | recipient rows, event | — |
| 3 | operator · `grant_recontact_override` | `audience_frozen` | override triple set on named recipients (W12) | event | any later state → refused |
| 4 | operator · `dry_run` | `audience_frozen` | none — evaluates the predicate and renders | one report artifact in Storage, one event | — |
| 5 | approver · `approve_campaign` | a dry-run event exists | `approved`; override count and budget recorded | event | no dry run → refused |
| 6 | admin · `activate_campaign` | `approved`; `marketing_enabled` true | `active` | event | flag false → refused |
| 7 | worker · `reserve_attempts` | `active`; predicate true; budget available | recipients `reserved`, attempts `reserved` | attempt rows | predicate false → recipient `excluded` with reason |
| 8 | worker · `begin_dispatch` | attempt `reserved` | `dispatching`, lease, minted id | event | predicate false → `skipped`, recipient released |
| 9 | worker · `send_one` | attempt `dispatching`, < 5 s old, full predicate re-check | provider call | — | see W9 |
| 10 | worker · `finish_attempt(accepted)` | provider returned ids | `accepted`; `prior_contact` upserted; `cooldown` set to `accepted_at + recontact_interval_days`; recipient `sent` | attempt, contact controls, event | — |
| 11 | worker · Gmail sync | reply arrives | recipient `replied`; message stored | `comms.message` | — |
| 12 | operator · `link_activity` / `create_opportunity` | operator judgement | activity and/or opportunity created | event | — |

**A reply never creates or advances an opportunity on its own.** Step 12 is
always an operator decision.

**An accepted send records prior contact permanently and additionally creates
a dated cooldown.** The cooldown expires; the prior-contact fact does not.

### W5 — Transactional quote email

The same functions with `purpose = transactional`, a `quote_revision`
reference, `send_control.transactional_enabled`, **no campaign and no budget**,
and **`block` checks only** — prior contact and cooldown do not apply to a
quote the customer asked for. Acceptance still writes a permanent
`prior_contact` fact.

### W6 — Inbound Gmail reply

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | worker · sync | mailbox cursor valid | `comms.message` inserted on `(mailbox, provider_message_id)`; participants; attachments to Storage | message rows | duplicate → `DO NOTHING`; **no domain event** |
| 2 | worker · match | message is a reply to a minted id in the sender mailbox | `send_attempt` / recipient linked | event | inbound message carrying a minted id but not outbound in the sender mailbox → **flagged, never linked** |
| 3 | operator · `resolve_participant` | address not yet a contact point | contact point created and linked | event | ambiguous person → refused |
| 4 | operator · `link_activity` | message and CRM object chosen | `crm.activity` created | event | duplicate `(message, opportunity)` → refused |

A message nobody linked is evidence, not an activity
([`DATA.md`](DATA.md) §1.1).

### W7 — ChileCompra notice → review → opportunity

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | worker · fetch | notice reachable | `procurement.notice` upserted on `codigo_externo`; head history appended | notice row | — |
| 2 | worker · absent | notice gone from the source | `disappeared_at` set | — | never deleted |
| 3 | operator · review | notice in queue | — | — | — |
| 4 | operator · `promote_notice` | buyer resolved to an organization, or created by W1 | opportunity at `lead`, `external_identifier(chilecompra_buyer_code)` linked | event | buyer ambiguous → refused |

A promoted opportunity does **not** track later notice changes automatically;
the notice history is evidence an operator reads.

### W8 — Supplier and product review

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | worker/loader · import | workbook or price source | `source_record` + `supplier_candidate` assertions, **pending** | evidence | — |
| 2 | operator · `promote_supplier` | one resolved organization; operator marks approved | `organization_relationship(supplier)` opened | event | ambiguous or unreviewed → refused |
| 3 | operator · `create_product` | manufacturer organization exists | `catalog.product` | event | duplicate `(manufacturer, model)` → refused |
| 4 | worker/operator · `record_price` | supplier and product exist | new `catalog.supplier_product` row | append-only row | never updates an existing row |

**Zero automatic promotion.** No supplier candidate becomes an organization
without an operator command.

### W9 — Ambiguous send resolution

| Outcome of `send_one` | Submission state | Recipient | Budget / lock |
|---|---|---|---|
| Provider returned ids | `accepted` | `sent` | held |
| Definitive provider rejection | `rejected` (closed `error_class`) | `failed`, or back to `snapshotted` if `error_class = transient` and the recipient has fewer than 2 attempts **(impl)** | released |
| Connection failure **proven** before any request bytes were written | `not_dispatched` | released | released |
| Timeout, reset after write, crash, or lease expiry while `dispatching` | **`ambiguous`** | `needs_review` | **held** — address lock and budget both stay |

**`ambiguous` is never retried automatically. No timer ever retries.**

1. The reconciler searches the sender mailbox for `rfc822msgid:<minted>`
   across all labels.
2. **Found** → `accepted` + `sent_copy_confirmed`, the message linked through
   the unique `message.send_attempt_id`, prior contact and cooldown upserted
   if missing.
3. **Not found** → `search_evidence` (history id, searched-at, grace elapsed)
   is recorded on the row and it stays `ambiguous` with `needs_human = true`.
   **Absence from Gmail is evidence for a human, never an automatic
   transition.**
4. `resolve_ambiguous(attempt, verdict ∈ {accepted, not_dispatched}, reason)`
   is an operator command and requires the recorded evidence. FastAPI
   authorizes the operator; the function itself is privileged, executable only
   by `origenlab_api`, and writes nothing but that attempt's resolution
   fields ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2).
5. **A retry may be created only after an authorized resolution makes the
   original attempt `not_dispatched`**, or after another explicitly documented
   safe resolution that is compatible with the one-open-attempt invariant.
   `authorize_retry(attempt, reason)` then creates a **new** attempt with
   `retry_of_attempt_id` and `retry_reason`. The original row is never
   modified except in its resolution fields.

**[OPEN]** human deadline for an ambiguous attempt; recommended default is
7 days, with unresolved rows blocking campaign completion.

### W10 — Bounce, complaint, unsubscribe

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | worker · NDR match | an NDR arrives | matched by the minted id in the NDR headers or body, else by address plus time window | — | unmatched → review queue |
| 2 | worker · `record_delivery` | matched attempt is `accepted` | `delivery_state = bounced(hard\|soft)` or `complained`; **`submission_state` stays `accepted`** | event | — |
| 3 | worker · `add_contact_control` | hard bounce, complaint, or unsubscribe | `contact_control(block)` with reason `bounce_hard` / `complaint` / `unsubscribe` | control row, event | — |
| 4 | — | — | recipient `bounced` / `unsubscribed`; **`prior_contact` remains** | — | — |
| 5 | admin · `revoke_block` | explicit reason | block removed | event | **campaigns can never override a block** |

**[OPEN]** unsubscribe mechanism; recommended default is a `List-Unsubscribe`
mailto plus a one-click endpoint on FastAPI.

### W11 — Tasks and follow-ups

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `create_task` | an opportunity exists | task with owner and due date | event | — |
| 2 | operator · `complete_task` | task open | `done`, `completed_at` set | event | — |
| 3 | operator · `cancel_task` | task open | cancelled with a reason | event | — |

`done ⇔ completed_at IS NOT NULL`. Tasks never change an opportunity stage.

### W12 — Campaign-specific recontact approval

The **only** way to contact one of the permanent prior-contact addresses.

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `grant_recontact_override(campaign, recipients, reason)` | campaign is **`audience_frozen`**; each named recipient has a `prior_contact` and **no** `block` and **no** active `cooldown` | `recontact_override_by`, `recontact_override_reason`, `recontact_override_at` set together on the recipient row | event | any other campaign state → refused |
| 2 | — | — | a trigger rejects **every later change** to the override triple | — | — |
| 3 | approver · `approve_campaign` | — | override count recorded in the approval event | event | — |

The override lifts `prior_contact` **for that campaign only**. It **cannot**
lift a `block` or an active `cooldown` — those are never campaign-overridable.
There is no override table; the columns on `campaign_recipient` are the record.

## 4. Cross-cutting failure behaviour

| Situation | Behaviour |
|---|---|
| Duplicate command (same operator, same idempotency key, same digest) | the original result is returned; nothing runs twice |
| Same key, different digest | `409`, nothing runs |
| Concurrent edit of the same aggregate | `expected_version` mismatch → `409`, nothing runs |
| Worker crash mid-transaction | the transaction rolls back; no partial state |
| Worker crash after the provider call began | the attempt becomes `ambiguous` (W9) — never silently retried |
| Kill switch flipped during a run | reservations stop immediately; in-flight attempts complete or become `ambiguous`; nothing new is dispatched |
