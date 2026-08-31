"""CRM-Q1 deployment-packaging audit: does the production image install the
optional Drive credential dependency (`uv sync --extra drive`)?

Finding (CRM-Q1A, 2026-08-31): it did not -- apps/api/Dockerfile ran
`uv sync --frozen --no-dev` only, so google-auth never reached the
production runtime tree.

Decision (CRM-Q1B, 2026-08-31): the operator decided the production image
should be Drive-capable, since contacto@origenlab.cl activation is the near
-term plan. The Dockerfile now runs `uv sync --frozen --no-dev --extra
drive`. The clear drive_dependency_missing fail-closed path (see
test_drive_factory_and_quote_settings.py) is preserved for any environment
that still runs without the extra (e.g. a stale image, a non-Docker
deployment); this test now pins the opposite fact so a future edit that
silently drops the Drive extra from the production image is a deliberate,
reviewed change rather than an accident.
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


def test_production_dockerfile_installs_drive_extra() -> None:
    sync_lines = _dockerfile_uv_sync_lines()

    assert sync_lines, "expected at least one uv sync line in apps/api/Dockerfile"

    assert any("--extra drive" in line for line in sync_lines), (
        "apps/api/Dockerfile no longer installs the Drive extra in the "
        "production image -- if this is intentional (reverting the "
        "CRM-Q1B decision), update the operator docs and this audit note."
    )


def test_production_dockerfile_uses_frozen_no_dev_sync() -> None:
    # The Render-style build must stay exactly the no-dev runtime install
    # scripts/validate.sh smoke-tests against.
    sync_lines = _dockerfile_uv_sync_lines()

    assert any(
        "--frozen" in line and "--no-dev" in line for line in sync_lines
    )


def _validate_sh_sync_lines() -> list[str]:
    validate_sh = (_API_ROOT / "scripts" / "validate.sh").read_text(encoding="utf-8")
    return [
        line.strip()
        for line in validate_sh.splitlines()
        if "uv sync" in line
    ]


def test_validate_sh_render_smoke_matches_dockerfile_install_shape() -> None:
    # validate.sh's initial "Render-style runtime install" block exists to
    # prove the exact command the production image runs; it must not be
    # allowed to silently drift from the Dockerfile it claims to mirror.
    dockerfile_lines = _dockerfile_uv_sync_lines()
    validate_lines = _validate_sh_sync_lines()

    assert any(
        "--frozen" in line and "--no-dev" in line and "--extra drive" in line
        for line in dockerfile_lines
    )
    assert any(
        "--frozen" in line and "--no-dev" in line and "--extra drive" in line
        for line in validate_lines
    ), (
        "scripts/validate.sh's Render-style smoke no longer matches the "
        "Dockerfile's install command -- update it to keep proving the "
        "actual production dependency contract."
    )
