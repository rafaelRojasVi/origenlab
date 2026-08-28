"""Guardrails for docs/TATIANA_LAB_BOUNDARY.md and lab cross-links."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_BOUNDARY = _REPO / "docs" / "TATIANA_LAB_BOUNDARY.md"
_SCRIPT_MAP = _REPO / "docs" / "SCRIPT_MAP.md"

_LAB_SCRIPT_PATHS = (
    "scripts/tatiana",
    "scripts/dataset",
    "scripts/ml",
    "scripts/leads/campaigns",
    "scripts/reports/build_ml_report.py",
)


def test_tatiana_lab_boundary_lists_lab_script_surfaces() -> None:
    text = _BOUNDARY.read_text(encoding="utf-8")
    for path in _LAB_SCRIPT_PATHS:
        assert path in text, f"TATIANA_LAB_BOUNDARY must list lab surface: {path!r}"


def test_tatiana_lab_boundary_not_daily_production() -> None:
    text = _BOUNDARY.read_text(encoding="utf-8").lower()
    assert "not daily outbound" in text or "not daily production" in text


def test_tatiana_lab_boundary_not_send_approval() -> None:
    text = _BOUNDARY.read_text(encoding="utf-8").lower()
    assert "not send approval" in text


def test_tatiana_lab_boundary_mentions_gmail_sent() -> None:
    text = _BOUNDARY.read_text(encoding="utf-8")
    lower = text.lower()
    assert "gmail sent" in lower


def test_script_map_links_tatiana_lab_boundary_near_lab_entries() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    assert "TATIANA_LAB_BOUNDARY.md" in text
    assert "lab boundary" in text.lower()
    parked_section = text.split("### Parked / legacy / removed (do not use for new work)", 1)[-1].split(
        "\n---\n", 1
    )[0]
    assert "scripts/tatiana" in parked_section
    assert "TATIANA_LAB_BOUNDARY.md" in parked_section
    lab_section = text.split("## Lab scripts (LAB)", 1)[-1].split("\n---\n", 1)[0]
    assert "scripts/tatiana" in lab_section
    assert "TATIANA_LAB_BOUNDARY.md" in lab_section
