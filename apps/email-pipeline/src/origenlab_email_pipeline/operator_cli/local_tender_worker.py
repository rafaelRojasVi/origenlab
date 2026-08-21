"""OriginLab workstation worker for Mercado Público tender dossiers.

A dashboard-created ticket and a subsequently downloaded ``Licitaciones_*.zip``
are paired locally. ZIP validation, extraction, OCR, and T1 all run on the
workstation. Only the validated structured result is sent to the OriginLab API.

The worker never automates Mercado Público, never handles CAPTCHA, never sends
the raw ZIP to Render, and never places API credentials in tickets or outbox
payloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (
    OperatorZipAttachmentSource,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    assert_no_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.operator_annex_bundle_preview import (
    build_operator_annex_bundle_preview,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.semantic_fallback import (
    OllamaSemanticFallbackClient,
)

TICKET_CONTRACT_VERSION = "local_tender_processing_ticket_v1"
LOCAL_IMPORT_CONTRACT_VERSION = "local_tender_annex_import_v1"

TICKET_GLOB = "origenlab-tender-*.json"
ZIP_GLOB = "Licitaciones_*.zip"

DEFAULT_API_BASE_URL = "https://api.origenlab.cl"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "origenlab" / "tender-worker"

API_URL_ENV = "ORIGENLAB_API_BASE_URL"
API_TOKEN_ENV = "ORIGENLAB_API_AUTH_TOKEN"
CF_ACCESS_CLIENT_ID_ENV = "CF_ACCESS_CLIENT_ID"
CF_ACCESS_CLIENT_SECRET_ENV = "CF_ACCESS_CLIENT_SECRET"

# A dashboard ticket is intentionally short-lived. This prevents an abandoned
# ticket from claiming an unrelated Licitaciones ZIP downloaded much later.
MAX_TICKET_AGE_SECONDS = 60 * 60

_TENDER_CODE_RE = re.compile(r"^[A-Za-z0-9-]+$")
_TICKET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class LocalTenderWorkerError(RuntimeError):
    """A local-worker job could not safely complete."""


class LocalTenderWorkerHttpError(LocalTenderWorkerError):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status >= 500


@dataclass(frozen=True)
class LocalTenderProcessingTicket:
    path: Path
    ticket_id: str
    tender_code: str
    operator_declared_complete: bool
    created_at_utc: datetime


@dataclass(frozen=True)
class LocalTenderJob:
    ticket: LocalTenderProcessingTicket
    zip_path: Path


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LocalTenderWorkerError("ticket created_at_utc is missing")

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LocalTenderWorkerError("ticket created_at_utc is invalid") from exc

    if parsed.tzinfo is None:
        raise LocalTenderWorkerError("ticket created_at_utc must include timezone")

    return parsed.astimezone(timezone.utc)


def load_processing_ticket(path: Path) -> LocalTenderProcessingTicket:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalTenderWorkerError(
            f"unable to read processing ticket: {path.name}"
        ) from exc

    if not isinstance(decoded, dict):
        raise LocalTenderWorkerError("processing ticket must be a JSON object")

    expected_keys = {
        "contract_version",
        "ticket_id",
        "tender_code",
        "operator_declared_complete",
        "created_at_utc",
    }
    if set(decoded) != expected_keys:
        raise LocalTenderWorkerError(
            "processing ticket fields do not match v1 contract"
        )

    if decoded.get("contract_version") != TICKET_CONTRACT_VERSION:
        raise LocalTenderWorkerError("unsupported processing ticket contract")

    ticket_id = decoded.get("ticket_id")
    if not isinstance(ticket_id, str) or not _TICKET_ID_RE.fullmatch(ticket_id):
        raise LocalTenderWorkerError("invalid ticket_id")

    tender_code = decoded.get("tender_code")
    if not isinstance(tender_code, str) or not _TENDER_CODE_RE.fullmatch(tender_code):
        raise LocalTenderWorkerError("invalid tender_code")

    # ChileCompra tender identifiers are case-insensitive throughout the
    # persisted API boundary. Canonicalize here before attachment/chunk IDs
    # and semantic digests are derived so equivalent ticket casing cannot
    # produce different evidence identities.
    tender_code = tender_code.casefold()

    declared = decoded.get("operator_declared_complete")
    if not isinstance(declared, bool):
        raise LocalTenderWorkerError("operator_declared_complete must be boolean")

    return LocalTenderProcessingTicket(
        path=path,
        ticket_id=ticket_id,
        tender_code=tender_code,
        operator_declared_complete=declared,
        created_at_utc=_parse_utc(decoded.get("created_at_utc")),
    )


def _done_path(state_dir: Path, ticket_id: str) -> Path:
    return state_dir / "done" / f"{ticket_id}.json"


def _outbox_path(state_dir: Path, ticket_id: str) -> Path:
    return state_dir / "outbox" / f"{ticket_id}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    assert_no_portal_tokens(serialized, where="local_tender_worker_state")

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temp = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _consumed_zip_paths(state_dir: Path) -> set[str]:
    done_dir = state_dir / "done"
    if not done_dir.is_dir():
        return set()

    consumed: set[str] = set()
    for path in done_dir.glob("*.json"):
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        zip_path = decoded.get("zip_path") if isinstance(decoded, dict) else None
        if isinstance(zip_path, str) and zip_path:
            consumed.add(str(Path(zip_path).resolve()))
    return consumed


def find_pending_job(
    downloads_dir: Path,
    state_dir: Path,
    *,
    max_ticket_age_seconds: float = MAX_TICKET_AGE_SECONDS,
) -> LocalTenderJob | None:
    """Return one unambiguous ticket -> ZIP pairing, or fail closed.

    Pairing is deliberately conservative:
      * completed tickets are ignored;
      * tickets expire after a bounded interval;
      * more than one active ticket is ambiguous;
      * only ZIPs downloaded after the ticket are eligible;
      * more than one eligible ZIP is ambiguous.

    The worker never guesses which tender a Mercado Público ZIP belongs to.
    """
    if not downloads_dir.is_dir():
        raise LocalTenderWorkerError(
            f"downloads directory does not exist: {downloads_dir}"
        )
    if max_ticket_age_seconds <= 0:
        raise LocalTenderWorkerError("max_ticket_age_seconds must be > 0")

    consumed = _consumed_zip_paths(state_dir)
    now_ns = time.time_ns()
    max_age_ns = int(max_ticket_age_seconds * 1_000_000_000)

    active: list[LocalTenderProcessingTicket] = []

    for ticket_path in downloads_dir.glob(TICKET_GLOB):
        try:
            ticket_mtime = ticket_path.stat().st_mtime_ns
        except OSError as exc:
            raise LocalTenderWorkerError(
                f"unable to stat processing ticket: {ticket_path.name}"
            ) from exc

        age_ns = now_ns - ticket_mtime

        if age_ns < 0:
            raise LocalTenderWorkerError(
                f"processing ticket has a future filesystem timestamp: {ticket_path.name}"
            )

        # Skip stale files before parsing them. An abandoned/corrupt ticket
        # from an old run must not permanently block watch mode.
        if age_ns > max_age_ns:
            continue

        ticket = load_processing_ticket(ticket_path)

        created_at_ns = int(ticket.created_at_utc.timestamp() * 1_000_000_000)
        content_age_ns = now_ns - created_at_ns

        if content_age_ns < 0:
            raise LocalTenderWorkerError(
                f"processing ticket has a future created_at_utc: {ticket_path.name}"
            )

        # Filesystem mtime alone is not an authority for freshness: copying an
        # old ticket back into Downloads must not resurrect it.
        if content_age_ns > max_age_ns:
            continue

        if _done_path(state_dir, ticket.ticket_id).is_file():
            continue

        active.append(ticket)

    if not active:
        return None

    if len(active) > 1:
        names = ", ".join(sorted(ticket.path.name for ticket in active))
        raise LocalTenderWorkerError(
            f"multiple active tender tickets; refusing to guess: {names}"
        )

    ticket = active[0]
    ticket_mtime = ticket.path.stat().st_mtime_ns
    latest_allowed = ticket_mtime + max_age_ns

    candidates: list[Path] = []

    for zip_path in downloads_dir.glob(ZIP_GLOB):
        resolved = str(zip_path.resolve())
        if resolved in consumed:
            continue

        stat = zip_path.stat()
        if stat.st_mtime_ns < ticket_mtime:
            continue
        if stat.st_mtime_ns > latest_allowed:
            continue

        candidates.append(zip_path)

    if not candidates:
        return None

    if len(candidates) > 1:
        names = ", ".join(sorted(path.name for path in candidates))
        raise LocalTenderWorkerError(
            f"multiple candidate tender ZIPs; refusing to guess: {names}"
        )

    return LocalTenderJob(
        ticket=ticket,
        zip_path=candidates[0],
    )


def zip_is_settled(
    path: Path,
    *,
    settle_seconds: float = 2.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    try:
        first = path.stat()
    except OSError:
        return False

    if first.st_size <= 0:
        return False

    sleep_fn(settle_seconds)

    try:
        second = path.stat()
    except OSError:
        return False

    if first.st_size != second.st_size or first.st_mtime_ns != second.st_mtime_ns:
        return False

    return zipfile.is_zipfile(path)


def build_structured_local_import(job: LocalTenderJob) -> dict[str, Any]:
    source = OperatorZipAttachmentSource.from_path(
        job.zip_path,
        tender_code=job.ticket.tender_code,
        declare_complete=job.ticket.operator_declared_complete,
    )

    semantic_client = OllamaSemanticFallbackClient(
        model="gpt-oss:20b",
        thinking="medium",
    )

    raw = build_operator_annex_bundle_preview(
        source,
        tender_code=job.ticket.tender_code,
        semantic_client=semantic_client,
    )

    if raw.get("result") != "imported":
        raise LocalTenderWorkerError(
            str(raw.get("error") or "local tender bundle was rejected")
        )

    payload = {
        "contract_version": LOCAL_IMPORT_CONTRACT_VERSION,
        "tender_code": job.ticket.tender_code,
        "operator_declared_complete": job.ticket.operator_declared_complete,
        "raw": raw,
    }

    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    assert_no_portal_tokens(serialized, where="local_tender_worker_outbox")

    return payload


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LocalTenderWorkerError("could not hash paired tender ZIP") from exc

    return digest.hexdigest()


def load_or_build_outbox(
    job: LocalTenderJob,
    state_dir: Path,
) -> dict[str, Any]:
    path = _outbox_path(state_dir, job.ticket.ticket_id)
    current_zip_sha256 = _file_sha256(job.zip_path)

    if path.is_file():
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalTenderWorkerError("local worker outbox is unreadable") from exc

        if not isinstance(decoded, dict):
            raise LocalTenderWorkerError("local worker outbox is malformed")

        try:
            assert_no_portal_tokens(
                json.dumps(decoded, ensure_ascii=True, sort_keys=True),
                where="local_tender_worker_cached_outbox",
            )
        except AssertionError as exc:
            raise LocalTenderWorkerError(
                "local worker outbox contains forbidden portal token"
            ) from exc

        if decoded.get("contract_version") != LOCAL_IMPORT_CONTRACT_VERSION:
            raise LocalTenderWorkerError(
                "outbox contract_version does not match worker"
            )

        if decoded.get("tender_code") != job.ticket.tender_code:
            raise LocalTenderWorkerError("outbox tender_code does not match ticket")

        if (
            decoded.get("operator_declared_complete")
            is not job.ticket.operator_declared_complete
        ):
            raise LocalTenderWorkerError(
                "outbox operator_declared_complete does not match ticket"
            )

        raw = decoded.get("raw")
        archive = raw.get("archive") if isinstance(raw, dict) else None
        cached_zip_sha256 = (
            archive.get("zip_sha256") if isinstance(archive, dict) else None
        )

        if not isinstance(cached_zip_sha256, str):
            raise LocalTenderWorkerError(
                "local worker outbox is missing ZIP provenance"
            )

        if cached_zip_sha256.casefold() != current_zip_sha256:
            raise LocalTenderWorkerError("outbox ZIP digest does not match paired ZIP")

        return decoded

    payload = build_structured_local_import(job)

    raw = payload.get("raw")
    archive = raw.get("archive") if isinstance(raw, dict) else None
    built_zip_sha256 = archive.get("zip_sha256") if isinstance(archive, dict) else None

    if not isinstance(built_zip_sha256, str):
        raise LocalTenderWorkerError(
            "local processing result is missing ZIP provenance"
        )

    if built_zip_sha256.casefold() != current_zip_sha256:
        raise LocalTenderWorkerError(
            "local processing ZIP digest does not match paired ZIP"
        )

    _atomic_write_json(path, payload)
    return payload


def resolve_api_base_url() -> str:
    value = (os.environ.get(API_URL_ENV) or DEFAULT_API_BASE_URL).strip()

    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise LocalTenderWorkerError(
            f"{API_URL_ENV} must be a valid http(s) URL"
        ) from exc

    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise LocalTenderWorkerError(f"{API_URL_ENV} must be a valid http(s) URL")

    if parsed.username is not None or parsed.password is not None:
        raise LocalTenderWorkerError(f"{API_URL_ENV} must not contain URL credentials")

    if parsed.query or parsed.fragment:
        raise LocalTenderWorkerError(
            f"{API_URL_ENV} must not contain query or fragment components"
        )

    hostname = parsed.hostname.casefold()
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}

    if parsed.scheme == "http" and hostname not in loopback_hosts:
        raise LocalTenderWorkerError(
            f"{API_URL_ENV} requires HTTPS except for loopback development"
        )

    return value.rstrip("/")


def resolve_api_token() -> str:
    token = (os.environ.get(API_TOKEN_ENV) or "").strip()
    if not token:
        raise LocalTenderWorkerError(f"{API_TOKEN_ENV} is required for --apply")
    return token


def post_structured_local_import(
    *,
    api_base_url: str,
    api_token: str,
    tender_code: str,
    payload: dict[str, Any],
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    encoded_code = urllib.parse.quote(tender_code, safe="-")
    url = (
        f"{api_base_url}/operator/procurement/tenders/"
        f"{encoded_code}/annex-bundle/import"
    )

    body = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
        "User-Agent": "OriginLab-Local/1.0",
    }

    cf_access_client_id = (os.environ.get(CF_ACCESS_CLIENT_ID_ENV) or "").strip()
    cf_access_client_secret = (
        os.environ.get(CF_ACCESS_CLIENT_SECRET_ENV) or ""
    ).strip()

    if bool(cf_access_client_id) != bool(cf_access_client_secret):
        raise LocalTenderWorkerError(
            "Cloudflare Access credentials must be configured as a pair"
        )

    if cf_access_client_id:
        headers["CF-Access-Client-Id"] = cf_access_client_id
        headers["CF-Access-Client-Secret"] = cf_access_client_secret

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        raise LocalTenderWorkerHttpError(
            exc.code,
            f"OriginLab API rejected structured import with HTTP {exc.code}",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LocalTenderWorkerHttpError(
            None,
            "OriginLab API could not be reached",
        ) from exc

    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise LocalTenderWorkerHttpError(
            None,
            "OriginLab API returned invalid JSON",
        ) from exc

    if not isinstance(decoded, dict):
        raise LocalTenderWorkerHttpError(
            None,
            "OriginLab API returned an invalid response object",
        )

    response_tender_code = decoded.get("tender_code")
    if (
        not isinstance(response_tender_code, str)
        or response_tender_code.strip().casefold() != tender_code.strip().casefold()
    ):
        raise LocalTenderWorkerError(
            "OriginLab API response tender_code does not match request"
        )

    if decoded.get("persisted") is not True:
        raise LocalTenderWorkerHttpError(
            None,
            "OriginLab API did not confirm persistence",
        )

    return decoded


def mark_job_done(
    job: LocalTenderJob,
    state_dir: Path,
    payload: dict[str, Any],
) -> None:
    raw = payload.get("raw")
    archive = raw.get("archive") if isinstance(raw, dict) else None
    zip_sha256 = archive.get("zip_sha256") if isinstance(archive, dict) else None

    _atomic_write_json(
        _done_path(state_dir, job.ticket.ticket_id),
        {
            "ticket_id": job.ticket.ticket_id,
            "tender_code": job.ticket.tender_code,
            "zip_path": str(job.zip_path.resolve()),
            "zip_sha256": zip_sha256,
            "completed_at_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
        },
    )


def ticket_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "API_TOKEN_ENV",
    "API_URL_ENV",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_STATE_DIR",
    "LOCAL_IMPORT_CONTRACT_VERSION",
    "LocalTenderJob",
    "LocalTenderProcessingTicket",
    "LocalTenderWorkerError",
    "LocalTenderWorkerHttpError",
    "TICKET_CONTRACT_VERSION",
    "build_structured_local_import",
    "find_pending_job",
    "load_or_build_outbox",
    "load_processing_ticket",
    "mark_job_done",
    "post_structured_local_import",
    "resolve_api_base_url",
    "resolve_api_token",
    "ticket_digest",
    "zip_is_settled",
]
