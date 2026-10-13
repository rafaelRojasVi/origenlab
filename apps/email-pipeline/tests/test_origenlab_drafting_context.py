from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_email_pipeline.tatiana_copilot.draft_package import build_draft_package
from origenlab_email_pipeline.tatiana_copilot.index import TatianaExampleIndex
from origenlab_email_pipeline.tatiana_copilot.origenlab_context import (
    DRAFTING_PROFILE_ORIGENLAB,
    DRAFTING_PROFILE_TATIANA_HISTORICAL,
)
from origenlab_email_pipeline.tatiana_copilot.origenlab_facts_loader import (
    load_origenlab_drafting_context,
    monorepo_root,
)
from origenlab_email_pipeline.tatiana_copilot.prompting import build_prompt_blocks
from origenlab_email_pipeline.tatiana_copilot.schemas import DraftCase, RetrievedExample


def test_load_origenlab_context_from_repo() -> None:
    root = monorepo_root()
    assert (root / "apps" / "web" / "src" / "data" / "company.ts").is_file()
    ctx = load_origenlab_drafting_context(repo_root=root)
    assert ctx.company_name == "OrigenLab"
    assert "contacto@origenlab.cl" in ctx.contact_email
    assert "OrigenLab" in ctx.approved_signature_block
    assert "Labdelivery" not in ctx.approved_signature_block
    assert ctx.fact_sources


def test_build_prompt_blocks_origenlab_requires_context() -> None:
    case = DraftCase(case_id="c1", subject="S", body_text="x" * 50, expected_label=None)
    with pytest.raises(ValueError, match="origenlab_context"):
        build_prompt_blocks(
            case=case,
            style_examples=[],
            retrieved_examples=[],
            drafting_profile=DRAFTING_PROFILE_ORIGENLAB,
            origenlab_context=None,
        )


def test_build_prompt_blocks_origenlab_includes_company_facts() -> None:
    ctx = load_origenlab_drafting_context()
    ex = RetrievedExample(
        example_id="e_style",
        score=1.0,
        label="quote_followup",
        subject="S",
        body_text=("Ejemplo de tono " * 12).strip(),
    )
    case = DraftCase(case_id="c1", subject="Cotización", body_text="Necesitamos balanza analítica." * 3)
    blocks = build_prompt_blocks(
        case=case,
        style_examples=[ex],
        retrieved_examples=[],
        drafting_profile=DRAFTING_PROFILE_ORIGENLAB,
        origenlab_context=ctx,
    )
    assert blocks["drafting_profile"] == DRAFTING_PROFILE_ORIGENLAB
    assert "company_facts" in blocks
    assert blocks["company_facts"]["company_name"] == "OrigenLab"
    assert blocks["approved_signature_block"] == ctx.approved_signature_block
    assert blocks["style_examples"][0].get("role") == "STYLE_REFERENCE_ONLY_NOT_FACTS"


def test_tatiana_profile_no_role_noise_in_style_keys() -> None:
    load_origenlab_drafting_context()
    ex = RetrievedExample(
        example_id="e1",
        score=1.0,
        label="x",
        subject="s",
        body_text="body " * 20,
    )
    case = DraftCase(case_id="c1", subject="S", body_text="body " * 20)
    blocks = build_prompt_blocks(
        case=case,
        style_examples=[ex],
        retrieved_examples=[],
        drafting_profile=DRAFTING_PROFILE_TATIANA_HISTORICAL,
        origenlab_context=None,
    )
    assert blocks["style_examples"][0].get("role") is None


def test_mock_package_origenlab_signature(tmp_path: Path) -> None:
    from origenlab_email_pipeline.tatiana_copilot.generator import MockDraftGenerator

    repo = Path(__file__).resolve().parents[1]
    labeled = repo / "reports" / "out" / "tatiana_candidate_cohort_marketing_top200_labeled_final.csv"
    if not labeled.is_file():
        pytest.skip("seed CSV missing")
    ctx = load_origenlab_drafting_context()
    from origenlab_email_pipeline.tatiana_copilot.normalize import build_example_sets

    style_ex, retr_ex = build_example_sets(
        labeled_final_csv=labeled,
        style_seed_csv=repo / "reports" / "out" / "tatiana_candidate_cohort_marketing_top200_seed_style_guide.csv",
        retrieval_seed_csv=repo / "reports" / "out" / "tatiana_candidate_cohort_marketing_top200_seed_retrieval.csv",
    )
    idx = TatianaExampleIndex.build(style_examples=style_ex, retrieval_examples=retr_ex, method="tfidf")
    case = DraftCase(
        case_id="ol1",
        subject="Consulta equipo",
        body_text="Estimados, necesitamos cotización de incubadora. " * 2,
    )
    pkg = build_draft_package(
        case=case,
        index=idx,
        generator=MockDraftGenerator(),
        style_top_k=1,
        retrieval_top_k=1,
        drafting_profile=DRAFTING_PROFILE_ORIGENLAB,
        origenlab_context=ctx,
    )
    assert not pkg.abstained
    assert "contacto@origenlab.cl" in (pkg.generated_draft or "")
    assert "Tatiana Vivanco" not in (pkg.generated_draft or "")
    assert pkg.prompt_blocks.get("drafting_profile") == DRAFTING_PROFILE_ORIGENLAB
