"""Tests for warm case Postgres promotion (DB-2B)."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from origenlab_email_pipeline import warm_case_promotion as promo
from origenlab_email_pipeline.cases_review_queue import fetch_cases_review_queue
from origenlab_email_pipeline.warm_case_classification import infer_warm_case_category

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "commercial" / "promote_warm_cases_to_postgres.py"

# Use relative dates because queue filtering is based on date.today(); fixed dates make CI fail as time passes.


def _recent_email_iso(days_ago: int = 1, time: str = "10:00:00Z") -> str:
    return f"{(date.today() - timedelta(days=days_ago)).isoformat()}T{time}"


def _postgres_test_url_ready() -> str | None:
    url = (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip()
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


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("promote_warm_cases_cli", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mk_sqlite(path: Path, rows: list[tuple[int, str, str, str, str]]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          date_iso TEXT,
          subject TEXT,
          sender TEXT,
          source_file TEXT
        );
        """
    )
    for email_id, date_iso, subject, sender, source_file in rows:
        conn.execute(
            "INSERT INTO emails (id, date_iso, subject, sender, source_file) VALUES (?, ?, ?, ?, ?)",
            (email_id, date_iso, subject, sender, source_file),
        )
    conn.commit()
    conn.close()


def _queue_row(
    email_id: int,
    *,
    sender: str = "client@hospital.cl",
    subject: str = "Re: Cotización equipo",
    date_iso: str | None = None,
    source_file: str = "gmail:contacto@origenlab.cl/INBOX",
) -> dict[str, Any]:
    return {
        "email_id": email_id,
        "date_iso": date_iso or _recent_email_iso(1),
        "subject_preview": subject,
        "sender_preview": sender,
        "source_file": source_file,
        "has_positive_signal": 0,
        "has_suppression_signal": 0,
        "max_positive_strength": None,
    }


def test_build_case_key_stable_and_normalized() -> None:
    k1 = promo.build_case_key("Client@Hospital.CL", "hospital.cl")
    k2 = promo.build_case_key("client@hospital.cl", "hospital.cl")
    assert k1 == k2
    assert k1.startswith("warm:")
    assert len(k1) == len("warm:") + 64


def test_rg_energia_thread_hint_links_internal_forward_and_supplier_quote() -> None:
    forward = promo.queue_row_to_promotion_record(
        _queue_row(
            10,
            sender="Tatiana Vivanco <contacto@labdelivery.cl>",
            subject="RV: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200// RG ENERGIA SPA",
        ),
        enrichment_available=False,
    )
    supplier = promo.queue_row_to_promotion_record(
        _queue_row(
            11,
            sender='"Bonon Ferreira, Beatriz" <beatriz.bonon@ika.net.br>',
            subject="RES: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200// RG ENERGIA SPA",
        ),
        enrichment_available=False,
    )
    assert forward is not None and supplier is not None
    assert forward.case_key == supplier.case_key
    assert forward.title == "RG Energía — IKA RV10.70 tubo vapor — qty 3"
    assert supplier.title == "RG Energía — IKA RV10.70 tubo vapor — qty 3"
    assert forward.account_name == "RG ENERGIA SPA"


def test_crtop_reactor_thread_hint_links_duplicate_subjects() -> None:
    rows = [
        promo.queue_row_to_promotion_record(
            _queue_row(
                20,
                sender="Ariel <ariel@crtopmachine.com>",
                subject="Re: Thank you very much for your inquiry about our reactor.",
                date_iso=_recent_email_iso(2),
            ),
            enrichment_available=False,
        ),
        promo.queue_row_to_promotion_record(
            _queue_row(
                21,
                sender="Ariel <ariel@crtopmachine.com>",
                subject="Re: Thank you very much for your inquiry about our reactor.",
                date_iso=_recent_email_iso(1),
            ),
            enrichment_available=False,
        ),
    ]
    assert rows[0] is not None and rows[1] is not None
    assert rows[0].case_key == rows[1].case_key
    assert rows[0].case_key.startswith("warm:thread:")
    deduped = promo.dedupe_candidates([rows[0], rows[1]])
    assert len(deduped) == 1


def test_duplicate_queue_rows_same_case_key_deduped() -> None:
    rows = [
        promo.queue_row_to_promotion_record(
            _queue_row(1, date_iso=_recent_email_iso(2)),
            enrichment_available=False,
        ),
        promo.queue_row_to_promotion_record(
            _queue_row(2, date_iso=_recent_email_iso(1)),
            enrichment_available=False,
        ),
    ]
    assert rows[0] is not None and rows[1] is not None
    assert rows[0].case_key == rows[1].case_key
    deduped = promo.dedupe_candidates([rows[0], rows[1]])
    assert len(deduped) == 1
    assert deduped[rows[0].case_key].last_email_id == 2


def test_dedupe_prefers_supplier_quote_received_over_newer_quote_sent() -> None:
    inbound = promo.queue_row_to_promotion_record(
        {
            "email_id": 10,
            "date_iso": _recent_email_iso(2),
            "subject_preview": "RE: Solicitud de cotización",
            "sender_preview": "Carmen Llorente <carmen.llorente@ortoalresa.com>",
            "source_file": "gmail:contacto@origenlab.cl/INBOX",
        },
        enrichment_available=False,
    )
    sent = promo.queue_row_to_promotion_record(
        {
            "email_id": 11,
            "date_iso": _recent_email_iso(1),
            "subject_preview": "RE: Solicitud de cotización",
            "sender_preview": "contacto@origenlab.cl",
            "recipients_preview": "carmen.llorente@ortoalresa.com",
            "source_file": "gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        },
        enrichment_available=False,
    )
    assert inbound is not None and sent is not None
    assert inbound.case_key == sent.case_key
    assert inbound.role_category == "supplier_quote_received"
    assert sent.role_category == "quote_sent"

    deduped = promo.dedupe_candidates([inbound, sent])
    picked = deduped[inbound.case_key]
    assert picked.role_category == "supplier_quote_received"
    assert picked.last_email_id == 10


def test_dedupe_same_priority_keeps_latest_activity() -> None:
    from origenlab_email_pipeline.mart_core_postgres_migrate import iso_text_to_datetime

    older = promo.WarmCasePromotionRecord(
        case_key="warm:test",
        title="older",
        account_name="Acme",
        primary_contact_email="buyer@hospital.cl",
        primary_domain="hospital.cl",
        category="client_reply",
        role_category="client_response",
        status="open",
        next_action="reply",
        equipment_signal=None,
        last_email_id=1,
        last_activity_at=iso_text_to_datetime(_recent_email_iso(2)) or datetime.now(timezone.utc),
    )
    newer = promo.WarmCasePromotionRecord(
        case_key="warm:test",
        title="newer",
        account_name="Acme",
        primary_contact_email="buyer@hospital.cl",
        primary_domain="hospital.cl",
        category="client_reply",
        role_category="client_response",
        status="open",
        next_action="reply",
        equipment_signal=None,
        last_email_id=2,
        last_activity_at=iso_text_to_datetime(_recent_email_iso(1)) or datetime.now(timezone.utc),
    )
    picked = promo.dedupe_candidates([older, newer])["warm:test"]
    assert picked.last_email_id == 2


def test_preview_dry_run_does_not_connect_postgres(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(1, _recent_email_iso(1), "Re: Equipo", "buyer@udec.cl", "gmail:contacto@origenlab.cl/inbox")],
    )
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: (_ for _ in ()).throw(AssertionError())))
    summary = promo.preview_promotion(db)
    assert summary["dry_run"] is True
    assert summary["applied"] is False
    assert summary["candidate_count"] >= 1


def test_queue_row_rejects_body_fields() -> None:
    row = _queue_row(1)
    row["body"] = "secret"
    with pytest.raises(ValueError, match="body"):
        promo.queue_row_to_promotion_record(row, enrichment_available=False)


def test_no_body_fields_in_promotion_record() -> None:
    rec = promo.queue_row_to_promotion_record(_queue_row(1), enrichment_available=False)
    assert rec is not None
    dumped = repr(rec)
    for forbidden in ("body_html", "full_body_clean", "raw_json"):
        assert forbidden not in dumped


class _PromoCursor:
    def __init__(self, *, existing: dict[str, tuple[Any, ...]] | None = None) -> None:
        # case_key -> (id, status, source, closed, legacy_category, role_category)
        self.cases: dict[str, tuple[int, str, str, bool, str, str | None]] = {}
        for key, value in (existing or {}).items():
            if len(value) == 4:
                case_id, status, source, closed = value
                self.cases[key] = (int(case_id), str(status), str(source), bool(closed), "client_reply", None)
            else:
                self.cases[key] = value  # type: ignore[assignment]
        self.next_id = max((v[0] for v in self.cases.values()), default=0) + 1
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []
        self._fetch: tuple[Any, ...] | None = None
        self._fetchall: list[tuple[Any, ...]] = []
        self.linked: set[tuple[int, int]] = set()

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.statements.append((sql, params))
        self._fetchall = []
        if "SELECT id, status, (closed_at IS NOT NULL)" in sql:
            key = str(params[0]) if params else ""
            hit = self.cases.get(key)
            self._fetch = (hit[0], hit[1], hit[3]) if hit else None
        elif "INSERT INTO commercial.warm_case_linked_email" in sql:
            assert params is not None
            pair = (int(params[0]), int(params[1]))
            if pair not in self.linked:
                self.linked.add(pair)
                self._fetch = (pair[0],)
            else:
                self._fetch = None
        elif "INSERT INTO commercial.warm_case (" in sql and "ON CONFLICT" in sql:
            assert params is not None
            key = str(params[0])
            legacy_cat = str(params[5])
            role_cat = str(params[6])
            status = str(params[7])
            source = str(params[12])
            if key in self.cases:
                case_id, _, _, _, _, _ = self.cases[key]
                self.cases[key] = (case_id, status, source, False, legacy_cat, role_cat)
            else:
                case_id = self.next_id
                self.next_id += 1
                self.cases[key] = (case_id, status, source, False, legacy_cat, role_cat)
            self._fetch = (case_id, status)
        elif (
            "UPDATE commercial.warm_case" in sql
            and "closed_at = now()" in sql
            and "RETURNING id, case_key" in sql
        ):
            assert params is not None
            str(params[0])
            source = str(params[1])
            keys = {str(k) for k in params[2]}
            closed: list[tuple[int, str]] = []
            for key, (case_id, status, case_source, is_closed, _, _) in list(self.cases.items()):
                if case_source == source and not is_closed and key not in keys:
                    self.cases[key] = (case_id, status, case_source, True)
                    closed.append((case_id, key))
            self._fetchall = closed
            self._fetch = None
        elif "INSERT INTO commercial.warm_case_status_history" in sql:
            self._fetch = (1,)
        elif "INSERT INTO commercial.warm_case_event" in sql:
            self._fetch = (1,)
        elif "INSERT INTO commercial.warm_case_equipment_signal" in sql:
            self._fetch = (1,)
        else:
            self._fetch = None

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._fetch

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._fetchall)

    def __enter__(self) -> _PromoCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class _PromoConn:
    def __init__(self, cur: _PromoCursor) -> None:
        self.cur = cur
        self.committed = False

    def cursor(self) -> _PromoCursor:
        return self.cur

    def commit(self) -> None:
        self.committed = True

    def __enter__(self) -> _PromoConn:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_apply_inserts_case_links_email_and_promote_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(5, _recent_email_iso(1), "Re: Equipo", "buyer@udec.cl", "gmail:contacto@origenlab.cl/inbox")],
    )
    cur = _PromoCursor()
    conn = _PromoConn(cur)
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: conn))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    summary = promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="test",
    )
    assert summary["applied"] is True
    assert summary["inserted_cases"] == 1
    assert summary["linked_emails"] == 1
    assert summary["events_inserted"] == 1
    assert conn.committed is True
    sqls = " ".join(s for s, _ in cur.statements)
    assert "TRUNCATE" not in sqls.upper()


def test_apply_same_email_link_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(5, _recent_email_iso(1), "Re: Equipo", "buyer@udec.cl", "gmail:contacto@origenlab.cl/inbox")],
    )
    candidates, _ = promo.load_candidates_from_sqlite(db)
    key = next(iter(candidates.keys()))
    case_id = 99
    rec = candidates[key]
    cur = _PromoCursor(existing={key: (case_id, "open", promo.PROMOTION_SOURCE, False)})
    cur.linked.add((case_id, rec.last_email_id))
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    summary = promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="re-run",
    )
    assert summary["updated_cases"] == 1
    assert summary["inserted_cases"] == 0
    assert summary["linked_emails"] == 0
    assert summary["events_inserted"] == 0


def test_status_change_writes_status_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(5, _recent_email_iso(1), "Re: Equipo", "buyer@udec.cl", "gmail:contacto@origenlab.cl/inbox")],
    )
    candidates, _ = promo.load_candidates_from_sqlite(db)
    key = next(iter(candidates.keys()))
    rec = candidates[key]
    cur = _PromoCursor(existing={key: (7, "quoted", promo.PROMOTION_SOURCE, False)})
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    summary = promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="status change",
    )
    assert rec.status == "new"
    assert summary["status_history_rows"] == 1
    assert any("warm_case_status_history" in s for s, _ in cur.statements)


def test_apply_close_missing_false_leaves_stale_promoted_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(5, _recent_email_iso(1), "Re: Equipo", "buyer@udec.cl", "gmail:contacto@origenlab.cl/inbox")],
    )
    candidates, _ = promo.load_candidates_from_sqlite(db)
    key = next(iter(candidates.keys()))
    stale_key = "warm:stale"
    cur = _PromoCursor(
        existing={
            key: (1, "open", promo.PROMOTION_SOURCE, False),
            stale_key: (2, "open", promo.PROMOTION_SOURCE, False),
        }
    )
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    summary = promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="test",
        close_missing=False,
    )
    assert summary["closed_missing_cases"] == 0
    assert cur.cases[stale_key][3] is False


def test_apply_close_missing_true_closes_stale_promoted_rows_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(5, _recent_email_iso(1), "Re: Equipo", "buyer@udec.cl", "gmail:contacto@origenlab.cl/inbox")],
    )
    candidates, _ = promo.load_candidates_from_sqlite(db)
    key = next(iter(candidates.keys()))
    stale_key = "warm:stale"
    manual_key = "warm:manual"
    cur = _PromoCursor(
        existing={
            key: (1, "open", promo.PROMOTION_SOURCE, False),
            stale_key: (2, "open", promo.PROMOTION_SOURCE, False),
            manual_key: (3, "open", "manual_review", False),
        }
    )
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    summary = promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="close stale",
        close_missing=True,
    )
    assert summary["closed_missing_cases"] == 1
    assert cur.cases[stale_key][3] is True
    assert cur.cases[manual_key][3] is False
    assert cur.cases[key][3] is False
    assert any("status_change" in s for s, _ in cur.statements)


def test_apply_reopens_closed_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(5, _recent_email_iso(1), "Re: Equipo", "buyer@udec.cl", "gmail:contacto@origenlab.cl/inbox")],
    )
    candidates, _ = promo.load_candidates_from_sqlite(db)
    key = next(iter(candidates.keys()))
    cur = _PromoCursor(existing={key: (1, "open", promo.PROMOTION_SOURCE, True)})
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    summary = promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="reopen",
        close_missing=True,
    )
    assert summary["reopened_cases"] == 1
    assert cur.cases[key][3] is False


def test_apply_no_candidates_with_close_missing_does_not_close_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(db, [])
    cur = _PromoCursor(existing={"warm:stale": (2, "open", promo.PROMOTION_SOURCE, False)})
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    summary = promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="empty snapshot",
        close_missing=True,
    )
    assert summary["warning"] == "no_candidates"
    assert summary["closed_missing_cases"] == 0
    assert cur.cases["warm:stale"][3] is False
    assert not any("UPDATE commercial.warm_case" in s for s, _ in cur.statements)


def test_cli_apply_requires_operator_and_reason(tmp_path: Path) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [(1, _recent_email_iso(1), "Hola", "a@ext.com", "gmail:contacto@origenlab.cl/inbox")],
    )
    cli = _load_cli_module()
    code = cli.main(["--sqlite-db", str(db), "--apply", "--postgres-url", "postgresql://u:p@127.0.0.1/db"])
    assert code == 2


def test_classify_bounce_shared_module() -> None:
    row = {
        "email_id": 1,
        "sender_preview": "mailer-daemon@google.com",
        "subject_preview": "Delivery Status Notification (Failure)",
        "source_file": "gmail:contacto@origenlab.cl/INBOX",
    }
    assert infer_warm_case_category(row, enrichment_available=False, include_noise=False) == "bounce"


def test_queue_row_supplier_quote_received_stores_legacy_and_role() -> None:
    rec = promo.queue_row_to_promotion_record(
        _queue_row(
            1,
            sender='"Bonon Ferreira, Beatriz" <beatriz.bonon@ika.net.br>',
            subject="RES: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200",
        ),
        enrichment_available=False,
    )
    assert rec is not None
    assert rec.category == "supplier_reply"
    assert rec.role_category == "supplier_quote_received"


def test_queue_row_supplier_followup_stores_legacy_and_role() -> None:
    rec = promo.queue_row_to_promotion_record(
        {
            **_queue_row(
                2,
                sender="Ariel <ariel@crtopmachine.com>",
                subject="Re: Thank you very much for your inquiry about our reactor.",
            ),
            "snippet": "Please send your address to calculate shipping cost to Chile.",
        },
        enrichment_available=False,
    )
    assert rec is not None
    assert rec.category == "supplier_reply"
    assert rec.role_category == "supplier_followup"


def test_queue_row_waiting_supplier_keeps_role_and_legacy() -> None:
    rec = promo.queue_row_to_promotion_record(
        {
            **_queue_row(
                3,
                sender="Marcos Acevedo <marcos.a@hielscher.com>",
                subject="[RCH-Universidad Adventista de Chile] Hielscher Ultrasonics: Su solicitud sobre el UIP2000hdT",
            ),
            "snippet": "extracción vegetal asistida por ultrasonido, escalamiento 30-50 L",
        },
        enrichment_available=False,
    )
    assert rec is not None
    assert rec.category == "waiting_supplier"
    assert rec.role_category == "waiting_supplier"


def test_queue_row_logistics_admin_stores_legacy_and_role() -> None:
    rec = promo.queue_row_to_promotion_record(
        _queue_row(
            4,
            sender="Monica Silva <monica.silva@dhl.com>",
            subject="PROPUESTA COMERCIAL DHL",
        ),
        enrichment_available=False,
    )
    assert rec is not None
    assert rec.category == "client_reply"
    assert rec.role_category == "logistics_admin"


def test_apply_writes_role_category_on_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [
            (
                50,
                _recent_email_iso(1, "12:00:00Z"),
                "RES: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200",
                '"Bonon Ferreira, Beatriz" <beatriz.bonon@ika.net.br>',
                "gmail:contacto@origenlab.cl/inbox",
            ),
        ],
    )
    cur = _PromoCursor()
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="role category",
    )
    stored = next(iter(cur.cases.values()))
    assert stored[4] == "supplier_reply"
    assert stored[5] == "supplier_quote_received"


def test_apply_on_conflict_updates_role_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [
            (
                50,
                _recent_email_iso(1, "12:00:00Z"),
                "RES: Solicitud de Cotización Tubo Vapor IKA RV10.70 3812200",
                '"Bonon Ferreira, Beatriz" <beatriz.bonon@ika.net.br>',
                "gmail:contacto@origenlab.cl/inbox",
            ),
        ],
    )
    candidates, _ = promo.load_candidates_from_sqlite(db)
    key = next(iter(candidates.keys()))
    cur = _PromoCursor(
        existing={
            key: (
                1,
                "open",
                promo.PROMOTION_SOURCE,
                False,
                "supplier_reply",
                "supplier_followup",
            )
        }
    )
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    rec = candidates[key]
    assert rec.role_category == "supplier_quote_received"

    promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="update role",
    )
    assert cur.cases[key][5] == "supplier_quote_received"


def test_apply_reopened_row_updates_role_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "warm.sqlite"
    _mk_sqlite(
        db,
        [
            (
                50,
                _recent_email_iso(1, "12:00:00Z"),
                "PROPUESTA COMERCIAL DHL",
                "Monica Silva <monica.silva@dhl.com>",
                "gmail:contacto@origenlab.cl/inbox",
            ),
        ],
    )
    candidates, _ = promo.load_candidates_from_sqlite(db)
    key = next(iter(candidates.keys()))
    cur = _PromoCursor(
        existing={
            key: (1, "open", promo.PROMOTION_SOURCE, True, "client_reply", None),
        }
    )
    monkeypatch.setattr(promo, "psycopg", MagicMock(connect=lambda *a, **k: _PromoConn(cur)))
    monkeypatch.setattr(promo, "Json", lambda obj: obj)

    promo.apply_promotion(
        "postgresql://u:p@127.0.0.1/db",
        db,
        updated_by="op",
        reason="reopen role",
        close_missing=True,
    )
    assert cur.cases[key][3] is False
    assert cur.cases[key][4] == "client_reply"
    assert cur.cases[key][5] == "logistics_admin"


@pytest.mark.skipif(
    _postgres_test_url_ready() is None,
    reason="Set ORIGENLAB_TEST_POSTGRES_URL to a reachable disposable Postgres DB.",
)
def test_apply_visible_in_v_warm_case(tmp_path: Path) -> None:
    pytest.importorskip("psycopg")
    pg_url = _postgres_test_url_ready()
    assert pg_url

    import psycopg

    from origenlab_email_pipeline.mart_core_postgres_migrate import normalize_postgres_url

    db = tmp_path / "warm_pg.sqlite"
    _mk_sqlite(
        db,
        [
            (
                50,
                _recent_email_iso(1, "12:00:00Z"),
                "Re: Centrifuga clinica",
                "compras@hospital.cl",
                "gmail:contacto@origenlab.cl/inbox",
            ),
        ],
    )
    url = normalize_postgres_url(pg_url)

    summary = promo.apply_promotion(
        pg_url,
        db,
        updated_by="pytest",
        reason="integration",
    )
    assert summary["applied"] is True
    assert summary["inserted_cases"] >= 1 or summary["updated_cases"] >= 1

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM api.v_warm_case
                WHERE contact_email = %s
                """,
                ("compras@hospital.cl",),
            )
            assert int(cur.fetchone()[0]) >= 1
