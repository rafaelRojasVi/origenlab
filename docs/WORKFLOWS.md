# OrigenLab V2 — state machines and operator workflows

**Purpose.** What actually happens, step by step, and what each step is
allowed to change.

**This document owns:** every state vocabulary and transition table; the
twelve operator workflows; the purpose-scoped send predicate and its three
enforcement points; the dispatch linearization limit; the quotation
arithmetic, party snapshot and supersession rules.

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

**This table is a trigger as of 2026-09-22**
(`crm.opportunity_stage_guard`, `20260922200000_slice3_commercial_case_commands.sql`),
not only a table in this document: a case is opened at `lead` and at no other
stage, moves only along the rows above, and once terminal is refused every
target including another terminal one. `advance_case_stage` refuses the same
moves first, with a sentence naming the rule, so an operator reads prose and a
stray `UPDATE` still meets the rule.

**`lost` and `abandoned` are reachable from `lead`**, which the shipped
`opportunity_organization_required_from_qualified` CHECK accidentally
prevented for a case with no institution — corrected in the same migration
([`DOMAIN.md`](DOMAIN.md) §3.6.5). **`won` is not reachable through
`advance_case_stage`**: a case is won against a specific quote revision, which
that request cannot name, so it refuses `won` by name (`won_requires_a_quote`)
rather than letting it end in a constraint violation. `record_case_won` is the
command that names the revision (step 7 below).

**`abandoned` requires an operator and a motive** ([`DOMAIN.md`](DOMAIN.md)
§3.4, 2026-09-22). `abandon_opportunity(opportunity, reason)` is an operator
command; `close_reason` must be non-blank, and **no timer, cron job, queue
worker, classifier or import may write `stage`**. Inactivity produces no
transition: it is read at query time from `crm.activity` and displayed as a
warning, never stored as a state.

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
  and `superseded_at`. **A sent revision stays `sent` forever.** A
  `historical_import` revision may instead be superseded by a lower-numbered
  current revision of its quote, when an operator names the current one
  (`resolve_current_revision`, §W2 step 8): its number is import order, not
  send order.

### 1.3 Campaign status

`draft → audience_frozen → approved → active ⇄ paused → completed →
archived`; any non-terminal → `cancelled`.

**A campaign block is not a status** (§W13, 2026-09-27). The status machine is still unbuilt, so
`paused` has no command that sets it; what stops a campaign today is an active
`outbound.campaign_block` — a separate, append-only fact with its own author and reason that no
status change can lift. A `paused` status, once something sets it, is enforced alongside it:
`outbound.campaign_hold_refusals` reports `campaign_paused` and every reservation and dispatch
refuses it.

### 1.4 Campaign recipient state

`snapshotted → {excluded, reserved}`;
`reserved → {sent, excluded, failed, snapshotted, needs_review}`;
`needs_review → {sent, snapshotted, failed}`;
`sent → {bounced, replied, unsubscribed}`.

`excluded` carries **every** reason it was excluded for, not the first that fired:
`exclusion_reasons text[]`, non-empty exactly when the state is `excluded`, never
holding NULL, and drawn from the closed vocabulary

`{block, block_domain, prior_contact, prior_reply, cooldown, policy_supplier,
policy_no_channel, policy_internal_domain, policy_noise_address,
policy_noise_organization, precheck_block, precheck_switch, manual_inactive,
manual_hold, invalid_address, already_in_audience}`.

The first seven are the original Slice 0 codes; the rest reconcile the two V1 export
gates onto one vocabulary. Evaluation is **complete** — an operator who clears one
reason must not then discover a second — and the application sorts and de-duplicates
the array, which a CHECK cannot express without a subquery. `invalid_address` is the
one terminal reason: with no mailbox, no other rule can be evaluated.

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

<a id="m-wf-control-truth"></a>
### 1.6 Contact control kinds and purposes — the truth table

Every `outbound.contact_control` row carries `purpose ∈ {all, marketing}`,
and `(scope, value_norm, kind, purpose)` is unique. **One applicability rule,
no exceptions: a row applies to an attempt when its `purpose` is `all` or
equals the attempt's purpose.** `all` therefore reaches marketing and
transactional attempts alike; `marketing` reaches marketing attempts only.
This table is the single normative statement; other documents link here.

| Kind | Purpose | Recorded when | Marketing attempt | Transactional attempt | Expires | Campaign-overridable |
|---|---|---|---|---|---|---|
| `block` | `all` | hard bounce, invalid address, complaint, explicit global operator block, legacy suppression whose reason cannot be safely classified (pending operator review) | **refused** | **refused** | never | **never** |
| `block` | `marketing` | marketing unsubscribe; marketing-only exclusion (campaign, supplier or domain policy) | **refused** | ignored | never | **never** |
| `prior_contact` | `marketing` | every accepted send of either purpose; every Wave 1A historical contact | **refused**, unless an approved per-recipient override (W12) | ignored | **never** | override only |
| `cooldown` | `marketing` | an accepted marketing send; `until_at = accepted_at + recontact_interval_days` | **refused** while `until_at > now()` | ignored | at `until_at` | **never** |
| `prior_contact`, `cooldown` | `all` | — | — | — | — | **does not exist** — rejected by CHECK |

`prior_contact` and `cooldown` are facts about outreach, so they are always
`marketing`: a previously contacted address stays reachable for a legitimate
transactional delivery unless an applicable `all` block exists. The kind ⇒
purpose restrictions are CHECK constraints. **Refusals, not permissions**:
there is no opt-in or consent state, and nothing is ever sent because a flag
says "consented" — only because every clause of §2 applicable to the purpose
holds.

**Transactional is a workflow property, never an operator choice.** An
attempt's purpose is set by the command that creates it, from a closed list of
eligible transactional workflows, and every transactional attempt carries a
typed reference to its business object or triggering evidence. Today the list
has one entry — `send_quote`, referencing an `approved` `quote_revision` (W5).
Candidates for later entries — a reply to a customer's inbound `comms.message`,
a communication on an existing non-terminal opportunity to one of its current
participants — enter only by migration and review, each with its own mandatory
reference. Campaign content, bulk sends and any message without such a
reference are marketing; `begin_dispatch` and `send_one` refuse a transactional
attempt whose reference is missing or fails its workflow's preconditions.
Marketing content cannot be relabelled transactional.

**Transactional duplicates are prevented by idempotency, not by
`prior_contact`**: the command receipt (`platform.command_receipt`), the
one-open-attempt-per-address index on `send_attempt`, and the document-delivery
invariant that a revision becomes `sent` with exactly one `sent_attempt_id`
and is never sent again (§1.2).

`prior_contact` rows are never deleted and never expire. The 8,580 Wave 1A
historical addresses load as `prior_contact` / `marketing`; Wave 1A blocks are
classified by their recorded V1 reason per this table ([`DATA.md`](DATA.md)
§7).

## 2. The send predicate

`outbound.dispatch_allowed(attempt_or_recipient)` is one SQL function — a
read-only, `SECURITY INVOKER` predicate that grants no privilege of its own
([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2). It is
**purpose-scoped**: it takes `purpose ∈ {marketing, transactional}` and is true
only when **every clause applicable to that purpose** holds.

| # | Clause | Applies to |
|---|---|---|
| 1 | `send_control` has the flag for this purpose set (`marketing_enabled` or `transactional_enabled`) | both |
| 2 | The campaign is `active` and approved, is not `paused`, and no active campaign block (§W13) covers it | marketing only |
| 3 | The mailbox is the production sender and is authorized | both |
| 4 | No **applicable** `block` — `purpose = all`, or equal to the attempt's purpose — exists on the address **or** its domain | both |
| 5 | No `prior_contact` exists, **or** the recipient carries an approved recontact override | marketing only |
| 6 | No `cooldown` with `until_at > now()` exists | marketing only |
| 7 | Campaign policy admits the recipient (supplier policy, channel policy) | marketing only |

Clauses 5, 6 and 7 do not apply to a transactional send: a quotation the
customer asked for is not cold outreach (W5). **A `purpose = all` block
always applies, to every purpose**; a `marketing` block applies to marketing
attempts and is ignored by transactional ones (§1.6).
"The complete applicable predicate" below means every clause in this table
whose "applies to" column covers the attempt's purpose — never a subset of it.

**Three enforcement points, one predicate:**

**Clauses 4-6 as built (2026-09-27).** `outbound.marketing_contact_refusals(campaign_recipient_id)`
is a read-only `SECURITY INVOKER` function returning the refusals of clauses 4 (applicable address
block, split into `unsubscribe` and `block`, plus `unsubscribe_pending_review` for a «BAJA» held for
review on that exact address (§W10), and a block on the domain or a parent domain), 5
(`prior_contact` without the recipient's W12 override) and 6 (active `cooldown`), plus
`not_snapshotted` / `recipient_unknown`, against the live controls. An empty array means those
clauses hold; it is not `dispatch_allowed`, which still needs clauses 1-3 and 7.

**Clause 2's hold half as built (2026-09-27).** `outbound.campaign_hold_refusals(campaign_id)` returns
`all_campaigns_blocked`, `campaign_blocked` (an active `outbound.campaign_block`) and `campaign_paused`,
and `outbound.marketing_contact_refusals` appends them, so the one send-time contract refuses every
recipient of a held campaign. The database also enforces them by trigger (§W13): whatever code a
future send path is, it cannot reserve, create or dispatch a marketing attempt for a held campaign.

The two SQL steps are `SECURITY DEFINER` functions on the closed list in
[`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2: `EXECUTE` belongs to
`origenlab_worker` alone and is the primary authorization boundary, the worker
holds no direct DML on `outbound.send_attempt`, and each function asserts that
`session_user` is `origenlab_worker` — not `current_user`, which inside a
definer call is the owner `origenlab_owner` — and validates its arguments
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
| 2 | operator · `create_opportunity` | an organization **or** ≥1 participant supplied; participant consistency ([`DOMAIN.md`](DOMAIN.md) §3.3) | opportunity at `stage = lead` with its first participants, in one transaction | event | neither organization nor participant, or an inconsistent participant → refused with the failing rule |
| 3 | operator · `add_participant(role, person and/or contact point, is_primary)` | consistency; no overlap for the same subject and role; at most one current primary per role | participant row inserted | event | violation → refused with the failing rule |
| 4 | operator · `link_participant_person` | the row has no `person_id`; `contact_point.person_id` is NULL or equals the supplied person | `person_id` set on the **same** row | event | mismatch → refused |
| 5 | operator · `set_primary_participant` / `end_participant` | row current | primary flag re-pointed within the role / `valid_to` closed | event | ending the last current participant of an opportunity with no organization → refused |
| 6 | operator · `set_organization` | no quote exists; consistency with every current participant's contact point | `organization_id` set | event | quote exists or inconsistent → refused |
| 7 | operator · `add_address` / `supersede_address` | organization exists; required structured fields present ([`DOMAIN.md`](DOMAIN.md) §2.8) | address row inserted; on supersession the predecessor's `valid_to` closed and `superseded_by_address_id` set | event | duplicate current address → refused |
| 8 | operator · `advance_stage(qualifying)` | transition allowed | stage changed | event | — |
| 9 | operator · `advance_stage(qualified)` | **`organization_id` is set** | stage changed | event | no organization → refused |
| 10 | operator · `advance_stage(abandoned\|lost)` | non-terminal | `closed_at` set | event | — |

An opportunity may live at `lead` with participants only — even a single
unresolved contact point. It may not reach `qualified`, and no quote may
exist, without an organization. Participants are the only record of who is
involved; the opportunity row names no person and no channel.

#### W2b — the commercial case, as six commands (2026-09-22)

The commercial-case tables ([`DOMAIN.md`](DOMAIN.md) §3.6) and their commands
both exist as of 2026-09-22. The steps above describe the participant-centred
path, which has no command boundary; the path below does, and it is the one a
case actually takes. `crm.person` is empty and no command creates a
participant, so steps 3–5 above remain unimplemented.

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `open_commercial_case` | an `evidence.source_record` that exists and is not quarantined | `crm.opportunity` at `stage = lead`, `organization_id` NULL, **and** its `origin` evidence link, in one transaction | `opportunity.created`, `case_evidence.linked` | no such record → 404; quarantined → 409 |
| 2 | operator · `link_case_evidence` | exactly one typed subject, which must already exist; the case is open | `crm.opportunity_evidence` row | `case_evidence.linked` | the same `(case, subject, relation)` twice → `evidence_already_linked`; a subject that does not exist → 404 |
| 3 | operator · `add_case_organization` | the case and the institution at the versions shown; the part is not already current | `crm.opportunity_organization` row, `confirmed` with the operator named; for `requesting_institution`, `crm.opportunity.organization_id` in the same transaction | `case_organization.added` (+ `opportunity.organization_set`) | a second requester → `case_already_has_a_requesting_institution`; a registered supplier/manufacturer as requester without a motive → `supplier_exception_required`; a motive nobody needed → `supplier_exception_is_not_needed` |
| 4 | operator · `set_case_organization_role` | the row is current and belongs to this case | a `machine_proposed` row is confirmed in place; **or** the current row is closed (`valid_to`) and a new part is opened, both in one transaction | `case_organization.role_confirmed`, or `case_organization.ended` + `case_organization.added` | already confirmed in that part → `case_organization_role_unchanged`; taking the requester away from a case at `qualified` or later → refused |
| 5 | operator · `record_case_interest` | ≥1 of product, manufacturer, model text; product and manufacturer agree; quantity > 0 | `crm.opportunity_interest` row, `confirmed` | `case_interest.added` | a manufacturer the catalogue contradicts → refused; a price field → 422 (the request forbids unknown fields) |
| 6 | operator · `advance_case_stage` | the transition is in §1.1; from `qualified` on, a human-confirmed requesting institution exists; `lost`/`abandoned` carry a motive | `stage`, `closed_at`, `close_reason`, `version + 1` | `opportunity.staged` (+ `opportunity.closed`) | `stage_transition_not_allowed`, `stage_requires_a_confirmed_requesting_institution`, `closing_a_case_needs_a_motive`, `won_requires_a_quote`, `case_is_closed` |
| 7 | operator · `record_case_won` | the case is at `negotiating`; the named revision (`quote_id` + `revision_no`) is on this case, `sent` and not superseded | `stage = won`, `won_quote_id`, `won_revision_no`, `closed_at`, `version + 1` | `opportunity.staged` + `opportunity.closed` | `case_not_negotiating`, `quote_revision_not_on_case` (404), `quote_revision_not_current`, `case_version_conflict`, `case_is_closed` |
| 8 | operator · `resolve_current_revision` | the case is open; the quote is on it; the named revision is `sent` and not superseded; the quote has another current revision, each `sent`/`approved` and (if V2-authored) numbered below the chosen one | every other current revision of the quote gains `superseded_by_revision_no` = the chosen one and `superseded_at`; nothing is voided; case `version + 1` | `quote_revision.superseded` per revision | `quote_not_on_case` / `quote_revision_not_on_case` (404), `quote_revision_not_current`, `nothing_to_resolve`, `revision_cannot_be_superseded`, `case_version_conflict`, `case_is_closed` |
| 9 | operator · `record_case_quotation` | the case is open, at `quoting`/`negotiating`, with a requesting institution; the Gmail message is linked to the case, carries the document and records `sent_at`; the number is not a quote on another case; with `supersedes_revision_no`, that revision of the same number is on this case | through the shared writer (`case_quotation.py`): the `printed_historical` `crm.quote` if new, one `sent` `historical_import` revision; the replaced revision superseded; case `version + 1` | `quote.created`, `quote_revision.historical_recorded` (+ `quote_revision.superseded`) | `message_not_on_case` (404), `not_a_gmail_message`, `source_record_does_not_carry_document`, `message_has_no_sent_at`, `number_on_other_opportunity`, `number_is_a_minted_quote`, `document_already_recorded`, `case_not_quoting`, `case_version_conflict` |

Every command carries the case it is about, the `version` of it the operator
was shown, a non-blank reason and an `Idempotency-Key`; the operator comes from
the verified identity and never from the body. Each runs in **one transaction**
with its receipt and its events, so a refusal anywhere leaves the database
byte-identical and the key free. None of them touches `outbound.*`, creates a
person, opens an `organization_relationship`, or confers any marketing
permission (§3.6.4) — a database-backed test counts thirteen such tables before
and after every command in the vocabulary.

**Nothing has been decided through them.** `apps/dashboard-proxy` allows no
POST under `/v2`, every button in the operator workspace is `disabled`, and the
three tables are empty.

### W3 — Opportunity → quotation → approval → send → outcome

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `create_quote` | opportunity has an organization and is non-terminal | `crm.quote` with a unique number | event | terminal stage or no organization → refused |
| 2 | operator · `create_revision` | no open revision for this quote | revision `draft`, lines and party references (addresses, recipient, signatory, terms) copied from the previous revision if any | event | second open revision → refused |
| 3 | operator · `edit_lines` / `edit_parties` | revision `draft` | lines changed / `billing_address_id`, `delivery_address_id`, recipient and signatory participants, terms changed | — | revision `approved`/`sent` → trigger rejects |
| 4 | operator · `submit_for_review` | `draft`; ≥1 priced line | `in_review` | event | — |
| 5 | approver · `approve_revision` | `in_review`; every derived total recomputes to the stored value; a current `billing_address_id`; a current primary `quote_recipient` participant with an email contact point | `approved`; FX, totals **and the party snapshot** frozen (W3b); the previous approved/sent revision gains `superseded_by_revision_no` | event | any total mismatch, superseded address or missing recipient → **approval refused** |
| 6 | worker · `render_pdf` | `approved` | `pdf_sha256` written through `crm.record_quote_pdf`, which can write no other column; the PDF renders from the snapshot only | Storage object + hash | render failure → retry; status unchanged |
| 7 | operator · `send_quote` | `approved`; `pdf_sha256` present; `transactional_enabled`; no `purpose = all` block on the snapshot's recipient address or its domain | transactional send (W5) to the snapshot's recipient; on acceptance `sent` with `sent_attempt_id` | attempt row, event | any precondition false → refused |
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
- **Commercial terms live on the revision too**: `payment_terms`,
  `valid_until`, `delivery_terms` (Incoterm or plain text), `tax_note` — all
  frozen with the party snapshot (W3b).
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

<a id="m-wf-snapshot"></a>
#### W3b — Party snapshot

Every revision carries `party_snapshot jsonb` with `party_snapshot_version`:
**NULL while `draft` or `in_review`, written by `approve_revision` in the same
transaction that freezes FX and totals, and immutable from then on.** The
trigger that rejects line and price changes on an `approved` or `sent`
revision rejects snapshot changes too. The closed key set is validated by a
`SECURITY INVOKER` check function, exactly as the `crm.domain_event` payload
is ([`DATA.md`](DATA.md) §1.1); a new key or a changed meaning is a new
`party_snapshot_version` by migration.

| Key | Captured from | Content |
|---|---|---|
| `sold_to` | the opportunity's organization and its `rut` external identifier | legal name, tax identifier scheme and value, organization id |
| `billing_address`, `delivery_address` | the revision's `billing_address_id`, `delivery_address_id` (delivery optional) | every structured `crm.address` field plus the address id |
| `recipient` | the current primary `quote_recipient` participant | display name, email `value_norm`, person id, contact point id, participant id |
| `signatory` | the current primary `signatory` participant, when one exists | as `recipient`; otherwise absent |
| `terms` | the revision's own columns | `payment_terms`, `valid_until`, `delivery_terms`, `tax_rate`, `tax_note` |
| `currency` | the revision and its lines, already frozen by W3a | `quote_currency`, `price_decimals`, and the distinct `(cost_currency, fx_rate, fx_as_of, fx_source)` tuples used |

- **The PDF and every later read of an issued revision render from the
  snapshot only.** `billing_address_id`, `delivery_address_id`,
  `recipient_participant_id` and `signatory_participant_id` on the revision
  are nullable typed FKs kept as lineage: they answer "which rows were used",
  never "what does the document say".
- **A superseded address or an ended participant changes nothing on an issued
  revision.** Correcting a party on a quotation is a new revision, which
  captures its own snapshot at its own approval.
- `send_quote` delivers to the snapshot's recipient address. Reaching another
  address with the same revision is a manual forward plus
  `link_sent_message`, or a new revision — never an edit.
- **The snapshot is a document value, not a relationship store.** It is never
  a join target, never queried for CRM facts, and carries no key outside the
  closed set. Relational questions go through the lineage FKs.
- **Adopted V1 revisions** carry a snapshot assembled only from the values V1
  stored, with absent values null — never back-filled from current V2 rows
  ([`MIGRATION.md`](MIGRATION.md) §5, slice 3).

### W4 — Campaign: draft → frozen audience → approval → sending → reply

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | operator · `create_campaign` | — | `draft` with budget and policy | event | — |
| 2 | operator · `freeze_audience` (`freeze-campaign-audience`) | `draft`; the preview fingerprint the operator confirmed still matches; every ambiguous identity has an operator decision with a note | recipients inserted `snapshotted` or `excluded` with every reason; campaign `audience_frozen`; content, criteria, policy version and audience fingerprint written once | recipient snapshot rows, `campaign.audience_frozen` | audience changed since the preview → refused; a frozen campaign is never re-frozen — a changed draft or audience is a new campaign |
| 3 | operator · recontact decision (W12), taken **inside step 2** (`recontact_decisions` on `freeze-campaign-audience`) | `draft`; the W12 switch on; `prior_contact` is the recipient's only reason | `recontact_review` and, on approval, the override triple written with the snapshot row | `campaign.override_granted` per approved recipient | W12 off, any other reason, or any state after `draft` → refused |
| — | operator · `set-campaign-planning`, at any point during steps 1–3 | `draft` or `audience_frozen`; sales/admin; `expected_planning_version` matches | `planned_for_date` / `planned_for_at` set, changed or cleared; nothing else — no status, recipient, attempt or job | `campaign.planning_set` / `campaign.planning_cleared` | sent, archived or cancelled campaign, past day, stale version → refused. **Planning schedules nothing**: no step reads it |
| 4 | operator · `dry_run` | `audience_frozen` | none — evaluates the predicate and renders | one report artifact in Storage, one event | — |
| 5 | approver · `approve_campaign` | a dry-run event exists | `approved`; override count and budget recorded | event | no dry run → refused |
| 6 | admin · `activate_campaign` | `approved`; `marketing_enabled` true | `active` | event | flag false → refused |
| 7 | worker · `reserve_attempts` | `active`; predicate true; budget available | recipients `reserved`, attempts `reserved` | attempt rows | predicate false → recipient `excluded` with reason |
| 8 | worker · `begin_dispatch` | attempt `reserved` | `dispatching`, lease, minted id | event | predicate false → `skipped`, recipient released |
| 9 | worker · `send_one` | attempt `dispatching`, < 5 s old, full predicate re-check | provider call | — | see W9 |
| 10 | worker · `finish_attempt(accepted)` | provider returned ids | `accepted`; `prior_contact` (`marketing`) upserted; `cooldown` (`marketing`) set to `accepted_at + recontact_interval_days`; recipient `sent` | attempt, contact controls, event | — |
| 11 | worker · Gmail sync | reply arrives | recipient `replied`; message stored; the reply attributed | `comms.message`, `outbound.campaign_reply` | — |
| 12 | operator · `link_activity` / `create_opportunity` | operator judgement | activity and/or opportunity created | event | — |

**A reply never creates or advances an opportunity on its own.** Step 12 is
always an operator decision.

**Step 2 as built (2026-09-27).** The snapshot is write-once. `frozen_inclusion`,
`frozen_reasons` and `frozen_notes` are the freeze decision; `state` and
`exclusion_reasons` start equal to them and are the live lifecycle columns steps 7–11
would move. At freeze, `prior_contact` is an exclusion reason unless a W12 approval taken on
the same screen lifts it (§W12),
an unreviewed supplier candidate is `manual_hold`, a second address of a person already in
the audience is `already_in_audience`, and a destination that cannot satisfy the address
shape is counted, never stored. A destination with no CRM contact point, linked to more
than one institution, or whose contact point names another institution is frozen only
after an operator decides include or exclude with a note (`identity_review`; exclude is
`manual_hold`). Relevance is recorded apart — `evidenced` with each basis, source and date,
or `sin_informacion` — and never decides eligibility. Steps 3–12 remain unbuilt, and
sending stays blocked: «BAJA» replies are recorded only when an operator applies a fetched batch
(§W10), Gmail replies are not synchronized automatically, and any future send must call
`outbound.marketing_contact_refusals`, which a frozen snapshot never overrides. Step 3 is built
as part of step 2 (§W12). **Every step from 2 on is refused while a campaign block covers the
campaign** (§W13): the freeze preview lists `campaign_held` first, the freeze command refuses it,
and the database refuses the freeze write, a dry-run or approval event, activation, reservation and
dispatch on its own.

**An accepted send records prior contact permanently and additionally creates
a dated cooldown.** The cooldown expires; the prior-contact fact does not.

### W5 — Transactional quote email

The same functions with `purpose = transactional` — set by `send_quote`, the
one eligible transactional workflow (§1.6), never chosen by an operator — a
mandatory `quote_revision` reference to an `approved` revision,
`send_control.transactional_enabled`, **no campaign and no budget**, and
**applicable-`block` checks only**: a `purpose = all` block stops the send; a
`marketing` block (an unsubscribe), `prior_contact` and `cooldown` are
`marketing` rows and do not apply to a quote the customer asked for. Duplicate
delivery is prevented by the command receipt, the one-open-attempt index and
the revision's unique `sent_attempt_id` — not by `prior_contact`. Acceptance
still writes a permanent `prior_contact` / `marketing` fact, so a quoted
address is never cold-contacted later without an override.

### W6 — Inbound Gmail reply

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | worker · sync | mailbox cursor valid | `comms.message` inserted on `(mailbox, provider_message_id)`; participants; attachments to Storage | message rows | duplicate → `DO NOTHING`; **no domain event** |
| 2 | worker · match | message is a reply to a minted id in the sender mailbox | `send_attempt` / recipient linked; one `outbound.campaign_reply` row per message carrying the classifier's **proposed** class | event, `outbound.campaign_reply` | inbound message carrying a minted id but not outbound in the sender mailbox → **flagged, never linked** |
| 3 | operator · `resolve_participant` | a `comms.message_participant` address not yet a contact point (not an opportunity participant) | contact point created and linked | event | ambiguous person → refused |
| 4 | operator · `link_activity` | message and CRM object chosen | `crm.activity` created | event | duplicate `(message, opportunity)` → refused |

A message nobody linked is evidence, not an activity
([`DATA.md`](DATA.md) §1.1).

**The classifier proposes; the operator records.** `outbound.campaign_reply` is
evidence-shaped exactly like `evidence.source_record`: the worker may insert a
`proposed_class`, and only an operator writes `operator_class` (which may overturn the
proposal entirely with `not_a_reply`). It is **never** the writer of
`campaign_recipient.state` — moving a recipient to `replied` is a command, not a side
effect of ingestion — and an ambiguous reply stays in the table with its proposal
unconfirmed rather than being resolved or dropped. The `unsubscribe_request` and
`complaint` classes are *proposals only*: the contact control they imply is written by
§W10 step 3 — for an unsubscribe, the «BAJA» reply command built there, which applies its own
grammar and never trusts a classifier's proposal.

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

#### W8a — Catalog commands (catalog 1a)

Every command is `POST /v2/commands/<name>` with an
`Idempotency-Key`; a change to an existing row carries `expected_version`; each writes exactly one
domain event. Roles: `admin` and `sales` write; `viewer` reads without costs.

| Command | Effect | Event |
|---|---|---|
| `create-product` | a manufacturer's model; refused if the normalised `model_key` already exists for that manufacturer | `product.created` |
| `update-product` | content, specs, physical data; `content_origin` becomes `operator` | `product.updated` |
| `confirm-product-content` | an operator confirms machine-proposed content | `product.content_confirmed` |
| `add-product-image` / `update-product-image` | upload (multipart, through FastAPI to the private bucket) / reorder, caption, hide | `product.image_added` / `product.image_updated` |
| `record-supplier-cost` | a new `catalog.supplier_product` observation; append-only | `product.cost_recorded` (no price in the payload) |
| `set-supplier-terms` | create or update one supplier's terms | `organization.supplier_terms_set` |
| `set-cost-parameter` | a new `catalog.cost_parameter` row; the event names the key, not the value | `cost_parameter.set` |
| `record-fx-rate` | a manual exchange rate with a reason | `fx_rate.recorded` |
| `review-document-line` | the one review of a disputed document line | `source_record.document_line_reviewed` |

**Enrichment: proposal, then confirmation.** The enrichment tool (OPERATIONS §14.4) proposes
Spanish content and manufacturer images for products whose content an operator has not
confirmed. Its writes are `content_origin = machine` and image `status = proposed`, with no
operator; nothing is shown as confirmed. An operator reviews each product and confirms it
(`confirm-product-content`) or edits it (`update-product`, which makes the content the
operator's). The tool never overwrites operator or confirmed content.

**History review of disputed lines.** The quote-history importer cross-checks each AI-extracted
line against the PDF's layout text. A line that disagrees is `disputed`; the importer writes
`disputed.csv` beside its plan for the owner. An operator resolves each through
`review-document-line`, supplying the corrected quantity and prices and a note; the line becomes
`reviewed`, once. Verified and single-source lines need no action.

Catalog 1a ships no price calculation. The rounding amendment to the quotation price rule (§W3a)
belongs to catalog 1b.

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
   the unique `message.send_attempt_id`, `prior_contact` — and, for a
   marketing attempt, `cooldown` — upserted if missing.
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
| 3 | worker · `add_contact_control` | hard bounce, invalid address, complaint, or unsubscribe | `contact_control(block, purpose=all)` with reason `bounce_hard` / `invalid_address` / `complaint`; `contact_control(block, purpose=marketing)` with reason `unsubscribe` (§1.6) | control row, event | — |
| 4 | — | — | recipient `bounced` / `unsubscribed`; **`prior_contact` remains** | — | — |
| 5 | admin · `revoke_block` / `add_contact_control(block, purpose)` | explicit reason; an operator block defaults to `purpose = all` and is `marketing` only for an unsubscribe request or an explicitly marketing-only exclusion (§1.6) | block removed / added | event | **campaigns can never override a block** |

An unsubscribe is a marketing refusal, so it is recorded with
`purpose = marketing` and leaves transactional quotations reachable; a bounce
or a complaint says the channel itself is bad, so it is `purpose = all`.

**Step 3 for an unsubscribe, as built (2026-09-27).** The mechanism is the one the campaign
footer offers: the recipient **replies «BAJA»**. A batch of replies the mail pipeline has already
fetched is previewed (`POST /v2/unsubscribe/preview`, a read) and then applied
(`POST /v2/commands/apply-unsubscribe-replies`, sales/admin, `Idempotency-Key`, the preview's
`input_sha256` and `plan_sha256` — a changed batch or outcome is refused — behind `ORIGENLAB_V2_UNSUBSCRIBE_APPLY_ENABLED`, default off). Nothing reads,
labels or answers a mailbox; synchronizing Gmail replies automatically is **not built**.

- **Grammar** (`baja-reply/2026-09-27.v2`, body only, never the subject): the reply's own text —
  cut before quoted history (`>`, «El … escribió:», «On … wrote:», «-----Mensaje original-----»,
  an Outlook rule or `De:`/`From:` header) or a `--` signature — trimmed, with zero-width
  characters removed, NFKC-normalized and case-folded, must be exactly `baja`, `baja.`, `remover`
  or `remover.`. `REMOVER` is the word the V1 templates asked for (v2 added it; v1 was `BAJA` only).
  Anything else (`BAJA!`, `REMOVER!`, «dar de baja», «favor remover mi correo», «BAJA por favor», a
  name or «Enviado desde mi iPhone» after it, an unrecognized attribution) is **not** an
  instruction and is left for a human; it writes nothing. The grammar version that accepted a
  reply is stored with its evidence, its request and its event.
- **Who** (sender policy `unsubscribe-sender/2026-09-27.v2`, also stored with each record): the
  sender must be exactly one well-formed address, not an OrigenLab mailbox (those are ignored).
  Then, in order: an address OrigenLab already knows (a contact control, a campaign recipient or a
  CRM email contact point) is suppressed; otherwise, when the reply's `In-Reply-To` names a
  recorded outbound `comms.message` whose to/cc/bcc recipients include exactly that normalized
  address, it is suppressed on that **outbound lineage**; otherwise — no lineage
  (`lineage_missing`) or a sender who was not that message's recipient (`recipient_mismatch`) —
  the «BAJA» is **held for review**. No person, contact point or organization is ever created by
  this workflow. Any malformed record refuses the whole batch.
- **What is written**, by `outbound.add_contact_control` ([`ARCHITECTURE.md`](ARCHITECTURE.md)
  §6.2) and nothing else, in one transaction: the reply as received (`evidence.source_record`,
  kind `gmail_message`, key `gmail_unsubscribe:<sha256 of the Message-ID>`, body and its hash);
  then either the `contact_control(block, purpose = marketing, reason = unsubscribe, source =
  unsubscribe_handler)`, an `evidence.assertion` (`unsubscribe_request`) resolving the message to
  that control and one `contact_control.added` event — or, held for review, the evidence with
  `review_status = pending`, an **unresolved** `unsubscribe_request` and one
  `assertion.unsubscribe_review_opened` event, and no control. The function re-proves the basis
  itself (the address is known; or the lineage and the recipient equality hold) and refuses a
  claimed basis it cannot prove. A later «BAJA» from an already suppressed address — or one
  already under another marketing block — adds its evidence, links it
  (`contact_control.evidence_linked`) and changes nothing else. The same message twice is one fact
  (a held one stays one review item).
- **Review**: `POST /v2/commands/resolve-unsubscribe-review` (sales/admin, same switch, a note)
  confirms one held request: the same function creates or links the permanent unsubscribe block and
  resolves the request to it, once; confirming again answers `already_resolved` and writes nothing.
  A request an admin dismissed can still be confirmed the same way (a mistaken dismissal is
  corrected toward suppression): the answer says `was: rejected`, the confirmation's event carries
  `overrides_dismissal`, and the dismissal's own event stays. Never the reverse.
- **Dismissal of a false positive**: `POST /v2/commands/dismiss-unsubscribe-review` (**admin
  only**, same switch, `Idempotency-Key`, the request id, its address, the `review_sha256` the
  suppressions read served, and a non-blank explanation) dismisses one **pending** hold. The same
  function sets the request `rejected` (decision time and admin recorded, every other field as
  received), marks its reply evidence `reviewed`, writes no control, and records one
  `assertion.unsubscribe_review_dismissed` event carrying the explanation. A confirmed request,
  a request already decided, another address's request or a stale `review_sha256` is refused;
  replaying the same key returns the stored answer. Only the dismissed hold stops refusing: a
  permanent unsubscribe for the same address, or another pending hold, still refuses. Nothing
  dismisses, weakens, updates or deletes a contact control.
- **Where**: the dashboard's Bajas page offers **Confirmar BAJA** (sales/admin) and **Descartar
  (falso positivo)** (admin) on each held review, only when the API mounts these commands; the
  proxy forwards exactly these two POSTs under the marketing-command guard (allowed `Origin`, no
  cross-site fetch, JSON within 8 KB, `Idempotency-Key`). Applying reply batches and the preview
  stay API-only.
- **Permanence**: an unsubscribe block, and any block a «BAJA» was linked to, is never updated
  or deleted, and its evidence is immutable (trigger `outbound.unsubscribe_permanent`) — the one
  change ever made to it is the review deciding a held request (confirmed, or dismissed and
  possibly confirmed later), inside that function. Step 5's
  `revoke_block` does not reach it; **re-subscribing does not exist**.
- **Enforcement**: the audience preview and the freeze exclude the address (`block` with the
  note `unsubscribed`, or `unsubscribe_pending_review` while held — the exact address only, never
  its domain); W12 cannot lift either. A snapshot is never permission:
  `outbound.marketing_contact_refusals(recipient)` evaluates §2 clauses 4-6 against today's
  controls and holds, so a recipient frozen before its «BAJA» is refused. It is the contract every
  future campaign send step must call. The one send path that exists — the admin-only test send
  «Enviar prueba» (`POST /v2/commands/send-campaign-test`, one campaign email to one address) —
  has no campaign recipient, so it checks the address refusals itself, live, before it records or
  sends anything: an address-scope `block` for purpose `all` or `marketing` (every unsubscribe is
  one) or an unresolved «BAJA» held for review refuses the test with 422
  `test_recipient_blocked`.

**[OPEN]** automatic Gmail reply synchronization, and a `List-Unsubscribe` header with a one-click
endpoint on FastAPI for when a send path exists.

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
| 1 | sales/admin operator · a recontact decision per recipient (`approve` or `keep_excluded`, mandatory note), taken on the freeze confirmation and sent with `freeze-campaign-audience` | campaign is **`draft`**; W12 switch on (`ORIGENLAB_V2_RECONTACT_REVIEW_ENABLED`); recomputed inside the freeze transaction: the recipient's **only** reason is `prior_contact` | the snapshot row is written with `recontact_review` (decision, note, operator, mode, W12 policy version, the prior-contact facts shown); on `approve`, `prior_contact` is lifted and `recontact_override_by`, `recontact_override_reason`, `recontact_override_at` are set together | `campaign.override_granted` per approved recipient; the W12 summary in `campaign.audience_frozen` | W12 off, a recipient with any other reason, a stale preview or any state after `draft` → refused, nothing written |
| 2 | — | — | the snapshot guard rejects **every later change** to `recontact_review` and the override triple: no grant, rewrite or withdrawal after the freeze | — | — |
| 3 | approver · `approve_campaign` | — | override count recorded in the approval event | event | — |

**Reviewable** means `prior_contact` is the destination's only reason. A reviewer is shown the
last contact date (the last accepted send; unknown for a historical V1 fact, never guessed),
the campaign or source, and the destination. **Without a valid decision `prior_contact` stays
an exclusion reason**, and an undecided prior contact never blocks the freeze. A **bulk**
decision is applied only to a selection the operator has reviewed, and is still persisted as
one decision per recipient (`mode = bulk`, the same note on each). An approved destination
whose identity is ambiguous still needs its identity decision.

The override lifts `prior_contact` **for that campaign only**. It **cannot** lift a `block`
(unsubscribe or suppression included), a domain block, a supplier or manufacturer exclusion,
a malformed or bounced destination, a second address of a person already in the audience, or
an active `cooldown` — those are never campaign-overridable, and the database refuses an
approval on a row carrying any of them. There is no override table; the columns on
`campaign_recipient` are the record, frozen with the snapshot under the policy version
`marketing-audience/2026-09-27.v2-w12` (decision rules `recontact-review/2026-09-27.v1`).

**One implementation.** `freeze-campaign-audience` (`apps/api`, `v2/audience_freeze.py`) is
the only code that decides W12 or writes the override triple. The pipeline's pure
`outbound_v2.eligibility` no longer has a recontact override: its `RecontactOverride` type had
no caller and also cleared `prior_reply` and `cooldown`, which this section forbids, so it was
retired and a test refuses its return. Shipped migration comments are not rewritten; where they
disagree, this section wins:

| Migration | Comment says | Current truth |
|---|---|---|
| `20260905230814_slice0_outbound_tables` | override-triple immutability "is a Slice 5 trigger" | enforced by the snapshot guard of `20260927200000_slice5_w12_recontact_review` (step 2 above) |
| `20260920190000_slice0_wave1b_…_archived_recontact_interval` | "`outbound_v2.eligibility` reads" `recontact_interval_days` | it never did: `CampaignPolicy` carries the field unread, and cooldown arrives precomputed as `cooldown_until`. The cooldown the freeze enforces is an active `outbound.contact_control` row of kind `cooldown` (read by the API freeze, never lifted by W12); `recontact_interval_days` is only stored and selected with the campaign — no code derives a cooldown from it yet |
| `20260908120200_slice0_outbound_address_shape_…` | `ADDRESS_SHAPE_PATTERN` lives in `outbound_v2/eligibility.py` | still true — unaffected by the retirement |

### W13 — Campaign safety block (pause)

An admin stops one campaign, or every campaign, and later — as a separate decision — lets it go
again. Built 2026-09-27 (`20260928100000_slice5_campaign_block`); the commands mount only with
`ORIGENLAB_V2_CAMPAIGN_BLOCKS_ENABLED` (default off). Enforcement and the read are not behind the
switch.

| Step | Actor · command | Preconditions | State change | Durable evidence | Failure |
|---|---|---|---|---|---|
| 1 | **admin** · `block-campaign` (`scope` = `campaign` with its id, or `all_campaigns`), mandatory reason, `Idempotency-Key` | active admin (the route refuses sales and viewers; `outbound.campaign_block_guard` refuses the row again unless the named operator is an active admin); `expected_block_version` equals the target's (blocks + lifts so far); no active block on the target | one active `outbound.campaign_block` row. **Nothing else**: no status, recipient, snapshot, attempt, contact control or send flag moves; nothing is enqueued or sent; Gmail is not touched | `campaign_block.placed` (scope, target, reason, from/to block version) | stale version → `stale_block_version`; already blocked → `already_blocked`; unknown campaign → 404; not an admin → 403 `role_may_not_block` — each with nothing written |
| 2 | — while active | — | the database refuses, by trigger (`outbound.campaign_hold_guard`): setting `audience_frozen_at` or moving to `audience_frozen`, `approved` or `active`, setting `approved_at`, recording `campaign.dry_run_recorded` / `campaign.approved` / `campaign.audience_frozen`, reserving a recipient, creating a marketing send attempt, moving one to `dispatching`. `outbound.marketing_contact_refusals` reports it. Drafting, planning, pausing and cancelling stay possible | — | the refused write, whatever issued it |
| 3 | **admin** · `unblock-campaign` (`block_id`, `expected_version` = 1), mandatory reason, `Idempotency-Key` | the block is active and at that version | `lifted_at` (the database clock), `lifted_by`, `lift_reason`, `version = 2`; the row is immutable from then on. Lifting **starts nothing** — it only stops refusing | `campaign_block.lifted` | lifted already → `block_already_lifted`; stale → `stale_version`; not an admin → 403 |

**Never silently expires.** The table has no expiry column; a block is lifted by step 3 or not at
all, and is never deleted (trigger). A placement is write-once. Only an operator lifts; a
migration may place a block (as the owner, `placed_by_kind = migrator`) but never lifts one.

**The September wave-2 incident hold is one.** The migration places an active `legacy_campaign`
block on V1 campaign `septiembre18-2026-wave2` (reference `incident_hold_september_2026`): the hold
of 2026-09-21, when 279 remaining candidates were contained in the V1 ledger by blocking recipients
one by one because no pause existed. It names the campaign by its V1 key, because no V2 campaign
row carries that key (the import loads V1 campaigns as `archived`), and it carries no address. It
has no `campaign_block.placed` event — a migration writes no audit row (the zero-business-row
foundation check) — so the row records its own provenance; lifting it is step 3 like any other,
with its event.

**Viewer** sees whether something is held, its scope and since when; **sales** also sees the
reason and who decided; only **admin** decides. The dashboard shows a banner for every
non-campaign hold (the September hold included) and a panel on each campaign; the proxy forwards
exactly `GET /v2/workspace/marketing/campaign-blocks` and the two POSTs.

**The V1 ledger.** `outbound_campaign.status` in the V1 SQLite ledger always carried `paused` and
nothing read it. `reserve_next_batch` and `send_campaign_batch` (`apps/email-pipeline`) now refuse
any campaign whose status is not `active` before reading a recipient, dry-run or live, and the
sender re-reads the status before every live Gmail call so a pause committed mid-batch stops the
batch. The September campaign is still `active` in that ledger, contained by its blocked
recipients: setting it to `paused` is a real-data write for the owner to authorize.

## 4. Cross-cutting failure behaviour

| Situation | Behaviour |
|---|---|
| Duplicate command (same operator, same idempotency key, same digest) | the original result is returned; nothing runs twice |
| Same key, different digest | `409`, nothing runs |
| Concurrent edit of the same aggregate | `expected_version` mismatch → `409`, nothing runs |
| Worker crash mid-transaction | the transaction rolls back; no partial state |
| Worker crash after the provider call began | the attempt becomes `ambiguous` (W9) — never silently retried |
| Kill switch flipped during a run | reservations stop immediately; in-flight attempts complete or become `ambiguous`; nothing new is dispatched |
| Campaign block placed during a run (§W13) | the next reservation, attempt creation or dispatch of that campaign is refused by the database; an attempt already `dispatching` still records its outcome (§2.1) |
