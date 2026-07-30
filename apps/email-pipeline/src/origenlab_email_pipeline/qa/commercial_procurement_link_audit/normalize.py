"""Normalization helpers for procurement signals (deterministic, no fuzzy match)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from origenlab_email_pipeline.commercial_identity.normalize import (
    domain_from_email,
    is_consumer_domain,
    is_institutional_domain,
    is_internal_domain,
    normalize_identity_email,
    safe_org_normalized,
)
from origenlab_email_pipeline.org_normalize import normalize_domain
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    MARKETPLACE_DOMAINS,
    WEAK_NAME_TOKENS,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.I)


def stable_token(label: str, value: str | None, *, n: int = 12) -> str:
    """Deterministic redacted token (never Python's randomized hash())."""
    raw = f"{label}|{(value or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:n]


def is_marketplace_domain(domain: str | None) -> bool:
    d = (domain or "").strip().lower()
    if not d:
        return False
    if d in MARKETPLACE_DOMAINS:
        return True
    return any(d == m or d.endswith("." + m) for m in ("mercadopublico.cl", "chilecompra.cl"))


def sanitize_buyer_domain(domain: str | None) -> str | None:
    """Institutional buyer domain only — never marketplace / consumer / internal."""
    d = normalize_domain(domain) if domain else None
    if not d:
        return None
    if is_marketplace_domain(d) or is_consumer_domain(d) or is_internal_domain(d):
        return None
    if not is_institutional_domain(d):
        return None
    return d


def extract_email(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    direct = normalize_identity_email(s)
    if direct:
        return direct
    m = _EMAIL_RE.search(s)
    if not m:
        return None
    return normalize_identity_email(m.group(0))


def is_weak_public_unit_name(name_norm: str | None, display: str | None = None) -> bool:
    """True when the buyer name is too generic for name-only auto-link."""
    blob = f"{name_norm or ''} {display or ''}".strip().lower()
    if not blob:
        return True
    # Very short normalized names are weak.
    if name_norm and len(name_norm) < 8:
        return True
    hits = sum(1 for tok in WEAK_NAME_TOKENS if tok in blob)
    # Single weak token + short remainder → review.
    if hits >= 1 and (not name_norm or len(name_norm) < 24):
        return True
    return False


def parse_raw_json(raw_json: str | None) -> dict[str, Any]:
    if not raw_json:
        return {}
    try:
        obj = json.loads(raw_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def pick_first(raw: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = raw.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def canonical_tender_key_from_raw(
    *,
    source_record_id: str | None,
    raw: dict[str, Any],
) -> tuple[str | None, str]:
    """Return (canonical_tender_key, key_kind).

    Prefer CodigoExterno (tender-level). Fall back to source_record_id (often line-level).
    """
    externo = pick_first(raw, "CodigoExterno", "codigo_externo", "CodigoLicitacion", "codigo_licitacion")
    if externo:
        return externo.strip(), "codigo_externo"
    line = pick_first(raw, "Codigo", "Correlativo", "codigo", "tender_id", "id")
    if line:
        return line.strip(), "line_or_record_id"
    sid = (source_record_id or "").strip()
    if sid:
        return sid, "source_record_id"
    return None, "missing"


def extract_status_fields(raw: dict[str, Any]) -> dict[str, str | None]:
    code = pick_first(raw, "CodigoEstado", "codigo_estado", "chilecompra_status_code", "EstadoCodigo") or None
    name = pick_first(raw, "Estado", "estado", "chilecompra_status", "NombreEstado") or None
    close = pick_first(
        raw,
        "FechaCierre",
        "fecha_cierre",
        "close_date",
        "Fechas.FechaCierre",
    ) or None
    pub = pick_first(
        raw,
        "FechaPublicacion",
        "fecha_publicacion",
        "FechaCreacion",
    ) or None
    return {
        "status_code": code,
        "status_name": name,
        "close_date": close,
        "publication_date": pub,
    }


def buyer_fields_from_raw_and_lead(
    *,
    org_name: str | None,
    domain: str | None,
    email: str | None,
    raw: dict[str, Any],
) -> dict[str, Any]:
    buyer_display = (org_name or "").strip() or pick_first(
        raw,
        "NombreOrganismo",
        "NombreUnidad",
        "OrganismoComprador",
        "Comprador",
        "NombreComprador",
    )
    buyer_norm = safe_org_normalized(buyer_display)
    email_n = extract_email(email) or extract_email(pick_first(raw, "Email", "Correo", "Mail", "email"))
    email_dom = domain_from_email(email_n) if email_n else None
    explicit_dom = sanitize_buyer_domain(domain) or sanitize_buyer_domain(
        pick_first(raw, "Dominio", "Website", "SitioWeb", "domain")
    )
    return {
        "buyer_display": buyer_display or None,
        "buyer_name_norm": buyer_norm,
        "email_norm": email_n,
        "email_domain": email_dom,
        "buyer_domain": explicit_dom,
        "weak_public_unit_name": is_weak_public_unit_name(buyer_norm, buyer_display),
    }


__all__ = [
    "buyer_fields_from_raw_and_lead",
    "canonical_tender_key_from_raw",
    "extract_email",
    "extract_status_fields",
    "is_marketplace_domain",
    "is_weak_public_unit_name",
    "parse_raw_json",
    "sanitize_buyer_domain",
    "stable_token",
]
