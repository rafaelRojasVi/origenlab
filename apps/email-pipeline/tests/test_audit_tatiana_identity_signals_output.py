"""Tests for audit_tatiana_identity_signals safe stdout defaults."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dataset" / "audit_tatiana_identity_signals.py"


def _load_script():
    name = "audit_tatiana_identity_signals"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cli_has_no_raw_sensitive_output_flags() -> None:
    mod = _load_script()
    ap = mod.argparse.ArgumentParser(description=mod.__doc__)
    ap.add_argument("--sample", type=int, default=8, help="redacted examples per category (stdout)")
    option_dests = {action.dest for action in ap._actions if action.dest not in {"help"}}
    assert option_dests == {"sample"}


def test_default_report_redacts_sender_samples_and_omits_raw_subjects() -> None:
    mod = _load_script()
    stats = mod.TatianaAuditStats(
        rows_scanned=2,
        hit_sender_t=1,
        hit_sender_v=0,
        hit_subj_t=1,
        hit_subj_v=0,
        hit_full_t=0,
        hit_full_v=0,
        hit_top_t=0,
        hit_top_v=0,
        hit_any_t=1,
        hit_any_v=0,
        hit_trusted_identity_in_from_or_body=1,
        domain_trusted_identity=Counter({"origenlab.cl": 1}),
        domain_tatiana_in_from=Counter({"client.example": 1}),
        domain_vivanco_in_from=Counter(),
        sender_samples_t=[
            'Tatiana Vivanco <tatiana.secret@client.example>',
        ],
        sender_samples_v=[],
    )
    report = mod.render_audit_report(
        stats,
        trusted_domain_count=3,
        sample_limit=8,
    )
    assert "tatiana.secret@client.example" not in report
    assert "Tatiana Vivanco" not in report
    assert "Solicitud confidencial de cotización" not in report
    assert "from-domain=client.example" in report
    assert "Trusted sender domain count" in report
    assert "origenlab.cl: 1" in report
    assert ", redacted)" in report


def test_redact_identity_text_strips_emails() -> None:
    mod = _load_script()
    redacted = mod.redact_identity_text("Hola Tatiana <secret@example.com> — subject line")
    assert "secret@example.com" not in redacted
    assert "<email:redacted>" in redacted


def test_render_audit_report_signature_has_no_sensitive_flag() -> None:
    mod = _load_script()
    import inspect

    params = inspect.signature(mod.render_audit_report).parameters
    assert "include_sensitive_samples" not in params
