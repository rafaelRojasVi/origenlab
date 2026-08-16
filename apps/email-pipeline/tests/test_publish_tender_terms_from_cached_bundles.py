"""Tests for scripts/commercial/publish_tender_terms_from_cached_bundles.py.

This is a MANUAL/operator-invoked utility (see the script's own module
docstring). These tests verify:
  - it never performs acquisition or any network call (offline only)
  - --bundles-dir is required (no default that could silently read a stale
    directory)
  - requested --tender-id values absent from the cache fail closed with a
    clear error and no partial/fabricated publication
  - a non-existent bundles directory fails before any publication attempt
  - a populated bundles directory produces a valid publication end to end
"""

from __future__ import annotations

import importlib.util
import pickle
import socket
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO / "scripts/commercial/publish_tender_terms_from_cached_bundles.py"

sys.path.insert(0, str(_REPO / "src"))

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (  # noqa: E402
    TenderAttachmentBundle,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (  # noqa: E402
    production_publish as publication,
)

# Reuse the exact bundle-building helper already validated by the
# production_publish test suite, instead of duplicating a fragile fixture.
import importlib  # noqa: E402

_prod_publish_tests = importlib.import_module(
    "test_commercial_procurement_anexo_tender_terms_production_publish"
)
_bundle = _prod_publish_tests._bundle

_BUDGET_TEXT = (
    "Artículo 5°: Datos de la licitación. "
    "Presupuesto Disponible $ 28.419.400.- CLP impuestos incluidos"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "publish_tender_terms_from_cached_bundles", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_cached_bundle(bundles_dir: Path, tender_id: str) -> None:
    bundles_dir.mkdir(parents=True, exist_ok=True)
    bundle = _bundle(tender_id, _BUDGET_TEXT)
    (bundles_dir / f"{tender_id}.pkl").write_bytes(pickle.dumps(bundle))


def _git_ignored_email_pipeline_root(tmp_path: Path) -> Path:
    """Build a throwaway git repo whose reports/out/ is git-ignored, so
    write_atomically()'s git-ignore safety check passes without touching the
    real repo."""
    email_pipeline_root = tmp_path / "email-pipeline"
    reports_out = email_pipeline_root / "reports" / "out"
    reports_out.mkdir(parents=True)
    (email_pipeline_root / ".gitignore").write_text("reports/out/\n")
    subprocess.run(["git", "init", "-q"], cwd=str(email_pipeline_root), check=True, timeout=10)
    subprocess.run(
        ["git", "add", ".gitignore"], cwd=str(email_pipeline_root), check=True, timeout=10
    )
    return email_pipeline_root


# ---------------------------------------------------------------------------
# --bundles-dir is required; no default
# ---------------------------------------------------------------------------


def test_bundles_dir_is_required_cli_flag() -> None:
    """Running with no --bundles-dir must fail argparse, not silently pick a default."""
    cp = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode != 0
    assert "--bundles-dir" in cp.stderr
    assert "required" in cp.stderr.lower()


def test_help_text_labels_bundles_dir_as_manual_dev_use() -> None:
    cp = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0
    combined = cp.stdout + cp.stderr
    assert "MANUAL" in combined or "manual" in combined
    assert "no default" in combined.lower() or "no clean same-run" in combined.lower() or "required" in combined.lower()


def test_module_docstring_disclaims_production_auto_publication() -> None:
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "MANUAL" in text
    assert "NOT" in text and "PRODUCTION AUTO-PUBLICATION" in text
    assert "auto-refresh-chilecompra-equipment" in text


# ---------------------------------------------------------------------------
# Missing / non-existent bundles directory
# ---------------------------------------------------------------------------


def test_nonexistent_bundles_dir_fails_closed(tmp_path: Path) -> None:
    module = _load_script_module()
    out_dir = tmp_path / "tender_terms"
    rc = module.main(
        [
            "--bundles-dir",
            str(tmp_path / "does_not_exist"),
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 2
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# Requested tender IDs absent from the cache must fail closed
# ---------------------------------------------------------------------------


def test_requested_tender_id_absent_from_cache_fails_closed(tmp_path: Path) -> None:
    module = _load_script_module()
    bundles_dir = tmp_path / "acquired_bundles"
    _write_cached_bundle(bundles_dir, "745712-8-LP26")

    out_dir = tmp_path / "tender_terms"
    with pytest.raises(SystemExit) as exc_info:
        module.main(
            [
                "--bundles-dir",
                str(bundles_dir),
                "--out-dir",
                str(out_dir),
                "--tender-id",
                "999999-9-LP99",
            ]
        )
    assert "999999-9-LP99" in str(exc_info.value)
    assert not out_dir.exists(), "no partial/fabricated publication may be written"


def test_empty_bundles_dir_fails_closed_with_no_bundles(tmp_path: Path) -> None:
    module = _load_script_module()
    bundles_dir = tmp_path / "acquired_bundles"
    bundles_dir.mkdir()
    out_dir = tmp_path / "tender_terms"

    rc = module.main(["--bundles-dir", str(bundles_dir), "--out-dir", str(out_dir)])
    assert rc == 2
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# End-to-end: valid cached bundle publishes successfully, offline
# ---------------------------------------------------------------------------


def test_valid_cached_bundle_publishes_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script_module()
    bundles_dir = tmp_path / "acquired_bundles"
    _write_cached_bundle(bundles_dir, "745712-8-LP26")

    email_pipeline_root = _git_ignored_email_pipeline_root(tmp_path)
    reports_out = email_pipeline_root / "reports" / "out"
    monkeypatch.setattr(publication, "_email_pipeline_root", lambda: email_pipeline_root)

    out_dir = reports_out / "tender_terms"
    rc = module.main(
        [
            "--bundles-dir",
            str(bundles_dir),
            "--out-dir",
            str(out_dir),
            "--as-of-utc",
            "2026-08-15T17:30:14Z",
        ]
    )
    assert rc == 0, "valid bundle publication should succeed"
    loaded = publication.load_published_tender_terms(out_dir)
    assert loaded["summary"]["tender_count"] == 1


def test_restricting_to_present_tender_id_publishes_only_that_tender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script_module()
    bundles_dir = tmp_path / "acquired_bundles"
    _write_cached_bundle(bundles_dir, "745712-8-LP26")
    _write_cached_bundle(bundles_dir, "745712-13-LP26")

    email_pipeline_root = _git_ignored_email_pipeline_root(tmp_path)
    reports_out = email_pipeline_root / "reports" / "out"
    monkeypatch.setattr(publication, "_email_pipeline_root", lambda: email_pipeline_root)

    out_dir = reports_out / "tender_terms"
    rc = module.main(
        [
            "--bundles-dir",
            str(bundles_dir),
            "--out-dir",
            str(out_dir),
            "--tender-id",
            "745712-8-LP26",
            "--as-of-utc",
            "2026-08-15T17:30:14Z",
        ]
    )
    assert rc == 0
    loaded = publication.load_published_tender_terms(out_dir)
    assert loaded["summary"]["tender_count"] == 1
    assert loaded["tender_terms"][0]["tender_id"] == "745712-8-LP26"


# ---------------------------------------------------------------------------
# No acquisition / no network calls, ever
# ---------------------------------------------------------------------------


def test_script_source_has_no_network_capable_imports() -> None:
    """Static check: the script must not import any HTTP/network client."""
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden_imports = (
        "import requests",
        "import httpx",
        "import urllib.request",
        "import socket",
        "from origenlab_email_pipeline.chilecompra_anexo_evidence.acquisition",
        "from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import build_tender_bundle",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in text, f"script must not import {forbidden!r}"


def test_script_never_calls_socket_connect_when_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime check: patch socket.socket.connect to fail loudly if ever invoked."""
    module = _load_script_module()
    bundles_dir = tmp_path / "acquired_bundles"
    _write_cached_bundle(bundles_dir, "745712-8-LP26")

    email_pipeline_root = _git_ignored_email_pipeline_root(tmp_path)
    reports_out = email_pipeline_root / "reports" / "out"
    monkeypatch.setattr(publication, "_email_pipeline_root", lambda: email_pipeline_root)

    def _forbidden_connect(*_args, **_kwargs):
        raise AssertionError("network connect attempted during publication")

    monkeypatch.setattr(socket.socket, "connect", _forbidden_connect)

    out_dir = reports_out / "tender_terms"
    rc = module.main(
        [
            "--bundles-dir",
            str(bundles_dir),
            "--out-dir",
            str(out_dir),
            "--as-of-utc",
            "2026-08-15T17:30:14Z",
        ]
    )
    assert rc == 0
