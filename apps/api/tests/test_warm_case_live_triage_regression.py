"""Live Gmail triage regressions for warm-case response normalization."""

from __future__ import annotations

from origenlab_api.schemas.cases import WarmCaseItem
from origenlab_api.services.warm_case_output_normalize import (
    normalize_warm_case_item,
    resolve_normalized_category,
)

_CONTACTO_SENT = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"


def _item(
    *,
    contact_email: str,
    subject: str,
    category: str = "client_response",
    status: str = "new",
    snippet: str = "",
    source_file: str = "gmail:contacto@origenlab.cl/INBOX",
) -> WarmCaseItem:
    return WarmCaseItem(
        case_id="gmail-contacto-live-regression",
        last_email_id=1,
        last_seen_at="2026-07-02T11:42:00-04:00",
        account_name="Live regression",
        contact_email=contact_email,
        subject=subject,
        category=category,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        next_action="old",
        equipment_signal="",
        snippet=snippet,
        source_file=source_file,
        gmail_url=None,
    )


def test_tidio_weekly_report_hidden_from_default_warm_queue() -> None:
    raw = _item(
        contact_email="no-reply@reports.tidioreply.com",
        subject="Tu semana con Tidio",
        snippet="TIDIO Informe de 22 – 28.06.2026 Proyecto: origenlab.cl",
    )

    assert resolve_normalized_category(raw) == "system_noise"
    assert normalize_warm_case_item(raw, include_noise=False) is None

    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category == "system_noise"
    assert shown.status == "problem"


def test_tidio_tooling_notification_hidden_from_default_warm_queue() -> None:
    raw = _item(
        contact_email="no-reply@tidio.net",
        subject="Lyro ha respondido con éxito la primera pregunta",
        snippet="TIDIO Lyro ha respondido con éxito la primera consulta de un cliente.",
    )

    assert resolve_normalized_category(raw) == "system_noise"
    assert normalize_warm_case_item(raw, include_noise=False) is None

    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category == "system_noise"


def test_google_workspace_product_update_hidden_from_default_warm_queue() -> None:
    raw = _item(
        contact_email="workspace-noreply@google.com",
        subject="Nuevas herramientas de IA para crear videos y mucho más",
        snippet="Impulsa la creatividad y la productividad con nuevas herramientas de Google Workspace.",
    )

    assert resolve_normalized_category(raw) == "system_noise"
    assert normalize_warm_case_item(raw, include_noise=False) is None

    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category == "system_noise"


def test_hinotek_offer_is_supplier_quote_not_client_response() -> None:
    raw = _item(
        contact_email="southamerican@hinotek.com",
        subject="RE: HINOTEK AMR-100 HG302-CL-ORI01(3477.0)",
        snippet="Please kindly review the offer HG302-CL-ORI01(3477.0). We are looking forward to your reply.",
    )

    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "supplier_quote_received"
    assert out.status == "open"
    assert "proveedor" in out.next_action.lower()


def test_aurora_outbound_supplier_thread_waits_on_supplier() -> None:
    raw = _item(
        contact_email="hr.moon@aurorabiomed.com",
        subject="Re: Equipos Analiticos y Automatizacion: Aurora",
        category="waiting_client",
        status="waiting",
        snippet="I am working with my clients and projects. There is a public tender.",
        source_file=_CONTACTO_SENT,
    )

    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "waiting_supplier"
    assert out.status == "waiting"
    assert "proveedor" in out.next_action.lower()
