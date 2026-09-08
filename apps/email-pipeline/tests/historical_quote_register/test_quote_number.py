from __future__ import annotations

from origenlab_email_pipeline.historical_quote_register.quote_number import (
    build_historical_quote_key,
    extract_quote_number,
)


def test_extracts_from_subject_high_confidence():
    m = extract_quote_number(subject="Cotización COT-2026-014 equipo X", filename=None)
    assert m is not None
    assert m.raw_quote_number == "COT-2026-014"
    assert m.confidence == "high"
    assert m.matched_in == ("subject",)


def test_extracts_from_filename_only_medium_confidence():
    m = extract_quote_number(subject="Adjunto cotización", filename="COT-2026-014_rev1.pdf")
    assert m is not None
    assert m.raw_quote_number == "COT-2026-014"
    assert m.confidence == "medium"


def test_no_match_returns_none():
    assert extract_quote_number(subject="Seguimiento comercial", filename="propuesta.pdf") is None


def test_pads_short_sequence_number():
    m = extract_quote_number(subject="COT 2026 7", filename=None)
    assert m is not None
    assert m.raw_quote_number == "COT-2026-007"


def test_composite_key_differs_by_customer_even_with_same_number():
    match = extract_quote_number(subject="COT-2026-014", filename=None)
    key_a = build_historical_quote_key(
        customer_domain="cliente-a.cl", quote_number_match=match, send_email_id=1,
    )
    key_b = build_historical_quote_key(
        customer_domain="cliente-b.cl", quote_number_match=match, send_email_id=2,
    )
    assert key_a != key_b


def test_composite_key_stable_for_same_customer_and_number():
    match = extract_quote_number(subject="COT-2026-014", filename=None)
    key_1 = build_historical_quote_key(
        customer_domain="cliente-a.cl", quote_number_match=match, send_email_id=1,
    )
    key_2 = build_historical_quote_key(
        customer_domain="cliente-a.cl", quote_number_match=match, send_email_id=2,
    )
    assert key_1 == key_2  # same quote identity — send_email_id must NOT affect the key when a number exists


def test_no_number_falls_back_to_standalone_send_event_key():
    key_1 = build_historical_quote_key(
        customer_domain="cliente-a.cl", quote_number_match=None, send_email_id=1,
    )
    key_2 = build_historical_quote_key(
        customer_domain="cliente-a.cl", quote_number_match=None, send_email_id=2,
    )
    assert key_1 != key_2  # no synthetic clustering without a confident number
