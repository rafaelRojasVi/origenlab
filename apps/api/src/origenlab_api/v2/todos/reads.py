"""Read-only query behind `GET /v2/workspace/todos` («Pendientes» on Hoy). Same `(connect, dsn)` wiring as the other readers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_ITEM = """
select t.id::text as todo_id, t.version, t.body, t.is_urgent, t.position::text as position,
       t.due_on, t.status, t.completed_at, t.created_at, op.display_name as created_by,
       o.id::text as o_id, o.title as o_title, o.stage as o_stage, org.name as o_org,
       (select q.quote_number from crm.quote q where q.opportunity_id = o.id
         order by q.quote_number desc limit 1) as o_quote
  from crm.todo t
  join platform.operator op on op.id = t.created_by_operator_id
  left join crm.opportunity o on o.id = t.opportunity_id
  left join crm.organization org on org.id = o.organization_id
"""
_OPEN = _ITEM + " where t.status = 'open' order by t.is_urgent desc, t.position, t.created_at desc, t.id"
_DONE = _ITEM + (" where t.status = 'done' and t.completed_at >= now() - make_interval(days => %s)"
                 " order by t.completed_at desc, t.id")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _item(row: dict[str, Any]) -> dict[str, Any]:
    case = None
    if row["o_id"] is not None:
        case = {"id": row["o_id"], "title": row["o_title"], "stage": row["o_stage"],
                "organization_name": row["o_org"], "quote_number": row["o_quote"]}
    return {"todo_id": row["todo_id"], "version": row["version"], "body": row["body"],
            "is_urgent": row["is_urgent"], "position": row["position"], "due_on": _iso(row["due_on"]),
            "status": row["status"], "completed_at": _iso(row["completed_at"]),
            "created_at": _iso(row["created_at"]), "created_by": row["created_by"], "opportunity": case}


class TodoReadRepository:
    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self) -> Iterator[Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    @staticmethod
    def _rows(cur: Any, sql: str, params: Any = None) -> list[dict[str, Any]]:
        cur.execute(sql, params)
        names = [d.name for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def list(self, done_days: int) -> dict[str, Any]:
        """Open lines in list order, and the lines done in the last `done_days` days, newest first."""
        with self._read() as cur:
            open_rows = self._rows(cur, _OPEN)
            done_rows = self._rows(cur, _DONE, (int(done_days),))
        return {"open": [_item(r) for r in open_rows], "done": [_item(r) for r in done_rows]}
