"""ANEXO-T1 structured tender-intelligence contract tests."""

from dataclasses import replace

import pytest

from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    FACT_STATE_CONFLICTING,
    FACT_STATE_EXPLICIT,
    FACT_STATE_NOT_EXPLICITLY_FOUND,
    FACT_STATE_UNKNOWN,
    FactCandidate,
    TenderItemTerms,
    TenderTermFact,
    TenderTermsBundle,
    TenderTermsCoverage,
    TermEvidence,
    finalize_bundle,
)


def _evidence(
    *,
    evidence_id: str,
    page: int,
    excerpt: str,
) -> TermEvidence:
    return TermEvidence(
        evidence_id=evidence_id,
        attachment_id="att-resolution",
        evidence_attachment_id="att-resolution",
        safe_filename="2-Res._1756_Ap._Bases.pdf",
        attachment_sha256="a" * 64,
        detected_format="pdf",
        document_role="administrative_basis",
        chunk_id=f"chunk-page-{page}",
        locator_type="pdf_page",
        locator={"page": page, "page_count": 102, "part": 1},
        locator_display=f"2-Res._1756_Ap._Bases.pdf :: page {page}",
        evidence_excerpt=excerpt,
        evidence_text_sha256=("b" if page == 5 else "c") * 64,
    )


def _complete_coverage() -> TenderTermsCoverage:
    return TenderTermsCoverage(
        has_source_bundle=True,
        extraction_complete=True,
        attachments_discovered=1,
        attachments_downloaded=1,
    )


def test_missing_is_not_false_when_coverage_is_incomplete() -> None:
    with pytest.raises(
        ValueError,
        match="not_explicitly_found requires complete extraction coverage",
    ):
        TenderTermFact(
            fact_id="f-missing",
            field_name="bid_security_requirement",
            state=FACT_STATE_NOT_EXPLICITLY_FOUND,
            coverage_status=COVERAGE_INCOMPLETE,
        )

    fact = TenderTermFact(
        fact_id="f-unknown",
        field_name="bid_security_requirement",
        state=FACT_STATE_UNKNOWN,
        coverage_status=COVERAGE_INCOMPLETE,
    )

    assert fact.value is None


def test_not_explicitly_found_is_allowed_only_with_complete_coverage() -> None:
    fact = TenderTermFact(
        fact_id="f-not-found",
        field_name="some_optional_term",
        state=FACT_STATE_NOT_EXPLICITLY_FOUND,
        coverage_status=COVERAGE_COMPLETE,
    )

    assert fact.state == FACT_STATE_NOT_EXPLICITLY_FOUND
    assert fact.value is None


def test_conflicting_fact_preserves_each_candidate_and_its_provenance() -> None:
    first = _evidence(
        evidence_id="signing-calendar",
        page=5,
        excerpt="plazo de 15 días corridos",
    )
    second = replace(
        _evidence(
            evidence_id="signing-business",
            page=33,
            excerpt="plazo de 15 días hábiles",
        ),
        attachment_sha256="d" * 64,
    )

    fact = TenderTermFact(
        fact_id="contract-signing-conflict",
        field_name="contract_signing_deadline",
        state=FACT_STATE_CONFLICTING,
        coverage_status=COVERAGE_COMPLETE,
        candidates=(
            FactCandidate(
                candidate_id="calendar",
                value="15 días corridos",
                evidence=(first,),
            ),
            FactCandidate(
                candidate_id="business",
                value="15 días hábiles",
                evidence=(second,),
            ),
        ),
    )

    assert fact.value is None
    assert len(fact.candidates) == 2


def test_1057890_gold_contract_represents_budget_and_centrifuge_specs() -> None:
    """Gold contract from Resolution Exenta 1756 / tender 1057890-1-LE26."""

    page5 = _evidence(
        evidence_id="page5-budget",
        page=5,
        excerpt=(
            "Presupuesto total $28.419.400 ... "
            "Centrífuga universal ... $10.455.000"
        ),
    )
    page33 = _evidence(
        evidence_id="page33-centrifuge",
        page=33,
        excerpt=(
            "Centrífuga universal 24-36 capachos ... "
            "rango de temperatura -10°C a 40°C o superior"
        ),
    )

    total_budget = TenderTermFact(
        fact_id="1057890-1-LE26:total-budget",
        field_name="total_budget",
        state=FACT_STATE_EXPLICIT,
        coverage_status=COVERAGE_COMPLETE,
        value=28_419_400,
        value_type="integer",
        currency="CLP",
        tax_basis="taxes_included",
        evidence=(page5,),
    )

    item_budget = TenderTermFact(
        fact_id="1057890-1-LE26:item-2:budget",
        field_name="item_budget",
        state=FACT_STATE_EXPLICIT,
        coverage_status=COVERAGE_COMPLETE,
        value=10_455_000,
        value_type="integer",
        currency="CLP",
        tax_basis="taxes_included",
        evidence=(page5,),
    )

    quantity = TenderTermFact(
        fact_id="1057890-1-LE26:item-2:quantity",
        field_name="quantity",
        state=FACT_STATE_EXPLICIT,
        coverage_status=COVERAGE_COMPLETE,
        value=1,
        value_type="integer",
        evidence=(page5,),
    )

    temperature = TenderTermFact(
        fact_id="1057890-1-LE26:item-2:temperature",
        field_name="temperature_requirement",
        state=FACT_STATE_EXPLICIT,
        coverage_status=COVERAGE_COMPLETE,
        value="-10°C to 40°C or superior",
        value_type="text",
        evidence=(page33,),
    )

    item = TenderItemTerms(
        item_id="1057890-1-LE26:item-2",
        item_number="2",
        item_label="Centrífuga universal 24-36 capachos",
        facts=(item_budget, quantity, temperature),
    )

    bundle = finalize_bundle(
        TenderTermsBundle(
            tender_id="1057890-1-LE26",
            source_bundle_semantic_digest="source-bundle-digest",
            tender_facts=(total_budget,),
            items=(item,),
            coverage=_complete_coverage(),
        )
    )

    assert bundle.semantic_digest
    assert bundle.tender_facts[0].value == 28_419_400
    assert bundle.items[0].item_number == "2"

    item_facts = {
        fact.field_name: fact
        for fact in bundle.items[0].facts
    }
    assert item_facts["item_budget"].value == 10_455_000
    assert item_facts["quantity"].value == 1
    assert item_facts["temperature_requirement"].value == (
        "-10°C to 40°C or superior"
    )

    assert all(
        fact.evidence
        for fact in (bundle.tender_facts[0], *bundle.items[0].facts)
    )

    assert bundle.contact_authorization is False
    assert bundle.outreach_authorization is False
    assert bundle.production_queue_mutated is False
    assert bundle.persisted is False


def test_semantic_digest_is_independent_of_fact_and_item_order() -> None:
    evidence = _evidence(
        evidence_id="digest-source",
        page=5,
        excerpt="budget evidence",
    )

    fact_a = TenderTermFact(
        fact_id="a",
        field_name="total_budget",
        state=FACT_STATE_EXPLICIT,
        coverage_status=COVERAGE_COMPLETE,
        value=100,
        evidence=(evidence,),
    )
    fact_b = TenderTermFact(
        fact_id="b",
        field_name="offer_validity_days",
        state=FACT_STATE_EXPLICIT,
        coverage_status=COVERAGE_COMPLETE,
        value=90,
        evidence=(evidence,),
    )

    coverage = _complete_coverage()

    first = finalize_bundle(
        TenderTermsBundle(
            tender_id="t1",
            source_bundle_semantic_digest="source",
            tender_facts=(fact_a, fact_b),
            items=(
                TenderItemTerms(
                    item_id="item-2",
                    item_number="2",
                    item_label="second",
                    facts=(fact_b, fact_a),
                ),
                TenderItemTerms(
                    item_id="item-1",
                    item_number="1",
                    item_label="first",
                    facts=(fact_a,),
                ),
            ),
            coverage=coverage,
        )
    )

    second = finalize_bundle(
        TenderTermsBundle(
            tender_id="t1",
            source_bundle_semantic_digest="source",
            tender_facts=(fact_b, fact_a),
            items=(
                TenderItemTerms(
                    item_id="item-1",
                    item_number="1",
                    item_label="first",
                    facts=(fact_a,),
                ),
                TenderItemTerms(
                    item_id="item-2",
                    item_number="2",
                    item_label="second",
                    facts=(fact_a, fact_b),
                ),
            ),
            coverage=coverage,
        )
    )

    assert first.semantic_digest == second.semantic_digest
