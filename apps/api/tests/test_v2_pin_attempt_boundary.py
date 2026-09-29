"""The PIN attempt boundary (`20260929100000`), attacked as the real `origenlab_api` login.

Runs only with `ORIGENLAB_V2_TEST_DSN` and `ORIGENLAB_V2_API_TEST_DSN` (`v2_command_harness.py`),
on a disposable database built for this module. Every attack below is plain SQL sent over a
direct `origenlab_api` connection — the position of an injection, a leaked runtime password or a
bug in the API — without the PIN pepper or the session secret. The fixtures (roster, PINs, the
app wrapper) are the ones `test_v2_profile_login.py` uses.

What is proven:

* The throttle cannot be reset: 50 cycles of four wrong proofs, each followed by every reset the
  role could try, lock the profile at the fifth counted failure and never unlock it.
* `finish_pin_attempt` runs only after `begin_pin_attempt`, in the same transaction, once, for
  the exact principal and profile begun with; a foreign profile is an unknown one.
* The stored verifier is unreadable: not by `select *`, a named column, a view, `pg_stats`, a
  function's result or an error message.
* No proof succeeds without the PIN and the pepper: random bytes, a wrong pepper, the raw
  Argon2id output and a proof replayed from an earlier attempt all fail.
* The four PIN outcome events cannot be inserted directly, and every finish writes exactly one.
* Concurrent failures stop at the exact threshold, with the exact events.
* A failure injected at the final event write leaves neither the throttle change nor the event.
* Success, wrong PIN, malformed PIN and a lock behave as documented.
* No PIN, pepper, derived verifier or proof reaches a log, a response or an auth table.
* An `auth_session` row inserted by the runtime role is not a cookie: without the session
  secret it authenticates nothing.

Every name, address and PIN here is invented.
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import hmac
import json
import logging
import os
import subprocess
import time
import uuid
from typing import Any

import psycopg
import pytest

from origenlab_api.v2.auth_session import CookieSigner, PrincipalSession, ProfileSelection, new_session_id
from origenlab_api.v2.profile_auth import PRINCIPAL_MAX_FAILURES, PROFILE_MAX_FAILURES
from origenlab_api.v2.profile_pin import FLOOR, PinChallenge, PinHasher, attempt_message
from test_v2_profile_login import (  # noqa: F401 — fixtures are used by name
    PEPPER, PINS, REFUSED, SESSION_SECRET, SHARED, Api, _clean_throttle, _owner, _sql, db, ids,
)
from v2_command_harness import needs_db, runtime_dsn

pytestmark = needs_db

HASHER = PinHasher(PEPPER, FLOOR)
PROTECTED_EVENTS = ("profile.selected", "profile.selection_refused", "profile.locked", "principal.locked")


def _api(db: str) -> psycopg.Connection:
    return psycopg.connect(runtime_dsn(db))


def _begin(conn: psycopg.Connection, principal: str, operator: str | None) -> PinChallenge:
    row = conn.execute(
        "select attempt_id::text, refused, memory_kib, iterations, lanes, salt, hash_length, nonce "
        "from platform.begin_pin_attempt(%s::uuid, %s::uuid)", (principal, operator)).fetchone()
    return PinChallenge(row[0], row[1], row[2], row[3], row[4], row[5], row[6], bytes(row[7]))


def _finish(conn: psycopg.Connection, challenge: PinChallenge | str, principal: str, operator: str | None,
            proof: bytes | None) -> tuple[bool, str | None]:
    attempt = challenge.attempt_id if isinstance(challenge, PinChallenge) else challenge
    return conn.execute(
        "select selected, reason from platform.finish_pin_attempt(%s::uuid, %s::uuid, %s::uuid, %s)",
        (attempt, principal, operator, proof)).fetchone()


def _proof(pin: str, challenge: PinChallenge, principal: str, operator: str | None,
           hasher: PinHasher = HASHER) -> bytes:
    proof = hasher.attempt_proof(pin, challenge, principal_id=principal, operator_id=operator)
    assert proof is not None
    return proof


def _attempt(db: str, principal: str, operator: str | None, proof: Any = None, *,
             pin: str | None = None) -> tuple[bool, str | None]:
    """begin → finish → commit, on one fresh runtime connection."""
    with _api(db) as conn:
        challenge = _begin(conn, principal, operator)
        if pin is not None:
            proof = _proof(pin, challenge, principal, operator)
        result = _finish(conn, challenge, principal, operator, proof)
        conn.commit()
        return result


def _throttle(db: str, operator: str) -> tuple[int, int, bool]:
    return _sql(db, "select failed_attempts, lockout_count, coalesce(locked_until > now(), false) "
                    "from platform.operator_profile where operator_id = %s", (operator,))[0]


def _principal_throttle(db: str, principal: str) -> tuple[int, int, bool]:
    return _sql(db, "select failed_attempts, lockout_count, coalesce(locked_until > now(), false) "
                    "from platform.auth_principal where id = %s", (principal,))[0]


def _event_count(db: str) -> int:
    return _sql(db, "select count(*) from platform.auth_event")[0][0]


def _events_since(db: str, count: int) -> list[tuple[str, str | None, str | None]]:
    return _sql(db, "select event_type, operator_id::text, refusal_reason from platform.auth_event "
                    "order by occurred_at, id offset %s", (count,))


def _wrong() -> bytes:
    return os.urandom(32)


# ---------------------------------------------------------------- reset attempts, 50 cycles


def _reset_attempts(db: str, principal: str, operator: str) -> None:
    """Everything the runtime role might try to clear a throttle. Each must fail or change nothing."""
    for sql in (
        "update platform.operator_profile set failed_attempts = 0, locked_until = null",
        "update platform.auth_principal set failed_attempts = 0, locked_until = null",
        "update platform.auth_principal set pin_attempt_id = null",
        "select 1 from platform.operator_profile for update",
        "delete from platform.auth_event",
        "select * from platform.record_pin_attempt('record_success', null, null)",
    ):
        with _api(db) as conn, pytest.raises((psycopg.errors.InsufficientPrivilege,
                                              psycopg.errors.UndefinedFunction)):
            conn.execute(sql)
    with _api(db) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("insert into platform.auth_event (event_type, principal_id, principal_email_norm, operator_id) "
                     "values ('profile.selected', %s, %s, %s)", (principal, SHARED, operator))
    # A claimed success with a made-up proof, rolled back so it would not count if it failed:
    # it fails, and the rollback changes nothing.
    with _api(db) as conn:
        challenge = _begin(conn, principal, operator)
        assert _finish(conn, challenge, principal, operator, _wrong())[0] is False
        conn.rollback()
    # An abandoned begin, committed without a finish: it counts nothing and resets nothing.
    with _api(db) as conn:
        _begin(conn, principal, operator)
        conn.commit()


def test_fifty_cycles_of_four_wrong_proofs_never_reset_the_throttle(db, ids) -> None:
    principal, carla = ids["shared"], ids["carla"]
    before = _event_count(db)
    lock_seen = None
    for cycle in range(50):
        for _ in range(4):
            assert _attempt(db, principal, carla, _wrong())[0] is False
        _reset_attempts(db, principal, carla)
        failed, lockouts, locked = _throttle(db, carla)
        if cycle == 0:
            assert (failed, lockouts, locked) == (4, 0, False)
        else:
            assert (lockouts, locked) == (1, True), f"cycle {cycle}: the lock held"
            until = _sql(db, "select locked_until from platform.operator_profile where operator_id = %s",
                         (carla,))[0][0]
            lock_seen = lock_seen or until
            assert until == lock_seen, "guessing never extends or lifts the lock"
    events = _events_since(db, before)
    assert len(events) == 200, "one event per committed finish, none for anything else"
    assert [e for e in events if e[0] != "profile.selection_refused"] == [("profile.locked", carla, "pin_mismatch")]
    assert sum(1 for e in events if e[2] == "pin_mismatch") == PROFILE_MAX_FAILURES
    assert sum(1 for e in events if e[2] == "locked") == 200 - PROFILE_MAX_FAILURES
    assert _principal_throttle(db, principal)[0] == PROFILE_MAX_FAILURES, "a lock counts nothing further"
    assert not any(e[0] == "profile.selected" for e in events)


# ------------------------------------------------------------- the attempt is one-use and bound


def test_finish_without_begin_is_refused_and_changes_nothing(db, ids) -> None:
    before = (_event_count(db), _throttle(db, ids["ana"]))
    with _api(db) as conn, pytest.raises(psycopg.errors.NoDataFound, match="no such attempt"):
        _finish(conn, str(uuid.uuid4()), ids["shared"], ids["ana"], _wrong())
    assert (_event_count(db), _throttle(db, ids["ana"])) == before


def test_an_attempt_reference_is_never_reused(db, ids) -> None:
    principal, ana = ids["shared"], ids["ana"]
    with _api(db) as conn:
        challenge = _begin(conn, principal, ana)
        assert _finish(conn, challenge, principal, ana, _wrong()) == (False, "pin_mismatch")
        with pytest.raises(psycopg.errors.NoDataFound):
            _finish(conn, challenge, principal, ana, _wrong())  # same transaction, second finish
        conn.rollback()
    with _api(db) as conn:
        challenge = _begin(conn, principal, ana)
        _finish(conn, challenge, principal, ana, _wrong())
        conn.commit()
        with pytest.raises(psycopg.errors.NoDataFound):
            _finish(conn, challenge, principal, ana, _wrong())  # a later transaction
    with _api(db) as conn:
        challenge = _begin(conn, principal, ana)
        conn.commit()  # begun and committed, never finished
        with pytest.raises(psycopg.errors.NoDataFound):
            _finish(conn, challenge, principal, ana, _proof(PINS["ana"], challenge, principal, ana))
    assert _throttle(db, ana) == (1, 0, False), "only the one committed finish counted"


def test_a_proof_from_one_attempt_fails_in_the_next(db, ids) -> None:
    principal, ana = ids["shared"], ids["ana"]
    with _api(db) as conn:
        first = _begin(conn, principal, ana)
        captured = _proof(PINS["ana"], first, principal, ana)
        conn.rollback()  # never used
        second = _begin(conn, principal, ana)
        assert _finish(conn, second, principal, ana, captured) == (False, "pin_mismatch")
        conn.commit()


def test_a_wrong_principal_or_profile_pairing_is_refused(db, ids) -> None:
    principal, ana, bruno = ids["shared"], ids["ana"], ids["bruno"]
    other = _sql(db, "select principal_id::text from platform.operator_profile where operator_id = %s",
                 (ids["franco"],))[0][0]
    for finish_principal, finish_operator in ((principal, bruno), (principal, None), (other, ana)):
        with _api(db) as conn:
            challenge = _begin(conn, principal, ana)
            with pytest.raises(psycopg.errors.NoDataFound):
                _finish(conn, challenge, finish_principal, finish_operator,
                        _proof(PINS["ana"], challenge, principal, ana))
    assert _throttle(db, ana) == (0, 0, False)


def test_a_selection_records_no_caller_supplied_previous_profile(db, ids) -> None:
    # Nothing inside the database can verify which profile the caller's session held, so the
    # protected event carries none — and not even the owner may write one into it.
    principal, ana = ids["shared"], ids["ana"]
    with _api(db) as conn:
        challenge = _begin(conn, principal, ana)
        assert _finish(conn, challenge, principal, ana,
                       _proof(PINS["ana"], challenge, principal, ana)) == (True, None)
    assert _sql(db, "select previous_operator_id from platform.auth_event "
                    "where event_type = 'profile.selected' order by occurred_at desc limit 1")[0][0] is None
    with _owner(db) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("set role origenlab_owner")
        conn.execute("insert into platform.auth_event (event_type, principal_id, principal_email_norm, "
                     "operator_id, previous_operator_id) "
                     "select 'profile.selected', principal_id, email_norm, %s::uuid, %s::uuid "
                     "from platform.operator_profile p join platform.auth_principal a on a.id = p.principal_id "
                     "where p.operator_id = %s::uuid", (ana, ids["bruno"], ana))


def test_another_principals_profile_is_a_decoy_even_with_its_right_pin(db, ids) -> None:
    principal, franco = ids["shared"], ids["franco"]
    before = _throttle(db, franco)
    with _api(db) as conn:
        challenge = _begin(conn, principal, franco)
        real_salt = _sql(db, "select split_part(pin_hash, '$', 5) from platform.operator_profile "
                             "where operator_id = %s", (franco,))[0][0]
        assert challenge.salt != real_salt and challenge.refused is False
        assert _finish(conn, challenge, principal, franco, _proof(PINS["franco"], challenge, principal, franco)) \
            == (False, "unknown_profile")
        conn.commit()
    assert _throttle(db, franco) == before, "another principal's profile is never counted"
    assert _principal_throttle(db, principal)[0] == 1


@pytest.mark.parametrize("operator", ["unknown", "disabled", "foreign", None])
def test_every_decoy_has_the_real_cost_and_a_stable_salt(db, ids, operator) -> None:
    target = {"unknown": "00000000-0000-4000-8000-00000000beef", "disabled": ids["elena"],
              "foreign": ids["franco"], None: None}[operator]
    stored = _sql(db, "select pin_hash from platform.operator_profile where operator_id = %s", (ids["ana"],))[0][0]
    with _api(db) as conn:
        first = _begin(conn, ids["shared"], target)
        conn.rollback()
        second = _begin(conn, ids["shared"], target)
        conn.rollback()
    params = stored.split("$")[3]
    assert f"m={first.memory_kib},t={first.iterations},p={first.lanes}" == params
    assert first.hash_length == 32 and first.salt == second.salt, "a decoy looks like a real, stable profile"
    assert first.nonce != second.nonce and first.attempt_id != second.attempt_id


# ----------------------------------------------------------------------- the verifier is unreadable


def test_the_stored_verifier_is_unreadable_to_the_runtime_role(db, ids) -> None:
    stored = _sql(db, "select pin_hash from platform.operator_profile where operator_id = %s", (ids["ana"],))[0][0]
    digest_b64 = stored.split("$")[5]
    for sql in ("select * from platform.operator_profile",
                "select pin_hash from platform.operator_profile",
                "select p from platform.operator_profile p",
                "select row_to_json(p) from platform.operator_profile p",
                "select count(*) from platform.operator_profile where pin_hash like '$argon2id%'"):
        with _api(db) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege) as refused:
            conn.execute(sql)
        assert digest_b64 not in str(refused.value)
    with _api(db) as conn:
        assert conn.execute("select count(*) from platform.operator_profile where status = 'active'").fetchone()[0] >= 4
        # No view or materialized view the runtime role can read depends on the column.
        views = conn.execute(
            """
            select count(*) from pg_depend d
              join pg_rewrite r on r.oid = d.objid
              join pg_class v on v.oid = r.ev_class
             where d.refobjid = 'platform.operator_profile'::regclass
               and d.refobjsubid = (select attnum from pg_attribute
                                     where attrelid = 'platform.operator_profile'::regclass and attname = 'pin_hash')
               and has_table_privilege(v.oid, 'SELECT')
            """).fetchone()[0]
        assert views == 0
        # The only functions of the seven schemas the runtime role may call that read the table.
        callable_ = conn.execute(
            """
            select array_agg(p.proname::text order by p.proname) from pg_proc p
              join pg_namespace n on n.oid = p.pronamespace
             where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
               and has_function_privilege(p.oid, 'EXECUTE') and p.prosrc ilike '%operator_profile%'
            """).fetchone()[0]
        assert callable_ == ["begin_pin_attempt", "finish_pin_attempt"]
    _sql(db, "analyze platform.operator_profile")
    with _api(db) as conn:
        assert conn.execute("select count(*) from pg_stats where tablename = 'operator_profile' "
                            "and attname = 'pin_hash'").fetchone()[0] == 0, "pg_stats hides the column"
        for operator in (ids["ana"], ids["elena"], ids["franco"], None):
            row = conn.execute("select * from platform.begin_pin_attempt(%s::uuid, %s::uuid)",
                               (ids["shared"], operator)).fetchone()
            conn.rollback()
            assert digest_b64 not in repr(row) and stored not in repr(row)
            assert all(not (isinstance(v, (bytes, memoryview)) and bytes(v) in stored.encode()) for v in row)
        for args in ((None, None), (ids["shared"], "not-a-uuid")):
            with pytest.raises(psycopg.Error) as failed:
                conn.execute("select * from platform.begin_pin_attempt(%s::uuid, %s::uuid)", args)
            conn.rollback()
            assert digest_b64 not in str(failed.value)


# ----------------------------------------------------------- no success without the PIN and pepper


def test_no_proof_succeeds_without_the_pin_and_the_pepper(db, ids) -> None:
    principal, ana = ids["shared"], ids["ana"]
    wrong_pepper = PinHasher("another-pepper-" + "x1y2z3w4v5u6t7s8r9q0" * 2, FLOOR)

    def raw_output(challenge: PinChallenge) -> bytes:  # the unbound Argon2id output itself
        return HASHER._derive(PINS["ana"], base64.b64decode(challenge.salt + "=="),
                              m=challenge.memory_kib, t=challenge.iterations, p=challenge.lanes,
                              length=challenge.hash_length)

    forgeries = {
        "random": lambda c: _wrong(),
        "zeros": lambda c: bytes(32),
        "right pin, wrong pepper": lambda c: _proof(PINS["ana"], c, principal, ana, wrong_pepper),
        "raw verifier, unbound": raw_output,
        "hmac keyed with the salt": lambda c: hmac.new(c.salt.encode(), attempt_message(
            c, principal_id=principal, operator_id=ana), hashlib.sha256).digest(),
        "proof bound to another profile": lambda c: _proof(PINS["ana"], c, principal, ids["bruno"]),
    }
    started = _sql(db, "select clock_timestamp()")[0][0]
    for label, forge in forgeries.items():
        _clean_throttle_now(db)
        with _api(db) as conn:
            challenge = _begin(conn, principal, ana)
            assert _finish(conn, challenge, principal, ana, forge(challenge)) == (False, "pin_mismatch"), label
            conn.commit()
    assert _sql(db, "select count(*) from platform.auth_event where event_type = 'profile.selected' "
                    "and operator_id = %s and occurred_at >= %s", (ana, started))[0][0] == 0


def _clean_throttle_now(db: str) -> None:
    for table in ("auth_principal", "operator_profile"):
        _sql(db, f"update platform.{table} set failed_attempts = 0, lockout_count = 0, "
                 "locked_until = null, last_failed_at = null")


# --------------------------------------------------------------------------- the audit is unforgeable


@pytest.mark.parametrize("event_type", PROTECTED_EVENTS)
def test_a_pin_outcome_event_cannot_be_inserted_directly(db, ids, event_type) -> None:
    before = _event_count(db)
    with _api(db) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege, match="only by its own function"):
        conn.execute(
            "insert into platform.auth_event (event_type, principal_id, principal_email_norm, operator_id, "
            "refusal_reason) values (%s, %s, %s, %s, %s)",
            (event_type, ids["shared"], SHARED, ids["ana"],
             None if event_type == "profile.selected" else "pin_mismatch"))
    assert _event_count(db) == before


def test_the_api_still_records_its_own_two_events(db, ids) -> None:
    with _api(db) as conn:
        conn.execute("insert into platform.auth_event (event_type, principal_id, principal_email_norm) "
                     "values ('profile.cleared', %s, %s)", (ids["shared"], SHARED))
        conn.execute("insert into platform.auth_event (event_type, principal_id, principal_email_norm) "
                     "values ('session.logout', %s, %s)", (ids["shared"], SHARED))
        conn.rollback()


def test_every_finish_writes_exactly_one_event(db, ids) -> None:
    principal = ids["shared"]
    cases = [(ids["ana"], PINS["ana"], None), (ids["bruno"], None, _wrong()), (ids["carla"], None, None),
             (ids["elena"], PINS["elena"], None), ("00000000-0000-4000-8000-0000000000aa", None, _wrong()),
             (None, None, _wrong())]
    for operator, pin, proof in cases:
        before = _event_count(db)
        _attempt(db, principal, operator, proof, pin=pin)
        assert _event_count(db) == before + 1, operator
    with _api(db) as conn:  # a begun attempt rolled back, or never finished, writes none
        before = _event_count(db)
        _begin(conn, principal, ids["ana"])
        conn.commit()
        assert _event_count(db) == before


# ------------------------------------------------------------------------------- concurrency


def _concurrently(db: str, principal: str, operators: list[str | None]) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(operators)) as pool:
        list(pool.map(lambda op: _attempt(db, principal, op, _wrong()), operators))


def test_concurrent_failures_stop_at_the_exact_profile_threshold(db, ids) -> None:
    before = _event_count(db)
    _concurrently(db, ids["shared"], [ids["carla"]] * 12)
    assert _throttle(db, ids["carla"]) == (0, 1, True)
    assert _principal_throttle(db, ids["shared"])[0] == PROFILE_MAX_FAILURES
    kinds = sorted((e[0], e[2]) for e in _events_since(db, before))
    assert kinds == sorted([("profile.selection_refused", "pin_mismatch")] * (PROFILE_MAX_FAILURES - 1)
                           + [("profile.locked", "pin_mismatch")]
                           + [("profile.selection_refused", "locked")] * (12 - PROFILE_MAX_FAILURES))


def test_concurrent_failures_stop_at_the_exact_principal_threshold(db, ids) -> None:
    before = _event_count(db)
    unknown = [f"00000000-0000-4000-8000-{i:012d}" for i in range(15)]
    _concurrently(db, ids["shared"], unknown)
    assert _principal_throttle(db, ids["shared"]) == (0, 1, True)
    kinds = sorted((e[0], e[2]) for e in _events_since(db, before))
    assert kinds == sorted([("profile.selection_refused", "unknown_profile")] * (PRINCIPAL_MAX_FAILURES - 1)
                           + [("principal.locked", "unknown_profile")]
                           + [("profile.selection_refused", "locked")] * (15 - PRINCIPAL_MAX_FAILURES))
    assert _attempt(db, ids["shared"], ids["ana"], pin=PINS["ana"]) == (False, "locked")


def test_begin_holds_the_rows_until_the_attempt_ends(db, ids) -> None:
    first, second = _api(db), _api(db)
    try:
        challenge = _begin(first, ids["shared"], ids["carla"])
        second.execute("set lock_timeout = '300ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            _begin(second, ids["shared"], ids["bruno"])
        second.rollback()
        _finish(first, challenge, ids["shared"], ids["carla"], _wrong())
        first.commit()
        assert _begin(second, ids["shared"], ids["bruno"]).refused is False
        second.rollback()
    finally:
        first.close()
        second.close()
    assert _throttle(db, ids["carla"]) == (1, 0, False)


# --------------------------------------------------------------------------- failure injection


def _inject_event_failure(db: str, event_type: str) -> None:
    _sql(db, f"""
        create function platform.__inject_auth_event_failure() returns trigger
        language plpgsql set search_path = pg_catalog as $$
        begin
          if new.event_type = '{event_type}' then
            raise exception 'injected failure at the final event write' using errcode = 'XX000';
          end if;
          return new;
        end $$;
        create trigger __inject_auth_event_failure before insert on platform.auth_event
          for each row execute function platform.__inject_auth_event_failure();
    """)


def _remove_injection(db: str) -> None:
    _sql(db, "drop trigger if exists __inject_auth_event_failure on platform.auth_event; "
             "drop function if exists platform.__inject_auth_event_failure()")


def _snapshot(db: str, ids: dict[str, str]) -> Any:
    return (_event_count(db),
            _sql(db, "select failed_attempts, lockout_count, locked_until, last_failed_at, pin_attempt_id "
                     "from platform.auth_principal where id = %s", (ids["shared"],)),
            _sql(db, "select operator_id, failed_attempts, lockout_count, locked_until, last_failed_at "
                     "from platform.operator_profile order by operator_id"))


@pytest.mark.parametrize("event_type, operator, pin", [
    ("profile.selection_refused", "carla", None),
    ("profile.locked", "carla", None),
    ("profile.selected", "ana", "ana"),
])
def test_a_failure_at_the_final_event_write_leaves_nothing(db, ids, event_type, operator, pin) -> None:
    if event_type == "profile.locked":
        _sql(db, "update platform.operator_profile set failed_attempts = 4, last_failed_at = now() "
                 "where operator_id = %s", (ids["carla"],))
    else:
        _sql(db, "update platform.operator_profile set failed_attempts = 2, last_failed_at = now() "
                 "where operator_id = %s", (ids[operator],))
    before = _snapshot(db, ids)
    _inject_event_failure(db, event_type)
    try:
        with _api(db) as conn:
            challenge = _begin(conn, ids["shared"], ids[operator])
            proof = _proof(PINS[pin], challenge, ids["shared"], ids[operator]) if pin else _wrong()
            with pytest.raises(psycopg.errors.InternalError, match="injected failure"):
                _finish(conn, challenge, ids["shared"], ids[operator], proof)
            conn.rollback()
    finally:
        _remove_injection(db)
    assert _snapshot(db, ids) == before, "neither the throttle transition nor the event survived"


def test_a_failed_event_write_through_the_app_selects_nothing(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    cookie = api.session_cookie()
    before = _snapshot(db, ids)
    _inject_event_failure(db, "profile.selected")
    try:
        client = api.new_client()
        client.cookies.set(api.cookie, cookie)
        with pytest.raises(psycopg.errors.InternalError):
            api.select("ana", PINS["ana"], ids, client=client)
    finally:
        _remove_injection(db)
    assert _snapshot(db, ids) == before
    assert _sql(db, "select count(*) from platform.auth_session where operator_id = %s and revoked_at is null",
                (ids["ana"],))[0][0] == 0, "no session was rotated to the profile"
    assert api.select("ana", PINS["ana"], ids).status_code == 200, "the same session still selects afterwards"


# ------------------------------------------------------------------- the four documented outcomes


def test_success_wrong_malformed_and_locked(db, ids) -> None:
    principal, ana, bruno = ids["shared"], ids["ana"], ids["bruno"]
    _sql(db, "update platform.operator_profile set failed_attempts = 3, last_failed_at = now() "
             "where operator_id = %s", (ana,))
    before = _event_count(db)
    assert _attempt(db, principal, ana, pin=PINS["ana"]) == (True, None)
    assert _throttle(db, ana) == (0, 0, False)
    assert _attempt(db, principal, bruno, pin="111119") == (False, "pin_mismatch")
    assert _throttle(db, bruno)[0] == 1
    assert _attempt(db, principal, bruno, None) == (False, "malformed_pin")
    assert _attempt(db, principal, bruno, b"short") == (False, "malformed_pin")
    assert _throttle(db, bruno)[0] == 3, "a malformed PIN is a counted failure"
    assert _attempt(db, principal, ids["elena"], pin=PINS["elena"]) == (False, "profile_inactive")
    _sql(db, "update platform.operator_profile set locked_until = now() + interval '5 minutes' "
             "where operator_id = %s", (bruno,))
    assert _attempt(db, principal, bruno, pin=PINS["bruno"]) == (False, "locked")
    assert _throttle(db, bruno)[0] == 3, "a locked attempt counts nothing"
    assert [(e[0], e[2]) for e in _events_since(db, before)] == [
        ("profile.selected", None), ("profile.selection_refused", "pin_mismatch"),
        ("profile.selection_refused", "malformed_pin"), ("profile.selection_refused", "malformed_pin"),
        ("profile.selection_refused", "profile_inactive"), ("profile.selection_refused", "locked")]


def test_only_the_runtime_login_may_run_either_function(db, ids) -> None:
    with _owner(db) as conn, conn.cursor() as cur:
        for fn in ("platform.begin_pin_attempt(uuid,uuid)",
                   "platform.finish_pin_attempt(uuid,uuid,uuid,bytea)"):
            cur.execute("select coalesce(array_agg(a.grantee::regrole::text order by 1) "
                        "filter (where a.grantee <> p.proowner), '{}') "
                        "from pg_proc p, aclexplode(p.proacl) a where p.oid = %s::regprocedure", (fn,))
            assert cur.fetchone()[0] == ["origenlab_api"], fn
        cur.execute("grant origenlab_api to session_user with set true, inherit false")
        cur.execute("set role origenlab_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="refused for login"):
            cur.execute("select * from platform.begin_pin_attempt(%s::uuid, null)", (ids["shared"],))
        conn.rollback()


# -------------------------------------------------------------------- nothing secret is kept anywhere


def test_no_pin_pepper_verifier_or_proof_is_logged_returned_or_stored(monkeypatch, db, ids, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    api = Api(monkeypatch, db)
    hasher = api.app.state.v2_profile_login.hasher
    derived: list[bytes] = []
    proofs: list[bytes] = []
    real_derive, real_proof = hasher._derive, hasher.attempt_proof
    monkeypatch.setattr(hasher, "_derive", lambda *a, **kw: derived.append(real_derive(*a, **kw)) or derived[-1])
    def recording_proof(*args: Any, **kwargs: Any) -> bytes | None:
        proof = real_proof(*args, **kwargs)
        if proof is not None:
            proofs.append(proof)
        return proof

    monkeypatch.setattr(hasher, "attempt_proof", recording_proof)
    responses = [api.google()]
    for key, pin in (("carla", "111119"), ("carla", "12ab"), ("bruno", PINS["bruno"]), ("ana", PINS["ana"])):
        responses.append(api.select(key, pin, ids))
    responses.append(api.client.get("/auth/session"))
    assert responses[-2].status_code == 200 and proofs and derived

    needles = {PEPPER, SESSION_SECRET, *PINS.values()}
    for raw in derived + proofs:
        needles |= {raw.hex(), base64.b64encode(raw).decode().rstrip("="),
                    base64.urlsafe_b64encode(raw).decode().rstrip("=")}
    haystacks = {
        "logs": caplog.text,
        "responses": "\n".join(f"{r.status_code} {dict(r.headers)} {r.text}" for r in responses),
        "auth tables": "\n".join(
            json.dumps(row[0], default=str) for table in ("auth_event", "auth_session", "auth_principal")
            for row in _sql(db, f"select to_jsonb(t) from platform.{table} t")),
        "operator_profile without the stored hash": "\n".join(
            json.dumps(row[0], default=str) for row in
            _sql(db, "select to_jsonb(t) - 'pin_hash' from platform.operator_profile t")),
    }
    cluster = os.environ.get("OL_TEST_CLUSTER")
    if cluster:
        haystacks["database server log"] = subprocess.run(
            ["docker", "logs", cluster], capture_output=True, text=True, check=False).stdout
    for where, text in haystacks.items():
        for needle in needles:
            assert needle not in text, f"a secret reached the {where}"
    # The stored hash's digest is the verifier; only operator_profile.pin_hash holds it.
    for (stored,) in _sql(db, "select pin_hash from platform.operator_profile"):
        digest = stored.split("$")[5]
        for where, text in haystacks.items():
            assert digest not in text, f"a stored verifier reached the {where}"


# ------------------------------------------------------------------------ the session boundary


def test_an_inserted_session_row_is_not_a_cookie_without_the_session_secret(monkeypatch, db, ids) -> None:
    """A SQL-only attacker can insert an auth_session row; it authenticates nobody without the key.

    The cookie is HMAC-signed and names the row by a random identifier whose keyed HMAC is what
    the row holds (`auth_session.py`). This is not a claim about a compromised API host, which
    holds the key and can mint any session (`apps/api/docs/PRODUCTION_AUTH.md`).
    """
    api = Api(monkeypatch, db)
    principal_version, = _sql(db, "select version from platform.auth_principal where id = %s", (ids["shared"],))[0]
    operator_version, profile_version = _sql(
        db, "select o.version, p.version from platform.operator o join platform.operator_profile p "
            "on p.operator_id = o.id where o.id = %s", (ids["ana"],))[0]
    now = int(time.time())
    sid = new_session_id()
    guessed_key = "attacker-guess-of-the-session-secret-" + "q" * 16
    session = PrincipalSession(
        principal_id=ids["shared"], email=SHARED, subject=f"sub-{SHARED.split('@')[0]}",
        principal_version=principal_version, auth_time=now, exp=now + 3600, sid=sid,
        profile=ProfileSelection(ids["ana"], operator_version, profile_version, now))
    # Every row the attacker could insert for that sid: its plain hash, its HMAC under a guessed
    # key, the identifier itself — all with the admin profile already selected.
    for token_hash in (hashlib.sha256(sid.encode()).digest(),
                       CookieSigner(guessed_key).session_token_hash(sid),
                       hashlib.sha256(os.urandom(32)).digest()):
        with _api(db) as conn:
            conn.execute("insert into platform.auth_session (token_hash, principal_id, operator_id, expires_at) "
                         "values (%s, %s, %s, now() + interval '1 hour')", (token_hash, ids["shared"], ids["ana"]))
    forged = {
        "signed with a guessed key": CookieSigner(guessed_key).dump_principal_session(session),
        "unsigned payload": base64.urlsafe_b64encode(json.dumps({
            "k": "principal", "pid": ids["shared"], "sid": sid, "op": ids["ana"]}).encode()).decode().rstrip("="),
        "the identifier alone": sid,
    }
    for label, value in forged.items():
        client = api.new_client()
        client.cookies.set(api.cookie, value)
        assert client.get("/auth/session").json()["state"] == "signed_out", label
        assert client.get("/v2/contacts").status_code == 401, label
    # The same session signed with the real key is refused too: no row holds the keyed hash of
    # its identifier, and computing that hash needs the key.
    real = CookieSigner(SESSION_SECRET)
    client = api.new_client()
    client.cookies.set(api.cookie, real.dump_principal_session(session))
    assert client.get("/v2/contacts").status_code == 401, "a row the key never hashed is no session"
    # Control, and the documented limit: whoever holds the key (the API host) can mint a session.
    with _api(db) as conn:
        conn.execute("insert into platform.auth_session (token_hash, principal_id, operator_id, expires_at) "
                     "values (%s, %s, %s, now() + interval '1 hour')",
                     (real.session_token_hash(sid), ids["shared"], ids["ana"]))
    assert client.get("/v2/contacts").status_code == 200, "the key is the only missing ingredient"
