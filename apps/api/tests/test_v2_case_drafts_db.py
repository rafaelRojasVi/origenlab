"""«Borrador en Gmail» against a real database: a draft on an open case's thread shows on that case,
not on a closed one. Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api`
role. Every id is fictitious."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from origenlab_api.v2.case_drafts import DraftThreads, read_case_drafts
from test_v2_mail_rules_client_reply_db import _reply, _sent_case
from test_v2_mail_rules_cotizador_sent_db import _admin
from test_v2_quote_commands import _owner
from test_v2_quote_commands import world as world  # noqa: PLC0414 - the module's catalog fixture, reused
from v2_command_harness import needs_db, runtime_dsn

pytestmark = needs_db


class _Lister:
    def __init__(self, threads):
        self.threads = threads

    def list_draft_threads(self):
        return self.threads


def _thread(dsn: str, record: str) -> str:
    return _owner(dsn, "select payload->>'gmail_thread_id' from evidence.source_record where id = %s", (record,))[0][0]


def test_a_draft_on_the_case_thread_shows_on_the_open_case_only(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    open_case = _sent_case(world, admin)
    answer = _reply(dsn, open_case, admin.operator_id, datetime.now(UTC) + timedelta(minutes=5))
    closed_case = _sent_case(world, admin)
    other = _reply(dsn, closed_case, admin.operator_id, datetime.now(UTC) + timedelta(minutes=6))
    _owner(dsn, "update crm.opportunity set stage = 'lost', closed_at = now() where id = %s", (closed_case,))
    threads = {_thread(dsn, answer): ["m1", "m2"], _thread(dsn, other): ["m3"], "t-sin-caso": ["m4"]}

    got = read_case_drafts(DraftThreads(_Lister(threads)), psycopg.connect, runtime_dsn(dsn), 5000)
    assert got["available"] is True
    assert set(got["items"]) == {open_case}
    assert got["items"][open_case]["drafts"] == 2


def _quote_threads(dsn: str, case_id: str) -> set[str]:
    rows = _owner(dsn, """select sm.provider_thread_id from crm.quote_revision qr
                            join crm.quote q on q.id = qr.quote_id
                            join comms.message sm on sm.id = qr.sent_message_id
                           where q.opportunity_id = %s""", (case_id,))
    return {r[0] for r in rows}


def test_a_draft_on_the_quote_revisions_thread_shows_on_the_open_case_only(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    open_case = _sent_case(world, admin)
    closed_case = _sent_case(world, admin)
    _owner(dsn, "update crm.opportunity set stage = 'lost', closed_at = now() where id = %s", (closed_case,))
    open_threads, closed_threads = _quote_threads(dsn, open_case), _quote_threads(dsn, closed_case)
    assert open_threads and closed_threads
    # Only the quote's own thread carries a draft: no email evidence is linked to it.
    _owner(dsn, "update crm.opportunity_evidence set unlinked_at = now(), unlink_reason = 'test'"
                " where opportunity_id in (%s, %s) and unlinked_at is null",
           (open_case, closed_case))
    threads = {t: ["m1"] for t in open_threads | closed_threads}

    got = read_case_drafts(DraftThreads(_Lister(threads)), psycopg.connect, runtime_dsn(dsn), 5000)
    assert set(got["items"]) == {open_case}
    assert got["items"][open_case]["drafts"] == len(open_threads)
