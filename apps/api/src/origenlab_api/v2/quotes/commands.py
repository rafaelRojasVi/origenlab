"""Quote commands — create a quote on a case, save its draft priced by the engine.

Every handler runs inside the one transaction `CommandTransaction` provides: the receipt, the
handler's writes and its events commit together or not at all; a `CommandRefused` rolls all of it
back (receipt included).

**One calculation, on the server** (spec 2026-10-15 O5). `save-quote-draft` takes the whole draft,
resolves it against the catalog, the parameters and the day's exchange rates (`inputs.py`), runs
`quoting.engine.compute`, and stores exactly what it computed: the lines are replaced, the totals
written, the version bumped. The database decides the rest — lines change only on an authored draft
and carry one exchange rate per currency (`crm.quote_line_authored_draft_only`).

**Locks.** `create-quote` locks the case row (so a case being closed cannot get a quote in the same
instant) and mints the number under the numbering advisory lock; its events open two new streams.
`save-quote-draft` locks the revision row, which owns the `quote_revision` stream it appends to.

**Nothing of Labdelivery origin** enters a quote: `execute` refuses any submitted string that names
it, before a transaction opens. Event payloads carry no cost, price or margin — only the net total
of a saved draft, which is the client-facing figure.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from origenlab_api.quoting.engine import EngineError, compute
from origenlab_api.v2.catalog.commands import _submitted_text
from origenlab_api.v2.catalog.keys import LabdeliveryRefused, refuse_labdelivery
from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quotes.draft import QuoteDraft
from origenlab_api.v2.quotes.inputs import (
    FxResolver,
    Resolved,
    expense_target,
    fx_override_reasons,
    load_parameters,
    resolve_draft,
    result_json,
)
from origenlab_api.v2.quotes.numbering import SANTIAGO, allocate_quote_number

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
            cur.execute(_INSERT_LINE_SQL, row)
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
    cur.execute(
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


# ------------------------------------------------------------------ not yet

def _not_implemented(self: "V2QuoteRepository", cur: Any, operator: OperatorIdentity,
                     fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    raise CommandRefused(501, "not_implemented", "this quote command is not available yet")


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
        "approve-quote-revision": _not_implemented,
        "void-quote-revision": _not_implemented,
        "create-quote-revision": _not_implemented,
    }
