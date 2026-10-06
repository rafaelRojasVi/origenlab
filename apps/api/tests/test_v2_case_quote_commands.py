"""`resolve_current_revision` and `record_case_quotation` — the case drawer's quote acts, against a database.

The in-process rules (request shape, the routes, the switch) live in
`test_v2_case_command_boundary.py`. This module proves what only PostgreSQL can:

* «Elegir revisión vigente» supersedes every other current revision of the quote by the chosen
  one — including a *higher*-numbered historical revision (migration 20261006120000) — with one
  `quote_revision.superseded` event each, advances the case version, and refuses by name;
* «Registrar cotización» records a quote already sent, from a Gmail message already linked to
  the case, through the one writer the historical import and rules R3/R4 use, and refuses a
  printed number that is already a quote on another case;
* «Nueva revisión» is the same command naming the revision it replaces.

Runs in a disposable `origenlab_test_<hex>` (tests/v2_command_harness.py) as the unprivileged
`origenlab_api` role. Every name, number and address is fictitious.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_QUOTATION,
    RECORD_CASE_WON,
    RESOLVE_CURRENT_REVISION,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    LinkCaseEvidenceBody,
    OpenCommercialCaseBody,
    RecordCaseQuotationBody,
    RecordCaseWonBody,
    ResolveCurrentRevisionBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity

pytestmark = needs_db

SENT = "2026-09-25T15:00:00+00:00"
_KEYS = iter(range(1, 1_000_000))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    import psycopg

    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


def _gmail(cur, tag: str, shas: list[str], *, sent_at: str | None = SENT) -> str:
    """A live 4a capture: a Gmail message carrying `shas`, and no assertions."""
    payload = {"subject_raw": f"Cotización {tag}",
               "documents": [{"sha256": s, "filename": f"CN{s[:4]}.pdf", "cn_tokens": [f"CN{s[:4]}"]}
                             for s in shas]}
    if sent_at:
        payload["sent_at"] = sent_at
    cur.execute(
        "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
        "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
        (f"pytest-cq:{tag}:{uuid.uuid4().hex[:8]}", json.dumps(payload), f"gmail://msg/cq{tag}"),
    )
    return cur.fetchone()[0]


@pytest.fixture
def world(db):
    """One sales operator, one institution, a Gmail message carrying four quote PDFs."""
    tag = uuid.uuid4().hex[:12]
    sha = {k: hashlib.sha256(f"{k}{tag}".encode()).hexdigest() for k in "abcd"}
    w: dict[str, object] = {"tag": tag, **{f"sha_{k}": v for k, v in sha.items()},
                            "number": f"0{tag[:5]}-26", "other_number": f"1{tag[:5]}-26"}
    with _owner(db) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Vendedor Ficticio', 'sales', 'active') returning id::text",
            (f"ventas-{tag}@example.test",),
        )
        w["operator_id"] = cur.fetchone()[0]
        w["record_id"] = _gmail(cur, tag, list(sha.values()))
        w["unsent_record_id"] = _gmail(cur, f"{tag}-x", [sha["d"]], sent_at=None)
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) values "
            "('institution', %s, 'confirmed') returning id::text, version",
            (f"Instituto Ficticio {tag}",),
        )
        w["org_id"], w["org_version"] = cur.fetchone()
    return w


def _operator(world) -> OperatorIdentity:
    return OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                            display_name="Vendedor Ficticio", role="sales", status="active")


def _cases(db):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository

    return V2CaseCommandRepository(psycopg.connect, runtime_dsn(db))


def _run(db, world, name, body, key=None):
    return _cases(db).execute(command_name=name, operator=_operator(world), fields=validated_case(name, body),
                              idempotency_key=key or f"pytest-cq-{next(_KEYS)}",
                              digest=request_digest(name, body))


def _one(db, sql, *args):
    with _owner(db) as conn:
        return conn.execute(sql, args).fetchone()


def _events(db):
    return _one(db, "select count(*) from crm.domain_event")[0]


def _version(db, case_id) -> int:
    return _one(db, "select version from crm.opportunity where id = %s", case_id)[0]


def _case_at(db, world, stop_at="quoting") -> str:
    """A case opened from the world's message (so it is linked), with a confirmed requester."""
    opened = _run(db, world, OPEN_COMMERCIAL_CASE, OpenCommercialCaseBody(
        title="Equipo de laboratorio", origin_source_record_id=world["record_id"], note="correo"))
    case_id, version = opened["opportunity_id"], opened["opportunity_version"]
    version = _run(db, world, ADD_CASE_ORGANIZATION, AddCaseOrganizationBody(
        opportunity_id=case_id, opportunity_version=version, organization_id=world["org_id"],
        organization_version=world["org_version"], role="requesting_institution", note="confirmada"),
    )["opportunity_version"]
    stages = {"lead": [], "quoting": ["qualifying", "qualified", "quoting"]}[stop_at]
    for stage in stages:
        version = _run(db, world, ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage=stage, note="avanza"),
        )["opportunity_version"]
    return case_id


def _record(db, world, case_id, sha, number, *, record_id=None, supersedes=None, key=None, version=None):
    return _run(db, world, RECORD_CASE_QUOTATION, RecordCaseQuotationBody(
        opportunity_id=case_id, opportunity_version=version or _version(db, case_id), quote_number=number,
        source_record_id=record_id or world["record_id"], document_sha256=sha,
        supersedes_revision_no=supersedes, note="la enviamos desde Gmail"), key=key)


def _resolve(db, world, case_id, quote_id, revision_no, *, version=None, key=None):
    return _run(db, world, RESOLVE_CURRENT_REVISION, ResolveCurrentRevisionBody(
        opportunity_id=case_id, opportunity_version=version or _version(db, case_id), quote_id=quote_id,
        revision_no=revision_no, note="la del PDF firmado es la vigente"), key=key)


def _current(db, quote_id) -> list[int]:
    with _owner(db) as conn:
        rows = conn.execute(
            "select revision_no from crm.quote_revision where quote_id = %s and status <> 'void' "
            "and superseded_by_revision_no is null order by revision_no", (quote_id,)).fetchall()
    return [r[0] for r in rows]


def _refused(fn, *args, **kwargs) -> tuple[int, str]:
    with pytest.raises(CommandRefused) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.status_code, excinfo.value.code


# ───────────────────────────────────────────────────────────── «Registrar cotización» ──


def test_the_database_is_disposable(db) -> None:
    assert db.rpartition("/")[2].startswith("origenlab_test_")


def test_a_sent_quote_is_recorded_from_a_linked_gmail_message(db, world) -> None:
    case_id = _case_at(db, world)
    version = _version(db, case_id)
    result = _record(db, world, case_id, world["sha_a"], world["number"])
    assert result["command"] == RECORD_CASE_QUOTATION
    assert result["opportunity_version"] == version + 1 == _version(db, case_id)
    assert result["created"] == ["quote", "quote_revision"] and result["revision_no"] == 1
    assert result["assertion_id"] is None  # a live capture carries no assertion to promote
    row = _one(db, "select q.quote_number, q.number_origin, r.status, r.origin, r.pdf_sha256, "
                   "r.origin_source_record_id::text, r.sent_at = %s::timestamptz, q.opportunity_id::text "
                   "from crm.quote q join crm.quote_revision r on r.quote_id = q.id where r.id = %s",
               SENT, result["quote_revision_id"])
    assert row == (world["number"], "printed_historical", "sent", "historical_import", world["sha_a"],
                   world["record_id"], True, case_id)
    types, actors, mine = _one(db, "select array_agg(event_type), array_agg(distinct actor_kind), "
                                   "bool_and(actor_operator_id::text = %s) from crm.domain_event "
                                   "where command_receipt_id = %s", world["operator_id"], result["command_receipt_id"])
    assert (sorted(types), actors, mine) == (["quote.created", "quote_revision.historical_recorded"],
                                             ["operator"], True)


def test_a_new_revision_replaces_the_one_it_names(db, world) -> None:
    case_id = _case_at(db, world)
    first = _record(db, world, case_id, world["sha_a"], world["number"])
    second = _record(db, world, case_id, world["sha_b"], world["number"], supersedes=1)
    assert second["created"] == ["quote_revision"] and second["revision_no"] == 2
    assert second["superseded_revision_id"] == first["quote_revision_id"]
    assert _current(db, first["quote_id"]) == [2]


def test_a_number_already_on_another_case_is_refused(db, world) -> None:
    case_id = _case_at(db, world)
    other = _case_at(db, world)
    _record(db, world, other, world["sha_a"], world["number"])
    before = _events(db)
    assert _refused(_record, db, world, case_id, world["sha_b"], world["number"]) == (
        409, "number_on_other_opportunity")
    assert _events(db) == before
    assert _one(db, "select count(*) from crm.quote where opportunity_id = %s", case_id)[0] == 0


def test_a_message_not_linked_to_the_case_or_without_the_document_is_refused(db, world) -> None:
    case_id = _case_at(db, world)
    assert _refused(_record, db, world, case_id, world["sha_d"], world["number"],
                    record_id=world["unsent_record_id"]) == (404, "message_not_on_case")
    stranger = hashlib.sha256(b"not-carried").hexdigest()
    assert _refused(_record, db, world, case_id, stranger, world["number"]) == (
        409, "source_record_does_not_carry_document")


def test_a_linked_message_without_a_sending_time_is_refused(db, world) -> None:
    case_id = _case_at(db, world)
    _run(db, world, LINK_CASE_EVIDENCE, LinkCaseEvidenceBody(
        opportunity_id=case_id, opportunity_version=_version(db, case_id), relation="mentions",
        source_record_id=world["unsent_record_id"], note="también habla del equipo"))
    assert _refused(_record, db, world, case_id, world["sha_d"], world["number"],
                    record_id=world["unsent_record_id"]) == (409, "message_has_no_sent_at")


def test_a_case_not_yet_quoting_and_a_stale_version_are_refused(db, world) -> None:
    lead = _case_at(db, world, stop_at="lead")
    assert _refused(_record, db, world, lead, world["sha_a"], world["number"]) == (409, "case_not_quoting")
    case_id = _case_at(db, world)
    stale = _version(db, case_id) - 1
    assert _refused(_record, db, world, case_id, world["sha_a"], world["number"], version=stale) == (
        409, "case_version_conflict")
    assert _one(db, "select count(*) from crm.quote where opportunity_id = %s", case_id)[0] == 0


def test_the_same_key_replays_instead_of_recording_twice(db, world) -> None:
    case_id = _case_at(db, world)
    version = _version(db, case_id)
    key = f"pytest-cq-replay-{world['tag']}"
    first = _record(db, world, case_id, world["sha_a"], world["number"], key=key, version=version)
    events = _events(db)
    again = _record(db, world, case_id, world["sha_a"], world["number"], key=key, version=version)
    assert again["replayed"] is True and again["quote_revision_id"] == first["quote_revision_id"]
    assert _events(db) == events


def test_the_drawer_reads_the_linked_messages_and_their_documents(db, world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    case_id = _case_at(db, world)
    _record(db, world, case_id, world["sha_a"], world["number"])
    body = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db)).opportunity_mail_documents(case_id)
    assert body is not None and [m["source_record_id"] for m in body["messages"]] == [world["record_id"]]
    docs = {d["sha256"]: d for d in body["messages"][0]["documents"]}
    assert set(docs) == {world[f"sha_{k}"] for k in "abcd"}
    assert docs[world["sha_a"]]["recorded"] == {"quote_number": world["number"], "revision_no": 1,
                                                "on_this_case": True}
    assert docs[world["sha_b"]]["recorded"] is None
    assert docs[world["sha_b"]]["cn_tokens"] == [f"CN{world['sha_b'][:4]}"]
    assert CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db)).opportunity_mail_documents(
        str(uuid.uuid4())) is None


# ───────────────────────────────────────────────────────── «Elegir revisión vigente» ──


def _undetermined(db, world) -> tuple[str, str]:
    """A case whose quote has three current revisions: 1, 2, 3 (nobody named a supersession)."""
    case_id = _case_at(db, world)
    quote_id = _record(db, world, case_id, world["sha_a"], world["number"])["quote_id"]
    _record(db, world, case_id, world["sha_b"], world["number"])
    _record(db, world, case_id, world["sha_c"], world["number"])
    assert _current(db, quote_id) == [1, 2, 3]
    return case_id, quote_id


def test_choosing_the_oldest_revision_supersedes_the_newer_ones(db, world) -> None:
    case_id, quote_id = _undetermined(db, world)
    version = _version(db, case_id)
    result = _resolve(db, world, case_id, quote_id, 1)
    assert result["current_revision_no"] == 1 and result["superseded_revision_nos"] == [2, 3]
    assert result["opportunity_version"] == version + 1 == _version(db, case_id)
    assert _current(db, quote_id) == [1]
    rows = _one(db, "select array_agg(revision_no order by revision_no), bool_and(superseded_by_revision_no = 1), "
                    "bool_and(superseded_at is not null), bool_and(status = 'sent') from crm.quote_revision "
                    "where quote_id = %s and revision_no > 1", quote_id)
    assert rows == ([2, 3], True, True, True)
    events = _one(db, "select array_agg(event_type), array_agg(distinct actor_kind), "
                      "bool_and((payload->>'superseded_by_revision_no')::int = 1), "
                      "bool_and(payload->>'note' = 'la del PDF firmado es la vigente') "
                      "from crm.domain_event where command_receipt_id = %s", result["command_receipt_id"])
    assert events == (["quote_revision.superseded"] * 2, ["operator"], True, True)


def test_the_chosen_revision_can_then_be_won(db, world) -> None:
    case_id, quote_id = _undetermined(db, world)
    _resolve(db, world, case_id, quote_id, 2)
    version = _run(db, world, ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
        opportunity_id=case_id, opportunity_version=_version(db, case_id), stage="negotiating", note="negocia"),
    )["opportunity_version"]
    assert _refused(_run, db, world, RECORD_CASE_WON, RecordCaseWonBody(
        opportunity_id=case_id, opportunity_version=version, quote_id=quote_id, revision_no=3,
        note="orden de compra")) == (409, "quote_revision_not_current")
    won = _run(db, world, RECORD_CASE_WON, RecordCaseWonBody(
        opportunity_id=case_id, opportunity_version=version, quote_id=quote_id, revision_no=2,
        note="orden de compra"))
    assert won["stage"] == "won" and won["won_revision_no"] == 2


def test_resolve_refuses_by_name_and_writes_nothing(db, world) -> None:
    case_id, quote_id = _undetermined(db, world)
    other_case = _case_at(db, world)
    other_quote = _record(db, world, other_case, world["sha_d"], world["other_number"])["quote_id"]
    before = _events(db)
    assert _refused(_resolve, db, world, case_id, other_quote, 1) == (404, "quote_not_on_case")
    assert _refused(_resolve, db, world, case_id, quote_id, 9) == (404, "quote_revision_not_on_case")
    assert _refused(_resolve, db, world, other_case, other_quote, 1) == (409, "nothing_to_resolve")
    assert _refused(_resolve, db, world, case_id, quote_id, 1, version=_version(db, case_id) - 1) == (
        409, "case_version_conflict")
    assert _events(db) == before and _current(db, quote_id) == [1, 2, 3]
    _resolve(db, world, case_id, quote_id, 3)
    assert _refused(_resolve, db, world, case_id, quote_id, 1) == (409, "quote_revision_not_current")


def test_resolve_replays_with_the_same_key(db, world) -> None:
    case_id, quote_id = _undetermined(db, world)
    version = _version(db, case_id)
    key = f"pytest-cq-resolve-{world['tag']}"
    first = _resolve(db, world, case_id, quote_id, 2, version=version, key=key)
    events = _events(db)
    again = _resolve(db, world, case_id, quote_id, 2, version=version, key=key)
    assert again["replayed"] is True and again["superseded_revision_nos"] == first["superseded_revision_nos"]
    assert _events(db) == events


# ─────────────────────────────────────────────────────────────── «Último contacto» ──


def test_last_contact_reads_the_newest_email_each_way_on_the_case_threads(db, world) -> None:
    """A follow-up sent on a thread tied to the case is its last contact, linked or not."""
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    thread = f"thread-{world['tag']}"
    with _owner(db) as conn, conn.cursor() as cur:
        cur.execute(
            "update evidence.source_record set payload = payload || jsonb_build_object("
            "'gmail_thread_id', %s::text, 'gmail_message_id', %s::text, 'sender', 'contacto@origenlab.cl') "
            "where id = %s",
            (thread, f"m0-{world['tag']}", world["record_id"]),
        )
        for mid, sent, sender in (
            (f"m1-{world['tag']}", "2026-09-28T10:00:00-03:00", "Persona <persona@ejemplo.invalid>"),
            (f"m2-{world['tag']}", "2026-10-02T10:00:00-03:00", "Ventas <contacto@origenlab.cl>"),
        ):
            cur.execute(
                "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                "values ('gmail_message', %s, %s::jsonb, %s)",
                (f"pytest-lc:{mid}", json.dumps({"gmail_thread_id": thread, "gmail_message_id": mid,
                                                   "sent_at": sent, "sender": sender,
                                                   "subject_raw": f"Re: {world['tag']}"}),
                 f"gmail://msg/{mid}"),
            )
    case_id = _case_at(db, world, stop_at="lead")  # linked to the thread through its origin email
    pipeline = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db)).pipeline()
    card = next(c for c in pipeline["items"] if c["opportunity_id"] == case_id)
    assert card["last_contact"]["outbound"]["at"] == "2026-10-02T10:00:00-03:00"
    assert card["last_contact"]["outbound"]["url"].endswith(f"m2-{world['tag']}")
    assert card["last_contact"]["inbound"]["at"] == "2026-09-28T10:00:00-03:00"
