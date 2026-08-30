#!/usr/bin/env python3
"""DB-3F HTTP smoke against postgres backend (TestClient, read-only)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

_FORBIDDEN_KEYS = re.compile(r"(^body$|body_preview|raw_|message_body)", re.I)


def _check_no_forbidden(obj: object, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_path = f"{path}.{k}" if path else k
            if _FORBIDDEN_KEYS.search(str(k)):
                hits.append(key_path)
            hits.extend(_check_no_forbidden(v, key_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_check_no_forbidden(v, f"{path}[{i}]"))
    return hits


def main() -> int:
    os.environ.setdefault("ORIGENLAB_API_BACKEND", "postgres")
    pg = (os.environ.get("ORIGENLAB_POSTGRES_URL") or os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip()
    if not pg:
        print("ERROR: set ORIGENLAB_POSTGRES_URL or ORIGENLAB_TEST_POSTGRES_URL", file=sys.stderr)
        return 2
    os.environ["ORIGENLAB_POSTGRES_URL"] = pg

    try:
        import psycopg
    except ImportError:
        print("ERROR: psycopg required", file=sys.stderr)
        return 2

    from fastapi.testclient import TestClient

    from origenlab_api.main import create_app
    from origenlab_api.settings import get_settings

    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    known_email = "nobody@example.com"
    with psycopg.connect(pg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT email_norm FROM api.v_contact_profile
                ORDER BY message_count DESC NULLS LAST
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row:
                known_email = str(row[0])

    report: dict[str, object] = {
        "known_email": known_email,
        "endpoints": {},
        "forbidden_field_hits": [],
    }

    checks = [
        ("GET /health", "/health", {}),
        ("GET /operator/status", "/operator/status", {}),
        ("GET /emails/recent", "/emails/recent", {"days": 7, "limit": 10}),
        ("GET /cases/warm", "/cases/warm", {"days": 30, "limit": 50, "positive_signal_only": "false"}),
        ("GET /operator/procurement/status", "/operator/procurement/status", {}),
        (f"GET /contacts/{known_email}", f"/contacts/{known_email}", {}),
    ]

    for label, path, params in checks:
        r = client.get(path, params=params)
        entry = {
            "status_code": r.status_code,
            "ok": r.status_code == 200,
        }
        if r.status_code == 200:
            data = r.json()
            entry["json"] = data
            report["forbidden_field_hits"].extend(_check_no_forbidden(data))
            if label.startswith("GET /health"):
                entry["backend"] = data.get("backend")
                entry["mode"] = data.get("mode")
            elif "items" in data:
                entry["item_count"] = len(data.get("items") or [])
                meta = data.get("meta") or {}
                entry["meta"] = {
                    "data_source": meta.get("data_source"),
                    "reduced_mode": meta.get("reduced_mode"),
                    "note": meta.get("note", "")[:200],
                    "scope_note": data.get("scope_note", "")[:200],
                }
            elif "contact" in data:
                entry["meta"] = data.get("meta")
                entry["warnings"] = data.get("warnings")
            elif "verdict" in data:
                entry["verdict"] = data.get("verdict")
                entry["outbound_readiness"] = data.get("outbound_readiness")
        else:
            entry["body"] = r.text[:500]
        report["endpoints"][label] = entry

    print(json.dumps(report, indent=2, default=str))
    forbidden = report["forbidden_field_hits"]
    if forbidden:
        print(f"FAIL: forbidden keys: {forbidden}", file=sys.stderr)
        return 1
    failed = [k for k, v in report["endpoints"].items() if not v.get("ok")]
    if failed:
        print(f"FAIL: endpoints not 200: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
