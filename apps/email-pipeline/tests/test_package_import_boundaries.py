"""Phase 0 import guards aligned with docs/pipeline/PACKAGE_DOMAINS.md."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "src" / "origenlab_email_pipeline"
TATIANA_PKG = PKG_ROOT / "tatiana_copilot"

# Non-Streamlit production modules must not import streamlit_* (Streamlit retirement S1+).
_NON_STREAMLIT_PRODUCTION_PATHS = [
    PKG_ROOT / "classification_postgres_mirror.py",
    PKG_ROOT / "tatiana_copilot" / "borrador_support.py",
]

# Tatiana must not own eligibility or archive batch integration; avoid coupling to parent streamlit_* modules.
_FORBIDDEN_FROM = re.compile(
    r"^\s*from\s+origenlab_email_pipeline\.("
    r"candidate_export_gate|marketing_export_context|"
    r"archive_send_batch_builder|archive_outreach_queue|archive_shortlist_commercial_precheck|"
    r"streamlit_\w+"
    r")\b"
)
_FORBIDDEN_IMPORT = re.compile(
    r"^\s*import\s+origenlab_email_pipeline\.("
    r"candidate_export_gate|marketing_export_context|"
    r"archive_send_batch_builder|archive_outreach_queue|archive_shortlist_commercial_precheck|"
    r"streamlit_\w+"
    r")\b"
)


def _violations_in_file(path: Path) -> list[str]:
    bad: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith("#"):
            continue
        if _FORBIDDEN_FROM.search(line) or _FORBIDDEN_IMPORT.search(line):
            bad.append(f"{path.relative_to(REPO_ROOT)}:{i}:{line.strip()}")
    return bad


@pytest.mark.parametrize(
    "path",
    sorted(p for p in TATIANA_PKG.rglob("*.py") if p.is_file()),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_tatiana_copilot_import_boundaries(path: Path) -> None:
    bad = _violations_in_file(path)
    assert not bad, (
        "tatiana_copilot must not import gate/archive/parent streamlit modules "
        f"(see docs/pipeline/PACKAGE_DOMAINS.md). Violations:\n" + "\n".join(bad)
    )


@pytest.mark.parametrize(
    "path",
    _NON_STREAMLIT_PRODUCTION_PATHS,
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_non_streamlit_production_modules_do_not_import_streamlit(path: Path) -> None:
    bad = _violations_in_file(path)
    assert not bad, (
        f"{path.relative_to(PKG_ROOT)} must not import streamlit_* modules; use "
        f"canonical_operational_sql or other neutral read modules. "
        "Violations:\n" + "\n".join(bad)
    )
