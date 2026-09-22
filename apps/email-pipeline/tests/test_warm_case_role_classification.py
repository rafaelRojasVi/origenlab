"""Phase 7A regression tests for role-level warm-case classification."""

from __future__ import annotations

from operator_identity_fixture import configured_operator_identity  # noqa: F401
from origenlab_email_pipeline.warm_case_classification import infer_warm_case_role
from origenlab_email_pipeline.warm_case_role_classification import infer_warm_case_role_category
from origenlab_email_pipeline.warm_case_sender_rules import CYBERDAY_CAMPAIGN_SUBJECT


def _row(
    *,
    sender: str,
    subject: str,
    contact_email: str | None = None,
    snippet: str = "",
    has_positive_signal: bool = False,
    source_file: str = "gmail:contacto@origenlab.cl/INBOX",
) -> dict:
    return {
        "email_id": 1,
        "sender_preview": sender,
        "subject_preview": subject,
        "contact_email": contact_email,
        "snippet": snippet,
        "source_file": source_file,
        "has_positive_signal": has_positive_signal,
    }


def test_payments_operator_re_serva_is_internal_admin_not_client_response(
    configured_operator_identity,
) -> None:
    """A SERVA/Wise note from the payments operator is an internal admin thread.

    The rule keys on *which* operator wrote, so it needs a configured identity to fire at
    all. The address below is fictitious; the real one lives outside Git.
    """
    operator = configured_operator_identity.address_for("payments_operator")
    row = _row(
        sender=f"Operador de pagos <{operator}>",
        subject="Re: serva",
        contact_email=operator,
        snippet="Wise transfer note for SERVA invoice",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "internal_admin"
    assert role not in ("client_response", "client_opportunity")


def test_beatriz_ika_quote_is_supplier_quote_received() -> None:
    row = _row(
        sender="Beatriz Bonon <beatriz.bonon@ika.net.br>",
        subject="RE: IKA RV10.70 price response",
        contact_email="beatriz.bonon@ika.net.br",
    )
    role = infer_warm_case_role(row, enrichment_available=False, include_noise=False)
    assert role == "supplier_quote_received"
    assert role != "client_opportunity"


def test_internal_forwarded_rg_energia_quote_is_client_opportunity() -> None:
    row = _row(
        sender="Tatiana Vivanco <contacto@labdelivery.cl>",
        subject="RV: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200// RG ENERGIA SPA",
        contact_email="contacto@labdelivery.cl",
        snippet="Reenvío de solicitud cliente RG ENERGIA SPA para tubo vapor",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "client_opportunity"


def test_ika_autoresponse_is_system_noise_not_supplier_quote() -> None:
    row = _row(
        sender="IKA Brasil <noreply@ika.net.br>",
        subject="RES: Resposta automática: Cotización",
        contact_email="noreply@ika.net.br",
        snippet="Esta é uma resposta automática. Retornaremos em breve.",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "system_noise"
    assert role != "supplier_quote_received"


def test_beatriz_ika_quote_with_price_stays_supplier_quote_received() -> None:
    row = _row(
        sender="Beatriz Bonon <beatriz.bonon@ika.net.br>",
        subject="RES: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200",
        contact_email="beatriz.bonon@ika.net.br",
        snippet="Monto 112,00 — stock disponible para 3 unidades RV10.70",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "supplier_quote_received"


def test_crtop_reactor_quote_is_supplier_quote_received() -> None:
    row = _row(
        sender="Ariel <ariel@crtopmachine.com>",
        subject="Re: Thank you very much for your inquiry about our reactor.",
        contact_email="ariel@crtopmachine.com",
        snippet="CRTOP quotation Lab Reactor OLT-HP-5L, EXW USD 10600",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "supplier_quote_received"


def test_dhl_account_email_is_logistics_admin() -> None:
    row = _row(
        sender="Monica Silva <monica.silva@dhl.com>",
        subject="PROPUESTA COMERCIAL DHL",
        contact_email="monica.silva@dhl.com",
    )
    assert infer_warm_case_role_category(row, enrichment_available=False, include_noise=False) == (
        "logistics_admin"
    )


def test_dhl_esecure_access_request_is_logistics_admin() -> None:
    row = _row(
        sender="DHL eSecure <esecure@dhl.com>",
        subject=(
            "DHL eSecure: acción requerida: aprobar/rechazar la solicitud de acceso "
            "a la cuenta de DHL Express"
        ),
        contact_email="esecure@dhl.com",
        snippet="SERVA customer 310471 mentioned in unrelated footer",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "logistics_admin"
    assert role != "deal_evidence_candidate"


def test_dhl_noreply_one_time_code_is_logistics_admin() -> None:
    row = _row(
        sender="DHL <noreply@dhl.com>",
        subject="Your one-time code for DHL Express account access",
        contact_email="noreply@dhl.com",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "logistics_admin"


def test_bancochile_factura_is_payment_admin() -> None:
    row = _row(
        sender="Banco Chile <serviciodetransferencias@bancochile.cl>",
        subject="FACTURA 6",
        contact_email="serviciodetransferencias@bancochile.cl",
    )
    assert infer_warm_case_role_category(row, enrichment_available=False, include_noise=False) == (
        "payment_admin"
    )


def test_google_security_alert_is_system_noise() -> None:
    row = _row(
        sender="Google <no-reply@accounts.google.com>",
        subject="Alerta de seguridad",
        contact_email="no-reply@accounts.google.com",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "system_noise"
    assert role not in ("client_response", "client_opportunity")


def test_udec_sonicador_reply_is_client_opportunity() -> None:
    row = _row(
        sender="Contacto UdeC <contacto@udec.cl>",
        subject="Re: cotización sonicador ultrasonidos",
        contact_email="contacto@udec.cl",
        has_positive_signal=True,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role in ("client_opportunity", "client_response")
    assert "supplier" not in role


def test_crtop_reactor_followup_is_supplier_followup_not_fresh_quote() -> None:
    row = _row(
        sender="Ariel <ariel@crtopmachine.com>",
        subject="Re: Thank you very much for your inquiry about our reactor.",
        contact_email="ariel@crtopmachine.com",
        snippet="Please send your address to calculate shipping cost to Chile.",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "supplier_followup"
    assert role != "supplier_quote_received"


def test_ortoalresa_solicitud_cotizacion_is_supplier_quote_received() -> None:
    row = _row(
        sender="Carmen Llorente <carmen.llorente@ortoalresa.com>",
        subject="RE: Solicitud de cotización",
        contact_email="carmen.llorente@ortoalresa.com",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "supplier_quote_received"
    assert role != "supplier_followup"


def test_serva_quotation_request_company_310471_is_deal_evidence() -> None:
    row = _row(
        sender="Serva_Order <order@serva.de>",
        subject="AW: Quotation Request / New adress created for your compagny 310471",
        contact_email="order@serva.de",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "deal_evidence_candidate"
    assert role != "logistics_admin"
    assert role != "supplier_followup"


def test_unach_hielscher_thread_is_waiting_supplier_not_default_warm() -> None:
    row = _row(
        sender="Marcos Acevedo <marcos.a@hielscher.com>",
        subject="[RCH-Universidad Adventista de Chile] Hielscher Ultrasonics: Su solicitud sobre el UIP2000hdT",
        contact_email="susanaalfaro@unach.cl",
        snippet="extracción vegetal asistida por ultrasonido, escalamiento 30-50 L",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "waiting_supplier"


def test_francisca_uc_lo_revisaremos_is_waiting_client() -> None:
    row = _row(
        sender="Francisca Echeverria <franciscaecheverria@uc.cl>",
        subject="RE: OrigenLab - Equipos para Laboratorio",
        contact_email="franciscaecheverria@uc.cl",
        snippet="Muchas gracias, lo revisaremos",
    )
    assert (
        infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
        == "waiting_client"
    )
    row_no_snippet = _row(
        sender="Francisca Echeverria <franciscaecheverria@uc.cl>",
        subject="RE: OrigenLab - Equipos para Laboratorio",
        contact_email="franciscaecheverria@uc.cl",
        snippet="",
    )
    role = infer_warm_case_role_category(row_no_snippet, enrichment_available=False, include_noise=False)
    assert role == "waiting_client"


def test_ongo_up400st_sent_quote_is_quote_sent() -> None:
    row = _row(
        sender="Tatiana Vivanco <contacto@origenlab.cl>",
        subject="Cotización Sonicador UP400St",
        contact_email="hola@ongo.cl",
        snippet="Adjunto cotización UP400St",
        source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
    )
    row["recipients_preview"] = "ONGO Lab <hola@ongo.cl>"
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "quote_sent"


def test_adriana_thanks_for_info_is_client_response_not_opportunity() -> None:
    row = _row(
        sender="Adriana Roman Tapia <adrianaromantapia@gmail.com>",
        subject="Re: OrigenLab - Equipos para Laboratorio",
        contact_email="adrianaromantapia@gmail.com",
        snippet="Gracias por su información",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "client_response"
    assert role != "client_opportunity"


def test_ist_autorespuesta_is_system_noise() -> None:
    row = _row(
        sender="Alfredo Valdebenito <alfredo.valdebenito@ist.cl>",
        subject="autorespuesta",
        contact_email="alfredo.valdebenito@ist.cl",
        snippet="Los correos se reenvían a Sebastian Cornejo Vargas sebastian.cornejov@ist.cl",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "system_noise"


def test_mailer_daemon_bounce_is_bounce_problem() -> None:
    row = _row(
        sender="Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        subject="Delivery Status Notification (Failure)",
        contact_email="mailer-daemon@googlemail.com",
        snippet="550 5.1.1 User unknown astaudt@chacoicsa.com",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "bounce_problem"


def test_cyberday_sent_email_is_campaign_outreach_not_waiting_client() -> None:
    row = _row(
        sender="Tatiana Vivanco <contacto@origenlab.cl>",
        subject=CYBERDAY_CAMPAIGN_SUBJECT,
        contact_email="lab@example.cl",
        source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
    )
    row["recipients_preview"] = "Lab Example <lab@example.cl>"
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "campaign_outreach"
    assert role != "waiting_client"


def test_cyberday_bounced_suppressed_sent_still_campaign_outreach() -> None:
    """Sent-folder campaign copy stays campaign_outreach; bounce NDR is a separate row."""
    row = _row(
        sender="Tatiana Vivanco <contacto@origenlab.cl>",
        subject=CYBERDAY_CAMPAIGN_SUBJECT,
        contact_email="bounce@example.cl",
        source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        snippet="",
    )
    row["recipients_preview"] = "Bounce <bounce@example.cl>"
    row["has_suppression_signal"] = True
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "campaign_outreach"


def test_cyberday_inbound_ndr_with_suppression_is_bounce_problem() -> None:
    row = _row(
        sender="Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        subject=CYBERDAY_CAMPAIGN_SUBJECT,
        contact_email="mailer-daemon@googlemail.com",
        snippet="550 5.1.1 User unknown",
        source_file="gmail:contacto@origenlab.cl/INBOX",
    )
    row["has_suppression_signal"] = True
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "bounce_problem"


def test_idiem_auto_ack_is_auto_acknowledgement() -> None:
    row = _row(
        sender="IDIEM <contacto@idiem.cl>",
        subject="Re: CYBERDAY — equipos de laboratorio",
        contact_email="contacto@idiem.cl",
        snippet="Hemos recibido su mensaje. Acuse de recibo automático.",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "auto_acknowledgement"


def test_cesmec_stays_visible_as_client_opportunity() -> None:
    row = _row(
        sender="Juan Pablo García <juan-pablo.garcia@bureauveritas.com>",
        subject="Re: Catálogo equipos laboratorio CESMEC",
        contact_email="juan-pablo.garcia@bureauveritas.com",
        snippet="Solicitud catálogo metrología balances CESMEC",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "client_opportunity"


# --- E2E characterization: sender-rule helper edges (role classifier) -------------------


def test_idiem_no_reply_is_auto_acknowledgement_not_client_opportunity() -> None:
    row = _row(
        sender="IDIEM <no-reply@idiem.cl>",
        subject="Acuse de recibo",
        contact_email="no-reply@idiem.cl",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "auto_acknowledgement"
    assert role not in ("client_opportunity", "client_response", "system_noise")


def test_ist_autorespuesta_routing_notice_not_client_or_supplier_opportunity() -> None:
    row = _row(
        sender="Alfredo Valdebenito <alfredo.valdebenito@ist.cl>",
        subject="Autorespuesta: consulta equipos",
        contact_email="alfredo.valdebenito@ist.cl",
        snippet="ist.cl se reenvían automáticamente al contacto sugerido",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "system_noise"
    assert role not in ("client_opportunity", "supplier_quote_received", "supplier_followup")


def test_gracias_por_la_informacion_is_client_response_not_opportunity() -> None:
    row = _row(
        sender="Cliente Externo <cliente@gmail.com>",
        subject="Re: información equipos",
        contact_email="cliente@gmail.com",
        snippet="Gracias por la información, saludos cordiales.",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "client_response"
    assert role != "client_opportunity"


def test_formal_cotizacion_request_is_client_opportunity_not_low_intent_response() -> None:
    row = _row(
        sender="Buyer <buyer@hospital.cl>",
        subject="Cotización balanza y microscopio",
        contact_email="buyer@hospital.cl",
        snippet="Solicito cotización formal con precio y stock.",
        has_positive_signal=True,
    )
    role = infer_warm_case_role_category(row, enrichment_available=True, include_noise=False)
    assert role == "client_opportunity"
    assert role != "client_response"


def test_uc_pipe_subject_without_snippet_is_waiting_client() -> None:
    row = _row(
        sender="Francisca Echeverria <franciscaecheverria@uc.cl>",
        subject="Re: OrigenLab | Equipos para laboratorio",
        contact_email="franciscaecheverria@uc.cl",
        snippet="",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "waiting_client"


def test_non_uc_pipe_subject_not_waiting_client() -> None:
    row = _row(
        sender="Buyer <buyer@hospital.cl>",
        subject="Re: OrigenLab | Equipos para laboratorio",
        contact_email="buyer@hospital.cl",
        snippet="",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role != "waiting_client"
    assert role not in ("client_opportunity", "supplier_quote_received")


def test_unach_rch_hielscher_from_client_sender_is_waiting_supplier() -> None:
    row = _row(
        sender="Susana Alfaro <susanaalfaro@unach.cl>",
        subject="RE: [RCH-456] Hielscher UIP2000 UNACH escalamiento",
        contact_email="susanaalfaro@unach.cl",
        snippet="cotización ultrason Hielscher universidad adventista",
    )
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "waiting_supplier"
    assert role not in ("client_opportunity", "supplier_quote_received")


def test_tidio_suppressed_promo_is_system_noise_not_client_response() -> None:
    row = _row(
        sender="Tidio <no-reply@tidio.net>",
        subject="🚀 ¡95% de DESCUENTO en Tidio!",
        contact_email="no-reply@tidio.net",
    )
    row["has_suppression_signal"] = True
    role = infer_warm_case_role_category(row, enrichment_available=False, include_noise=False)
    assert role == "system_noise"
    assert role not in ("client_response", "client_opportunity")
