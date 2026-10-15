"""memory_model.py unit tests: request building, parsing, refusals. No network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from origenlab_worker.memory_model import (
    FACT_KINDS,
    MEMORY_SCHEMA,
    NEXT_ACTIONS,
    build_memory_request,
    memory_content,
    parse_memory,
)
from origenlab_worker.triage_model import ModelRefused

EMAIL_BODY = "Buenas, necesitamos cotizar el microscopio ZX100 para el laboratorio."
PREV_MEM: dict = {
    "summary": "Instituto pidió microscopio ZX100.",
    "facts": [{"k": "product", "v": "ZX100"}],
    "open_questions": ["¿Cuándo lo necesitan?"],
    "next_step": {"action": "esperar", "why": "Esperando respuesta.", "confidence": "medium"},
}


def answer(payload, stop="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


def valid_memory(**kwargs):
    base = {
        "summary": "Cotización microscopio ZX100 para Instituto Ejemplo.",
        "facts": [{"k": "product", "v": "Microscopio ZX100"}],
        "open_questions": ["¿Cuándo lo necesitan?"],
        "next_step": {"action": "esperar", "why": "Pendiente respuesta del cliente.", "confidence": "medium"},
    }
    base.update(kwargs)
    return base


# --- build_memory_request -------------------------------------------------

def test_request_contains_system_prompt_and_email_body() -> None:
    req = build_memory_request(None, EMAIL_BODY, subject_kind="opportunity")
    assert len(req["system"]) == 1
    assert "memoria" in req["system"][0]["text"].lower()
    content = req["messages"][0]["content"]
    assert EMAIL_BODY[:50] in content
    assert content.count("<email>") == 1


def test_request_includes_previous_memory_when_given() -> None:
    req = build_memory_request(PREV_MEM, EMAIL_BODY, subject_kind="opportunity")
    content = req["messages"][0]["content"]
    assert "ZX100" in content
    assert "PREVIOUS MEMORY" in content


def test_request_output_config_is_json_schema() -> None:
    req = build_memory_request(None, EMAIL_BODY, subject_kind="opportunity")
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"] is MEMORY_SCHEMA


def test_organization_system_prompt_is_different_from_opportunity() -> None:
    req_opp = build_memory_request(None, EMAIL_BODY, subject_kind="opportunity")
    req_org = build_memory_request(None, EMAIL_BODY, subject_kind="organization")
    assert req_opp["system"][0]["text"] != req_org["system"][0]["text"]


def test_email_body_is_inside_tags() -> None:
    req = build_memory_request(None, EMAIL_BODY, subject_kind="person")
    content = req["messages"][0]["content"]
    tag_start = content.index("<email>")
    tag_end = content.index("</email>")
    assert EMAIL_BODY[:50] in content[tag_start:tag_end]


def test_context_includes_stage_and_quote_numbers_for_opportunity() -> None:
    req = build_memory_request(None, EMAIL_BODY, subject_kind="opportunity",
                               stage="quoting", quote_numbers=["CN-2601"])
    content = req["messages"][0]["content"]
    assert "quoting" in content
    assert "CN-2601" in content


def test_context_does_not_include_stage_for_organization() -> None:
    req = build_memory_request(None, EMAIL_BODY, subject_kind="organization")
    content = req["messages"][0]["content"]
    # The organization system prompt talks about «institución», not stage.
    assert '"stage"' not in content


def test_body_is_truncated_at_6000_chars() -> None:
    long_body = "x" * 8000
    req = build_memory_request(None, long_body, subject_kind="opportunity")
    content = req["messages"][0]["content"]
    assert "x" * 6000 in content
    assert "x" * 6001 not in content


# --- parse_memory ---------------------------------------------------------

def test_valid_memory_is_parsed_correctly() -> None:
    mem = parse_memory(answer(valid_memory()))
    assert mem["summary"] == "Cotización microscopio ZX100 para Instituto Ejemplo."
    assert mem["facts"][0]["k"] == "product"
    assert mem["next_step"]["action"] == "esperar"
    assert mem["next_step"]["confidence"] == "medium"


def test_memory_without_next_step_is_accepted_when_null() -> None:
    mem = parse_memory(answer(valid_memory(next_step=None)))
    assert mem["next_step"] is None


def test_memory_open_questions_empty_list_is_accepted() -> None:
    mem = parse_memory(answer(valid_memory(open_questions=[])))
    assert mem["open_questions"] == []


def test_parse_injects_source_record_id_into_facts_without_one() -> None:
    payload = valid_memory()
    # Remove source_record_id from first fact if present
    if "source_record_id" in payload["facts"][0]:
        del payload["facts"][0]["source_record_id"]
    mem = parse_memory(answer(payload), source_record_id="test-src-id")
    assert mem["facts"][0]["source_record_id"] == "test-src-id"


def test_summary_is_truncated_at_600_chars() -> None:
    long = "a " * 400  # > 600
    mem = parse_memory(answer(valid_memory(summary=long)))
    assert len(mem["summary"]) <= 600


def test_facts_capped_at_15() -> None:
    many_facts = [{"k": "other", "v": f"fact {i}"} for i in range(20)]
    mem = parse_memory(answer(valid_memory(facts=many_facts)))
    assert len(mem["facts"]) == 15


def test_open_questions_capped_at_3() -> None:
    many = ["¿q" + str(i) + "?" for i in range(10)]
    mem = parse_memory(answer(valid_memory(open_questions=many)))
    assert len(mem["open_questions"]) <= 3


def test_unknown_fact_kind_is_skipped() -> None:
    facts = [{"k": "unknown_kind", "v": "x"}, {"k": "product", "v": "ZX100"}]
    mem = parse_memory(answer(valid_memory(facts=facts)))
    kinds = [f["k"] for f in mem["facts"]]
    assert "unknown_kind" not in kinds
    assert "product" in kinds


def test_invalid_next_step_action_is_set_to_none() -> None:
    ns = {"action": "do_magic", "why": "porque sí", "confidence": "high"}
    mem = parse_memory(answer(valid_memory(next_step=ns)))
    assert mem["next_step"] is None


def test_invalid_next_step_confidence_is_set_to_none() -> None:
    ns = {"action": "esperar", "why": "porque sí", "confidence": "very_high"}
    mem = parse_memory(answer(valid_memory(next_step=ns)))
    assert mem["next_step"] is None


@pytest.mark.parametrize("payload,code", [
    ("not json", "not_json"),
    ([], "not_an_object"),
    ({}, "summary_missing"),
    ({"summary": "ok", "facts": "not a list", "open_questions": [], "next_step": None}, "facts_not_list"),
])
def test_malformed_responses_raise_model_refused(payload, code) -> None:
    with pytest.raises(ModelRefused, match=code):
        parse_memory(answer(payload))


def test_model_refused_on_refusal_stop_reason() -> None:
    with pytest.raises(ModelRefused, match="model_refused"):
        parse_memory(answer({}, stop="refusal"))


def test_model_refused_on_truncation() -> None:
    with pytest.raises(ModelRefused, match="truncated"):
        parse_memory(answer(valid_memory(), stop="max_tokens"))


def test_all_fact_kinds_are_accepted() -> None:
    for kind in FACT_KINDS:
        facts = [{"k": kind, "v": "valor"}]
        mem = parse_memory(answer(valid_memory(facts=facts)))
        assert mem["facts"][0]["k"] == kind


def test_all_next_step_actions_are_accepted() -> None:
    for action in NEXT_ACTIONS:
        ns = {"action": action, "why": "razón válida", "confidence": "low"}
        mem = parse_memory(answer(valid_memory(next_step=ns)))
        assert mem["next_step"]["action"] == action


# --- memory_content -------------------------------------------------------

def test_memory_content_contains_context_and_untrusted_body() -> None:
    content = memory_content(None, EMAIL_BODY, subject_kind="opportunity",
                             stage="quoting", institution_name="Instituto Ejemplo")
    assert "CONTEXT" in content
    assert "PREVIOUS MEMORY" in content
    assert "<email>" in content
    assert "</email>" in content
    assert "Instituto Ejemplo" in content


def test_memory_content_previous_memory_shows_prior_facts() -> None:
    content = memory_content(PREV_MEM, EMAIL_BODY, subject_kind="opportunity")
    assert "ZX100" in content
    assert "PREVIOUS MEMORY" in content
