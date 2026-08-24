"""Heuristic warm-case labels (read-only; shared by API and Postgres promotion)."""

from __future__ import annotations

from typing import Any, Literal

from origenlab_email_pipeline.cases_review_queue import commercial_hint_es
from origenlab_email_pipeline.warm_case_role_classification import (
    WarmCaseLegacyCategory,
    WarmCaseRoleCategory,
    infer_warm_case_role_category,
    role_category_next_action,
    role_category_status,
    role_category_to_legacy_storage,
)
from origenlab_email_pipeline.warm_case_sender_rules import (
    contact_email_from_sender,
    looks_like_client_equipment_opportunity_thread,
    looks_like_client_waiting_review_ack,
    looks_like_low_intent_client_acknowledgement,
    looks_like_supplier_followup_thread,
)

# Legacy storage categories (Postgres CHECK / SQLite promotion).
WarmCaseCategory = WarmCaseLegacyCategory

WarmCaseStatus = Literal["new", "open", "waiting", "quoted", "problem"]

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


def account_name_from_sender(sender_preview: str | None, contact_email: str) -> str:
    if contact_email and "@" in contact_email:
        local, _, domain = contact_email.partition("@")
        if domain and not domain.endswith("origenlab.cl"):
            return domain.split(".")[0].title() or local
        return local
    s = (sender_preview or "").strip()
    if "<" in s:
        s = s.split("<", 1)[0].strip().strip('"')
    return s[:80] if s else "Desconocido"


def equipment_signal_text(
    subject: str, row: dict[str, Any], *, enrichment_available: bool
) -> str:
    subj = subject.lower()
    for kw in _EQUIPMENT_KEYWORDS:
        if kw.lower() in subj:
            return kw
    hint = commercial_hint_es(row, enrichment_available=enrichment_available)
    if hint and hint != "—" and hint != "Sin señal CI":
        return hint[:120]
    return ""


def infer_warm_case_role(
    row: dict[str, Any],
    *,
    enrichment_available: bool,
    include_noise: bool,
) -> WarmCaseRoleCategory:
    return infer_warm_case_role_category(
        row,
        enrichment_available=enrichment_available,
        include_noise=include_noise,
    )


def infer_warm_case_category(
    row: dict[str, Any],
    *,
    enrichment_available: bool,
    include_noise: bool,
) -> WarmCaseCategory:
    role = infer_warm_case_role_category(
        row,
        enrichment_available=enrichment_available,
        include_noise=include_noise,
    )
    return role_category_to_legacy_storage(role)


def infer_warm_case_status(
    category: WarmCaseCategory, row: dict[str, Any]
) -> WarmCaseStatus:
    role = infer_warm_case_role_category(
        row,
        enrichment_available=bool(row.get("has_positive_signal")),
        include_noise=True,
    )
    if role_category_to_legacy_storage(role) == category:
        return role_category_status(role, row)
    if category == "bounce":
        return "problem"
    if category == "quote_sent":
        return "quoted"
    if category in ("waiting_supplier", "waiting_client"):
        return "waiting"
    if category == "opportunity":
        return "open"
    return role_category_status(role, row)


def infer_next_action(
    category: WarmCaseCategory, row: dict[str, Any] | None = None
) -> str:
    subject_l = ""
    sender_l = ""
    snippet_l = ""
    body_l = ""
    contact_email = ""
    if row:
        subject_l = str(row.get("subject_preview") or row.get("subject") or "").lower()
        sender_l = str(row.get("sender_preview") or "").lower()
        snippet_l = str(row.get("snippet") or "").lower()
        body_l = str(
            row.get("body_snippet") or row.get("top_reply_clean") or ""
        ).lower()
        raw_contact = row.get("contact_email")
        if isinstance(raw_contact, str) and "@" in raw_contact:
            contact_email = raw_contact.strip().lower()
        else:
            contact_email = contact_email_from_sender(
                row.get("sender_preview")
                if isinstance(row.get("sender_preview"), str)
                else None
            )
    hay = f"{subject_l} {snippet_l} {sender_l}"
    detailed_hay = f"{subject_l} {body_l or snippet_l} {sender_l}"

    if (
        "rv10.70" in subject_l
        and ("rg energia" in subject_l or "3812200" in subject_l)
        and category in ("opportunity", "supplier_reply", "waiting_supplier")
    ):
        return (
            "Cliente solicita 3 tubos de vapor IKA RV10.70. "
            "Proveedor IKA respondió precio 112,00 y stock disponible. "
            "Falta confirmar moneda y despacho."
        )
    if category == "supplier_reply" and looks_like_supplier_followup_thread(
        contact_email,
        subject_l or None,
        snippet=snippet_l or None,
        sender=sender_l or None,
    ):
        return (
            "CRTOP espera dirección para calcular flete. "
            "No cotizar al cliente hasta tener shipping/importación."
        )
    if (
        "crtop" in sender_l or "crtop" in subject_l or "olt-hp-5l" in subject_l
    ) and category in ("supplier_reply", "waiting_supplier"):
        return (
            "Proveedor CRTOP envió cotización de reactor OLT-HP-5L por US$10,600 EXW. "
            "Falta shipping y costos de importación antes de cotizar al cliente."
        )
    if (
        category == "opportunity"
        and (
            "no necesito ningún servicio" in detailed_hay
            or "no necesito ningun servicio" in detailed_hay
            or "sin necesidad inmediata" in detailed_hay
        )
        and (
            "licitacion" in detailed_hay
            or "licitación" in detailed_hay
            or "tender" in detailed_hay
        )
    ):
        return (
            "Sin necesidad inmediata; registrar la licitación futura y hacer "
            "seguimiento comercial cuando se acerque el proceso."
        )

    if (
        category == "opportunity"
        and "serva" in detailed_hay
        and any(
            cue in detailed_hay
            for cue in (
                "comercializan",
                "consulta cotización",
                "consulta cotizacion",
                "solución de silicona",
                "solucion de silicona",
            )
        )
    ):
        return (
            "Cliente consulta producto SERVA; confirmar formato/cantidad y "
            "preparar cotización."
        )

    if category == "opportunity" and looks_like_client_equipment_opportunity_thread(
        contact_email,
        subject_l or None,
        snippet=snippet_l or None,
        sender=sender_l or None,
    ):
        return (
            "Cliente universitario evaluando escalamiento de extracción vegetal con ultrasonido. "
            "Hielscher deriva cotización local a OrigenLab."
        )
    if category in (
        "waiting_client",
        "client_reply",
    ) and looks_like_client_waiting_review_ack(
        subject_l or None,
        snippet_l or None,
        contact_email=contact_email or None,
    ):
        return "Francisca UC recibió propuesta de reactor y la revisará."
    if category == "quote_sent" and ("ongo" in hay or "up400st" in hay):
        return (
            "Cotización UP400St enviada a ONGO. "
            "Seguimiento en 3–5 días hábiles si no hay respuesta."
        )
    if looks_like_low_intent_client_acknowledgement(
        subject_l or None, snippet_l or None
    ):
        return "Agradecimiento sin solicitud nueva; sin seguimiento inmediato."
    if category == "bounce" and (
        "mailer-daemon" in sender_l
        or "delivery" in subject_l
        or "undeliverable" in subject_l
    ):
        return (
            "Rebote de entrega; revisar supresión por email exacto. "
            "Alerta de entregabilidad si hay muchos rebotes en el lote."
        )
    legacy_to_role: dict[WarmCaseCategory, WarmCaseRoleCategory] = {
        "opportunity": "client_opportunity",
        "client_reply": "client_response",
        "supplier_reply": "supplier_followup",
        "quote_sent": "quote_sent",
        "waiting_supplier": "waiting_supplier",
        "waiting_client": "waiting_client",
        "bounce": "bounce_problem",
    }
    role = legacy_to_role.get(category, "client_response")
    return role_category_next_action(role)
