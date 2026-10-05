"""Database round trips per V2 request: a budget that may only go down.

The hosted API (Render, Oregon) reaches its database (Supabase, São Paulo) through the
Supavisor session pooler, about 180 ms per round trip. Query execution is a few milliseconds;
what a dashboard page costs is the number of times the API waits for the database, times 180.
This module counts those waits on the wire (`v2_roundtrip_counter.py`) for the requests the
dashboard makes most, through the real app — the real pool, the real shared sign-in with a
selected profile, the real routes — against a disposable database with a small synthetic world.

`BUDGET` holds the current numbers. A change may lower one (then lower it here); a change that
raises one fails. `round_trips` is the query-phase count; `connects` must stay zero for a warm
pool, and `checkouts`/`health_checks` are recorded alongside so the table explains itself.

`test_heavy_reads_answer_what_they_answered_before` pins the answers of the reads the latency
work restructured to a normalised snapshot recorded from the implementation before it
(`fixtures/v2_roundtrip_reads.json`): pipelining must not change one byte of what they return.
An intentional change to one of those answers regenerates the snapshot with
`ORIGENLAB_V2_WRITE_READ_SNAPSHOT=1` and shows the diff in review.

Runs only with `ORIGENLAB_V2_TEST_DSN` and `ORIGENLAB_V2_API_TEST_DSN` (`v2_command_harness.py`).
Every name, address and PIN here is invented; `.test` and `.invalid` are reserved names.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import get_settings
from origenlab_api.v2.connection_pool import V2ConnectionPool
from origenlab_api.v2.profile_pin import FLOOR, PinHasher
from origenlab_api.v2.profile_roster import apply as roster_apply
from origenlab_api.v2.profile_roster import parse_roster
from origenlab_api.v2.profile_roster import plan as roster_plan
from test_v2_google_auth import CLIENT_ID, _claims, _google_jwks, _jwt
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn
from v2_roundtrip_counter import Measurement, RoundTripProxy

pytestmark = needs_db

#: Fixed, invented secrets: the snapshot carries address refs keyed by the session secret.
PEPPER = "roundtrip-pepper-for-tests-0123456789abcdefghijklmnopqrstuv"
SESSION_SECRET = "roundtrip-session-secret-for-tests-0123456789abcdefghijklmnop"
PRINCIPAL = "latencia@origenlab.cl"
SUBJECT = "sub-latencia"
PIN = "482917"
SNAPSHOT = Path(__file__).parent / "fixtures" / "v2_roundtrip_reads.json"

#: Query-phase round trips per request, measured with a warm pool. May only go down.
BUDGET: dict[str, int] = {
    # name: round trips      # before the latency work (afb6d7d8), same seed and app
    "session_check": 1,                     # 5
    "workspace_overview": 2,                # 15
    "workspace_pipeline": 2,                # 10
    "workspace_marketing": 2,               # 25
    "workspace_marketing_audience": 2,      # 33
    "workspace_equipment_interests": 2,     # 24
    "cockpit_opportunities_page": 2,        # 11
    "contacts_page": 2,                     # 11
    "crm_create_person": 8,                 # 13
    "marketing_campaign_thumbnail": 2,      # 14
    "workspace_person_suggestions": 5,      # 12 (and it now carries the People count)
    "cockpit_kpis": 2,                      # 17
    "cockpit_work_queue": 2,                # 11
    "organizations_page": 2,                # 12
}

#: What each budget line requests.
REQUESTS: dict[str, tuple[str, str, dict[str, Any] | None]] = {
    "session_check": ("GET", "/auth/session", None),
    "workspace_overview": ("GET", "/v2/workspace/overview", None),
    "workspace_pipeline": ("GET", "/v2/workspace/pipeline", None),
    "workspace_marketing": ("GET", "/v2/workspace/marketing", None),
    "workspace_marketing_audience": ("GET", "/v2/workspace/marketing/audience", None),
    "workspace_equipment_interests": ("GET", "/v2/workspace/equipment-interests", None),
    "cockpit_opportunities_page": ("GET", "/v2/cockpit/opportunities?limit=20", None),
    "contacts_page": ("GET", "/v2/contacts?limit=20", None),
    "crm_create_person": ("POST", "/v2/commands/create-person",
                          {"display_name": "Persona Medida Ficticia", "note": "Medición de latencia"}),
    # Beyond the dashboard's heaviest pages: a Marketing thumbnail (one per card), the People
    # suggestions, the cockpit KPIs and work queue, and the organizations list with its facets.
    "marketing_campaign_thumbnail": ("GET", "/v2/workspace/marketing/campaigns/{camp_b}", None),
    "workspace_person_suggestions": ("GET", "/v2/workspace/person-suggestions", None),
    "cockpit_kpis": ("GET", "/v2/cockpit/kpis", None),
    "cockpit_work_queue": ("GET", "/v2/cockpit/work-queue?limit=50", None),
    "organizations_page": ("GET", "/v2/organizations?limit=20", None),
}

#: The reads whose answers must not move when their round trips do.
SNAPSHOT_READS: dict[str, str] = {
    "overview": "/v2/workspace/overview",
    "pipeline": "/v2/workspace/pipeline",
    "marketing": "/v2/workspace/marketing",
    "marketing_audience": "/v2/workspace/marketing/audience",
    "equipment_interests": "/v2/workspace/equipment-interests",
    "cockpit_kpis": "/v2/cockpit/kpis",
    "cockpit_opportunities": "/v2/cockpit/opportunities?limit=20",
    "cockpit_opportunities_past_end": "/v2/cockpit/opportunities?limit=20&offset=500",
    "contacts": "/v2/contacts?limit=20",
    "contacts_past_end": "/v2/contacts?limit=20&offset=500",
    "organizations": "/v2/organizations?limit=20",
    "person_suggestions": "/v2/workspace/person-suggestions",
    "campaign_blocks": "/v2/workspace/marketing/campaign-blocks",
    "suppressions": "/v2/workspace/marketing/suppressions",
    "review": "/v2/workspace/review",
    "cockpit_work_queue": "/v2/cockpit/work-queue?limit=50",
    "cockpit_quotations": "/v2/cockpit/quotations?limit=20",
    "marketing_campaign_draft": "/v2/workspace/marketing/campaigns/{camp_b}",
    "marketing_campaign_archived": "/v2/workspace/marketing/campaigns/{camp_a}",
}


# ----------------------------------------------------------------------------- the world


def _one(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> str:
    cur.execute(sql, params)
    return cur.fetchone()[0]


def seed_world(dsn: str) -> dict[str, str]:
    """A small, fixed commercial world: every name invented, every address under `.test`."""
    import psycopg

    w: dict[str, str] = {}
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        w["op"] = _one(cur, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                            "values (gen_random_uuid(), 'medicion-op@example.test', 'Operador Medición', 'sales', "
                            "'active') returning id::text")
        orgs = (("uni", "institution", "Universidad Ficticia del Sur", "confirmed"),
                ("lab", "institution", "Laboratorio Ficticio Norte", "machine_proposed"),
                ("sup", "company", "Fabricante Ficticio Ltda", "confirmed"))
        for key, kind, name, confirmation in orgs:
            w[key] = _one(cur, "insert into crm.organization (kind, name, confirmation) values (%s, %s, %s) "
                               "returning id::text", (kind, name, confirmation))
        cur.execute("insert into crm.organization_relationship (organization_id, role, valid_from) "
                    "values (%s, 'supplier', '2024-01-01')", (w["sup"],))
        cur.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) "
                    "values (%s, 'fabricante-ficticio.test', 'exclusive')", (w["sup"],))
        w["ana"] = _one(cur, "insert into crm.person (display_name, confirmation) values "
                             "('Ana Ficticia', 'confirmed') returning id::text")
        w["cp_ana"] = _one(cur, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation, "
                                "person_id, organization_id) values ('email', 'ana@uni-ficticia.test', "
                                "'ana@uni-ficticia.test', 'work', 'confirmed', %s, %s) returning id::text",
                           (w["ana"], w["uni"]))
        w["cp_lab"] = _one(cur, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation, "
                                "organization_id) values ('email', 'compras@lab-ficticio.test', "
                                "'compras@lab-ficticio.test', 'shared_mailbox', 'machine_proposed', %s) "
                                "returning id::text", (w["lab"],))
        for address in ("suelto@otro-ficticio.test", "baja@uni-ficticia.test"):
            cur.execute("insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                        "values ('email', %s, %s, 'unattributed', 'machine_proposed')", (address, address))

        # A Gmail quotation and a case that asked for it, with a sent revision.
        payload = {
            "subject_raw": "Cotización 01024-26 sonicador",
            "documents": [{"filename": "COT 01024-26 UP200St.pdf", "sha256": "a" * 64}],
            "recipients": "Ana <ana@uni-ficticia.test>, baja@uni-ficticia.test, ventas@fabricante-ficticio.test",
            "sent_at": "2026-03-12T10:00:00+00:00",
            "gmail_thread_id": "hilo-ficticio-1",
        }
        w["sr"] = _one(cur, "insert into evidence.source_record (kind, dedupe_key, payload) values "
                            "('gmail_message', 'medicion:cotizacion-1', %s) returning id::text", (json.dumps(payload),))
        with conn.transaction():
            # A case opens at `lead` and walks the stage machine (WORKFLOWS.md §1.1).
            w["case"] = _one(cur, "insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                                  "values ('Cotización 01024-26', 'lead', %s, %s) returning id::text",
                             (w["op"], w["uni"]))
            cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                        "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                        "'2026-03-01', 'confirmed', %s)", (w["case"], w["uni"], w["op"]))
            for stage in ("qualifying", "qualified", "quoting"):
                cur.execute("update crm.opportunity set stage = %s where id = %s", (stage, w["case"]))
        cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                    "confirmation, confirmed_by_operator_id) values (%s, %s, 'supplier', '2026-03-01', 'confirmed', %s)",
                    (w["case"], w["sup"], w["op"]))
        cur.execute("insert into crm.opportunity_participant (opportunity_id, person_id, contact_point_id, role, "
                    "valid_from, confirmation) values (%s, %s, %s, 'quote_recipient', '2026-03-01', 'machine_proposed')",
                    (w["case"], w["ana"], w["cp_ana"]))
        cur.execute("insert into crm.opportunity_interest (opportunity_id, model_text, description, confirmation) "
                    "values (%s, 'UP200St', 'Sonicador de laboratorio', 'machine_proposed')", (w["case"],))
        w["quote"] = _one(cur, "insert into crm.quote (opportunity_id, quote_number, number_origin) "
                               "values (%s, '01024-26', 'printed_historical') returning id::text", (w["case"],))
        cur.execute("insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at, "
                    "origin_source_record_id) values (%s, 1, 'sent', 'historical_import', %s, "
                    "'2026-03-12T10:00:00+00:00', %s)", (w["quote"], "a" * 64, w["sr"]))
        # A lead with no institution (a cockpit work item) and a closed case.
        w["lead"] = _one(cur, "insert into crm.opportunity (title, stage, owner_operator_id) "
                              "values ('Consulta centrífuga sin institución', 'lead', %s) returning id::text", (w["op"],))
        with conn.transaction():
            w["lost"] = _one(cur, "insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                                  "values ('Balanza analítica', 'lead', %s, %s) returning id::text", (w["op"], w["lab"]))
            cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                        "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                        "'2026-03-15', 'confirmed', %s)", (w["lost"], w["lab"], w["op"]))
            cur.execute("update crm.opportunity set stage = 'lost', closed_at = '2026-04-01T12:00:00+00:00', "
                        "close_reason = 'precio' where id = %s", (w["lost"],))

        # Evidence awaiting review: an ambiguous institution name, a supplier candidate.
        w["sr2"] = _one(cur, "insert into evidence.source_record (kind, dedupe_key, payload) values "
                             "('gmail_message', 'medicion:consulta-2', %s) returning id::text",
                        (json.dumps({"subject_raw": "Consulta balanza", "recipients": "compras@lab-ficticio.test"}),))
        cur.execute("insert into evidence.assertion (source_record_id, kind, value_norm, resolution, ambiguity_note) "
                    "values (%s, 'organization_name', 'laboratorio ficticio', 'ambiguous', 'dos candidatos')",
                    (w["sr2"],))
        cur.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value) values "
                    "(%s, 'supplier_candidate', 'fabricante-ficticio.test', %s)",
                    (w["sr2"], json.dumps({"trade_name": "Fabricante Ficticio"})))

        # Marketing: an archived campaign with sends, a bounce and a reply; a draft with HTML.
        w["mailbox"] = _one(cur, "insert into comms.mailbox (address_norm, display_name) values "
                                 "('ventas@origenlab-ficticio.test', 'OrigenLab Ficticio') returning id::text")
        w["camp_a"] = _one(cur, "insert into outbound.campaign (name, status, mailbox_id, max_sends) values "
                                "('Campaña Ficticia Archivada', 'archived', %s, 500) returning id::text",
                           (w["mailbox"],))
        w["camp_b"] = _one(cur, "insert into outbound.campaign (name, status, mailbox_id, max_sends, recontact_interval_days, "
                                "subject, body_html) values ('Borrador Ficticio', 'draft', %s, 100, 180, 'Ofertas de laboratorio', "
                                "'<html><body><p>Hola</p></body></html>') returning id::text", (w["mailbox"],))
        rid: dict[str, str] = {}
        for key, address, state in (("ana", "ana@uni-ficticia.test", "sent"),
                                    ("lab", "compras@lab-ficticio.test", "bounced"),
                                    ("baja", "baja@uni-ficticia.test", "excluded")):
            rid[key] = _one(cur, "insert into outbound.campaign_recipient (campaign_id, address_norm, state, "
                                 "exclusion_reasons) values (%s, %s, %s, %s) returning id::text",
                            (w["camp_a"], address, state, ["block"] if state == "excluded" else []))
        attempts: dict[str, str] = {}
        for key, address, when in (("ana", "ana@uni-ficticia.test", "2026-09-02T13:08:06+00:00"),
                                   ("lab", "compras@lab-ficticio.test", "2026-09-03T15:00:00+00:00")):
            attempts[key] = _one(cur, "insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, "
                                      "mailbox_id, address_norm, submission_state, delivery_state, accepted_at) "
                                      "values ('marketing', %s, %s, %s, %s, 'accepted', 'pending', %s) returning id::text",
                                 (w["camp_a"], rid[key], w["mailbox"], address, when))
        inbound = _one(cur, "insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) "
                            "values (%s, 'gm-respuesta-ficticia', 'inbound', '2026-09-04T10:00:00+00:00') "
                            "returning id::text", (w["mailbox"],))
        cur.execute("insert into outbound.campaign_reply (campaign_id, campaign_recipient_id, message_id, "
                    "send_attempt_id, received_at, proposed_class, proposed_by) values (%s, %s, %s, %s, "
                    "'2026-09-04T10:00:00+00:00', 'human_reply', 'ingest_classifier')",
                    (w["camp_a"], rid["ana"], inbound, attempts["ana"]))
        cur.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values "
                    "('address', 'baja@uni-ficticia.test', 'block', 'all', 'pidió no recibir correos', "
                    "'operator_command')")
        cur.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values "
                    "('address', 'ana@uni-ficticia.test', 'prior_contact', 'marketing', 'campaña anterior', "
                    "'operator_command')")
    return w


def provision_profile(dsn: str) -> str:
    """The shared sign-in and one admin profile; returns the profile's operator id."""
    import psycopg

    roster = parse_roster({"principal": {"email": PRINCIPAL, "provider_subject": SUBJECT},
                           "profiles": [{"key": "medicion", "display_name": "Medición", "role": "admin"}]},
                          workspace_domain="origenlab.cl")
    pins = {"medicion": PIN}
    with psycopg.connect(dsn) as conn:
        planned = roster_plan(conn, roster, pins)
        roster_apply(conn, roster, pins, hasher=PinHasher(PEPPER, FLOOR),
                     expected_changes=planned.pending, expected_database=planned.database)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select operator_id::text from platform.operator_profile where profile_key = 'medicion'")
        return cur.fetchone()[0]


@pytest.fixture(scope="module")
def proxy() -> Iterator[RoundTripProxy]:
    rtt = RoundTripProxy.for_dsn(os.environ["ORIGENLAB_V2_API_TEST_DSN"])
    try:
        yield rtt
    finally:
        rtt.close()


@pytest.fixture(scope="module")
def budget_db() -> Iterator[str]:
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def budget_world(budget_db: str) -> dict[str, str]:
    world = seed_world(budget_db)
    world["profile"] = provision_profile(budget_db)
    return world


@pytest.fixture(scope="module")
def reads_db() -> Iterator[str]:
    """A second world, only ever read: the budget's command must not move the snapshot."""
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def reads_world(reads_db: str) -> dict[str, str]:
    world = seed_world(reads_db)
    world["profile"] = provision_profile(reads_db)
    return world


# ----------------------------------------------------------------------------- the app


class SignedInApp:
    """One app over the proxy, signed in through the shared Google account with a profile."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, db: str, proxy: RoundTripProxy, profile_id: str) -> None:
        env = {
            "ORIGENLAB_DISABLE_DOTENV": "1",
            "ORIGENLAB_V2_DATABASE_URL": proxy.dsn(runtime_dsn(db)),
            "ORIGENLAB_GOOGLE_AUTH_ENABLED": "true",
            "ORIGENLAB_GOOGLE_CLIENT_ID": CLIENT_ID,
            "ORIGENLAB_GOOGLE_CLIENT_SECRET": "client-secret-for-tests",
            "ORIGENLAB_AUTH_PUBLIC_BASE_URL": "http://localhost:5173",
            "ORIGENLAB_AUTH_SESSION_SECRET": SESSION_SECRET,
            "ORIGENLAB_PROFILE_LOGIN_ENABLED": "true",
            "ORIGENLAB_PROFILE_PIN_PEPPER": PEPPER,
            "ORIGENLAB_DEV_LOGIN_ENABLED": "false",
            "ORIGENLAB_V2_COMMANDS_ENABLED": "true",
            "ORIGENLAB_V2_CRM_AUTHORING_ENABLED": "true",
        }
        for name in ("ORIGENLAB_ENV", "ORIGENLAB_V2_JWKS_URL", "ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL",
                     "ORIGENLAB_V2_DRIVE_ARCHIVE_LEDGERS", "ORIGENLAB_V2_V1_LANE_CONTENT_DIR",
                     "ORIGENLAB_V2_ORG_SUGGESTIONS_FILE", "ORIGENLAB_V2_POOL_SIZE"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        # Every checkout of the pool is counted, through the class the app binds at startup.
        original = V2ConnectionPool.connect

        def counting_connect(pool: V2ConnectionPool, dsn: str, *, autocommit: bool = False) -> Any:
            proxy.count_checkout()
            return original(pool, dsn, autocommit=autocommit)

        monkeypatch.setattr(V2ConnectionPool, "connect", counting_connect)
        get_settings.cache_clear()
        self.app = create_app()
        self.app.state.v2_token_exchanger = self._exchange
        self.app.state.v2_google_jwks = _google_jwks()
        self.profile_id = profile_id
        self._nonce = ""

    def _exchange(self, *, code: str, verifier: str, config: Any) -> dict[str, Any]:
        claims = _claims(self._nonce, email=PRINCIPAL, sub=SUBJECT)
        return {"id_token": _jwt(claims), "access_token": "discarded"}

    def __enter__(self) -> TestClient:
        self.client = TestClient(self.app).__enter__()
        login = self.client.get("/auth/google/login", follow_redirects=False)
        query = parse_qs(urlsplit(login.headers["location"]).query)
        self._nonce = query["nonce"][0]
        callback = self.client.get("/auth/google/callback", params={"code": "c", "state": query["state"][0]},
                                   follow_redirects=False)
        assert callback.status_code == 303, callback.text
        selected = self.client.post("/auth/profile/select", json={"profile_id": self.profile_id, "pin": PIN})
        assert selected.status_code == 200, selected.text
        # Measure against a pool that has finished opening its connections.
        self.app.state.v2_pool._pool.wait(timeout=30)
        return self.client

    def __exit__(self, *exc: Any) -> None:
        self.client.__exit__(*exc)
        get_settings.cache_clear()


def measure(client: TestClient, proxy: RoundTripProxy, name: str, world: dict[str, str]) -> tuple[Measurement, Any]:
    method, path, body = REQUESTS[name]
    path = path.format(**world)
    headers = {"Idempotency-Key": f"roundtrip-{uuid.uuid4().hex}"} if method == "POST" else {}
    with proxy.measure() as m:
        response = client.request(method, path, json=body, headers=headers)
    return m, response


# ----------------------------------------------------------------------------- the snapshot


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}(?::?\d{2})?)?")
_DATABASE = re.compile(r"origenlab_test_[0-9a-f]{8}")


def normalise(answers: dict[str, Any], seeded: dict[str, str]) -> dict[str, Any]:
    """The answers with what differs between two identical seeds replaced by stable names.

    Seeded ids keep their role name (`<uni>`), other ids are numbered by first appearance, the
    disposable database's name is `<database>`, and timestamps are `<timestamp>` — the seed's
    own dates are still compared through the dates and counts derived from them.
    """
    names = {v: k for k, v in seeded.items()}
    answers = dict(answers)
    if "marketing" in answers:
        # Not database answers: checked-in V1-lane declarations and switches of this app.
        answers["marketing"] = {k: v for k, v in answers["marketing"].items()
                                if k not in ("v1_lane_campaigns", "test_send", "authoring", "time_zone")}
    text = json.dumps(answers, sort_keys=True, ensure_ascii=False, indent=1)
    text = _DATABASE.sub("<database>", text)
    text = _TIMESTAMP.sub("<timestamp>", text)
    numbered: dict[str, str] = {}

    def rename(match: re.Match[str]) -> str:
        value = match.group(0)
        if value in names:
            return f"<{names[value]}>"
        return numbered.setdefault(value, f"<id{len(numbered) + 1}>")

    return json.loads(_UUID.sub(rename, text))


def test_heavy_reads_answer_what_they_answered_before(monkeypatch, reads_db, reads_world, proxy) -> None:
    with SignedInApp(monkeypatch, reads_db, proxy, reads_world["profile"]) as client:
        answers = {}
        for name, path in SNAPSHOT_READS.items():
            response = client.get(path.format(**reads_world))
            assert response.status_code == 200, (name, response.text)
            answers[name] = response.json()
    got = normalise(answers, reads_world)
    if os.environ.get("ORIGENLAB_V2_WRITE_READ_SNAPSHOT") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(got, sort_keys=True, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for name in SNAPSHOT_READS:
        assert got[name] == expected[name], f"{name} answers differently than before"


# ----------------------------------------------------------------------------- the budget


def test_every_request_has_a_budget() -> None:
    assert set(BUDGET) == set(REQUESTS)


@pytest.mark.parametrize("name", list(BUDGET))
def test_round_trips_stay_within_budget(name, monkeypatch, budget_db, budget_world, proxy) -> None:
    with SignedInApp(monkeypatch, budget_db, proxy, budget_world["profile"]) as client:
        m, response = measure(client, proxy, name, budget_world)
    assert response.status_code == 200, response.text
    report = os.environ.get("ORIGENLAB_V2_RTT_REPORT")
    if report:
        with open(report, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": name, **m.as_dict(), "queries": m.queries}, ensure_ascii=False) + "\n")
    assert m.connects == 0, f"{name} opened {m.connects} new connection(s) on a warm pool"
    assert m.round_trips <= BUDGET[name], (
        f"{name}: {m.round_trips} database round trips, budget {BUDGET[name]} "
        f"(checkouts {m.checkouts}, health checks {m.health_checks}): {m.queries}"
    )
