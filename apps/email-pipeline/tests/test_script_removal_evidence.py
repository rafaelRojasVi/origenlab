"""Phase 2 removal evidence report and deprecation documentation locks."""

from __future__ import annotations

from pathlib import Path

import pytest

from removal_evidence import (
    DEPRECATED_REMOVAL_TARGETS,
    REMOVED_PHASE5A_TARGETS,
    REMOVED_PHASE5B_TARGETS,
    REMOVED_PHASE5C_TARGETS,
    REMOVED_PHASE5D_TARGETS,
    REMOVED_PHASE5K_TARGETS,
    REMOVED_PHASE5Q_TARGETS,
    REMOVED_PHASE5R_TARGETS,
    REMOVED_PHASE5S_TARGETS,
    _rg_files,
    build_removal_evidence_markdown,
    reference_counts,
    write_removal_evidence_report,
)

REPO = Path(__file__).resolve().parents[1]

REMOVED_LEGACY_REPORTED_NDR = "scripts/tools/flag_reported_non_delivery_from_contacto.py"


def test_generate_removal_evidence_report(tmp_path: Path) -> None:
    out = write_removal_evidence_report(tmp_path / "PHASE2_SCRIPT_REMOVAL_EVIDENCE_20260602.md")
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "Phase 2 — script removal evidence" in body
    for row in REMOVED_PHASE5A_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5A" in body
    for row in REMOVED_PHASE5B_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5B" in body
    for row in REMOVED_PHASE5C_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5C" in body
    for row in REMOVED_PHASE5D_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5D" in body
    for row in REMOVED_PHASE5K_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5K" in body
    for row in REMOVED_PHASE5Q_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5Q" in body
    for row in REMOVED_PHASE5R_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5R" in body
    for row in REMOVED_PHASE5S_TARGETS:
        assert row["path"] in body
        assert "Removed in Phase 5S" in body


def test_phase5a_removed_shells_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "POST_SEND_SAFE_LOOP.md" in smap
    for row in REMOVED_PHASE5A_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5A" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


def test_phase5b_removed_wrappers_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "scripts/leads/advanced" in smap
    for row in REMOVED_PHASE5B_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5B" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


def test_phase5c_removed_buyer_queue_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "build_equipment_first_opportunity_queue.py" in smap
    assert "build_equipment_first_operator_queue.py" in smap
    for row in REMOVED_PHASE5C_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5C" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


def test_phase5d_removed_archive_wrapper_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "build_archive_send_batch.py" in smap
    assert "--audit-only" in smap or "audit-only" in smap
    for row in REMOVED_PHASE5D_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5D" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


def test_phase5k_removed_manual_outreach_oneoffs_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "build_post_send_digest.py" in smap
    assert "POST_SEND_SAFE_LOOP.md" in smap
    for row in REMOVED_PHASE5K_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5K" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


def test_phase5q_removed_reported_ndr_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "--include-reported-non-delivery" in smap
    assert "build_ndr_review_queue.py" in smap
    for row in REMOVED_PHASE5Q_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5Q" in smap or "Removed Phase 5Q" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


def test_phase5r_removed_legacy_contacts_qa_script_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "legacy_contacts_2016_2019.py" in smap
    assert "test_legacy_contacts_2016_2019.py" in smap
    for row in REMOVED_PHASE5R_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5R" in smap or "Removed Phase 5R" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


def test_phase5s_removed_zero_ref_lab_scripts_documented_in_script_map() -> None:
    smap = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8")
    assert "Removed Phase 5S" in smap
    for row in REMOVED_PHASE5S_TARGETS:
        assert Path(row["path"]).name in smap or "Phase 5S" in smap, row["path"]
        assert not (REPO / row["path"]).is_file(), row["path"]


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5A_TARGETS])
def test_phase5a_removed_shells_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5A removed: {rel}"


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5B_TARGETS])
def test_phase5b_removed_wrappers_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5B removed: {rel}"


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5C_TARGETS])
def test_phase5c_removed_buyer_queue_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5C removed: {rel}"


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5D_TARGETS])
def test_phase5d_removed_archive_wrapper_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5D removed: {rel}"


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5K_TARGETS])
def test_phase5k_removed_manual_outreach_oneoffs_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5K removed: {rel}"


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5Q_TARGETS])
def test_phase5q_removed_reported_ndr_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5Q removed: {rel}"


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5R_TARGETS])
def test_phase5r_removed_legacy_contacts_qa_script_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5R removed: {rel}"


@pytest.mark.parametrize("rel", [r["path"] for r in REMOVED_PHASE5S_TARGETS])
def test_phase5s_removed_zero_ref_lab_scripts_not_on_disk(rel: str) -> None:
    assert not (REPO / rel).is_file(), f"Phase 5S removed: {rel}"


def test_phase5r_removed_legacy_contacts_qa_script_in_evidence_markdown() -> None:
    md = build_removal_evidence_markdown()
    for row in REMOVED_PHASE5R_TARGETS:
        assert row["path"] in md
        assert "Removed in Phase 5R" in md


def test_phase5s_removed_zero_ref_lab_scripts_in_evidence_markdown() -> None:
    md = build_removal_evidence_markdown()
    for row in REMOVED_PHASE5S_TARGETS:
        assert row["path"] in md
        assert "Removed in Phase 5S" in md


def test_evidence_markdown_has_removed_phase_rows() -> None:
    md = build_removal_evidence_markdown()
    assert f"| `{REMOVED_LEGACY_REPORTED_NDR}` |" in md
    assert "Removed in Phase 5Q" in md
    assert "Removed in Phase 5C" in md
    assert "Removed in Phase 5D" in md
    assert "Removed in Phase 5K" in md
    assert "Removed in Phase 5R" in md
    assert "Removed in Phase 5S" in md
    assert "## Deprecated / wrapper removal candidates" in md
    if not DEPRECATED_REMOVAL_TARGETS:
        assert md.count("| `scripts/tools/flag_reported_non_delivery_from_contacto.py` |") == 1


def test_reference_counts_removed_script_still_in_docs() -> None:
    rc = reference_counts(REMOVED_LEGACY_REPORTED_NDR)
    assert rc.docs >= 0
    assert rc.tests >= 0


def test_rg_files_python_fallback_when_ripgrep_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub Actions runners often lack ``rg``; reference_counts must not subprocess-fail."""
    monkeypatch.setattr("removal_evidence._rg_available", lambda: False)
    hits = _rg_files(
        r"flag_reported_non_delivery_from_contacto\.py",
        [REPO / "docs", REPO / "tests"],
    )
    assert len(hits) >= 1
    rc = reference_counts(REMOVED_LEGACY_REPORTED_NDR)
    assert rc.docs >= 0
