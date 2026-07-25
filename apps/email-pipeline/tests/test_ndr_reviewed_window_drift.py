"""Tests for reviewed NDR evidence-window freezing (no rolling apply-time cutoff drift)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from origenlab_email_pipeline.ndr_contacto_scan import (
    compute_scan_start_date_utc,
    scan_ndr_planned_recipients,
    validate_since_date_utc,
)
from origenlab_email_pipeline.operator_cli.ndr_safe_auto_apply import (
    ALLOWLIST_BATCH_A_FILENAME,
    NdrSafeAutoApplyOptions,
    build_ndr_safe_auto_apply_plan,
    run_ndr_safe_auto_apply,
)
from origenlab_email_pipeline.qa.ndr_review_queue import (
    APPLY_ONLY_CODE_BATCH_A,
    build_ndr_review_queue,
    derive_legacy_scan_start_date_utc,
    resolve_reviewed_ndr_scan_start_date_utc,
)


CONTACTO_SOURCE = "gmail:contacto@origenlab.cl/INBOX/file1.eml"


def _seed_ndr_db(db: Path, *, rows: list[tuple[str, str, str]]) -> None:
    """rows: (email, date_iso, body_fragment)."""
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            source_file TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            recipients TEXT,
            date_raw TEXT,
            date_iso TEXT,
            body TEXT,
            body_html TEXT,
            body_text_raw TEXT,
            body_text_clean TEXT,
            body_source_type TEXT,
            body_has_plain INTEGER,
            body_has_html INTEGER,
            full_body_clean TEXT,
            top_reply_clean TEXT,
            attachment_count INTEGER,
            has_attachments INTEGER
        );
        CREATE TABLE contact_email_suppression (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            suppression_reason_code TEXT NOT NULL,
            suppression_reason_text TEXT,
            suppression_source TEXT,
            last_bounced_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_by TEXT
        );
        """
    )
    for idx, (email, date_iso, fragment) in enumerate(rows, start=1):
        body = (
            f"Final-Recipient: rfc822; {email}\n"
            f"{fragment}"
        )
        conn.execute(
            """
            INSERT INTO emails (
                id, source_file, folder, subject, sender, recipients, date_raw, date_iso,
                body, body_text_clean, full_body_clean
            ) VALUES (?, ?, 'INBOX', ?, 'mailer-daemon@x', '', ?, ?, ?, ?, ?)
            """,
            (
                idx,
                CONTACTO_SOURCE,
                "Delivery Status Notification (Failure)",
                date_iso,
                date_iso,
                body,
                body,
                body,
            ),
        )
    conn.commit()
    conn.close()


def test_queue_generated_july_24_stores_scan_start_july_22(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    _seed_ndr_db(
        db,
        rows=[("sales@vortexg.com", "2026-07-22", "550 5.1.1 User unknown")],
    )
    as_of = datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc)
    result = build_ndr_review_queue(
        sqlite_path=db,
        out_dir=tmp_path / "out",
        since_days=2,
        date_label="2026_07_24",
        as_of_utc=as_of,
    )
    assert result.scan_start_date_utc == "2026-07-22"
    assert result.summary_json()["scan_start_date_utc"] == "2026-07-22"
    assert "sales@vortexg.com" in result.allowlist_batch_a


def test_compute_scan_start_matches_sqlite_calendar_semantics() -> None:
    as_of = datetime(2026, 7, 24, 23, 59, 59, tzinfo=timezone.utc)
    assert compute_scan_start_date_utc(since_days=2, as_of_utc=as_of) == "2026-07-22"


def test_absolute_scan_keeps_july_22_evidence_on_july_25(tmp_path: Path) -> None:
    db = tmp_path / "scan.db"
    _seed_ndr_db(
        db,
        rows=[("sales@vortexg.com", "2026-07-22", "550 5.1.1 User unknown")],
    )
    conn = sqlite3.connect(db)
    try:
        planned, scanned, _ = scan_ndr_planned_recipients(
            conn,
            since_date_utc="2026-07-22",
            limit=100,
        )
    finally:
        conn.close()
    assert scanned == 1
    assert "sales@vortexg.com" in planned
    assert planned["sales@vortexg.com"][0] == "bounce_no_such_user"


def test_relative_scan_on_july_25_would_drop_july_22(tmp_path: Path) -> None:
    """Documents the production defect: relative --since-days drifts after review day."""
    db = tmp_path / "drift.db"
    _seed_ndr_db(
        db,
        rows=[("sales@vortexg.com", "2026-07-22", "550 5.1.1 User unknown")],
    )
    conn = sqlite3.connect(db)
    # relative since_days=2 on 2026-07-25 => lower bound 2026-07-23.
    drifted = compute_scan_start_date_utc(
        since_days=2,
        as_of_utc=datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert drifted == "2026-07-23"
    try:
        planned, _, _ = scan_ndr_planned_recipients(
            conn,
            since_date_utc=drifted,
            limit=100,
        )
    finally:
        conn.close()
    assert "sales@vortexg.com" not in planned


def test_apply_plan_july_25_uses_frozen_july_22(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    queue = active / "ndr_review_queue_2026_07_24"
    queue.mkdir(parents=True)
    summary = {
        "generated_at": "2026-07-24T18:00:00+00:00",
        "since_days": 2,
        "scan_start_date_utc": "2026-07-22",
        "date_label": "2026_07_24",
        "candidates_total": 1,
        "candidates_already_suppressed": 0,
        "candidates_unsuppressed": 1,
        "batch_counts": {"A": 1, "B": 0, "C": 0, "D": 0, "E": 0},
    }
    (queue / "ndr_review_summary.json").write_text(
        __import__("json").dumps(summary),
        encoding="utf-8",
    )
    (queue / ALLOWLIST_BATCH_A_FILENAME).write_text(
        "sales@vortexg.com\n",
        encoding="utf-8",
    )
    plan, code, _ = build_ndr_safe_auto_apply_plan(
        NdrSafeAutoApplyOptions(batch="A", reports_dir=tmp_path, queue_dir=queue),
        now_fn=lambda: datetime(2026, 7, 25, 15, 0, 0, tzinfo=timezone.utc),
    )
    assert code == 0
    assert plan["scan_start_date_utc"] == "2026-07-22"
    assert plan["scan_window_error"] is None


def test_apply_plan_july_26_still_uses_july_22_inside_age_limit(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    queue = active / "ndr_review_queue_2026_07_24"
    queue.mkdir(parents=True)
    summary = {
        "generated_at": "2026-07-24T18:00:00+00:00",
        "since_days": 2,
        "scan_start_date_utc": "2026-07-22",
        "candidates_total": 1,
        "candidates_unsuppressed": 1,
        "batch_counts": {"A": 1, "B": 0, "C": 0, "D": 0, "E": 0},
    }
    (queue / "ndr_review_summary.json").write_text(
        __import__("json").dumps(summary),
        encoding="utf-8",
    )
    (queue / ALLOWLIST_BATCH_A_FILENAME).write_text("sales@vortexg.com\n", encoding="utf-8")
    plan, code, _ = build_ndr_safe_auto_apply_plan(
        NdrSafeAutoApplyOptions(batch="A", reports_dir=tmp_path, queue_dir=queue),
        now_fn=lambda: datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert code == 0
    assert plan["scan_start_date_utc"] == "2026-07-22"


def test_legacy_queue_derives_july_22() -> None:
    derived = derive_legacy_scan_start_date_utc(
        generated_at="2026-07-24T18:00:00+00:00",
        since_days=2,
    )
    assert derived == "2026-07-22"
    resolved, err = resolve_reviewed_ndr_scan_start_date_utc(
        {
            "generated_at": "2026-07-24T18:00:00+00:00",
            "since_days": 2,
        },
        now_utc=datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert err is None
    assert resolved == "2026-07-22"


def test_legacy_queue_malformed_generated_fails_closed() -> None:
    resolved, err = resolve_reviewed_ndr_scan_start_date_utc(
        {"generated_at": "not-a-timestamp", "since_days": 2},
        now_utc=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    assert resolved is None
    assert err == "malformed_queue_generated_at"


def test_legacy_queue_missing_since_days_fails_closed() -> None:
    resolved, err = resolve_reviewed_ndr_scan_start_date_utc(
        {"generated_at": "2026-07-24T18:00:00+00:00"},
        now_utc=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    assert resolved is None
    assert err == "missing_queue_since_days"


def test_queue_too_old_refused() -> None:
    resolved, err = resolve_reviewed_ndr_scan_start_date_utc(
        {
            "generated_at": "2026-07-10T18:00:00+00:00",
            "since_days": 2,
            "scan_start_date_utc": "2026-07-08",
        },
        now_utc=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    assert resolved is None
    assert err == "queue_too_old_rebuild_required"


def test_apply_refuses_queue_too_old(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    queue = active / "ndr_review_queue_2026_07_10"
    queue.mkdir(parents=True)
    summary = {
        "generated_at": "2026-07-10T18:00:00+00:00",
        "since_days": 2,
        "scan_start_date_utc": "2026-07-08",
        "candidates_total": 1,
        "candidates_unsuppressed": 1,
        "batch_counts": {"A": 1, "B": 0, "C": 0, "D": 0, "E": 0},
    }
    (queue / "ndr_review_summary.json").write_text(
        __import__("json").dumps(summary),
        encoding="utf-8",
    )
    (queue / ALLOWLIST_BATCH_A_FILENAME).write_text("old@example.cl\n", encoding="utf-8")

    from origenlab_email_pipeline.operator_cli import ndr_safe_auto_apply as mod

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "load_settings", lambda: MagicMock(resolved_reports_dir=lambda: tmp_path))
        rc = run_ndr_safe_auto_apply(
            NdrSafeAutoApplyOptions(
                batch="A",
                dry_run=False,
                apply=True,
                confirm_reviewed=True,
                operator="rafael",
                reports_dir=tmp_path,
                queue_dir=queue,
            ),
            now_fn=lambda: datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc),
            subprocess_run=MagicMock(),
        )
    assert rc == 1


def test_mutual_exclusion_raises_in_scan(tmp_path: Path) -> None:
    db = tmp_path / "mx.db"
    _seed_ndr_db(db, rows=[("a@x.cl", "2026-07-22", "550 5.1.1 User unknown")])
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(ValueError, match="must not both be active"):
            scan_ndr_planned_recipients(
                conn,
                since_days=2,
                since_date_utc="2026-07-22",
                limit=10,
            )
    finally:
        conn.close()


def test_validate_since_date_utc_strict() -> None:
    assert validate_since_date_utc("2026-07-22") == "2026-07-22"
    with pytest.raises(ValueError):
        validate_since_date_utc("2026/07/22")
    with pytest.raises(ValueError):
        validate_since_date_utc("2026-13-01")


def test_sql_uses_bound_parameter_for_absolute_date(tmp_path: Path) -> None:
    db = tmp_path / "bind.db"
    _seed_ndr_db(db, rows=[("a@x.cl", "2026-07-22", "550 5.1.1 User unknown")])
    conn = sqlite3.connect(db)
    executed: list[tuple[str, tuple[object, ...]]] = []

    class TrackingConnection:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def execute(self, sql: str, params: tuple[object, ...] = ()) -> object:
            executed.append((sql, params))
            return self._inner.execute(sql, params)

    try:
        scan_ndr_planned_recipients(
            TrackingConnection(conn),  # type: ignore[arg-type]
            since_date_utc="2026-07-22",
            limit=5,
        )
    finally:
        conn.close()
    assert executed
    sql, params = executed[0]
    assert "date_iso >= ?" in sql
    assert "2026-07-22" in params
    assert "2026-07-22" not in sql


def test_cli_mutual_exclusion(tmp_path: Path, ndr_tool_module=None) -> None:
    import importlib.util
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/tools/flag_ndr_bounces_from_contacto.py"
    )
    spec = importlib.util.spec_from_file_location("flag_ndr_cli_mx", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flag_ndr_cli_mx"] = mod
    spec.loader.exec_module(mod)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            sys,
            "argv",
            [
                "flag_ndr_bounces_from_contacto.py",
                "--since-days",
                "2",
                "--since-date-utc",
                "2026-07-22",
                "--db",
                str(tmp_path / "missing.db"),
            ],
        )
        assert mod.main() == 1


def test_candidate_absent_from_frozen_window_still_refused(tmp_path: Path) -> None:
    import importlib.util
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/tools/flag_ndr_bounces_from_contacto.py"
    )
    spec = importlib.util.spec_from_file_location("flag_ndr_cli_absent", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flag_ndr_cli_absent"] = mod
    spec.loader.exec_module(mod)

    planned = {"known@x.cl": ("bounce_no_such_user", "2026-07-22", 1, "x")}
    selected, refused_missing, refused_wrong = mod.select_planned_for_apply(
        planned,
        emails_allowlist=["ghost@x.cl"],
        only_code=APPLY_ONLY_CODE_BATCH_A,
    )
    assert selected == {}
    assert refused_missing == ["ghost@x.cl"]
    assert refused_wrong == []


def test_wrong_code_still_refused() -> None:
    import importlib.util
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/tools/flag_ndr_bounces_from_contacto.py"
    )
    spec = importlib.util.spec_from_file_location("flag_ndr_cli_wrong", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flag_ndr_cli_wrong"] = mod
    spec.loader.exec_module(mod)
    planned = {"x@y.cl": ("bounce_other", "2026-07-22", 1, None)}
    selected, refused_missing, refused_wrong = mod.select_planned_for_apply(
        planned,
        emails_allowlist=["x@y.cl"],
        only_code=APPLY_ONLY_CODE_BATCH_A,
    )
    assert selected == {}
    assert refused_missing == []
    assert refused_wrong == ["x@y.cl"]


def test_apply_passes_absolute_date_not_relative_days(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    queue = active / "ndr_review_queue_2026_07_24"
    queue.mkdir(parents=True)
    summary = {
        "generated_at": "2026-07-24T18:00:00+00:00",
        "since_days": 2,
        "scan_start_date_utc": "2026-07-22",
        "candidates_total": 1,
        "candidates_unsuppressed": 1,
        "batch_counts": {"A": 1, "B": 0, "C": 0, "D": 0, "E": 0},
    }
    (queue / "ndr_review_summary.json").write_text(
        __import__("json").dumps(summary),
        encoding="utf-8",
    )
    (queue / ALLOWLIST_BATCH_A_FILENAME).write_text("sales@vortexg.com\n", encoding="utf-8")

    from origenlab_email_pipeline.operator_cli import ndr_safe_auto_apply as mod

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="ok\n", stderr="")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "load_settings", lambda: MagicMock(resolved_reports_dir=lambda: tmp_path))
        rc = run_ndr_safe_auto_apply(
            NdrSafeAutoApplyOptions(
                batch="A",
                dry_run=False,
                apply=True,
                confirm_reviewed=True,
                operator="rafael",
                reports_dir=tmp_path,
                queue_dir=queue,
            ),
            now_fn=lambda: datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc),
            subprocess_run=fake_run,
            rebuild_queue_fn=lambda **kwargs: {"out_dir": str(active / "rebuilt")},
        )
    assert rc == 0
    ndr_cmd = calls[0]
    assert "--since-date-utc" in ndr_cmd
    assert ndr_cmd[ndr_cmd.index("--since-date-utc") + 1] == "2026-07-22"
    assert "--since-days" not in ndr_cmd
