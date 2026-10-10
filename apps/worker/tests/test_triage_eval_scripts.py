"""The owner's offline evaluation: an exported folder in, a report out; the export refuses to
write customer mail inside the repository."""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace
from pathlib import Path

import pytest

from mailfixtures import make_raw
from scriptload import load_script


def _folder(tmp_path: Path) -> Path:
    (tmp_path / "eml").mkdir()
    msgs = [
        ("a", "inbound", make_raw(subject="Solicitud de cotización UP200Ht")),
        ("b", "inbound", make_raw(subject="Respuesta automática: Solicitud de cotización")),
        ("c", "outbound", make_raw(frm="contacto@origenlab.cl", subject="Cotización CN 1234")),
    ]
    lines = []
    for sid, direction, raw in msgs:
        (tmp_path / "eml" / f"{sid}.eml").write_bytes(raw)
        lines.append(json.dumps({"source_record_id": sid, "direction": direction, "labels": ["INBOX"],
                                 "subject": "x", "internal_date": "2026-10-06T10:00:00+00:00"}))
    (tmp_path / "messages.jsonl").write_text("\n".join(lines) + "\n")
    (tmp_path / "catalog.jsonl").write_text(json.dumps(
        {"id": "p-1", "model_number": "UP200Ht", "model_key": "UP200HT", "name": "Homogeneizador ultrasónico",
         "category": "Homogeneizadores"}) + "\n")
    return tmp_path


def test_the_offline_eval_classifies_matches_and_reports_without_the_model(tmp_path, capsys) -> None:
    ev = load_script("triage_eval")
    folder = _folder(tmp_path)
    assert ev.main([str(folder)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["classes"] == {"quote_request": 1, "auto_reply": 1, "outbound": 1}
    assert summary["to_model"] == 1 and summary["model_calls"] == 0
    report = [json.loads(x) for x in (folder / "report.jsonl").read_text().splitlines()]
    assert report[0]["candidates"] == ["UP200Ht (model_key)"] and report[0]["mentions"] == ["UP200HT"]
    assert stat.S_IMODE((folder / "report.jsonl").stat().st_mode) == 0o600


def test_the_model_sample_needs_a_key(tmp_path, monkeypatch) -> None:
    ev = load_script("triage_eval")
    monkeypatch.delenv("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="needs"):
        ev.main([str(_folder(tmp_path)), "--model-sample", "3"])


def test_the_export_refuses_a_folder_inside_the_repository() -> None:
    ex = load_script("triage_export")
    with pytest.raises(SystemExit, match="outside the repository"):
        ex.check_out_dir(Path(__file__).resolve().parent / "out")


def _answer(model: str, intent: str) -> SimpleNamespace:
    text = json.dumps({"intent": intent, "stage": "lead", "urgency": "normal", "products": [],
                       "requester_organization": None, "summary_es": f"leído por {model}", "needs_reply": True})
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def test_several_models_read_the_same_sample_and_their_agreement_is_counted(tmp_path, capsys, monkeypatch) -> None:
    ev = load_script("triage_eval")
    monkeypatch.setenv("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", "k")
    seen: list[str] = []

    def fake_reader(_key: str):
        def read(request):
            seen.append(request["model"])
            return _answer(request["model"], "quote_request" if request["model"] == "claude-opus-5-5"
                           else "technical_question")
        return read

    monkeypatch.setattr(ev, "anthropic_reader", fake_reader)
    folder = _folder(tmp_path)
    assert ev.main([str(folder), "--model-sample", "3", "--models", "claude-opus-5-5,claude-haiku-5-5"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert seen == ["claude-opus-5-5", "claude-haiku-5-5"]  # one message needs the model; each model reads it once
    assert summary["model_calls"] == 2
    assert summary["models"] == {
        "claude-opus-5-5": {"ran": 1, "input_tokens": 100, "output_tokens": 10},
        "claude-haiku-5-5": {"ran": 1, "input_tokens": 100, "output_tokens": 10},
    }
    assert summary["agreement"] == [{
        "reference": "claude-opus-5-5", "model": "claude-haiku-5-5", "compared": 1,
        "intent": 0, "stage": 1, "urgency": 1, "needs_reply": 1,
    }]
    report = [json.loads(x) for x in (folder / "report.jsonl").read_text().splitlines()]
    row = report[0]
    assert row["model_state"] == "ran" and row["reading"]["model"] == "claude-opus-5-5"
    assert row["readings"]["claude-haiku-5-5"]["intent"] == "technical_question"
    assert row["readings"]["claude-opus-5-5"]["intent"] == "quote_request"


def test_a_model_that_fails_does_not_stop_the_others(tmp_path, capsys, monkeypatch) -> None:
    ev = load_script("triage_eval")
    monkeypatch.setenv("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", "k")

    def fake_reader(_key: str):
        def read(request):
            if request["model"] == "claude-haiku-5-5":
                raise RuntimeError("boom")
            return _answer(request["model"], "quote_request")
        return read

    monkeypatch.setattr(ev, "anthropic_reader", fake_reader)
    folder = _folder(tmp_path)
    assert ev.main([str(folder), "--model-sample", "3", "--models", "claude-opus-5-5,claude-haiku-5-5"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["models"]["claude-haiku-5-5"] == {"ran": 0, "input_tokens": 0, "output_tokens": 0,
                                                     "error_RuntimeError": 1}
    assert summary["agreement"][0]["compared"] == 0
    report = [json.loads(x) for x in (folder / "report.jsonl").read_text().splitlines()]
    assert report[0]["readings"]["claude-haiku-5-5"] == "error_RuntimeError"
