"""RequestTracingMiddleware — log format, privacy, Server-Timing, threshold logic.

Checked properties:
* Route template used in log lines, not the raw path with IDs.
* Query string never logged.
* Server-Timing response header carries ``db;dur=…;desc="N queries"``.
* Query counters reset between requests.
* Slow requests log at INFO; fast requests log at DEBUG.
* High query-count logs at INFO (N+1 signal); normal count logs at DEBUG.
* Health path never escalates to INFO.
* No query string or body in any log line.
"""
from __future__ import annotations

import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.query_counter import record_query, reset_request_stats
from origenlab_api.request_tracing import RequestTracingMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _app(*, slow_ms: int = 500, high_count: int = 25) -> FastAPI:
    """Minimal FastAPI app with only the tracing middleware."""
    app = FastAPI()
    app.add_middleware(
        RequestTracingMiddleware,
        slow_request_threshold_ms=slow_ms,
        high_query_count_threshold=high_count,
    )

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/v2/cases/{case_id}")
    def get_case(case_id: str):
        return {"case_id": case_id}

    @app.get("/v2/cases/{case_id}/items")
    def get_case_items(case_id: str, q: str = ""):
        return {"case_id": case_id, "q": q}

    return app


# ---------------------------------------------------------------------------
# Route template — not the raw path
# ---------------------------------------------------------------------------

def test_log_uses_route_template_not_raw_path(caplog) -> None:
    """Log line must contain the route template, not the raw path with the ID."""
    client = TestClient(_app())
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/v2/cases/some-uuid-1234")
    assert caplog.records, "no log record emitted"
    msg = caplog.records[0].getMessage()
    assert "/v2/cases/{case_id}" in msg, f"expected route template in: {msg!r}"
    assert "some-uuid-1234" not in msg, f"raw path ID must not appear: {msg!r}"


def test_log_contains_method_template_status_duration(caplog) -> None:
    """Log line has method, template, status, duration, query count and db time."""
    client = TestClient(_app())
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/v2/cases/abc")
    assert caplog.records
    msg = caplog.records[0].getMessage()
    # method
    assert "GET" in msg
    # route template
    assert "/v2/cases/{case_id}" in msg
    # status
    assert "200" in msg
    # duration token: \d+ms
    assert re.search(r"\d+ms", msg), f"no duration in: {msg!r}"
    # query count token: \d+q
    assert re.search(r"\d+q", msg), f"no query count in: {msg!r}"


# ---------------------------------------------------------------------------
# No query string in log
# ---------------------------------------------------------------------------

def test_query_string_not_in_log(caplog) -> None:
    """Query parameters must not appear in the logged message."""
    client = TestClient(_app())
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/v2/cases/abc/items?q=secret%40example.com")
    assert caplog.records
    msg = caplog.records[0].getMessage()
    assert "secret" not in msg, f"query string leaked into log: {msg!r}"
    assert "@" not in msg, f"query string leaked into log: {msg!r}"
    assert "/v2/cases/{case_id}/items" in msg


# ---------------------------------------------------------------------------
# Server-Timing header
# ---------------------------------------------------------------------------

def test_server_timing_header_has_db_entry() -> None:
    """Response must include a ``db;dur=…;desc="N queries"`` Server-Timing entry."""
    client = TestClient(_app())
    response = client.get("/v2/cases/abc")
    assert response.status_code == 200
    timing = response.headers.get("Server-Timing", "")
    assert "db;dur=" in timing, f"no db entry in Server-Timing: {timing!r}"
    assert 'desc="' in timing, f"no desc in Server-Timing: {timing!r}"


def test_server_timing_db_entry_format() -> None:
    """The ``db`` Server-Timing entry matches ``db;dur=D.DD;desc="N queries"``."""
    client = TestClient(_app())
    response = client.get("/health")
    timing = response.headers.get("Server-Timing", "")
    # The app;dur part comes from ResponseTimingMiddleware if present, or is absent
    # in this minimal test app (no ResponseTimingMiddleware).  The db part is ours.
    db_match = re.search(r'db;dur=(\d+\.\d{2});desc="(\d+) queries"', timing)
    assert db_match, f"db entry not found or malformed in: {timing!r}"
    dur = float(db_match.group(1))
    count = int(db_match.group(2))
    assert dur >= 0.0
    assert count >= 0


# ---------------------------------------------------------------------------
# Per-request counter isolation
# ---------------------------------------------------------------------------

def test_query_counters_reset_between_requests(caplog) -> None:
    """Each request starts with a fresh counter; counts from the previous request
    must not bleed into the next."""
    app = FastAPI()
    app.add_middleware(RequestTracingMiddleware, slow_request_threshold_ms=500)

    @app.get("/probe")
    def probe(n: int = 0):
        for _ in range(n):
            record_query(1.0)
        return {"ok": True}

    client = TestClient(app)
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/probe?n=5")   # first request: 5 simulated queries
        client.get("/probe?n=0")   # second request: 0 simulated queries

    assert len(caplog.records) == 2
    first_msg = caplog.records[0].getMessage()
    second_msg = caplog.records[1].getMessage()

    # Extract query counts (the token before "q").
    first_count_match = re.search(r"(\d+)q", first_msg)
    second_count_match = re.search(r"(\d+)q", second_msg)
    assert first_count_match, f"no query count in: {first_msg!r}"
    assert second_count_match, f"no query count in: {second_msg!r}"
    assert int(first_count_match.group(1)) == 5
    assert int(second_count_match.group(1)) == 0


# ---------------------------------------------------------------------------
# Threshold logic — INFO vs DEBUG
# ---------------------------------------------------------------------------

def test_fast_request_logs_at_debug(caplog) -> None:
    """A request faster than the threshold is logged at DEBUG, not INFO."""
    # Use a very high threshold so the test request is always "fast".
    client = TestClient(_app(slow_ms=100_000))
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/v2/cases/abc")
    assert caplog.records
    assert caplog.records[0].levelno == logging.DEBUG


def test_slow_request_logs_at_info(caplog) -> None:
    """A request that exceeds the slow threshold is logged at INFO."""
    import time

    app = FastAPI()
    app.add_middleware(
        RequestTracingMiddleware,
        slow_request_threshold_ms=1,  # 1 ms — any real request will be slower
    )

    @app.get("/slow")
    def slow_handler():
        time.sleep(0.002)  # ensure > 1 ms
        return {"ok": True}

    client = TestClient(app)
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/slow")
    assert caplog.records
    assert caplog.records[0].levelno == logging.INFO, (
        f"expected INFO for slow request, got {caplog.records[0].levelno}"
    )


def test_high_query_count_logs_at_info(caplog) -> None:
    """A request with more queries than the threshold is logged at INFO."""
    app = FastAPI()
    app.add_middleware(
        RequestTracingMiddleware,
        slow_request_threshold_ms=100_000,  # effectively no slow threshold
        high_query_count_threshold=3,
    )

    @app.get("/many")
    def many_queries():
        for _ in range(5):  # exceeds threshold of 3
            record_query(1.0)
        return {"ok": True}

    client = TestClient(app)
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/many")
    assert caplog.records
    assert caplog.records[0].levelno == logging.INFO, (
        f"expected INFO for high query count, got {caplog.records[0].levelno}"
    )


def test_normal_query_count_logs_at_debug(caplog) -> None:
    """A request with fewer queries than the threshold is logged at DEBUG."""
    app = FastAPI()
    app.add_middleware(
        RequestTracingMiddleware,
        slow_request_threshold_ms=100_000,
        high_query_count_threshold=10,
    )

    @app.get("/few")
    def few_queries():
        for _ in range(3):  # below threshold of 10
            record_query(1.0)
        return {"ok": True}

    client = TestClient(app)
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/few")
    assert caplog.records
    assert caplog.records[0].levelno == logging.DEBUG


# ---------------------------------------------------------------------------
# Health endpoint is exempt from INFO escalation
# ---------------------------------------------------------------------------

def test_health_always_debug_even_when_slow(caplog) -> None:
    """The /health path never escalates to INFO regardless of duration or count."""
    import time

    app = FastAPI()
    app.add_middleware(
        RequestTracingMiddleware,
        slow_request_threshold_ms=1,  # very low threshold
    )

    @app.get("/health")
    def health_handler():
        time.sleep(0.002)
        return {"ok": True}

    client = TestClient(app)
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/health")
    assert caplog.records
    assert caplog.records[0].levelno == logging.DEBUG, (
        "health endpoint must not log at INFO"
    )


# ---------------------------------------------------------------------------
# One log line per request
# ---------------------------------------------------------------------------

def test_one_log_line_per_request(caplog) -> None:
    """Exactly one log record is emitted per HTTP request."""
    client = TestClient(_app())
    with caplog.at_level(logging.DEBUG, logger="origenlab_api.request_tracing"):
        client.get("/health")
        client.get("/v2/cases/xyz")
    assert len(caplog.records) == 2
