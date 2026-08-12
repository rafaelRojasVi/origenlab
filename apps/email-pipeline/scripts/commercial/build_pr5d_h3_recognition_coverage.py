#!/usr/bin/env python3
"""Build PR5D-H3 recognition-coverage review packet (offline, gitignored out/).

Writes under reports/out/active/current/pr5d_h3_recognition_coverage/:
  coverage_matrix.json / .csv
  positive_examples.jsonl
  negative_controls.jsonl
  walkthrough.md

Does not mutate queues, catalogs, or production state. No network.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    load_catalog_capability,
    match_catalog_status,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (
    CANONICAL_EQUIPMENT_CLASSES,
    EXACT_CLASS_MAPPINGS,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_RELEVANCE_RULES_VERSION,
    PRODUCT_RELEVANCE_TAXONOMY_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    AUTOCLAVE_EQ_RE,
    BALANCE_EQ_RE,
    CENTRIFUGE_EQ_RE,
    DISSOLUTION_RE,
    HOMOGENIZER_RE,
    INCUBATOR_EQ_RE,
    MAGNETIC_STIRRER_RE,
    MICROSCOPE_EQ_RE,
    ORBITAL_SHAKER_RE,
    RULE_PRECEDENCE,
    SEDIMENTATION_RE,
    TABLET_TEST_RE,
    ULTRASONIC_BATH_RE,
    ULTRASONIC_PROCESSOR_RE,
    VORTEX_RE,
)

# Positive detectors currently wired into equipment_class_positive_hits.
_IMPLEMENTED: dict[str, tuple[re.Pattern[str], tuple[str, ...]]] = {
    "ultrasonic_processor": (ULTRASONIC_PROCESSOR_RE, ("ultrasonic_processor",)),
    "ultrasonic_bath": (ULTRASONIC_BATH_RE, ("ultrasonic_bath",)),
    "centrifuge": (CENTRIFUGE_EQ_RE, ("centrifuge",)),
    "balance": (BALANCE_EQ_RE, ("balance",)),
    "microscope": (MICROSCOPE_EQ_RE, ("microscope",)),
    "incubator": (INCUBATOR_EQ_RE, ("incubator",)),
    "autoclave": (AUTOCLAVE_EQ_RE, ("autoclave",)),
    "homogenizer": (HOMOGENIZER_RE, ("homogenizer",)),
    "shaker": (ORBITAL_SHAKER_RE, ("shaker",)),
    "vortex_mixer": (VORTEX_RE, ("vortex_mixer",)),
    "magnetic_stirrer": (MAGNETIC_STIRRER_RE, ("magnetic_stirrer",)),
    "tablet_hardness_tester": (TABLET_TEST_RE, ("tablet_hardness_tester",)),
    "dissolution_apparatus": (DISSOLUTION_RE, ("dissolution_apparatus",)),
    "sedimentation_settlometer": (SEDIMENTATION_RE, ("sedimentation_settlometer",)),
}

_AUDIT_SCAN = {
    "autoclave": re.compile(r"\bautoclaves?\b", re.I),
    "chromatography_hplc": re.compile(r"\bhplc\b|cromat[oó]grafo", re.I),
    "pipette": re.compile(r"\bpipet", re.I),
    "lyophilizer": re.compile(r"liofiliz|lyophil", re.I),
    "reactor": re.compile(r"\breactor(es)?\b", re.I),
    "osmometer": re.compile(r"osm[oó]metr", re.I),
    "spectrophotometer": re.compile(r"espectrofot|spectrophot", re.I),
    "ph_meter": re.compile(r"\bph[\s\-]?metr", re.I),
    "plate_reader": re.compile(
        r"plate\s*reader|lector\s+de\s+placas|lector\s+multimod", re.I
    ),
    "oven_muffle": re.compile(r"\bmufla\b|horno\s+mufla|muffle", re.I),
    "titrator": re.compile(r"titulador|titrator", re.I),
}

_KNOWN_POSITIVES: dict[str, list[dict[str, Any]]] = {
    "autoclave": [
        {
            "tender_id": "986278-12-LE26",
            "example": "ADQUISICION DE AUTOCLAVE LABORATORIO",
            "provenance": "anexo_e2_deferred52 evidence_chunks + human_review",
            "note": "genuine buyer acquisition; incomplete OCR coverage debt remains",
        }
    ],
}

_KNOWN_NEGATIVES: dict[str, list[dict[str, Any]]] = {
    "chromatography_hplc": [
        {
            "example": "HEMOGLOBINA GLICADA POR HPLC",
            "provenance": "deferred52 1057510-44-LR26 / H1 gold",
            "note": "method semantics, not instrument procurement",
        },
        {
            "example": "Método de Medición | HPLC de intercambio iónico",
            "provenance": "deferred52 1057510-44-LR26 / H1 gold",
            "note": "method semantics",
        },
    ],
    "lyophilizer": [
        {
            "example": "Reactivo liofilizado",
            "provenance": "deferred52 1057510-30-LR26 / H1 gold",
            "note": "adjective on reagent, not lyophilizer instrument",
        },
        {
            "example": "Calibradores liofilizados",
            "provenance": "deferred52 1057510-44-LR26 / H1 gold",
            "note": "adjective on calibrators",
        },
    ],
    "pipette": [
        {
            "example": "Pipeta Pasteur plástica",
            "provenance": "deferred52 4034-16-LE26 / H1 gold",
            "note": "labware/consumable",
        },
        {
            "example": "PUNTA PARA PIPETA CON CORONA",
            "provenance": "deferred52 699866-85-LE26 / H1 gold",
            "note": "pipette tip consumable",
        },
    ],
    "autoclave": [
        {
            "example": "mantención de autoclave",
            "provenance": "synthetic H3 negative control",
            "note": "service/maintenance",
        },
        {
            "example": "repuesto para autoclave",
            "provenance": "synthetic H3 negative control",
            "note": "accessory/replacement",
        },
        {
            "example": "el oferente deberá disponer de autoclave",
            "provenance": "synthetic H3 negative control",
            "note": "supplier-required context",
        },
    ],
}


def _aliases(canonical: str) -> list[str]:
    out: list[str] = []
    for row in EXACT_CLASS_MAPPINGS:
        if row.get("canonical") == canonical:
            out.extend(list(row.get("source_aliases") or []))
    return sorted(set(out))


def _recommend(canonical: str, implemented: bool, corpus_hits: int) -> str:
    if implemented and canonical != "autoclave":
        return "already_implemented"
    if canonical == "autoclave":
        return "implement_now"
    if canonical in {"chromatography_hplc", "pipette", "lyophilizer"}:
        return "context_required"
    if canonical == "other_lab_equipment":
        return "keep_unimplemented"
    if canonical in {
        "reactor",
        "osmometer",
        "spectrophotometer",
        "ph_meter",
        "plate_reader",
        "oven_muffle",
        "titrator",
    }:
        return "requires_more_gold" if corpus_hits == 0 else "context_required"
    return "requires_more_gold"


def _scan_chunks(chunks_path: Path) -> dict[str, list[dict[str, Any]]]:
    hits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not chunks_path.is_file():
        return hits
    with chunks_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            text = row.get("text") or ""
            tid = row.get("tender_id")
            chunk_id = row.get("chunk_id")
            for key, pattern in _AUDIT_SCAN.items():
                m = pattern.search(text)
                if not m:
                    continue
                if len(hits[key]) >= 12:
                    continue
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 60)
                hits[key].append(
                    {
                        "tender_id": tid,
                        "chunk_id": chunk_id,
                        "matched_term": m.group(0),
                        "snippet": text[start:end].replace("\n", " ")[:180],
                        "provenance": str(chunks_path),
                    }
                )
    return hits


def build_matrix(corpus_hits: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    capability = load_catalog_capability()
    rows: list[dict[str, Any]] = []
    for canonical in CANONICAL_EQUIPMENT_CLASSES:
        implemented = canonical in _IMPLEMENTED
        rule_ids: list[str] = []
        if implemented:
            rule_ids = ["equipment_class_positive_hits", canonical]
            if canonical == "autoclave":
                rule_ids.append("autoclave_accessory_or_replacement")
        status = match_catalog_status(canonical, is_purchase_signal=True)
        confirmed = canonical in capability.product_confirmed_equipment_classes
        hits = corpus_hits.get(canonical, [])
        rows.append(
            {
                "canonical_class": canonical,
                "recognition_implemented": implemented,
                "recognition_rule_ids": rule_ids,
                "known_source_aliases": _aliases(canonical),
                "catalog_match_status": status,
                "confirmed_catalog_product": confirmed,
                "known_positive_evidence_examples": [
                    e.get("example") or e.get("snippet")
                    for e in (_KNOWN_POSITIVES.get(canonical) or []) + hits[:3]
                ],
                "known_negative_noise_examples": [
                    e.get("example") for e in (_KNOWN_NEGATIVES.get(canonical) or [])
                ],
                "deferred52_scan_hit_count": len(hits),
                "recommended_action": _recommend(canonical, implemented, len(hits)),
                "rules_version": PRODUCT_RELEVANCE_RULES_VERSION,
                "taxonomy_version": PRODUCT_RELEVANCE_TAXONOMY_VERSION,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path(
            "reports/out/active/current/chilecompra_anexo_evidence_e2_deferred52/"
            "evidence_chunks.jsonl"
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("reports/out/active/current/pr5d_h3_recognition_coverage"),
    )
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    corpus_hits = _scan_chunks(args.chunks)
    matrix = build_matrix(corpus_hits)

    (out_dir / "coverage_matrix.json").write_text(
        json.dumps(
            {
                "rules_version": PRODUCT_RELEVANCE_RULES_VERSION,
                "taxonomy_version": PRODUCT_RELEVANCE_TAXONOMY_VERSION,
                "rule_ids": [r["rule_id"] for r in RULE_PRECEDENCE],
                "items": matrix,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    fieldnames = list(matrix[0].keys()) if matrix else []
    with (out_dir / "coverage_matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in matrix:
            flat = dict(row)
            for key in (
                "recognition_rule_ids",
                "known_source_aliases",
                "known_positive_evidence_examples",
                "known_negative_noise_examples",
            ):
                flat[key] = "|".join(str(x) for x in (row.get(key) or []))
            writer.writerow(flat)

    with (out_dir / "positive_examples.jsonl").open("w", encoding="utf-8") as fh:
        for canonical, examples in _KNOWN_POSITIVES.items():
            for ex in examples:
                fh.write(
                    json.dumps({"canonical_class": canonical, **ex}, sort_keys=True)
                    + "\n"
                )
        for canonical, hits in corpus_hits.items():
            if canonical not in _KNOWN_POSITIVES:
                continue
            for hit in hits:
                fh.write(
                    json.dumps(
                        {"canonical_class": canonical, "kind": "corpus_scan", **hit},
                        sort_keys=True,
                    )
                    + "\n"
                )

    with (out_dir / "negative_controls.jsonl").open("w", encoding="utf-8") as fh:
        for canonical, examples in _KNOWN_NEGATIVES.items():
            for ex in examples:
                fh.write(
                    json.dumps({"canonical_class": canonical, **ex}, sort_keys=True)
                    + "\n"
                )

    implement_now = [r["canonical_class"] for r in matrix if r["recommended_action"] == "implement_now"]
    more_gold = [r["canonical_class"] for r in matrix if r["recommended_action"] == "requires_more_gold"]
    context = [r["canonical_class"] for r in matrix if r["recommended_action"] == "context_required"]
    implemented = [r["canonical_class"] for r in matrix if r["recognition_implemented"]]
    missing = [r["canonical_class"] for r in matrix if not r["recognition_implemented"]]

    walkthrough = "\n".join(
        [
            "# PR5D-H3 recognition coverage walkthrough",
            "",
            f"- rules_version: `{PRODUCT_RELEVANCE_RULES_VERSION}`",
            f"- taxonomy_version: `{PRODUCT_RELEVANCE_TAXONOMY_VERSION}` (unchanged)",
            "- aggregation_policy: `aggregation_policy_v3` (unchanged)",
            f"- canonical class count: {len(CANONICAL_EQUIPMENT_CLASSES)}",
            f"- recognition implemented: {', '.join(implemented)}",
            f"- recognition missing: {', '.join(missing)}",
            f"- recommend implement_now: {', '.join(implement_now) or '(none)'}",
            f"- recommend context_required: {', '.join(context) or '(none)'}",
            f"- recommend requires_more_gold: {', '.join(more_gold) or '(none)'}",
            "",
            "## H3 scope decision",
            "",
            "Implement **autoclave** only in this PR.",
            "Catalog capability unchanged — autoclave remains `outside_recorded_catalog`.",
            "Recognition proves equipment intelligence; it does not create a sellable opportunity.",
            "",
            "## Deferred-52 scan notes",
            "",
            "- autoclave: 986278-12-LE26 acquisition titles",
            "- HPLC / liofilizado / pipette: method or consumable noise only",
            "- reactor / osmometer / spectrophotometer / ph_meter / plate_reader /",
            "  oven_muffle / titrator: no high-confidence instrument hits in deferred52 chunks",
            "",
            "No raw annex bytes are stored in this packet.",
            "",
        ]
    )
    (out_dir / "walkthrough.md").write_text(walkthrough, encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), "implement_now": implement_now}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
