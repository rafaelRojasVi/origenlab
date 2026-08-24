"""Role-level warm-case triage categories (read-only; shared by API + promotion)."""

from __future__ import annotations

import re
from typing import Any, Literal

from origenlab_email_pipeline.cases_review_queue import looks_like_obvious_noise
from origenlab_email_pipeline.warm_case_sender_rules import (
    contact_email_from_recipients,
    contact_email_from_sender,
    email_domain,
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
    looks_like_promotional_marketing_thread,
    looks_like_supplier_followup_thread,
    looks_like_supplier_quote_response,
    looks_like_supplier_marketing_thread,
    looks_like_suppressed_promotional_marketing_noise,
    looks_like_system_noise_contact,
    looks_like_unach_hielscher_supplier_wait,
    should_keep_visible_despite_suppression,
)

WarmCaseRoleCategory = Literal[
    "client_opportunity",
    "client_response",
    "supplier_quote_received",
    "supplier_followup",
    "payment_admin",
    "logistics_admin",
    "internal_admin",
    "system_noise",
    "bounce_problem",
    "deal_evidence_candidate",
    "quote_sent",
    "waiting_supplier",
    "waiting_client",
    "campaign_outreach",
    "waiting_campaign_reply",
    "auto_acknowledgement",
]

# Legacy storage categories (Postgres CHECK / promotion).
WarmCaseLegacyCategory = Literal[
    "client_reply",
    "supplier_reply",
    "quote_sent",
    "waiting_supplier",
    "waiting_client",
    "bounce",
    "opportunity",
]

_EQUIPMENT_KEYWORDS = (
    "centrifug",
    "ultrason",
    "sonicador",
    "reactor",
    "balanza",
    "incubad",
    "licitacion",
    "licitación",
    "equipo",
    "turrax",
)

_DEAL_EVIDENCE_MARKERS = (
    "serva",
    "ceaf",
    "orden de compra",
    "remite oc",
    "oc n",
    "po-",
    "deal_key",
)


def _bool_signal(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return bool(value)


def _is_sent_folder(source_file: str | None) -> bool:
    s = (source_file or "").lower()
    return "enviados" in s or "sent mail" in s or "/sent" in s


def _row_contact_email(row: dict[str, Any]) -> str:
    explicit = row.get("contact_email")
    if isinstance(explicit, str) and "@" in explicit:
        return explicit.strip().lower()
    source = row.get("source_file")
    if _is_sent_folder(source if isinstance(source, str) else None):
        for key in ("recipients_preview", "recipients"):
            raw = row.get(key)
            if isinstance(raw, str):
                external = contact_email_from_recipients(raw)
                if external:
                    return external
    sender = row.get("sender_preview")
    return contact_email_from_sender(sender if isinstance(sender, str) else None)


def _row_subject(row: dict[str, Any]) -> str | None:
    subject = row.get("subject_preview") or row.get("subject")
    return subject if isinstance(subject, str) else None


def _row_sender(row: dict[str, Any]) -> str | None:
    sender = row.get("sender_preview")
    return sender if isinstance(sender, str) else None


def _row_body_snippet(row: dict[str, Any]) -> str | None:
    for key in ("body_snippet", "top_reply_clean"):
        raw = row.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _row_heuristic_snippet(row: dict[str, Any]) -> str | None:
    """Body text for classification heuristics (not API display snippet)."""
    body = _row_body_snippet(row)
    if body:
        return body[:2000]
    snippet = row.get("snippet")
    if isinstance(snippet, str) and snippet.strip():
        return snippet
    return None


def looks_like_deal_evidence_thread(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
) -> bool:
    """CEAF/SERVA commercial-deal threads (link to deal timeline in Phase 7B)."""
    if looks_like_dhl_logistics_account_access_thread(
        contact_email,
        subject,
        snippet=snippet,
    ):
        return False
    domain = email_domain(contact_email)
    subject_hay = " ".join([subject or "", snippet or ""]).lower()
    contact_l = contact_email.strip().lower()

    if (
        domain == "uc.cl"
        and "serva" in subject_hay
        and any(
            cue in subject_hay
            for cue in (
                "comercializan",
                "consulta cotización",
                "consulta cotizacion",
                "solución de silicona",
                "solucion de silicona",
            )
        )
    ):
        return False

    if domain == "serva.de" or contact_l.endswith("@serva.de"):
        serva_hay = " ".join([contact_l, subject_hay]).lower()
        serva_markers = (
            "310471",
            "quotation request",
            "address created",
            "adress created",
            "new adress",
            "compagny",
            "company number",
            "po n",
            "a2602545",
        )
        if any(marker in serva_hay for marker in serva_markers):
            return True
    if is_real_client_domain(domain) and looks_like_client_oc_post_sale_subject(
        subject,
        snippet=snippet,
    ):
        return True
    if any(marker in subject_hay for marker in _DEAL_EVIDENCE_MARKERS):
        if "serva" in subject_hay or is_real_client_domain(domain):
            return True
    return False


def infer_warm_case_role_category(
    row: dict[str, Any],
    *,
    enrichment_available: bool,
    include_noise: bool,
) -> WarmCaseRoleCategory:
    sender_s = _row_sender(row)
    subject_s = _row_subject(row)
    contact_email = _row_contact_email(row)
    snippet = _row_heuristic_snippet(row)
    account_name = (
        row.get("account_name") if isinstance(row.get("account_name"), str) else None
    )

    if looks_like_obvious_noise(sender_s, subject_s):
        return "bounce_problem"

    if looks_like_cyberday_bulk_campaign_subject(subject_s):
        source_cyber = row.get("source_file")
        if _is_sent_folder(source_cyber if isinstance(source_cyber, str) else None):
            return "campaign_outreach"
        if _bool_signal(row.get("has_suppression_signal")) or looks_like_obvious_noise(
            sender_s, subject_s
        ):
            return "bounce_problem"
        return "waiting_campaign_reply"

    if looks_like_idiem_auto_acknowledgement(
        contact_email, subject_s, snippet=snippet, sender=sender_s
    ):
        return "auto_acknowledgement"

    if looks_like_contact_routing_notice(subject_s, snippet, sender=sender_s):
        return "system_noise"

    if looks_like_auto_reply_text(subject_s, snippet):
        return "system_noise"

    if looks_like_system_noise_contact(contact_email, sender_s, subject_s):
        return "system_noise"

    if looks_like_suppressed_promotional_marketing_noise(
        contact_email,
        sender_s,
        subject_s,
        has_suppression_signal=_bool_signal(row.get("has_suppression_signal")),
    ):
        return "system_noise"

    if looks_like_promotional_marketing_thread(
        contact_email,
        sender_s,
        subject_s,
        snippet=snippet,
    ):
        return "system_noise"

    source_early = row.get("source_file")
    if _is_sent_folder(source_early if isinstance(source_early, str) else None):
        if not is_internal_operator_contact(contact_email):
            subj_early = (subject_s or "").lower()
            outbound_rfq_early = any(
                cue in subj_early
                for cue in (
                    "request for quotation",
                    "quotation request",
                    "request for quote",
                    "rfq",
                )
            )
            if not outbound_rfq_early and (
                "cotiz" in subj_early
                or "quote" in subj_early
                or "presupuesto" in subj_early
            ):
                return "quote_sent"

    if looks_like_internal_forwarded_client_quote_request(
        contact_email=contact_email,
        subject=subject_s,
        snippet=snippet,
        sender=sender_s,
    ):
        return "client_opportunity"

    if looks_like_internal_admin_thread(
        contact_email,
        subject_s,
        snippet=snippet,
        sender=sender_s,
    ):
        return "internal_admin"

    if looks_like_payment_admin_thread(
        contact_email,
        subject_s,
        snippet=snippet,
        account_name=account_name,
    ):
        return "payment_admin"

    if looks_like_dhl_logistics_account_access_thread(
        contact_email,
        subject_s,
        snippet=snippet,
    ):
        return "logistics_admin"

    source_direction = row.get("source_file")
    if _is_sent_folder(source_direction if isinstance(source_direction, str) else None):
        subject_direction = (subject_s or "").lower()
        if any(
            cue in subject_direction
            for cue in (
                "request for quotation",
                "quotation request",
                "request for quote",
                "rfq",
            )
        ):
            return "waiting_supplier"

    if email_domain(contact_email) == "serva.de":
        if looks_like_supplier_quote_response(
            contact_email,
            subject_s,
            snippet=snippet,
            sender=sender_s,
        ):
            return "supplier_quote_received"

        if looks_like_supplier_followup_thread(
            contact_email,
            subject_s,
            snippet=snippet,
            sender=sender_s,
        ):
            return "supplier_followup"

    if looks_like_deal_evidence_thread(
        contact_email,
        subject_s,
        snippet=snippet,
    ):
        return "deal_evidence_candidate"

    if looks_like_logistics_admin_contact(contact_email, subject_s, snippet=snippet):
        return "logistics_admin"

    if looks_like_cesmec_catalogue_client_thread(
        contact_email, subject_s, snippet=snippet, sender=sender_s
    ):
        return "client_opportunity"

    if looks_like_unach_hielscher_supplier_wait(
        contact_email, subject_s, snippet=snippet, sender=sender_s
    ):
        return "waiting_supplier"

    if looks_like_client_waiting_review_ack(
        subject_s, snippet, contact_email=contact_email
    ):
        return "waiting_client"

    source = row.get("source_file")
    if _is_sent_folder(source if isinstance(source, str) else None):
        if is_supplier_vendor_domain(email_domain(contact_email)):
            return "waiting_supplier"
        subj_l_early = (subject_s or "").lower()
        if (
            "cotiz" in subj_l_early
            or "quote" in subj_l_early
            or "presupuesto" in subj_l_early
        ):
            return "quote_sent"
        return "waiting_client"

    if looks_like_client_equipment_opportunity_thread(
        contact_email,
        subject_s,
        snippet=snippet,
        sender=sender_s,
    ):
        return "client_opportunity"

    if looks_like_low_intent_client_acknowledgement(subject_s, snippet):
        return "client_response"

    if looks_like_supplier_quote_response(
        contact_email,
        subject_s,
        snippet=snippet,
        sender=sender_s,
    ):
        return "supplier_quote_received"

    if looks_like_supplier_followup_thread(
        contact_email,
        subject_s,
        snippet=snippet,
        sender=sender_s,
    ):
        return "supplier_followup"

    if looks_like_supplier_marketing_thread(
        contact_email=contact_email,
        sender=sender_s,
        subject=subject_s,
    ):
        return "supplier_followup"

    subj_l = (subject_s or "").lower()
    snd_l = (sender_s or "").lower()

    if enrichment_available and _bool_signal(row.get("has_positive_signal")):
        if any(k in subj_l for k in _EQUIPMENT_KEYWORDS) or any(
            k in subj_l for k in ("licit", "equipo", "tender", "compra")
        ):
            return "client_opportunity"
        return "client_opportunity"

    if re.match(r"^re:\s", subj_l) or subj_l.startswith("re "):
        if "origenlab" in snd_l or is_internal_operator_contact(contact_email):
            return "waiting_supplier"
        return "client_response"

    if "cotiz" in subj_l or "presupuesto" in subj_l:
        return "waiting_supplier"

    if include_noise:
        return "client_response"
    return "client_response"


def role_category_to_legacy_storage(
    role: WarmCaseRoleCategory,
) -> WarmCaseLegacyCategory:
    """Map role category to Postgres/SQLite promotion CHECK values."""
    return {
        "client_opportunity": "opportunity",
        "client_response": "client_reply",
        "supplier_quote_received": "supplier_reply",
        "supplier_followup": "supplier_reply",
        "payment_admin": "client_reply",
        "logistics_admin": "client_reply",
        "internal_admin": "bounce",
        "system_noise": "bounce",
        "bounce_problem": "bounce",
        "deal_evidence_candidate": "client_reply",
        "quote_sent": "quote_sent",
        "waiting_supplier": "waiting_supplier",
        "waiting_client": "waiting_client",
        "campaign_outreach": "waiting_client",
        "waiting_campaign_reply": "waiting_client",
        "auto_acknowledgement": "client_reply",
    }[role]


def role_category_status(
    role: WarmCaseRoleCategory,
    row: dict[str, Any],
) -> Literal["new", "open", "waiting", "quoted", "problem"]:
    if role in (
        "bounce_problem",
        "system_noise",
        "internal_admin",
        "auto_acknowledgement",
    ):
        return "problem"
    if role == "quote_sent":
        return "quoted"
    if role in ("waiting_supplier", "waiting_client", "waiting_campaign_reply"):
        return "waiting"
    if role == "campaign_outreach":
        return "waiting"
    if role in (
        "client_opportunity",
        "payment_admin",
        "logistics_admin",
        "deal_evidence_candidate",
        "supplier_quote_received",
    ):
        return "open"
    if role == "supplier_followup":
        return "open"
    legacy = role_category_to_legacy_storage(role)
    if _bool_signal(row.get("has_suppression_signal")):
        if should_keep_visible_despite_suppression(
            _row_contact_email(row),
            _row_subject(row),
            category=legacy,
            snippet=row.get("snippet") if isinstance(row.get("snippet"), str) else None,
        ):
            return "open"
        return "problem"
    if _bool_signal(row.get("has_positive_signal")):
        return "open"
    return "new"


def role_category_next_action(role: WarmCaseRoleCategory) -> str:
    return {
        "bounce_problem": "Revisar NDR o rebote; no reenviar sin corregir dirección.",
        "system_noise": "Ignorar alerta de sistema/cuenta; no requiere acción comercial.",
        "internal_admin": "Nota interna de operador; no tratar como cliente ni proveedor.",
        "payment_admin": "Registrar/confirmar pago o datos bancarios; no cotizar.",
        "logistics_admin": "Revisar logística/cuenta de importación; no cotizar al remitente.",
        "deal_evidence_candidate": "Vincular al deal comercial; no cotizar desde este hilo.",
        "supplier_quote_received": "Cotización de proveedor recibida; vincular a oportunidad/cliente.",
        "supplier_followup": (
            "Seguimiento con proveedor; confirmar datos logísticos antes de cotizar al cliente."
        ),
        "quote_sent": "Confirmar si el cliente respondió; no duplicar cotización.",
        "waiting_client": "Esperar respuesta o seguimiento suave si pasó el plazo.",
        "waiting_supplier": "Seguimiento a proveedor por cotización pendiente.",
        "client_response": "Responder hilo comercial; verificar specs antes de cotizar.",
        "client_opportunity": "Priorizar según señal comercial; validar equipo y plazo.",
        "campaign_outreach": "Envío masivo CYBERDAY; no tratar como oportunidad tibia individual.",
        "waiting_campaign_reply": "Campaña CYBERDAY enviada; esperar respuesta (vista Campañas).",
        "auto_acknowledgement": "Acuse automático institucional; no requiere seguimiento comercial.",
    }[role]
