"""Equipment-interest audiences: relevance with its evidence, eligibility kept separate.

Pure: `compose` and `apply_filter` over hand-built inputs. Every institution, address and
domain is fictitious (`.test` / `.invalid`).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from origenlab_api.v2.equipment_taxonomy import load_taxonomy
from origenlab_api.v2.marketing_audience import (
    AudienceFilter,
    AudienceInputs,
    CaseFacts,
    EligibilityFacts,
    addresses_in,
    apply_filter,
    compose,
    eligibility,
)

T = datetime(2026, 3, 10, tzinfo=timezone.utc)


def _case(cid: str, org: str | None, **kw) -> CaseFacts:
    return CaseFacts(
        opportunity_id=cid, title=kw.pop("title", f"Caso {cid}"), stage=kw.pop("stage", "lead"),
        created_at=T, organization_id=org, organization_name=f"Instituto {org}" if org else None, **kw,
    )


def _inputs(**kw) -> AudienceInputs:
    return AudienceInputs(
        cases=kw.pop("cases", {}), interests=kw.pop("interests", []),
        quotation_evidence=kw.pop("evidence", []), eligibility=kw.pop("facts", EligibilityFacts()), **kw,
    )


def _evidence(sr: str, case: str | None, subject: str, recipients: str, files=()) -> dict:
    return {"source_record_id": sr, "opportunity_id": case, "subject": subject,
            "filenames": list(files), "sent_at": "2026-03-12T10:00:00+00:00", "recipients": recipients}


def test_quotation_evidence_is_a_person_and_an_institution_interest_not_recorded_in_crm() -> None:
    out = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", first_sent_at=T, quote_numbers=["01024-26"])},
        evidence=[_evidence("sr1", "c1", "Cotización 01024-26", "Ana <ana@lab.test>", ["COT UP200St.pdf"])],
    ))
    [inst] = out["institutions"]
    [person] = out["persons"]
    for row in (inst, person):
        [it] = row["interests"]
        assert (it["brand_id"], it["model_id"]) == ("hielscher", "hielscher-up200st")
        assert it["basis"] == "requested_quotation" and it["basis_label"] == "Pidió cotización"
        assert it["recorded_in_crm"] is False
        assert it["source"]["kind"] == "quotation_evidence"
        assert it["source"]["source_record_id"] == "sr1"
        assert it["source"]["opportunity_id"] == "c1"
        assert it["date"] == "2026-03-12T10:00:00+00:00"
        assert it["source"]["detail"] == "COT UP200St.pdf"  # where the model was actually named
        assert "_in" not in it
    assert person["address"] == "ana@lab.test"
    assert person["organization_ids"] == ["org1"]
    assert out["coverage"]["brand_linkage"]["hielscher"] == {"crm": 0, "evidence_only": 1}


@pytest.mark.parametrize(
    ("stage", "sent", "confirmation", "basis"),
    [
        ("won", T, "confirmed", "purchased"),
        ("negotiating", T, "machine_proposed", "requested_quotation"),
        ("qualifying", None, "confirmed", "requested_information"),
        ("lead", None, "machine_proposed", "inferred_relevance"),
    ],
)
def test_a_recorded_interest_takes_its_basis_from_what_happened_on_the_case(stage, sent, confirmation, basis) -> None:
    closed = T if stage == "won" else None
    out = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", stage=stage, first_sent_at=sent, closed_at=closed)},
        interests=[{"interest_id": "i1", "opportunity_id": "c1", "confirmation": confirmation,
                    "created_at": T, "texts": ["Osmometer basic"],
                    "participants": [{"address": "jefe@lab.test"}]}],
    ))
    [inst] = out["institutions"]
    [it] = inst["interests"]
    assert it["basis"] == basis
    assert it["recorded_in_crm"] is True and it["confirmation"] == confirmation
    assert it["source"]["interest_id"] == "i1"
    # The participant is the person-level holder of the same interest.
    [person] = out["persons"]
    assert person["address"] == "jefe@lab.test" and person["interests"][0]["basis"] == basis


def test_evidence_already_recorded_as_an_interest_is_shown_once() -> None:
    out = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", first_sent_at=T)},
        interests=[{"interest_id": "i1", "opportunity_id": "c1", "confirmation": "confirmed", "created_at": T,
                    "texts": ["UP200St"], "origin_source_record_id": "sr1", "participants": []}],
        evidence=[_evidence("sr1", "c1", "UP200St", "ana@lab.test")],
    ))
    [inst] = out["institutions"]
    assert [i["source"]["kind"] for i in inst["interests"]] == ["crm_interest"]
    assert out["persons"] == []  # the recipient came only from the evidence already recorded
    assert out["coverage"]["quotation_evidence_already_recorded"] == 1


def test_person_and_institution_interests_stay_distinct() -> None:
    """A case title names the institution's interest; it is not any person's."""
    out = compose(load_taxonomy(), _inputs(cases={"c1": _case("c1", "org1", title="Solicitud via Hielscher Ultrasonics")}))
    [inst] = out["institutions"]
    assert inst["interests"][0]["source"]["kind"] == "case_title"
    assert inst["interests"][0]["basis"] == "inferred_relevance"
    assert out["persons"] == []


def test_a_brand_mention_yields_to_a_model_named_in_the_same_record() -> None:
    out = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", first_sent_at=T)},
        evidence=[_evidence("sr1", "c1", "Cotización osmómetro Löser", "ana@lab.test", ["COT i Osmometer basic.pdf"])],
    ))
    [inst] = out["institutions"]
    assert [(i["brand_id"], i["model_id"]) for i in inst["interests"]] == [("loeser", "loeser-i-osmometer-basic")]


def test_a_case_title_mention_is_not_an_interest_recorded_in_the_crm() -> None:
    out = compose(load_taxonomy(), _inputs(cases={"c1": _case("c1", "org1", title="Solicitud UP200St")}))
    [it] = out["institutions"][0]["interests"]
    assert it["source"]["kind"] == "case_title" and it["recorded_in_crm"] is False


def test_a_person_can_hold_several_interests() -> None:
    out = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", first_sent_at=T), "c2": _case("c2", "org1", first_sent_at=T)},
        evidence=[_evidence("sr1", "c1", "UP200St", "ana@lab.test"),
                  _evidence("sr2", "c2", "Osmometer basic", "ana@lab.test")],
    ))
    [person] = out["persons"]
    assert {i["brand_id"] for i in person["interests"]} == {"hielscher", "loeser"}


def test_no_evidence_means_absent_never_a_low_score() -> None:
    out = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", title="Consulta general")},
        evidence=[_evidence("sr1", "c1", "Saludos", "ana@lab.test")],
    ))
    assert out["persons"] == [] and out["institutions"] == []
    for row in out["persons"] + out["institutions"]:
        assert "score" not in row


# --------------------------------------------------------------------------- eligibility


def test_suppliers_suppressed_and_invalid_destinations_are_excluded_with_reasons() -> None:
    facts = EligibilityFacts(
        blocked_addresses={"baja@lab.test"},
        blocked_domains={"bloqueado.test"},
        invalid_addresses={"rebote@lab.test"},
        supplier_domains={"fabricante.test"},
        candidate_supplier_domains={"quizas-proveedor.test"},
        cooldown_addresses={"espera@lab.test"},
        contact_points={"ok@lab.test": {"id": "cp1", "address": "ok@lab.test", "usage": "unattributed"}},
    )
    expect = {
        "ok@lab.test": [],
        "baja@lab.test": ["blocked_address"],
        "x@bloqueado.test": ["blocked_domain"],
        "rebote@lab.test": ["invalid_address"],
        "ventas@fabricante.test": ["supplier"],
        "a@quizas-proveedor.test": ["possible_supplier_unreviewed"],
        "espera@lab.test": ["cooldown"],
        "sin-arroba": ["invalid_address"],
    }
    for address, reasons in expect.items():
        e = eligibility(address, facts)
        assert [r["code"] for r in e["reasons"]] == reasons, address
        assert e["eligible"] is (not reasons)
    assert [n["code"] for n in eligibility("nuevo@lab.test", facts)["notes"]] == ["no_contact_point"]


def test_an_organization_recorded_as_supplier_excludes_its_addresses() -> None:
    facts = EligibilityFacts(supplier_organization_ids={"org-sup"})
    assert [r["code"] for r in eligibility("a@x.test", facts, "org-sup")["reasons"]] == ["supplier"]


def test_relevance_never_overrides_eligibility_and_recipients_are_deduplicated() -> None:
    facts = EligibilityFacts(blocked_addresses={"baja@lab.test"})
    composed = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", first_sent_at=T), "c2": _case("c2", "org2", first_sent_at=T)},
        evidence=[
            _evidence("sr1", "c1", "UP200St", "ana@lab.test, baja@lab.test"),
            _evidence("sr2", "c2", "UP400St", "ANA@lab.test"),
        ],
        facts=facts,
    ))
    out = apply_filter(composed, AudienceFilter(brand_id="hielscher"))
    by_address = {p["address"]: p for p in out["persons"]}
    assert by_address["baja@lab.test"]["eligibility"]["eligible"] is False
    assert len(by_address["ana@lab.test"]["interests"]) == 2  # two cases, one person
    s = out["sending"]
    assert s["unique_destinations"] == 2
    assert s["eligible_unique_destinations"] == 1
    assert s["excluded_by_reason"] == [{"code": "blocked_address", "label": "Dirección bloqueada", "count": 1}]


# --------------------------------------------------------------------------- filtering


def test_filters_narrow_by_family_brand_model_basis_institution_and_recording() -> None:
    composed = compose(load_taxonomy(), _inputs(
        cases={"c1": _case("c1", "org1", first_sent_at=T), "c2": _case("c2", "org2", stage="won", closed_at=T, first_sent_at=T)},
        evidence=[_evidence("sr1", "c1", "UP200St", "ana@lab.test"),
                  _evidence("sr2", "c2", "T 25 digital", "luis@otro.test")],
    ))

    def insts(**kw):
        return {i["organization_id"] for i in apply_filter(composed, AudienceFilter(**kw))["institutions"]}

    assert insts(family_id="sonicacion") == {"org1"}
    assert insts(brand_id="ika") == {"org2"}
    assert insts(model_id="ika-t25-digital") == {"org2"}
    assert insts(model_id="ika-t18-digital") == set()
    assert insts(bases=("purchased",)) == {"org2"}
    assert insts(organization_id="org1") == {"org1"}
    assert insts(recorded="crm") == set()
    assert insts(recorded="evidence") == {"org1", "org2"}
    assert insts(q="instituto org2") == {"org2"}


def test_recipient_headers_are_parsed_once_per_address() -> None:
    assert addresses_in('"Ana" <Ana@Lab.test>, luis@otro.test; ana@lab.test') == ["ana@lab.test", "luis@otro.test"]
    assert addresses_in(None) == []


# --------------------------------------------------------------------------- database (opt-in)
#
# The SQL behind `marketing_audience_inputs`, read as the runtime role from a disposable
# `origenlab_test_<hex>` holding a fictitious world. Skips without the test DSNs.

import uuid  # noqa: E402

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def audience_world(disposable_database):
    import json

    import psycopg

    tag = uuid.uuid4().hex[:8]
    s: dict[str, str] = {}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, 'Aud Op', 'sales', 'active') returning id::text",
                    (f"aud-{tag}@example.test",))
        s["op"] = cur.fetchone()[0]
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
            "recipients": f"Ana <ana-{tag}@uni.test>, baja-{tag}@uni.test, ventas@fabricante-{tag}.test",
            "sent_at": "2026-03-12T10:00:00+00:00",
        }
        cur.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('gmail_message', %s, %s) "
                    "returning id::text", (f"aud:{tag}", json.dumps(payload)))
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
        cur.execute("insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                    "values ('email', %s, %s, 'unattributed', 'machine_proposed')",
                    (f"ana-{tag}@uni.test", f"ana-{tag}@uni.test"))
        cur.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                    "values ('address', %s, 'block', 'all', 'pidió no recibir correos', 'operator_command')",
                    (f"baja-{tag}@uni.test",))
    s["tag"] = tag
    return s


@needs_db
def test_the_audience_read_finds_evidence_only_interest_and_excludes_as_it_should(disposable_database, audience_world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    w = audience_world
    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    composed = compose(load_taxonomy(), repo.marketing_audience_inputs())
    out = apply_filter(composed, AudienceFilter(model_id="hielscher-up200st"))

    [inst] = [i for i in out["institutions"] if i["organization_id"] == w["uni"]]
    [it] = inst["interests"]
    assert it["basis"] == "requested_quotation" and it["recorded_in_crm"] is False
    assert it["source"]["source_record_id"] == w["sr"] and it["source"]["quote_numbers"] == ["01024-26"]
    assert it["date"].startswith("2026-03-12")

    people = {p["address"]: p for p in out["persons"]}
    tag = w["tag"]
    assert people[f"ana-{tag}@uni.test"]["eligibility"]["eligible"] is True
    assert [r["code"] for r in people[f"baja-{tag}@uni.test"]["eligibility"]["reasons"]] == ["blocked_address"]
    assert [r["code"] for r in people[f"ventas@fabricante-{tag}.test"]["eligibility"]["reasons"]] == ["supplier"]
    assert out["sending"]["eligible_unique_destinations"] == 1
    assert composed["coverage"]["crm_interest_rows"] == 0


@needs_db
def test_the_card_index_splits_crm_people_from_address_only_evidence(disposable_database, audience_world) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
    from origenlab_api.v2.equipment_interests import interest_index
    from origenlab_api.v2.marketing_audience import address_ref

    w = audience_world
    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    taxonomy = load_taxonomy()
    idx = interest_index(taxonomy, compose(taxonomy, repo.marketing_audience_inputs()))

    people = {p["address"]: p for p in idx["persons"]}
    tag = w["tag"]
    ana = people[f"ana-{tag}@uni.test"]
    # A contact point with no person is still address-level: no CRM person is invented.
    assert ana["link"] == "address_only" and ana["contact_point_id"] and ana["person_id"] is None
    assert ana["address_ref"] == address_ref(f"ana-{tag}@uni.test")
    assert people[f"baja-{tag}@uni.test"]["contact_point_id"] is None
    [inst] = [i for i in idx["institutions"] if i["organization_id"] == w["uni"]]
    assert {i["family_id"] for i in inst["interests"]} == {"sonicacion"}
    assert [l["family_id"] for l in idx["lines"]] == [f["id"] for f in taxonomy.data["families"]]
