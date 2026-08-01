"""Budgeted read-only HTTP transport for Ticket + public OCDS probes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from origenlab_email_pipeline.chilecompra_api import (
    DEFAULT_TIMEOUT_SECONDS,
    build_licitaciones_url,
    redact_ticket,
    redact_ticket_in_url,
    ticket_from_env,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
    sha256_bytes,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ENDPOINT_OCDS_MONTHLY_RANGE,
    ENDPOINT_PATH_OCDS_TEMPLATE,
    ENDPOINT_TICKET_LICITACION_DETAIL,
    ENDPOINT_TICKET_LICITACIONES_SUMMARY,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.budget import (
    RequestBudget,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    endpoint_path_only,
)

UrlopenFn = Callable[..., Any]

OCDS_PUBLIC_HOST = "https://api.mercadopublico.cl"


@dataclass(frozen=True)
class TransportResult:
    http_status: int | None
    raw_bytes: bytes
    payload: dict[str, Any] | None
    error_classification: str | None
    ledger_ordinal: int
    raw_byte_sha256: str
    canonical_json_digest: str | None


def build_ocds_probe_path(*, year: int, month: int, start: int, end: int) -> str:
    """Build raw OCDS probe path ``/{offset}/{limit}`` for index-semantics probes.

    Keeps probe transport isolated from ``build_ocds_query`` so validation can
    emit literal path pairs (including ``0/0``) without going through the
    inclusive-span → offset/limit conversion used by acquisition planners.
    """
    return ENDPOINT_PATH_OCDS_TEMPLATE.format(
        year=year, month=month, start=start, end=end
    )


def build_ocds_probe_url(*, year: int, month: int, start: int, end: int) -> str:
    path = build_ocds_probe_path(year=year, month=month, start=start, end=end)
    return f"{OCDS_PUBLIC_HOST}{path}"


def _path_only_from_url(url: str) -> str:
    return endpoint_path_only(url) or urlparse(url).path


def _fetch_raw(
    url: str,
    *,
    timeout: float,
    urlopen_fn: UrlopenFn | None,
    ticket_for_redaction: str,
) -> tuple[int | None, bytes, str | None]:
    opener = urlopen_fn or urlopen
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "origenlab-pr5b1-live-contract/1.0",
        },
        method="GET",
    )
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read()
            return int(status) if status is not None else None, body, None
    except HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        try:
            raw = body if isinstance(body, (bytes, bytearray)) else b""
        except Exception:  # noqa: BLE001
            raw = b""
        message = redact_ticket(
            f"HTTP {exc.code} while fetching {_path_only_from_url(url)}",
            ticket_for_redaction,
        )
        return int(exc.code), bytes(raw), message
    except URLError as exc:
        message = redact_ticket(
            f"Network error while fetching {_path_only_from_url(url)}: {exc.reason}",
            ticket_for_redaction,
        )
        return None, b"", message


def _decode_payload(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    if not raw:
        return None, "empty_body"
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "non_object_json"
    return payload, None


class BudgetedLiveTransport:
    """Single-shot budgeted transport. No automatic retries."""

    def __init__(
        self,
        budget: RequestBudget,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        urlopen_fn: UrlopenFn | None = None,
        ticket: str | None = None,
    ) -> None:
        self.budget = budget
        self.timeout_seconds = timeout_seconds
        self.urlopen_fn = urlopen_fn
        self._ticket = ticket

    def _resolve_ticket(self) -> str:
        if self._ticket is not None:
            return self._ticket
        return ticket_from_env()

    def fetch_ticket_summary(self, *, estado: str = "activas") -> TransportResult:
        ticket = self._resolve_ticket()
        ordinal = self.budget.reserve("ticket")
        started = RequestBudget.now()
        url = build_licitaciones_url(ticket=ticket, estado=estado)
        status, raw, err = _fetch_raw(
            url,
            timeout=self.timeout_seconds,
            urlopen_fn=self.urlopen_fn,
            ticket_for_redaction=ticket,
        )
        payload, decode_err = _decode_payload(raw)
        error = err or decode_err
        if status is not None and status >= 400 and error is None:
            error = f"http_{status}"
        completed = status is not None and status < 400 and payload is not None
        digest = canonical_json_digest(payload) if payload is not None else None
        raw_sha = sha256_bytes(raw)
        self.budget.record(
            ordinal=ordinal,
            authentication_kind="ticket",
            endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
            sanitized_endpoint_path="/servicios/v1/publico/licitaciones.json",
            sanitized_query_fields={"estado": estado},
            started_at_utc=started,
            completed_at_utc=RequestBudget.now(),
            http_status=status,
            response_byte_count=len(raw),
            raw_byte_sha256=raw_sha,
            canonical_json_digest=digest,
            parser_outcome="ok" if completed else "error",
            error_classification=error,
            completed=completed,
        )
        return TransportResult(
            http_status=status,
            raw_bytes=raw,
            payload=payload,
            error_classification=error,
            ledger_ordinal=ordinal,
            raw_byte_sha256=raw_sha,
            canonical_json_digest=digest,
        )

    def fetch_ticket_detail(self, *, codigo: str) -> TransportResult:
        ticket = self._resolve_ticket()
        ordinal = self.budget.reserve("ticket")
        started = RequestBudget.now()
        url = build_licitaciones_url(ticket=ticket, codigo=codigo)
        # Never log/redact path with ticket; assert redaction works on accidental URL use.
        _ = redact_ticket_in_url(url)
        status, raw, err = _fetch_raw(
            url,
            timeout=self.timeout_seconds,
            urlopen_fn=self.urlopen_fn,
            ticket_for_redaction=ticket,
        )
        payload, decode_err = _decode_payload(raw)
        error = err or decode_err
        if status is not None and status >= 400 and error is None:
            error = f"http_{status}"
        completed = status is not None and status < 400 and payload is not None
        digest = canonical_json_digest(payload) if payload is not None else None
        raw_sha = sha256_bytes(raw)
        self.budget.record(
            ordinal=ordinal,
            authentication_kind="ticket",
            endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
            sanitized_endpoint_path="/servicios/v1/publico/licitaciones.json",
            sanitized_query_fields={"codigo": "<in_memory_only>"},
            started_at_utc=started,
            completed_at_utc=RequestBudget.now(),
            http_status=status,
            response_byte_count=len(raw),
            raw_byte_sha256=raw_sha,
            canonical_json_digest=digest,
            parser_outcome="ok" if completed else "error",
            error_classification=error,
            completed=completed,
        )
        return TransportResult(
            http_status=status,
            raw_bytes=raw,
            payload=payload,
            error_classification=error,
            ledger_ordinal=ordinal,
            raw_byte_sha256=raw_sha,
            canonical_json_digest=digest,
        )

    def fetch_ocds_probe(
        self, *, year: int, month: int, start: int, end: int
    ) -> TransportResult:
        ordinal = self.budget.reserve("public")
        started = RequestBudget.now()
        path = build_ocds_probe_path(year=year, month=month, start=start, end=end)
        url = f"{OCDS_PUBLIC_HOST}{path}"
        status, raw, err = _fetch_raw(
            url,
            timeout=self.timeout_seconds,
            urlopen_fn=self.urlopen_fn,
            ticket_for_redaction="",
        )
        payload, decode_err = _decode_payload(raw)
        error = err or decode_err
        if status is not None and status >= 400 and error is None:
            error = f"http_{status}"
        completed = status is not None and status < 400 and payload is not None
        digest = canonical_json_digest(payload) if payload is not None else None
        raw_sha = sha256_bytes(raw)
        self.budget.record(
            ordinal=ordinal,
            authentication_kind="public",
            endpoint_kind=ENDPOINT_OCDS_MONTHLY_RANGE,
            sanitized_endpoint_path=path,
            sanitized_query_fields={
                "year": year,
                "month": month,
                "start": start,
                "end": end,
            },
            started_at_utc=started,
            completed_at_utc=RequestBudget.now(),
            http_status=status,
            response_byte_count=len(raw),
            raw_byte_sha256=raw_sha,
            canonical_json_digest=digest,
            parser_outcome="ok" if completed else "error",
            error_classification=error,
            completed=completed,
        )
        return TransportResult(
            http_status=status,
            raw_bytes=raw,
            payload=payload,
            error_classification=error,
            ledger_ordinal=ordinal,
            raw_byte_sha256=raw_sha,
            canonical_json_digest=digest,
        )


def plan_requests(
    *,
    ticket_summary_limit_details: int,
    authenticated_request_budget: int,
    public_request_budget: int,
) -> dict[str, Any]:
    """No-network plan of sanitized requests that would be made."""
    detail_slots = min(3, max(0, ticket_summary_limit_details), authenticated_request_budget - 1)
    if authenticated_request_budget < 1:
        detail_slots = 0
    probes = [
        {"year": 2026, "month": 7, "start": 0, "end": 0},
        {"year": 2026, "month": 7, "start": 1, "end": 1},
        {"year": 2026, "month": 7, "start": 0, "end": 9},
        {"year": 2026, "month": 7, "start": 1, "end": 10},
    ][: max(0, min(4, public_request_budget))]
    return {
        "mode": "plan_only_no_network",
        "authenticated_request_budget": authenticated_request_budget,
        "public_request_budget": public_request_budget,
        "planned_authenticated": [
            {
                "endpoint_kind": ENDPOINT_TICKET_LICITACIONES_SUMMARY,
                "sanitized_endpoint_path": "/servicios/v1/publico/licitaciones.json",
                "sanitized_query_fields": {"estado": "activas"},
            },
            *[
                {
                    "endpoint_kind": ENDPOINT_TICKET_LICITACION_DETAIL,
                    "sanitized_endpoint_path": "/servicios/v1/publico/licitaciones.json",
                    "sanitized_query_fields": {
                        "codigo": f"live_detail_selection_{i:03d}",
                    },
                }
                for i in range(1, detail_slots + 1)
            ],
        ],
        "planned_public": [
            {
                "endpoint_kind": ENDPOINT_OCDS_MONTHLY_RANGE,
                "sanitized_endpoint_path": build_ocds_probe_path(**p),
                "sanitized_query_fields": p,
            }
            for p in probes
        ],
        "notes": [
            "Detail codes selected only after summary capture; tokens are placeholders in plan.",
            "OCDS probes use isolated path builder (may include start=0).",
            "No automatic retries. Failed attempts consume budget.",
        ],
    }


# Silence unused import if urlunparse reserved for future scrubbing.
_ = urlencode, urlunparse
