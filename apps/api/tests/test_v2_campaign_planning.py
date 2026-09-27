"""Campaign planning, the sent-HTML archive and the calendar reads.

**In process, always run:** body validation, the route requirements (operator role,
Idempotency-Key), the switch, that no route anywhere sends/approves/activates/enqueues, the
equipment-line derivation, and that the planning module imports nothing that could send.

**Database-backed, opt-in:** set / change / clear with one audit event each, replay, the
compare-and-set, the Santiago day rules, the refusal on sent or historical campaigns, the
regression that planning writes no send attempt, job or Gmail call, the archive's honesty
states, and the real send batches. These run only in a disposable `origenlab_test_<hex>` the
harness creates and drops, as the `origenlab_api` runtime role. Every value is fictitious.
"""

from __future__ import annotations

import ast
import pathlib
import socket
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.campaign_calendar import campaign_lines
from origenlab_api.v2.campaign_planning import (
    SET_CAMPAIGN_PLANNING,
    SetCampaignPlanningBody,
    validated_planning,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.equipment_taxonomy import load_taxonomy
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "origenlab_api"


def _body(**kw):
    return SetCampaignPlanningBody(**{"campaign_id": str(uuid.uuid4()), "expected_planning_version": 0, **kw})


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("extra", [{"status": "approved"}, {"send": True}, {"approve": True},
                                   {"enqueue": True}, {"planned_for_at": "2030-01-01T00:00:00Z"}])
def test_the_body_carries_no_status_send_or_raw_instant(extra) -> None:
    with pytest.raises(ValidationError):
        _body(planned_for_date="2030-01-01", **extra)


@pytest.mark.parametrize("wall", ["24:00", "7:30", "07:60", "07:30:00", "mañana"])
def test_a_time_is_hh_mm(wall) -> None:
    with pytest.raises(ValidationError):
        _body(planned_for_date="2030-01-01", planned_for_time=wall)


def test_a_time_needs_a_day_and_blank_time_is_absent() -> None:
    with pytest.raises(CommandRefused) as err:
        validated_planning(_body(planned_for_time="09:30"))
    assert err.value.status_code == 422 and err.value.code == "time_without_date"
    assert _body(planned_for_date="2030-01-01", planned_for_time="  ").planned_for_time is None
    assert validated_planning(_body())["planned_for_date"] is None  # clearing is a valid request


# --------------------------------------------------------------------------- routes


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


def _operator(role="sales"):
    return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001",
                            email_norm="op@example.test", display_name="Op", role=role, status="active")


class _FakeRepo:
    def __init__(self):
        self.calls = []

    def execute(self, **kw):
        self.calls.append(kw)
        return {"campaign_id": "x", "planning_version": 1, "replayed": False}


def _client(repo, role="sales"):
    from fastapi import FastAPI

    from origenlab_api.v2.campaign_planning_routes import campaign_planning_router

    app = FastAPI()
    app.include_router(campaign_planning_router)
    app.state.campaign_planning_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


PATH = "/v2/commands/set-campaign-planning"
BODY = {"campaign_id": str(uuid.uuid4()), "expected_planning_version": 0, "planned_for_date": "2030-01-01"}
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}


def test_a_viewer_may_not_plan() -> None:
    repo = _FakeRepo()
    r = _client(repo, role="viewer").post(PATH, json=BODY, headers={**HEADERS, "Idempotency-Key": "k-12345678"})
    assert r.status_code == 403 and repo.calls == []


def test_planning_needs_an_idempotency_key() -> None:
    repo = _FakeRepo()
    r = _client(repo).post(PATH, json=BODY, headers=HEADERS)
    assert r.status_code == 400 and repo.calls == []


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_plan(role) -> None:
    repo = _FakeRepo()
    r = _client(repo, role=role).post(PATH, json=BODY, headers={**HEADERS, "Idempotency-Key": "k-12345678"})
    assert r.status_code == 200 and len(repo.calls) == 1
    assert repo.calls[0]["command_name"] == SET_CAMPAIGN_PLANNING


def test_the_planning_router_exposes_one_post_and_nothing_else() -> None:
    from origenlab_api.v2.campaign_planning_routes import campaign_planning_router

    assert [(r.path, set(r.methods)) for r in campaign_planning_router.routes] == [(PATH, {"POST"})]


def test_planning_mounts_only_behind_its_own_switch() -> None:
    from fastapi import FastAPI

    from origenlab_api import main
    from origenlab_api.settings import Settings

    def paths(**kw):
        app = FastAPI()
        main._mount_campaign_planning(app, Settings(_env_file=None, **kw), "postgresql://x@127.0.0.1/db")
        return app.state.campaign_planning_enabled, set(app.openapi()["paths"])

    for kw in ({}, {"v2_database_url": LOOPBACK}, {"v2_database_url": LOOPBACK, "v2_campaign_drafts_enabled": True},
               {"v2_database_url": LOOPBACK, "v2_commands_enabled": True, "v2_audience_freeze_enabled": True}):
        enabled, routes = paths(**kw)
        assert enabled is False and PATH not in routes
    enabled, routes = paths(v2_database_url=LOOPBACK, v2_campaign_planning_enabled=True)
    assert enabled is True and PATH in routes


def test_no_route_sends_approves_activates_or_enqueues_with_every_switch_on(monkeypatch) -> None:
    from origenlab_api.main import create_app

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    for switch in ("COMMANDS", "CAMPAIGN_DRAFTS", "AUDIENCE_FREEZE", "RECONTACT_REVIEW", "CAMPAIGN_PLANNING"):
        monkeypatch.setenv(f"ORIGENLAB_V2_{switch}_ENABLED", "true")
    app = create_app()
    paths = app.openapi()["paths"]
    v2 = [(p, sorted(m.upper() for m in ops)) for p, ops in paths.items() if p.startswith("/v2")]
    assert (PATH, ["POST"]) in v2
    assert ("/v2/workspace/marketing/campaigns/{campaign_id}/archive", ["GET"]) in v2
    import re

    # Whole path tokens: `attribute-sender-organization` names a sender, it does not send.
    forbidden = {"send", "sends", "approve", "activate", "enqueue", "dispatch", "schedule", "launch"}
    offenders = [p for p, _ in v2 if forbidden & set(re.split(r"[/_{}-]+", p.lower()))]
    assert offenders == []


def test_the_planning_module_imports_nothing_that_could_send() -> None:
    for name in ("campaign_planning.py", "campaign_planning_routes.py", "campaign_calendar.py"):
        tree = ast.parse((SRC / "v2" / name).read_text(encoding="utf-8"))
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in imported if any(w in m.lower() for w in ("gmail", "smtp", "send", "google", "http", "drive"))], name


# --------------------------------------------------------------------------- equipment lines


def test_a_line_is_the_frozen_criterion_first_then_a_brand_named_in_name_or_subject() -> None:
    taxonomy = load_taxonomy()
    brand = next(iter(taxonomy.brands.values()))
    frozen = campaign_lines(taxonomy, {"name": "x", "audience_criteria": {"brand_id": brand["id"]}})
    assert frozen == [{"family_id": brand["family_id"], "source": "audience_criteria", "matched_term": None}]
    named = campaign_lines(taxonomy, {"name": "Hielscher Sonicadores Laboratorio", "subject": None})
    assert [(line["family_id"], line["source"]) for line in named] == [("sonicacion", "name_or_subject")]
    assert campaign_lines(taxonomy, {"name": "Septiembre 2026 — Fiestas Patrias FINAL"}) == []


# --------------------------------------------------------------------------- database (opt-in)


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


MAILBOX = "planning-sender@example.invalid"


@pytest.fixture(scope="module")
def world(disposable_database):
    import psycopg

    tag = uuid.uuid4().hex[:10]
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Planning Operator', 'sales', 'active') returning id::text",
            (f"planning-{tag}@example.test",),
        )
        operator_id = cur.fetchone()[0]
        cur.execute("insert into comms.mailbox (address_norm, display_name) values (%s, 'Ventas') returning id::text",
                    (MAILBOX,))
        mailbox_id = cur.fetchone()[0]
    return {"mailbox": mailbox_id,
            "operator": OperatorIdentity(operator_id=operator_id, email_norm=f"planning-{tag}@example.test",
                                         display_name="Planning Operator", role="sales", status="active")}


def _owner(dsn, sql, params=()):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _campaign(dsn, world, status="draft", **cols):
    names = ["name", "status", "mailbox_id", "max_sends", "recontact_interval_days", *cols]
    values = [f"Campaña {uuid.uuid4().hex[:6]}", status, world["mailbox"], 50, 90, *cols.values()]
    rows = _owner(dsn, f"insert into outbound.campaign ({', '.join(names)}) values ({', '.join(['%s'] * len(names))}) "
                       "returning id::text", values)
    return rows[0][0]


def _repo(dsn):
    import psycopg

    from origenlab_api.v2.campaign_planning import V2CampaignPlanningRepository

    return V2CampaignPlanningRepository(psycopg.connect, runtime_dsn(dsn))


def _plan(dsn, world, cid, version, day=None, wall=None, key=None):
    body = SetCampaignPlanningBody(campaign_id=cid, expected_planning_version=version,
                                   planned_for_date=day, planned_for_time=wall)
    return _repo(dsn).execute(command_name=SET_CAMPAIGN_PLANNING, operator=world["operator"],
                              fields=validated_planning(body), idempotency_key=key or uuid.uuid4().hex,
                              digest=request_digest(SET_CAMPAIGN_PLANNING, body))


def _events(dsn, cid):
    rows = _owner(dsn, "select event_type, payload from crm.domain_event where aggregate_kind = 'campaign' "
                       "and aggregate_id = %s order by seq", (cid,))
    return rows


def _all_counts(dsn):
    """Row count of every table in every application schema."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("""select schemaname || '.' || tablename from pg_tables
                        where schemaname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
                        order by 1""")
        out = {}
        for (table,) in cur.fetchall():
            cur.execute(f"select count(*) from {table}")  # noqa: S608 - names from the catalogue
            out[table] = cur.fetchone()[0]
        return out


SANTIAGO = "America/Santiago"


def _today():
    import datetime as dt
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo(SANTIAGO)).date()


def _in(days: int) -> str:
    import datetime as dt

    return (_today() + dt.timedelta(days=days)).isoformat()


def _utc(day: str, wall: str) -> str:
    """What PostgreSQL should store for a Santiago wall time, computed independently."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    local = dt.datetime.fromisoformat(f"{day}T{wall}").replace(tzinfo=ZoneInfo(SANTIAGO))
    return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_spring_forward_day() -> str:
    """The next first Sunday of September — Chile moves its clocks from 00:00 to 01:00."""
    import datetime as dt

    year = _today().year + 1
    first = dt.date(year, 9, 1)
    return (first + dt.timedelta(days=(6 - first.weekday()) % 7)).isoformat()


FUTURE = _in(200)


@needs_db
def test_set_change_and_clear_are_each_one_audited_event(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    first = _plan(disposable_database, world, cid, 0, FUTURE)
    assert first["changed"] is True and first["planning_version"] == 1 and first["planned_for_at"] is None
    assert first["schedules_send"] is False and first["status"] == "draft"
    changed = _plan(disposable_database, world, cid, 1, FUTURE, "09:30")
    assert changed["planned_for_at"] == _utc(FUTURE, "09:30") and changed["planning_version"] == 2
    cleared = _plan(disposable_database, world, cid, 2)
    assert cleared["planned_for_date"] is None and cleared["planned_for_at"] is None
    events = _events(disposable_database, cid)
    assert [e[0] for e in events] == ["campaign.planning_set", "campaign.planning_set", "campaign.planning_cleared"]
    assert events[1][1]["from"] == {"planned_for_date": FUTURE, "planned_for_at": None}
    assert events[1][1]["to"] == {"planned_for_date": FUTURE, "planned_for_at": _utc(FUTURE, "09:30")}
    assert events[2][1]["from"]["planned_for_at"] == _utc(FUTURE, "09:30")
    assert all(e[1]["schedules_send"] is False for e in events)


@needs_db
def test_a_replayed_key_writes_once_and_the_same_planning_writes_nothing(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    first = _plan(disposable_database, world, cid, 0, FUTURE, key="planning-replay-1")
    again = _plan(disposable_database, world, cid, 0, FUTURE, key="planning-replay-1")
    assert again["replayed"] is True and again["planning_version"] == first["planning_version"] == 1
    same = _plan(disposable_database, world, cid, 1, FUTURE)
    assert same["changed"] is False and same["planning_version"] == 1
    assert len(_events(disposable_database, cid)) == 1


@needs_db
def test_a_stale_planning_version_is_refused(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    _plan(disposable_database, world, cid, 0, FUTURE)
    with pytest.raises(CommandRefused) as err:
        _plan(disposable_database, world, cid, 0, _in(201))
    assert err.value.code == "stale_planning_version"


@needs_db
def test_the_day_rules_are_santiago_days(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    with pytest.raises(CommandRefused) as err:
        _plan(disposable_database, world, cid, 0, _in(-1))
    assert err.value.code == "planned_in_past"
    with pytest.raises(CommandRefused) as err:
        _plan(disposable_database, world, cid, 0, _in(1_101))
    assert err.value.code == "planned_too_far"
    gap = _next_spring_forward_day()
    if _utc(gap, "00:30") == _utc(gap, "01:30"):  # the local tz database agrees 00:30 does not exist
        with pytest.raises(CommandRefused) as err:
            _plan(disposable_database, world, cid, 0, gap, "00:30")
        assert err.value.code == "planned_time_nonexistent"
    today = _plan(disposable_database, world, cid, 0, _in(0))
    assert today["planned_for_date"] == _in(0)  # today is not in the past
    # 23:30 in Santiago is already the next day in UTC; the day stays the Santiago day.
    late = _plan(disposable_database, world, cid, 1, _in(30), "23:30")
    assert late["planned_for_date"] == _in(30) and late["planned_for_at"] == _utc(_in(30), "23:30")
    assert late["planned_for_at"][:10] > _in(30)


@needs_db
def test_a_historical_or_cancelled_campaign_is_not_plannable(disposable_database, world) -> None:
    for status in ("archived", "cancelled"):
        cid = _campaign(disposable_database, world, status=status)
        with pytest.raises(CommandRefused) as err:
            _plan(disposable_database, world, cid, 0, FUTURE)
        assert err.value.code == "campaign_not_plannable"


@needs_db
def test_planning_writes_no_send_attempt_no_job_and_calls_no_network(disposable_database, world, monkeypatch) -> None:
    """The regression: saving a planned date sends nothing, enqueues nothing, calls nothing."""
    import sys

    cid = _campaign(disposable_database, world)
    before = _all_counts(disposable_database)
    modules_before = set(sys.modules)

    def refuse(*_a, **_k):
        raise AssertionError("planning opened a network connection")

    # psycopg talks to PostgreSQL through libpq, not Python sockets; any Gmail, SMTP or HTTP
    # client would go through these.
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    out = _plan(disposable_database, world, cid, 0, FUTURE, "10:00")
    monkeypatch.undo()

    after = _all_counts(disposable_database)
    moved = {t: (before[t], after[t]) for t in before if before[t] != after[t]}
    assert moved == {"crm.domain_event": (before["crm.domain_event"], before["crm.domain_event"] + 1),
                     "platform.command_receipt": (before["platform.command_receipt"],
                                                  before["platform.command_receipt"] + 1)}
    assert after["outbound.send_attempt"] == before["outbound.send_attempt"]
    assert after["outbound.campaign_recipient"] == before["outbound.campaign_recipient"]
    row = _owner(disposable_database, "select status, approved_at, audience_frozen_at, version from outbound.campaign "
                                      "where id = %s", (cid,))[0]
    assert row == ("draft", None, None, 1) and out["status"] == "draft"
    control = _owner(disposable_database, "select marketing_enabled, transactional_enabled from outbound.send_control")
    assert control == [(False, False)]
    assert not [m for m in set(sys.modules) - modules_before if "gmail" in m or "smtp" in m]


# --------------------------------------------------------------------------- archive and batches


def _workspace(dsn):
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    return CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn))


def _frozen(dsn, world, *, subject="Sonicadores", preheader="UP200St", body_text="Hola", html="<p>Hola</p>", sha=None):
    from origenlab_api.v2.audience_freeze import content_sha256

    return _campaign(
        dsn, world, status="audience_frozen", subject=subject, preheader=preheader, body_text=body_text,
        body_html=html, content_sha256=sha or content_sha256(subject, preheader, body_text, html),
        content_frozen_at="2026-09-20T12:00:00Z", audience_criteria='{"version": 1}', audience_criteria_version=1,
        audience_frozen_at="2026-09-20T12:00:00Z", audience_policy_version="marketing-audience/2026-09-27.v1",
        audience_sha256="c" * 64,
    )


@needs_db
def test_the_archive_shows_frozen_html_only_when_its_fingerprint_recomputes(disposable_database, world) -> None:
    good = _frozen(disposable_database, world)
    archive = _workspace(disposable_database).campaign_archive(good)
    assert archive["html_state"] == "archived_verified" and archive["html"] == "<p>Hola</p>"
    assert archive["subject"] == "Sonicadores" and archive["sender_address"] == MAILBOX and archive["origin"] == "native_v2"
    assert archive["metrics"]["opens"] is None and archive["metrics"]["clicks"] is None

    tampered = _frozen(disposable_database, world, sha="d" * 64)
    archive = _workspace(disposable_database).campaign_archive(tampered)
    assert archive["html_state"] == "fingerprint_mismatch" and archive["html"] is None

    draft = _campaign(disposable_database, world, subject="Borrador", body_html="<p>editable</p>")
    archive = _workspace(disposable_database).campaign_archive(draft)
    assert archive["html_state"] == "not_frozen" and archive["html"] is None


@needs_db
def test_an_imported_campaign_without_html_says_so_and_keeps_its_real_batches(disposable_database, world) -> None:
    origin = _owner(disposable_database, "insert into evidence.source_record (kind, dedupe_key, payload) "
                                         "values ('migration_manifest', %s, '{}'::jsonb) returning id::text",
                    (f"manifest-{uuid.uuid4().hex[:8]}",))[0][0]
    cid = _campaign(disposable_database, world, status="archived", origin_source_record_id=origin)
    # Two batches in Santiago: the evening of the 15th (one of them already the 16th in UTC) and the 16th.
    accepted = ["2026-09-15T22:03:00Z", "2026-09-16T01:30:00Z", "2026-09-16T13:00:00Z"]
    for i, at in enumerate([*accepted, None]):
        addr = f"r{i}-{uuid.uuid4().hex[:6]}@example.invalid"
        rid = _owner(disposable_database, "insert into outbound.campaign_recipient (campaign_id, address_norm, state) "
                                          "values (%s, %s, 'sent') returning id::text", (cid, addr))[0][0]
        if at is None:
            _owner(disposable_database,
                   "insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, "
                   "address_norm, submission_state, error_class) values ('marketing', %s, %s, %s, %s, 'rejected', 'permanent')",
                   (cid, rid, world["mailbox"], addr))
        else:
            _owner(disposable_database,
                   "insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, "
                   "address_norm, submission_state, delivery_state, accepted_at) "
                   "values ('marketing', %s, %s, %s, %s, 'accepted', 'pending', %s)",
                   (cid, rid, world["mailbox"], addr, at))
    archive = _workspace(disposable_database).campaign_archive(cid)
    assert archive["html_state"] == "not_archived" and archive["html"] is None and archive["subject"] is None
    assert archive["origin"] == "imported_v1"
    assert [(b["day"], b["accepted"]) for b in archive["send_batches"]] == [("2026-09-15", 2), ("2026-09-16", 1)]
    listed = next(c for c in _workspace(disposable_database).marketing()["campaigns"] if c["campaign_id"] == cid)
    assert [(b["day"], b["accepted"]) for b in listed["send_batches"]] == [("2026-09-15", 2), ("2026-09-16", 1)]
    assert listed["attempts_without_date"] == 1 and listed["sender_address"] == MAILBOX


@needs_db
def test_the_marketing_list_carries_planning(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    _plan(disposable_database, world, cid, 0, FUTURE, "08:15")
    listed = next(c for c in _workspace(disposable_database).marketing()["campaigns"] if c["campaign_id"] == cid)
    assert listed["planned_for_date"] == FUTURE and listed["planned_for_at"] == _utc(FUTURE, "08:15")
    assert listed["planning_version"] == 1 and listed["origin"] == "native_v2"
