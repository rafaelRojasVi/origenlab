"""Focused P2A activation and production dependency-wiring contracts."""

from __future__ import annotations

import builtins
import importlib
import json
import runpy
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
    planner as institution_planner,
)


AS_OF_UTC = "2026-08-12T16:15:00Z"
SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts/commercial/build_commercial_procurement_institution_prospect_plan.py"
)


def _cli_base_args(tmp_path: Path) -> list[str]:
    return [
        "--sqlite-path",
        str(tmp_path / "emails.sqlite"),
        "--detail-cache-dir",
        str(tmp_path / "detail-cache"),
        "--as-of-utc",
        AS_OF_UTC,
        "--out-dir",
        str(tmp_path / "out"),
    ]


def _planner_required_args(tmp_path: Path) -> dict[str, Any]:
    return {
        "sqlite_path": tmp_path / "emails.sqlite",
        "as_of_utc": AS_OF_UTC,
        "out_dir": tmp_path / "out",
        "run_context": "local_fixture",
        "freshness_threshold_hours": 48,
    }


@pytest.mark.parametrize(
    ("extra_args", "expected_error"),
    [
        (
            ["--enable-annex-opportunity-evidence"],
            "--enable-annex-opportunity-evidence requires --annex-evidence-dir",
        ),
        (
            ["--annex-evidence-dir", "evidence"],
            "--annex-evidence-dir requires --enable-annex-opportunity-evidence",
        ),
    ],
)
def test_cli_requires_explicit_enable_and_evidence_path_together(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
    expected_error: str,
) -> None:
    namespace = runpy.run_path(str(SCRIPT_PATH))

    with pytest.raises(SystemExit) as exc_info:
        namespace["main"](_cli_base_args(tmp_path) + extra_args)

    assert exc_info.value.code == 2
    assert expected_error in capsys.readouterr().err


def test_cli_forwards_explicit_annex_activation_to_canonical_planner(
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(str(SCRIPT_PATH))
    main = namespace["main"]
    captured: dict[str, Any] = {}
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps({"ok": True}), encoding="utf-8")

    def fake_build(**kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"summary.json": str(summary_path)}

    main.__globals__["build_institution_prospect_plan"] = fake_build
    evidence_dir = tmp_path / "verified-evidence"

    assert (
        main(
            _cli_base_args(tmp_path)
            + [
                "--enable-annex-opportunity-evidence",
                "--annex-evidence-dir",
                str(evidence_dir),
            ]
        )
        == 0
    )
    assert captured["enable_annex_opportunity_evidence"] is True
    assert captured["annex_evidence_dir"] == evidence_dir


@pytest.mark.parametrize(
    ("enabled", "evidence_path", "expected_error"),
    [
        (
            True,
            None,
            "enable_annex_opportunity_evidence requires annex_evidence_dir",
        ),
        (
            False,
            "evidence",
            "annex_evidence_dir requires enable_annex_opportunity_evidence",
        ),
    ],
)
def test_planner_rejects_activation_path_mismatches(
    tmp_path: Path,
    enabled: bool,
    evidence_path: str | None,
    expected_error: str,
) -> None:
    evidence_dir = tmp_path / evidence_path if evidence_path is not None else None

    with pytest.raises(ValueError, match=expected_error):
        institution_planner.build_institution_prospect_plan(
            **_planner_required_args(tmp_path),
            enable_annex_opportunity_evidence=enabled,
            annex_evidence_dir=evidence_dir,
        )


def test_enabled_planner_rejects_injected_stale_pr5e(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    with pytest.raises(
        ValueError,
        match="cannot consume an injected baseline PR5E plan",
    ):
        institution_planner.build_institution_prospect_plan(
            **_planner_required_args(tmp_path),
            enable_annex_opportunity_evidence=True,
            annex_evidence_dir=evidence_dir,
            pr5e_plan=object(),
        )


def _fake_atomic_writer(
    tmp_path: Path,
) -> Callable[..., dict[str, str]]:
    def fake_write_atomically(
        _out_dir: Path,
        *,
        repo_email_pipeline_root: Path,
        writer: Callable[[Path], dict[str, str]],
    ) -> dict[str, str]:
        assert repo_email_pipeline_root.name == "email-pipeline"
        return writer(tmp_path / "staging")

    return fake_write_atomically


def test_disabled_mode_never_imports_or_calls_annex_loader_and_preserves_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_module = importlib.import_module(
        "origenlab_email_pipeline.commercial_procurement_anexo_integration.bundle"
    )

    def forbidden_loader(_path: Path) -> object:
        pytest.fail("disabled mode called the strict annex loader")

    monkeypatch.setattr(bundle_module, "load_verified_anexo_evidence", forbidden_loader)

    pr5c = object()
    pr5d = object()
    pr5e = object()
    result = object()
    standard_paths = {"summary.json": "/stable/summary.json"}
    build_calls: list[dict[str, Any]] = []

    def forbidden_contact_builder(**_kwargs: Any) -> object:
        pytest.fail("disabled mode rebuilt an injected PR5E plan")

    def fake_build_from_plans(**kwargs: Any) -> object:
        build_calls.append(kwargs)
        return result

    def fake_write_bundle(
        _dest: Path,
        actual_result: object,
        *,
        adapter_records: list[dict[str, Any]],
    ) -> dict[str, str]:
        assert actual_result is result
        assert adapter_records == []
        return standard_paths

    monkeypatch.setattr(
        institution_planner,
        "write_atomically",
        _fake_atomic_writer(tmp_path),
    )
    monkeypatch.setattr(
        institution_planner,
        "build_contact_resolution_plan",
        forbidden_contact_builder,
    )
    monkeypatch.setattr(
        institution_planner,
        "build_institution_prospects_from_plans",
        fake_build_from_plans,
    )
    monkeypatch.setattr(
        institution_planner,
        "classify_acquisition_snapshots",
        lambda _paths, *, as_of_utc: {"as_of_utc": as_of_utc},
    )
    monkeypatch.setattr(institution_planner, "_write_bundle", fake_write_bundle)

    real_import = builtins.__import__
    integration_imports: list[str] = []

    def tracking_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name.startswith(
            "origenlab_email_pipeline.commercial_procurement_anexo_integration"
        ):
            integration_imports.append(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", tracking_import)

    written = institution_planner.build_institution_prospect_plan(
        **_planner_required_args(tmp_path),
        pr5c_plan=pr5c,
        pr5d_plan=pr5d,
        pr5e_plan=pr5e,
    )

    assert written is standard_paths
    assert integration_imports == []
    assert set(written) == {"summary.json"}
    assert len(build_calls) == 1
    assert build_calls[0]["pr5c"] is pr5c
    assert build_calls[0]["pr5d"] is pr5d
    assert build_calls[0]["pr5e"] is pr5e


def _install_enabled_orchestration_fakes(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled_dependency_digest: str,
) -> dict[str, Any]:
    augment_module = importlib.import_module(
        "origenlab_email_pipeline.commercial_procurement_anexo_integration.augment"
    )
    bundle_module = importlib.import_module(
        "origenlab_email_pipeline.commercial_procurement_anexo_integration.bundle"
    )
    audit_module = importlib.import_module(
        "origenlab_email_pipeline.commercial_procurement_anexo_integration.audit"
    )

    pr5c = object()
    baseline_pr5d = SimpleNamespace(
        fingerprints={"semantic_digest": "baseline-pr5d-digest"}
    )
    augmented_pr5d = SimpleNamespace(
        fingerprints={"semantic_digest": "augmented-pr5d-digest"}
    )
    baseline_pr5e = SimpleNamespace(
        dependency_fingerprints={"pr5d_semantic_digest": "baseline-pr5d-digest"}
    )
    enabled_pr5e = SimpleNamespace(
        dependency_fingerprints={
            "pr5d_semantic_digest": enabled_dependency_digest,
        }
    )
    baseline_result = object()
    enabled_result = object()
    verified = object()
    augmentation = SimpleNamespace(
        plan=augmented_pr5d,
        baseline_plan=baseline_pr5d,
    )
    calls: dict[str, Any] = {
        "contact_pr5d": [],
        "institution_graph": [],
        "audit": [],
        "sidecars": [],
    }

    def fake_loader(path: Path) -> object:
        assert path == (tmp_path / "evidence").resolve()
        return verified

    def fake_augment(**kwargs: Any) -> object:
        assert kwargs == {
            "pr5c": pr5c,
            "baseline": baseline_pr5d,
            "verified": verified,
        }
        return augmentation

    def fake_contact_builder(**kwargs: Any) -> object:
        dependency = kwargs["pr5d_plan"]
        calls["contact_pr5d"].append(dependency)
        if dependency is baseline_pr5d:
            return baseline_pr5e
        assert dependency is augmented_pr5d
        return enabled_pr5e

    def fake_build_from_plans(**kwargs: Any) -> object:
        calls["institution_graph"].append((kwargs["pr5d"], kwargs["pr5e"]))
        if kwargs["pr5d"] is baseline_pr5d:
            assert kwargs["pr5e"] is baseline_pr5e
            return baseline_result
        assert kwargs["pr5d"] is augmented_pr5d
        assert kwargs["pr5e"] is enabled_pr5e
        return enabled_result

    def fake_write_bundle(
        _dest: Path,
        actual_result: object,
        *,
        adapter_records: list[dict[str, Any]],
    ) -> dict[str, str]:
        assert actual_result is enabled_result
        assert adapter_records == []
        return {"summary.json": "/enabled/summary.json"}

    def fake_build_audit(
        **kwargs: Any,
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        calls["audit"].append(kwargs)
        assert kwargs == {
            "augmentation": augmentation,
            "baseline": baseline_result,
            "enabled": enabled_result,
        }
        return {"integration_mode": "enabled_local_verified"}, ()

    def fake_write_sidecars(
        _dest: Path,
        *,
        reconciliation: dict[str, Any],
        provenance_rows: tuple[dict[str, Any], ...],
    ) -> dict[str, str]:
        calls["sidecars"].append((reconciliation, provenance_rows))
        return {
            "annex_integration_reconciliation.json": "/enabled/reconciliation.json",
            "annex_opportunity_provenance.jsonl": "/enabled/provenance.jsonl",
        }

    monkeypatch.setattr(bundle_module, "load_verified_anexo_evidence", fake_loader)
    monkeypatch.setattr(augment_module, "augment_product_relevance_plan", fake_augment)
    monkeypatch.setattr(audit_module, "build_annex_integration_audit", fake_build_audit)
    monkeypatch.setattr(
        audit_module,
        "write_annex_integration_sidecars",
        fake_write_sidecars,
    )
    monkeypatch.setattr(
        institution_planner,
        "write_atomically",
        _fake_atomic_writer(tmp_path),
    )
    monkeypatch.setattr(
        institution_planner,
        "build_contact_resolution_plan",
        fake_contact_builder,
    )
    monkeypatch.setattr(
        institution_planner,
        "build_institution_prospects_from_plans",
        fake_build_from_plans,
    )
    monkeypatch.setattr(
        institution_planner,
        "classify_acquisition_snapshots",
        lambda _paths, *, as_of_utc: {"as_of_utc": as_of_utc},
    )
    monkeypatch.setattr(institution_planner, "_write_bundle", fake_write_bundle)

    return {
        "pr5c": pr5c,
        "baseline_pr5d": baseline_pr5d,
        "augmented_pr5d": augmented_pr5d,
        "baseline_pr5e": baseline_pr5e,
        "enabled_pr5e": enabled_pr5e,
        "calls": calls,
    }


def test_enabled_mode_rebuilds_pr5e_from_augmented_pr5d_and_writes_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    harness = _install_enabled_orchestration_fakes(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enabled_dependency_digest="augmented-pr5d-digest",
    )

    written = institution_planner.build_institution_prospect_plan(
        **_planner_required_args(tmp_path),
        pr5c_plan=harness["pr5c"],
        pr5d_plan=harness["baseline_pr5d"],
        enable_annex_opportunity_evidence=True,
        annex_evidence_dir=evidence_dir,
    )

    assert harness["calls"]["contact_pr5d"] == [
        harness["baseline_pr5d"],
        harness["augmented_pr5d"],
    ]
    assert harness["calls"]["institution_graph"] == [
        (harness["baseline_pr5d"], harness["baseline_pr5e"]),
        (harness["augmented_pr5d"], harness["enabled_pr5e"]),
    ]
    assert len(harness["calls"]["audit"]) == 1
    assert len(harness["calls"]["sidecars"]) == 1
    assert set(written) == {
        "summary.json",
        "annex_integration_reconciliation.json",
        "annex_opportunity_provenance.jsonl",
    }


def test_enabled_mode_rejects_pr5e_dependency_not_bound_to_augmented_pr5d(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    harness = _install_enabled_orchestration_fakes(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enabled_dependency_digest="stale-pr5d-digest",
    )

    with pytest.raises(
        ValueError,
        match="PR5E dependency does not bind to augmented annex PR5D",
    ):
        institution_planner.build_institution_prospect_plan(
            **_planner_required_args(tmp_path),
            pr5c_plan=harness["pr5c"],
            pr5d_plan=harness["baseline_pr5d"],
            enable_annex_opportunity_evidence=True,
            annex_evidence_dir=evidence_dir,
        )

    assert harness["calls"]["contact_pr5d"] == [
        harness["baseline_pr5d"],
        harness["augmented_pr5d"],
    ]
    assert harness["calls"]["audit"] == []
    assert harness["calls"]["sidecars"] == []
