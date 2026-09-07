# OrigenLab V2 — domain model

**Purpose.** The exact vocabulary of the business and the exact shape of the
data that carries it.

**This document owns:** the glossary; organization, unit, domain, person,
affiliation and contact-point semantics; the prospect / lead / signal /
opportunity / quote distinctions; product, manufacturer and supplier
relationships; address and opportunity-participant semantics; identity,
merge and evidence-promotion principles; and the complete **32-table
inventory**.

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
| **Address** | One structured postal location of one organization — a site, branch, billing or delivery place. Distinct from a contact point; structured fields, never formatted text, are canonical. | `crm.address` |
| **Relationship** | A time-bound role an organization plays for OrigenLab: customer, supplier, manufacturer, prospect, partner, competitor. Roles coexist. | `crm.organization_relationship` |
| **Prospect** | An organization holding an active `prospect` relationship. Not a lifecycle object and not a table. | relationship role |
| **Signal / evidence** | A pending assertion, a classified message, or a public notice. **Never truth.** | `evidence.*`, `comms.message`, `procurement.notice` |
| **Lead** | An opportunity in stage `lead`. Not a separate entity. | `crm.opportunity` |
| **Opportunity** | One pursuable sale, with an owner, a stage, a customer organization and/or participants, and an outcome. **The only place `won` and `lost` exist.** | `crm.opportunity` |
| **Participant** | One human role on one opportunity — end user, technical, purchasing, finance, approver, quotation recipient, signatory — held over time by a person and/or a contact point. | `crm.opportunity_participant` |
| **Quote** | A numbered offer attached to exactly one opportunity, expressed as immutable revisions with lines. | `crm.quote`, `crm.quote_revision`, `crm.quote_line` |
| **Message** | One provider message in one mailbox. Communication evidence, not a ledger. | `comms.message` |
| **Activity** | An operator-relevant interaction deliberately linked to a CRM object: call, meeting, note, or a linked email. | `crm.activity` |
| **Domain event** | Append-only audit of one state transition, with a closed type and a versioned payload. | `crm.domain_event` |
| **Send attempt** | One intent to deliver one message to one address. Submission and delivery are tracked separately. **The only send ledger.** | `outbound.send_attempt` |
| **Contact control** | A `block`, a permanent `prior_contact` fact, or a dated `cooldown` on an address or a domain, each scoped to a `purpose` (`all` or `marketing`). | `outbound.contact_control` |
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
- **[OPEN]** the closed list of organization `kind` values. Until it is decided,
  the schema enforces only a lower_snake_case token shape on `kind`; the closed
  list arrives as a `CHECK` constraint by migration, like every other vocabulary.

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
- **A contact point is never an address.** A postal location is a
  `crm.address` row (§2.8); a contact point is a channel only.

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

<a id="m-dom-address"></a>
### 2.8 Address — the place, and only the place

`crm.address` is one structured postal location of one organization. It is
**not** a contact point (a channel), **not** a person, and **not** a partner
record: OrigenLab does not adopt Odoo's combined person / company / address
row ([`ARCHITECTURE.md`](ARCHITECTURE.md) §13).

Columns: `organization_id` (**NOT NULL**, a typed FK to a root or a unit —
never a `parent_type` / `parent_id` pair), `site_label` (nullable — campus,
branch, laboratory, warehouse), `street_line_1`, `street_line_2` (nullable),
`locality`, `administrative_area`, `postal_code` (nullable), `country_code`
(ISO 3166-1 alpha-2), `valid_from`, `valid_to` (NULL means current),
`superseded_by_address_id` (nullable self-reference), `confirmation`,
`confirmed_by`, `origin_source_record_id` (nullable, retained forever), `note`.

Rules:

- **Structured fields are the canonical address.** For Chile, `locality`
  holds the comuna and `administrative_area` the región; elsewhere the same
  columns hold the city and the state / province. Any formatted string is
  derived at render time per `country_code` and is never stored as authority.
  `street_line_1`, `locality` and `country_code` are required.
- **Billing and delivery are roles an address plays on a document, not
  properties of the place.** The same site is often both. A quote revision
  names its `billing_address_id` and `delivery_address_id`; the address row
  carries no billing / delivery / default flag, and no row is duplicated per
  role. The dashboard proposes the addresses used on the organization's latest
  approved revision — a view, not a column.
- **An organization may have any number of current addresses.** Exact
  duplicates among an organization's current rows are refused
  (**(impl)** a stored normalized `address_key` over the structured fields).
- **Addresses are corrected by supersession, never by edit or delete.** A
  move or a correction inserts a new row, closes the old row's `valid_to` and
  sets its `superseded_by_address_id`; `valid_to > valid_from`. **Address rows
  are never deleted.**
- **An address change never alters an issued quotation.** Approved and sent
  revisions render from their own party snapshot; the address FKs on a
  revision are lineage only ([`WORKFLOWS.md`](WORKFLOWS.md) §W3b).
- **Provenance and identifiers use the existing machinery.** An address
  arriving from a workbook, a tender or a message is an `evidence.assertion`
  of kind `postal_address`, promoted by an operator (§5); the created row
  keeps `origin_source_record_id` like every other promoted row. An address
  carries no external identifier — the RUT belongs to the organization. No
  address-specific provenance, history or geocoding table exists.
- **A person has no address in V2.** A person is reached through contact
  points and located through affiliations. If a scheme ever needs one, it is
  a migration adding a typed FK, under the same rule as §2.6.

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

<a id="m-dom-participant"></a>
### 3.3 Customer organization and participants

An opportunity carries **one** nullable typed target of its own —
`organization_id`, the customer organization — and any number of human
participants in `crm.opportunity_participant`. **The opportunity row holds no
`person_id` and no `contact_point_id`**; participants are the only authority
for who is involved and in which role.

`crm.opportunity_participant` columns: `opportunity_id` (NOT NULL),
`person_id` (nullable), `contact_point_id` (nullable), `role ∈ {end_user,
technical, purchasing, finance, approver, quote_recipient, signatory, other}`,
`is_primary`, `valid_from`, `valid_to` (NULL means current), `confirmation`,
`confirmed_by`, `origin_source_record_id` (nullable, retained forever), `note`.

- `num_nonnulls(person_id, contact_point_id) >= 1`. **A participant may begin
  as only a contact point** — an inbound address, a mailbox on a tender — and
  gain `person_id` later through `link_participant_person`
  ([`WORKFLOWS.md`](WORKFLOWS.md) §W2). Resolution updates the row; it never
  creates a second one.
- **One person may hold several concurrent roles** on one opportunity: a
  laboratory director who is also the quotation recipient is two current rows.
- **Exact duplicates are forbidden; concurrent distinct roles are allowed.**
  The same `(opportunity, person or contact point, role)` may not overlap
  itself in time. **(impl)** an exclusion constraint over `opportunity_id`,
  `role`, `COALESCE(person_id, contact_point_id)` and
  `daterange(valid_from, valid_to, '[)')`.
- **At most one current primary participant per role**: a partial unique
  index over `(opportunity_id, role) WHERE is_primary AND valid_to IS NULL`.
  Historical rows keep the flag they had; `set_primary_participant` re-points
  it inside one transaction and closes nothing.
- **Participant rows are never deleted.** Leaving the deal closes `valid_to`.
- **Typed foreign keys only.** No `subject_type` / `subject_id` pair, no JSON
  list of contacts on the opportunity, no association-label table. A new role
  is a migration extending the CHECK; a new kind of participant is a migration
  adding a typed FK.

**Existence rule.** `organization_id IS NOT NULL` **or** at least one current
participant exists — at creation and after every participant change. An
unqualified opportunity may therefore exist with only an unresolved
contact-point participant. **Organization is mandatory from `qualified`
onward and before any quote exists**: `stage IN ('lead','qualifying') OR
organization_id IS NOT NULL`, and quote creation additionally requires a
non-terminal stage.

**Consistency validation.** So that unrelated parties cannot be combined into
one opportunity by accident:

| Present | Required |
|---|---|
| participant person + participant contact point | if `contact_point.person_id` is set, it equals the participant's `person_id` |
| participant contact point + opportunity organization | if `contact_point.organization_id` is set, it is the opportunity's organization or an ancestor of it |
| participant person + opportunity organization | nothing — a participant may sit outside the customer organization (a procurement agency, a funding body's approver). Recording a participant never creates an affiliation; an affiliation is recorded separately when that fact is confirmed (§2.4) |

The checks run in the API command and again as database triggers — on every
participant insert or change, and when `organization_id` is set on an
opportunity that already has participants. A failing combination is refused
with the specific rule that failed. An `unattributed` contact point constrains
nothing and always passes.

### 3.4 Opportunity outcome

`stage ∈ {lead, qualifying, qualified, quoting, negotiating, won, lost,
abandoned}`. **`won` and `lost` exist only on the opportunity** — never on a
quote, a revision or a campaign recipient. Transitions and outcome rules are
owned by [`WORKFLOWS.md`](WORKFLOWS.md).

### 3.5 Quotes

A `crm.quote` is a numbered offer for exactly one opportunity. A
`crm.quote_revision` is an immutable priced snapshot; `crm.quote_line` holds
its items, logistics, fees and discounts. From approval on, a revision also
carries a **party snapshot** — sold-to legal identity and tax identifier,
billing and delivery addresses, recipient and signatory, terms, validity,
currency and FX context — so the current `crm.address` and participant rows
are lineage, never the rendering authority. A quote never carries an outcome;
the opportunity records which revision won. Currency, FX, margin, snapshot and
supersession rules are owned by [`WORKFLOWS.md`](WORKFLOWS.md) §W3a–W3b.

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
  (`organization_name`, `contact_address`, `postal_address`, `affiliation`,
  `contacted_address`, `supplier_candidate`, `historical_quote_candidate`, …)
  with `(source_record, kind, value_norm)` and a resolution.
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
| 2 | A named laboratory head at an institution, no address known | one `person`, one `affiliation(P, I)`, zero contact points. An opportunity may list P as a participant (`role = end_user`). Campaigns cannot include this person: no channel. |
| 3 | `compras@universidad.example` used by the whole university | one `contact_point(usage=shared_mailbox, person=NULL, organization=root)`. Global uniqueness means one block target, one prior-contact fact, one resolution target. Which faculty a given thread concerns lives on the opportunity and activity, not on the channel. |
| 4 | A faculty and its laboratory both use `contacto@facultad.universidad.example` | one contact point whose `organization_id` is the faculty (the common ancestor of the units that share it). The laboratory is a child organization row; the domain is `shared`. |
| 5 | A distributor that sells to OrigenLab and also buys from it | one organization; two `organization_relationship` rows (`supplier`, `customer`) overlapping in time. Marketing campaign policy decides inclusion explicitly, with a recorded reason — it is never resolved by guessing which role is "primary". |
| 6 | A lead with a known person but unknown employer | `opportunity(stage=lead, organization_id=NULL)` plus `opportunity_participant(person=P, role=end_user, is_primary)` — passes the existence rule. `qualified` and quote creation are refused until an organization is set. |
| 7 | A person is both laboratory director and purchasing contact at one institute | two current `affiliation` rows for the same `(person, organization)` pair with different `role_title`. Allowed — the uniqueness rule is per role, not per pair. |
| 8 | An inbound address on a tender, sender and institution unknown | `opportunity(stage=lead, organization_id=NULL)` with one participant `(person=NULL, contact_point=CP, role=purchasing)`. When the sender is identified, `link_participant_person` sets `person_id` on that same row; `qualified` still waits for an organization. |
| 9 | One person is technical contact and quotation recipient; purchasing is a shared mailbox | three participant rows on one opportunity: `(P, role=technical, is_primary)`, `(P, role=quote_recipient, is_primary)`, `(person=NULL, contact_point=compras@universidad.example, role=purchasing, is_primary)`. Concurrent roles for one person are allowed; primaries are per role. |
| 10 | A faculty laboratory receives equipment on campus and is billed through the university's central office | two `address` rows: `(organization=laboratory unit, site_label=laboratory)` and `(organization=root, site_label=central office)`. The revision names the first as `delivery_address_id` and the second as `billing_address_id` and freezes both into its party snapshot at approval. A later move of the laboratory supersedes the first row; the sent PDF does not change. |

## 7. Table inventory — the reviewed 32-table foundation

Seven private schemas. **32 application tables** — the current reviewed
foundation after the external CRM benchmark
([`ARCHITECTURE.md`](ARCHITECTURE.md) §13), not a permanent budget: a table
is added only when a relational invariant proves it necessary, removed when
nothing needs it, and every change is recorded here. Numbers are stable
identifiers, so the two D0.3 additions are appended as 31 and 32 rather than
renumbered into the `crm` block.
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
| 8 | `crm.opportunity` | lead → outcome lifecycle; the only owner of `won`/`lost` | organization **or** ≥1 current participant; no person or contact-point column; organization required from `qualified`; closed transition table |
| 9 | `crm.task` | follow-up with a due date | `done ⇔ completed_at IS NOT NULL` |
| 10 | `crm.activity` | operator-relevant interaction, optional message link | append-only; `(message_id, opportunity_id)` unique |
| 11 | `crm.domain_event` | the single audit stream | closed `event_type`; `payload_version`; validated payload; no UPDATE, no DELETE |
| 12 | `crm.quote` | numbered offer for one opportunity | number unique; the opportunity has an organization |
| 13 | `crm.quote_revision` | immutable priced snapshot with its party snapshot | frozen once approved, party snapshot included; one open revision per quote; stored totals reconcile; snapshot NOT NULL and validated from `approved` |
| 14 | `crm.quote_line` | items, logistics, fees, discounts with their own currency and FX | at most one principal item; a logistics allocation target is an item line |
| 15 | `comms.mailbox` | provider account, permissions, sync cursor | address unique; at most one production sender |
| 16 | `comms.message` | provider message evidence | `(mailbox_id, provider_message_id)` unique; `send_attempt_id` unique |
| 17 | `comms.message_participant` | from/to/cc addresses with optional resolution | `(message_id, role, address_norm)` |
| 18 | `comms.attachment` | MIME part metadata and Storage reference | `(message_id, part_index)`; `sha256` present when stored |
| 19 | `outbound.send_control` | global kill switches | single row `id = 1`; both flags default false; every change carries a reason |
| 20 | `outbound.campaign` | lifecycle, budget, policy, approval | status machine; budget serialized by a row lock |
| 21 | `outbound.campaign_recipient` | frozen audience, state, immutable recontact override | `(campaign_id, address_norm)` unique; inserts only while `draft` |
| 22 | `outbound.send_attempt` | the only send ledger, marketing and transactional | minted RFC 822 id unique; at most one open attempt per address |
| 23 | `outbound.contact_control` | purpose-scoped `block` / `prior_contact` / `cooldown` | `(scope, value_norm, kind, purpose)` unique; `block.purpose ∈ {all, marketing}`; `prior_contact` and `cooldown` ⇒ `marketing` only; `prior_contact` never deleted, never expires; truth table in [`WORKFLOWS.md`](WORKFLOWS.md) §1.6 |
| 24 | `evidence.source_record` | acquired external record and migration manifests | `dedupe_key` unique; supersession chain; quarantine flag |
| 25 | `evidence.assertion` | typed observation with resolution | `(source_record_id, kind, value_norm)`; closed `kind` |
| 26 | `catalog.product` | manufacturer model | `(manufacturer_organization_id, model_number)` unique |
| 27 | `catalog.supplier_product` | supplier price observation | append-only; `(supplier_organization_id, product_id, as_of)` |
| 28 | `procurement.notice` | ChileCompra notice head and history | `codigo_externo` unique; `disappeared_at` for withdrawal |
| 29 | `platform.operator` | auth user → role and status | auth uid unique; `role ∈ {admin, sales, viewer}` |
| 30 | `platform.command_receipt` | command idempotency | `(operator_id, idempotency_key)`; digest mismatch → 409 |
| 31 | `crm.address` | one structured postal location of one organization | typed `organization_id` NOT NULL; structured fields canonical; supersession chain; never edited or deleted; no billing / delivery / default flag |
| 32 | `crm.opportunity_participant` | human roles on one opportunity | person and/or contact point; closed `role`; one current primary per role; no overlap for the same subject and role; person ↔ contact point validated |

Counts by schema: `crm` 16, `comms` 4, `outbound` 5, `evidence` 2,
`catalog` 2, `procurement` 1, `platform` 2 — **32**.

**Deliberately absent.** A delivery-event table (attempt columns plus domain
events suffice); a recontact-override table (immutable recipient columns
suffice); a quote party-snapshot table (a validated snapshot column on the
revision suffices); an address history, provenance or geocoding table
(supersession rows and `origin_source_record_id` suffice); a generic
association or association-label table; a parse-failure table (the archive
holds them); per-aggregate event tables; any mart, mirror or projection table;
a party supertype over organization and person; a combined person / company /
address record; a lead table; a prospect table; a consent table. Adding any of
these reopens this decision.
