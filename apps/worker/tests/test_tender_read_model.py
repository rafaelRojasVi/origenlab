"""The tender `read` request and the validation of its answer (Task 9). Pure: a fake reader, no
network, no database. Every value is invented."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from origenlab_worker import v1_reuse
from origenlab_worker.tender.extract import AnnexPage, AnnexText
from origenlab_worker.tender.read_model import (
    CONVERT_TO_WORD,
    EFFORT,
    MAX_TOKENS,
    READ_SCHEMA,
    RESUMEN_FIELDS,
    ReadFailed,
    build_read_request,
    parse_read,
    streaming_reader,
    validate_read,
)

MODEL = "claude-opus-5-5"


def annex(document_id: str, locators: list[str], *, outcome: str = v1_reuse.OUTCOME_EXTRACTION_SUCCESS,
          filename: str = "bases.pdf", warnings: list[str] | None = None) -> AnnexText:
    return AnnexText(document_id, filename, outcome, [AnnexPage(loc, f"texto {loc}") for loc in locators],
                     list(warnings or []))


def answer_with(*, cita: dict[str, str]) -> dict:
    field = {"texto": "Cierra el 30 de octubre", "citas": [cita]}
    return {
        "resumen": {name: dict(field, citas=[dict(cita)]) for name in RESUMEN_FIELDS},
        "checklist": [{"documento": "Anexo 1 Declaración jurada", "tipo": "declaracion", "requiere_firma": True,
                       "archivo_formulario_id": None, "fuente": dict(cita)}],
        "items": [{"linea": 1, "descripcion": "Balanza analítica", "cantidad": "2", "unidad": "unidad",
                   "requisitos": ["0,1 mg"], "citas": [dict(cita)]}],
    }


def _message(payload=None, *, stop="end_turn", text=None, model=MODEL):
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop, model=model, content=[SimpleNamespace(type="text", text=body)],
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=300))


# ── the request ─────────────────────────────────────────────────────────────────────────────


def test_request_uses_structured_output_and_fallbacks() -> None:
    r = build_read_request("…", {}, model=MODEL)
    assert r["output_config"]["format"]["type"] == "json_schema" and r["fallbacks"] == "default"
    assert r["output_config"]["format"]["schema"] == READ_SCHEMA
    assert r["output_config"]["effort"] == EFFORT == "high"
    assert r["max_tokens"] == MAX_TOKENS == 32000
    assert r["thinking"] == {"type": "adaptive"}
    assert r["model"] == MODEL


def test_the_system_prompt_says_the_anexos_are_untrusted_data() -> None:
    r = build_read_request("<<<ARCHIVO id=a nombre=b>>>\n[[p.1]]\nx\n<<<FIN ARCHIVO>>>", {"titulo": "Licitación X"},
                           model=MODEL)
    system = r["system"][0]["text"]
    assert "<<<ARCHIVO" in system and "<<<FIN ARCHIVO>>>" in system
    assert "untrusted" in system and "never instructions" in system
    content = r["messages"][0]["content"]
    assert "<<<ARCHIVO id=a nombre=b>>>" in content and "Licitación X" in content


def test_a_model_without_fallbacks_gets_neither_beta_nor_fallbacks() -> None:
    r = build_read_request("x", {}, model="claude-haiku-5-5")
    assert "fallbacks" not in r and "betas" not in r


def test_the_schema_is_closed_everywhere() -> None:
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
                assert set(node["required"]) == set(node["properties"]), node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(READ_SCHEMA)
    assert set(READ_SCHEMA["properties"]["resumen"]["properties"]) == set(RESUMEN_FIELDS)
    tipo = READ_SCHEMA["properties"]["checklist"]["items"]["properties"]["tipo"]
    assert tipo["enum"] == ["formulario", "documento", "certificado", "garantia", "declaracion", "otro"]


def test_the_streaming_reader_returns_the_final_message() -> None:
    final = object()
    seen = {}

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return final

    class Messages:
        def stream(self, **request):
            seen.update(request)
            return Stream()

    client = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
    read = streaming_reader("k", client=client)
    assert read({"model": MODEL, "max_tokens": 1}) is final
    assert seen == {"model": MODEL, "max_tokens": 1}


# ── the answer ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stop,code", [("refusal", "model_refused"), ("max_tokens", "model_truncated")])
def test_a_refused_or_truncated_answer_fails_by_code(stop, code) -> None:
    with pytest.raises(ReadFailed) as exc:
        parse_read(_message({"resumen": {}}, stop=stop))
    assert exc.value.code == code


@pytest.mark.parametrize("text", ["no es json", "[]"])
def test_an_answer_that_is_not_an_object_fails(text) -> None:
    with pytest.raises(ReadFailed) as exc:
        parse_read(_message(text=text))
    assert exc.value.code == "model_answer_invalid"


def test_invented_citation_dropped_and_flagged() -> None:
    out = validate_read(answer_with(cita={"archivo_id": "nope", "ubicacion": "p.99"}), [annex("a", ["p.1"])])
    assert out["resumen"]["cierre"]["citas"] == [] and out["resumen"]["cierre"]["sin_cita"] is True
    assert out["checklist"][0]["fuente"] is None and out["checklist"][0]["sin_cita"] is True
    assert out["items"][0]["citas"] == [] and out["items"][0]["sin_cita"] is True


def test_a_known_file_with_an_unknown_page_is_also_dropped() -> None:
    out = validate_read(answer_with(cita={"archivo_id": "a", "ubicacion": "p.2"}), [annex("a", ["p.1"])])
    assert out["resumen"]["objeto"]["citas"] == [] and out["resumen"]["objeto"]["sin_cita"] is True


def test_a_real_citation_is_kept_even_written_with_its_brackets() -> None:
    out = validate_read(answer_with(cita={"archivo_id": "a", "ubicacion": "[[p.1]]"}), [annex("a", ["p.1"])])
    assert out["resumen"]["cierre"] == {"texto": "Cierra el 30 de octubre",
                                        "citas": [{"archivo_id": "a", "ubicacion": "p.1"}], "sin_cita": False}
    assert out["checklist"][0]["fuente"] == {"archivo_id": "a", "ubicacion": "p.1"}
    assert out["checklist"][0]["sin_cita"] is False
    assert out["items"][0]["citas"] == [{"archivo_id": "a", "ubicacion": "p.1"}]


def test_a_rendered_locator_is_matched_as_the_model_saw_it() -> None:
    # A sheet name is neutralised when rendered (`<<<` → `‹‹‹`); the model cites what it saw.
    out = validate_read(answer_with(cita={"archivo_id": "x", "ubicacion": "‹‹‹Hoja!A1:B2"}),
                        [annex("x", ["<<<Hoja!A1:B2"], filename="items.xlsx")])
    assert out["resumen"]["cierre"]["citas"] == [{"archivo_id": "x", "ubicacion": "‹‹‹Hoja!A1:B2"}]


def test_a_missing_summary_field_is_empty_and_flagged() -> None:
    answer = answer_with(cita={"archivo_id": "a", "ubicacion": "p.1"})
    del answer["resumen"]["garantia_seriedad"]
    out = validate_read(answer, [annex("a", ["p.1"])])
    assert out["resumen"]["garantia_seriedad"] == {"texto": "", "citas": [], "sin_cita": True}


def test_closed_values_and_ids_the_worker_did_not_offer_are_cleared() -> None:
    answer = answer_with(cita={"archivo_id": "a", "ubicacion": "p.1"})
    answer["checklist"][0].update(tipo="inventado", archivo_formulario_id="otro-archivo", requiere_firma="si")
    answer["checklist"].append({"documento": "Formulario oferta", "tipo": "formulario", "requiere_firma": False,
                                "archivo_formulario_id": "a", "fuente": {"archivo_id": "a", "ubicacion": "p.1"}})
    out = validate_read(answer, [annex("a", ["p.1"])])
    first, second = out["checklist"][:2]
    assert first["tipo"] == "otro" and first["archivo_formulario_id"] is None and first["requiere_firma"] is False
    assert second["archivo_formulario_id"] == "a"


def test_an_annex_that_needs_a_converter_becomes_a_checklist_item() -> None:
    answer = answer_with(cita={"archivo_id": "a", "ubicacion": "p.1"})
    doc = annex("legacy", [], outcome=v1_reuse.OUTCOME_NEEDS_CONVERTER, filename="anexo 3.doc",
                warnings=["no_safe_converter_for:doc"])
    out = validate_read(answer, [annex("a", ["p.1"]), doc])
    [added] = [c for c in out["checklist"] if c.get("archivo_formulario_id") == "legacy"]
    assert added["documento"] == f"{CONVERT_TO_WORD}: anexo 3.doc"
    assert CONVERT_TO_WORD == "Falta: convertir a Word (.docx)"
    assert added["fuente"] == {"archivo_id": "legacy", "ubicacion": ""} and added["falta"] == "convertir_a_word"


def test_an_unreadable_annex_is_never_dropped_silently() -> None:
    answer = answer_with(cita={"archivo_id": "a", "ubicacion": "p.1"})
    broken = annex("rota", [], outcome=v1_reuse.OUTCOME_CORRUPT, filename="dañado.pdf", warnings=["corrupt"])
    out = validate_read(answer, [annex("a", ["p.1"]), broken])
    [added] = [c for c in out["checklist"] if c.get("archivo_formulario_id") == "rota"]
    assert added["falta"] == "no_se_pudo_leer" and "dañado.pdf" in added["documento"]


def test_lists_and_texts_are_bounded() -> None:
    answer = answer_with(cita={"archivo_id": "a", "ubicacion": "p.1"})
    answer["resumen"]["objeto"]["texto"] = "x" * 50_000
    answer["items"] = answer["items"] * 2000
    out = validate_read(answer, [annex("a", ["p.1"])])
    assert len(out["resumen"]["objeto"]["texto"]) <= 4000
    assert len(out["items"]) <= 500
