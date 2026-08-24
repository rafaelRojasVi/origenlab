"""Commercial intelligence rules (v1, deterministic + explainable)."""

from __future__ import annotations

from dataclasses import dataclass
import re

from origenlab_email_pipeline.business_mart import (
    classify_email_intents,
    domain_of,
    emails_in,
    equipment_tags_from_text,
    is_noise_sender,
    primary_sender_email,
)
from origenlab_email_pipeline.warm_case_sender_rules import (
    is_internal_operator_contact,
)

TECHNICAL_INQUIRY_RE = re.compile(
    r"\b(especificaci[oó]n|ficha t[eé]cnica|instalaci[oó]n|calibraci[oó]n|mantenimiento|"
    r"servicio t[eé]cnico|compatibilidad|validaci[oó]n|capacitaci[oó]n|soporte)\b",
    re.I,
)
VENDOR_RE = re.compile(
    r"\b(distribuidor|representante|fabricante|wholesale|reseller|proveedor)\b",
    re.I,
)
INVOICE_PAYMENT_RE = re.compile(
    r"\b(factura|invoice|pago|payment|transferencia|vencimiento|cobranza|estado de cuenta)\b",
    re.I,
)
LOGISTICS_RE = re.compile(
    r"\b(despacho|env[ií]o|tracking|gu[ií]a|aduana|courier|entrega|log[ií]stica)\b",
    re.I,
)
LAB_CONTEXT_RE = re.compile(
    r"\b(laboratorio|lab|ensayo|muestra|anal[ií]tica|investigaci[oó]n|universidad|hospital)\b",
    re.I,
)


@dataclass(frozen=True)
class EmailSignalFact:
    signal_code: str
    signal_kind: str  # positive | suppression
    reason_code: str
    reason_text: str
    confidence_score: float
    strength_score: float
    rationale_json: str


def infer_direction(sender_domain: str | None, internal_domains: set[str]) -> str:
    if not sender_domain:
        return "unknown"
    return "outbound" if sender_domain in internal_domains else "inbound"


def pick_external_contact(
    sender_raw: str,
    recipients_raw: str,
    internal_domains: set[str],
) -> tuple[str | None, str | None]:
    """Resolve the external counterparty for inbound or outbound mail.

    Canonical OriginLab operator identities are always internal even if the
    caller's domain set is stale. Personal Gmail operator addresses are handled
    by exact address, never by treating the whole Gmail domain as internal.
    """
    sender_email = primary_sender_email(sender_raw or "")
    sender_domain = domain_of(sender_email)

    sender_is_internal = bool(
        sender_email and is_internal_operator_contact(sender_email)
    ) or bool(
        sender_domain and sender_domain in internal_domains
    )

    if (
        not sender_is_internal
        and sender_email
        and sender_domain
        and sender_domain not in internal_domains
    ):
        return sender_email, sender_domain

    for raw in emails_in(recipients_raw or ""):
        email = raw.strip().lower()
        if is_internal_operator_contact(email):
            continue
        domain = domain_of(email)
        if domain and domain not in internal_domains:
            return email, domain

    return None, None


def _fmt_rationale(fields: dict[str, object]) -> str:
    pairs = [f'"{k}":"{fields[k]}"' for k in sorted(fields)]
    return "{" + ",".join(pairs) + "}"


def derive_email_signal_facts(
    *,
    subject: str,
    sender_raw: str,
    recipients_raw: str,
    top_reply_clean: str,
    full_body_clean: str,
    sender_domain: str | None,
    internal_domains: set[str],
    vendor_domains: set[str],
    existing_client_domains: set[str],
) -> list[EmailSignalFact]:
    body = top_reply_clean or full_body_clean or ""
    blob = (subject or "") + "\n" + body
    blob_l = blob.lower()

    out: list[EmailSignalFact] = []
    intents = classify_email_intents(subject or "", body)
    eq_tags = equipment_tags_from_text(blob)
    has_lab_context = bool(LAB_CONTEXT_RE.search(blob))
    technical_hit = bool(TECHNICAL_INQUIRY_RE.search(blob))

    if intents.get("is_quote_email"):
        out.append(
            EmailSignalFact(
                signal_code="quote_intent",
                signal_kind="positive",
                reason_code="QUOTE_TERMS",
                reason_text="Detected quote/cotizacion terms in subject/body.",
                confidence_score=0.9,
                strength_score=0.75,
                rationale_json=_fmt_rationale({"intent": "quote", "source": "subject_or_body"}),
            )
        )
    if intents.get("is_purchase_email"):
        out.append(
            EmailSignalFact(
                signal_code="procurement_intent",
                signal_kind="positive",
                reason_code="PROCUREMENT_TERMS",
                reason_text="Detected procurement/purchase-order terms.",
                confidence_score=0.86,
                strength_score=0.72,
                rationale_json=_fmt_rationale({"intent": "procurement", "source": "subject_or_body"}),
            )
        )
    if technical_hit and (has_lab_context or bool(eq_tags)):
        out.append(
            EmailSignalFact(
                signal_code="technical_inquiry",
                signal_kind="positive",
                reason_code="TECHNICAL_QUERY",
                reason_text="Detected technical inquiry in lab/equipment context.",
                confidence_score=0.82,
                strength_score=0.68,
                rationale_json=_fmt_rationale(
                    {
                        "technical_hit": technical_hit,
                        "lab_context": has_lab_context,
                        "equipment_tags": ",".join(eq_tags[:5]),
                    }
                ),
            )
        )
    if eq_tags:
        out.append(
            EmailSignalFact(
                signal_code="equipment_relevance",
                signal_kind="positive",
                reason_code="EQUIPMENT_TERMS",
                reason_text="Detected lab/equipment terms relevant to OrigenLab.",
                confidence_score=0.8,
                strength_score=min(0.9, 0.55 + 0.05 * len(eq_tags)),
                rationale_json=_fmt_rationale({"equipment_tags": ",".join(eq_tags[:8])}),
            )
        )

    # Suppressions
    sender_email = primary_sender_email(sender_raw or "")
    picked_contact, picked_domain = pick_external_contact(
        sender_raw=sender_raw,
        recipients_raw=recipients_raw,
        internal_domains=internal_domains,
    )
    sender_is_vendor_domain = bool(sender_domain and sender_domain in vendor_domains)
    picked_is_vendor_domain = bool(picked_domain and picked_domain in vendor_domains)
    if sender_is_vendor_domain or picked_is_vendor_domain or VENDOR_RE.search(blob_l):
        out.append(
            EmailSignalFact(
                signal_code="vendor_suppression",
                signal_kind="suppression",
                reason_code="VENDOR_LIKE",
                reason_text="Vendor/supplier-like signal detected.",
                confidence_score=0.85,
                strength_score=0.8,
                rationale_json=_fmt_rationale(
                    {
                        "sender_domain": sender_domain or "",
                        "contact_domain": picked_domain or "",
                        "sender_email": sender_email or "",
                        "contact_email": picked_contact or "",
                    }
                ),
            )
        )
    if INVOICE_PAYMENT_RE.search(blob_l):
        out.append(
            EmailSignalFact(
                signal_code="invoice_payment_suppression",
                signal_kind="suppression",
                reason_code="INVOICE_PAYMENT_HEAVY",
                reason_text="Invoice/payment-heavy language detected.",
                confidence_score=0.78,
                strength_score=0.7,
                rationale_json=_fmt_rationale({"pattern": "invoice_payment"}),
            )
        )
    if LOGISTICS_RE.search(blob_l):
        out.append(
            EmailSignalFact(
                signal_code="logistics_suppression",
                signal_kind="suppression",
                reason_code="LOGISTICS_HEAVY",
                reason_text="Logistics/shipping-heavy language detected.",
                confidence_score=0.72,
                strength_score=0.62,
                rationale_json=_fmt_rationale({"pattern": "logistics"}),
            )
        )
    if is_noise_sender(sender_raw or "", subject or "", body):
        out.append(
            EmailSignalFact(
                signal_code="noise_suppression",
                signal_kind="suppression",
                reason_code="NOISE_SENDER",
                reason_text="System/noise sender pattern detected.",
                confidence_score=0.95,
                strength_score=0.9,
                rationale_json=_fmt_rationale({"sender": (sender_raw or "")[:120]}),
            )
        )
    if (sender_domain and sender_domain in existing_client_domains) or (
        picked_domain and picked_domain in existing_client_domains
    ):
        out.append(
            EmailSignalFact(
                signal_code="existing_client_suppression",
                signal_kind="suppression",
                reason_code="EXISTING_CLIENT_LIKELY",
                reason_text="Domain appears in existing-client reference set.",
                confidence_score=0.75,
                strength_score=0.7,
                rationale_json=_fmt_rationale(
                    {"sender_domain": sender_domain or "", "contact_domain": picked_domain or ""}
                ),
            )
        )
    return out

