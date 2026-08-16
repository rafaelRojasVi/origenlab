"""Tests for the T1 Semantic Benchmark v1 harness."""

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
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.benchmark import (
    CorpusEntry,
    KNOWN_CONTROL_TENDER_IDS,
    benchmark_tender,
    discover_corpus_entries,
    load_adjudication,
    load_bundle,
    score_against_adjudication,
    select_corpus,
    summarize,
    write_outputs,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.constants import (
    FACT_STATE_EXPLICIT,
    FACT_STATE_UNKNOWN,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.semantic_fallback import (
    SemanticClaim,
    SemanticFallbackClient,
)


def _bundle(tender_id: str, text: str) -> TenderAttachmentBundle:
    chunk = EvidenceChunk(
        chunk_id=f"chunk-{tender_id}",
        attachment_id="att-1",
        tender_id=tender_id,
        source_format="pdf",
        locator_type="pdf_page",
        locator={"page": 1, "page_count": 1, "part": 1},
        text=text,
        text_sha256=sha256_text(text),
        char_count=len(text),
        ordinal=1,
    )
    attachment = AttachmentRecord(
        attachment_id="att-1",
        tender_id=tender_id,
        listing_ordinal=1,
        row_ordinal=1,
        portal_filename="BASES.pdf",
        safe_filename="BASES.pdf",
        tipo="Bases",
        descripcion="Bases",
        fecha_adjunto="2026-04-08",
        content_type="application/pdf",
        detected_format="pdf",
        extension="pdf",
        byte_count=1000,
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
            page_count=1,
            extractor="test",
        ),
    )
    return TenderAttachmentBundle(
        tender_id=tender_id,
        source_kind="test",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(attachment,),
        chunks=(chunk,),
        bytes_downloaded=1000,
        semantic_digest="test-digest",
        outcome_counts={"extraction_success": 1},
    )


def _entry(tender_id: str, *, institution: str = "SAG") -> CorpusEntry:
    return CorpusEntry(
        tender_id=tender_id,
        bundle_path=None,  # not needed when bundle is passed directly
        institution=institution,
        equipment_categories=(),
        attachment_count=1,
    )


class _ExplicitOfferValidityClient(SemanticFallbackClient):
    def extract_claims(self, *, spec, spans):
        span = next(row for row in spans if "45 días" in row.text)
        return (
            SemanticClaim(value=45, day_basis=None, evidence_span_ids=(span.span_id,)),
        )


class _RejectedClient(SemanticFallbackClient):
    def extract_claims(self, *, spec, spans):
        return (SemanticClaim(value=999, day_basis="business", evidence_span_ids=(spans[0].span_id,)),)


class _FailingClient(SemanticFallbackClient):
    def extract_claims(self, *, spec, spans):
        raise RuntimeError("ollama endpoint unreachable")


# --- corpus selection -------------------------------------------------------


def test_select_corpus_is_deterministic_and_repeatable() -> None:
    entries = tuple(_entry(f"1-{i}-LP26") for i in range(10))
    first = select_corpus(entries, corpus_size=5)
    second = select_corpus(entries, corpus_size=5)
    assert [e.tender_id for e in first] == [e.tender_id for e in second]
    assert len(first) == 5


def test_select_corpus_includes_known_controls_when_present() -> None:
    entries = tuple(_entry(tid) for tid in KNOWN_CONTROL_TENDER_IDS) + tuple(
        _entry(f"9-{i}-LP26") for i in range(10)
    )
    corpus = select_corpus(entries, corpus_size=5, required_tender_ids=KNOWN_CONTROL_TENDER_IDS)
    ids = {e.tender_id for e in corpus}
    for control in KNOWN_CONTROL_TENDER_IDS:
        assert control in ids


def test_select_corpus_diversifies_across_institutions() -> None:
    entries = tuple(
        _entry(f"1-{i}-LP26", institution="A" if i % 2 == 0 else "B") for i in range(10)
    )
    corpus = select_corpus(entries, corpus_size=4)
    institutions = {e.institution for e in corpus}
    assert institutions == {"A", "B"}


def test_discover_corpus_entries_handles_missing_dir(tmp_path) -> None:
    assert discover_corpus_entries(
        bundles_dir=tmp_path / "missing", detail_cache_dir=tmp_path
    ) == ()


# --- semantic accounting / deterministic preservation -----------------------


def test_deterministic_facts_are_never_overwritten_by_semantic_pass() -> None:
    text = "Monto estimado del contrato $ 77.330.890 (IVA incluido)."
    bundle = _bundle("1-1-LP26", text)
    entry = _entry("1-1-LP26")

    cases = benchmark_tender(entry, bundle, client=_RejectedClient(), model_name="qwen3:14b")
    total_budget_case = next(c for c in cases if c.field == "total_budget")

    assert total_budget_case.det_state == FACT_STATE_EXPLICIT
    assert total_budget_case.det_value == total_budget_case.sem_value
    assert total_budget_case.model_called is False
    assert total_budget_case.priority != "deterministic_changed_unexpectedly"


def test_unresolved_field_becomes_semantic_explicit_case() -> None:
    text = "La vigencia de la oferta será de 45 días desde la apertura."
    bundle = _bundle("1-2-LP26", text)
    entry = _entry("1-2-LP26")

    cases = benchmark_tender(
        entry, bundle, client=_ExplicitOfferValidityClient(), model_name="qwen3:14b"
    )
    validity_case = next(c for c in cases if c.field == "offer_validity_days")

    assert validity_case.det_state == FACT_STATE_UNKNOWN
    assert validity_case.model_called is True
    assert validity_case.sem_state == FACT_STATE_EXPLICIT
    assert validity_case.changed is True
    assert validity_case.priority == "unresolved_to_semantic_explicit"
    assert validity_case.evidence_excerpt
    assert validity_case.accepted_claims == 1


def test_rejected_claim_is_recorded_with_reason_and_stays_unknown() -> None:
    text = "El pago se realizará en un plazo máximo de 30 días corridos."
    bundle = _bundle("1-3-LP26", text)
    entry = _entry("1-3-LP26")

    cases = benchmark_tender(entry, bundle, client=_RejectedClient(), model_name="qwen3:14b")
    payment_case = next(c for c in cases if c.field == "payment_deadline_days")

    assert payment_case.sem_state == FACT_STATE_UNKNOWN
    assert payment_case.accepted_claims == 0
    assert len(payment_case.rejected_claims) == 1
    assert payment_case.rejected_claims[0]["reason"]
    assert payment_case.candidate_span_count > 0
    assert payment_case.priority == "candidate_spans_but_abstention"


def test_operational_error_stays_an_error_not_unknown() -> None:
    text = "El pago se realizará en un plazo máximo de 30 días corridos."
    bundle = _bundle("1-4-LP26", text)
    entry = _entry("1-4-LP26")

    cases = benchmark_tender(entry, bundle, client=_FailingClient(), model_name="qwen3:14b")
    payment_case = next(c for c in cases if c.field == "payment_deadline_days")

    assert payment_case.operational_error is not None
    assert payment_case.sem_state == "model_operational_error"
    assert payment_case.sem_state != FACT_STATE_UNKNOWN
    assert payment_case.priority == "operational_error"


def test_no_semantic_client_skips_model_calls() -> None:
    text = "Monto estimado del contrato $ 1.000.000 (IVA incluido)."
    bundle = _bundle("1-5-LP26", text)
    entry = _entry("1-5-LP26")

    cases = benchmark_tender(entry, bundle, client=None, model_name=None)
    assert all(c.model_called is False for c in cases)
    assert all(c.model is None for c in cases)


# --- review priority ---------------------------------------------------------


def test_review_priority_ranks_operational_errors_highest() -> None:
    text = "El pago se realizará en un plazo máximo de 30 días corridos."
    bundle = _bundle("1-6-LP26", text)
    entry = _entry("1-6-LP26")

    cases = benchmark_tender(entry, bundle, client=_FailingClient(), model_name="qwen3:14b")
    ranked = sorted(cases, key=lambda c: c.priority_rank)
    assert ranked[0].priority == "operational_error"


# --- summary reconciliation ---------------------------------------------------


def test_summary_reconciles_case_counts() -> None:
    text = "La vigencia de la oferta será de 45 días desde la apertura."
    bundle = _bundle("1-7-LP26", text)
    entry = _entry("1-7-LP26")

    cases = benchmark_tender(
        entry, bundle, client=_ExplicitOfferValidityClient(), model_name="qwen3:14b"
    )
    summary = summarize(cases, tender_count=1)

    assert summary.field_evaluations == len(cases)
    assert summary.tenders == 1
    assert summary.accepted_semantic_positives == 1
    assert summary.release_gates["passed"] is True
    assert sum(summary.priority_counts.values()) == len(cases)


def test_release_gate_fails_when_deterministic_value_changes() -> None:
    text = "Monto estimado del contrato $ 77.330.890 (IVA incluido)."
    bundle = _bundle("1-8-LP26", text)
    entry = _entry("1-8-LP26")

    cases = benchmark_tender(entry, bundle, client=None, model_name=None)
    tampered = cases[0].__class__(
        **{**cases[0].to_dict(), "sem_value": "tampered", "priority": "deterministic_changed_unexpectedly", "priority_rank": 1, "rejected_claims": ()}
    )
    summary = summarize((tampered,) + cases[1:], tender_count=1)
    assert summary.release_gates["passed"] is False
    assert summary.release_gates["deterministic_overwrite_count"] == 1


# --- output writers ------------------------------------------------------------


def test_write_outputs_produces_deterministic_jsonl_and_csv(tmp_path) -> None:
    text = "La vigencia de la oferta será de 45 días desde la apertura."
    bundle = _bundle("1-9-LP26", text)
    entry = _entry("1-9-LP26")

    cases = benchmark_tender(
        entry, bundle, client=_ExplicitOfferValidityClient(), model_name="qwen3:14b"
    )
    summary = summarize(cases, tender_count=1)

    out_dir = tmp_path / "run"
    paths = write_outputs(
        out_dir=out_dir,
        cases=cases,
        summary=summary,
        corpus=(entry,),
        adjudication_metrics=None,
        model_name="qwen3:14b",
    )

    assert paths["cases"].is_file()
    assert paths["review_queue"].is_file()
    assert paths["summary"].is_file()
    assert paths["report"].is_file()

    lines = paths["cases"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(cases)
    for line in lines:
        json.loads(line)  # must be valid JSON

    summary_payload = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary_payload["field_evaluations"] == len(cases)
    assert "release_gates" in summary_payload

    # rerun must produce byte-identical cases.jsonl (deterministic ordering)
    write_outputs(
        out_dir=out_dir,
        cases=cases,
        summary=summary,
        corpus=(entry,),
        adjudication_metrics=None,
        model_name="qwen3:14b",
    )
    lines_again = paths["cases"].read_text(encoding="utf-8").splitlines()
    assert lines == lines_again


def test_write_outputs_does_not_touch_network_or_persistence(tmp_path, monkeypatch) -> None:
    def _fail(*args, **kwargs):  # pragma: no cover - guard only
        raise AssertionError("write_outputs must not touch the network")

    monkeypatch.setattr("urllib.request.urlopen", _fail)

    entry = _entry("1-10-LP26")
    bundle = _bundle("1-10-LP26", "Monto estimado del contrato $ 1.000.000 (IVA incluido).")
    cases = benchmark_tender(entry, bundle, client=None, model_name=None)
    summary = summarize(cases, tender_count=1)

    write_outputs(
        out_dir=tmp_path / "run",
        cases=cases,
        summary=summary,
        corpus=(entry,),
        adjudication_metrics=None,
        model_name=None,
    )


# --- adjudication ---------------------------------------------------------


def test_optional_adjudication_scores_precision_recall(tmp_path) -> None:
    text = "La vigencia de la oferta será de 45 días desde la apertura."
    bundle = _bundle("1-11-LP26", text)
    entry = _entry("1-11-LP26")

    cases = benchmark_tender(
        entry, bundle, client=_ExplicitOfferValidityClient(), model_name="qwen3:14b"
    )

    adjudication_path = tmp_path / "adjudication.jsonl"
    adjudication_path.write_text(
        json.dumps(
            {
                "tender_id": "1-11-LP26",
                "field": "offer_validity_days",
                "gold_state": FACT_STATE_EXPLICIT,
                "gold_value": 45,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    adjudication = load_adjudication(adjudication_path)
    metrics = score_against_adjudication(cases, adjudication)

    assert metrics.true_positives == 1
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0


def test_adjudication_file_is_optional(tmp_path) -> None:
    assert load_adjudication(tmp_path / "does-not-exist.jsonl") == {}


def test_load_bundle_validates_tender_id_matches_filename(tmp_path) -> None:
    import pickle

    bundle = _bundle("1-12-LP26", "text")
    path = tmp_path / "1-12-LP26.pkl"
    with path.open("wb") as handle:
        pickle.dump(bundle, handle)

    entry = CorpusEntry(
        tender_id="1-12-LP26",
        bundle_path=path,
        institution="",
        equipment_categories=(),
        attachment_count=0,
    )
    loaded = load_bundle(entry)
    assert loaded.tender_id == "1-12-LP26"

    mismatched_entry = CorpusEntry(
        tender_id="wrong-id",
        bundle_path=path,
        institution="",
        equipment_categories=(),
        attachment_count=0,
    )
    with pytest.raises(ValueError):
        load_bundle(mismatched_entry)
