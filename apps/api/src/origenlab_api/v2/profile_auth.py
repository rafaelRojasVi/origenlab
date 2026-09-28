"""Operator profiles behind a shared Google sign-in: lookups, the PIN check, the throttle, the audit.

`docs/ARCHITECTURE.md` §5.1, `docs/DOMAIN.md` §7.3. A shared Google Workspace account signs in
as a *principal* (`platform.auth_principal`); the person using it is the `platform.operator`
they select through a profile (`platform.operator_profile`) with their own PIN. Everything the
database does for that lives here, as the `origenlab_api` runtime role, which can read the three
tables, update only their throttle columns and append audit events — never a PIN hash.

**The throttle is the database's, not the process's**, so every API worker shares it. Before a
PIN is checked the principal row and the requested profile row are taken `for update` — in that
order, always, so two attempts cannot deadlock — and the counters are read, compared with the
database clock and written back in the same transaction. Two concurrent attempts through two
workers therefore serialize, and neither can read a counter the other is about to raise.

Policy (documented for operators in `apps/api/docs/PRODUCTION_AUTH.md`):

* **Per profile**: :data:`PROFILE_MAX_FAILURES` consecutive failures lock that profile.
* **Per principal**: :data:`PRINCIPAL_MAX_FAILURES` consecutive failures, across all of its
  profiles and unknown profile ids, lock every profile of that principal.
* A lock lasts :data:`LOCK_BASE` × 2^(earlier lockouts), capped at :data:`LOCK_MAX`.
* A failure older than :data:`FAILURE_WINDOW` no longer counts; a lockout older than
  :data:`LOCKOUT_MEMORY` no longer doubles the next one.
* An attempt while locked is refused **without counting**, and without checking the PIN.
* A success resets the selected profile's counters and the principal's failure count,
  but keeps the principal's lockout history. It never resets another profile's, so
  knowing one PIN never helps guess another.

Every outcome — success, refusal, lockout — writes one `platform.auth_event`. None of them
carries the PIN; the refusal reason is a closed vocabulary the table enforces.

The public answer to every refusal is the same (`profile_routes.py`): a caller cannot tell an
unknown profile from a wrong PIN, a disabled profile or a lock, and the time taken is one Argon2
verification in every case (`PinHasher.dummy_verify`).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterator

from origenlab_api.v2.profile_pin import NEW_PIN_RE, PinHasher

#: Roles that may use the dashboard at all (`docs/OPERATIONS.md` §2).
DASHBOARD_ROLES = ("viewer", "sales", "admin")
#: What the profile screen shows for a role. Never the raw value, never anything more.
ROLE_LABELS = {"admin": "Administración", "sales": "Ventas", "viewer": "Lectura"}

PROFILE_MAX_FAILURES = 5
PRINCIPAL_MAX_FAILURES = 10
LOCK_BASE = timedelta(minutes=15)
LOCK_MAX = timedelta(hours=24)
FAILURE_WINDOW = timedelta(hours=1)
LOCKOUT_MEMORY = timedelta(hours=24)

#: The closed refusal vocabulary of `platform.auth_event.refusal_reason`.
REFUSAL_REASONS = ("pin_mismatch", "locked", "unknown_profile", "profile_inactive", "malformed_pin")


@dataclass(frozen=True)
class PrincipalRecord:
    principal_id: str
    email_norm: str
    provider_subject: str | None
    status: str
    version: int

    @property
    def is_active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class ProfileBinding:
    """A profile of a principal and its operator, as the database has them now."""

    operator_id: str
    display_name: str
    role: str
    operator_status: str
    operator_version: int
    profile_status: str
    profile_version: int

    @property
    def is_usable(self) -> bool:
        return (self.operator_status == "active" and self.profile_status == "active"
                and self.role in DASHBOARD_ROLES)


@dataclass(frozen=True)
class ProfileCard:
    operator_id: str
    display_name: str
    role: str

    def public(self) -> dict[str, str]:
        return {"id": self.operator_id, "display_name": self.display_name,
                "role_label": ROLE_LABELS.get(self.role, "Perfil")}


@dataclass(frozen=True)
class SelectionOutcome:
    """What a selection attempt came to. `reason` is internal; the caller shows none of it."""

    selected: ProfileBinding | None
    reason: str | None
    principal_valid: bool = True

    @property
    def ok(self) -> bool:
        return self.selected is not None


def parse_uuid(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def lock_duration(earlier_lockouts: int) -> timedelta:
    return min(LOCK_BASE * (2 ** min(earlier_lockouts, 16)), LOCK_MAX)


@dataclass(frozen=True)
class _Throttle:
    failed_attempts: int
    lockout_count: int
    locked_until: datetime | None
    last_failed_at: datetime | None

    def locked(self, now: datetime) -> bool:
        return self.locked_until is not None and self.locked_until > now

    def after_failure(self, now: datetime, limit: int) -> tuple["_Throttle", bool]:
        """The counters after one more failure, and whether this failure started a lock."""
        recent = self.last_failed_at is not None and now - self.last_failed_at < FAILURE_WINDOW
        remembered = self.last_failed_at is not None and now - self.last_failed_at < LOCKOUT_MEMORY
        failed = (self.failed_attempts if recent else 0) + 1
        lockouts = self.lockout_count if remembered else 0
        if failed >= limit:
            return _Throttle(0, lockouts + 1, now + lock_duration(lockouts), now), True
        return _Throttle(failed, lockouts, None, now), False


_CLEARED = _Throttle(0, 0, None, None)
_THROTTLE_TARGETS = frozenset({("auth_principal", "id"), ("operator_profile", "operator_id")})


class ProfileAuthRepository:
    """The profile sign-in queries, as `origenlab_api`."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 10_000) -> None:
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

    @contextmanager
    def _write(self) -> Iterator[Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                except BaseException:
                    conn.rollback()
                    raise
                conn.commit()

    # ------------------------------------------------------------------ reads

    def principal_by_email(self, email_norm: str) -> PrincipalRecord | None:
        with self._read() as cur:
            cur.execute(
                "select id::text, email_norm, provider_subject, status, version "
                "from platform.auth_principal where provider = 'google' and email_norm = %s",
                (email_norm,),
            )
            row = cur.fetchone()
        return PrincipalRecord(*row) if row else None

    def binding(
        self, principal_id: str, operator_id: str | None
    ) -> tuple[PrincipalRecord | None, ProfileBinding | None]:
        """The principal, and the profile of it named by `operator_id` if there is one.

        One statement, so the three rows are read at one instant. A profile of another
        principal is simply not found: the join is on both ids.
        """
        pid = parse_uuid(principal_id)
        if pid is None:
            return None, None
        oid = parse_uuid(operator_id) if operator_id is not None else None
        with self._read() as cur:
            cur.execute(
                """
                select a.id::text, a.email_norm, a.provider_subject, a.status, a.version,
                       p.operator_id::text, o.display_name, o.role, o.status, o.version,
                       p.status, p.version
                  from platform.auth_principal a
                  left join platform.operator_profile p
                    on p.principal_id = a.id and p.operator_id = %s::uuid
                  left join platform.operator o on o.id = p.operator_id
                 where a.id = %s::uuid
                """,
                (oid, pid),
            )
            row = cur.fetchone()
        if row is None:
            return None, None
        principal = PrincipalRecord(*row[:5])
        if row[5] is None:
            return principal, None
        return principal, ProfileBinding(*row[5:])

    def list_profiles(self, principal_id: str) -> list[ProfileCard]:
        """The usable profiles of one principal, in card order. Nothing of any other."""
        pid = parse_uuid(principal_id)
        if pid is None:
            return []
        with self._read() as cur:
            cur.execute(
                """
                select p.operator_id::text, o.display_name, o.role
                  from platform.operator_profile p
                  join platform.operator o on o.id = p.operator_id
                 where p.principal_id = %s::uuid
                   and p.status = 'active' and o.status = 'active'
                   and o.role = any(%s)
                 order by p.sort_order, o.display_name, p.operator_id
                """,
                (pid, list(DASHBOARD_ROLES)),
            )
            return [ProfileCard(*row) for row in cur.fetchall()]

    # ------------------------------------------------------------------ the attempt

    def select_profile(
        self,
        *,
        principal_id: str,
        principal_email: str,
        principal_version: int,
        requested_operator_id: Any,
        pin: Any,
        hasher: PinHasher,
        previous_operator_id: str | None = None,
    ) -> SelectionOutcome:
        """Check one PIN for one profile of one principal, under the shared throttle.

        Exactly one Argon2 verification happens on every path that reaches the principal row,
        real or decoy, so the time taken says nothing about why an attempt failed.
        """
        pid = parse_uuid(principal_id)
        if pid is None:
            hasher.dummy_verify()
            return SelectionOutcome(None, None, principal_valid=False)
        with self._write() as cur:
            cur.execute(
                """
                select email_norm, status, version, failed_attempts, lockout_count,
                       locked_until, last_failed_at, now()
                  from platform.auth_principal where id = %s::uuid for update
                """,
                (pid,),
            )
            prow = cur.fetchone()
            if (prow is None or prow[1] != "active" or prow[2] != principal_version
                    or prow[0] != principal_email):
                hasher.dummy_verify()
                return SelectionOutcome(None, None, principal_valid=False)
            now: datetime = prow[7]
            principal_throttle = _Throttle(*prow[3:7])

            oid = parse_uuid(requested_operator_id)
            candidate: ProfileBinding | None = None
            pin_hash: str | None = None
            profile_throttle: _Throttle | None = None
            if oid is not None:
                cur.execute(
                    """
                    select p.operator_id::text, o.display_name, o.role, o.status, o.version,
                           p.status, p.version, p.pin_hash,
                           p.failed_attempts, p.lockout_count, p.locked_until, p.last_failed_at
                      from platform.operator_profile p
                      join platform.operator o on o.id = p.operator_id
                     where p.operator_id = %s::uuid and p.principal_id = %s::uuid
                       for update of p
                    """,
                    (oid, pid),
                )
                row = cur.fetchone()
                if row is not None:
                    candidate = ProfileBinding(*row[:7])
                    pin_hash = row[7]
                    profile_throttle = _Throttle(*row[8:12])

            locked = principal_throttle.locked(now) or (
                profile_throttle is not None and profile_throttle.locked(now))
            well_formed = isinstance(pin, str) and bool(NEW_PIN_RE.match(pin))
            if candidate is not None and not locked and well_formed:
                verified = hasher.verify(pin, pin_hash)
            else:
                hasher.dummy_verify()
                verified = False

            if verified and candidate is not None and candidate.is_usable:
                # The principal's failure count restarts, but its lockout history is kept (it
                # still decays after LOCKOUT_MEMORY): knowing one PIN must not reset the
                # principal-wide backoff while another profile is being guessed.
                self._write_throttle(cur, "auth_principal", "id", pid, _Throttle(
                    0, principal_throttle.lockout_count, None, principal_throttle.last_failed_at))
                self._write_throttle(cur, "operator_profile", "operator_id", candidate.operator_id,
                                     _CLEARED)
                self._event(cur, "profile.selected", pid, principal_email,
                            operator_id=candidate.operator_id,
                            previous_operator_id=parse_uuid(previous_operator_id))
                return SelectionOutcome(candidate, None)

            if locked:
                reason = "locked"
            elif candidate is None:
                reason = "unknown_profile"
            elif not well_formed:
                reason = "malformed_pin"
            elif not verified:
                reason = "pin_mismatch"
            else:
                reason = "profile_inactive"
            operator_ref = candidate.operator_id if candidate is not None else None
            self._event(cur, "profile.selection_refused", pid, principal_email,
                        operator_id=operator_ref, refusal_reason=reason)
            if not locked:
                new_principal, principal_locked = principal_throttle.after_failure(
                    now, PRINCIPAL_MAX_FAILURES)
                self._write_throttle(cur, "auth_principal", "id", pid, new_principal)
                if principal_locked:
                    self._event(cur, "profile.locked", pid, principal_email)
                if candidate is not None and profile_throttle is not None:
                    new_profile, profile_locked = profile_throttle.after_failure(
                        now, PROFILE_MAX_FAILURES)
                    self._write_throttle(cur, "operator_profile", "operator_id",
                                         candidate.operator_id, new_profile)
                    if profile_locked:
                        self._event(cur, "profile.locked", pid, principal_email,
                                    operator_id=candidate.operator_id)
            return SelectionOutcome(None, reason)

    # ------------------------------------------------------------------ audit

    def record_event(
        self,
        event_type: str,
        *,
        principal_id: str | None,
        principal_email: str | None,
        operator_id: str | None = None,
        previous_operator_id: str | None = None,
    ) -> None:
        """Append one audit event (profile cleared, logout) in its own transaction."""
        with self._write() as cur:
            self._event(cur, event_type, parse_uuid(principal_id), principal_email,
                        operator_id=parse_uuid(operator_id),
                        previous_operator_id=parse_uuid(previous_operator_id))

    @staticmethod
    def _event(
        cur: Any,
        event_type: str,
        principal_id: str | None,
        principal_email: str | None,
        *,
        operator_id: str | None = None,
        previous_operator_id: str | None = None,
        refusal_reason: str | None = None,
    ) -> None:
        cur.execute(
            """
            insert into platform.auth_event
              (event_type, principal_id, principal_email_norm, operator_id, previous_operator_id,
               refusal_reason)
            values (%s, %s::uuid, %s, %s::uuid, %s::uuid, %s)
            """,
            (event_type, principal_id, principal_email if principal_id else None, operator_id,
             previous_operator_id, refusal_reason),
        )

    @staticmethod
    def _write_throttle(cur: Any, table: str, key: str, key_value: str, t: _Throttle) -> None:
        # `table` and `key` are two fixed pairs chosen above, never caller input; an explicit
        # check (not an `assert`, which `python -O` strips) keeps it that way.
        if (table, key) not in _THROTTLE_TARGETS:
            raise ValueError("unexpected throttle target")
        cur.execute(
            f"update platform.{table} set failed_attempts = %s, lockout_count = %s, "
            f"locked_until = %s, last_failed_at = %s where {key} = %s::uuid",
            (t.failed_attempts, t.lockout_count, t.locked_until, t.last_failed_at, key_value),
        )
