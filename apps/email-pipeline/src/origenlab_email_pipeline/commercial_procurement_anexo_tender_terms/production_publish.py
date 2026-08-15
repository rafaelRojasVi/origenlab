"""Validated atomic publication for ANEXO-T1 structured tender terms.

This module performs no ChileCompra or Mercado Público acquisition. It accepts
already-built TenderAttachmentBundle objects, applies the existing deterministic
T1 extractor, validates the serialized publication, and only then atomically
promotes it to the canonical output directory.

A failed extraction, serialization, validation, or promote never replaces the
previous canonical publication.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    TenderAttachmentBundle,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    assert_no_portal_tokens,
    redact_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)

from .constants import FACT_STATES, TENDER_TERMS_VERSION
from .extract import extract_tender_terms
from .fingerprint import tender_terms_semantic_digest
from .models import (
    FactCandidate,
    TenderItemTerms,
    TenderTermFact,
    TenderTermsBundle,
    TenderTermsCoverage,
    TermEvidence,
)

TENDER_TERMS_PUBLICATION_CONTRACT_VERSION = "tender_terms_publication_v1"
TENDER_TERMS_DIRNAME = "tender_terms"
SUMMARY_FILENAME = "summary.json"
TENDER_TERMS_FILENAME = "tender_terms.jsonl"


class TenderTermsPublicationError(ValueError):
    """Published T1 bundle is missing, malformed, or violates its contract."""


@dataclass(frozen=True)
class TenderTermsPublicationResult:
    result: str  # "applied" | "failed"
    output_dir_basename: str
    contract_version: str | None = None
    terms_version: str | None = None
    as_of_utc: str | None = None
    tender_count: int | None = None
    complete_coverage_count: int | None = None
    incomplete_coverage_count: int | None = None
    publication_digest: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "output_dir_basename": self.output_dir_basename,
            "contract_version": self.contract_version,
            "terms_version": self.terms_version,
            "as_of_utc": self.as_of_utc,
            "tender_count": self.tender_count,
            "complete_coverage_count": self.complete_coverage_count,
            "incomplete_coverage_count": self.incomplete_coverage_count,
            "publication_digest": self.publication_digest,
            "error": self.error,
        }


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TenderTermsPublicationError(f"{field} must be an object")
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise TenderTermsPublicationError(f"{field} must be an array")
    return value


def _require_str(value: Any, field: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise TenderTermsPublicationError(f"{field} must be a string")
    if nonempty and not value.strip():
        raise TenderTermsPublicationError(f"{field} must be non-empty")
    return value


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field)


def _require_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise TenderTermsPublicationError(f"{field} must be a boolean")
    return value


def _require_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise TenderTermsPublicationError(f"{field} must be an integer")
    return value


def _str_tuple(value: Any, field: str) -> tuple[str, ...]:
    items = _require_list(value, field)
    return tuple(_require_str(item, f"{field}[]") for item in items)


def _validate_as_of_utc(raw: Any) -> str:
    value = _require_str(raw, "as_of_utc", nonempty=True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TenderTermsPublicationError("as_of_utc must be ISO-8601") from exc

    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise TenderTermsPublicationError("as_of_utc must carry a UTC offset")

    return value


def _term_evidence_from_dict(data: Any) -> TermEvidence:
    row = _require_dict(data, "evidence")
    return TermEvidence(
        evidence_id=_require_str(
            row.get("evidence_id"), "evidence.evidence_id", nonempty=True
        ),
        attachment_id=_require_str(
            row.get("attachment_id"), "evidence.attachment_id", nonempty=True
        ),
        evidence_attachment_id=_require_str(
            row.get("evidence_attachment_id"),
            "evidence.evidence_attachment_id",
            nonempty=True,
        ),
        safe_filename=_require_str(row.get("safe_filename"), "evidence.safe_filename"),
        attachment_sha256=_require_str(
            row.get("attachment_sha256"),
            "evidence.attachment_sha256",
            nonempty=True,
        ),
        detected_format=_require_str(
            row.get("detected_format"), "evidence.detected_format"
        ),
        document_role=_require_str(row.get("document_role"), "evidence.document_role"),
        chunk_id=_require_str(row.get("chunk_id"), "evidence.chunk_id", nonempty=True),
        locator_type=_require_str(
            row.get("locator_type"), "evidence.locator_type", nonempty=True
        ),
        locator=_require_dict(row.get("locator"), "evidence.locator"),
        locator_display=_require_str(
            row.get("locator_display"), "evidence.locator_display"
        ),
        evidence_excerpt=_require_str(
            row.get("evidence_excerpt"), "evidence.evidence_excerpt"
        ),
        evidence_text_sha256=_require_str(
            row.get("evidence_text_sha256"),
            "evidence.evidence_text_sha256",
            nonempty=True,
        ),
        container_attachment_id=_optional_str(
            row.get("container_attachment_id"),
            "evidence.container_attachment_id",
        ),
        member_path=_optional_str(row.get("member_path"), "evidence.member_path"),
    )


def _candidate_from_dict(data: Any) -> FactCandidate:
    row = _require_dict(data, "candidate")
    return FactCandidate(
        candidate_id=_require_str(
            row.get("candidate_id"), "candidate.candidate_id", nonempty=True
        ),
        value=row.get("value"),
        evidence=tuple(
            _term_evidence_from_dict(item)
            for item in _require_list(row.get("evidence"), "candidate.evidence")
        ),
    )


def _fact_from_dict(data: Any) -> TenderTermFact:
    row = _require_dict(data, "fact")
    return TenderTermFact(
        fact_id=_require_str(row.get("fact_id"), "fact.fact_id", nonempty=True),
        field_name=_require_str(
            row.get("field_name"), "fact.field_name", nonempty=True
        ),
        state=_require_str(row.get("state"), "fact.state", nonempty=True),
        coverage_status=_require_str(
            row.get("coverage_status"), "fact.coverage_status", nonempty=True
        ),
        value=row.get("value"),
        value_type=_optional_str(row.get("value_type"), "fact.value_type"),
        unit=_optional_str(row.get("unit"), "fact.unit"),
        currency=_optional_str(row.get("currency"), "fact.currency"),
        tax_basis=_optional_str(row.get("tax_basis"), "fact.tax_basis"),
        evidence=tuple(
            _term_evidence_from_dict(item)
            for item in _require_list(row.get("evidence"), "fact.evidence")
        ),
        candidates=tuple(
            _candidate_from_dict(item)
            for item in _require_list(row.get("candidates"), "fact.candidates")
        ),
        reason_codes=_str_tuple(row.get("reason_codes"), "fact.reason_codes"),
    )


def _item_from_dict(data: Any) -> TenderItemTerms:
    row = _require_dict(data, "item")
    return TenderItemTerms(
        item_id=_require_str(row.get("item_id"), "item.item_id", nonempty=True),
        item_number=_optional_str(row.get("item_number"), "item.item_number"),
        item_label=_optional_str(row.get("item_label"), "item.item_label"),
        facts=tuple(
            _fact_from_dict(item)
            for item in _require_list(row.get("facts"), "item.facts")
        ),
    )


def _coverage_from_dict(data: Any) -> TenderTermsCoverage:
    row = _require_dict(data, "coverage")
    outcome_counts_raw = _require_dict(
        row.get("outcome_counts"),
        "coverage.outcome_counts",
    )
    outcome_counts = {
        _require_str(key, "coverage.outcome_counts key", nonempty=True): _require_int(
            value,
            f"coverage.outcome_counts.{key}",
        )
        for key, value in outcome_counts_raw.items()
    }

    coverage = TenderTermsCoverage(
        has_source_bundle=_require_bool(
            row.get("has_source_bundle"), "coverage.has_source_bundle"
        ),
        extraction_complete=_require_bool(
            row.get("extraction_complete"), "coverage.extraction_complete"
        ),
        attachments_discovered=_require_int(
            row.get("attachments_discovered"),
            "coverage.attachments_discovered",
        ),
        attachments_downloaded=_require_int(
            row.get("attachments_downloaded"),
            "coverage.attachments_downloaded",
        ),
        incomplete_reason_codes=_str_tuple(
            row.get("incomplete_reason_codes"),
            "coverage.incomplete_reason_codes",
        ),
        unread_attachment_ids=_str_tuple(
            row.get("unread_attachment_ids"),
            "coverage.unread_attachment_ids",
        ),
        outcome_counts=outcome_counts,
    )

    derived = {
        "debt_outcome_counts": coverage.debt_outcome_counts,
        "is_complete": coverage.is_complete,
        "fact_coverage_status": coverage.fact_coverage_status,
    }
    for field, expected in derived.items():
        if row.get(field) != expected:
            raise TenderTermsPublicationError(
                f"coverage.{field} does not match its derived value"
            )

    return coverage


def _bundle_from_dict(data: Any) -> TenderTermsBundle:
    row = _require_dict(data, "tender_terms row")

    for field in (
        "contact_authorization",
        "outreach_authorization",
        "production_queue_mutated",
        "persisted",
    ):
        if row.get(field) is not False:
            raise TenderTermsPublicationError(f"{field} must be false")

    bundle = TenderTermsBundle(
        tender_id=_require_str(row.get("tender_id"), "tender_id", nonempty=True),
        source_bundle_semantic_digest=_require_str(
            row.get("source_bundle_semantic_digest"),
            "source_bundle_semantic_digest",
            nonempty=True,
        ),
        tender_facts=tuple(
            _fact_from_dict(item)
            for item in _require_list(row.get("tender_facts"), "tender_facts")
        ),
        items=tuple(
            _item_from_dict(item) for item in _require_list(row.get("items"), "items")
        ),
        coverage=_coverage_from_dict(row.get("coverage")),
        terms_version=_require_str(
            row.get("terms_version"), "terms_version", nonempty=True
        ),
        semantic_digest=_require_str(
            row.get("semantic_digest"), "semantic_digest", nonempty=True
        ),
        contact_authorization=False,
        outreach_authorization=False,
        production_queue_mutated=False,
        persisted=False,
    )

    if bundle.terms_version != TENDER_TERMS_VERSION:
        raise TenderTermsPublicationError(
            f"unsupported terms_version: {bundle.terms_version}"
        )

    recomputed = tender_terms_semantic_digest(bundle)
    if recomputed != bundle.semantic_digest:
        raise TenderTermsPublicationError(
            f"semantic digest mismatch for tender {bundle.tender_id}"
        )

    return bundle


def _fact_state_counts(
    bundles: Iterable[TenderTermsBundle],
) -> dict[str, int]:
    counts = {state: 0 for state in sorted(FACT_STATES)}

    for bundle in bundles:
        for fact in bundle.tender_facts:
            counts[fact.state] += 1
        for item in bundle.items:
            for fact in item.facts:
                counts[fact.state] += 1

    return counts


def _source_digest(bundles: Iterable[TenderTermsBundle]) -> str:
    sources = [
        {
            "tender_id": bundle.tender_id,
            "source_bundle_semantic_digest": bundle.source_bundle_semantic_digest,
        }
        for bundle in bundles
    ]
    return canonical_json_digest({"sources": sources})


def _publication_digest(
    rows: list[dict[str, Any]],
    *,
    as_of_utc: str,
) -> str:
    return canonical_json_digest(
        {
            "contract_version": TENDER_TERMS_PUBLICATION_CONTRACT_VERSION,
            "terms_version": TENDER_TERMS_VERSION,
            "as_of_utc": as_of_utc,
            "tender_terms": rows,
        }
    )


def _build_summary(
    rows: list[dict[str, Any]],
    bundles: list[TenderTermsBundle],
    *,
    as_of_utc: str,
) -> dict[str, Any]:
    complete = sum(1 for bundle in bundles if bundle.coverage.is_complete)

    return {
        "contract_version": TENDER_TERMS_PUBLICATION_CONTRACT_VERSION,
        "terms_version": TENDER_TERMS_VERSION,
        "as_of_utc": as_of_utc,
        "tender_count": len(bundles),
        "complete_coverage_count": complete,
        "incomplete_coverage_count": len(bundles) - complete,
        "fact_state_counts": _fact_state_counts(bundles),
        "source_digest": _source_digest(bundles),
        "publication_digest": _publication_digest(rows, as_of_utc=as_of_utc),
        "contact_authorization": False,
        "outreach_authorization": False,
        "production_queue_mutated": False,
        "persisted": False,
    }


def _serialize_jsonl(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []

    for row in rows:
        line = json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        assert_no_portal_tokens(line, where=TENDER_TERMS_FILENAME)
        lines.append(line)

    return "\n".join(lines) + ("\n" if lines else "")


def _write_publication_files(
    staged: Path,
    *,
    bundles: list[TenderTermsBundle],
    as_of_utc: str,
) -> dict[str, str]:
    rows = [bundle.to_dict() for bundle in bundles]
    summary = _build_summary(rows, bundles, as_of_utc=as_of_utc)

    summary_text = (
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )
    assert_no_portal_tokens(summary_text, where=SUMMARY_FILENAME)

    summary_path = staged / SUMMARY_FILENAME
    terms_path = staged / TENDER_TERMS_FILENAME

    summary_path.write_text(summary_text, encoding="utf-8")
    terms_path.write_text(_serialize_jsonl(rows), encoding="utf-8")

    return {
        SUMMARY_FILENAME: str(summary_path),
        TENDER_TERMS_FILENAME: str(terms_path),
    }


def load_published_tender_terms(out_dir: Path) -> dict[str, Any]:
    """Load and fail-closed validate a canonical T1 publication."""

    summary_path = out_dir / SUMMARY_FILENAME
    terms_path = out_dir / TENDER_TERMS_FILENAME

    if not summary_path.is_file():
        raise TenderTermsPublicationError(f"missing {SUMMARY_FILENAME}")
    if not terms_path.is_file():
        raise TenderTermsPublicationError(f"missing {TENDER_TERMS_FILENAME}")

    summary_text = summary_path.read_text(encoding="utf-8")
    terms_text = terms_path.read_text(encoding="utf-8")

    assert_no_portal_tokens(summary_text, where=SUMMARY_FILENAME)
    assert_no_portal_tokens(terms_text, where=TENDER_TERMS_FILENAME)

    try:
        summary = json.loads(summary_text)
    except json.JSONDecodeError as exc:
        raise TenderTermsPublicationError("summary.json is malformed") from exc

    summary = _require_dict(summary, "summary")

    if summary.get("contract_version") != TENDER_TERMS_PUBLICATION_CONTRACT_VERSION:
        raise TenderTermsPublicationError(
            f"unsupported publication contract: {summary.get('contract_version')}"
        )

    if summary.get("terms_version") != TENDER_TERMS_VERSION:
        raise TenderTermsPublicationError(
            f"unsupported terms_version: {summary.get('terms_version')}"
        )

    as_of_utc = _validate_as_of_utc(summary.get("as_of_utc"))

    rows: list[dict[str, Any]] = []
    bundles: list[TenderTermsBundle] = []

    for line_number, line in enumerate(terms_text.splitlines(), start=1):
        if not line.strip():
            raise TenderTermsPublicationError(
                f"blank line in {TENDER_TERMS_FILENAME} at line {line_number}"
            )
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TenderTermsPublicationError(
                f"malformed JSON in {TENDER_TERMS_FILENAME} line {line_number}"
            ) from exc

        row = _require_dict(decoded, f"{TENDER_TERMS_FILENAME} line {line_number}")
        bundle = _bundle_from_dict(row)
        rows.append(row)
        bundles.append(bundle)

    tender_ids = [bundle.tender_id for bundle in bundles]

    if len(tender_ids) != len(set(tender_ids)):
        raise TenderTermsPublicationError("duplicate tender_id in publication")

    if tender_ids != sorted(tender_ids):
        raise TenderTermsPublicationError(
            "tender_terms.jsonl must be sorted by tender_id"
        )

    expected_summary = _build_summary(
        rows,
        bundles,
        as_of_utc=as_of_utc,
    )
    if summary != expected_summary:
        raise TenderTermsPublicationError(
            "summary.json does not reconcile with tender_terms.jsonl"
        )

    return {
        "summary": summary,
        "tender_terms": rows,
    }


def _safe_error(exc: Exception) -> str:
    text = redact_portal_tokens(str(exc)).replace("\r", " ").replace("\n", " ")
    return f"{type(exc).__name__}: {text}"[:500]


def publish_tender_terms(
    *,
    bundles: Iterable[TenderAttachmentBundle],
    as_of_utc: str,
    out_dir: Path,
    require_git_ignored: bool = True,
) -> TenderTermsPublicationResult:
    """Extract, validate, and atomically publish T1 terms.

    This function performs no attachment acquisition and no database writes.
    """

    basename = out_dir.name

    try:
        as_of = _validate_as_of_utc(as_of_utc)
        source_bundles = list(bundles)

        source_ids = [bundle.tender_id for bundle in source_bundles]
        if any(not tender_id.strip() for tender_id in source_ids):
            raise TenderTermsPublicationError(
                "source TenderAttachmentBundle has blank tender_id"
            )
        if len(source_ids) != len(set(source_ids)):
            raise TenderTermsPublicationError("duplicate tender_id in source bundles")

        source_bundles.sort(key=lambda bundle: bundle.tender_id)

        extracted: list[TenderTermsBundle] = []
        for source in source_bundles:
            terms = extract_tender_terms(source)

            if terms.tender_id != source.tender_id:
                raise TenderTermsPublicationError(
                    "T1 extractor changed tender identity"
                )
            if terms.source_bundle_semantic_digest != source.semantic_digest:
                raise TenderTermsPublicationError(
                    f"source digest mismatch for tender {source.tender_id}"
                )
            if terms.semantic_digest != tender_terms_semantic_digest(terms):
                raise TenderTermsPublicationError(
                    f"T1 semantic digest mismatch for tender {source.tender_id}"
                )

            for field in (
                "contact_authorization",
                "outreach_authorization",
                "production_queue_mutated",
                "persisted",
            ):
                if getattr(terms, field) is not False:
                    raise TenderTermsPublicationError(f"{field} must remain false")

            extracted.append(terms)

        validated: dict[str, Any] = {}

        def _writer(staged: Path) -> dict[str, str]:
            written = _write_publication_files(
                staged,
                bundles=extracted,
                as_of_utc=as_of,
            )
            loaded = load_published_tender_terms(staged)
            validated.update(loaded["summary"])
            return written

        write_atomically(
            out_dir,
            repo_email_pipeline_root=_email_pipeline_root(),
            writer=_writer,
            require_git_ignored=require_git_ignored,
        )

        return TenderTermsPublicationResult(
            result="applied",
            output_dir_basename=basename,
            contract_version=str(validated["contract_version"]),
            terms_version=str(validated["terms_version"]),
            as_of_utc=str(validated["as_of_utc"]),
            tender_count=int(validated["tender_count"]),
            complete_coverage_count=int(validated["complete_coverage_count"]),
            incomplete_coverage_count=int(validated["incomplete_coverage_count"]),
            publication_digest=str(validated["publication_digest"]),
        )
    except Exception as exc:  # noqa: BLE001 - fail closed at publication boundary
        return TenderTermsPublicationResult(
            result="failed",
            output_dir_basename=basename,
            error=_safe_error(exc),
        )


__all__ = [
    "SUMMARY_FILENAME",
    "TENDER_TERMS_DIRNAME",
    "TENDER_TERMS_FILENAME",
    "TENDER_TERMS_PUBLICATION_CONTRACT_VERSION",
    "TenderTermsPublicationError",
    "TenderTermsPublicationResult",
    "load_published_tender_terms",
    "publish_tender_terms",
]
