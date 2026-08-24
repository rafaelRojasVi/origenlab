from __future__ import annotations

from origenlab_email_pipeline.commercial.commercial_intel_rules import (
    derive_email_signal_facts,
    pick_external_contact,
)

_INTERNAL = {"origenlab.cl"}


def _positive_codes(facts: list) -> set[str]:
    return {f.signal_code for f in facts if f.signal_kind == "positive"}


def _suppression_codes(facts: list) -> set[str]:
    return {f.signal_code for f in facts if f.signal_kind == "suppression"}


def test_pick_external_contact_recognizes_canonical_origenlab_sender() -> None:
    # Reproduce the production failure: the caller's stale inferred set does
    # NOT contain origenlab.cl and contains unrelated external domains.
    contact = pick_external_contact(
        sender_raw="OrigenLab <contacto@origenlab.cl>",
        recipients_raw="sales@apdataweigh.com",
        internal_domains={
            "labdelivery.cl",
            "googlemail.com",
            "mercadopublico.cl",
            "soviquim.cl",
        },
    )

    assert contact == (
        "sales@apdataweigh.com",
        "apdataweigh.com",
    )


def test_pick_external_contact_handles_exact_operator_gmail_only() -> None:
    outbound = pick_external_contact(
        sender_raw="Tatiana <tvivancob@gmail.com>",
        recipients_raw="compras@hospital.cl",
        internal_domains={
            "origenlab.cl",
            "labdelivery.cl",
        },
    )

    assert outbound == (
        "compras@hospital.cl",
        "hospital.cl",
    )

    # A normal external Gmail address must remain external.
    inbound = pick_external_contact(
        sender_raw="Cliente <buyer@gmail.com>",
        recipients_raw="contacto@origenlab.cl",
        internal_domains={
            "origenlab.cl",
            "labdelivery.cl",
        },
    )

    assert inbound == (
        "buyer@gmail.com",
        "gmail.com",
    )


def test_rules_emit_positive_quote_and_procurement_signals() -> None:
    facts = derive_email_signal_facts(
        subject="Solicitud de cotizacion y orden de compra",
        sender_raw="Compras U. Demo <compras@udemo.cl>",
        recipients_raw="contacto@origenlab.cl",
        top_reply_clean="Necesitamos cotizacion formal y condiciones para OC.",
        full_body_clean="",
        sender_domain="udemo.cl",
        internal_domains=_INTERNAL,
        vendor_domains=set(),
        existing_client_domains=set(),
    )
    codes = _positive_codes(facts)
    assert "quote_intent" in codes
    assert "procurement_intent" in codes


def test_rules_emit_vendor_and_invoice_suppressions() -> None:
    facts = derive_email_signal_facts(
        subject="Factura pendiente y despacho proveedor",
        sender_raw="Proveedor Demo <ventas@supplier.cl>",
        recipients_raw="contacto@origenlab.cl",
        top_reply_clean="Adjuntamos factura y guia de despacho.",
        full_body_clean="",
        sender_domain="supplier.cl",
        internal_domains=_INTERNAL,
        vendor_domains={"supplier.cl"},
        existing_client_domains=set(),
    )
    suppression_codes = _suppression_codes(facts)
    assert "vendor_suppression" in suppression_codes
    assert "invoice_payment_suppression" in suppression_codes
    assert "logistics_suppression" in suppression_codes


def test_santo_tomas_fluorescence_plate_reader_inquiry_emits_equipment_signal() -> None:
    """Real Jun 2026 thread: equipment question should not be suppression-only."""
    facts = derive_email_signal_facts(
        subject="Re: CYBER OrigenLab — equipos de laboratorio seleccionados hasta el 7 de junio",
        sender_raw="Tamara Muñoz <tmunoz6@santotomas.cl>",
        recipients_raw="contacto@origenlab.cl",
        top_reply_clean="Te consulto si venden lectores de placas con fluorescencia. Quedo atenta...",
        full_body_clean="",
        sender_domain="santotomas.cl",
        internal_domains=_INTERNAL,
        vendor_domains=set(),
        existing_client_domains={"santotomas.cl"},
    )
    positive = _positive_codes(facts)
    assert positive & {"equipment_relevance", "technical_inquiry"}
    assert "existing_client_suppression" in _suppression_codes(facts)


def test_corteva_quotation_request_emits_quote_and_equipment_signals() -> None:
    """Forwarded Corteva RFQ: quote terms plus named centrifuge and shaker models."""
    body = (
        "Karem Cortes / Corteva\n"
        "Eppendorf Centrifuge 5810R\n"
        "VWR Shaker Model 3500 Orbital Shaker"
    )
    facts = derive_email_signal_facts(
        subject="RV: Solicitud cotización equipos",
        sender_raw="Lab Delivery <contacto@labdelivery.cl>",
        recipients_raw="contacto@origenlab.cl",
        top_reply_clean=body,
        full_body_clean="",
        sender_domain="labdelivery.cl",
        internal_domains=_INTERNAL,
        vendor_domains=set(),
        existing_client_domains=set(),
    )
    positive = _positive_codes(facts)
    assert "quote_intent" in positive
    assert "equipment_relevance" in positive
    equipment_fact = next(f for f in facts if f.signal_code == "equipment_relevance")
    assert "centrifuga" in equipment_fact.rationale_json
    assert "shaker" in equipment_fact.rationale_json


def test_marova_quote_ack_body_alone_does_not_emit_quote_intent() -> None:
    """Ack-only reply without thread subject should not look like a new quote request."""
    facts = derive_email_signal_facts(
        subject="Re: hello",
        sender_raw="Cliente Demo <jovalle@marova.cl>",
        recipients_raw="contacto@origenlab.cl",
        top_reply_clean="Muchas gracias, recibido",
        full_body_clean="",
        sender_domain="marova.cl",
        internal_domains=_INTERNAL,
        vendor_domains=set(),
        existing_client_domains=set(),
    )
    assert _positive_codes(facts) == set()


def test_marova_quote_ack_subject_thread_carries_quote_intent_only() -> None:
    """Thread subject retains cotización context; body alone is a weak ack, not procurement."""
    facts = derive_email_signal_facts(
        subject="Re: Cotización Sonicador Ultrasonido",
        sender_raw="Cliente Demo <jovalle@marova.cl>",
        recipients_raw="contacto@origenlab.cl",
        top_reply_clean="Muchas gracias, recibido",
        full_body_clean="",
        sender_domain="marova.cl",
        internal_domains=_INTERNAL,
        vendor_domains=set(),
        existing_client_domains=set(),
    )
    positive = _positive_codes(facts)
    assert positive == {"quote_intent"}


def test_unach_fondef_waiting_reply_keeps_subject_quote_without_noise() -> None:
    """Funding wait reply stays on quote thread; not treated as system noise."""
    facts = derive_email_signal_facts(
        subject="Re: Cotización-Universidad Adventista de Chile-UIP2000hdT",
        sender_raw="Susana Alfaro <susanaalfaro@unach.cl>",
        recipients_raw="contacto@origenlab.cl",
        top_reply_clean=(
            "muchas gracias por la cotización. Estoy a la espera de los resultados del Fondef Idea..."
        ),
        full_body_clean="",
        sender_domain="unach.cl",
        internal_domains=_INTERNAL,
        vendor_domains=set(),
        existing_client_domains=set(),
    )
    positive = _positive_codes(facts)
    assert positive == {"quote_intent"}
    assert "noise_suppression" not in _suppression_codes(facts)
