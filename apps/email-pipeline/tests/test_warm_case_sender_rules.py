"""Regression and characterization tests for warm-case sender/subject routing."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain_fixture import assert_domain_in_collection
from origenlab_email_pipeline.warm_case_classification import infer_warm_case_category
from origenlab_email_pipeline.warm_case_role_classification import infer_warm_case_role_category
from origenlab_email_pipeline.warm_case_sender_rules import (
    CLIENT_OC_POST_SALE_MARKERS,
    CYBERDAY_CAMPAIGN_SUBJECT,
    INTERNAL_OPERATOR_DOMAINS,
    INTERNAL_OPERATOR_EMAILS,
    PAYMENT_ADMIN_TEXT_MARKERS,
    REAL_CLIENT_DOMAINS,
    SUPPLIER_VENDOR_DOMAINS,
    contact_email_from_recipients,
    contact_email_from_sender,
    email_domain,
    is_chile_institution_client_domain,
    is_internal_operator_contact,
    is_real_client_domain,
    is_supplier_vendor_domain,
    looks_like_auto_reply_text,
    looks_like_cesmec_catalogue_client_thread,
    looks_like_client_equipment_opportunity_thread,
    looks_like_client_oc_post_sale_subject,
    looks_like_client_waiting_review_ack,
    looks_like_contact_routing_notice,
    looks_like_cyberday_bulk_campaign_subject,
    looks_like_dhl_logistics_account_access_thread,
    looks_like_idiem_auto_acknowledgement,
    looks_like_internal_admin_thread,
    looks_like_internal_forwarded_client_quote_request,
    looks_like_logistics_admin_contact,
    looks_like_low_intent_client_acknowledgement,
    looks_like_payment_admin_thread,
    looks_like_suppressed_promotional_marketing_noise,
    looks_like_real_supplier_quote_content,
    looks_like_security_notification,
    looks_like_supplier_followup_thread,
    looks_like_supplier_marketing_thread,
    looks_like_supplier_quote_response,
    looks_like_system_noise_contact,
    looks_like_unach_hielscher_supplier_wait,
    should_keep_visible_despite_suppression,
)

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "origenlab_email_pipeline"
    / "warm_case_sender_rules.py"
)

# Symbols imported by warm_case_role_classification (caller contract).
_ROLE_CLASSIFICATION_IMPORTS = (
    "contact_email_from_recipients",
    "contact_email_from_sender",
    "email_domain",
    "is_internal_operator_contact",
    "is_real_client_domain",
    "is_supplier_vendor_domain",
    "looks_like_auto_reply_text",
    "looks_like_cesmec_catalogue_client_thread",
    "looks_like_client_equipment_opportunity_thread",
    "looks_like_client_oc_post_sale_subject",
    "looks_like_client_waiting_review_ack",
    "looks_like_contact_routing_notice",
    "looks_like_cyberday_bulk_campaign_subject",
    "looks_like_idiem_auto_acknowledgement",
    "looks_like_internal_admin_thread",
    "looks_like_internal_forwarded_client_quote_request",
    "looks_like_logistics_admin_contact",
    "looks_like_low_intent_client_acknowledgement",
    "looks_like_payment_admin_thread",
    "looks_like_supplier_followup_thread",
    "looks_like_supplier_quote_response",
    "looks_like_supplier_marketing_thread",
    "looks_like_system_noise_contact",
    "looks_like_unach_hielscher_supplier_wait",
    "should_keep_visible_despite_suppression",
)


def _row(sender: str, subject: str) -> dict:
    return {
        "email_id": 1,
        "sender_preview": sender,
        "subject_preview": subject,
        "source_file": "gmail:contacto@origenlab.cl/INBOX",
    }


def test_google_security_alert_not_client_reply() -> None:
    sender = "Google <no-reply@accounts.google.com>"
    subject = "Alerta de seguridad"
    assert looks_like_security_notification(sender, subject, contact_email="no-reply@accounts.google.com")
    assert (
        infer_warm_case_category(_row(sender, subject), enrichment_available=False, include_noise=False)
        == "bounce"
    )


def test_eppendorf_registration_is_supplier() -> None:
    sender = "Eppendorf <eppendorf@eppendorf.com>"
    subject = "Please confirm your registration!"
    assert looks_like_supplier_marketing_thread(
        contact_email="eppendorf@eppendorf.com",
        sender=sender,
        subject=subject,
    )
    assert (
        infer_warm_case_category(_row(sender, subject), enrichment_available=False, include_noise=False)
        == "supplier_reply"
    )


def test_valuenindustrial_sales_is_supplier() -> None:
    assert (
        infer_warm_case_category(
            _row("sales@valuenindustrial.com", "Product line 2026"),
            enrichment_available=False,
            include_noise=False,
        )
        == "supplier_reply"
    )


def test_gzfanbolun_sales_is_supplier() -> None:
    assert (
        infer_warm_case_category(
            _row("sales001@gzfanbolun.com", "Lab equipment promo"),
            enrichment_available=False,
            include_noise=False,
        )
        == "supplier_reply"
    )


def test_yuanhuai_yhchem_is_supplier() -> None:
    assert (
        infer_warm_case_category(
            _row("jizhendong@yuanhuai.com", "YHCHEM catalog offer"),
            enrichment_available=False,
            include_noise=False,
        )
        == "supplier_reply"
    )


def test_ceaf_bank_details_payment_admin_thread() -> None:
    assert looks_like_payment_admin_thread(
        "lhidalgo@ceaf.cl",
        "Solicita datos Bancarios",
        snippet="factura N°06 y proceder al pago",
    )


def test_ceaf_oc_not_payment_admin() -> None:
    assert not looks_like_payment_admin_thread("cgaray@ceaf.cl", "Remite OC N º 26172")
    assert looks_like_client_oc_post_sale_subject("Remite OC N º 26172")


def test_banco_factura_payment_admin_domain() -> None:
    assert looks_like_payment_admin_thread(
        "serviciodetransferencias@bancochile.cl",
        "FACTURA 6",
    )


def test_gmail_cotizacion_stays_waiting_supplier_not_client() -> None:
    assert (
        infer_warm_case_category(
            _row(
                "aliro.ramirezf@gmail.com",
                "COTIZACION OSMOMETRO LOSER 16 M EN M.MOLL",
            ),
            enrichment_available=False,
            include_noise=False,
        )
        == "waiting_supplier"
    )


def test_portuguese_resposta_automatica_is_auto_reply() -> None:
    assert looks_like_auto_reply_text("RES: Resposta automática: Cotización")
    assert not looks_like_real_supplier_quote_content(
        "RES: Resposta automática: Cotización",
        "Esta é uma resposta automática.",
    )
    assert not looks_like_supplier_quote_response(
        "noreply@ika.net.br",
        "RES: Resposta automática: Cotización",
        snippet="Esta é uma resposta automática.",
    )


def test_beatriz_real_quote_passes_supplier_quote_gate() -> None:
    assert looks_like_supplier_quote_response(
        "beatriz.bonon@ika.net.br",
        "RE: IKA RV10.70 price response",
        snippet="Monto 112,00 stock disponible",
    )


def test_internal_forwarded_quote_request_detected_as_client_forward() -> None:
    assert looks_like_internal_forwarded_client_quote_request(
        contact_email="contacto@labdelivery.cl",
        subject="RV: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200// RG ENERGIA SPA",
        snippet="Solicitud cliente externa",
        sender="Tatiana Vivanco <contacto@labdelivery.cl>",
    )


# --- Characterization: public constants -------------------------------------------------


def test_public_domain_constants_non_empty() -> None:
    assert_domain_in_collection("origenlab.cl", INTERNAL_OPERATOR_DOMAINS)
    assert "contacto@origenlab.cl" in INTERNAL_OPERATOR_EMAILS
    assert_domain_in_collection("ceaf.cl", REAL_CLIENT_DOMAINS)
    assert_domain_in_collection("eppendorf.com", SUPPLIER_VENDOR_DOMAINS)
    assert "datos bancarios" in PAYMENT_ADMIN_TEXT_MARKERS
    assert "remite oc" in CLIENT_OC_POST_SALE_MARKERS


def test_cyberday_campaign_subject_spanish_origenlab_wording() -> None:
    assert "CYBERDAY" in CYBERDAY_CAMPAIGN_SUBJECT
    assert "equipos de laboratorio" in CYBERDAY_CAMPAIGN_SUBJECT
    assert "junio" in CYBERDAY_CAMPAIGN_SUBJECT


# --- Characterization: email parsing / boundaries ---------------------------------------


def test_contact_email_from_sender_empty_and_invalid() -> None:
    assert contact_email_from_sender(None) == ""
    assert contact_email_from_sender("") == ""
    assert contact_email_from_sender("not-an-email") == ""
    assert contact_email_from_sender("Vendor <sales@ika.net.br>") == "sales@ika.net.br"


def test_contact_email_from_recipients_skips_internal_operator() -> None:
    raw = "Tatiana <contacto@origenlab.cl>, Cliente <cliente@hospital.cl>"
    assert contact_email_from_recipients(raw) == "cliente@hospital.cl"


def test_email_domain_missing_at_sign() -> None:
    assert email_domain("") == ""
    assert email_domain("   ") == ""
    assert email_domain("nodomain") == ""
    assert email_domain("user@CEAF.CL") == "ceaf.cl"


def test_is_internal_operator_contact_by_email_and_domain() -> None:
    assert is_internal_operator_contact("contacto@origenlab.cl")
    assert is_internal_operator_contact("ops@labdelivery.cl")
    assert not is_internal_operator_contact("buyer@hospital.cl")


def test_is_chile_institution_client_domain_boundaries() -> None:
    assert is_chile_institution_client_domain("universidad.cl")
    assert not is_chile_institution_client_domain("gmail.com")
    assert not is_chile_institution_client_domain("eppendorf.com")
    assert not is_chile_institution_client_domain("origenlab.cl")


# --- Characterization: rule precedence (low-level helpers) ------------------------------


def test_internal_admin_precedence_forwarded_quote_exception() -> None:
    """Internal operator + SERVA markers → admin; forwarded RV cotización → not admin."""
    assert looks_like_internal_admin_thread(
        "contacto@origenlab.cl",
        "re: serva payment transfer",
        sender="contacto@origenlab.cl",
    )
    assert not looks_like_internal_admin_thread(
        "contacto@labdelivery.cl",
        "RV: Solicitud de Cotización Tubo Vapor IKA RV10.70",
        snippet="solicitud cliente externa",
        sender="Tatiana <contacto@labdelivery.cl>",
    )
    assert looks_like_internal_forwarded_client_quote_request(
        contact_email="contacto@labdelivery.cl",
        subject="RV: Solicitud de Cotización Tubo Vapor IKA RV10.70",
        snippet="rg energia",
        sender="contacto@labdelivery.cl",
    )


def test_supplier_quote_weak_subject_requires_price_content() -> None:
    assert not looks_like_supplier_quote_response(
        "beatriz.bonon@ika.net.br",
        "RE: follow up",
        snippet="thanks for your email",
    )
    assert looks_like_supplier_quote_response(
        "beatriz.bonon@ika.net.br",
        "RE: price list",
        snippet="USD 112.00 stock disponible",
    )


def test_supplier_followup_without_price_not_quote_response() -> None:
    assert looks_like_supplier_followup_thread(
        "sales@valuenindustrial.com",
        "RE: shipping address for quotation",
        snippet="please send address to calculate shipping",
    )
    assert not looks_like_real_supplier_quote_content(
        "RE: shipping address",
        "please send address to calculate shipping",
    )


def test_system_noise_includes_security_and_mailer_daemon() -> None:
    assert looks_like_system_noise_contact(
        "no-reply@accounts.google.com",
        "Google <no-reply@accounts.google.com>",
        "Alerta de seguridad",
    )
    assert looks_like_system_noise_contact(
        "",
        "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        "Delivery Status Notification",
    )


def test_security_notification_spanish_and_english_markers() -> None:
    assert looks_like_security_notification(
        "Google <no-reply@accounts.google.com>",
        "Alerta de seguridad",
        contact_email="no-reply@accounts.google.com",
    )
    assert looks_like_security_notification(
        "Google <no-reply@accounts.google.com>",
        "Critical security alert",
        contact_email="no-reply@accounts.google.com",
    )
    assert not looks_like_security_notification(
        "buyer@hospital.cl",
        "Cotización equipos",
        contact_email="buyer@hospital.cl",
    )


def test_security_notification_rejects_spoofed_accounts_google_substrings() -> None:
    assert not looks_like_security_notification(
        "no-reply@accounts.google.com.evil.test",
        "Alerta de seguridad",
        contact_email="no-reply@accounts.google.com.evil.test",
    )
    assert not looks_like_security_notification(
        "Google <no-reply@evil.test>",
        "security alert accounts.google.com in body",
        contact_email="no-reply@evil.test",
    )
    assert not looks_like_security_notification(
        "Google <no-reply@evil.test?accounts.google.com>",
        "Alerta de seguridad",
        contact_email="no-reply@evil.test",
    )


def test_cyberday_subject_exact_and_normalized_dash_variants() -> None:
    assert looks_like_cyberday_bulk_campaign_subject(CYBERDAY_CAMPAIGN_SUBJECT)
    variant = CYBERDAY_CAMPAIGN_SUBJECT.replace("—", "–")
    assert looks_like_cyberday_bulk_campaign_subject(variant)
    assert not looks_like_cyberday_bulk_campaign_subject("CYBERDAY promo genérica")


def test_should_keep_visible_suppression_categories_and_cyberday_exception() -> None:
    assert should_keep_visible_despite_suppression(
        "sales@ika.net.br",
        "RE: price",
        category="supplier_reply",
    )
    assert not should_keep_visible_despite_suppression(
        "cliente@hospital.cl",
        CYBERDAY_CAMPAIGN_SUBJECT,
        category="waiting_client",
    )
    assert should_keep_visible_despite_suppression(
        "lhidalgo@ceaf.cl",
        "Remite OC N 26172",
        category="bounce",
    )
    assert should_keep_visible_despite_suppression(
        "serviciodetransferencias@bancochile.cl",
        "FACTURA 6",
        category="unknown_category",
    )


def test_logistics_admin_dhl_domain_and_spanish_cues() -> None:
    assert looks_like_logistics_admin_contact("ops@dhl.com", "Propuesta comercial DHL")
    assert looks_like_logistics_admin_contact(
        "cliente@hospital.cl",
        "Solicitud cuenta importación",
        snippet="cuenta importacion DHL",
    )


def test_cesmec_catalogue_client_spanish_markers() -> None:
    assert looks_like_cesmec_catalogue_client_thread(
        "contact@bureauveritas.com",
        "CESMEC catálogo metrología balances",
        snippet="bureau veritas",
    )


def test_client_equipment_opportunity_unach_hielscher_haystack() -> None:
    assert looks_like_client_equipment_opportunity_thread(
        "susanaalfaro@unach.cl",
        "RE: [RCH-123] Hielscher UIP2000 escalamiento universidad",
        snippet="extracción ultrason",
    )


# --- Characterization: role-classifier helper edges (direct) ----------------------------


def test_idiem_no_reply_and_auto_ack_snippets_match() -> None:
    assert looks_like_idiem_auto_acknowledgement("no-reply@idiem.cl", "Acuse de recibo")
    assert looks_like_idiem_auto_acknowledgement(
        "contacto@idiem.cl",
        "Re: cotización",
        snippet="Hemos recibido su mensaje y lo derivaremos.",
    )
    assert looks_like_idiem_auto_acknowledgement(
        "contacto@idiem.cl",
        "Re: consulta",
        snippet="acuse de recibo de su requerimiento",
    )


def test_idiem_auto_ack_does_not_match_normal_client_quote() -> None:
    assert not looks_like_idiem_auto_acknowledgement(
        "buyer@hospital.cl",
        "Solicitud de cotización equipos de laboratorio",
        snippet="Necesitamos cotizar microscopio y balanza.",
    )


def test_contact_routing_notice_ist_autorespuesta_with_forward_cues() -> None:
    assert looks_like_contact_routing_notice(
        "Autorespuesta: consulta equipos",
        snippet="ist.cl se reenvían automáticamente al contacto sugerido",
    )


def test_contact_routing_notice_not_plain_client_quote_request() -> None:
    assert not looks_like_contact_routing_notice(
        "Solicitud de cotización microscopio",
        snippet="Por favor enviar cotización con plazos de entrega.",
    )


def test_low_intent_client_acknowledgement_thanks_for_information() -> None:
    assert looks_like_low_intent_client_acknowledgement(
        "Re: información",
        snippet="Gracias por la información, la revisaremos.",
    )


def test_low_intent_client_acknowledgement_not_quote_request() -> None:
    assert not looks_like_low_intent_client_acknowledgement(
        "Cotización balanza y microscopio",
        snippet="Solicito cotización formal con precio y stock.",
    )


def test_client_waiting_review_ack_review_phrases() -> None:
    assert looks_like_client_waiting_review_ack("Re: consulta", snippet="Lo revisaremos y responderemos.")
    assert looks_like_client_waiting_review_ack(
        "Re: equipos",
        snippet="Gracias, revisaremos su solicitud.",
    )


def test_client_waiting_review_ack_uc_subject_boundary() -> None:
    uc_subject = "Re: OrigenLab | Equipos para laboratorio"
    assert looks_like_client_waiting_review_ack(
        uc_subject,
        contact_email="compras@uc.cl",
    )
    assert not looks_like_client_waiting_review_ack(
        uc_subject,
        contact_email="buyer@hospital.cl",
    )


def test_unach_hielscher_supplier_wait_rch_thread_matches() -> None:
    assert looks_like_unach_hielscher_supplier_wait(
        "susanaalfaro@unach.cl",
        "RE: [RCH-456] Hielscher UIP2000 UNACH escalamiento",
        snippet="cotización ultrason Hielscher universidad adventista",
    )


def test_unach_without_hielscher_or_supplier_cue_does_not_match() -> None:
    assert not looks_like_unach_hielscher_supplier_wait(
        "susanaalfaro@unach.cl",
        "Consulta general equipos laboratorio UNACH",
        snippet="Necesitamos información sobre microscopios.",
    )


def test_dhl_logistics_account_access_thread_esecure_cues() -> None:
    assert looks_like_dhl_logistics_account_access_thread(
        "ops@dhl.com",
        "Solicitud de acceso eSecure",
        snippet="aprobar o rechazar acceso a cuenta DHL",
    )


def test_suppressed_promotional_marketing_noise_no_reply_promo() -> None:
    assert looks_like_suppressed_promotional_marketing_noise(
        "no-reply@tidio.net",
        "Tidio <no-reply@tidio.net>",
        "🚀 ¡95% de DESCUENTO en Tidio!",
        has_suppression_signal=True,
    )
    assert not looks_like_suppressed_promotional_marketing_noise(
        "no-reply@tidio.net",
        "Tidio <no-reply@tidio.net>",
        "🚀 ¡95% de DESCUENTO en Tidio!",
        has_suppression_signal=False,
    )


# --- Characterization: role classifier precedence (caller integration) ----------------


def _role_row(
    *,
    sender: str,
    subject: str,
    source_file: str = "gmail:contacto@origenlab.cl/INBOX",
    snippet: str | None = None,
    recipients: str | None = None,
) -> dict:
    row: dict = {
        "email_id": 1,
        "sender_preview": sender,
        "subject_preview": subject,
        "source_file": source_file,
    }
    if snippet is not None:
        row["snippet"] = snippet
    if recipients is not None:
        row["recipients_preview"] = recipients
    return row


def test_role_precedence_cyberday_sent_folder_before_supplier_vendor() -> None:
    row = _role_row(
        sender="contacto@origenlab.cl",
        subject=CYBERDAY_CAMPAIGN_SUBJECT,
        source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
    )
    assert (
        infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
        == "campaign_outreach"
    )


def test_role_precedence_internal_forward_before_internal_admin() -> None:
    row = _role_row(
        sender="Tatiana <contacto@labdelivery.cl>",
        subject="RV: Solicitud de Cotización IKA RV10.70 3812200 RG ENERGIA",
        snippet="solicitud de cotizacion cliente externa",
    )
    assert (
        infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
        == "client_opportunity"
    )


def test_role_precedence_system_noise_before_payment_admin() -> None:
    row = _role_row(
        sender="Google <no-reply@accounts.google.com>",
        subject="Alerta de seguridad",
    )
    assert (
        infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
        == "system_noise"
    )


def test_role_precedence_payment_admin_on_ceaf_bank_details() -> None:
    row = _role_row(
        sender="lhidalgo@ceaf.cl",
        subject="Solicita datos Bancarios",
        snippet="factura N°06 y proceder al pago",
    )
    assert (
        infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
        == "payment_admin"
    )


# --- Classification quality regression (2026-06-05 new-day refresh) -------------------


def _role_row_with_signals(
    *,
    sender: str,
    subject: str,
    snippet: str | None = None,
    body_snippet: str | None = None,
    source_file: str = "gmail:contacto@origenlab.cl/INBOX",
    recipients: str | None = None,
    has_positive_signal: int = 0,
    has_suppression_signal: int = 0,
) -> dict:
    row = _role_row(
        sender=sender,
        subject=subject,
        snippet=snippet,
        source_file=source_file,
        recipients=recipients,
    )
    row["has_positive_signal"] = has_positive_signal
    row["has_suppression_signal"] = has_suppression_signal
    if body_snippet is not None:
        row["body_snippet"] = body_snippet
    return row


def test_ollital_customer_feedback_is_supplier_followup_not_quote_received() -> None:
    """710887: Ollital chasing customer feedback is follow-up, not a received quote."""
    row = _role_row_with_signals(
        sender="kelly@ollital.com",
        subject="Re:  Ollital reactor 5L",
        body_snippet="Good morning Tatiana,\nDo you have any feedback from customer?",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "supplier_followup"
    assert role != "supplier_quote_received"


def test_biosys_pre_quote_evaluation_is_supplier_followup_not_client_opportunity() -> None:
    """710889: BIOSYS pre-quote evaluation is vendor follow-up, not client opportunity."""
    row = _role_row_with_signals(
        sender="BIOSYS Scientific Devices <info@biosys.de>",
        subject="BIOSYS online request",
        body_snippet="Before we share further brochures and quotes, we need to evaluate that we are talking about the correct product.",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "supplier_followup"
    assert role != "client_opportunity"


def test_dasitaly_no_production_line_is_supplier_followup_not_client_opportunity() -> None:
    """710881: DAS Italy no-fit production-line reply is vendor follow-up."""
    row = _role_row_with_signals(
        sender="Francesca Rubino <commercial@dasitaly.com>",
        subject="Re: Quotation request",
        body_snippet="thanks for your kind enquiry, but I'm sorry to inform you that it is not in our production-line.",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "supplier_followup"
    assert role != "client_opportunity"


def test_moldev_channel_partner_redirect_is_supplier_followup_not_client_opportunity() -> None:
    """710882: Molecular Devices Chile channel-partner redirect is vendor routing follow-up."""
    row = _role_row_with_signals(
        sender='"Tavormina, Penny" <Penny.Tavormina@moldev.com>',
        subject="RE: Fluorescence Microplate Reader Quotation request",
        body_snippet="We have a channel partner in Chile. You can reach them for local support.",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "supplier_followup"
    assert role != "client_opportunity"


def test_outbound_supplier_request_to_vendor_domain_is_waiting_supplier() -> None:
    """710894/710895: Outbound quote requests to known vendor domains wait on supplier."""
    for recipients, email_id in (
        ("info@dasitaly.com", 710895),
        ("nsd@moldev.com", 710894),
    ):
        row = _role_row_with_signals(
            sender="Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>",
            subject="Quotation request",
            recipients=recipients,
            source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        )
        row["email_id"] = email_id
        role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
        assert role == "waiting_supplier"
        assert role != "waiting_client"


def test_tidio_promo_suppressed_domain_not_client_response_warm_case() -> None:
    """710893: Tidio promo from suppressed no-reply domain must not surface as client_response."""
    row = _role_row_with_signals(
        sender="Tidio <no-reply@tidio.net>",
        subject="🚀 ¡95% de DESCUENTO en Tidio!",
        has_suppression_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role != "client_response"
    assert role == "system_noise"


def test_ciqtek_quotation_specs_classifies_supplier_quote_received() -> None:
    """710890: CIQTEK quotation/spec attachment thread is supplier quote evidence, not client opportunity."""
    row = _role_row_with_signals(
        sender="Laura-CIQTEK <wangq@ciqtek.com>",
        subject="Re: Fwd: 回复: [CIQTEK] Sicope 40",
        snippet="Please find our quotation and specs attached.",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role in {"supplier_quote_received", "supplier_reply", "deal_evidence_candidate"}


def test_ultrassay_usd_price_quote_classifies_supplier_quote_received() -> None:
    """710885/710888: Ultrassay price lines (15300usd/pc) are supplier quotes, not client opportunity."""
    row = _role_row_with_signals(
        sender='"bo@ultrassay.com" <bo@ultrassay.com>',
        subject="Re: Re: Quotation request",
        snippet="Feyond-F100 15300usd/pc and uMP96f 7000usd/pc",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "supplier_quote_received"


def test_ciqtek_live_preview_snippet_classifies_supplier_quote_received() -> None:
    """710890: With top_reply body, CIQTEK spec review classifies as supplier quote received."""
    row = _role_row_with_signals(
        sender="Laura-CIQTEK <wangq@ciqtek.com>",
        subject="Re: Fwd: 回复: [CIQTEK] Sicope 40",
        snippet="Re: Fwd: 回复: [CIQTEK] Sicope 40 · Laura-CIQTEK <wangq@ciqtek.com>",
        body_snippet="We have reviewed the specifications, please find our comments in Yellow.",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "supplier_quote_received"


def test_ultrassay_live_preview_snippet_classifies_supplier_quote_received() -> None:
    """710888: With top_reply body, Ultrassay USD prices classify as supplier quote received."""
    row = _role_row_with_signals(
        sender='"bo@ultrassay.com" <bo@ultrassay.com>',
        subject="Re: Re: Quotation request",
        snippet='Re: Re: Quotation request · "bo@ultrassay.com" <bo@ultrassay.com>',
        body_snippet="Feyond-F100: 15300usd/pc (Ex-work)",
        has_positive_signal=1,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "supplier_quote_received"


def test_delay_dsn_remains_review_only_not_auto_suppressed() -> None:
    """DSN Delay stays in NDR review/monitoring (batch E); never batch-suppression apply."""
    from origenlab_email_pipeline.qa.ndr_review_queue import classify_ndr_candidate

    batch, reason = classify_ndr_candidate(
        proposed_code="bounce_other",
        subject="Delivery Status Notification (Delay)",
        body_blob="still trying",
        multi_recipient_uncertain=False,
    )
    assert batch == "E"
    assert reason == "delay_dsn_excluded"


# --- Characterization: module contract ------------------------------------------------


def test_warm_case_sender_rules_no_streamlit_imports() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "streamlit" not in alias.name.lower()
        elif isinstance(node, ast.ImportFrom):
            assert "streamlit" not in (node.module or "").lower()


@pytest.mark.parametrize("name", _ROLE_CLASSIFICATION_IMPORTS)
def test_role_classification_caller_imports_remain_exported(name: str) -> None:
    import origenlab_email_pipeline.warm_case_sender_rules as mod

    assert hasattr(mod, name), f"missing export for role classifier: {name}"
    assert callable(getattr(mod, name))


def test_is_supplier_vendor_domain_matches_public_frozenset() -> None:
    assert is_supplier_vendor_domain("serva.de")
    assert is_real_client_domain("ceaf.cl")
    assert not is_supplier_vendor_domain("ceaf.cl")
