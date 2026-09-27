"""Campaign safety blocks — the admin block/unblock commands, the hold reads and the freeze refusal.

**In process, always run:** body validation (mandatory reason, compare-and-set token, no
unknown field), admin-only routes (sales and viewer are 403 before anything runs), the
Idempotency-Key, the switch, the viewer's status-only redaction, that the freeze planner puts a
held campaign's refusal first and outside the preview fingerprint, and that the block modules
import nothing that could send.

**Database-backed, opt-in:** block and unblock with one audit event each and nothing else
written, replay, the compare-and-set on both, a second block refused, the database refusing a
sales operator even past the route, the migration-seeded September wave-2 hold visible and
liftable only by the command, the reads the dashboard gets, and that a freeze of a blocked
campaign is refused by the command and again by the database. These run only in a disposable
`origenlab_test_<hex>` the harness creates and drops, as the `origenlab_api` runtime role.
Every value is fictitious.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.campaign_blocks import (
    BLOCK_CAMPAIGN,
    UNBLOCK_CAMPAIGN,
    VIEWER_HIDDEN_FIELDS,
    BlockCampaignBody,
    UnblockCampaignBody,
    block_fields,
    redact_for_viewer,
    unblock_fields,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "origenlab_api"
BLOCK = "/v2/commands/block-campaign"
UNBLOCK = "/v2/commands/unblock-campaign"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}


def _block_body(**kw):
    return BlockCampaignBody(**{"scope": "campaign", "campaign_id": str(uuid.uuid4()),
                                "expected_block_version": 0, "reason": "Incidente en revisión", **kw})


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("reason", ["", "   ", "\n\t ", "x" * 2001])
def test_a_block_needs_a_real_reason(reason) -> None:
    with pytest.raises(ValidationError):
        _block_body(reason=reason)


@pytest.mark.parametrize("reason", ["", " \n"])
def test_an_unblock_needs_a_real_reason(reason) -> None:
    with pytest.raises(ValidationError):
        UnblockCampaignBody(block_id=str(uuid.uuid4()), expected_version=1, reason=reason)


@pytest.mark.parametrize("extra", [{"send": True}, {"status": "paused"}, {"expires_at": "2030-01-01"},
                                   {"placed_by_operator_id": str(uuid.uuid4())}, {"legacy_campaign_key": "x-y-z"}])
def test_the_block_body_carries_nothing_but_the_decision(extra) -> None:
    with pytest.raises(ValidationError):
        _block_body(**extra)


def test_the_compare_and_set_tokens_are_required() -> None:
    with pytest.raises(ValidationError):
        BlockCampaignBody(scope="all_campaigns", reason="x")
    with pytest.raises(ValidationError):
        UnblockCampaignBody(block_id=str(uuid.uuid4()), reason="x")
    with pytest.raises(ValidationError):
        UnblockCampaignBody(block_id=str(uuid.uuid4()), expected_version=0, reason="x")


def test_scope_and_campaign_agree() -> None:
    with pytest.raises(CommandRefused) as err:
        block_fields(_block_body(campaign_id=None))
    assert err.value.code == "campaign_required"
    with pytest.raises(CommandRefused) as err:
        block_fields(_block_body(scope="all_campaigns"))
    assert err.value.code == "campaign_not_allowed"
    assert block_fields(_block_body(scope="all_campaigns", campaign_id=None))["command"] == BLOCK_CAMPAIGN
    assert unblock_fields(UnblockCampaignBody(block_id=str(uuid.uuid4()), expected_version=1, reason="ok"))["command"] == UNBLOCK_CAMPAIGN


def test_a_reference_has_the_key_shape() -> None:
    assert _block_body(reference="incident_hold_september_2026").reference == "incident_hold_september_2026"
    assert _block_body(reference="  ").reference is None
    with pytest.raises(ValidationError):
        _block_body(reference="Not a key!")


# --------------------------------------------------------------------------- routes


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


def _operator(role="admin"):
    return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001",
                            email_norm="op@example.test", display_name="Op", role=role, status="active")


class _FakeRepo:
    def __init__(self):
        self.calls = []

    def execute(self, **kw):
        self.calls.append(kw)
        return {"block": {"block_id": "b"}, "replayed": False}


def _client(repo, role="admin"):
    from origenlab_api.v2.campaign_block_routes import campaign_block_router

    app = FastAPI()
    app.include_router(campaign_block_router)
    app.state.campaign_block_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


BLOCK_JSON = {"scope": "all_campaigns", "expected_block_version": 0, "reason": "Pausa general"}
UNBLOCK_JSON = {"block_id": str(uuid.uuid4()), "expected_version": 1, "reason": "Resuelto"}


@pytest.mark.parametrize("role", ["sales", "viewer"])
@pytest.mark.parametrize(("path", "body"), [(BLOCK, BLOCK_JSON), (UNBLOCK, UNBLOCK_JSON)])
def test_only_an_admin_blocks_or_unblocks(role, path, body) -> None:
    repo = _FakeRepo()
    r = _client(repo, role=role).post(path, json=body, headers={**HEADERS, "Idempotency-Key": "k-12345678"})
    assert r.status_code == 403 and repo.calls == []


@pytest.mark.parametrize(("path", "body"), [(BLOCK, BLOCK_JSON), (UNBLOCK, UNBLOCK_JSON)])
def test_blocking_needs_an_idempotency_key(path, body) -> None:
    repo = _FakeRepo()
    r = _client(repo).post(path, json=body, headers=HEADERS)
    assert r.status_code == 400 and repo.calls == []


def test_an_admin_blocks_and_unblocks_with_a_stable_digest() -> None:
    repo = _FakeRepo()
    client = _client(repo)
    assert client.post(BLOCK, json=BLOCK_JSON, headers={**HEADERS, "Idempotency-Key": "k-12345678"}).status_code == 200
    assert client.post(UNBLOCK, json=UNBLOCK_JSON, headers={**HEADERS, "Idempotency-Key": "k-87654321"}).status_code == 200
    assert [c["command_name"] for c in repo.calls] == [BLOCK_CAMPAIGN, UNBLOCK_CAMPAIGN]
    assert repo.calls[0]["digest"] == request_digest(BLOCK_CAMPAIGN, BlockCampaignBody(**BLOCK_JSON))


def test_the_block_router_exposes_two_posts_and_nothing_else() -> None:
    from origenlab_api.v2.campaign_block_routes import campaign_block_router

    assert sorted((r.path, tuple(sorted(r.methods))) for r in campaign_block_router.routes) == [
        (BLOCK, ("POST",)), (UNBLOCK, ("POST",))]


def test_blocks_mount_only_behind_their_own_switch() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    def paths(**kw):
        app = FastAPI()
        main._mount_campaign_blocks(app, Settings(_env_file=None, **kw), "postgresql://x@127.0.0.1/db")
        return app.state.campaign_blocks_enabled, set(app.openapi()["paths"])

    for kw in ({}, {"v2_database_url": LOOPBACK}, {"v2_campaign_blocks_enabled": True},
               {"v2_database_url": LOOPBACK, "v2_commands_enabled": True, "v2_audience_freeze_enabled": True,
                "v2_campaign_planning_enabled": True, "v2_unsubscribe_apply_enabled": True}):
        enabled, routes = paths(**kw)
        assert enabled is False and BLOCK not in routes and UNBLOCK not in routes
    enabled, routes = paths(v2_database_url=LOOPBACK, v2_campaign_blocks_enabled=True)
    assert enabled is True and {BLOCK, UNBLOCK} <= routes


def test_the_switch_defaults_off() -> None:
    from origenlab_api.settings import Settings

    assert Settings(_env_file=None).v2_campaign_blocks_enabled is False


def test_no_route_sends_or_enqueues_with_the_block_switch_on(monkeypatch) -> None:
    import re

    from origenlab_api.main import create_app

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    for switch in ("COMMANDS", "CAMPAIGN_DRAFTS", "AUDIENCE_FREEZE", "CAMPAIGN_PLANNING", "CAMPAIGN_BLOCKS"):
        monkeypatch.setenv(f"ORIGENLAB_V2_{switch}_ENABLED", "true")
    paths = create_app().openapi()["paths"]
    v2 = [(p, sorted(m.upper() for m in ops)) for p, ops in paths.items() if p.startswith("/v2")]
    assert (BLOCK, ["POST"]) in v2 and (UNBLOCK, ["POST"]) in v2
    assert ("/v2/workspace/marketing/campaign-blocks", ["GET"]) in v2
    forbidden = {"send", "sends", "approve", "activate", "enqueue", "dispatch", "schedule", "launch", "resume"}
    assert [p for p, _ in v2 if forbidden & set(re.split(r"[/_{}-]+", p.lower()))] == []


def test_the_block_modules_import_nothing_that_could_send() -> None:
    for name in ("campaign_blocks.py", "campaign_block_routes.py"):
        tree = ast.parse((SRC / "v2" / name).read_text(encoding="utf-8"))
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in imported if any(w in m.lower() for w in ("gmail", "smtp", "send", "google", "http", "drive"))], name


# --------------------------------------------------------------------------- viewer redaction


HOLDS = {
    "all_campaigns": {"blocked": True, "block_version": 1, "block": {
        "block_id": "b1", "scope": "all_campaigns", "reason": "Incidente", "placed_by": "Admin",
        "placed_at": "2026-09-27T00:00:00Z", "lifted_by": None, "lift_reason": None, "active": True}},
    "legacy": [{"block_id": "b2", "scope": "legacy_campaign", "legacy_campaign_key": "septiembre18-2026-wave2",
                "reason": "Retención", "placed_by": "Migración", "reference": "incident_hold_september_2026",
                "placed_at": "2026-09-27T00:00:00Z", "active": True}],
}


def test_a_viewer_sees_status_never_reason_or_operator() -> None:
    redacted = redact_for_viewer(HOLDS)
    text = repr(redacted)
    for hidden in ("Incidente", "Retención", "Admin", "Migración"):
        assert hidden not in text
    assert redacted["all_campaigns"]["blocked"] is True
    assert redacted["all_campaigns"]["block"]["placed_at"] == "2026-09-27T00:00:00Z"
    assert redacted["all_campaigns"]["block"]["redacted"] is True
    assert redacted["legacy"][0]["legacy_campaign_key"] == "septiembre18-2026-wave2"
    assert not VIEWER_HIDDEN_FIELDS & set(redacted["legacy"][0])


class _HoldsRepo:
    def campaign_blocks(self):
        return {**HOLDS, "active": [HOLDS["all_campaigns"]["block"], *HOLDS["legacy"]], "recently_lifted": [],
                "by_campaign": {}, "effect": "x", "expires": False}


@pytest.mark.parametrize(("role", "sees_reason", "may_decide"), [("viewer", False, False), ("sales", True, False),
                                                                 ("admin", True, True)])
def test_the_campaign_blocks_read_by_role(role, sees_reason, may_decide) -> None:
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    app = FastAPI()
    app.include_router(workspace_router)
    app.state.crm_workspace = _HoldsRepo()
    app.state.campaign_blocks_enabled = True
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    body = TestClient(app).get("/v2/workspace/marketing/campaign-blocks", headers=HEADERS).json()
    assert ("Incidente" in repr(body)) is sees_reason
    assert body["all_campaigns"]["blocked"] is True
    assert body["may_decide"] is may_decide and body["commands_enabled"] is True


# --------------------------------------------------------------------------- the freeze planner


def test_a_held_campaign_is_the_first_freeze_problem_and_not_part_of_the_fingerprint() -> None:
    from origenlab_api.v2.audience_freeze import FreezeCriteria, plan_freeze
    from origenlab_api.v2.equipment_taxonomy import load_taxonomy
    from origenlab_api.v2.marketing_audience import AudienceInputs, EligibilityFacts

    campaign = {"id": "c", "status": "draft", "version": 1, "name": "x", "subject": "A",
                "preheader": None, "body_html": "<p>Hola</p>", "max_sends": 10}
    inputs = AudienceInputs(cases={}, interests=[], quotation_evidence=[], eligibility=EligibilityFacts())
    clear = plan_freeze(load_taxonomy(), inputs, campaign, FreezeCriteria())
    held = plan_freeze(load_taxonomy(), inputs, {**campaign, "hold_refusals": ["campaign_blocked"]}, FreezeCriteria())
    assert held["problems"][0]["code"] == "campaign_held"
    assert held["hold_refusals"] == ["campaign_blocked"] and clear["hold_refusals"] == []
    assert held["preview_sha256"] == clear["preview_sha256"]
    # A paused status is the status machine's business; a draft is never paused anyway.
    paused = plan_freeze(load_taxonomy(), inputs, {**campaign, "hold_refusals": ["campaign_paused"]}, FreezeCriteria())
    assert all(p["code"] != "campaign_held" for p in paused["problems"])


# --------------------------------------------------------------------------- database (opt-in)


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn, sql, params=()):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _person(dsn, role, tag):
    email = f"block-{role}-{tag}@example.test"
    rows = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                       "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                  (email, f"Block {role}", role))
    return OperatorIdentity(operator_id=rows[0][0], email_norm=email, display_name=f"Block {role}",
                            role=role, status="active")


@pytest.fixture(scope="module")
def world(disposable_database):
    tag = uuid.uuid4().hex[:10]
    mailbox = _owner(disposable_database, "insert into comms.mailbox (address_norm, display_name) "
                                          "values (%s, 'Ventas') returning id::text",
                     (f"block-sender-{tag}@example.invalid",))[0][0]
    return {"mailbox": mailbox, "admin": _person(disposable_database, "admin", tag),
            "sales": _person(disposable_database, "sales", tag)}


def _campaign(dsn, world):
    return _owner(dsn, "insert into outbound.campaign (name, mailbox_id, max_sends, recontact_interval_days, subject, body_html) "
                       "values (%s, %s, 50, 90, 'Asunto', '<p>Hola</p>') returning id::text",
                  (f"Campaña {uuid.uuid4().hex[:6]}", world["mailbox"]))[0][0]


def _repo(dsn):
    import psycopg

    from origenlab_api.v2.campaign_blocks import V2CampaignBlockRepository

    return V2CampaignBlockRepository(psycopg.connect, runtime_dsn(dsn))


def _block(dsn, operator, *, scope="campaign", campaign_id=None, version=0, reason="Incidente en revisión", key=None):
    body = BlockCampaignBody(scope=scope, campaign_id=campaign_id, expected_block_version=version, reason=reason)
    return _repo(dsn).execute(command_name=BLOCK_CAMPAIGN, operator=operator, fields=block_fields(body),
                              idempotency_key=key or uuid.uuid4().hex, digest=request_digest(BLOCK_CAMPAIGN, body))


def _unblock(dsn, operator, block_id, *, version=1, reason="Incidente resuelto", key=None):
    body = UnblockCampaignBody(block_id=block_id, expected_version=version, reason=reason)
    return _repo(dsn).execute(command_name=UNBLOCK_CAMPAIGN, operator=operator, fields=unblock_fields(body),
                              idempotency_key=key or uuid.uuid4().hex, digest=request_digest(UNBLOCK_CAMPAIGN, body))


def _all_counts(dsn):
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


def _events(dsn, block_id):
    return _owner(dsn, "select event_type, payload, actor_operator_id::text from crm.domain_event "
                       "where aggregate_kind = 'campaign_block' and aggregate_id = %s order by seq", (block_id,))


@needs_db
def test_block_then_unblock_are_one_audited_event_each_and_nothing_else(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    before = _all_counts(disposable_database)
    placed = _block(disposable_database, world["admin"], campaign_id=cid)
    after_block = _all_counts(disposable_database)
    moved = {t: after_block[t] - before[t] for t in before if after_block[t] != before[t]}
    assert moved == {"outbound.campaign_block": 1, "crm.domain_event": 1, "platform.command_receipt": 1}
    block = placed["block"]
    assert block["active"] is True and block["scope"] == "campaign" and block["campaign_id"] == cid
    assert placed["block_version"] == 1 and placed["campaign_refusals"] == ["campaign_blocked"]
    assert placed["sends"] is False and placed["enqueues"] is False and placed["expires"] is False

    lifted = _unblock(disposable_database, world["admin"], block["block_id"])
    after_unblock = _all_counts(disposable_database)
    moved = {t: after_unblock[t] - after_block[t] for t in before if after_unblock[t] != after_block[t]}
    assert moved == {"crm.domain_event": 1, "platform.command_receipt": 1}
    assert lifted["block"]["active"] is False and lifted["block"]["lift_reason"] == "Incidente resuelto"
    assert lifted["block"]["version"] == 2 and lifted["block_version"] == 2 and lifted["campaign_refusals"] == []

    events = _events(disposable_database, block["block_id"])
    assert [e[0] for e in events] == ["campaign_block.placed", "campaign_block.lifted"]
    assert events[0][1]["reason"] == "Incidente en revisión" and events[1][1]["reason"] == "Incidente resuelto"
    assert {e[2] for e in events} == {world["admin"].operator_id}
    assert events[0][1]["sends"] is False and events[0][1]["modifies_frozen_evidence"] is False
    status = _owner(disposable_database, "select status, version from outbound.campaign where id = %s", (cid,))
    assert status == [("draft", 1)]


@needs_db
def test_a_replay_answers_the_same_and_writes_nothing(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    first = _block(disposable_database, world["admin"], campaign_id=cid, key="replay-block-key-1")
    counts = _all_counts(disposable_database)
    again = _block(disposable_database, world["admin"], campaign_id=cid, key="replay-block-key-1")
    assert again["replayed"] is True and again["block"] == first["block"]
    assert _all_counts(disposable_database) == counts


@needs_db
def test_compare_and_set_and_a_second_block_are_refused(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    with pytest.raises(CommandRefused) as err:
        _block(disposable_database, world["admin"], campaign_id=cid, version=3)
    assert err.value.code == "stale_block_version"
    placed = _block(disposable_database, world["admin"], campaign_id=cid)
    with pytest.raises(CommandRefused) as err:
        _block(disposable_database, world["admin"], campaign_id=cid, version=1)
    assert err.value.code == "already_blocked"
    with pytest.raises(CommandRefused) as err:
        _unblock(disposable_database, world["admin"], placed["block"]["block_id"], version=2)
    assert err.value.code == "stale_version"
    _unblock(disposable_database, world["admin"], placed["block"]["block_id"])
    with pytest.raises(CommandRefused) as err:
        _unblock(disposable_database, world["admin"], placed["block"]["block_id"])
    assert err.value.code == "block_already_lifted"
    with pytest.raises(CommandRefused) as err:
        _block(disposable_database, world["admin"], campaign_id=cid, version=1)
    assert err.value.code == "stale_block_version"  # placed + lifted = version 2
    again = _block(disposable_database, world["admin"], campaign_id=cid, version=2)
    assert again["block_version"] == 3
    with pytest.raises(CommandRefused) as err:
        _block(disposable_database, world["admin"], campaign_id=str(uuid.uuid4()))
    assert err.value.code == "campaign_not_found"


@needs_db
def test_the_database_refuses_a_sales_operator_even_past_the_route(disposable_database, world) -> None:
    cid = _campaign(disposable_database, world)
    counts = _all_counts(disposable_database)
    with pytest.raises(CommandRefused) as err:
        _block(disposable_database, world["sales"], campaign_id=cid)
    assert err.value.status_code == 403 and err.value.code == "role_may_not_block"
    assert _all_counts(disposable_database) == counts


@needs_db
def test_the_september_hold_is_visible_and_lifted_only_by_the_command(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    reads = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    legacy = reads.campaign_blocks()["legacy"]
    assert [(b["legacy_campaign_key"], b["reference"], b["placed_by_kind"], b["placed_by"]) for b in legacy] == [
        ("septiembre18-2026-wave2", "incident_hold_september_2026", "migrator", "Migración")]
    hold_id = legacy[0]["block_id"]
    with pytest.raises(CommandRefused) as err:
        _unblock(disposable_database, world["sales"], hold_id)
    assert err.value.code == "role_may_not_block"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
            cur.execute("delete from outbound.campaign_block where id = %s", (hold_id,))
    assert reads.campaign_blocks()["legacy"][0]["active"] is True
    lifted = _unblock(disposable_database, world["admin"], hold_id, reason="Owner review closed the incident")
    assert lifted["block"]["active"] is False and lifted["block_version"] is None
    assert reads.campaign_blocks()["legacy"] == []
    assert [e[0] for e in _events(disposable_database, hold_id)] == ["campaign_block.lifted"]


@needs_db
def test_an_all_campaigns_block_reaches_every_campaign_read(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    cid = _campaign(disposable_database, world)
    reads = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    version = reads.campaign_blocks()["all_campaigns"]["block_version"]
    placed = _block(disposable_database, world["admin"], scope="all_campaigns", version=version, reason="Pausa general")
    try:
        detail = reads.campaign(cid)
        assert detail["hold"]["held"] is True
        assert [r["code"] for r in detail["hold"]["refusals"]] == ["all_campaigns_blocked"]
        overview = reads.marketing()
        assert overview["holds"]["all_campaigns"]["blocked"] is True
        assert all(c["hold"]["held"] for c in overview["campaigns"])
    finally:
        _unblock(disposable_database, world["admin"], placed["block"]["block_id"], reason="Fin de la pausa")
    assert reads.campaign(cid)["hold"]["held"] is False


@needs_db
def test_freezing_a_blocked_campaign_is_refused_by_the_command_and_the_database(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.audience_freeze import (
        FREEZE_CAMPAIGN_AUDIENCE,
        FreezeAudienceBody,
        V2AudienceFreezeRepository,
        freeze_fields,
    )

    from origenlab_api.v2.audience_freeze import FreezeCriteria
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    cid = _campaign(disposable_database, world)
    placed = _block(disposable_database, world["admin"], campaign_id=cid)
    preview = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database)).freeze_preview(cid, FreezeCriteria())
    assert preview["problems"][0]["code"] == "campaign_held" and preview["hold_refusals"] == ["campaign_blocked"]
    body = FreezeAudienceBody(campaign_id=cid, expected_version=1, expected_preview_sha256=preview["preview_sha256"],
                              confirmed=True, criteria={})
    repo = V2AudienceFreezeRepository(psycopg.connect, runtime_dsn(disposable_database))
    counts = _all_counts(disposable_database)
    with pytest.raises(CommandRefused) as err:
        repo.execute(command_name=FREEZE_CAMPAIGN_AUDIENCE, operator=world["sales"], fields=freeze_fields(body),
                     idempotency_key=uuid.uuid4().hex, digest=request_digest(FREEZE_CAMPAIGN_AUDIENCE, body))
    assert err.value.code == "campaign_held"
    assert _all_counts(disposable_database) == counts
    # Past the command, the database refuses the freeze write itself.
    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
            cur.execute("update outbound.campaign set status = 'audience_frozen' where id = %s", (cid,))
    _unblock(disposable_database, world["admin"], placed["block"]["block_id"])
