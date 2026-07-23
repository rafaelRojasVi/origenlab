"""Compare-and-set lifecycle consistency for dashboard mirror runs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from origenlab_email_pipeline.contacto_gmail_source import CONTACTO_GMAIL_SOURCE_PREFIX
from origenlab_email_pipeline.dashboard_postgres_sync import (
    EXPECTED_ALEMBIC_HEAD,
    LifecycleConsistencyError,
    SyncRunHandle,
    finish_sync_run,
    format_summary_text,
    run_dashboard_mirror_sync,
    sanitize_lifecycle_error,
    start_sync_run,
    update_sync_run_details,
)
from origenlab_email_pipeline.db import init_schema

REPO = Path(__file__).resolve().parents[1]
STARTED = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
FINISHED = datetime(2026, 7, 23, 12, 5, tzinfo=timezone.utc)
PG_URL = "postgresql://u:notasecret-pw@127.0.0.1:5432/scratch"
PG_REDACTED = "postgresql://u:***@127.0.0.1:5432/scratch"
SQLITE = Path("/home/rafael/data/emails.sqlite")

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


def _setup_sqlite(db_path: Path) -> None:
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


@dataclass
class _FakeRun:
    status: str
    details: Any = None
    error_message: str | None = None


@dataclass
class _LifecycleStore:
    """In-memory reporting + KV adapter driven by SQL shape."""

    reporting: bool = True
    kv: bool = True
    runs: dict[int, _FakeRun] = field(default_factory=dict)
    next_id: int = 1
    kv_payload: dict[str, Any] | None = None
    commits: int = 0
    sql_log: list[str] = field(default_factory=list)
    params_log: list[tuple[Any, ...]] = field(default_factory=list)
    last_returning: Any = None

    def table_exists(self, *, schema: str, table: str) -> bool:
        if schema == "reporting" and table == "dashboard_sync_run":
            return self.reporting
        if schema == "ops" and table == "pipeline_kv":
            return self.kv
        return False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.sql_log.append(" ".join(sql.split()))
        self.params_log.append(tuple(params or ()))
        sql_n = " ".join(sql.split())
        params_t = tuple(params or ())

        if "INSERT INTO reporting.dashboard_sync_run" in sql_n:
            run_id = self.next_id
            self.next_id += 1
            self.runs[run_id] = _FakeRun(status="running")
            self.last_returning = (run_id,)
            return

        if (
            "UPDATE reporting.dashboard_sync_run" in sql_n
            and "SET finished_at" in sql_n
            and "AND status = 'running'" in sql_n
            and "RETURNING id" in sql_n
        ):
            run_id = int(params_t[-1])
            run = self.runs.get(run_id)
            if run is None or run.status != "running":
                self.last_returning = None
                return
            run.status = str(params_t[1])
            run.error_message = params_t[-3]  # type: ignore[assignment]
            self.last_returning = (run_id,)
            return

        if (
            "UPDATE reporting.dashboard_sync_run" in sql_n
            and "SET details_json" in sql_n
            and "AND status = 'running'" in sql_n
            and "RETURNING id" in sql_n
        ):
            run_id = int(params_t[-1])
            run = self.runs.get(run_id)
            if run is None or run.status != "running":
                self.last_returning = None
                return
            run.details = params_t[0]
            self.last_returning = (run_id,)
            return

        if "INSERT INTO ops.pipeline_kv" in sql_n:
            payload = params_t[1]
            if hasattr(payload, "obj"):
                payload = payload.obj
            self.kv_payload = dict(payload)
            self.last_returning = None
            return

        self.last_returning = None

    def fetchone(self) -> Any:
        return self.last_returning


def _connect_store(store: _LifecycleStore) -> tuple[MagicMock, Any]:
    cur = MagicMock()
    cur.execute.side_effect = store.execute
    cur.fetchone.side_effect = store.fetchone

    class _Ctx:
        def __enter__(self) -> MagicMock:
            return cur

        def __exit__(self, *args: object) -> None:
            return None

    conn = MagicMock()
    conn.cursor.return_value = _Ctx()
    conn.commit.side_effect = lambda: setattr(store, "commits", store.commits + 1)
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    mock_pg = MagicMock()
    mock_pg.connect.return_value = conn

    def _exists(_cur: Any, *, schema: str, table: str) -> bool:
        return store.table_exists(schema=schema, table=table)

    return mock_pg, _exists


def _finish_kwargs(
    handle: SyncRunHandle,
    *,
    status: str = "success",
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "handle": handle,
        "sqlite_path": SQLITE,
        "postgres_url_redacted": PG_REDACTED,
        "status": status,
        "started_at": STARTED,
        "finished_at": FINISHED,
        "counts": {"canonical_contact_count": 1},
        "error_message": error_message,
        "details": {
            "selected_loaders": ["mart_core"],
            "completed_loaders": ["mart_core"],
        },
    }


def test_cas_running_to_success_returns_expected_id() -> None:
    store = _LifecycleStore(runs={7: _FakeRun(status="running")})
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        finish_sync_run(PG_URL, **_finish_kwargs(SyncRunHandle(7, True, True)))
    assert store.runs[7].status == "success"
    assert store.kv_payload is not None
    assert store.kv_payload["status"] == "success"
    assert store.kv_payload["sync_run_id"] == 7
    assert any(
        "AND status = 'running'" in s and "RETURNING id" in s for s in store.sql_log
    )
    assert not any("INSERT INTO reporting.dashboard_sync_run" in s for s in store.sql_log)


def test_cas_running_to_failed_returns_expected_id() -> None:
    store = _LifecycleStore(runs={8: _FakeRun(status="running")})
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        finish_sync_run(
            PG_URL,
            **_finish_kwargs(
                SyncRunHandle(8, True, True),
                status="failed",
                error_message="Loader mart_core failed with exit code 3",
            ),
        )
    assert store.runs[8].status == "failed"
    assert store.kv_payload is not None
    assert store.kv_payload["status"] == "failed"
    assert store.kv_payload["error_message"] == "Loader mart_core failed with exit code 3"


@pytest.mark.parametrize(
    "initial",
    ["success", "failed", None],
    ids=["already-success", "already-failed", "missing-row"],
)
@pytest.mark.parametrize("attempt", ["success", "failed"])
def test_cas_zero_row_terminal_update_fails_without_kv(
    initial: str | None, attempt: str
) -> None:
    runs: dict[int, _FakeRun] = {}
    if initial is not None:
        runs[9] = _FakeRun(status=initial)
    store = _LifecycleStore(runs=runs)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="affected no running row"):
            finish_sync_run(
                PG_URL,
                **_finish_kwargs(SyncRunHandle(9, True, True), status=attempt),
            )
    assert store.kv_payload is None
    assert store.commits == 0
    if initial is not None:
        assert store.runs[9].status == initial


def test_stale_sync_run_id_fails() -> None:
    store = _LifecycleStore(runs={1: _FakeRun(status="running")})
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="affected no running row"):
            finish_sync_run(PG_URL, **_finish_kwargs(SyncRunHandle(999, True, True)))
    assert store.kv_payload is None
    assert store.runs[1].status == "running"


def test_sync_run_id_none_with_reporting_present_fails_closed() -> None:
    store = _LifecycleStore()
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="sync_run_id is missing"):
            finish_sync_run(
                PG_URL,
                **_finish_kwargs(SyncRunHandle(None, True, True)),
            )
    assert store.kv_payload is None
    assert store.sql_log == []


def test_reporting_appearing_mid_run_does_not_insert_terminal_row() -> None:
    store = _LifecycleStore(reporting=True, kv=True)
    handle = SyncRunHandle(None, reporting_enabled=False, kv_enabled=True)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="topology changed"):
            finish_sync_run(PG_URL, **_finish_kwargs(handle))
    assert store.runs == {}
    assert store.kv_payload is None
    assert not any("INSERT INTO reporting.dashboard_sync_run" in s for s in store.sql_log)


def test_reporting_disappearing_mid_run_does_not_publish_kv_success() -> None:
    store = _LifecycleStore(reporting=False, kv=True, runs={})
    handle = SyncRunHandle(11, reporting_enabled=True, kv_enabled=True)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="topology changed"):
            finish_sync_run(PG_URL, **_finish_kwargs(handle))
    assert store.kv_payload is None


def test_reporting_and_kv_terminal_updates_one_transaction() -> None:
    store = _LifecycleStore(runs={12: _FakeRun(status="running")})
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        finish_sync_run(PG_URL, **_finish_kwargs(SyncRunHandle(12, True, True)))
    assert store.commits == 1
    assert mock_pg.connect.call_count == 1
    assert store.runs[12].status == "success"
    assert store.kv_payload is not None
    assert store.kv_payload["status"] == "success"


def test_reporting_only_terminal_update_succeeds() -> None:
    store = _LifecycleStore(
        reporting=True, kv=False, runs={13: _FakeRun(status="running")}
    )
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        finish_sync_run(PG_URL, **_finish_kwargs(SyncRunHandle(13, True, False)))
    assert store.runs[13].status == "success"
    assert store.kv_payload is None


def test_explicit_kv_only_lifecycle_succeeds_without_run_row() -> None:
    store = _LifecycleStore(reporting=False, kv=True)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        handle = start_sync_run(
            PG_URL,
            sqlite_path=SQLITE,
            postgres_url_redacted=PG_REDACTED,
            started_at=STARTED,
            details={"selected_loaders": ["mart_core"], "completed_loaders": []},
        )
        assert handle == SyncRunHandle(None, False, True)
        assert store.runs == {}
        assert store.kv_payload is not None
        assert store.kv_payload["status"] == "running"
        finish_sync_run(PG_URL, **_finish_kwargs(handle, status="success"))
    assert store.runs == {}
    assert store.kv_payload is not None
    assert store.kv_payload["status"] == "success"
    assert store.kv_payload["sync_run_id"] is None
    assert not any("INSERT INTO reporting.dashboard_sync_run" in s for s in store.sql_log)


def test_neither_table_compatibility_is_noop() -> None:
    store = _LifecycleStore(reporting=False, kv=False)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        handle = start_sync_run(
            PG_URL,
            sqlite_path=SQLITE,
            postgres_url_redacted=PG_REDACTED,
            started_at=STARTED,
            details={"selected_loaders": [], "completed_loaders": []},
        )
        assert handle == SyncRunHandle(None, False, False)
        finish_sync_run(PG_URL, **_finish_kwargs(handle, status="success"))
    assert store.runs == {}
    assert store.kv_payload is None
    assert store.sql_log == []


def test_mid_run_details_requires_running_row() -> None:
    store = _LifecycleStore(runs={14: _FakeRun(status="success")})
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="mid-run details"):
            update_sync_run_details(
                PG_URL,
                SyncRunHandle(14, True, True),
                {"completed_loaders": ["outbound_sidecars"]},
                started_at=STARTED,
                sqlite_path=SQLITE,
                postgres_url_redacted=PG_REDACTED,
            )
    assert store.kv_payload is None


def test_mid_run_zero_row_does_not_rewrite_kv() -> None:
    store = _LifecycleStore(runs={})
    store.kv_payload = {"status": "success", "sync_run_id": 15}
    prior = dict(store.kv_payload)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="mid-run details"):
            update_sync_run_details(
                PG_URL,
                SyncRunHandle(15, True, True),
                {"completed_loaders": ["mart_core"]},
                started_at=STARTED,
                sqlite_path=SQLITE,
                postgres_url_redacted=PG_REDACTED,
            )
    assert store.kv_payload == prior


def test_mid_run_cannot_regress_terminal_kv_to_running() -> None:
    store = _LifecycleStore(runs={16: _FakeRun(status="failed")})
    store.kv_payload = {"status": "failed", "sync_run_id": 16}
    prior = dict(store.kv_payload)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        with pytest.raises(LifecycleConsistencyError, match="mid-run details"):
            update_sync_run_details(
                PG_URL,
                SyncRunHandle(16, True, True),
                {"completed_loaders": ["mart_core"]},
                started_at=STARTED,
                sqlite_path=SQLITE,
                postgres_url_redacted=PG_REDACTED,
            )
    assert store.kv_payload == prior


def test_terminal_sql_contains_running_predicate_and_returning() -> None:
    store = _LifecycleStore(runs={17: _FakeRun(status="running")})
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        finish_sync_run(PG_URL, **_finish_kwargs(SyncRunHandle(17, True, True)))
    terminal = [s for s in store.sql_log if "SET finished_at" in s][0]
    assert "AND status = 'running'" in terminal
    assert "RETURNING id" in terminal
    assert "INSERT INTO reporting.dashboard_sync_run" not in terminal


def test_start_finish_one_history_row_per_invocation() -> None:
    store = _LifecycleStore()
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        handle = start_sync_run(
            PG_URL,
            sqlite_path=SQLITE,
            postgres_url_redacted=PG_REDACTED,
            started_at=STARTED,
            details={"selected_loaders": ["mart_core"], "completed_loaders": []},
        )
        finish_sync_run(PG_URL, **_finish_kwargs(handle))
    assert len(store.runs) == 1
    assert list(store.runs.values())[0].status == "success"
    assert (
        sum("INSERT INTO reporting.dashboard_sync_run" in s for s in store.sql_log) == 1
    )


def test_sanitize_lifecycle_error_redacts_url_path_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", "notasecret-token")
    msg = (
        "connect failed postgresql://u:notasecret-pw@127.0.0.1:5432/scratch "
        f"sqlite={SQLITE} Bearer notasecret-token password=notasecret-pw"
    )
    cleaned = sanitize_lifecycle_error(msg, postgres_url=PG_URL, sqlite_path=SQLITE)
    assert "notasecret-pw" not in cleaned
    assert "notasecret-token" not in cleaned
    assert str(SQLITE) not in cleaned
    assert "postgresql://u:notasecret-pw@" not in cleaned
    assert "Loader mart_core failed with exit code 3" in sanitize_lifecycle_error(
        "Loader mart_core failed with exit code 3",
        postgres_url=PG_URL,
        sqlite_path=SQLITE,
    )


def test_persisted_failure_error_is_sanitized() -> None:
    store = _LifecycleStore(runs={18: _FakeRun(status="running")})
    raw = f"boom {PG_URL} path={SQLITE}"
    cleaned = sanitize_lifecycle_error(raw, postgres_url=PG_URL, sqlite_path=SQLITE)
    mock_pg, exists = _connect_store(store)
    with (
        patch("origenlab_email_pipeline.dashboard_postgres_sync.psycopg", mock_pg),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.pg_table_exists",
            side_effect=exists,
        ),
    ):
        finish_sync_run(
            PG_URL,
            **_finish_kwargs(
                SyncRunHandle(18, True, True),
                status="failed",
                error_message=cleaned,
            ),
        )
    assert store.runs[18].error_message == cleaned
    assert "notasecret-pw" not in str(store.runs[18].error_message)
    assert store.kv_payload is not None
    assert "notasecret-pw" not in store.kv_payload["error_message"]
    err_params = [p for p in store.params_log if cleaned in p]
    assert err_params
    assert all("notasecret-pw" not in str(p) for p in err_params)


def test_cli_formatted_output_sanitizes_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from origenlab_email_pipeline.dashboard_postgres_sync import main

    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", PG_URL)

    with (
        patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])),
        patch(_PATCH_COUNTS, return_value=_sample_mirror_counts()),
        patch(_PATCH_START, return_value=SyncRunHandle(1, True, True)),
        patch(_PATCH_FINISH),
        patch(
            _PATCH_CLASSIFY,
            side_effect=RuntimeError(f"classification failed using {PG_URL}"),
        ),
        patch(_PATCH_PURCHASE, return_value={}),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.run_loader_subprocess",
            return_value=0,
        ),
    ):
        assert main(["--sqlite-db", str(db)]) == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "notasecret-pw" not in combined
    assert "classification failed" in combined


def test_returned_errors_are_sanitized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "emails.sqlite"
    _setup_sqlite(db)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", PG_URL)

    with (
        patch(_PATCH_PG, return_value=(EXPECTED_ALEMBIC_HEAD, [])),
        patch(_PATCH_COUNTS, return_value=_sample_mirror_counts()),
        patch(_PATCH_START, return_value=SyncRunHandle(1, True, True)),
        patch(_PATCH_FINISH),
        patch(
            _PATCH_CLASSIFY,
            side_effect=RuntimeError(f"classification failed using {PG_URL}"),
        ),
        patch(_PATCH_PURCHASE, return_value={}),
        patch(
            "origenlab_email_pipeline.dashboard_postgres_sync.run_loader_subprocess",
            return_value=0,
        ),
    ):
        result = run_dashboard_mirror_sync(["--sqlite-db", str(db)], repo_root=REPO)
    assert all("notasecret-pw" not in e for e in result["errors"])
    assert any("classification failed" in e for e in result["errors"])
    assert "notasecret-pw" not in format_summary_text(result)
