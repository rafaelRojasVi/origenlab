from __future__ import annotations

from origenlab_email_pipeline.historical_quote_register.quote_signal import (
    classify_send_direction,
)


def test_customer_quote_detected():
    direction, contact = classify_send_direction(
        recipients="Compras <compras@universidadaustral.cl>",
        subject="Cotización equipo homogeneizador COT-2026-014",
    )
    assert direction == "customer_quote_candidate"
    assert contact == "compras@universidadaustral.cl"


def test_supplier_domain_is_never_customer_quote_even_with_quote_word():
    direction, _ = classify_send_direction(
        recipients="sales@benchmarkscientific.com",
        subject="Cotización / quote request for centrifuge rotors",
    )
    assert direction == "supplier_rfq"


def test_outbound_rfq_subject_without_supplier_domain_is_supplier_rfq():
    direction, _ = classify_send_direction(
        recipients="ventas@unknown-vendor.example",
        subject="Request for Quotation - filtration units",
    )
    assert direction == "supplier_rfq"


def test_rfq_cue_wins_over_quote_cue_on_overlap():
    """Regression: RFQ cues must win even when overlapping with quote cues.

    'request for quote' contains both:
    - 'request for quote' (an RFQ cue)
    - 'quote' (a quote cue)

    Should classify as supplier_rfq, not customer_quote_candidate.
    """
    direction, _ = classify_send_direction(
        recipients="new-unlisted-vendor@new-unlisted-vendor.example",
        subject="Request for Quote - filtration cartridges",
    )
    assert direction == "supplier_rfq"


def test_internal_only_recipient_is_excluded():
    direction, contact = classify_send_direction(
        recipients="equipo@origenlab.cl",
        subject="Cotización interna",
    )
    assert direction == "internal_only"
    assert contact == ""


def test_no_cue_subject_is_ambiguous_not_guessed():
    direction, _ = classify_send_direction(
        recipients="contacto@cliente-real.cl",
        subject="Seguimiento",
    )
    assert direction == "ambiguous"
