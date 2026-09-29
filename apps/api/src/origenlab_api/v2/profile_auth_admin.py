"""Migrator-only maintenance of shared sign-in: pruning ended session rows.

`docs/ARCHITECTURE.md` §5.1, `apps/api/docs/PRODUCTION_AUTH.md`. The runtime API role can insert
and revoke `platform.auth_session` rows but deletes nothing — no runtime role holds DELETE on any
table — so rows that have ended stay until this removes them. They grant nothing while they stay:
every request requires a live row, and an expired or revoked one is not.

Every operation here follows the roster tools' shape (`profile_roster.py`):

* `plan` reads only and prints the exact change count, the database name and every value the
  apply must be given back.
* `apply` recomputes the plan in one transaction as `origenlab_owner` (`set local role`) and
  refuses unless the caller confirms the exact count and database name the plan printed.
* It is a command-line tool for the migrator login. Nothing here is mounted as a route, and the
  runtime role cannot `set role origenlab_owner`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

#: A session row is pruned only this long after it expired, at the least.
MIN_PRUNE_AGE = timedelta(days=1)


class AuthAdminRefused(ValueError):
    """The request is unsafe or no longer matches the reviewed plan; nothing was written."""


@dataclass(frozen=True)
class PrunePlan:
    database: str
    cutoff: datetime
    rows: int

    @property
    def pending(self) -> int:
        return self.rows


def prune_cutoff(now: datetime, older_than_days: int) -> datetime:
    if older_than_days < MIN_PRUNE_AGE.days:
        raise AuthAdminRefused(f"--older-than-days must be at least {MIN_PRUNE_AGE.days}")
    return (now - timedelta(days=older_than_days)).replace(microsecond=0)


def _as_owner(cur: Any) -> str:
    cur.execute("set local role origenlab_owner")
    cur.execute("select current_database()")
    return cur.fetchone()[0]


def _count_prunable(cur: Any, cutoff: datetime) -> int:
    cur.execute("select count(*) from platform.auth_session where expires_at < %s", (cutoff,))
    return int(cur.fetchone()[0])


def plan_prune(conn: Any, *, older_than_days: int) -> PrunePlan:
    """Read-only: how many session rows expired before the cutoff."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        database = _as_owner(cur)
        cur.execute("select now()")
        cutoff = prune_cutoff(cur.fetchone()[0], older_than_days)
        return PrunePlan(database, cutoff, _count_prunable(cur, cutoff))


def apply_prune(
    conn: Any, *, cutoff: datetime, expected_changes: int, expected_database: str
) -> PrunePlan:
    """Delete exactly the rows the reviewed plan counted, before exactly its cutoff."""
    with conn.transaction(), conn.cursor() as cur:
        database = _as_owner(cur)
        if database != expected_database:
            raise AuthAdminRefused(
                f"connected to database {database!r}, not the {expected_database!r} you confirmed")
        cur.execute("select now()")
        if cutoff > cur.fetchone()[0] - MIN_PRUNE_AGE:
            raise AuthAdminRefused("the cutoff is too recent: a row is pruned a day after it expired at the earliest")
        rows = _count_prunable(cur, cutoff)
        if rows != expected_changes:
            raise AuthAdminRefused(
                f"the plan has {rows} change(s), not the {expected_changes} you confirmed; "
                "re-run the plan and review it again")
        cur.execute("delete from platform.auth_session where expires_at < %s", (cutoff,))
        return PrunePlan(database, cutoff, rows)


def describe_prune(result: PrunePlan) -> list[str]:
    cutoff = result.cutoff.astimezone(timezone.utc).isoformat()
    lines = [
        f"database: {result.database}",
        f"session rows that expired before {cutoff}: {result.rows}",
        f"pending changes: {result.pending}",
    ]
    lines.append(
        f"to apply this plan: prune-sessions --apply --confirm-changes {result.pending} "
        f"--confirm-database {result.database} --confirm-cutoff {cutoff}"
        if result.pending else "nothing to apply")
    return lines


def parse_cutoff(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AuthAdminRefused("--confirm-cutoff must be the ISO timestamp the plan printed") from exc
    if value.tzinfo is None:
        raise AuthAdminRefused("--confirm-cutoff must carry its UTC offset, as the plan printed it")
    return value
