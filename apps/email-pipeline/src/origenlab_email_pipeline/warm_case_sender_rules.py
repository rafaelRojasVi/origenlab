"""Shared warm-case sender/subject heuristics (read-only; API + SQLite promotion)."""

from __future__ import annotations

import re
from email.utils import parseaddr

from origenlab_email_pipeline.business_mart import emails_in

INTERNAL_OPERATOR_DOMAINS: frozenset[str] = frozenset(
    {"origenlab.cl", "labdelivery.cl"}
)

INTERNAL_OPERATOR_EMAILS: frozenset[str] = frozenset(
    {
        "tvivancob@gmail.com",
        "sebastian.rojas.vivanco@gmail.com",
        "contacto@labdelivery.cl",
        "contacto@origenlab.cl",
    }
)

_PAYMENT_ADMIN_DOMAINS: frozenset[str] = frozenset({"bancochile.cl"})

_LOGISTICS_VENDOR_DOMAINS: frozenset[str] = frozenset({"dhl.com"})

_SYSTEM_NOISE_EMAILS: frozenset[str] = frozenset(
    {
        "no-reply@accounts.google.com",
        "mailer-daemon@googlemail.com",
    }
)

_INTERNAL_ADMIN_SUBJECT_MARKERS: tuple[str, ...] = (
    "re: serva",
    "serva payment",
    "pago serva",
    "wise",
    "transferencia serva",
)

_SUPPLIER_QUOTE_SUBJECT_MARKERS: tuple[str, ...] = (
    "precio",
    "price",
    "cotiz",
    "quote",
    "quotation",
    "presupuesto",
    " rv",
    "re:",
    "re ",
)

_WEAK_SUPPLIER_QUOTE_SUBJECT_MARKERS: frozenset[str] = frozenset({"re:", "re "})

_AUTO_REPLY_TEXT_MARKERS: tuple[str, ...] = (
    "automatic reply",
    "auto-reply",
    "auto reply",
    "out of office",
    "out-of-office",
    "fuera de oficina",
    "fuera de la oficina",
    "respuesta automática",
    "respuesta automatica",
    "resposta automática",
    "resposta automatica",
    "autorespuesta",
    "automatische antwort",
    "réponse automatique",
    "reponse automatique",
    "absence du bureau",
    "i am out of the office",
    "estoy fuera de la oficina",
    "estou fora do escritório",
    "estou fora do escritorio",
    "vacation reply",
    "vacation responder",
    "away from the office",
    "office closed",
    "oficina cerrada",
    "escritório fechado",
    "escritorio fechado",
)

_REAL_QUOTE_PRICE_RE = re.compile(
    r"(?:"
    r"\busd\b|\beur\b|\bexw\b|\bfob\b|\bus\$\s*\d|\$\s*\d|\d+[,.]\d{2}\s*(?:usd|eur|clp)?"
    r"|\d{2,}\s*usd(?:/pc|/unit|/ea)?|\d{2,}usd(?:/pc|/unit|/ea)?"
    r"|\b(?:usd|us\$)\s*\d{2,}\b|\b(?:usd|us\$)\d{2,}\b"
    r")",
    re.I,
)

# Existing-client / post-sale threads (not suppliers).
REAL_CLIENT_DOMAINS: frozenset[str] = frozenset({"ceaf.cl"})

# Domains that should never appear as «Clientes reales» (vendor / industrial suppliers).
SUPPLIER_VENDOR_DOMAINS: frozenset[str] = frozenset(
    {
        "apdataweigh.com",
        "asynt.com",
        "biosys.de",
        "ciqtek.com",
        "crtopmachine.com",
        "dasitaly.com",
        "dlabsci.com",
        "eppendorf.com",
        "gzfanbolun.com",
        "hielscher.com",
        "ika.net.br",
        "kalstein.net",
        "moldev.com",
        "ollital.com",
        "ortoalresa.com",
        "serva.de",
        "ultrassay.com",
        "valuenindustrial.com",
        "yuanhuai.com",
    }
)

_PROMO_MARKETING_SUBJECT_MARKERS: tuple[str, ...] = (
    "descuento",
    "discount",
    "% off",
    "promo",
    "promoción",
    "promocion",
    "oferta especial",
    "limited time offer",
    "black friday",
    "cyberday",
    "cyber day",
)

_NO_REPLY_LOCAL_PARTS: frozenset[str] = frozenset(
    {"no-reply", "noreply", "donotreply", "do-not-reply"}
)

_SECURITY_CONTACT_DOMAINS: frozenset[str] = frozenset({"accounts.google.com"})

_SECURITY_SUBJECT_MARKERS: tuple[str, ...] = (
    "alerta de seguridad",
    "security alert",
    "critical security",
    "suspicious sign-in",
    "suspicious sign in",
    "new sign-in",
    "nuevo inicio de sesión",
    "nuevo inicio de sesion",
)

_ADMIN_SIGNUP_SUBJECT_MARKERS: tuple[str, ...] = (
    "confirm your registration",
    "please confirm your registration",
    "confirm your email",
    "verify your email",
    "email verification",
    "activate your account",
    "activar su cuenta",
)

# CyberDay 2026 bulk outreach — must not appear as default warm client queue.
CYBERDAY_CAMPAIGN_SUBJECT = (
    "CYBERDAY — equipos de laboratorio seleccionados hasta el 7 de junio"
)

_IDIEM_AUTO_ACK_MARKERS: tuple[str, ...] = (
    "acuse",
    "recibimos su mensaje",
    "hemos recibido su",
    "hemos recibido el",
    "mensaje recibido",
    "confirmamos recepción",
    "confirmamos recepcion",
    "gracias por contactar",
    "recepción de requerimiento",
    "recepcion de requerimiento",
    "recepción de su requerimiento",
    "recepcion de su requerimiento",
)

_CESMEC_CLIENT_MARKERS: tuple[str, ...] = (
    "cesmec",
    "bureau veritas",
    "bureauveritas",
    "catálogo",
    "catalogo",
    "metrolog",
    "balances",
)

_SUPPLIER_SUBJECT_MARKERS: tuple[str, ...] = (
    "supplier",
    "proveedor",
    "distrib",
    "hielscher",
    "ortoalresa",
    "serva.de",
    "ollital",
    "kern",
    "sartorius",
    "eppendorf",
    "yh chem",
    "yhchem",
    "fanbolun",
    "valuen",
)

_FORWARDED_CLIENT_QUOTE_SUBJECT_MARKERS: tuple[str, ...] = (
    "fwd:",
    "fw:",
    "rv:",
    "reenvio",
    "reenvío",
)

_CLIENT_QUOTE_REQUEST_MARKERS: tuple[str, ...] = (
    "solicitud de cotiz",
    "cotización",
    "cotizacion",
    "quotation request",
    "request for quotation",
    "rg energia",
    "rv10.70",
    "3812200",
)

# ---------------------------------------------------------------------------
# Parsing / domain helpers
# ---------------------------------------------------------------------------


def _message_haystack(
    subject: str | None,
    snippet: str | None = None,
    sender: str | None = None,
) -> str:
    return " ".join([subject or "", snippet or "", sender or ""]).lower()


def _resolve_contact_email(
    contact_email: str = "",
    *,
    sender: str | None = None,
) -> str:
    return (contact_email or "").strip().lower() or contact_email_from_sender(sender)


def _supplier_quote_haystack(
    subject: str | None,
    snippet: str | None = None,
) -> str:
    return " ".join([subject or "", snippet or ""]).lower()


def contact_email_from_sender(sender_preview: str | None) -> str:
    found = emails_in(sender_preview or "")
    return found[0].lower() if found else ""


def contact_email_from_recipients(recipients: str | None) -> str:
    """First external recipient on outbound (Sent) messages."""
    for raw in emails_in(recipients or ""):
        email = raw.lower()
        if is_internal_operator_contact(email):
            continue
        return email
    return ""


def is_internal_operator_contact(contact_email: str) -> bool:
    email = (contact_email or "").strip().lower()
    if email in INTERNAL_OPERATOR_EMAILS:
        return True
    return email_domain(email) in INTERNAL_OPERATOR_DOMAINS


def is_real_client_domain(domain: str) -> bool:
    return (domain or "").strip().lower() in REAL_CLIENT_DOMAINS


def is_chile_institution_client_domain(domain: str) -> bool:
    """University / lab / institutional .cl domains (not vendor suppliers)."""
    d = (domain or "").strip().lower()
    if not d.endswith(".cl"):
        return False
    if is_supplier_vendor_domain(d) or d in INTERNAL_OPERATOR_DOMAINS:
        return False
    if d in {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "googlemail.com"}:
        return False
    return True


# ---------------------------------------------------------------------------
# Payment / logistics admin
# ---------------------------------------------------------------------------

PAYMENT_ADMIN_TEXT_MARKERS: tuple[str, ...] = (
    "datos bancarios",
    "solicita datos banc",
    "cuenta corriente",
    "beneficiario",
    "factura n°",
    "factura nº",
    "factura n ",
    "factura no",
    "factura nro",
    "proceder al pago",
    "comprobante de transferencia",
    "transferencia",
    "registrarla en nuestro sistema",
    "registrar en nuestro sistema",
    "factura",
    "pago",
)

CLIENT_OC_POST_SALE_MARKERS: tuple[str, ...] = (
    "remite oc",
    "orden de compra",
    "re: remite oc",
)


def payment_admin_text_haystack(
    *,
    subject: str | None,
    snippet: str | None = None,
    account_name: str | None = None,
) -> str:
    return " ".join(
        [
            subject or "",
            snippet or "",
            account_name or "",
        ]
    ).lower()


def looks_like_payment_admin_thread(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    account_name: str | None = None,
) -> bool:
    if email_domain(contact_email) in _PAYMENT_ADMIN_DOMAINS:
        return True
    hay = payment_admin_text_haystack(
        subject=subject,
        snippet=snippet,
        account_name=account_name,
    )
    return any(marker in hay for marker in PAYMENT_ADMIN_TEXT_MARKERS)


def looks_like_payment_admin_contact(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    account_name: str | None = None,
) -> bool:
    return looks_like_payment_admin_thread(
        contact_email,
        subject,
        snippet=snippet,
        account_name=account_name,
    )


def looks_like_logistics_admin_contact(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
) -> bool:
    domain = email_domain(contact_email)
    sub = " ".join([subject or "", snippet or ""]).lower()
    if domain in _LOGISTICS_VENDOR_DOMAINS:
        return True
    return any(
        token in sub
        for token in (
            "dhl",
            "cuenta importación",
            "cuenta importacion",
            "propuesta comercial dhl",
            "solicitud cuenta",
        )
    )


def looks_like_dhl_logistics_account_access_thread(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
) -> bool:
    """DHL eSecure / account-access admin — not commercial deal evidence."""
    domain = email_domain(contact_email)
    hay = " ".join([subject or "", snippet or ""]).lower()
    is_dhl = domain in _LOGISTICS_VENDOR_DOMAINS or "dhl" in hay or "esecure" in hay
    if not is_dhl:
        return False
    access_cues = (
        "account",
        "acceso",
        "access",
        "approve",
        "aprobar",
        "reject",
        "rechazar",
        "solicitud de acceso",
        "solicitud cuenta",
        "solicitud de cuenta",
        "cuenta de dhl",
        "esecure",
        "one-time",
        "one time",
        "código",
        "codigo",
        "code",
    )
    return any(cue in hay for cue in access_cues)


def looks_like_vendor_logistics_contact(
    contact_email: str, subject: str | None
) -> bool:
    """Alias for legacy callers."""
    return looks_like_logistics_admin_contact(contact_email, subject)


# ---------------------------------------------------------------------------
# System / security noise
# ---------------------------------------------------------------------------


def _google_security_domain_alert(domain: str, sub: str) -> bool:
    if domain not in _SECURITY_CONTACT_DOMAINS:
        return False
    if any(m in sub for m in _SECURITY_SUBJECT_MARKERS):
        return True
    return "seguridad" in sub or "security" in sub


def _email_domain_matches_base(domain: str, base_domain: str) -> bool:
    """True when domain is exactly base_domain or a proper subdomain of it."""
    host = (domain or "").strip().lower()
    base = (base_domain or "").strip().lower()
    if not host or not base:
        return False
    return host == base or host.endswith(f".{base}")


def _sender_email_domain(sender: str | None) -> str:
    """Parse the sender address domain; ignore display-name substring tricks."""
    if not sender:
        return ""
    _display, addr = parseaddr(sender)
    candidate = (addr or "").strip().lower()
    if not candidate:
        found = emails_in(sender)
        candidate = found[0] if found else ""
    return email_domain(candidate)


def _google_security_sender_alert(snd: str, sub: str) -> bool:
    domain = _sender_email_domain(snd)
    if not _email_domain_matches_base(domain, "accounts.google.com"):
        return False
    return "seguridad" in sub or "security" in sub


def looks_like_system_noise_contact(
    contact_email: str,
    sender: str | None,
    subject: str | None,
) -> bool:
    email = _resolve_contact_email(contact_email, sender=sender)
    if email in _SYSTEM_NOISE_EMAILS:
        return True
    if looks_like_security_notification(sender, subject, contact_email=email):
        return True
    snd = (sender or "").lower()
    if "mailer-daemon" in snd or "mailer-daemon" in email:
        return True
    return False


def looks_like_suppressed_promotional_marketing_noise(
    contact_email: str,
    sender: str | None,
    subject: str | None,
    *,
    has_suppression_signal: bool = False,
) -> bool:
    """No-reply vendor promo with suppression flag — not a client response."""
    if not has_suppression_signal:
        return False
    email = _resolve_contact_email(contact_email, sender=sender)
    if "@" not in email:
        return False
    local_part = email.split("@", 1)[0]
    if local_part not in _NO_REPLY_LOCAL_PARTS:
        return False
    sub = (subject or "").lower()
    return any(marker in sub for marker in _PROMO_MARKETING_SUBJECT_MARKERS)


def looks_like_promotional_marketing_thread(
    contact_email: str,
    sender: str | None,
    subject: str | None,
    *,
    snippet: str | None = None,
) -> bool:
    """Explicit promotional blast, not an inbound buying opportunity."""
    email = _resolve_contact_email(contact_email, sender=sender)
    if is_internal_operator_contact(email):
        return False

    subject_l = (subject or "").lower()
    hay = _message_haystack(subject, snippet, sender)

    strong_cues = (
        "descuentos exclusivos",
        "exclusive discounts",
        "newsletter",
        "oferta especial",
        "special offer",
        "promoción especial",
        "promocion especial",
        "limited time offer",
    )
    if any(cue in hay for cue in strong_cues):
        return True

    has_promo_marker = any(
        marker in subject_l for marker in _PROMO_MARKETING_SUBJECT_MARKERS
    )
    has_percent_offer = bool(re.search(r"\b\d{1,2}\s*%", hay))
    return has_promo_marker and has_percent_offer


# ---------------------------------------------------------------------------
# Internal / operator / admin
# ---------------------------------------------------------------------------


def _internal_thread_haystack(
    subject: str | None,
    snippet: str | None,
    sender: str | None,
) -> str:
    return " ".join([subject or "", snippet or "", sender or ""]).lower()


def _personal_operator_admin_email_match(email: str, hay: str) -> bool:
    if email == "sebastian.rojas.vivanco@gmail.com" and "serva" in hay:
        return True
    if email == "tvivancob@gmail.com" and any(
        m in hay for m in _INTERNAL_ADMIN_SUBJECT_MARKERS
    ):
        return True
    return False


def looks_like_internal_admin_thread(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """Operator/internal notes (SERVA payment, Wise, etc.) — not client threads."""
    email = _resolve_contact_email(contact_email, sender=sender)
    if is_internal_operator_contact(email):
        if looks_like_internal_forwarded_client_quote_request(
            contact_email=email,
            subject=subject,
            snippet=snippet,
            sender=sender,
        ):
            return False
        return True
    hay = _internal_thread_haystack(subject, snippet, sender)
    return _personal_operator_admin_email_match(email, hay)


def looks_like_internal_forwarded_client_quote_request(
    *,
    contact_email: str,
    subject: str | None,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """Internal forward that carries a real external quote request."""
    email = _resolve_contact_email(contact_email, sender=sender)
    if not is_internal_operator_contact(email):
        return False
    hay = _internal_thread_haystack(subject, snippet, sender)
    has_forward_marker = any(
        marker in hay for marker in _FORWARDED_CLIENT_QUOTE_SUBJECT_MARKERS
    )
    has_quote_signal = any(marker in hay for marker in _CLIENT_QUOTE_REQUEST_MARKERS)
    return has_forward_marker and has_quote_signal


# ---------------------------------------------------------------------------
# Supplier / vendor quote
# ---------------------------------------------------------------------------


def _matched_supplier_quote_subject_markers(hay: str) -> list[str]:
    return [marker for marker in _SUPPLIER_QUOTE_SUBJECT_MARKERS if marker in hay]


def _weak_supplier_quote_markers_only(matched: list[str]) -> bool:
    return bool(matched) and all(
        marker in _WEAK_SUPPLIER_QUOTE_SUBJECT_MARKERS for marker in matched
    )


_SUPPLIER_NON_QUOTE_REPLY_CUES: tuple[str, ...] = (
    "feedback from customer",
    "feedback from the customer",
    "any feedback from",
    "before we share further brochures",
    "before we share further",
    "brochures and quotes",
    "not in our production line",
    "not in our production-line",
    "not in production line",
    "no longer in production",
    "not part of our product",
    "channel partner",
    "local distributor",
    "distributor in chile",
    "authorized distributor",
    "application worksheet",
    "provide as much information as possible",
    "configure and quote",
    "happy to configure and quote",
    "complete the attached worksheet",
    "contact our partner",
    "refer you to",
    "redirect you to",
    "reach out to our partner",
)


def looks_like_supplier_non_quote_reply(
    subject: str | None,
    snippet: str | None = None,
    *,
    sender: str | None = None,
) -> bool:
    """Vendor reply that is follow-up, routing, pre-quote, or no-fit — not a received quote."""
    hay = _message_haystack(subject, snippet, sender)
    return any(cue in hay for cue in _SUPPLIER_NON_QUOTE_REPLY_CUES)


def looks_like_auto_reply_text(
    subject: str | None,
    snippet: str | None = None,
) -> bool:
    """Supplier/client autoresponder — PT/ES/DE/EN vacation and office-closed cues."""
    hay = _supplier_quote_haystack(subject, snippet)
    if not hay.strip():
        return False
    if hay.strip().startswith("automatic reply"):
        return True
    return any(marker in hay for marker in _AUTO_REPLY_TEXT_MARKERS)


def looks_like_auto_reply_subject(subject: str | None) -> bool:
    """Subject-only autoreply check (API compat)."""
    return looks_like_auto_reply_text(subject, None)


def looks_like_real_supplier_quote_content(
    subject: str | None,
    snippet: str | None = None,
) -> bool:
    """Price/product/stock cues — not thread prefix alone."""
    hay = _supplier_quote_haystack(subject, snippet)
    if _REAL_QUOTE_PRICE_RE.search(hay):
        return True
    product_cues = (
        "rv10",
        "rv 10",
        "3812200",
        "reactor",
        "centrifug",
        "microplate",
        "fluorescence",
        "olt-hp",
        "olt hp",
        "stock dispon",
        "disponible",
        "disponibilidade",
        "in stock",
        "112,00",
        "112.00",
    )
    quote_words = ("precio", "price", "cotiz", "quote", "presupuesto", "quotation")
    if any(cue in hay for cue in product_cues) and any(
        word in hay for word in quote_words
    ):
        return True
    if "price response" in hay or "precio" in hay and "rv" in hay:
        return True
    if any(word in hay for word in quote_words) and any(
        cue in hay
        for cue in (
            "attached",
            "adjunt",
            "specs",
            "specification",
            "specifications",
            "datasheet",
            "ficha técnica",
            "ficha tecnica",
        )
    ):
        return True
    if any(c in hay for c in ("specifications", "specification", "specs")) and any(
        w in hay
        for w in ("reviewed", "comments", "attached", "find our", "quotation", "quote")
    ):
        return True
    return False


def looks_like_supplier_quote_response(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """Inbound supplier quote/price (e.g. IKA RV10.70), not a client opportunity."""
    email = _resolve_contact_email(contact_email, sender=sender)
    if not is_supplier_vendor_domain(email_domain(email)):
        return False
    if looks_like_supplier_admin_signup_subject(subject):
        return False
    if looks_like_auto_reply_text(subject, snippet):
        return looks_like_real_supplier_quote_content(subject, snippet)
    if looks_like_supplier_non_quote_reply(subject, snippet, sender=sender):
        return False
    hay = _supplier_quote_haystack(subject, snippet)
    matched = _matched_supplier_quote_subject_markers(hay)
    if not matched:
        return False
    if "solicitud de cotiz" in hay:
        return True
    return looks_like_real_supplier_quote_content(subject, snippet)


# ---------------------------------------------------------------------------
# Client post-sale / OC
# ---------------------------------------------------------------------------


def looks_like_client_oc_post_sale_subject(
    subject: str | None,
    *,
    snippet: str | None = None,
    account_name: str | None = None,
) -> bool:
    """CEAF/client OC threads — not bank-registration / payment setup."""
    hay = payment_admin_text_haystack(
        subject=subject,
        snippet=snippet,
        account_name=account_name,
    )
    if any(marker in hay for marker in CLIENT_OC_POST_SALE_MARKERS):
        return True
    return False


def looks_like_client_post_sale_subject(subject: str | None) -> bool:
    return looks_like_client_oc_post_sale_subject(subject)


# ---------------------------------------------------------------------------
# Visibility overrides (api.v_warm_case suppression gate)
# ---------------------------------------------------------------------------


def _warm_case_category_keeps_visible(category: str, subject: str | None) -> bool:
    if category not in (
        "supplier_reply",
        "quote_sent",
        "waiting_supplier",
        "waiting_client",
    ):
        return False
    if looks_like_cyberday_bulk_campaign_subject(subject):
        return False
    return True


def _client_oc_keeps_visible(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
) -> bool:
    return is_real_client_domain(
        email_domain(contact_email)
    ) and looks_like_client_oc_post_sale_subject(
        subject,
        snippet=snippet,
    )


def should_keep_visible_despite_suppression(
    contact_email: str,
    subject: str | None,
    *,
    category: str,
    snippet: str | None = None,
) -> bool:
    """Payment/logistics/supplier rows must stay in api.v_warm_case (status <> problem-only gate)."""
    if _warm_case_category_keeps_visible(category, subject):
        return True
    if looks_like_payment_admin_contact(contact_email, subject, snippet=snippet):
        return True
    if looks_like_vendor_logistics_contact(contact_email, subject):
        return True
    if _client_oc_keeps_visible(contact_email, subject, snippet=snippet):
        return True
    return False


def email_domain(contact_email: str) -> str:
    email = (contact_email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1]


def looks_like_security_notification(
    sender: str | None,
    subject: str | None,
    *,
    contact_email: str = "",
) -> bool:
    """Google / account security alerts — not commercial client threads."""
    email = _resolve_contact_email(contact_email, sender=sender)
    domain = email_domain(email)
    sub = (subject or "").lower()
    snd = (sender or "").lower()

    if _google_security_domain_alert(domain, sub):
        return True

    if _google_security_sender_alert(snd, sub):
        return True
    return False


def is_supplier_vendor_domain(domain: str) -> bool:
    return (domain or "").strip().lower() in SUPPLIER_VENDOR_DOMAINS


def looks_like_supplier_admin_signup_subject(subject: str | None) -> bool:
    sub = (subject or "").lower()
    return any(m in sub for m in _ADMIN_SIGNUP_SUBJECT_MARKERS)


# ---------------------------------------------------------------------------
# Campaign / outreach
# ---------------------------------------------------------------------------


def _cyberday_subject_matches(normalized: str, expected: str) -> bool:
    if normalized == expected:
        return True
    return (
        "cyberday" in normalized
        and "equipos de laboratorio seleccionados" in normalized
    )


def looks_like_cyberday_bulk_campaign_subject(subject: str | None) -> bool:
    """Exact CyberDay 2026 bulk-send subject (operator outreach, not warm client thread)."""
    if not subject:
        return False
    normalized = subject.strip().lower().replace("–", "-").replace("—", "-")
    expected = CYBERDAY_CAMPAIGN_SUBJECT.lower().replace("—", "-")
    return _cyberday_subject_matches(normalized, expected)


# ---------------------------------------------------------------------------
# Client / equipment opportunity threads
# ---------------------------------------------------------------------------


def looks_like_idiem_auto_acknowledgement(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """IDIEM institutional auto-ack — not a sales opportunity."""
    hay = _message_haystack(subject, snippet, sender)
    domain = email_domain(contact_email) or email_domain(
        contact_email_from_sender(sender)
    )
    if domain != "idiem.cl" and "idiem" not in hay:
        return False
    if domain == "idiem.cl" and (contact_email or "").startswith("no-reply@"):
        return True
    if looks_like_auto_reply_text(subject, snippet):
        return True
    return any(marker in hay for marker in _IDIEM_AUTO_ACK_MARKERS)


def looks_like_cesmec_catalogue_client_thread(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """CESMEC / Bureau Veritas catalogue follow-up — real client opportunity."""
    hay = _message_haystack(subject, snippet, sender)
    domain = email_domain(contact_email)
    if domain not in ("bureauveritas.com", "ceaf.cl") and not any(
        m in hay for m in ("cesmec", "bureau veritas", "bureauveritas")
    ):
        return False
    return any(marker in hay for marker in _CESMEC_CLIENT_MARKERS)


def looks_like_unach_hielscher_supplier_wait(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """UNACH + Hielscher scaling thread — operator waiting on supplier quote."""
    hay = _message_haystack(subject, snippet, sender)
    if not any(
        c in hay for c in ("unach", "universidad adventista", "[rch-", "uip2000")
    ):
        return False
    if "hielscher" not in hay and not is_supplier_vendor_domain(
        email_domain(contact_email)
    ):
        return False
    sender_domain = email_domain(contact_email_from_sender(sender))
    if is_supplier_vendor_domain(sender_domain) or is_supplier_vendor_domain(
        email_domain(contact_email)
    ):
        return True
    return "[rch-" in hay and "hielscher" in hay


def looks_like_contact_routing_notice(
    subject: str | None,
    snippet: str | None = None,
    *,
    sender: str | None = None,
) -> bool:
    """IST-style autorespuesta with forwarded contact (not a sales opportunity)."""
    hay = _message_haystack(subject, snippet, sender)
    if "autorespuesta" not in hay and "auto respuesta" not in hay:
        return False
    if "ist.cl" in hay:
        return True
    routing_cues = (
        "reenvi",
        "redirig",
        "forward",
        "forwarded",
        "contacto sugerido",
        "suggested contact",
        "se reenvían",
        "se reenvian",
        "automatically forwarded",
        "sebastian.cornejov",
    )
    return any(cue in hay for cue in routing_cues)


def looks_like_client_equipment_opportunity_thread(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """Client/university equipment thread (e.g. UNACH + Hielscher ultrasonic scaling)."""
    hay = _message_haystack(subject, snippet, sender)
    equipment_cues = (
        "hielscher",
        "ultrason",
        "sonicador",
        "uip2000",
        "up400st",
        "up200st",
        "extracción",
        "extraccion",
        "extraction",
        "reactor",
        "centrifug",
        "balanza",
        "incubad",
    )
    client_cues = (
        "universidad",
        "unach",
        "facultad",
        "susanaalfaro",
        "solicitud sobre",
        "evaluating",
        "escalamiento",
        "pilot",
        "semi-industrial",
    )
    has_equipment = any(cue in hay for cue in equipment_cues)
    has_client = any(cue in hay for cue in client_cues)
    domain = email_domain(contact_email)
    if is_chile_institution_client_domain(domain) and has_equipment:
        return True
    if has_client and has_equipment and "[rch-" in hay:
        return True
    if has_client and has_equipment and "universidad" in hay:
        return True
    return False


def looks_like_low_intent_client_acknowledgement(
    subject: str | None,
    snippet: str | None = None,
) -> bool:
    hay = _message_haystack(subject, snippet)
    low_cues = (
        "gracias por su información",
        "gracias por la información",
        "gracias por su informacion",
        "thank you for the information",
    )
    return any(cue in hay for cue in low_cues)


def looks_like_client_waiting_review_ack(
    subject: str | None,
    snippet: str | None = None,
    *,
    contact_email: str | None = None,
) -> bool:
    hay = _message_haystack(subject, snippet)
    normalized = hay.replace("á", "a")
    if "lo revisaremos" in hay or "lo revisaremos" in normalized:
        return True
    if "revisaremos" in hay and ("gracias" in hay or "thank" in hay):
        return True

    subject_l = (subject or "").strip().lower()
    snippet_l = (snippet or "").strip().lower().rstrip(".! ")

    quote_subject = any(
        marker in subject_l
        for marker in (
            "cotiz",
            "quotation",
            "quote",
            "presupuesto",
        )
    )
    terse_ack = snippet_l in {
        "gracias",
        "muchas gracias",
        "thanks",
        "thank you",
        "thank you very much",
    }
    if quote_subject and terse_ack:
        return True

    domain = email_domain(contact_email or "")
    subj_only = (subject or "").lower()
    if (
        domain == "uc.cl"
        and "origenlab" in subj_only
        and "equipos para laboratorio" in subj_only
    ):
        if subj_only.startswith("re:") or subj_only.startswith("re "):
            return True
    return False


def looks_like_supplier_followup_thread(
    contact_email: str,
    subject: str | None,
    *,
    snippet: str | None = None,
    sender: str | None = None,
) -> bool:
    """Supplier chase for quote receipt / shipping address — not a fresh price quote."""
    email = _resolve_contact_email(contact_email, sender=sender)
    if not is_supplier_vendor_domain(email_domain(email)):
        return False
    if looks_like_auto_reply_text(
        subject, snippet
    ) and not looks_like_real_supplier_quote_content(subject, snippet):
        return False
    if looks_like_supplier_non_quote_reply(subject, snippet, sender=sender):
        return True
    hay = _message_haystack(subject, snippet, sender)
    follow_cues = (
        "shipping",
        "shipment",
        "freight",
        "flete",
        "calculate shipping",
        "calculate the shipping",
        "address to calculate",
        "dirección para calcular",
        "direccion para calcular",
        "did you receive",
        "receive the quotation",
        "received the quotation",
        "recibió nuestra cotización",
        "recibio nuestra cotizacion",
        "review the quotation",
        "review our quotation",
    )
    has_follow = any(cue in hay for cue in follow_cues)
    has_price = looks_like_real_supplier_quote_content(subject, snippet)
    if has_follow and not has_price:
        return True
    if has_follow and "inquiry about our reactor" in hay:
        return True
    return False


def looks_like_supplier_marketing_thread(
    *,
    contact_email: str,
    sender: str | None = None,
    subject: str | None = None,
) -> bool:
    """Vendor/supplier outreach that must not be labeled client_reply."""
    email = _resolve_contact_email(contact_email, sender=sender)
    domain = email_domain(email)
    if is_supplier_vendor_domain(domain):
        return True

    sub = (subject or "").lower()
    snd = (sender or "").lower()
    if any(m in sub or m in snd for m in _SUPPLIER_SUBJECT_MARKERS):
        return True

    if looks_like_supplier_admin_signup_subject(subject):
        return True

    if email.startswith("sales@") or email.startswith("sales0"):
        if domain and domain not in {
            "gmail.com",
            "googlemail.com",
            "hotmail.com",
            "outlook.com",
            "yahoo.com",
        }:
            return True

    return False
