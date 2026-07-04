"""Read-only production smoke checks for OrigenLab dashboard API (Phase 9E)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from origenlab_email_pipeline.catalog.catalog_mirror_safety import (
    FORBIDDEN_JOINED_PROSE_ARTIFACTS,
)

MIN_CATALOG_PRODUCTS = 9
MIN_COMMERCIAL_DEALS = 1

BLUESLICK_PRODUCT_KEY = "serva-blueslick-250ml"
TEMED_PRODUCT_KEY = "serva-temed-25ml"

FORBIDDEN_JSON_KEYS: frozenset[str] = frozenset(
    {
        "gmail_url",
        "source_file",
        "source_path",
        "body",
        "email_body",
        "full_text",
        "transfer_id",
        "operation_id",
    }
)

FORBIDDEN_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bbank\b",
        r"\bbanco\b",
        r"\bswift\b",
        r"\biban\b",
        r"\bcuenta\b",
        r"\bbeneficiario\b",
        r"\brut\b",
        r"gmail\.com",
        r"mail\.google",
    )
)

PROSE_ARTIFACTS: tuple[str, ...] = FORBIDDEN_JOINED_PROSE_ARTIFACTS

FetchJsonFn = Callable[[str, dict[str, str | int]], tuple[int, dict[str, Any] | None, str]]

CF_ACCESS_CLIENT_ID_HEADER = "CF-Access-Client-Id"
CF_ACCESS_CLIENT_SECRET_HEADER = "CF-Access-Client-Secret"
API_AUTH_TOKEN_HEADER = "X-OriginLab-API-Key"

CF_ACCESS_403_HINT = (
    "HTTP 403: API is probably protected by Cloudflare Access. "
    "Provide CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET or run against local API."
)
API_AUTH_401_HINT = (
    "HTTP 401: API origin requires ORIGENLAB_API_AUTH_TOKEN. "
    "Export the token from your secret store — the smoke script sends "
    f"{API_AUTH_TOKEN_HEADER} automatically when the env var is set."
)
API_AUTH_401_INVALID_HINT = (
    "HTTP 401: unauthorized at API origin — ORIGENLAB_API_AUTH_TOKEN may be missing or invalid."
)


@dataclass(frozen=True)
class CloudflareAccessConfig:
    client_id: str
    client_secret: str

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip())

    def request_headers(self) -> dict[str, str]:
        return {
            CF_ACCESS_CLIENT_ID_HEADER: self.client_id,
            CF_ACCESS_CLIENT_SECRET_HEADER: self.client_secret,
        }


def resolve_cf_access_credentials(
    *,
    cli_client_id: str | None = None,
    cli_client_secret: str | None = None,
) -> CloudflareAccessConfig | None:
    """Load Cloudflare Access service token from env (preferred) or CLI overrides."""
    env_id = (
        os.environ.get("CF_ACCESS_CLIENT_ID")
        or os.environ.get("ORIGENLAB_CF_ACCESS_CLIENT_ID")
        or ""
    ).strip()
    env_secret = (
        os.environ.get("CF_ACCESS_CLIENT_SECRET")
        or os.environ.get("ORIGENLAB_CF_ACCESS_CLIENT_SECRET")
        or ""
    ).strip()
    client_id = (cli_client_id or env_id or "").strip()
    client_secret = (cli_client_secret or env_secret or "").strip()
    if not client_id and not client_secret:
        return None
    if not client_id or not client_secret:
        raise ValueError(
            "Cloudflare Access requires both client id and client secret "
            "(CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET)"
        )
    return CloudflareAccessConfig(client_id=client_id, client_secret=client_secret)


def resolve_api_auth_token() -> str | None:
    """Load API origin token from ORIGENLAB_API_AUTH_TOKEN (optional for local smoke)."""
    token = os.environ.get("ORIGENLAB_API_AUTH_TOKEN", "").strip()
    return token or None


def build_smoke_request_headers(
    *,
    cf_access: CloudflareAccessConfig | None = None,
    api_auth_token: str | None = None,
) -> dict[str, str]:
    """Merge optional Cloudflare Access and API origin auth headers for smoke GETs."""
    headers: dict[str, str] = {}
    if cf_access is not None and cf_access.is_configured:
        headers.update(cf_access.request_headers())
    token = (api_auth_token or "").strip()
    if token:
        headers[API_AUTH_TOKEN_HEADER] = token
    return headers


def format_http_status_detail(
    status: int,
    err: str,
    *,
    expected: int = 200,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> str:
    if status == 403 and not cf_access_configured:
        return CF_ACCESS_403_HINT
    if status == 401:
        if not api_auth_configured:
            return API_AUTH_401_HINT
        return API_AUTH_401_INVALID_HINT
    hint = err or "no body"
    return f"HTTP {status} (expected {expected}) — {hint[:120]}"


@dataclass
class EndpointCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class SmokeReport:
    api_base: str
    checks: list[EndpointCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    def summary_lines(self) -> list[str]:
        lines = [f"Dashboard API smoke — {'PASS' if self.passed else 'FAIL'}", f"  API base: {self.api_base}"]
        for check in self.checks:
            status = "OK" if check.ok else "FAIL"
            lines.append(f"  {check.name}: {status} — {check.detail}")
        lines.append(f"Overall: {'PASS' if self.passed else 'FAIL'}")
        return lines


def default_fetch_json(
    base_url: str,
    path: str,
    params: dict[str, str | int] | None = None,
    *,
    timeout: float = 30.0,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None, str]:
    """GET JSON from operator API. Returns (status, body, error_or_snippet)."""
    query = urlencode(params) if params else ""
    url = f"{base_url.rstrip('/')}{path}" + (f"?{query}" if query else "")
    headers: dict[str, str] = {"Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, method="GET", headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(resp.status)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    except URLError as exc:
        return -1, None, str(exc.reason)

    if not raw.strip():
        return status, None, "empty body"
    try:
        return status, json.loads(raw), ""
    except json.JSONDecodeError:
        return status, None, "invalid JSON"


def collect_json_keys(obj: object, keys: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(str(key))
            collect_json_keys(value, keys)
    elif isinstance(obj, list):
        for item in obj:
            collect_json_keys(item, keys)


def _forbidden_key_is_populated(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def find_populated_forbidden_keys(obj: object, *, path: str = "") -> list[str]:
    """Keys that must never carry data in operator JSON (null/empty allowed on legacy shapes)."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_s = str(key)
            subpath = f"{path}.{key_s}" if path else key_s
            if key_s in FORBIDDEN_JSON_KEYS and _forbidden_key_is_populated(value):
                found.append(subpath)
            found.extend(find_populated_forbidden_keys(value, path=subpath))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            found.extend(find_populated_forbidden_keys(item, path=f"{path}[{index}]"))
    return found


def scan_payload_safety(payload: object) -> list[str]:
    """Return human-readable safety violations (no secret values)."""
    errors: list[str] = []
    forbidden_keys = find_populated_forbidden_keys(payload)
    if forbidden_keys:
        errors.append(f"forbidden keys: {', '.join(sorted(set(forbidden_keys)))}")

    blob = json.dumps(payload, ensure_ascii=False).lower()
    for pattern in FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(blob):
            errors.append(f"forbidden text pattern: {pattern.pattern}")
    for artifact in PROSE_ARTIFACTS:
        if artifact.lower() in blob:
            errors.append(f"prose artifact: {artifact}")
    return errors


def _expect_status(
    name: str,
    status: int,
    body: dict[str, Any] | None,
    err: str,
    *,
    expected: int = 200,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    if status != expected:
        return EndpointCheck(
            name,
            False,
            format_http_status_detail(
                status,
                err,
                expected=expected,
                cf_access_configured=cf_access_configured,
                api_auth_configured=api_auth_configured,
            ),
        )
    if body is None:
        return EndpointCheck(name, False, err or "missing JSON body")
    safety = scan_payload_safety(body)
    if safety:
        return EndpointCheck(name, False, "; ".join(safety))
    return EndpointCheck(name, True, f"HTTP {status}")


def _check_health(
    fetch: FetchJsonFn,
    *,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    status, body, err = fetch("/health", {})
    check = _expect_status(
        "GET /health",
        status,
        body,
        err,
        cf_access_configured=cf_access_configured,
        api_auth_configured=api_auth_configured,
    )
    if not check.ok or body is None:
        return check
    if body.get("ok") is not True:
        return EndpointCheck("GET /health", False, "ok is not true")
    if not str(body.get("service") or "").strip():
        return EndpointCheck("GET /health", False, "missing service name")
    return EndpointCheck("GET /health", True, f"ok service={body.get('service')}")


def _check_operator_status(
    fetch: FetchJsonFn,
    *,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    status, body, err = fetch("/operator/status", {})
    check = _expect_status(
        "GET /operator/status",
        status,
        body,
        err,
        cf_access_configured=cf_access_configured,
        api_auth_configured=api_auth_configured,
    )
    if not check.ok or body is None:
        return check
    verdict = str(body.get("verdict") or "").strip()
    if not verdict:
        return EndpointCheck("GET /operator/status", False, "missing verdict")
    if "sqlite_path" in body:
        return EndpointCheck("GET /operator/status", False, "sqlite_path must not be exposed")
    return EndpointCheck("GET /operator/status", True, f"verdict={verdict}")


def _check_commercial_deals(
    fetch: FetchJsonFn,
    *,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    status, body, err = fetch("/mirror/commercial/deals", {"limit": 20})
    check = _expect_status(
        "GET /mirror/commercial/deals",
        status,
        body,
        err,
        cf_access_configured=cf_access_configured,
        api_auth_configured=api_auth_configured,
    )
    if not check.ok or body is None:
        return check
    if body.get("read_only") is not True:
        return EndpointCheck("GET /mirror/commercial/deals", False, "read_only is not true")
    if body.get("data_source") != "postgres_mirror":
        return EndpointCheck(
            "GET /mirror/commercial/deals",
            False,
            f"unexpected data_source={body.get('data_source')!r}",
        )
    total = int(body.get("total") or 0)
    if total < MIN_COMMERCIAL_DEALS:
        return EndpointCheck(
            "GET /mirror/commercial/deals",
            False,
            f"total={total} (expected >={MIN_COMMERCIAL_DEALS})",
        )
    if body.get("table_available") is not True:
        return EndpointCheck("GET /mirror/commercial/deals", False, "table_available is false")
    return EndpointCheck(
        "GET /mirror/commercial/deals",
        True,
        f"total={total} read_only postgres_mirror",
    )


def _check_catalog_products(
    fetch: FetchJsonFn,
    *,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    status, body, err = fetch("/mirror/catalog/products", {"limit": 100})
    check = _expect_status(
        "GET /mirror/catalog/products",
        status,
        body,
        err,
        cf_access_configured=cf_access_configured,
        api_auth_configured=api_auth_configured,
    )
    if not check.ok or body is None:
        return check
    if body.get("read_only") is not True:
        return EndpointCheck("GET /mirror/catalog/products", False, "read_only is not true")
    if body.get("data_source") != "postgres_mirror":
        return EndpointCheck(
            "GET /mirror/catalog/products",
            False,
            f"unexpected data_source={body.get('data_source')!r}",
        )
    total = int(body.get("total") or 0)
    if total < MIN_CATALOG_PRODUCTS:
        return EndpointCheck(
            "GET /mirror/catalog/products",
            False,
            f"total={total} (expected >={MIN_CATALOG_PRODUCTS})",
        )
    if body.get("table_available") is not True:
        return EndpointCheck("GET /mirror/catalog/products", False, "table_available is false")
    return EndpointCheck(
        "GET /mirror/catalog/products",
        True,
        f"total={total} read_only postgres_mirror",
    )


def _check_catalog_detail(
    fetch: FetchJsonFn,
    *,
    product_key: str,
    label: str,
    expect_eur: str,
    expect_clp: int,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    path = f"/mirror/catalog/products/{product_key}"
    status, body, err = fetch(path, {})
    name = f"GET {path}"
    check = _expect_status(
        name,
        status,
        body,
        err,
        cf_access_configured=cf_access_configured,
        api_auth_configured=api_auth_configured,
    )
    if not check.ok or body is None:
        return check
    product = body.get("product")
    if not isinstance(product, dict):
        return EndpointCheck(name, False, "missing product object")
    if body.get("read_only") is not True:
        return EndpointCheck(name, False, "read_only is not true")
    history = product.get("commercial_history") or []
    if not history:
        return EndpointCheck(name, False, "commercial_history is empty")
    blob = json.dumps(history, ensure_ascii=False)
    eur_ok = expect_eur in blob
    clp_ok = str(expect_clp) in blob
    if not eur_ok or not clp_ok:
        return EndpointCheck(
            name,
            False,
            f"{label}: commercial_history missing expected EUR/CLP reference amounts",
        )
    return EndpointCheck(name, True, f"{label} commercial_history reference amounts present")


def _check_warm_cases(
    fetch: FetchJsonFn,
    *,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    status, body, err = fetch("/cases/warm", {"limit": 20, "include_noise": "false"})
    check = _expect_status(
        "GET /cases/warm",
        status,
        body,
        err,
        cf_access_configured=cf_access_configured,
        api_auth_configured=api_auth_configured,
    )
    if not check.ok or body is None:
        return check
    meta = body.get("meta")
    if not isinstance(meta, dict):
        return EndpointCheck("GET /cases/warm", False, "missing meta")
    if meta.get("read_only") is not True:
        return EndpointCheck("GET /cases/warm", False, "meta.read_only is not true")
    data_source = meta.get("data_source")
    if data_source not in ("sqlite", "postgres_mirror"):
        return EndpointCheck("GET /cases/warm", False, f"unexpected data_source={data_source!r}")
    count = len(body.get("items") or [])
    return EndpointCheck(
        "GET /cases/warm",
        True,
        f"data_source={data_source} items={count}",
    )


def _check_equipment(
    fetch: FetchJsonFn,
    *,
    cf_access_configured: bool = False,
    api_auth_configured: bool = False,
) -> EndpointCheck:
    status, body, err = fetch(
        "/opportunities/equipment",
        {"limit": 20, "include_account_intelligence": "false"},
    )
    check = _expect_status(
        "GET /opportunities/equipment",
        status,
        body,
        err,
        cf_access_configured=cf_access_configured,
        api_auth_configured=api_auth_configured,
    )
    if not check.ok or body is None:
        return check
    meta = body.get("meta")
    if not isinstance(meta, dict):
        return EndpointCheck("GET /opportunities/equipment", False, "missing meta")
    if meta.get("read_only") is not True:
        return EndpointCheck("GET /opportunities/equipment", False, "meta.read_only is not true")
    if meta.get("source_path"):
        return EndpointCheck("GET /opportunities/equipment", False, "meta.source_path must be empty")
    data_source = meta.get("data_source")
    if data_source not in ("active_current_csv", "postgres_mirror"):
        return EndpointCheck(
            "GET /opportunities/equipment",
            False,
            f"unexpected data_source={data_source!r}",
        )
    count = len(body.get("items") or [])
    reduced = bool(meta.get("reduced_mode"))
    note = "reduced_mode" if reduced else "ok"
    return EndpointCheck(
        "GET /opportunities/equipment",
        True,
        f"data_source={data_source} items={count} ({note})",
    )


def run_dashboard_api_smoke(
    api_base: str,
    *,
    timeout: float = 30.0,
    fetch: FetchJsonFn | None = None,
    cf_access: CloudflareAccessConfig | None = None,
    api_auth_token: str | None = None,
) -> SmokeReport:
    """Run all read-only smoke checks against the operator API base URL."""
    base = api_base.strip().rstrip("/")
    if not base.startswith("http"):
        raise ValueError("api_base must start with http:// or https://")

    cf_configured = cf_access is not None and cf_access.is_configured
    api_auth_configured = bool((api_auth_token or "").strip())
    extra_headers = build_smoke_request_headers(
        cf_access=cf_access,
        api_auth_token=api_auth_token,
    ) or None

    def bound_fetch(path: str, params: dict[str, str | int]) -> tuple[int, dict[str, Any] | None, str]:
        if fetch is not None:
            return fetch(path, params)
        return default_fetch_json(
            base, path, params, timeout=timeout, extra_headers=extra_headers
        )

    cf_kw = {
        "cf_access_configured": cf_configured,
        "api_auth_configured": api_auth_configured,
    }
    report = SmokeReport(api_base=base)
    report.checks = [
        _check_health(bound_fetch, **cf_kw),
        _check_operator_status(bound_fetch, **cf_kw),
        _check_commercial_deals(bound_fetch, **cf_kw),
        _check_catalog_products(bound_fetch, **cf_kw),
        _check_catalog_detail(
            bound_fetch,
            product_key=BLUESLICK_PRODUCT_KEY,
            label="BlueSlick",
            expect_eur="117.00",
            expect_clp=695000,
            **cf_kw,
        ),
        _check_catalog_detail(
            bound_fetch,
            product_key=TEMED_PRODUCT_KEY,
            label="TEMED",
            expect_eur="31.00",
            expect_clp=545000,
            **cf_kw,
        ),
        _check_warm_cases(bound_fetch, **cf_kw),
        _check_equipment(bound_fetch, **cf_kw),
    ]
    return report
