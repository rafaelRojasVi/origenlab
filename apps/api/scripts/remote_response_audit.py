#!/usr/bin/env python3
"""Authenticated remote production response audit (live API, Cloudflare Access).

Usage:
  cd apps/api
  CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \\
    uv run python scripts/remote_response_audit.py

Optional env (cold Render / Cloudflare starts):
  ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS — per-request timeout (default 30; use 90 if needed)
  ORIGENLAB_REMOTE_AUDIT_RETRIES — network-level retries after timeout/connection errors (default 2)
  ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS — sleep between retries (default 2.0)

Requires Cloudflare Access service token headers. Exits 0 with a skip message when
``CF_ACCESS_CLIENT_ID`` / ``CF_ACCESS_CLIENT_SECRET`` are unset (CI without secrets).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://api.origenlab.cl"
USER_AGENT = "OrigenLab-API-Response-Audit/1.0"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_RETRIES = 2
REQUEST_RETRY_BACKOFF_SECONDS = 2.0

FORBIDDEN_LEAK_SUBSTRINGS: tuple[str, ...] = (
    "/home/",
    "/mnt/",
    "Traceback",
    "postgres://",
    "postgresql://",
    "ORIGENLAB_POSTGRES_URL=",
    "GMAIL_REFRESH_TOKEN",
    "password=",
    "api_key=",
)

LIST_ENDPOINT_PATHS: frozenset[str] = frozenset(
    {
        "/cases/warm",
        "/emails/recent",
    }
)

SKIP_MESSAGE = (
    "skip: authenticated remote response audit requires "
    "CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET"
)


class RemoteAuditError(AssertionError):
    pass


@dataclass(frozen=True)
class AuditCheck:
    label: str
    path: str
    expect_success: bool


@dataclass(frozen=True)
class RemoteResponse:
    status: int
    headers: dict[str, str]
    body_text: str


SUCCESS_CHECKS: tuple[AuditCheck, ...] = (
    AuditCheck("GET /health", "/health", True),
    AuditCheck("GET /operator/status", "/operator/status", True),
    AuditCheck("GET /operator/automation-status", "/operator/automation-status", True),
    AuditCheck("GET /cases/warm?limit=3", "/cases/warm?limit=3", True),
    AuditCheck("GET /operator/procurement/status", "/operator/procurement/status", True),
    AuditCheck("GET /emails/recent?limit=3", "/emails/recent?limit=3", True),
)

ERROR_CHECKS: tuple[AuditCheck, ...] = (
    AuditCheck("GET /cases/warm?limit=0", "/cases/warm?limit=0", False),
    AuditCheck("GET /definitely-not-a-route", "/definitely-not-a-route", False),
)


def base_url_from_env() -> str:
    raw = os.environ.get("ORIGENLAB_API_BASE_URL", DEFAULT_BASE_URL).strip()
    return (raw or DEFAULT_BASE_URL).rstrip("/")


def cf_credentials_from_env() -> tuple[str, str] | None:
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def normalize_headers(raw_headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw_headers.items():
        out[str(key).lower()] = str(value).strip()
    return out


def path_only(request_path: str) -> str:
    return request_path.split("?", 1)[0]


def is_list_endpoint(request_path: str) -> bool:
    return path_only(request_path) in LIST_ENDPOINT_PATHS


def require_request_id_header(headers: dict[str, str]) -> str:
    request_id = headers.get("x-request-id", "").strip()
    if not request_id:
        raise RemoteAuditError("missing x-request-id response header")
    return request_id


def require_json_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or isinstance(body, list):
        raise RemoteAuditError(f"expected JSON object, got {type(body).__name__}")
    return body


def scan_forbidden_leaks_in_text(text: str) -> list[str]:
    hits: list[str] = []
    for needle in FORBIDDEN_LEAK_SUBSTRINGS:
        if needle in text:
            hits.append(needle)
    return hits


def require_meta_items(body: dict[str, Any]) -> None:
    if "meta" not in body or "items" not in body:
        raise RemoteAuditError("list endpoint must include top-level meta and items")
    if not isinstance(body.get("meta"), dict):
        raise RemoteAuditError("meta must be an object")
    if not isinstance(body.get("items"), list):
        raise RemoteAuditError("items must be an array")


def require_error_envelope(body: dict[str, Any], *, request_id_header: str) -> str:
    if "error" not in body or not isinstance(body.get("error"), dict):
        raise RemoteAuditError("error response must include top-level error object")
    err = body["error"]
    for key in ("code", "message", "details", "request_id"):
        if key not in err:
            raise RemoteAuditError(f"error envelope missing error.{key}")
    if not isinstance(err["code"], str) or not err["code"].strip():
        raise RemoteAuditError("error.code must be a non-empty string")
    if not isinstance(err["message"], str) or not err["message"].strip():
        raise RemoteAuditError("error.message must be a non-empty string")
    if not isinstance(err["details"], dict):
        raise RemoteAuditError("error.details must be an object")
    request_id = err["request_id"]
    if not isinstance(request_id, str) or not request_id.strip():
        raise RemoteAuditError("error.request_id must be a non-empty string")
    if request_id_header != request_id:
        raise RemoteAuditError("error.request_id must match x-request-id header")
    return request_id


WARM_CASE_INTERNAL_FIELDS: frozenset[str] = frozenset(
    {
        "body_snippet",
        "source_file",
        "recipients_preview",
        "sender_preview",
    }
)


def require_warm_cases_contract(body: dict[str, Any]) -> None:
    """Assert production read-model shape for GET /cases/warm."""
    require_meta_items(body)
    meta = body["meta"]
    items = body["items"]

    if meta.get("data_source") != "postgres_mirror":
        raise RemoteAuditError(
            f"meta.data_source must be postgres_mirror, got {meta.get('data_source')!r}"
        )
    if meta.get("read_only") is not True:
        raise RemoteAuditError("meta.read_only must be true")
    if meta.get("enrichment_available") is not True:
        raise RemoteAuditError("meta.enrichment_available must be true")

    count = meta.get("count")
    if not isinstance(count, int):
        raise RemoteAuditError("meta.count must be an int")
    if count != len(items):
        raise RemoteAuditError(
            f"meta.count ({count}) must equal len(items) ({len(items)})"
        )

    for index, item in enumerate(items):
        if not isinstance(item, dict) or isinstance(item, list):
            raise RemoteAuditError(f"items[{index}] must be an object")
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise RemoteAuditError(f"items[{index}].case_id must be a non-empty string")
        last_email_id = item.get("last_email_id")
        if not isinstance(last_email_id, int):
            raise RemoteAuditError(f"items[{index}].last_email_id must be an int")
        category = item.get("category")
        if not isinstance(category, str) or not category.strip():
            raise RemoteAuditError(f"items[{index}].category must be a non-empty string")
        status = item.get("status")
        if not isinstance(status, str) or not status.strip():
            raise RemoteAuditError(f"items[{index}].status must be a non-empty string")
        for field in WARM_CASE_INTERNAL_FIELDS:
            if field in item:
                raise RemoteAuditError(f"items[{index}] must not include top-level {field}")

    leak_hits = scan_forbidden_leaks_in_text(json.dumps(body, ensure_ascii=False))
    path_leaks = [hit for hit in leak_hits if hit in ("/home/", "/mnt/")]
    if path_leaks:
        raise RemoteAuditError(f"forbidden path leak substrings in warm cases body: {sorted(path_leaks)}")


RECENT_EMAIL_INTERNAL_ITEM_FIELDS: frozenset[str] = frozenset(
    {
        "body",
        "raw_body",
        "headers",
        "recipients_raw",
    }
)


def _require_safe_recent_email_source_file(value: Any, *, index: int) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise RemoteAuditError(f"items[{index}].source_file must be a string or null")
    text = value.strip()
    if not text:
        return
    if text.startswith("/"):
        raise RemoteAuditError(
            f"items[{index}].source_file must not be an absolute filesystem path"
        )
    for needle in ("/home/", "/mnt/"):
        if needle in text:
            raise RemoteAuditError(
                f"items[{index}].source_file must not contain raw filesystem path {needle!r}"
            )


def _is_json_safe_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    return False


def require_recent_emails_contract(body: dict[str, Any]) -> None:
    """Assert production read-model shape for GET /emails/recent."""
    require_meta_items(body)
    items = body["items"]

    total_returned = body.get("total_returned")
    if not isinstance(total_returned, int):
        raise RemoteAuditError("total_returned must be an int")
    if total_returned != len(items):
        raise RemoteAuditError(
            f"total_returned ({total_returned}) must equal len(items) ({len(items)})"
        )

    enrichment_available = body.get("enrichment_available")
    if not isinstance(enrichment_available, bool):
        raise RemoteAuditError("enrichment_available must be a bool")

    reduced_mode = body.get("reduced_mode")
    if not isinstance(reduced_mode, bool):
        raise RemoteAuditError("reduced_mode must be a bool")

    days_window = body.get("days_window")
    if not isinstance(days_window, int):
        raise RemoteAuditError("days_window must be an int")

    meta = body.get("meta")
    if meta is not None:
        if not isinstance(meta, dict):
            raise RemoteAuditError("meta must be an object")
        if "data_source" in meta and meta.get("data_source") != "postgres_mirror":
            raise RemoteAuditError(
                f"meta.data_source must be postgres_mirror, got {meta.get('data_source')!r}"
            )

    for index, item in enumerate(items):
        if not isinstance(item, dict) or isinstance(item, list):
            raise RemoteAuditError(f"items[{index}] must be an object")
        email_id = item.get("email_id")
        if not isinstance(email_id, int):
            raise RemoteAuditError(f"items[{index}].email_id must be an int")
        for field in RECENT_EMAIL_INTERNAL_ITEM_FIELDS:
            if field in item:
                raise RemoteAuditError(f"items[{index}] must not include top-level {field}")
        if "source_file" in item:
            _require_safe_recent_email_source_file(item.get("source_file"), index=index)
        for key, value in item.items():
            if not _is_json_safe_value(value):
                raise RemoteAuditError(
                    f"items[{index}].{key} must be a JSON-safe scalar"
                )

    leak_hits = scan_forbidden_leaks_in_text(json.dumps(body, ensure_ascii=False))
    path_leaks = [hit for hit in leak_hits if hit in ("/home/", "/mnt/")]
    if path_leaks:
        raise RemoteAuditError(
            f"forbidden path leak substrings in recent emails body: {sorted(path_leaks)}"
        )


def request_timeout_seconds() -> int:
    raw = os.environ.get("ORIGENLAB_REMOTE_AUDIT_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return REQUEST_TIMEOUT_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return REQUEST_TIMEOUT_SECONDS


def request_retries() -> int:
    raw = os.environ.get("ORIGENLAB_REMOTE_AUDIT_RETRIES", "").strip()
    if not raw:
        return REQUEST_RETRIES
    try:
        return max(0, int(raw))
    except ValueError:
        return REQUEST_RETRIES


def request_retry_backoff_seconds() -> float:
    raw = os.environ.get("ORIGENLAB_REMOTE_AUDIT_RETRY_BACKOFF_SECONDS", "").strip()
    if not raw:
        return REQUEST_RETRY_BACKOFF_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return REQUEST_RETRY_BACKOFF_SECONDS


def _network_error_detail(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timed out"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason if exc.reason is not None else exc
        return str(reason)
    return str(exc)


def build_request_headers(client_id: str, client_secret: str) -> dict[str, str]:
    return {
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def _fetch_get_once(url: str, headers: dict[str, str], *, timeout: int) -> RemoteResponse:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body_text = response.read().decode("utf-8", errors="replace")
            return RemoteResponse(
                status=int(response.status),
                headers=normalize_headers(response.headers),
                body_text=body_text,
            )
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        return RemoteResponse(
            status=int(exc.code),
            headers=normalize_headers(exc.headers),
            body_text=body_text,
        )


def fetch_get(url: str, headers: dict[str, str], *, label: str = "") -> RemoteResponse:
    timeout = request_timeout_seconds()
    max_retries = request_retries()
    backoff = request_retry_backoff_seconds()
    max_attempts = max_retries + 1
    display_label = label or url

    for attempt in range(max_attempts):
        try:
            return _fetch_get_once(url, headers, timeout=timeout)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            if attempt < max_retries:
                retry_num = attempt + 1
                if isinstance(exc, TimeoutError):
                    print(
                        f"warning: {display_label} timed out after {timeout}s; "
                        f"retrying {retry_num}/{max_retries}",
                        file=sys.stderr,
                    )
                else:
                    detail = _network_error_detail(exc)
                    print(
                        f"warning: {display_label} failed ({detail}); "
                        f"retrying {retry_num}/{max_retries}",
                        file=sys.stderr,
                    )
                if backoff > 0:
                    time.sleep(backoff)
                continue

            if isinstance(exc, TimeoutError):
                raise RemoteAuditError(
                    f"request timed out after {timeout}s after {max_attempts} attempt(s): {url}"
                ) from exc
            detail = _network_error_detail(exc)
            raise RemoteAuditError(
                f"request failed after {max_attempts} attempt(s) for {url}: {detail}"
            ) from exc

    raise RemoteAuditError(f"request failed after {max_attempts} attempt(s) for {url}")


def audit_response(
    label: str,
    response: RemoteResponse,
    request_path: str,
    *,
    expect_success: bool,
) -> None:
    request_id = require_request_id_header(response.headers)

    leak_hits = scan_forbidden_leaks_in_text(response.body_text)
    if leak_hits:
        raise RemoteAuditError(f"forbidden leak substrings in response: {sorted(leak_hits)}")

    try:
        parsed = json.loads(response.body_text)
    except json.JSONDecodeError as exc:
        raise RemoteAuditError(f"response is not JSON: {exc}") from exc
    body = require_json_object(parsed)

    if expect_success:
        if response.status != 200:
            raise RemoteAuditError(f"expected HTTP 200, got {response.status}")
        if is_list_endpoint(request_path):
            endpoint = path_only(request_path)
            if endpoint == "/cases/warm":
                require_warm_cases_contract(body)
            elif endpoint == "/emails/recent":
                require_recent_emails_contract(body)
            else:
                require_meta_items(body)
        print(f"✓ {label} {response.status} request_id={request_id}")
        keys = ", ".join(sorted(body.keys()))
        print(f"  body keys: {keys}")
        return

    if response.status < 400:
        raise RemoteAuditError(f"expected HTTP >= 400, got {response.status}")
    require_error_envelope(body, request_id_header=request_id)
    print(f"✓ {label} {response.status} request_id={request_id}")
    print(f"  error.code={body['error']['code']}")


def main() -> int:
    credentials = cf_credentials_from_env()
    if credentials is None:
        print(SKIP_MESSAGE)
        return 0

    base_url = base_url_from_env()
    client_id, client_secret = credentials
    headers = build_request_headers(client_id, client_secret)

    print(f"remote response audit: {base_url}")
    for check in SUCCESS_CHECKS:
        url = f"{base_url}{check.path}"
        response = fetch_get(url, headers, label=check.label)
        audit_response(check.label, response, check.path, expect_success=check.expect_success)
    for check in ERROR_CHECKS:
        url = f"{base_url}{check.path}"
        response = fetch_get(url, headers, label=check.label)
        audit_response(check.label, response, check.path, expect_success=check.expect_success)

    print("ok: remote production responses passed response audit")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteAuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
