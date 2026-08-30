"""File-backed institution-prospect repository (published read model, no DB).

Reads the atomically-published institution-prospect bundle written by
``build_institution_prospect_plan(..., out_dir=settings.resolved_institution_prospect_dir())``.
All parsing/decoding is delegated to
``origenlab_email_pipeline.commercial_procurement_institution_prospects.read_model``
so this module never reimplements queue or contract semantics — it only
resolves a directory, calls the loader, and maps failures onto the
``reduced_mode``/``canonical_reason`` feed-status idiom used elsewhere in
apps/api (see ``repositories/tender_terms.py``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.read_model import (
    PublishedReadModelMalformedError,
    PublishedReadModelMissingError,
    UnsupportedContractVersionError,
    load_published_read_model,
)

from origenlab_api.schemas.institution_prospects import (
    QUEUE_NAME_TO_FILE_STEM,
    InstitutionProspectMeta,
)

_SOURCE_KIND = "institution_prospect_bundle"
_REASON_MISSING = "missing_institution_prospect_packet"
_REASON_MALFORMED = "malformed_institution_prospect_packet"
_REASON_UNSUPPORTED_CONTRACT = "unsupported_contract_version"
_REASON_OK = "institution_prospect_read_model"

DEFAULT_STALE_AFTER_HOURS = 48.0


def _empty_meta(*, canonical_reason: str, note: str, source_dir: Path) -> InstitutionProspectMeta:
    return InstitutionProspectMeta(
        read_only=True,
        contract_version="",
        supported_contract_version=False,
        source_path=str(source_dir),
        source_kind=_SOURCE_KIND,
        artifact_basename=source_dir.name,
        canonical_reason=canonical_reason,
        reduced_mode=True,
        note=note,
    )


def _is_stale(as_of_utc: str, *, max_age_hours: float) -> bool:
    text = (as_of_utc or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return age > timedelta(hours=max_age_hours)


def load_read_model_or_none(
    dest_dir: Path,
    *,
    max_stale_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> tuple[dict[str, Any] | None, InstitutionProspectMeta]:
    """Load the published bundle, or return (None, degraded meta) on any failure.

    Never raises: every documented failure mode (missing bundle, malformed
    JSON/CSV, unsupported contract version) is converted into an explicit
    ``reduced_mode=True`` meta rather than a 500 or a silently-empty result.
    """
    try:
        model = load_published_read_model(dest_dir)
    except PublishedReadModelMissingError:
        return None, _empty_meta(
            canonical_reason=_REASON_MISSING,
            note=(
                "No published institution_prospect_packet.json / operator queue "
                "CSVs found under the configured institution-prospect directory."
            ),
            source_dir=dest_dir,
        )
    except UnsupportedContractVersionError as exc:
        return None, InstitutionProspectMeta(
            read_only=True,
            contract_version=exc.contract_version,
            supported_contract_version=False,
            source_path=str(dest_dir),
            source_kind=_SOURCE_KIND,
            artifact_basename=dest_dir.name,
            canonical_reason=_REASON_UNSUPPORTED_CONTRACT,
            reduced_mode=True,
            note=(
                f"Published contract_version={exc.contract_version!r} is not "
                "supported by this API build."
            ),
        )
    except PublishedReadModelMalformedError as exc:
        return None, _empty_meta(
            canonical_reason=_REASON_MALFORMED,
            note=f"Published institution-prospect bundle is malformed: {exc}",
            source_dir=dest_dir,
        )

    stale = _is_stale(str(model.get("as_of_utc") or ""), max_age_hours=max_stale_hours)
    meta = InstitutionProspectMeta(
        read_only=True,
        contract_version=str(model.get("contract_version") or ""),
        supported_contract_version=True,
        planner_version=str(model.get("planner_version") or ""),
        recognition_layer_version=str(model.get("recognition_layer_version") or ""),
        as_of_utc=str(model.get("as_of_utc") or ""),
        run_context=str(model.get("run_context") or ""),
        generated_at_utc=str(model.get("generated_at_utc") or ""),
        source_digest=str(model.get("source_digest") or ""),
        source_path=str(dest_dir),
        source_kind=_SOURCE_KIND,
        artifact_basename=dest_dir.name,
        canonical_reason=_REASON_OK,
        reduced_mode=False,
        stale=stale,
        note="",
        not_persisted=bool(model.get("not_persisted", True)),
    )
    return model, meta


def get_institution_prospect_status(
    dest_dir: Path,
    *,
    max_stale_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> dict[str, Any]:
    model, meta = load_read_model_or_none(dest_dir, max_stale_hours=max_stale_hours)
    if model is None:
        return {"meta": meta, "counts": {}, "operator_queue_sizes": {}, "summary_ok": None}
    return {
        "meta": meta,
        "counts": model.get("counts") or {},
        "operator_queue_sizes": model.get("operator_queue_sizes") or {},
        "summary_ok": model.get("summary_ok"),
    }


def _matches_search(profile: dict[str, Any], query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    haystacks = [
        str(profile.get("institution_id") or ""),
        str((profile.get("identity") or {}).get("display_name") or ""),
        str((profile.get("identity") or {}).get("normalized_name") or ""),
    ]
    return any(q in h.lower() for h in haystacks)


def list_institutions(
    dest_dir: Path,
    *,
    limit: int = 50,
    offset: int = 0,
    institution_id: str | None = None,
    search: str | None = None,
    max_stale_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> tuple[list[dict[str, Any]], InstitutionProspectMeta, int]:
    model, meta = load_read_model_or_none(dest_dir, max_stale_hours=max_stale_hours)
    if model is None:
        return [], meta, 0
    profiles: list[dict[str, Any]] = list(model.get("profiles") or [])
    if institution_id:
        profiles = [p for p in profiles if str(p.get("institution_id") or "") == institution_id]
    if search:
        profiles = [p for p in profiles if _matches_search(p, search)]
    profiles = sorted(profiles, key=lambda p: str(p.get("institution_id") or ""))
    total = len(profiles)
    page = profiles[offset : offset + limit]
    return page, meta, total


def get_institution_detail(
    dest_dir: Path,
    institution_id: str,
    *,
    max_stale_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> tuple[dict[str, Any] | None, InstitutionProspectMeta]:
    model, meta = load_read_model_or_none(dest_dir, max_stale_hours=max_stale_hours)
    if model is None:
        return None, meta
    for profile in model.get("profiles") or []:
        if str(profile.get("institution_id") or "") == institution_id:
            return profile, meta
    return None, meta


def _matches_row_filters(
    row: dict[str, Any],
    *,
    institution_id: str | None,
    tender_code: str | None,
    equipment_category: str | None,
    commercial_signal_type: str | None,
    search: str | None,
) -> bool:
    if institution_id is not None and str(row.get("institution_id") or "") != institution_id:
        return False
    # tender_code/equipment_category/commercial_signal_type are stored lowercase
    # in the published queue CSVs (canonical_tender_key convention); match
    # case-insensitively so a caller passing the human-displayed code (e.g.
    # "745712-19-LP26") is not silently dropped.
    if (
        tender_code is not None
        and str(row.get("tender_code") or "").lower() != tender_code.lower()
    ):
        return False
    if (
        equipment_category is not None
        and str(row.get("equipment_category") or "").lower() != equipment_category.lower()
    ):
        return False
    if (
        commercial_signal_type is not None
        and str(row.get("commercial_signal_type") or "").lower()
        != commercial_signal_type.lower()
    ):
        return False
    if search:
        q = search.strip().lower()
        haystacks = [
            str(row.get("display_name") or ""),
            str(row.get("institution_id") or ""),
            str(row.get("tender_code") or ""),
        ]
        if not any(q in h.lower() for h in haystacks):
            return False
    return True


def list_queue_rows(
    dest_dir: Path,
    queue_route_name: str,
    *,
    limit: int = 50,
    offset: int = 0,
    institution_id: str | None = None,
    tender_code: str | None = None,
    equipment_category: str | None = None,
    commercial_signal_type: str | None = None,
    search: str | None = None,
    max_stale_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> tuple[list[dict[str, Any]], InstitutionProspectMeta, int]:
    model, meta = load_read_model_or_none(dest_dir, max_stale_hours=max_stale_hours)
    if model is None:
        return [], meta, 0
    file_stem = QUEUE_NAME_TO_FILE_STEM[queue_route_name]
    rows: list[dict[str, Any]] = list((model.get("queues") or {}).get(file_stem) or [])
    rows = [
        r
        for r in rows
        if _matches_row_filters(
            r,
            institution_id=institution_id,
            tender_code=tender_code,
            equipment_category=equipment_category,
            commercial_signal_type=commercial_signal_type,
            search=search,
        )
    ]
    total = len(rows)
    page = rows[offset : offset + limit]
    return page, meta, total


__all__ = [
    "DEFAULT_STALE_AFTER_HOURS",
    "get_institution_detail",
    "get_institution_prospect_status",
    "list_institutions",
    "list_queue_rows",
    "load_read_model_or_none",
]
