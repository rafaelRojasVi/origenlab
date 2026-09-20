"""Fail-closed preflight for Gmail Sent history before lead/archive exports.

Read-only probes on ``emails``; does not change ``candidate_export_gate`` policy.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from typing import TextIO

from origenlab_email_pipeline.marketing_export_context import (
    load_sent_recipient_norms,
    sent_history_where,
)

_DISTINCT_FOLDER_SAMPLE_LIMIT = 20

_OPERATOR_ALLOW_EMPTY_SENT_HISTORY = "ORIGENLAB_OPERATOR_ALLOW_EMPTY_SENT_HISTORY"
_LEGACY_STREAMLIT_ALLOW_EMPTY_SENT_HISTORY = "ORIGENLAB_STREAMLIT_ALLOW_EMPTY_SENT_HISTORY"


def _operator_env_flag_enabled(*, new_var: str, legacy_var: str) -> bool:
    if os.environ.get(new_var) is not None:
        return os.environ.get(new_var) == "1"
    return os.environ.get(legacy_var) == "1"


def operator_allow_empty_sent_history_enabled() -> bool:
    """True when env allows bypassing Sent-history fail-closed (legacy env alias still accepted)."""
    return _operator_env_flag_enabled(
        new_var=_OPERATOR_ALLOW_EMPTY_SENT_HISTORY,
        legacy_var=_LEGACY_STREAMLIT_ALLOW_EMPTY_SENT_HISTORY,
    )


def _emails_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        ("emails",),
    ).fetchone()
    return bool(row)


def _distinct_folders_for_gmail_user(conn: sqlite3.Connection, *, gmail_user_lower: str) -> tuple[str, ...]:
    """Sample folder labels stored for this mailbox (any folder), for operator diagnostics."""
    if not _emails_table_exists(conn):
        return ()
    like_pat = f"gmail:{gmail_user_lower}/%"
    cur = conn.execute(
        """
        SELECT DISTINCT folder FROM emails
        WHERE lower(source_file) LIKE ?
          AND folder IS NOT NULL
          AND length(trim(folder)) > 0
        ORDER BY folder
        LIMIT ?
        """,
        (like_pat, _DISTINCT_FOLDER_SAMPLE_LIMIT),
    )
    return tuple(str(r[0]) for r in cur if r[0] is not None)


@dataclass(frozen=True)
class SentHistoryProbeResult:
    """Observed Sent-history state for a Gmail user + folder set (read-only)."""

    gmail_user: str
    sent_folders: tuple[str, ...]
    sent_row_count: int
    parsed_recipient_count: int
    distinct_folders_sample: tuple[str, ...]


@dataclass(frozen=True)
class SentHistoryPreflightOutcome:
    """Whether export may proceed under preflight rules; errors vs warnings."""

    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    override_used: bool
    probe: SentHistoryProbeResult


def probe_sent_history(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
) -> SentHistoryProbeResult:
    """Count Sent rows and parsed recipients; sample distinct folders when Sent rows are missing.

    Uses the same ``source_file`` / ``folder`` predicates as
    :func:`~origenlab_email_pipeline.marketing_export_context.load_sent_recipient_norms`.
    """
    user = (gmail_user or "").strip()
    folders = tuple(f.strip() for f in sent_folders if f and str(f).strip())
    gmail_lower = user.lower()

    if not user or not folders:
        return SentHistoryProbeResult(
            gmail_user=user,
            sent_folders=folders,
            sent_row_count=0,
            parsed_recipient_count=0,
            distinct_folders_sample=(),
        )

    if not _emails_table_exists(conn):
        sample = _distinct_folders_for_gmail_user(conn, gmail_user_lower=gmail_lower)
        return SentHistoryProbeResult(
            gmail_user=user,
            sent_folders=folders,
            sent_row_count=0,
            parsed_recipient_count=0,
            distinct_folders_sample=sample,
        )

    where, params = sent_history_where(gmail_user=user, sent_folders=folders)
    row = conn.execute(f"SELECT COUNT(*) FROM emails WHERE {where}", params).fetchone()
    sent_row_count = int(row[0] or 0) if row else 0

    norms = load_sent_recipient_norms(conn, gmail_user=user, sent_folders=folders)
    parsed_count = len(norms)

    distinct_sample: tuple[str, ...] = ()
    if sent_row_count == 0:
        distinct_sample = _distinct_folders_for_gmail_user(conn, gmail_user_lower=gmail_lower)

    return SentHistoryProbeResult(
        gmail_user=user,
        sent_folders=folders,
        sent_row_count=sent_row_count,
        parsed_recipient_count=parsed_count,
        distinct_folders_sample=distinct_sample,
    )


def evaluate_sent_history_preflight(
    probe: SentHistoryProbeResult,
    *,
    allow_empty: bool,
) -> SentHistoryPreflightOutcome:
    """Fail closed unless ``allow_empty`` covers missing/unparsable Sent evidence."""
    errors: list[str] = []
    warnings: list[str] = []
    override_used = False

    if not (probe.gmail_user or "").strip():
        errors.append("Gmail user for Sent preflight is empty. Set --gmail-user or ORIGENLAB_GMAIL_WORKSPACE_USER.")
    elif not probe.sent_folders:
        errors.append("No Sent folder labels after resolution. Pass --sent-folder with the exact IMAP label(s).")
    elif probe.sent_row_count == 0:
        msg = (
            f"No rows in `emails` for Gmail user {probe.gmail_user!r} and Sent folder(s) "
            f"{list(probe.sent_folders)!r}. Sent-history blocking cannot be verified."
        )
        if allow_empty:
            override_used = True
            warnings.append(
                "Override: exporting without matching Sent mail in SQLite — "
                "already-contacted detection from Sent ingest may be ineffective."
            )
        else:
            errors.append(msg)
    elif probe.parsed_recipient_count == 0:
        msg = (
            f"Found {probe.sent_row_count} Sent row(s) for {probe.gmail_user!r}, but "
            "`recipients` parsed to zero addresses. Check encoding and mailbox export format."
        )
        if allow_empty:
            override_used = True
            warnings.append(
                "Override: exporting despite zero parsed Sent recipients — "
                "gate Sent blocking may be ineffective."
            )
        else:
            errors.append(msg)

    ok = len(errors) == 0
    return SentHistoryPreflightOutcome(
        ok=ok,
        errors=tuple(errors),
        warnings=tuple(warnings),
        override_used=override_used,
        probe=probe,
    )


def sent_preflight_summary_dict(outcome: SentHistoryPreflightOutcome) -> dict[str, object]:
    """JSON-serializable ``sent_preflight`` block for outbound summary files."""
    p = outcome.probe
    return {
        "ok": outcome.ok,
        "override_used": outcome.override_used,
        "gmail_user": p.gmail_user,
        "sent_folders": list(p.sent_folders),
        "sent_row_count": p.sent_row_count,
        "parsed_recipient_count": p.parsed_recipient_count,
        "distinct_folders_sample": list(p.distinct_folders_sample),
        "errors": list(outcome.errors),
        "warnings": list(outcome.warnings),
    }


class SentHistoryPreflightFailed(Exception):
    """Raised when :func:`evaluate_sent_history_preflight` is not OK and override was not used."""

    def __init__(self, outcome: SentHistoryPreflightOutcome) -> None:
        self.outcome = outcome
        msg = outcome.errors[0] if outcome.errors else "Sent-history preflight failed"
        super().__init__(msg)


def sent_preflight_failure_detail_lines(
    outcome: SentHistoryPreflightOutcome,
    *,
    allow_empty_flag: str = "--allow-empty-sent-history",
) -> list[str]:
    """Operator-facing detail lines (shared by lead/archive export CLIs)."""
    p = outcome.probe
    lines = [
        f"  gmail_user={p.gmail_user!r}",
        f"  sent_folders={list(p.sent_folders)!r}",
        f"  sent_row_count={p.sent_row_count}",
        f"  parsed_recipient_count={p.parsed_recipient_count}",
    ]
    if p.distinct_folders_sample:
        lines.append(f"  distinct_folder_sample_for_mailbox={list(p.distinct_folders_sample)!r}")
    for err in outcome.errors:
        lines.append(f"  {err}")
    lines.append("  hint: list exact IMAP folder names:")
    lines.append("    uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --list-folders")
    lines.append("  then ingest Sent with --folder '<exact label from the list>'.")
    lines.append(
        f"  If you accept the risk of incomplete Sent blocking, pass {allow_empty_flag} on the CLI"
    )
    lines.append(
        f"  or set {_OPERATOR_ALLOW_EMPTY_SENT_HISTORY}=1 "
        f"(legacy alias: {_LEGACY_STREAMLIT_ALLOW_EMPTY_SENT_HISTORY}=1)."
    )
    return lines


def print_sent_preflight_failure_to_stderr(
    outcome: SentHistoryPreflightOutcome,
    *,
    stream: TextIO | None = None,
    allow_empty_flag: str = "--allow-empty-sent-history",
    headline: str = "error: outbound Sent-history preflight failed — export aborted.",
) -> None:
    """Print the same multi-line failure as lead/archive export CLIs (exit code 3)."""
    out = stream if stream is not None else sys.stderr
    print(headline, file=out)
    for line in sent_preflight_failure_detail_lines(outcome, allow_empty_flag=allow_empty_flag):
        print(line, file=out)
