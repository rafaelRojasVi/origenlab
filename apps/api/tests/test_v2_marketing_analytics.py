"""Marketing analytics (`origenlab_api.v2.marketing_analytics`): every figure on a small, fully
known world — two campaigns, a dozen fictitious addresses, their mail, blocks and cases.

The world (campaign A sent 5 Oct, B sent 20 Oct, C sent 1 Sep before any captured mail):

==========  ======================================================================
a1          A + B; writes 23 Oct (3 d after B), read as quote_request; person P1 with
            an open case created 22 Oct → responded/quote_request/cases_after for B
a2          A; writes 25 Oct (20 d after A, no later send) → not a response
a3          A; auto-reply 7 Oct (excluded); a mailer-daemon notice in its A thread,
            no block → ndr_unblocked for A
a4          A; address block «invalid_address» 6 Oct → bounced, A
a5          B; «unsubscribe» (unsubscribe_handler) 21 Oct → baja, B
a6          B; its domain blocked «v1 bounce_no_such_user» 1 Oct (before the send) → bounced, B
c1          C only (1 Sep) — the mail of that period is not captured
P2          a7 (blocked) + a8 (not), never sent → coverage sin_caso / not_contacted
P3          a9 blocked, never sent → coverage sin_caso / blocked
P4          a10, never sent, open case with a task due in 10 days → coverage pausa
supplier    a11 at a supplier organization → left out of coverage
==========  ======================================================================
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from origenlab_api.v2.marketing_analytics import (
    METRICS,
    SEGMENTS,
    UnknownFigure,
    block_category,
    read_analytics,
    read_people,
)


@pytest.mark.parametrize(("source", "reason", "expected"), [
    ("ndr_handler", "hard bounce", "rebote"),
    ("unsubscribe_handler", "anything", "baja"),
    ("wave1a_suppression", "v1 bounce_no_such_user", "rebote"),
    ("wave1a_suppression", "v1 bounce_other", "rebote"),
    ("wave1a_suppression", "v1 manual_do_not_contact", "baja"),
    ("operator_command", "invalid_address", "rebote"),
    ("operator_command", "unsubscribe", "baja"),
    ("wave1b_block", "v1 bounce_access_denied", "rebote"),
    ("operator_command", "proveedor", "otro"),
])
def test_block_category_maps_every_known_source(source: str, reason: str, expected: str) -> None:
    assert block_category(source, reason) == expected


# --------------------------------------------------------------------------- database (opt-in)

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402

pytestmark_db = needs_db


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def world(disposable_database) -> dict[str, Any]:
    import psycopg

    tag = uuid.uuid4().hex[:6]
    a = {k: f"{k}-{tag}@instituto.invalid" for k in ("a1", "a2", "a3", "a4", "a5", "a7", "a8", "a9", "a10", "c1")}
    a["a6"] = f"a6@dominio-{tag}.invalid"
    a["a11"] = f"a11@proveedor-{tag}.invalid"
    w: dict[str, Any] = {"a": a, "tag": tag}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        op = _one(cur, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                       "values (gen_random_uuid(), %s, 'Op', 'admin', 'active') returning id", (f"op-{tag}@example.invalid",))
        mb = _one(cur, "insert into comms.mailbox (address_norm) values (%s) returning id", (f"ventas-{tag}@example.invalid",))
        camp = {}
        for name in ("A", "B", "C"):
            camp[name] = _one(cur, "insert into outbound.campaign (name, status, mailbox_id, max_sends) "
                                   "values (%s, 'archived', %s, 100) returning id", (f"{name} {tag}", mb))
        w["camp"] = {k: str(v) for k, v in camp.items()}
        when = {"A": "2026-10-05T12:00:00Z", "B": "2026-10-20T12:00:00Z", "C": "2026-09-01T12:00:00Z"}

        def send(c: str, key: str) -> str:
            rid = _one(cur, "insert into outbound.campaign_recipient (campaign_id, address_norm, state) "
                            "values (%s, %s, 'sent') returning id", (camp[c], a[key]))
            pmid = f"g-{c}-{key}-{tag}"
            cur.execute("insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, "
                        "address_norm, submission_state, delivery_state, accepted_at, provider_message_id) "
                        "values ('marketing', %s, %s, %s, %s, 'accepted', 'pending', %s, %s)",
                        (camp[c], rid, mb, a[key], when[c], pmid))
            # The sent copy the capture stored, with its Gmail thread.
            cur.execute("insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                        "internal_date) values (%s, %s, %s, 'outbound', %s)", (mb, pmid, f"th-{c}-{key}-{tag}", when[c]))
            return pmid

        for c, keys in (("A", ("a1", "a2", "a3", "a4")), ("B", ("a1", "a5", "a6")), ("C", ("c1",))):
            for k in keys:
                send(c, k)

        def inbound(sender: str, at: str, cls: str | None = None, thread: str | None = None) -> None:
            pmid = f"in-{uuid.uuid4().hex[:10]}"
            mid = _one(cur, "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                            "internal_date) values (%s, %s, %s, 'inbound', %s) returning id", (mb, pmid, thread, at))
            cur.execute("insert into comms.message_participant (message_id, role, address_norm) values (%s, 'from', %s)",
                        (mid, sender))
            if cls:
                sr = _one(cur, "insert into evidence.source_record (kind, dedupe_key, payload) values ('gmail_message', %s, '{}') "
                               "returning id", (f"gmail_message:{pmid}",))
                cur.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                            "values (%s, 'message_triage', 'triage:v2', %s)", (sr, json.dumps({"class": cls})))

        inbound(f"alguien-{tag}@otro.invalid", "2026-10-01T09:00:00Z")  # earliest captured inbound mail
        inbound(a["a1"], "2026-10-23T10:00:00Z", "quote_request")
        inbound(a["a2"], "2026-10-25T10:00:00Z")
        inbound(a["a3"], "2026-10-07T10:00:00Z", "auto_reply")
        inbound(f"mailer-daemon-{tag}@googlemail.invalid".replace(f"mailer-daemon-{tag}", "mailer-daemon"),
                "2026-10-05T12:05:00Z", None, thread=f"th-A-a3-{tag}")

        def block(scope: str, value: str, reason: str, source: str, at: str) -> None:
            cur.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source, created_at) "
                        "values (%s, %s, 'block', 'all', %s, %s, %s)", (scope, value, reason, source, at))

        block("address", a["a4"], "invalid_address", "operator_command", "2026-10-06T09:00:00Z")
        block("address", a["a5"], "unsubscribe", "unsubscribe_handler", "2026-10-21T09:00:00Z")
        block("domain", a["a6"].split("@")[1], "v1 bounce_no_such_user", "wave1a_suppression", "2026-10-01T09:00:00Z")
        block("address", a["a7"], "invalid_address", "operator_command", "2026-10-02T09:00:00Z")
        block("address", a["a9"], "invalid_address", "operator_command", "2026-10-02T09:00:00Z")

        def person(name: str, *keys: str, org=None) -> Any:
            pid = _one(cur, "insert into crm.person (display_name, confirmation) values (%s, 'confirmed') returning id",
                       (f"{name} {tag}",))
            for k in keys:
                cur.execute("insert into crm.contact_point (kind, value_norm, value_display, person_id, organization_id, "
                            "usage, confirmation) values ('email', %s, %s, %s, %s, %s, 'confirmed')",
                            (a[k], a[k], pid, org, "personal" if org is None else "work"))
            return pid

        p1 = person("P1", "a1")
        person("P2", "a7", "a8")
        person("P3", "a9")
        p4 = person("P4", "a10")
        sup = _one(cur, "insert into crm.organization (kind, name, confirmation) values ('supplier', %s, 'confirmed') "
                        "returning id", (f"Proveedor Ficticio {tag}",))
        person("Prov", "a11", org=sup)

        def case(pid, created: str) -> Any:
            oid = _one(cur, "insert into crm.opportunity (title, stage, owner_operator_id, created_at) "
                            "values ('Caso ficticio', 'lead', %s, %s) returning id", (op, created))
            cur.execute("insert into crm.opportunity_participant (opportunity_id, person_id, role, valid_from, confirmation) "
                        "values (%s, %s, 'end_user', '2026-10-01', 'confirmed')", (oid, pid))
            return oid

        w["case_p1"] = str(case(p1, "2026-10-22T10:00:00Z"))
        c4 = case(p4, "2026-09-10T10:00:00Z")
        cur.execute("insert into crm.task (opportunity_id, owner_operator_id, title, due_at) "
                    "values (%s, %s, 'Volver a llamar', now() + interval '10 days')", (c4, op))
    w["dsn"] = runtime_dsn(disposable_database)
    return w


def _analytics(world) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(world["dsn"]) as conn, conn.cursor() as cur:
        return read_analytics(cur)


def _people(world, **kw) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(world["dsn"]) as conn, conn.cursor() as cur:
        return read_people(cur, **kw)


def _figs(world, name: str) -> dict[str, Any]:
    return _analytics(world)["campaigns"][world["camp"][name]]


@needs_db
def test_sent_and_new_people(world) -> None:
    assert _figs(world, "A")["sent"] == 4 and _figs(world, "A")["new_people"] == 4
    assert _figs(world, "B")["sent"] == 3 and _figs(world, "B")["new_people"] == 2  # a1 got A first


@needs_db
def test_reply_attributed_to_latest_campaign_before_message(world) -> None:
    assert _figs(world, "B")["responded"] == 1
    assert _figs(world, "A")["responded"] == 0  # a1's reply is B's, a2's is too late, a3's an auto-reply


@needs_db
def test_reply_after_14_days_is_not_counted(world) -> None:
    rows = _people(world, metric="responded", campaign_id=world["camp"]["A"])["rows"]
    assert world["a"]["a2"] not in {r["address_norm"] for r in rows}


@needs_db
def test_ndr_and_outbound_are_not_replies(world) -> None:
    rows = _people(world, metric="responded", campaign_id=world["camp"]["A"])["rows"]
    assert rows == []
    assert _figs(world, "A")["ndr_unblocked"] == 1
    assert _analytics(world)["alerts"]["ndr_unblocked"] == 1


@needs_db
def test_quote_request_counts_only_responded_with_class(world) -> None:
    assert _figs(world, "B")["quote_request"] == 1 and _figs(world, "A")["quote_request"] == 0


@needs_db
def test_cases_after_counts_case_opened_within_window_for_person_or_org(world) -> None:
    assert _figs(world, "B")["cases_after"] == 1 and _figs(world, "A")["cases_after"] == 0
    rows = _people(world, metric="cases_after", campaign_id=world["camp"]["B"])["rows"]
    assert [r["opportunity_id"] for r in rows] == [world["case_p1"]]


@needs_db
def test_bounces_and_bajas_are_attributed_per_campaign(world) -> None:
    assert _figs(world, "A")["bounced"] == 1 and _figs(world, "A")["baja"] == 0
    assert _figs(world, "B")["bounced"] == 1 and _figs(world, "B")["baja"] == 1  # a6's domain, a5's baja


@needs_db
def test_overview_writable_is_reached_minus_blocked_address_or_domain(world) -> None:
    o = _analytics(world)["overview"]
    assert o["reached"] == 7  # a1..a6 + c1
    assert (o["bounced"], o["baja"]) == (2, 1)
    assert o["writable"] == 4
    assert o["with_open_case"] == 1  # a1 (P1's open case)


@needs_db
def test_coverage_rows_add_up(world) -> None:
    cov = {r["segment"]: r for r in _analytics(world)["coverage"]}
    assert set(cov) == set(SEGMENTS)
    for r in cov.values():
        assert r["contacted"] + r["not_contacted"] + r["blocked"] == r["people"]
    assert cov["solicitada"]["contacted"] == 1  # P1
    assert cov["pausa"] == {"segment": "pausa", "people": 1, "contacted": 0, "not_contacted": 1, "blocked": 0}


@needs_db
def test_person_blocked_only_when_every_address_blocked(world) -> None:
    cov = {r["segment"]: r for r in _analytics(world)["coverage"]}
    assert cov["sin_caso"]["not_contacted"] == 1  # P2: a7 blocked, a8 usable
    assert cov["sin_caso"]["blocked"] == 1  # P3


@needs_db
def test_suppliers_are_not_in_coverage(world) -> None:
    rows = _people(world, segment="total")["rows"]
    assert world["a"]["a11"] not in {r["address_norm"] for r in rows}


@needs_db
def test_pausa_segment_is_open_case_with_future_task(world) -> None:
    rows = _people(world, segment="pausa")["rows"]
    assert [r["address_norm"] for r in rows] == [world["a"]["a10"]]


@needs_db
def test_unmeasurable_is_none_not_zero(world) -> None:
    c = _figs(world, "C")  # sent 1 Sep, before the earliest captured inbound mail
    assert c["responded"] is None and c["quote_request"] is None and c["ndr_unblocked"] is None
    assert c["sent"] == 1 and c["cases_after"] == 0


@needs_db
def test_every_count_equals_its_people_list_total(world) -> None:
    an = _analytics(world)
    for cid, figures in an["campaigns"].items():
        if cid not in world["camp"].values():
            continue
        for m in METRICS:
            if figures[m] is not None:
                assert _people(world, metric=m, campaign_id=cid, limit=200)["total"] == figures[m], (cid, m)
    for m, n in an["overview"].items():
        if m != "other_blocks":
            assert _people(world, metric=m)["total"] == n, m
    for r in an["coverage"]:
        for col in ("contacted", "not_contacted", "blocked"):
            assert _people(world, segment=r["segment"], column=col)["total"] == r[col]


def test_unknown_figure_is_refused() -> None:
    with pytest.raises(UnknownFigure):
        read_people(None, metric="opens", campaign_id=None)
    with pytest.raises(UnknownFigure):
        read_people(None, segment="vip")
