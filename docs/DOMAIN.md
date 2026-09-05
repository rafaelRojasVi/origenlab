# OrigenLab V2 — domain model

**Purpose.** The exact vocabulary of the business and the exact shape of the
data that carries it.

**This document owns:** the glossary; organization, unit, domain, person,
affiliation and contact-point semantics; the prospect / lead / signal /
opportunity / quote distinctions; product, manufacturer and supplier
relationships; identity, merge and evidence-promotion principles; and the
complete **30-table inventory**.

**It does not own:** authority and retention ([`DATA.md`](DATA.md)), state
machines and operator steps ([`WORKFLOWS.md`](WORKFLOWS.md)), schemas, roles
and infrastructure ([`ARCHITECTURE.md`](ARCHITECTURE.md)). Labels are defined
in [`README.md`](README.md).

Everything below is **[V2 DECISION]** unless marked otherwise. No table
described here exists yet — the whole model is **[PLANNED]** as an
implementation.

## 1. Glossary

| Term | Exact definition | Lives in |
|---|---|---|
| **Organization** | A legal or institutional actor, or a unit of one. A unit is an organization row with `parent_organization_id` set. | `crm.organization` |
| **Unit** | An organization that is part of another (a faculty, a laboratory, a purchasing department). Structurally identical to a root organization. | `crm.organization` |
| **Domain** | An internet domain observed on addresses, either exclusive to one organization or shared across its units. **Never an identity.** | `crm.organization_domain` |
| **Person** | A natural person. May have zero organizations and zero channels. | `crm.person` |
| **Affiliation** | The time-bound fact that a person works at, or acts for, an organization: role, unit label, validity, confirmation, provenance. | `crm.affiliation` |
| **Contact point** | One normalized reachable channel — an email address or a phone number. Optionally used by one person and/or operated by one organization. **Never an affiliation.** | `crm.contact_point` |
| **Relationship** | A time-bound role an organization plays for OrigenLab: customer, supplier, manufacturer, prospect, partner, competitor. Roles coexist. | `crm.organization_relationship` |
| **Prospect** | An organization holding an active `prospect` relationship. Not a lifecycle object and not a table. | relationship role |
| **Signal / evidence** | A pending assertion, a classified message, or a public notice. **Never truth.** | `evidence.*`, `comms.message`, `procurement.notice` |
| **Lead** | An opportunity in stage `lead`. Not a separate entity. | `crm.opportunity` |
| **Opportunity** | One pursuable sale, with an owner, a stage, at least one target and an outcome. **The only place `won` and `lost` exist.** | `crm.opportunity` |
| **Quote** | A numbered offer attached to exactly one opportunity, expressed as immutable revisions with lines. | `crm.quote`, `crm.quote_revision`, `crm.quote_line` |
| **Message** | One provider message in one mailbox. Communication evidence, not a ledger. | `comms.message` |
| **Activity** | An operator-relevant interaction deliberately linked to a CRM object: call, meeting, note, or a linked email. | `crm.activity` |
| **Domain event** | Append-only audit of one state transition, with a closed type and a versioned payload. | `crm.domain_event` |
| **Send attempt** | One intent to deliver one message to one address. Submission and delivery are tracked separately. **The only send ledger.** | `outbound.send_attempt` |
| **Contact control** | A `block`, a permanent `prior_contact` fact, or a dated `cooldown` on an address or a domain. | `outbound.contact_control` |
| **Source record** | An acquired external record with provenance and review status. | `evidence.source_record` |
| **Assertion** | A typed observed fact extracted from a source record, with a resolution. | `evidence.assertion` |

## 2. Identity

### 2.1 Organizations and units

`crm.organization` holds a `kind`, an optional `parent_organization_id`, and a
`merged_into` pointer. A unit is not a different kind of thing: a faculty, a
laboratory and a purchasing department are organization rows with a parent.

- **Hierarchy depth is unconstrained in the database.** The self-reference
  allows arbitrary depth; a trigger rejects cycles. There is no depth CHECK.
- **Root-plus-one-level is a UI and operational default**, not a database
  constraint. Deeper trees are legal and must not fail. **[OPEN]** whether
  operators may create units below the second level by hand; recommended
  default is that they create root plus one level, and deeper rows arrive
  only from evidence promotion.

### 2.2 Domains

`crm.organization_domain` maps a normalized domain to an organization with
`scope ∈ {exclusive, shared}`. A domain has at most one exclusive owner. A
shared domain (a university's single mail domain across every faculty)
resolves only to the root organization; it never disambiguates a unit.

A domain is a routing hint. It never establishes that a person belongs to an
organization, and it is never an identity key on its own.

### 2.3 Persons

`crm.person` is a natural person, with `merged_into`. A person may exist with
no organization and no channel — a name on a tender document is enough to
create one.

### 2.4 Affiliation — the person↔organization relationship

`crm.affiliation` is the only place a person is connected to an organization.

Columns: `person_id`, `organization_id` (a root or a unit), `role_title`,
`unit_label` (an informal unit when no unit row exists), `valid_from`,
`valid_to` (NULL means current), `confirmation ∈ {machine_proposed,
confirmed}`, `confirmed_by`, `origin_source_record_id` (nullable, retained
forever), `note`.

Rules:

- **A person may hold several concurrent roles at the same organization.** A
  laboratory director who is also the purchasing contact is two current
  affiliation rows, not a conflict.
- **Exact duplicate affiliation rows are forbidden; concurrent distinct roles
  are allowed.** Uniqueness is over `(person, organization, role identity,
  validity period)`, where role identity is the normalized
  `(role_title, unit_label)` pair. The same role at the same organization may
  not overlap itself in time; different roles may overlap freely.
  **(impl)** an exclusion constraint over `person_id`, `organization_id`, a
  stored normalized `role_key`, and `daterange(valid_from, valid_to, '[)')`.
- `valid_to > valid_from` when `valid_to` is set. Leaving an organization
  closes `valid_to`. **Affiliation rows are never deleted.**
- **Nothing ever infers an affiliation from an email address.** A work address
  may produce an affiliation *assertion*; an operator confirms it.

### 2.5 Contact point — the channel, and only the channel

`crm.contact_point` holds `kind ∈ {email, phone}`, `value_norm`
(email lowercased with the domain in punycode, no tag stripping; phone in
E.164), `value_display`, nullable `person_id`, nullable `organization_id`,
`usage ∈ {personal, work, shared_mailbox, unattributed}`, and `confirmation`.

- **`UNIQUE (kind, value_norm)` globally.** One row per reachable channel, so
  blocks, prior-contact facts and message resolution can never split across
  duplicate rows.
- **There is no `consent_status` column.** Sending authority, blocks, the
  historical-contact fact and cooldowns live **only** in
  `outbound.contact_control` ([`DATA.md`](DATA.md), [`WORKFLOWS.md`](WORKFLOWS.md)).
- Shape checks: `personal ⇒ person NOT NULL ∧ organization NULL`;
  `work ⇒ person NOT NULL ∧ organization NOT NULL`;
  `shared_mailbox ⇒ person NULL`; `unattributed ⇒ both NULL`.
- **A contact point is never a substitute for an affiliation.** On a `work`
  row, `organization_id` means "this organization operates this mailbox", never
  "this person works at this organization".

| Case | Row |
|---|---|
| Personal address | `(person=P, organization=NULL, usage=personal)`; the employer is in `affiliation` |
| Person's work address | `(person=P, organization=O, usage=work)` |
| Shared mailbox of one organization | `(person=NULL, organization=O, usage=shared_mailbox)` |
| Address shared across units of one organization | one row, `organization` = the common ancestor; units are organizations, so no polymorphism is needed |
| Address shared by unrelated organizations, or owner unknown | `(NULL, NULL, unattributed)` |

Two people reading one address is, by definition, a shared mailbox. Inbound
addresses that have not been promoted live as
`comms.message_participant.address_norm` text, not as contact points.

### 2.6 External identifiers

`crm.external_identifier` carries a closed `scheme` (`rut`,
`chilecompra_buyer_code`, `chilecompra_supplier_code`, `drive_folder`,
`v1_organization`, `v1_contact`, `v1_opportunity`, `v1_quote`,
`v1_supplier_master`), a `value_norm`, and **four real typed foreign keys**:
`organization_id`, `person_id`, `opportunity_id`, `quote_id`, with
`CHECK (num_nonnulls(...) = 1)` and `UNIQUE (scheme, value_norm)`.

**There is no unconstrained polymorphic subject column.** A scheme that needs
a fifth subject requires a migration adding a fifth typed FK, not a text
`entity_id`.

### 2.7 Merges

An organization or person merge sets `merged_into` on the loser, repoints
every FK and every external identifier inside the same transaction, and writes
one merge domain event. Merges are never inferred: an operator command
performs them. A merge can never collide on `(kind, value_norm)` in
`contact_point`, because that value was already globally unique before the
merge. `outbound.contact_control` is keyed by normalized text, deliberately
not by FK, so safety facts survive every merge.

## 3. Relationships, prospects, leads and opportunities

### 3.1 Relationship roles

`crm.organization_relationship` records time-bound roles: `customer`,
`supplier`, `manufacturer`, `prospect`, `partner`, `competitor`. Roles coexist
— an organization that sells OrigenLab parts and buys equipment holds both
`supplier` and `customer` at once. The same role may not overlap itself in
time.

**Prospect is an organization relationship, not a lifecycle table and not a
duplicate of the opportunity.** A person is prospect-eligible only through an
affiliation or a hosted channel.

### 3.2 Lead is a stage, not an entity

**Lead is the first opportunity stage.** There is no lead table, no lead-to-
opportunity conversion, and no lead identifier. Accepted intent evidence
becomes `crm.opportunity` with `stage = 'lead'`.

### 3.3 Opportunity targets

An opportunity carries three nullable typed targets: `organization_id`,
`person_id`, `contact_point_id`.

- `num_nonnulls(organization_id, person_id, contact_point_id) >= 1`.
- **Early opportunities may target a person or a contact point with no known
  organization.** A name on a tender, or an inbound address, is a legitimate
  `lead`.
- **Organization is mandatory from `qualified` onward and before any quote
  exists**: `stage IN ('lead','qualifying') OR organization_id IS NOT NULL`,
  and quote creation additionally requires a non-terminal stage.

**Target consistency validation.** When more than one target is supplied, the
combination must be coherent, so unrelated targets cannot be accidentally
combined into one opportunity:

| Supplied | Required |
|---|---|
| person + contact point | if `contact_point.person_id` is set, it equals the opportunity's `person_id` |
| organization + contact point | if `contact_point.organization_id` is set, it is the opportunity's organization or an ancestor of it |
| person + organization | the person has an affiliation (of any validity) with the organization or with an ancestor of it |
| all three | all three rules above |

The check runs in the API command and again as a database trigger. A failing
combination is refused with the specific rule that failed; the operator either
corrects a target, drops one, or records the missing affiliation first. An
`unattributed` contact point constrains nothing and always passes.

### 3.4 Opportunity outcome

`stage ∈ {lead, qualifying, qualified, quoting, negotiating, won, lost,
abandoned}`. **`won` and `lost` exist only on the opportunity** — never on a
quote, a revision or a campaign recipient. Transitions and outcome rules are
owned by [`WORKFLOWS.md`](WORKFLOWS.md).

### 3.5 Quotes

A `crm.quote` is a numbered offer for exactly one opportunity. A
`crm.quote_revision` is an immutable priced snapshot; `crm.quote_line` holds
its items, logistics, fees and discounts. A quote never carries an outcome;
the opportunity records which revision won. Currency, FX, margin and
supersession rules are owned by [`WORKFLOWS.md`](WORKFLOWS.md).

## 4. Products, manufacturers and suppliers

- A **manufacturer** is an organization holding the `manufacturer` relationship.
- `catalog.product` is a manufacturer's model, identified by
  `(manufacturer_organization_id, model_number)`. A product belongs to exactly
  one manufacturer.
- A **supplier** is an organization holding the `supplier` relationship. A
  supplier is not a separate identity table; a distributor that also buys is
  one organization with two relationship rows.
- `catalog.supplier_product` is an **append-only price observation**:
  `(supplier_organization_id, product_id, as_of)` with a price, a currency and
  a provenance. It is never overwritten; the current price is the newest row.
- A quote line references a product and carries its own supplier cost currency
  and FX snapshot, so a repriced catalog never changes a sent quote.

## 5. Evidence and promotion

**Machine systems propose; commands record truth.** This is the one rule that
every other rule in this document serves.

- `evidence.source_record` is an acquired external record — a workbook import,
  a ChileCompra notice payload, a migration manifest, a promoted archive row.
  It carries a `dedupe_key` (unique), a supersession chain, a review status,
  and may be **quarantined**.
- `evidence.assertion` is one typed observation extracted from a source record
  (`organization_name`, `contact_address`, `affiliation`, `contacted_address`,
  `supplier_candidate`, `historical_quote_candidate`, …) with
  `(source_record, kind, value_norm)` and a resolution.
- **Promotion is an operator command.** It resolves an assertion to a real
  subject, creates or links the canonical row, writes the resolution back onto
  the assertion, and emits a domain event. Nothing promotes on a timer, a
  threshold, or a similarity score.
- **Evidence→truth links stay logical** (`resolved_kind`, `resolved_id`).
  No durable table takes a foreign key into rebuildable machine output.
- **New data sources enter through evidence and promotion.** A new lead
  source, workbook, scraper or provider produces `source_record` +
  `assertion` rows. It never gets its own identity table, its own
  organization table, or a direct write into `crm.*`.
- **Fail-closed ambiguity.** When evidence resolves to more than one candidate,
  the system records the ambiguity and stops. It never picks one.

## 6. Worked examples

| # | Situation | Representation |
|---|---|---|
| 1 | A researcher works at two universities and uses one personal Gmail address | one `person`; two `affiliation` rows with their own validity; one `contact_point(usage=personal, person=P, organization=NULL)`. Inbound mail resolves address → person → both affiliations. No duplicate channel row. |
| 2 | A named laboratory head at an institution, no address known | one `person`, one `affiliation(P, I)`, zero contact points. An opportunity may target `person_id`. Campaigns cannot include this person: no channel. |
| 3 | `compras@universidad.example` used by the whole university | one `contact_point(usage=shared_mailbox, person=NULL, organization=root)`. Global uniqueness means one block target, one prior-contact fact, one resolution target. Which faculty a given thread concerns lives on the opportunity and activity, not on the channel. |
| 4 | A faculty and its laboratory both use `contacto@facultad.universidad.example` | one contact point whose `organization_id` is the faculty (the common ancestor of the units that share it). The laboratory is a child organization row; the domain is `shared`. |
| 5 | A distributor that sells to OrigenLab and also buys from it | one organization; two `organization_relationship` rows (`supplier`, `customer`) overlapping in time. Marketing campaign policy decides inclusion explicitly, with a recorded reason — it is never resolved by guessing which role is "primary". |
| 6 | A lead with a known person but unknown employer | `opportunity(stage=lead, person_id=P, organization_id=NULL)` — passes the target check. `qualified` and quote creation are refused until an organization is set. |
| 7 | A person is both laboratory director and purchasing contact at one institute | two current `affiliation` rows for the same `(person, organization)` pair with different `role_title`. Allowed — the uniqueness rule is per role, not per pair. |

## 7. Table inventory — exactly 30 application tables

Seven private schemas. **30 application tables, no more and no fewer.**
Supabase-managed `auth`, `storage`, `pgmq` and migration-metadata tables are
outside this count and outside this inventory.

| # | Schema.table | Unique responsibility | Key invariant |
|---|---|---|---|
| 1 | `crm.organization` | canonical actor or unit | merge target confirmed; unit has a parent; no cycles |
| 2 | `crm.organization_domain` | domain ↔ organization with scope | at most one exclusive owner per domain |
| 3 | `crm.organization_relationship` | time-bound roles | the same role may not overlap itself in time |
| 4 | `crm.external_identifier` | RUT, ChileCompra codes, Drive ids, V1 ids | `(scheme, value_norm)` unique; exactly one typed FK subject |
| 5 | `crm.person` | canonical natural person | merge target confirmed |
| 6 | `crm.affiliation` | person ↔ organization over time | no overlapping validity for the same `(person, organization, role)`; concurrent distinct roles allowed |
| 7 | `crm.contact_point` | one normalized channel | `(kind, value_norm)` globally unique; usage shape checks; no consent column |
| 8 | `crm.opportunity` | lead → outcome lifecycle; the only owner of `won`/`lost` | ≥1 target; target consistency; organization required from `qualified`; closed transition table |
| 9 | `crm.task` | follow-up with a due date | `done ⇔ completed_at IS NOT NULL` |
| 10 | `crm.activity` | operator-relevant interaction, optional message link | append-only; `(message_id, opportunity_id)` unique |
| 11 | `crm.domain_event` | the single audit stream | closed `event_type`; `payload_version`; validated payload; no UPDATE, no DELETE |
| 12 | `crm.quote` | numbered offer for one opportunity | number unique; the opportunity has an organization |
| 13 | `crm.quote_revision` | immutable priced snapshot | frozen once approved; one open revision per quote; stored totals reconcile |
| 14 | `crm.quote_line` | items, logistics, fees, discounts with their own currency and FX | at most one principal item; a logistics allocation target is an item line |
| 15 | `comms.mailbox` | provider account, permissions, sync cursor | address unique; at most one production sender |
| 16 | `comms.message` | provider message evidence | `(mailbox_id, provider_message_id)` unique; `send_attempt_id` unique |
| 17 | `comms.message_participant` | from/to/cc addresses with optional resolution | `(message_id, role, address_norm)` |
| 18 | `comms.attachment` | MIME part metadata and Storage reference | `(message_id, part_index)`; `sha256` present when stored |
| 19 | `outbound.send_control` | global kill switches | single row `id = 1`; both flags default false; every change carries a reason |
| 20 | `outbound.campaign` | lifecycle, budget, policy, approval | status machine; budget serialized by a row lock |
| 21 | `outbound.campaign_recipient` | frozen audience, state, immutable recontact override | `(campaign_id, address_norm)` unique; inserts only while `draft` |
| 22 | `outbound.send_attempt` | the only send ledger, marketing and transactional | minted RFC 822 id unique; at most one open attempt per address |
| 23 | `outbound.contact_control` | `block` / `prior_contact` / `cooldown` | `(scope, value_norm, kind)` unique; `prior_contact` never deleted and never expires |
| 24 | `evidence.source_record` | acquired external record and migration manifests | `dedupe_key` unique; supersession chain; quarantine flag |
| 25 | `evidence.assertion` | typed observation with resolution | `(source_record_id, kind, value_norm)`; closed `kind` |
| 26 | `catalog.product` | manufacturer model | `(manufacturer_organization_id, model_number)` unique |
| 27 | `catalog.supplier_product` | supplier price observation | append-only; `(supplier_organization_id, product_id, as_of)` |
| 28 | `procurement.notice` | ChileCompra notice head and history | `codigo_externo` unique; `disappeared_at` for withdrawal |
| 29 | `platform.operator` | auth user → role and status | auth uid unique; `role ∈ {admin, sales, viewer}` |
| 30 | `platform.command_receipt` | command idempotency | `(operator_id, idempotency_key)`; digest mismatch → 409 |

Counts by schema: `crm` 14, `comms` 4, `outbound` 5, `evidence` 2,
`catalog` 2, `procurement` 1, `platform` 2 — **30**.

**Deliberately absent.** A delivery-event table (attempt columns plus domain
events suffice); a recontact-override table (immutable recipient columns
suffice); a parse-failure table (the archive holds them); per-aggregate event
tables; any mart, mirror or projection table; a party supertype over
organization and person; a lead table; a prospect table; a consent table.
Adding any of these reopens this decision.
