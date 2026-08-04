"""Shareable redaction helpers for PR5D (recursive, ChileCompra-aware)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_RUT_RE = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")
# ChileCompra / Mercado Público codes e.g. 3544-1-LE26, 1234-56-LP23
_CHILECOMPRA_CODE_RE = re.compile(
    r"\b\d{3,6}-\d{1,4}-[A-Z]{1,4}\d{2}\b",
    re.I,
)
_LEGACY_TENDER_RE = re.compile(r"\b[A-Z]{2,5}-\d{4}-\d{4,}\b")
_LONG_ID_RE = re.compile(r"\b\d{8,}\b")
_BUYER_MARKER_RE = re.compile(
    r"\b(buyer|organismo|comprador|rut\s*proveedor)\s*[:=]\s*\S+",
    re.I,
)
_SOURCE_RECORD_RE = re.compile(
    r"\b(?:src_rec_|source_record_|evidence_ref_|snapshot_|obs_|procurement_)[A-Za-z0-9_\-]+\b",
    re.I,
)
_RAW_SENTINEL_RE = re.compile(r"\bRAW_TEXT_SENTINEL_[A-Z0-9_]+\b")


def domain_redacted_alias(domain: str, stable_key: str) -> str:
    """Domain-separated alias — never a substring of the operational identifier."""
    digest = hashlib.sha256(f"pr5d_alias:{domain}:{stable_key}".encode("utf-8")).hexdigest()
    return f"redacted.{domain}.{digest[:20]}"


def redact_product_wording(text: str) -> tuple[str, dict[str, Any]]:
    """Redact PII and identifiers while retaining product wording."""
    original = text or ""
    # Identifier patterns before phone — digit-heavy codes must not be mangled by phone regex.
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", original)
    redacted = _URL_RE.sub("[REDACTED_URL]", redacted)
    redacted = _RUT_RE.sub("[REDACTED_TAX_ID]", redacted)
    redacted = _CHILECOMPRA_CODE_RE.sub("[REDACTED_CHILECOMPRA_CODE]", redacted)
    redacted = _LEGACY_TENDER_RE.sub("[REDACTED_TENDER_ID]", redacted)
    redacted = _SOURCE_RECORD_RE.sub("[REDACTED_SOURCE_RECORD_ID]", redacted)
    redacted = _RAW_SENTINEL_RE.sub("[REDACTED_RAW_SENTINEL]", redacted)
    redacted = _BUYER_MARKER_RE.sub("[REDACTED_BUYER_MARKER]", redacted)
    redacted = _PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = _LONG_ID_RE.sub("[REDACTED_ID]", redacted)
    proof = {
        "email_redactions": len(_EMAIL_RE.findall(original)),
        "phone_redactions": len(_PHONE_RE.findall(original)),
        "url_redactions": len(_URL_RE.findall(original)),
        "tax_id_redactions": len(_RUT_RE.findall(original)),
        "chilecompra_code_redactions": len(_CHILECOMPRA_CODE_RE.findall(original)),
        "source_record_redactions": len(_SOURCE_RECORD_RE.findall(original)),
        "sha256_original_normalized": hashlib.sha256(
            normalize_product_text(original).encode("utf-8")
        ).hexdigest(),
        "sha256_redacted_normalized": hashlib.sha256(
            normalize_product_text(redacted).encode("utf-8")
        ).hexdigest(),
        "retained_product_tokens": normalize_product_text(redacted).split()[:40],
    }
    return redacted, proof


def serialize_for_scan(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, default=str)


def assert_no_forbidden_substrings(
    artifact: Any,
    *,
    forbidden: list[str],
    context: str = "shareable_artifact",
) -> None:
    """Recursively verify no forbidden token survives anywhere in the artifact."""
    blob = serialize_for_scan(artifact)
    survivors = [tok for tok in forbidden if tok and tok in blob]
    if survivors:
        raise AssertionError(
            f"{context}: forbidden substrings survived recursive serialization: "
            f"{survivors[:10]}"
        )


def shareable_scan_forbidden_from_regression_inputs() -> list[str]:
    """Canonical regression sentinels that must never appear in shareable bundles."""
    return [
        "buyer@example.com",
        "+56 9 1234 5678",
        "https://secret.example/path",
        "12.345.678-9",
        "3544-1-LE26",
        "src_rec_UNIQUE_RAW_99",
        "BUYER:=HospitalSecreto",
        "RAW_TEXT_SENTINEL_XYZ_NEVER_SHARE",
    ]
