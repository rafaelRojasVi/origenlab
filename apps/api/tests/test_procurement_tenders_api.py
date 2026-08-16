"""GET /operator/procurement/tenders/{tender_code} — W1-gated T1 term detail.

W1's current_opportunity_queue is the sole actionability authority; T1
(ANEXO term intelligence) is additive detail only. See
services/tender_terms_service.py for the single gate enforcement point this
test module exercises.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.constants import (
    TENDER_TERMS_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.fingerprint import (
    tender_terms_semantic_digest,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.models import (
    FactCandidate,
    TenderItemTerms,
    TenderTermFact,
    TenderTermsBundle,
    TenderTermsCoverage,
    TermEvidence,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.production_publish import (
    TENDER_TERMS_PUBLICATION_CONTRACT_VERSION,
    _build_summary,  # test-only: exact reconciliation with load_published_tender_terms
)

from origenlab_api.main import create_app
from origenlab_api.settings import Settings, get_settings

from test_procurement_institutions_api import (  # noqa: E402  (path fixture reuse)
    _current_opportunity_row,
    _write_bundle,
)

_SAG_TENDER_CODE = "745712-19-LP26"
_AS_OF_UTC = "2026-08-15T00:00:00Z"


def _evidence(evidence_id: str = "ev-1", excerpt: str = "Plazo de entrega: 30 dias corridos.") -> TermEvidence:
    return TermEvidence(
        evidence_id=evidence_id,
        attachment_id="att-1",
        evidence_attachment_id="att-1",
        safe_filename="anexo.pdf",
        attachment_sha256="a" * 64,
        detected_format="pdf",
        document_role="anexo",
        chunk_id="chunk-1",
        locator_type="page_paragraph",
        locator={"page": 3, "paragraph": 2},
        locator_display="p.3 par.2",
        evidence_excerpt=excerpt,
        evidence_text_sha256="b" * 64,
    )


def _explicit_fact(field_name: str = "delivery_days") -> TenderTermFact:
    return TenderTermFact(
        fact_id=f"fact-{field_name}",
        field_name=field_name,
        state="explicit",
        coverage_status="complete",
        value=30,
        value_type="integer",
        unit="days",
        evidence=(_evidence(),),
    )


def _unknown_fact(field_name: str = "warranty_months") -> TenderTermFact:
    return TenderTermFact(
        fact_id=f"fact-{field_name}",
        field_name=field_name,
        state="unknown",
        coverage_status="incomplete",
    )


def _conflicting_fact(field_name: str = "payment_terms") -> TenderTermFact:
    return TenderTermFact(
        fact_id=f"fact-{field_name}",
        field_name=field_name,
        state="conflicting",
        coverage_status="complete",
        candidates=(
            FactCandidate(candidate_id="c1", value="30 days", evidence=(_evidence("ev-c1"),)),
            FactCandidate(candidate_id="c2", value="60 days", evidence=(_evidence("ev-c2"),)),
        ),
    )


def _complete_coverage() -> TenderTermsCoverage:
    return TenderTermsCoverage(
        has_source_bundle=True,
        extraction_complete=True,
        attachments_discovered=2,
        attachments_downloaded=2,
    )


def _incomplete_coverage() -> TenderTermsCoverage:
    return TenderTermsCoverage(
        has_source_bundle=True,
        extraction_complete=False,
        attachments_discovered=3,
        attachments_downloaded=1,
        incomplete_reason_codes=("attachment_download_incomplete",),
        unread_attachment_ids=("att-2", "att-3"),
    )


def _bundle(
    tender_id: str,
    *,
    tender_facts: tuple[TenderTermFact, ...] = (),
    items: tuple[TenderItemTerms, ...] = (),
    coverage: TenderTermsCoverage | None = None,
) -> TenderTermsBundle:
    b = TenderTermsBundle(
        tender_id=tender_id,
        source_bundle_semantic_digest="src-digest-" + tender_id,
        tender_facts=tender_facts,
        items=items,
        coverage=coverage or _complete_coverage(),
        terms_version=TENDER_TERMS_VERSION,
    )
    return dataclasses.replace(b, semantic_digest=tender_terms_semantic_digest(b))


def _write_t1_publication(dest: Path, bundles: list[TenderTermsBundle], *, as_of_utc: str = _AS_OF_UTC) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    rows = [b.to_dict() for b in bundles]
    summary = _build_summary(rows, bundles, as_of_utc=as_of_utc)
    (dest / "summary.json").write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
    (dest / "tender_terms.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _client(
    tmp_path: Path,
    *,
    w1_with_bundle: bool = True,
    w1_queue_rows: dict[str, list[dict]] | None = None,
    t1_bundles: list[TenderTermsBundle] | None = None,
    t1_publish: bool = True,
) -> TestClient:
    w1_dest = tmp_path / "institution_prospects"
    if w1_with_bundle:
        _write_bundle(w1_dest, queue_rows=w1_queue_rows)

    t1_dest = tmp_path / "tender_terms"
    if t1_publish:
        _write_t1_publication(t1_dest, t1_bundles or [])

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        institution_prospect_dir=w1_dest,
        tender_terms_dir=t1_dest,
    )
    return TestClient(app)


def _sag_queue_rows(equipment_categories: list[str]) -> dict[str, list[dict]]:
    return {
        "current_opportunity_queue": [
            _current_opportunity_row(
                institution_id="inst-sag",
                display_name="SAG",
                tender_code=_SAG_TENDER_CODE,
                equipment_category=cat,
                eligibility_reason_codes=[],
            )
            for cat in equipment_categories
        ]
    }


# --- healthy W1 + healthy T1 (available facts) ---


def test_healthy_w1_and_t1_returns_facts(tmp_path: Path) -> None:
    facts = (_explicit_fact(),)
    bundle = _bundle(_SAG_TENDER_CODE, tender_facts=facts)
    client = _client(
        tmp_path,
        w1_queue_rows=_sag_queue_rows(["balance"]),
        t1_bundles=[bundle],
    )
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    assert r.status_code == 200
    data = r.json()
    assert data["found_in_queue"] is True
    assert data["t1_published"] is True
    assert len(data["tender_facts"]) == 1
    assert data["tender_facts"][0]["state"] == "explicit"
    assert data["tender_facts"][0]["value"] == 30


# --- healthy W1 + no T1 publication (not_available) ---


def test_healthy_w1_no_t1_publication_is_not_available(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        w1_queue_rows=_sag_queue_rows(["balance"]),
        t1_publish=False,
    )
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    assert r.status_code == 200
    data = r.json()
    assert data["found_in_queue"] is True
    assert data["t1_published"] is False
    assert data["tender_facts"] == []
    assert data["t1_meta"]["reduced_mode"] is True


# --- healthy W1 + incomplete T1 coverage ---


def test_incomplete_t1_coverage_surfaces_reason_codes(tmp_path: Path) -> None:
    bundle = _bundle(_SAG_TENDER_CODE, tender_facts=(_unknown_fact(),), coverage=_incomplete_coverage())
    client = _client(
        tmp_path,
        w1_queue_rows=_sag_queue_rows(["balance"]),
        t1_bundles=[bundle],
    )
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    data = r.json()
    assert data["coverage"]["is_complete"] is False
    assert "attachment_download_incomplete" in data["coverage"]["incomplete_reason_codes"]


# --- healthy W1 + conflicting fact ---


def test_conflicting_fact_state_surfaces_honestly(tmp_path: Path) -> None:
    bundle = _bundle(_SAG_TENDER_CODE, tender_facts=(_conflicting_fact(),))
    client = _client(
        tmp_path,
        w1_queue_rows=_sag_queue_rows(["balance"]),
        t1_bundles=[bundle],
    )
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    data = r.json()
    fact = data["tender_facts"][0]
    assert fact["state"] == "conflicting"
    assert fact["value"] is None
    assert len(fact["candidates"]) == 2


# --- healthy W1 + explicit fact/evidence: exact evidence fields survive ---


def test_explicit_fact_evidence_survives_serialization(tmp_path: Path) -> None:
    bundle = _bundle(_SAG_TENDER_CODE, tender_facts=(_explicit_fact(),))
    client = _client(
        tmp_path,
        w1_queue_rows=_sag_queue_rows(["balance"]),
        t1_bundles=[bundle],
    )
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    data = r.json()
    ev = data["tender_facts"][0]["evidence"][0]
    expected = _evidence().to_dict()
    for key, value in expected.items():
        assert ev[key] == value, key


# --- healthy W1, tender absent from queue -> 404 even though T1 has data ---


def test_tender_absent_from_healthy_queue_is_404_even_with_t1_data(tmp_path: Path) -> None:
    other_code = "999999-1-LE26"
    bundle = _bundle(other_code, tender_facts=(_explicit_fact(),))
    client = _client(
        tmp_path,
        w1_queue_rows=_sag_queue_rows(["balance"]),  # only SAG tender in queue
        t1_bundles=[bundle],
    )
    r = client.get(f"/operator/procurement/tenders/{other_code}")
    assert r.status_code == 404


# --- degraded W1 -> T1 detail not exposed as actionable ---


def test_degraded_w1_never_exposes_t1_as_actionable(tmp_path: Path) -> None:
    bundle = _bundle(_SAG_TENDER_CODE, tender_facts=(_explicit_fact(),))
    client = _client(
        tmp_path,
        w1_with_bundle=False,  # W1 feed missing entirely -> reduced_mode
        t1_bundles=[bundle],
    )
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    # Not a 404 (W1 is degraded, not confirmed-absent) ...
    assert r.status_code == 200
    data = r.json()
    assert data["queue_meta"]["reduced_mode"] is True
    # ... but T1 detail must never be surfaced as though W1 established it.
    assert data["found_in_queue"] is False
    assert data["t1_published"] is False
    assert data["tender_facts"] == []
    assert data["items"] == []


# --- multiple W1 rows for one tender_code are preserved ---


def test_multiple_w1_category_rows_preserved_not_collapsed(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        w1_queue_rows=_sag_queue_rows(["balance", "centrifuge"]),
        t1_publish=False,
    )
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    data = r.json()
    assert len(data["queue_rows"]) == 2
    categories = {row["equipment_category"] for row in data["queue_rows"]}
    assert categories == {"balance", "centrifuge"}


# --- missing/malformed T1 publication -> not_available, never a 500 ---


def test_malformed_t1_publication_is_not_available_not_500(tmp_path: Path) -> None:
    w1_dest = tmp_path / "institution_prospects"
    _write_bundle(w1_dest, queue_rows=_sag_queue_rows(["balance"]))
    t1_dest = tmp_path / "tender_terms"
    t1_dest.mkdir(parents=True, exist_ok=True)
    (t1_dest / "summary.json").write_text("{not valid json", encoding="utf-8")
    (t1_dest / "tender_terms.jsonl").write_text("", encoding="utf-8")

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        institution_prospect_dir=w1_dest, tender_terms_dir=t1_dest
    )
    client = TestClient(app)
    r = client.get(f"/operator/procurement/tenders/{_SAG_TENDER_CODE}")
    assert r.status_code == 200
    data = r.json()
    assert data["t1_published"] is False
    assert data["t1_meta"]["reduced_mode"] is True
    assert data["t1_meta"]["supported_contract_version"] is False


# --- proxy allowlist path-traversal rejection is covered separately in
# apps/dashboard-proxy/src/allowlist.test.ts (Part 4 TS coverage). ---
