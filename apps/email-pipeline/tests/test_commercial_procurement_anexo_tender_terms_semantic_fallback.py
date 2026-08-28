"""Tests for the bounded local semantic ANEXO-T1 fallback."""

from __future__ import annotations

import json

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
    sha256_text,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.semantic_fallback import (
    FIELD_SPECS,
    OllamaSemanticFallbackClient,
    SemanticClaim,
    SemanticFallbackClient,
    build_candidate_spans,
    semantic_observations_for_field,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.source import (
    resolve_evidence_chunks,
)


def _sources(text: str):
    chunk = EvidenceChunk(
        chunk_id="chunk-01",
        attachment_id="att-1",
        tender_id="745712-8-LP26",
        source_format="pdf",
        locator_type="pdf_page",
        locator={"page": 18, "page_count": 35, "part": 1},
        text=text,
        text_sha256=sha256_text(text),
        char_count=len(text),
        ordinal=1,
    )
    attachment = AttachmentRecord(
        attachment_id="att-1",
        tender_id="745712-8-LP26",
        listing_ordinal=1,
        row_ordinal=1,
        portal_filename="RESOLUCION QUE APRUEBA BASES.pdf",
        safe_filename="RESOLUCION QUE APRUEBA BASES.pdf",
        tipo="Bases",
        descripcion="Bases administrativas y técnicas",
        fecha_adjunto="2026-04-08",
        content_type="application/pdf",
        detected_format="pdf",
        extension="pdf",
        byte_count=10_000,
        sha256="a" * 64,
        download_status="downloaded",
        outcome="extraction_success",
        role_tag="administrative_basis",
        evidence_attachment_id="att-1",
        extraction=AttachmentExtraction(
            outcome="extraction_success",
            detected_format="pdf",
            chunk_count=1,
            char_count=len(text),
            page_count=35,
            extractor="test",
        ),
    )
    bundle = TenderAttachmentBundle(
        tender_id="745712-8-LP26",
        source_kind="test",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(attachment,),
        chunks=(chunk,),
        bytes_downloaded=10_000,
        semantic_digest="test-semantic-digest",
        outcome_counts={"extraction_success": 1},
    )
    return resolve_evidence_chunks(bundle)


class _FirstMatchingClient(SemanticFallbackClient):
    def __init__(self, value, *, day_basis=None, marker=""):
        self.value = value
        self.day_basis = day_basis
        self.marker = marker

    def extract_claims(self, *, spec, spans):
        span = next(row for row in spans if not self.marker or self.marker in row.text)
        return (
            SemanticClaim(
                value=self.value,
                day_basis=self.day_basis,
                evidence_span_ids=(span.span_id,),
            ),
        )


def test_candidate_spans_are_bounded_stable_and_exact() -> None:
    text = (
        "x" * 4000
        + " Monto estimado del contrato $ 77.330.890 (IVA incluido). "
        + "y" * 4000
    )
    sources = _sources(text)

    first = build_candidate_spans(sources, "total_budget")
    second = build_candidate_spans(sources, "total_budget")

    assert first
    assert [row.span_id for row in first] == [row.span_id for row in second]
    assert all(len(row.text) <= 1225 for row in first)

    span = next(row for row in first if "77.330.890" in row.text)
    assert sources[0].chunk.text[span.char_start : span.char_end] == span.text


def test_supported_delivery_claim_becomes_exact_term_evidence() -> None:
    text = (
        "8.1 Plazo de entrega. "
        "El plazo de entrega ofertado, no podrá superar los 50 días hábiles."
    )
    sources = _sources(text)
    observations = semantic_observations_for_field(
        sources,
        "maximum_delivery_days",
        _FirstMatchingClient(
            50,
            day_basis="business",
            marker="50 días hábiles",
        ),
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.value == {"days": 50, "day_basis": "business"}
    assert len(observation.evidence) == 1

    evidence = observation.evidence[0]
    start = evidence.locator["char_start"]
    end = evidence.locator["char_end"]
    assert evidence.evidence_excerpt == text[start:end]
    assert "50 días hábiles" in evidence.evidence_excerpt


def test_semantic_claim_rejects_value_missing_from_evidence() -> None:
    sources = _sources(
        "El plazo de entrega ofertado no podrá superar los 50 días hábiles."
    )

    observations = semantic_observations_for_field(
        sources,
        "maximum_delivery_days",
        _FirstMatchingClient(60, day_basis="business"),
    )

    assert observations == ()


class _UnknownSpanClient(SemanticFallbackClient):
    def extract_claims(self, *, spec, spans):
        return (
            SemanticClaim(
                value=30,
                day_basis="calendar",
                evidence_span_ids=("not-a-real-span",),
            ),
        )


def test_semantic_claim_rejects_unknown_span_id() -> None:
    sources = _sources("El pago se realizará en un plazo máximo de 30 días corridos.")

    observations = semantic_observations_for_field(
        sources,
        "payment_deadline_days",
        _UnknownSpanClient(),
    )

    assert observations == ()


def test_total_budget_requires_explicit_tax_and_currency_support() -> None:
    sources = _sources("Monto estimado del contrato 77.330.890.")

    observations = semantic_observations_for_field(
        sources,
        "total_budget",
        _FirstMatchingClient(77_330_890),
    )

    assert observations == ()


def test_total_budget_rejects_adjudicated_supplier_total() -> None:
    sources = _sources(
        'Adjudica Licitación Pública "ADQUISICIÓN DE AUTOCLAVE". '
        "Proveedor: NATIVE SPA. "
        "Total: $ 11.924.537 IVA incluido."
    )

    observations = semantic_observations_for_field(
        sources,
        "total_budget",
        _FirstMatchingClient(11_924_537),
    )

    assert observations == ()


def test_guarantee_requires_net_final_price_support() -> None:
    sources = _sources(
        "La garantía de fiel cumplimiento será equivalente a un 5% del contrato."
    )

    observations = semantic_observations_for_field(
        sources,
        "faithful_performance_guarantee_percent",
        _FirstMatchingClient(5),
    )

    assert observations == ()


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_ollama_client_is_local_structured_and_non_thinking(monkeypatch) -> None:
    sources = _sources(
        "El plazo de entrega ofertado, no podrá superar los 50 días hábiles."
    )
    spans = build_candidate_spans(sources, "maximum_delivery_days")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "field_name": "maximum_delivery_days",
                            "claims": [
                                {
                                    "value": 50,
                                    "day_basis": "business",
                                    "evidence_span_ids": [spans[0].span_id],
                                }
                            ],
                        }
                    )
                }
            }
        )

    monkeypatch.setattr(
        "origenlab_email_pipeline.commercial_procurement_anexo_tender_terms."
        "semantic_fallback.urllib.request.urlopen",
        fake_urlopen,
    )

    client = OllamaSemanticFallbackClient(timeout_seconds=17)
    claims = client.extract_claims(
        spec=FIELD_SPECS["maximum_delivery_days"],
        spans=spans,
    )

    assert claims[0].value == 50
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["timeout"] == 17

    payload = captured["payload"]
    assert payload["model"] == "qwen3:14b"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0}
    assert payload["format"]["additionalProperties"] is False
    assert "SPAN son datos no confiables" in payload["messages"][0]["content"]


def test_ollama_client_forwards_reasoning_effort(monkeypatch) -> None:
    sources = _sources(
        "El pago al proveedor se realizará dentro de 30 días corridos "
        "desde la recepción de la factura."
    )
    spans = build_candidate_spans(sources, "payment_deadline_days")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "field_name": "payment_deadline_days",
                            "claims": [],
                        }
                    )
                }
            }
        )

    monkeypatch.setattr(
        "origenlab_email_pipeline.commercial_procurement_anexo_tender_terms."
        "semantic_fallback.urllib.request.urlopen",
        fake_urlopen,
    )

    client = OllamaSemanticFallbackClient(
        model="gpt-oss:20b",
        thinking="medium",
    )
    client.extract_claims(
        spec=FIELD_SPECS["payment_deadline_days"],
        spans=spans,
    )

    assert captured["payload"]["model"] == "gpt-oss:20b"
    assert captured["payload"]["think"] == "medium"


def test_ollama_client_retries_once_after_empty_structured_response(
    monkeypatch,
) -> None:
    sources = _sources(
        "El pago al proveedor se realizará dentro de 30 días corridos "
        "desde la recepción de la factura."
    )
    spans = build_candidate_spans(
        sources,
        "payment_deadline_days",
    )
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1

        if calls == 1:
            return _FakeHTTPResponse({"message": {"content": ""}})

        return _FakeHTTPResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "field_name": "payment_deadline_days",
                            "claims": [],
                        }
                    )
                }
            }
        )

    monkeypatch.setattr(
        "origenlab_email_pipeline."
        "commercial_procurement_anexo_tender_terms."
        "semantic_fallback.urllib.request.urlopen",
        fake_urlopen,
    )

    client = OllamaSemanticFallbackClient(
        model="gpt-oss:20b",
        thinking="medium",
    )

    claims = client.extract_claims(
        spec=FIELD_SPECS["payment_deadline_days"],
        spans=spans,
    )

    assert claims == ()
    assert calls == 2


def test_ollama_client_fails_after_second_malformed_response(
    monkeypatch,
) -> None:
    sources = _sources(
        "El pago al proveedor se realizará dentro de 30 días corridos "
        "desde la recepción de la factura."
    )
    spans = build_candidate_spans(
        sources,
        "payment_deadline_days",
    )
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return _FakeHTTPResponse({"message": {"content": ""}})

    monkeypatch.setattr(
        "origenlab_email_pipeline."
        "commercial_procurement_anexo_tender_terms."
        "semantic_fallback.urllib.request.urlopen",
        fake_urlopen,
    )

    client = OllamaSemanticFallbackClient(
        model="gpt-oss:20b",
        thinking="medium",
    )

    with pytest.raises(
        ValueError,
        match="invalid Ollama semantic fallback response after retry",
    ):
        client.extract_claims(
            spec=FIELD_SPECS["payment_deadline_days"],
            spans=spans,
        )

    assert calls == 2


def test_ollama_client_rejects_non_local_endpoint() -> None:
    with pytest.raises(ValueError, match="local endpoint"):
        OllamaSemanticFallbackClient(endpoint="https://example.com/api/chat")


class _MixedValidityClient(SemanticFallbackClient):
    def extract_claims(self, *, spec, spans):
        span = next(row for row in spans if "50 días hábiles" in row.text)

        return (
            # Hallucinated value: absent from the selected raw evidence.
            SemanticClaim(
                value=60,
                day_basis="business",
                evidence_span_ids=(span.span_id,),
            ),
            # Valid sibling claim must survive.
            SemanticClaim(
                value=50,
                day_basis="business",
                evidence_span_ids=(span.span_id,),
            ),
        )


def test_invalid_semantic_claim_does_not_discard_valid_sibling() -> None:
    sources = _sources(
        "El plazo de entrega ofertado, no podrá superar los 50 días hábiles."
    )

    observations = semantic_observations_for_field(
        sources,
        "maximum_delivery_days",
        _MixedValidityClient(),
    )

    assert len(observations) == 1
    assert observations[0].value == {
        "days": 50,
        "day_basis": "business",
    }
    assert observations[0].evidence
    assert "50 días hábiles" in observations[0].evidence[0].evidence_excerpt


def test_payment_rejects_factoring_notice_clause() -> None:
    sources = _sources(
        "El proveedor debe informar si efectuará cesión de los créditos "
        "contenidos en las facturas y las empresas de factoring con las "
        "cuales operará. En aquellos pagos acordados a 30 dias, se entenderá "
        "oportuna aquella información realizada al menos con 15 dias de "
        "anticipación a la fecha de pago."
    )

    observations = semantic_observations_for_field(
        sources,
        "payment_deadline_days",
        _FirstMatchingClient(30),
    )

    assert observations == ()


def test_payment_rejects_delivery_range_number() -> None:
    sources = _sources(
        "Precio Unitario Neto, en pesos chilenos (sin IVA) | "
        "Plazo de entrega en días hábiles | "
        "Entre 1 y 30 días hábiles | "
        "Entre 31 y 40 días hábiles | "
        "Entre 41 y 60 días hábiles"
    )

    observations = semantic_observations_for_field(
        sources,
        "payment_deadline_days",
        _FirstMatchingClient(30),
    )

    assert observations == ()


def test_contract_duration_rejects_worker_seniority_months() -> None:
    sources = _sources(
        "Copia del contrato de trabajo vigente, con una antigüedad "
        "de al menos 6 meses, contados desde la presentación de la oferta."
    )

    observations = semantic_observations_for_field(
        sources,
        "contract_duration_months",
        _FirstMatchingClient(6),
    )

    assert observations == ()


def test_offer_validity_rejects_generic_offer_reference() -> None:
    sources = _sources(
        "El oferente podrá presentar oferta por una o más líneas de equipos."
    )

    observations = semantic_observations_for_field(
        sources,
        "offer_validity_days",
        _FirstMatchingClient(30),
    )

    assert observations == ()


def test_delivery_rejects_unselected_range_choice_form() -> None:
    sources = _sources(
        "Plazo de entrega en días hábiles "
        "(marcar con una X los tramos de plazo de entrega): "
        "Entre 1 y 30 días hábiles; "
        "Entre 31 y 40 días hábiles; "
        "Entre 41 y 60 días hábiles."
    )

    observations = semantic_observations_for_field(
        sources,
        "maximum_delivery_days",
        _FirstMatchingClient(
            60,
            day_basis="business",
        ),
    )

    assert observations == ()


def test_delivery_rejects_bidder_scoring_matrix_value() -> None:
    sources = _sources(
        "Hasta 3 días corridos contados desde la emisión de la orden "
        "de compra sin monto mínimo de despacho 100. "
        "4 días corridos desde la emisión de compra sin monto mínimo "
        "de despacho 60. "
        "Plazo de entrega y monto mínimo de despacho (10%): "
        "El cálculo del puntaje asignado a este criterio se basará "
        "en la información proporcionada por el oferente."
    )

    observations = semantic_observations_for_field(
        sources,
        "maximum_delivery_days",
        _FirstMatchingClient(
            4,
            day_basis="calendar",
            marker="4 días corridos",
        ),
    )

    assert observations == ()


def test_delivery_rejects_awarded_line_specific_offer_value() -> None:
    sources = _sources(
        "La Comisión Evaluadora sugiere adjudicar la oferta presentada "
        "correspondiente a la Línea 10, por un monto total de $700.000 "
        "impuestos incluidos y un plazo de entrega de 3 días corridos."
    )

    observations = semantic_observations_for_field(
        sources,
        "maximum_delivery_days",
        _FirstMatchingClient(
            3,
            day_basis="calendar",
            marker="3 días corridos",
        ),
    )

    assert observations == ()


def test_payment_rejects_invoice_objection_deadline() -> None:
    sources = _sources(
        "En aquellos pagos acordados a 30 dias, el proveedor deberá informar "
        "sobre la cesión de créditos contenidos en las facturas. "
        "La Universidad se reserva el derecho de reclamar contra el contenido "
        "de la factura, en el plazo máximo de 08 dias corridos contados desde "
        "su presentación."
    )

    observations = semantic_observations_for_field(
        sources,
        "payment_deadline_days",
        _FirstMatchingClient(
            8,
            day_basis="calendar",
            marker="08 dias corridos",
        ),
    )

    assert observations == ()


def test_payment_context_accepts_real_payment_clause() -> None:
    sources = _sources(
        "El pago al proveedor se realizará dentro de 30 días corridos "
        "desde la recepción de la factura."
    )

    observations = semantic_observations_for_field(
        sources,
        "payment_deadline_days",
        _FirstMatchingClient(
            30,
            day_basis="calendar",
        ),
    )

    assert len(observations) == 1
    assert observations[0].value == 30


def test_contract_duration_context_accepts_real_contract_clause() -> None:
    sources = _sources(
        "La vigencia del contrato será de 18 meses desde su suscripción."
    )

    observations = semantic_observations_for_field(
        sources,
        "contract_duration_months",
        _FirstMatchingClient(18),
    )

    assert len(observations) == 1
    assert observations[0].value == 18


def test_contract_duration_accepts_present_contract_morphology() -> None:
    sources = _sources("La vigencia del presente contrato tendrá un plazo de 12 meses.")

    observations = semantic_observations_for_field(
        sources,
        "contract_duration_months",
        _FirstMatchingClient(12),
    )

    assert len(observations) == 1
    assert observations[0].value == 12


def test_offer_validity_context_accepts_real_validity_clause() -> None:
    sources = _sources(
        "La oferta deberá mantener una vigencia mínima de 120 días corridos."
    )

    observations = semantic_observations_for_field(
        sources,
        "offer_validity_days",
        _FirstMatchingClient(
            120,
            day_basis="calendar",
        ),
    )

    assert len(observations) == 1
    assert observations[0].value == 120


def test_offer_validity_accepts_plural_real_tender_clause() -> None:
    sources = _sources(
        "16 VALIDEZ DE LAS OFERTAS. "
        "Las ofertas tendrán una validez mínima de 60 días corridos, "
        "contados desde la fecha de cierre de recepción de ofertas."
    )

    observations = semantic_observations_for_field(
        sources,
        "offer_validity_days",
        _FirstMatchingClient(
            60,
            day_basis="calendar",
            marker="60 días corridos",
        ),
    )

    assert len(observations) == 1
    assert observations[0].value == 60


def test_offer_validity_rejects_certificate_vigencia_age() -> None:
    sources = _sources(
        "Certificado de Vigencia del poder del representante legal "
        "con una antigüedad no superior a 60 días corridos, "
        "contados desde la fecha de notificación de la adjudicación, "
        "a efectos de acreditar la vigencia del poder del representante "
        "legal del oferente a la época de presentación de la oferta."
    )

    observations = semantic_observations_for_field(
        sources,
        "offer_validity_days",
        _FirstMatchingClient(
            60,
            day_basis="calendar",
        ),
    )

    assert observations == ()


def test_semantic_numeric_parser_does_not_join_newline_numbers() -> None:
    sources = _sources(
        "Pago al proveedor: 30\n60 días corridos desde la recepción de la factura."
    )

    observations = semantic_observations_for_field(
        sources,
        "payment_deadline_days",
        _FirstMatchingClient(
            30,
            day_basis="calendar",
        ),
    )

    assert len(observations) == 1
    assert observations[0].value == 30


def test_offer_validity_rejects_loose_offer_and_vigencia_cooccurrence() -> None:
    sources = _sources(
        "La oferta comprende los equipos indicados en estas bases. "
        "El certificado conserva su vigencia durante 60 días corridos."
    )

    observations = semantic_observations_for_field(
        sources,
        "offer_validity_days",
        _FirstMatchingClient(
            60,
            day_basis="calendar",
        ),
    )

    assert observations == ()


class _RuntimeFailureClient(SemanticFallbackClient):
    def extract_claims(self, *, spec, spans):
        raise RuntimeError("ollama unavailable")


def test_semantic_client_runtime_failure_propagates() -> None:
    sources = _sources(
        "El pago al proveedor se realizará dentro de 30 días corridos "
        "desde la recepción de la factura."
    )

    with pytest.raises(RuntimeError, match="ollama unavailable"):
        semantic_observations_for_field(
            sources,
            "payment_deadline_days",
            _RuntimeFailureClient(),
        )


def test_payment_rejects_unrelated_article_number_in_same_span() -> None:
    sources = _sources(
        "El pago al proveedor se realizará dentro de 30 días corridos "
        "desde la recepción de la factura. "
        "Conforme a las disposiciones administrativas aplicables, "
        "se exceptúa lo indicado en el artículo 133 del Reglamento."
    )

    observations = semantic_observations_for_field(
        sources,
        "payment_deadline_days",
        _FirstMatchingClient(
            133,
            day_basis="calendar",
        ),
    )

    assert observations == ()


def test_ollama_client_rejects_local_prefix_with_external_actual_host() -> None:
    with pytest.raises(ValueError, match="local endpoint"):
        OllamaSemanticFallbackClient(
            endpoint="http://127.0.0.1:11434@example.com/api/chat"
        )
