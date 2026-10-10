# OrigenLab V2 — domain model

**Purpose.** The exact vocabulary of the business and the exact shape of the
data that carries it.

**This document owns:** the glossary; organization, unit, domain, person,
affiliation and contact-point semantics; the prospect / lead / signal /
opportunity / quote distinctions; product, manufacturer and supplier
relationships; address and opportunity-participant semantics; the
**commercial case** and its three tables; identity, merge and
evidence-promotion principles; and the complete **36-table inventory**.

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
| **Opportunity** / **caso comercial** | One pursuable sale, with an owner, a stage, a requesting institution and/or participants, and an outcome. **The only place `won` and `lost` exist.** "Caso comercial" is the operator-facing Spanish name for this same row — a label, never a second table (§3.6). | `crm.opportunity` |
| **Requesting institution** | The organization that wants to buy on one case — its customer. **Only an operator decides one**; nothing infers it from a message. | `crm.opportunity_organization` (`role = requesting_institution`) + `crm.opportunity.organization_id` |
| **Case organization role** | What one institution is **to one case** — requesting institution, end user, purchasing agent, funder, supplier, manufacturer, or merely mentioned. Distinct from the relationship it holds with OrigenLab over all time (§3.1). | `crm.opportunity_organization` |
| **Interest** | One thing the case is seeking — a catalogue product, a manufacturer's model named in the evidence, or a description. Never a commitment and never a price. | `crm.opportunity_interest` |
| **Case evidence link** | The recorded reason to believe something about a case: a source record, an assertion, a message or a notice, cited with what it supports — or what it contradicts. | `crm.opportunity_evidence` |
| **Participant** | One human role on one opportunity — end user, technical, purchasing, finance, approver, quotation recipient, signatory — held over time by a person and/or a contact point. | `crm.opportunity_participant` |
| **Quote** | A numbered offer attached to exactly one opportunity, expressed as immutable revisions with lines. | `crm.quote`, `crm.quote_revision`, `crm.quote_line` |
| **Message** | One provider message in one mailbox. Communication evidence, not a ledger. | `comms.message` |
| **Activity** | An operator-relevant interaction deliberately linked to a CRM object: call, meeting, note, or a linked email. | `crm.activity` |
| **Domain event** | Append-only audit of one state transition, with a closed type and a versioned payload. | `crm.domain_event` |
| **Send attempt** | One intent to deliver one message to one address. Submission and delivery are tracked separately. **The only send ledger.** | `outbound.send_attempt` |
| **Contact control** | A `block`, a permanent `prior_contact` fact, or a dated `cooldown` on an address or a domain, each scoped to a `purpose` (`all` or `marketing`). | `outbound.contact_control` |
| **Quote document** | A past quote PDF held as evidence: one `source_record` of kind `quote_document` with its lines in `evidence.document_line`, each cross-checked. Never trusted truth; it informs price history. | `evidence.source_record`, `evidence.document_line` |
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
`usage ∈ {personal, work, shared_mailbox, individual_owner_unknown, unattributed}`, and
`confirmation`.

- **`UNIQUE (kind, value_norm)` globally.** One row per reachable channel, so
  blocks, prior-contact facts and message resolution can never split across
  duplicate rows.
- **There is no `consent_status` column.** Sending authority, blocks, the
  historical-contact fact and cooldowns live **only** in
  `outbound.contact_control` ([`DATA.md`](DATA.md), [`WORKFLOWS.md`](WORKFLOWS.md)).
- Shape checks: `personal ⇒ person NOT NULL ∧ organization NULL`;
  `work ⇒ person NOT NULL ∧ organization NOT NULL`;
  `shared_mailbox ⇒ person NULL`;
  `individual_owner_unknown ⇒ person NULL ∧ organization NOT NULL`;
  `unattributed ⇒ both NULL`.
- **`shared_mailbox` and `individual_owner_unknown` are opposite claims, and neither is a
  default.** The first says two or more people read this desk. The second says one
  individual owns the address and nobody has recorded who — the institution because it is
  known, the person absent because they are not. A named-looking address is
  `individual_owner_unknown`; calling it a shared mailbox is a claim an operator makes
  deliberately and justifies, never a value a boundary picks because the row shape allowed
  it. Neither value creates a `person`.
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
| A named address at a known organization, owner not recorded | `(person=NULL, organization=O, usage=individual_owner_unknown)`. It becomes `work` when a person is recorded — a promotion of knowledge, not a correction |
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

<a id="m-dom-silence"></a>
**Silence never closes a case.** **[V2 DECISION]**, approved 2026-09-22.
`abandoned` is a human act with a recorded motive, not an expiry:

- `stage = 'abandoned'` requires a non-blank `close_reason`. **(impl)** the
  shipped `opportunity_closed_shape` CHECK is joined by
  `stage <> 'abandoned' OR length(btrim(close_reason)) > 0`.
- **No timer, cron job, queue worker, classifier or import may write `stage`.**
  `crm.*` has exactly one writer — the FastAPI command boundary
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §3.1) — and `abandon_opportunity`
  carries an operator identity like every other command.
- **Inactivity is a read, not a state.** The dashboard may show *sin actividad
  hace X días*, derived at query time from `crm.activity` and the case's
  evidence links. It is never a column, never a stage, never an event, and
  never a decision the system makes on the operator's behalf. A case with no
  activity for a year and no recorded motive is still open, and honestly so.

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

**Historical quotations** — **[V2 DECISION]**, owner gates G1–G5/2026-09-25.1; schema in
migration `20260925200000`. A quotation OrigenLab sent before V2 existed is recorded as found,
never re-authored. `crm.quote.number_origin` says where the number came from: `minted` (V2's
own serial, unique across the CRM) or `printed_historical` (the number exactly as printed on the
document — never padded, suffixed or renumbered — unique **per opportunity**, because the same
serial really was printed for two different clients). Its revisions carry
`origin = 'historical_import'`: the document hash, the time it was sent and the evidence record
of the message that carried it are required; totals, approval, party snapshot and currency may
be absent because nobody parsed them. A historical revision is immutable; its only moves are a
supersession (the owner's canonical version) and `sent → void`, the append-only rollback. It is
written only by one writer (`apps/api` `v2/case_quotation.py`), reached by three commands: the
import's `record_historical_quotation`, the email → cases rules R3/R4, and an operator's
`record_case_quotation` («Registrar cotización» — a quote already sent, from a Gmail message
already linked to the case). Because its revision number is the order the documents were
recorded, not the order they were sent, a historical revision may be superseded by a
lower-numbered revision of its quote — only ever by a current one, never by itself (migration
`20261006120000`); that is how an operator settles «more than one current revision» with
`resolve_current_revision`. Every V2-authored revision keeps the forward rule.

<a id="m-dom-case"></a>
### 3.6 The commercial case

**[V2 DECISION]**, approved 2026-09-22. **Built 2026-09-22** by
`supabase/migrations/20260922170000_slice3_crm_commercial_case.sql`: the three
tables, their constraints, guards, grants and RLS policies exist and are proven
by `supabase/tests/062_constraints_crm_commercial_case.sql`.

**Six commands write them, as of 2026-09-22** —
`20260922200000_slice3_commercial_case_commands.sql` added the audit vocabulary
they emit and the stage machine as a trigger, and
`apps/api/src/origenlab_api/v2/case_command*.py` is the boundary
([`WORKFLOWS.md`](WORKFLOWS.md) §W2, [`STATUS.md`](STATUS.md) §2.7.17). **They
are not reachable from a browser**: `apps/dashboard-proxy` allows no POST under
`/v2`, every button in the operator workspace is `disabled`, and the routes are
mounted only behind `ORIGENLAB_V2_COMMANDS_ENABLED`. Every table below is still
empty, because nothing real has been decided.

**The commercial case *is* `crm.opportunity`.** *Caso comercial* is the
operator-facing Spanish name for the row this schema calls an opportunity. It
is a naming decision, not a modelling one: there is **no `commercial_case`
table**, no case identifier, no case-to-opportunity conversion and no second
lifecycle. A case is one pursuable sale, exactly as §3.3 and §3.4 define it,
and it keeps the one stage machine of [`WORKFLOWS.md`](WORKFLOWS.md) §1.1.

What the case lacks is not a table of its own. It is three facts the
opportunity row cannot hold:

| Missing fact | Why the existing model cannot hold it |
|---|---|
| Every institution named on the case, and what each one is **to this case** | `opportunity.organization_id` is a single nullable pointer, and `crm.organization_relationship` records what an organization is to **OrigenLab over all time** — not what it is to one deal |
| What is actually being sought | nothing below the quote holds it. `crm.quote_line` exists only once a quote does, and most cases end before one |
| Which evidence is the reason to believe any of it | `opportunity.origin_source_record_id` holds exactly one record, and `crm.activity` records that an interaction *happened*, not that a belief is *justified* |

Three tables, each answering exactly one of them.

#### 3.6.1 `crm.opportunity_organization` — an institution's role on one case

Columns: `opportunity_id` (NOT NULL), `organization_id` (NOT NULL), `role`,
`valid_from`, `valid_to` (NULL means current), `confirmation ∈
{machine_proposed, confirmed}`, `confirmed_by_operator_id`,
`origin_source_record_id` (nullable, retained forever), `note`, and the
supplier-exception triple `supplier_exception_by_operator_id`,
`supplier_exception_reason`, `supplier_exception_at` (§3.6.1, all-or-none).

`role ∈ {requesting_institution, end_user_institution, purchasing_agent,
funder, supplier, manufacturer, mentioned}`:

| Role | Meaning |
|---|---|
| `requesting_institution` | the institution that wants to buy — the customer of this case |
| `end_user_institution` | where the equipment will be used, when that is not the buyer (a laboratory bought for by a central purchasing office) |
| `purchasing_agent` | a body purchasing on another's behalf (a *unidad de compra*, a *central de abastecimiento*) |
| `funder` | a grant or funding body named on the case |
| `supplier` | the organization OrigenLab would buy from to serve this case |
| `manufacturer` | the maker of the equipment sought |
| `mentioned` | named in the evidence, role not decided. **Honest silence, not a guess** |

Rules:

- **An organization may hold several concurrent roles on one case**; a
  distributor may be `supplier` and `manufacturer` at once. The same
  `(opportunity, organization, role)` may not overlap itself in time.
  **(impl)** an exclusion constraint over `opportunity_id`,
  `organization_id`, `role` and `daterange(valid_from, valid_to, '[)')`.
- **At most one current `requesting_institution` per case.** **(impl)** a
  partial unique index over `(opportunity_id)
  WHERE role = 'requesting_institution' AND valid_to IS NULL`.
- **A machine may only ever propose `mentioned`.** **[V2 DECISION]**,
  approved 2026-09-22 at implementation. Every other role — including
  `manufacturer`, which the approved-brand lookup can suggest with confidence —
  is a reading of what an institution is *to this deal*, and that reading is an
  operator's. **(impl)** `confirmation <> 'machine_proposed' OR role =
  'mentioned'` as a CHECK. Naming an institution and deciding its part are now
  two different acts, and only the first is automatable.
- **A `requesting_institution` row can only be written by a human.**
  **(impl)** `role <> 'requesting_institution' OR (confirmation = 'confirmed'
  AND confirmed_by_operator_id IS NOT NULL)` as a CHECK. A machine-proposed
  requesting institution is not refused by policy — it is unrepresentable.
- **Rows are never deleted.** A wrong decision is corrected by closing
  `valid_to` and opening the right row; both stay readable.
- **A row here never creates, implies or lifts a
  `crm.organization_relationship`.** Being named on a case is not a
  commercial relationship with OrigenLab. Opening a `prospect` or `customer`
  relationship is a separate operator act, with its own command and its own
  event ([`WORKFLOWS.md`](WORKFLOWS.md) §W2 step 1).

**Reconciliation with `opportunity.organization_id`.** The shipped column
keeps its meaning — the customer organization — and gains an exact
definition: **it is the confirmed requesting institution**. When
`organization_id` is set, a current confirmed `requesting_institution` row
must exist on that case for that same organization; `set_requesting_institution`
writes both inside one transaction, and a trigger enforces the agreement in
both directions.

*Rejected alternative:* dropping `organization_id` and deriving it. It would
turn the shipped declarative guarantees — `opportunity_organization_required_from_qualified`
and the quote precondition — into application-level triggers, trading a
promise the database keeps for one the code keeps. The redundancy is the
link-plus-value pattern already adopted for the quote party snapshot
([`ARCHITECTURE.md`](ARCHITECTURE.md) §13), and it is bounded by one trigger.

<a id="m-dom-supplier-never-customer"></a>
**A supplier or manufacturer is never made a customer by appearing in a
message.** Hielscher is the worked case: it is an approved supplier brand, and
a message that names it is almost always a *third party asking OrigenLab about
Hielscher equipment*. On such a case Hielscher appears as
`opportunity_organization(role ∈ {manufacturer, supplier})` and as
`opportunity_interest.manufacturer_organization_id` — never as the
`requesting_institution`, and never as a prospect.

- The brand is read from the **closed approved-brand list**, which is a lookup,
  not an inference from the message.
- Appearing on a case opens **no** `prospect` and **no** `customer`
  relationship for it, and confers no marketing permission on any of its
  addresses (§3.6.4).
- **(impl)** a CHECK cannot consult the brand list, so the rule is enforced
  twice: `add_case_organization` refuses `requesting_institution` for an
  organization holding a current `manufacturer` or `supplier` relationship
  (`supplier_exception_required`, 422), and a trigger repeats the refusal in the
  database. An exception sent where none was needed is refused too
  (`supplier_exception_is_not_needed`): recording one nobody needed would read
  later as a supplier OrigenLab sold to.
**The supplier exception.** **[V2 DECISION]**, approved 2026-09-22. §3.1
already allows roles to coexist, and worked example 5 is a real distributor
that both sells to and buys from OrigenLab, so the refusal above is
overridable — **only by an operator, only with a justification, and only in
the open.**

- **Never proposed.** No machine, import, classifier or promotion may propose
  such a row. This is already unrepresentable: a `requesting_institution` row
  must carry `confirmation = 'confirmed'` and a named operator, and the
  exception triple is only writable by the command.
- **The exception is a reason-bearing triple**, written together with the row:
  `supplier_exception_by_operator_id`, `supplier_exception_reason` (non-blank)
  and `supplier_exception_at`. **(impl)** an all-or-none CHECK; a second CHECK
  confining the triple to `role = 'requesting_institution'`; a trigger
  requiring it whenever the organization holds a current `supplier` or
  `manufacturer` relationship; and — as on the recontact override
  ([`WORKFLOWS.md`](WORKFLOWS.md) §W12) — a trigger rejecting **every later
  change** to the triple. The justification is also carried in the domain
  event. There is no override table; the columns are the record.
- **Both truths stay visible.** The exception never edits, closes, weakens or
  hides the organization's `supplier` / `manufacturer` relationship, and every
  surface that shows the case must show both readings at once — *proveedor /
  fabricante registrado* **and** *solicitante en este caso — excepción
  justificada*, with the operator and the reason. A surface that shows only
  one of the two is wrong.
- **It grants nothing else.** No marketing permission on any address
  (§3.6.4), no global `prospect` or `customer` relationship, no change to any
  other case. It is scoped to the one case, exactly like the recontact
  override is scoped to one campaign.
- **No data exception exists today.** Hielscher remains a supplier and
  manufacturer by default; this decision creates the mechanism, not a row.

#### 3.6.2 `crm.opportunity_interest` — what the case is seeking

Columns: `opportunity_id` (NOT NULL), `product_id` (nullable →
`catalog.product`), `manufacturer_organization_id` (nullable →
`crm.organization`), `model_text` (nullable, as observed), `description`,
`quantity` (nullable), `quantity_unit`, `confirmation`,
`confirmed_by_operator_id`, `origin_source_record_id`, `withdrawn_at`,
`withdraw_reason`, `note`.

- `num_nonnulls(product_id, manufacturer_organization_id, model_text) >= 1`.
  An interest may begin as a model string read off a message and gain a
  `product_id` later, on the same row — exactly as a participant gains a
  `person_id` (§3.3).
- When both `product_id` and `manufacturer_organization_id` are set they must
  agree with `catalog.product`'s manufacturer. **(impl)** a trigger, because
  the check crosses tables.
- `quantity > 0` when present. `withdrawn_at` and `withdraw_reason` are
  all-or-none, and **rows are never deleted**: a case that stopped being about
  a homogenizer should still show that it once was.
- **An interest is never a commitment and never a price.** It carries no
  amount, no currency and no margin; money exists only on
  `crm.quote_revision` and `crm.quote_line` (§3.5).
- A machine may propose an interest (`machine_proposed`); an operator confirms
  it. Naming a manufacturer here creates nothing for that manufacturer.

#### 3.6.3 `crm.opportunity_evidence` — why the case believes what it believes

Columns: `opportunity_id` (NOT NULL), `source_record_id`, `assertion_id`,
`message_id`, `notice_id`, `relation`, `linked_by_operator_id` (NOT NULL),
`linked_at`, `unlinked_at`, `unlink_reason`, `note`.

- **Exactly one typed subject**: `num_nonnulls(source_record_id,
  assertion_id, message_id, notice_id) = 1`. Typed foreign keys only — no
  `subject_type` / `subject_id` pair (§3.3).
- `relation ∈ {origin, supports_requesting_institution, supports_interest,
  supports_participant, mentions, contradicts}`. **`contradicts` is
  first-class**: recording the evidence *against* a case is how a case is
  later closed honestly, and it is the only durable place that reading can
  live.
- One `(opportunity, subject, relation)` while linked. **(impl)** one partial
  unique index per subject column, `WHERE unlinked_at IS NULL`.
- Append-only. Unlinking sets `unlinked_at` and `unlink_reason` together; rows
  are never deleted.
- **This does not replace `crm.activity`, and does not duplicate it.** An
  activity says *an interaction happened* and is unique per
  `(message_id, opportunity_id)`. An evidence link says *this document is the
  reason we believe X*, admits several relations for one subject, and carries
  assertions and notices, which `crm.activity` has no column for. A single
  message may legitimately be both.
- **The links stay within durable tables.** `evidence.*`, `comms.message` and
  `procurement.notice` are durable evidence, not rebuildable projections, so a
  typed FK is allowed here; the §5 prohibition is on foreign keys into
  rebuildable machine output, and none is created.

#### 3.6.4 Marketing is separate, in both directions

**Nothing in the case model touches `outbound.*`.**

- A case does not create an `outbound.campaign_recipient`, does not lift a
  `prior_contact`, does not create, weaken, scope or expire an
  `outbound.contact_control`, and **confers no marketing permission on any
  address it names**. An institution being a live case is not a reason to mail
  it a campaign.
- Conversely, **no campaign, reply, bounce or send outcome creates or advances
  a case**. Campaign-derived intent enters the same way every other signal
  does: `evidence.source_record` + `evidence.assertion`, then an operator
  command (§5, [`WORKFLOWS.md`](WORKFLOWS.md) §W1).
- The two systems meet in exactly one place: `comms.message`, which is
  evidence to both and authority for neither.
- No table in §3.6 carries a consent, subscription, opt-in or audience column.
  The only safety authority remains `outbound.contact_control`, keyed by
  normalized text and untouched by anything here ([`DATA.md`](DATA.md) §3).

#### 3.6.5 A case without a requesting institution

**A case may exist with no requesting institution, and must be able to.** An
inbound message from an unknown address naming an unknown institution is a
real case on the day it arrives, and forcing an institution onto it would mean
guessing one.

| Stage | Requesting institution |
|---|---|
| `lead`, `qualifying` | **optional** — the case may have only participants, even a single unresolved contact point (§3.3 existence rule) |
| `qualified` and onward | **required, and confirmed by a human** |
| any quote exists | **required, and confirmed by a human** |

This is the shipped constraint
(`opportunity_organization_required_from_qualified`, plus the quote
precondition) read through §3.6.1: because `organization_id` may only be set
together with a confirmed `requesting_institution` row, *qualified* and
*quoted* now both mean **an operator decided who is asking**. Nothing else can
satisfy them. A case may sit at `lead` indefinitely; §3.4 guarantees that
sitting there never closes it.

**A case that never found its requester must still be closable.** Corrected
2026-09-22 by `20260922200000_slice3_commercial_case_commands.sql`. The
constraint above read `stage IN ('lead', 'qualifying')`, which caught `lost` and
`abandoned` as well — and those are exits available from `lead` itself
([`WORKFLOWS.md`](WORKFLOWS.md) §1.1), not stages "onward" from `qualified`. As
shipped, an unanswered enquiry from an unknown institution could be opened and
then never closed: its only reachable states were `lead` and `qualifying`,
forever. The rule now names the two exits explicitly. `won` keeps the
requirement — a case is not won without a customer.

**A case is opened at `lead` and at no other stage**, and a terminal stage is
never revived. **(impl)** `crm.opportunity_stage_guard`, a trigger carrying the
§1.1 transition table; `advance_case_stage` refuses the same moves first, by
name. Optimistic concurrency stays a boundary contract rather than a trigger:
every case command reads `version`, shows it to the operator and writes back
under `WHERE version = %s`.

#### 3.6.6 Email → cases: the machine records, a person corrects

**[V2 DECISION]**, owner-approved 2026-10-05 (spec `2026-10-05-gmail-auto-cases-design.md`).

For the rule-backed actions R1–R6 only — link an email to its case, open a
case with its quote (and an institution «por confirmar», `machine_proposed`,
for an unknown domain), move a case to `won` on a purchase order or to `lost`
on «otro proveedor» — the system records instead of proposing. Everything
else (R7, R8) stays a proposal in Revisión.

- **Who acts.** The rules run in the API when an admin presses «Aplicar» on
  the previewed actions — and, for **R1 and R2 only** (a link to the one open
  case the thread or the quote number names — a number read from a PDF name,
  or from the subject line when no PDF names one (2026-10-10); owner-approved
  2026-10-06), on a
  timer in the API while an admin has switched the automatic run on in
  Revisión. The switch is off until an admin turns it on, is turned off the
  same way, and needs a note both ways; the automatic run acts on behalf of
  the admin who switched it on, and stops if that operator is no longer an
  active admin. R3–R6 always wait for «Aplicar». Their events are `crm.domain_event.actor_kind =
  'worker'`, with no `actor_operator_id`, and `payload.attribution` naming the
  rule, its reasons, the email and the admin who applied them. The row
  columns that require an operator (`owner_operator_id`,
  `linked_by_operator_id`, `confirmed_by_operator_id`,
  `created_by_operator_id`) name that admin, and so does the command receipt.
- **What they read.** Live Phase 4a captures only — never a staged
  historical record, never a rejected one.
- **Every action is undoable**, by a person, as a new event
  (`attribution.undoes_receipt_id`); nothing is deleted. A link is unlinked
  (`case_evidence.unlinked`); a created case is abandoned as
  `discarded_by_correction` with its quote revision voided; an institution «por
  confirmar» is archived and its domain removed unless another open case uses
  it; a `won`/`lost` the rules set is corrected back to the stage it came from
  (`opportunity.stage_corrected`) — the one exception to "a terminal case is
  never revived", allowed by the stage guard only for a terminal stage whose
  latest `opportunity.staged` event is a worker event. A case a person closed
  is never revived.

## 4. Products, manufacturers and suppliers

- A **manufacturer** is an organization holding the `manufacturer` relationship.
- `catalog.product` is a manufacturer's model, identified by
  `(manufacturer_organization_id, model_number)` and, since catalog 1a, by the
  wider `(manufacturer_organization_id, model_key)`: `model_key` is the model
  upper-cased with spaces, hyphens, underscores, dots and slashes removed, so
  «SONIC-100» and «sonic 100» are one product. A product belongs to exactly
  one manufacturer. It carries Spanish content (`name_es`, `description_es`,
  `category_es`, `specs`), physical data (weight, dimensions, origin country,
  HS code, dangerous goods) and a `product_kind` in `{equipment, accessory,
  consumable, spare_part, service}`. Its content has an origin
  (`import`, `machine`, `operator`); machine content is a proposal until an
  operator confirms it, and operator content is never overwritten by a tool.
- A **supplier** is an organization holding the `supplier` relationship. A
  supplier is not a separate identity table; a distributor that also buys is
  one organization with two relationship rows.
- `catalog.supplier_product` is an **append-only price observation**:
  `(supplier_organization_id, product_id, as_of, price_kind, min_qty)` with a
  price, a currency and a provenance. It is never overwritten; the current
  cost is the newest non-stale row by `as_of`, preferring a per-deal kind on
  the same day.
- **Price kinds** (`catalog.supplier_product.price_kind`): `dealer_net`,
  `list`, `map`, `supplier_offer`, `order_confirmation`, `purchase_order`,
  `costing_sheet`, `negotiated`. The last five are *per-deal* kinds — they
  record what one deal cost, not a standing list — and win over a list kind
  observed the same day. `is_stale` marks a price from a superseded list.
- **Supplier terms** (`catalog.supplier_terms`, one row per supplier
  organization): currency, origin country, import route (`import_courier`,
  `import_freight`, `domestic`), incoterm, default discount, packing share,
  whether the maker enforces MAP, default lead time. Set by an operator or by
  an importer when the supplier has none; never deleted.
- **Cost parameters** (`catalog.cost_parameter`): a closed vocabulary of
  costing inputs (carrier discount and fuel surcharge, customs fee, duty
  rates, insurance, cost profiles, markups per product kind, margin targets,
  price-deviation warning, VAT). Append-only history; the current value is the
  newest `valid_from <= now()`. The repository is public, so only the five
  public or regulatory keys are seeded; every other key is unset until an
  operator sets it per environment.
- **Document line check statuses** (`evidence.document_line.check_status`):
  `verified` (the AI extraction and the layout text agree), `single_source`
  (only one reading exists), `disputed` (they disagree) and `reviewed` (an
  operator resolved a disputed line). Only a disputed line may be reviewed,
  once, and only its quantity, prices and optional flag change.
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

### 5.1 Migration promotion — the one bulk carve-out

**[V2 DECISION]**, approved 2026-09-21.

§5 says promotion is an operator command and that nothing promotes on a timer, a
threshold or a similarity score. The **migration** promotion of historical evidence is
the single carve-out, and it is narrow enough that the rule above still holds in
substance: it never resolves an identity, so there is no judgement for an operator to
be deprived of.

It may create exactly two things, and only by **direct transcription** of a recorded
observation:

- a `crm.contact_point`, from a `contacted_address` assertion — the channel, and only
  the channel (§2.5);
- a `crm.organization`, from an `organization_name` assertion whose folded name is
  unique in the evidence.

It may never create a `person`, an `affiliation`, an `organization_relationship`, an
`address`, an `opportunity` or any participant, and it may never merge anything.

**The conservative identity defaults it applies.**

| Evidence | Resolution |
|---|---|
| A recognised role local part — `ventas@`, `contacto@`, `compras@`, `info@` and their kin | `contact_point` with `usage = 'shared_mailbox'`. An organization or shared mailbox, **never a person** |
| Anything else | `contact_point` with `usage = 'unattributed'`. **Unknown remains unknown** |
| A named personal address on a public mail domain | still `unattributed`; the public domain implies **no organization** |
| Two or more observed organization names that fold to the same key | **never merged, and never promoted separately.** All members go to review together |
| Anything that cannot be classified at all | review |

Four properties make the carve-out safe, and each is asserted by the migration rather
than asserted about it:

1. **No `crm.person` is ever created.** The migration evidence records no display name
   for anybody, so a person could only be derived from an address local part — an
   inference about a real human, not an observation. The count is checked inside the
   transaction and a non-zero value rolls the promotion back.
2. **No contact point is attached to an organization.** §2.2 makes a domain a routing
   hint and never an identity key on its own, so a role address is not joined to an
   organization because it shares its mail domain.
3. **A similarity score never promotes anything.** Folding two names to one key is only
   ever a reason to *stop* and ask, which is §5's fail-closed ambiguity rule applied
   rather than bypassed.
4. **Everything created is `machine_proposed`**, and carries `actor_kind = 'migrator'`
   in its domain event. No operator is impersonated, and nothing is confirmed truth
   until an operator confirms it.

**Organization `kind` under this carve-out.** §2.1 leaves the closed list of kinds
`[OPEN]` and the evidence records no kind, so a migrated organization takes
`kind = 'unknown'` — an explicit absence marker, not a classification. When the closed
list is decided, these rows are reclassified by the migration that introduces it.

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
| 11 | An unknown address writes asking for a price on a Hielscher ultrasonic homogenizer | `opportunity(stage=lead, organization_id=NULL)`; one participant `(person=NULL, contact_point=CP, role=purchasing)`; `opportunity_organization(Hielscher, role=mentioned, machine_proposed)` — the approved-brand lookup may name Hielscher but may not decide its part, so an operator confirms the `manufacturer` row; `opportunity_interest(manufacturer=Hielscher, model_text='UP200Ht')`; `opportunity_evidence(message=M, relation=origin)`. **Hielscher is not a prospect, not a customer and not the requesting institution.** The case has no requesting institution and cannot reach `qualified` until an operator names one |
| 12 | The same message names the sender's university; the operator recognizes it | `set_requesting_institution(case, university)` in one transaction: `opportunity_organization(university, role=requesting_institution, confirmation=confirmed, confirmed_by=O)` and `opportunity.organization_id = university`, plus `opportunity_evidence(assertion=A, relation=supports_requesting_institution)`. Hielscher's `manufacturer` row is untouched. No `prospect` relationship is opened for anyone — that is a separate act |
| 13 | A case at `lead` has had no activity for eleven months | it is still `lead`. The dashboard shows *sin actividad hace 334 días*, computed at read time. Closing it is `abandon_opportunity(case, reason)` by an operator; without that act no row changes and no event exists (§3.4) |
| 14 | A distributor that supplies OrigenLab asks to buy a unit for its own laboratory | the distributor's `supplier` relationship is untouched. `set_requesting_institution` is refused until the operator supplies a justification; with it, one `opportunity_organization(distributor, role=requesting_institution, confirmation=confirmed, confirmed_by=O)` carries `supplier_exception_reason`, and the triple can never be rewritten. The case card shows *proveedor registrado* **and** *solicitante — excepción justificada*. No marketing permission and no `prospect` relationship follow |

## 7. Table inventory — the reviewed 51-table foundation

Seven private schemas. **51 application tables** — 33 reviewed after the
external CRM benchmark, the three of §7.1, the one of §7.2, the four of
§7.3, the four of §7.4, the five of §7.5 and the one of §7.6 — the current
reviewed foundation
([`ARCHITECTURE.md`](ARCHITECTURE.md) §13), not a permanent budget: a table
is added only when a relational invariant proves it necessary, removed when
nothing needs it, and every change is recorded here. Numbers are stable
identifiers, so the two D0.3 additions are appended as 31 and 32, and the
Slice 0 / M10c reply table as 33, the commercial case as 34–36, the
campaign safety block as 37, the shared sign-in tables as 38–41 and the
campaign-content archive and CRM authoring tables as 42–45 and the catalog
1a tables as 50–54 (46–49 are not assigned by this change), rather
than renumbered into their schema blocks.
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
| 12 | `crm.quote` | numbered offer for one opportunity | a minted number is unique; a printed historical number is unique per opportunity (§3.5); the opportunity has an organization |
| 13 | `crm.quote_revision` | immutable priced snapshot with its party snapshot | frozen once approved, party snapshot included; one open revision per quote; stored totals reconcile; snapshot NOT NULL and validated from `approved`; a `historical_import` revision is one document, immutable, sent or void (§3.5) |
| 14 | `crm.quote_line` | items, logistics, fees, discounts with their own currency and FX | at most one principal item; a logistics allocation target is an item line |
| 15 | `comms.mailbox` | provider account, permissions, sync cursor | address unique; at most one production sender |
| 16 | `comms.message` | provider message evidence | `(mailbox_id, provider_message_id)` unique; `send_attempt_id` unique |
| 17 | `comms.message_participant` | from/to/cc addresses with optional resolution | `(message_id, role, address_norm)` |
| 18 | `comms.attachment` | MIME part metadata and Storage reference | `(message_id, part_index)`; `sha256` present when stored |
| 19 | `outbound.send_control` | global kill switches | single row `id = 1`; both flags default false; every change carries a reason |
| 20 | `outbound.campaign` | lifecycle, budget, policy, approval, frozen content (subject, preheader, bodies), frozen audience criteria, the eligibility policy version and the audience fingerprint | status machine; budget serialized by a row lock; content changes only while `draft` (trigger); from `audience_frozen` onward `subject`, `body_text`, `content_sha256`, `content_frozen_at`, a versioned `audience_criteria` object, `audience_policy_version` and `audience_sha256` are all present and never rewritten (trigger `campaign_freeze_facts_write_once`), and the campaign never returns to `draft` — a changed campaign is a new draft and a new freeze; internal planning (`planned_for_date` in America/Santiago, optional `planned_for_at` in UTC, `planning_version`) moves only while `draft` or `audience_frozen`, never with a status change (trigger `campaign_planning_guard`), and schedules nothing |
| 21 | `outbound.campaign_recipient` | frozen audience, state, every exclusion reason, immutable recontact override; the write-once snapshot (inclusion, reasons, notes, relevance with its evidence and date, identity review, W12 recontact decision, campaign version, content fingerprint, policy version) | `(campaign_id, address_norm)` unique; a snapshot row is inserted only while its campaign is `draft`, never rewritten in its snapshot columns — `recontact_review` and the override triple included — and never deleted (trigger `campaign_recipient_snapshot_guard`); on a snapshot row the override triple is set exactly when `recontact_review` approves, and an approval lifts `prior_contact` only ([`WORKFLOWS.md`](WORKFLOWS.md) §W12); `(campaign_id, content_sha256, policy_version)` references its campaign's freeze; `excluded ⇔ cardinality(exclusion_reasons) > 0`, drawn from the closed vocabulary of [`WORKFLOWS.md`](WORKFLOWS.md) §1.4, never NULL; relevance is `evidenced` or `sin_informacion`, never a score and never a permission |
| 22 | `outbound.send_attempt` | the only send ledger, marketing and transactional | minted RFC 822 id unique; at most one open attempt per address |
| 23 | `outbound.contact_control` | purpose-scoped `block` / `prior_contact` / `cooldown` | `(scope, value_norm, kind, purpose)` unique; `block.purpose ∈ {all, marketing}`; `prior_contact` and `cooldown` ⇒ `marketing` only; `prior_contact` never deleted, never expires; an unsubscribe (`reason = unsubscribe`, or any block a «BAJA» was linked to) never updated or deleted — no re-subscription; written only through `outbound.add_contact_control`; truth table in [`WORKFLOWS.md`](WORKFLOWS.md) §1.6, «BAJA» in §W10 |
| 24 | `evidence.source_record` | acquired external record and migration manifests | `dedupe_key` unique; supersession chain; quarantine flag |
| 25 | `evidence.assertion` | typed observation with resolution | `(source_record_id, kind, value_norm)`; closed `kind`; an `unsubscribe_request` is written only by `outbound.add_contact_control` and resolves to its `contact_control` — or stays `unresolved` while held for review, blocking its exact address from marketing, until that function decides it once — resolved to its control, or (admin, pending holds only) dismissed as `rejected`, which may still be confirmed later; a confirmed request is never dismissed; otherwise never changed. The mail triage (ARCHITECTURE.md §8, D2) inserts `message_triage` (one per message and classifier version, `value_norm = triage:v<N>`), `product_mention`, and versioned `delivery_failure` rows as machine evidence and never resolves them; only a single-recipient Batch-A no-such-user `delivery_failure` may feed the closed-list global `invalid_address` block |
| 26 | `catalog.product` | manufacturer model, its Spanish content, specs and physical data | `(manufacturer_organization_id, model_number)` unique **and** `(manufacturer_organization_id, model_key)` unique, where `model_key = upper(model_number` with whitespace, `-`, `_`, `.`, `/` removed`)` (generated; the database decides, not the API); `product_kind` closed; `content_origin ∈ {import, machine, operator}`; a confirmation names its operator and time together; `version` for optimistic concurrency; never deleted (`active = false` retires it) |
| 27 | `catalog.supplier_product` | supplier price observation | append-only; `(supplier_organization_id, product_id, as_of, price_kind, min_qty)` unique, nulls not distinct; closed `price_kind`; `discount_pct` in [0, 1) |
| 28 | `procurement.notice` | ChileCompra notice head and history | `codigo_externo` unique; `disappeared_at` for withdrawal |
| 29 | `platform.operator` | auth user → role and status | auth uid unique; `role ∈ {admin, sales, viewer}`; the runtime API role only reads it — created and changed by the migrator roster tools (`20260928192000`) |
| 30 | `platform.command_receipt` | command idempotency | `(operator_id, idempotency_key)`; digest mismatch → 409 |
| 31 | `crm.address` | one structured postal location of one organization | typed `organization_id` NOT NULL; structured fields canonical; supersession chain; never edited or deleted; no billing / delivery / default flag |
| 32 | `crm.opportunity_participant` | human roles on one opportunity | person and/or contact point; closed `role`; one current primary per role; no overlap for the same subject and role; person ↔ contact point validated |
| 33 | `outbound.campaign_reply` | reply attribution for one frozen audience — the classifier's proposed class and the operator's recorded verdict | one row per `comms.message`; the reply never crosses campaigns (`(recipient, campaign)` FK); closed `proposed_class` and `operator_class`; the operator verdict triple is all-or-none; **never a writer of `campaign_recipient.state`** |

Counts by the first 33: `crm` 16, `comms` 4, `outbound` 6, `evidence` 2,
`catalog` 2, `procurement` 1, `platform` 2 — **33**.

### 7.1 The commercial case (§3.6) — built, and written by six commands

Approved and built 2026-09-22. Three tables, all in `crm`, numbered 34–36.
`supabase/tests/010_inventory.sql` and `supabase/scripts/verify_chain.sh`
assert **36** and `crm` **19**, moved in the same change as the migration that
creates them. The commands that write them arrived on 2026-09-22 and added **no
table**: the inventory is unchanged at 36. All three are still **empty**,
because the boundary is unreachable from any browser and nothing real has been
decided through it.

| # | Schema.table | Unique responsibility | Key invariant |
|---|---|---|---|
| 34 | `crm.opportunity_organization` | what one institution is **to one case** | closed `role`; the machine may only propose `mentioned`; at most one current `requesting_institution` per case; `requesting_institution` ⇒ `confirmed` with a named operator; a current supplier / manufacturer ⇒ the all-or-none supplier-exception triple, never rewritten; no overlap for the same `(case, organization, role)`; never deleted; writes no `organization_relationship` |
| 35 | `crm.opportunity_interest` | what the case is seeking | ≥1 of product, manufacturer, model text; product and manufacturer agree; `quantity > 0`; withdrawal is a reason-bearing pair; **no money column** |
| 36 | `crm.opportunity_evidence` | why the case believes what it believes | exactly one typed subject among source record / assertion / message / notice; closed `relation` including `contradicts`; one link per `(case, subject, relation)` while linked; append-only |

Counts by schema: `crm` 19, `comms` 4, `outbound` 6, `evidence` 2,
`catalog` 2, `procurement` 1, `platform` 2 — **36**.

### 7.2 The campaign safety block — built 2026-09-27

One table, in `outbound`, numbered 37 (`20260928100000_slice5_campaign_block`).
A pause needed its own relation: `outbound.campaign.status = 'paused'` had no
command and nothing enforced it, a status can be moved by the (unbuilt) status
machine, and a pause must carry who decided it and why, survive every status
change and be lifted only by an explicit, evented decision.
`supabase/tests/010_inventory.sql`, `supabase/scripts/verify_chain.sh` and
`supabase/scripts/replay_evidence.sh` assert **37** and `outbound` **7**.

| # | Schema.table | Unique responsibility | Key invariant |
|---|---|---|---|
| 37 | `outbound.campaign_block` | an admin's safety block on one campaign, on every campaign, or on a V1 campaign known only by its key — the pause every campaign boundary is refused by ([`WORKFLOWS.md`](WORKFLOWS.md) §W13) | closed `scope` with a matching target shape; mandatory reason; at most one active block per target; placed only by an active admin (or a migration, as the owner) and lifted only by an active admin with a reason (trigger `campaign_block_guard`, checked against `platform.operator`); placement write-once, lift exactly once (`version` 1 → 2), immutable afterwards, never deleted; **no expiry column**; while active, `outbound.campaign_hold_guard` refuses freeze, approval, activation, dry-run/approval events, reservation and marketing attempt creation or dispatch, and `outbound.marketing_contact_refusals` reports it; placing or lifting one writes no other table |

Counts by schema: `crm` 19, `comms` 4, `outbound` 7, `evidence` 2,
`catalog` 2, `procurement` 1, `platform` 2 — **37**. The migration seeds one
row: the September wave-2 incident hold (`legacy_campaign`
`septiembre18-2026-wave2`, reference `incident_hold_september_2026`), active,
placed by the migration.

### 7.3 Shared Workspace sign-in with operator profiles — built 2026-09-28

Four tables, all in `platform`, numbered 38–41
(`20260928180000_slice1_shared_workspace_operator_profiles`, then
`20260928190000_slice1_principal_pinned_google_subject` and
`20260928191000_slice1_revocable_profile_sessions`). One Google
Workspace account is used by several people, so **the Google identity and the
person acting are two facts in two relations**: signing in with Google proves
the account (a *principal*) and grants nothing; the person is the
`platform.operator` chosen on a profile screen and proven by their own PIN,
which the API verifies. The operator is still the only thing any command
authorizes or attributes to (`crm.domain_event.actor_operator_id`,
`platform.command_receipt.operator_id`), so nothing downstream changes.

`platform.operator` (#29) gains `sign_in_kind`: `google_account` (today's
operator, reached by signing in with its own address, which is still
required) or `shared_profile` (reached only through a profile, **with no
address at all** — no invented or duplicate mailbox represents a person). The
kind never changes in place, so individual Google accounts can be added later
beside the shared one without touching a profile. A principal's address can
never also be an operator's address (both directions are refused by trigger).

| # | Schema.table | Unique responsibility | Key invariant |
|---|---|---|---|
| 38 | `platform.auth_principal` | an external sign-in identity (one Google account), its principal-wide PIN throttle and the one PIN attempt in flight through it | `(provider, email_norm)` unique; the pinned Google account — `provider_issuer` (canonical `https://accounts.google.com`) and `provider_subject`, set together or not at all, `(provider, provider_subject)` unique — required by the API in production and set only by the migrator roster tool; never an operator's address; `version` bumps on any address, issuer, subject or status change; the attempt record (`pin_attempt_id`, `_xact`, `_operator_id`, 32-byte `_nonce`) is all set or all null, written only by the two PIN-attempt functions, and never bumps `version` |
| 39 | `platform.operator_profile` | a `shared_profile` operator selectable from one principal, its PIN and its per-profile throttle | the composite key `(operator_id, 'shared_profile')` → `platform.operator (id, sign_in_kind)` admits no other kind; `(principal_id, profile_key)` unique; `pin_hash` must be an Argon2id PHC string, so a plaintext PIN cannot be stored, and is the one column the runtime role cannot read (`20260929100000`); `version` bumps on any PIN, link, key or status change, never on a throttle update |
| 40 | `platform.auth_event` | the append-only authentication audit: profile selected, refused, a profile or a whole principal locked, cleared, logout, and a migrator's early lockout clear (`lockout.cleared`) | closed `event_type` and closed `refusal_reason` (no free text, so no PIN can be recorded), the reason carried by every refusal and by the failure that started a lock; principal id and address recorded as a pair; append-only (`platform.reject_mutation`); the four PIN outcomes (`profile.selected`, `profile.selection_refused`, `profile.locked`, `principal.locked`) are written only by `platform.finish_pin_attempt`, exactly one per attempt, and `lockout.cleared` only by the migrator tool — each accepted only from a member of `origenlab_owner`, never from the runtime role (trigger `auth_event_actor_guard`, `20260928193000`, `20260929100000`) |
| 41 | `platform.auth_session` | one dashboard session — a shared sign-in (a principal, and the profile selected through it) or an operator's own Google account — so logout and «Cambiar perfil» can revoke it | `token_hash` is a 32-byte keyed HMAC of the cookie's random identifier — never the identifier; lifetime at most seven days; `sign_in_kind` `shared_profile` (principal required, `operator_id` → `platform.operator_profile`) or `google_account` (no principal, no profile, `(account_operator_id, sign_in_kind)` → `platform.operator (id, sign_in_kind)`); a request is served only while its row is neither revoked nor expired (database clock); selecting or clearing a profile revokes the row and inserts its successor with the same `expires_at`; identity, kind, principal, operators and issue time immutable, `expires_at` only ever moves earlier, a revocation (closed reason: `logout`, `profile_selected`, `profile_cleared`) is final (trigger `auth_session_guard`); the runtime role never deletes — the migrator prunes |

`platform.operator`'s own `version` now also bumps on any role, status or
address change **whoever writes the row**. A signed session carries the
operator's, the profile's and the principal's `version` and is re-checked on
every request, so a role, PIN or status change ends every session it affects.
The runtime API role reads all three tables — `operator_profile` column by
column, never `pin_hash` — and appends the `profile.cleared` and
`session.logout` audit events. A PIN is decided only by the SECURITY DEFINER
pair `platform.begin_pin_attempt` / `platform.finish_pin_attempt`
(`20260929100000`, ARCHITECTURE.md §6.2), which compare a proof bound to a
one-use attempt with the stored verifier and themselves write the verdict,
the counters, the lock and the one audit event; the runtime role cannot
declare a success, write a throttle column, or write a principal, a profile
link or a PIN hash — provisioning runs as the owner through
`apps/api/scripts/profile_roster.py`. The worker has no privilege on any of
them. No names, principals, profiles or hashes are seeded by the migration.
`supabase/tests/010_inventory.sql`, `supabase/scripts/verify_chain.sh` and
`supabase/scripts/replay_evidence.sh` assert **41** and `platform` **6**;
`supabase/tests/073_shared_workspace_profiles.sql` and
`074_revocable_profile_sessions.sql` prove the invariants.

`platform.auth_session` (#41) makes every dashboard session revocable: a
shared sign-in's (`sign_in_kind = 'shared_profile'`) and, since
`20260928195000`, an operator's own Google account's
(`sign_in_kind = 'google_account'`, naming the operator in
`account_operator_id`). The runtime role reads and inserts rows and may set
only `revoked_at` / `revoked_reason`; it deletes nothing (no runtime role holds
`DELETE` anywhere), so ended rows are pruned by the migrator
(`apps/api/scripts/profile_auth_admin.py prune-sessions`).

Counts by schema: `crm` 19, `comms` 4, `outbound` 7, `evidence` 2,
`catalog` 2, `procurement` 1, `platform` 6 — **41**.

### 7.4 Campaign-content archive and CRM authoring — built 2026-09-30

Four tables — two in `outbound` and two in `crm` — numbered 42–45
(`20260930120000_slice6_campaign_content_archive_and_crm_authoring`).
`supabase/tests/010_inventory.sql` and `supabase/scripts/verify_chain.sh`
assert **45**, `crm` **21** and `outbound` **9**.

**Lifecycle columns on existing tables.** The migration also adds soft-removal
and soft-archive columns to five existing CRM tables rather than separate
history tables: `crm.organization` gains `archived_at / archived_by_operator_id / archive_reason`
(all-or-none triple); `crm.person` gains `title`, a `status` vocabulary
(`active / inactive / archived`) and the same archive triple; `crm.contact_point`
gains `status` (`active / inactive`), `version`, `note`, `deactivated_at` and
`deactivated_by_operator_id`; `crm.organization_domain` and `crm.external_identifier`
each gain a soft-removal triple (`removed_at / removed_by_operator_id / remove_reason`,
all-or-none). Never-deleted triggers protect `crm.organization_product_line`
(never physically deleted; closed by `valid_to`).

**Domain event vocabulary rewrite.** The `crm.domain_event_is_valid` CHECK function
is replaced to add the `note` aggregate kind (`note.created`, `note.revised`,
`note.archived`) and new event types for the lifecycle mutations:
`person.updated / archived / restored`, `contact_point.updated / deactivated`,
`organization.updated / archived / restored / identifier_added / identifier_removed / domain_added / domain_removed / domain_restored / product_line_linked / product_line_unlinked`,
and `assertion.supplier_candidate_confirmed / rejected`.

| # | Schema.table | Unique responsibility | Key invariant |
|---|---|---|---|
| 42 | `outbound.campaign_content` | immutable archive of one campaign's frozen HTML and plain-text bodies, keyed by the campaign's `content_sha256` | owned by `origenlab_owner`; INSERT-only (trigger `campaign_content_immutable`); `(campaign_id, content_sha256)` unique; no address or personal data columns; api and worker may SELECT |
| 43 | `outbound.campaign_content_message` | per-send-attempt content snapshot linking a send attempt to the archived content | belongs to exactly one `campaign_content`; one row per `send_attempt_id`; INSERT-only through the same owner guard; api and worker may SELECT |
| 44 | `crm.note` | operator-authored annotation on any CRM object — person, organization, case, contact point, product | body, author and subject are immutable once written (trigger `crm.note_guard`); archiving advances `version` by exactly one and is final; never deleted; a revision chains to a root note; api may INSERT and archive (column-level UPDATE: `status`, `archived_at`, `archived_by_operator_id`, `archive_reason`, `version`) |
| 45 | `crm.organization_product_line` | links one organization to one product-line label — the brands and equipment families it handles | closed product-line vocabulary; at most one active link per `(organization_id, product_line)` (partial unique index excludes soft-removed rows); unlinked by setting `valid_to` (never deleted; trigger refuses DELETE); api may INSERT and unlink (column-level UPDATE: `valid_to`, `unlinked_by_operator_id`, `note`, `updated_at`) |

### 7.5 Catalog 1a — products, images, supplier terms, exchange rates, cost parameters, document lines

Built locally 2026-10-05 by `20261005134832_slice7_catalog_products_suppliers.sql` and
`20261005140628_slice7_catalog_pricing_inputs_document_lines.sql`. Not applied to any
database other than disposable test clusters. Five tables, numbered 50–54; #26, #27 and #44
change as above.

| # | Schema.table | Unique responsibility | Key invariant |
|---|---|---|---|
| 50 | `catalog.product_image` | one product photo in the private `catalog` Storage bucket | `(storage_bucket, storage_path)` unique; bucket is always `catalog`; `status ∈ {proposed, confirmed, hidden}`; never deleted (hide it); a machine-proposed image has no creating operator; api may INSERT and update ordering, caption, status |
| 51 | `catalog.supplier_terms` | commercial terms of one supplier organization | one row per supplier; `currency ∈ {EUR, USD, CLP}`; `route` closed; discount in [0, 0.99], packing in [0, 0.5]; never deleted; `version` for concurrency |
| 52 | `catalog.fx_rate` | pricing exchange rate, CLP per unit of USD or EUR | append-only; one Banco Central row per day, currency and provider; a manual row names its operator and reason; the newest manual row wins for its day, else the Banco Central row. Display-only rates in `v2/fx_rates.py` stay separate (a fetched mindicador USD/EUR is also written through here) |
| 53 | `catalog.cost_parameter` | costing parameter history | append-only; closed `key`; exactly one of `value_numeric` / `value_json`; `(key, valid_from)` unique; five public keys seeded with no operator |
| 54 | `evidence.document_line` | one line of a quote document, as extracted and cross-checked | `(source_record_id, line_no)` unique; never deleted; inserted never reviewed; only a disputed line may become reviewed, changing only quantity, prices, optional flag and the review fields (trigger `document_line_review_guard`) |

Domain events added: `product.updated`, `product.content_confirmed`, `product.image_added`,
`product.image_updated`, `product.cost_recorded`, `organization.supplier_terms_set`,
`fx_rate.recorded`, `cost_parameter.set`, `source_record.document_line_reviewed`; aggregate kinds
`fx_rate` and `cost_parameter`. An event names a cost parameter's key and never its value, and a
product cost event carries no price.

### 7.6 Mail triage review — built 2026-10-06

The mail triage (ARCHITECTURE.md §8, D2) proposes a reading of each captured email as an
`evidence.assertion` of kind `message_triage`; a person decides what it was worth.

| # | Schema.table | Unique responsibility | Key invariant |
|---|---|---|---|
| 55 | `evidence.triage_review` | a person's verdict on one mail-triage reading | append-only (no UPDATE or DELETE grant); `verdict ∈ {approved, corrected, rejected}`; `corrected` is non-empty exactly for `corrected` and holds only `class`, `stage`, `intent`, `products`; a note is never blank; the latest row per assertion is its current verdict |

Domain event added: `assertion.triage_reviewed` (aggregate `assertion`). A verdict moves no case:
«Aprobar» on a stage suggestion runs `advance-case-stage` afterwards, which records its own event.

Counts by schema: `crm` 21, `comms` 4, `outbound` 9, `evidence` 4,
`catalog` 6, `procurement` 1, `platform` 6 — **51**.

**Deliberately absent.** A delivery-event table (attempt columns plus domain
events suffice); a recontact-override table (immutable recipient columns
suffice); a quote party-snapshot table (a validated snapshot column on the
revision suffices); an address history, provenance or geocoding table
(supersession rows and `origin_source_record_id` suffice); a generic
association or association-label table; a parse-failure table (the archive
holds them); per-aggregate event tables; any mart, mirror or projection table;
a party supertype over organization and person; a combined person / company /
address record; a lead table; a prospect table; a consent table. **A
`commercial_case` table or any second case lifecycle** (the case is
`crm.opportunity`, §3.6); **a case-level audience, subscription or marketing
permission** (§3.6.4); **an inactivity, dormancy or auto-close state** (§3.4 —
inactivity is computed at read time, never stored). Adding any of these
reopens this decision.
