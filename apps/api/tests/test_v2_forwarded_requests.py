"""Forwarded requests in Hoy: pairs collapsed, handled ones gone, Labdelivery copies never a link.
Every name and address is invented."""
from __future__ import annotations

from origenlab_api.v2.forwarded_requests import group_forwards


def row(fid, status, origin, email="ana@laboratorio-ficticio.cl", item="Solicitud cotización Gradillas",
        at="2026-10-05T12:00:00+00:00", gmail="g-" + "x", company=None):
    return {"id": fid, "review_status": status, "created_at": at,
            "payload": {"original_email": email, "original_name": "Ana Prueba", "company": company, "rut": None,
                        "phone": None, "requested_item": item, "relay": None, "origin": origin, "subject": item,
                        "forwarded_message_id": f"m-{fid}"},
            "gmail_message_id": f"{gmail}{fid}", "sent_at": at}


def test_the_rv_and_fwd_copies_of_one_request_are_one_row_linking_the_origenlab_copy() -> None:
    items = group_forwards([row("f1", "pending", "labdelivery", item="RV: Solicitud cotización Gradillas"),
                            row("f2", "pending", "origenlab", at="2026-10-05T13:00:00+00:00")])
    assert len(items) == 1
    it = items[0]
    assert it["id"] == "f2" and sorted(it["ids"]) == ["f1", "f2"]
    assert it["customer"]["email"] == "ana@laboratorio-ficticio.cl"
    assert it["gmail_url"].endswith("g-xf2") and it["origins"] == ["labdelivery", "origenlab"]


def test_a_labdelivery_only_forward_has_no_email_link() -> None:
    [it] = group_forwards([row("f1", "pending", "labdelivery")])
    assert it["id"] == "f1" and it["gmail_url"] is None


def test_a_request_handled_or_dismissed_through_either_copy_is_gone() -> None:
    assert group_forwards([row("f1", "rejected", "labdelivery"), row("f2", "pending", "origenlab")]) == []
    assert group_forwards([row("f1", "promoted", "origenlab")]) == []


def test_different_customers_or_items_stay_apart_newest_first() -> None:
    items = group_forwards([row("f1", "pending", "origenlab", at="2026-10-01T00:00:00+00:00"),
                            row("f2", "pending", "origenlab", email="otro@empresa-ficticia.cl",
                                at="2026-10-09T00:00:00+00:00"),
                            row("f3", "pending", "origenlab", item="Otra cosa", at="2026-10-03T00:00:00+00:00")])
    assert [i["id"] for i in items] == ["f2", "f3", "f1"]


# -- against PostgreSQL: the read, «Crear solicitud» and «Descartar»

import itertools  # noqa: E402
import json  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402

from origenlab_api.v2.case_commands import (  # noqa: E402
    CREATE_REQUEST_FROM_FORWARD,
    DISMISS_FORWARDED_REQUEST,
    CreateRequestFromForwardBody,
    DismissForwardedRequestBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest  # noqa: E402
from origenlab_api.v2.forwarded_requests import read_forwarded_requests  # noqa: E402
from origenlab_api.v2.identity import OperatorIdentity  # noqa: E402

_KEYS = itertools.count(1)


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    import psycopg

    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


def _seed(db, origins: list[str], email: str) -> tuple[str, list[str], list[str]]:
    """An operator and one forwarded request per origin (each with its Gmail message)."""
    tag = uuid.uuid4().hex[:8]
    fids, mids = [], []
    with _owner(db) as conn, conn.cursor() as cur:
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) values "
                    "(gen_random_uuid(), %s, 'Vendedora', 'sales', 'active') returning id::text", (f"v-{tag}@example.test",))
        op = cur.fetchone()[0]
        for i, origin in enumerate(origins):
            cur.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('gmail_message', %s, "
                        "%s::jsonb) returning id::text",
                        (f"gmail_message:fw-{tag}-{i}", json.dumps({"gmail_message_id": f"g{tag}{i}",
                                                                     "sent_at": f"2026-10-0{i + 1}T12:00:00+00:00"})))
            mid = cur.fetchone()[0]
            payload = {"original_email": email, "original_name": "Ana Prueba", "company": "Laboratorio Ficticio EIRL",
                       "rut": "11.111.111-1", "phone": "900000001", "requested_item": "Tabletas de verificación",
                       "relay": "marketplace", "origin": origin, "subject": "Tabletas de verificación",
                       "forwarded_message_id": mid, "reading": "forwarded:v1"}
            cur.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('forwarded_request', %s, "
                        "%s::jsonb) returning id::text", (f"forwarded_request:{mid}", json.dumps(payload)))
            fids.append(cur.fetchone()[0])
            mids.append(mid)
    return op, fids, mids


def _run(db, op, name, body):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository

    operator = OperatorIdentity(operator_id=op, email_norm="v@example.test", display_name="Vendedora", role="sales",
                                status="active")
    return V2CaseCommandRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name=name, operator=operator, fields=validated_case(name, body),
        idempotency_key=f"pytest-fw-{next(_KEYS)}-{uuid.uuid4().hex[:6]}", digest=request_digest(name, body))


def _items(db):
    import psycopg

    with psycopg.connect(runtime_dsn(db)) as conn, conn.cursor() as cur:
        return read_forwarded_requests(cur)


@needs_db
def test_crear_solicitud_opens_a_lead_never_links_the_labdelivery_copy_and_clears_both(db) -> None:
    email = f"ana-{uuid.uuid4().hex[:6]}@laboratorio-ficticio.cl"
    op, (lab, ours), (lab_mail, our_mail) = _seed(db, ["labdelivery", "origenlab"], email)
    [row] = [i for i in _items(db) if i["customer"]["email"] == email]
    assert row["id"] == ours and sorted(row["ids"]) == sorted([lab, ours])

    out = _run(db, op, CREATE_REQUEST_FROM_FORWARD, CreateRequestFromForwardBody(
        forwarded_request_id=lab, new_organization_name="Laboratorio Ficticio EIRL", note="Solicitud reenviada"))
    case = out["opportunity_id"]
    with _owner(db) as conn:
        stage, origin, org = conn.execute(
            "select o.stage, o.origin_source_record_id::text, org.name from crm.opportunity o "
            "left join crm.organization org on org.id = o.organization_id where o.id = %s", (case,)).fetchone()
        links = {r[0] for r in conn.execute("select source_record_id::text from crm.opportunity_evidence "
                                            "where opportunity_id = %s", (case,)).fetchall()}
        note = conn.execute("select body from crm.note where subject_id = %s", (case,)).fetchone()[0]
        statuses = {r[0] for r in conn.execute("select review_status from evidence.source_record where id = any(%s::uuid[])",
                                               ([lab, ours],)).fetchall()}
    assert stage == "lead" and origin == lab and org == "Laboratorio Ficticio EIRL"
    assert our_mail in links and lab_mail not in links           # never the Labdelivery-mailbox email
    assert email in note and "11.111.111-1" in note
    assert statuses == {"promoted"}
    assert not [i for i in _items(db) if i["customer"]["email"] == email]
    with pytest.raises(CommandRefused) as again:
        _run(db, op, CREATE_REQUEST_FROM_FORWARD, CreateRequestFromForwardBody(forwarded_request_id=ours, note="otra"))
    assert again.value.code == "forwarded_request_handled"


@needs_db
def test_a_labdelivery_only_forward_opens_a_case_with_no_email_at_all(db) -> None:
    email = f"solo-{uuid.uuid4().hex[:6]}@gmail.com"
    op, (lab,), (lab_mail,) = _seed(db, ["labdelivery"], email)
    out = _run(db, op, CREATE_REQUEST_FROM_FORWARD, CreateRequestFromForwardBody(forwarded_request_id=lab, note="ok"))
    with _owner(db) as conn:
        links = {r[0] for r in conn.execute("select source_record_id::text from crm.opportunity_evidence "
                                            "where opportunity_id = %s", (out["opportunity_id"],)).fetchall()}
    assert lab_mail not in links and links == {lab} and out["organization_id"] is None


@needs_db
def test_descartar_removes_every_copy(db) -> None:
    email = f"nota-{uuid.uuid4().hex[:6]}@empresa-ficticia.cl"
    op, (a, b), _ = _seed(db, ["labdelivery", "origenlab"], email)
    _run(db, op, DISMISS_FORWARDED_REQUEST, DismissForwardedRequestBody(forwarded_request_id=b, note="no es una solicitud"))
    assert not [i for i in _items(db) if i["customer"]["email"] == email]
    with _owner(db) as conn:
        assert {r[0] for r in conn.execute("select review_status from evidence.source_record where id = any(%s::uuid[])",
                                           ([a, b],)).fetchall()} == {"rejected"}
