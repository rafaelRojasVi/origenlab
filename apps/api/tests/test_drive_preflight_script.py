"""CRM-Q1 tests for the operator-facing Drive preflight CLI script.

The script never makes a live Drive call in this suite: run_drive_preflight
is monkeypatched. It exists so an operator can verify a Drive configuration
(from real settings/credentials) before activating quote creation, without
building a full HTTP surface for it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _load_script(monkeypatch: pytest.MonkeyPatch) -> None:
    # scripts/ isn't a package; import it directly by path.
    import importlib.util
    from pathlib import Path

    script_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "drive_preflight.py"
    )
    spec = importlib.util.spec_from_file_location(
        "drive_preflight_script_under_test", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    globals()["_module"] = module


def test_main_prints_ok_and_returns_zero_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = globals()["_module"]

    monkeypatch.setattr(
        module,
        "run_drive_preflight",
        lambda settings: module.DrivePreflightResult(
            ok=True, step=None, category=None
        ),
    )

    exit_code = module.main()

    assert exit_code == 0
    assert "ok" in capsys.readouterr().out.lower()


def test_main_prints_redacted_category_and_returns_nonzero_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = globals()["_module"]

    monkeypatch.setattr(
        module,
        "run_drive_preflight",
        lambda settings: module.DrivePreflightResult(
            ok=False, step="destination", category="drive_auth_mode_incompatible"
        ),
    )

    exit_code = module.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "destination" in output
    assert "drive_auth_mode_incompatible" in output
    for forbidden in ("token", "credential", "Bearer", "/secure/"):
        assert forbidden not in output


def test_main_prints_principal_and_template_ownership_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = globals()["_module"]

    monkeypatch.setattr(
        module,
        "run_drive_preflight",
        lambda settings: module.DrivePreflightResult(
            ok=True,
            step=None,
            category=None,
            principal_email="contacto@origenlab.cl",
            template_owned_by_expected_principal=False,
        ),
    )

    exit_code = module.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "contacto@origenlab.cl" in output
    # Non-blocking, but the operator must see the template isn't owned by
    # the expected principal yet (it may still be shared from elsewhere).
    assert "template" in output.lower()
    assert "false" in output.lower()
