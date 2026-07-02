"""Tests for equipment opportunity Postgres mirror loader (DB-2A)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from origenlab_email_pipeline import equipment_opportunity_mirror as mirror
from origenlab_email_pipeline.active_current_manifest import resolve_equipment_operator_queue_csv

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "sync" / "load_equipment_opportunity_mirror.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("load_equipment_opportunity_mirror_cli", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

OPERATOR_HEADER = (
    "priority_rank,codigo_licitacion,buyer,region,close_date,equipment_category,"
    "item_description,next_action,contact_status,safe_channel,supplier_needed,"
    "supplier_contact,gmail_prior_thread,outreach_state,operator_note\n"
)


def _postgres_test_url() -> str | None:
    return (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip() or None


def _postgres_test_url_ready() -> str | None:
    url = _postgres_test_url()
    if not url:
        return None
    try:
        import psycopg

        from origenlab_email_pipeline.mart_core_postgres_migrate import normalize_postgres_url

        with psycopg.connect(normalize_postgres_url(url), connect_timeout=2):
            pass
        return url
    except Exception:
        return None


def _write_queue_csv(path: Path, rows: list[str]) -> None:
    path.write_text(OPERATOR_HEADER + "".join(rows), encoding="utf-8")


def _write_manifest(active: Path, *, canonical: list[str] | None = None, stale: list[str] | None = None) -> None:
    manifest = {
        "campaign_mode": "equipment_first",
        "canonical_files": canonical or ["equipment_first_operator_queue_20260518.csv"],
        "stale_files": [{"path": p, "reason": "test"} for p in (stale or [])],
    }
    (active / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def active_workspace(tmp_path: Path) -> Path:
    active = tmp_path / "current"
    active.mkdir()
    _write_manifest(active)
    _write_queue_csv(
        active / "equipment_first_operator_queue_20260518.csv",
        [
            "1,CODE-A,Buyer,RM,04/06/2026 17:00:00,centrifuge,Item,quote_now,x,bid,yes,,,,note\n",
            "2,CODE-B,Buyer B,RM,22/05/2026,balance,Item B,quote_now,x,bid,no,,,,note2\n",
        ],
    )
    return active


def test_parse_close_at_dd_mm_yyyy_hms() -> None:
    dt = mirror.parse_close_at("04/06/2026 17:00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.month == 6 and dt.day == 4
    assert dt.hour == 17 and dt.minute == 0


def test_parse_close_at_iso_formats() -> None:
    iso_full = mirror.parse_close_at("2026-06-17T19:00:00")
    assert iso_full is not None
    assert iso_full.year == 2026 and iso_full.month == 6 and iso_full.day == 17
    assert iso_full.hour == 19 and iso_full.minute == 0

    iso_minutes = mirror.parse_close_at("2026-06-17T19:00")
    assert iso_minutes is not None
    assert iso_minutes.hour == 19

    iso_space = mirror.parse_close_at("2026-06-17 19:00:00")
    assert iso_space is not None
    assert iso_space.hour == 19

    iso_z = mirror.parse_close_at("2026-06-17T19:00:00Z")
    assert iso_z is not None
    assert iso_z.tzinfo is not None


def test_parse_priority_rank_empty_is_null() -> None:
    assert mirror.parse_priority_rank("") is None
    assert mirror.parse_priority_rank("   ") is None
    assert mirror.parse_priority_rank("3") == 3


def test_derive_opportunity_key_uses_source_and_codigo() -> None:
    row = {"codigo_licitacion": "2777-10-LE26", "source": "chilecompra_api"}
    assert mirror.derive_opportunity_key(row) == "equipment:chilecompra_api:2777-10-le26"


def test_derive_opportunity_key_normalizes_source_slug() -> None:
    row = {"codigo_licitacion": "LP-1", "source": " ChileCompra API "}
    assert mirror.derive_opportunity_key(row) == "equipment:chilecompra_api:lp-1"


def test_derive_opportunity_key_missing_source_uses_equipment_queue() -> None:
    row = {"codigo_licitacion": "LP-001"}
    assert mirror.derive_opportunity_key(row) == "equipment:equipment_queue:lp-001"


def test_row_to_opportunity_values_includes_opportunity_key() -> None:
    values = mirror.row_to_opportunity_values({"codigo_licitacion": "LP-9", "source": "chilecompra_api"})
    assert values["opportunity_key"] == "equipment:chilecompra_api:lp-9"


def test_extra_json_contains_non_operator_fields() -> None:
    row = {
        "priority_rank": "1",
        "codigo_licitacion": "X",
        "buyer": "B",
        "region": "R",
        "close_date": "",
        "equipment_category": "c",
        "item_description": "d",
        "next_action": "quote_now",
        "contact_status": "x",
        "safe_channel": "s",
        "supplier_needed": "y",
        "supplier_contact": "contact@x.cl",
        "gmail_prior_thread": "none",
        "outreach_state": "n/a",
        "operator_note": "n",
        "legacy_fit_score": "99",
    }
    extra = mirror.build_extra_json(row)
    assert extra["supplier_contact"] == "contact@x.cl"
    assert extra["gmail_prior_thread"] == "none"
    assert extra["outreach_state"] == "n/a"
    assert extra["legacy_fit_score"] == "99"


def test_buyer_opportunity_crosscheck_path_rejected(active_workspace: Path) -> None:
    cross = active_workspace / "buyer_opportunity_crosscheck_20260518.csv"
    cross.write_text("priority_rank,codigo_licitacion\n1,X\n", encoding="utf-8")
    with pytest.raises(ValueError, match="crosscheck"):
        mirror.assert_queue_path_allowed(cross)


def test_resolve_prefers_manifest_canonical_not_crosscheck(active_workspace: Path) -> None:
    manifest = json.loads((active_workspace / "manifest.json").read_text(encoding="utf-8"))
    resolved = resolve_equipment_operator_queue_csv(active_workspace, manifest)
    assert resolved is not None
    assert "crosscheck" not in resolved.name


def test_preview_dry_run_defaults_to_typed_rows_without_connecting_postgres(
    active_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"connect": False}

    def _boom(*args: Any, **kwargs: Any) -> None:
        called["connect"] = True
        raise AssertionError("psycopg.connect should not run on dry-run")

    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=_boom))
    summary = mirror.preview_load(active_workspace)
    assert summary["dry_run"] is True
    assert summary["applied"] is False
    assert summary["row_count"] == 2
    assert summary["source_input"] == "typed_rows"
    assert summary["source_kind"] == "typed_read_model"
    assert summary["artifact_basename"] == "equipment_first_operator_queue_20260518.csv"
    assert "/" not in summary["artifact_basename"]
    assert summary["canonical_reason"] == "typed_rows_from_manifest_canonical"
    assert summary["csv_export_path"].endswith("equipment_first_operator_queue_20260518.csv")
    assert called["connect"] is False


def test_legacy_preview_still_available(active_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"connect": False}

    def _boom(*args: Any, **kwargs: Any) -> None:
        called["connect"] = True
        raise AssertionError("psycopg.connect should not run on dry-run")

    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=_boom))
    summary = mirror.preview_load(active_workspace, use_direct_rows=False)
    assert summary["source_kind"] == "csv_artifact"
    assert summary["artifact_basename"] == "equipment_first_operator_queue_20260518.csv"
    assert summary["canonical_reason"] == "manifest_canonical"
    assert called["connect"] is False


def test_derive_source_artifact_metadata_resolved_active_queue(tmp_path: Path) -> None:
    active = tmp_path / "current"
    active.mkdir()
    _write_queue_csv(
        active / "equipment_first_operator_queue_20260518.csv",
        ["1,CODE-A,B,RM,,c,d,quote_now,x,s,y,,,,n\n"],
    )
    manifest: dict[str, Any] = {}
    resolved = active / "equipment_first_operator_queue_20260518.csv"
    meta = mirror.derive_source_artifact_metadata(manifest, resolved, active)
    assert meta["source_kind"] == "csv_artifact"
    assert meta["artifact_basename"] == "equipment_first_operator_queue_20260518.csv"
    assert meta["canonical_reason"] == "resolved_active_current_queue"


def test_derive_source_artifact_metadata_non_canonical_source(active_workspace: Path) -> None:
    other_csv = active_workspace / "equipment_first_operator_queue_20260501.csv"
    _write_queue_csv(other_csv, ["1,OLD-1,B,RM,,c,d,quote_now,x,s,y,,,,n\n"])
    manifest = json.loads((active_workspace / "manifest.json").read_text(encoding="utf-8"))
    meta = mirror.derive_source_artifact_metadata(manifest, other_csv, active_workspace)
    assert meta["source_kind"] == "csv_artifact"
    assert meta["artifact_basename"] == "equipment_first_operator_queue_20260501.csv"
    assert meta["canonical_reason"] is None


def test_duplicate_codigo_aborts_apply(active_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dup_csv = active_workspace / "equipment_first_operator_queue_20260599.csv"
    _write_queue_csv(
        dup_csv,
        [
            "1,DUP-1,B,RM,,c,d,quote_now,x,s,y,,,,n\n",
            "2,DUP-1,B2,RM,,c,d,quote_now,x,s,y,,,,n\n",
        ],
    )
    summary = mirror.apply_load(
        "postgresql://u:p@127.0.0.1/db",
        active_workspace,
        csv_path=dup_csv,
        updated_by="tester",
        reason="unit",
    )
    assert summary["applied"] is False
    assert summary["duplicate_codigos"] == ["DUP-1"]
    assert summary["error"] == "duplicate_codigo_licitacion_in_csv_export_rows"


class _RecordingCursor:
    def __init__(self, *, existing_source_id: int | None = None) -> None:
        self.existing_source_id = existing_source_id
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []
        self._fetch: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.statements.append((sql, params))
        if "SELECT id FROM commercial.equipment_opportunity_source" in sql:
            self._fetch = (self.existing_source_id,) if self.existing_source_id is not None else None
        elif "RETURNING id" in sql:
            self._fetch = (99,)
        else:
            self._fetch = None

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._fetch

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class _RecordingConn:
    def __init__(self, *, existing_source_id: int | None = None) -> None:
        self.cur = _RecordingCursor(existing_source_id=existing_source_id)
        self.committed = False

    def cursor(self) -> _RecordingCursor:
        return self.cur

    def commit(self) -> None:
        self.committed = True

    def __enter__(self) -> _RecordingConn:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_same_csv_apply_idempotently_promotes_canonical_without_reload(
    active_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _RecordingConn(existing_source_id=1)
    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=lambda *a, **k: conn))

    summary = mirror.apply_load(
        "postgresql://u:p@127.0.0.1/db",
        active_workspace,
        updated_by="op",
        reason="reapply",
        use_direct_rows=False,
    )
    assert summary["applied"] is False
    assert summary["idempotent"] == "canonical_source_already_loaded"
    assert summary["is_canonical"] is True
    assert summary["existing_source_id"] == 1
    assert summary["source_id"] == 1
    assert conn.committed is True
    sqls = [s for s, _ in conn.cur.statements]
    assert any("SET is_canonical = TRUE" in s for s in sqls)
    assert not any("INSERT INTO commercial.equipment_opportunity_source" in s for s in sqls)


def test_non_active_queue_source_already_loaded_still_errors(
    active_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_csv = active_workspace / "equipment_first_operator_queue_20260501.csv"
    _write_queue_csv(
        other_csv,
        ["1,OLD-1,B,RM,,c,d,quote_now,x,s,y,,,,n\n"],
    )
    (active_workspace / "manifest.json").write_text(
        json.dumps(
            {
                "campaign_mode": "equipment_first",
                "canonical_files": ["equipment_first_operator_queue_20260518.csv"],
                "stale_files": [],
            }
        ),
        encoding="utf-8",
    )
    conn = _RecordingConn(existing_source_id=9)
    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=lambda *a, **k: conn))

    summary = mirror.apply_load(
        "postgresql://u:p@127.0.0.1/db",
        active_workspace,
        csv_path=other_csv,
        updated_by="op",
        reason="stale reapply",
        use_direct_rows=False,
    )
    assert summary["applied"] is False
    assert summary["error"] == "source_already_loaded"
    assert conn.committed is False


def test_replace_source_reuses_existing_source_id(
    active_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _RecordingConn(existing_source_id=42)
    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=lambda *a, **k: conn))
    monkeypatch.setattr(mirror, "Json", lambda obj: obj)

    summary = mirror.apply_load(
        "postgresql://u:p@127.0.0.1/db",
        active_workspace,
        updated_by="op",
        reason="replace",
        replace_source=True,
        use_direct_rows=False,
    )
    assert summary["applied"] is True
    assert summary["source_id"] == 42
    assert summary["replaced_source"] is True
    assert conn.committed is True
    sqls = [s for s, _ in conn.cur.statements]
    assert not any("INSERT INTO commercial.equipment_opportunity_source" in s for s in sqls)
    assert any("UPDATE commercial.equipment_opportunity_source" in s for s in sqls)
    delete_params = [p for s, p in conn.cur.statements if "DELETE FROM commercial.equipment_opportunity" in s]
    assert delete_params == [(42,)]
    assert not any("TRUNCATE" in s.upper() for s, _ in conn.cur.statements)


def test_replace_source_keeps_canonical_flag(active_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RecordingConn(existing_source_id=7)
    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=lambda *a, **k: conn))
    monkeypatch.setattr(mirror, "Json", lambda obj: obj)

    summary = mirror.apply_load(
        "postgresql://u:p@127.0.0.1/db",
        active_workspace,
        updated_by="op",
        reason="replace canonical",
        replace_source=True,
        use_direct_rows=False,
    )
    assert summary["is_canonical"] is True
    sqls = [s for s, _ in conn.cur.statements]
    assert any("SET is_canonical = TRUE" in s for s in sqls)


def test_preview_reports_existing_source_when_pg_url_set(
    active_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _LookupCursor(_RecordingCursor):
        def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
            if "SELECT id FROM commercial.equipment_opportunity_source" in sql:
                self._fetch = (5,)
            else:
                super().execute(sql, params)

    conn = _RecordingConn()
    conn.cur = _LookupCursor(existing_source_id=5)
    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=lambda *a, **k: conn))

    summary = mirror.preview_load(
        active_workspace,
        pg_url="postgresql://u:p@127.0.0.1/db",
        replace_source=False,
        use_direct_rows=False,
    )
    assert summary["existing_source_id"] == 5
    assert summary["would_fail_without_replace"] is True
    assert summary["would_replace_source"] is False
    assert summary["would_insert_source"] is False

    summary_replace = mirror.preview_load(
        active_workspace,
        pg_url="postgresql://u:p@127.0.0.1/db",
        replace_source=True,
        use_direct_rows=False,
    )
    assert summary_replace["would_replace_source"] is True
    assert summary_replace["would_fail_without_replace"] is False


def test_cli_apply_source_already_loaded_exit_code(
    active_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_mod = _load_cli_module()

    monkeypatch.setattr(
        cli_mod,
        "apply_load",
        lambda *a, **k: {
            "applied": False,
            "error": "source_already_loaded",
            "csv_path": str(active_workspace / "equipment_first_operator_queue_20260518.csv"),
            "existing_source_id": 1,
            "hint": mirror._SOURCE_ALREADY_LOADED_HINT,
        },
    )
    code = cli_mod.main(
        [
            "--active-current",
            str(active_workspace),
            "--apply",
            "--updated-by",
            "op",
            "--reason",
            "reapply",
            "--postgres-url",
            "postgresql://u:p@127.0.0.1/db",
        ]
    )
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["error"] == "source_already_loaded"
    assert data["existing_source_id"] == 1


def test_apply_writes_typed_source_and_rows(active_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _RecordingConn()
    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=lambda *a, **k: conn))
    monkeypatch.setattr(mirror, "Json", lambda obj: obj)

    summary = mirror.apply_load(
        "postgresql+psycopg://u:p@127.0.0.1/db",
        active_workspace,
        updated_by="op",
        reason="mirror test",
    )
    assert summary["applied"] is True
    assert summary["is_canonical"] is True
    assert summary["source_id"] == 99
    assert summary["rows_inserted"] == 2
    assert summary["source_input"] == "typed_rows"
    assert summary["source_kind"] == "typed_read_model"
    assert summary["artifact_basename"] == "equipment_first_operator_queue_20260518.csv"
    assert summary["canonical_reason"] == "typed_rows_from_manifest_canonical"
    assert summary["legacy_csv_loader_used"] is False
    assert conn.committed is True
    sqls = [s for s, _ in conn.cur.statements]
    assert any("INSERT INTO commercial.equipment_opportunity_source" in s for s in sqls)
    insert_params = [
        p
        for s, p in conn.cur.statements
        if "INSERT INTO commercial.equipment_opportunity_source" in s
    ][0]
    assert insert_params[-3] == "typed_read_model"
    assert insert_params[-2] == "equipment_first_operator_queue_20260518.csv"
    assert insert_params[-1] == "typed_rows_from_manifest_canonical"
    assert any("INSERT INTO commercial.equipment_opportunity" in s for s in sqls)
    opp_insert_params = [
        p
        for s, p in conn.cur.statements
        if "INSERT INTO commercial.equipment_opportunity (" in s
    ]
    assert opp_insert_params
    assert opp_insert_params[0][1] == "equipment:equipment_queue:code-a"


def test_empty_manifest_still_promotes_resolved_active_queue(active_workspace: Path) -> None:
    (active_workspace / "manifest.json").unlink()
    manifest = {}
    resolved = resolve_equipment_operator_queue_csv(active_workspace, manifest)
    assert resolved is not None
    assert mirror.should_promote_equipment_source_as_canonical(manifest, resolved, active_workspace)


def test_canonical_flag_updates_on_apply(active_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []

    class CanonCursor(_RecordingCursor):
        def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
            statements.append(sql)
            return super().execute(sql, params)

    conn = _RecordingConn()
    conn.cur = CanonCursor(existing_source_id=None)

    monkeypatch.setattr(mirror, "psycopg", MagicMock(connect=lambda *a, **k: conn))
    monkeypatch.setattr(mirror, "Json", lambda obj: obj)

    summary = mirror.apply_load(
        "postgresql://u:p@127.0.0.1/db",
        active_workspace,
        updated_by="op",
        reason="canonical",
    )
    assert summary["is_canonical"] is True
    assert any("SET is_canonical = FALSE" in s for s in statements)
    assert any("SET is_canonical = TRUE" in s for s in statements)


def test_cli_dry_run_json(active_workspace: Path) -> None:
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--active-current",
            str(active_workspace),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env={k: v for k, v in os.environ.items() if k != "ORIGENLAB_POSTGRES_URL"},
    )
    assert r.returncode == 0, r.stderr + r.stdout
    data = json.loads(r.stdout)
    assert data["dry_run"] is True
    assert data["row_count"] == 2
    assert data["source_input"] == "typed_rows"


def test_cli_apply_requires_operator_and_reason(active_workspace: Path) -> None:
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--active-current",
            str(active_workspace),
            "--apply",
            "--postgres-url",
            "postgresql://u:p@127.0.0.1/db",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 2


@pytest.mark.skipif(
    _postgres_test_url_ready() is None,
    reason="Set ORIGENLAB_TEST_POSTGRES_URL to a reachable disposable Postgres for integration tests.",
)
def test_apply_and_view_on_disposable_postgres(active_workspace: Path) -> None:
    pytest.importorskip("psycopg")
    pg_url = _postgres_test_url_ready()
    assert pg_url

    import psycopg

    from origenlab_email_pipeline.mart_core_postgres_migrate import normalize_postgres_url

    url = normalize_postgres_url(pg_url)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM commercial.equipment_opportunity WHERE source_id IN "
                "(SELECT id FROM commercial.equipment_opportunity_source WHERE csv_path LIKE %s)",
                (f"%{active_workspace.name}%",),
            )
            cur.execute(
                "DELETE FROM commercial.equipment_opportunity_source WHERE csv_path LIKE %s",
                (f"%{active_workspace.name}%",),
            )
        conn.commit()

    summary = mirror.apply_load(
        pg_url,
        active_workspace,
        updated_by="pytest",
        reason="integration",
    )
    assert summary["applied"] is True
    assert summary["rows_inserted"] == 2

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM api.v_equipment_opportunity WHERE codigo_licitacion = %s", ("CODE-A",))
            assert int(cur.fetchone()[0]) == 1
            cur.execute(
                """
                SELECT is_canonical, source_kind, artifact_basename, canonical_reason, opportunity_key
                FROM commercial.equipment_opportunity_source eos
                JOIN commercial.equipment_opportunity eo ON eo.source_id = eos.id
                WHERE eos.id = %s
                LIMIT 1
                """,
                (summary["source_id"],),
            )
            row = cur.fetchone()
            assert row is not None
            is_canonical, source_kind, artifact_basename, canonical_reason, opportunity_key = row
            assert is_canonical is True
            assert source_kind == "typed_read_model"
            assert artifact_basename == "equipment_first_operator_queue_20260518.csv"
            assert canonical_reason == "typed_rows_from_manifest_canonical"
            assert opportunity_key == "equipment:equipment_queue:code-a"
