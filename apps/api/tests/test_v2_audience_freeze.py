"""Audience freeze: the snapshot a freeze writes, what stops it, and what PostgreSQL keeps.

**In process, always run:** the six canonical lines, the planner over hand-built inputs
(relevance kept apart from permission, every exclusion reason, identity review, duplicates,
malformed destinations, the preview fingerprint), the request body, the switch and the route
requirements.

**Database-backed, opt-in:** create a draft, preview, freeze, and prove the snapshot is
immutable and that a changed campaign is a new freeze. Only in a disposable
`origenlab_test_<hex>` the harness creates and drops, as the `origenlab_api` runtime role.
Every value is fictitious.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.audience_freeze import (
    CANONICAL_LINES,
    FREEZE_CAMPAIGN_AUDIENCE,
    SEND_BLOCKERS,
    FreezeAudienceBody,
    AUDIENCE_POLICY_VERSION,
    AUDIENCE_POLICY_VERSION_W12,
    RECONTACT_POLICY_VERSION,
    FreezeCriteria,
    RecontactDecision,
    ReviewDecision,
    content_sha256,
    freeze_fields,
    plan_freeze,
    text_from_html,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.equipment_taxonomy import load_taxonomy
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.marketing_audience import AudienceInputs, CaseFacts, EligibilityFacts
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


def _never_connects(*_args, **_kwargs):
    raise AssertionError("mounting a router must not open a database connection")


T = datetime(2026, 3, 10, tzinfo=timezone.utc)
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
CAMPAIGN = {"id": "c-1", "status": "draft", "version": 3, "name": "Sonicadores", "subject": "Sonicadores Hielscher",
            "preheader": "UP200St", "body_html": "<p>Hola</p><p>Conozca el UP200St.</p>", "max_sends": 50}
HIELSCHER = FreezeCriteria(brand_id="hielscher")


# --------------------------------------------------------------------------- canonical lines


def test_the_six_canonical_lines_are_each_a_brand_on_its_family() -> None:
    taxonomy = load_taxonomy()
    assert [ln["brand_id"] for ln in CANONICAL_LINES] == [
        "hielscher", "ortoalresa", "ika", "adam-equipment", "loeser", "serva"]
    for ln in CANONICAL_LINES:
        assert taxonomy.brands[ln["brand_id"]]["family_id"] == ln["family_id"]
    assert {ln["family_id"] for ln in CANONICAL_LINES} == set(taxonomy.families)
    assert [ln["line"] for ln in CANONICAL_LINES][-1] == "Electroforesis, reactivos y consumibles"


# --------------------------------------------------------------------------- content


def test_the_text_part_is_the_html_text_and_the_fingerprint_covers_the_preheader() -> None:
    html = "<html><head><title>x</title><style>p{}</style></head><body><p>Hola&nbsp;Ana</p><p>UP200St</p></body></html>"
    assert text_from_html(html) == "Hola Ana\n\nUP200St"
    a = content_sha256("Asunto", "Pre", "Texto", "<p>Texto</p>")
    assert a != content_sha256("Asunto", "Otro", "Texto", "<p>Texto</p>")
    assert a != content_sha256("Asunto", None, "Texto", "<p>Texto</p>")


# --------------------------------------------------------------------------- planner


def _case(cid: str, org: str | None, **kw) -> CaseFacts:
    return CaseFacts(opportunity_id=cid, title=kw.pop("title", f"Caso {cid}"), stage=kw.pop("stage", "lead"),
                     created_at=T, organization_id=org, organization_name=f"Instituto {org}" if org else None, **kw)


def _ev(sr: str, case: str, recipients: str, subject: str = "Cotización UP200St") -> dict:
    return {"source_record_id": sr, "opportunity_id": case, "subject": subject, "filenames": [],
            "sent_at": "2026-03-12T10:00:00+00:00", "recipients": recipients}


def _cp(cid: str, address: str, org: str | None = None, person: str | None = None) -> dict:
    return {"id": cid, "address": address, "usage": "personal", "person_id": person, "organization_id": org,
            "person_name": None}


def _world(**overrides) -> AudienceInputs:
    cases = {"k1": _case("k1", "uni", first_sent_at=T), "k2": _case("k2", "lab", first_sent_at=T)}
    recipients = ("ana@uni.test, bea@uni.test, sup@proveedor.test, blocked@uni.test, dom@bloqueado.test, "
                  "cool@uni.test, prior@uni.test, bounced@uni.test, cand@candidato.test, anon@uni.test")
    facts_kw = dict(
        blocked_addresses={"blocked@uni.test"}, blocked_domains={"bloqueado.test"},
        cooldown_addresses={"cool@uni.test"}, prior_contact_addresses={"prior@uni.test", "bounced@uni.test"},
        invalid_addresses={"bounced@uni.test"}, supplier_domains={"proveedor.test"},
        candidate_supplier_domains={"candidato.test"},
        contact_points={a: _cp(f"cp-{a.split('@')[0]}", a, "uni" if a.endswith("uni.test") else None,
                               "p-ana" if a in ("ana@uni.test", "bea@uni.test") else None)
                        for a in ("ana@uni.test", "bea@uni.test", "sup@proveedor.test", "blocked@uni.test",
                                  "dom@bloqueado.test", "cool@uni.test", "prior@uni.test", "bounced@uni.test",
                                  "cand@candidato.test")},
    )
    facts = EligibilityFacts(**{**facts_kw, **overrides})
    return AudienceInputs(cases=cases, interests=[], quotation_evidence=[_ev("sr-1", "k1", recipients)], eligibility=facts)


def _rows(plan) -> dict:
    return {r["address"]: r for r in plan["rows"]}


def test_every_exclusion_reason_is_recorded_and_relevance_stays_apart() -> None:
    plan = plan_freeze(load_taxonomy(), _world(), CAMPAIGN, HIELSCHER)
    rows = _rows(plan)
    assert rows["ana@uni.test"]["inclusion"] == "included" and rows["ana@uni.test"]["frozen_reasons"] == []
    expected = {
        "sup@proveedor.test": ["policy_supplier"],
        "blocked@uni.test": ["block"],
        "dom@bloqueado.test": ["block_domain"],
        "cool@uni.test": ["cooldown"],
        "prior@uni.test": ["prior_contact"],
        "bounced@uni.test": ["invalid_address"],  # terminal: prior_contact is not also listed
        "cand@candidato.test": ["manual_hold"],
        "bea@uni.test": ["already_in_audience"],  # same person as ana
    }
    for address, reasons in expected.items():
        assert rows[address]["frozen_reasons"] == reasons, address
        assert rows[address]["inclusion"] == "excluded"
    assert "possible_supplier_unreviewed" in rows["cand@candidato.test"]["frozen_notes"]
    # Excluded rows keep their evidence: relevance is not permission, and permission is not relevance.
    assert rows["blocked@uni.test"]["relevance"] == "evidenced"
    assert all(e["brand_id"] == "hielscher" for e in rows["ana@uni.test"]["evidence"])


def test_a_line_without_evidence_is_sin_informacion_never_low() -> None:
    plan = plan_freeze(load_taxonomy(), _world(), CAMPAIGN, HIELSCHER)
    lines = {ln["brand_id"]: ln for ln in _rows(plan)["ana@uni.test"]["lines"]}
    assert lines["hielscher"]["status"] == "evidenced" and lines["hielscher"]["latest_observed_at"].startswith("2026-03-12")
    for b in ("ortoalresa", "ika", "adam-equipment", "loeser", "serva"):
        assert lines[b]["status"] == "sin_informacion" and lines[b]["label"] == "Sin información"
    by_line = {x["brand_id"]: x for x in plan["counts"]["included_by_line"]}
    assert by_line["serva"] == {**by_line["serva"], "evidenced": 0, "sin_informacion": plan["counts"]["included"]}


def test_an_ambiguous_identity_blocks_the_freeze_until_a_person_decides() -> None:
    taxonomy, world = load_taxonomy(), _world()
    preview = plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER)
    anon = _rows(preview)["anon@uni.test"]
    assert anon["review_codes"] == ["no_contact_point"] and preview["review_pending"] == [anon["key"]]
    assert [p["code"] for p in preview["problems"]] == ["review_pending"]
    # Only an otherwise-includable destination needs a decision.
    assert _rows(preview)["blocked@uni.test"]["review_codes"] == []

    include = plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER,
                          decisions={anon["key"]: ReviewDecision(key=anon["key"], decision="include", note="Es la jefa de lab")})
    row = _rows(include)["anon@uni.test"]
    assert row["inclusion"] == "included" and "identity_reviewed_include" in row["frozen_notes"]
    assert row["identity_review"] == {"codes": ["no_contact_point"], "decision": "include", "note": "Es la jefa de lab"}
    assert include["problems"] == [] and include["preview_sha256"] == preview["preview_sha256"]

    exclude = plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER,
                          decisions={anon["key"]: ReviewDecision(key=anon["key"], decision="exclude", note="No es cliente")})
    assert _rows(exclude)["anon@uni.test"]["frozen_reasons"] == ["manual_hold"]


def test_a_person_reached_through_two_institutions_needs_review() -> None:
    world = _world()
    world.cases["k2"] = _case("k2", "lab", first_sent_at=T)
    world.quotation_evidence.append(_ev("sr-2", "k2", "ana@uni.test"))
    ana = _rows(plan_freeze(load_taxonomy(), world, CAMPAIGN, HIELSCHER))["ana@uni.test"]
    assert set(ana["review_codes"]) == {"multiple_institutions"}
    assert {o["organization_id"] for o in ana["organizations"]} == {"uni", "lab"}


def test_malformed_destinations_are_excluded_and_unstorable_ones_counted() -> None:
    world = _world()
    world.quotation_evidence.append(_ev("sr-3", "k1", "roto@uni..test, a..b@uni.test"))
    plan = plan_freeze(load_taxonomy(), world, CAMPAIGN, HIELSCHER)
    rows = _rows(plan)
    for address in ("roto@uni..test", "a..b@uni.test"):
        assert rows[address]["frozen_reasons"] == ["invalid_address"], address
        assert "malformed_address" in rows[address]["frozen_notes"] and rows[address]["review_codes"] == []
    # An address the database could not even hold is never a row: it is counted.
    world.eligibility.contact_points["x y@uni.test"] = _cp("cp-xy", "x y@uni.test", "uni")
    inst_plan = plan_freeze(load_taxonomy(), world, CAMPAIGN, FreezeCriteria(brand_id="hielscher", scope="institutions"))
    assert "x y@uni.test" not in _rows(inst_plan) and inst_plan["malformed_count"] == 1
    assert inst_plan["counts"]["candidates"] == inst_plan["counts"]["rows"] + inst_plan["counts"]["malformed_not_stored"]


def test_the_preview_fingerprint_moves_with_the_audience_not_with_decisions() -> None:
    taxonomy = load_taxonomy()
    a = plan_freeze(taxonomy, _world(), CAMPAIGN, HIELSCHER)["preview_sha256"]
    assert a == plan_freeze(taxonomy, _world(), CAMPAIGN, HIELSCHER, excluded_keys=frozenset())["preview_sha256"]
    blocked = plan_freeze(taxonomy, _world(blocked_addresses={"blocked@uni.test", "ana@uni.test"}), CAMPAIGN, HIELSCHER)
    assert blocked["preview_sha256"] != a
    assert plan_freeze(taxonomy, _world(), {**CAMPAIGN, "version": 4}, HIELSCHER)["preview_sha256"] != a
    assert plan_freeze(taxonomy, _world(), CAMPAIGN, FreezeCriteria(brand_id="hielscher", scope="persons"))["preview_sha256"] != a


def test_what_stops_a_freeze_is_listed_not_guessed() -> None:
    taxonomy, world = load_taxonomy(), _world()
    anon = _rows(plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER))["anon@uni.test"]["key"]
    ok = {anon: ReviewDecision(key=anon, decision="exclude", note="no aplica")}
    codes = lambda p: [x["code"] for x in p["problems"]]  # noqa: E731
    assert codes(plan_freeze(taxonomy, world, {**CAMPAIGN, "status": "audience_frozen"}, HIELSCHER, decisions=ok)) == ["campaign_not_draft"]
    assert codes(plan_freeze(taxonomy, world, {**CAMPAIGN, "subject": None}, HIELSCHER, decisions=ok)) == ["no_subject"]
    assert codes(plan_freeze(taxonomy, world, {**CAMPAIGN, "body_html": None}, HIELSCHER, decisions=ok)) == ["no_body"]
    assert codes(plan_freeze(taxonomy, world, {**CAMPAIGN, "max_sends": 1}, HIELSCHER, decisions=ok,
                             excluded_keys=frozenset())) == []
    ana_key = _rows(plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER))["ana@uni.test"]["key"]
    two = {anon: ReviewDecision(key=anon, decision="include", note="sí aplica")}
    assert codes(plan_freeze(taxonomy, world, {**CAMPAIGN, "max_sends": 1}, HIELSCHER, decisions=two)) == ["exceeds_max_sends"]
    assert codes(plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER, decisions=ok,
                             excluded_keys=frozenset({ana_key}))) == ["nobody_included"]
    assert "unknown_keys" in codes(plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER, decisions=ok,
                                               excluded_keys=frozenset({"cp:not-in-audience"})))
    with pytest.raises(CommandRefused):
        plan_freeze(taxonomy, world, CAMPAIGN, FreezeCriteria(brand_id="acme"))


def test_the_send_blockers_name_the_missing_unsubscribe_processor() -> None:
    plan = plan_freeze(load_taxonomy(), _world(), {**CAMPAIGN, "body_html": "<p>Responda BAJA</p>"}, HIELSCHER)
    assert plan["send_blockers"][0]["code"] == "unsubscribe_sync_not_automatic"
    assert plan["content"]["promises_baja"] is True
    assert {b["code"] for b in SEND_BLOCKERS} == {"unsubscribe_sync_not_automatic", "no_send_path", "approval_not_built"}


# --------------------------------------------------------------------------- W12 recontact review


def _anon_ok(plan) -> dict:
    """Settle the one ambiguous identity every _world() carries, so W12 is what the test sees."""
    key = _rows(plan)["anon@uni.test"]["key"]
    return {key: ReviewDecision(key=key, decision="exclude", note="no aplica")}


def _w12(world=None, *, recontact=None, decisions=None, excluded=frozenset(), enabled=True):
    taxonomy, world = load_taxonomy(), world or _world()
    base = plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER, recontact_review=enabled)
    return plan_freeze(taxonomy, world, CAMPAIGN, HIELSCHER, recontact_review=enabled,
                       decisions=decisions if decisions is not None else _anon_ok(base),
                       excluded_keys=excluded, recontact_decisions=recontact or {})


def _rd(plan, address, decision="approve", note="El cliente pidió la ficha nueva", mode="individual"):
    key = _rows(plan)[address]["key"]
    return {key: RecontactDecision(key=key, decision=decision, note=note, mode=mode)}


def test_w12_off_prior_contact_stays_excluded_and_no_decision_can_lift_it() -> None:
    plan = _w12(enabled=False)
    prior = _rows(plan)["prior@uni.test"]
    assert plan["policy_version"] == AUDIENCE_POLICY_VERSION
    assert prior["frozen_reasons"] == ["prior_contact"] and prior["recontact_review_required"] is False
    assert plan["recontact_review"] == {"enabled": False, "policy_version": None, "required": []}
    lifted = _w12(enabled=False, recontact=_rd(plan, "prior@uni.test"))
    assert _rows(lifted)["prior@uni.test"]["inclusion"] == "excluded"
    assert _rows(lifted)["prior@uni.test"]["recontact_review"] is None
    assert "recontact_review_disabled" in [p["code"] for p in lifted["problems"]]


def test_w12_on_prior_contact_is_review_required_with_what_the_operator_must_see() -> None:
    world = _world(prior_contact_details={"prior@uni.test": {
        "sources": [{"source": "wave1a_union", "reason": "v1 prior contact: wave1a_contacted_union", "recorded_at": "2026-09-20"}],
        "last_contact_at": "2025-11-03T14:00:00+00:00", "campaign_id": "cmp-v1", "campaign_name": "Campaña V1 noviembre"}})
    plan = _w12(world)
    prior = _rows(plan)["prior@uni.test"]
    assert plan["policy_version"] == AUDIENCE_POLICY_VERSION_W12
    assert plan["recontact_review"]["required"] == [prior["key"]] and plan["counts"]["recontact_review_required"] == 1
    assert prior["prior_contact"] == {"destination": "prior@uni.test", "last_contact_at": "2025-11-03T14:00:00+00:00",
                                      "campaign_id": "cmp-v1", "campaign_name": "Campaña V1 noviembre",
                                      "sources": world.eligibility.prior_contact_details["prior@uni.test"]["sources"]}
    # Without a decision it stays excluded, and the missing decision does not block the freeze.
    assert prior["inclusion"] == "excluded" and prior["frozen_reasons"] == ["prior_contact"]
    assert "recontact_not_reviewed" in prior["frozen_notes"] and prior["recontact_review"] is None
    assert plan["problems"] == [] and plan["counts"]["recontact_not_reviewed"] == 1
    # A prior contact with no recorded date shows it as unknown, never a guess.
    assert _rows(_w12())["prior@uni.test"]["prior_contact"]["last_contact_at"] is None


def test_an_approval_lifts_prior_contact_and_records_one_audited_decision() -> None:
    base = _w12()
    approved = _w12(recontact=_rd(base, "prior@uni.test"))
    row = _rows(approved)["prior@uni.test"]
    assert row["inclusion"] == "included" and row["frozen_reasons"] == []
    assert "recontact_approved" in row["frozen_notes"] and "recontact_not_reviewed" not in row["frozen_notes"]
    assert row["recontact_review"] == {"decision": "approve", "note": "El cliente pidió la ficha nueva", "mode": "individual",
                                       "policy_version": RECONTACT_POLICY_VERSION, "prior_contact": row["prior_contact"]}
    assert approved["counts"]["recontact_approved"] == 1 and approved["problems"] == []
    # The decision is what the operator adds: the fingerprint of what they were shown does not move.
    assert approved["preview_sha256"] == base["preview_sha256"]

    kept = _rows(_w12(recontact=_rd(base, "prior@uni.test", "keep_excluded", "Ya dijo que no")))["prior@uni.test"]
    assert kept["inclusion"] == "excluded" and kept["frozen_reasons"] == ["prior_contact"]
    assert "recontact_kept_excluded" in kept["frozen_notes"] and kept["recontact_review"]["decision"] == "keep_excluded"


def test_w12_never_lifts_a_block_a_supplier_a_bounce_a_duplicate_or_a_cooldown() -> None:
    # Every one of these also has a prior contact; each keeps the reason W12 may not touch.
    world = _world(prior_contact_addresses={"prior@uni.test", "bounced@uni.test", "cool@uni.test", "blocked@uni.test",
                                            "dom@bloqueado.test", "sup@proveedor.test", "cand@candidato.test", "bea@uni.test"})
    base = _w12(world)
    rows = _rows(base)
    untouchable = {"bounced@uni.test": "invalid_address", "cool@uni.test": "cooldown", "blocked@uni.test": "block",
                   "dom@bloqueado.test": "block_domain", "sup@proveedor.test": "policy_supplier",
                   "cand@candidato.test": "manual_hold", "bea@uni.test": "already_in_audience"}
    for address, reason in untouchable.items():
        assert rows[address]["recontact_review_required"] is False, address
        plan = _w12(world, recontact=_rd(base, address))
        row = _rows(plan)[address]
        assert row["inclusion"] == "excluded" and reason in row["frozen_reasons"], address
        assert row["recontact_review"] is None and "recontact_approved" not in row["frozen_notes"], address
        assert "recontact_not_eligible" in [p["code"] for p in plan["problems"]], address
    assert base["recontact_review"]["required"] == [rows["prior@uni.test"]["key"]]


def test_a_second_address_of_the_same_person_is_never_reviewable() -> None:
    # ana is a prior contact, bea (same person) is clean: bea takes the person's slot.
    base = _w12(_world(prior_contact_addresses={"ana@uni.test"}))
    ana, bea = _rows(base)["ana@uni.test"], _rows(base)["bea@uni.test"]
    assert bea["inclusion"] == "included"
    assert set(ana["frozen_reasons"]) == {"prior_contact", "already_in_audience"} and ana["recontact_review_required"] is False


def test_an_approved_destination_with_an_ambiguous_identity_still_needs_a_person() -> None:
    world = _world(prior_contact_addresses={"prior@uni.test", "anon@uni.test"})
    base = _w12(world, decisions={})
    anon = _rows(base)["anon@uni.test"]
    assert anon["recontact_review_required"] is True and anon["review_codes"] == ["no_contact_point"]
    assert base["review_pending"] == []  # not approved: excluded whatever its identity
    approved = _w12(world, decisions={}, recontact=_rd(base, "anon@uni.test"))
    assert approved["review_pending"] == [anon["key"]] and "review_pending" in [p["code"] for p in approved["problems"]]
    held = _w12(world, recontact=_rd(base, "anon@uni.test"),
                decisions={anon["key"]: ReviewDecision(key=anon["key"], decision="exclude", note="no es la persona")})
    row = _rows(held)["anon@uni.test"]
    assert row["frozen_reasons"] == ["manual_hold"] and "recontact_approved" in row["frozen_notes"]


def test_a_bulk_decision_is_persisted_as_one_decision_per_recipient() -> None:
    world = _world(prior_contact_addresses={"prior@uni.test", "ana@uni.test", "bea@uni.test"})
    base = _w12(world)
    reviewable = [r["address"] for r in base["rows"] if r["recontact_review_required"]]
    assert reviewable == ["ana@uni.test", "prior@uni.test"]  # bea is ana's second address
    bulk = {}
    for address in reviewable:
        bulk |= _rd(base, address, note="Lote revisado: clientes 2024 con equipo en garantía", mode="bulk")
    plan = _w12(world, recontact=bulk)
    for address in reviewable:
        review = _rows(plan)[address]["recontact_review"]
        assert review["mode"] == "bulk" and review["prior_contact"]["destination"] == address
    assert plan["counts"]["recontact_approved"] == 2 and plan["counts"]["included"] == 2


def test_with_w12_on_the_fingerprint_covers_what_the_reviewer_was_shown() -> None:
    off, on = _w12(enabled=False), _w12()
    assert off["preview_sha256"] != on["preview_sha256"]  # the policy version differs
    later = _w12(_world(prior_contact_details={"prior@uni.test": {"sources": [], "last_contact_at": "2026-09-01T00:00:00+00:00"}}))
    assert later["preview_sha256"] != on["preview_sha256"]


def test_the_recontact_decision_is_closed_and_needs_a_note() -> None:
    RecontactDecision(key="cp:1", decision="approve", note="motivo")
    for bad in ({"key": "cp:1", "decision": "approve", "note": ""}, {"key": "cp:1", "decision": "approve", "note": "  a "},
                {"key": "cp:1", "decision": "approve"}, {"key": "cp:1", "decision": "override_block", "note": "motivo"},
                {"key": "cp:1", "decision": "approve", "note": "motivo", "mode": "auto"},
                {"key": "cp:1", "decision": "approve", "note": "motivo", "lift": ["block"]}):
        with pytest.raises(ValidationError):
            RecontactDecision(**bad)


# --------------------------------------------------------------------------- request and routes


BODY = {"campaign_id": str(uuid.uuid4()), "expected_version": 1, "expected_preview_sha256": "a" * 64,
        "criteria": {"brand_id": "hielscher"}, "confirmed": True}


def test_the_body_requires_the_final_confirmation_and_nothing_else_moves_state() -> None:
    FreezeAudienceBody(**BODY)
    for bad in ({**BODY, "confirmed": False}, {k: v for k, v in BODY.items() if k != "confirmed"},
                {**BODY, "status": "approved"}, {**BODY, "send": True},
                {**BODY, "criteria": {"brand_id": "hielscher", "score_min": 3}},
                {**BODY, "criteria": {"bases": ["lukewarm"]}}):
        with pytest.raises(ValidationError):
            FreezeAudienceBody(**bad)


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
        return {"campaign_id": "x", "replayed": False}


def _client(repo, role="sales"):
    from fastapi import FastAPI

    from origenlab_api.v2.audience_freeze_routes import audience_freeze_router

    app = FastAPI()
    app.include_router(audience_freeze_router)
    app.state.audience_freeze_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


PATH = "/v2/commands/freeze-campaign-audience"


def test_a_viewer_may_not_freeze_and_a_key_is_required() -> None:
    repo = _FakeRepo()
    r = _client(repo, role="viewer").post(PATH, json=BODY, headers={OPERATOR_EMAIL_HEADER: "op@example.test", "Idempotency-Key": "k"})
    assert r.status_code == 403
    r = _client(repo).post(PATH, json=BODY, headers={OPERATOR_EMAIL_HEADER: "op@example.test"})
    assert r.status_code == 400 and repo.calls == []
    r = _client(repo).post(PATH, json=BODY, headers={OPERATOR_EMAIL_HEADER: "op@example.test", "Idempotency-Key": "k"})
    assert r.status_code == 200 and repo.calls[0]["command_name"] == FREEZE_CAMPAIGN_AUDIENCE


def test_the_freeze_router_exposes_one_post_and_mounts_only_behind_its_switch() -> None:
    from fastapi import FastAPI

    from origenlab_api import main
    from origenlab_api.settings import Settings
    from origenlab_api.v2.audience_freeze_routes import audience_freeze_router

    assert [(r.path, set(r.methods)) for r in audience_freeze_router.routes] == [(PATH, {"POST"})]

    def paths(**kw):
        app = FastAPI()
        main._mount_audience_freeze(app, Settings(_env_file=None, **kw), "postgresql://x@127.0.0.1/db", _never_connects)
        return app.state.audience_freeze_enabled, set(app.openapi()["paths"])

    for kw in ({"v2_database_url": LOOPBACK}, {"v2_database_url": LOOPBACK, "v2_commands_enabled": True},
               {"v2_database_url": LOOPBACK, "v2_campaign_drafts_enabled": True}):
        enabled, routes = paths(**kw)
        assert enabled is False and PATH not in routes
    enabled, routes = paths(v2_database_url=LOOPBACK, v2_audience_freeze_enabled=True)
    assert enabled is True and PATH in routes


def test_w12_rides_on_the_freeze_switch_and_defaults_off() -> None:
    from fastapi import FastAPI

    from origenlab_api import main
    from origenlab_api.settings import Settings

    def state(**kw):
        app = FastAPI()
        main._mount_audience_freeze(app, Settings(_env_file=None, **kw), "postgresql://x@127.0.0.1/db", _never_connects)
        repo = getattr(app.state, "audience_freeze_repository", None)
        return app.state.recontact_review_enabled, repo and repo._recontact_review

    assert state(v2_database_url=LOOPBACK, v2_audience_freeze_enabled=True) == (False, False)
    assert state(v2_database_url=LOOPBACK, v2_recontact_review_enabled=True) == (False, None)
    assert state(v2_database_url=LOOPBACK, v2_audience_freeze_enabled=True, v2_recontact_review_enabled=True) == (True, True)


def test_no_send_capability_exists_anywhere_in_the_api() -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "origenlab_api"
    text = "\n".join(p.read_text(encoding="utf-8") for p in src.rglob("*.py"))
    # The one sanctioned sender: «Enviar prueba», one admin test to one address (switch-gated).
    text = text.replace("/send-campaign-test", "")
    for forbidden in ("googleapiclient", "gmail.users().messages().send", "smtplib", "/send-campaign",
                      "send_one(", "reserve_attempts("):
        assert forbidden not in text, forbidden


# --------------------------------------------------------------------------- database (opt-in)


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def world(disposable_database):
    import json

    import psycopg

    tag = uuid.uuid4().hex[:8]
    s: dict[str, str] = {"tag": tag}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, 'Freeze Op', 'sales', 'active') returning id::text",
                    (f"freeze-{tag}@example.test",))
        s["op"] = cur.fetchone()[0]
        cur.execute("insert into comms.mailbox (address_norm) values (%s)", (f"ventas-{tag}@example.invalid",))
        for key, kind, name in (("uni", "institution", f"Universidad Ficticia {tag}"),
                                ("sup", "company", f"Fabricante Ficticio {tag}")):
            cur.execute("insert into crm.organization (kind, name, confirmation) values (%s, %s, 'confirmed') "
                        "returning id::text", (kind, name))
            s[key] = cur.fetchone()[0]
        cur.execute("insert into crm.organization_relationship (organization_id, role, valid_from) "
                    "values (%s, 'supplier', '2024-01-01')", (s["sup"],))
        cur.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) "
                    "values (%s, %s, 'exclusive')", (s["sup"], f"fabricante-{tag}.test"))
        payload = {
            "subject_raw": "Cotización 01024-26 sonicador",
            "documents": [{"filename": "COT 01024-26 UP200St.pdf"}],
            "recipients": (f"Ana <ana-{tag}@uni.test>, baja-{tag}@uni.test, ventas@fabricante-{tag}.test, "
                           f"prev-{tag}@uni.test, nuevo-{tag}@uni.test"),
            "sent_at": "2026-03-12T10:00:00+00:00",
        }
        cur.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('gmail_message', %s, %s) "
                    "returning id::text", (f"frz:{tag}", json.dumps(payload)))
        s["sr"] = cur.fetchone()[0]
        with conn.transaction():
            cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                        "values ('Cotización 01024-26', 'lead', %s, %s) returning id::text", (s["op"], s["uni"]))
            s["case"] = cur.fetchone()[0]
            cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                        "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                        "current_date, 'confirmed', %s)", (s["case"], s["uni"], s["op"]))
        cur.execute("insert into crm.quote (opportunity_id, quote_number, number_origin) "
                    "values (%s, '01024-26', 'printed_historical') returning id::text", (s["case"],))
        quote = cur.fetchone()[0]
        cur.execute("insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at, "
                    "origin_source_record_id) values (%s, 1, 'sent', 'historical_import', %s, "
                    "'2026-03-12T10:00:00+00:00', %s)", (quote, "a" * 64, s["sr"]))
        for who in ("ana", "baja", "prev"):
            cur.execute("insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                        "values ('email', %s, %s, 'unattributed', 'machine_proposed')",
                        (f"{who}-{tag}@uni.test", f"{who}-{tag}@uni.test"))
        cur.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                    "values ('address', %s, 'block', 'all', 'pidió no recibir correos', 'operator_command')",
                    (f"baja-{tag}@uni.test",))
        cur.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                    "values ('address', %s, 'prior_contact', 'marketing', 'contacto histórico', 'operator_command')",
                    (f"prev-{tag}@uni.test",))
    s["operator"] = OperatorIdentity(operator_id=s["op"], email_norm=f"freeze-{tag}@example.test",
                                     display_name="Freeze Op", role="sales", status="active")
    return s


def _connect(dsn):
    import psycopg

    return psycopg.connect(runtime_dsn(dsn), autocommit=True)


def _draft(dsn, w, name, html="<p>Hola</p><p>Conozca el UP200St.</p>", duplicated_from=None):
    import psycopg

    from origenlab_api.v2.campaign_drafts import (
        CREATE_CAMPAIGN_DRAFT,
        CreateCampaignDraftBody,
        V2CampaignDraftRepository,
        validated_draft,
    )

    body = CreateCampaignDraftBody(name=name, subject="Sonicadores Hielscher", preheader="UP200St",
                                   body_html=html, max_sends=50, recontact_interval_days=90,
                                   duplicated_from_campaign_id=duplicated_from)
    return V2CampaignDraftRepository(psycopg.connect, runtime_dsn(dsn)).execute(
        command_name=CREATE_CAMPAIGN_DRAFT, operator=w["operator"], fields=validated_draft(CREATE_CAMPAIGN_DRAFT, body),
        idempotency_key=uuid.uuid4().hex, digest=request_digest(CREATE_CAMPAIGN_DRAFT, body))


def _preview(dsn, cid):
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    return CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn)).freeze_preview(cid, HIELSCHER)


def _freeze(dsn, w, cid, preview, decisions=(), key=None, version=1):
    import psycopg

    from origenlab_api.v2.audience_freeze import V2AudienceFreezeRepository

    body = FreezeAudienceBody(campaign_id=cid, expected_version=version, expected_preview_sha256=preview["preview_sha256"],
                              criteria=HIELSCHER, review_decisions=list(decisions), confirmed=True)
    return V2AudienceFreezeRepository(psycopg.connect, runtime_dsn(dsn)).execute(
        command_name=FREEZE_CAMPAIGN_AUDIENCE, operator=w["operator"], fields=freeze_fields(body),
        idempotency_key=key or uuid.uuid4().hex, digest=request_digest(FREEZE_CAMPAIGN_AUDIENCE, body))


def _decide_all(preview, decision="include"):
    return [ReviewDecision(key=k, decision=decision, note="revisado en la prueba") for k in preview["review_pending"]]


def _count(dsn, table):
    with _connect(dsn) as conn:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608 - fixed names


@needs_db
def test_a_freeze_writes_one_immutable_snapshot_and_one_event(disposable_database, world) -> None:
    import psycopg

    tag = world["tag"]
    cid = _draft(disposable_database, world, f"Hielscher {tag}")["campaign_id"]
    attempts_before = _count(disposable_database, "outbound.send_attempt")
    controls_before = _count(disposable_database, "outbound.contact_control")
    preview = _preview(disposable_database, cid)
    by_addr = _rows(preview)
    assert by_addr[f"nuevo-{tag}@uni.test"]["review_codes"] == ["no_contact_point"]
    with pytest.raises(CommandRefused) as err:
        _freeze(disposable_database, world, cid, preview)
    assert err.value.code == "review_pending"

    out = _freeze(disposable_database, world, cid, preview, decisions=_decide_all(preview))
    assert out["status"] == "audience_frozen" and out["version"] == 2
    assert out["policy_version"] == "marketing-audience/2026-09-27.v1"
    assert out["send_blockers"][0]["code"] == "unsubscribe_sync_not_automatic"

    with _connect(disposable_database) as conn:
        rows = {r[0]: r[1:] for r in conn.execute(
            "select address_norm, frozen_inclusion, frozen_reasons, relevance, content_sha256, policy_version, "
            "campaign_version, state, evidence_observed_at is not null, identity_review is not null "
            "from outbound.campaign_recipient where campaign_id = %s", (cid,)).fetchall()}
        campaign = conn.execute("select status, content_sha256, audience_sha256, audience_criteria->>'brand_id', "
                                "body_text from outbound.campaign where id = %s", (cid,)).fetchone()
        events = conn.execute("select event_type, payload from crm.domain_event where aggregate_kind = 'campaign' "
                              "and aggregate_id = %s order by seq", (cid,)).fetchall()
    assert campaign[0] == "audience_frozen" and campaign[1] == out["content_sha256"] and campaign[3] == "hielscher"
    assert campaign[4] == "Hola\n\nConozca el UP200St."
    assert rows[f"ana-{tag}@uni.test"][:3] == ("included", [], "evidenced")
    assert rows[f"baja-{tag}@uni.test"][:2] == ("excluded", ["block"])
    assert rows[f"ventas@fabricante-{tag}.test"][:2] == ("excluded", ["policy_supplier"])
    assert rows[f"prev-{tag}@uni.test"][:2] == ("excluded", ["prior_contact"])
    assert rows[f"nuevo-{tag}@uni.test"][0] == "included" and rows[f"nuevo-{tag}@uni.test"][8] is True
    for r in rows.values():
        assert r[3:6] == (out["content_sha256"], out["policy_version"], 2) and r[7] is True
    assert [e[0] for e in events] == ["campaign.draft_created", "campaign.audience_frozen"]
    assert events[1][1]["audience_sha256"] == out["audience_sha256"] == campaign[2]
    # A freeze sends nothing and records no contact.
    assert _count(disposable_database, "outbound.send_attempt") == attempts_before
    assert _count(disposable_database, "outbound.contact_control") == controls_before

    # Never mutated: not by the commands, not by a writer that skips them.
    with pytest.raises(CommandRefused) as err:
        _freeze(disposable_database, world, cid, preview, decisions=_decide_all(preview), version=2)
    assert err.value.code == "campaign_not_draft"
    with _connect(disposable_database) as conn:
        for sql in ("update outbound.campaign set audience_criteria = '{\"version\": 1}' where id = %s",
                    "update outbound.campaign set subject = 'otro' where id = %s",
                    "update outbound.campaign set status = 'draft' where id = %s",
                    "update outbound.campaign_recipient set frozen_inclusion = 'excluded', frozen_reasons = '{manual_hold}' "
                    "where campaign_id = %s and frozen_inclusion = 'included'",
                    "update outbound.campaign_recipient set relevance = 'sin_informacion', interest_evidence = '[]', "
                    "evidence_observed_at = null where campaign_id = %s",
                    ):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, (cid,))
        # The runtime role holds no DELETE at all; the trigger would refuse it anyway (pgTAP 066).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from outbound.campaign_recipient where campaign_id = %s", (cid,))


@needs_db
def test_the_snapshot_read_returns_what_was_frozen(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    cid = _draft(disposable_database, world, f"Lectura {world['tag']}")["campaign_id"]
    preview = _preview(disposable_database, cid)
    out = _freeze(disposable_database, world, cid, preview, decisions=_decide_all(preview, "exclude"))
    snap = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database)).frozen_recipients(cid)
    assert snap["audience_sha256"] == out["audience_sha256"] and snap["status"] == "audience_frozen"
    assert len(snap["recipients"]) == out["counts"]["rows"]
    held = [r for r in snap["recipients"] if r["identity_review"]]
    assert held and all(r["frozen_reasons"] == ["manual_hold"] for r in held)
    assert all(r["policy_version"] == out["policy_version"] for r in snap["recipients"])


@needs_db
def test_a_changed_audience_is_refused_and_a_changed_draft_is_a_new_freeze(disposable_database, world) -> None:
    tag = world["tag"]
    cid = _draft(disposable_database, world, f"Hielscher v1 {tag}")["campaign_id"]
    preview = _preview(disposable_database, cid)
    # Someone blocks an address after the operator looked: the confirmed audience no longer exists.
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        conn.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                     "values ('address', %s, 'block', 'marketing', 'baja', 'operator_command')", (f"nuevo-{tag}@uni.test",))
    with pytest.raises(CommandRefused) as err:
        _freeze(disposable_database, world, cid, preview, decisions=_decide_all(preview))
    assert err.value.code == "audience_changed"
    assert _count(disposable_database, f"outbound.campaign_recipient where campaign_id = '{cid}'") == 0

    fresh = _preview(disposable_database, cid)
    first = _freeze(disposable_database, world, cid, fresh, decisions=_decide_all(fresh), key=f"k-{tag}")
    again = _freeze(disposable_database, world, cid, fresh, decisions=_decide_all(fresh), key=f"k-{tag}")
    assert again["replayed"] is True and again["audience_sha256"] == first["audience_sha256"]

    # The operator changes the copy: a new draft duplicated from the frozen one, frozen on its own.
    cid2 = _draft(disposable_database, world, f"Hielscher v2 {tag}", html="<p>Hola de nuevo</p>",
                  duplicated_from=cid)["campaign_id"]
    p2 = _preview(disposable_database, cid2)
    second = _freeze(disposable_database, world, cid2, p2, decisions=_decide_all(p2))
    assert second["content_sha256"] != first["content_sha256"]
    with _connect(disposable_database) as conn:
        statuses = dict(conn.execute("select id::text, status from outbound.campaign where id in (%s, %s)", (cid, cid2)).fetchall())
        n1 = conn.execute("select count(*) from outbound.campaign_recipient where campaign_id = %s", (cid,)).fetchone()[0]
    assert statuses == {cid: "audience_frozen", cid2: "audience_frozen"}
    assert n1 == first["counts"]["rows"]  # the first snapshot did not move


def _freeze_w12(dsn, w, cid, preview, *, recontact=(), decisions=(), enabled=True, key=None):
    import psycopg

    from origenlab_api.v2.audience_freeze import V2AudienceFreezeRepository

    body = FreezeAudienceBody(campaign_id=cid, expected_version=1, expected_preview_sha256=preview["preview_sha256"],
                              criteria=HIELSCHER, review_decisions=list(decisions),
                              recontact_decisions=list(recontact), confirmed=True)
    return V2AudienceFreezeRepository(psycopg.connect, runtime_dsn(dsn), recontact_review_enabled=enabled).execute(
        command_name=FREEZE_CAMPAIGN_AUDIENCE, operator=w["operator"], fields=freeze_fields(body),
        idempotency_key=key or uuid.uuid4().hex, digest=request_digest(FREEZE_CAMPAIGN_AUDIENCE, body))


@needs_db
def test_w12_is_recomputed_at_freeze_and_frozen_with_the_snapshot(disposable_database, world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    tag = world["tag"]
    prev, baja = f"prev-{tag}@uni.test", f"baja-{tag}@uni.test"
    with psycopg.connect(disposable_database, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        # An archived V1 campaign that reached prev (no attempt, so no date), and a prior contact on
        # the blocked address, which W12 must never lift.
        v1 = conn.execute("insert into outbound.campaign (name, mailbox_id, max_sends, recontact_interval_days, status) "
                          "select %s, id, 10, 90, 'archived' from comms.mailbox where address_norm = %s returning id::text",
                          (f"Campaña V1 {tag}", f"ventas-{tag}@example.invalid")).fetchone()[0]
        conn.execute("insert into outbound.campaign_recipient (campaign_id, address_norm, state) values (%s, %s, 'sent')", (v1, prev))
        conn.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                     "values ('address', %s, 'prior_contact', 'marketing', 'contacto histórico', 'operator_command')", (baja,))
    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    cid = _draft(disposable_database, world, f"W12 {tag}")["campaign_id"]
    preview = repo.freeze_preview(cid, HIELSCHER, recontact_review=True)
    rows = _rows(preview)
    assert preview["policy_version"] == AUDIENCE_POLICY_VERSION_W12
    assert rows[prev]["recontact_review_required"] is True and rows[baja]["recontact_review_required"] is False
    shown = rows[prev]["prior_contact"]
    assert shown["destination"] == prev and shown["campaign_name"] == f"Campaña V1 {tag}" and shown["last_contact_at"] is None
    assert [s["source"] for s in shown["sources"]] == ["operator_command"]
    decisions = _decide_all(preview)
    approve = RecontactDecision(key=rows[prev]["key"], decision="approve", note="Pidió la ficha del UP200St", mode="bulk")

    # Revalidated in the command: W12 off refuses, a block is never lifted, a preview taken under
    # the other policy is refused — and none of it writes anything.
    for kwargs, code in (({"enabled": False}, "recontact_review_disabled"),
                         ({"recontact": [approve, RecontactDecision(key=rows[baja]["key"], decision="approve", note="insistir")]},
                          "recontact_not_eligible")):
        with pytest.raises(CommandRefused) as err:
            _freeze_w12(disposable_database, world, cid, preview, decisions=decisions,
                        recontact=kwargs.pop("recontact", [approve]), **kwargs)
        assert err.value.code == code
    v1_preview = repo.freeze_preview(cid, HIELSCHER, recontact_review=False)
    with pytest.raises(CommandRefused) as err:
        _freeze_w12(disposable_database, world, cid, v1_preview, decisions=_decide_all(v1_preview))
    assert err.value.code == "audience_changed"
    assert _count(disposable_database, f"outbound.campaign_recipient where campaign_id = '{cid}'") == 0

    out = _freeze_w12(disposable_database, world, cid, preview, decisions=decisions, recontact=[approve])
    assert out["policy_version"] == AUDIENCE_POLICY_VERSION_W12
    assert out["recontact_review"]["approved"] == 1 and out["recontact_review"]["bulk"] == 1
    with _connect(disposable_database) as conn:
        r = conn.execute("select frozen_inclusion, frozen_reasons, frozen_notes, recontact_review, recontact_override_by_operator_id::text, "
                         "recontact_override_reason, recontact_override_at is not null, policy_version, id::text "
                         "from outbound.campaign_recipient where campaign_id = %s and address_norm = %s", (cid, prev)).fetchone()
        b = conn.execute("select frozen_reasons, recontact_review, recontact_override_at from outbound.campaign_recipient "
                         "where campaign_id = %s and address_norm = %s", (cid, baja)).fetchone()
        events = conn.execute("select event_type, payload from crm.domain_event where aggregate_kind = 'campaign' "
                              "and aggregate_id = %s order by seq", (cid,)).fetchall()
        policy = conn.execute("select audience_policy_version from outbound.campaign where id = %s", (cid,)).fetchone()[0]
    assert r[:3] == ("included", [], ["recontact_approved"])
    assert r[3]["decision"] == "approve" and r[3]["mode"] == "bulk" and r[3]["operator_id"] == world["op"]
    assert r[3]["policy_version"] == RECONTACT_POLICY_VERSION and r[3]["prior_contact"]["campaign_name"] == f"Campaña V1 {tag}"
    assert r[4:7] == (world["op"], "Pidió la ficha del UP200St", True) and r[7] == policy == AUDIENCE_POLICY_VERSION_W12
    assert set(b[0]) == {"block", "prior_contact"} and b[1] is None and b[2] is None
    assert [e[0] for e in events] == ["campaign.draft_created", "campaign.audience_frozen", "campaign.override_granted"]
    assert events[1][1]["recontact_review"]["approved"] == 1
    assert events[2][1]["recipient_id"] == r[8] and events[2][1]["lifted"] == ["prior_contact"]

    # Sealed: no rewrite, no withdrawal, no grant after the freeze — by any writer.
    with _connect(disposable_database) as conn:
        for sql in ("update outbound.campaign_recipient set recontact_review = '{}'::jsonb where campaign_id = %s and address_norm = '" + prev + "'",
                    "update outbound.campaign_recipient set recontact_override_by_operator_id = null, recontact_override_reason = null, "
                    "recontact_override_at = null where campaign_id = %s and address_norm = '" + prev + "'"):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, (cid,))
    snap = repo.frozen_recipients(cid)
    by = {x["address"]: x for x in snap["recipients"]}
    assert by[prev]["recontact_review"]["decision"] == "approve" and by[prev]["recontact_override_at"]
