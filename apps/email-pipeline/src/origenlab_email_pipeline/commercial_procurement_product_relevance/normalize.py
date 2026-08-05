"""Deterministic product-text normalization for PR5D."""

from __future__ import annotations

import re
import unicodedata


_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_product_text(text: str | None) -> str:
    """Casefold, strip accents, collapse punctuation/whitespace; preserve tokens."""
    if text is None:
        return ""
    raw = str(text).strip()
    if not raw:
        return ""
    folded = strip_accents(raw).casefold()
    folded = _PUNCT_RE.sub(" ", folded)
    folded = _WS_RE.sub(" ", folded).strip()
    return folded


def stable_token_sort_key(text: str) -> str:
    """Order-insensitive fingerprint helper: sorted unique tokens."""
    tokens = sorted(set(normalize_product_text(text).split()))
    return " ".join(tokens)
