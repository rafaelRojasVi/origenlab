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


def test_the_export_writes_one_case_per_row_with_what_a_person_would_look_at() -> None:
    from datetime import datetime, timezone

    ex = load_script("triage_export")
    at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    row = ex.case_row(("o-1", "Balanza", "quoting", at, None, None, "Universidad Ficticia", ["uni.test"], ["09147-26"],
                       ["t1"], ["ana@uni.test"], ["Balanza analítica", None], ["p-2", "p-1"]))
    assert row == {"opportunity_id": "o-1", "title": "Balanza", "stage": "quoting", "created_at": at.isoformat(),
                   "closed_at": None, "close_reason": None, "institution": "Universidad Ficticia", "domains": ["uni.test"],
                   "quote_numbers": ["09147-26"], "threads": ["t1"], "addresses": ["ana@uni.test"],
                   "interests": ["Balanza analítica"], "person_ids": ["p-1", "p-2"]}
    assert "discarded_by_correction" in ex._CASES and "120 days" in ex._CASES
    assert "provider_thread_id" in ex._MESSAGES


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


def test_truth_mode_scores_the_context_matcher_with_counts_only(tmp_path, capsys) -> None:
    ev = load_script("triage_eval")
    (tmp_path / "eml").mkdir()
    rows = [("a", make_raw(subject="Re: despacho puntas", pdf_name="Cotización 09153A.pdf"), "t-9"),
            ("b", make_raw(frm="x@proveedor.invalid", subject="Certificado de calibración"), "t-8")]
    lines = []
    for sid, raw, thread in rows:
        (tmp_path / "eml" / f"{sid}.eml").write_bytes(raw)
        lines.append(json.dumps({"source_record_id": sid, "direction": "inbound", "labels": ["INBOX"],
                                 "internal_date": "2026-10-06T10:00:00+00:00", "thread_id": thread,
                                 "sender": "ana@cliente.invalid", "recipients": ["contacto@origenlab.cl"]}))
    (tmp_path / "messages.jsonl").write_text("\n".join(lines) + "\n")
    (tmp_path / "catalog.jsonl").write_text("")
    (tmp_path / "cases.jsonl").write_text(json.dumps(
        {"opportunity_id": "o-1", "title": "Caso", "stage": "won", "closed_at": "2026-10-01T00:00:00+00:00",
         "institution": None, "domains": [], "quote_numbers": ["09153-26"], "threads": ["t-1"],
         "addresses": [], "interests": []}) + "\n")
    truth = tmp_path / "truth.jsonl"
    truth.write_text(json.dumps({"source_record_id": "a", "case_id": "o-1", "reviewed_by": "owner"}) + "\n"
                     + json.dumps({"source_record_id": "b", "case_id": None, "reviewed_by": None}) + "\n")
    assert ev.main([str(tmp_path), "--truth", str(truth)]) == 0
    out = capsys.readouterr().out
    assert "despacho" not in out and "Certificado" not in out
    score = json.loads(out.splitlines()[-1])
    assert score["event"] == "triage_eval_truth"
    assert score["existing_case_rows"] == 1 and score["top1_right"] == 1 and score["thread_only_right"] == 0
    assert score["none_rows"] == 1 and score["false_link"] == 0 and score["owner_reviewed_wrong"] == 0
    report = [json.loads(x) for x in (tmp_path / "report.jsonl").read_text().splitlines()]
    assert report[0]["case_match"]["decision"] == "exact"


def test_truth_mode_reads_the_quote_pdf_sent_earlier_on_the_thread(tmp_path, capsys) -> None:
    ev = load_script("triage_eval")
    (tmp_path / "eml").mkdir()
    rows = [("sent", "outbound", make_raw(frm="contacto@origenlab.cl", subject="Cotización", pdf_name="CN09136.pdf")),
            ("reply", "inbound", make_raw(subject="Re: Cotización — transferencia realizada"))]
    lines = []
    for sid, direction, raw in rows:
        (tmp_path / "eml" / f"{sid}.eml").write_bytes(raw)
        lines.append(json.dumps({"source_record_id": sid, "direction": direction, "labels": ["INBOX"],
                                 "internal_date": "2026-10-06T10:00:00+00:00", "thread_id": "t-1",
                                 "sender": "ana@cliente.invalid", "recipients": []}))
    (tmp_path / "messages.jsonl").write_text("\n".join(lines) + "\n")
    (tmp_path / "catalog.jsonl").write_text("")
    (tmp_path / "cases.jsonl").write_text("")
    truth = tmp_path / "truth.jsonl"
    truth.write_text(json.dumps({"source_record_id": "reply", "case_id": None, "missing_case": "x"}) + "\n")
    assert ev.main([str(tmp_path), "--truth", str(truth)]) == 0
    score = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert score["new_case_rows"] == 1 and score["new_case_proposed"] == 1


def test_truth_mode_with_a_pick_sample_scores_the_model_and_the_combination(tmp_path, capsys, monkeypatch) -> None:
    ev = load_script("triage_eval")
    monkeypatch.setenv("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", "k")
    picks: list[dict] = []

    def fake_reader(_key: str):
        def read(request):
            picks.append(request)
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=50, output_tokens=5),
                                   content=[SimpleNamespace(type="text", text=json.dumps(
                                       {"choice": "o-1", "confidence": "medium", "reason_es": "Mismo producto.",
                                        "new_case": None}))])
        return read

    monkeypatch.setattr(ev, "anthropic_reader", fake_reader)
    (tmp_path / "eml").mkdir()
    rows = [("a", make_raw(frm="ivan@prov.invalid", subject="Re: centrífuga price")),
            ("b", make_raw(frm="sam@prov.invalid", subject="Re: centrífuga lead time"))]
    lines = []
    for sid, raw in rows:
        (tmp_path / "eml" / f"{sid}.eml").write_bytes(raw)
        lines.append(json.dumps({"source_record_id": sid, "direction": "inbound", "labels": ["INBOX"],
                                 "internal_date": "2026-10-06T10:00:00+00:00", "thread_id": f"t-{sid}",
                                 "sender": f"{'paul' if sid == 'a' else 'sam'}@prov.invalid", "recipients": []}))
    (tmp_path / "messages.jsonl").write_text("\n".join(lines) + "\n")
    (tmp_path / "catalog.jsonl").write_text("")
    (tmp_path / "cases.jsonl").write_text(json.dumps(
        {"opportunity_id": "o-1", "title": "Centrífuga", "stage": "qualified", "closed_at": None, "institution": None,
         "domains": [], "quote_numbers": [], "threads": [], "addresses": [], "interests": ["Centrífuga 24 tubos"]}) + "\n")
    truth = tmp_path / "truth.jsonl"
    truth.write_text("".join(json.dumps({"source_record_id": sid, "case_id": "o-1", "reviewed_by": "owner"}) + "\n"
                             for sid in ("a", "b")))
    assert ev.main([str(tmp_path), "--truth", str(truth), "--pick-sample", "5"]) == 0
    lines_out = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    rules, combined = lines_out[1], lines_out[2]
    assert rules["top1_right"] == 0 and combined["event"] == "triage_eval_truth_combined"
    assert combined["top1_right"] == 2 and combined["pick_calls"] == 2 and combined["pick_input_tokens"] == 100
    assert combined["pick_medium_right"] == 2  # by confidence: what R12 (high only) hangs on
    # Leave-one-out: each email is shown the other's owner decision from the same domain, never its own.
    assert "Re: centrífuga lead time" in picks[0]["messages"][0]["content"]
    assert "Re: centrífuga price" not in picks[0]["messages"][0]["content"].split("<email>")[0]
