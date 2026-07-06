"""CLI tests for run_contact_hunt_web_server (safe localhost defaults)."""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "leads" / "advanced" / "run_contact_hunt_web_server.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("run_contact_hunt_web_server", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_main(mod, argv: list[str]) -> tuple[int | None, str, str]:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    code: int | None = 0
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = mod.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, out_buf.getvalue(), err_buf.getvalue()


def _seed_reports_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "leads_shortlist.csv").write_text("id_lead\n1\n", encoding="utf-8")


def test_default_host_is_localhost_when_password_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    _seed_reports_dir(reports_dir)
    captured: dict[str, object] = {}

    def fake_run_server(host: str, port: int, user: str, pw: str, reports_dir_arg: Path) -> None:
        captured.update(host=host, port=port, user=user, pw=pw, reports_dir=reports_dir_arg)

    monkeypatch.setattr(mod, "run_server", fake_run_server)
    monkeypatch.setenv("LEADS_WEB_PASS", "strong-secret")

    code, stdout, _stderr = _run_main(
        mod,
        ["--reports-dir", str(reports_dir), "--port", "9001"],
    )
    assert code == 0
    assert captured["host"] == "127.0.0.1"
    assert "Localhost only" in stdout


def test_lan_host_is_all_interfaces_when_password_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    _seed_reports_dir(reports_dir)
    captured: dict[str, object] = {}

    def fake_run_server(host: str, port: int, user: str, pw: str, reports_dir_arg: Path) -> None:
        captured["host"] = host

    monkeypatch.setattr(mod, "run_server", fake_run_server)
    monkeypatch.setenv("LEADS_WEB_PASS", "strong-secret")

    code, stdout, _stderr = _run_main(
        mod,
        ["--lan", "--reports-dir", str(reports_dir)],
    )
    assert code == 0
    assert captured["host"] == "0.0.0.0"
    assert "LAN mode" in stdout


def test_missing_password_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    _seed_reports_dir(reports_dir)
    monkeypatch.setattr(mod, "run_server", lambda *a, **k: None)
    monkeypatch.delenv("LEADS_WEB_PASS", raising=False)

    code, _stdout, stderr = _run_main(
        mod,
        ["--reports-dir", str(reports_dir)],
    )
    assert code == 2
    assert "Set LEADS_WEB_PASS or pass --pass." in stderr


def test_default_password_rejected_without_allow_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    _seed_reports_dir(reports_dir)
    monkeypatch.setattr(mod, "run_server", lambda *a, **k: None)

    code, _stdout, stderr = _run_main(
        mod,
        ["--reports-dir", str(reports_dir), "--pass", "leads123"],
    )
    assert code == 2
    assert "leads123" in stderr
    assert "allow-default-password" in stderr.lower()


def test_allow_default_password_allows_leads123(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    _seed_reports_dir(reports_dir)
    captured: dict[str, object] = {}

    def fake_run_server(host: str, port: int, user: str, pw: str, reports_dir_arg: Path) -> None:
        captured["pw"] = pw

    monkeypatch.setattr(mod, "run_server", fake_run_server)

    code, _stdout, _stderr = _run_main(
        mod,
        ["--reports-dir", str(reports_dir), "--pass", "leads123", "--allow-default-password"],
    )
    assert code == 0
    assert captured["pw"] == "leads123"


def test_lan_and_host_together_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    _seed_reports_dir(reports_dir)
    monkeypatch.setattr(mod, "run_server", lambda *a, **k: None)
    monkeypatch.setenv("LEADS_WEB_PASS", "strong-secret")

    code, _stdout, stderr = _run_main(
        mod,
        ["--lan", "--host", "192.168.1.10", "--reports-dir", str(reports_dir)],
    )
    assert code == 2
    assert "cannot be used together" in stderr


def test_missing_reports_dir_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    missing = tmp_path / "missing"
    monkeypatch.setattr(mod, "run_server", lambda *a, **k: None)
    monkeypatch.setenv("LEADS_WEB_PASS", "strong-secret")

    code, _stdout, stderr = _run_main(
        mod,
        ["--reports-dir", str(missing)],
    )
    assert code == 2
    assert "reports_dir does not exist" in stderr


def test_run_server_receives_reports_dir_from_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "custom-reports"
    _seed_reports_dir(reports_dir)
    captured: dict[str, object] = {}

    def fake_run_server(host: str, port: int, user: str, pw: str, reports_dir_arg: Path) -> None:
        captured["reports_dir"] = reports_dir_arg

    monkeypatch.setattr(mod, "run_server", fake_run_server)
    monkeypatch.setenv("LEADS_WEB_PASS", "strong-secret")

    code, _stdout, _stderr = _run_main(
        mod,
        ["--reports-dir", str(reports_dir)],
    )
    assert code == 0
    assert captured["reports_dir"] == reports_dir


def test_password_not_printed_in_startup_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    _seed_reports_dir(reports_dir)
    secret = "super-secret-not-in-logs"
    monkeypatch.setattr(mod, "run_server", lambda *a, **k: None)
    monkeypatch.setenv("LEADS_WEB_PASS", secret)

    _code, stdout, stderr = _run_main(
        mod,
        ["--reports-dir", str(reports_dir)],
    )
    combined = stdout + stderr
    assert secret not in combined
    assert "leads123" not in combined


def test_sanitize_header_value_rejects_crlf() -> None:
    mod = _load_script()
    with pytest.raises(ValueError, match="CR or LF"):
        mod.sanitize_header_value("evil\r\nSet-Cookie: x")


def test_download_content_disposition_is_constant() -> None:
    mod = _load_script()
    assert mod.download_content_disposition() == 'attachment; filename="leads.csv"'
    assert mod.download_content_disposition() == mod.DOWNLOAD_CONTENT_DISPOSITION
    evil = 'evil\r\nSet-Cookie: x"; leads_ok.csv'
    assert mod.download_content_disposition() == mod.download_content_disposition()
    assert evil not in mod.download_content_disposition()


def test_safe_csv_basename_rejects_path_traversal_and_non_leads_names() -> None:
    mod = _load_script()
    assert mod.safe_csv_basename("../../etc/passwd") is None
    assert mod.safe_csv_basename("not_leads.csv") is None
    assert mod.safe_csv_basename("leads_shortlist.csv") == "leads_shortlist.csv"
    assert mod.safe_csv_basename('evil\r\nSet-Cookie: x"; leads_ok.csv') is None


def test_serve_csv_uses_constant_headers(tmp_path: Path) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    csv_path = reports_dir / "leads_shortlist.csv"
    csv_path.write_text("id_lead\n1\n", encoding="utf-8")

    handler = mod.LeadsRequestHandler.__new__(mod.LeadsRequestHandler)
    handler.reports_dir = reports_dir

    sent_headers: list[tuple[str, str]] = []
    handler.send_response = lambda code: None  # type: ignore[method-assign]
    handler.send_header = lambda name, value: sent_headers.append((name, value))  # type: ignore[method-assign]
    handler.end_headers = lambda: None  # type: ignore[method-assign]
    handler.wfile = type("W", (), {"write": staticmethod(lambda *_: None)})()  # type: ignore[method-assign]

    handler._serve_csv("leads_shortlist.csv")

    header_map = dict(sent_headers)
    assert header_map["Content-Disposition"] == 'attachment; filename="leads.csv"'
    assert header_map["Content-Type"] == mod.DOWNLOAD_CONTENT_TYPE
    assert "leads_shortlist.csv" not in header_map["Content-Disposition"]


def test_serve_csv_crlf_filename_does_not_emit_headers(tmp_path: Path) -> None:
    mod = _load_script()
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "leads_shortlist.csv").write_text("id_lead\n1\n", encoding="utf-8")

    handler = mod.LeadsRequestHandler.__new__(mod.LeadsRequestHandler)
    handler.reports_dir = reports_dir

    sent_headers: list[tuple[str, str]] = []
    handler.send_response = lambda code: None  # type: ignore[method-assign]
    handler.send_header = lambda name, value: sent_headers.append((name, value))  # type: ignore[method-assign]
    handler.end_headers = lambda: None  # type: ignore[method-assign]
    handler.wfile = type("W", (), {"write": staticmethod(lambda *_: None)})()  # type: ignore[method-assign]

    handler._serve_csv('evil\r\nSet-Cookie: x"; leads_shortlist.csv')

    assert sent_headers == []
