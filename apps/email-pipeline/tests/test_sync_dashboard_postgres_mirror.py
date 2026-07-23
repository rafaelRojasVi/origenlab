"""Tests for dashboard Postgres mirror sync orchestration."""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from origenlab_email_pipeline.contacto_gmail_source import CONTACTO_GMAIL_SOURCE_PREFIX
from origenlab_email_pipeline.dashboard_postgres_sync import (
    DASHBOARD_SYNC_KV_KEY,
    EXPECTED_ALEMBIC_HEAD,
    assert_sqlite_mart_ready_for_mirror_sync,
    build_loader_command,
    check_alembic_head,
    format_summary_text,
    merge_optional_loader_details,
    plan_loader_steps,
    redact_postgres_url,
    run_dashboard_mirror_sync,
    validate_optional_loader_audit,
    write_sync_watermark,
)
from origenlab_email_pipeline.db import init_schema

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "sync" / "sync_dashboard_postgres_mirror.py"
ALEMBIC_VERSIONS_DIR = REPO / "alembic" / "versions"

_REVISION_RE = re.compile(r'^revision:\s*str\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DOWN_REVISION_RE = re.compile(
    r'^down_revision:\s*Union\[str,\s*Sequence\[str\],\s*None\]\s*=\s*(.+)$',
    re.MULTILINE,
)


def _parse_down_revision(raw: str) -> list[str]:
    value = raw.strip()
    if value == "None":
        return []
    if value.startswith(('"', "'")):
        return [value.strip("\"'")]
    return re.findall(r'["\']([^"\']+)["\']', value)


def compute_alembic_head_from_versions(versions_dir: Path = ALEMBIC_VERSIONS_DIR) -> str:
    revisions: set[str] = set()
    referenced: set[str] = set()
    for path in sorted(versions_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev_match = _REVISION_RE.search(text)
        if not rev_match:
            continue
        revision = rev_match.group(1)
        revisions.add(revision)
        down_match = _DOWN_REVISION_RE.search(text)
        if down_match:
            referenced.update(_parse_down_revision(down_match.group(1)))
    heads = revisions - referenced
    assert len(heads) == 1, f"expected exactly one Alembic head, got {sorted(heads)}"
    return next(iter(heads))

_PATCH_PG = "origenlab_email_pipeline.dashboard_postgres_sync.preflight_postgres"
_PATCH_COUNTS = "origenlab_email_pipeline.dashboard_postgres_sync.collect_mirror_counts"
_PATCH_START = "origenlab_email_pipeline.dashboard_postgres_sync.start_sync_run"
_PATCH_FINISH = "origenlab_email_pipeline.dashboard_postgres_sync.finish_sync_run"
_PATCH_WM = "origenlab_email_pipeline.dashboard_postgres_sync.write_sync_watermark"
_PATCH_CLASSIFY = (
    "origenlab_email_pipeline.dashboard_postgres_sync.sync_email_classification_canonical"
)
_PATCH_PURCHASE = (
    "origenlab_email_pipeline.dashboard_postgres_sync.sync_commercial_purchase_events"
)
_PATCH_DEALS = "origenlab_email_pipeline.dashboard_postgres_sync.sync_commercial_deals"
_PATCH_OPTIONAL = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_optional_db2_loaders"
)
_PATCH_EQUIP = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_equipment_opportunity_sync"
)
_PATCH_WARM = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_warm_case_promotion_sync"
)
_PATCH_UPDATE_DETAILS = (
    "origenlab_email_pipeline.dashboard_postgres_sync.update_sync_run_details"
)
_PATCH_SNAPSHOTS = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_operator_snapshots_sync"
)


def _setup_sqlite(db_path: Path, *, with_mart_rows: bool) -> None:
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO emails (
          source_file, message_id, date_iso, folder, sender, recipients, subject, body
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{CONTACTO_GMAIL_SOURCE_PREFIX}INBOX/msg",
            "msg-1",
            "2026-05-16T10:00:00",
            "INBOX",
            "buyer@lab.cl",
            "contacto@origenlab.cl",
            "Test",
            "body",
        ),
    )
    if with_mart_rows:
        conn.execute("INSERT INTO contact_master (email) VALUES ('buyer@lab.cl')")
        conn.execute("INSERT INTO organization_master (domain) VALUES ('lab.cl')")
        conn.execute(
            """
            INSERT INTO opportunity_signals (
              signal_type, entity_kind, entity_key, created_at
            ) VALUES ('test', 'contact', 'buyer@lab.cl', '2026-05-16T10:00:00')
            """
        )
    conn.commit()
    conn.close()


def _sample_mirror_counts() -> dict[str, int]:
    return {
        "canonical_contact_count": 10,
        "canonical_organization_count": 5,
        "canonical_opportunity_signal_count": 3,
        "archive_contact_count": 100,
        "archive_organization_count": 50,
        "archive_opportunity_signal_count": 20,
        "email_suppression_count": 1,
        "domain_suppression_count": 1,
        "outreach_state_count": 2,
        "commercial_purchase_event_count": 1,
        "commercial_purchase_event_item_count": 3,
    }


def test_redact_postgres_url_strips_password() -> None:
    url = "postgresql://origenlab:secret@127.0.0.1:5432/origenlab_scratch"
    red = redact_postgres_url(url)
    assert "secret" not in red
    assert "origenlab:***" in red
    assert "origenlab_scratch" in red


def test_plan_loader_steps_default_order() -> None:
    steps = plan_loader_steps(only=None, skip_outbound=False, skip_mart=False)
    assert [s.name for s in steps] == ["outbound_sidecars", "mart_core"]
    assert steps[1].argv == ("--replace", "--tables", "all")


def test_plan_loader_steps_only_canonical() -> None:
    steps = plan_loader_steps(only="canonical", skip_outbound=False, skip_mart=False)
    assert len(steps) == 1
    assert steps[0].name == "mart_core"
    assert "--tables" in steps[0].argv
    assert "canonical" in steps[0].argv


def test_dashboard_fast_maps_to_only_canonical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    calls: list[str] = []

    def _fake_loader(cmd: list[str], _root: Path) -> int:
        calls.append(" ".join(cmd))
        return 0

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=5), patch(_PATCH_FINISH), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--dashboard-fast"],
            repo_root=REPO,
            loader_runner=_fake_loader,
        )

    assert result["ok"] is True
    assert len(calls) == 1
    assert "sqlite_mart_core_to_postgres.py" in calls[0]
    assert "--tables canonical" in calls[0]


def test_refuses_missing_postgres_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "x.sqlite"
    db.write_bytes(b"")
    monkeypatch.delenv("ORIGENLAB_POSTGRES_URL", raising=False)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_CLOUD_POSTGRES_URL", raising=False)
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))
    result = run_dashboard_mirror_sync(
        ["--sqlite-db", str(db)],
        repo_root=REPO,
        loader_runner=lambda _c, _r: 0,
    )
    assert result["ok"] is False
    assert any("Postgres URL required" in e for e in result["errors"])


def test_dry_run_does_not_invoke_loaders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    calls: list[list[str]] = []

    def _fake_loader(cmd: list[str], _root: Path) -> int:
        calls.append(cmd)
        return 0

    sample_counts = {
        "canonical_contact_count": 497,
        "archive_contact_count": 27198,
        "canonical_organization_count": 261,
        "archive_organization_count": 10688,
        "canonical_opportunity_signal_count": 200,
        "archive_opportunity_signal_count": 2705,
        "email_suppression_count": 2,
        "domain_suppression_count": 1,
        "outreach_state_count": 4,
    }

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=sample_counts,
    ), patch(_PATCH_START) as mock_start, patch(_PATCH_FINISH) as mock_finish:
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--dry-run"],
            repo_root=REPO,
            loader_runner=_fake_loader,
        )

    assert result["ok"] is True
    assert result["status"] == "dry_run"
    assert result["counts"]["canonical_contact_count"] == 497
    assert result["counts"]["archive_contact_count"] == 27198
    assert calls == []
    mock_start.assert_not_called()
    mock_finish.assert_not_called()


def test_loaders_called_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)

    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    order: list[str] = []

    def _fake_loader(cmd: list[str], _root: Path) -> int:
        if "outbound_sidecars" in " ".join(cmd):
            order.append("outbound")
        if "mart_core" in " ".join(cmd):
            order.append("mart")
        return 0

    sample_counts = {f"k{i}": i for i in range(9)}
    keys = [
        "canonical_contact_count",
        "canonical_organization_count",
        "canonical_opportunity_signal_count",
        "archive_contact_count",
        "archive_organization_count",
        "archive_opportunity_signal_count",
        "email_suppression_count",
        "domain_suppression_count",
        "outreach_state_count",
    ]
    sample_counts = dict(zip(keys, [1, 2, 3, 4, 5, 6, 7, 8, 9], strict=True))

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=sample_counts,
    ), patch(_PATCH_START, return_value=42), patch(_PATCH_FINISH), patch(
        _PATCH_CLASSIFY,
        return_value={"rows_written": 0, "skipped": False},
    ), patch(
        _PATCH_PURCHASE,
        return_value={"events_written": 0, "skipped": False},
    ):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=_fake_loader,
        )

    assert result["ok"] is True
    assert order == ["outbound", "mart"]
    assert result["sync_run_id"] == 42


def test_summary_includes_canonical_and_archive_counts() -> None:
    text = format_summary_text(
        {
            "status": "success",
            "dry_run": False,
            "elapsed_seconds": 1.2,
            "counts": {
                "canonical_contact_count": 497,
                "archive_contact_count": 27198,
                "canonical_organization_count": 261,
                "archive_organization_count": 10688,
                "canonical_opportunity_signal_count": 200,
                "archive_opportunity_signal_count": 2705,
                "email_suppression_count": 3,
                "domain_suppression_count": 2,
                "outreach_state_count": 10,
            },
        }
    )
    assert "canonical:" in text
    assert "497" in text
    assert "27198" in text
    assert "mirror/dashboard/summary" in text
    assert "8001" in text
    assert "8000/dashboard/summary" not in text


def test_summary_includes_warm_case_optional_loader() -> None:
    text = format_summary_text(
        {
            "status": "success",
            "dry_run": True,
            "elapsed_seconds": 0.5,
            "counts": {},
            "warm_case_sync": {
                "dry_run": True,
                "inserted_cases": 3,
                "updated_cases": 1,
                "linked_emails": 5,
                "candidate_count": 55,
                "queue_row_count": 58,
                "warm_days": 14,
                "warm_limit": 100,
                "close_missing": True,
                "closed_missing_cases": 17,
                "reopened_cases": 2,
                "categories_summary": {
                    "waiting_client": 24,
                    "quote_sent": 4,
                },
            },
        }
    )
    assert "optional dashboard loaders:" in text
    assert "warm_cases:" in text
    assert "inserted_cases: 3" in text
    assert "linked_emails: 5" in text
    assert "candidate_count: 55" in text
    assert "queue_row_count: 58" in text
    assert "warm_days: 14" in text
    assert "warm_limit: 100" in text
    assert "close_missing: True" in text
    assert "closed_missing_cases: 17" in text
    assert "reopened_cases: 2" in text
    assert "categories_summary: quote_sent=4, waiting_client=24" in text


def test_summary_includes_equipment_opportunity_optional_loader() -> None:
    text = format_summary_text(
        {
            "status": "success",
            "dry_run": False,
            "elapsed_seconds": 1.0,
            "counts": {},
            "equipment_opportunity_sync": {
                "applied": True,
                "row_count": 9,
                "source_id": "equipment_first_operator_queue_20260518",
            },
        }
    )
    assert "equipment_opportunities:" in text
    assert "row_count: 9" in text
    assert "source_id: equipment_first_operator_queue_20260518" in text


def test_summary_includes_commercial_deals_optional_loader() -> None:
    text = format_summary_text(
        {
            "status": "success",
            "dry_run": False,
            "elapsed_seconds": 2.0,
            "counts": {},
            "commercial_deals_sync": {
                "applied": True,
                "rows_inserted": 1,
                "skipped": False,
            },
        }
    )
    assert "commercial_deals:" in text
    assert "rows_inserted: 1" in text


def test_write_sync_watermark_inserts_run_and_kv() -> None:
    cur = MagicMock()
    cur.fetchone.return_value = (99,)

    class _Ctx:
        def __enter__(self) -> MagicMock:
            return cur

        def __exit__(self, *args: object) -> None:
            return None

    conn = MagicMock()
    conn.cursor.return_value = _Ctx()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    counts = {
        "canonical_contact_count": 1,
        "canonical_organization_count": 2,
        "canonical_opportunity_signal_count": 3,
        "archive_contact_count": 4,
        "archive_organization_count": 5,
        "archive_opportunity_signal_count": 6,
        "email_suppression_count": 7,
        "domain_suppression_count": 8,
        "outreach_state_count": 9,
    }

    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg") as mock_pg,
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            return_value=True,
        ),
    ):
        mock_pg.connect.return_value = conn
        from datetime import datetime, timezone

        sync_id = write_sync_watermark(
            "postgresql://u:p@127.0.0.1/scratch",
            sqlite_path=Path("/data/emails.sqlite"),
            postgres_url_redacted="postgresql://u:***@127.0.0.1/scratch",
            status="success",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            counts=counts,
            error_message=None,
            details={"loader_steps": []},
            dry_run=False,
        )

    assert sync_id == 99
    executed = " ".join(str(c) for c in cur.execute.call_args_list)
    assert "reporting.dashboard_sync_run" in executed
    assert DASHBOARD_SYNC_KV_KEY in executed


def test_build_loader_command_includes_replace() -> None:
    from origenlab_email_pipeline.dashboard_postgres_sync import LoaderStep

    step = LoaderStep("mart_core", "scripts/migrate/sqlite_mart_core_to_postgres.py", ("--replace",))
    cmd = build_loader_command(
        REPO,
        step,
        sqlite_path=Path("/tmp/x.sqlite"),
        postgres_url="postgresql://u:p@127.0.0.1/scratch",
        allow_non_scratch=False,
    )
    assert "--replace" in cmd
    assert str(Path("/tmp/x.sqlite")) in cmd


def test_assert_sqlite_mart_fails_when_canonical_gmail_but_empty_mart(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=False)
    with pytest.raises(ValueError, match="contact_master") as exc:
        assert_sqlite_mart_ready_for_mirror_sync(db, allow_empty_mart=False)
    msg = str(exc.value)
    assert "organization_master" in msg
    assert "opportunity_signals" in msg
    assert "build_business_mart" in msg


def test_assert_sqlite_mart_passes_when_mart_has_rows(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    counts = assert_sqlite_mart_ready_for_mirror_sync(db, allow_empty_mart=False)
    assert counts.canonical_gmail_email_count == 1
    assert counts.mart_table_counts["contact_master"] == 1


def test_assert_sqlite_mart_allow_empty_mart_override(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=False)
    counts = assert_sqlite_mart_ready_for_mirror_sync(db, allow_empty_mart=True)
    assert counts.mart_table_counts["contact_master"] == 0


def test_sync_fails_when_canonical_gmail_but_empty_mart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=False)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    loader_calls: list[str] = []

    def _fake_loader(cmd: list[str], _root: Path) -> int:
        loader_calls.append(" ".join(cmd))
        return 0

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=_fake_loader,
        )

    assert result["ok"] is False
    assert loader_calls == []
    assert any("contact_master" in e for e in result["errors"])


def test_sync_passes_when_mart_tables_have_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    loader_calls: list[str] = []

    def _fake_loader(cmd: list[str], _root: Path) -> int:
        if "mart_core" in " ".join(cmd):
            loader_calls.append("mart")
        return 0

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=7), patch(_PATCH_FINISH), patch(
        _PATCH_CLASSIFY,
        return_value={"rows_written": 0, "skipped": False},
    ), patch(
        _PATCH_PURCHASE,
        return_value={"events_written": 0, "skipped": False},
    ):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=_fake_loader,
        )

    assert result["ok"] is True
    assert "mart" in loader_calls
    assert "commercial_purchase_sync" in result


def test_sync_passes_with_allow_empty_mart_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=False)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    loader_calls: list[str] = []

    def _fake_loader(cmd: list[str], _root: Path) -> int:
        loader_calls.append("ran")
        return 0

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=1), patch(_PATCH_FINISH), patch(
        _PATCH_CLASSIFY,
        return_value={"rows_written": 0, "skipped": False},
    ), patch(
        _PATCH_PURCHASE,
        return_value={"events_written": 0, "skipped": False},
    ):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--allow-empty-mart"],
            repo_root=REPO,
            loader_runner=_fake_loader,
        )

    assert result["ok"] is True
    assert len(loader_calls) == 2


def test_script_help() -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0
    assert "--dry-run" in r.stdout
    assert "--only" in r.stdout
    assert "--allow-empty-mart" in r.stdout
    assert "--include-equipment-opportunities" in r.stdout
    assert "--include-warm-cases" in r.stdout
    assert "--dashboard-fast" in r.stdout


def test_validate_optional_loader_audit_requires_operator_on_apply() -> None:
    with pytest.raises(ValueError, match="updated-by"):
        validate_optional_loader_audit(
            include_equipment=True,
            include_warm_cases=False,
            dry_run=False,
            updated_by=None,
            reason="audit",
        )


def test_merge_optional_loader_details_shape() -> None:
    details = merge_optional_loader_details(
        {"loader_steps": []},
        equipment_summary={
            "source_id": 9,
            "rows_inserted": 12,
            "applied": True,
        },
        warm_summary={
            "inserted_cases": 3,
            "updated_cases": 2,
            "linked_emails": 5,
            "applied": True,
        },
    )
    assert details["equipment_opportunity_source_id"] == 9
    assert details["equipment_opportunity_row_count"] == 12
    assert details["warm_case_inserted_count"] == 3
    assert details["warm_case_updated_count"] == 2
    assert details["warm_case_linked_email_count"] == 5


def test_default_sync_does_not_call_optional_loaders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    optional_called = {"n": 0}

    def _optional(*args: Any, **kwargs: Any) -> tuple[None, None]:
        optional_called["n"] += 1
        return None, None

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=1), patch(_PATCH_FINISH), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ), patch(_PATCH_OPTIONAL, side_effect=_optional):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert optional_called["n"] == 0


def test_include_equipment_flag_calls_optional_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    def _optional(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], None]:
        assert kwargs.get("dry_run") is False
        return (
            {"applied": True, "source_id": 42, "rows_inserted": 9},
            None,
        )

    captured_details: dict[str, Any] = {}

    def _capture_update(
        _url: str, _sync_id: int, details: dict[str, Any], **_kwargs: Any
    ) -> None:
        captured_details.update(details)

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=7), patch(_PATCH_FINISH), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ), patch(_PATCH_EQUIP, side_effect=lambda *a, **k: {
        "applied": True, "source_id": 42, "rows_inserted": 9
    }), patch(
        _PATCH_UPDATE_DETAILS, side_effect=_capture_update
    ):
        result = run_dashboard_mirror_sync(
            [
                "--sqlite-db",
                str(db),
                "--include-equipment-opportunities",
                "--updated-by",
                "op",
                "--reason",
                "sync test",
            ],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert result["equipment_opportunity_sync"]["source_id"] == 42
    assert captured_details["equipment_opportunity_source_id"] == 42
    assert captured_details["equipment_opportunity_row_count"] == 9


def test_include_warm_cases_flag_calls_optional_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    def _optional(*args: Any, **kwargs: Any) -> tuple[None, dict[str, Any]]:
        return (
            None,
            {
                "applied": True,
                "inserted_cases": 4,
                "updated_cases": 1,
                "linked_emails": 4,
            },
        )

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=3), patch(_PATCH_FINISH), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ), patch(_PATCH_WARM, side_effect=lambda *a, **k: {
        "applied": True,
        "inserted_cases": 4,
        "updated_cases": 1,
        "linked_emails": 4,
    }), patch(_PATCH_UPDATE_DETAILS):
        result = run_dashboard_mirror_sync(
            [
                "--sqlite-db",
                str(db),
                "--include-warm-cases",
                "--updated-by",
                "op",
                "--reason",
                "warm sync",
            ],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert result["warm_case_sync"]["inserted_cases"] == 4
    assert result["details"]["warm_case_linked_email_count"] == 4


def test_warm_case_promotion_receives_warm_days_and_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    captured: dict[str, Any] = {}

    def _warm(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "applied": False,
            "dry_run": True,
            "candidate_count": 2,
            "queue_row_count": 3,
            "warm_days": kwargs["days_window"],
            "warm_limit": kwargs["limit"],
        }

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(
        "origenlab_email_pipeline.dashboard_postgres_sync.run_warm_case_promotion_sync",
        side_effect=_warm,
    ):
        result = run_dashboard_mirror_sync(
            [
                "--sqlite-db",
                str(db),
                "--dry-run",
                "--include-warm-cases",
                "--warm-days",
                "14",
                "--warm-limit",
                "100",
            ],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert captured["days_window"] == 14
    assert captured["limit"] == 100
    assert captured["close_missing"] is False
    assert result["warm_case_sync"]["warm_days"] == 14
    assert result["warm_case_sync"]["warm_limit"] == 100


def test_warm_case_promotion_receives_close_missing_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    captured: dict[str, Any] = {}

    def _warm(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "applied": False,
            "dry_run": True,
            "closed_missing_cases": 0,
            "close_missing": kwargs["close_missing"],
        }

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(
        "origenlab_email_pipeline.dashboard_postgres_sync.run_warm_case_promotion_sync",
        side_effect=_warm,
    ):
        result = run_dashboard_mirror_sync(
            [
                "--sqlite-db",
                str(db),
                "--dry-run",
                "--include-warm-cases",
                "--close-missing-warm-cases",
            ],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert captured["close_missing"] is True
    assert result["warm_case_sync"]["close_missing"] is True


def test_include_commercial_deals_flag_calls_deals_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    deals_called = {"n": 0}

    def _deals(*args: Any, **kwargs: Any) -> dict[str, Any]:
        deals_called["n"] += 1
        assert kwargs.get("dry_run") is False
        return {"deals_built": 1, "deals_written": 1}

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=3), patch(_PATCH_FINISH), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ), patch(_PATCH_DEALS, side_effect=_deals):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--include-commercial-deals"],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert deals_called["n"] == 1
    assert result["commercial_deals_sync"]["deals_written"] == 1


def test_optional_loader_failure_surfaces_in_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    def _optional(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], None]:
        raise RuntimeError("equipment_opportunity_mirror failed: source_already_loaded")

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=1), patch(_PATCH_FINISH), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ), patch(_PATCH_EQUIP, side_effect=RuntimeError("equipment_opportunity_mirror failed: source_already_loaded")):
        result = run_dashboard_mirror_sync(
            [
                "--sqlite-db",
                str(db),
                "--include-equipment-opportunities",
                "--updated-by",
                "op",
                "--reason",
                "fail test",
            ],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is False
    assert any("source_already_loaded" in e for e in result["errors"])


def test_apply_missing_reason_fails_before_loaders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    db.write_bytes(b"x")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    loader_called = {"n": 0}

    def _fake_loader(cmd: list[str], _root: Path) -> int:
        loader_called["n"] += 1
        return 0

    result = run_dashboard_mirror_sync(
        [
            "--sqlite-db",
            str(db),
            "--include-warm-cases",
            "--updated-by",
            "op",
        ],
        repo_root=REPO,
        loader_runner=_fake_loader,
    )
    assert result["ok"] is False
    assert any("reason" in e.lower() for e in result["errors"])
    assert loader_called["n"] == 0


def test_alembic_migration_defines_dashboard_sync_run() -> None:
    path = REPO / "alembic" / "versions" / "20260517_0008_reporting_dashboard_sync_run.py"
    text = path.read_text(encoding="utf-8")
    assert "reporting.dashboard_sync_run" in text


def test_alembic_migration_defines_email_classification_canonical() -> None:
    path = REPO / "alembic" / "versions" / "20260518_0009_reporting_email_classification_canonical.py"
    text = path.read_text(encoding="utf-8")
    assert "reporting.email_classification_canonical" in text


def test_alembic_migration_defines_commercial_deal_mirror() -> None:
    path = REPO / "alembic" / "versions" / "20260526_0018_commercial_deal_mirror.py"
    text = path.read_text(encoding="utf-8")
    assert "commercial.deal" in text


def test_expected_alembic_head_matches_migration_chain_head() -> None:
    assert compute_alembic_head_from_versions() == EXPECTED_ALEMBIC_HEAD


def test_alembic_head_matches_db1_api_read_model_chain() -> None:
    catalog_path = REPO / "alembic" / "versions" / "20260527_0019_catalog_mirror.py"
    assert catalog_path.is_file()
    assert "catalog.product" in catalog_path.read_text(encoding="utf-8")
    origin_path = REPO / "alembic" / "versions" / "20260531_0022_lead_intel_prospect_origin.py"
    assert origin_path.is_file()
    origin_text = origin_path.read_text(encoding="utf-8")
    assert "lead_intel.prospect" in origin_text
    assert "source_type" in origin_text
    assert "gmail_first_contacted_at" in origin_text
    assert "gmail_latest_subject_safe" in origin_text
    role_path = REPO / "alembic" / "versions" / "20260607_0023_warm_case_role_category.py"
    assert role_path.is_file()
    role_text = role_path.read_text(encoding="utf-8")
    assert "role_category" in role_text
    assert "COALESCE(c.role_category, c.category)" in role_text
    assert "supplier_quote_received" in role_text
    equipment_path = (
        REPO / "alembic" / "versions" / "20260614_0024_api_v_equipment_opportunity_extra_json.py"
    )
    assert equipment_path.is_file()
    assert "extra_json" in equipment_path.read_text(encoding="utf-8")
    current_path = (
        REPO / "alembic" / "versions" / "20260617_0030_api_v_equipment_opportunity_current.py"
    )
    assert current_path.is_file()
    assert "api.v_equipment_opportunity_current" in current_path.read_text(encoding="utf-8")


def test_check_alembic_head_accepts_expected_head() -> None:
    with patch(
        "origenlab_email_pipeline.dashboard_postgres_sync.fetch_alembic_version",
        return_value=EXPECTED_ALEMBIC_HEAD,
    ):
        ok, version, msg = check_alembic_head(MagicMock())
    assert ok is True
    assert version == EXPECTED_ALEMBIC_HEAD
    assert msg == ""


def test_check_alembic_head_mismatch_message_uses_current_expected_head() -> None:
    with patch(
        "origenlab_email_pipeline.dashboard_postgres_sync.fetch_alembic_version",
        return_value="20260607_0023",
    ):
        ok, version, msg = check_alembic_head(MagicMock())
    assert ok is False
    assert version == "20260607_0023"
    assert msg == (
        f"Alembic head mismatch: database='20260607_0023' expected={EXPECTED_ALEMBIC_HEAD!r}"
    )


def test_alembic_migration_defines_warm_case_role_category() -> None:
    path = REPO / "alembic" / "versions" / "20260607_0023_warm_case_role_category.py"
    text = path.read_text(encoding="utf-8")
    assert "commercial.warm_case" in text
    assert "api.v_warm_case" in text


_PATCH_OPERATOR_SNAPSHOTS = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_operator_snapshots_sync"
)


def test_dry_run_includes_operator_snapshot_summary_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(
        _PATCH_OPERATOR_SNAPSHOTS,
        return_value={
            "dry_run": True,
            "gmail_interaction_audit": {"domain_count": 2, "lookback_days": 180},
            "operator_automation_status": {"verdict": "healthy"},
        },
    ):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--dry-run", "--include-operator-snapshots"],
            repo_root=REPO,
        )

    assert result["ok"] is True
    assert result["operator_snapshots"]["gmail_interaction_audit"]["domain_count"] == 2
    assert "operator_snapshots" in result["details"]


def test_apply_writes_operator_snapshots_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS,
        return_value=_sample_mirror_counts(),
    ), patch(_PATCH_START, return_value=1), patch(_PATCH_FINISH), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ), patch(_PATCH_UPDATE_DETAILS), patch(
        _PATCH_SNAPSHOTS,
        return_value={
            "dry_run": False,
            "gmail_interaction_audit": {
                "domain_count": 1,
                "publish": {"published": True, "kv_key": "dashboard_gmail_interaction_audit_v1"},
            },
            "operator_automation_status": {
                "verdict": "healthy",
                "publish": {
                    "published": True,
                    "kv_key": "operator_automation_status_snapshot_v1",
                },
            },
        },
    ):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--include-operator-snapshots"],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert result["operator_snapshots"]["operator_automation_status"]["verdict"] == "healthy"


def test_live_dashboard_flags_include_operator_snapshots() -> None:
    from origenlab_email_pipeline.operator_cli.mirror import (
        LIVE_DASHBOARD_FLAGS,
        build_live_dashboard_passthrough,
    )

    assert "--include-operator-snapshots" in LIVE_DASHBOARD_FLAGS
    passthrough = build_live_dashboard_passthrough(
        updated_by="op",
        reason="live mirror",
        passthrough=[],
    )
    assert "--include-operator-snapshots" in passthrough
