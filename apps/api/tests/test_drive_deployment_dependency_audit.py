"""CRM-Q1 deployment-packaging audit: does the production image install the
optional Drive credential dependency (`uv sync --extra drive`)?

Finding: it does not. apps/api/Dockerfile runs `uv sync --frozen --no-dev`
only -- the production runtime dependency tree never gets google-auth. This
test pins that fact so a future edit that silently starts (or stops)
installing the Drive extra in the production image is a deliberate,
reviewed change rather than an accident -- because the factory's behavior
when Drive is configured but the extra is absent (drive_dependency_missing,
see test_drive_factory_and_quote_settings.py) depends on this being true.
"""

from __future__ import annotations

from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]


def _dockerfile_uv_sync_lines() -> list[str]:
    dockerfile = (_API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    return [
        line.strip()
        for line in dockerfile.splitlines()
        if "uv sync" in line
    ]


def test_production_dockerfile_does_not_install_drive_extra() -> None:
    sync_lines = _dockerfile_uv_sync_lines()

    assert sync_lines, "expected at least one uv sync line in apps/api/Dockerfile"

    for line in sync_lines:
        assert "--extra drive" not in line, (
            "apps/api/Dockerfile now installs the Drive extra in the "
            "production image -- if this is intentional, update the "
            "operator docs and this audit note; if not, remove it (the "
            "factory's drive_dependency_missing fail-closed path depends "
            "on this extra staying opt-in)."
        )


def test_production_dockerfile_uses_frozen_no_dev_sync() -> None:
    # The Render-style build must stay exactly the no-dev runtime install
    # scripts/validate.sh smoke-tests against.
    sync_lines = _dockerfile_uv_sync_lines()

    assert any(
        "--frozen" in line and "--no-dev" in line for line in sync_lines
    )
