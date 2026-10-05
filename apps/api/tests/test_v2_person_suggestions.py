"""People the quote emails already name: the computation (pure), the route, and the SQL read
(database, opt-in). Every name, address, institution and file name is invented.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2 import quote_crm_import_plan
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.person_suggestions import (
    OWN_DOMAINS,
    HeldAddress,
    OrgNames,
    SourceRow,
    clean_name,
    compute_person_suggestions,
    parse_recipients,
    person_from_filename,
    safe_person_suggestions,
)
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

UNI = "0a000000-0000-4000-8000-00000000000a"
LAB = "0b000000-0000-4000-8000-00000000000b"
ORGS = {
    UNI: OrgNames(name="Universidad Ficticia", legal_name=None, domains=("ficticia.example.invalid",)),
    LAB: OrgNames(name="Labficticio", legal_name="Laboratorio de Ensayos Ficticio SpA", domains=()),
}


def row(recipients: str | None, *, quote: str = "q1", sent: str = "2026-03-01T10:00:00+00:00",
        org: str | None = UNI, filenames: tuple[str, ...] | list[str] = ()) -> SourceRow:
    return SourceRow(quote_id=quote, sent_at=sent, recipients=recipients, filenames=tuple(filenames), organization_id=org)


# ─────────────────────────────────────────────────────────────── recipients header ──


def test_quoted_names_with_commas_stay_whole() -> None:
    assert parse_recipients('"Pérez Soto, Ana" <ana@ficticia.example.invalid>, beto@ficticia.example.invalid') == [
        ("Pérez Soto, Ana", "ana@ficticia.example.invalid"),
        (None, "beto@ficticia.example.invalid"),
    ]


def test_semicolons_upper_case_and_repeats() -> None:
    assert parse_recipients("Ana <ANA@Ficticia.example.invalid>; ana@ficticia.example.invalid") == [
        ("Ana", "ana@ficticia.example.invalid"),
    ]


def test_addresses_separated_by_spaces_are_still_found() -> None:
    found = {a for _, a in parse_recipients("ana@ficticia.example.invalid beto@ficticia.example.invalid")}
    assert found == {"ana@ficticia.example.invalid", "beto@ficticia.example.invalid"}


def test_a_header_without_addresses_gives_nothing() -> None:
    assert parse_recipients("sin direcciones, <>, @@") == []
    assert parse_recipients(None) == []


@pytest.mark.parametrize(
    ("raw", "local", "expected"),
    [
        ("Ana Pérez Soto", "aperez", "Ana Pérez Soto"),
        ("Pérez Soto, Ana", "aperez", "Ana Pérez Soto"),
        ("ANA PÉREZ", "aperez", "Ana Pérez"),
        ("=?utf-8?q?Ana_P=C3=A9rez?=", "aperez", "Ana Pérez"),
        ("'Ana Pérez'", "aperez", "Ana Pérez"),
        ("aperez", "aperez", None),
        ("Ventas Ficticia", "ventas", None),
        ("Laboratorio Hudson", "lab", None),
        ("ana@ficticia.example.invalid", "ana", None),
        ("Ana 2", "ana2", None),
        ("", "x", None),
        (None, "x", None),
    ],
)
def test_only_a_persons_name_is_a_name(raw: str | None, local: str, expected: str | None) -> None:
    assert clean_name(raw, local_part=local) == expected


@pytest.mark.parametrize("raw", ["=?utf-8?b?x?=", "=?utf-8?q?Ana?= =?utf-8?b?x?=", "=?utf-8?b?"])
def test_a_cut_off_encoded_word_is_no_name_not_an_error(raw: str) -> None:
    assert clean_name(raw, local_part="x") is None


def test_a_cut_off_encoded_word_does_not_stop_the_computation() -> None:
    header = "=?utf-8?b?x?= <bad@ficticia.example.invalid>, Ana Pérez <ana@ficticia.example.invalid>"
    got = [s["email"] for s in compute_person_suggestions([row(header)], ORGS, {})]
    assert got == ["ana@ficticia.example.invalid"]


@pytest.mark.parametrize("raw", ["Universidad Ficticia", "Mesa de Ayuda", "Biblioteca Central", "Clínica Norte",
                                 "FUNDACIÓN Sur", "Hospital Base", "Ministerio X"])
def test_an_institutions_words_are_not_a_persons_name(raw: str) -> None:
    assert clean_name(raw, local_part="x") is None


def test_a_name_equal_to_the_institution_is_no_name_but_a_person_is() -> None:
    inst = ("Labficticio", "Laboratorio de Ensayos Ficticio SpA")
    assert clean_name("LABFICTICIO", local_part="x", institutions=inst) is None
    assert clean_name("Labfictício", local_part="x", institutions=inst) is None
    assert clean_name("Ana Pérez", local_part="x", institutions=inst) == "Ana Pérez"
    assert compute_person_suggestions([row("Labficticio <x@labficticio.example.invalid>", org=LAB)], ORGS, {}) == []


def test_the_pdf_name_gives_a_person_and_an_institution() -> None:
    assert person_from_filename("CN00987-Marta Inventada – Labficticio.pdf") == ("Marta Inventada", "Labficticio")
    assert person_from_filename("cn00987 - Marta Inventada - Labficticio.PDF") == ("Marta Inventada", "Labficticio")
    assert person_from_filename("COT 01024-26 UP200St.pdf") is None
    assert person_from_filename("Cotización.pdf") is None


# ─────────────────────────────────────────────────────────────────── suggestions ──


def test_a_named_recipient_is_a_suggestion_with_its_institution_count_and_date() -> None:
    [s] = compute_person_suggestions(
        [
            row('"Ana Pérez" <ana@ficticia.example.invalid>', quote="q1", sent="2026-03-01T10:00:00+00:00"),
            row("ana@ficticia.example.invalid", quote="q2", sent="2026-04-01T10:00:00+00:00"),
        ],
        ORGS,
        {},
    )
    assert (s["email"], s["display_name"], s["name_source"]) == ("ana@ficticia.example.invalid", "Ana Pérez", "recipient")
    assert (s["organization_id"], s["organization_name"], s["quotes"]) == (UNI, "Universidad Ficticia", 2)
    assert s["last_sent_at"] == "2026-04-01T10:00:00+00:00" and s["existing_contact_point"] is None
    assert s["suggestion_ref"] and "ana" not in s["suggestion_ref"]


def test_own_desk_and_nameless_addresses_are_not_suggested() -> None:
    header = (
        "Cotizaciones <cotizaciones@origenlab.cl>, x@labdelivery.cl, contacto@ficticia.example.invalid, "
        "Ventas <ventas@ficticia.example.invalid>, sin-nombre@ficticia.example.invalid"
    )
    assert compute_person_suggestions([row(header)], ORGS, {}) == []


def test_a_desk_mailbox_with_a_persons_name_is_suggested() -> None:
    [s] = compute_person_suggestions([row("Ana Pérez <ventas@ficticia.example.invalid>")], ORGS, {})
    assert s["display_name"] == "Ana Pérez"


def test_the_pdf_names_the_one_nameless_recipient_when_its_institution_is_the_cases() -> None:
    hit = row("minventada@labficticio.example.invalid", org=LAB, filenames=["CN00987-Marta Inventada – Labficticio.pdf"])
    [s] = compute_person_suggestions([hit], ORGS, {})
    assert (s["display_name"], s["name_source"], s["organization_id"]) == ("Marta Inventada", "filename", LAB)


def test_the_pdf_is_not_used_for_another_institution_or_two_nameless_recipients() -> None:
    other = row("minventada@labficticio.example.invalid", org=UNI, filenames=["CN00987-Marta Inventada – Labficticio.pdf"])
    two = row("minventada@labficticio.example.invalid, jefe@labficticio.example.invalid", org=LAB,
              filenames=["CN00987-Marta Inventada – Labficticio.pdf"])
    assert compute_person_suggestions([other], ORGS, {}) == []
    assert compute_person_suggestions([two], ORGS, {}) == []


def test_a_named_recipient_beside_a_nameless_one_leaves_the_pdf_to_the_nameless() -> None:
    both = row("Jefa Uno <jefa@labficticio.example.invalid>, minventada@labficticio.example.invalid", org=LAB,
               filenames=["CN00987-Marta Inventada – Labficticio.pdf"])
    got = {s["email"]: s["display_name"] for s in compute_person_suggestions([both], ORGS, {})}
    assert got == {"jefa@labficticio.example.invalid": "Jefa Uno", "minventada@labficticio.example.invalid": "Marta Inventada"}


def test_a_domain_label_also_matches_the_pdf_institution() -> None:
    [s] = compute_person_suggestions(
        [row("x1@ficticia.example.invalid", filenames=["CN00001-Rosa Díaz – Ficticia.pdf"])], ORGS, {},
    )
    assert s["display_name"] == "Rosa Díaz"


def test_an_address_on_a_person_a_desk_or_deactivated_is_not_suggested() -> None:
    header = ("Ana Pérez <ana@ficticia.example.invalid>, Beto Ruiz <beto@ficticia.example.invalid>, "
              "Carla Soto <carla@ficticia.example.invalid>, Dina Vera <dina@ficticia.example.invalid>")
    held = {
        "ana@ficticia.example.invalid": HeldAddress(on_person=True, usage="personal", status="active"),
        "beto@ficticia.example.invalid": HeldAddress(on_person=False, usage="shared_mailbox", status="active"),
        "carla@ficticia.example.invalid": HeldAddress(on_person=False, usage="unattributed", status="inactive"),
        "dina@ficticia.example.invalid": HeldAddress(on_person=False, usage="unattributed", status="active"),
    }
    [s] = compute_person_suggestions([row(header)], ORGS, held)
    assert (s["email"], s["existing_contact_point"]) == ("dina@ficticia.example.invalid", "unattributed")


def test_the_recipients_own_name_beats_a_pdf_and_the_commoner_spelling_wins() -> None:
    rows = [
        row("x@labficticio.example.invalid", org=LAB, quote="a", filenames=["CN1001-Mart Inventada – Labficticio.pdf"]),
        row("Marta Inventada <x@labficticio.example.invalid>", org=LAB, quote="b", sent="2026-02-01T00:00:00+00:00"),
        row("Marta Inventada <x@labficticio.example.invalid>", org=LAB, quote="c", sent="2026-02-02T00:00:00+00:00"),
        row("M. Inventada <x@labficticio.example.invalid>", org=LAB, quote="d", sent="2026-05-01T00:00:00+00:00"),
    ]
    [s] = compute_person_suggestions(rows, ORGS, {})
    assert (s["display_name"], s["name_source"], s["quotes"]) == ("Marta Inventada", "recipient", 4)


def test_a_case_without_an_institution_suggests_the_person_without_one() -> None:
    [s] = compute_person_suggestions([row("Ana Pérez <ana@otra.example.invalid>", org=None)], ORGS, {})
    assert (s["organization_id"], s["organization_name"]) == (None, None)


def test_own_domains_are_the_import_plans() -> None:
    assert OWN_DOMAINS == quote_crm_import_plan.OWN_DOMAINS


def test_a_held_nameless_recipient_still_counts_so_the_pdf_is_not_guessed_onto_the_free_one() -> None:
    both = row("held@labficticio.example.invalid, free@labficticio.example.invalid", org=LAB,
               filenames=["CN00987-Marta Inventada – Labficticio.pdf"])
    held = {"held@labficticio.example.invalid": HeldAddress(on_person=True, usage="personal", status="active")}
    assert compute_person_suggestions([both], ORGS, held) == []


def test_a_person_named_own_domain_address_is_not_suggested() -> None:
    assert compute_person_suggestions([row("Persona Ficticia <persona@origenlab.cl>")], ORGS, {}) == []


def test_an_own_domain_nameless_address_does_not_count_toward_the_pdf_guard() -> None:
    hit = row("ficticio@origenlab.cl, minventada@labficticio.example.invalid", org=LAB,
              filenames=["CN00987-Marta Inventada – Labficticio.pdf"])
    [s] = compute_person_suggestions([hit], ORGS, {})
    assert (s["email"], s["display_name"]) == ("minventada@labficticio.example.invalid", "Marta Inventada")


def test_an_encoded_display_name_is_decoded_end_to_end() -> None:
    header = "=?UTF-8?Q?P=C3=A9rez=2C_Ana?= <ana@ficticia.example.invalid>"
    assert parse_recipients(header) == [("=?UTF-8?Q?P=C3=A9rez=2C_Ana?=", "ana@ficticia.example.invalid")]
    [s] = compute_person_suggestions([row(header)], ORGS, {})
    assert s["display_name"] == "Ana Pérez"


def test_a_desk_mailbox_alone_is_not_named_by_the_pdf() -> None:
    hit = row("contacto@ficticia.example.invalid", filenames=["CN00001-Rosa Díaz – Ficticia.pdf"])
    assert compute_person_suggestions([hit], ORGS, {}) == []


def test_an_address_with_an_unknown_individual_owner_is_suggested() -> None:
    held = {"ana@ficticia.example.invalid": HeldAddress(on_person=False, usage="individual_owner_unknown", status="active")}
    [s] = compute_person_suggestions([row("Ana Pérez <ana@ficticia.example.invalid>")], ORGS, held)
    assert s["existing_contact_point"] == "individual_owner_unknown"


# ───────────────────────────────────────────────────────────────────────── route ──


class _Identity:
    def __init__(self, role: str) -> None:
        self.role = role

    def resolve(self, headers: Any) -> OperatorIdentity:  # noqa: ARG002
        return OperatorIdentity(
            operator_id="00000000-0000-4000-8000-000000000001", email_norm=f"{self.role}@example.test",
            display_name=self.role, role=self.role, status="active",
        )


class _Repo:
    def person_suggestions(self) -> dict[str, Any]:
        item = {
            "suggestion_ref": "ref-ana", "email": "ana@ficticia.example.invalid", "display_name": "Ana Pérez",
            "name_source": "recipient", "organization_id": UNI, "organization_name": "Universidad Ficticia",
            "quotes": 2, "last_sent_at": "2026-04-01T10:00:00+00:00", "existing_contact_point": None,
        }
        return {"items": [item], "total": 1}

    def organization_authoring(self, org_id: str) -> dict[str, Any]:
        return {"organization": {"id": org_id}, "person_suggestions": self.person_suggestions()["items"]}


def _client(role: str) -> TestClient:
    app = FastAPI()
    app.state.v2_identity = _Identity(role)
    app.state.crm_workspace = _Repo()
    app.include_router(workspace_router)
    return TestClient(app)


def test_sales_reads_the_suggestions_with_their_addresses() -> None:
    response = _client("sales").get("/v2/workspace/person-suggestions")
    assert response.status_code == 200
    assert response.json()["items"][0]["email"] == "ana@ficticia.example.invalid"


def test_a_viewer_reads_them_with_addresses_masked() -> None:
    response = _client("viewer").get("/v2/workspace/person-suggestions")
    assert response.status_code == 200 and "ana@" not in response.text
    assert response.headers.get("X-OrigenLab-Redaction")


def test_a_viewer_sees_the_card_suggestions_with_addresses_masked() -> None:
    response = _client("viewer").get(f"/v2/workspace/organizations/{UNI}/authoring")
    assert response.status_code == 200 and "ana@" not in response.text
    assert response.json()["person_suggestions"][0]["display_name"] == "Ana Pérez"


class _BrokenCursor:
    """Fails the way a malformed row would, inside the computation."""

    description = (("quote_id",),)

    def execute(self, *_: Any) -> None:
        raise RuntimeError("Marta Secreta <leak@ficticia.example.invalid>")


def test_suggestions_that_raise_give_nothing_and_a_warning_with_the_class_only(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert safe_person_suggestions(_BrokenCursor()) == []
    assert [r.getMessage() for r in caplog.records] == ["person_suggestions: no suggestions — RuntimeError"]
    assert "Secreta" not in caplog.text and "leak@" not in caplog.text


def test_the_card_and_the_get_answer_200_when_the_computation_raises(monkeypatch) -> None:
    import contextlib

    from origenlab_api.v2 import person_suggestions as module
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    def boom(*_: Any) -> None:
        raise ValueError("Marta Secreta")

    monkeypatch.setattr(module, "compute_person_suggestions", boom)

    class _Cur:
        description = (("quote_id",),)

        def execute(self, *_: Any) -> None: ...

        def fetchall(self) -> list[Any]:
            return []

    repo = CrmWorkspaceRepository(None, "unused")

    class _Count:
        def fetchone(self) -> tuple[int]:
            return (3,)

    class _Session:
        def cursor(self):  # type: ignore[no-untyped-def]
            return contextlib.nullcontext(_Cur())

        def final(self, *_: Any) -> _Count:
            return _Count()

    @contextlib.contextmanager
    def fake_session():  # type: ignore[no-untyped-def]
        yield _Session()

    monkeypatch.setattr(repo, "_session", fake_session)
    assert repo.person_suggestions() == {"items": [], "total": 0, "registered_persons": 3}

    app = FastAPI()
    app.state.v2_identity = _Identity("sales")
    app.state.crm_workspace = repo
    app.include_router(workspace_router)
    response = TestClient(app).get("/v2/workspace/person-suggestions")
    assert response.status_code == 200 and response.json() == {"items": [], "total": 0, "registered_persons": 3}


# ─────────────────────────────────────────────────────────────────────── database ──


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def seeded(disposable_database: str) -> dict[str, str]:
    import psycopg

    tag = uuid.uuid4().hex[:8]
    w = {
        "ana": f"ana-{tag}@uni.example.invalid",
        "registered": f"registrada-{tag}@uni.example.invalid",
        "gis": f"gis-{tag}@lab.example.invalid",
    }
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
            (f"op-{tag}@example.test",),
        )
        op = cur.fetchone()[0]
        cur.execute(
            "insert into crm.organization (kind, name, confirmation, confirmed_by_operator_id) "
            "values ('universidad', %s, 'confirmed', %s) returning id::text",
            (f"Universidad Ficticia {tag}", op),
        )
        w["uni"] = cur.fetchone()[0]
        cur.execute(
            "insert into crm.organization (kind, name, confirmation, confirmed_by_operator_id) "
            "values ('laboratorio', %s, 'confirmed', %s) returning id::text",
            (f"Labficticio{tag}", op),
        )
        w["lab"] = cur.fetchone()[0]
        cur.execute(
            "insert into crm.person (display_name, confirmation, confirmed_by_operator_id) "
            "values ('Ya Registrada', 'confirmed', %s) returning id::text",
            (op,),
        )
        person = cur.fetchone()[0]
        cur.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
            "values ('email', %s, %s, %s, 'personal', 'confirmed')",
            (w["registered"], w["registered"], person),
        )
        cur.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
            "values ('email', %s, %s, 'unattributed', 'machine_proposed')",
            (w["ana"], w["ana"]),
        )

        def quote(org: str, recipients: str, filename: str, sha: str, number: str) -> None:
            payload = {"recipients": recipients, "documents": [{"filename": filename, "sha256": sha}]}
            cur.execute(
                "insert into evidence.source_record (kind, dedupe_key, payload) "
                "values ('gmail_message', %s, %s) returning id::text",
                (f"ps:{tag}:{number}", json.dumps(payload)),
            )
            sr = cur.fetchone()[0]
            with conn.transaction():  # the requesting-institution agreement is checked at commit
                cur.execute(
                    "insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                    "values (%s, 'lead', %s, %s) returning id::text",
                    (f"Cotización {number}", op, org),
                )
                case = cur.fetchone()[0]
                cur.execute(
                    "insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                    "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', current_date, "
                    "'confirmed', %s)",
                    (case, org, op),
                )
            cur.execute(
                "insert into crm.quote (opportunity_id, quote_number, number_origin) "
                "values (%s, %s, 'printed_historical') returning id::text",
                (case, number),
            )
            q = cur.fetchone()[0]
            cur.execute(
                "insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at, "
                "origin_source_record_id) values (%s, 1, 'sent', 'historical_import', %s, "
                "'2026-03-12T10:00:00+00:00', %s)",
                (q, sha, sr),
            )

        quote(w["uni"], f'"Pérez Soto, Ana" <{w["ana"]}>, Ya Registrada <{w["registered"]}>, cotizaciones@origenlab.cl',
              "COT 01024-26.pdf", "a" * 64, f"01024-{tag}")
        quote(w["lab"], w["gis"], f"CN00987-Marta Inventada – Labficticio{tag}.pdf", "b" * 64, f"00987-{tag}")
    return w


@needs_db
def test_the_read_suggests_from_the_crm_and_skips_who_is_already_a_person(disposable_database, seeded) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    answer = repo.person_suggestions()
    items = {s["email"]: s for s in answer["items"]}
    # The People page's count rides on this answer: crm.person as the database counts it.
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select count(*) from crm.person")
        assert answer["registered_persons"] == cur.fetchone()[0] >= 1
    assert set(items) == {seeded["ana"], seeded["gis"]}
    ana, gis = items[seeded["ana"]], items[seeded["gis"]]
    assert (ana["display_name"], ana["organization_id"], ana["existing_contact_point"]) == (
        "Ana Pérez Soto", seeded["uni"], "unattributed",
    )
    assert (gis["display_name"], gis["name_source"], gis["organization_id"]) == ("Marta Inventada", "filename", seeded["lab"])
    card = repo.organization_authoring(seeded["lab"])
    assert [s["email"] for s in card["person_suggestions"]] == [seeded["gis"]]
