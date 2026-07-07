"""Derive read-only commercial action buckets for Prospectos dashboard."""

from __future__ import annotations

from typing import Any, Final

BUCKET_READY_TO_CONTACT: Final = "ready_to_contact"
BUCKET_NEEDS_EMAIL_ENRICHMENT: Final = "needs_email_enrichment"
BUCKET_TENDER_OPPORTUNITY: Final = "tender_opportunity"
BUCKET_REVIEW_HISTORY: Final = "review_history"
BUCKET_ALREADY_CONTACTED: Final = "already_contacted"
BUCKET_BLOCKED: Final = "blocked"

COMMERCIAL_ACTION_BUCKETS: frozenset[str] = frozenset(
    {
        BUCKET_READY_TO_CONTACT,
        BUCKET_NEEDS_EMAIL_ENRICHMENT,
        BUCKET_TENDER_OPPORTUNITY,
        BUCKET_REVIEW_HISTORY,
        BUCKET_ALREADY_CONTACTED,
        BUCKET_BLOCKED,
    }
)

_BLOCKED_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "already_contacted_block",
        "bounced_block",
        "suppressed_block",
        "supplier_or_internal_block",
        "bounced_suppressed",
    }
)

_REVIEW_HISTORY_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "same_domain_contacted_review",
        "old_gmail_prospect_review",
        "old_followup_review",
        "active_case_hold",
        "legacy_contact_review",
        "contacted_by_domain_review",
    }
)

_CONTACTED_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "gmail_historico",
        "followup_antiguo",
        "caso_activo",
    }
)

_OPERATIONAL_NEXT_ACTION: dict[str, str] = {
    BUCKET_BLOCKED: "No contactar",
    BUCKET_ALREADY_CONTACTED: "Esperar respuesta; revisar follow-up después (no reenviar)",
    BUCKET_TENDER_OPPORTUNITY: "Preparar encaje técnico y revisar bases de licitación",
    BUCKET_NEEDS_EMAIL_ENRICHMENT: "Enriquecer contacto: buscar email y nombre del responsable",
    BUCKET_REVIEW_HISTORY: "Inspeccionar historial Gmail/dominio antes de cualquier acción",
    BUCKET_READY_TO_CONTACT: "Listo para contacto inicial tras revisión humana",
}


def _gmail_sent(prospect: dict[str, Any]) -> int:
    return int(prospect.get("gmail_sent_count") or 0)


def _gmail_received(prospect: dict[str, Any]) -> int:
    return int(prospect.get("gmail_received_count") or 0)


def _has_email(prospect: dict[str, Any]) -> bool:
    return bool(str(prospect.get("email") or "").strip())


def derive_commercial_action_bucket(prospect: dict[str, Any]) -> str:
    """Map one overlaid prospect row to a single commercial action bucket."""
    classification = str(prospect.get("classification") or "").strip()
    if prospect.get("is_blocked") or classification in _BLOCKED_CLASSIFICATIONS:
        return BUCKET_BLOCKED

    if classification == "public_tender_review":
        return BUCKET_TENDER_OPPORTUNITY

    if classification == "research_only_contact_needed":
        return BUCKET_NEEDS_EMAIL_ENRICHMENT

    if classification == "manual_outreach_sent":
        return BUCKET_ALREADY_CONTACTED

    sent = _gmail_sent(prospect)
    received = _gmail_received(prospect)
    if sent > 0 or received > 0:
        return BUCKET_ALREADY_CONTACTED

    source_type = str(prospect.get("source_type") or "").strip()
    if source_type in _CONTACTED_SOURCE_TYPES:
        return BUCKET_ALREADY_CONTACTED

    if classification in _REVIEW_HISTORY_CLASSIFICATIONS:
        return BUCKET_REVIEW_HISTORY

    if classification == "net_new_safe_review":
        return BUCKET_READY_TO_CONTACT if _has_email(prospect) else BUCKET_NEEDS_EMAIL_ENRICHMENT

    return BUCKET_REVIEW_HISTORY


def derive_operational_next_action(prospect: dict[str, Any], bucket: str | None = None) -> str:
    """Operator-facing próxima acción text (read-only guidance)."""
    resolved = bucket or derive_commercial_action_bucket(prospect)
    return _OPERATIONAL_NEXT_ACTION.get(resolved, _OPERATIONAL_NEXT_ACTION[BUCKET_REVIEW_HISTORY])


def is_followup_eligible(prospect: dict[str, Any]) -> bool:
    """Contacted rows that may warrant follow-up review later (no send)."""
    if prospect.get("is_blocked"):
        return False
    if derive_commercial_action_bucket(prospect) != BUCKET_ALREADY_CONTACTED:
        return False
    sent = _gmail_sent(prospect)
    received = _gmail_received(prospect)
    if sent > 0 and received == 0:
        return True
    if str(prospect.get("classification") or "") == "manual_outreach_sent":
        return True
    return False


def enrich_prospect_for_dashboard(prospect: dict[str, Any]) -> dict[str, Any]:
    """Add commercial_action_bucket and operational_next_action to a prospect dict."""
    out = dict(prospect)
    bucket = derive_commercial_action_bucket(out)
    out["commercial_action_bucket"] = bucket
    out["operational_next_action"] = derive_operational_next_action(out, bucket)
    return out


def summarize_commercial_action_buckets(prospects: list[dict[str, Any]]) -> dict[str, int]:
    """Count commercial buckets after operational overlay."""
    counts = {bucket: 0 for bucket in COMMERCIAL_ACTION_BUCKETS}
    followup_eligible = 0
    for prospect in prospects:
        bucket = derive_commercial_action_bucket(prospect)
        counts[bucket] += 1
        if is_followup_eligible(prospect):
            followup_eligible += 1
    return {
        "ready_to_contact": counts[BUCKET_READY_TO_CONTACT],
        "needs_email_enrichment": counts[BUCKET_NEEDS_EMAIL_ENRICHMENT],
        "tender_opportunity": counts[BUCKET_TENDER_OPPORTUNITY],
        "review_history": counts[BUCKET_REVIEW_HISTORY],
        "already_contacted": counts[BUCKET_ALREADY_CONTACTED],
        "blocked": counts[BUCKET_BLOCKED],
        "followup_eligible": followup_eligible,
    }
