"""R16 against a real database (owner 2026-10-21): quotes made and approved in the Cotizador, their PDFs
emailed by contacto@ in one message already on the case → each revision sent, the case «Enviada», once;
never undone into «approved» (the frozen-revision trigger forbids it).

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role, through the real
Cotizador commands. Every name, number and address is fictitious.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quotes.commands import V2QuoteRepository
from origenlab_api.v2.quotes.draft import QUOTE_BODIES
from test_v2_quote_commands import WARRANTY, _case, _catalog_item, _owner, _rows, fake_fx
from test_v2_quote_commands import world as world  # noqa: PLC0414 - the module's catalog fixture, reused
from v2_command_harness import needs_db, runtime_dsn

pytestmark = needs_db


def _run(world: dict, command: str, raw: dict) -> dict:
    body = QUOTE_BODIES[command](**raw)
    repo = V2QuoteRepository(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=fake_fx)
    return repo.execute(command_name=command, operator=world["operator"], fields=body.model_dump(mode="json"),
                        idempotency_key=uuid.uuid4().hex, digest=request_digest(command, body))


def _approved(world: dict, case_id: str) -> dict:
    created = _run(world, "create-quote", {"opportunity_id": case_id})
    saved = _run(world, "save-quote-draft", {"revision_id": created["revision_id"], "expected_version": created["version"],
                                             "draft": {"items": [_catalog_item(world)],
                                                       "terms": {"warranty_terms_es": WARRANTY}}})
    _run(world, "approve-quote-revision", {"revision_id": created["revision_id"], "expected_version": saved["version"]})
    return created


def _admin(dsn: str) -> OperatorIdentity:
    email = f"admin-{uuid.uuid4().hex[:8]}@example.test"
    [(op_id,)] = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                             "values (gen_random_uuid(), %s, 'Admin Ficticio', 'admin', 'active') returning id::text",
                        (email,))
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Admin Ficticio", role="admin",
                            status="active")


def _email(dsn: str, documents: list[dict], *, case_id: str | None, linked_by: str, sent_at: datetime,
           thread: str | None = None, sender: str = "Tatiana <contacto@origenlab.cl>") -> tuple[str, str]:
    """A captured outbound Gmail message from contacto@, its stored copy, and its link to the case (R2's)."""
    gid = uuid.uuid4().hex[:16]
    thread = thread or f"t-{gid}"
    payload = {"gmail_message_id": gid, "gmail_thread_id": thread, "direction_hint": "external",
               "sender": sender, "recipients": "compras@cliente-ficticio.test",
               "subject_raw": "Cotizaciones", "sent_at": sent_at.isoformat(), "documents": documents}
    [(record,)] = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                              "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
                         (f"gmail_message:{gid}", json.dumps(payload), f"gmail://msg/{gid}"))
    [(mailbox,)] = _owner(dsn, "insert into comms.mailbox (address_norm) values (%s) returning id",
                          (f"mbox-{gid}@example.test",))
    [(message,)] = _owner(dsn, "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, "
                               "direction, internal_date) values (%s, %s, %s, 'outbound', now()) returning id::text",
                          (mailbox, gid, thread))
    if case_id is not None:
        _owner(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                    "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (case_id, record, linked_by))
    return record, message


def _doc(name: str) -> dict:
    """An attachment as the capture stores it: CN tokens by `capture.CN_TOKEN` (digits only)."""
    import re

    tokens = sorted({f"CN{int(d):05d}" for d in re.findall(r"(?i)(?<![A-Za-z0-9])CN[\s_-]?(\d{3,6})(?!\d)", name)})
    return {"filename": name, "sha256": uuid.uuid4().hex * 2, "cn_tokens": tokens}


def _mail_rules(dsn: str):
    from origenlab_api.v2.mail_rules_repository import MailRulesRepository

    return MailRulesRepository(psycopg.connect, runtime_dsn(dsn), commands_enabled=True)


def _planned(repo, evidence_id: str) -> dict[str, dict]:
    return {a["rule_id"]: a for a in repo.preview()["actions"] if a["evidence_id"] == evidence_id}


def _revision(dsn: str, revision_id: str) -> dict:
    return _rows(dsn, "select * from crm.quote_revision where id = %s", (revision_id,))[0]


def test_two_cotizador_pdfs_in_one_email_mark_both_sent_and_the_case_enviada(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _case(dsn, world["operator"], world["institution"])
    # Quote one, approved, then revised: revision 2 («…A») approved supersedes revision 1.
    one = _approved(world, case_id)
    revised = _run(world, "create-quote-revision", {"quote_id": one["quote_id"]})
    saved = _run(world, "save-quote-draft", {"revision_id": revised["revision_id"], "expected_version": revised["version"],
                                             "draft": {"items": [_catalog_item(world)],
                                                       "terms": {"warranty_terms_es": WARRANTY}}})
    _run(world, "approve-quote-revision", {"revision_id": revised["revision_id"], "expected_version": saved["version"]})
    two = _approved(world, case_id)
    serial_one = one["quote_number"][:5]

    # One renamed by hand, one saved under the name the browser offers.
    docs = [_doc(f"CN{serial_one}A-Cliente Ficticio.pdf"),
            _doc(f"Cotización CN{two['quote_number'][:5]} — Cliente Ficticio.pdf")]
    sent_at = datetime.now(UTC) + timedelta(minutes=1)
    email, message = _email(dsn, docs, case_id=case_id, linked_by=admin.operator_id, sent_at=sent_at)

    repo = _mail_rules(dsn)
    action = _planned(repo, email)["R16"]
    assert action["mode"] == "auto" and action["case_id"] == case_id
    result = repo.apply(admin, [{"evidence_id": email, "rule_id": "R16"}])
    assert not result["refused"], result["refused"]
    [applied] = result["applied"]
    assert [s["command"] for s in applied["steps"]] == ["record_authored_quote_sent"] * 2 + ["advance_case_stage"] * 3

    for revision_id, doc in ((revised["revision_id"], docs[0]), (two["revision_id"], docs[1])):
        rev = _revision(dsn, revision_id)
        assert (rev["status"], rev["pdf_sha256"], str(rev["sent_message_id"])) == ("sent", doc["sha256"], message)
        assert rev["sent_at"] == sent_at
    # Revision 1 of quote one stays as it was: superseded, never sent.
    assert _revision(dsn, one["revision_id"])["status"] == "approved"
    assert _rows(dsn, "select stage from crm.opportunity where id = %s", (case_id,))[0]["stage"] == "quoting"
    events = _rows(dsn, "select actor_kind, payload from crm.domain_event where aggregate_id = %s "
                        "and event_type = 'quote_revision.transitioned' order by seq desc limit 1", (two["revision_id"],))
    assert events[0]["actor_kind"] == "worker"
    assert (events[0]["payload"]["from"], events[0]["payload"]["to"]) == ("approved", "sent")
    assert events[0]["payload"]["attribution"]["rule_id"] == "R16"

    assert "R16" not in _planned(repo, email)  # once
    with pytest.raises(CommandRefused) as refused:
        repo.undo(admin, applied["command_receipt_id"], "no correspondía")
    assert refused.value.code == "sent_quote_is_final"


def test_a_pdf_emailed_before_the_approval_is_left_alone(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _case(dsn, world["operator"], world["institution"])
    quote = _approved(world, case_id)
    email, _ = _email(dsn, [_doc(f"CN{quote['quote_number'][:5]}-Cliente.pdf")], case_id=case_id,
                      linked_by=admin.operator_id, sent_at=datetime.now(UTC) - timedelta(hours=1))
    assert "R16" not in _planned(_mail_rules(dsn), email)
    assert _revision(dsn, quote["revision_id"])["status"] == "approved"


def test_a_new_email_is_linked_by_r2_then_sent_by_r16_on_the_next_pass(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _case(dsn, world["operator"], world["institution"])
    quote = _approved(world, case_id)
    doc = _doc(f"Cotización CN{quote['quote_number'][:5]} — Cliente Ficticio.pdf")
    email, _ = _email(dsn, [doc], case_id=None, linked_by=admin.operator_id,
                      sent_at=datetime.now(UTC) + timedelta(minutes=1))
    repo = _mail_rules(dsn)
    assert set(_planned(repo, email)) == {"R2"}  # one pass: the link by number, alone
    linked = repo.apply(admin, [{"evidence_id": email, "rule_id": "R2"}])
    assert not linked["refused"], linked["refused"]
    assert _revision(dsn, quote["revision_id"])["status"] == "approved"
    sent = repo.apply(admin, [{"evidence_id": email, "rule_id": "R16"}])  # the next pass
    assert not sent["refused"], sent["refused"]
    assert [s["command"] for s in sent["applied"][0]["steps"]][:2] == ["record_authored_quote_sent",
                                                                       "advance_case_stage"]
    assert _revision(dsn, quote["revision_id"])["status"] == "sent"
    assert _rows(dsn, "select count(*) as n from crm.opportunity_evidence where source_record_id = %s "
                      "and opportunity_id = %s and unlinked_at is null", (email, case_id))[0]["n"] == 1
    assert _rows(dsn, "select stage from crm.opportunity where id = %s", (case_id,))[0]["stage"] == "quoting"


def test_the_ygaero_shape_two_cases_one_reply(world) -> None:
    """A request (case A) whose thread gets the reply; the Cotizador put the second quote on case B."""
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_a = _case(dsn, world["operator"], world["institution"])
    case_b = _case(dsn, world["operator"], world["institution"])
    one, two = _approved(world, case_a), _approved(world, case_b)
    thread = f"t-{uuid.uuid4().hex[:12]}"
    _email(dsn, [], case_id=case_a, linked_by=admin.operator_id, sent_at=datetime.now(UTC) - timedelta(days=1),
           thread=thread, sender="compras@cliente-ficticio.test")
    docs = [_doc(f"Cotización CN{one['quote_number'][:5]} — Cliente.pdf"),
            _doc(f"Cotización CN{two['quote_number'][:5]} — Cliente.pdf")]
    email, _ = _email(dsn, docs, case_id=None, linked_by=admin.operator_id,
                      sent_at=datetime.now(UTC) + timedelta(minutes=1), thread=thread)
    repo = _mail_rules(dsn)
    result = repo.apply(admin, [{"evidence_id": email, "rule_id": "R16"}])
    assert not result["refused"], result["refused"]
    assert result["applied"][0]["case_id"] == case_a  # the thread's case is the action's
    for revision_id in (one["revision_id"], two["revision_id"]):
        assert _revision(dsn, revision_id)["status"] == "sent"
    for case_id in (case_a, case_b):
        assert _rows(dsn, "select stage from crm.opportunity where id = %s", (case_id,))[0]["stage"] == "quoting"
        assert _rows(dsn, "select count(*) as n from crm.opportunity_evidence where source_record_id = %s "
                          "and opportunity_id = %s and unlinked_at is null", (email, case_id))[0]["n"] == 1


def _request_of(dsn: str, operator: OperatorIdentity, institution: str | None, address: str) -> str:
    """An open request (a lead, no quote) whose quote recipient is `address` — as a forward (R11) opens it."""
    [(case_id,)] = _owner(dsn, "insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                               "values ('Solicitud — Balanzas', 'lead', %s, %s) returning id::text",
                          (operator.operator_id, institution))
    [(point,)] = _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                             "values ('email', %s, %s, 'unattributed', 'machine_proposed') returning id", (address, address))
    _owner(dsn, "insert into crm.opportunity_participant (opportunity_id, contact_point_id, role, is_primary, "
                "valid_from, confirmation) values (%s, %s, 'quote_recipient', true, current_date, 'machine_proposed')",
           (case_id, point))
    return case_id


def test_the_seed_company_shape_the_recipients_quoteless_request_closes_as_a_duplicate(world) -> None:
    """Production 2026-11-06: a forward opened the request (no institution yet); the Cotizador, not seeing
    it, opened its own case for the quote. The quote goes out → the request closes as a duplicate."""
    dsn = world["dsn"]
    admin = _admin(dsn)
    quoted = _case(dsn, world["operator"], world["institution"])
    request = _request_of(dsn, world["operator"], None, "compras@cliente-ficticio.test")
    one = _approved(world, quoted)
    email, _ = _email(dsn, [_doc(f"Cotización CN{one['quote_number'][:5]} — Cliente.pdf")], case_id=None,
                      linked_by=admin.operator_id, sent_at=datetime.now(UTC) + timedelta(minutes=1))
    repo = _mail_rules(dsn)
    # Pass one: R2 links the email to the case whose number the PDF carries; R16 waits. Pass two: R16.
    assert not repo.apply(admin, [{"evidence_id": email, "rule_id": "R2"}])["refused"]
    planned = _planned(repo, email)["R16"]
    assert planned["case_id"] == quoted and any("duplicad" in r for r in planned["reasons"])
    result = repo.apply(admin, [{"evidence_id": email, "rule_id": "R16"}])
    assert not result["refused"], result["refused"]
    assert _revision(dsn, one["revision_id"])["status"] == "sent"
    assert _rows(dsn, "select stage from crm.opportunity where id = %s", (quoted,))[0]["stage"] == "quoting"
    closed = _rows(dsn, "select stage, closed_at, close_reason from crm.opportunity where id = %s", (request,))[0]
    assert closed["stage"] == "abandoned" and closed["closed_at"] is not None
    assert quoted[:8] in closed["close_reason"] and one["quote_number"] in closed["close_reason"]
    for case_id in (quoted, request):
        assert _rows(dsn, "select count(*) as n from crm.opportunity_evidence where source_record_id = %s "
                          "and opportunity_id = %s and unlinked_at is null", (email, case_id))[0]["n"] == 1
    # Once: the second pass plans nothing more for this email.
    assert "R16" not in _planned(repo, email)


def test_the_golden_omega_shape_a_reply_on_another_open_cases_thread_still_sends(world) -> None:
    """Production 2026-11-06: a quote made on a new case, emailed as a reply on the thread of an
    older open case. R1 only proposes (thread vs number); R16 sends on the named case and leaves the other."""
    dsn = world["dsn"]
    admin = _admin(dsn)
    older = _case(dsn, world["operator"], world["institution"])
    newer = _case(dsn, world["operator"], world["institution"])
    sent_before = _approved(world, older)
    thread = f"t-{uuid.uuid4().hex[:12]}"
    _email(dsn, [_doc(f"Cotización CN{sent_before['quote_number'][:5]} — Cliente.pdf")], case_id=older,
           linked_by=admin.operator_id, sent_at=datetime.now(UTC) - timedelta(days=10), thread=thread)
    two = _approved(world, newer)
    email, _ = _email(dsn, [_doc(f"Cotización CN{two['quote_number'][:5]} — Cliente.pdf")], case_id=None,
                      linked_by=admin.operator_id, sent_at=datetime.now(UTC) + timedelta(minutes=1), thread=thread)
    repo = _mail_rules(dsn)
    planned = _planned(repo, email)
    assert planned["R1"]["mode"] == "proposal" and planned["R16"]["case_id"] == newer
    result = repo.apply(admin, [{"evidence_id": email, "rule_id": "R16"}])
    assert not result["refused"], result["refused"]
    assert _revision(dsn, two["revision_id"])["status"] == "sent"
    assert _rows(dsn, "select stage from crm.opportunity where id = %s", (newer,))[0]["stage"] == "quoting"
    assert _rows(dsn, "select count(*) as n from crm.opportunity_evidence where source_record_id = %s "
                      "and opportunity_id = %s and unlinked_at is null", (email, older))[0]["n"] == 0
