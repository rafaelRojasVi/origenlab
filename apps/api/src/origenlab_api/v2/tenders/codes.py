"""Mercado Público tender codes: parse a bare code or a portal link into one canonical code."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from origenlab_email_pipeline.chilecompra_api import build_licitacion_detail_url_for_codigo

# `1234567-12-LE26`, L1 codes like `1057500-25-L126`, `1057049-388-CO26`.
TENDER_CODE_RE = re.compile(r"^\d{1,8}-\d{1,4}-[A-Z][A-Z0-9]\d{2}$")
_PORTAL_HOSTS = ("mercadopublico.cl",)


class TenderReferenceInvalid(ValueError):
    """The text is not a tender code nor a link carrying one. `code` is machine readable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_tender_reference(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise TenderReferenceInvalid("tender_reference_invalid")
    if "://" in text or text.lower().startswith("www."):
        parts = urlparse(text if "://" in text else f"https://{text}")
        host = (parts.hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in _PORTAL_HOSTS):
            raise TenderReferenceInvalid("tender_reference_invalid")
        query = {k.lower(): v for k, v in parse_qs(parts.query).items()}
        for key in ("idlicitacion", "codigo"):
            for value in query.get(key, []):
                code = value.strip().upper()
                if TENDER_CODE_RE.match(code):
                    return code
        if "qs" in query:
            raise TenderReferenceInvalid("tender_code_not_in_link")
        raise TenderReferenceInvalid("tender_reference_invalid")
    code = text.upper()
    if not TENDER_CODE_RE.match(code):
        raise TenderReferenceInvalid("tender_reference_invalid")
    return code


def portal_url(code: str) -> str:
    return build_licitacion_detail_url_for_codigo(code)
