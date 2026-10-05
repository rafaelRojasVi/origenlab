"""Operator profiles behind a shared Google sign-in: lookups, the PIN check, the throttle, the audit.

`docs/ARCHITECTURE.md` §5.1, `docs/DOMAIN.md` §7.3. A shared Google Workspace account signs in
as a *principal* (`platform.auth_principal`); the person using it is the `platform.operator`
they select through a profile (`platform.operator_profile`) with their own PIN. Everything the
database does for that lives here, as the `origenlab_api` runtime role, which can read the three
tables (every column but the stored PIN verifier) and append the audit events that are not PIN
outcomes — never write a PIN hash, a throttle column or a PIN outcome event.

**The PIN is decided by the database, not the process** (`20260929100000`), so every API worker
shares one throttle and no SQL the runtime role can send is able to declare a success. An
attempt is two calls in one transaction, both SECURITY DEFINER functions on the closed list of
`docs/ARCHITECTURE.md` §6.2:

1. `platform.begin_pin_attempt` takes the principal row and the requested profile row
   `for update` — in that order, always, so two attempts cannot deadlock — and returns a
   :class:`~origenlab_api.v2.profile_pin.PinChallenge`: whether a lock refuses the attempt,
   the Argon2id parameters and salt to derive with (a decoy for an unknown, foreign or unusable
   profile), and a one-use attempt id and nonce. The locks are held until the transaction ends,
   so concurrent attempts through different workers serialize.
2. The API derives Argon2id(PIN, salt, pepper) and sends only an HMAC of it over the attempt
   (:meth:`~origenlab_api.v2.profile_pin.PinHasher.attempt_proof`).
3. `platform.finish_pin_attempt` compares that proof with the one it computes from the stored
   verifier, and itself writes the verdict, the counters, the lock and its duration, and exactly
   one `platform.auth_event` — all in the same statement. It returns only the verdict and the
   (internal) reason.

Policy (computed by the function; documented for operators in `apps/api/docs/PRODUCTION_AUTH.md`,
and mirrored by the constants below, which the database tests hold it to):

* **Per profile**: :data:`PROFILE_MAX_FAILURES` consecutive failures lock that profile.
* **Per principal**: :data:`PRINCIPAL_MAX_FAILURES` consecutive failures, across all of its
  profiles and unknown profile ids, lock every profile of that principal.
* A lock lasts :data:`LOCK_BASE` × 2^(earlier lockouts), capped at :data:`LOCK_MAX`.
* A failure older than :data:`FAILURE_WINDOW` no longer counts; a lockout older than
  :data:`LOCKOUT_MEMORY` no longer doubles the next one.
* An attempt while locked is refused **without counting**, and the submitted PIN is never
  used; one Argon2 derivation is still spent, on an empty input at the profile's own
  parameters.
* A malformed PIN is a counted failure (`malformed_pin`), never a free probe.
* A success resets the selected profile's counters and the principal's failure count,
  but keeps the principal's lockout history. It never resets another profile's, so
  knowing one PIN never helps guess another.

Every attempt that reaches `finish_pin_attempt` writes exactly one `platform.auth_event`:
`profile.selected`, `profile.selection_refused`, `profile.locked` or `principal.locked`. None
of them carries the PIN or the proof; the refusal reason is a closed vocabulary the table
enforces.

The public answer to every refusal is the same (`profile_routes.py`): status, body and cookies
do not tell an unknown profile from a wrong PIN, a disabled profile or a lock.

**The time taken is not claimed to be the same.** Every path that reaches the principal row
spends one Argon2 derivation — at the requested profile's own parameters when there is one, at
the principal's first profile's otherwise — which removes the dominant difference. The
database work still differs by outcome: an unknown profile id locks one row, a known one two; a
locked attempt writes no counters; a success rotates the session. No `/auth/*` response carries a
timing header (`response_timing.py`), but wall-clock time is observable anyway. So a lock, or whether a profile id exists, may be told
apart by timing. Neither reveals a PIN, and a lock bounds the guessing that matters.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterator

from origenlab_api.v2.profile_pin import NEW_PIN_RE, PinChallenge, PinHasher
from origenlab_api.v2.read_transaction import ReadSession, read_transaction

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
    """The lock `platform.finish_pin_attempt` imposes after `earlier_lockouts` remembered ones."""
    return min(LOCK_BASE * (2 ** min(earlier_lockouts, 16)), LOCK_MAX)


_PRINCIPAL_COLUMNS = "id::text, email_norm, provider_subject, status, version, provider_issuer"


class ProfileAuthRepository:
    """The profile sign-in queries, as `origenlab_api`."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 10_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self) -> Iterator[ReadSession]:
        """One read-only transaction (`read_transaction.py`). Every read here is one statement,
        sent as the session's final one: setup, statement and rollback in one round trip."""
        with read_transaction(self._connect, self._dsn, self._statement_timeout_ms) as session:
            yield session

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
        with self._read() as session:
            cur = session.final(
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
        with self._read() as session:
            cur = session.final(
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
        with self._read() as session:
            cur = session.final(
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
        with self._read() as session:
            cur = session.final(
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

        `platform.begin_pin_attempt` locks the principal row and, when the id names a profile of
        that principal, the profile row, before anything else is read; then the session row the
        request came with is locked too (principal → profile → session, always in that order)
        and must be live and still describe what the cookie says — otherwise the transaction is
        rolled back and nothing is counted. `platform.finish_pin_attempt` decides and records
        the attempt. On success the session row is revoked and its successor — carrying the
        selected operator and the same `expires_at` — is inserted with `new_token_hash`, in the
        same transaction.

        Exactly one Argon2 derivation happens on every path that reaches the principal row, so
        the dominant cost does not depend on why an attempt failed. The rest of the work does
        (module docstring): this is not a constant-time claim.
        """
        pid = parse_uuid(principal_id)
        if pid is None:
            hasher.dummy_verify()
            return SelectionOutcome(None, None, principal_valid=False)
        oid = parse_uuid(requested_operator_id)
        previous = parse_uuid(previous_operator_id)
        with self._write() as cur:
            cur.execute("select 1 from platform.auth_principal where id = %s::uuid", (pid,))
            if cur.fetchone() is None:
                hasher.dummy_verify()
                return SelectionOutcome(None, None, principal_valid=False)
            challenge = self._begin(cur, pid, oid)
            # The function holds the principal row (and the profile row) now, so what is read
            # below cannot change before this transaction ends.
            cur.execute(
                "select email_norm, status, version from platform.auth_principal where id = %s::uuid",
                (pid,),
            )
            prow = cur.fetchone()
            if (prow is None or prow[1] != "active" or prow[2] != principal_version
                    or prow[0] != principal_email
                    or not self._lock_live_session(cur, token_hash, pid, previous)):
                hasher.dummy_verify()
                cur.connection.rollback()  # the attempt is abandoned unfinished: nothing counted
                return SelectionOutcome(None, None, principal_valid=False)

            candidate: ProfileBinding | None = None
            if oid is not None:
                cur.execute(
                    """
                    select p.operator_id::text, o.display_name, o.role, o.status, o.version,
                           p.status, p.version
                      from platform.operator_profile p
                      join platform.operator o on o.id = p.operator_id
                     where p.operator_id = %s::uuid and p.principal_id = %s::uuid
                    """,
                    (oid, pid),
                )
                row = cur.fetchone()
                if row is not None:
                    candidate = ProfileBinding(*row)

            well_formed = isinstance(pin, str) and bool(NEW_PIN_RE.match(pin))
            # Locked or malformed: the submitted PIN is never used; the cost is still spent.
            proof = hasher.attempt_proof(pin if well_formed and not challenge.refused else None,
                                         challenge, principal_id=pid, operator_id=oid)
            selected, reason = self._finish(cur, challenge, pid, oid, proof)
            del proof
            if not selected:
                return SelectionOutcome(None, reason)
            if candidate is None:  # the database selected a profile this read did not find
                raise RuntimeError("profile selection disagrees with the profile read")
            self._rotate(cur, token_hash, new_token_hash, pid, candidate.operator_id,
                         "profile_selected")
            return SelectionOutcome(candidate, None)

    @staticmethod
    def _begin(cur: Any, principal_id: str, operator_id: str | None) -> PinChallenge:
        cur.execute(
            "select attempt_id::text, refused, memory_kib, iterations, lanes, salt, hash_length, nonce "
            "from platform.begin_pin_attempt(%s::uuid, %s::uuid)",
            (principal_id, operator_id),
        )
        row = cur.fetchone()
        return PinChallenge(row[0], bool(row[1]), int(row[2]), int(row[3]), int(row[4]),
                            str(row[5]), int(row[6]), bytes(row[7]))

    @staticmethod
    def _finish(cur: Any, challenge: PinChallenge, principal_id: str, operator_id: str | None,
                proof: bytes | None) -> tuple[bool, str | None]:
        cur.execute(
            "select selected, reason from platform.finish_pin_attempt(%s::uuid, %s::uuid, %s::uuid, %s)",
            (challenge.attempt_id, principal_id, operator_id, proof),
        )
        selected, reason = cur.fetchone()
        return bool(selected), reason

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
