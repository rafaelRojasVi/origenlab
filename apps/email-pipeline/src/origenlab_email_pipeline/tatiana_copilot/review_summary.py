from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .review_schema import ReviewRow, parse_decision, parse_score_1_5


FAILURE_BUCKETS = [
    "grounding_risk",
    "missing_context",
    "tone_mismatch",
    "too_generic",
    "too_long",
    "too_short",
    "unclear_customer_intent",
    "commercial_facts_missing",
    "rewrite_required",
    "other",
]


def _mean(xs: list[int]) -> float | None:
    if not xs:
        return None
    return float(statistics.mean(xs))


def load_review_rows(path: Path) -> list[ReviewRow]:
    rows: list[ReviewRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            abst = (row.get("abstained") or "").strip().lower() == "y"
            rows.append(
                ReviewRow(
                    eval_case_id=(row.get("eval_case_id") or "").strip(),
                    label_expected=(row.get("label_expected") or "").strip(),
                    abstained=abst,
                    reviewer_score_tone=parse_score_1_5(row.get("reviewer_score_tone")),
                    reviewer_score_usefulness=parse_score_1_5(row.get("reviewer_score_usefulness")),
                    reviewer_score_groundedness=parse_score_1_5(row.get("reviewer_score_groundedness")),
                    reviewer_score_edit_distance_estimate=parse_score_1_5(
                        row.get("reviewer_score_edit_distance_estimate")
                    ),
                    reviewer_decision=parse_decision(row.get("reviewer_decision")),
                    notes=(row.get("notes") or "").strip(),
                    system_notes=(row.get("system_notes") or row.get("notes") or "").strip(),
                )
            )
    return rows


def failure_bucket_for_row(rr: ReviewRow) -> str:
    """
    Deterministic, review-friendly heuristics.
    Uses notes keywords first, then scores/decision.
    """
    note = (rr.notes or "").lower()

    # notes-driven keyword mapping
    kw_map = [
        ("missing_context", ["falta", "faltan", "sin info", "sin información", "no indica", "no especifica"]),
        ("commercial_facts_missing", ["precio", "stock", "plazo", "entrega", "garant", "cantidad", "modelo"]),
        ("unclear_customer_intent", ["no entiendo", "no queda claro", "intención", "intent", "confuso"]),
        ("tone_mismatch", ["tono", "muy informal", "muy formal", "suena raro"]),
        ("too_generic", ["genéric", "muy genérico", "vago", "sin detalles"]),
        ("too_long", ["muy largo", "demasiado largo", "acortar"]),
        ("too_short", ["muy corto", "demasiado corto", "falta texto"]),
        ("grounding_risk", ["alucina", "invent", "no corresponde", "afirma", "promete"]),
    ]
    for bucket, kws in kw_map:
        if any(k in note for k in kws):
            return bucket

    # score/decision inference
    if rr.reviewer_decision in {"reject"} and (rr.reviewer_score_edit_distance_estimate or 0) >= 4:
        return "rewrite_required"
    if (rr.reviewer_score_groundedness or 6) <= 2:
        return "grounding_risk"
    if (rr.reviewer_score_usefulness or 6) <= 2:
        return "too_generic"
    if rr.reviewer_decision in {"edit_heavy"}:
        return "rewrite_required"
    if rr.reviewer_decision in {"edit_light"} and (rr.reviewer_score_groundedness or 6) <= 3:
        return "grounding_risk"
    return "other"


@dataclass(frozen=True)
class Recommendation:
    label: str
    rationale: list[str]
    thresholds: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "rationale": list(self.rationale), "thresholds": dict(self.thresholds)}


def recommend_next_phase(summary: dict[str, Any]) -> Recommendation:
    scored = int(summary["counts"]["scored_cases"])
    if scored < 10:
        return Recommendation(
            label="insufficient_review_data",
            rationale=["Need at least 10 scored cases for meaningful decision."],
            thresholds={"min_scored_cases": 10, "scored_cases": scored},
        )

    av = summary["averages"]
    accept = summary["quality"]["pct_accept"]
    accept_or_light = summary["quality"]["pct_accept_or_edit_light"]
    rej_or_heavy = summary["quality"]["pct_edit_heavy_or_reject"]
    grounded_ge4 = summary["quality"]["pct_groundedness_gte_4"]
    useful_ge4 = summary["quality"]["pct_usefulness_gte_4"]

    avg_g = av.get("avg_groundedness")
    avg_u = av.get("avg_usefulness")

    # Transparent thresholds
    thr = {
        "min_scored_cases": 10,
        "avg_groundedness_gte": 4.0,
        "avg_usefulness_gte": 4.0,
        "pct_accept_or_edit_light_gte": 0.70,
        "pct_edit_heavy_or_reject_lte": 0.20,
    }

    if (
        avg_g is not None
        and avg_u is not None
        and avg_g >= thr["avg_groundedness_gte"]
        and avg_u >= thr["avg_usefulness_gte"]
        and accept_or_light >= thr["pct_accept_or_edit_light_gte"]
        and rej_or_heavy <= thr["pct_edit_heavy_or_reject_lte"]
    ):
        return Recommendation(
            label="ready_for_provider_pilot",
            rationale=["Strong scores and low reject/heavy-edit rate."],
            thresholds={**thr, "observed": {"avg_groundedness": avg_g, "avg_usefulness": avg_u, "pct_accept_or_edit_light": accept_or_light, "pct_edit_heavy_or_reject": rej_or_heavy}},
        )

    # Diagnose likely lever
    if avg_g is not None and avg_g < 3.5:
        return Recommendation(
            label="needs_prompting_iteration",
            rationale=["Groundedness is low; tighten guardrails and factual constraints in prompting."],
            thresholds={**thr, "observed": {"avg_groundedness": avg_g}},
        )
    if avg_u is not None and avg_u < 3.5:
        return Recommendation(
            label="needs_retrieval_iteration",
            rationale=["Usefulness is low; retrieval may not be surfacing the right precedents."],
            thresholds={**thr, "observed": {"avg_usefulness": avg_u}},
        )
    if accept_or_light < 0.55:
        return Recommendation(
            label="needs_dataset_cleanup",
            rationale=["Too many heavy edits/rejects; review seed quality and labels."],
            thresholds={**thr, "observed": {"pct_accept_or_edit_light": accept_or_light}},
        )
    return Recommendation(
        label="needs_prompting_iteration",
        rationale=["Mixed results; iterate prompting first, then retrieval if needed."],
        thresholds={**thr, "observed": {"pct_accept": accept, "pct_accept_or_edit_light": accept_or_light, "pct_edit_heavy_or_reject": rej_or_heavy, "pct_groundedness_gte_4": grounded_ge4, "pct_usefulness_gte_4": useful_ge4}},
    )


def summarize_review(rows: list[ReviewRow]) -> dict[str, Any]:
    total = len(rows)
    scored_rows = [r for r in rows if r.is_scored]
    unscored = total - len(scored_rows)
    abstained = sum(1 for r in rows if r.abstained)
    non_abstained = total - abstained

    def vals(attr: str) -> list[int]:
        out: list[int] = []
        for r in scored_rows:
            v = getattr(r, attr)
            if isinstance(v, int):
                out.append(v)
        return out

    tone = vals("reviewer_score_tone")
    useful = vals("reviewer_score_usefulness")
    grounded = vals("reviewer_score_groundedness")
    edit = vals("reviewer_score_edit_distance_estimate")

    decision_counts = Counter(r.reviewer_decision or "" for r in scored_rows)
    # remove empty key for cleanliness
    if "" in decision_counts:
        decision_counts.pop("", None)

    dist = {
        "tone": dict(Counter(tone)),
        "usefulness": dict(Counter(useful)),
        "groundedness": dict(Counter(grounded)),
        "edit_distance_estimate": dict(Counter(edit)),
    }

    def pct(n: int, d: int) -> float:
        return (n / d) if d else 0.0

    accept = decision_counts.get("accept", 0)
    edit_light = decision_counts.get("edit_light", 0)
    edit_heavy = decision_counts.get("edit_heavy", 0)
    reject = decision_counts.get("reject", 0)

    quality = {
        "pct_accept": pct(accept, len(scored_rows)),
        "pct_accept_or_edit_light": pct(accept + edit_light, len(scored_rows)),
        "pct_edit_heavy_or_reject": pct(edit_heavy + reject, len(scored_rows)),
        "pct_groundedness_gte_4": pct(sum(1 for x in grounded if x >= 4), len(grounded)),
        "pct_usefulness_gte_4": pct(sum(1 for x in useful if x >= 4), len(useful)),
        "pct_edit_distance_lte_2": pct(sum(1 for x in edit if x <= 2), len(edit)),
    }

    # cross-metric diagnostics
    diag = {
        "low_groundedness_cases": [
            r.eval_case_id for r in scored_rows if (r.reviewer_score_groundedness or 6) <= 2
        ],
        "high_useful_low_grounded_cases": [
            r.eval_case_id
            for r in scored_rows
            if (r.reviewer_score_usefulness or 0) >= 4 and (r.reviewer_score_groundedness or 6) <= 2
        ],
        "good_tone_poor_usefulness_cases": [
            r.eval_case_id
            for r in scored_rows
            if (r.reviewer_score_tone or 0) >= 4 and (r.reviewer_score_usefulness or 6) <= 2
        ],
        "heavy_edit_or_reject_cases": [
            r.eval_case_id for r in scored_rows if (r.reviewer_decision or "") in {"edit_heavy", "reject"}
        ],
    }

    bucket_counts = Counter(failure_bucket_for_row(r) for r in scored_rows)

    summary: dict[str, Any] = {
        "counts": {
            "total_eval_cases": total,
            "scored_cases": len(scored_rows),
            "unscored_cases": unscored,
            "abstained_cases": abstained,
            "non_abstained_cases": non_abstained,
        },
        "averages": {
            "avg_tone": _mean(tone),
            "avg_usefulness": _mean(useful),
            "avg_groundedness": _mean(grounded),
            "avg_edit_distance_estimate": _mean(edit),
        },
        "distributions": {
            "reviewer_decision_counts": dict(decision_counts),
            "score_distributions": dist,
        },
        "quality": quality,
        "diagnostics": diag,
        "failure_buckets": dict(bucket_counts),
    }

    summary["recommendation"] = recommend_next_phase(summary).to_dict()
    return summary


def write_review_outputs(*, summary: dict[str, Any], rows: list[ReviewRow], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # failures/priorities
    scored = [r for r in rows if r.is_scored]
    failures = [
        r
        for r in scored
        if (r.reviewer_decision in {"edit_heavy", "reject"})
        or ((r.reviewer_score_groundedness or 6) <= 2)
        or ((r.reviewer_score_usefulness or 6) <= 2)
        or (r.notes.strip() != "")
    ]

    def _write_csv(name: str, rs: list[ReviewRow]) -> None:
        p = out_dir / name
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "eval_case_id",
                    "label_expected",
                    "abstained",
                    "reviewer_decision",
                    "reviewer_score_tone",
                    "reviewer_score_usefulness",
                    "reviewer_score_groundedness",
                    "reviewer_score_edit_distance_estimate",
                    "failure_bucket",
                    "notes",
                ],
            )
            w.writeheader()
            for r in rs:
                w.writerow(
                    {
                        "eval_case_id": r.eval_case_id,
                        "label_expected": r.label_expected,
                        "abstained": "y" if r.abstained else "n",
                        "reviewer_decision": r.reviewer_decision or "",
                        "reviewer_score_tone": r.reviewer_score_tone or "",
                        "reviewer_score_usefulness": r.reviewer_score_usefulness or "",
                        "reviewer_score_groundedness": r.reviewer_score_groundedness or "",
                        "reviewer_score_edit_distance_estimate": r.reviewer_score_edit_distance_estimate
                        or "",
                        "failure_bucket": failure_bucket_for_row(r),
                        "notes": r.notes,
                    }
                )

    _write_csv("review_failures.csv", failures)
    _write_csv("review_priority_cases.csv", sorted(failures, key=lambda r: r.eval_case_id))

    # markdown summary
    rec = summary["recommendation"]
    md = []
    md.append("# Tatiana Draft Eval Review Summary\n")
    md.append("## Headline\n")
    md.append(
        f"- **scored_cases**: {summary['counts']['scored_cases']} / {summary['counts']['total_eval_cases']}\n"
    )
    md.append(f"- **abstained_cases**: {summary['counts']['abstained_cases']}\n")
    md.append(
        f"- **decision_counts**: {summary['distributions']['reviewer_decision_counts']}\n"
    )
    md.append("\n## Averages (1–5)\n")
    for k, v in summary["averages"].items():
        md.append(f"- **{k}**: {v if v is not None else 'n/a'}\n")
    md.append("\n## Quality indicators\n")
    for k, v in summary["quality"].items():
        md.append(f"- **{k}**: {round(v*100,1)}%\n")
    md.append("\n## Failure buckets\n")
    md.append(f"- {summary['failure_buckets']}\n")
    md.append("\n## Recommendation\n")
    md.append(f"- **{rec['label']}**\n")
    for line in rec["rationale"]:
        md.append(f"  - {line}\n")
    md.append("\n## Diagnostics (case ids)\n")
    for k, ids in summary["diagnostics"].items():
        md.append(f"- **{k}**: {ids[:20]}\n")

    (out_dir / "review_summary.md").write_text("".join(md), encoding="utf-8")
