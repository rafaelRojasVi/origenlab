#!/usr/bin/env python3
"""Authenticated remote production latency audit (live API, Cloudflare Access).

Read-only GET timing for operator endpoints. Exits 0 with a skip message when
``CF_ACCESS_CLIENT_ID`` / ``CF_ACCESS_CLIENT_SECRET`` are unset.

Usage:
  cd apps/api
  CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \\
    uv run python scripts/remote_latency_audit.py

Optional env:
  ORIGENLAB_API_BASE_URL — API origin (default https://api.origenlab.cl)
  ORIGENLAB_REMOTE_LATENCY_RUNS — warm runs after the cold-start probe (default 3)
  ORIGENLAB_REMOTE_LATENCY_TIMEOUT_SECONDS — per-request timeout (default 30)
  ORIGENLAB_REMOTE_LATENCY_BUDGET_MS — max warm-run latency (default 2500)
  ORIGENLAB_REMOTE_LATENCY_COLD_START_BUDGET_MS — cold-start warning threshold (default 45000)

The first (cold) probe is advisory: timeouts and non-200 responses warn and the script
continues to warm runs. Warm runs enforce HTTP 200 and latency budgets. With
``ORIGENLAB_REMOTE_LATENCY_RUNS=0``, only the cold probe runs and must succeed.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Reuse small read-only helpers from the response audit script (same directory).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from remote_response_audit import (  # noqa: E402
    RemoteResponse,
    _fetch_get_once,
    base_url_from_env,
    cf_credentials_from_env,
    normalize_headers,
)

USER_AGENT = "OrigenLab-API-Latency-Audit/1.0"
DEFAULT_WARM_RUNS = 3
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_WARM_BUDGET_MS = 2500.0
DEFAULT_COLD_START_BUDGET_MS = 45000.0
ERROR_SNIPPET_MAX_LEN = 120

SKIP_MESSAGE = (
    "skip: authenticated remote latency audit requires "
    "CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET"
)


class RemoteLatencyAuditError(AssertionError):
    pass


@dataclass(frozen=True)
class LatencyEndpoint:
    label: str
    path: str


@dataclass(frozen=True)
class TimedResponse:
    status: int
    elapsed_ms: float
    request_id: str | None
    error_snippet: str | None


@dataclass(frozen=True)
class EndpointLatencySummary:
    label: str
    path: str
    status: int
    first_ms: float
    warm_min_ms: float | None
    warm_avg_ms: float | None
    warm_max_ms: float | None
    request_id: str | None


LATENCY_ENDPOINTS: tuple[LatencyEndpoint, ...] = (
    LatencyEndpoint("GET /health", "/health"),
    LatencyEndpoint("GET /operator/status", "/operator/status"),
    LatencyEndpoint("GET /operator/automation-status", "/operator/automation-status"),
    LatencyEndpoint("GET /cases/warm?limit=3", "/cases/warm?limit=3"),
    LatencyEndpoint("GET /cases/warm?limit=100", "/cases/warm?limit=100"),
    LatencyEndpoint("GET /operator/procurement/status", "/operator/procurement/status"),
    LatencyEndpoint("GET /emails/recent?limit=3", "/emails/recent?limit=3"),
    LatencyEndpoint("GET /emails/recent?limit=100", "/emails/recent?limit=100"),
)


def build_request_headers(client_id: str, client_secret: str) -> dict[str, str]:
    return {
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def _parse_positive_int(raw: str | None, default: int) -> int:
    if not raw or not raw.strip():
        return default
    try:
        return max(1, int(raw.strip()))
    except ValueError:
        return default


def _parse_non_negative_int(raw: str | None, default: int) -> int:
    if not raw or not raw.strip():
        return default
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return default


def _parse_positive_float(raw: str | None, default: float) -> float:
    if not raw or not raw.strip():
        return default
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return default


def latency_warm_runs() -> int:
    return _parse_non_negative_int(
        os.environ.get("ORIGENLAB_REMOTE_LATENCY_RUNS"),
        DEFAULT_WARM_RUNS,
    )


def latency_timeout_seconds() -> int:
    return _parse_positive_int(
        os.environ.get("ORIGENLAB_REMOTE_LATENCY_TIMEOUT_SECONDS"),
        DEFAULT_TIMEOUT_SECONDS,
    )


def latency_warm_budget_ms() -> float:
    return _parse_positive_float(
        os.environ.get("ORIGENLAB_REMOTE_LATENCY_BUDGET_MS"),
        DEFAULT_WARM_BUDGET_MS,
    )


def latency_cold_start_budget_ms() -> float:
    return _parse_positive_float(
        os.environ.get("ORIGENLAB_REMOTE_LATENCY_COLD_START_BUDGET_MS"),
        DEFAULT_COLD_START_BUDGET_MS,
    )


def request_id_from_headers(headers: dict[str, str]) -> str | None:
    request_id = headers.get("x-request-id", "").strip()
    return request_id or None


def safe_error_snippet(body_text: str, *, max_len: int = ERROR_SNIPPET_MAX_LEN) -> str:
    compact = " ".join(body_text.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def timed_get(url: str, headers: dict[str, str], *, timeout: int) -> TimedResponse:
    started = time.perf_counter()
    try:
        response = _fetch_get_once(url, headers, timeout=timeout)
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        detail = "timed out" if isinstance(exc, TimeoutError) else str(exc)
        return TimedResponse(
            status=0,
            elapsed_ms=elapsed_ms,
            request_id=None,
            error_snippet=detail[:ERROR_SNIPPET_MAX_LEN],
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    normalized = normalize_headers(response.headers)
    snippet: str | None = None
    if response.status != 200:
        snippet = safe_error_snippet(response.body_text)
    return TimedResponse(
        status=response.status,
        elapsed_ms=elapsed_ms,
        request_id=request_id_from_headers(normalized),
        error_snippet=snippet,
    )


def _warm_stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    return min(values), sum(values) / len(values), max(values)


def _warn_cold_probe(endpoint: LatencyEndpoint, cold: TimedResponse) -> None:
    if cold.status == 200:
        return
    detail = cold.error_snippet or "unknown error"
    print(
        f"warning: {endpoint.label} cold probe got status {cold.status} ({detail}); "
        "measuring warm runs",
        file=sys.stderr,
    )


def _resolve_summary_status(cold: TimedResponse, warm_runs: list[TimedResponse]) -> int:
    for warm in warm_runs:
        if warm.status == 200:
            return 200
    if warm_runs:
        return warm_runs[-1].status
    return cold.status


def _resolve_request_id(cold: TimedResponse, warm_runs: list[TimedResponse]) -> str | None:
    if cold.request_id:
        return cold.request_id
    for warm in warm_runs:
        if warm.request_id:
            return warm.request_id
    return None


def audit_endpoint_latency(
    endpoint: LatencyEndpoint,
    *,
    base_url: str,
    headers: dict[str, str],
    timeout: int,
    warm_runs: int,
    warm_budget_ms: float,
    cold_start_budget_ms: float,
) -> EndpointLatencySummary:
    url = f"{base_url}{endpoint.path}"

    cold = timed_get(url, headers, timeout=timeout)
    _warn_cold_probe(endpoint, cold)
    if cold.status == 200 and cold.elapsed_ms > cold_start_budget_ms:
        print(
            f"warning: {endpoint.label} cold-start {cold.elapsed_ms:.1f}ms "
            f"exceeds advisory budget {cold_start_budget_ms:.0f}ms",
            file=sys.stderr,
        )

    warm_responses: list[TimedResponse] = []
    for _ in range(warm_runs):
        warm = timed_get(url, headers, timeout=timeout)
        warm_responses.append(warm)
        if warm.status != 200:
            raise RemoteLatencyAuditError(
                f"{endpoint.label} warm run expected HTTP 200, got {warm.status}"
                + (f" ({warm.error_snippet})" if warm.error_snippet else "")
            )
        if warm.elapsed_ms > warm_budget_ms:
            raise RemoteLatencyAuditError(
                f"{endpoint.label} warm run {warm.elapsed_ms:.1f}ms "
                f"exceeds budget {warm_budget_ms:.0f}ms"
            )

    if warm_runs == 0 and cold.status != 200:
        raise RemoteLatencyAuditError(
            f"{endpoint.label} cold run expected HTTP 200, got {cold.status}"
            + (f" ({cold.error_snippet})" if cold.error_snippet else "")
        )

    warm_values = [run.elapsed_ms for run in warm_responses]
    warm_min, warm_avg, warm_max = _warm_stats(warm_values)
    return EndpointLatencySummary(
        label=endpoint.label,
        path=endpoint.path,
        status=_resolve_summary_status(cold, warm_responses),
        first_ms=cold.elapsed_ms,
        warm_min_ms=warm_min,
        warm_avg_ms=warm_avg,
        warm_max_ms=warm_max,
        request_id=_resolve_request_id(cold, warm_responses),
    )


def format_latency_line(summary: EndpointLatencySummary) -> str:
    def _fmt(value: float | None) -> str:
        return f"{value:.1f}" if value is not None else "—"

    request_id = summary.request_id or "—"
    return (
        f"{summary.label}  status={summary.status}  "
        f"first_ms={summary.first_ms:.1f}  "
        f"min_ms={_fmt(summary.warm_min_ms)}  "
        f"avg_ms={_fmt(summary.warm_avg_ms)}  "
        f"max_ms={_fmt(summary.warm_max_ms)}  "
        f"request_id={request_id}"
    )


def main() -> int:
    credentials = cf_credentials_from_env()
    if credentials is None:
        print(SKIP_MESSAGE)
        return 0

    base_url = base_url_from_env()
    client_id, client_secret = credentials
    headers = build_request_headers(client_id, client_secret)
    timeout = latency_timeout_seconds()
    warm_runs = latency_warm_runs()
    warm_budget_ms = latency_warm_budget_ms()
    cold_start_budget_ms = latency_cold_start_budget_ms()

    print(
        f"remote latency audit: {base_url} "
        f"(warm_runs={warm_runs}, warm_budget_ms={warm_budget_ms:.0f}, timeout_s={timeout})"
    )

    for endpoint in LATENCY_ENDPOINTS:
        summary = audit_endpoint_latency(
            endpoint,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            warm_runs=warm_runs,
            warm_budget_ms=warm_budget_ms,
            cold_start_budget_ms=cold_start_budget_ms,
        )
        print(format_latency_line(summary))

    print("ok: remote production latency audit passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteLatencyAuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
