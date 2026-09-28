"""W10 unsubscribe: the «BAJA» grammar, the staging boundary, the command and its enforcement.

**In process, always run:** every accepted and refused BAJA variant, record validation (values
never echoed), the batch plan and its fingerprint, the audience/freeze rule that an unsubscribe
beats relevance, selection and W12, the routes (roles, switch, key, database failure), and the
regression that none of this can reach Gmail, the network or a send path.

**Database-backed, opt-in:** preview writes nothing; apply writes the evidence, the permanent
suppression (or the link) and one event per record through `outbound.add_contact_control`, as
the real `origenlab_api` login; replay and re-application are idempotent; a changed batch, a
malformed record, an unknown or unauthorised operator and a database refusal write nothing; the
audience preview and the freeze exclude the address and W12 cannot lift it; a recipient frozen
before its «BAJA» is refused by the send-time contract and shown as such, masked for a viewer.
A historical recipient proven by outbound lineage is suppressed without any identity being
created; a mismatched sender or missing lineage is held for review (durable, deduplicated,
blocking preview, freeze and send) and resolved once, idempotently; the function refuses a
forged basis, operator or receipt; and a fault at its last write leaves nothing behind.
Every value is fictitious.
"""

from __future__ import annotations

import ast
import json
import pathlib
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.audience_freeze import FreezeCriteria, RecontactDecision, plan_freeze
from origenlab_api.v2.equipment_taxonomy import load_taxonomy
from origenlab_api.v2.identity import IdentityRefused, OperatorIdentity
from origenlab_api.v2.marketing_audience import AudienceInputs, CaseFacts, EligibilityFacts, eligibility
from origenlab_api.v2.unsubscribe_replies import (
    APPLY_UNSUBSCRIBE_REPLIES,
    BAJA_GRAMMAR_VERSION,
    DISMISS_UNSUBSCRIBE_REVIEW,
    RESOLVE_UNSUBSCRIBE_REVIEW,
    UNSUBSCRIBE_POLICY_VERSION,
    ReplyFacts,
    classify_reply,
    input_sha256,
    parse_record,
    plan_batch,
    sender_address,
)
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


def _never_connects(*_args, **_kwargs):
    raise AssertionError("mounting a router must not open a database connection")


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "origenlab_api"
W10_MODULES = ("v2/unsubscribe_replies.py", "v2/unsubscribe_commands.py", "v2/unsubscribe_routes.py")


def _record(body: str | None = "BAJA", sender: str = "Ana Pérez <ana@uni.test>", mid: str | None = None, **kw) -> dict:
    return {
        "message_id": mid or f"<{uuid.uuid4().hex}@mail.uni.test>",
        "source": "gmail:ventas@origenlab.cl/INBOX",
        "from_header": sender,
        "received_at": "2026-09-26T15:04:05-03:00",
        "subject": "Re: Sonicadores Hielscher",
        "body_text": body,
        **kw,
    }


# --------------------------------------------------------------------------- the grammar

ACCEPTED = [
    "BAJA", "baja", "Baja", "bAjA", "  BAJA  ", "\n\nBAJA\n\n", "\tbaja\t", "BAJA.", "baja.",
    " BAJA ",                       # NBSP around it (NFKC → space)
    "​BAJA‍",                       # zero-width characters
    "ＢＡＪＡ",                                 # full-width letters (NFKC)
    "BAJA\r\n",                               # CRLF
    "BAJA\n\nEl lun, 21 sept 2026 a las 10:00, OrigenLab <ventas@origenlab.cl> escribió:\n> Responda BAJA",
    "BAJA\nOn Mon, Sep 21, 2026 at 10:00 AM OrigenLab <ventas@origenlab.cl> wrote:\n> text",
    "BAJA\n> Si no desea recibir más correos, responda BAJA",
    "baja\n-- \nAna Pérez\nLaboratorio",
    "BAJA\n\n-----Mensaje original-----\nDe: OrigenLab",
    "BAJA\n-----Original Message-----\nFrom: OrigenLab",
    "BAJA\n________________________________\nDe: OrigenLab <ventas@origenlab.cl>",
    "BAJA\nDe: OrigenLab <ventas@origenlab.cl>\nEnviado: lunes",
]

REFUSED = [
    ("BAJA!", "baja_not_standalone"),
    ("¡BAJA!", "baja_not_standalone"),
    ("BAJA?", "baja_not_standalone"),
    ("¿BAJA?", "baja_not_standalone"),
    ("BAJA..", "baja_not_standalone"),
    ("BAJA,", "baja_not_standalone"),
    ('"BAJA"', "baja_not_standalone"),
    ("«BAJA»", "baja_not_standalone"),
    ("*BAJA*", "baja_not_standalone"),
    ("BAJA BAJA", "baja_not_standalone"),
    ("BAJA\nBAJA", "baja_not_standalone"),
    ("BAJA por favor", "baja_not_standalone"),
    ("Por favor, dar de baja", "baja_not_standalone"),
    ("Denme de baja", "baja_not_standalone"),
    ("baja gracias", "baja_not_standalone"),
    ("BAJA\n\nAna Pérez", "baja_not_standalone"),
    ("BAJA\n\nEnviado desde mi iPhone", "baja_not_standalone"),
    ("La baja del equipo ya está lista", "baja_not_standalone"),
    ("No quiero la baja, sigan enviando", "baja_not_standalone"),
    ("BAJAR", "baja_not_standalone"),
    ("bajas", "baja_not_standalone"),
    # An attribution this grammar does not recognise (wrapped over two lines) is not cut, so the
    # quoted footer stays in the reply and the reply is refused — the safe direction.
    ("BAJA\nEl lun, 21 sept 2026 a las 10:00, OrigenLab (<\nventas@origenlab.cl>) escribió:\n> Responda BAJA",
     "baja_not_standalone"),
    ("B A J A", "no_baja"),
    ("Bája", "no_baja"),
    ("REMOVER!", "remover_not_standalone"),
    ("REMOVER..", "remover_not_standalone"),
    ("REMOVER,", "remover_not_standalone"),
    ('"REMOVER"', "remover_not_standalone"),
    ("«REMOVER»", "remover_not_standalone"),
    ("REMOVER REMOVER", "remover_not_standalone"),
    ("REMOVER por favor", "remover_not_standalone"),
    ("Favor remover mi correo", "remover_not_standalone"),
    ("remover de la lista", "remover_not_standalone"),
    ("REMOVER\n\nEnviado desde mi iPhone", "remover_not_standalone"),
    ("REMOVERME", "remover_not_standalone"),
    ("REMOVER BAJA", "baja_not_standalone"),
    ("R E M O V E R", "no_baja"),
    ("REMOVE", "no_baja"),
    ("Remóver", "no_baja"),
    ("Gracias, me interesa el UP200St", "no_baja"),
    ("> Si no desea recibir más correos, responda BAJA", "empty_reply"),  # only the quoted footer
    ("", "empty_reply"),
    ("   \n\t ", "empty_reply"),
    ("-- \nBAJA", "empty_reply"),
    (None, "no_plain_text"),
]


@pytest.mark.parametrize("body", ACCEPTED)
def test_a_clear_standalone_baja_is_accepted(body) -> None:
    verdict = classify_reply(body)
    assert verdict.accepted is True and verdict.code == "standalone_baja", repr(body)


#: The legacy V1 word, under exactly the same normalization: every accepted BAJA shape with
#: «REMOVER» in its place is accepted, and classified as such.
ACCEPTED_REMOVER = [b.replace("BAJA", "REMOVER").replace("baja", "remover").replace("Baja", "Remover")
                    .replace("bAjA", "rEmOvEr").replace("ＢＡＪＡ", "ＲＥＭＯＶＥＲ") for b in ACCEPTED]


@pytest.mark.parametrize("body", ACCEPTED_REMOVER)
def test_a_clear_standalone_remover_is_accepted_under_the_same_normalization(body) -> None:
    verdict = classify_reply(body)
    assert verdict.accepted is True and verdict.code == "standalone_remover", repr(body)


def test_the_grammar_version_names_the_remover_extension() -> None:
    assert BAJA_GRAMMAR_VERSION == "baja-reply/2026-09-27.v2"
    assert classify_reply("REMOVER").label == "Instrucción REMOVER clara (plantillas V1)"


@pytest.mark.parametrize(("body", "code"), REFUSED)
def test_anything_else_is_refused_with_its_reason(body, code) -> None:
    verdict = classify_reply(body)
    assert verdict.accepted is False and verdict.code == code, repr(body)


def test_punctuation_rule_is_exactly_one_optional_full_stop() -> None:
    assert classify_reply("BAJA.").accepted and classify_reply("baja.").accepted
    for mark in "!?,;:…-_'\"*":
        assert not classify_reply(f"BAJA{mark}").accepted, mark
    assert not classify_reply(".BAJA").accepted
    assert classify_reply("BAJA. ").accepted  # trailing whitespace is trimmed first
    assert classify_reply("REMOVER.").accepted and classify_reply("remover.").accepted
    for mark in "!?,;:…-_'\"*":
        assert not classify_reply(f"REMOVER{mark}").accepted, mark
    assert not classify_reply(".REMOVER").accepted


def test_own_mail_is_matched_by_whole_address_or_the_origenlab_domain_only() -> None:
    facts = ReplyFacts(now=NOW, known_addresses={"ana@gmail.test", "ventas@gmail.test", "x@origenlab.cl"},
                       own_addresses={"ventas@gmail.test"})
    plan = plan_batch([_record(sender="ana@gmail.test"), _record(sender="ventas@gmail.test"),
                       _record(sender="x@origenlab.cl")], facts)
    assert [r["outcome"] for r in plan["rows"]] == ["suppress", "own_mailbox", "own_mailbox"]


def test_the_subject_never_counts() -> None:
    plan = plan_batch([_record(body="Gracias", subject="BAJA")], ReplyFacts(now=NOW, known_addresses={"ana@uni.test"}))
    assert plan["rows"][0]["outcome"] == "not_baja"


# --------------------------------------------------------------------------- the record


@pytest.mark.parametrize(("header", "expected"), [
    ("Ana <Ana@Uni.Test>", "ana@uni.test"),
    ("ana@uni.test", "ana@uni.test"),
    ('"Pérez, Ana" <ana@uni.test>', "ana@uni.test"),
    ("Ana <ana@uni.test>, Bea <bea@uni.test>", None),
    ("Ana", None),
    ("Ana <ana@localhost>", None),
    ("Ana <ana@uni.test>\nBcc: x@y.test", None),
])
def test_the_sender_is_exactly_one_normalized_address(header, expected) -> None:
    assert sender_address(header) == expected


@pytest.mark.parametrize(("change", "problem"), [
    ({"message_id": "no-at-sign"}, "message_id:shape"),
    ({"source": "imap:ventas/INBOX"}, "source:shape"),
    ({"from_header": "Ana <ana@uni.test>, Bea <bea@uni.test>"}, "from_header:not_one_address"),
    ({"received_at": "2026-09-26T15:04:05"}, "received_at:timezone_aware"),
    ({"received_at": "2026-10-30T00:00:00Z"}, "received_at:future"),
    ({"extra": "field"}, "extra:extra_forbidden"),
    ({"body_text": "x" * 20_001}, "body_text:string_too_long"),
])
def test_a_malformed_record_is_named_without_echoing_its_values(change, problem) -> None:
    raw = {**_record(body="BAJA secreto-7781"), **change}
    parsed, problems = parse_record(raw, NOW)
    assert parsed is None and problem in problems
    assert "secreto-7781" not in json.dumps(problems) and "ana@uni.test" not in json.dumps(problems)


def test_a_record_must_be_an_object_and_have_every_required_field() -> None:
    assert parse_record(["BAJA"], NOW) == (None, ["not_an_object"])
    parsed, problems = parse_record({"body_text": "BAJA"}, NOW)
    assert parsed is None and {"message_id:missing", "from_header:missing", "received_at:missing"} <= set(problems)


# --------------------------------------------------------------------------- the plan


def _facts(**kw) -> ReplyFacts:
    return ReplyFacts(now=NOW, **{"known_addresses": {"ana@uni.test", "bea@uni.test", "wave@uni.test"}, **kw})


def test_the_plan_names_one_outcome_per_record_and_applies_only_clear_bajas() -> None:
    repeat_mid = "<repeat@mail.uni.test>"
    records = [
        _record(),                                                    # 0 suppress
        _record(body="baja."),                                        # 1 same address again → evidence
        _record(sender="wave@uni.test"),                              # 2 already marketing-blocked → evidence
        _record(sender="bea@uni.test", mid=repeat_mid),               # 3 already recorded
        _record(sender="nadie@desconocido.test"),                     # 4 unknown sender, no lineage
        _record(sender="ventas@origenlab.cl"),                        # 5 our own mailbox
        _record(body="Dar de baja por favor"),                        # 6 not a clear BAJA
    ]
    facts = _facts(marketing_blocked={"wave@uni.test": "suppression list"},
                   recorded_keys={f"gmail_unsubscribe:{parse_record(records[3], NOW)[0].message_id_sha256}"})
    plan = plan_batch(records, facts)
    assert [r["outcome"] for r in plan["rows"]] == [
        "suppress", "evidence_only", "evidence_only", "already_recorded", "pending_review", "own_mailbox", "not_baja"]
    assert plan["applicable"] == 4 and plan["blocked"] is False
    assert [r["basis"] for r in plan["rows"]] == [
        "known_address", "known_address", "known_address", None, "pending_review", None, None]
    assert plan["rows"][4]["review_reason"] == "lineage_missing"
    assert plan["policy_version"] == UNSUBSCRIBE_POLICY_VERSION
    # The plan fingerprint moves with any outcome, not only with the records.
    other = plan_batch(records, _facts(recorded_keys=facts.recorded_keys))
    assert other["input_sha256"] == plan["input_sha256"] and other["plan_sha256"] != plan["plan_sha256"]
    assert plan["rows"][2]["existing_block_reason"] == "suppression list"
    assert plan["rows"][6]["verdict"]["code"] == "baja_not_standalone"
    public = json.dumps({k: v for k, v in plan.items() if not k.startswith("_")}, ensure_ascii=False)
    assert "Dar de baja" not in public and "body_text" not in public and "Re: Sonicadores" not in public


OUTBOUND = "<campana-7@mail.origenlab.test>"


def test_an_unknown_sender_proven_by_outbound_lineage_is_suppressed_and_anything_less_is_held() -> None:
    facts = _facts(outbound_recipients={"campana-7@mail.origenlab.test": {"zoe@lab.test", "cc@lab.test"}})
    records = [
        _record(sender="Zoe <Zoe@Lab.Test>", in_reply_to=OUTBOUND),            # 0 exact recipient
        _record(sender="cc@lab.test", in_reply_to=" " + OUTBOUND.upper()),     # 1 a cc recipient, id normalized
        _record(sender="otra@lab.test", in_reply_to=OUTBOUND),                 # 2 replied, but was not sent it
        _record(sender="zoe@lab.test.evil", in_reply_to=OUTBOUND),             # 3 lookalike: not equal
        _record(sender="nadie@lab.test", in_reply_to="<nunca-enviado@x.test>"),  # 4 names no recorded message
        _record(sender="nadie2@lab.test"),                                     # 5 no In-Reply-To at all
        _record(sender="zoe@lab.test", in_reply_to="sin-arroba"),              # 6 malformed id: no lineage
        _record(sender="Zoe <zoe@lab.test>", body="REMOVER", in_reply_to=OUTBOUND),  # 7 same address again
    ]
    plan = plan_batch(records, facts)
    rows = plan["rows"]
    assert [r["outcome"] for r in rows] == [
        "suppress", "suppress", "pending_review", "pending_review", "pending_review", "pending_review",
        "pending_review", "evidence_only"]
    assert [r["basis"] for r in rows[:2]] == ["outbound_lineage", "outbound_lineage"]
    assert [r["review_reason"] for r in rows[2:7]] == [
        "recipient_mismatch", "recipient_mismatch", "lineage_missing", "lineage_missing", "lineage_missing"]
    assert rows[7]["verdict"]["code"] == "standalone_remover" and rows[7]["basis"] == "outbound_lineage"
    assert plan["counts"]["pending_review"] == 5 and plan["applicable"] == 8
    # A pending review is part of what the operator confirms: the plan fingerprint moves with it.
    known = plan_batch(records, _facts(known_addresses={"otra@lab.test"},
                                       outbound_recipients=facts.outbound_recipients))
    assert known["rows"][2]["outcome"] == "suppress" and known["plan_sha256"] != plan["plan_sha256"]


def test_a_message_held_for_review_is_already_pending_and_a_known_sender_needs_no_lineage() -> None:
    held = _record(sender="nadie@lab.test", mid="<held@x.test>")
    key = f"gmail_unsubscribe:{parse_record(held, NOW)[0].message_id_sha256}"
    plan = plan_batch([held, _record(sender="ana@uni.test", in_reply_to="<otro@x.test>")],
                      _facts(recorded_keys={key}, pending_keys={key}))
    assert [r["outcome"] for r in plan["rows"]] == ["already_pending", "suppress"]
    assert plan["rows"][1]["basis"] == "known_address"


def test_the_evidence_carries_the_basis_the_versions_and_the_reason() -> None:
    parsed, _ = parse_record(_record(sender="nadie@lab.test", in_reply_to=OUTBOUND), NOW)
    assert parsed.in_reply_to_norm == "campana-7@mail.origenlab.test"
    ev = parsed.evidence("a" * 64, basis="pending_review", review_reason="lineage_missing")
    assert ev["basis"] == "pending_review" and ev["review_reason"] == "lineage_missing"
    assert ev["policy_version"] == ev["payload"]["policy_version"] == UNSUBSCRIBE_POLICY_VERSION
    assert ev["grammar_version"] == ev["payload"]["grammar_version"] == BAJA_GRAMMAR_VERSION
    assert ev["payload"]["in_reply_to"] == OUTBOUND
    assert "review_reason" not in parsed.evidence("a" * 64)


def test_one_malformed_record_blocks_the_whole_batch() -> None:
    plan = plan_batch([_record(), {**_record(), "received_at": "ayer"}], _facts())
    assert plan["blocked"] is True and plan["counts"]["malformed"] == 1
    assert plan["rows"][1]["outcome"] == "malformed"


def test_a_message_twice_in_one_batch_is_malformed() -> None:
    r = _record(mid="<same@mail.uni.test>")
    plan = plan_batch([r, dict(r)], _facts())
    assert plan["blocked"] is True and plan["rows"][1]["problems"] == ["message_id:duplicate_in_batch"]


def test_the_fingerprint_covers_every_record_their_order_and_the_grammar(monkeypatch) -> None:
    a, b = _record(mid="<a@x.test>"), _record(mid="<b@x.test>")
    assert input_sha256([a, b]) == input_sha256([json.loads(json.dumps(a)), json.loads(json.dumps(b))])
    assert input_sha256([a, b]) != input_sha256([b, a])
    assert input_sha256([a]) != input_sha256([{**a, "body_text": "BAJA."}])
    before = input_sha256([a])
    monkeypatch.setattr("origenlab_api.v2.unsubscribe_replies.BAJA_GRAMMAR_VERSION", "baja-reply/2099-01-01.v9")
    assert input_sha256([a]) != before


def test_the_evidence_keeps_the_original_text_and_its_hash() -> None:
    body = "BAJA\n\nEl lun, 21 sept 2026 a las 10:00, OrigenLab <ventas@origenlab.cl> escribió:\n> Responda BAJA"
    parsed, _ = parse_record(_record(body=body, mid="<Evidence@Mail.Uni.Test>"), NOW)
    ev = parsed.evidence("f" * 64)
    assert ev["payload"]["body_text"] == body and ev["payload"]["from_address"] == "ana@uni.test"
    import hashlib

    assert ev["payload"]["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert ev["payload"]["message_id_norm"] == "evidence@mail.uni.test"
    assert ev["message_id_sha256"] == hashlib.sha256(b"evidence@mail.uni.test").hexdigest()
    assert ev["grammar_version"] == BAJA_GRAMMAR_VERSION and ev["input_sha256"] == "f" * 64


# --------------------------------------------------------------------------- enforcement (pure)

T = datetime(2026, 3, 10, tzinfo=timezone.utc)
CAMPAIGN = {"id": "c-1", "status": "draft", "version": 3, "name": "Sonicadores", "subject": "Sonicadores Hielscher",
            "preheader": "UP200St", "body_html": "<p>Hola</p>", "max_sends": 50}


def _world(**facts_kw) -> AudienceInputs:
    cases = {"k1": CaseFacts(opportunity_id="k1", title="Caso k1", stage="won", created_at=T, organization_id="uni",
                             organization_name="Instituto uni", first_sent_at=T)}
    recipients = "ana@uni.test, prior@uni.test, baja@uni.test"
    ev = [{"source_record_id": "sr-1", "opportunity_id": "k1", "subject": "Cotización UP200St", "filenames": [],
           "sent_at": "2026-03-12T10:00:00+00:00", "recipients": recipients}]
    cps = {a: {"id": f"cp-{a}", "address": a, "usage": "personal", "person_id": None, "organization_id": "uni",
               "person_name": None} for a in ("ana@uni.test", "prior@uni.test", "baja@uni.test")}
    return AudienceInputs(cases=cases, interests=[], quotation_evidence=ev,
                          eligibility=EligibilityFacts(contact_points=cps, **facts_kw))


def test_an_unsubscribe_excludes_even_the_most_relevant_address() -> None:
    facts = EligibilityFacts(unsubscribed_addresses={"baja@uni.test"})
    out = eligibility("baja@uni.test", facts, "uni")
    assert out["eligible"] is False and [r["code"] for r in out["reasons"]] == ["unsubscribed"]
    assert out["reasons"][0]["label"].startswith("Solicitó la BAJA")


def test_the_freeze_records_an_unsubscribe_as_a_block_with_its_note_and_w12_cannot_lift_it() -> None:
    taxonomy = load_taxonomy()
    world = _world(unsubscribed_addresses={"prior@uni.test"}, prior_contact_addresses={"prior@uni.test"})
    base = plan_freeze(taxonomy, world, CAMPAIGN, FreezeCriteria(brand_id="hielscher"), recontact_review=True)
    rows = {r["address"]: r for r in base["rows"]}
    prior = rows["prior@uni.test"]
    assert prior["inclusion"] == "excluded" and set(prior["frozen_reasons"]) == {"block", "prior_contact"}
    assert "unsubscribed" in prior["frozen_notes"] and prior["recontact_review_required"] is False
    assert rows["ana@uni.test"]["inclusion"] == "included"
    key = prior["key"]
    lifted = plan_freeze(taxonomy, world, CAMPAIGN, FreezeCriteria(brand_id="hielscher"), recontact_review=True,
                         recontact_decisions={key: RecontactDecision(key=key, decision="approve", note="insistir", mode="individual")})
    assert "recontact_not_eligible" in [p["code"] for p in lifted["problems"]]
    assert {r["address"]: r for r in lifted["rows"]}["prior@uni.test"]["inclusion"] == "excluded"


def test_a_baja_held_for_review_blocks_its_exact_address_in_preview_and_freeze() -> None:
    facts = EligibilityFacts(unsubscribe_pending_addresses={"baja@uni.test"})
    out = eligibility("baja@uni.test", facts, "uni")
    assert out["eligible"] is False and [r["code"] for r in out["reasons"]] == ["unsubscribe_pending_review"]
    # Exact address only: a neighbour on the same domain is untouched.
    assert eligibility("ana@uni.test", facts, "uni")["eligible"] is True
    world = _world(unsubscribe_pending_addresses={"baja@uni.test"})
    rows = {r["address"]: r for r in
            plan_freeze(load_taxonomy(), world, CAMPAIGN, FreezeCriteria(brand_id="hielscher"))["rows"]}
    assert rows["baja@uni.test"]["inclusion"] == "excluded" and rows["baja@uni.test"]["frozen_reasons"] == ["block"]
    assert "unsubscribe_pending_review" in rows["baja@uni.test"]["frozen_notes"]
    assert rows["ana@uni.test"]["inclusion"] == "included"


# --------------------------------------------------------------------------- routes


class _Port:
    def __init__(self, role: str | None, operator_id: str = "00000000-0000-4000-8000-000000000001"):
        self._role, self._id = role, operator_id

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._role is None:
            raise IdentityRefused("no identity")
        return OperatorIdentity(operator_id=self._id, email_norm="operator@example.invalid",
                                display_name="Operator", role=self._role, status="active")


class _FakeRepo:
    def __init__(self, fail: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def preview(self, records):
        if self.fail:
            raise self.fail
        self.calls.append({"preview": records})
        return {"input_sha256": "0" * 64}

    def execute(self, **kw):
        if self.fail:
            raise self.fail
        self.calls.append(kw)
        return {"replayed": False}


def _client(repo, role="sales", *, apply=True) -> TestClient:
    from origenlab_api.v2.unsubscribe_routes import unsubscribe_apply_router, unsubscribe_preview_router

    app = FastAPI()
    app.state.v2_identity = _Port(role)
    app.state.unsubscribe_repository = repo
    app.include_router(unsubscribe_preview_router)
    if apply:
        app.include_router(unsubscribe_apply_router)
    return TestClient(app)


PREVIEW, APPLY = "/v2/unsubscribe/preview", "/v2/commands/apply-unsubscribe-replies"
RESOLVE = "/v2/commands/resolve-unsubscribe-review"
DISMISS = "/v2/commands/dismiss-unsubscribe-review"
DISMISS_BODY = {"assertion_id": "00000000-0000-4000-8000-00000000abcd", "expected_address": "nadie@lab.test",
                "expected_review_sha256": "c" * 64, "explanation": "Falso positivo: respondió «BAJA» a otra cosa"}
RESOLVE_BODY = {"assertion_id": "00000000-0000-4000-8000-00000000abcd", "expected_address": "nadie@lab.test",
                "note": "Revisado: el remitente pidió la baja"}
APPLY_BODY = {"records": [_record()], "expected_input_sha256": "a" * 64, "expected_plan_sha256": "b" * 64}


@pytest.mark.parametrize(("role", "status"), [(None, 401), ("viewer", 403)])
def test_only_sales_and_admin_reach_preview_or_apply(role, status) -> None:
    repo = _FakeRepo()
    client = _client(repo, role)
    assert client.post(PREVIEW, json={"records": [_record()]}).status_code == status
    assert client.post(APPLY, json=APPLY_BODY, headers={"Idempotency-Key": "k-1"}).status_code == status
    assert client.post(RESOLVE, json=RESOLVE_BODY, headers={"Idempotency-Key": "k-2"}).status_code == status
    assert client.post(DISMISS, json=DISMISS_BODY, headers={"Idempotency-Key": "k-3"}).status_code == status
    assert repo.calls == []


def test_only_an_admin_reaches_the_dismissal() -> None:
    repo = _FakeRepo()
    r = _client(repo, "sales").post(DISMISS, json=DISMISS_BODY, headers={"Idempotency-Key": "k-1"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "role_may_not_dismiss" and repo.calls == []
    r = _client(repo, "admin").post(DISMISS, json=DISMISS_BODY, headers={"Idempotency-Key": "k-1"})
    assert r.status_code == 200 and repo.calls[-1]["command_name"] == DISMISS_UNSUBSCRIBE_REVIEW
    assert repo.calls[-1]["idempotency_key"] == "k-1"


def test_dismiss_needs_a_key_the_review_version_the_address_and_an_explanation() -> None:
    repo = _FakeRepo()
    client = _client(repo, "admin")
    assert client.post(DISMISS, json=DISMISS_BODY).status_code == 400
    for bad in ({**DISMISS_BODY, "assertion_id": "x"}, {**DISMISS_BODY, "explanation": ""},
                {**DISMISS_BODY, "explanation": "   \n\t "}, {**DISMISS_BODY, "explanation": "x" * 1001},
                {**DISMISS_BODY, "expected_review_sha256": "abc"},
                {k: v for k, v in DISMISS_BODY.items() if k != "expected_review_sha256"},
                {k: v for k, v in DISMISS_BODY.items() if k != "expected_address"},
                {k: v for k, v in DISMISS_BODY.items() if k != "explanation"},
                {**DISMISS_BODY, "contact_control_id": "00000000-0000-4000-8000-00000000abcd"}):
        assert client.post(DISMISS, json=bad, headers={"Idempotency-Key": "k-1"}).status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_preview_and_apply(role) -> None:
    repo = _FakeRepo()
    client = _client(repo, role)
    assert client.post(PREVIEW, json={"records": [_record()]}).status_code == 200
    r = client.post(APPLY, json=APPLY_BODY, headers={"Idempotency-Key": "k-1"})
    assert r.status_code == 200 and repo.calls[-1]["command_name"] == APPLY_UNSUBSCRIBE_REPLIES
    r = client.post(RESOLVE, json=RESOLVE_BODY, headers={"Idempotency-Key": "k-2"})
    assert r.status_code == 200 and repo.calls[-1]["command_name"] == RESOLVE_UNSUBSCRIBE_REVIEW


def test_resolve_needs_a_key_a_request_id_the_address_and_a_note() -> None:
    repo = _FakeRepo()
    client = _client(repo)
    assert client.post(RESOLVE, json=RESOLVE_BODY).status_code == 400
    for bad in ({**RESOLVE_BODY, "assertion_id": "x"}, {**RESOLVE_BODY, "note": ""},
                {**RESOLVE_BODY, "note": "x" * 501}, {k: v for k, v in RESOLVE_BODY.items() if k != "expected_address"},
                {**RESOLVE_BODY, "decision": "dismiss"}):
        assert client.post(RESOLVE, json=bad, headers={"Idempotency-Key": "k-1"}).status_code == 422
    assert repo.calls == []


def test_apply_needs_a_key_and_a_well_formed_expected_hash() -> None:
    repo = _FakeRepo()
    client = _client(repo)
    assert client.post(APPLY, json=APPLY_BODY).status_code == 400
    for bad in ({"records": [_record()]}, {**APPLY_BODY, "expected_input_sha256": "abc"},
                {k: v for k, v in APPLY_BODY.items() if k != "expected_plan_sha256"},
                {**APPLY_BODY, "records": []}, {**APPLY_BODY, "records": [_record()] * 501},
                {**APPLY_BODY, "operator_id": "x"}):
        assert client.post(APPLY, json=bad, headers={"Idempotency-Key": "k-1"}).status_code == 422
    assert repo.calls == []


def test_a_database_failure_fails_closed_without_detail() -> None:
    import psycopg

    for route, kw in ((PREVIEW, {"json": {"records": [_record()]}}),
                      (APPLY, {"json": APPLY_BODY, "headers": {"Idempotency-Key": "k-1"}})):
        r = _client(_FakeRepo(fail=psycopg.OperationalError("connection refused: host secret-db"))).post(route, **kw)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "database_error"
        assert "secret-db" not in r.text


def test_the_apply_route_exists_only_behind_its_switch_and_preview_always() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    loopback = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"

    def paths(**kw):
        app = FastAPI()
        main._mount_unsubscribe(app, Settings(_env_file=None, **kw), "postgresql://x@127.0.0.1/db", _never_connects)
        return app.state.unsubscribe_apply_enabled, set(app.openapi()["paths"])

    for kw in ({"v2_database_url": loopback}, {"v2_database_url": loopback, "v2_commands_enabled": True},
               {"v2_database_url": loopback, "v2_audience_freeze_enabled": True},
               {"v2_unsubscribe_apply_enabled": True}):
        enabled, routes = paths(**kw)
        assert enabled is False and not {APPLY, RESOLVE, DISMISS} & routes and PREVIEW in routes
    enabled, routes = paths(v2_database_url=loopback, v2_unsubscribe_apply_enabled=True)
    assert enabled is True and {APPLY, RESOLVE, DISMISS, PREVIEW} <= routes
    assert Settings(_env_file=None).v2_unsubscribe_apply_enabled is False


def test_the_w10_routers_expose_exactly_four_posts() -> None:
    from origenlab_api.v2.unsubscribe_routes import unsubscribe_apply_router, unsubscribe_preview_router

    assert [(r.path, set(r.methods)) for r in unsubscribe_preview_router.routes] == [(PREVIEW, {"POST"})]
    assert [(r.path, set(r.methods)) for r in unsubscribe_apply_router.routes] == [
        (APPLY, {"POST"}), (RESOLVE, {"POST"}), (DISMISS, {"POST"})]


# --------------------------------------------------------------------------- no Gmail, no network, no send

FORBIDDEN_IMPORTS = {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "smtplib", "imaplib", "poplib",
                     "googleapiclient", "google", "google_auth_oauthlib", "email.mime", "subprocess"}


@pytest.mark.parametrize("module", W10_MODULES)
def test_the_w10_modules_import_nothing_that_could_reach_a_mailbox_or_the_network(module) -> None:
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN_IMPORTS), (module, name)
    text = (SRC / module).read_text(encoding="utf-8")
    for token in ("users().messages()", "users().threads()", "modifyLabels", "batchModify", ".send(", "sendmail",
                  "send_one(", "reserve_attempts(", "begin_dispatch("):
        assert token not in text, (module, token)


def test_planning_a_batch_opens_no_socket(monkeypatch) -> None:
    def refuse(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("a network connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    plan = plan_batch([_record(), _record(body="Gracias")], _facts())
    assert plan["sends_email"] is False and plan["reads_mailbox"] is False


def test_no_send_or_gmail_route_exists_in_the_api() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    app = FastAPI()
    main._mount_unsubscribe(app, Settings(_env_file=None, v2_database_url="postgresql://x@127.0.0.1/db",
                                          v2_unsubscribe_apply_enabled=True), "postgresql://x@127.0.0.1/db", _never_connects)
    for path in app.openapi()["paths"]:
        assert not any(w in path for w in ("send", "gmail", "sync", "label", "mailbox", "resubscribe")), path


# --------------------------------------------------------------------------- database (opt-in)


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def world(disposable_database):
    import psycopg

    tag = uuid.uuid4().hex[:8]
    s: dict[str, Any] = {"tag": tag}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        for key, role in (("op", "sales"), ("viewer", "viewer"), ("admin", "admin")):
            cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                        "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                        (f"w10-{key}-{tag}@example.test", f"W10 {key}", role))
            s[key] = cur.fetchone()[0]
        cur.execute("insert into comms.mailbox (address_norm) values (%s) returning id::text", (f"ventas-{tag}@example.invalid",))
        mailbox = cur.fetchone()[0]
        # A recorded outbound message (a historical campaign mail): sent to hist-… and cc-…, never
        # to anyone else. Neither address is a person, a contact point or a control here.
        s["outbound_mid"] = f"<campana-{tag}@mail.origenlab.test>"
        cur.execute("insert into comms.message (mailbox_id, provider_message_id, rfc822_message_id_norm, direction, internal_date) "
                    "values (%s, %s, %s, 'outbound', '2026-03-01T10:00:00+00:00') returning id::text",
                    (mailbox, f"gm-{tag}", f"campana-{tag}@mail.origenlab.test"))
        s["outbound_message"] = cur.fetchone()[0]
        for role, who in (("from", "ventas"), ("to", "hist"), ("cc", "cc")):
            domain = "example.invalid" if who == "ventas" else "lab.test"
            cur.execute("insert into comms.message_participant (message_id, role, address_norm) values (%s, %s, %s)",
                        (s["outbound_message"], role, f"{who}-{tag}@{domain}"))
        cur.execute("insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
                    "returning id::text", (f"Universidad Ficticia {tag}",))
        s["uni"] = cur.fetchone()[0]
        payload = {"subject_raw": "Cotización 01024-26 sonicador", "documents": [{"filename": "COT UP200St.pdf"}],
                   "recipients": f"ana-{tag}@uni.test, prior-{tag}@uni.test, late-{tag}@uni.test",
                   "sent_at": "2026-03-12T10:00:00+00:00"}
        cur.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('gmail_message', %s, %s) "
                    "returning id::text", (f"w10:{tag}", json.dumps(payload)))
        sr = cur.fetchone()[0]
        with conn.transaction():
            cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                        "values ('Cotización 01024-26', 'lead', %s, %s) returning id::text", (s["op"], s["uni"]))
            case = cur.fetchone()[0]
            cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                        "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                        "current_date, 'confirmed', %s)", (case, s["uni"], s["op"]))
        cur.execute("insert into crm.quote (opportunity_id, quote_number, number_origin) "
                    "values (%s, '01024-26', 'printed_historical') returning id::text", (case,))
        quote = cur.fetchone()[0]
        cur.execute("insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at, "
                    "origin_source_record_id) values (%s, 1, 'sent', 'historical_import', %s, "
                    "'2026-03-12T10:00:00+00:00', %s)", (quote, "a" * 64, sr))
        for who in ("ana", "prior", "late"):
            cur.execute("insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                        "values ('email', %s, %s, 'unattributed', 'machine_proposed')",
                        (f"{who}-{tag}@uni.test", f"{who}-{tag}@uni.test"))
        cur.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values "
                    "('address', %s, 'prior_contact', 'marketing', 'contacto histórico', 'wave1a_union'), "
                    "('address', %s, 'block', 'marketing', 'suppression list', 'wave1a_suppression'), "
                    "('address', %s, 'prior_contact', 'marketing', 'contacto histórico', 'wave1a_union')",
                    (f"prior-{tag}@uni.test", f"wave-{tag}@uni.test", f"wave-{tag}@uni.test"))
    s["operator"] = OperatorIdentity(operator_id=s["op"], email_norm=f"w10-op-{tag}@example.test",
                                     display_name="W10 op", role="sales", status="active")
    s["admin_identity"] = OperatorIdentity(operator_id=s["admin"], email_norm=f"w10-admin-{tag}@example.test",
                                           display_name="W10 admin", role="admin", status="active")
    s["viewer_identity"] = OperatorIdentity(operator_id=s["viewer"], email_norm=f"w10-viewer-{tag}@example.test",
                                            display_name="W10 viewer", role="viewer", status="active")
    return s


def _repo(dsn):
    import psycopg

    from origenlab_api.v2.unsubscribe_commands import V2UnsubscribeRepository

    return V2UnsubscribeRepository(psycopg.connect, runtime_dsn(dsn),
                                   clock=lambda: datetime.now(timezone.utc) + timedelta(seconds=1))


def _apply(dsn, operator, records, *, expected=None, plan=None, key=None):
    from origenlab_api.v2.commands import request_digest
    from origenlab_api.v2.unsubscribe_commands import ApplyUnsubscribeBody

    if plan is None:  # what an operator would do: preview, then confirm exactly that plan
        plan = _repo(dsn).preview(records).get("plan_sha256") or "0" * 64
    body = ApplyUnsubscribeBody(records=records, expected_input_sha256=expected or input_sha256(records),
                                expected_plan_sha256=plan)
    return _repo(dsn).execute(command_name=APPLY_UNSUBSCRIBE_REPLIES, operator=operator,
                              fields=body.model_dump(mode="json"), idempotency_key=key or uuid.uuid4().hex,
                              digest=request_digest(APPLY_UNSUBSCRIBE_REPLIES, body))


TABLES = ("outbound.contact_control", "evidence.source_record", "evidence.assertion", "crm.domain_event",
          "platform.command_receipt", "outbound.send_attempt", "outbound.campaign_recipient", "comms.message",
          "crm.person", "crm.contact_point", "crm.organization")


def _counts(dsn) -> dict[str, int]:
    import psycopg

    with psycopg.connect(runtime_dsn(dsn), autocommit=True) as conn:
        return {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in TABLES}  # noqa: S608


def _rec(w, who, body="BAJA", mid=None, received=None):
    return _record(body=body, sender=f"{who.title()} <{who}-{w['tag']}@uni.test>", mid=mid,
                   received_at=received or "2026-09-26T15:04:05-03:00")


@needs_db
def test_preview_writes_nothing_and_apply_records_a_permanent_suppression(disposable_database, world) -> None:
    import psycopg

    tag = world["tag"]
    records = [
        _rec(world, "ana", mid=f"<ana-1-{tag}@mail.uni.test>"),
        _rec(world, "ana", body="baja.", mid=f"<ana-2-{tag}@mail.uni.test>"),
        _rec(world, "wave", mid=f"<wave-{tag}@mail.uni.test>"),
        _rec(world, "nadie", mid=f"<nadie-{tag}@mail.uni.test>"),
        _rec(world, "ana", body="Por favor dar de baja", mid=f"<ana-3-{tag}@mail.uni.test>"),
        _record(sender=f"ventas-{tag}@example.invalid", mid=f"<own-{tag}@mail.test>"),
    ]
    before = _counts(disposable_database)
    preview = _repo(disposable_database).preview(records)
    assert _counts(disposable_database) == before
    assert [r["outcome"] for r in preview["rows"]] == [
        "suppress", "evidence_only", "evidence_only", "pending_review", "not_baja", "own_mailbox"]
    assert "_parsed" not in preview and "Por favor dar de baja" not in json.dumps(preview, ensure_ascii=False)

    out = _apply(disposable_database, world["operator"], records, expected=preview["input_sha256"],
                 plan=preview["plan_sha256"])
    assert out["applied"] == {"added": 1, "evidence_linked": 2, "pending_review": 1, "already_recorded": 0,
                              "already_pending": 0} and out["not_applied"] == 2
    after = _counts(disposable_database)
    assert after["outbound.contact_control"] == before["outbound.contact_control"] + 1
    assert after["evidence.source_record"] == before["evidence.source_record"] + 4
    assert after["evidence.assertion"] == before["evidence.assertion"] + 4
    assert after["crm.domain_event"] == before["crm.domain_event"] + 4
    for t in ("outbound.send_attempt", "outbound.campaign_recipient", "comms.message",
              "crm.person", "crm.contact_point", "crm.organization"):
        assert after[t] == before[t], t

    with psycopg.connect(runtime_dsn(disposable_database), autocommit=True) as conn:
        c = conn.execute("select kind, purpose, reason, source, created_by_operator_id::text, origin_source_record_id is not null "
                         "from outbound.contact_control where value_norm = %s and kind = 'block'", (f"ana-{tag}@uni.test",)).fetchone()
        assert c == ("block", "marketing", "unsubscribe", "unsubscribe_handler", world["op"], True)
        src = conn.execute("select payload, payload_sha256, review_status from evidence.source_record s "
                           "where dedupe_key like 'gmail_unsubscribe:%%' and payload->>'from_address' = %s "
                           "and payload->>'body_text' = 'baja.'", (f"ana-{tag}@uni.test",)).fetchone()
        assert src[0]["grammar_version"] == BAJA_GRAMMAR_VERSION and src[2] == "promoted"
        assert src[0]["input_sha256"] == preview["input_sha256"] and len(src[1]) == 64
        events = conn.execute("select e.event_type, e.payload->>'reason', e.command_receipt_id::text, e.actor_operator_id::text "
                              "from crm.domain_event e where e.aggregate_kind = 'contact_control' "
                              "and e.command_receipt_id = %s order by e.stream_position", (out["command_receipt_id"],)).fetchall()
        assert [e[0] for e in events] == ["contact_control.added", "contact_control.evidence_linked",
                                          "contact_control.evidence_linked"]
        assert events[2][1] == "suppression list"  # linked to the existing wave block, which keeps its reason
        assert {e[3] for e in events} == {world["op"]}
        linked = conn.execute("select resolution from evidence.assertion where kind = 'unsubscribe_request' and value_norm = %s",
                              (f"wave-{tag}@uni.test",)).fetchone()[0]
        assert linked == "linked"
        receipt = conn.execute("select status from platform.command_receipt where id = %s", (out["command_receipt_id"],)).fetchone()
        assert receipt == ("completed",)

    # The api login cannot weaken it afterwards; nobody below superuser can.
    with psycopg.connect(runtime_dsn(disposable_database), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from outbound.contact_control where value_norm = %s", (f"ana-{tag}@uni.test",))
    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        for sql in ("delete from outbound.contact_control where value_norm = %s and kind = 'block'",
                    "update outbound.contact_control set purpose = 'all' where value_norm = %s and kind = 'block'"):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, (f"wave-{tag}@uni.test",))


@needs_db
def test_apply_is_idempotent_and_a_changed_or_malformed_batch_writes_nothing(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.commands import CommandRefused

    tag = world["tag"]
    records = [_rec(world, "prior", mid=f"<prior-{tag}@mail.uni.test>")]
    key = uuid.uuid4().hex
    shown = _repo(disposable_database).preview(records)["plan_sha256"]
    first = _apply(disposable_database, world["operator"], records, plan=shown, key=key)
    assert first["applied"]["added"] == 1
    before = _counts(disposable_database)
    # A replay resends the identical request; the stored answer comes back, nothing runs.
    replay = _apply(disposable_database, world["operator"], records, plan=shown, key=key)
    assert replay["replayed"] is True and replay["command_receipt_id"] == first["command_receipt_id"]
    again = _apply(disposable_database, world["operator"], records)  # a new key, the same message
    assert again["applied"] == {"added": 0, "evidence_linked": 0, "pending_review": 0, "already_recorded": 0,
                                "already_pending": 0}
    assert again["rows"][0]["planned"] == "already_recorded"
    after = _counts(disposable_database)
    assert {t: after[t] - before[t] for t in TABLES} == {**{t: 0 for t in TABLES}, "platform.command_receipt": 1}

    before = _counts(disposable_database)
    other = [_rec(world, "late", mid=f"<late-x-{tag}@mail.uni.test>")]
    with pytest.raises(CommandRefused) as err:
        _apply(disposable_database, world["operator"], other, expected=input_sha256(records))
    assert err.value.status_code == 409 and err.value.code == "input_hash_mismatch"
    # A sender that becomes known between preview and apply changes the plan: refused.
    stranger = [_rec(world, "stranger", mid=f"<stranger-{tag}@mail.uni.test>")]
    shown = _repo(disposable_database).preview(stranger)
    assert shown["rows"][0]["outcome"] == "pending_review"
    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        conn.execute("insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                     "values ('email', %s, %s, 'unattributed', 'machine_proposed')",
                     (f"stranger-{tag}@uni.test", f"stranger-{tag}@uni.test"))
    before = _counts(disposable_database)
    with pytest.raises(CommandRefused) as err:
        _apply(disposable_database, world["operator"], stranger, plan=shown["plan_sha256"])
    assert err.value.status_code == 409 and err.value.code == "plan_changed"
    bad = [_rec(world, "late", mid=f"<late-y-{tag}@mail.uni.test>"), {**_rec(world, "late"), "received_at": "ayer"}]
    with pytest.raises(CommandRefused) as err:
        _apply(disposable_database, world["operator"], bad)
    assert err.value.status_code == 422 and err.value.code == "malformed_records"
    assert _counts(disposable_database) == before


@needs_db
def test_unknown_or_unauthorised_operators_and_database_refusals_write_nothing(disposable_database, world) -> None:
    import psycopg

    tag = world["tag"]
    records = [_rec(world, "late", mid=f"<late-op-{tag}@mail.uni.test>")]
    before = _counts(disposable_database)
    # A viewer that slipped past the route is refused by the privileged function itself.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _apply(disposable_database, world["viewer_identity"], records)
    # An operator the database does not know cannot even claim a receipt.
    ghost = OperatorIdentity(operator_id=str(uuid.uuid4()), email_norm="ghost@example.test", display_name="Ghost",
                             role="sales", status="active")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _apply(disposable_database, ghost, records)
    # Called directly by the api login: without an open receipt of this command; and, with one,
    # for a forged address, evidence whose body does not match its hash, a future date or any
    # operation other than the marketing unsubscribe — every call refuses and nothing stays.
    parsed, _ = parse_record(records[0], datetime.now(timezone.utc))
    ev = parsed.evidence("e" * 64)
    call = "select outbound.add_contact_control(%s, %s, %s, %s, %s, %s, %s::jsonb)"
    with psycopg.connect(runtime_dsn(disposable_database), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(call, ("block", "marketing", parsed.address, "unsubscribe", world["op"], str(uuid.uuid4()), json.dumps(ev)))
        receipt = conn.execute("insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status) "
                               "values (%s, %s, %s, %s, 'in_progress') returning id::text",
                               (world["op"], uuid.uuid4().hex, APPLY_UNSUBSCRIBE_REPLIES, "0" * 64)).fetchone()[0]
        future = {**ev, "observed_at": "2099-01-01T00:00:00+00:00"}
        for args in (("block", "marketing", f"otro-{tag}@uni.test", "unsubscribe", ev),
                     ("block", "marketing", parsed.address, "unsubscribe", {**ev, "payload": {**ev["payload"], "body_text": "BAJA."}}),
                     ("block", "marketing", parsed.address, "unsubscribe", future),
                     ("block", "marketing", parsed.address.upper(), "unsubscribe", ev),
                     ("block", "all", parsed.address, "hard bounce", ev),
                     ("prior_contact", "marketing", parsed.address, "unsubscribe", ev)):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                conn.execute(call, (*args[:4], world["op"], receipt, json.dumps(args[4])))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # another operator's receipt
            conn.execute(call, ("block", "marketing", parsed.address, "unsubscribe", world["viewer"], receipt, json.dumps(ev)))
    after = _counts(disposable_database)
    assert {t: after[t] - before[t] for t in TABLES} == {**{t: 0 for t in TABLES}, "platform.command_receipt": 1}


@needs_db
def test_suppression_wins_over_w12_the_freeze_and_a_snapshot_that_predates_it(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.audience_freeze import (
        FREEZE_CAMPAIGN_AUDIENCE,
        FreezeAudienceBody,
        ReviewDecision,
        V2AudienceFreezeRepository,
        freeze_fields,
    )
    from origenlab_api.v2.campaign_drafts import (
        CREATE_CAMPAIGN_DRAFT,
        CreateCampaignDraftBody,
        V2CampaignDraftRepository,
        validated_draft,
    )
    from origenlab_api.v2.commands import CommandRefused, request_digest
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    tag = world["tag"]
    dsn = runtime_dsn(disposable_database)
    ana, prior, late = (f"{w}-{tag}@uni.test" for w in ("ana", "prior", "late"))
    hielscher = FreezeCriteria(brand_id="hielscher")
    body = CreateCampaignDraftBody(name=f"W10 {tag}", subject="Sonicadores Hielscher", preheader="UP200St",
                                   body_html="<p>Conozca el UP200St. Responda BAJA</p>", max_sends=50,
                                   recontact_interval_days=90)
    cid = V2CampaignDraftRepository(psycopg.connect, dsn).execute(
        command_name=CREATE_CAMPAIGN_DRAFT, operator=world["operator"], fields=validated_draft(CREATE_CAMPAIGN_DRAFT, body),
        idempotency_key=uuid.uuid4().hex, digest=request_digest(CREATE_CAMPAIGN_DRAFT, body))["campaign_id"]
    workspace = CrmWorkspaceRepository(psycopg.connect, dsn)

    # prior-… has already answered BAJA (previous test); ana-… too (first test).
    audience = workspace.marketing_audience_inputs().eligibility
    assert {ana, prior} <= audience.unsubscribed_addresses and late not in audience.unsubscribed_addresses
    preview = workspace.freeze_preview(cid, hielscher, recontact_review=True)
    rows = {r["address"]: r for r in preview["rows"]}
    assert rows[prior]["inclusion"] == "excluded" and "block" in rows[prior]["frozen_reasons"]
    assert "unsubscribed" in rows[prior]["frozen_notes"] and rows[prior]["recontact_review_required"] is False
    assert rows[ana]["inclusion"] == "excluded" and rows[late]["inclusion"] == "included"

    def freeze(recontact=()):
        f = FreezeAudienceBody(campaign_id=cid, expected_version=1, expected_preview_sha256=preview["preview_sha256"],
                               criteria=hielscher, recontact_decisions=list(recontact), confirmed=True,
                               review_decisions=[ReviewDecision(key=k, decision="include", note="revisado")
                                                 for k in preview["review_pending"]])
        return V2AudienceFreezeRepository(psycopg.connect, dsn, recontact_review_enabled=True).execute(
            command_name=FREEZE_CAMPAIGN_AUDIENCE, operator=world["operator"], fields=freeze_fields(f),
            idempotency_key=uuid.uuid4().hex, digest=request_digest(FREEZE_CAMPAIGN_AUDIENCE, f))

    with pytest.raises(CommandRefused) as err:
        freeze([RecontactDecision(key=rows[prior]["key"], decision="approve", note="insistir", mode="individual")])
    assert err.value.code == "recontact_not_eligible"
    freeze()

    # late-… answers BAJA only after the freeze: the snapshot stays, the contract refuses it.
    _apply(disposable_database, world["operator"], [_rec(world, "late", mid=f"<late-after-{tag}@mail.uni.test>")])
    frozen = workspace.frozen_recipients(cid)
    by = {r["address"]: r for r in frozen["recipients"]}
    assert by[late]["inclusion"] == "included" and by[late]["lifecycle_state"] == "snapshotted"
    assert by[late]["suppressed_since_freeze"] is True
    assert [x["code"] for x in by[late]["send_time_refusals"]] == ["unsubscribe"]
    assert "unsubscribed" in by[prior]["frozen_notes"] and by[prior]["suppressed_since_freeze"] is False
    assert frozen["unsubscribed_since_freeze"] == 1
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("select outbound.marketing_contact_refusals(%s)", (by[late]["recipient_id"],)).fetchone()[0] == ["unsubscribe"]

    sup = workspace.suppressions()
    entries = {e["address"]: e for e in sup["entries"]}
    assert {ana, prior, late, f"wave-{tag}@uni.test"} <= set(entries)
    assert entries[ana]["baja_messages"] == 2 and entries[f"wave-{tag}@uni.test"]["reason"] == "suppression list"
    [camp] = [c for c in sup["frozen_campaigns"] if c["campaign_id"] == cid]
    assert camp["unsubscribed_since_freeze"] == 1
    assert "body_text" not in json.dumps(sup) and "BAJA" not in json.dumps(sup)


def _db_rows(dsn, sql, args=()):
    import psycopg

    with psycopg.connect(runtime_dsn(dsn), autocommit=True) as conn:
        return conn.execute(sql, args).fetchall()


def _delta(before, after) -> dict[str, int]:
    return {t: after[t] - before[t] for t in TABLES if after[t] != before[t]}


@needs_db
def test_a_historical_recipient_proven_by_lineage_is_suppressed_without_creating_an_identity(
        disposable_database, world) -> None:
    tag = world["tag"]
    hist = f"hist-{tag}@lab.test"
    records = [_record(body="REMOVER", sender=f"Hist <{hist.upper()}>", mid=f"<hist-1-{tag}@lab.test>",
                       in_reply_to=world["outbound_mid"])]
    preview = _repo(disposable_database).preview(records)
    assert [(r["outcome"], r["basis"], r["verdict"]["code"]) for r in preview["rows"]] == [
        ("suppress", "outbound_lineage", "standalone_remover")]
    before = _counts(disposable_database)
    out = _apply(disposable_database, world["operator"], records, plan=preview["plan_sha256"])
    assert out["applied"]["added"] == 1 and out["rows"][0]["basis"] == "outbound_lineage"
    # The control, its evidence and one event — and no person, contact point or organization.
    assert _delta(before, _counts(disposable_database)) == {
        "outbound.contact_control": 1, "evidence.source_record": 1, "evidence.assertion": 1,
        "crm.domain_event": 1, "platform.command_receipt": 1}
    [(reason, source, value, payload, event)] = _db_rows(disposable_database, """
        select c.reason, c.source, a.value, s.payload, e.payload
          from outbound.contact_control c
          join evidence.assertion a on a.resolved_id = c.id and a.kind = 'unsubscribe_request'
          join evidence.source_record s on s.id = a.source_record_id
          join crm.domain_event e on e.aggregate_kind = 'contact_control' and e.aggregate_id = c.id
         where c.value_norm = %s and c.kind = 'block'""", (hist,))
    assert (reason, source) == ("unsubscribe", "unsubscribe_handler")
    assert value["basis"] == event["basis"] == "outbound_lineage"
    assert value["lineage_message_id"] == event["lineage_message_id"] == world["outbound_message"]
    assert value["grammar_version"] == payload["grammar_version"] == event["grammar_version"] == BAJA_GRAMMAR_VERSION
    assert value["policy_version"] == payload["policy_version"] == event["policy_version"] == UNSUBSCRIBE_POLICY_VERSION
    assert payload["verdict"] == "standalone_remover" and payload["body_text"] == "REMOVER"


def _draft_and_freeze(world, dsn, name):
    """A new Hielscher draft frozen with every review item included — what an operator would do."""
    import psycopg

    from origenlab_api.v2.audience_freeze import (
        FREEZE_CAMPAIGN_AUDIENCE,
        FreezeAudienceBody,
        ReviewDecision,
        V2AudienceFreezeRepository,
        freeze_fields,
    )
    from origenlab_api.v2.campaign_drafts import (
        CREATE_CAMPAIGN_DRAFT,
        CreateCampaignDraftBody,
        V2CampaignDraftRepository,
        validated_draft,
    )
    from origenlab_api.v2.commands import request_digest
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    body = CreateCampaignDraftBody(name=name, subject="Sonicadores Hielscher", preheader="UP200St",
                                   body_html="<p>Conozca el UP200St. Responda BAJA</p>", max_sends=50,
                                   recontact_interval_days=90)
    cid = V2CampaignDraftRepository(psycopg.connect, dsn).execute(
        command_name=CREATE_CAMPAIGN_DRAFT, operator=world["operator"], fields=validated_draft(CREATE_CAMPAIGN_DRAFT, body),
        idempotency_key=uuid.uuid4().hex, digest=request_digest(CREATE_CAMPAIGN_DRAFT, body))["campaign_id"]
    workspace = CrmWorkspaceRepository(psycopg.connect, dsn)
    preview = workspace.freeze_preview(cid, FreezeCriteria(brand_id="hielscher"), recontact_review=True)

    def freeze():
        f = FreezeAudienceBody(campaign_id=cid, expected_version=1, expected_preview_sha256=preview["preview_sha256"],
                               criteria=FreezeCriteria(brand_id="hielscher"), recontact_decisions=[], confirmed=True,
                               review_decisions=[ReviewDecision(key=k, decision="include", note="revisado")
                                                 for k in preview["review_pending"]])
        return V2AudienceFreezeRepository(psycopg.connect, dsn, recontact_review_enabled=True).execute(
            command_name=FREEZE_CAMPAIGN_AUDIENCE, operator=world["operator"], fields=freeze_fields(f),
            idempotency_key=uuid.uuid4().hex, digest=request_digest(FREEZE_CAMPAIGN_AUDIENCE, f))

    return cid, workspace, preview, freeze


@needs_db
def test_unproven_senders_are_held_for_review_and_the_hold_blocks_preview_freeze_and_send(
        disposable_database, world) -> None:
    import psycopg

    tag = world["tag"]
    dsn = runtime_dsn(disposable_database)
    solo, other, lost = f"solo-{tag}@uni.test", f"otra-{tag}@lab.test", f"perdida-{tag}@lab.test"

    # solo-… is in the quotation evidence (an audience candidate) but nowhere else: not a contact
    # point, not a control, not a campaign recipient. (A frozen recipient would be known, and its
    # «BAJA» suppressed outright.)
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        conn.execute("update evidence.source_record set payload = jsonb_set(payload, '{recipients}', "
                     "to_jsonb(payload->>'recipients' || ', ' || %s::text)) where dedupe_key = %s",
                     (f"{solo}, fresh-{tag}@uni.test", f"w10:{tag}"))  # fresh-…: someone left to freeze
    workspace = CrmWorkspaceRepository(psycopg.connect, dsn)
    assert any(solo in r["recipients"] for r in workspace.marketing_audience_inputs().quotation_evidence)
    records = [
        _record(sender=solo, mid=f"<solo-1-{tag}@uni.test>"),                                  # no In-Reply-To
        _record(sender=other, mid=f"<otra-1-{tag}@lab.test>", in_reply_to=world["outbound_mid"]),  # not a recipient
        _record(sender=lost, mid=f"<perdida-1-{tag}@lab.test>", in_reply_to="<nunca@x.test>"),  # unknown message
    ]
    shown = _repo(disposable_database).preview(records)
    assert [(r["outcome"], r["review_reason"]) for r in shown["rows"]] == [
        ("pending_review", "lineage_missing"), ("pending_review", "recipient_mismatch"),
        ("pending_review", "lineage_missing")]
    before = _counts(disposable_database)
    out = _apply(disposable_database, world["operator"], records, plan=shown["plan_sha256"])
    assert out["applied"]["pending_review"] == 3 and out["applied"]["added"] == 0
    # Evidence, an unresolved request and one event each; no control and no identity at all.
    assert _delta(before, _counts(disposable_database)) == {
        "evidence.source_record": 3, "evidence.assertion": 3, "crm.domain_event": 3, "platform.command_receipt": 1}
    held = _db_rows(disposable_database, """
        select a.value_norm, a.resolution, a.value->>'review_reason', a.value->>'grammar_version',
               a.value->>'policy_version', s.review_status, e.event_type
          from evidence.assertion a
          join evidence.source_record s on s.id = a.source_record_id
          join crm.domain_event e on e.aggregate_kind = 'assertion' and e.aggregate_id = a.id
         where a.kind = 'unsubscribe_request' and a.value_norm = any(%s) order by a.value_norm""", ([solo, other, lost],))
    assert held == [
        (other, "unresolved", "recipient_mismatch", BAJA_GRAMMAR_VERSION, UNSUBSCRIBE_POLICY_VERSION, "pending",
         "assertion.unsubscribe_review_opened"),
        (lost, "unresolved", "lineage_missing", BAJA_GRAMMAR_VERSION, UNSUBSCRIBE_POLICY_VERSION, "pending",
         "assertion.unsubscribe_review_opened"),
        (solo, "unresolved", "lineage_missing", BAJA_GRAMMAR_VERSION, UNSUBSCRIBE_POLICY_VERSION, "pending",
         "assertion.unsubscribe_review_opened")]

    # The same message again is the same review item: nothing new but the receipt.
    before = _counts(disposable_database)
    again = _apply(disposable_database, world["operator"], records[:1])
    assert again["rows"][0]["planned"] == "already_pending" and again["applied"]["already_pending"] == 0
    assert _delta(before, _counts(disposable_database)) == {"platform.command_receipt": 1}
    # Even called directly, the function answers the existing item and writes nothing.
    parsed, _ = parse_record(records[0], datetime.now(timezone.utc))
    with psycopg.connect(dsn, autocommit=True) as conn:
        receipt = conn.execute("insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status) "
                               "values (%s, %s, %s, %s, 'in_progress') returning id::text",
                               (world["op"], uuid.uuid4().hex, APPLY_UNSUBSCRIBE_REPLIES, "0" * 64)).fetchone()[0]
        dup = conn.execute("select outbound.add_contact_control('block', 'marketing', %s, 'unsubscribe', %s, %s, %s::jsonb)",
                           (solo, world["op"], receipt,
                            json.dumps(parsed.evidence("e" * 64, basis="pending_review", review_reason="lineage_missing")))
                           ).fetchone()[0]
    assert dup["outcome"] == "already_pending"
    assert _delta(before, _counts(disposable_database)) == {"platform.command_receipt": 2}
    # A second, different message from a held address is its own item; the hold is per address.
    _apply(disposable_database, world["operator"], [_record(sender=solo, mid=f"<solo-2-{tag}@uni.test>")])
    assert _db_rows(disposable_database, "select count(*) from evidence.assertion where kind = 'unsubscribe_request' "
                                         "and resolution = 'unresolved' and value_norm = %s", (solo,)) == [(2,)]

    # The hold: out of the audience preview, excluded by the freeze with its note, and refused by
    # the send-time contract for the row the freeze wrote.
    assert {solo, other, lost} <= workspace.marketing_audience_inputs().eligibility.unsubscribe_pending_addresses
    cid, _, preview, freeze = _draft_and_freeze(world, dsn, f"W10 hold {tag}")
    row = {r["address"]: r for r in preview["rows"]}[solo]
    assert row["inclusion"] == "excluded" and row["frozen_reasons"] == ["block"]
    assert "unsubscribe_pending_review" in row["frozen_notes"]
    freeze()
    frozen = {r["address"]: r for r in workspace.frozen_recipients(cid)["recipients"]}
    assert frozen[solo]["inclusion"] == "excluded" and "unsubscribe_pending_review" in frozen[solo]["frozen_notes"]
    assert [x["code"] for x in frozen[solo]["send_time_refusals"]] == ["not_snapshotted", "unsubscribe_pending_review"]
    sup = workspace.suppressions()
    assert {solo, other, lost} <= {p["address"] for p in sup["pending_reviews"]}
    assert sup["summary"]["pending_reviews"] >= 4
    assert "body_text" not in json.dumps(sup)


def _resolve(dsn, operator, assertion_id, address, note="Revisado: pidió la baja", key=None):
    from origenlab_api.v2.commands import request_digest
    from origenlab_api.v2.unsubscribe_commands import ResolveUnsubscribeReviewBody

    body = ResolveUnsubscribeReviewBody(assertion_id=assertion_id, expected_address=address, note=note)
    return _repo(dsn).execute(command_name=RESOLVE_UNSUBSCRIBE_REVIEW, operator=operator,
                              fields=body.model_dump(mode="json"), idempotency_key=key or uuid.uuid4().hex,
                              digest=request_digest(RESOLVE_UNSUBSCRIBE_REVIEW, body))


@needs_db
def test_a_held_review_is_resolved_once_into_a_permanent_suppression_and_idempotently(
        disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.commands import CommandRefused

    tag = world["tag"]
    dsn = runtime_dsn(disposable_database)
    other = f"otra-{tag}@lab.test"
    [(aid,)] = _db_rows(disposable_database, "select id::text from evidence.assertion where kind = 'unsubscribe_request' "
                                             "and value_norm = %s and resolution = 'unresolved'", (other,))
    with pytest.raises(CommandRefused) as err:
        _resolve(disposable_database, world["operator"], aid, f"otra-persona-{tag}@lab.test")
    assert err.value.code == "review_address_mismatch"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # a viewer, even past the route
        _resolve(disposable_database, world["viewer_identity"], aid, other)

    before = _counts(disposable_database)
    out = _resolve(disposable_database, world["operator"], aid, other)
    assert (out["was"], out["outcome"]) == ("unresolved", "added")
    assert _delta(before, _counts(disposable_database)) == {
        "outbound.contact_control": 1, "crm.domain_event": 1, "platform.command_receipt": 1}
    [(resolution, resolved_id, review_status, reason, event_type, event)] = _db_rows(disposable_database, """
        select a.resolution, a.resolved_id::text, s.review_status, c.reason, e.event_type, e.payload
          from evidence.assertion a
          join evidence.source_record s on s.id = a.source_record_id
          join outbound.contact_control c on c.id = a.resolved_id
          join crm.domain_event e on e.aggregate_kind = 'contact_control' and e.aggregate_id = c.id
         where a.id = %s""", (aid,))
    assert (resolution, resolved_id, review_status, reason) == ("promoted", out["contact_control_id"], "promoted", "unsubscribe")
    assert event_type == "contact_control.added" and event["basis"] == "review_confirmed"
    assert event["review_note"] == "Revisado: pidió la baja" and event["assertion_id"] == aid
    assert event["grammar_version"] == BAJA_GRAMMAR_VERSION and event["policy_version"] == UNSUBSCRIBE_POLICY_VERSION

    # Confirming again — a new key, the same request — changes nothing but the receipt.
    before = _counts(disposable_database)
    again = _resolve(disposable_database, world["operator"], aid, other)
    assert (again["was"], again["outcome"], again["contact_control_id"]) == (
        "promoted", "already_resolved", out["contact_control_id"])
    assert _delta(before, _counts(disposable_database)) == {"platform.command_receipt": 1}
    # The hold became the permanent suppression; nothing lifts either, not even the owner.
    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        for sql in ("update evidence.assertion set resolution = 'rejected', resolved_kind = null, resolved_id = null "
                    "where id = %s",
                    "update evidence.assertion set resolved_id = gen_random_uuid() where id = %s",
                    "update evidence.source_record set review_status = 'rejected' "
                    "where id = (select source_record_id from evidence.assertion where id = %s)"):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, (aid,))
    workspace_audience = __import__("origenlab_api.v2.crm_workspace", fromlist=["x"]).CrmWorkspaceRepository(
        psycopg.connect, dsn).marketing_audience_inputs().eligibility
    assert other in workspace_audience.unsubscribed_addresses
    assert other not in workspace_audience.unsubscribe_pending_addresses
    # Now known, the address's next «BAJA» is suppressed outright: no new review item.
    later = _apply(disposable_database, world["operator"], [_record(sender=other, mid=f"<otra-2-{tag}@lab.test>")])
    assert later["rows"][0]["planned"] == "evidence_only" and later["applied"]["evidence_linked"] == 1
    # Two items held for one address: the first confirmation creates the control, the second
    # links to it.
    solo = f"solo-{tag}@uni.test"
    items = [r[0] for r in _db_rows(disposable_database, "select id::text from evidence.assertion where kind = 'unsubscribe_request' "
                                                         "and value_norm = %s and resolution = 'unresolved' order by created_at",
                                    (solo,))]
    assert len(items) == 2
    first = _resolve(disposable_database, world["operator"], items[0], solo)
    second = _resolve(disposable_database, world["operator"], items[1], solo)
    assert (first["outcome"], second["outcome"]) == ("added", "evidence_linked")
    assert second["contact_control_id"] == first["contact_control_id"]


@needs_db
def test_the_privileged_writer_refuses_forged_bases_operators_and_receipts(disposable_database, world) -> None:
    import psycopg

    tag = world["tag"]
    dsn = runtime_dsn(disposable_database)
    call = "select outbound.add_contact_control('block', 'marketing', %s, 'unsubscribe', %s, %s, %s::jsonb)"
    stranger = _record(sender=f"forja-{tag}@lab.test", mid=f"<forja-{tag}@lab.test>", in_reply_to=world["outbound_mid"])
    parsed, _ = parse_record(stranger, datetime.now(timezone.utc))
    held_address = f"perdida-{tag}@lab.test"
    [(held_request,)] = _db_rows(disposable_database, "select id::text from evidence.assertion where kind = 'unsubscribe_request' "
                                                      "and value_norm = %s and resolution = 'unresolved'", (held_address,))
    before = _counts(disposable_database)
    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        disabled = conn.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                                "values (gen_random_uuid(), %s, 'W10 disabled', 'sales', 'disabled') returning id::text",
                                (f"w10-disabled-{tag}@example.test",)).fetchone()[0]
    with psycopg.connect(dsn, autocommit=True) as conn:
        def receipt(operator, command=APPLY_UNSUBSCRIBE_REPLIES):
            return conn.execute(
                "insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status) "
                "values (%s, %s, %s, %s, 'in_progress') returning id::text",
                (operator, uuid.uuid4().hex, command, "0" * 64)).fetchone()[0]

        mine = receipt(world["op"])
        # A basis the database cannot prove is refused, not trusted.
        for ev in (parsed.evidence("e" * 64, basis="known_address"),            # not known here
                   parsed.evidence("e" * 64, basis="outbound_lineage"),         # replied, but was not a recipient
                   parsed.evidence("e" * 64, basis="pending_review"),           # a hold without its reason
                   parsed.evidence("e" * 64, basis="pending_review", review_reason="porque_si"),
                   parsed.evidence("e" * 64, basis="dismissed"),                # no such basis (it is review_dismissed)
                   {**parsed.evidence("e" * 64, basis="pending_review", review_reason="lineage_missing"),
                    "policy_version": "otra/2026.v9"},                          # versions disagree with the payload
                   {**parsed.evidence("e" * 64, basis="pending_review", review_reason="lineage_missing"),
                    "policy_version": None}):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                conn.execute(call, (parsed.address, world["op"], mine, json.dumps(ev)))
        lineage_forged = parsed.evidence("e" * 64, basis="outbound_lineage")
        lineage_forged["payload"]["from_address"] = f"hist-{tag}@lab.test"  # claims to be the recipient
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            conn.execute(call, (parsed.address, world["op"], mine, json.dumps(lineage_forged)))

        held = parsed.evidence("e" * 64, basis="pending_review", review_reason="recipient_mismatch")
        confirm = {"basis": "review_confirmed", "assertion_id": held_request, "note": "revisado",
                   "policy_version": UNSUBSCRIBE_POLICY_VERSION}
        forged_receipts = (
            (world["op"], str(uuid.uuid4()), held),                                  # no such receipt
            (world["viewer"], mine, held),                                           # another operator's open receipt
            (world["op"], receipt(world["viewer"]), held),                           # … even one they opened
            (world["op"], receipt(world["op"], RESOLVE_UNSUBSCRIBE_REVIEW), held),   # another command's receipt
            (world["op"], mine, confirm),                                            # a reply receipt cannot confirm
            (disabled, mine, held),                                                  # a disabled operator
            (str(uuid.uuid4()), mine, held),                                         # an operator that does not exist
        )
        for operator, rid, ev in forged_receipts:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(call, (parsed.address, operator, rid, json.dumps(ev)))
        done = receipt(world["op"])
        conn.execute("update platform.command_receipt set status = 'completed', response_status = 200, "
                     "response_body = '{}'::jsonb, completed_at = now() where id = %s", (done,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # a completed receipt is closed
            conn.execute(call, (parsed.address, world["op"], done, json.dumps(held)))
        # A confirmation must name the held request's own address.
        resolve_receipt = receipt(world["op"], RESOLVE_UNSUBSCRIBE_REVIEW)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            conn.execute(call, (parsed.address, world["op"], resolve_receipt, json.dumps(confirm)))
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            conn.execute(call, (held_address, world["op"], resolve_receipt, json.dumps({**confirm, "note": "  "})))
        with pytest.raises(psycopg.errors.InvalidParameterValue):  # whitespace of any kind is blank
            conn.execute(call, (held_address, world["op"], resolve_receipt, json.dumps({**confirm, "note": " \n\t "})))
        # No direct write reaches the tables the function owns: no grant for the first two, and
        # the api's column grant on assertion resolutions stops at the permanence guard.
        for sql in ("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                    "values ('address', 'x@lab.test', 'block', 'marketing', 'unsubscribe', 'unsubscribe_handler')",
                    "insert into evidence.source_record (kind, dedupe_key, payload) "
                    "values ('gmail_message', 'gmail_unsubscribe:' || repeat('9', 64), '{}')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("update evidence.assertion set resolution = 'promoted', resolved_kind = 'contact_control', "
                         "resolved_id = gen_random_uuid(), resolved_at = now() where id = %s", (held_request,))
    after = _counts(disposable_database)
    assert _delta(before, after) == {"platform.command_receipt": 5}


def _dismiss(dsn, operator, assertion_id, address, review_sha256, explanation="Falso positivo: pedía la baja de otro boletín",
             key=None):
    from origenlab_api.v2.commands import request_digest
    from origenlab_api.v2.unsubscribe_commands import DismissUnsubscribeReviewBody

    body = DismissUnsubscribeReviewBody(assertion_id=assertion_id, expected_address=address,
                                        expected_review_sha256=review_sha256, explanation=explanation)
    return _repo(dsn).execute(command_name=DISMISS_UNSUBSCRIBE_REVIEW, operator=operator,
                              fields=body.model_dump(mode="json"), idempotency_key=key or uuid.uuid4().hex,
                              digest=request_digest(DISMISS_UNSUBSCRIBE_REVIEW, body))


def _pending(dsn) -> dict[str, list[dict[str, Any]]]:
    """The suppressions read's held reviews, by address — what an admin would dismiss from."""
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    held: dict[str, list[dict[str, Any]]] = {}
    for p in CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn)).suppressions(limit=10_000)["pending_reviews"]:
        held.setdefault(p["address"], []).append(p)
    return held


@needs_db
def test_an_admin_dismisses_a_pending_false_positive_once_and_it_can_still_be_confirmed(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.commands import CommandRefused
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    tag = world["tag"]
    dsn = runtime_dsn(disposable_database)
    false_pos, keeps = f"falso-{tag}@lab.test", f"sigue-{tag}@lab.test"
    out = _apply(disposable_database, world["operator"], [
        _record(sender=false_pos, mid=f"<falso-1-{tag}@lab.test>"),
        _record(sender=keeps, mid=f"<sigue-1-{tag}@lab.test>"),
    ])
    assert out["applied"]["pending_review"] == 2
    [held] = _pending(disposable_database)[false_pos]
    aid, version = held["assertion_id"], held["review_sha256"]
    [(value_before, payload_before)] = _db_rows(disposable_database, """
        select a.value, s.payload from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id
         where a.id = %s""", (aid,))

    # Refused, and nothing written — not even a receipt: sales, another address, a stale version.
    before = _counts(disposable_database)
    with pytest.raises(CommandRefused) as err:
        _dismiss(disposable_database, world["operator"], aid, false_pos, version)
    assert err.value.code == "role_may_not_dismiss"
    with pytest.raises(CommandRefused) as err:
        _dismiss(disposable_database, world["admin_identity"], aid, keeps, version)
    assert err.value.code == "review_address_mismatch"
    with pytest.raises(CommandRefused) as err:
        _dismiss(disposable_database, world["admin_identity"], str(uuid.uuid4()), false_pos, version)
    assert err.value.code == "review_not_found"
    with pytest.raises(psycopg.errors.RaiseException, match="review_sha256 differs"):
        _dismiss(disposable_database, world["admin_identity"], aid, false_pos, "0" * 64)
    assert _counts(disposable_database) == before

    key = uuid.uuid4().hex
    dismissed = _dismiss(disposable_database, world["admin_identity"], aid, false_pos, version, key=key)
    assert (dismissed["was"], dismissed["outcome"], dismissed["replayed"]) == ("unresolved", "dismissed", False)
    assert dismissed["suppression"] is None and dismissed["review_sha256"] == version
    # Exactly one decision event and one receipt: no control, no evidence, no identity.
    assert _delta(before, _counts(disposable_database)) == {"crm.domain_event": 1, "platform.command_receipt": 1}
    [(resolution, by, at, value, review_status, payload)] = _db_rows(disposable_database, """
        select a.resolution, a.resolved_by_operator_id::text, a.resolved_at is not null, a.value, s.review_status, s.payload
          from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id where a.id = %s""", (aid,))
    assert (resolution, by, at, review_status) == ("rejected", world["admin"], True, "reviewed")
    assert (value, payload) == (value_before, payload_before)  # the hold and the reply, exactly as received
    [(event_type, seq, event, actor, receipt)] = _db_rows(disposable_database, """
        select event_type, seq, payload, actor_operator_id::text, command_receipt_id::text from crm.domain_event
         where aggregate_kind = 'assertion' and aggregate_id = %s and event_type <> 'assertion.unsubscribe_review_opened'""",
        (aid,))
    assert (event_type, seq, actor, receipt) == (
        "assertion.unsubscribe_review_dismissed", 2, world["admin"], dismissed["command_receipt_id"])
    assert event["explanation"] == "Falso positivo: pedía la baja de otro boletín"
    assert (event["decision"], event["resolution_from"], event["resolution_to"]) == ("dismissed", "unresolved", "rejected")
    assert event["review_sha256"] == version and false_pos not in json.dumps(event)

    # The same key replays the stored answer; nothing is written again.
    before = _counts(disposable_database)
    again = _dismiss(disposable_database, world["admin_identity"], aid, false_pos, version, key=key)
    assert again["replayed"] is True and {k: v for k, v in again.items() if k != "replayed"} == {
        k: v for k, v in dismissed.items() if k != "replayed"}
    assert _counts(disposable_database) == before
    # A new key finds it decided: it cannot be dismissed twice.
    with pytest.raises(CommandRefused) as err:
        _dismiss(disposable_database, world["admin_identity"], aid, false_pos, version)
    assert err.value.code == "review_not_pending"
    assert _counts(disposable_database) == before

    # The hold no longer holds; the other one still does.
    held_now = _pending(disposable_database)
    assert false_pos not in held_now and keeps in held_now
    facts = CrmWorkspaceRepository(psycopg.connect, dsn).marketing_audience_inputs().eligibility
    assert false_pos not in facts.unsubscribe_pending_addresses and keeps in facts.unsubscribe_pending_addresses
    assert false_pos not in facts.unsubscribed_addresses

    # The dismissal was a mistake: the same request is confirmed afterwards (toward suppression
    # only). The dismissal's own event stays; the confirmation says it overrides it.
    confirmed = _resolve(disposable_database, world["operator"], aid, false_pos, note="Revisado otra vez: sí pidió la baja")
    assert (confirmed["was"], confirmed["outcome"]) == ("rejected", "added")
    assert _delta(before, _counts(disposable_database)) == {
        "outbound.contact_control": 1, "crm.domain_event": 1, "platform.command_receipt": 1}
    [(resolution, resolved_id, review_status)] = _db_rows(disposable_database, """
        select a.resolution, a.resolved_id::text, s.review_status
          from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id where a.id = %s""", (aid,))
    assert (resolution, resolved_id, review_status) == ("promoted", confirmed["contact_control_id"], "promoted")
    [(override,)] = _db_rows(disposable_database, "select payload from crm.domain_event where aggregate_kind = 'contact_control' "
                                                  "and aggregate_id = %s", (confirmed["contact_control_id"],))
    assert (override["resolution_from"], override["overrides_dismissal"], override["basis"]) == (
        "rejected", True, "review_confirmed")
    assert [r[0] for r in _db_rows(disposable_database, "select event_type from crm.domain_event where aggregate_kind = 'assertion' "
                                                        "and aggregate_id = %s order by seq", (aid,))] == [
        "assertion.unsubscribe_review_opened", "assertion.unsubscribe_review_dismissed"]
    facts = CrmWorkspaceRepository(psycopg.connect, dsn).marketing_audience_inputs().eligibility
    assert false_pos in facts.unsubscribed_addresses
    # Never the reverse: once confirmed, it cannot be dismissed again.
    with pytest.raises(CommandRefused) as err:
        _dismiss(disposable_database, world["admin_identity"], aid, false_pos, version)
    assert err.value.code == "review_already_confirmed"


@needs_db
def test_a_confirmed_unsubscribe_is_never_dismissed_and_always_wins(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.commands import CommandRefused
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    tag = world["tag"]
    dsn = runtime_dsn(disposable_database)
    both = f"ambas-{tag}@lab.test"
    _apply(disposable_database, world["operator"], [_record(sender=both, mid=f"<ambas-1-{tag}@lab.test>"),
                                                     _record(sender=both, mid=f"<ambas-2-{tag}@lab.test>")])
    first, second = sorted(_pending(disposable_database)[both], key=lambda p: p["assertion_id"])
    confirmed = _resolve(disposable_database, world["operator"], first["assertion_id"], both)
    assert confirmed["outcome"] == "added"
    # The confirmed request is refused by the command, and by the function if the command is bypassed.
    with pytest.raises(CommandRefused) as err:
        _dismiss(disposable_database, world["admin_identity"], first["assertion_id"], both, first["review_sha256"])
    assert err.value.code == "review_already_confirmed"
    with psycopg.connect(dsn, autocommit=True) as conn:
        rid = conn.execute(
            "insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status) "
            "values (%s, %s, 'dismiss-unsubscribe-review', %s, 'in_progress') returning id::text",
            (world["admin"], uuid.uuid4().hex, "0" * 64)).fetchone()[0]
        call = "select outbound.add_contact_control('block', 'marketing', %s, 'unsubscribe', %s, %s, %s::jsonb)"
        dismissal = {"basis": "review_dismissed", "assertion_id": first["assertion_id"],
                     "review_sha256": first["review_sha256"], "explanation": "x", "policy_version": UNSUBSCRIBE_POLICY_VERSION}
        with pytest.raises(psycopg.errors.RaiseException, match="confirmed unsubscribe"):
            conn.execute(call, (both, world["admin"], rid, json.dumps(dismissal)))
        # A foreign request (another address), a blank explanation, and a sales operator are refused too.
        pending = {**dismissal, "assertion_id": second["assertion_id"], "review_sha256": second["review_sha256"]}
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            conn.execute(call, (f"otro-{tag}@lab.test", world["admin"], rid, json.dumps(pending)))
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            conn.execute(call, (both, world["admin"], rid, json.dumps({**pending, "explanation": " \n "})))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(call, (both, world["op"], rid, json.dumps(pending)))
        sales_rid = conn.execute(
            "insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status) "
            "values (%s, %s, 'dismiss-unsubscribe-review', %s, 'in_progress') returning id::text",
            (world["op"], uuid.uuid4().hex, "0" * 64)).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only an active admin"):
            conn.execute(call, (both, world["op"], sales_rid, json.dumps(pending)))
        resolve_rid = conn.execute(
            "insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status) "
            "values (%s, %s, 'resolve-unsubscribe-review', %s, 'in_progress') returning id::text",
            (world["admin"], uuid.uuid4().hex, "0" * 64)).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # a dismissal needs its own command's receipt
            conn.execute(call, (both, world["admin"], resolve_rid, json.dumps(pending)))
    # The second hold on the same address can still be dismissed — and the permanent unsubscribe wins.
    before = _counts(disposable_database)
    out = _dismiss(disposable_database, world["admin_identity"], second["assertion_id"], both, second["review_sha256"])
    assert out["outcome"] == "dismissed"
    assert _delta(before, _counts(disposable_database)) == {"crm.domain_event": 1, "platform.command_receipt": 1}
    facts = CrmWorkspaceRepository(psycopg.connect, dsn).marketing_audience_inputs().eligibility
    assert both in facts.unsubscribed_addresses and both not in facts.unsubscribe_pending_addresses
    [(reason, n)] = _db_rows(disposable_database, "select reason, count(*) from outbound.contact_control "
                                                  "where value_norm = %s group by reason", (both,))
    assert (reason, n) == ("unsubscribe", 1)


@needs_db
def test_evidence_control_and_event_commit_together_or_not_at_all(disposable_database, world) -> None:
    import psycopg

    tag = world["tag"]
    known = _record(sender=f"Late <late-{tag}@uni.test>", mid=f"<atomic-known-{tag}@uni.test>")
    unknown = _record(sender=f"atomic-{tag}@lab.test", mid=f"<atomic-held-{tag}@lab.test>")
    shas = [parse_record(r, NOW)[0].message_id_sha256 for r in (known, unknown)]
    # A fault injected at the very last write of the function: the domain event.
    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        conn.execute("create function platform.__w10_fail_event() returns trigger language plpgsql as $f$ "
                     "begin if new.payload->>'message_id_sha256' = any(tg_argv) then "
                     "raise exception 'injected event failure'; end if; return new; end $f$")
        conn.execute(f"create trigger __w10_fail_event before insert on crm.domain_event for each row "
                     f"execute function platform.__w10_fail_event('{shas[0]}', '{shas[1]}')")
    try:
        for record in (known, unknown):
            before = _counts(disposable_database)
            with pytest.raises(psycopg.errors.RaiseException):
                _apply(disposable_database, world["operator"], [record])
            assert _counts(disposable_database) == before
            assert _db_rows(disposable_database, "select count(*) from evidence.source_record where dedupe_key = %s",
                            (f"gmail_unsubscribe:{parse_record(record, NOW)[0].message_id_sha256}",)) == [(0,)]
    finally:
        with psycopg.connect(disposable_database, autocommit=True) as conn:
            conn.execute("set role origenlab_owner")
            conn.execute("drop trigger __w10_fail_event on crm.domain_event")
            conn.execute("drop function platform.__w10_fail_event()")
    # Without the fault the same records land whole: evidence, control or hold, and the event.
    before = _counts(disposable_database)
    out = _apply(disposable_database, world["operator"], [known, unknown])
    assert out["applied"]["evidence_linked"] + out["applied"]["added"] == 1 and out["applied"]["pending_review"] == 1
    delta = _delta(before, _counts(disposable_database))
    assert delta["evidence.source_record"] == delta["evidence.assertion"] == delta["crm.domain_event"] == 2


@needs_db
def test_the_suppression_read_is_masked_for_a_viewer_and_says_gmail_is_not_synchronized(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    tag = world["tag"]

    def client(role):
        app = FastAPI()
        app.state.v2_identity = _Port(role, world["op"])
        app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
        app.include_router(workspace_router)
        return TestClient(app)

    viewer = client("viewer").get("/v2/workspace/marketing/suppressions")
    assert viewer.status_code == 200 and viewer.headers.get("X-OrigenLab-Redaction") == "contact-addresses"
    text = viewer.text
    assert f"ana-{tag}@uni.test" not in text and "***@uni.test" in text
    body = viewer.json()
    assert body["gmail_sync"]["automatic"] is False and "no se sincronizan automáticamente" in body["gmail_sync"]["label"]
    assert body["resubscribe_supported"] is False and body["apply_enabled"] is False
    assert body["grammar"]["accepted"] == ["BAJA", "BAJA.", "REMOVER", "REMOVER."]
    assert body["grammar"]["version"] == BAJA_GRAMMAR_VERSION
    assert body["sender_policy"]["version"] == UNSUBSCRIBE_POLICY_VERSION
    held = [p for p in body["pending_reviews"] if p["review_reason"] == "lineage_missing"]
    assert held and all(p["address"].startswith("***@") for p in body["pending_reviews"])
    assert f"perdida-{tag}@lab.test" not in text and held[0]["review_reason_label"].startswith("La respuesta no remite")
    sales = client("sales").get("/v2/workspace/marketing/suppressions")
    assert f"ana-{tag}@uni.test" in sales.text
    assert f"perdida-{tag}@lab.test" in {p["address"] for p in sales.json()["pending_reviews"]}
