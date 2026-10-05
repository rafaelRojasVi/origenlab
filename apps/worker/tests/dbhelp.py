"""Owner-side helpers for the database tests: seed and inspect a disposable database as
`origenlab_owner` (the maintenance login's SET ROLE) — never as the worker under test."""

from __future__ import annotations

from typing import Any

import psycopg

from mailfixtures import MAILBOX


def owner_rows(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]] | None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def seed_mailbox(dsn: str) -> str:
    """The hosted row as the 2026-10-04 load left it: unauthorized, no cursor, never synced."""
    rows = owner_rows(
        dsn,
        "insert into comms.mailbox (address_norm, display_name) values (%s, 'Contacto') returning id::text",
        (MAILBOX,),
    )
    return rows[0][0]


def business_rows(dsn: str) -> int:
    """Every row in `crm.*` and `outbound.*` — a number the worker must never change."""
    tables = owner_rows(
        dsn,
        "select table_schema || '.' || table_name from information_schema.tables "
        "where table_schema in ('crm', 'outbound') and table_type = 'BASE TABLE' order by 1",
    )
    return sum(owner_rows(dsn, f"select count(*) from {name}")[0][0] for (name,) in tables)
