"""Unit tests for scripts/remote_response_audit.py helpers (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "remote_response_audit.py"
_SPEC = importlib.util.spec_from_file_location("remote_response_audit", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_remote = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _remote
_SPEC.loader.exec_module(_remote)

RemoteAuditError = _remote.RemoteAuditError
RemoteResponse = _remote.RemoteResponse
SKIP_MESSAGE = _remote.SKIP_MESSAGE
audit_response = _remote.audit_response
cf_credentials_from_env = _remote.cf_credentials_from_env
fetch_get = _remote.fetch_get
main = _remote.main
require_recent_emails_contract = _remote.require_recent_emails_contract
require_warm_cases_contract = _remote.require_warm_cases_contract
require_error_envelope = _remote.require_error_envelope
require_meta_items = _remote.require_meta_items
require_request_id_header = _remote.require_request_id_header
request_retries = _remote.request_retries
request_retry_backoff_seconds = _remote.request_retry_backoff_seconds
request_timeout_seconds = _remote.request_timeout_seconds
scan_forbidden_leaks_in_text = _remote.scan_forbidden_leaks_in_text


def _valid_recent_emails_body(**overrides: object) -> dict[str, object]:
    items = [
        {
            "email_id": 42,
            "date_iso": "2026-05-19T10:00:00-04:00",
            "subject_preview": "Cotización equipo",
            "sender_preview": "client@example.cl",
            "folder_hint": "[Gmail]/Enviados",
            "source_file": None,
            "has_positive_signal": True,
            "has_suppression_signal": False,
        }
    ]
    body: dict[str, object] = {
        "meta": {
            "data_source": "postgres_mirror",
            "read_only": True,
        },
        "items": items,
        "total_returned": len(items),
        "days_window": 7,
        "scope_note": "",
        "enrichment_available": True,
        "reduced_mode": False,
    }
    body.update(overrides)
    return body


def _valid_warm_body(**overrides: object) -> dict[str, object]:
    items = [
        {
            "case_id": "case:42",
            "last_email_id": 1001,
            "category": "client_response",
            "status": "open",
            "subject": "Need quote",
        }
    ]
    body: dict[str, object] = {
        "meta": {
            "data_source": "postgres_mirror",
            "read_only": True,
            "reduced_mode": False,
            "count": len(items),
            "enrichment_available": True,
            "note": "",
        },
        "items": items,
    }
    body.update(overrides)
    return body


def test_require_request_id_header_missing_fails() -> None:
    try:
        require_request_id_header({})
    except RemoteAuditError as exc:
        assert "x-request-id" in str(exc).lower()
    else:
        raise AssertionError("expected RemoteAuditError")


def test_require_request_id_header_accepts_lowercase_header() -> None:
    assert require_request_id_header({"x-request-id": "abc123"}) == "abc123"


def test_scan_forbidden_leaks_in_text_detects_home_path() -> None:
    hits = scan_forbidden_leaks_in_text('{"sqlite_path":"/home/ops/data.sqlite"}')
    assert "/home/" in hits


def test_scan_forbidden_leaks_in_text_empty_when_safe() -> None:
    assert scan_forbidden_leaks_in_text('{"ok":true,"meta":{"read_only":true}}') == []


def test_require_meta_items_requires_meta_and_items() -> None:
    try:
        require_meta_items({"items": []})
    except RemoteAuditError as exc:
        assert "meta" in str(exc).lower()
    else:
        raise AssertionError("expected RemoteAuditError")


def test_require_meta_items_accepts_valid_list_shape() -> None:
    require_meta_items({"meta": {"read_only": True}, "items": []})


def test_require_recent_emails_contract_accepts_valid_response() -> None:
    require_recent_emails_contract(_valid_recent_emails_body())  # type: ignore[arg-type]


def test_require_recent_emails_contract_accepts_safe_gmail_source_file() -> None:
    body = _valid_recent_emails_body()
    body["items"][0]["source_file"] = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"  # type: ignore[index]
    require_recent_emails_contract(body)  # type: ignore[arg-type]


def test_require_recent_emails_contract_total_returned_mismatch_fails() -> None:
    body = _valid_recent_emails_body()
    body["total_returned"] = 99  # type: ignore[assignment]
    with pytest.raises(RemoteAuditError, match="total_returned"):
        require_recent_emails_contract(body)  # type: ignore[arg-type]


def test_require_recent_emails_contract_enrichment_available_wrong_type_fails() -> None:
    body = _valid_recent_emails_body()
    body["enrichment_available"] = "yes"  # type: ignore[assignment]
    with pytest.raises(RemoteAuditError, match="enrichment_available"):
        require_recent_emails_contract(body)  # type: ignore[arg-type]


def test_require_recent_emails_contract_reduced_mode_wrong_type_fails() -> None:
    body = _valid_recent_emails_body()
    body["reduced_mode"] = 0  # type: ignore[assignment]
    with pytest.raises(RemoteAuditError, match="reduced_mode"):
        require_recent_emails_contract(body)  # type: ignore[arg-type]


def test_require_recent_emails_contract_unsafe_source_file_path_fails() -> None:
    body = _valid_recent_emails_body()
    body["items"][0]["source_file"] = "/mnt/data/mailbox.json"  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="source_file"):
        require_recent_emails_contract(body)  # type: ignore[arg-type]


def test_require_recent_emails_contract_internal_raw_body_field_fails() -> None:
    body = _valid_recent_emails_body()
    body["items"][0]["raw_body"] = "MIME body"  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="raw_body"):
        require_recent_emails_contract(body)  # type: ignore[arg-type]


def test_require_recent_emails_contract_raw_filesystem_path_fails() -> None:
    body = _valid_recent_emails_body()
    body["items"][0]["subject_preview"] = "see /mnt/data/mailbox"  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="/mnt/"):
        require_recent_emails_contract(body)  # type: ignore[arg-type]


def test_audit_response_recent_emails_contract() -> None:
    response = RemoteResponse(
        status=200,
        headers={"x-request-id": "rid-1"},
        body_text=json.dumps(_valid_recent_emails_body()),
    )
    audit_response(
        "GET /emails/recent?limit=3",
        response,
        "/emails/recent?limit=3",
        expect_success=True,
    )


def test_require_warm_cases_contract_accepts_valid_response() -> None:
    require_warm_cases_contract(_valid_warm_body())  # type: ignore[arg-type]


def test_require_warm_cases_contract_wrong_data_source_fails() -> None:
    body = _valid_warm_body()
    body["meta"]["data_source"] = "sqlite"  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="postgres_mirror"):
        require_warm_cases_contract(body)  # type: ignore[arg-type]


def test_require_warm_cases_contract_meta_count_mismatch_fails() -> None:
    body = _valid_warm_body()
    body["meta"]["count"] = 99  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="meta.count"):
        require_warm_cases_contract(body)  # type: ignore[arg-type]


def test_require_warm_cases_contract_missing_case_id_fails() -> None:
    body = _valid_warm_body()
    body["items"][0].pop("case_id")  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="case_id"):
        require_warm_cases_contract(body)  # type: ignore[arg-type]


def test_require_warm_cases_contract_internal_source_file_field_fails() -> None:
    body = _valid_warm_body()
    body["items"][0]["source_file"] = "/secret/thread.json"  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="source_file"):
        require_warm_cases_contract(body)  # type: ignore[arg-type]


def test_require_warm_cases_contract_raw_filesystem_path_fails() -> None:
    body = _valid_warm_body()
    body["items"][0]["subject"] = "see /home/ops/thread.json"  # type: ignore[index]
    with pytest.raises(RemoteAuditError, match="/home/"):
        require_warm_cases_contract(body)  # type: ignore[arg-type]


def test_fetch_get_timeout_raises_remote_audit_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "0")

    def _timeout(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("timed out")

    with patch("urllib.request.urlopen", side_effect=_timeout):
        with pytest.raises(RemoteAuditError, match="timed out") as exc_info:
            fetch_get("https://api.origenlab.cl/cases/warm?limit=3", {})
    assert "Traceback" not in str(exc_info.value)
    assert "after 1 attempt(s)" in str(exc_info.value)


def test_fetch_get_urlerror_raises_remote_audit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "0")

    def _url_error(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=_url_error):
        with pytest.raises(RemoteAuditError, match="connection refused"):
            fetch_get("https://api.origenlab.cl/health", {})


def test_fetch_get_retries_after_timeout_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "2")
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS", "0")

    response = MagicMock()
    response.status = 200
    response.headers = {"x-request-id": "rid-1"}
    response.read.return_value = b'{"ok":true}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
        "urllib.request.urlopen",
        side_effect=[TimeoutError("timed out"), response],
    ) as urlopen_mock:
        result = fetch_get("https://api.origenlab.cl/health", {}, label="GET /health")

    assert result.status == 200
    assert urlopen_mock.call_count == 2
    captured = capsys.readouterr()
    assert "warning: GET /health timed out after" in captured.err
    assert "retrying 1/2" in captured.err


def test_fetch_get_stops_after_configured_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "1")
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS", "0")

    def _timeout(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("timed out")

    with patch("urllib.request.urlopen", side_effect=_timeout) as urlopen_mock:
        with pytest.raises(RemoteAuditError, match="after 2 attempt\\(s\\)"):
            fetch_get("https://api.origenlab.cl/health", {}, label="GET /health")

    assert urlopen_mock.call_count == 2
    captured = capsys.readouterr()
    assert captured.err.count("retrying") == 1


def test_fetch_get_retries_urlerror_then_raises_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "1")
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS", "0")

    def _url_error(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=_url_error) as urlopen_mock:
        with pytest.raises(RemoteAuditError, match="connection refused"):
            fetch_get("https://api.origenlab.cl/health", {}, label="GET /health")

    assert urlopen_mock.call_count == 2


def test_contract_validation_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "2")
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS", "0")

    bad_warm_body = _valid_warm_body()
    bad_warm_body["items"][0].pop("case_id")  # type: ignore[index]

    response = MagicMock()
    response.status = 200
    response.headers = {"x-request-id": "rid-1"}
    response.read.return_value = json.dumps(bad_warm_body).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    warm_check = next(
        check for check in _remote.SUCCESS_CHECKS if "/cases/warm" in check.path
    )

    with patch.object(_remote, "SUCCESS_CHECKS", (warm_check,)):
        with patch.object(_remote, "ERROR_CHECKS", ()):
            with patch("urllib.request.urlopen", return_value=response) as urlopen_mock:
                with pytest.raises(RemoteAuditError, match="case_id"):
                    main()

    assert urlopen_mock.call_count == 1


def test_request_retries_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "5")
    assert request_retries() == 5
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRIES", "not-a-number")
    assert request_retries() == _remote.REQUEST_RETRIES


def test_request_retry_backoff_seconds_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS", "3.5")
    assert request_retry_backoff_seconds() == 3.5
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS", "bad")
    assert request_retry_backoff_seconds() == _remote.REQUEST_RETRY_BACKOFF_SECONDS


def test_request_timeout_seconds_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS", "45")
    assert request_timeout_seconds() == 45
    monkeypatch.setenv("ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS", "not-a-number")
    assert request_timeout_seconds() == _remote.REQUEST_TIMEOUT_SECONDS


def test_require_error_envelope_requires_request_id_matching_header() -> None:
    body = {
        "error": {
            "code": "validation_error",
            "message": "Invalid",
            "details": {},
            "request_id": "abc123",
        }
    }
    try:
        require_error_envelope(body, request_id_header="different")
    except RemoteAuditError as exc:
        assert "match" in str(exc).lower()
    else:
        raise AssertionError("expected RemoteAuditError")


def test_require_error_envelope_accepts_matching_request_id() -> None:
    body = {
        "error": {
            "code": "not_found",
            "message": "Missing",
            "details": {},
            "request_id": "req-1",
        }
    }
    assert require_error_envelope(body, request_id_header="req-1") == "req-1"


def test_audit_response_success_list_shape() -> None:
    body = _valid_warm_body(items=[])
    body["meta"]["count"] = 0  # type: ignore[index]
    body["meta"]["reduced_mode"] = True  # type: ignore[index]
    response = RemoteResponse(
        status=200,
        headers={"x-request-id": "rid-1"},
        body_text=json.dumps(body),
    )
    audit_response(
        "GET /cases/warm?limit=3",
        response,
        "/cases/warm?limit=3",
        expect_success=True,
    )


def test_audit_response_warm_cases_contract() -> None:
    response = RemoteResponse(
        status=200,
        headers={"x-request-id": "rid-1"},
        body_text=json.dumps(_valid_warm_body()),
    )
    audit_response(
        "GET /cases/warm?limit=3",
        response,
        "/cases/warm?limit=3",
        expect_success=True,
    )


def test_audit_response_fails_on_forbidden_leak() -> None:
    response = RemoteResponse(
        status=200,
        headers={"x-request-id": "rid-1"},
        body_text='{"path":"/home/ops/file"}',
    )
    with pytest.raises(RemoteAuditError, match="/home/"):
        audit_response("GET /health", response, "/health", expect_success=True)


def test_main_skips_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    assert main() == 0
    captured = capsys.readouterr()
    assert SKIP_MESSAGE in captured.out
