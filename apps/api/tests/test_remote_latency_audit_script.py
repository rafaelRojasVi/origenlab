"""Unit tests for scripts/remote_latency_audit.py helpers (no network)."""

from __future__ import annotations

import importlib.util
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "remote_latency_audit.py"
_SPEC = importlib.util.spec_from_file_location("remote_latency_audit", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_latency = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _latency
_SPEC.loader.exec_module(_latency)

RemoteLatencyAuditError = _latency.RemoteLatencyAuditError
SKIP_MESSAGE = _latency.SKIP_MESSAGE
LATENCY_ENDPOINTS = _latency.LATENCY_ENDPOINTS
EndpointLatencySummary = _latency.EndpointLatencySummary
LatencyEndpoint = _latency.LatencyEndpoint
TimedResponse = _latency.TimedResponse
audit_endpoint_latency = _latency.audit_endpoint_latency
build_request_headers = _latency.build_request_headers
format_latency_line = _latency.format_latency_line
latency_cold_start_budget_ms = _latency.latency_cold_start_budget_ms
latency_timeout_seconds = _latency.latency_timeout_seconds
latency_warm_budget_ms = _latency.latency_warm_budget_ms
latency_warm_runs = _latency.latency_warm_runs
main = _latency.main
safe_error_snippet = _latency.safe_error_snippet
timed_get = _latency.timed_get


def _ok_response(*, request_id: str = "req-1") -> MagicMock:
    response = MagicMock()
    response.status = 200
    response.headers = {"x-request-id": request_id}
    response.read.return_value = b'{"ok":true}'
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_main_skips_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    assert main() == 0
    captured = capsys.readouterr()
    assert SKIP_MESSAGE in captured.out


def test_build_request_headers_does_not_leak_secret_in_main_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client-id-value")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "super-secret-token")
    monkeypatch.setenv("ORIGENLAB_REMOTE_LATENCY_RUNS", "0")

    def _fast_ok(*_args: object, **_kwargs: object) -> TimedResponse:
        return TimedResponse(status=200, elapsed_ms=1.0, request_id="rid-1", error_snippet=None)

    monkeypatch.setattr(_latency, "timed_get", _fast_ok)
    assert main() == 0
    captured = capsys.readouterr()
    assert "super-secret-token" not in captured.out
    assert "super-secret-token" not in captured.err
    headers = build_request_headers("client-id-value", "super-secret-token")
    assert headers["CF-Access-Client-Secret"] == "super-secret-token"


def test_timed_get_measures_elapsed_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    perf_values = iter([1.0, 1.075])
    monkeypatch.setattr(_latency.time, "perf_counter", lambda: next(perf_values))
    with patch.object(_latency, "_fetch_get_once", return_value=_latency.RemoteResponse(
        status=200,
        headers={"x-request-id": "rid-1"},
        body_text='{"ok":true}',
    )):
        result = timed_get("https://api.origenlab.cl/health", {}, timeout=30)
    assert result.status == 200
    assert result.elapsed_ms == pytest.approx(75.0)
    assert result.request_id == "rid-1"


def test_timed_get_uses_get_method_only() -> None:
    with patch("urllib.request.urlopen") as urlopen_mock:
        urlopen_mock.return_value = _ok_response()
        timed_get("https://api.origenlab.cl/health", {"Accept": "application/json"}, timeout=5)
    request = urlopen_mock.call_args[0][0]
    assert isinstance(request, urllib.request.Request)
    assert request.get_method() == "GET"


def test_audit_endpoint_latency_warm_non_200_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_latency, "timed_get", lambda *_args, **_kwargs: TimedResponse(
        status=503,
        elapsed_ms=12.0,
        request_id="rid-503",
        error_snippet="service unavailable",
    ))
    with pytest.raises(RemoteLatencyAuditError, match="warm run expected HTTP 200, got 503"):
        audit_endpoint_latency(
            LatencyEndpoint("GET /health", "/health"),
            base_url="https://api.origenlab.cl",
            headers={},
            timeout=30,
            warm_runs=1,
            warm_budget_ms=2500.0,
            cold_start_budget_ms=45000.0,
        )


def test_audit_endpoint_latency_cold_timeout_does_not_fail_when_warm_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = {"n": 0}

    def _fake_timed_get(*_args: object, **_kwargs: object) -> TimedResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return TimedResponse(
                status=0,
                elapsed_ms=30000.0,
                request_id=None,
                error_snippet="timed out",
            )
        return TimedResponse(status=200, elapsed_ms=120.0, request_id="warm-rid", error_snippet=None)

    monkeypatch.setattr(_latency, "timed_get", _fake_timed_get)
    summary = audit_endpoint_latency(
        LatencyEndpoint("GET /health", "/health"),
        base_url="https://api.origenlab.cl",
        headers={},
        timeout=30,
        warm_runs=2,
        warm_budget_ms=2500.0,
        cold_start_budget_ms=45000.0,
    )
    assert summary.status == 200
    assert summary.first_ms == pytest.approx(30000.0)
    assert summary.warm_max_ms == pytest.approx(120.0)
    assert summary.request_id == "warm-rid"
    captured = capsys.readouterr()
    assert "cold probe got status 0 (timed out)" in captured.err


def test_audit_endpoint_latency_cold_non_200_does_not_fail_when_warm_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = {"n": 0}

    def _fake_timed_get(*_args: object, **_kwargs: object) -> TimedResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return TimedResponse(
                status=502,
                elapsed_ms=800.0,
                request_id="cold-rid",
                error_snippet="bad gateway",
            )
        return TimedResponse(status=200, elapsed_ms=90.0, request_id="warm-rid", error_snippet=None)

    monkeypatch.setattr(_latency, "timed_get", _fake_timed_get)
    summary = audit_endpoint_latency(
        LatencyEndpoint("GET /health", "/health"),
        base_url="https://api.origenlab.cl",
        headers={},
        timeout=30,
        warm_runs=1,
        warm_budget_ms=2500.0,
        cold_start_budget_ms=45000.0,
    )
    assert summary.status == 200
    assert summary.request_id == "cold-rid"
    captured = capsys.readouterr()
    assert "cold probe got status 502 (bad gateway)" in captured.err


def test_audit_endpoint_latency_warm_timeout_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake_timed_get(*_args: object, **_kwargs: object) -> TimedResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return TimedResponse(status=200, elapsed_ms=50.0, request_id="cold", error_snippet=None)
        return TimedResponse(status=0, elapsed_ms=30000.0, request_id=None, error_snippet="timed out")

    monkeypatch.setattr(_latency, "timed_get", _fake_timed_get)
    with pytest.raises(RemoteLatencyAuditError, match="warm run expected HTTP 200, got 0"):
        audit_endpoint_latency(
            LatencyEndpoint("GET /health", "/health"),
            base_url="https://api.origenlab.cl",
            headers={},
            timeout=30,
            warm_runs=1,
            warm_budget_ms=2500.0,
            cold_start_budget_ms=45000.0,
        )


def test_audit_endpoint_latency_fails_when_warm_runs_zero_and_cold_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_latency, "timed_get", lambda *_args, **_kwargs: TimedResponse(
        status=0,
        elapsed_ms=30000.0,
        request_id=None,
        error_snippet="timed out",
    ))
    with pytest.raises(RemoteLatencyAuditError, match="cold run expected HTTP 200, got 0"):
        audit_endpoint_latency(
            LatencyEndpoint("GET /health", "/health"),
            base_url="https://api.origenlab.cl",
            headers={},
            timeout=30,
            warm_runs=0,
            warm_budget_ms=2500.0,
            cold_start_budget_ms=45000.0,
        )


def test_audit_endpoint_latency_fails_when_warm_run_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake_timed_get(*_args: object, **_kwargs: object) -> TimedResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return TimedResponse(status=200, elapsed_ms=100.0, request_id="rid-1", error_snippet=None)
        return TimedResponse(status=200, elapsed_ms=5000.0, request_id="rid-2", error_snippet=None)

    monkeypatch.setattr(_latency, "timed_get", _fake_timed_get)
    with pytest.raises(RemoteLatencyAuditError, match="warm run .* exceeds budget"):
        audit_endpoint_latency(
            LatencyEndpoint("GET /health", "/health"),
            base_url="https://api.origenlab.cl",
            headers={},
            timeout=30,
            warm_runs=1,
            warm_budget_ms=2500.0,
            cold_start_budget_ms=45000.0,
        )


def test_audit_endpoint_latency_treats_first_run_separately_from_warm_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake_timed_get(*_args: object, **_kwargs: object) -> TimedResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return TimedResponse(status=200, elapsed_ms=60000.0, request_id="cold", error_snippet=None)
        return TimedResponse(status=200, elapsed_ms=120.0, request_id="warm", error_snippet=None)

    monkeypatch.setattr(_latency, "timed_get", _fake_timed_get)
    summary = audit_endpoint_latency(
        LatencyEndpoint("GET /health", "/health"),
        base_url="https://api.origenlab.cl",
        headers={},
        timeout=30,
        warm_runs=2,
        warm_budget_ms=2500.0,
        cold_start_budget_ms=45000.0,
    )
    assert summary.first_ms == pytest.approx(60000.0)
    assert summary.warm_max_ms == pytest.approx(120.0)


def test_latency_endpoints_include_expected_paths() -> None:
    paths = [endpoint.path for endpoint in LATENCY_ENDPOINTS]
    assert paths == [
        "/health",
        "/operator/status",
        "/operator/automation-status",
        "/cases/warm?limit=3",
        "/cases/warm?limit=100",
        "/operator/procurement/status",
        "/emails/recent?limit=3",
        "/emails/recent?limit=100",
    ]


def test_env_parsing_is_defensive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGENLAB_REMOTE_LATENCY_RUNS", "not-a-number")
    assert latency_warm_runs() == _latency.DEFAULT_WARM_RUNS
    monkeypatch.setenv("ORIGENLAB_REMOTE_LATENCY_TIMEOUT_SECONDS", "bad")
    assert latency_timeout_seconds() == _latency.DEFAULT_TIMEOUT_SECONDS
    monkeypatch.setenv("ORIGENLAB_REMOTE_LATENCY_BUDGET_MS", "nope")
    assert latency_warm_budget_ms() == _latency.DEFAULT_WARM_BUDGET_MS
    monkeypatch.setenv("ORIGENLAB_REMOTE_LATENCY_COLD_START_BUDGET_MS", "???")
    assert latency_cold_start_budget_ms() == _latency.DEFAULT_COLD_START_BUDGET_MS


def test_format_latency_line_includes_timing_fields() -> None:
    line = format_latency_line(
        EndpointLatencySummary(
            label="GET /health",
            path="/health",
            status=200,
            first_ms=120.5,
            warm_min_ms=45.0,
            warm_avg_ms=50.0,
            warm_max_ms=55.0,
            request_id="rid-abc",
        )
    )
    assert "first_ms=120.5" in line
    assert "min_ms=45.0" in line
    assert "request_id=rid-abc" in line


def test_safe_error_snippet_truncates_long_body() -> None:
    snippet = safe_error_snippet("x" * 200)
    assert len(snippet) <= 120
    assert snippet.endswith("...")


def test_main_runs_all_endpoints_when_healthy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("ORIGENLAB_REMOTE_LATENCY_RUNS", "1")

    def _fast_ok(*_args: object, **_kwargs: object) -> TimedResponse:
        return TimedResponse(status=200, elapsed_ms=10.0, request_id="rid-1", error_snippet=None)

    monkeypatch.setattr(_latency, "timed_get", _fast_ok)
    assert main() == 0
    captured = capsys.readouterr()
    assert "remote latency audit:" in captured.out
    assert captured.out.count("request_id=rid-1") == len(LATENCY_ENDPOINTS)
    assert "client-secret" not in captured.out
    assert "ok: remote production latency audit passed" in captured.out
