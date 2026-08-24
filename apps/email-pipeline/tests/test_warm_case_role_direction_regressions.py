"""Real-message regressions for warm-case commercial directionality."""

from __future__ import annotations

from origenlab_email_pipeline.warm_case_classification import (
    infer_next_action,
    infer_warm_case_category,
    infer_warm_case_status,
)
from origenlab_email_pipeline.warm_case_role_classification import (
    infer_warm_case_role_category,
)


def _inbound(
    *,
    sender: str,
    subject: str,
    snippet: str = "",
    body_snippet: str = "",
    positive: bool = True,
) -> dict:
    return {
        "email_id": 1,
        "sender_preview": sender,
        "subject_preview": subject,
        "snippet": snippet,
        "body_snippet": body_snippet,
        "source_file": "gmail:contacto@origenlab.cl/INBOX",
        "has_positive_signal": positive,
        "has_suppression_signal": False,
    }


def _role(row: dict) -> str:
    return infer_warm_case_role_category(
        row,
        enrichment_available=True,
        include_noise=True,
    )


def test_kalstein_discount_blast_is_marketing_noise_not_client_opportunity() -> None:
    row = _inbound(
        sender="Diana Lopez <dianalopez@kalstein.net>",
        subject="Kalstein Plus: disfrute de descuentos exclusivos de entre el 22 % y el 36 %.",
        snippet=(
            "Una de las principales ventajas de formar parte de Kalstein Plus "
            "es acceder a condiciones comerciales diseñadas para aumentar..."
        ),
    )

    assert _role(row) == "system_noise"
    assert (
        infer_warm_case_category(
            row,
            enrichment_available=True,
            include_noise=True,
        )
        == "bounce"
    )


def test_kalstein_supplier_recruitment_is_supplier_followup() -> None:
    row = _inbound(
        sender="Kalstein Sales <sales@kalstein.net>",
        subject="Invitación a Reunión - Colaboración con Kalstein",
        body_snippet=(
            "We are looking for a distributor in Chile and would like "
            "to discuss a commercial partnership."
        ),
    )

    assert _role(row) == "supplier_followup"


def test_post_quote_terse_thanks_waits_for_client_instead_of_open_opportunity() -> None:
    row = _inbound(
        sender="Gonzalo Leyton <gleyton@estudioleyton.com>",
        subject="Re: Cotización Analizador de Humedad ADAM",
        body_snippet="Gracias",
        snippet="Gracias",
    )

    assert _role(row) == "waiting_client"

    category = infer_warm_case_category(
        row,
        enrichment_available=True,
        include_noise=True,
    )
    assert category == "waiting_client"
    assert infer_warm_case_status(category, row) == "waiting"


def test_uc_serva_product_inquiry_is_client_opportunity_not_deal_evidence() -> None:
    row = _inbound(
        sender='"Calidad Agua.ing" <calidadagua.ing@uc.cl>',
        subject="Consulta cotización",
        body_snippet=(
            "Estimados, junto con saludar, me comunico para consultar "
            "si comercializan la solución de silicona SERVA en isopropanol."
        ),
    )

    assert _role(row) == "client_opportunity"


def test_serva_attached_supplier_offer_is_supplier_quote_received() -> None:
    row = _inbound(
        sender="Serva_Order <order@serva.de>",
        subject="AW: Quotation Request / New adress created for your compagny 310471",
        body_snippet=(
            "Dear Tatiana, please find attached our additional offer N260733 "
            "for the positions listed below from your Tender request."
        ),
    )

    assert _role(row) == "supplier_quote_received"

    category = infer_warm_case_category(
        row,
        enrichment_available=True,
        include_noise=True,
    )
    assert category == "supplier_reply"
    assert infer_warm_case_status(category, row) == "open"


def test_apdata_application_worksheet_is_supplier_followup_not_client_lead() -> None:
    row = _inbound(
        sender="Ron Debiaso <rdebiaso@apdataweigh.com>",
        subject="Re: Request for quotation - Dynamic Checkweigher",
        body_snippet=(
            "Please use the attached worksheet to provide as much information "
            "as possible about the product you want to weigh and your process. "
            "I will be happy to configure and quote a system for you."
        ),
    )

    assert _role(row) == "supplier_followup"


def test_future_tender_without_immediate_need_gets_non_urgent_action() -> None:
    row = _inbound(
        sender="Daniela Sepulveda Rojas <daniela.sepulveda@carozzi.cl>",
        subject="RE: [EXTERNO]: Balanzas y Soluciones ADAM Equipment | OrigenLab",
        body_snippet=(
            "Gracias por tu pronta respuesta, en lo inmediato no necesito "
            "ningún servicio, pero sí contarte que nos encontramos en proceso "
            "de licitación para el contrato que gestionamos por 2 o 3 años."
        ),
    )

    category = infer_warm_case_category(
        row,
        enrichment_available=True,
        include_noise=True,
    )

    assert category == "opportunity"

    action = infer_next_action(category, row)
    assert "sin necesidad inmediata" in action.lower()
    assert "licitación" in action.lower()


def test_outbound_apdata_rfq_waits_for_supplier_not_quote_received() -> None:
    row = {
        "email_id": 2,
        "sender_preview": ("Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>"),
        "recipients_preview": "sales@apdataweigh.com",
        "subject_preview": ("Request for quotation - Dynamic Checkweigher"),
        "body_snippet": ("Please quote a dynamic checkweigher for our customer."),
        "source_file": ("gmail:contacto@origenlab.cl/[Gmail]/Enviados"),
        "has_positive_signal": True,
        "has_suppression_signal": False,
    }

    assert _role(row) == "waiting_supplier"


def test_outbound_request_for_quote_waits_for_supplier() -> None:
    row = {
        "email_id": 3,
        "sender_preview": (
            "Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>"
        ),
        "recipients_preview": "sales@apdataweigh.com",
        "subject_preview": "Request for quote - Dynamic Checkweigher",
        "body_snippet": "Please quote this system.",
        "source_file": (
            "gmail:contacto@origenlab.cl/[Gmail]/Enviados"
        ),
        "has_positive_signal": True,
        "has_suppression_signal": False,
    }

    assert _role(row) == "waiting_supplier"
