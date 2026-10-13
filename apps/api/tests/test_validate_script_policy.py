"""Local validate script matches CI and README."""

from __future__ import annotations

from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
_VALIDATE_SCRIPT = _API_ROOT / "scripts" / "validate.sh"
_README = _API_ROOT / "README.md"


def test_validate_script_exists_and_matches_ci_shape() -> None:
    assert _VALIDATE_SCRIPT.is_file()
    text = _VALIDATE_SCRIPT.read_text(encoding="utf-8")
    assert "uv sync --group dev --frozen" in text
    assert "../../scripts/run_pytest.sh api" in text
    assert "ORIGENLAB_API_BACKEND=sqlite" in text
    assert "ORIGENLAB_POSTGRES_URL=" in text
    assert "ALEMBIC_DATABASE_URL=" in text


def test_readme_documents_validate_script() -> None:
    readme = _README.read_text(encoding="utf-8")
    assert "./scripts/validate.sh" in readme
