"""Evaluation corpus foundation: contract fixtures vs real review queue."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_RUT_RE = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")


@dataclass(frozen=True)
class EvaluationRecord:
    record_id: str
    coalesced_tender_id_redacted: str
    sample_stratum: str
    product_text_redacted: str
    has_line_descriptions: bool
    source_plane: str
    text_richness: str
    predicted_relevance_class: str | None
    label_status: str  # proposed | reviewed | gold | rejected
    gold_relevance_class: str | None
    review_source: str | None
    is_synthetic: bool
    redaction_proof: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def redact_product_wording(text: str) -> tuple[str, dict[str, Any]]:
    """Redact PII-ish tokens while retaining product wording."""
    original = text or ""
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", original)
    redacted = _PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = _URL_RE.sub("[REDACTED_URL]", redacted)
    redacted = _RUT_RE.sub("[REDACTED_TAX_ID]", redacted)
    # Scrub long numeric IDs that look like tender codes (keep short quantities).
    redacted = re.sub(r"\b[A-Z]{2,5}-\d{4}-\d{4,}\b", "[REDACTED_TENDER_ID]", redacted)
    redacted = re.sub(r"\b\d{8,}\b", "[REDACTED_ID]", redacted)
    proof = {
        "email_redactions": len(_EMAIL_RE.findall(original)),
        "phone_redactions": len(_PHONE_RE.findall(original)),
        "url_redactions": len(_URL_RE.findall(original)),
        "tax_id_redactions": len(_RUT_RE.findall(original)),
        "sha256_original_normalized": hashlib.sha256(
            normalize_product_text(original).encode("utf-8")
        ).hexdigest(),
        "sha256_redacted_normalized": hashlib.sha256(
            normalize_product_text(redacted).encode("utf-8")
        ).hexdigest(),
        "retained_product_tokens": normalize_product_text(redacted).split()[:40],
    }
    return redacted, proof


def stable_sample_key(coalesced_tender_id: str, salt: str = "pr5d_eval_v1") -> str:
    return hashlib.sha256(f"{salt}:{coalesced_tender_id}".encode("utf-8")).hexdigest()


def assign_stratum(
    *,
    predicted_class: str | None,
    has_line_descriptions: bool,
    source_plane: str,
    text_richness: str,
    sample_hash: str,
) -> str:
    """Deterministic stratum for stratified sampling (not keyword-only)."""
    nibble = int(sample_hash[:2], 16)
    if predicted_class in {
        "exact_catalog_product",
        "strong_equipment_class",
        "compatible_equipment_class",
    }:
        base = "equipment_first_hit"
    elif predicted_class in {
        "consumable_or_reagent",
        "service_or_maintenance_only",
        "rental_or_comodato",
        "non_laboratory_false_positive",
    }:
        base = "likely_hard_negative"
    elif predicted_class == "ambiguous":
        base = "ambiguous_or_mixed"
    else:
        base = "random_non_hit" if nibble % 2 == 0 else "random_non_hit_b"
    richness = "with_lines" if has_line_descriptions else "title_only"
    return f"{base}|{source_plane}|{richness}|{text_richness}"


def build_labeling_queue(
    decisions: Iterable[TenderRelevanceDecision],
    *,
    product_text_by_tender: Mapping[str, str],
    has_lines_by_tender: Mapping[str, bool],
    source_plane_by_tender: Mapping[str, str],
    target_size: int = 200,
) -> list[EvaluationRecord]:
    """Operator-local proposed labels from stable-hash sample. Not gold."""
    rows: list[tuple[str, TenderRelevanceDecision, str]] = []
    for d in decisions:
        h = stable_sample_key(d.coalesced_tender_id)
        rows.append((h, d, h))
    rows.sort(key=lambda t: t[0])

    # Take a deterministic prefix; diversify by forcing stratum coverage.
    selected: list[EvaluationRecord] = []
    seen_strata: set[str] = set()
    for h, d, _ in rows:
        text = product_text_by_tender.get(d.coalesced_tender_id, "")
        has_lines = bool(has_lines_by_tender.get(d.coalesced_tender_id, False))
        plane = source_plane_by_tender.get(d.coalesced_tender_id, "unknown")
        richness = (
            "rich"
            if has_lines
            else ("moderate" if len(text) > 40 else "sparse")
        )
        stratum = assign_stratum(
            predicted_class=d.relevance_class,
            has_line_descriptions=has_lines,
            source_plane=plane,
            text_richness=richness,
            sample_hash=h,
        )
        # Prefer filling new strata first.
        if stratum in seen_strata and len(selected) >= min(40, target_size // 2):
            continue
        redacted, proof = redact_product_wording(text)
        redacted_id = f"redacted_tender_{h[:16]}"
        selected.append(
            EvaluationRecord(
                record_id=f"eval_{h[:24]}",
                coalesced_tender_id_redacted=redacted_id,
                sample_stratum=stratum,
                product_text_redacted=redacted,
                has_line_descriptions=has_lines,
                source_plane=plane,
                text_richness=richness,
                predicted_relevance_class=d.relevance_class,
                label_status="proposed",
                gold_relevance_class=None,
                review_source=None,
                is_synthetic=False,
                redaction_proof=proof,
            )
        )
        seen_strata.add(stratum)
        if len(selected) >= target_size:
            break

    # Fill remaining slots from hash order if strata gate was too strict.
    if len(selected) < target_size:
        have = {r.record_id for r in selected}
        for h, d, _ in rows:
            rid = f"eval_{h[:24]}"
            if rid in have:
                continue
            text = product_text_by_tender.get(d.coalesced_tender_id, "")
            has_lines = bool(has_lines_by_tender.get(d.coalesced_tender_id, False))
            plane = source_plane_by_tender.get(d.coalesced_tender_id, "unknown")
            richness = (
                "rich"
                if has_lines
                else ("moderate" if len(text) > 40 else "sparse")
            )
            stratum = assign_stratum(
                predicted_class=d.relevance_class,
                has_line_descriptions=has_lines,
                source_plane=plane,
                text_richness=richness,
                sample_hash=h,
            )
            redacted, proof = redact_product_wording(text)
            selected.append(
                EvaluationRecord(
                    record_id=rid,
                    coalesced_tender_id_redacted=f"redacted_tender_{h[:16]}",
                    sample_stratum=stratum,
                    product_text_redacted=redacted,
                    has_line_descriptions=has_lines,
                    source_plane=plane,
                    text_richness=richness,
                    predicted_relevance_class=d.relevance_class,
                    label_status="proposed",
                    gold_relevance_class=None,
                    review_source=None,
                    is_synthetic=False,
                    redaction_proof=proof,
                )
            )
            if len(selected) >= target_size:
                break

    selected.sort(key=lambda r: r.record_id)
    return selected


def compute_evaluation_metrics(
    records: Iterable[EvaluationRecord],
) -> dict[str, Any]:
    """Metrics only over independently labelled gold/reviewed records."""
    labelled = [
        r
        for r in records
        if r.label_status in {"gold", "reviewed"} and r.gold_relevance_class
    ]
    proposed = sum(1 for r in records if r.label_status == "proposed")
    reviewed = sum(1 for r in records if r.label_status == "reviewed")
    gold = sum(1 for r in records if r.label_status == "gold")

    base = {
        "proposed_count": proposed,
        "reviewed_count": reviewed,
        "gold_count": gold,
        "labelled_for_metrics_count": len(labelled),
        "insufficient_independent_labels": len(labelled) == 0,
        "note": (
            "Proposed auto-sample labels are excluded from precision/recall. "
            "Do not claim production quality without gold labels."
        ),
    }
    if not labelled:
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

    # Binary relevant vs not for headline metrics; detailed matrix by class.
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

    for r in labelled:
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

        if pred == "ambiguous":
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
    abstention_rate = abstain / len(labelled) if labelled else None
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
        "schema": "pr5d_labeling_queue_v1",
        "records": [r.to_dict() for r in records],
        "metrics": compute_evaluation_metrics(records),
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
