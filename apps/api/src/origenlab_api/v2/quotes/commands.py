"""Quote commands — create a quote on a case, save its draft priced by the engine, approve, void, revise.

Every handler runs inside the one transaction `CommandTransaction` provides: the receipt, the
handler's writes and its events commit together or not at all; a `CommandRefused` rolls all of it
back (receipt included).

**One calculation, on the server** (spec 2026-10-15 O5). `save-quote-draft` takes the whole draft,
resolves it against the catalog, the parameters and the day's exchange rates (`inputs.py`), runs
`quoting.engine.compute`, and stores exactly what it computed: the lines are replaced, the totals
written, the version bumped. The database decides the rest — lines change only on an authored draft
and carry one exchange rate per currency (`crm.quote_line_authored_draft_only`).

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
instant) and mints the number under the numbering advisory lock; its events open two new streams.
`save-quote-draft`, `approve-quote-revision` and `void-quote-revision` lock the revision row, which
owns the `quote_revision` stream they append to; approval also holds the case `for share` (it cannot
be closed underneath), locks the revision it supersedes and the products whose stream gets a cost.
`create-quote-revision` locks the quote, so two cannot open the same next revision.

**Nothing of Labdelivery origin** enters a quote: `execute` refuses any submitted string that names
it, before a transaction opens. Event payloads carry no cost, price or margin — only the net total
of a saved draft, which is the client-facing figure.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from origenlab_api.quoting.engine import EngineError, EngineResult, compute
from origenlab_api.v2.catalog.commands import _submitted_text
from origenlab_api.v2.catalog.keys import LabdeliveryRefused, refuse_labdelivery
from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quotes.draft import DraftTerms, QuoteDraft
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


def _handle_create_quote(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                         fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    case_id = fields["opportunity_id"]
    cur.execute("select id::text as id, closed_at from crm.opportunity where id = %s::uuid for update", (case_id,))
    case = _one(cur)
    if case is None:
        raise CommandRefused(404, "case_not_found", "no such commercial case")
    if case["closed_at"] is not None:
        raise CommandRefused(409, "case_closed", "this case is closed; a closed case gets no new quote")

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
    cur.execute(_PRIMARY_PARTICIPANT_SQL, (case_id,))
    recipient = _one(cur)
    cur.execute(
        """
        insert into crm.quote_revision
            (quote_id, revision_no, status, origin, quote_currency, price_decimals, tax_rate,
             delivery_terms, payment_terms, warranty_terms_es, delivery_place_es, installation_terms_es,
             validity_days, recipient_participant_id, created_by_operator_id)
        values (%(quote_id)s::uuid, 1, 'draft', 'authored', 'CLP', 0, %(tax_rate)s::numeric,
                %(delivery_terms)s, %(payment_terms)s, %(warranty_terms_es)s, %(delivery_place_es)s,
                %(installation_terms_es)s, %(validity_days)s, %(recipient)s::uuid, %(operator)s::uuid)
        returning id::text as id, version
        """,
        {"quote_id": quote["id"], "tax_rate": _iva(params), **defaults, "validity_days": _validity_days(params),
         "recipient": recipient["id"] if recipient else None, "operator": operator.operator_id},
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
    return {"ok": True, "quote_id": quote["id"], "quote_number": number, "revision_id": revision["id"],
            "version": revision["version"]}


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


def _check_recipient(cur: Any, participant_id: str, opportunity_id: str) -> None:
    cur.execute("select 1 from crm.opportunity_participant where id = %s::uuid and opportunity_id = %s::uuid "
                "and valid_to is null", (participant_id, opportunity_id))
    if cur.fetchone() is None:
        raise CommandRefused(422, "recipient_not_on_case", "the recipient is not a current participant of this case")


def _handle_save_quote_draft(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                             fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    revision_id, expected_version = fields["revision_id"], fields["expected_version"]
    draft = QuoteDraft.model_validate(fields["draft"])
    revision = _locked_draft(cur, revision_id, expected_version)
    recipient = str(draft.recipient_participant_id) if draft.recipient_participant_id else None
    if recipient is not None:
        _check_recipient(cur, recipient, revision["opportunity_id"])

    today = santiago_today(self._clock())
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
               subtotal = %(net)s, discount_total = 0, tax_base = %(net)s, tax_total = %(iva)s,
               grand_total = %(total)s, totals_computed_at = now(), version = version + 1, updated_at = now()
         where id = %(id)s::uuid and version = %(version)s
        returning version
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
            "computed": result_json(resolved, result)}


# ------------------------------------------------------------------ approve-quote-revision

#: Why a draft cannot be approved yet, in the words the dashboard shows beside the button.
APPROVAL_REFUSALS = {
    "no_items": "Agrega al menos un producto.",
    "unpriced_line": "La línea {line_no} no tiene precio (falta costo o precio).",
    "principal_missing": "Hay gastos de importación pero ningún equipo principal; elige uno.",
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
    if not facts.has_recipient:
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

# The recipient, while still a current participant of the case: the person's name and the
# participant's own email address, else the person's (an active one first).
_RECIPIENT_SQL = """
select p.id::text as participant_id, coalesce(pe.display_name, cp.value_display) as name, cp.value_norm as email
  from crm.opportunity_participant p
  left join crm.person pe on pe.id = p.person_id
  left join lateral (
    select c.value_norm, c.value_display from crm.contact_point c
     where c.kind = 'email'
       and (c.id = p.contact_point_id or (p.contact_point_id is null and c.person_id = p.person_id))
     order by (c.status = 'active') desc, c.created_at, c.id
     limit 1) cp on true
 where p.id = %s::uuid and p.opportunity_id = %s::uuid and p.valid_to is null
"""


def _label(quote_number: str, revision_no: int) -> str:
    try:
        return revision_label(quote_number, revision_no)
    except ValueError as exc:
        raise CommandRefused(409, "revision_letters_exhausted",
                             f"quote {quote_number} has no letter left for revision {revision_no}") from exc


def _approval_facts(lines: list[dict[str, Any]], result: EngineResult, institution: Any, recipient: Any,
                    terms: DraftTerms) -> ApprovalFacts:
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
        printed=printed_texts(terms, lines))


def party_snapshot(*, institution: dict[str, Any], recipient: dict[str, Any], terms: DraftTerms, validity_days: int,
                   quote_number: str, label: str, approved_by: str, approved_on: date,
                   valid_until: date) -> dict[str, Any]:
    """What the client's document says, frozen at approval. No cost, price or margin: the PDF reads it."""
    return {
        "institution": {"id": institution["id"], "name": institution["name"], "rut": institution["rut"]},
        "recipient": {"participant_id": recipient["participant_id"], "name": recipient["name"],
                      "email": recipient["email"]},
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


def _record_negotiated_costs(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                             lines: list[dict[str, Any]], label: str, revision_id: str, receipt_id: str) -> None:
    """A cost the operator typed for a catalog product and a named supplier becomes a `negotiated`
    observation (10-05 D6), and the line names it from then on — written while the lines are still
    a draft, so a revision copied from this one does not record the same cost twice. The same cost
    already observed is reused, not recorded again; two different costs for one product and
    supplier in one revision are two observations (`clock_timestamp()` keeps their as_of apart).
    """
    typed = [r for r in lines if r["line_kind"] == "item" and r["cost_observation_id"] is None
             and r["product_id"] and r["supplier_organization_id"] and r["unit_cost"] is not None
             and r["cost_currency"]]
    if not typed:
        return
    # The product's stream gets the event: lock it as record-supplier-cost does, in id order.
    cur.execute("select id from catalog.product where id = any(%s::uuid[]) order by id for no key update",
                (sorted({str(r["product_id"]) for r in typed}),))
    for row in typed:
        supplier, product = str(row["supplier_organization_id"]), str(row["product_id"])
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
    _locked_draft(cur, revision_id, expected_version)
    cur.execute(_APPROVAL_REVISION_SQL, (revision_id,))
    revision = _one(cur)
    assert revision is not None  # noqa: S101 - locked just above
    cur.execute("select closed_at, organization_id::text as organization_id from crm.opportunity "
                "where id = %s::uuid for share", (revision["case_id"],))
    case = _one(cur)
    assert case is not None  # noqa: S101 - crm.quote.opportunity_id is a foreign key
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
    refusals = check_approvable(_approval_facts(lines, result, institution, recipient, terms))
    if refusals:
        raise CommandRefused(409, refusals[0][0], " · ".join(message for _, message in refusals))
    assert institution is not None and recipient is not None  # noqa: S101 - refused above otherwise

    # The stored prices are what was priced, from the same inputs; a difference means the engine
    # changed since the save, and freezing would make the lines disagree with the totals.
    stored = {r["line_no"]: (r["unit_price_qc"], r["line_total_qc"]) for r in lines if r["line_kind"] == "item"}
    if any(stored[r.line_no] != (r.unit_price, r.line_total) for r in result.lines):
        raise CommandRefused(409, "draft_needs_saving", "Guarda el borrador de nuevo: sus precios cambiaron.")

    label = _label(revision["quote_number"], revision["revision_no"])
    _record_negotiated_costs(self, cur, operator, lines, label, revision_id, receipt_id)
    today = santiago_today(self._clock())
    validity = terms.validity_days or _validity_days(params)
    valid_until = today + timedelta(days=validity)
    snapshot = party_snapshot(institution=institution, recipient=recipient, terms=terms,
                              validity_days=validity, quote_number=revision["quote_number"], label=label,
                              approved_by=operator.display_name, approved_on=today, valid_until=valid_until)
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
            "quote_number": revision["quote_number"], "revision_label": label}


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


class V2QuoteRepository(CommandTransaction):
    """The quote commands, each in one transaction."""

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
