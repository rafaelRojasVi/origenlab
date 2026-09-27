"""Campaign drafts: what a request must carry, and what PostgreSQL records.

**In process, always run:** body validation, the HTML refusal, the switch, and the route
requirements (operator role, Idempotency-Key).

**Database-backed, opt-in:** create, save, compare-and-set on `version`, replay, the audit
events, and that a draft save writes nothing but `outbound.campaign` and the audit stream.
These run only in a disposable `origenlab_test_<hex>` the harness creates and drops, as the
`origenlab_api` runtime role. Every value is fictitious.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.campaign_drafts import (
    CREATE_CAMPAIGN_DRAFT,
    SAVE_CAMPAIGN_DRAFT,
    CreateCampaignDraftBody,
    SaveCampaignDraftBody,
    unsafe_html_reason,
    validated_draft,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
DRAFT = {"name": "Sonicadores", "max_sends": 50, "recontact_interval_days": 90}


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "html",
    [
        "<p>hola</p><script>alert(1)</script>",
        "<iframe src='https://x.test'></iframe>",
        "<form action='https://x.test'><input></form>",
        "<img src=x onerror=alert(1)>",
        "<a href='javascript:alert(1)'>x</a>",
        "<a href=\" JavaScript:alert(1)\">x</a>",
        '<meta http-equiv="refresh" content="0;url=https://x.test">',
        "<base href='https://x.test/'>",
        "<object data='x'></object>",
    ],
)
def test_unsafe_html_is_refused_not_cleaned(html: str) -> None:
    assert unsafe_html_reason(html) is not None
    with pytest.raises(CommandRefused) as err:
        validated_draft(CREATE_CAMPAIGN_DRAFT, CreateCampaignDraftBody(**DRAFT, body_html=html))
    assert err.value.status_code == 422 and err.value.code == "unsafe_html"


def test_ordinary_email_html_is_accepted() -> None:
    html = ('<table role="presentation"><tr><td style="font-family:Arial">'
            '<img src="https://origenlab.cl/products/hielscher/up200st.png" alt="UP200St" width="240">'
            '<a href="https://origenlab.cl/marcas/hielscher/">Ver</a></td></tr></table>')
    assert unsafe_html_reason(html) is None


def test_blank_content_is_absent_and_limits_have_no_default() -> None:
    body = CreateCampaignDraftBody(**DRAFT, subject="  ", preheader="", body_html="   ")
    assert body.subject is None and body.preheader is None and body.body_html is None
    with pytest.raises(ValidationError):
        CreateCampaignDraftBody(name="x")  # max_sends and recontact_interval_days are policy
    with pytest.raises(ValidationError):
        CreateCampaignDraftBody(**DRAFT, audience_criteria={"brand": "ika"})  # no audience here
    with pytest.raises(ValidationError):
        CreateCampaignDraftBody(**DRAFT, status="approved")


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
        return {"campaign_id": "x", "version": 1, "replayed": False}


def _client(repo, role="sales"):
    from fastapi import FastAPI

    from origenlab_api.v2.campaign_draft_routes import campaign_draft_router

    app = FastAPI()
    app.include_router(campaign_draft_router)
    app.state.campaign_draft_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


ROUTES = (
    ("/v2/commands/create-campaign-draft", DRAFT),
    ("/v2/commands/save-campaign-draft", {**DRAFT, "campaign_id": str(uuid.uuid4()), "expected_version": 1}),
)


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_a_viewer_may_not_write_a_draft(path, body) -> None:
    repo = _FakeRepo()
    r = _client(repo, role="viewer").post(path, json=body, headers={OPERATOR_EMAIL_HEADER: "op@example.test", "Idempotency-Key": "k"})
    assert r.status_code == 403 and repo.calls == []


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_a_draft_write_needs_an_idempotency_key(path, body) -> None:
    repo = _FakeRepo()
    r = _client(repo).post(path, json=body, headers={OPERATOR_EMAIL_HEADER: "op@example.test"})
    assert r.status_code == 400 and repo.calls == []


def test_the_draft_router_exposes_nothing_but_post() -> None:
    from origenlab_api.v2.campaign_draft_routes import campaign_draft_router

    assert {r.path for r in campaign_draft_router.routes} == {
        "/v2/commands/create-campaign-draft", "/v2/commands/save-campaign-draft"}
    for route in campaign_draft_router.routes:
        assert set(route.methods) == {"POST"}


def test_drafts_mount_only_behind_their_own_switch(monkeypatch) -> None:
    """Off by default; on only with a V2 database and the drafts switch, not the commands one."""
    from fastapi import FastAPI

    from origenlab_api import main
    from origenlab_api.settings import Settings

    def paths(**kw):
        app = FastAPI()
        main._mount_campaign_drafts(app, Settings(_env_file=None, **kw), "postgresql://x@127.0.0.1/db")
        return app.state.campaign_drafts_enabled, set(app.openapi()["paths"])

    enabled, routes = paths(v2_database_url=LOOPBACK)
    assert enabled is False and "/v2/commands/create-campaign-draft" not in routes
    enabled, routes = paths(v2_database_url=LOOPBACK, v2_commands_enabled=True)
    assert enabled is False and "/v2/commands/create-campaign-draft" not in routes
    enabled, routes = paths(v2_database_url=LOOPBACK, v2_campaign_drafts_enabled=True)
    assert enabled is True and "/v2/commands/save-campaign-draft" in routes


# --------------------------------------------------------------------------- database (opt-in)

#: A draft save may move only these. Everything else is counted before and after.
UNTOUCHABLE = ("outbound.campaign_recipient", "outbound.send_attempt", "outbound.contact_control",
               "outbound.send_control", "crm.opportunity", "crm.contact_point", "crm.opportunity_interest")


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def world(disposable_database):
    import psycopg

    tag = uuid.uuid4().hex[:10]
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Draft Operator', 'sales', 'active') returning id::text",
            (f"drafts-{tag}@example.test",),
        )
        operator_id = cur.fetchone()[0]
        cur.execute("insert into comms.mailbox (address_norm) values (%s)", (f"ventas-{tag}@example.invalid",))
    return {"operator": OperatorIdentity(operator_id=operator_id, email_norm=f"drafts-{tag}@example.test",
                                         display_name="Draft Operator", role="sales", status="active")}


def _repo(dsn):
    import psycopg

    from origenlab_api.v2.campaign_drafts import V2CampaignDraftRepository

    return V2CampaignDraftRepository(psycopg.connect, runtime_dsn(dsn))


def _run(dsn, world, command, body, key=None):
    return _repo(dsn).execute(
        command_name=command, operator=world["operator"], fields=validated_draft(command, body),
        idempotency_key=key or uuid.uuid4().hex, digest=request_digest(command, body),
    )


def _counts(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        out = {}
        for t in UNTOUCHABLE:
            cur.execute(f"select count(*) from {t}")  # noqa: S608 - fixed table names
            out[t] = cur.fetchone()[0]
        return out


def _events(dsn, campaign_id):
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select seq, event_type, payload from crm.domain_event where aggregate_kind = 'campaign' "
                    "and aggregate_id = %s order by seq", (campaign_id,))
        return cur.fetchall()


@needs_db
def test_create_then_save_persists_content_and_one_event_per_change(disposable_database, world) -> None:
    before = _counts(disposable_database)
    created = _run(disposable_database, world, CREATE_CAMPAIGN_DRAFT,
                   CreateCampaignDraftBody(**DRAFT, subject="Sonicadores Hielscher", body_html="<p>v1</p>"))
    assert created["status"] == "draft" and created["version"] == 1
    assert created["storage"] == {"table": "outbound.campaign", "database": disposable_database.rpartition("/")[2]}
    cid = created["campaign_id"]

    saved = _run(disposable_database, world, SAVE_CAMPAIGN_DRAFT, SaveCampaignDraftBody(
        **DRAFT, campaign_id=cid, expected_version=1, subject="Sonicadores Hielscher",
        preheader="UP200St para su laboratorio", body_html="<p>v2</p>"))
    assert saved["version"] == 2 and saved["changed_fields"] == ["preheader", "body_html"]

    import psycopg

    with psycopg.connect(runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select status, subject, preheader, body_html, version, audience_criteria "
                    "from outbound.campaign where id = %s", (cid,))
        assert cur.fetchone() == ("draft", "Sonicadores Hielscher", "UP200St para su laboratorio", "<p>v2</p>", 2, None)
    assert [e[1] for e in _events(disposable_database, cid)] == ["campaign.draft_created", "campaign.draft_content_saved"]
    assert _counts(disposable_database) == before


@needs_db
def test_a_stale_version_is_refused_and_writes_nothing(disposable_database, world) -> None:
    cid = _run(disposable_database, world, CREATE_CAMPAIGN_DRAFT, CreateCampaignDraftBody(**DRAFT))["campaign_id"]
    _run(disposable_database, world, SAVE_CAMPAIGN_DRAFT,
         SaveCampaignDraftBody(**DRAFT, campaign_id=cid, expected_version=1, subject="A"))
    with pytest.raises(CommandRefused) as err:
        _run(disposable_database, world, SAVE_CAMPAIGN_DRAFT,
             SaveCampaignDraftBody(**DRAFT, campaign_id=cid, expected_version=1, subject="B"))
    assert err.value.code == "stale_version"
    assert len(_events(disposable_database, cid)) == 2


@needs_db
def test_an_unchanged_save_records_nothing(disposable_database, world) -> None:
    cid = _run(disposable_database, world, CREATE_CAMPAIGN_DRAFT, CreateCampaignDraftBody(**DRAFT))["campaign_id"]
    out = _run(disposable_database, world, SAVE_CAMPAIGN_DRAFT,
               SaveCampaignDraftBody(**DRAFT, campaign_id=cid, expected_version=1))
    assert out["changed_fields"] == [] and out["version"] == 1
    assert len(_events(disposable_database, cid)) == 1


@needs_db
def test_a_replayed_key_returns_the_first_answer_and_writes_once(disposable_database, world) -> None:
    body = CreateCampaignDraftBody(**{**DRAFT, "name": f"Replay {uuid.uuid4().hex[:6]}"})
    first = _run(disposable_database, world, CREATE_CAMPAIGN_DRAFT, body, key="replay-key-1")
    again = _run(disposable_database, world, CREATE_CAMPAIGN_DRAFT, body, key="replay-key-1")
    assert again["replayed"] is True and again["campaign_id"] == first["campaign_id"]


@needs_db
def test_a_campaign_that_left_draft_cannot_be_edited_by_the_command_or_directly(disposable_database, world) -> None:
    import psycopg

    cid = _run(disposable_database, world, CREATE_CAMPAIGN_DRAFT, CreateCampaignDraftBody(**DRAFT))["campaign_id"]
    with psycopg.connect(runtime_dsn(disposable_database), autocommit=True) as conn:
        conn.execute("update outbound.campaign set status = 'cancelled' where id = %s", (cid,))
    with pytest.raises(CommandRefused) as err:
        _run(disposable_database, world, SAVE_CAMPAIGN_DRAFT,
             SaveCampaignDraftBody(**DRAFT, campaign_id=cid, expected_version=1, subject="tarde"))
    assert err.value.code == "campaign_not_draft"
    # The trigger holds even for a writer that skips the application's check.
    with psycopg.connect(runtime_dsn(disposable_database), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("update outbound.campaign set subject = 'tarde' where id = %s", (cid,))
