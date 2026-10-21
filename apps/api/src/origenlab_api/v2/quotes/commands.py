"""Quote commands — create a quote on a case, save its draft priced by the engine, approve, void, revise.

Every handler runs inside the one transaction `CommandTransaction` provides: the receipt, the
handler's writes and its events commit together or not at all; a `CommandRefused` rolls all of it
back (receipt included).

**One calculation, on the server** (spec 2026-10-15 O5). `save-quote-draft` takes the whole draft,
resolves it against the catalog, the parameters and the day's exchange rates (`inputs.py`), runs
`quoting.engine.compute`, and stores exactly what it computed: the lines are replaced, the totals
written, the version bumped. The database decides the rest — lines change only on an authored draft
and carry one exchange rate per currency (`crm.quote_line_authored_draft_only`).

**The recipient.** A draft names whom it goes to by a current participant of the case, or by a
person, a contact point or an address picked from the case's emails (`DraftRecipient`); a pick
that is not a participant yet becomes a confirmed `quote_recipient` participant in the save's
transaction (`_recipient`), and an address the CRM lacks becomes an `unattributed` contact point
first, as the CRM authoring commands create one.

**Approval prices what was saved.** `approve-quote-revision` prices the stored lines exactly as
they are (`inputs.engine_input_from_rows`: their FX snapshots, their stored expense lines and
principal — nothing looked up again), so the frozen totals equal the last save. It refuses with
every reason the operator must fix (`check_approvable`, Spanish, joined by « · »); otherwise it
records the costs the operator typed for a catalog product as `negotiated` supplier observations
and points the lines at them (10-05 D6), freezes the totals, the validity and the party snapshot
(10-05 W3a) and supersedes the earlier revision that was current. From then on the database keeps
the revision as it is (`crm.quote_revision_authored_frozen`, `crm.quote_line_authored_draft_only`).
`create-quote-revision` copies the newest issued revision into the next draft (the next letter);
`void-quote-revision` voids a draft or an approved revision, never one already sent.

**Locks.** `create-quote` locks the case row (so a case being closed cannot get a quote in the same
instant) and mints the number under the numbering advisory lock, the last lock it takes; its events
open two new streams. From a client (no case named) the full order is: the client's advisory locks →
its contact point / person rows → the institution's row → the case → the numbering lock
("create-quote for a client", below).
`save-quote-draft`, `approve-quote-revision` and `void-quote-revision` lock the revision row, which
owns the `quote_revision` stream they append to; a save that picks a recipient then holds the
picked contact point `for update` (or the person `for share`), as `create-person` holds an address
it claims. Two saves adding the same new recipient at once: the second is a 409
(`recipient_changed_concurrently`), never a duplicate participant. Approval takes the order «Elegir revisión vigente»
takes — the case (`for share`: it cannot be closed underneath), then the quote, then the revision
and the one it supersedes — and then the products whose stream gets a cost and their suppliers
(`for share`). `create-quote-revision` locks the quote, so two cannot open the same next revision.

**Nothing of Labdelivery origin** enters a quote: `execute` refuses any submitted string that names
it, before a transaction opens. Event payloads carry no cost, price or margin — only the net total
of a saved draft, which is the client-facing figure.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from origenlab_api.quoting.engine import EngineError, EngineResult, compute
from origenlab_api.v2.catalog.commands import _live_party, _submitted_text
from origenlab_api.v2.catalog.keys import LabdeliveryRefused, is_labdelivery, refuse_labdelivery
from origenlab_api.v2.case_commands import REQUESTING_INSTITUTION
from origenlab_api.v2.case_kind import is_tender
from origenlab_api.v2.case_command_repository import (
    V2CaseCommandRepository,
    lock_new_organization_name,
    register_customer_organization,
    same_name_or_domain_organization,
)
from origenlab_api.v2.commands import CommandRefused, normalize_email
from origenlab_api.v2.crm_authoring import _create_contact_point, _handle_link_person_organization
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quotes.draft import ClientInstitution, DraftRecipient, DraftTerms, QuoteClient, QuoteDraft
from origenlab_api.v2.quotes.inputs import (
    FxResolver,
    Resolved,
    draft_from_rows,
    engine_input_from_rows,
    expense_target,
    fx_override_reasons,
    load_parameters,
    resolve_draft,
    result_json,
)
from origenlab_api.v2.quotes.numbering import SANTIAGO, allocate_quote_number, revision_label
from origenlab_api.v2.mail_rules import FREE_MAIL_DOMAINS
from origenlab_api.v2.quotes.reads import client_institution, client_institution_by_domain, client_open_cases
from origenlab_api.v2.quotes.recipients import PERSON_ADDRESS_ORDER, not_a_recipient

#: Revision columns filled from `quote_default_*` text parameters when a quote is created.
_DEFAULT_TEXTS = {
    "delivery_terms": "quote_default_delivery_terms",
    "payment_terms": "quote_default_payment_terms",
    "warranty_terms_es": "quote_default_warranty",
    "delivery_place_es": "quote_default_delivery_place",
    "installation_terms_es": "quote_default_installation",
}
_DEFAULT_VALIDITY_DAYS = 30
_DEFAULT_IVA = Decimal("0.19")
_NUMBER_TAKEN = frozenset({"quote_number_key", "quote_number_minted_key"})

_PRIMARY_PARTICIPANT_SQL = """
select id::text as id
  from crm.opportunity_participant
 where opportunity_id = %s::uuid and is_primary and valid_to is null
 order by (role = 'quote_recipient') desc, valid_from desc, created_at desc, id
 limit 1
"""

_INSERT_LINE_SQL = """
insert into crm.quote_line
    (quote_revision_id, line_no, line_kind, description, product_id, quantity,
     cost_currency, unit_cost, fx_rate, fx_as_of, fx_source, margin_mode, margin_pct,
     unit_price_qc, line_total_qc, is_principal, allocated_to_line_no, item_type, expense_method,
     calc_inputs, supplier_organization_id, cost_observation_id, line_note_es)
values (%(revision_id)s::uuid, %(line_no)s, %(line_kind)s, %(description)s, %(product_id)s::uuid,
        %(quantity)s::numeric, %(cost_currency)s, %(unit_cost)s::numeric, %(fx_rate)s::numeric,
        %(fx_as_of)s::date, %(fx_source)s, %(margin_mode)s, %(margin_pct)s::numeric,
        %(unit_price_qc)s::numeric, %(line_total_qc)s::numeric, %(is_principal)s, %(allocated_to_line_no)s,
        %(item_type)s, %(expense_method)s, %(calc_inputs)s::jsonb, %(supplier_organization_id)s::uuid,
        %(cost_observation_id)s::uuid, %(line_note_es)s)
"""


def _one(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))


def _all(cur: Any) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _write_amounts(cur: Any, sql: str, params: Any) -> None:
    """A write of computed money: a figure its column cannot hold is the operator's 422, not a 500."""
    try:
        cur.execute(sql, params)
    except psycopg.errors.NumericValueOutOfRange as exc:
        raise CommandRefused(422, "amount_too_large", "Un monto es demasiado grande.") from exc


def _text(value: str | None) -> str | None:
    """A blank printed text is no text."""
    return value or None


def santiago_today(now: datetime) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(SANTIAGO).date()


# ------------------------------------------------------------------ create-quote

def _validity_days(params: dict[str, Decimal | str]) -> int:
    value = params.get("quote_default_validity_days")
    if isinstance(value, Decimal) and value == int(value) and 1 <= int(value) <= 365:
        return int(value)
    return _DEFAULT_VALIDITY_DAYS


def _iva(params: dict[str, Decimal | str]) -> Decimal:
    value = params.get("iva_rate")
    return value if isinstance(value, Decimal) and 0 <= value < 1 else _DEFAULT_IVA


# ------------------------------------------------------------------ create-quote for a client
#
# `create-quote` with `client` instead of a case (spec 2026-10-16 §3, plan R1–R3): the client is
# resolved — an institution (the survivor of a merge), a person and their institution, an email
# address and whoever owns it (an address the CRM lacks: the one institution its domain names), or
# a new institution registered as `register-organization` does — and then its open cases are read
# with the rule the new-quote dialog shows them by (`reads.client_open_cases`): exactly one is used;
# several are a 409 `case_choice_needed` carrying them, and the operator sends the chosen one back
# as `opportunity_id`; none (or `new_case`) opens one through
# `V2CaseCommandRepository._open_case_for_client`, with the institution as requesting one. A person
# or an address becomes the quote's recipient (`_recipient_for`, the save's path).
#
# **Locks, in the one order every create-quote takes them** (and no other command inverts):
#
# 1. the client's transaction advisory locks (`_client_lock`) — a new institution's name (the
#    client's own, or the one a person or an address is connected to: `lock_new_organization_name`,
#    the lock `add-case-organization` takes for one too), the address, the person, the
#    institution, in that order. Two `create-quote`s for the same client
#    queue here: the second reads the cases after the first committed, finds the case it opened and
#    quotes on it — two quotes, two numbers, one new case, never a quote without one;
# 2. the client's rows: the contact point `for update`, then its person `for share` — the order
#    `create-person` takes them (an address, then the person it is given to) — read unlocked
#    first to know which locks to take, and refused `client_changed_concurrently` (409, nothing
#    written) when locking shows another owner or institution than that first read;
# 3. the institution's row (`_open_case_for_client` → `_add_case_organization`, a new case only) —
#    after the person, as `link-person-organization` takes them;
# 4. the case (`for update`) — an existing case a connected institution is given to is locked by
#    `_add_case_organization` itself, the case and then the institution, as the command does;
# 5. a person connected to an institution: their affiliation (`link-person-organization`'s own
#    handler — the person `for update`, then the institution's row, both held already);
# 6. last, the global numbering lock (`allocate_quote_number`) — the recipient is made before it.
#
# **Connecting a client to an institution** (`client.institution`, a person, a contact point or
# an address the CRM knows no institution of — `_connect_institution`): the institution, existing
# or new (registered by the rule `add-case-organization` uses, `same_name_or_domain_organization`),
# becomes the client's; the client's cases are still those the dialog showed (the person's or the
# address's own, not the institution's); a case opened gets it as its requesting institution, an
# existing one without a requesting institution gets it through `_add_case_organization`; a person
# gets a confirmed affiliation. An address no person owns is not attributed: no operator command
# gives a bare contact point an institution outside evidence review, so only its case is connected
# (and a new institution takes the address's domain, unless free mail or already some
# institution's, so the next quote to that domain finds it). A client the CRM already knows an
# institution of is refused `client_has_institution` unless it names that same one.

_CLIENT_NOTE = "Cotización creada desde el Cotizador."


def _client_lock(cur: Any, key: str) -> None:
    cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"quote-client:{key}",))


def _supplier_refused() -> CommandRefused:
    return CommandRefused(422, "supplier_exception_required",
                          "OrigenLab le compra a esta institución (es proveedora o fabricante); una cotización "
                          "para ella se crea desde su caso, con la excepción justificada.")


def _client_changed() -> CommandRefused:
    return CommandRefused(409, "client_changed_concurrently",
                          "Los datos de este cliente cambiaron mientras se creaba la cotización; inténtalo de nuevo.")


@dataclass
class _Client:
    organization_id: str | None = None
    organization_name: str | None = None
    person_id: str | None = None
    contact_point_id: str | None = None
    label: str | None = None  # names the new case when there is no institution
    pick: DraftRecipient | None = None  # whom the quote goes to, when the client is a person or an address
    organization_created: bool = False
    email: str | None = None  # the address, when the client is one
    institution_connected: bool = False  # the institution is `client.institution`, connected now
    affiliation_id: str | None = None


def _organization_checked(self: "V2QuoteRepository", cur: Any, organization_id: str) -> tuple[str, str]:
    """The institution (the survivor of a merge), refused when it is archived, of Labdelivery or
    OrigenLab's own, or one of OrigenLab's suppliers (R3). Reads only: no lock."""
    cur.execute("select s.id::text as id, s.name, s.status, coalesce((select array_agg(d.domain_norm) "
                "from crm.organization_domain d where d.organization_id = s.id and d.removed_at is null), '{}') "
                "as domains from crm.organization m "
                "join crm.organization s on s.id = coalesce(m.merged_into_organization_id, m.id) "
                "where m.id = %s::uuid", (organization_id,))
    found = _one(cur)
    if found is None:
        raise CommandRefused(404, "organization_not_found", "no such organization")
    if found["status"] != "active":
        raise CommandRefused(409, "archived_subject", "that institution is archived")
    if is_labdelivery(found["name"]):
        raise CommandRefused(422, "labdelivery_refused", "input of Labdelivery origin is refused")
    for domain in found["domains"]:
        _keep_address_rules(f"x@{domain}")
    if self._is_one_of_our_suppliers(cur, found["id"]):
        raise _supplier_refused()
    return found["id"], found["name"]


def _institution_of(cur: Any, person_id: str | None, point_organization_id: str | None,
                    point_email: str | None = None) -> dict[str, Any]:
    """The person's (or the address's) institution as the dialog shows it — for an address with no
    person and no institution, its domain's (`reads.client_institution`); a supplier's is refused."""
    found = client_institution(cur, person_id=person_id, point_organization_id=point_organization_id,
                               point_email=point_email)
    if found["is_supplier"]:
        raise _supplier_refused()
    return found


_PERSON_SQL = ("select s.id::text as id, s.status, s.display_name from crm.person m "
               "join crm.person s on s.id = coalesce(m.merged_into_person_id, m.id) where m.id = %s::uuid")

_ADDRESS_SQL = ("select c.id::text as id, c.status, c.organization_id::text as organization_id, "
                "s.id::text as person_id, s.status as person_status, s.display_name "
                "from crm.contact_point c left join crm.person m on m.id = c.person_id "
                "left join crm.person s on s.id = coalesce(m.merged_into_person_id, m.id) "
                "where c.kind = 'email' and c.value_norm = %s")


def _person_institution(cur: Any, person_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The address that stands for the person (`recipients.PERSON_ADDRESS_ORDER`) and the institution
    they are a client of: their affiliation's, else that address's own."""
    cur.execute(_PERSON_ADDRESS_SQL, (person_id,))
    address = _one(cur)
    return address, _institution_of(cur, person_id, address["organization_id"] if address else None)


def _person_client(self: "V2QuoteRepository", cur: Any, person_id: str) -> _Client:
    cur.execute(_PERSON_SQL, (person_id,))
    found = _one(cur)
    if found is None:
        raise CommandRefused(404, "person_not_found", "no such person")
    if found["status"] != "active":
        raise CommandRefused(409, "archived_subject", "that person is archived")
    _, institution = _person_institution(cur, found["id"])
    _client_lock(cur, f"person:{found['id']}")
    if institution["organization_id"] is not None:
        _client_lock(cur, f"organization:{institution['organization_id']}")
    cur.execute(_PERSON_SQL + " for share of s", (person_id,))
    locked = _one(cur)
    if locked is None or locked["id"] != found["id"] or locked["status"] != "active":
        raise _client_changed()
    address, now = _person_institution(cur, found["id"])
    if now["organization_id"] != institution["organization_id"]:
        raise _client_changed()
    _keep_address_rules(address["value_norm"] if address else None, found["display_name"])
    client = _Client(person_id=found["id"], label=found["display_name"], pick=DraftRecipient(person_id=found["id"]))
    if institution["organization_id"] is not None:
        client.organization_id, client.organization_name = _organization_checked(
            self, cur, institution["organization_id"])
    return client


def _address_client(self: "V2QuoteRepository", cur: Any, *, contact_point_id: str | None, email: str | None,
                    name: str | None) -> _Client:
    """An address — an email contact point, or an address typed — and whoever owns it; an address
    the CRM lacks, or holds with no person and no institution, is the client of the one
    institution its domain names, if any (`reads.client_institution_by_domain`, the same rule)."""
    if contact_point_id is not None:
        cur.execute("select value_norm from crm.contact_point where id = %s::uuid and kind = 'email'",
                    (contact_point_id,))
        found = _one(cur)
        if found is None:
            raise CommandRefused(404, "contact_point_not_found", "no such email contact point")
        email = found["value_norm"]
    assert email is not None  # noqa: S101 - QuoteClient gives exactly one
    email = normalize_email(email)
    _keep_address_rules(email, name)
    _client_lock(cur, f"email:{email}")
    cur.execute(_ADDRESS_SQL, (email,))
    point = _one(cur)
    if point is None:  # an address the CRM lacks: the quote's recipient creates it
        client = _Client(label=name or email, pick=DraftRecipient(email=email, name=name), email=email)
        by_domain = client_institution_by_domain(cur, email)
        if by_domain is not None:
            client.organization_id, client.organization_name = _organization_checked(self, cur, by_domain["id"])
            _client_lock(cur, f"organization:{client.organization_id}")
        return client
    if point["status"] != "active":
        raise CommandRefused(409, "contact_point_inactive", "this email address is deactivated in the CRM")
    person = point["person_id"] if point["person_status"] == "active" else None
    institution = _institution_of(cur, person, point["organization_id"], email)
    if person is not None:
        _client_lock(cur, f"person:{person}")
    if institution["organization_id"] is not None:
        _client_lock(cur, f"organization:{institution['organization_id']}")
    cur.execute(_ADDRESS_SQL + " for update of c", (email,))
    locked = _one(cur)
    if person is not None:
        cur.execute(_PERSON_SQL + " for share of s", (person,))
    if locked is None or (locked["id"], locked["status"], locked["person_id"], locked["organization_id"]) != (
            point["id"], point["status"], point["person_id"], point["organization_id"]):
        raise _client_changed()
    if _institution_of(cur, person, point["organization_id"], email)["organization_id"] != institution["organization_id"]:
        raise _client_changed()
    _keep_address_rules(email, point["display_name"])
    client = _Client(contact_point_id=point["id"], person_id=person, label=point["display_name"] or name or email,
                     pick=DraftRecipient(contact_point_id=point["id"]), email=email)
    if institution["organization_id"] is not None:
        client.organization_id, client.organization_name = _organization_checked(
            self, cur, institution["organization_id"])
    return client


def _new_organization_client(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, receipt_id: str,
                             new: dict[str, Any], note: str) -> _Client:
    """A new institution (plan R2). Not a second one: the one active institution of exactly that
    name, else the one its email's domain names (`same_name_or_domain_organization`), is used
    instead; two or more of that name are refused `organization_name_ambiguous`, with them. A new
    one is registered through `register-organization`'s own path: classification `customer`, the
    email's domain (a free-mail one excepted). Its email, if given, is the quote's recipient."""
    name, email = new["name"], new.get("email")
    if email is not None:
        _keep_address_rules(email)
    lock_new_organization_name(cur, name)
    if email is not None:
        _client_lock(cur, f"email:{email}")
    found = same_name_or_domain_organization(cur, name, email)
    existing = found["id"] if found is not None else None
    pick = DraftRecipient(email=email) if email is not None else None
    if existing is not None:
        organization_id, organization_name = _organization_checked(self, cur, existing)
        _client_lock(cur, f"organization:{organization_id}")
    if email is not None:  # the address's row before any institution's (`create-person`'s order)
        cur.execute("select 1 from crm.contact_point where kind = 'email' and value_norm = %s for update", (email,))
    if existing is not None:
        return _Client(organization_id=organization_id, organization_name=organization_name, pick=pick)
    made = register_customer_organization(self, cur, operator, receipt_id, name=name,
                                          kind=new.get("kind") or "company",
                                          domain=email.rpartition("@")[2] if email is not None else None, note=note)
    return _Client(organization_id=made, organization_name=name, pick=pick, organization_created=True)


def _client_has_institution(client: _Client) -> CommandRefused:
    return CommandRefused(409, "client_has_institution",
                          f"Este cliente ya es de «{client.organization_name}»; la cotización va a esa institución.",
                          details={"organization_id": client.organization_id,
                                   "organization_name": client.organization_name})


def _domain_for_new_institution(cur: Any, client: _Client) -> str | None:
    """The domain a new institution an address no person owns is connected to takes: the
    address's, unless free mail (`register_customer_organization` drops it) or already on an
    institution — never a second claim on a domain."""
    if client.person_id is not None or client.email is None:
        return None
    domain = client.email.rpartition("@")[2]
    cur.execute("select 1 from crm.organization_domain where domain_norm = %s and removed_at is null limit 1",
                (domain,))
    return None if _one(cur) is not None else domain


def _connect_institution(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, receipt_id: str,
                         client: _Client, institution: ClientInstitution, note: str) -> None:
    """Make `institution` the client's (a person, a contact point or an address) — read and
    refused as any client institution is (`_organization_checked`: archived, Labdelivery,
    OrigenLab's own, a supplier); a new one is the institution of that name or registered now.
    The case and the affiliation are written once the case is known (`_case_for_client`)."""
    if institution.organization_id is not None:
        organization_id, name = _organization_checked(self, cur, str(institution.organization_id))
    else:
        assert institution.new_organization is not None  # noqa: S101 - ClientInstitution gives exactly one
        new = institution.new_organization
        _keep_address_rules(None, new.name)
        found = same_name_or_domain_organization(cur, new.name, None)
        organization_id, name = (_organization_checked(self, cur, found["id"]) if found is not None
                                 else (None, new.name))
    if client.organization_id is not None:
        if client.organization_id == organization_id:
            return  # already theirs: nothing to connect
        raise _client_has_institution(client)
    if organization_id is None:
        assert institution.new_organization is not None  # noqa: S101 - only a new one is not found
        organization_id = register_customer_organization(
            self, cur, operator, receipt_id, name=name, kind=institution.new_organization.kind,
            domain=_domain_for_new_institution(cur, client), note=note)
        client.organization_created = True
    else:
        _client_lock(cur, f"organization:{organization_id}")
    client.organization_id, client.organization_name = organization_id, name
    client.institution_connected = True


def _resolve_client(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, receipt_id: str,
                    client: QuoteClient, note: str) -> _Client:
    if client.organization_id is not None:
        organization_id, name = _organization_checked(self, cur, str(client.organization_id))
        _client_lock(cur, f"organization:{organization_id}")
        return _Client(organization_id=organization_id, organization_name=name)
    if client.new_organization is not None:
        return _new_organization_client(self, cur, operator, receipt_id, client.new_organization.model_dump(), note)
    if client.institution is not None and client.institution.new_organization is not None:
        lock_new_organization_name(cur, client.institution.new_organization.name)  # the first of the client's locks
    if client.person_id is not None:
        resolved = _person_client(self, cur, str(client.person_id))
    else:
        resolved = _address_client(self, cur, contact_point_id=str(client.contact_point_id)
                                   if client.contact_point_id else None, email=client.email, name=client.name)
    if client.institution is not None:
        _connect_institution(self, cur, operator, receipt_id, resolved, client.institution, note)
    return resolved


def _give_case_the_institution(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, receipt_id: str,
                               case_id: str, client: _Client, note: str) -> None:
    """An existing case of a client just connected to an institution: the institution becomes its
    requesting one through `_add_case_organization` (the supplier refusal and the agreement check
    run as by hand), unless the case already names one — that reading is the operator's, kept."""
    if self._current_requesting_institution(cur, case_id) is not None:
        return
    cur.execute("select version from crm.opportunity where id = %s::uuid", (case_id,))
    case = _one(cur)
    cur.execute("select version from crm.organization where id = %s::uuid", (client.organization_id,))
    organization = _one(cur)
    assert case is not None and organization is not None  # noqa: S101 - both read just before
    self._add_case_organization(cur, operator, {
        "opportunity_id": case_id, "opportunity_version": int(case["version"]),
        "organization_id": client.organization_id, "organization_version": int(organization["version"]),
        "role": REQUESTING_INSTITUTION, "supplier_exception_reason": None, "note": note}, receipt_id)


def _affiliate(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, receipt_id: str,
               client: _Client, note: str) -> None:
    """A person client connected to an institution: a confirmed affiliation, through
    `link-person-organization`'s own handler (from today, Santiago)."""
    cur.execute("select version from crm.person where id = %s::uuid", (client.person_id,))
    person = _one(cur)
    assert person is not None  # noqa: S101 - resolved and share-locked by `_resolve_client`
    linked = _handle_link_person_organization(self, cur, operator, {
        "person_id": client.person_id, "expected_version": int(person["version"]),
        "organization_id": client.organization_id, "role_title": None, "unit_label": None,
        "valid_from": santiago_today(self._clock()).isoformat(), "note": note}, receipt_id)
    client.affiliation_id = linked["affiliation_id"]


def _case_choice_needed(cases: list[dict[str, Any]]) -> CommandRefused:
    return CommandRefused(
        409, "case_choice_needed", "Este cliente tiene varios casos abiertos: elige uno, o abre uno nuevo.",
        details={"candidates": [
            {"id": c["id"], "title": c["title"], "stage": c["stage"],
             "requested_at": c["requested_at"].isoformat() if c["requested_at"] is not None else None}
            for c in cases]})


def _case_for_client(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                     receipt_id: str) -> tuple[str, _Client, bool]:
    """The case a client's new quote goes on, opened when needed: (case id, the client, opened)."""
    note = fields.get("note") or _CLIENT_NOTE
    client = _resolve_client(self, cur, operator, receipt_id, QuoteClient.model_validate(fields["client"]), note)
    # A client just connected to an institution has the cases the dialog showed: their own.
    cases = client_open_cases(cur, organization_id=None if client.institution_connected else client.organization_id,
                              person_id=client.person_id, contact_point_id=client.contact_point_id)
    case_id, opened = _chosen_case(self, cur, operator, fields, receipt_id, client, cases, note)
    if client.institution_connected:
        if not opened:
            _give_case_the_institution(self, cur, operator, receipt_id, case_id, client, note)
        if client.person_id is not None:
            _affiliate(self, cur, operator, receipt_id, client, note)
    return case_id, client, opened


def _chosen_case(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                 receipt_id: str, client: _Client, cases: list[dict[str, Any]], note: str) -> tuple[str, bool]:
    """The client's open case the quote goes on (the one, or the one chosen), else one opened."""
    chosen = fields.get("opportunity_id")
    if chosen is not None:
        if chosen not in {c["id"] for c in cases}:
            cur.execute("select closed_at from crm.opportunity where id = %s::uuid", (chosen,))
            found = _one(cur)
            if found is None:
                raise CommandRefused(404, "case_not_found", "no such commercial case")
            if found["closed_at"] is not None:
                raise CommandRefused(409, "case_closed", "this case is closed; a closed case gets no new quote")
            raise CommandRefused(422, "case_not_of_client", "Ese caso no es de este cliente.")
        return chosen, False
    if not fields.get("new_case"):
        if len(cases) == 1:
            return cases[0]["id"], False
        if len(cases) > 1:
            raise _case_choice_needed(cases)
    named = client.organization_name or client.label or "Cliente"
    opened = self._open_case_for_client(cur, operator, receipt_id, title=f"{named} — cotización"[:200],
                                        organization_id=client.organization_id, note=note)
    return opened["opportunity_id"], True


def _handle_create_quote(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                         fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    client: _Client | None = None
    case_created = False
    if fields.get("client") is not None:
        case_id, client, case_created = _case_for_client(self, cur, operator, fields, receipt_id)
    else:
        case_id = fields["opportunity_id"]
    cur.execute("select id::text as id, closed_at from crm.opportunity where id = %s::uuid for update", (case_id,))
    case = _one(cur)
    if case is None:
        raise CommandRefused(404, "case_not_found", "no such commercial case")
    if case["closed_at"] is not None:
        raise CommandRefused(409, "case_closed", "this case is closed; a closed case gets no new quote")

    # The recipient first: its row locks come before the numbering lock, the last one taken.
    revision_id = str(uuid.uuid4())  # the recipient's `participant.added` names the revision it is for
    if client is not None and client.pick is not None:
        recipient: str | None = _recipient_for(self, cur, operator, receipt_id, client.pick, None, case_id=case_id,
                                               revision_id=revision_id, today=santiago_today(self._clock()),
                                               case_closed=False)
    else:
        cur.execute(_PRIMARY_PARTICIPANT_SQL, (case_id,))
        primary = _one(cur)
        recipient = primary["id"] if primary else None

    number = allocate_quote_number(cur, self._clock())
    try:
        cur.execute(
            """
            insert into crm.quote (opportunity_id, quote_number, number_origin, created_by_operator_id)
            values (%s::uuid, %s, 'minted', %s::uuid)
            returning id::text as id
            """,
            (case_id, number, operator.operator_id),
        )
    except psycopg.errors.UniqueViolation as exc:  # a number typed elsewhere between mint and insert
        if exc.diag.constraint_name in _NUMBER_TAKEN:
            raise CommandRefused(409, "quote_number_taken",
                                 f"the number {number} was just taken; try again") from exc
        raise
    quote = _one(cur)
    assert quote is not None  # noqa: S101 - `returning` on a successful insert

    params = load_parameters(cur)
    defaults = {column: _text(v.strip()) if isinstance(v := params.get(key), str) else None
                for column, key in _DEFAULT_TEXTS.items()}
    cur.execute(
        """
        insert into crm.quote_revision
            (id, quote_id, revision_no, status, origin, quote_currency, price_decimals, tax_rate,
             delivery_terms, payment_terms, warranty_terms_es, delivery_place_es, installation_terms_es,
             validity_days, recipient_participant_id, created_by_operator_id)
        values (%(id)s::uuid, %(quote_id)s::uuid, 1, 'draft', 'authored', 'CLP', 0, %(tax_rate)s::numeric,
                %(delivery_terms)s, %(payment_terms)s, %(warranty_terms_es)s, %(delivery_place_es)s,
                %(installation_terms_es)s, %(validity_days)s, %(recipient)s::uuid, %(operator)s::uuid)
        returning id::text as id, version
        """,
        {"id": revision_id, "quote_id": quote["id"], "tax_rate": _iva(params), **defaults,
         "validity_days": _validity_days(params), "recipient": recipient, "operator": operator.operator_id},
    )
    revision = _one(cur)
    assert revision is not None  # noqa: S101 - `returning` on a successful insert

    payload: dict[str, Any] = {"quote_number": number, "opportunity_id": case_id}
    if fields.get("note"):
        payload["note"] = fields["note"]
    self._append_event(cur, aggregate_kind="quote", aggregate_id=quote["id"], event_type="quote.created",
                       payload=payload, operator=operator, receipt_id=receipt_id)
    self._append_event(cur, aggregate_kind="quote_revision", aggregate_id=revision["id"],
                       event_type="quote_revision.opened", payload={"quote_id": quote["id"], "revision_no": 1},
                       operator=operator, receipt_id=receipt_id)
    out = {"ok": True, "quote_id": quote["id"], "quote_number": number, "revision_id": revision["id"],
           "version": revision["version"], "opportunity_id": case_id}
    if client is not None:
        out.update({"case_created": case_created, "organization_id": client.organization_id,
                    "organization_name": client.organization_name,
                    "organization_created": client.organization_created, "recipient_participant_id": recipient,
                    "institution_connected": client.institution_connected, "affiliation_id": client.affiliation_id})
    return out


# ------------------------------------------------------------------ save-quote-draft

def _snapshot(currency: str | None, amount: Decimal | None, resolved: Resolved, today: date) -> dict[str, Any]:
    """The five cost-snapshot columns: all of them, or none when there is no cost."""
    if currency is None or amount is None:
        return {"cost_currency": None, "unit_cost": None, "fx_rate": None, "fx_as_of": None, "fx_source": None}
    if currency == "CLP":
        rate, as_of, source = Decimal(1), today, "manual"
    else:
        fx = resolved.fx[currency]
        rate, as_of, source = fx.clp_per_unit, fx.as_of, fx.source
    return {"cost_currency": currency, "unit_cost": amount, "fx_rate": rate, "fx_as_of": as_of, "fx_source": source}


def _insert_lines(cur: Any, revision_id: str, resolved: Resolved, result: Any, today: date) -> None:
    computed = {r.line_no: r for r in result.lines}
    blank = {"product_id": None, "item_type": None, "expense_method": None, "calc_inputs": None,
             "supplier_organization_id": None, "cost_observation_id": None, "line_note_es": None,
             "allocated_to_line_no": None}
    rows = []
    for item in resolved.items:
        r = computed[item.line_no]
        rows.append({
            **blank, "revision_id": revision_id, "line_no": item.line_no, "line_kind": "item",
            "description": item.draft.description, "product_id": item.product_id, "quantity": item.draft.quantity,
            **_snapshot(item.cost_currency, item.unit_cost, resolved, today),
            "margin_mode": item.draft.margin_mode,
            "margin_pct": None if item.draft.margin_mode == "none" else item.margin_pct,
            "unit_price_qc": r.unit_price, "line_total_qc": r.line_total, "is_principal": r.is_principal,
            "item_type": item.item_type, "supplier_organization_id": item.supplier_organization_id,
            "cost_observation_id": item.cost_observation_id, "line_note_es": item.draft.line_note_es,
        })
    target = expense_target(resolved, result)
    for expense in resolved.expenses:
        rows.append({
            **blank, "revision_id": revision_id, "line_no": expense.line_no, "line_kind": "logistics",
            "description": expense.label, "quantity": Decimal(1),
            **_snapshot(expense.currency, expense.amount, resolved, today),
            "margin_mode": "none", "margin_pct": None, "unit_price_qc": Decimal(0), "line_total_qc": Decimal(0),
            "is_principal": False, "allocated_to_line_no": target, "expense_method": expense.method,
            "calc_inputs": json.dumps(expense.calc_inputs) if expense.calc_inputs is not None else None,
        })
    try:
        for row in rows:
            _write_amounts(cur, _INSERT_LINE_SQL, row)
    except psycopg.errors.ForeignKeyViolation as exc:
        raise CommandRefused(422, "reference_not_found",
                             "a line names a supplier or cost observation that does not exist") from exc


def _locked_draft(cur: Any, revision_id: str, expected_version: int) -> dict[str, Any]:
    cur.execute(
        """
        select r.id::text as id, r.quote_id::text as quote_id, r.status, r.origin, r.version, r.tax_rate,
               q.opportunity_id::text as opportunity_id
          from crm.quote_revision r
          join crm.quote q on q.id = r.quote_id
         where r.id = %s::uuid
           for update of r
        """,
        (revision_id,),
    )
    revision = _one(cur)
    if revision is None:
        raise CommandRefused(404, "revision_not_found", "no such quote revision")
    if revision["origin"] != "authored" or revision["status"] != "draft":
        raise CommandRefused(409, "not_a_draft", "only a draft revision is edited")
    if revision["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "the draft was modified since you loaded it")
    return revision


_ADDRESS_REFUSALS = {"labdelivery_refused": "input of Labdelivery origin is refused",
                     "recipient_own_address": "Esa dirección es de OrigenLab, no del cliente."}


def _keep_address_rules(email: str | None, name: str | None = None) -> None:
    """Every pick, however it names the recipient, is held to the address it prints (`recipients`)."""
    reason = not_a_recipient(email, name)
    if reason is not None:
        raise CommandRefused(422, reason, _ADDRESS_REFUSALS[reason])


def _unknown_recipient(what: str) -> CommandRefused:
    return CommandRefused(422, "recipient_unknown", f"no such {what} to send the quote to")


def _recipient_raced() -> CommandRefused:
    return CommandRefused(409, "recipient_changed_concurrently",
                          "the case's people changed while saving; save again")


def _picked_address(cur: Any, *, contact_point_id: str | None, email: str | None,
                    name: str | None) -> tuple[str | None, str | None, str]:
    """The email contact point picked — by id, or by its address — as (id or None when the CRM
    does not hold the address yet, the person it belongs to, the address), locked `for update` as
    `create-person` locks an address it claims. The address rules hold on both forms; a
    deactivated address is refused, as `create-person` refuses it."""
    if contact_point_id is not None:
        cur.execute("select c.id::text as id, c.person_id::text as person_id, c.status, c.value_norm, "
                    "pe.display_name from crm.contact_point c left join crm.person pe on pe.id = c.person_id "
                    "where c.id = %s::uuid and c.kind = 'email' for update of c", (contact_point_id,))
        found = _one(cur)
        if found is None:
            raise _unknown_recipient("email contact point")
        _keep_address_rules(found["value_norm"], found["display_name"])
    else:
        assert email is not None  # noqa: S101 - DraftRecipient gives exactly one
        email = normalize_email(email)
        _keep_address_rules(email, name)
        cur.execute("select id::text as id, person_id::text as person_id, status, value_norm from crm.contact_point "
                    "where kind = 'email' and value_norm = %s for update", (email,))
        found = _one(cur)
        if found is None:
            return None, None, email
    if found["status"] != "active":
        raise CommandRefused(409, "contact_point_inactive", "this email address is deactivated in the CRM")
    return found["id"], found["person_id"], found["value_norm"]


# The case's current participant for the pick: the same address (a contact-point pick), or the
# same person (a person pick); a quote recipient first.
_CURRENT_PARTICIPANT_SQL = """
select id::text as id
  from crm.opportunity_participant
 where opportunity_id = %(case)s::uuid and valid_to is null
   and (contact_point_id = %(contact_point)s::uuid or person_id = %(person)s::uuid)
 order by (role = 'quote_recipient') desc, is_primary desc, valid_from, created_at, id
 limit 1
"""

# A person's address as a participant of theirs would print it (`_RECIPIENT_SQL`), in the order
# `recipients.PERSON_ADDRESS_ORDER` gives (an active one, one a quote may go to, the oldest) — the
# address the new-quote dialog shows for them; with the institution the address names, which a
# person with no affiliation is a client of.
_PERSON_ADDRESS_SQL = f"""
select c.value_norm, c.organization_id::text as organization_id from crm.contact_point c
 where c.kind = 'email' and c.person_id = %s::uuid
 order by {PERSON_ADDRESS_ORDER}
 limit 1
"""


def _recipient(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, receipt_id: str,
               draft: QuoteDraft, revision: dict[str, Any], today: date, *, case_closed: bool) -> str | None:
    """The participant the draft names as its recipient — made one first when it is not yet.

    Every form is held to the address rules (`recipients.not_a_recipient`) on the address the
    quote would print: no OrigenLab address (subdomains included), nothing of Labdelivery.

    A participant id is checked: a current participant of this case. A person (the survivor of a
    merge), or an address picked from the case's emails (a contact point, or an address the CRM
    does not hold yet), is looked for among the case's current participants — the same person, or
    the same address; a quote recipient first — and used when found. Otherwise it becomes one,
    unless the case is closed (`case_closed`, before anything is written): a `quote_recipient`
    participant confirmed by this operator from today (Santiago), primary when the case has no
    primary recipient yet, with a `participant.added` event.

    - A person pick: the person's row.
    - An address pick: the address's row (an address the CRM lacks becomes an `unattributed`
      contact point first, through `crm_authoring._create_contact_point` and its
      `contact_point.created` event — picking it says nothing about who owns it). When a person
      owns the address and is not yet a quote recipient of the case, the row names that person
      too, so the person's card counts it; when they already are one (under another address), the
      address stays a row of its own — the same person cannot be the same role twice at once.
    - For an address no person owns, the name it was picked by is kept as the participant's
      `note`; approval prints it (`_RECIPIENT_SQL`).
    """
    pick = draft.recipient
    picked_participant = pick.participant_id if pick is not None else draft.recipient_participant_id
    if pick is None and picked_participant is None:
        return None
    return _recipient_for(self, cur, operator, receipt_id, pick, picked_participant,
                          case_id=revision["opportunity_id"], revision_id=revision["id"], today=today,
                          case_closed=case_closed)


def _recipient_for(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, receipt_id: str,
                   pick: DraftRecipient | None, picked_participant: Any, *, case_id: str, revision_id: str,
                   today: date, case_closed: bool) -> str:
    """`_recipient` for one pick — also how `create-quote` makes a client the quote's recipient."""
    if picked_participant is not None:
        cur.execute(_RECIPIENT_SQL, (str(picked_participant), case_id))
        found = _one(cur)
        if found is None:
            raise CommandRefused(422, "recipient_not_on_case",
                                 "the recipient is not a current participant of this case")
        _keep_address_rules(found["email"], found["name"])
        return str(picked_participant)

    assert pick is not None  # noqa: S101 - one of the two forms is set
    person: str | None = None  # the person picked
    contact_point: str | None = None  # or the address picked (None: not in the CRM yet) …
    address_person: str | None = None  # … and the person it belongs to, if any
    address: str | None = None
    if pick.person_id is not None:
        cur.execute("select s.id::text as id, s.status, s.display_name from crm.person m "
                    "join crm.person s on s.id = coalesce(m.merged_into_person_id, m.id) "
                    "where m.id = %s::uuid for share of s", (str(pick.person_id),))
        found = _one(cur)
        if found is None:
            raise _unknown_recipient("person")
        if found["status"] != "active":
            raise CommandRefused(409, "archived_subject", "that person is archived")
        person = found["id"]
        cur.execute(_PERSON_ADDRESS_SQL, (person,))
        own = _one(cur)
        _keep_address_rules(own["value_norm"] if own else None, found["display_name"])
    else:
        contact_point, address_person, address = _picked_address(
            cur, contact_point_id=str(pick.contact_point_id) if pick.contact_point_id else None,
            email=pick.email, name=pick.name)

    if person is not None or contact_point is not None:
        cur.execute(_CURRENT_PARTICIPANT_SQL, {"case": case_id, "contact_point": contact_point, "person": person})
        existing = _one(cur)
        if existing is not None:
            return existing["id"]
    if case_closed:
        raise CommandRefused(409, "case_closed", "this case is closed; it gets no new recipient")

    if pick.email is not None and contact_point is None:
        try:
            contact_point = _create_contact_point(self, cur, operator, receipt_id, kind="email", value_norm=address,
                                                  value_display=address, person_id=None, organization_id=None,
                                                  usage="unattributed")
        except psycopg.errors.UniqueViolation as exc:  # the same address created elsewhere just now
            raise _recipient_raced() from exc
    if address_person is not None:
        cur.execute("select exists (select 1 from crm.opportunity_participant where opportunity_id = %s::uuid "
                    "and role = 'quote_recipient' and person_id = %s::uuid and valid_to is null) as already",
                    (case_id, address_person))
        if not _one(cur)["already"]:  # type: ignore[index]
            person = address_person

    cur.execute("select exists (select 1 from crm.opportunity_participant where opportunity_id = %s::uuid "
                "and role = 'quote_recipient' and is_primary and valid_to is null) as taken", (case_id,))
    primary = not _one(cur)["taken"]  # type: ignore[index]
    note = pick.name if contact_point is not None and address_person is None else None
    try:
        cur.execute(
            """
            insert into crm.opportunity_participant
                (opportunity_id, person_id, contact_point_id, role, is_primary, valid_from, confirmation,
                 confirmed_by_operator_id, note)
            values (%s::uuid, %s::uuid, %s::uuid, 'quote_recipient', %s, %s, 'confirmed', %s::uuid, %s)
            returning id::text as id
            """,
            (case_id, person, contact_point, primary, today, operator.operator_id, note),
        )
    except (psycopg.errors.UniqueViolation, psycopg.errors.ExclusionViolation) as exc:
        raise _recipient_raced() from exc
    participant = _one(cur)
    assert participant is not None  # noqa: S101 - `returning` on a successful insert
    self._append_event(cur, aggregate_kind="opportunity_participant", aggregate_id=participant["id"],
                       event_type="participant.added",
                       payload={"opportunity_id": case_id, "role": "quote_recipient", "person_id": person,
                                "contact_point_id": contact_point, "is_primary": primary,
                                "quote_revision_id": revision_id},
                       operator=operator, receipt_id=receipt_id)
    return participant["id"]


def _lock_case_and_quote(cur: Any, revision_id: str, *, quote_lock: str) -> dict[str, Any]:
    """The revision's case `for share` (it cannot be closed underneath), then its quote — the order
    every quote-wide command takes, before the revision itself is locked. The revision's quote and
    case never change, so reading them unlocked first is safe."""
    cur.execute("select r.quote_id::text as quote_id, q.opportunity_id::text as case_id from crm.quote_revision r "
                "join crm.quote q on q.id = r.quote_id where r.id = %s::uuid", (revision_id,))
    owner = _one(cur)
    if owner is None:
        raise CommandRefused(404, "revision_not_found", "no such quote revision")
    cur.execute("select closed_at, organization_id::text as organization_id, kind from crm.opportunity "
                "where id = %s::uuid for share", (owner["case_id"],))
    case = _one(cur)
    assert case is not None  # noqa: S101 - crm.quote.opportunity_id is a foreign key
    cur.execute(f"select 1 from crm.quote where id = %s::uuid for {quote_lock}",  # noqa: S608 - a constant
                (owner["quote_id"],))
    return {**owner, **case}


def _handle_save_quote_draft(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                             fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    revision_id, expected_version = fields["revision_id"], fields["expected_version"]
    draft = QuoteDraft.model_validate(fields["draft"])
    case = _lock_case_and_quote(cur, revision_id, quote_lock="share")
    revision = _locked_draft(cur, revision_id, expected_version)
    today = santiago_today(self._clock())
    recipient = _recipient(self, cur, operator, receipt_id, draft, revision, today,
                           case_closed=case["closed_at"] is not None)
    # The revision's own tax rate (set at creation) prices its IVA, so tax_total agrees with tax_rate.
    params = {**load_parameters(cur), "iva_rate": Decimal(revision["tax_rate"])}
    try:
        resolved = resolve_draft(cur, draft, params, self._fx_resolver, today)
        result = compute(resolved.engine_input)
    except EngineError as exc:
        raise CommandRefused(422, exc.code, f"the draft cannot be priced ({exc.code})") from exc

    cur.execute("delete from crm.quote_line where quote_revision_id = %s::uuid", (revision_id,))
    _insert_lines(cur, revision_id, resolved, result, today)

    terms = draft.terms
    _write_amounts(
        cur,
        """
        update crm.quote_revision
           set delivery_terms = %(delivery_terms)s, payment_terms = %(payment_terms)s,
               warranty_terms_es = %(warranty_terms_es)s, delivery_place_es = %(delivery_place_es)s,
               installation_terms_es = %(installation_terms_es)s, client_notes_es = %(client_notes_es)s,
               terms_extra = %(terms_extra)s::jsonb, validity_days = %(validity_days)s,
               expense_method = %(expense_method)s, expense_allocation_mode = %(allocation_mode)s,
               principal_rule = %(principal_rule)s, fx_override_reasons = %(fx_override_reasons)s::jsonb,
               recipient_participant_id = coalesce(%(recipient)s::uuid, recipient_participant_id),
               client_details = %(client_details)s::jsonb,
               subtotal = %(net)s, discount_total = 0, tax_base = %(net)s, tax_total = %(iva)s,
               grand_total = %(total)s, totals_computed_at = now(), version = version + 1, updated_at = now()
         where id = %(id)s::uuid and version = %(version)s
        returning version, recipient_participant_id::text as recipient_participant_id
        """,
        {"delivery_terms": _text(terms.delivery_terms), "payment_terms": _text(terms.payment_terms),
         "warranty_terms_es": _text(terms.warranty_terms_es), "delivery_place_es": _text(terms.delivery_place_es),
         "installation_terms_es": _text(terms.installation_terms_es),
         "client_notes_es": _text(terms.client_notes_es),
         "terms_extra": json.dumps([t.model_dump() for t in terms.terms_extra], ensure_ascii=False),
         "validity_days": terms.validity_days, "expense_method": draft.expense_method,
         "allocation_mode": draft.allocation_mode, "principal_rule": draft.principal_rule,
         "fx_override_reasons": json.dumps(fx_override_reasons(draft), ensure_ascii=False),
         "recipient": recipient, "net": result.net, "iva": result.iva, "total": result.total,
         "client_details": (json.dumps(stored, ensure_ascii=False)
                            if draft.client and (stored := draft.client.stored()) else None),
         "id": revision_id, "version": expected_version},
    )
    updated = _one(cur)
    if updated is None:  # pragma: no cover - the row is locked; kept as the compare-and-set's other half
        raise CommandRefused(409, "stale_version", "concurrent modification")
    self._append_event(cur, aggregate_kind="quote_revision", aggregate_id=revision_id,
                       event_type="quote_revision.draft_saved",
                       payload={"items": len(resolved.items), "net": str(result.net)},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "revision_id": revision_id, "version": updated["version"],
            "recipient_participant_id": updated["recipient_participant_id"],
            "computed": result_json(resolved, result)}


# ------------------------------------------------------------------ approve-quote-revision

#: Why a draft cannot be approved yet, in the words the dashboard shows beside the button.
APPROVAL_REFUSALS = {
    "no_items": "Agrega al menos un producto.",
    "unpriced_line": "La línea {line_no} no tiene precio (falta costo o precio).",
    "principal_missing": "Hay gastos de importación pero ningún equipo los lleva; marca «Lleva gastos» en uno.",
    "fx_missing": "Falta el tipo de cambio de {currency}.",
    "no_institution": "El caso no tiene institución.",
    "no_recipient": "Elige a quién va dirigida.",
    "placeholder_left": "Completa «{field}»: aún tiene un texto entre corchetes.",
}
_PLACEHOLDER = re.compile(r"\[[^\]]+\]")

#: The printed conditions, by revision column, with the name the operator knows each by.
PRINTED_TERMS = (
    ("delivery_terms", "Plazo de entrega"),
    ("payment_terms", "Forma de pago"),
    ("warranty_terms_es", "Garantía"),
    ("delivery_place_es", "Lugar de entrega"),
    ("installation_terms_es", "Instalación"),
    ("client_notes_es", "Nota al cliente"),
)


@dataclass(frozen=True)
class ApprovalFacts:
    """What approval checks, already read and priced: `check_approvable` decides on it alone."""
    item_line_nos: tuple[int, ...]
    unpriced_line_nos: tuple[int, ...]
    expense_count: int
    principal_line_no: int | None
    missing_fx: tuple[str, ...]
    has_institution: bool
    has_recipient: bool
    printed: tuple[tuple[str, str | None], ...]  # (the condition's name, its printed text)
    #: A tender's offer goes to no one in particular: its print names the bidder and the buying
    #: institution, and it is uploaded to the portal, never mailed (spec 2026-10-19).
    recipient_required: bool = True


def check_approvable(facts: ApprovalFacts) -> list[tuple[str, str]]:
    """Every reason the revision cannot be approved, in the order the operator should fix them.

    A principal is required whenever any expense line exists, a manual one included: without it
    the engine allocates the expenses to no line and they silently drop out of the price.
    """
    out: list[tuple[str, str]] = []
    if not facts.item_line_nos:
        out.append(("no_items", APPROVAL_REFUSALS["no_items"]))
    out += [("unpriced_line", APPROVAL_REFUSALS["unpriced_line"].format(line_no=n)) for n in facts.unpriced_line_nos]
    if facts.expense_count and facts.principal_line_no is None:
        out.append(("principal_missing", APPROVAL_REFUSALS["principal_missing"]))
    out += [("fx_missing", APPROVAL_REFUSALS["fx_missing"].format(currency=c)) for c in facts.missing_fx]
    if not facts.has_institution:
        out.append(("no_institution", APPROVAL_REFUSALS["no_institution"]))
    if facts.recipient_required and not facts.has_recipient:
        out.append(("no_recipient", APPROVAL_REFUSALS["no_recipient"]))
    out += [("placeholder_left", APPROVAL_REFUSALS["placeholder_left"].format(field=name))
            for name, text in facts.printed if text and _PLACEHOLDER.search(text)]
    return out


def printed_texts(terms: DraftTerms, lines: list[dict[str, Any]] = ()) -> tuple[tuple[str, str | None], ...]:
    """Every text the client will read, named: the conditions (an extra one's name is printed
    too) and the note under each item line. Product descriptions are not checked: a bracket in a
    model name («Pipeta [10-100 µL]») is the product's, not a placeholder."""
    fixed = tuple((name, getattr(terms, column)) for column, name in PRINTED_TERMS)
    extra = tuple((t.label_es, f"{t.label_es} {t.text_es}") for t in terms.terms_extra)
    notes = tuple((f"Nota de la línea {r['line_no']}", r["line_note_es"])
                  for r in lines if r["line_kind"] == "item" and r["line_note_es"])
    return fixed + extra + notes


_APPROVAL_REVISION_SQL = """
select r.*, q.quote_number, q.opportunity_id::text as case_id
  from crm.quote_revision r join crm.quote q on q.id = r.quote_id
 where r.id = %s::uuid
"""

# The case's institution (the survivor of a merge) and its first RUT.
_INSTITUTION_SQL = """
select o.id::text as id, o.name,
       (select e.value_norm from crm.external_identifier e
         where e.organization_id = o.id and e.scheme = 'rut' order by e.created_at, e.id limit 1) as rut
  from crm.organization m
  join crm.organization o on o.id = coalesce(m.merged_into_organization_id, m.id)
 where m.id = %s::uuid
"""

# The recipient, while still a current participant of the case: the participant's own email
# address, else the person's (an active one first); the name of the participant's person, else of
# the address's person, else — for an address with no person — the name it was picked by (the
# participant's note, `_recipient`), else the address as displayed.
_RECIPIENT_SQL = f"""
select p.id::text as participant_id,
       coalesce(pe.display_name, cpe.display_name, case when p.person_id is null then p.note end,
                cp.value_display) as name,
       cp.value_norm as email
  from crm.opportunity_participant p
  left join crm.person pe on pe.id = p.person_id
  left join lateral (
    select c.value_norm, c.value_display, c.person_id from crm.contact_point c
     where c.kind = 'email'
       and (c.id = p.contact_point_id or (p.contact_point_id is null and c.person_id = p.person_id))
     order by {PERSON_ADDRESS_ORDER}
     limit 1) cp on true
  left join crm.person cpe on cpe.id = cp.person_id
 where p.id = %s::uuid and p.opportunity_id = %s::uuid and p.valid_to is null
"""


def _label(quote_number: str, revision_no: int) -> str:
    try:
        return revision_label(quote_number, revision_no)
    except ValueError as exc:
        raise CommandRefused(409, "revision_letters_exhausted",
                             f"quote {quote_number} has no letter left for revision {revision_no}") from exc


def _approval_facts(lines: list[dict[str, Any]], result: EngineResult, institution: Any, recipient: Any,
                    terms: DraftTerms, *, recipient_required: bool = True) -> ApprovalFacts:
    # Every rate a stored cost needs travels with its line (the five snapshot columns are all or
    # none), so a missing rate cannot reach approval: save refuses it first (fx_unavailable).
    missing = {r["cost_currency"] for r in lines
               if r["cost_currency"] not in (None, "CLP") and r["fx_rate"] is None}
    return ApprovalFacts(
        item_line_nos=tuple(r["line_no"] for r in lines if r["line_kind"] == "item"),
        unpriced_line_nos=tuple(r.line_no for r in result.lines if r.unit_price is None),
        expense_count=sum(1 for r in lines if r["line_kind"] == "logistics" and (r["unit_cost"] or 0) > 0),
        principal_line_no=result.principal_line_no, missing_fx=tuple(sorted(missing)),
        has_institution=institution is not None, has_recipient=recipient is not None,
        printed=printed_texts(terms, lines), recipient_required=recipient_required)


def party_snapshot(*, institution: dict[str, Any], recipient: dict[str, Any] | None, terms: DraftTerms, validity_days: int,
                   quote_number: str, label: str, approved_by: str, approved_on: date,
                   valid_until: date, client_details: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the client's document says, frozen at approval. No cost, price or margin: the PDF reads it.

    What the operator typed in «Datos del cliente» (`client_details`) wins over what the CRM knows.
    A tender's offer has no recipient: `recipient` is null and nothing prints an «Atención» line."""
    typed = {k: v for k, v in (client_details or {}).items() if isinstance(v, str) and v.strip()}
    return {
        "institution": {"id": institution["id"], "name": typed.get("institution_name") or institution["name"],
                        "rut": typed.get("rut") or institution["rut"]},
        "recipient": None if recipient is None else {
            "participant_id": recipient["participant_id"],
            "name": typed.get("contact_name") or recipient["name"], "email": recipient["email"]},
        "client": {k: typed.get(k) for k in ("contact_role", "department", "phone", "address", "city")},
        "terms": {**{column: getattr(terms, column) or None for column, _ in PRINTED_TERMS},
                  "validity_days": validity_days, "terms_extra": [t.model_dump() for t in terms.terms_extra]},
        "quote_number": quote_number, "revision_label": label, "approved_by": approved_by,
        "approved_at": approved_on.isoformat(), "valid_until": valid_until.isoformat(),
    }


_SAME_NEGOTIATED_SQL = """
select id::text as id from catalog.supplier_product
 where supplier_organization_id = %s::uuid and product_id = %s::uuid and currency = %s and price = %s
   and price_kind = 'negotiated'
 order by as_of desc, created_at desc, id
 limit 1
"""


#: Why a typed cost was not recorded, by the refusal `_live_party` gives its supplier.
_DEAD_SUPPLIER = {"organization_merged": "supplier_merged", "archived_subject": "supplier_archived",
                  "supplier_not_found": "supplier_not_found"}


def _record_negotiated_costs(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                             lines: list[dict[str, Any]], label: str, revision_id: str,
                             receipt_id: str) -> list[dict[str, Any]]:
    """A cost the operator typed for a catalog product and a named supplier becomes a `negotiated`
    observation (10-05 D6), and the line names it from then on — written while the lines are still
    a draft, so a revision copied from this one does not record the same cost twice. The same cost
    already observed is reused, not recorded again; two different costs for one product and
    supplier in one revision are two observations (`clock_timestamp()` keeps their as_of apart).

    As `record-supplier-cost` would: the cost goes to the product the line's product survives as
    (merged since the save: one hop, as the draft resolves it), and only a live supplier gets one —
    held `for share` so it is not archived or merged underneath. A cost whose supplier was archived
    or merged since the save is not recorded, and approval still goes ahead: the returned list
    names each skipped line and why (`supplier_archived`, `supplier_merged`).
    """
    typed = [r for r in lines if r["line_kind"] == "item" and r["cost_observation_id"] is None
             and r["product_id"] and r["supplier_organization_id"] and r["unit_cost"] is not None
             and r["cost_currency"]]
    if not typed:
        return []
    cur.execute("select id::text as id, coalesce(merged_into_product_id, id)::text as survivor "
                "from catalog.product where id = any(%s::uuid[])", (sorted({str(r["product_id"]) for r in typed}),))
    survivor = {r["id"]: r["survivor"] for r in _all(cur)}
    # The product's stream gets the event: lock it as record-supplier-cost does, in id order, then the suppliers.
    cur.execute("select id from catalog.product where id = any(%s::uuid[]) order by id for no key update",
                (sorted(set(survivor.values())),))
    dead: dict[str, str] = {}
    for supplier in sorted({str(r["supplier_organization_id"]) for r in typed}):
        try:
            _live_party(cur, supplier, not_found_code="supplier_not_found", what="supplier")
        except CommandRefused as exc:
            dead[supplier] = _DEAD_SUPPLIER.get(exc.code, exc.code)
    skipped = []
    for row in typed:
        supplier, product = str(row["supplier_organization_id"]), survivor[str(row["product_id"])]
        if supplier in dead:
            skipped.append({"line_no": row["line_no"], "reason": dead[supplier]})
            continue
        cur.execute(_SAME_NEGOTIATED_SQL, (supplier, product, row["cost_currency"], row["unit_cost"]))
        found = _one(cur)
        if found is None:
            cur.execute(
                """
                insert into catalog.supplier_product
                    (supplier_organization_id, product_id, as_of, price, currency, price_kind, provenance_note,
                     recorded_by_operator_id)
                values (%s::uuid, %s::uuid, clock_timestamp(), %s, %s, 'negotiated', %s, %s::uuid)
                returning id::text as id
                """,
                (supplier, product, row["unit_cost"], row["cost_currency"], f"Cotización {label}",
                 operator.operator_id),
            )
            found = _one(cur)
            assert found is not None  # noqa: S101 - `returning` on a successful insert
            self._append_event(cur, aggregate_kind="product", aggregate_id=product,
                               event_type="product.cost_recorded",
                               payload={"supplier_product_id": found["id"], "supplier_organization_id": supplier,
                                        "price_kind": "negotiated", "currency": row["cost_currency"],
                                        "quote_revision_id": revision_id},
                               operator=operator, receipt_id=receipt_id)
        cur.execute("update crm.quote_line set cost_observation_id = %s::uuid, updated_at = now() "
                    "where id = %s::uuid", (found["id"], row["id"]))
    return skipped


def _supersede_current(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity, revision: dict[str, Any],
                       receipt_id: str) -> None:
    """The quote's earlier issued, not yet superseded revisions are superseded by this one."""
    cur.execute(
        """
        select id::text as id, revision_no from crm.quote_revision
         where quote_id = %s and revision_no < %s and status in ('approved', 'sent')
           and superseded_by_revision_no is null
         order by revision_no
           for update
        """,
        (revision["quote_id"], revision["revision_no"]),
    )
    for prior in _all(cur):
        cur.execute("update crm.quote_revision set superseded_by_revision_no = %s, superseded_at = now(), "
                    "version = version + 1, updated_at = now() where id = %s::uuid",
                    (revision["revision_no"], prior["id"]))
        self._append_event(cur, aggregate_kind="quote_revision", aggregate_id=prior["id"],
                           event_type="quote_revision.superseded",
                           payload={"quote_id": str(revision["quote_id"]), "revision_no": prior["revision_no"],
                                    "superseded_by_revision_no": revision["revision_no"]},
                           operator=operator, receipt_id=receipt_id)


def _handle_approve_quote_revision(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                                   fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    revision_id, expected_version = fields["revision_id"], fields["expected_version"]
    # Locks in the order every quote-wide command takes them («Elegir revisión vigente»: the case,
    # the quote, its revisions; create-quote-revision: the quote), so approval never deadlocks with
    # them: the case `for share` (it cannot be closed underneath), the quote, then this revision and,
    # in `_supersede_current`, the earlier ones. The revision's quote and case never change.
    case = _lock_case_and_quote(cur, revision_id, quote_lock="update")
    _locked_draft(cur, revision_id, expected_version)
    cur.execute(_APPROVAL_REVISION_SQL, (revision_id,))
    revision = _one(cur)
    assert revision is not None  # noqa: S101 - locked just above
    if case["closed_at"] is not None:
        raise CommandRefused(409, "case_closed", "this case is closed; its quote is not approved")

    cur.execute("select * from crm.quote_line where quote_revision_id = %s::uuid order by line_no", (revision_id,))
    lines = _all(cur)
    terms = draft_from_rows(revision, lines).terms
    params = load_parameters(cur)
    try:
        result = compute(engine_input_from_rows(revision, lines, params))
    except EngineError as exc:
        raise CommandRefused(422, exc.code, f"the draft cannot be priced ({exc.code})") from exc

    institution = None
    if case["organization_id"] is not None:
        cur.execute(_INSTITUTION_SQL, (case["organization_id"],))
        institution = _one(cur)
    recipient = None
    if revision["recipient_participant_id"] is not None:
        cur.execute(_RECIPIENT_SQL, (revision["recipient_participant_id"], revision["case_id"]))
        recipient = _one(cur)
    tender = is_tender(case["kind"])
    refusals = check_approvable(_approval_facts(lines, result, institution, recipient, terms,
                                                recipient_required=not tender))
    if refusals:
        raise CommandRefused(409, refusals[0][0], " · ".join(message for _, message in refusals))
    assert institution is not None and (tender or recipient is not None)  # noqa: S101 - refused above otherwise

    # The stored prices are what was priced, from the same inputs; a difference means the engine
    # changed since the save, and freezing would make the lines disagree with the totals.
    stored = {r["line_no"]: (r["unit_price_qc"], r["line_total_qc"]) for r in lines if r["line_kind"] == "item"}
    if any(stored[r.line_no] != (r.unit_price, r.line_total) for r in result.lines):
        raise CommandRefused(409, "draft_needs_saving", "Guarda el borrador de nuevo: sus precios cambiaron.")

    label = _label(revision["quote_number"], revision["revision_no"])
    skipped = _record_negotiated_costs(self, cur, operator, lines, label, revision_id, receipt_id)
    today = santiago_today(self._clock())
    validity = terms.validity_days or _validity_days(params)
    valid_until = today + timedelta(days=validity)
    snapshot = party_snapshot(institution=institution, recipient=recipient, terms=terms,
                              validity_days=validity, quote_number=revision["quote_number"], label=label,
                              approved_by=operator.display_name, approved_on=today, valid_until=valid_until,
                              client_details=revision["client_details"])
    _write_amounts(
        cur,
        """
        update crm.quote_revision
           set status = 'approved', approved_at = now(), approved_by_operator_id = %(operator)s::uuid,
               valid_until = %(valid_until)s, subtotal = %(net)s, discount_total = 0, tax_base = %(net)s,
               tax_total = %(iva)s, grand_total = %(total)s, totals_computed_at = now(),
               party_snapshot = %(snapshot)s::jsonb, party_snapshot_version = 1,
               version = version + 1, updated_at = now()
         where id = %(id)s::uuid and version = %(version)s
        returning version
        """,
        {"operator": operator.operator_id, "valid_until": valid_until, "net": result.net, "iva": result.iva,
         "total": result.total, "snapshot": json.dumps(snapshot, ensure_ascii=False), "id": revision_id,
         "version": expected_version},
    )
    updated = _one(cur)
    if updated is None:  # pragma: no cover - the row is locked; kept as the compare-and-set's other half
        raise CommandRefused(409, "stale_version", "concurrent modification")
    self._append_event(cur, aggregate_kind="quote_revision", aggregate_id=revision_id,
                       event_type="quote_revision.transitioned", payload={"from": "draft", "to": "approved"},
                       operator=operator, receipt_id=receipt_id)
    _supersede_current(self, cur, operator, revision, receipt_id)
    return {"ok": True, "revision_id": revision_id, "version": updated["version"],
            "quote_number": revision["quote_number"], "revision_label": label, "negotiated_costs_skipped": skipped}


# ------------------------------------------------------------------ void-quote-revision

def _handle_void_quote_revision(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                                fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """A draft or an approved revision is voided; one already sent is answered by a new revision."""
    revision_id, expected_version = fields["revision_id"], fields["expected_version"]
    cur.execute("select status, origin, version, superseded_by_revision_no from crm.quote_revision "
                "where id = %s::uuid for update", (revision_id,))
    revision = _one(cur)
    if revision is None:
        raise CommandRefused(404, "revision_not_found", "no such quote revision")
    if revision["origin"] != "authored":
        raise CommandRefused(409, "not_authored", "a historical revision is voided by its own command")
    if revision["status"] == "sent":
        raise CommandRefused(409, "already_sent", "Ya se envió: crea una nueva revisión.")
    if revision["status"] == "void":
        raise CommandRefused(409, "already_void", "this revision is already void")
    if revision["superseded_by_revision_no"] is not None:  # a superseded revision stays approved
        raise CommandRefused(409, "superseded",
                             f"Ya fue reemplazada por la revisión {revision['superseded_by_revision_no']}.")
    if revision["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "the revision was modified since you loaded it")
    cur.execute("update crm.quote_revision set status = 'void', version = version + 1, updated_at = now() "
                "where id = %s::uuid returning version", (revision_id,))
    updated = _one(cur)
    assert updated is not None  # noqa: S101 - locked above
    self._append_event(cur, aggregate_kind="quote_revision", aggregate_id=revision_id,
                       event_type="quote_revision.transitioned",
                       payload={"from": revision["status"], "to": "void", "reason": fields["reason"]},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "revision_id": revision_id, "version": updated["version"]}


# ------------------------------------------------------------------ create-quote-revision

_REVISION_COPY_COLUMNS = (
    "quote_currency", "price_decimals", "tax_rate", "rounding_rule", "subtotal", "discount_total", "tax_base",
    "tax_total", "grand_total", "totals_computed_at", "payment_terms", "delivery_terms", "tax_note",
    "billing_address_id", "delivery_address_id", "recipient_participant_id", "signatory_participant_id",
    "warranty_terms_es", "delivery_place_es", "installation_terms_es", "client_notes_es", "terms_extra",
    "validity_days", "expense_method", "expense_allocation_mode", "principal_rule", "fx_override_reasons",
    "client_details",
)
_LINE_COPY_COLUMNS = (
    "line_no", "line_kind", "description", "product_id", "quantity", "cost_currency", "unit_cost", "fx_rate",
    "fx_as_of", "fx_source", "margin_mode", "margin_pct", "unit_price_qc", "line_total_qc", "is_principal",
    "allocated_to_line_no", "item_type", "expense_method", "calc_inputs", "supplier_organization_id",
    "cost_observation_id", "line_note_es",
)
_OPEN_REVISION = "quote_revision_one_open_per_quote"


def _draft_open() -> CommandRefused:
    return CommandRefused(409, "draft_open", "this quote already has an open draft; edit or void it first")


def _handle_create_quote_revision(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                                  fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """The newest issued revision, copied whole (terms, pricing choices, lines) into the next draft."""
    quote_id = fields["quote_id"]
    cur.execute("select id::text as id, quote_number from crm.quote where id = %s::uuid for update", (quote_id,))
    quote = _one(cur)
    if quote is None:
        raise CommandRefused(404, "quote_not_found", "no such quote")
    cur.execute("select 1 from crm.quote_revision where quote_id = %s::uuid and status in ('draft', 'in_review')",
                (quote_id,))
    if cur.fetchone() is not None:
        raise _draft_open()
    cur.execute("select id::text as id, revision_no from crm.quote_revision where quote_id = %s::uuid "
                "and origin = 'authored' and status in ('approved', 'sent') order by revision_no desc limit 1",
                (quote_id,))
    source = _one(cur)
    if source is None:
        raise CommandRefused(409, "nothing_to_revise", "this quote has no approved or sent revision to revise")
    cur.execute("select max(revision_no) + 1 as next from crm.quote_revision where quote_id = %s::uuid", (quote_id,))
    revision_no = _one(cur)["next"]  # type: ignore[index]
    label = _label(quote["quote_number"], revision_no)

    columns = ", ".join(_REVISION_COPY_COLUMNS)
    try:
        cur.execute(
            f"""
            insert into crm.quote_revision (quote_id, revision_no, status, origin, created_by_operator_id, {columns})
            select quote_id, %s, 'draft', 'authored', %s::uuid, {columns}
              from crm.quote_revision where id = %s::uuid
            returning id::text as id, version
            """,  # noqa: S608 - column names are module constants
            (revision_no, operator.operator_id, source["id"]),
        )
    except psycopg.errors.UniqueViolation as exc:
        if exc.diag.constraint_name == _OPEN_REVISION:
            raise _draft_open() from exc
        raise
    revision = _one(cur)
    assert revision is not None  # noqa: S101 - `returning` on a successful insert
    line_columns = ", ".join(_LINE_COPY_COLUMNS)
    cur.execute(
        f"""
        insert into crm.quote_line (quote_revision_id, {line_columns})
        select %s::uuid, {line_columns} from crm.quote_line where quote_revision_id = %s::uuid order by line_no
        """,  # noqa: S608 - column names are module constants
        (revision["id"], source["id"]),
    )
    self._append_event(cur, aggregate_kind="quote_revision", aggregate_id=revision["id"],
                       event_type="quote_revision.opened",
                       payload={"quote_id": quote_id, "revision_no": revision_no,
                                "copied_from": source["revision_no"]},
                       operator=operator, receipt_id=receipt_id)
    return {"ok": True, "quote_id": quote_id, "revision_id": revision["id"], "revision_no": revision_no,
            "revision_label": label, "version": revision["version"]}


class V2QuoteRepository(V2CaseCommandRepository):
    """The quote commands, each in one transaction. A case repository too — not for its commands
    (`_HANDLERS` is the quotes' own) but for the moves `create-quote` reuses when it opens a case
    for a client (`_open_case_for_client`, `_add_case_organization`, the supplier lookup)."""

    def __init__(self, connect: Any, dsn: str, *, fx_resolver: FxResolver, statement_timeout_ms: int = 30_000,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        super().__init__(connect, dsn, statement_timeout_ms)
        self._fx_resolver = fx_resolver
        self._clock = clock

    def execute(self, *, command_name: str, operator: OperatorIdentity, fields: dict[str, Any],
                idempotency_key: str, digest: str) -> dict[str, Any]:
        """Refuse Labdelivery material in any submitted text, then run the command."""
        try:
            refuse_labdelivery(*_submitted_text(fields))
        except LabdeliveryRefused as exc:
            raise CommandRefused(422, "labdelivery_refused", "input of Labdelivery origin is refused") from exc
        return super().execute(command_name=command_name, operator=operator, fields=fields,
                               idempotency_key=idempotency_key, digest=digest)

    _HANDLERS = {
        "create-quote": _handle_create_quote,
        "save-quote-draft": _handle_save_quote_draft,
        "approve-quote-revision": _handle_approve_quote_revision,
        "void-quote-revision": _handle_void_quote_revision,
        "create-quote-revision": _handle_create_quote_revision,
    }
