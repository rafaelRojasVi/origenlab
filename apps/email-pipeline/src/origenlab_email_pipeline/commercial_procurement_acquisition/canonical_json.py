"""Deterministic canonical JSON for acquisition fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_dumps(obj: Any) -> str:
    """UTF-8-ready canonical JSON: sorted object keys, stable separators.

    List order is preserved (meaningful for ordered source collections).
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_digest(obj: Any) -> str:
    return sha256_hex(canonical_json_dumps(obj))


def original_bytes_digest(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return sha256_bytes(raw.encode("utf-8"))
    return sha256_bytes(raw)


def digest_difference_notes() -> dict[str, str]:
    return {
        "original_byte_digest": (
            "SHA-256 of the original response bytes (or UTF-8 encoding of a "
            "string fixture). Sensitive to whitespace and key order in the source text."
        ),
        "canonical_json_digest": (
            "SHA-256 of sort_keys canonical JSON. Stable across key reordering; "
            "differs from original_byte_digest when source key order or whitespace differs."
        ),
        "parser_input_digest": (
            "Digest of the Python object graph actually passed to the parser after "
            "json.loads (equivalent to canonical_json_digest of that object)."
        ),
    }
