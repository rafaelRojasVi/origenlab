"""T1 Semantic Benchmark v1: deterministic vs. deterministic+semantic-fallback.

Measurement-only tooling. This module never mutates SQLite/Postgres state,
never sends outreach, and never changes T1 extraction or semantic-fallback
semantics — it only calls the existing public `extract_tender_terms` entry
point twice per tender (once deterministic-only, once with a semantic
client) and records what differs.

RELEASE GATES (must hold for every real run before T1 is exposed downstream):
  1. unsupported accepted claims = 0
     -> every case with sem_state == FACT_STATE_EXPLICIT/CONFLICTING that
        came from the semantic path must carry non-empty evidence with an
        excerpt that was validated by `semantic_fallback._validate_semantic_support`.
  2. deterministic explicit facts are never overwritten
     -> any case where det_state in {explicit, conflicting} but
        det_value != sem_value is a `deterministic_changed_unexpectedly`
        case and fails the run.
  3. model operational failures are not represented as factual unknown
     -> a field whose semantic call raised is recorded with
        operational_error set and sem_state == "model_operational_error",
        never silently downgraded to "unknown".
  4. every accepted semantic claim has exact TermEvidence provenance
     -> enforced structurally: SemanticObservation.evidence is populated by
        `term_evidence_from_span`, and FactCandidate/TenderTermFact reject
        empty evidence in `__post_init__`.
"""

from __future__ import annotations

import csv
import json
import pickle
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    TenderAttachmentBundle,
)

from . import semantic_fallback as _semantic_fallback_module
from .constants import (
    FACT_STATE_CONFLICTING,
    FACT_STATE_EXPLICIT,
    FACT_STATE_UNKNOWN,
)
from .extract import extract_tender_terms
from .models import TenderTermsBundle
from .semantic_fallback import (
    FIELD_SPECS,
    OllamaSemanticFallbackClient,
    SemanticClaim,
    SemanticEvidenceSpan,
    SemanticFallbackClient,
    SemanticFieldSpec,
)


KNOWN_CONTROL_TENDER_IDS: tuple[str, ...] = (
    "745712-8-LP26",
    "745712-13-LP26",
    "1093303-1-LR26",
)

BENCHMARK_FIELD_NAMES: tuple[str, ...] = tuple(sorted(FIELD_SPECS))

_MODEL_OPERATIONAL_ERROR = "model_operational_error"

# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusEntry:
    """One benchmark corpus member: a pickled bundle plus its metadata."""

    tender_id: str
    bundle_path: Path
    institution: str
    equipment_categories: tuple[str, ...]
    attachment_count: int


def _tender_id_from_bundle_filename(path: Path) -> str:
    return path.stem


def _institution_and_categories_from_detail_cache(
    detail_cache_dir: Path,
    tender_id: str,
) -> tuple[str, tuple[str, ...]]:
    detail_path = detail_cache_dir / f"{tender_id}.json"
    if not detail_path.is_file():
        return "", ()
    try:
        payload = json.loads(detail_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ()

    listado = payload.get("Listado")
    if not isinstance(listado, list) or not listado or not isinstance(listado[0], dict):
        return "", ()
    entry = listado[0]

    comprador = entry.get("Comprador") or {}
    institution = str(comprador.get("NombreOrganismo") or "").strip()

    items = (entry.get("Items") or {}).get("Listado") or []
    categories: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("Categoria") or "").strip()
        if category and category not in categories:
            categories.append(category)

    return institution, tuple(sorted(categories))


def discover_corpus_entries(
    *,
    bundles_dir: Path,
    detail_cache_dir: Path,
) -> tuple[CorpusEntry, ...]:
    """Enumerate every locally cached, already-acquired tender bundle.

    Deterministic order: sorted by tender_id. Purely read-only filesystem
    enumeration; no network, no attachment bytes read here.
    """

    if not bundles_dir.is_dir():
        return ()

    entries: list[CorpusEntry] = []
    for bundle_path in sorted(bundles_dir.glob("*.pkl")):
        tender_id = _tender_id_from_bundle_filename(bundle_path)
        institution, categories = _institution_and_categories_from_detail_cache(
            detail_cache_dir, tender_id
        )
        entries.append(
            CorpusEntry(
                tender_id=tender_id,
                bundle_path=bundle_path,
                institution=institution,
                equipment_categories=categories,
                attachment_count=0,
            )
        )

    entries.sort(key=lambda e: e.tender_id)
    return tuple(entries)


def select_corpus(
    entries: tuple[CorpusEntry, ...],
    *,
    corpus_size: int,
    required_tender_ids: tuple[str, ...] = (),
) -> tuple[CorpusEntry, ...]:
    """Deterministically pick up to `corpus_size` entries.

    Required tender IDs (known controls) are always included first, when
    present in `entries`. Remaining slots are filled by round-robin across
    institutions (in first-seen, tender_id-sorted order) so the corpus does
    not collapse onto a single buyer, then by tender_id for full determinism.
    """

    if corpus_size < 1:
        raise ValueError("corpus_size must be >= 1")

    by_id = {entry.tender_id: entry for entry in entries}

    selected: list[CorpusEntry] = []
    selected_ids: set[str] = set()

    for tender_id in required_tender_ids:
        entry = by_id.get(tender_id)
        if entry is not None and tender_id not in selected_ids:
            selected.append(entry)
            selected_ids.add(tender_id)

    remaining = [e for e in entries if e.tender_id not in selected_ids]

    buckets: dict[str, list[CorpusEntry]] = {}
    bucket_order: list[str] = []
    for entry in remaining:
        key = entry.institution or "__unknown__"
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(entry)

    cursor = 0
    while len(selected) < corpus_size and any(buckets[key] for key in bucket_order):
        key = bucket_order[cursor % len(bucket_order)]
        cursor += 1
        bucket = buckets[key]
        if bucket:
            selected.append(bucket.pop(0))
            selected_ids.add(selected[-1].tender_id)

    return tuple(selected[:corpus_size])


def load_bundle(entry: CorpusEntry) -> TenderAttachmentBundle:
    """Load one already-acquired bundle from local disk. Read-only."""

    with entry.bundle_path.open("rb") as handle:
        bundle = pickle.load(handle)  # noqa: S301 - trusted local cache written by this repo
    if not isinstance(bundle, TenderAttachmentBundle):
        raise ValueError(f"cached bundle is not a TenderAttachmentBundle: {entry.bundle_path}")
    if bundle.tender_id != entry.tender_id:
        raise ValueError(
            f"cached bundle tender_id mismatch: {bundle.tender_id!r} != {entry.tender_id!r}"
        )
    return bundle


# ---------------------------------------------------------------------------
# Instrumented semantic client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimAccounting:
    field_name: str
    value: Any
    accepted: bool
    reject_reason: str | None
    evidence_span_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticCallRecord:
    field_name: str
    span_count: int
    raw_claim_count: int
    latency_seconds: float
    error: str | None
    claims: tuple[ClaimAccounting, ...]


class InstrumentedSemanticClient:
    """Wraps a real `SemanticFallbackClient` to record benchmark accounting.

    Does not change which claims are accepted by `extract_tender_terms`; it
    independently re-runs the same public validation helpers used by
    `semantic_observations_for_field` against the claims the wrapped client
    returned, purely to explain acceptance/rejection for the review queue.
    """

    def __init__(self, inner: SemanticFallbackClient, *, model_name: str) -> None:
        self.inner = inner
        self.model_name = model_name
        self.calls: list[SemanticCallRecord] = []

    def extract_claims(
        self,
        *,
        spec: SemanticFieldSpec,
        spans: tuple[SemanticEvidenceSpan, ...],
    ) -> tuple[SemanticClaim, ...]:
        started = time.monotonic()
        error: str | None = None
        try:
            claims = self.inner.extract_claims(spec=spec, spans=spans)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            error = f"{type(exc).__name__}: {exc}"
            latency = time.monotonic() - started
            self.calls.append(
                SemanticCallRecord(
                    field_name=spec.field_name,
                    span_count=len(spans),
                    raw_claim_count=0,
                    latency_seconds=latency,
                    error=error,
                    claims=(),
                )
            )
            raise

        latency = time.monotonic() - started
        spans_by_id = {span.span_id: span for span in spans}
        accounted: list[ClaimAccounting] = []

        for claim in claims:
            try:
                _semantic_fallback_module._coerce_value(spec, claim)
                _semantic_fallback_module._validate_semantic_support(
                    spec=spec,
                    claim=claim,
                    spans_by_id=spans_by_id,
                )
            except ValueError as exc:
                accounted.append(
                    ClaimAccounting(
                        field_name=spec.field_name,
                        value=claim.value,
                        accepted=False,
                        reject_reason=str(exc),
                        evidence_span_ids=claim.evidence_span_ids,
                    )
                )
                continue

            accounted.append(
                ClaimAccounting(
                    field_name=spec.field_name,
                    value=claim.value,
                    accepted=True,
                    reject_reason=None,
                    evidence_span_ids=claim.evidence_span_ids,
                )
            )

        self.calls.append(
            SemanticCallRecord(
                field_name=spec.field_name,
                span_count=len(spans),
                raw_claim_count=len(claims),
                latency_seconds=latency,
                error=None,
                claims=tuple(accounted),
            )
        )
        return claims


# ---------------------------------------------------------------------------
# Case records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseRecord:
    tender_id: str
    institution: str
    field: str

    det_state: str
    det_value: Any
    sem_state: str
    sem_value: Any

    changed: bool
    model_called: bool
    candidate_span_count: int
    accepted_claims: int
    rejected_claims: tuple[dict[str, Any], ...]

    attachment: str
    locator_display: str
    evidence_excerpt: str

    model: str | None
    latency_seconds: float
    operational_error: str | None

    priority: str
    priority_rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "institution": self.institution,
            "field": self.field,
            "det_state": self.det_state,
            "det_value": self.det_value,
            "sem_state": self.sem_state,
            "sem_value": self.sem_value,
            "changed": self.changed,
            "model_called": self.model_called,
            "candidate_span_count": self.candidate_span_count,
            "accepted_claims": self.accepted_claims,
            "rejected_claims": list(self.rejected_claims),
            "attachment": self.attachment,
            "locator_display": self.locator_display,
            "evidence_excerpt": self.evidence_excerpt,
            "model": self.model,
            "latency_seconds": self.latency_seconds,
            "operational_error": self.operational_error,
            "priority": self.priority,
            "priority_rank": self.priority_rank,
        }


_PRIORITY_RANKS: dict[str, int] = {
    "operational_error": 0,
    "deterministic_changed_unexpectedly": 1,
    "unresolved_to_conflict": 2,
    "unresolved_to_semantic_explicit": 3,
    "candidate_spans_but_abstention": 4,
    "rejected_claims_present": 5,
    "no_change": 6,
}


def _classify_priority(
    *,
    det_state: str,
    sem_state: str,
    det_value: Any,
    sem_value: Any,
    operational_error: str | None,
    candidate_span_count: int,
    accepted_claims: int,
    rejected_claims: tuple[dict[str, Any], ...],
) -> str:
    if operational_error is not None:
        return "operational_error"

    if det_state in {FACT_STATE_EXPLICIT, FACT_STATE_CONFLICTING} and det_value != sem_value:
        return "deterministic_changed_unexpectedly"

    if det_state == FACT_STATE_UNKNOWN and sem_state == FACT_STATE_CONFLICTING:
        return "unresolved_to_conflict"

    if det_state == FACT_STATE_UNKNOWN and sem_state == FACT_STATE_EXPLICIT:
        return "unresolved_to_semantic_explicit"

    if candidate_span_count > 0 and accepted_claims == 0 and det_state == FACT_STATE_UNKNOWN:
        return "candidate_spans_but_abstention"

    if rejected_claims:
        return "rejected_claims_present"

    return "no_change"


def _fact_by_field(bundle: TenderTermsBundle) -> dict[str, Any]:
    return {fact.field_name: fact for fact in bundle.tender_facts}


def _representative_evidence(fact: Any) -> Any:
    if fact.evidence:
        return fact.evidence[0]
    if fact.candidates:
        for candidate in fact.candidates:
            if candidate.evidence:
                return candidate.evidence[0]
    return None


def benchmark_tender(
    entry: CorpusEntry,
    bundle: TenderAttachmentBundle,
    *,
    client: SemanticFallbackClient | None,
    model_name: str | None,
) -> tuple[CaseRecord, ...]:
    """Run deterministic and deterministic+semantic extraction for one tender."""

    det_bundle = extract_tender_terms(bundle)
    det_by_field = _fact_by_field(det_bundle)

    tender_operational_error: str | None = None
    sem_by_field: dict[str, Any] = det_by_field
    instrumented: InstrumentedSemanticClient | None = None

    if client is not None:
        instrumented = InstrumentedSemanticClient(client, model_name=model_name or "unknown")
        try:
            sem_bundle = extract_tender_terms(bundle, semantic_client=instrumented)
            sem_by_field = _fact_by_field(sem_bundle)
        except Exception as exc:  # noqa: BLE001 - recorded per release gate 3
            tender_operational_error = f"{type(exc).__name__}: {exc}"
            sem_by_field = det_by_field

    calls_by_field: dict[str, SemanticCallRecord] = {}
    if instrumented is not None:
        for call in instrumented.calls:
            # Later calls for the same field (should not happen; one call per
            # field per extraction) overwrite, keeping the last observation.
            calls_by_field[call.field_name] = call

    records: list[CaseRecord] = []

    for field_name in BENCHMARK_FIELD_NAMES:
        det_fact = det_by_field.get(field_name)
        sem_fact = sem_by_field.get(field_name)
        if det_fact is None or sem_fact is None:
            continue

        det_state = det_fact.state
        det_value = det_fact.value
        model_called = client is not None and det_state == FACT_STATE_UNKNOWN

        call = calls_by_field.get(field_name)
        candidate_span_count = call.span_count if call else 0
        accepted_claims = sum(1 for c in call.claims if c.accepted) if call else 0
        rejected_claims = tuple(
            {"value": c.value, "reason": c.reject_reason}
            for c in (call.claims if call else ())
            if not c.accepted
        )
        latency_seconds = call.latency_seconds if call else 0.0

        field_operational_error = tender_operational_error
        if field_operational_error is None and call is not None and call.error is not None:
            field_operational_error = call.error

        if field_operational_error is not None and model_called:
            sem_state = _MODEL_OPERATIONAL_ERROR
            sem_value = None
        else:
            sem_state = sem_fact.state
            sem_value = sem_fact.value

        changed = model_called and (det_state != sem_state or det_value != sem_value)

        evidence = _representative_evidence(sem_fact if field_operational_error is None else det_fact)
        attachment = evidence.safe_filename if evidence is not None else ""
        locator_display = evidence.locator_display if evidence is not None else ""
        evidence_excerpt = evidence.evidence_excerpt if evidence is not None else ""

        priority = _classify_priority(
            det_state=det_state,
            sem_state=sem_state,
            det_value=det_value,
            sem_value=sem_value,
            operational_error=field_operational_error,
            candidate_span_count=candidate_span_count,
            accepted_claims=accepted_claims,
            rejected_claims=rejected_claims,
        )

        records.append(
            CaseRecord(
                tender_id=entry.tender_id,
                institution=entry.institution,
                field=field_name,
                det_state=det_state,
                det_value=det_value,
                sem_state=sem_state,
                sem_value=sem_value,
                changed=changed,
                model_called=model_called,
                candidate_span_count=candidate_span_count,
                accepted_claims=accepted_claims,
                rejected_claims=rejected_claims,
                attachment=attachment,
                locator_display=locator_display,
                evidence_excerpt=evidence_excerpt,
                model=model_name if model_called else None,
                latency_seconds=latency_seconds,
                operational_error=field_operational_error,
                priority=priority,
                priority_rank=_PRIORITY_RANKS[priority],
            )
        )

    return tuple(records)


# ---------------------------------------------------------------------------
# Summary / metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkSummary:
    tenders: int
    field_evaluations: int
    model_calls: int
    accepted_semantic_positives: int
    conflicts: int
    rejected_claim_reasons: dict[str, int]
    deterministic_preservation_violations: int
    model_errors: int
    latency_seconds_total: float
    latency_seconds_mean: float
    priority_counts: dict[str, int]
    release_gates: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenders": self.tenders,
            "field_evaluations": self.field_evaluations,
            "model_calls": self.model_calls,
            "accepted_semantic_positives": self.accepted_semantic_positives,
            "conflicts": self.conflicts,
            "rejected_claim_reasons": dict(self.rejected_claim_reasons),
            "deterministic_preservation_violations": self.deterministic_preservation_violations,
            "model_errors": self.model_errors,
            "latency_seconds_total": self.latency_seconds_total,
            "latency_seconds_mean": self.latency_seconds_mean,
            "priority_counts": dict(self.priority_counts),
            "release_gates": self.release_gates,
        }


def summarize(cases: tuple[CaseRecord, ...], *, tender_count: int) -> BenchmarkSummary:
    model_calls = sum(1 for c in cases if c.model_called)
    accepted_positives = sum(
        1 for c in cases if c.priority == "unresolved_to_semantic_explicit"
    )
    conflicts = sum(1 for c in cases if c.sem_state == FACT_STATE_CONFLICTING)
    deterministic_violations = sum(
        1 for c in cases if c.priority == "deterministic_changed_unexpectedly"
    )
    model_errors = sum(1 for c in cases if c.operational_error is not None)

    rejected_reasons: dict[str, int] = {}
    for case in cases:
        for rejection in case.rejected_claims:
            reason = str(rejection.get("reason") or "unknown")
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    latencies = [c.latency_seconds for c in cases if c.model_called]
    latency_total = sum(latencies)
    latency_mean = latency_total / len(latencies) if latencies else 0.0

    priority_counts: dict[str, int] = {}
    for case in cases:
        priority_counts[case.priority] = priority_counts.get(case.priority, 0) + 1

    unsupported_accepted = sum(
        1
        for c in cases
        if c.model_called
        and c.sem_state in {FACT_STATE_EXPLICIT, FACT_STATE_CONFLICTING}
        and not c.evidence_excerpt
    )
    operational_errors_as_unknown = sum(
        1
        for c in cases
        if c.operational_error is not None and c.sem_state == FACT_STATE_UNKNOWN
    )

    release_gates = {
        "unsupported_accepted_claims_is_zero": unsupported_accepted == 0,
        "unsupported_accepted_claims_count": unsupported_accepted,
        "deterministic_never_overwritten": deterministic_violations == 0,
        "deterministic_overwrite_count": deterministic_violations,
        "operational_errors_not_reported_as_unknown": operational_errors_as_unknown == 0,
        "operational_errors_reported_as_unknown_count": operational_errors_as_unknown,
        "all_accepted_claims_have_provenance": unsupported_accepted == 0,
        "passed": (
            unsupported_accepted == 0
            and deterministic_violations == 0
            and operational_errors_as_unknown == 0
        ),
    }

    return BenchmarkSummary(
        tenders=tender_count,
        field_evaluations=len(cases),
        model_calls=model_calls,
        accepted_semantic_positives=accepted_positives,
        conflicts=conflicts,
        rejected_claim_reasons=rejected_reasons,
        deterministic_preservation_violations=deterministic_violations,
        model_errors=model_errors,
        latency_seconds_total=latency_total,
        latency_seconds_mean=latency_mean,
        priority_counts=priority_counts,
        release_gates=release_gates,
    )


# ---------------------------------------------------------------------------
# Adjudication (optional)
# ---------------------------------------------------------------------------


def load_adjudication(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Load an optional human gold file: JSONL of {tender_id, field, ...}."""

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = (str(row["tender_id"]), str(row["field"]))
            rows[key] = row
    return rows


@dataclass(frozen=True)
class AdjudicationMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    correct_abstentions: int
    incorrect_abstentions: int
    correct_conflicts: int
    incorrect_conflicts: int

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
            "correct_abstentions": self.correct_abstentions,
            "incorrect_abstentions": self.incorrect_abstentions,
            "correct_conflicts": self.correct_conflicts,
            "incorrect_conflicts": self.incorrect_conflicts,
            "precision": self.precision,
            "recall": self.recall,
        }


def score_against_adjudication(
    cases: tuple[CaseRecord, ...],
    adjudication: Mapping[tuple[str, str], dict[str, Any]],
) -> AdjudicationMetrics:
    true_positives = false_positives = false_negatives = true_negatives = 0
    correct_abstentions = incorrect_abstentions = 0
    correct_conflicts = incorrect_conflicts = 0

    for case in cases:
        gold = adjudication.get((case.tender_id, case.field))
        if gold is None:
            continue

        gold_state = str(gold.get("gold_state") or "")
        gold_value = gold.get("gold_value")

        if case.sem_state == FACT_STATE_EXPLICIT:
            if gold_state == FACT_STATE_EXPLICIT and case.sem_value == gold_value:
                true_positives += 1
            else:
                false_positives += 1
        elif case.sem_state == FACT_STATE_UNKNOWN:
            if gold_state in {FACT_STATE_UNKNOWN, ""}:
                true_negatives += 1
                correct_abstentions += 1
            else:
                false_negatives += 1
                incorrect_abstentions += 1
        elif case.sem_state == FACT_STATE_CONFLICTING:
            if gold_state == FACT_STATE_CONFLICTING:
                correct_conflicts += 1
            else:
                incorrect_conflicts += 1

    return AdjudicationMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
        correct_abstentions=correct_abstentions,
        incorrect_abstentions=incorrect_abstentions,
        correct_conflicts=correct_conflicts,
        incorrect_conflicts=incorrect_conflicts,
    )


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

_REVIEW_QUEUE_FIELDS: tuple[str, ...] = (
    "priority",
    "priority_rank",
    "tender_id",
    "institution",
    "field",
    "det_state",
    "det_value",
    "sem_state",
    "sem_value",
    "model_called",
    "accepted_claims",
    "operational_error",
    "attachment",
    "locator_display",
)


def write_outputs(
    *,
    out_dir: Path,
    cases: tuple[CaseRecord, ...],
    summary: BenchmarkSummary,
    corpus: tuple[CorpusEntry, ...],
    adjudication_metrics: AdjudicationMetrics | None,
    model_name: str | None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    cases_path = out_dir / "cases.jsonl"
    with cases_path.open("w", encoding="utf-8") as handle:
        for case in sorted(cases, key=lambda c: (c.priority_rank, c.tender_id, c.field)):
            handle.write(json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    review_path = out_dir / "review_queue.csv"
    ranked = sorted(cases, key=lambda c: (c.priority_rank, c.tender_id, c.field))
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_REVIEW_QUEUE_FIELDS)
        writer.writeheader()
        for case in ranked:
            row = case.to_dict()
            writer.writerow({key: row.get(key) for key in _REVIEW_QUEUE_FIELDS})

    summary_payload = summary.to_dict()
    summary_payload["model"] = model_name
    summary_payload["corpus"] = {
        "tender_ids": [entry.tender_id for entry in corpus],
        "institutions": sorted({entry.institution for entry in corpus if entry.institution}),
    }
    if adjudication_metrics is not None:
        summary_payload["adjudication"] = adjudication_metrics.to_dict()

    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    report_path = out_dir / "benchmark_report.md"
    report_path.write_text(
        _render_report(
            summary=summary,
            corpus=corpus,
            ranked_cases=ranked,
            adjudication_metrics=adjudication_metrics,
            model_name=model_name,
        ),
        encoding="utf-8",
    )

    return {
        "cases": cases_path,
        "review_queue": review_path,
        "summary": summary_path,
        "report": report_path,
    }


def _render_report(
    *,
    summary: BenchmarkSummary,
    corpus: tuple[CorpusEntry, ...],
    ranked_cases: list[CaseRecord],
    adjudication_metrics: AdjudicationMetrics | None,
    model_name: str | None,
) -> str:
    gates = summary.release_gates
    lines: list[str] = []
    lines.append("# T1 Semantic Benchmark v1 Report")
    lines.append("")
    lines.append("## Release gates")
    lines.append("")
    gate_status = "PASS" if gates["passed"] else "FAIL"
    lines.append(f"**Overall: {gate_status}**")
    lines.append("")
    lines.append(
        f"- unsupported accepted claims = 0: "
        f"{'PASS' if gates['unsupported_accepted_claims_is_zero'] else 'FAIL'} "
        f"({gates['unsupported_accepted_claims_count']})"
    )
    lines.append(
        f"- deterministic explicit facts never overwritten: "
        f"{'PASS' if gates['deterministic_never_overwritten'] else 'FAIL'} "
        f"({gates['deterministic_overwrite_count']})"
    )
    lines.append(
        f"- model operational failures not reported as unknown: "
        f"{'PASS' if gates['operational_errors_not_reported_as_unknown'] else 'FAIL'} "
        f"({gates['operational_errors_reported_as_unknown_count']})"
    )
    lines.append(
        "- every accepted semantic claim has exact TermEvidence provenance: "
        "enforced structurally by TenderTermFact/FactCandidate validation"
    )
    lines.append("")

    lines.append("## Corpus")
    lines.append("")
    lines.append(f"- tenders: {summary.tenders}")
    institutions = sorted({e.institution for e in corpus if e.institution})
    lines.append(f"- institutions: {len(institutions)}")
    lines.append(f"- model: {model_name or 'none (deterministic-only run)'}")
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    lines.append(f"- field evaluations: {summary.field_evaluations}")
    lines.append(f"- model calls: {summary.model_calls}")
    lines.append(f"- accepted semantic positives: {summary.accepted_semantic_positives}")
    lines.append(f"- conflicts: {summary.conflicts}")
    lines.append(f"- model errors: {summary.model_errors}")
    lines.append(
        f"- latency total/mean (s): "
        f"{summary.latency_seconds_total:.2f} / {summary.latency_seconds_mean:.2f}"
    )
    lines.append("- rejected claim reasons:")
    for reason, count in sorted(summary.rejected_claim_reasons.items()):
        lines.append(f"  - {reason}: {count}")
    lines.append("- priority counts:")
    for priority, count in sorted(
        summary.priority_counts.items(), key=lambda kv: _PRIORITY_RANKS.get(kv[0], 99)
    ):
        lines.append(f"  - {priority}: {count}")
    lines.append("")

    if adjudication_metrics is not None:
        lines.append("## Adjudication")
        lines.append("")
        lines.append(f"- precision: {adjudication_metrics.precision:.3f}")
        lines.append(f"- recall: {adjudication_metrics.recall:.3f}")
        lines.append(f"- false positives: {adjudication_metrics.false_positives}")
        lines.append(f"- false negatives: {adjudication_metrics.false_negatives}")
        lines.append(f"- correct abstentions: {adjudication_metrics.correct_abstentions}")
        lines.append(f"- incorrect abstentions: {adjudication_metrics.incorrect_abstentions}")
        lines.append("")

    lines.append("## Top review-queue cases")
    lines.append("")
    for case in ranked_cases[:25]:
        lines.append(
            f"- [{case.priority}] {case.tender_id} / {case.field}: "
            f"det={case.det_state}:{case.det_value!r} -> sem={case.sem_state}:{case.sem_value!r}"
        )
    lines.append("")

    return "\n".join(lines)


def build_ollama_client(*, model: str) -> OllamaSemanticFallbackClient:
    return OllamaSemanticFallbackClient(model=model)
