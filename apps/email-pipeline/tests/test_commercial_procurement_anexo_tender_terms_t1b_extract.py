"""T1-B high-value commercial term extraction tests."""

from dataclasses import replace

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
    sha256_text,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    FACT_STATE_CONFLICTING,
    FACT_STATE_EXPLICIT,
    FACT_STATE_UNKNOWN,
    extract_tender_terms,
)


def _bundle(
    *texts: str,
    role_tag: str = "administrative_basis",
    incomplete_reason_codes: tuple[str, ...] = (),
) -> TenderAttachmentBundle:
    chunks = tuple(
        EvidenceChunk(
            chunk_id=f"chunk-{index:02d}",
            attachment_id="att-1",
            tender_id="1057890-1-LE26",
            source_format="pdf",
            locator_type="pdf_page",
            locator={
                "page": index,
                "page_count": max(len(texts), 1),
                "part": 1,
            },
            text=text,
            text_sha256=sha256_text(text),
            char_count=len(text),
            ordinal=index,
        )
        for index, text in enumerate(texts, start=1)
    )

    record = AttachmentRecord(
        attachment_id="att-1",
        tender_id="1057890-1-LE26",
        listing_ordinal=1,
        row_ordinal=1,
        portal_filename="2-Res._1756_Ap._Bases.pdf",
        safe_filename="2-Res._1756_Ap._Bases.pdf",
        tipo="Bases",
        descripcion="Bases administrativas y técnicas",
        fecha_adjunto="2026-07-24",
        content_type="application/pdf",
        detected_format="pdf",
        extension="pdf",
        byte_count=10_000,
        sha256="a" * 64,
        download_status="downloaded",
        outcome="extraction_success",
        role_tag=role_tag,
        evidence_attachment_id="att-1",
        extraction=AttachmentExtraction(
            outcome="extraction_success",
            detected_format="pdf",
            chunk_count=len(chunks),
            char_count=sum(len(text) for text in texts),
            page_count=max(len(texts), 1),
            extractor="test",
        ),
    )

    return TenderAttachmentBundle(
        tender_id="1057890-1-LE26",
        source_kind="test",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(record,),
        chunks=chunks,
        bytes_downloaded=10_000,
        incomplete_reason_codes=incomplete_reason_codes,
        semantic_digest="gold-source-digest",
        outcome_counts={"extraction_success": 1},
    )


def _facts(bundle: TenderAttachmentBundle):
    result = extract_tender_terms(bundle)
    return result, {
        fact.field_name: fact
        for fact in result.tender_facts
    }


def test_gold_tender_extracts_initial_commercial_terms() -> None:
    bundle = _bundle(
        (
            "Artículo 5°: Datos de la licitación. "
            "Tiempo máximo de duración del contrato "
            "Ejecución Inmediata, por 12 meses. "
            "Modalidad de Pago del contrato "
            "Pago máximo 30 días, previa emisión de orden de compra, "
            "recepción conforme de los bienes y entrega de factura. "
            "Presupuesto Disponible $ 28.419.400.- CLP impuestos incluidos"
        ),
        (
            "Artículo 15°: Validez de las Propuestas. "
            "Las ofertas tendrán una validez de 90 días contados desde "
            "la fecha de apertura electrónica de la licitación."
        ),
        (
            "Anexo N°7. El plazo total de entrega convenido se contará "
            "desde la respectiva orden de compra. "
            "De superar los 120 días corridos la oferta deberá ser "
            "declarada inadmisible."
        ),
        (
            "Artículo 21°: Garantía de Fiel Cumplimiento del Contrato. "
            "El adjudicatario deberá entregar antes de la firma del "
            "contrato una garantía, por un monto que alcance el 5% "
            "del precio final neto ofertado por el adjudicatario."
        ),
    )

    result, facts = _facts(bundle)

    budget = facts["total_budget"]
    assert budget.state == FACT_STATE_EXPLICIT
    assert budget.value == 28_419_400
    assert budget.currency == "CLP"
    assert budget.tax_basis == "taxes_included"
    assert len(budget.evidence) == 1
    assert "Presupuesto Disponible" in budget.evidence[0].evidence_excerpt

    validity = facts["offer_validity_days"]
    assert validity.state == FACT_STATE_EXPLICIT
    assert validity.value == 90
    assert validity.unit == "days"

    duration = facts["contract_duration_months"]
    assert duration.state == FACT_STATE_EXPLICIT
    assert duration.value == 12
    assert duration.unit == "months"

    payment = facts["payment_deadline_days"]
    assert payment.state == FACT_STATE_EXPLICIT
    assert payment.value == 30

    delivery = facts["maximum_delivery_days"]
    assert delivery.state == FACT_STATE_EXPLICIT
    assert delivery.value == {
        "days": 120,
        "day_basis": "calendar",
    }
    assert delivery.value_type == "duration_days"
    assert delivery.unit == "days"

    guarantee = facts["faithful_performance_guarantee_percent"]
    assert guarantee.state == FACT_STATE_EXPLICIT
    assert guarantee.value == 5
    assert guarantee.unit == "percent"
    assert guarantee.tax_basis == "net_final_awarded_price"

    assert result.semantic_digest
    assert result.contact_authorization is False
    assert result.outreach_authorization is False
    assert result.production_queue_mutated is False
    assert result.persisted is False


def test_budget_tax_basis_requires_explicit_taxes_included_text() -> None:
    bundle = _bundle(
        "Presupuesto Disponible $ 28.419.400.- CLP"
    )

    _, facts = _facts(bundle)
    fact = facts["total_budget"]

    assert fact.state == FACT_STATE_UNKNOWN
    assert fact.value is None
    assert fact.tax_basis == "taxes_included"
    assert fact.reason_codes == (
        "no_supported_explicit_pattern_match",
    )


def test_real_sag_monto_estimado_del_contrato_is_explicit() -> None:
    bundle = _bundle(
        (
            "II. BASES TÉCNICAS. "
            "Monto estimado del contrato $ 77.330.890 (IVA incluido). "
            "Duración del Contrato Fecha de inicio: desde la aceptación "
            "de la orden de compra."
        )
    )

    _, facts = _facts(bundle)
    fact = facts["total_budget"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == 77_330_890
    assert fact.currency == "CLP"
    assert fact.tax_basis == "taxes_included"
    assert len(fact.evidence) == 1
    assert "Monto estimado del contrato" in fact.evidence[0].evidence_excerpt


def test_real_sag_offer_vigencia_minima_is_explicit() -> None:
    bundle = _bundle(
        (
            "12. DE LA VIGENCIA DE LAS OFERTAS. "
            "Las ofertas tendrán una vigencia mínima de 120 días corridos, "
            "a contar de la fecha de cierre de Recepción de ofertas "
            "en el portal www.mercadopublico.cl."
        )
    )

    _, facts = _facts(bundle)
    fact = facts["offer_validity_days"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == 120
    assert fact.unit == "days"
    assert len(fact.evidence) == 1
    assert "120 días corridos" in fact.evidence[0].evidence_excerpt


def test_real_sag_payment_deadline_is_explicit() -> None:
    bundle = _bundle(
        (
            "22. DEL PRECIO Y FORMA DE PAGO. "
            "El SAG pagará los equipos en pesos chilenos, "
            "a través de transferencia bancaria, en el plazo "
            "máximo de 30 días corridos, contados desde la "
            "recepción de la factura."
        )
    )

    _, facts = _facts(bundle)
    fact = facts["payment_deadline_days"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == 30
    assert fact.unit == "days"
    assert len(fact.evidence) == 1
    assert "30 días corridos" in fact.evidence[0].evidence_excerpt
    assert "recepción de la factura" in fact.evidence[0].evidence_excerpt


def test_missing_source_bundle_digest_fails_closed() -> None:
    bundle = replace(
        _bundle(
            "Artículo 15°. Las ofertas tendrán una validez de "
            "90 días contados desde la apertura electrónica."
        ),
        semantic_digest="",
    )

    with pytest.raises(
        ValueError,
        match="source bundle semantic digest is required",
    ):
        extract_tender_terms(bundle)


def test_same_explicit_value_coalesces_supporting_evidence() -> None:
    clause = (
        "Garantía de Fiel Cumplimiento del Contrato. "
        "La garantía será por un monto que alcance el 5% "
        "del precio final neto ofertado por el adjudicatario."
    )
    bundle = _bundle(clause, clause)

    _, facts = _facts(bundle)
    fact = facts["faithful_performance_guarantee_percent"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == 5
    assert len(fact.evidence) == 2
    assert fact.candidates == ()


def test_distinct_explicit_values_are_preserved_as_conflict() -> None:
    bundle = _bundle(
        (
            "Artículo 15°. Las ofertas tendrán una validez de "
            "90 días contados desde la fecha de apertura electrónica."
        ),
        (
            "Artículo 15°. Las ofertas tendrán una validez de "
            "120 días contados desde la fecha de apertura electrónica."
        ),
    )

    _, facts = _facts(bundle)
    fact = facts["offer_validity_days"]

    assert fact.state == FACT_STATE_CONFLICTING
    assert fact.value is None
    assert sorted(candidate.value for candidate in fact.candidates) == [
        90,
        120,
    ]
    assert all(candidate.evidence for candidate in fact.candidates)


def test_complete_source_coverage_without_supported_match_remains_unknown() -> None:
    bundle = _bundle("Documento sin los términos comerciales buscados.")

    _, facts = _facts(bundle)

    assert all(
        fact.state == FACT_STATE_UNKNOWN
        for fact in facts.values()
    )
    assert all(
        fact.reason_codes == ("no_supported_explicit_pattern_match",)
        for fact in facts.values()
    )


def test_incomplete_coverage_without_match_is_unknown() -> None:
    bundle = _bundle(
        "Documento parcial sin los términos buscados.",
        incomplete_reason_codes=("archive_member_needs_ocr",),
    )

    result, facts = _facts(bundle)

    assert result.coverage.is_complete is False
    assert all(
        fact.state == FACT_STATE_UNKNOWN
        for fact in facts.values()
    )


def test_positive_fact_survives_incomplete_coverage() -> None:
    bundle = _bundle(
        (
            "Artículo 15°. Las ofertas tendrán una validez de "
            "90 días contados desde la fecha de apertura electrónica."
        ),
        incomplete_reason_codes=("archive_member_needs_ocr",),
    )

    result, facts = _facts(bundle)

    assert result.coverage.is_complete is False
    assert facts["offer_validity_days"].state == FACT_STATE_EXPLICIT
    assert facts["offer_validity_days"].value == 90
    assert facts["total_budget"].state == FACT_STATE_UNKNOWN


def test_unrelated_guarantee_and_warranty_do_not_cross_match() -> None:
    bundle = _bundle(
        
            "Garantía de seriedad de la oferta: monto equivalente al 5%. "
            "El periodo de garantía técnica mínimo a ofertar es de "
            "12 meses sin excepción."
        
    )

    _, facts = _facts(bundle)

    assert (
        facts["faithful_performance_guarantee_percent"].state
        == FACT_STATE_UNKNOWN
    )
    assert (
        facts["contract_duration_months"].state
        == FACT_STATE_UNKNOWN
    )


def test_document_role_is_advisory_not_a_recognition_gate() -> None:
    bundle = _bundle(
        (
            "Artículo 15°. Las ofertas tendrán una validez de "
            "90 días contados desde la fecha de apertura electrónica."
        ),
        role_tag="template",
    )

    _, facts = _facts(bundle)

    assert facts["offer_validity_days"].state == FACT_STATE_EXPLICIT
    assert facts["offer_validity_days"].value == 90



def test_delivery_word_number_business_days_is_explicit() -> None:
    bundle = _bundle(
        (
            "ESPECIFICACIONES TECNICAS GENERALES OBLIGATORIAS. "
            "Plazo máximo de entrega de equipos quince (15) "
            "días hábiles contados desde la adjudicación del contrato."
        )
    )

    _, facts = _facts(bundle)
    fact = facts["maximum_delivery_days"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == {
        "days": 15,
        "day_basis": "business",
    }
    assert fact.value_type == "duration_days"
    assert fact.unit == "days"


def test_delivery_table_value_without_day_basis_preserves_unknown_basis() -> None:
    bundle = _bundle(
        (
            "Critico | Instalación | Incluye flete, instalación y "
            "puesta en marcha sin costo para el establecimiento. "
            "Critico | Plazo máximo de entrega | 30 días"
        )
    )

    _, facts = _facts(bundle)
    fact = facts["maximum_delivery_days"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == {
        "days": 30,
        "day_basis": None,
    }


def test_delivery_same_value_from_distinct_morphologies_coalesces() -> None:
    bundle = _bundle(
        (
            "Nota: El plazo máximo para la entrega de los equipos "
            "debe ser de 40 días hábiles."
        ),
        (
            "Aquella oferta que ingrese un plazo de entrega superior "
            "a 40 días hábiles, su oferta no continuará con el proceso "
            "de evaluación y será declarada inadmisible."
        ),
    )

    _, facts = _facts(bundle)
    fact = facts["maximum_delivery_days"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == {
        "days": 40,
        "day_basis": "business",
    }
    assert len(fact.evidence) == 2
    assert fact.candidates == ()


def test_delivery_offered_cap_morphology_is_explicit() -> None:
    bundle = _bundle(
        (
            "El plazo de entrega ofertado, no podrá superar "
            "los 30 días hábiles."
        )
    )

    _, facts = _facts(bundle)
    fact = facts["maximum_delivery_days"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == {
        "days": 30,
        "day_basis": "business",
    }


def test_delivery_day_basis_difference_is_a_real_conflict() -> None:
    bundle = _bundle(
        "Plazo máximo de entrega | 30 días hábiles",
        "Plazo máximo de entrega | 30 días corridos",
    )

    _, facts = _facts(bundle)
    fact = facts["maximum_delivery_days"]

    assert fact.state == FACT_STATE_CONFLICTING
    assert fact.value is None

    values = {
        (
            candidate.value["days"],
            candidate.value["day_basis"],
        )
        for candidate in fact.candidates
    }

    assert values == {
        (30, "business"),
        (30, "calendar"),
    }
    assert all(
        candidate.evidence
        for candidate in fact.candidates
    )


def test_faithful_performance_equivalent_amount_morphology_is_explicit() -> None:
    bundle = _bundle(
        (
            "Unidades de Fomento, destinada a cautelar el fiel y "
            "oportuno cumplimiento del contrato. "
            "Dicha garantía deberá ser constituida por un monto "
            "equivalente a un 5% del precio ﬁnal neto ofertado "
            "por el adjudicatario, con una vigencia adicional."
        )
    )

    _, facts = _facts(bundle)
    fact = facts["faithful_performance_guarantee_percent"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == 5
    assert fact.unit == "percent"
    assert fact.tax_basis == "net_final_awarded_price"



def test_contract_duration_real_narrative_morphologies_coalesce() -> None:
    bundle = _bundle(
        (
            "DE LA VIGENCIA Y DURACIÓN. "
            "La vigencia del contrato se extenderá por el periodo "
            "de 8 meses contados desde la total tramitación de la "
            "Resolución que aprueba el contrato."
        ),
        (
            "Duración del Contrato "
            "Tiempo de Duración: 8 meses "
            "Fecha de inicio: desde la aceptación de la orden de compra."
        ),
    )

    _, facts = _facts(bundle)
    fact = facts["contract_duration_months"]

    assert fact.state == FACT_STATE_EXPLICIT
    assert fact.value == 8
    assert fact.unit == "months"
    assert len(fact.evidence) == 2
    assert fact.candidates == ()


def test_extraction_is_deterministic_across_chunk_input_order() -> None:
    bundle = _bundle(
        (
            "Artículo 15°. Las ofertas tendrán una validez de "
            "90 días contados desde la fecha de apertura electrónica."
        ),
        (
            "Presupuesto Disponible $ 28.419.400.- "
            "CLP impuestos incluidos"
        ),
    )

    forward = extract_tender_terms(bundle)
    reverse = extract_tender_terms(
        replace(bundle, chunks=tuple(reversed(bundle.chunks)))
    )

    assert forward.to_dict() == reverse.to_dict()
    assert forward.semantic_digest == reverse.semantic_digest
