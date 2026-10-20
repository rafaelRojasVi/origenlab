"""«En Mercado Público»'s discovery sweep on a disposable `origenlab_test_<hex>` database, as the real
`origenlab_worker` login: candidate titles are kept and the rest never written; details replace the
list's head (the old one goes to the history) up to the run's limit; a known notice keeps its detail;
a notice that left a whole list is withdrawn, never one a case was opened from, never on a short list.

The tests share one database, so each names its own codes. Every value is invented."""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from dbhelp import owner_rows
from origenlab_worker.database import local_test_target, open_verified_connection
from origenlab_worker.tender.discovery import (
    DISCOVERY_REQUIRED_POLICIES,
    DISCOVERY_REQUIRED_PRIVILEGES,
    run_discovery,
)
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db
_SERIALS = itertools.count(1)


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


def _conn(dsn):
    return open_verified_connection(local_test_target(worker_dsn(dsn)), application_name="test-tender-discovery",
                                    privileges=DISCOVERY_REQUIRED_PRIVILEGES, policies=DISCOVERY_REQUIRED_POLICIES)


def _code() -> str:
    return f"{9876500 + next(_SERIALS)}-{uuid.uuid4().hex[:2].upper()}-LE26"


def _summary(code: str, title: str, closes: str = "30/11/2026 15:00:00") -> dict:
    return {"CodigoExterno": code, "Nombre": title, "CodigoEstado": 5, "FechaCierre": closes}


def _detail(code: str, title: str) -> dict:
    return {"CodigoExterno": code, "Nombre": title, "Estado": "Publicada", "FechaCierre": "2026-11-30T15:00:00",
            "Comprador": {"NombreOrganismo": "Hospital Ejemplo Norte", "CodigoOrganismo": "70001",
                          "RegionUnidad": "Región Ejemplo"},
            "Fechas": {"FechaPublicacion": "2026-10-20T09:00:00"},
            "Items": {"Cantidad": 1, "Listado": [{"CodigoProducto": 41111500, "NombreProducto": "Balanzas",
                                                   "Descripcion": "Balanza analítica 0,1 mg", "Cantidad": 2}]}}


def _notice(dsn, code: str) -> dict | None:
    rows = owner_rows(dsn, "select title, buyer_name, closes_at, published_at, disappeared_at, head, "
                           "jsonb_array_length(head_history) from procurement.notice where codigo_externo = %s",
                      (code,))
    if not rows:
        return None
    keys = ("title", "buyer_name", "closes_at", "published_at", "disappeared_at", "head", "history")
    return dict(zip(keys, rows[0], strict=True))


def _run(dsn, listado: list[dict], details: dict[str, dict] | None = None, **kwargs) -> dict:
    asked: list[str] = []

    def fetch_detail(code: str) -> dict | None:
        asked.append(code)
        return (details or {}).get(code)

    with _conn(dsn) as conn:
        counts = run_discovery(conn, lambda: {"Cantidad": len(listado), "Listado": listado}, fetch_detail,
                               now=lambda: datetime.now(UTC), sleep=lambda _: None, **kwargs)
    counts["asked"] = asked
    return counts


def test_only_candidate_titles_are_kept_and_their_detail_becomes_the_head(dsn) -> None:
    balanza, pintura = _code(), _code()
    listado = [_summary(balanza, "Adquisición de balanza analítica"), _summary(pintura, "Pintura de fachada")]
    counts = _run(dsn, listado, {balanza: _detail(balanza, "Adquisición de balanza analítica")}, min_list_rows=10)

    assert counts["candidates"] == 1 and counts["detailed"] == 1 and counts["asked"] == [balanza]
    assert _notice(dsn, pintura) is None
    kept = _notice(dsn, balanza)
    assert kept["buyer_name"] == "Hospital Ejemplo Norte"
    assert kept["head"]["Comprador"]["NombreOrganismo"] == "Hospital Ejemplo Norte"
    assert kept["history"] == 1  # the list's row
    # Chilean time: 15:00 in Santiago (UTC-3 in November) is 18:00 UTC.
    assert kept["closes_at"] == datetime(2026, 11, 30, 18, 0, tzinfo=UTC)
    assert kept["published_at"] is not None


def test_a_known_notice_keeps_its_detail_and_is_not_read_again(dsn) -> None:
    code = _code()
    _run(dsn, [_summary(code, "Centrífuga de mesa")], {code: _detail(code, "Centrífuga de mesa")}, min_list_rows=10)
    again = _run(dsn, [_summary(code, "Centrífuga de mesa refrigerada", closes="05/12/2026 12:00:00")],
                 min_list_rows=10)

    assert code not in again["asked"]
    row = _notice(dsn, code)
    assert row["title"] == "Centrífuga de mesa refrigerada"
    assert row["closes_at"] == datetime(2026, 12, 5, 15, 0, tzinfo=UTC)
    assert "Comprador" in row["head"] and row["history"] == 1


def test_details_beyond_the_limit_wait_for_the_next_run(dsn) -> None:
    codes = [_code() for _ in range(3)]
    listado = [_summary(c, f"Homogeneizador {c}") for c in codes]
    counts = _run(dsn, listado, {c: _detail(c, f"Homogeneizador {c}") for c in codes}, detail_limit=2,
                  min_list_rows=10)
    assert (counts["detailed"], counts["detail_waiting"]) == (2, 1)
    later = _run(dsn, listado, {c: _detail(c, f"Homogeneizador {c}") for c in codes}, min_list_rows=10)
    assert later["detailed"] == 1


def test_a_failed_detail_is_counted_and_retried_next_run(dsn) -> None:
    code = _code()
    first = _run(dsn, [_summary(code, "Sonicador de laboratorio")], {}, min_list_rows=10)
    assert first["detail_failed"] == 1 and "Comprador" not in _notice(dsn, code)["head"]
    second = _run(dsn, [_summary(code, "Sonicador de laboratorio")], {code: _detail(code, "Sonicador")},
                  min_list_rows=10)
    assert second["detailed"] == 1


def test_a_notice_that_left_a_whole_list_is_withdrawn_and_comes_back_when_listed_again(dsn) -> None:
    gone, stays = _code(), _code()
    _run(dsn, [_summary(gone, "Osmómetro"), _summary(stays, "Electroforesis")], min_list_rows=1)
    counts = _run(dsn, [_summary(stays, "Electroforesis")], min_list_rows=1)

    assert counts["withdrawn"] >= 1
    assert _notice(dsn, gone)["disappeared_at"] is not None
    assert _notice(dsn, stays)["disappeared_at"] is None
    _run(dsn, [_summary(gone, "Osmómetro"), _summary(stays, "Electroforesis")], min_list_rows=1)
    assert _notice(dsn, gone)["disappeared_at"] is None


def test_a_short_list_withdraws_nothing(dsn) -> None:
    code = _code()
    _run(dsn, [_summary(code, "Agitador magnético")], min_list_rows=1)
    _run(dsn, [], min_list_rows=10)
    assert _notice(dsn, code)["disappeared_at"] is None


def test_a_notice_a_case_was_opened_from_or_an_old_one_is_never_withdrawn(dsn) -> None:
    promoted, old = _code(), _code()
    _run(dsn, [_summary(promoted, "Balanza de precisión")], min_list_rows=1)
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-{uuid.uuid4().hex[:6]}@example.test",))[0][0]
    case = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id, kind, tender_status) "
                           "values ('Licitación ficticia', 'lead', %s, 'tender', 'preparando') returning id::text",
                      (operator,))[0][0]
    owner_rows(dsn, "update procurement.notice set promoted_opportunity_id = %s where codigo_externo = %s",
               (case, promoted))
    past = datetime.now(UTC) - timedelta(days=30)
    owner_rows(dsn, "insert into procurement.notice (codigo_externo, title, closes_at, head, first_seen_at, "
                    "last_seen_at) values (%s, 'Balanza antigua', %s, '{}'::jsonb, %s, %s)", (old, past, past, past))

    _run(dsn, [_summary(_code(), "Centrífuga")], min_list_rows=1)
    assert _notice(dsn, promoted)["disappeared_at"] is None
    assert _notice(dsn, old)["disappeared_at"] is None
