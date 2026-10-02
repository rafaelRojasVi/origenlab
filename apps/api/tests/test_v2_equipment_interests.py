"""The supplier directory and the equipment-interest index for the CRM cards.

Pure functions over hand-built inputs, plus the two routes over stub repositories. Every
institution, address and domain is fictitious (`.test` / `.invalid`).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.equipment_interests import interest_index, supplier_directory
from origenlab_api.v2.equipment_taxonomy import load_taxonomy
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity
from origenlab_api.v2 import marketing_audience
from origenlab_api.v2.marketing_audience import (
    AudienceInputs,
    CaseFacts,
    EligibilityFacts,
    address_ref,
    compose,
    configure_address_ref_key,
    destination_key,
)

T = datetime(2026, 3, 10, tzinfo=timezone.utc)
TAX = load_taxonomy()

CANONICAL = ["hielscher", "ortoalresa", "ika", "adam-equipment", "loeser", "serva"]


# ─────────────────────────────────────────────────────────── directory ──


def test_the_directory_is_the_six_catalogue_brands_in_order() -> None:
    directory = supplier_directory(TAX, [], [])
    assert [d["brand_id"] for d in directory] == CANONICAL
    # One line per brand, and the lines are the taxonomy's own families — no second list.
    assert [d["family"]["id"] for d in directory] == [b["family_id"] for b in TAX.data["brands"]]
    assert {d["family"]["id"] for d in directory} == set(TAX.families)
    for d in directory:
        assert d["crm_organizations"] == [] and d["candidate_hints"] == []
        assert d["model_count"] >= 1


def _candidate(assertion_id: str, domain: str, trade_name: str | None, *, state: str = "unresolved") -> dict[str, Any]:
    """One machine candidate exactly as `V2CrmWorkspaceRepository.providers()` returns it."""
    return {
        "assertion_id": assertion_id,
        "domain": domain,
        "trade_name": trade_name,
        "review": {"state": state, "decided_at": None, "note": None},
    }


def test_a_candidate_is_a_hint_beside_its_brand_never_promoted() -> None:
    candidates = [
        _candidate("a1", "hielscher.test", None),
        _candidate("a2", "mecanika.test", "Mecánica Ltda"),
        _candidate("a3", "otro.test", "SERVA Chile", state="rejected"),
    ]
    before = [dict(c) for c in candidates]
    directory = {d["brand_id"]: d for d in supplier_directory(TAX, [], candidates)}
    # A hint carries the candidate's review verbatim; `resolution` mirrors `review.state` so a
    # count over hints and a count over candidates read the same fact.
    hint = directory["hielscher"]["candidate_hints"][0]
    assert hint["domain"] == "hielscher.test"
    assert hint["trade_name"] is None
    assert hint["assertion_id"] == "a1"
    assert hint["review"] == {"state": "unresolved", "decided_at": None, "note": None}
    assert hint["resolution"] == "unresolved"
    assert directory["serva"]["candidate_hints"][0]["resolution"] == "rejected"
    assert [h["domain"] for h in directory["serva"]["candidate_hints"]] == ["otro.test"]
    # "IKA" does not match inside "Mecánica": word boundaries.
    assert directory["ika"]["candidate_hints"] == []
    # The candidate keeps its own review state; nothing was rewritten.
    assert candidates == before


def test_a_crm_organization_named_for_a_brand_is_shown_on_it() -> None:
    on_cases = [
        {"organization_id": "o1", "name": "IKA Works", "confirmation": "confirmed", "role": "manufacturer", "cases": 2},
        {"organization_id": "o1", "name": "IKA Works", "confirmation": "confirmed", "role": "supplier", "cases": 1},
        {"organization_id": "o2", "name": "Distribuidora Andina", "confirmation": "machine_proposed", "role": "supplier", "cases": 3},
    ]
    directory = {d["brand_id"]: d for d in supplier_directory(TAX, on_cases, [])}
    [ika] = directory["ika"]["crm_organizations"]
    assert ika["roles"] == ["manufacturer", "supplier"] and ika["cases"] == 2
    assert all(o["organization_id"] != "o2" for d in directory.values() for o in d["crm_organizations"])


# ─────────────────────────────────────────────────────────── interests ──


def _world() -> dict[str, Any]:
    cases = {
        "c1": CaseFacts(opportunity_id="c1", title="Cotización sonicador", stage="lead", created_at=T,
                        organization_id="org-uni", organization_name="Universidad Ficticia",
                        first_sent_at="2026-03-12T10:00:00+00:00", quote_numbers=["01024-26"]),
        "c2": CaseFacts(opportunity_id="c2", title="Consulta general", stage="lead", created_at=T,
                        organization_id="org-otro", organization_name="Instituto Sin Evidencia"),
    }
    evidence = [{
        "source_record_id": "sr1", "opportunity_id": "c1", "subject": "Cotización UP200St",
        "filenames": ["COT 01024-26 Biocen 22 R.pdf"], "sent_at": "2026-03-12T10:00:00+00:00",
        "recipients": "Ana <ana@uni.test>, lab@uni.test",
    }]
    facts = EligibilityFacts(contact_points={
        "ana@uni.test": {"id": "cp-ana", "address": "ana@uni.test", "person_id": "per-ana",
                         "person_name": "Ana", "organization_id": "org-uni"},
    })
    composed = compose(TAX, AudienceInputs(cases=cases, interests=[], quotation_evidence=evidence, eligibility=facts))
    return interest_index(TAX, composed)


def test_lines_are_the_six_families_with_people_split_by_crm_link() -> None:
    idx = _world()
    assert [l["family_id"] for l in idx["lines"]] == [f["id"] for f in TAX.data["families"]]
    lines = {l["family_id"]: l for l in idx["lines"]}
    for fid in ("sonicacion", "centrifugacion"):
        assert lines[fid]["crm_people"] == 1  # ana, linked to a CRM person
        assert lines[fid]["address_only"] == 1  # lab@, an address in the Gmail evidence only
        assert lines[fid]["institutions"] == 1
    assert lines["electroforesis"] == {**lines["electroforesis"], "crm_people": 0, "address_only": 0, "institutions": 0}


def test_every_interest_carries_its_source_and_date_and_several_lines_are_kept() -> None:
    idx = _world()
    people = {p["address"]: p for p in idx["persons"]}
    ana = people["ana@uni.test"]
    assert ana["link"] == "crm_person" and ana["person_id"] == "per-ana"
    assert people["lab@uni.test"]["link"] == "address_only"
    assert people["lab@uni.test"]["address_ref"] == address_ref("LAB@uni.test")
    assert {i["family_id"] for i in ana["interests"]} == {"sonicacion", "centrifugacion"}
    for it in ana["interests"]:
        assert it["source"]["kind"] == "quotation_evidence" and it["source"]["label"]
        assert it["date"].startswith("2026-03-12")
        assert it["recorded_in_crm"] is False


def test_no_evidence_means_no_entry_never_an_inferred_one() -> None:
    idx = _world()
    assert [i["organization_id"] for i in idx["institutions"]] == ["org-uni"]  # org-otro: absent


# ─────────────────────────────────────────────────────────── routes ──


class _Port(IdentityPort):
    def __init__(self, role: str | None) -> None:
        self._role = role

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._role is None:
            raise IdentityRefused("no identity")
        return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001",
                                email_norm="operator@example.invalid", display_name="Operator",
                                role=self._role, status="active")


class _Repo:
    def providers(self) -> dict[str, Any]:
        return {
            "on_cases": [{"organization_id": "o1", "name": "Löser Messtechnik", "confirmation": "confirmed",
                          "role": "manufacturer", "cases": 1}],
            "candidates": [_candidate("a-loeser", "loeser.test", None)],
        }

    def marketing_audience_inputs(self) -> AudienceInputs:
        cases = {"c1": CaseFacts(opportunity_id="c1", title="Osmómetro", stage="lead", created_at=T,
                                 organization_id="org-uni", organization_name="Universidad Ficticia",
                                 first_sent_at="2026-03-12T10:00:00+00:00")}
        evidence = [{"source_record_id": "sr1", "opportunity_id": "c1", "subject": "Cotización Löser",
                     "filenames": [], "sent_at": "2026-03-12T10:00:00+00:00", "recipients": "ana@uni.test"}]
        return AudienceInputs(cases=cases, interests=[], quotation_evidence=evidence, eligibility=EligibilityFacts())


def _client(role: str | None) -> TestClient:
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    app = FastAPI()
    app.state.v2_identity = _Port(role)
    app.state.crm_workspace = _Repo()
    app.include_router(workspace_router)
    return TestClient(app)


def test_providers_read_leads_with_the_directory_and_keeps_candidates_as_they_are() -> None:
    body = _client("sales").get("/v2/workspace/providers").json()
    assert [d["brand_id"] for d in body["directory"]] == CANONICAL
    loeser = body["directory"][4]
    assert [o["organization_id"] for o in loeser["crm_organizations"]] == ["o1"]
    assert loeser["candidate_hints"][0]["review"]["state"] == "unresolved"
    # The canonical candidate shape: `review.state` is the state; there is no top-level `resolution`.
    assert body["candidates"] == [_candidate("a-loeser", "loeser.test", None)]
    assert "resolution" not in body["candidates"][0]


@pytest.mark.parametrize("role", ["viewer", "sales"])
def test_equipment_interests_read_joins_by_address_ref_even_when_masked(role: str) -> None:
    response = _client(role).get("/v2/workspace/equipment-interests")
    assert response.status_code == 200
    body = response.json()
    [p] = body["persons"]
    assert p["address_ref"] == address_ref("ana@uni.test")
    assert p["link"] == "address_only"
    assert p["interests"][0]["family_id"] == "osmometria"
    if role == "viewer":
        assert p["address"] == "***@uni.test" and "ana@" not in response.text
        # Nothing a viewer receives is an unkeyed digest of the address they cannot see.
        for digest in _unkeyed_digests("ana@uni.test"):
            assert digest[:12] not in response.text
    else:
        assert p["address"] == "ana@uni.test"


def test_equipment_interests_read_requires_an_operator() -> None:
    client = _client(None)
    assert client.get("/v2/workspace/equipment-interests").status_code == 401
    assert client.post("/v2/workspace/equipment-interests").status_code == 405


# ─────────────────────────────────────────────────────── address refs ──


def _unkeyed_digests(address: str) -> list[str]:
    """Every digest a viewer could recompute offline from a guessed address."""
    out = []
    for form in {address, address.strip().lower()}:
        for algo in ("sha256", "sha1", "md5", "sha512", "blake2b"):
            out.append(hashlib.new(algo, form.encode("utf-8")).hexdigest())
    return out


@pytest.fixture
def restore_ref_key():
    saved = marketing_audience._address_ref_key
    yield
    marketing_audience._address_ref_key = saved


def test_an_address_ref_is_never_an_unkeyed_digest_of_the_address() -> None:
    ref = address_ref("ana@uni.test")
    assert len(ref) == 24
    for digest in _unkeyed_digests("ana@uni.test"):
        assert not digest.startswith(ref) and ref not in digest
    # An address-only destination key is the same keyed ref; a contact point keeps its UUID.
    assert destination_key("ana@uni.test", None) == f"addr:{ref}"
    assert destination_key("ana@uni.test", "cp-1") == "cp:cp-1"


def test_an_address_ref_depends_on_the_api_key_and_is_stable_under_it(restore_ref_key) -> None:
    configure_address_ref_key("a" * 48)
    under_a = address_ref("Ana@Uni.test ")
    assert under_a == address_ref("ana@uni.test")  # normalized before keying
    configure_address_ref_key("b" * 48)
    assert address_ref("ana@uni.test") != under_a  # another API key, another ref
    configure_address_ref_key("a" * 48)
    assert address_ref("ana@uni.test") == under_a  # same key, same ref across processes


def test_the_app_pins_the_ref_key_from_its_session_secret(monkeypatch: pytest.MonkeyPatch, restore_ref_key) -> None:
    """Two processes with the same secret agree on every ref; the key is not the secret itself."""
    import hmac

    from origenlab_api.main import create_app

    secret = "s" * 48
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORIGENLAB_AUTH_SESSION_SECRET", secret)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    create_app()
    pinned = address_ref("ana@uni.test")
    configure_address_ref_key(secret)
    assert address_ref("ana@uni.test") == pinned
    # Domain-separated: the session-cookie key and the ref key are different keys.
    assert pinned != hmac.new(secret.encode(), b"ana@uni.test", hashlib.sha256).hexdigest()[:24]


# ──────────────────────────────────────── the two reads the dashboard-proxy forwards ──

PROXIED = ("/v2/workspace/providers", "/v2/workspace/equipment-interests")


@pytest.mark.parametrize("path", PROXIED)
def test_a_proxied_card_read_is_get_only_and_redacts_for_a_viewer(path: str) -> None:
    from origenlab_api.v2.contact_redaction import ContactRedactingRoute
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    [route] = [r for r in workspace_router.routes if getattr(r, "path", None) == path]
    assert route.methods == {"GET"} and isinstance(route, ContactRedactingRoute)
    viewer = _client("viewer")
    assert viewer.get(path).status_code == 200
    assert "ana@uni.test" not in viewer.get(path).text
    for method in ("post", "put", "patch", "delete"):
        assert getattr(viewer, method)(path).status_code == 405, method
    assert _client(None).get(path).status_code == 401


@pytest.mark.parametrize("path", [f"{p}/x" for p in PROXIED] + ["/v2/workspace/equipment-interest"])
def test_nothing_is_mounted_beside_a_proxied_card_read(path: str) -> None:
    assert _client("admin").get(path).status_code == 404
