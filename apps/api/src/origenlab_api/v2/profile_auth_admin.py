"""Migrator-only maintenance of shared sign-in: clearing a PIN lockout, pruning ended sessions.

`docs/ARCHITECTURE.md` §5.1, `apps/api/docs/PRODUCTION_AUTH.md`.

**Clearing a lockout.** A lock on a profile, or on the whole principal, expires by itself
(`profile_auth.py`). Clearing one early resets that target's four throttle columns and appends one
`platform.auth_event` `lockout.cleared` per target that was not already clear — an event the
runtime role cannot write (trigger `auth_event_actor_guard`). It changes no version, so nobody
already using a profile is signed out, and it never touches a PIN.

**Pruning sessions.** The runtime API role can insert
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

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from origenlab_api.v2.operator_roster import mask_email

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


# ------------------------------------------------------------------------ lockouts

_KEY = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class LockTarget:
    subject: str                 # "principal" or "profile <key>"
    operator_id: str | None      # the profile's operator; None for the principal-wide throttle
    failed_attempts: int
    lockout_count: int
    locked_until: datetime | None
    last_failed_at: datetime | None
    locked_now: bool

    @property
    def needs_clearing(self) -> bool:
        return bool(self.failed_attempts or self.lockout_count
                    or self.locked_until is not None or self.last_failed_at is not None)


@dataclass(frozen=True)
class LockoutPlan:
    database: str
    principal_email: str
    principal_id: str
    targets: tuple[LockTarget, ...]

    @property
    def pending(self) -> int:
        return sum(1 for t in self.targets if t.needs_clearing)


def _lockout_request(principal_email: str, profile_keys: list[str], include_principal: bool
                     ) -> tuple[str, tuple[str, ...]]:
    email = (principal_email or "").strip().lower()
    if not _EMAIL.match(email):
        raise AuthAdminRefused("--principal-email must be the shared sign-in's address")
    keys = tuple(dict.fromkeys(profile_keys))
    bad = [k for k in keys if not _KEY.match(k)]
    if bad:
        raise AuthAdminRefused(f"not profile keys: {bad!r}")
    if not keys and not include_principal:
        raise AuthAdminRefused("name what to clear: --profile KEY (repeatable) and/or --principal")
    return email, keys


def _read_lockouts(cur: Any, email: str, keys: tuple[str, ...], include_principal: bool,
                   *, lock: bool) -> tuple[str, list[LockTarget]]:
    suffix = " for update" if lock else ""
    cur.execute(
        "select id::text, failed_attempts, lockout_count, locked_until, last_failed_at, "
        "coalesce(locked_until > now(), false) from platform.auth_principal "
        "where provider = 'google' and email_norm = %s" + suffix,
        (email,),
    )
    row = cur.fetchone()
    if row is None:
        raise AuthAdminRefused(f"no shared sign-in principal {mask_email(email)} in this database")
    principal_id = row[0]
    targets = [LockTarget("principal", None, *row[1:])] if include_principal else []
    for key in keys:
        cur.execute(
            "select operator_id::text, failed_attempts, lockout_count, locked_until, last_failed_at, "
            "coalesce(locked_until > now(), false) from platform.operator_profile "
            "where principal_id = %s::uuid and profile_key = %s" + suffix,
            (principal_id, key),
        )
        prow = cur.fetchone()
        if prow is None:
            raise AuthAdminRefused(f"the principal has no profile {key!r}")
        targets.append(LockTarget(f"profile {key}", *prow))
    return principal_id, targets


def plan_clear_lockout(conn: Any, *, principal_email: str, profile_keys: list[str],
                       include_principal: bool) -> LockoutPlan:
    """Read-only: the throttle state of each named target, and how many need clearing."""
    email, keys = _lockout_request(principal_email, profile_keys, include_principal)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        database = _as_owner(cur)
        principal_id, targets = _read_lockouts(cur, email, keys, include_principal, lock=False)
        return LockoutPlan(database, email, principal_id, tuple(targets))


def apply_clear_lockout(conn: Any, *, principal_email: str, profile_keys: list[str],
                        include_principal: bool, expected_changes: int,
                        expected_database: str) -> LockoutPlan:
    """Clear exactly the reviewed targets, with one audit event each, in one transaction."""
    email, keys = _lockout_request(principal_email, profile_keys, include_principal)
    with conn.transaction(), conn.cursor() as cur:
        database = _as_owner(cur)
        if database != expected_database:
            raise AuthAdminRefused(
                f"connected to database {database!r}, not the {expected_database!r} you confirmed")
        # Principal row first, then profiles: the same order the sign-in path locks them in.
        principal_id, targets = _read_lockouts(cur, email, keys, include_principal, lock=True)
        result = LockoutPlan(database, email, principal_id, tuple(targets))
        if result.pending != expected_changes:
            raise AuthAdminRefused(
                f"the plan has {result.pending} change(s), not the {expected_changes} you confirmed; "
                "re-run the plan and review it again")
        for target in result.targets:
            if not target.needs_clearing:
                continue
            if target.operator_id is None:
                cur.execute(
                    "update platform.auth_principal set failed_attempts = 0, lockout_count = 0, "
                    "locked_until = null, last_failed_at = null where id = %s::uuid", (principal_id,))
            else:
                cur.execute(
                    "update platform.operator_profile set failed_attempts = 0, lockout_count = 0, "
                    "locked_until = null, last_failed_at = null where operator_id = %s::uuid",
                    (target.operator_id,))
            cur.execute(
                "insert into platform.auth_event (event_type, principal_id, principal_email_norm, operator_id) "
                "values ('lockout.cleared', %s::uuid, %s, %s::uuid)",
                (principal_id, email, target.operator_id))
        return result


def describe_lockout(result: LockoutPlan) -> list[str]:
    lines = [f"database: {result.database}", f"principal: {mask_email(result.principal_email)}"]
    for t in result.targets:
        if t.locked_now and t.locked_until is not None:
            state = f"LOCKED until {t.locked_until.astimezone(timezone.utc).isoformat()}"
        elif t.needs_clearing:
            state = "not locked, failures on record"
        else:
            state = "clear"
        lines.append(f"  {t.subject:<24} {state} (failures {t.failed_attempts}, lockouts {t.lockout_count})"
                     + ("  -> clear" if t.needs_clearing else ""))
    lines.append(f"pending changes: {result.pending}")
    lines.append(
        f"to apply this plan: clear-lockout … --apply --confirm-changes {result.pending} "
        f"--confirm-database {result.database}" if result.pending else "nothing to apply")
    return lines


# ------------------------------------------------------------------------ helpers


def parse_cutoff(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AuthAdminRefused("--confirm-cutoff must be the ISO timestamp the plan printed") from exc
    if value.tzinfo is None:
        raise AuthAdminRefused("--confirm-cutoff must carry its UTC offset, as the plan printed it")
    return value
