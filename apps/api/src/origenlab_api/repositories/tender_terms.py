"""File-backed T1 tender-terms repository (published read model, no DB).

Mirrors ``repositories/institution_prospects.py``: reads the atomically
published ANEXO-T1 bundle written by
``publish_tender_terms(..., out_dir=settings.resolved_tender_terms_dir())``
via ``load_published_tender_terms``. All contract/coverage semantics are
owned by ``commercial_procurement_anexo_tender_terms``; this module only
resolves a directory, calls the loader, and maps failures onto the
``reduced_mode``/``canonical_reason`` idiom used elsewhere in apps/api.

Fail-closed: missing directory, malformed publication, or unsupported
contract/terms version never raise past this module and never 500 the
calling route — they become an explicit ``reduced_mode=True`` meta with
``published=False``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.production_publish import (
    TENDER_TERMS_PUBLICATION_CONTRACT_VERSION,
    TenderTermsPublicationError,
    load_published_tender_terms,
)

from origenlab_api.schemas.tender_terms import TenderTermsMeta

_REASON_MISSING = "missing_tender_terms_publication"
_REASON_MALFORMED = "malformed_tender_terms_publication"
_REASON_OK = "tender_terms_read_model"
_REASON_NOT_PUBLISHED = "tender_id_not_in_publication"


def _empty_meta(*, canonical_reason: str, note: str, source_dir: Path) -> TenderTermsMeta:
    return TenderTermsMeta(
        source_path=str(source_dir),
        artifact_basename=source_dir.name,
        canonical_reason=canonical_reason,
        reduced_mode=True,
        published=False,
        note=note,
    )


def load_tender_terms_model_or_none(
    dest_dir: Path,
) -> tuple[dict[str, Any] | None, TenderTermsMeta]:
    """Load the published T1 bundle, or return (None, degraded meta) on any failure.

    Never raises: every documented failure mode (missing publication,
    malformed JSON/JSONL, contract mismatch) is converted into an explicit
    ``reduced_mode=True`` meta rather than a 500 or silently-empty result.
    """
    try:
        model = load_published_tender_terms(dest_dir)
    except TenderTermsPublicationError as exc:
        reason = _REASON_MISSING if "missing" in str(exc) else _REASON_MALFORMED
        return None, _empty_meta(
            canonical_reason=reason,
            note=f"Published tender-terms bundle unavailable: {exc}",
            source_dir=dest_dir,
        )
    except OSError as exc:
        return None, _empty_meta(
            canonical_reason=_REASON_MISSING,
            note=f"Published tender-terms directory unreadable: {exc}",
            source_dir=dest_dir,
        )

    summary = model.get("summary") or {}
    meta = TenderTermsMeta(
        contract_version=str(summary.get("contract_version") or ""),
        supported_contract_version=(
            summary.get("contract_version") == TENDER_TERMS_PUBLICATION_CONTRACT_VERSION
        ),
        terms_version=str(summary.get("terms_version") or ""),
        as_of_utc=str(summary.get("as_of_utc") or ""),
        source_path=str(dest_dir),
        artifact_basename=dest_dir.name,
        canonical_reason=_REASON_OK,
        reduced_mode=False,
        published=True,
        note="",
    )
    return model, meta


def get_tender_terms(
    dest_dir: Path,
    tender_id: str,
) -> tuple[dict[str, Any] | None, TenderTermsMeta]:
    """Return the T1 bundle dict for one tender_id, or (None, meta).

    ``meta.published`` distinguishes "no publication exists at all"
    (reduced_mode=True) from "publication exists but has no row for this
    tender_id" (published=True, reduced_mode=False, but the caller still
    gets ``None`` back and must render an honest not-published state).
    """
    model, meta = load_tender_terms_model_or_none(dest_dir)
    if model is None:
        return None, meta
    canonical_tender_id = tender_id.strip().casefold()
    for row in model.get("tender_terms") or []:
        row_tender_id = str(row.get("tender_id") or "").strip().casefold()
        if row_tender_id == canonical_tender_id:
            return row, meta
    # Publication is healthy but has nothing for this tender — not an error,
    # just "not published for this tender_id".
    not_found_meta = meta.model_copy(
        update={"canonical_reason": _REASON_NOT_PUBLISHED, "note": "No T1 publication row for this tender_id."}
    )
    return None, not_found_meta


__all__ = [
    "get_tender_terms",
    "load_tender_terms_model_or_none",
]
