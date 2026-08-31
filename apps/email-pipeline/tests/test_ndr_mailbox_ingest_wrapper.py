"""Tests for archived/trash Gmail catch-up used before NDR review."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.cli import (
    CLI_COMMAND_NAMES,
    SUBCOMMAND_SCRIPTS,
    build_subcommand_argv,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "ingest_ndr_mailboxes.py"

_spec = importlib.util.spec_from_file_location("ingest_ndr_mailboxes_test_module", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def test_command_is_registered() -> None:
    assert "gmail-ingest-ndr" in CLI_COMMAND_NAMES
    assert (
        SUBCOMMAND_SCRIPTS["gmail-ingest-ndr"]
        == "scripts/qa/ingest_ndr_mailboxes.py"
    )


def test_generic_cli_preserves_since_days_passthrough() -> None:
    argv = build_subcommand_argv(
        "gmail-ingest-ndr",
        ["--", "--since-days", "1"],
    )

    assert argv[0] == sys.executable
    assert argv[1].endswith("scripts/qa/ingest_ndr_mailboxes.py")
    assert argv[-2:] == ["--since-days", "1"]


def test_build_commands_targets_all_mail_then_trash() -> None:
    commands = _module.build_commands(1)

    assert len(commands) == 2

    folders = [
        cmd[cmd.index("--folder") + 1]
        for cmd in commands
    ]

    assert folders == [
        "[Gmail]/Todos",
        "[Gmail]/Papelera",
    ]

    for cmd in commands:
        assert "--skip-duplicate-message-id" in cmd
        assert cmd[cmd.index("--since-days") + 1] == "1"
        assert cmd[1].endswith("05_workspace_gmail_imap_to_sqlite.py")


def test_build_commands_rejects_negative_since_days() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        _module.build_commands(-1)


def test_runner_stops_on_first_folder_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class Result:
            returncode = 7 if len(calls) == 1 else 0

        return Result()

    monkeypatch.setattr(_module.subprocess, "run", fake_run)

    assert _module.main(["--since-days", "1"]) == 7
    assert len(calls) == 1
    assert calls[0][calls[0].index("--folder") + 1] == "[Gmail]/Todos"


def test_runner_runs_all_mail_then_trash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(_module.subprocess, "run", fake_run)

    assert _module.main(["--since-days", "1"]) == 0
    assert len(calls) == 2

    assert calls[0][calls[0].index("--folder") + 1] == "[Gmail]/Todos"
    assert calls[1][calls[1].index("--folder") + 1] == "[Gmail]/Papelera"
