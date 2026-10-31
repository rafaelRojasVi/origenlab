"""API replies leave Render compressed (2026-10-30, Render bandwidth: JSON shrinks 5-10x)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from origenlab_api.main import create_app


def _client() -> TestClient:
    app = create_app()

    @app.get("/__test/large")
    def large() -> dict:
        return {"rows": [{"name": f"Instituto Ficticio {i}", "stage": "lead"} for i in range(400)]}

    @app.get("/__test/small")
    def small() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_a_large_json_reply_is_gzipped_when_the_client_accepts_it() -> None:
    r = _client().get("/__test/large", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert len(r.json()["rows"]) == 400


def test_a_small_reply_still_arrives_intact() -> None:
    # The security middleware streams bodies, so the 1 KB floor cannot always see the size; a few
    # bytes of gzip overhead on a tiny reply are harmless. What matters is the body is right.
    r = _client().get("/__test/small", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_no_compression_without_accept_encoding() -> None:
    r = _client().get("/__test/large", headers={"Accept-Encoding": "identity"})
    assert r.headers.get("content-encoding") is None
