"""The owner's offline evaluation: an exported folder in, a report out; the export refuses to
write customer mail inside the repository."""

from __future__ import annotations

import json
import stat
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
