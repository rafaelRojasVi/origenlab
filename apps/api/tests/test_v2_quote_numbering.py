"""Quote numbering: the pure helpers, and the allocator against PostgreSQL as the real `origenlab_api` role.

Every number and name is invented.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.quotes.numbering import (
    RESERVED_SERIALS,
    allocate_quote_number,
    cn_label,
    format_number,
    next_serial,
    numbering_status,
    parse_serial,
    revision_label,
    santiago_yy,
)
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


# ------------------------------------------------------------------ pure

@pytest.mark.parametrize("text", ["01268-26", "1268-26", "01268A-26", "CN01268", "CN01268A", "cn 01268",
                                  " 01268-26 ", "cn01268b"])
def test_parse_serial_accepts_every_printed_form(text) -> None:
    assert parse_serial(text) == 1268


@pytest.mark.parametrize("text", ["2026", "01268", "", "abc", "1268-2026", "123456-26", "CN", "Q-01268",
                                  "01268-26 extra", "M1234abcd"])
def test_parse_serial_refuses_everything_else(text) -> None:
    assert parse_serial(text) is None


def test_format_number_pads_to_five_digits() -> None:
    assert format_number(7, 26) == "00007-26"
    assert format_number(1268, 26) == "01268-26"


def test_revision_labels() -> None:
    assert revision_label("01268-26", 1) == "01268-26"
    assert revision_label("01268-26", 2) == "01268A-26"
    assert revision_label("01268-26", 27) == "01268Z-26"
    with pytest.raises(ValueError, match="revision_letters_exhausted"):
        revision_label("01268-26", 28)


def test_cn_labels() -> None:
    assert cn_label("01268-26", 1) == "CN01268"
    assert cn_label("01268-26", 2) == "CN01268A"
    assert cn_label("01268-26", 3) == "CN01268B"
    with pytest.raises(ValueError, match="revision_letters_exhausted"):
        cn_label("01268-26", 28)


def test_next_serial_skips_the_reserved_and_the_known() -> None:
    assert RESERVED_SERIALS == frozenset({1500})
    assert next_serial([1498, 1499], 1200) == 1501
    assert next_serial([], 1300) == 1300
    assert next_serial([1301], 1301) == 1302
    assert next_serial([], 1500) == 1501
    assert next_serial([40], 1300) == 1300


def test_santiago_year_changes_at_chilean_midnight() -> None:
    assert santiago_yy(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)) == 26  # 20:30 in Santiago
    assert santiago_yy(datetime(2027, 1, 1, 2, 0, tzinfo=UTC)) == 26  # 23:00 on 31 Dec in Santiago
    assert santiago_yy(datetime(2027, 1, 1, 4, 0, tzinfo=UTC)) == 27  # 01:00 on 1 Jan in Santiago


# ------------------------------------------------------------------ database

@pytest.fixture
def db():
    """A fresh disposable database per test: the seed and the quotes are global state."""
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _seed(dsn: str, value, *, valid_from: str = "now() - interval '1 day'") -> None:
    _owner(dsn, f"insert into catalog.cost_parameter (key, value_numeric, valid_from, reason) "
                f"values ('quote_number_seed', %s, {valid_from}, 'prueba')", (value,))


def _case(dsn: str) -> str:
    op = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                     "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active') returning id::text",
                (f"sales-{uuid.uuid4().hex[:8]}@example.test",))[0][0]
    return _owner(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                       "values ('Caso ficticio', 'lead', %s) returning id::text", (op,))[0][0]


def _quote(dsn: str, number: str) -> None:
    _owner(dsn, "insert into crm.quote (opportunity_id, quote_number) values (%s, %s)", (_case(dsn), number))


def _mail(dsn: str, proposed: list[str]) -> None:
    _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                "values ('gmail_message', %s, %s::jsonb, %s)",
           (f"pytest-numbering:{uuid.uuid4().hex}", json.dumps({"proposed_quote_numbers": proposed}),
            f"gmail://msg/{uuid.uuid4().hex}"))


def _allocate(dsn: str) -> str:
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        return allocate_quote_number(cur, NOW)


@needs_db
def test_no_seed_refuses(db) -> None:
    with pytest.raises(CommandRefused) as exc:
        _allocate(db)
    assert (exc.value.status_code, exc.value.code) == (409, "quote_numbering_not_configured")
    with psycopg.connect(runtime_dsn(db)) as conn, conn.cursor() as cur:
        assert numbering_status(cur, NOW) == {"configured": False, "seed": None, "next_number": None,
                                              "highest_known": None}


@needs_db
def test_the_seed_alone_decides_on_an_empty_database(db) -> None:
    _seed(db, "1300.000000")
    assert _allocate(db) == "01300-26"


@needs_db
def test_the_current_seed_is_the_newest_one_already_in_force(db) -> None:
    _seed(db, 1300, valid_from="now() - interval '2 days'")
    _seed(db, 1400, valid_from="now() - interval '1 day'")
    _seed(db, 9000, valid_from="now() + interval '1 day'")  # not in force yet
    assert _allocate(db) == "01400-26"


@needs_db
def test_an_existing_quote_and_the_reserved_serial_are_skipped(db) -> None:
    _seed(db, 1499)
    _quote(db, "01499-26")
    assert _allocate(db) == "01501-26"


@needs_db
def test_a_number_proposed_from_sent_mail_counts_as_known(db) -> None:
    _seed(db, 1300)
    _mail(db, ["01600-26", "not a number", "2026"])
    assert _allocate(db) == "01601-26"
    with psycopg.connect(runtime_dsn(db)) as conn, conn.cursor() as cur:
        assert numbering_status(cur, NOW) == {"configured": True, "seed": 1300, "next_number": "01601-26",
                                              "highest_known": 1600}


@needs_db
def test_a_quote_number_in_another_format_does_not_move_the_counter(db) -> None:
    _seed(db, 1300)
    _quote(db, "M1234abcd")
    assert _allocate(db) == "01300-26"


@needs_db
def test_two_simultaneous_allocations_get_different_numbers(db) -> None:
    _seed(db, 1300)
    cases = [_case(db), _case(db)]
    out: list[str] = []
    errors: list[BaseException] = []
    first_allocated = threading.Event()

    def worker(index: int, wait_for: threading.Event | None) -> None:
        try:
            with psycopg.connect(runtime_dsn(db)) as conn, conn.cursor() as cur:
                if wait_for is not None:
                    wait_for.wait(5)  # start while the first still holds its lock
                number = allocate_quote_number(cur, NOW)
                if wait_for is None:
                    first_allocated.set()
                    threading.Event().wait(0.5)  # hold the lock before inserting, so the second must queue
                cur.execute("insert into crm.quote (opportunity_id, quote_number) values (%s, %s)",
                            (cases[index], number))
                out.append(number)
                conn.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(0, None)),
               threading.Thread(target=worker, args=(1, first_allocated))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    assert sorted(out) == ["01300-26", "01301-26"]
