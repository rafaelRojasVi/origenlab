"""Regression tests: Tatiana identity audit never prints sender-derived strings."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import logging
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dataset" / "audit_tatiana_identity_signals.py"

# Synthetic fixtures only — never production mailbox data.
_SENSITIVE_EMAIL = "tatiana.secret.fixture@client.example"
_SENSITIVE_DISPLAY = "Tatiana Vivanco Fixture"
_SENSITIVE_SENDER = f"{_SENSITIVE_DISPLAY} <{_SENSITIVE_EMAIL}>"
_SENSITIVE_SUBJECT = "Solicitud confidencial de cotización fixture-XYZ"
_SENSITIVE_BODY = (
    f"Hola Tatiana, confírmame a {_SENSITIVE_EMAIL} el presupuesto. "
    "Token=sk_live_fixture_do_not_log."
)
_DOMAIN_FIXTURES = (
    "client.example",
    "origenlab.cl",
)
_SENSITIVE_MARKERS = (
    _SENSITIVE_EMAIL,
    _SENSITIVE_DISPLAY,
    "sk_live_fixture_do_not_log",
    "cotización fixture-XYZ",
    *_DOMAIN_FIXTURES,
    hashlib.sha256(_SENSITIVE_SENDER.encode("utf-8")).hexdigest()[:10],
    hashlib.sha256(_SENSITIVE_EMAIL.encode("utf-8")).hexdigest()[:10],
)
_FORBIDDEN_LABELS = (
    "Sender domains",
    "parsed From-address domain",
    "from-domain=",
    "Sample From headers",
)


def _load_script():
    name = "audit_tatiana_identity_signals"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _assert_no_sensitive(text: str) -> None:
    lower = text.lower()
    for marker in _SENSITIVE_MARKERS:
        assert marker not in text, f"sensitive marker leaked: {marker!r}"
        assert marker.lower() not in lower, f"sensitive marker leaked (casefold): {marker!r}"
    for label in _FORBIDDEN_LABELS:
        assert label not in text, f"forbidden label leaked: {label!r}"
    assert "@client.example" not in text


def test_cli_has_no_raw_sensitive_output_flags() -> None:
    mod = _load_script()
    ap = mod.argparse.ArgumentParser(description=mod.__doc__)
    ap.add_argument(
        "--sample",
        type=int,
        default=8,
        help="ignored (identity samples are never printed; kept for CLI compatibility)",
    )
    option_dests = {action.dest for action in ap._actions if action.dest not in {"help"}}
    assert option_dests == {"sample"}
    assert "include_sensitive" not in (mod.__doc__ or "").lower()


def test_render_audit_report_exposes_numeric_aggregates_only() -> None:
    mod = _load_script()
    stats = mod.TatianaAuditStats(
        rows_scanned=2,
        hit_sender_t=1,
        hit_sender_v=0,
        hit_subj_t=1,
        hit_subj_v=0,
        hit_full_t=1,
        hit_full_v=0,
        hit_top_t=0,
        hit_top_v=0,
        hit_any_t=1,
        hit_any_v=0,
        hit_trusted_identity_in_from_or_body=1,
    )
    report = mod.render_audit_report(stats, trusted_domain_count=3)
    _assert_no_sensitive(report)
    assert "Rows scanned: 2" in report
    assert "Trusted sender domain count (internal ∪ voice): 3" in report
    assert "From header contains 'Tatiana': 1" in report
    assert "Subject contains 'Tatiana': 1" in report
    assert "full_body_clean contains 'Tatiana': 1" in report
    assert "Any field above — Tatiana: 1" in report
    assert "cohort-style signal): 1" in report
    assert "Privacy:" in report
    assert "numeric aggregates and static labels only" in report
    assert "id=" not in report


def test_render_audit_report_signature_has_no_sensitive_or_sample_params() -> None:
    mod = _load_script()
    import inspect

    params = inspect.signature(mod.render_audit_report).parameters
    assert "include_sensitive_samples" not in params
    assert "sample_limit" not in params
    assert set(params) == {"stats", "trusted_domain_count"}


def test_stats_dataclass_has_no_domain_or_sample_fields() -> None:
    mod = _load_script()
    fields = {f.name for f in mod.TatianaAuditStats.__dataclass_fields__.values()}
    assert "sender_samples_t" not in fields
    assert "sender_samples_v" not in fields
    assert not any(name.startswith("domain_") for name in fields)


def test_scan_and_report_omit_sensitive_fixtures_from_stdout_stderr_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    mod = _load_script()
    db_path = tmp_path / "fixture_emails.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            sender TEXT,
            subject TEXT,
            full_body_clean TEXT,
            top_reply_clean TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO emails (sender, subject, full_body_clean, top_reply_clean) VALUES (?, ?, ?, ?)",
        (_SENSITIVE_SENDER, _SENSITIVE_SUBJECT, _SENSITIVE_BODY, _SENSITIVE_BODY),
    )
    conn.commit()

    caplog.set_level(logging.DEBUG)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        stats = mod.scan_tatiana_identity_signals(conn)
        report = mod.render_audit_report(stats, trusted_domain_count=0)
        print(report)
    conn.close()

    out = stdout.getvalue()
    err = stderr.getvalue()
    log_blob = "\n".join(r.getMessage() for r in caplog.records)

    assert stats.rows_scanned == 1
    assert stats.hit_sender_t >= 1
    assert stats.hit_any_t >= 1
    assert "client.example" not in out
    assert "origenlab.cl" not in out
    _assert_no_sensitive(out)
    _assert_no_sensitive(err)
    _assert_no_sensitive(log_blob)
    assert "Rows scanned: 1" in out
    assert "From header contains 'Tatiana':" in out
    assert "Sample From" not in out
    assert "Sender domains" not in out
    assert "parsed From-address domain" not in out
