"""Guardrail: zero-ref advanced helpers stay classified as non-active in SCRIPT_MAP."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT_MAP = _REPO / "docs" / "SCRIPT_MAP.md"

_SPANISH_SCRIPT = "export_leads_spanish_csvs.py"
_WEB_SERVER_SCRIPT = "run_contact_hunt_web_server.py"


def test_script_map_does_not_classify_helpers_as_active_operator_command() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    for script in (_SPANISH_SCRIPT, _WEB_SERVER_SCRIPT):
        assert script in text
        for line in text.splitlines():
            if script not in line:
                continue
            assert "active_operator_command" not in line, (
                f"{script} must not be classified active_operator_command: {line!r}"
            )


def test_script_map_marks_helpers_parked_or_owner_review() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8").lower()
    assert _SPANISH_SCRIPT in text
    assert _WEB_SERVER_SCRIPT in text
    assert "owner review" in text or "parked" in text
