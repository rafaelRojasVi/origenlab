"""Evaluation corpus foundation: contract fixtures vs real review queue."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    RELEVANCE_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.redaction import (
    domain_redacted_alias,
    redact_product_wording,
)

LABEL_STATUSES_METRIC_ELIGIBLE = frozenset({"reviewed", "gold"})
VALID_LABEL_STATUSES = frozenset({"proposed", "reviewed", "gold", "rejected"})

# Diagnostic stratum keys (prediction-aware — sealed only, never in blind packet).
STRATUM_EQUIPMENT = "equipment_first_hit"
STRATUM_HARD_NEG = "likely_hard_negative"
STRATUM_AMBIGUOUS = "ambiguous_or_manual_review"
STRATUM_RANDOM = "random_non_hit"

DEFAULT_STRATUM_QUOTAS: dict[str, int] = {
    STRATUM_EQUIPMENT: 40,
    STRATUM_HARD_NEG: 40,
    STRATUM_AMBIGUOUS: 40,
    STRATUM_RANDOM: 80,
}


@dataclass(frozen=True)
class EvaluationRecord:
    """Operational evaluation row (may contain prediction fields — not blind)."""

    record_id: str
    coalesced_tender_alias: str
    diagnostic_stratum: str
    product_text_redacted: str
    has_line_descriptions: bool
    source_plane: str
    text_richness: str
    predicted_relevance_class: str | None
    predicted_confidence_band: str | None
    predicted_resolution_status: str | None
    predicted_ambiguity_reason_codes: tuple[str, ...]
    predicted_aggregation_reason_codes: tuple[str, ...]
    manual_review_recommended: bool
    label_status: str
    gold_relevance_class: str | None
    review_source: str | None
    independently_reviewed: bool
    is_synthetic: bool
    redaction_proof: dict[str, Any]
    sample_role: str = "diagnostic_stratified"  # or representative_holdout

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["predicted_ambiguity_reason_codes"] = list(
            self.predicted_ambiguity_reason_codes
        )
        d["predicted_aggregation_reason_codes"] = list(
            self.predicted_aggregation_reason_codes
        )
        return d


@dataclass(frozen=True)
class BlindReviewRecord:
    """Human-facing packet — no predicted class or prediction-derived stratum."""

    record_id: str
    coalesced_tender_alias: str
    product_text_redacted: str
    has_line_descriptions: bool
    source_plane: str
    text_richness: str
    sample_role: str
    label_status: str
    instructions: str = (
        "Assign an independent relevance_class from the PR5A vocabulary. "
        "Do not infer from any external prediction."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SealedScoringRecord:
    """Prediction-bearing record joined to blind labels by record_id only."""

    record_id: str
    diagnostic_stratum: str
    predicted_relevance_class: str | None
    predicted_confidence_band: str | None
    predicted_resolution_status: str | None
    predicted_ambiguity_reason_codes: tuple[str, ...]
    predicted_aggregation_reason_codes: tuple[str, ...]
    manual_review_recommended: bool
    sample_role: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["predicted_ambiguity_reason_codes"] = list(
            self.predicted_ambiguity_reason_codes
        )
        d["predicted_aggregation_reason_codes"] = list(
            self.predicted_aggregation_reason_codes
        )
        return d


def stable_sample_key(coalesced_tender_id: str, salt: str = "pr5d_eval_v1") -> str:
    import hashlib

    return hashlib.sha256(f"{salt}:{coalesced_tender_id}".encode("utf-8")).hexdigest()


def is_manual_review_prediction(
    *,
    relevance_class: str | None,
    confidence_band: str | None,
    resolution_status: str | None,
    ambiguity_reason_codes: Iterable[str] = (),
    aggregation_reason_codes: Iterable[str] = (),
) -> bool:
    amb = set(ambiguity_reason_codes or ())
    agg = set(aggregation_reason_codes or ())
    if confidence_band == "abstain":
        return True
    if relevance_class in {"ambiguous", "laboratory_context_only"}:
        return True
    if resolution_status in {
        "context_required_unresolved",
        "mixed_requires_review",
        "insufficient_product_text",
    }:
        return True
    if "conflicting_line_evidence" in amb:
        return True
    if "mixed_positive_and_negative_requires_review" in agg:
        return True
    return False


def assign_diagnostic_stratum(
    *,
    predicted_class: str | None,
    manual_review: bool,
) -> str:
    if predicted_class in {
        "exact_catalog_product",
        "strong_equipment_class",
        "compatible_equipment_class",
    }:
        return STRATUM_EQUIPMENT
    if predicted_class in {
        "consumable_or_reagent",
        "service_or_maintenance_only",
        "rental_or_comodato",
        "non_laboratory_false_positive",
    }:
        return STRATUM_HARD_NEG
    if manual_review or predicted_class == "ambiguous":
        return STRATUM_AMBIGUOUS
    return STRATUM_RANDOM


def validate_metric_eligibility(record: EvaluationRecord) -> list[str]:
    """Return rejection reasons; empty means eligible for metrics."""
    reasons: list[str] = []
    if record.label_status == "proposed":
        reasons.append("proposed_excluded")
    if record.is_synthetic:
        reasons.append("synthetic_excluded")
    if record.label_status not in LABEL_STATUSES_METRIC_ELIGIBLE:
        reasons.append(f"label_status_not_metric_eligible:{record.label_status}")
    if not record.gold_relevance_class:
        reasons.append("missing_gold_relevance_class")
    elif record.gold_relevance_class not in RELEVANCE_CLASSES:
        reasons.append(f"invalid_gold_class:{record.gold_relevance_class}")
    if not (record.review_source or "").strip():
        reasons.append("missing_review_source")
    if not record.independently_reviewed:
        reasons.append("not_independently_reviewed")
    if record.label_status not in VALID_LABEL_STATUSES:
        reasons.append(f"invalid_label_status:{record.label_status}")
    return reasons


def build_labeling_queue(
    decisions: Iterable[TenderRelevanceDecision],
    *,
    product_text_by_tender: Mapping[str, str],
    has_lines_by_tender: Mapping[str, bool],
    source_plane_by_tender: Mapping[str, str],
    target_size: int = 200,
    stratum_quotas: Mapping[str, int] | None = None,
    holdout_size: int = 0,
) -> tuple[list[EvaluationRecord], dict[str, Any]]:
    """Deterministic per-stratum stable-hash sample with explicit quotas.

    Returns (records, sampling_report). Predictions stay on records for sealed
    scoring; use ``to_blind_packet`` / ``to_sealed_scoring`` for human vs join.
    """
    quotas = dict(stratum_quotas or DEFAULT_STRATUM_QUOTAS)
    # Scale quotas to target_size while preserving ratios.
    quota_sum = sum(quotas.values()) or 1
    scaled = {
        k: max(1, int(round(target_size * v / quota_sum))) for k, v in quotas.items()
    }
    # Adjust to exact target_size.
    while sum(scaled.values()) > target_size:
        k = max(scaled, key=scaled.get)
        if scaled[k] > 1:
            scaled[k] -= 1
        else:
            break
    while sum(scaled.values()) < target_size:
        k = max(scaled, key=lambda x: quotas.get(x, 0))
        scaled[k] += 1

    buckets: dict[str, list[tuple[str, TenderRelevanceDecision]]] = defaultdict(list)
    for d in decisions:
        h = stable_sample_key(d.coalesced_tender_id)
        manual = is_manual_review_prediction(
            relevance_class=d.relevance_class,
            confidence_band=d.confidence_band,
            resolution_status=d.product_resolution_status,
            ambiguity_reason_codes=d.ambiguity_reason_codes,
            aggregation_reason_codes=d.aggregation_reason_codes,
        )
        stratum = assign_diagnostic_stratum(
            predicted_class=d.relevance_class,
            manual_review=manual,
        )
        buckets[stratum].append((h, d))
    for stratum in buckets:
        buckets[stratum].sort(key=lambda t: t[0])

    selected: list[EvaluationRecord] = []
    exhaustion: dict[str, Any] = {}
    for stratum, quota in sorted(scaled.items()):
        pool = buckets.get(stratum, [])
        take = pool[:quota]
        exhaustion[stratum] = {
            "quota": quota,
            "available": len(pool),
            "selected": len(take),
            "exhausted": len(pool) < quota,
        }
        for h, d in take:
            selected.append(
                _make_eval_record(
                    h,
                    d,
                    stratum=stratum,
                    product_text_by_tender=product_text_by_tender,
                    has_lines_by_tender=has_lines_by_tender,
                    source_plane_by_tender=source_plane_by_tender,
                    sample_role="diagnostic_stratified",
                )
            )

    # Redistribute unused quota to non-exhausted strata (deterministic hash order).
    shortfall = target_size - len(selected)
    if shortfall > 0:
        selected_ids = {r.record_id for r in selected}
        donors = sorted(
            (
                (stratum, pool[len(take) :])
                for stratum, info in exhaustion.items()
                for pool in [buckets.get(stratum, [])]
                for take in [pool[: info["quota"]]]
                if len(pool) > info["selected"]
            ),
            key=lambda t: t[0],
        )
        extras: list[EvaluationRecord] = []
        for stratum, remainder in donors:
            for h, d in remainder:
                rid = f"eval_{stable_sample_key(d.coalesced_tender_id)[:24]}"
                if rid in selected_ids:
                    continue
                extras.append(
                    _make_eval_record(
                        h,
                        d,
                        stratum=stratum,
                        product_text_by_tender=product_text_by_tender,
                        has_lines_by_tender=has_lines_by_tender,
                        source_plane_by_tender=source_plane_by_tender,
                        sample_role="diagnostic_stratified",
                    )
                )
                selected_ids.add(rid)
                if len(extras) >= shortfall:
                    break
            if len(extras) >= shortfall:
                break
        selected.extend(extras[:shortfall])
        exhaustion["redistributed_to_fill_target"] = {
            "shortfall_before": shortfall,
            "added": min(shortfall, len(extras)),
            "note": (
                "Unused quota from exhausted strata redistributed to remaining "
                "pools in deterministic stratum/hash order."
            ),
        }

    # Optional representative random holdout (separate from stratified diagnostic set).
    holdout: list[EvaluationRecord] = []
    if holdout_size > 0:
        all_sorted = sorted(
            ((stable_sample_key(d.coalesced_tender_id, salt="pr5d_holdout_v1"), d)
             for d in decisions),
            key=lambda t: t[0],
        )
        selected_ids = {r.record_id for r in selected}
        for h, d in all_sorted:
            rid = f"eval_{stable_sample_key(d.coalesced_tender_id)[:24]}"
            if rid in selected_ids:
                continue
            holdout.append(
                _make_eval_record(
                    h,
                    d,
                    stratum=assign_diagnostic_stratum(
                        predicted_class=d.relevance_class,
                        manual_review=is_manual_review_prediction(
                            relevance_class=d.relevance_class,
                            confidence_band=d.confidence_band,
                            resolution_status=d.product_resolution_status,
                            ambiguity_reason_codes=d.ambiguity_reason_codes,
                            aggregation_reason_codes=d.aggregation_reason_codes,
                        ),
                    ),
                    product_text_by_tender=product_text_by_tender,
                    has_lines_by_tender=has_lines_by_tender,
                    source_plane_by_tender=source_plane_by_tender,
                    sample_role="representative_holdout",
                )
            )
            if len(holdout) >= holdout_size:
                break

    all_records = sorted(selected + holdout, key=lambda r: r.record_id)
    report = sampling_distribution_report(all_records, exhaustion=exhaustion)
    report["note"] = (
        "Diagnostic stratified sample is for error analysis only. "
        "Do not report unweighted stratified results as production-wide performance. "
        "Representative holdout is separate when holdout_size > 0."
    )
    return all_records, report


def _make_eval_record(
    h: str,
    d: TenderRelevanceDecision,
    *,
    stratum: str,
    product_text_by_tender: Mapping[str, str],
    has_lines_by_tender: Mapping[str, bool],
    source_plane_by_tender: Mapping[str, str],
    sample_role: str,
) -> EvaluationRecord:
    text = product_text_by_tender.get(d.coalesced_tender_id, "")
    has_lines = bool(has_lines_by_tender.get(d.coalesced_tender_id, False))
    plane = source_plane_by_tender.get(d.coalesced_tender_id, "unknown")
    richness = "rich" if has_lines else ("moderate" if len(text) > 40 else "sparse")
    redacted, proof = redact_product_wording(text)
    manual = is_manual_review_prediction(
        relevance_class=d.relevance_class,
        confidence_band=d.confidence_band,
        resolution_status=d.product_resolution_status,
        ambiguity_reason_codes=d.ambiguity_reason_codes,
        aggregation_reason_codes=d.aggregation_reason_codes,
    )
    # Alias from full stable hash of tender id — never a prefix substring of the id.
    alias = domain_redacted_alias("tender", d.coalesced_tender_id)
    return EvaluationRecord(
        record_id=f"eval_{stable_sample_key(d.coalesced_tender_id)[:24]}",
        coalesced_tender_alias=alias,
        diagnostic_stratum=stratum,
        product_text_redacted=redacted,
        has_line_descriptions=has_lines,
        source_plane=plane,
        text_richness=richness,
        predicted_relevance_class=d.relevance_class,
        predicted_confidence_band=d.confidence_band,
        predicted_resolution_status=d.product_resolution_status,
        predicted_ambiguity_reason_codes=tuple(d.ambiguity_reason_codes),
        predicted_aggregation_reason_codes=tuple(d.aggregation_reason_codes),
        manual_review_recommended=manual,
        label_status="proposed",
        gold_relevance_class=None,
        review_source=None,
        independently_reviewed=False,
        is_synthetic=False,
        redaction_proof=proof,
        sample_role=sample_role,
    )


def sampling_distribution_report(
    records: Iterable[EvaluationRecord],
    *,
    exhaustion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = list(records)
    by_stratum = Counter(r.diagnostic_stratum for r in rows)
    by_plane = Counter(r.source_plane for r in rows)
    by_richness = Counter(r.text_richness for r in rows)
    with_lines = sum(1 for r in rows if r.has_line_descriptions)
    title_only = sum(1 for r in rows if not r.has_line_descriptions)
    by_role = Counter(r.sample_role for r in rows)
    return {
        "total_records": len(rows),
        "by_diagnostic_stratum": dict(sorted(by_stratum.items())),
        "equipment_first_hits": by_stratum.get(STRATUM_EQUIPMENT, 0),
        "hard_negatives": by_stratum.get(STRATUM_HARD_NEG, 0),
        "ambiguous_or_manual_review": by_stratum.get(STRATUM_AMBIGUOUS, 0),
        "random_non_hits": by_stratum.get(STRATUM_RANDOM, 0),
        "with_lines": with_lines,
        "title_only": title_only,
        "by_source_plane": dict(sorted(by_plane.items())),
        "by_text_richness": dict(sorted(by_richness.items())),
        "by_sample_role": dict(sorted(by_role.items())),
        "stratum_quota_exhaustion": exhaustion or {},
    }


def to_blind_packet(records: Iterable[EvaluationRecord]) -> list[BlindReviewRecord]:
    return [
        BlindReviewRecord(
            record_id=r.record_id,
            coalesced_tender_alias=r.coalesced_tender_alias,
            product_text_redacted=r.product_text_redacted,
            has_line_descriptions=r.has_line_descriptions,
            source_plane=r.source_plane,
            text_richness=r.text_richness,
            sample_role=r.sample_role,
            label_status=r.label_status,
        )
        for r in records
    ]


def to_sealed_scoring(records: Iterable[EvaluationRecord]) -> list[SealedScoringRecord]:
    return [
        SealedScoringRecord(
            record_id=r.record_id,
            diagnostic_stratum=r.diagnostic_stratum,
            predicted_relevance_class=r.predicted_relevance_class,
            predicted_confidence_band=r.predicted_confidence_band,
            predicted_resolution_status=r.predicted_resolution_status,
            predicted_ambiguity_reason_codes=r.predicted_ambiguity_reason_codes,
            predicted_aggregation_reason_codes=r.predicted_aggregation_reason_codes,
            manual_review_recommended=r.manual_review_recommended,
            sample_role=r.sample_role,
        )
        for r in records
    ]


def compute_evaluation_metrics(
    records: Iterable[EvaluationRecord],
) -> dict[str, Any]:
    """Metrics only over independently labelled, non-synthetic gold/reviewed records."""
    rows = list(records)
    proposed = sum(1 for r in rows if r.label_status == "proposed")
    reviewed = sum(1 for r in rows if r.label_status == "reviewed")
    gold = sum(1 for r in rows if r.label_status == "gold")

    rejected: list[dict[str, Any]] = []
    eligible: list[EvaluationRecord] = []
    for r in rows:
        reasons = validate_metric_eligibility(r)
        if reasons:
            if r.label_status in LABEL_STATUSES_METRIC_ELIGIBLE or r.gold_relevance_class:
                rejected.append({"record_id": r.record_id, "reasons": reasons})
            continue
        eligible.append(r)

    base = {
        "proposed_count": proposed,
        "reviewed_count": reviewed,
        "gold_count": gold,
        "labelled_for_metrics_count": len(eligible),
        "rejected_from_metrics": rejected,
        "insufficient_independent_labels": len(eligible) == 0,
        "note": (
            "Proposed and synthetic records are excluded. Metric-eligible records "
            "require reviewed/gold status, non-empty review_source, "
            "independently_reviewed=True, and a valid gold relevance class. "
            "Do not claim production quality without eligible gold labels. "
            "Stratified diagnostic samples must not be reported as production-wide "
            "performance."
        ),
    }
    if not eligible:
        return {
            **base,
            "confusion_matrix": {},
            "precision": None,
            "recall": None,
            "f1": None,
            "abstention_rate": None,
            "false_positives": [],
            "false_negatives": [],
            "by_relevance_class": {},
            "by_evidence_completeness": {},
        }

    strong = {
        "exact_catalog_product",
        "strong_equipment_class",
        "compatible_equipment_class",
    }
    tp = fp = tn = fn = abstain = 0
    matrix: dict[str, dict[str, int]] = {}
    fps: list[str] = []
    fns: list[str] = []
    by_class: dict[str, dict[str, int]] = {}
    by_richness: dict[str, dict[str, int]] = {}

    for r in eligible:
        pred = r.predicted_relevance_class or "ambiguous"
        gold_c = r.gold_relevance_class or "ambiguous"
        matrix.setdefault(gold_c, {})
        matrix[gold_c][pred] = matrix[gold_c].get(pred, 0) + 1
        by_class.setdefault(gold_c, {"n": 0, "correct": 0})
        by_class[gold_c]["n"] += 1
        if pred == gold_c:
            by_class[gold_c]["correct"] += 1
        by_richness.setdefault(r.text_richness, {"n": 0, "correct": 0})
        by_richness[r.text_richness]["n"] += 1
        if pred == gold_c:
            by_richness[r.text_richness]["correct"] += 1

        if is_manual_review_prediction(
            relevance_class=pred,
            confidence_band=r.predicted_confidence_band,
            resolution_status=r.predicted_resolution_status,
            ambiguity_reason_codes=r.predicted_ambiguity_reason_codes,
            aggregation_reason_codes=r.predicted_aggregation_reason_codes,
        ) or r.manual_review_recommended:
            abstain += 1
            continue
        pred_pos = pred in strong
        gold_pos = gold_c in strong
        if pred_pos and gold_pos:
            tp += 1
        elif pred_pos and not gold_pos:
            fp += 1
            fps.append(r.record_id)
        elif not pred_pos and gold_pos:
            fn += 1
            fns.append(r.record_id)
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        (2 * precision * recall / (precision + recall))
        if precision is not None and recall is not None and (precision + recall)
        else None
    )
    abstention_rate = abstain / len(eligible) if eligible else None
    return {
        **base,
        "confusion_matrix": matrix,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "abstention_rate": abstention_rate,
        "false_positives": fps,
        "false_negatives": fns,
        "by_relevance_class": by_class,
        "by_evidence_completeness": by_richness,
        "binary_counts": {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "abstain": abstain},
    }


def write_labeling_queue(path: Path, records: list[EvaluationRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "pr5d_labeling_queue_v2",
        "blind_packet": [r.to_dict() for r in to_blind_packet(records)],
        "sealed_scoring_manifest": [r.to_dict() for r in to_sealed_scoring(records)],
        "metrics": compute_evaluation_metrics(records),
        "sampling": sampling_distribution_report(records),
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


# Back-compat alias used by older imports in planner.
assign_stratum = assign_diagnostic_stratum
