"""Tests for the outbound campaign operator CLI. No real Gmail sends anywhere."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "campaigns" / "outbound_campaign_cli.py"
_spec = importlib.util.spec_from_file_location("outbound_campaign_cli", _CLI_PATH)
outbound_campaign_cli = importlib.util.module_from_spec(_spec)
sys.modules["outbound_campaign_cli"] = outbound_campaign_cli
_spec.loader.exec_module(outbound_campaign_cli)  # type: ignore[union-attr]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "t.sqlite"


def _run(db_path: Path, *args: str, capsys) -> tuple[int, str]:
    code = outbound_campaign_cli.main([*args, "--db", str(db_path)])
    out = capsys.readouterr().out
    return code, out


def test_init_creates_hielscher_campaign(db_path: Path, capsys) -> None:
    code, out = _run(
        db_path, "init",
        "--campaign-id", "hielscher-sonicators-2026",
        "--name", "Hielscher Sonicadores Laboratorio",
        "--sender-email", "contacto@origenlab.cl",
        "--sender-name", "Tatiana Vivanco | OrigenLab",
        "--subject", "Sonicadores Hielscher para laboratorio | OrigenLab",
        "--target", "2000", "--baseline", "874",
        capsys=capsys,
    )
    assert code == 0
    code2, status_out = _run(db_path, "status", "--campaign-id", "hielscher-sonicators-2026", capsys=capsys)
    assert code2 == 0
    payload = json.loads(status_out)
    assert payload["target"] == 2000
    assert payload["baseline"] == 874
    assert payload["remaining"] == 1126


def test_duplicate_init_fails_cleanly(db_path: Path, capsys) -> None:
    args = (
        "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "s@x.cl", "--sender-name", "S", "--subject", "Subj",
        "--target", "2000", "--baseline", "874",
    )
    code1, _ = _run(db_path, *args, capsys=capsys)
    assert code1 == 0
    code2, _ = _run(db_path, *args, capsys=capsys)
    assert code2 == 1


def test_contact_status_set_and_show_roundtrip(db_path: Path, capsys) -> None:
    _run(db_path, "contact-status", "set", "--email", "carolinalobo@pharmaisa.cl", "--status", "inactive",
         "--reason", "No longer works at Pharma Isa", "--effective-at", "2026-04-06", capsys=capsys)
    code, out = _run(db_path, "contact-status", "show", "--email", "carolinalobo@pharmaisa.cl", capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["status"] == "inactive"


def test_candidates_add_and_select_blocks_carolina(db_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(db_path, "contact-status", "set", "--email", "carolinalobo@pharmaisa.cl", "--status", "inactive", capsys=capsys)
    _run(
        db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026",
        "--email", "carolinalobo@pharmaisa.cl", "--email", "good@x.cl", capsys=capsys,
    )
    code, out = _run(
        db_path, "select", "--campaign-id", "hielscher-sonicators-2026", "--n", "10",
        "--gmail-user", "contacto@origenlab.cl", capsys=capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["reserved"] == 1
    assert payload["blocked"] == 1

    code2, batch_out = _run(db_path, "batch", "show", "--campaign-id", "hielscher-sonicators-2026", capsys=capsys)
    batch = json.loads(batch_out)
    assert [r["email_norm"] for r in batch] == ["good@x.cl"]


def test_send_dry_run_end_to_end(db_path: Path, tmp_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    _run(db_path, "candidates", "add", "--campaign-id", "hielscher-sonicators-2026", "--email", "good@x.cl", capsys=capsys)
    _run(db_path, "select", "--campaign-id", "hielscher-sonicators-2026", "--n", "10", "--gmail-user", "contacto@origenlab.cl", capsys=capsys)

    html_file = tmp_path / "campaign.html"
    html_file.write_text("<html><body>Hola</body></html>", encoding="utf-8")

    with patch("outbound_campaign_cli.send_campaign_batch") as mock_send:
        from origenlab_email_pipeline.outbound_campaign_sender import SendOutcome
        mock_send.return_value = [SendOutcome(recipient_id=1, email="good@x.cl", mode="dry_run", result="accepted", gmail_message_id=None, error=None)]
        code, out = _run(db_path, "send", "--campaign-id", "hielscher-sonicators-2026", "--html", str(html_file), capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["mode"] == "dry_run"
    assert payload["accepted"] == 1
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["live"] is False


def test_send_refuses_downloads_out_path(db_path: Path, tmp_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    html_file = tmp_path / "campaign.html"
    html_file.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(SystemExit):
        outbound_campaign_cli.main([
            "send", "--campaign-id", "hielscher-sonicators-2026", "--html", str(html_file),
            "--out", "/mnt/c/Users/Rafael/Downloads/batch.csv", "--db", str(db_path),
        ])


def test_reconcile_runs_against_empty_campaign(db_path: Path, capsys) -> None:
    _run(
        db_path, "init", "--campaign-id", "hielscher-sonicators-2026", "--name", "N",
        "--sender-email", "contacto@origenlab.cl", "--sender-name", "S",
        "--subject", "Subj", "--target", "2000", "--baseline", "874", capsys=capsys,
    )
    code, out = _run(db_path, "reconcile", "--campaign-id", "hielscher-sonicators-2026", "--gmail-user", "contacto@origenlab.cl", capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["checked"] == 0


def test_default_db_path_never_resolves_under_downloads(monkeypatch, tmp_path: Path) -> None:
    """Sanity check on the config default — resolved_sqlite_path must never live under Downloads."""
    from origenlab_email_pipeline.config import load_settings

    monkeypatch.delenv("ORIGENLAB_SQLITE_PATH", raising=False)
    monkeypatch.setenv("ORIGENLAB_DATA_ROOT", str(tmp_path / "data"))
    resolved = load_settings().resolved_sqlite_path()
    assert "Downloads" not in str(resolved)
