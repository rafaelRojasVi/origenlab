"""Quote draft and command bodies: shape validation only. Every value is invented."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from origenlab_api.v2.quotes.draft import (
    QUOTE_BODIES,
    CreateQuoteBody,
    DraftItemLine,
    DraftTerms,
    QuoteDraft,
    SaveQuoteDraftBody,
    VoidQuoteBody,
)

_LINE = {"description": "Equipo ficticio", "quantity": "1"}


def test_a_minimal_draft_parses_with_defaults() -> None:
    draft = QuoteDraft()
    assert draft.items == [] and draft.expense_method == "profile"
    assert draft.allocation_mode == "principal"
    assert draft.principal_rule == "highest_cost_imported_equipment"
    assert draft.terms == DraftTerms()
    line = DraftItemLine(**_LINE)
    assert line.quantity == Decimal("1") and line.margin_mode == "markup"


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        QuoteDraft(surprise=1)
    with pytest.raises(ValidationError):
        DraftItemLine(**_LINE, surprise=1)


def test_unit_cost_and_currency_come_together() -> None:
    with pytest.raises(ValidationError, match="unit_cost and cost_currency"):
        DraftItemLine(**_LINE, unit_cost="10")
    with pytest.raises(ValidationError, match="unit_cost and cost_currency"):
        DraftItemLine(**_LINE, cost_currency="EUR")
    assert DraftItemLine(**_LINE, unit_cost="10", cost_currency="EUR").unit_cost == Decimal("10")


def test_margin_mode_none_needs_a_typed_price() -> None:
    with pytest.raises(ValidationError, match="typed_unit_price"):
        DraftItemLine(**_LINE, margin_mode="none")
    assert DraftItemLine(**_LINE, margin_mode="none", typed_unit_price="99").typed_unit_price == Decimal("99")


def test_margin_mode_margin_needs_a_percentage_below_one() -> None:
    with pytest.raises(ValidationError, match="margin_pct"):
        DraftItemLine(**_LINE, margin_mode="margin")
    with pytest.raises(ValidationError, match="margin_pct"):
        DraftItemLine(**_LINE, margin_mode="margin", margin_pct="1")
    assert DraftItemLine(**_LINE, margin_mode="margin", margin_pct="0.3").margin_pct == Decimal("0.3")


def test_item_bounds() -> None:
    for bad in ({"quantity": "0"}, {"quantity": "100001"}, {"description": ""}, {"description": "x" * 501},
                {"margin_pct": "10.5"}, {"unit_cost": "-1", "cost_currency": "CLP"}, {"item_type": "gadget"},
                {"line_note_es": "x" * 1001}):
        with pytest.raises(ValidationError):
            DraftItemLine(**{**_LINE, **bad})


def test_marked_lines_only_with_the_manual_rule() -> None:
    two = [{**_LINE, "is_principal": True}, {**_LINE, "is_principal": True}]
    with pytest.raises(ValidationError, match="manual"):
        QuoteDraft(items=two)
    with pytest.raises(ValidationError, match="manual"):
        QuoteDraft(items=[{**_LINE, "is_principal": True}])
    ok = QuoteDraft(items=[*two, _LINE], principal_rule="manual")   # several share the expenses
    assert sum(i.is_principal for i in ok.items) == 2


def test_duplicate_fx_override_currencies_are_refused() -> None:
    one = {"currency": "EUR", "clp_per_unit": "1000", "reason": "acuerdo ficticio"}
    with pytest.raises(ValidationError, match="unique"):
        QuoteDraft(fx_overrides=[one, one])
    assert len(QuoteDraft(fx_overrides=[one, {**one, "currency": "USD"}]).fx_overrides) == 2
    with pytest.raises(ValidationError):
        QuoteDraft(fx_overrides=[{**one, "currency": "CLP"}])
    with pytest.raises(ValidationError):
        QuoteDraft(fx_overrides=[one, {**one, "currency": "USD"}, {**one, "currency": "USD"}])


def test_list_limits() -> None:
    QuoteDraft(items=[_LINE] * 60)
    with pytest.raises(ValidationError):
        QuoteDraft(items=[_LINE] * 61)
    with pytest.raises(ValidationError):
        QuoteDraft(manual_expenses=[{"label": "x", "amount": "1"}] * 21)
    with pytest.raises(ValidationError):
        QuoteDraft(terms={"terms_extra": [{"label_es": "a", "text_es": "b"}] * 13})


def test_terms_text_fields_are_capped_and_stripped() -> None:
    assert DraftTerms(delivery_terms="  hola  ").delivery_terms == "hola"
    for field in ("delivery_terms", "payment_terms", "warranty_terms_es", "delivery_place_es",
                  "installation_terms_es", "client_notes_es"):
        DraftTerms(**{field: "x" * 1000})
        with pytest.raises(ValidationError):
            DraftTerms(**{field: "x" * 1001})
    for bad in (0, 366):
        with pytest.raises(ValidationError):
            DraftTerms(validity_days=bad)


def test_command_bodies() -> None:
    rid, oid = str(uuid.uuid4()), str(uuid.uuid4())
    assert CreateQuoteBody(opportunity_id=oid).note is None
    with pytest.raises(ValidationError):
        SaveQuoteDraftBody(revision_id=rid, expected_version=0, draft={})
    assert SaveQuoteDraftBody(revision_id=rid, expected_version=1, draft={}).draft == QuoteDraft()
    with pytest.raises(ValidationError):
        VoidQuoteBody(revision_id=rid, expected_version=1, reason="x")
    with pytest.raises(ValidationError):
        VoidQuoteBody(revision_id=rid, expected_version=1, reason="motivo", extra=1)
    assert set(QUOTE_BODIES) == {"create-quote", "save-quote-draft", "approve-quote-revision",
                                 "void-quote-revision", "create-quote-revision"}


def test_numbers_carry_the_precision_of_their_columns() -> None:
    from origenlab_api.v2.quotes.draft import DraftExpenseLine, DraftFxOverride

    ok = DraftItemLine(**{**_LINE, "quantity": "1.123456", "unit_cost": "999999999999.999999", "cost_currency": "EUR",
                          "margin_pct": "0.123456"})
    assert ok.unit_cost == Decimal("999999999999.999999") and ok.quantity == Decimal("1.123456")
    for bad in ({"quantity": "1.1234567"}, {"unit_cost": "1.1234567", "cost_currency": "EUR"},
                {"unit_cost": "1000000000000", "cost_currency": "EUR"}, {"margin_pct": "0.1234567"},
                {"margin_mode": "none", "typed_unit_price": "1000000000000"},
                {"margin_mode": "none", "typed_unit_price": "1.1234567"}):
        with pytest.raises(ValidationError):
            DraftItemLine(**{**_LINE, **bad})
    assert DraftFxOverride(currency="EUR", clp_per_unit="1023.12345678", reason="prueba").clp_per_unit == Decimal(
        "1023.12345678")
    for bad in ("1.123456789", "10000000000"):
        with pytest.raises(ValidationError):
            DraftFxOverride(currency="EUR", clp_per_unit=bad, reason="prueba")
    with pytest.raises(ValidationError):
        DraftExpenseLine(label="Flete", amount="1000000000000")


def test_the_create_quote_note_is_capped_like_other_command_notes() -> None:
    oid = str(uuid.uuid4())
    assert CreateQuoteBody(opportunity_id=oid, note="x" * 500).note == "x" * 500
    assert CreateQuoteBody(opportunity_id=oid, note="  Nota  ").note == "Nota"
    for note in ("", "   ", "x" * 501):
        with pytest.raises(ValidationError):
            CreateQuoteBody(opportunity_id=oid, note=note)


def test_the_recipient_is_exactly_one_pick() -> None:
    pid = str(uuid.uuid4())
    assert QuoteDraft(recipient={"contact_point_id": pid}).recipient.contact_point_id == uuid.UUID(pid)
    picked = QuoteDraft(recipient={"email": "  Persona@Ejemplo.TEST ", "name": "Persona Ficticia"}).recipient
    assert (picked.email, picked.name) == ("persona@ejemplo.test", "Persona Ficticia")
    for bad in ({}, {"participant_id": pid, "person_id": pid}, {"email": "a@b.test", "contact_point_id": pid},
                {"person_id": pid, "name": "Sin correo"}, {"email": "no-es-un-correo"}, {"email": "a@b.test", "x": 1}):
        with pytest.raises(ValidationError):
            QuoteDraft(recipient=bad)
    # The older field still works, but not together with the new one.
    assert QuoteDraft(recipient_participant_id=pid).recipient_participant_id == uuid.UUID(pid)
    with pytest.raises(ValidationError, match="not both"):
        QuoteDraft(recipient_participant_id=pid, recipient={"participant_id": pid})
