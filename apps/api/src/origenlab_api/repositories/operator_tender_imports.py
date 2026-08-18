"""Persistent per-tender operator annex/T1 overlay.

This store is deliberately separate from the canonical T1 publication:
saving one tender can never replace the multi-tender canonical bundle, and a
later canonical T1 sync can never erase an operator-saved dossier.

Only validated structured T1/provenance is persisted. Raw ZIP bytes are never
written here.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    assert_no_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    validate_tender_terms_row,
)

from origenlab_api.schemas.tender_terms import TenderTermsMeta

OPERATOR_TENDER_IMPORT_CONTRACT_VERSION = "operator_tender_import_v1"
_TENDER_CODE_RE = re.compile(r"^[A-Za-z0-9-]+$")


def _canonical_tender_code(tender_code: str) -> str:
    value = tender_code.strip()
    if not value or not _TENDER_CODE_RE.fullmatch(value):
        raise ValueError("invalid tender_code")
    return value.casefold()


def _path_for(dest_dir: Path, tender_code: str) -> Path:
    return dest_dir / f"{_canonical_tender_code(tender_code)}.json"


def _load_path(path: Path, tender_code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    assert_no_portal_tokens(text, where="operator_tender_import")

    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError("operator import envelope must be an object")

    if decoded.get("contract_version") != OPERATOR_TENDER_IMPORT_CONTRACT_VERSION:
        raise ValueError("unsupported operator import contract")

    expected = _canonical_tender_code(tender_code)
    actual = _canonical_tender_code(str(decoded.get("tender_code") or ""))
    if actual != expected:
        raise ValueError("operator import tender_code mismatch")

    saved_at = decoded.get("saved_at_utc")
    if not isinstance(saved_at, str) or not saved_at:
        raise ValueError("saved_at_utc missing")

    terms = validate_tender_terms_row(decoded.get("terms"))
    if _canonical_tender_code(str(terms.get("tender_id") or "")) != expected:
        raise ValueError("saved T1 tender_id mismatch")

    return decoded, terms


def load_operator_tender_import(
    dest_dir: Path,
    tender_code: str,
) -> tuple[dict[str, Any] | None, TenderTermsMeta | None]:
    """Load one saved operator import.

    ``meta is None`` means no overlay exists and the caller may fall back to
    canonical T1. A malformed existing overlay fails closed and returns an
    explicit reduced-mode meta rather than silently falling back.
    """
    path = _path_for(dest_dir, tender_code)
    if not path.is_file():
        return None, None

    try:
        envelope, terms = _load_path(path, tender_code)
    except Exception as exc:  # noqa: BLE001 - fail closed at persisted boundary
        return None, TenderTermsMeta(
            data_source="operator_tender_import",
            source_kind="operator_annex_import",
            source_path=str(dest_dir),
            artifact_basename=path.name,
            canonical_reason="malformed_operator_tender_import",
            reduced_mode=True,
            published=False,
            note=f"Saved operator annex import is invalid: {type(exc).__name__}",
        )

    return terms, TenderTermsMeta(
        data_source="operator_tender_import",
        contract_version=OPERATOR_TENDER_IMPORT_CONTRACT_VERSION,
        supported_contract_version=True,
        terms_version=str(terms.get("terms_version") or ""),
        as_of_utc=str(envelope["saved_at_utc"]),
        source_path=str(dest_dir),
        source_kind="operator_annex_import",
        artifact_basename=path.name,
        canonical_reason="operator_tender_import",
        reduced_mode=False,
        published=True,
        note="Validated operator-saved annex evidence overlay.",
        contact_authorization=False,
        outreach_authorization=False,
    )


def save_operator_tender_import(
    dest_dir: Path,
    tender_code: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Validate and atomically save one operator-imported tender.

    The raw ZIP is intentionally absent from the persisted envelope.
    """
    if raw.get("result") != "imported":
        raise ValueError("only successfully imported annex bundles may be persisted")

    canonical = _canonical_tender_code(tender_code)
    if _canonical_tender_code(str(raw.get("tender_code") or "")) != canonical:
        raise ValueError("raw import tender_code mismatch")

    terms = validate_tender_terms_row(raw.get("terms"))
    if _canonical_tender_code(str(terms.get("tender_id") or "")) != canonical:
        raise ValueError("T1 tender_id mismatch")

    envelope: dict[str, Any] = {
        "contract_version": OPERATOR_TENDER_IMPORT_CONTRACT_VERSION,
        "tender_code": tender_code,
        "saved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provenance": raw.get("provenance") or {},
        "archive": raw.get("archive") or {},
        "bundle_complete": bool(raw.get("bundle_complete")),
        "incomplete_reason_codes": list(raw.get("incomplete_reason_codes") or []),
        "terms": terms,
        "contact_authorization": False,
        "outreach_authorization": False,
    }

    text = (
        json.dumps(
            envelope,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )
    assert_no_portal_tokens(text, where="operator_tender_import")

    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest_dir.is_symlink():
        raise ValueError("operator import directory must not be a symlink")

    target = _path_for(dest_dir, tender_code)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(dest_dir),
        text=True,
    )
    temp = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        # Validate the exact bytes that will be promoted while the previous
        # canonical per-tender file is still untouched.
        _load_path(temp, tender_code)

        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)

    # Final readback is another fail-closed integrity check.
    saved, meta = load_operator_tender_import(dest_dir, tender_code)
    if saved is None or meta is None or meta.reduced_mode:
        raise ValueError("operator import failed post-write validation")

    return envelope


__all__ = [
    "OPERATOR_TENDER_IMPORT_CONTRACT_VERSION",
    "load_operator_tender_import",
    "save_operator_tender_import",
]
