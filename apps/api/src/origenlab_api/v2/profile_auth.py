"""Operator profiles behind a shared Google sign-in: lookups, the PIN check, the throttle, the audit.

`docs/ARCHITECTURE.md` §5.1, `docs/DOMAIN.md` §7.3. A shared Google Workspace account signs in
as a *principal* (`platform.auth_principal`); the person using it is the `platform.operator`
they select through a profile (`platform.operator_profile`) with their own PIN. Everything the
database does for that lives here, as the `origenlab_api` runtime role, which can read the three
tables and append audit events — never write a PIN hash, and never write a throttle column.

**The throttle is the database's, not the process's**, so every API worker shares it — and it
is written only by `platform.record_pin_attempt` (`20260928194000`), a SECURITY DEFINER function
on the closed list of `docs/ARCHITECTURE.md` §6.2. This module names *what happened*
(`begin_attempt`, `record_failure`, `record_success`) and nothing else: the counters, the lock
deadline and the timestamps are computed inside the function, from the rows it has locked and
the database clock. Before a PIN is checked, `begin_attempt` takes the principal row and the
requested profile row `for update` — in that order, always, so two attempts cannot deadlock —
and reports whether either is locked; the locks are held until this transaction ends. Two
concurrent attempts through two workers therefore serialize, and neither can read a lock state
the other is about to change.

Policy (computed by the function; documented for operators in `apps/api/docs/PRODUCTION_AUTH.md`,
and mirrored by the constants below, which the database tests hold it to):

* **Per profile**: :data:`PROFILE_MAX_FAILURES` consecutive failures lock that profile.
* **Per principal**: :data:`PRINCIPAL_MAX_FAILURES` consecutive failures, across all of its
  profiles and unknown profile ids, lock every profile of that principal.
* A lock lasts :data:`LOCK_BASE` × 2^(earlier lockouts), capped at :data:`LOCK_MAX`.
* A failure older than :data:`FAILURE_WINDOW` no longer counts; a lockout older than
  :data:`LOCKOUT_MEMORY` no longer doubles the next one.
* An attempt while locked is refused **without counting**, and the submitted PIN is never
  checked; one Argon2 derivation is still spent, on an empty input against the profile's own
  hash (:meth:`PinHasher.dummy_verify`).
* A success resets the selected profile's counters and the principal's failure count,
  but keeps the principal's lockout history. It never resets another profile's, so
  knowing one PIN never helps guess another. The function refuses a success while locked.

Every outcome — success, refusal, lockout — writes one `platform.auth_event`. None of them
carries the PIN; the refusal reason is a closed vocabulary the table enforces.

The public answer to every refusal is the same (`profile_routes.py`): status, body and cookies
do not tell an unknown profile from a wrong PIN, a disabled profile or a lock.

**The time taken is not claimed to be the same.** Every path that reaches the principal row
spends one Argon2 derivation — against the requested profile's own hash when there is one (so at
its parameters), against the decoy otherwise — which removes the dominant difference. The
database work still differs by outcome: an unknown profile id locks one row, a known one two; a
locked attempt writes no counters; a failure that starts a lock writes a second audit row; a
success rotates the session. No `/auth/*` response carries a timing header (`response_timing.py`), but
wall-clock time is observable anyway. So a lock, or whether a profile id exists, may be told
apart by timing. Neither reveals a PIN, and a lock bounds the guessing that matters.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
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
    provider_issuer: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def has_pinned_account(self) -> bool:
        """True when the row names the Google account itself (issuer + subject), not only an address."""
        return self.provider_subject is not None and self.provider_issuer is not None

    def is_account(self, issuer: str, subject: str) -> bool:
        """True only when the row is pinned to exactly this issuer and subject."""
        return (self.has_pinned_account and self.provider_issuer == issuer
                and self.provider_subject == subject)


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
class SessionState:
    """The `platform.auth_session` row behind a principal session, as the database has it now."""

    live: bool
    operator_id: str | None


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
    """The lock `platform.record_pin_attempt` imposes after `earlier_lockouts` remembered ones."""
    return min(LOCK_BASE * (2 ** min(earlier_lockouts, 16)), LOCK_MAX)


#: The closed operations of `platform.record_pin_attempt`.
THROTTLE_OPERATIONS = ("begin_attempt", "record_failure", "record_success")


@dataclass(frozen=True)
class ThrottleState:
    """What `platform.record_pin_attempt` reports: each row's lock as found, and any lock it started."""

    principal_locked: bool
    profile_locked: bool
    principal_lock_started: bool
    profile_lock_started: bool

    @property
    def locked(self) -> bool:
        return self.principal_locked or self.profile_locked


_PRINCIPAL_COLUMNS = "id::text, email_norm, provider_subject, status, version, provider_issuer"


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
        """By address alone — the local development shortcut only. Google sign-in uses
        :meth:`principals_for_google`, which also matches the account's issuer and subject."""
        with self._read() as cur:
            cur.execute(
                f"select {_PRINCIPAL_COLUMNS} "
                "from platform.auth_principal where provider = 'google' and email_norm = %s",
                (email_norm,),
            )
            row = cur.fetchone()
        return PrincipalRecord(*row) if row else None

    def principals_for_google(self, email_norm: str, issuer: str, subject: str) -> list[PrincipalRecord]:
        """Every principal the verified token could mean: by its address, or by its account.

        The caller admits the token only when this is exactly one row that is both — the
        address *and* the pinned issuer and subject. A row that matches one and not the other
        (a recreated account under the same address; a renamed account) is a refusal, never a
        fall-through to the individual-operator path.
        """
        with self._read() as cur:
            cur.execute(
                f"select {_PRINCIPAL_COLUMNS} from platform.auth_principal "
                "where provider = 'google' "
                "  and (email_norm = %s or (provider_issuer = %s and provider_subject = %s)) "
                "order by id",
                (email_norm, issuer, subject),
            )
            return [PrincipalRecord(*row) for row in cur.fetchall()]

    def binding(
        self, principal_id: str, operator_id: str | None, token_hash: bytes | None = None
    ) -> tuple[PrincipalRecord | None, ProfileBinding | None, SessionState | None]:
        """The principal, the profile of it named by `operator_id`, and the session row.

        One statement, so the four rows are read at one instant, against the database clock. A
        profile of another principal is simply not found (the join is on both ids), and so is
        a session row of another principal.
        """
        pid = parse_uuid(principal_id)
        if pid is None:
            return None, None, None
        oid = parse_uuid(operator_id) if operator_id is not None else None
        with self._read() as cur:
            cur.execute(
                """
                select a.id::text, a.email_norm, a.provider_subject, a.status, a.version,
                       a.provider_issuer,
                       s.id is not null, coalesce(s.revoked_at is null and s.expires_at > now(), false),
                       s.operator_id::text,
                       p.operator_id::text, o.display_name, o.role, o.status, o.version,
                       p.status, p.version
                  from platform.auth_principal a
                  left join platform.operator_profile p
                    on p.principal_id = a.id and p.operator_id = %s::uuid
                  left join platform.operator o on o.id = p.operator_id
                  left join platform.auth_session s
                    on s.token_hash = %s and s.principal_id = a.id
                 where a.id = %s::uuid
                """,
                (oid, token_hash, pid),
            )
            row = cur.fetchone()
        if row is None:
            return None, None, None
        principal = PrincipalRecord(*row[:6])
        session = SessionState(live=bool(row[7]), operator_id=row[8]) if row[6] else None
        if row[9] is None:
            return principal, None, session
        return principal, ProfileBinding(*row[9:]), session

    # ------------------------------------------------------------------ sessions

    def open_session(self, *, principal_id: str, token_hash: bytes, expires_at: int) -> None:
        """Record a new principal session (no profile yet), expiring at `expires_at` (epoch)."""
        pid = parse_uuid(principal_id)
        if pid is None:
            raise ValueError("not a principal id")
        with self._write() as cur:
            cur.execute(
                "insert into platform.auth_session (token_hash, principal_id, expires_at) "
                "values (%s, %s::uuid, to_timestamp(%s))",
                (token_hash, pid, int(expires_at)),
            )

    def clear_profile(
        self,
        *,
        principal_id: str,
        principal_email: str,
        token_hash: bytes,
        new_token_hash: bytes,
        previous_operator_id: str | None,
    ) -> bool:
        """«Cambiar perfil»: revoke this session and open its profile-less successor.

        The successor keeps the revoked row's `expires_at`, so the sign-in is never extended.
        False — nothing written — when the session is not live or no longer the one the cookie
        describes (revoked meanwhile, or rotated by another tab).
        """
        pid = parse_uuid(principal_id)
        if pid is None:
            return False
        with self._write() as cur:
            if not self._lock_live_session(cur, token_hash, pid, parse_uuid(previous_operator_id)):
                return False
            self._rotate(cur, token_hash, new_token_hash, pid, None, "profile_cleared")
            self._event(cur, "profile.cleared", pid, principal_email,
                        previous_operator_id=parse_uuid(previous_operator_id))
            return True

    def revoke_session(
        self,
        *,
        token_hash: bytes,
        principal_id: str,
        principal_email: str,
        operator_id: str | None,
    ) -> None:
        """Logout: revoke this exact session row and record it, in one transaction.

        Raises on any database failure, so the caller can say the revocation did not happen.
        A row already revoked or expired is left as it is; the logout is still recorded.
        """
        pid = parse_uuid(principal_id)
        with self._write() as cur:
            cur.execute(
                "update platform.auth_session set revoked_at = now(), revoked_reason = 'logout' "
                "where token_hash = %s and principal_id = %s::uuid and revoked_at is null",
                (token_hash, pid),
            )
            self._event(cur, "session.logout", pid, principal_email,
                        operator_id=parse_uuid(operator_id))

    @staticmethod
    def _lock_live_session(cur: Any, token_hash: bytes, principal_id: str,
                           operator_id: str | None) -> bool:
        cur.execute(
            """
            select operator_id::text, revoked_at is null and expires_at > now()
              from platform.auth_session
             where token_hash = %s and principal_id = %s::uuid
               for update
            """,
            (token_hash, principal_id),
        )
        row = cur.fetchone()
        return row is not None and bool(row[1]) and row[0] == operator_id

    @staticmethod
    def _rotate(cur: Any, token_hash: bytes, new_token_hash: bytes, principal_id: str,
                operator_id: str | None, reason: str) -> None:
        cur.execute(
            "update platform.auth_session set revoked_at = now(), revoked_reason = %s "
            "where token_hash = %s returning expires_at",
            (reason, token_hash),
        )
        (expires_at,) = cur.fetchone()
        cur.execute(
            "insert into platform.auth_session (token_hash, principal_id, operator_id, expires_at) "
            "values (%s, %s::uuid, %s::uuid, %s)",
            (new_token_hash, principal_id, operator_id, expires_at),
        )

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
        token_hash: bytes,
        new_token_hash: bytes,
        previous_operator_id: str | None = None,
    ) -> SelectionOutcome:
        """Check one PIN for one profile of one principal, under the shared throttle.

        `platform.record_pin_attempt('begin_attempt', …)` locks the principal row and, when the
        id names a profile of that principal, the profile row, before anything else is read;
        then the session row the request came with is locked too (principal → profile →
        session, always in that order) and must be live and still describe what the cookie
        says. On success the function records it, and the session row is revoked and its
        successor — carrying the selected operator and the same `expires_at` — is inserted with
        `new_token_hash`, in the same transaction.

        Exactly one Argon2 derivation happens on every path that reaches the principal row — the
        real check, or a dummy against the profile's own hash or the decoy — so the dominant
        cost does not depend on why an attempt failed. The rest of the work does (module
        docstring): this is not a constant-time claim.
        """
        pid = parse_uuid(principal_id)
        if pid is None:
            hasher.dummy_verify()
            return SelectionOutcome(None, None, principal_valid=False)
        oid = parse_uuid(requested_operator_id)
        with self._write() as cur:
            cur.execute("select 1 from platform.auth_principal where id = %s::uuid", (pid,))
            if cur.fetchone() is None:
                hasher.dummy_verify()
                return SelectionOutcome(None, None, principal_valid=False)
            throttle = self._throttle(cur, "begin_attempt", pid, oid)
            # The function holds the principal row (and the profile row) now, so what is read
            # below cannot change before this transaction ends.
            cur.execute(
                "select email_norm, status, version from platform.auth_principal where id = %s::uuid",
                (pid,),
            )
            prow = cur.fetchone()
            if (prow is None or prow[1] != "active" or prow[2] != principal_version
                    or prow[0] != principal_email
                    or not self._lock_live_session(cur, token_hash, pid,
                                                   parse_uuid(previous_operator_id))):
                hasher.dummy_verify()
                return SelectionOutcome(None, None, principal_valid=False)

            candidate: ProfileBinding | None = None
            pin_hash: str | None = None
            if oid is not None:
                cur.execute(
                    """
                    select p.operator_id::text, o.display_name, o.role, o.status, o.version,
                           p.status, p.version, p.pin_hash
                      from platform.operator_profile p
                      join platform.operator o on o.id = p.operator_id
                     where p.operator_id = %s::uuid and p.principal_id = %s::uuid
                    """,
                    (oid, pid),
                )
                row = cur.fetchone()
                if row is not None:
                    candidate = ProfileBinding(*row[:7])
                    pin_hash = row[7]

            locked = throttle.locked
            well_formed = isinstance(pin, str) and bool(NEW_PIN_RE.match(pin))
            if candidate is not None and not locked and well_formed:
                verified = hasher.verify(pin, pin_hash)
            else:
                # Locked, malformed or unknown: the submitted PIN is never checked. The cost is
                # still spent — at the profile's own parameters when there is a profile.
                hasher.dummy_verify(pin_hash)
                verified = False

            if verified and candidate is not None and candidate.is_usable:
                # The principal's failure count restarts, but its lockout history is kept (it
                # still decays after LOCKOUT_MEMORY): knowing one PIN must not reset the
                # principal-wide backoff while another profile is being guessed.
                self._throttle(cur, "record_success", pid, candidate.operator_id)
                self._rotate(cur, token_hash, new_token_hash, pid, candidate.operator_id,
                             "profile_selected")
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
                after = self._throttle(cur, "record_failure", pid, operator_ref)
                if after.principal_lock_started:
                    self._event(cur, "profile.locked", pid, principal_email)
                if after.profile_lock_started:
                    self._event(cur, "profile.locked", pid, principal_email,
                                operator_id=operator_ref)
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
    def _throttle(cur: Any, operation: str, principal_id: str,
                  operator_id: str | None) -> ThrottleState:
        """One closed throttle transition, computed and written by the database."""
        if operation not in THROTTLE_OPERATIONS:
            raise ValueError("unexpected throttle operation")
        cur.execute(
            "select principal_locked, profile_locked, principal_lock_started, profile_lock_started "
            "from platform.record_pin_attempt(%s, %s::uuid, %s::uuid)",
            (operation, principal_id, operator_id),
        )
        return ThrottleState(*(bool(v) for v in cur.fetchone()))
