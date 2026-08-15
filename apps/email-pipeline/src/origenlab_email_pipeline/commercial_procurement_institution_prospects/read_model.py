"""Read-only loader for a published institution-prospect plan bundle.

This module does not compute anything: it only reads back the JSON/CSV bundle
that ``build_institution_prospect_plan(..., out_dir=...)`` already writes
atomically via ``output_safety.write_atomically``. It exists so that
apps/api (or any other consumer) never re-derives queue membership, contract
shape, or field semantics — the planner package remains the single source of
semantic truth; this loader only projects its on-disk artifacts back into
Python values, decoding the JSON-encoded nested CSV cells that
``queues.canonical_queue_row`` writes.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONTRACT_VERSION,
    OPERATOR_QUEUE_NAMES,
)

# Every contract_version this loader knows how to interpret. A packet stamped
# with any other value is refused rather than guessed at (fail closed).
SUPPORTED_CONTRACT_VERSIONS: Final[frozenset[str]] = frozenset({CONTRACT_VERSION})

PACKET_FILENAME: Final[str] = "institution_prospect_packet.json"
SUMMARY_FILENAME: Final[str] = "summary.json"

# Per-queue field names whose CSV cell is a JSON-encoded list/dict, per
# queues.canonical_queue_row's `_cell()` serialization. Kept here (not
# re-derived from EMPTY_QUEUE_HEADERS) because only these specific fields are
# ever assigned list/tuple/dict/set values in queues.build_operator_queues.
_NESTED_LIST_FIELDS: Final[dict[str, frozenset[str]]] = {
    "current_opportunity_queue": frozenset({"reason_codes", "eligibility_reason_codes"}),
    "historical_prospect_queue": frozenset({"tender_codes", "review_dispositions"}),
    "institution_match_review_queue": frozenset(
        {"member_profile_ids", "identifier_conflicts", "cluster_reason_codes"}
    ),
    "contact_gap_queue": frozenset(),
    "line_evidence_review_queue": frozenset(
        {
            "equipment_scopes",
            "canonical_equipment_classes",
            "line_reason_codes",
            "ambiguity_reason_codes",
        }
    ),
    "retender_review_queue": frozenset(
        {"member_tender_codes", "family_reason_codes", "relationship_edges"}
    ),
}

_BOOL_FIELDS: Final[frozenset[str]] = frozenset(
    {"contact_authorization", "outreach_authorization", "identity_review_required", "confirmed_account"}
)


class PublishedReadModelError(RuntimeError):
    """Base class for institution-prospect read-model load failures."""


class PublishedReadModelMissingError(PublishedReadModelError):
    """No published bundle found at the requested directory."""


class PublishedReadModelMalformedError(PublishedReadModelError):
    """A bundle file exists but is not valid/parseable JSON or CSV."""


class UnsupportedContractVersionError(PublishedReadModelError):
    """The packet's contract_version is not one this loader understands."""

    def __init__(self, contract_version: str) -> None:
        self.contract_version = contract_version
        super().__init__(f"unsupported contract_version={contract_version!r}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PublishedReadModelMissingError(str(path)) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublishedReadModelMalformedError(f"{path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PublishedReadModelMalformedError(f"{path}: expected a JSON object")
    return parsed


def _coerce_bool(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def _decode_cell(field: str, value: str, *, nested_fields: frozenset[str]) -> Any:
    if field in _BOOL_FIELDS:
        return _coerce_bool(value)
    if field in nested_fields:
        text = (value or "").strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return [text]
    return value


def load_institution_prospect_packet(dest_dir: Path) -> dict[str, Any]:
    """Load and validate ``institution_prospect_packet.json`` from a published bundle.

    Raises ``PublishedReadModelMissingError``, ``PublishedReadModelMalformedError``,
    or ``UnsupportedContractVersionError`` rather than returning a partial/guessed
    result.
    """
    packet_path = dest_dir / PACKET_FILENAME
    packet = _read_json(packet_path)
    contract_version = str(packet.get("contract_version") or "")
    if contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        raise UnsupportedContractVersionError(contract_version)
    if not isinstance(packet.get("profiles"), list):
        raise PublishedReadModelMalformedError(
            f"{packet_path}: 'profiles' is missing or not a list"
        )
    return packet


def load_institution_prospect_summary(dest_dir: Path) -> dict[str, Any] | None:
    """Load ``summary.json`` if present. Non-fatal: returns None when absent/malformed."""
    summary_path = dest_dir / SUMMARY_FILENAME
    if not summary_path.is_file():
        return None
    try:
        return _read_json(summary_path)
    except PublishedReadModelError:
        return None


def load_operator_queue(dest_dir: Path, queue_name: str) -> list[dict[str, Any]]:
    """Load and decode one published operator-queue CSV.

    A missing CSV means the bundle is incomplete (raises); a CSV containing only
    the header row is a genuinely empty (but valid) queue and returns ``[]``.
    """
    if queue_name not in OPERATOR_QUEUE_NAMES:
        raise ValueError(f"unknown queue_name={queue_name!r}")
    csv_path = dest_dir / f"{queue_name}.csv"
    if not csv_path.is_file():
        raise PublishedReadModelMissingError(str(csv_path))
    nested_fields = _NESTED_LIST_FIELDS.get(queue_name, frozenset())
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows: list[dict[str, Any]] = []
            for raw_row in reader:
                rows.append(
                    {
                        field: _decode_cell(field, value or "", nested_fields=nested_fields)
                        for field, value in raw_row.items()
                        if field is not None
                    }
                )
    except csv.Error as exc:
        raise PublishedReadModelMalformedError(f"{csv_path}: {exc}") from exc
    return rows


def load_published_read_model(dest_dir: Path) -> dict[str, Any]:
    """Load the full published institution-prospect read model for one bundle directory.

    This is the single entry point apps/api should call: it validates the
    packet contract version, loads all six operator queues, and returns one
    envelope dict. Raises ``PublishedReadModelMissingError``,
    ``PublishedReadModelMalformedError``, or ``UnsupportedContractVersionError``
    on any structural problem — callers must not silently substitute an empty
    result for a load failure.
    """
    packet = load_institution_prospect_packet(dest_dir)
    summary = load_institution_prospect_summary(dest_dir)
    queues = {name: load_operator_queue(dest_dir, name) for name in OPERATOR_QUEUE_NAMES}
    operator_queue_sizes = (
        summary.get("operator_queue_sizes")
        if isinstance(summary, dict) and isinstance(summary.get("operator_queue_sizes"), dict)
        else {name: len(rows) for name, rows in queues.items()}
    )
    packet_path = dest_dir / PACKET_FILENAME
    try:
        generated_at_utc = (
            datetime.fromtimestamp(packet_path.stat().st_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except OSError:
        generated_at_utc = ""
    fingerprints = packet.get("fingerprints") if isinstance(packet.get("fingerprints"), dict) else {}
    return {
        "contract_version": packet.get("contract_version"),
        "planner_version": packet.get("planner_version"),
        "recognition_layer_version": packet.get("recognition_layer_version"),
        "as_of_utc": packet.get("as_of_utc"),
        "run_context": packet.get("run_context"),
        "generated_at_utc": generated_at_utc,
        "not_persisted": bool(packet.get("not_persisted", True)),
        "contact_authorization": False,
        "outreach_authorization": False,
        "source_digest": str(fingerprints.get("build_fingerprint") or ""),
        "fingerprints": fingerprints,
        "profiles": packet.get("profiles") or [],
        "counts": packet.get("counts") or {},
        "operator_queue_sizes": operator_queue_sizes,
        "summary_ok": bool(summary.get("ok")) if isinstance(summary, dict) else None,
        "queues": queues,
        "source_dir": str(dest_dir),
    }


__all__ = [
    "PACKET_FILENAME",
    "SUMMARY_FILENAME",
    "SUPPORTED_CONTRACT_VERSIONS",
    "PublishedReadModelError",
    "PublishedReadModelMalformedError",
    "PublishedReadModelMissingError",
    "UnsupportedContractVersionError",
    "load_institution_prospect_packet",
    "load_institution_prospect_summary",
    "load_operator_queue",
    "load_published_read_model",
]
