"""Warm-case response normalization (audit-driven, read-only)."""

# ruff: noqa: E402
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.services.warm_case_output_normalize import (
    dedupe_warm_case_items,
    filter_positive_normalized_items,
    is_auto_reply_subject,
    normalize_warm_case_item,
    normalize_warm_case_items,
    resolve_normalized_category,
)
from origenlab_api.schemas.cases import WarmCaseItem
from origenlab_api.settings import Settings

_CONTACTO_INBOX = "gmail:contacto@origenlab.cl/INBOX"
_CONTACTO_SENT = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"


def _recent_iso(days_ago: int, time_part: str = "12:00:00-04:00") -> str:
    return f"{date.today() - timedelta(days=days_ago)}T{time_part}"


def _item(
    *,
    contact_email: str,
    subject: str,
    category: str = "client_reply",
    status: str = "new",
    snippet: str = "",
    account_name: str = "Test",
) -> WarmCaseItem:
    return WarmCaseItem(
        case_id="gmail-contacto-1",
        last_email_id=1,
        last_seen_at="2026-05-22T10:00:00-04:00",
        account_name=account_name,
        contact_email=contact_email,
        subject=subject,
        category=category,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        next_action="old",
        equipment_signal="",
        snippet=snippet,
        gmail_url=None,
    )


def test_auto_reply_subject_detection() -> None:
    assert is_auto_reply_subject("Automatic reply: Quotation Request")
    assert is_auto_reply_subject("Out of office until Monday")
    assert is_auto_reply_subject("RES: Resposta automática: Cotización")
    assert is_auto_reply_subject("Autorespuesta: fuera de oficina")
    assert is_auto_reply_subject("Automatische Antwort: Anfrage")


def test_ika_autoresponse_downgrades_stored_supplier_quote() -> None:
    raw = _item(
        contact_email="noreply@ika.net.br",
        subject="RES: Resposta automática: Cotización",
        category="supplier_quote_received",
        snippet="Resposta automática — retornaremos em breve.",
    )
    assert normalize_warm_case_item(raw, include_noise=False) is None
    out = normalize_warm_case_item(raw, include_noise=True)
    assert out is not None
    assert out.category == "system_noise"


def test_crtop_duplicate_rows_collapsed_with_grouped_count() -> None:
    rows = [
        _item(
            contact_email="ariel@crtopmachine.com",
            subject="Re: Thank you very much for your inquiry about our reactor.",
            category="supplier_quote_received",
            snippet="CRTOP quotation OLT-HP-5L EXW USD 10600",
        ).model_copy(
            update={"last_email_id": 10, "last_seen_at": "2026-05-18T10:00:00Z"}
        ),
        _item(
            contact_email="ariel@crtopmachine.com",
            subject="Re: Thank you very much for your inquiry about our reactor.",
            category="supplier_quote_received",
            snippet="Follow-up on reactor quote",
        ).model_copy(
            update={"last_email_id": 11, "last_seen_at": "2026-05-19T10:00:00Z"}
        ),
    ]
    merged = dedupe_warm_case_items(
        [normalize_warm_case_item(r) for r in rows if normalize_warm_case_item(r)]
    )
    assert len(merged) == 1
    assert merged[0].last_email_id == 11
    assert merged[0].grouped_email_count == 2


def test_mail_delivery_subsystem_bounce_hidden_by_default() -> None:
    raw = _item(
        contact_email="mailer-daemon@googlemail.com",
        subject="Delivery Status Notification (Failure)",
        category="bounce",
        account_name="Mail Delivery Subsystem",
    )
    assert normalize_warm_case_item(raw, include_noise=False) is None
    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category == "bounce_problem"


def test_normalize_dhl_vendor_logistics() -> None:
    raw = _item(contact_email="monica.silva@dhl.com", subject="PROPUESTA COMERCIAL DHL")
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "logistics_admin"
    assert out.category != "client_response"
    assert "logística" in out.next_action.lower()


def test_normalize_dlab_supplier() -> None:
    raw = _item(contact_email="chloe.yang@dlabsci.com", subject="DLAB visit reply")
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category in (
        "supplier_reply",
        "supplier_followup",
        "supplier_quote_received",
    )
    assert "proveedor" in out.next_action.lower()


def test_normalize_crtopmachine_supplier() -> None:
    raw = _item(contact_email="ariel@crtopmachine.com", subject="Re: reactor inquiry")
    assert resolve_normalized_category(raw) in (
        "supplier_followup",
        "supplier_quote_received",
    )


def test_normalize_keeps_rg_energia_operator_safe_summary() -> None:
    raw = _item(
        contact_email="contacto@labdelivery.cl",
        subject="RV: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200// RG ENERGIA SPA",
        category="opportunity",
    )
    raw = raw.model_copy(
        update={
            "next_action": (
                "Cliente solicita 3 tubos de vapor IKA RV10.70. "
                "Proveedor IKA respondió precio 112,00 y stock disponible. "
                "Falta confirmar moneda y despacho."
            )
        }
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert "RV10.70" in out.next_action
    assert "confirmar moneda y despacho" in out.next_action.lower()


def test_normalize_keeps_crtop_operator_safe_summary() -> None:
    raw = _item(
        contact_email="ariel@crtopmachine.com",
        subject="Re: Thank you very much for your inquiry about our reactor.",
        category="supplier_reply",
    )
    raw = raw.model_copy(
        update={
            "next_action": (
                "Proveedor CRTOP envió cotización de reactor OLT-HP-5L por US$10,600 EXW. "
                "Falta shipping y costos de importación antes de cotizar al cliente."
            )
        }
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert "CRTOP" in out.next_action
    assert "US$10,600 EXW" in out.next_action


def test_normalize_redacts_bank_and_rut_from_snippet() -> None:
    raw = _item(
        contact_email="ariel@crtopmachine.com",
        subject="Re: reactor inquiry",
        snippet="Beneficiario ACME Spa, SWIFT ABCDCLRMXXX, cuenta corriente 123456789012 y RUT 12.345.678-9",
        category="supplier_reply",
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert "swift" not in out.snippet.lower()
    assert "12.345.678-9" not in out.snippet
    assert "123456789012" not in out.snippet


def test_normalize_asynt_supplier() -> None:
    raw = _item(contact_email="sales@asynt.com", subject="Re: reactor specs")
    assert resolve_normalized_category(raw) in (
        "supplier_followup",
        "supplier_quote_received",
    )


def test_normalize_serva_auto_reply_hidden_by_default() -> None:
    raw = _item(
        contact_email="order@serva.de",
        subject="Automatic reply: Quotation Request",
        category="supplier_reply",
    )
    assert normalize_warm_case_item(raw, include_noise=False) is None
    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category in ("system_noise", "internal_admin")


def test_normalize_banco_payment_admin() -> None:
    raw = _item(contact_email="x@bancochile.cl", subject="FACTURA 6")
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "payment_admin"
    assert "pago" in out.next_action.lower()


def test_normalize_ollital_and_ortoalresa_stay_supplier() -> None:
    oll = _item(contact_email="kelly@ollital.com", subject="Re: Ollital reactor 5L")
    out = normalize_warm_case_item(oll)
    assert out is not None
    assert out.category in (
        "supplier_reply",
        "supplier_followup",
        "supplier_quote_received",
    )

    orto = _item(
        contact_email="carmen.llorente@ortoalresa.com",
        subject="RE: Cotizar Centrifuga",
        category="supplier_reply",
    )
    assert normalize_warm_case_item(orto) is not None
    assert normalize_warm_case_item(orto).category in (
        "supplier_reply",
        "supplier_quote_received",
        "supplier_followup",
    )


def test_normalize_google_security_hidden_from_default() -> None:
    raw = _item(
        contact_email="no-reply@accounts.google.com",
        subject="Alerta de seguridad",
        category="client_reply",
    )
    assert normalize_warm_case_item(raw, include_noise=False) is None
    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category in ("system_noise", "internal_admin")


def test_normalize_eppendorf_registration_supplier() -> None:
    raw = _item(
        contact_email="eppendorf@eppendorf.com",
        subject="Please confirm your registration!",
        category="client_reply",
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category in (
        "supplier_reply",
        "supplier_followup",
        "supplier_quote_received",
    )


def test_normalize_valuenindustrial_sales_supplier() -> None:
    raw = _item(
        contact_email="sales@valuenindustrial.com",
        subject="Promo",
        category="client_reply",
    )
    assert resolve_normalized_category(raw) in (
        "supplier_followup",
        "supplier_quote_received",
    )


def test_normalize_gzfanbolun_sales_supplier() -> None:
    raw = _item(
        contact_email="sales001@gzfanbolun.com",
        subject="Offer",
        category="client_reply",
    )
    assert resolve_normalized_category(raw) in (
        "supplier_followup",
        "supplier_quote_received",
    )


def test_normalize_yuanhuai_supplier() -> None:
    raw = _item(
        contact_email="jizhendong@yuanhuai.com",
        subject="YHCHEM line",
        category="client_reply",
    )
    assert resolve_normalized_category(raw) in (
        "supplier_followup",
        "supplier_quote_received",
    )


def test_internal_contacto_waiting_client_hidden_by_default() -> None:
    raw = _item(
        contact_email="contacto@origenlab.cl",
        subject="Re: Quotation Request / New adress created for your compagny 310471",
        category="waiting_client",
    )
    assert normalize_warm_case_item(raw, include_noise=False) is None
    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category in ("system_noise", "internal_admin")


def test_bancochile_factura_payment_admin() -> None:
    raw = _item(
        contact_email="serviciodetransferencias@bancochile.cl", subject="FACTURA 6"
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "payment_admin"


def test_dhl_import_account_vendor_logistics() -> None:
    raw = _item(
        contact_email="monica.silva@dhl.com",
        subject="Solicitud cuenta importación",
        category="client_reply",
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "logistics_admin"


def test_ceaf_oc_thread_is_deal_evidence_candidate() -> None:
    raw = _item(
        contact_email="cgaray@ceaf.cl",
        subject="Remite OC N º 26172",
        category="waiting_supplier",
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "deal_evidence_candidate"


def test_ceaf_bank_details_payment_admin_open() -> None:
    raw = _item(
        contact_email="lhidalgo@ceaf.cl",
        subject="Solicita datos Bancarios",
        category="client_reply",
        status="problem",
        snippet="factura N°06 y proceder al pago; registrarla en nuestro sistema",
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "payment_admin"
    assert out.status == "open"
    assert "datos bancarios" in out.next_action.lower()
    assert "no cotizar" in out.next_action.lower()


def test_ceaf_bank_details_subject_only_payment_admin() -> None:
    raw = _item(
        contact_email="lhidalgo@ceaf.cl",
        subject="Solicita datos Bancarios",
        category="waiting_client",
        status="problem",
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "payment_admin"
    assert out.status == "open"


def test_post_normalize_positive_keeps_payment_and_logistics() -> None:
    rows = [
        _item(
            contact_email="serviciodetransferencias@bancochile.cl", subject="FACTURA 6"
        ),
        _item(contact_email="monica.silva@dhl.com", subject="PROPUESTA COMERCIAL DHL"),
        _item(
            contact_email="no-reply@accounts.google.com", subject="Alerta de seguridad"
        ),
    ]
    normalized = normalize_warm_case_items(rows, positive_signal_only=True)
    emails = {i.contact_email for i in normalized}
    assert "serviciodetransferencias@bancochile.cl" in emails
    assert "monica.silva@dhl.com" in emails
    assert "no-reply@accounts.google.com" not in emails


def test_filter_positive_normalized_items() -> None:
    banco = normalize_warm_case_item(
        _item(
            contact_email="serviciodetransferencias@bancochile.cl", subject="FACTURA 1"
        )
    )
    assert banco is not None
    kept = filter_positive_normalized_items(
        [
            banco,
            _item(
                contact_email="no-reply@accounts.google.com",
                subject="Alerta de seguridad",
                category="bounce",  # type: ignore[arg-type]
            ),
        ]
    )
    assert len(kept) == 1
    assert kept[0].category == "payment_admin"


def test_quote_sent_preserved_for_external_customer_thread() -> None:
    raw = _item(
        contact_email="client@hospital.cl",
        subject="Re: Solicitud de cotización",
        category="quote_sent",
    )
    out = normalize_warm_case_item(raw)
    assert out is not None
    assert out.category == "quote_sent"


def _warm_client(tmp_path: Path, rows: list[tuple]) -> TestClient:
    db = tmp_path / "t.sqlite"
    active = tmp_path / "current"
    active.mkdir(parents=True)
    (active / "manifest.json").write_text(
        json.dumps({"canonical_files": [], "campaign_mode": "equipment_first"}),
        encoding="utf-8",
    )
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            date_iso TEXT,
            source_file TEXT,
            folder TEXT,
            sender TEXT,
            subject TEXT
        )
        """
    )
    for row in rows:
        conn.execute(
            "INSERT INTO emails (date_iso, source_file, folder, sender, subject) VALUES (?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()
    conn.close()
    app = create_app()
    from origenlab_api.settings import get_settings

    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=db,
        active_current=active,
    )
    return TestClient(app)


def test_cases_warm_api_normalizes_audit_samples(tmp_path: Path) -> None:
    rows = [
        (
            _recent_iso(2, "16:09:00-04:00"),
            _CONTACTO_INBOX,
            "INBOX",
            "Monica Silva <monica.silva@dhl.com>",
            "PROPUESTA COMERCIAL DHL",
        ),
        (
            _recent_iso(2, "16:31:00-04:00"),
            _CONTACTO_INBOX,
            "INBOX",
            "chloe.yang@dlabsci.com",
            "DLAB catalogue visit",
        ),
        (
            _recent_iso(2, "15:47:00-04:00"),
            _CONTACTO_INBOX,
            "INBOX",
            "Serva_Order <order@serva.de>",
            "Automatic reply: Quotation Request",
        ),
        (
            _recent_iso(2, "11:34:00-04:00"),
            _CONTACTO_INBOX,
            "INBOX",
            "serviciodetransferencias@bancochile.cl",
            "FACTURA 6",
        ),
        (
            _recent_iso(2, "22:14:00-04:00"),
            _CONTACTO_INBOX,
            "INBOX",
            "Kelly <kelly@ollital.com>",
            "Re: Ollital reactor 5L",
        ),
        (
            _recent_iso(3, "18:19:00-04:00"),
            _CONTACTO_INBOX,
            "INBOX",
            "Carmen Llorente <carmen.llorente@ortoalresa.com>",
            "RE: Cotizar Centrifuga",
        ),
        (
            _recent_iso(3, "16:14:00-04:00"),
            _CONTACTO_SENT,
            "[Gmail]/Enviados",
            "contacto@origenlab.cl",
            "Re: Solicitud de cotización Virbac",
        ),
    ]
    client = _warm_client(tmp_path, rows)
    data = client.get("/cases/warm?positive_signal_only=false&limit=50").json()
    by_email = {i["contact_email"].lower(): i for i in data["items"]}

    assert by_email["monica.silva@dhl.com"]["category"] == "logistics_admin"
    assert by_email["chloe.yang@dlabsci.com"]["category"] in (
        "supplier_reply",
        "supplier_followup",
    )
    assert "order@serva.de" not in by_email
    assert (
        by_email["serviciodetransferencias@bancochile.cl"]["category"]
        == "payment_admin"
    )
    assert by_email["kelly@ollital.com"]["category"] in (
        "supplier_reply",
        "supplier_quote_received",
        "supplier_followup",
    )
    assert by_email["carmen.llorente@ortoalresa.com"]["category"] in (
        "supplier_reply",
        "supplier_quote_received",
    )
    assert "contacto@origenlab.cl" not in by_email


def test_cyberday_campaign_hidden_from_default_warm_queue() -> None:
    from origenlab_email_pipeline.warm_case_sender_rules import (
        CYBERDAY_CAMPAIGN_SUBJECT,
    )

    raw = _item(
        contact_email="lab@example.cl",
        subject=CYBERDAY_CAMPAIGN_SUBJECT,
        category="waiting_client",
        status="waiting",
    )
    assert normalize_warm_case_item(raw, include_noise=False) is None
    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category == "campaign_outreach"


def test_idiem_auto_ack_hidden_by_default() -> None:
    raw = _item(
        contact_email="contacto@idiem.cl",
        subject="Re: consulta equipos",
        category="client_reply",
        snippet="Hemos recibido su mensaje. Acuse automático IDIEM.",
    )
    assert normalize_warm_case_item(raw, include_noise=False) is None
    shown = normalize_warm_case_item(raw, include_noise=True)
    assert shown is not None
    assert shown.category == "auto_acknowledgement"


def test_cesmec_client_opportunity_stays_in_positive_filter() -> None:
    raw = _item(
        contact_email="juan-pablo.garcia@bureauveritas.com",
        subject="Re: Catálogo equipos laboratorio CESMEC",
        category="opportunity",
        snippet="CESMEC metrología balances",
    )
    out = normalize_warm_case_item(raw, include_noise=False)
    assert out is not None
    assert out.category == "client_opportunity"
    assert out in filter_positive_normalized_items([out])


def test_mirror_role_category_supplier_quote_received_preserved() -> None:
    raw = _item(
        contact_email="beatriz.bonon@ika.net.br",
        subject="RES: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200",
        category="supplier_quote_received",
        status="open",
    )
    out = normalize_warm_case_item(raw, include_noise=False)
    assert out is not None
    assert out.category == "supplier_quote_received"
    assert resolve_normalized_category(raw) == "supplier_quote_received"


def test_legacy_supplier_reply_still_re_infers_role() -> None:
    raw = _item(
        contact_email="beatriz.bonon@ika.net.br",
        subject="RES: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200",
        category="supplier_reply",
        status="open",
        snippet="Monto 112,00 — stock disponible para 3 unidades RV10.70",
    )
    assert resolve_normalized_category(raw) in (
        "supplier_followup",
        "supplier_quote_received",
    )


def test_category_filter_supplier_quote_received_from_mirror() -> None:
    quote = _item(
        contact_email="beatriz.bonon@ika.net.br",
        subject="RES: Cotización IKA RV10.70",
        category="supplier_quote_received",
        status="open",
    )
    followup = _item(
        contact_email="ariel@crtopmachine.com",
        subject="Re: reactor shipping",
        category="supplier_followup",
        status="open",
    )
    filtered = normalize_warm_case_items(
        [quote, followup],
        include_noise=False,
        category_filter="supplier_quote_received",
    )
    assert len(filtered) == 1
    assert filtered[0].category == "supplier_quote_received"


def test_mirror_waiting_supplier_preserved() -> None:
    raw = _item(
        contact_email="info@ultrassay.com",
        subject="Quotation request",
        category="waiting_supplier",
        status="waiting",
    )
    out = normalize_warm_case_item(raw, include_noise=False)
    assert out is not None
    assert out.category == "waiting_supplier"
    assert resolve_normalized_category(raw) == "waiting_supplier"


def test_mirror_waiting_client_preserved() -> None:
    raw = _item(
        contact_email="franciscaecheverria@uc.cl",
        subject="RE: OrigenLab - Equipos para Laboratorio",
        category="waiting_client",
        status="waiting",
        snippet="Lo revisaré y le comento.",
    )
    out = normalize_warm_case_item(raw, include_noise=False)
    assert out is not None
    assert out.category == "waiting_client"


def test_mirror_quote_sent_preserved() -> None:
    raw = _item(
        contact_email="buyer@client.cl",
        subject="Re: Cotización equipo",
        category="quote_sent",
        status="quoted",
    )
    out = normalize_warm_case_item(raw, include_noise=False)
    assert out is not None
    assert out.category == "quote_sent"
    assert out.status == "quoted"


def test_legacy_supplier_reply_still_re_infers_to_followup() -> None:
    raw = _item(
        contact_email="ariel@crtopmachine.com",
        subject="Re: Thank you very much for your inquiry about our reactor.",
        category="supplier_reply",
        status="open",
        snippet="Please send your address to calculate shipping cost to Chile.",
    )
    assert resolve_normalized_category(raw) == "supplier_followup"


def test_live_serva_offer_normalizes_to_supplier_quote_received() -> None:
    raw = _item(
        contact_email="order@serva.de",
        subject=("AW: Quotation Request / New adress created for your compagny 310471"),
        category="supplier_reply",
    ).model_copy(
        update={
            "body_snippet": (
                "Dear Tatiana, please find attached our additional "
                "offer N260733 for the positions listed below."
            ),
            "sender_preview": "Serva_Order <order@serva.de>",
            "source_file": "gmail:contacto@origenlab.cl/INBOX",
        }
    )

    out = normalize_warm_case_item(raw)

    assert out is not None
    assert out.category == "supplier_quote_received"
    assert "cotización de proveedor recibida" in out.next_action.lower()


def test_live_serva_outbound_rfq_normalizes_to_waiting_supplier() -> None:
    raw = _item(
        contact_email="order@serva.de",
        subject=("Re: Quotation Request / New adress created for your compagny 310471"),
        category="waiting_supplier",
    ).model_copy(
        update={
            "sender_preview": ("Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>"),
            "recipients_preview": "order@serva.de",
            "source_file": ("gmail:contacto@origenlab.cl/[Gmail]/Enviados"),
        }
    )

    out = normalize_warm_case_item(raw)

    assert out is not None
    assert out.category == "waiting_supplier"
    assert out.status == "waiting"


def test_live_uc_serva_inquiry_keeps_targeted_action() -> None:
    raw = _item(
        contact_email="calidadagua.ing@uc.cl",
        subject="Consulta cotización",
        category="opportunity",
    ).model_copy(
        update={
            "body_snippet": (
                "Me comunico para consultar si comercializan la "
                "solución de silicona SERVA en isopropanol."
            ),
            "sender_preview": ('"Calidad Agua.ing" <calidadagua.ing@uc.cl>'),
            "source_file": "gmail:contacto@origenlab.cl/INBOX",
            "next_action": (
                "Cliente consulta producto SERVA; confirmar "
                "formato/cantidad y preparar cotización."
            ),
        }
    )

    out = normalize_warm_case_item(raw)

    assert out is not None
    assert out.category == "client_opportunity"
    assert "producto serva" in out.next_action.lower()
    assert "preparar cotización" in out.next_action.lower()


def test_live_carozzi_future_tender_keeps_nonurgent_action() -> None:
    raw = _item(
        contact_email="daniela.sepulveda@carozzi.cl",
        subject=("RE: [EXTERNO]: Balanzas y Soluciones ADAM Equipment | OrigenLab"),
        category="opportunity",
    ).model_copy(
        update={
            "body_snippet": (
                "En lo inmediato no necesito ningún servicio, "
                "pero nos encontramos en proceso de licitación "
                "para el contrato que gestionamos por 2 o 3 años."
            ),
            "sender_preview": (
                "Daniela Sepulveda Rojas <daniela.sepulveda@carozzi.cl>"
            ),
            "source_file": "gmail:contacto@origenlab.cl/INBOX",
            "next_action": (
                "Sin necesidad inmediata; registrar la licitación "
                "futura y hacer seguimiento comercial cuando se "
                "acerque el proceso."
            ),
        }
    )

    out = normalize_warm_case_item(raw)

    assert out is not None
    assert out.category == "client_opportunity"
    assert "sin necesidad inmediata" in out.next_action.lower()
    assert "licitación futura" in out.next_action.lower()


def test_live_apdata_outbound_rfq_normalizes_to_waiting_supplier() -> None:
    raw = _item(
        contact_email="sales@apdataweigh.com",
        subject="Request for quotation - Dynamic Checkweigher",
        category="waiting_supplier",
    ).model_copy(
        update={
            "sender_preview": ("Tatiana Vivanco | OrigenLab <contacto@origenlab.cl>"),
            "recipients_preview": "sales@apdataweigh.com",
            "source_file": ("gmail:contacto@origenlab.cl/[Gmail]/Enviados"),
        }
    )

    out = normalize_warm_case_item(raw)

    assert out is not None
    assert out.category == "waiting_supplier"
    assert out.status == "waiting"


def test_kalstein_promotion_normalizes_to_system_noise_action() -> None:
    raw = _item(
        contact_email="dianalopez@kalstein.net",
        subject=(
            "Kalstein Plus: disfrute de descuentos exclusivos "
            "de entre el 22 % y el 36 %."
        ),
        category="bounce",
        status="problem",
    ).model_copy(
        update={
            "sender_preview": ("Diana Lopez <dianalopez@kalstein.net>"),
            "source_file": "gmail:contacto@origenlab.cl/INBOX",
        }
    )

    out = normalize_warm_case_item(
        raw,
        include_noise=True,
    )

    assert out is not None
    assert out.category == "system_noise"
    assert "ignorar" in out.next_action.lower()
    assert "ndr" not in out.next_action.lower()
