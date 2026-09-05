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
| 2 | The campaign is `active` and approved | marketing only |
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
| 2 | operator · `freeze_audience` | `draft` | recipients inserted `snapshotted` or `excluded` with a reason; campaign `audience_frozen` | recipient rows, event | — |
| 3 | operator · `grant_recontact_override` | `audience_frozen` | override triple set on named recipients (W12) | event | any later state → refused |
| 4 | operator · `dry_run` | `audience_frozen` | none — evaluates the predicate and renders | one report artifact in Storage, one event | — |
| 5 | approver · `approve_campaign` | a dry-run event exists | `approved`; override count and budget recorded | event | no dry run → refused |
| 6 | admin · `activate_campaign` | `approved`; `marketing_enabled` true | `active` | event | flag false → refused |
| 7 | worker · `reserve_attempts` | `active`; predicate true; budget available | recipients `reserved`, attempts `reserved` | attempt rows | predicate false → recipient `excluded` with reason |
| 8 | worker · `begin_dispatch` | attempt `reserved` | `dispatching`, lease, minted id | event | predicate false → `skipped`, recipient released |
| 9 | worker · `send_one` | attempt `dispatching`, < 5 s old, full predicate re-check | provider call | — | see W9 |
| 10 | worker · `finish_attempt(accepted)` | provider returned ids | `accepted`; `prior_contact` (`marketing`) upserted; `cooldown` (`marketing`) set to `accepted_at + recontact_interval_days`; recipient `sent` | attempt, contact controls, event | — |
| 11 | worker · Gmail sync | reply arrives | recipient `replied`; message stored | `comms.message` | — |
| 12 | operator · `link_activity` / `create_opportunity` | operator judgement | activity and/or opportunity created | event | — |

**A reply never creates or advances an opportunity on its own.** Step 12 is
always an operator decision.

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
| 2 | worker · match | message is a reply to a minted id in the sender mailbox | `send_attempt` / recipient linked | event | inbound message carrying a minted id but not outbound in the sender mailbox → **flagged, never linked** |
| 3 | operator · `resolve_participant` | a `comms.message_participant` address not yet a contact point (not an opportunity participant) | contact point created and linked | event | ambiguous person → refused |
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
