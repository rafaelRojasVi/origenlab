"""Dashboard mirror lifecycle: one run row progressing running → success|failed."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from origenlab_email_pipeline.contacto_gmail_source import CONTACTO_GMAIL_SOURCE_PREFIX
from origenlab_email_pipeline.dashboard_postgres_sync import (
    DASHBOARD_SYNC_KV_KEY,
    EXPECTED_ALEMBIC_HEAD,
    build_selected_loaders,
    finish_sync_run,
    plan_loader_steps,
    run_dashboard_mirror_sync,
    start_sync_run,
)
from origenlab_email_pipeline.db import init_schema

REPO = Path(__file__).resolve().parents[1]


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

_PATCH_PG = "origenlab_email_pipeline.dashboard_postgres_sync.preflight_postgres"
_PATCH_COUNTS = "origenlab_email_pipeline.dashboard_postgres_sync.collect_mirror_counts"
_PATCH_START = "origenlab_email_pipeline.dashboard_postgres_sync.start_sync_run"
_PATCH_FINISH = "origenlab_email_pipeline.dashboard_postgres_sync.finish_sync_run"
_PATCH_CLASSIFY = (
    "origenlab_email_pipeline.dashboard_postgres_sync.sync_email_classification_canonical"
)
_PATCH_PURCHASE = (
    "origenlab_email_pipeline.dashboard_postgres_sync.sync_commercial_purchase_events"
)
_PATCH_DEALS = "origenlab_email_pipeline.dashboard_postgres_sync.sync_commercial_deals"
_PATCH_EQUIP = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_equipment_opportunity_sync"
)
_PATCH_WARM = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_warm_case_promotion_sync"
)
_PATCH_SNAPSHOTS = (
    "origenlab_email_pipeline.dashboard_postgres_sync.run_operator_snapshots_sync"
)


def _base_patches(**extra: Any):
    patches = {
        _PATCH_PG: (EXPECTED_ALEMBIC_HEAD, []),
        _PATCH_COUNTS: _sample_mirror_counts(),
        _PATCH_CLASSIFY: {"rows_written": 1},
        _PATCH_PURCHASE: {"events_written": 1},
    }
    patches.update(extra)
    return patches


def test_build_selected_loaders_default_and_optional() -> None:
    steps = plan_loader_steps(only=None, skip_outbound=False, skip_mart=False)
    assert build_selected_loaders(steps) == [
        "outbound_sidecars",
        "mart_core",
        "classification",
        "commercial_purchase",
    ]
    assert build_selected_loaders(
        steps,
        include_commercial_deals=True,
        include_equipment_opportunities=True,
        include_warm_cases=True,
        include_operator_snapshots=True,
    ) == [
        "outbound_sidecars",
        "mart_core",
        "classification",
        "commercial_purchase",
        "commercial_deals",
        "equipment_opportunities",
        "warm_cases",
        "operator_snapshots",
    ]


def test_successful_default_run_finishes_after_classification_and_purchase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    order: list[str] = []

    def _start(*_a: Any, **_k: Any) -> int:
        order.append("start")
        return 77

    def _finish(*_a: Any, **kwargs: Any) -> None:
        order.append(f"finish:{kwargs['status']}")
        assert kwargs["sync_run_id"] == 77
        assert "classification" in kwargs["details"]["completed_loaders"]
        assert "commercial_purchase" in kwargs["details"]["completed_loaders"]

    def _classify(*_a: Any, **_k: Any) -> dict[str, Any]:
        order.append("classification")
        return {"rows_written": 1}

    def _purchase(*_a: Any, **_k: Any) -> dict[str, Any]:
        order.append("purchase")
        return {"events_written": 1}

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, side_effect=_start), patch(
        _PATCH_FINISH, side_effect=_finish
    ), patch(_PATCH_CLASSIFY, side_effect=_classify), patch(
        _PATCH_PURCHASE, side_effect=_purchase
    ):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["sync_run_id"] == 77
    assert order == ["start", "classification", "purchase", "finish:success"]
    assert result["completed_loaders"][-2:] == ["classification", "commercial_purchase"]


def test_optional_loaders_success_after_all_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    finish_details: dict[str, Any] = {}

    def _finish(*_a: Any, **kwargs: Any) -> None:
        finish_details.update(kwargs["details"])
        assert kwargs["status"] == "success"

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, return_value=9), patch(
        _PATCH_FINISH, side_effect=_finish
    ), patch(_PATCH_CLASSIFY, return_value={}), patch(
        _PATCH_PURCHASE, return_value={}
    ), patch(_PATCH_DEALS, return_value={"deals_written": 1}), patch(
        _PATCH_EQUIP, return_value={"applied": True, "rows_inserted": 2}
    ), patch(
        _PATCH_WARM, return_value={"applied": True, "inserted_cases": 1}
    ), patch(
        _PATCH_SNAPSHOTS, return_value={"dry_run": False, "ok": True}
    ), patch(
        "origenlab_email_pipeline.dashboard_postgres_sync.update_sync_run_details"
    ):
        result = run_dashboard_mirror_sync(
            [
                "--sqlite-db",
                str(db),
                "--include-commercial-deals",
                "--include-equipment-opportunities",
                "--include-warm-cases",
                "--include-operator-snapshots",
                "--updated-by",
                "op",
                "--reason",
                "lifecycle",
            ],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    selected = set(result["selected_loaders"])
    completed = set(result["completed_loaders"])
    assert selected == completed
    assert "commercial_deals" in completed
    assert "equipment_opportunities" in completed
    assert "warm_cases" in completed
    assert "operator_snapshots" in completed
    assert finish_details["selected_loaders"] == finish_details["completed_loaders"]


@pytest.mark.parametrize(
    "fail_step,argv_extra,patch_target,side_effect",
    [
        ("outbound_sidecars", [], None, None),
        ("mart_core", [], None, None),
        (
            "classification",
            [],
            _PATCH_CLASSIFY,
            RuntimeError("classification boom"),
        ),
        (
            "commercial_purchase",
            [],
            _PATCH_PURCHASE,
            RuntimeError("purchase boom"),
        ),
        (
            "commercial_deals",
            ["--include-commercial-deals"],
            _PATCH_DEALS,
            RuntimeError("deals boom"),
        ),
        (
            "equipment_opportunities",
            [
                "--include-equipment-opportunities",
                "--updated-by",
                "op",
                "--reason",
                "x",
            ],
            _PATCH_EQUIP,
            RuntimeError("equip boom"),
        ),
        (
            "warm_cases",
            ["--include-warm-cases", "--updated-by", "op", "--reason", "x"],
            _PATCH_WARM,
            RuntimeError("warm boom"),
        ),
        (
            "operator_snapshots",
            ["--include-operator-snapshots"],
            _PATCH_SNAPSHOTS,
            RuntimeError("snapshot boom"),
        ),
    ],
)
def test_loader_failure_marks_same_run_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fail_step: str,
    argv_extra: list[str],
    patch_target: str | None,
    side_effect: Exception | None,
) -> None:
    from contextlib import ExitStack

    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    finishes: list[dict[str, Any]] = []

    def _finish(*_a: Any, **kwargs: Any) -> None:
        finishes.append(kwargs)

    def _loader(cmd: list[str], _root: Path) -> int:
        joined = " ".join(cmd)
        if fail_step == "outbound_sidecars" and "sqlite_outbound_sidecars_to_postgres.py" in joined:
            return 2
        if fail_step == "mart_core" and "sqlite_mart_core_to_postgres.py" in joined:
            return 3
        return 0

    with ExitStack() as stack:
        stack.enter_context(patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])))
        stack.enter_context(patch(_PATCH_COUNTS, return_value=_sample_mirror_counts()))
        stack.enter_context(patch(_PATCH_START, return_value=55))
        stack.enter_context(patch(_PATCH_FINISH, side_effect=_finish))
        if fail_step == "classification":
            stack.enter_context(patch(_PATCH_CLASSIFY, side_effect=side_effect))
        else:
            stack.enter_context(patch(_PATCH_CLASSIFY, return_value={}))
        if fail_step == "commercial_purchase":
            stack.enter_context(patch(_PATCH_PURCHASE, side_effect=side_effect))
        else:
            stack.enter_context(patch(_PATCH_PURCHASE, return_value={}))
        if fail_step == "commercial_deals":
            stack.enter_context(patch(_PATCH_DEALS, side_effect=side_effect))
        else:
            stack.enter_context(patch(_PATCH_DEALS, return_value={}))
        if fail_step == "equipment_opportunities":
            stack.enter_context(patch(_PATCH_EQUIP, side_effect=side_effect))
        if fail_step == "warm_cases":
            stack.enter_context(patch(_PATCH_WARM, side_effect=side_effect))
        if fail_step == "operator_snapshots":
            stack.enter_context(patch(_PATCH_SNAPSHOTS, side_effect=side_effect))
        stack.enter_context(
            patch(
                "origenlab_email_pipeline.dashboard_postgres_sync.update_sync_run_details"
            )
        )
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), *argv_extra],
            repo_root=REPO,
            loader_runner=_loader,
        )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert len(finishes) == 1
    assert finishes[0]["status"] == "failed"
    assert finishes[0]["sync_run_id"] == 55
    assert finishes[0]["details"]["failed_loader"] == fail_step
    assert all(f["status"] != "success" for f in finishes)
    if fail_step == "outbound_sidecars":
        assert finishes[0]["details"]["completed_loaders"] == []
    if fail_step == "classification":
        assert "outbound_sidecars" in finishes[0]["details"]["completed_loaders"]
        assert "mart_core" in finishes[0]["details"]["completed_loaders"]
        assert "classification" not in finishes[0]["details"]["completed_loaders"]


def test_final_count_failure_marks_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    finishes: list[str] = []

    def _finish(*_a: Any, **kwargs: Any) -> None:
        finishes.append(kwargs["status"])

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, side_effect=RuntimeError("count boom")
    ), patch(_PATCH_START, return_value=1), patch(
        _PATCH_FINISH, side_effect=_finish
    ), patch(_PATCH_CLASSIFY, return_value={}), patch(_PATCH_PURCHASE, return_value={}):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is False
    assert finishes == ["failed"]


def test_start_failure_runs_no_loaders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    loaders = {"n": 0}

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, side_effect=RuntimeError("cannot insert running")), patch(
        _PATCH_FINISH
    ) as mock_finish, patch(_PATCH_CLASSIFY) as mock_c, patch(_PATCH_PURCHASE) as mock_p:
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=lambda _c, _r: loaders.__setitem__("n", loaders["n"] + 1) or 0,
        )

    assert result["ok"] is False
    assert loaders["n"] == 0
    mock_c.assert_not_called()
    mock_p.assert_not_called()
    mock_finish.assert_not_called()
    assert any("running sync lifecycle" in e for e in result["errors"])


def test_terminal_success_publish_failure_does_not_claim_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, return_value=3), patch(
        _PATCH_FINISH, side_effect=RuntimeError("finish boom")
    ), patch(_PATCH_CLASSIFY, return_value={}), patch(_PATCH_PURCHASE, return_value={}):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert any("terminal success" in e for e in result["errors"])


def test_terminal_failure_publish_failure_keeps_primary_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, return_value=3), patch(
        _PATCH_FINISH, side_effect=RuntimeError("finish boom")
    ), patch(
        _PATCH_CLASSIFY, side_effect=RuntimeError("primary classification failure")
    ), patch(_PATCH_PURCHASE, return_value={}):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db)],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is False
    assert result["errors"][0] == "primary classification failure"
    assert any("terminal failure" in e for e in result["errors"])


def test_dry_run_writes_no_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START) as mock_start, patch(_PATCH_FINISH) as mock_finish:
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--dry-run"],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["status"] == "dry_run"
    mock_start.assert_not_called()
    mock_finish.assert_not_called()


@pytest.mark.parametrize("only", ["outbound", "mart", "canonical"])
def test_only_modes_selected_completed_accuracy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, only: str
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    finishes: list[dict[str, Any]] = []

    def _finish(*_a: Any, **kwargs: Any) -> None:
        finishes.append(kwargs["details"])

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, return_value=2), patch(
        _PATCH_FINISH, side_effect=_finish
    ), patch(_PATCH_CLASSIFY, return_value={}), patch(_PATCH_PURCHASE, return_value={}):
        result = run_dashboard_mirror_sync(
            ["--sqlite-db", str(db), "--only", only],
            repo_root=REPO,
            loader_runner=lambda _c, _r: 0,
        )

    assert result["ok"] is True
    assert finishes[0]["selected_loaders"] == finishes[0]["completed_loaders"]
    assert "classification" in finishes[0]["completed_loaders"]
    if only == "outbound":
        assert "outbound_sidecars" in finishes[0]["selected_loaders"]
        assert "mart_core" not in finishes[0]["selected_loaders"]
    else:
        assert "mart_core" in finishes[0]["selected_loaders"]
        assert "outbound_sidecars" not in finishes[0]["selected_loaders"]


def test_start_and_finish_sql_one_row_lifecycle() -> None:
    cur = MagicMock()
    cur.fetchone.return_value = (101,)

    class _Ctx:
        def __enter__(self) -> MagicMock:
            return cur

        def __exit__(self, *args: object) -> None:
            return None

    conn = MagicMock()
    conn.cursor.return_value = _Ctx()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    started = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 7, 23, 12, 5, tzinfo=timezone.utc)

    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg") as mock_pg,
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            return_value=True,
        ),
    ):
        mock_pg.connect.return_value = conn
        sync_id = start_sync_run(
            "postgresql://u:p@127.0.0.1/scratch",
            sqlite_path=Path("/data/emails.sqlite"),
            postgres_url_redacted="postgresql://u:***@127.0.0.1/scratch",
            started_at=started,
            details={"selected_loaders": ["outbound_sidecars"], "completed_loaders": []},
            counts=None,
        )
        assert sync_id == 101
        finish_sync_run(
            "postgresql://u:p@127.0.0.1/scratch",
            sync_run_id=101,
            sqlite_path=Path("/data/emails.sqlite"),
            postgres_url_redacted="postgresql://u:***@127.0.0.1/scratch",
            status="success",
            started_at=started,
            finished_at=finished,
            counts={"canonical_contact_count": 1},
            error_message=None,
            details={
                "selected_loaders": ["outbound_sidecars"],
                "completed_loaders": ["outbound_sidecars"],
            },
        )

    sql = " ".join(str(c) for c in cur.execute.call_args_list)
    assert "INSERT INTO reporting.dashboard_sync_run" in sql
    assert "'running'" in sql or "running" in sql
    assert "UPDATE reporting.dashboard_sync_run" in sql
    assert DASHBOARD_SYNC_KV_KEY in sql
    assert sql.count("INSERT INTO reporting.dashboard_sync_run") == 1


def test_cli_exit_code_compatible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from origenlab_email_pipeline.dashboard_postgres_sync import main

    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db, with_mart_rows=True)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, return_value=1), patch(_PATCH_FINISH), patch(
        _PATCH_CLASSIFY, return_value={}
    ), patch(_PATCH_PURCHASE, return_value={}), patch(
        "origenlab_email_pipeline.dashboard_postgres_sync.run_loader_subprocess",
        return_value=0,
    ):
        assert main(["--sqlite-db", str(db)]) == 0

    with patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])), patch(
        _PATCH_COUNTS, return_value=_sample_mirror_counts()
    ), patch(_PATCH_START, return_value=1), patch(_PATCH_FINISH), patch(
        _PATCH_CLASSIFY, side_effect=RuntimeError("x")
    ), patch(_PATCH_PURCHASE, return_value={}), patch(
        "origenlab_email_pipeline.dashboard_postgres_sync.run_loader_subprocess",
        return_value=0,
    ):
        assert main(["--sqlite-db", str(db)]) == 1
