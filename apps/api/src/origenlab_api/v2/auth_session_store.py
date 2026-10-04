"""Revocable sessions of an operator's own Google account — `platform.auth_session` rows.

`20260928195000`. A shared sign-in's session has been a database row since `20260928191000`
(`profile_auth.py`); an individual operator's session now is one too, in the same table with
`sign_in_kind = 'google_account'`, so that logout ends it everywhere and a copied cookie stops
working on its next request, on any API instance.

As the `origenlab_api` runtime role: it inserts a row at sign-in, reads it (joined to the
operator, in one statement, against the database clock) on every request, and revokes it at
logout together with the `session.logout` audit event, in one transaction. It never deletes a
row — ended rows are pruned by the migrator (`scripts/profile_auth_admin.py prune-sessions`) —
and it stores only the keyed hash of the cookie's session identifier
(:meth:`CookieSigner.session_token_hash`), never the identifier.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.profile_auth import parse_uuid


@dataclass(frozen=True)
class OperatorSessionRow:
    """The row behind an operator session and its operator, as the database has them now."""

    live: bool
    operator: OperatorIdentity


class AuthSessionStore:
    """`platform.auth_session` for `google_account` sessions, as `origenlab_api`."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 10_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _cursor(self, *, read_only: bool) -> Iterator[Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                if read_only:
                    # Pipeline both setup statements: 1 RTT instead of 2.
                    with conn.pipeline():
                        cur.execute("set transaction read only")
                        cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                else:
                    cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                except BaseException:
                    conn.rollback()
                    raise
                if read_only:
                    conn.rollback()
                else:
                    conn.commit()

    def open_operator_session(self, *, operator_id: str, token_hash: bytes, expires_at: int) -> None:
        """Record a new session of this operator, expiring at `expires_at` (epoch seconds)."""
        oid = parse_uuid(operator_id)
        if oid is None:
            raise ValueError("not an operator id")
        with self._cursor(read_only=False) as cur:
            cur.execute(
                "insert into platform.auth_session "
                "  (token_hash, sign_in_kind, account_operator_id, expires_at) "
                "values (%s, 'google_account', %s::uuid, to_timestamp(%s))",
                (token_hash, oid, int(expires_at)),
            )

    def operator_session(self, token_hash: bytes) -> OperatorSessionRow | None:
        """The session row named by `token_hash` and its operator, or None when there is none."""
        with self._cursor(read_only=True) as cur:
            cur.execute(
                """
                select s.revoked_at is null and s.expires_at > now(),
                       o.id::text, o.email_norm, o.display_name, o.role, o.status, o.version
                  from platform.auth_session s
                  join platform.operator o
                    on o.id = s.account_operator_id and o.sign_in_kind = s.sign_in_kind
                 where s.token_hash = %s and s.sign_in_kind = 'google_account'
                """,
                (token_hash,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return OperatorSessionRow(
            live=bool(row[0]),
            operator=OperatorIdentity(operator_id=row[1], email_norm=row[2], display_name=row[3],
                                      role=row[4], status=row[5], version=row[6]),
        )

    def revoke_operator_session(self, *, token_hash: bytes, operator_id: str) -> None:
        """Logout: revoke this exact row and record it, in one transaction.

        Raises on any database failure, so the caller can say the revocation did not happen. A
        row already revoked or expired is left as it is; the logout is still recorded.
        """
        oid = parse_uuid(operator_id)
        if oid is None:
            raise ValueError("not an operator id")
        with self._cursor(read_only=False) as cur:
            cur.execute(
                "update platform.auth_session set revoked_at = now(), revoked_reason = 'logout' "
                "where token_hash = %s and sign_in_kind = 'google_account' "
                "  and account_operator_id = %s::uuid and revoked_at is null",
                (token_hash, oid),
            )
            cur.execute(
                "insert into platform.auth_event (event_type, operator_id) "
                "values ('session.logout', %s::uuid)",
                (oid,),
            )
