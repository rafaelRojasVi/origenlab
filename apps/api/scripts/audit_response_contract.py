#!/usr/bin/env python3
"""Human-readable audit of apps/api response contract (TestClient, no live server).

Usage:
  cd apps/api
  uv run python scripts/audit_response_contract.py

This script is intentionally small and operator-friendly: it prints a compact report
and fails on obvious contract violations (missing X-Request-ID, wrong envelope shape,
or obvious secret/path leaks).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))


FORBIDDEN_LEAK_SUBSTRINGS: tuple[str, ...] = (
    "Traceback",
    "postgres://",
    "postgresql://",
    "ORIGENLAB_POSTGRES_URL=",
    "GMAIL_REFRESH_TOKEN",
    "/home/",
    "/mnt/",
    'File "',
    "password=",
    "api_key=",
)

LIST_ENDPOINT_PREFIXES: tuple[str, ...] = (
    "/emails/recent",
    "/cases/warm",
)


class ContractAuditError(AssertionError):
    pass


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def scan_forbidden_leaks(payload: Any) -> list[str]:
    text = _json_text(payload)
    hits: list[str] = []
    for needle in FORBIDDEN_LEAK_SUBSTRINGS:
        if needle in text:
            hits.append(needle)
    return hits


def require_request_id_header(response: httpx.Response) -> str:
    request_id = (response.headers.get("X-Request-ID") or "").strip()
    if not request_id:
        raise ContractAuditError("missing X-Request-ID response header")
    return request_id


def require_json_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or isinstance(body, list):
        raise ContractAuditError(f"expected JSON object, got {type(body).__name__}")
    return body


def require_meta_items(body: dict[str, Any]) -> None:
    if "meta" not in body or "items" not in body:
        raise ContractAuditError("list endpoint must include top-level meta and items")
    if not isinstance(body.get("meta"), dict):
        raise ContractAuditError("meta must be an object")
    if not isinstance(body.get("items"), list):
        raise ContractAuditError("items must be an array")


def require_error_envelope(body: dict[str, Any], *, response: httpx.Response) -> str:
    if "error" not in body or not isinstance(body.get("error"), dict):
        raise ContractAuditError("error response must include top-level error object")
    err = body["error"]
    for key in ("code", "message", "details", "request_id"):
        if key not in err:
            raise ContractAuditError(f"error envelope missing error.{key}")
    if not isinstance(err["code"], str) or not err["code"].strip():
        raise ContractAuditError("error.code must be a non-empty string")
    if not isinstance(err["message"], str) or not err["message"].strip():
        raise ContractAuditError("error.message must be a non-empty string")
    if not isinstance(err["details"], dict):
        raise ContractAuditError("error.details must be an object")
    request_id = err["request_id"]
    if not isinstance(request_id, str) or not request_id.strip():
        raise ContractAuditError("error.request_id must be a non-empty string")

    header_id = require_request_id_header(response)
    if header_id != request_id:
        raise ContractAuditError("error.request_id must match X-Request-ID header")
    return request_id


@dataclass(frozen=True)
class AuditCheck:
    label: str
    path: str
    expect_success: bool


SUCCESS_CHECKS: tuple[AuditCheck, ...] = (
    AuditCheck("GET /health", "/health", True),
    AuditCheck("GET /operator/status", "/operator/status", True),
    AuditCheck("GET /operator/automation-status", "/operator/automation-status", True),
    AuditCheck("GET /emails/recent", "/emails/recent?limit=5", True),
    AuditCheck("GET /cases/warm", "/cases/warm?limit=5&positive_signal_only=false", True),
)

ERROR_CHECKS: tuple[AuditCheck, ...] = (
    AuditCheck(
        "GET /cases/warm?category=not_a_real_category",
        "/cases/warm?category=not_a_real_category",
        False,
    ),
    AuditCheck("GET /cases/warm?limit=0", "/cases/warm?limit=0", False),
    AuditCheck("GET /contacts/not-an-email", "/contacts/not-an-email", False),
    AuditCheck("GET /definitely-not-a-route", "/definitely-not-a-route", False),
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_audit_client() -> "tuple[object, object]":
    """Return (TestClient, cleanup ctx) with a minimal deterministic sqlite/active_current."""
    from fastapi.testclient import TestClient

    from origenlab_api.main import create_app
    from origenlab_api.settings import Settings, get_settings

    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    active = root / "current"
    active.mkdir(parents=True, exist_ok=True)

    _write_json(
        active / "manifest.json",
        {
            "known_warnings": [],
            "canonical_files": [],
            "campaign_mode": "equipment_first",
            "current_operator_focus": "audit",
            "operator_notes": {"fastlab": {"outreach_state": "not_contacted"}},
        },
    )

    db = root / "contract.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            date_iso TEXT,
            source_file TEXT,
            folder TEXT,
            sender TEXT,
            subject TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO emails (date_iso, source_file, folder, sender, subject) VALUES (?, ?, ?, ?, ?)",
        (
            "2026-06-11T10:00:00-04:00",
            "gmail:contacto@origenlab.cl/INBOX",
            "INBOX",
            "Kelly Liu <kelly@supplier.com>",
            "Re: centrifuga laboratorio",
        ),
    )
    conn.commit()
    conn.close()

    # Ensure automation-status has state files to read.
    _write_json(
        active / "daily_core_run_manifest.json",
        {
            "schema_version": 1,
            "workflow": "daily-core",
            "generated_at_utc": "2026-06-10T18:12:48+00:00",
            "status": "success",
            "returncode": 0,
            "steps": [{"label": "gmail-ingest", "returncode": 0}] * 8,
        },
    )
    _write_json(
        active / "mail_auto_refresh_state.json",
        {
            "dirty": False,
            "last_result": "no_change",
            "last_successful_refresh_at": "2026-06-10T18:12:48+00:00",
            "last_seen_inbox_total": 403,
            "last_seen_sent_total": 971,
            "consecutive_failures": 0,
        },
    )
    _write_json(
        active / "dashboard_auto_mirror_state.json",
        {
            "last_result": "success",
            "last_successful_mirror_at": "2026-06-10T18:18:33+00:00",
            "last_mirrored_daily_core_generated_at": "2026-06-10T18:12:48+00:00",
            "consecutive_failures": 0,
        },
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=db,
        active_current=active,
    )
    return TestClient(app, raise_server_exceptions=False), td


def audit_response(label: str, response: httpx.Response, *, expect_success: bool) -> None:
    request_id = require_request_id_header(response)
    body = require_json_object(response.json())

    leak_hits = scan_forbidden_leaks(body)
    if leak_hits:
        raise ContractAuditError(f"forbidden leak substrings in response: {sorted(leak_hits)}")

    if expect_success:
        if response.status_code != 200:
            raise ContractAuditError(f"expected HTTP 200, got {response.status_code}")
        if any(response.request.url.path.startswith(prefix) for prefix in LIST_ENDPOINT_PREFIXES):
            require_meta_items(body)
        print(f"✓ {label} {response.status_code} request_id={request_id}")
        keys = ", ".join(sorted(body.keys()))
        print(f"  body keys: {keys}")
        return

    if response.status_code < 400:
        raise ContractAuditError(f"expected HTTP >= 400, got {response.status_code}")
    envelope_id = require_error_envelope(body, response=response)
    print(f"✓ {label} {response.status_code} request_id={request_id}")
    print(f"  error.code={body['error']['code']}")
    if envelope_id != request_id:
        raise ContractAuditError("error.request_id must match header request id")


def main() -> int:
    print("API response contract audit")
    client, tmp_ctx = build_audit_client()
    try:
        for check in SUCCESS_CHECKS:
            audit_response(check.label, client.get(check.path), expect_success=check.expect_success)
        for check in ERROR_CHECKS:
            audit_response(check.label, client.get(check.path), expect_success=check.expect_success)
    finally:
        tmp_ctx.cleanup()

    print("ok: API response contract audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

