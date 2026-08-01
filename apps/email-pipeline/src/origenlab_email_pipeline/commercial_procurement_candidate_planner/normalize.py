"""Normalization helpers — reuse PR4/PR5B/PR5A parsers (no second implementation)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
    sha256_hex,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    is_mercado_publico_codigo_shape,
    normalize_mercado_publico_codigo,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.artifact_open import (
    parse_close_at_america_santiago,
    parse_trusted_utc_timestamp,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    AS_OF_TIMEZONE,
    CANONICAL_KIND_MERCADO_PUBLICO,
    COALESCED_TENDER_ID_ALGORITHM,
    PR4_VERIFIED_TENDER_KEY_KINDS,
    SOURCE_RANK_OCDS_LISTA_INDEX,
    SOURCE_RANK_OCDS_RECORD,
    SOURCE_RANK_OCDS_RELEASE,
    SOURCE_RANK_PR4,
    SOURCE_RANK_TICKET_DETAIL,
    SOURCE_RANK_TICKET_SUMMARY,
    SOURCE_RANK_UNKNOWN,
    STATUS_CODE_EXPECTED_NAMES,
)

SANTIAGO = ZoneInfo(AS_OF_TIMEZONE)
UTC = timezone.utc


def stable_content_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = canonical_json_digest(payload)[:24]
    return f"{prefix}_{digest}"


def coalesced_tender_id(*, canonical_tender_key: str, tender_key_kind: str) -> str:
    """Stable ID from identity only — evidence refresh must not change it."""
    return stable_content_id(
        "coalesced_tender",
        {
            "algorithm": COALESCED_TENDER_ID_ALGORITHM,
            "canonical_tender_key": canonical_tender_key,
            "tender_key_kind": tender_key_kind,
        },
    )


def accept_canonical_tender_key(
    *,
    candidate: str | None,
    candidate_kind: str | None,
) -> tuple[str | None, str | None]:
    """Plane B: only exact mercado_publico_codigo_externo shape may join."""
    if not candidate or not str(candidate).strip():
        return None, "live_canonical_candidate_missing"
    if candidate_kind != CANONICAL_KIND_MERCADO_PUBLICO:
        if candidate_kind in {None, "", "none"}:
            return None, "live_canonical_candidate_missing"
        return None, "unsupported_candidate_kind"
    norm = normalize_mercado_publico_codigo(candidate)
    if not norm:
        return None, "live_canonical_candidate_missing"
    if not is_mercado_publico_codigo_shape(norm):
        return None, "live_canonical_candidate_malformed"
    return norm, None


def normalize_pr4_canonical_key(raw_key: str | None) -> str | None:
    """Bounded whitespace/case normalization for Plane A PR4 keys (no MP regex)."""
    return normalize_mercado_publico_codigo(raw_key)


def accept_pr4_signal_identity(
    *,
    raw_key: str | None,
    tender_key_kind: str | None,
) -> tuple[str | None, str | None, str | None, bool]:
    """Return (canonical_key, tender_key_kind, unresolved_reason, cross_source_eligible).

    Verified PR4 kinds keep Plane A identity without requiring the stricter
    PR5B Mercado Público CodigoExterno cross-source regex.
    """
    kind = (tender_key_kind or "").strip()
    if kind not in PR4_VERIFIED_TENDER_KEY_KINDS:
        if not kind:
            return None, None, "pr4_tender_key_kind_unsupported", False
        return None, kind, "pr4_tender_key_kind_unsupported", False
    if raw_key is None or not str(raw_key).strip():
        return None, kind, "pr4_canonical_key_missing", False
    norm = normalize_pr4_canonical_key(raw_key)
    if not norm:
        return None, kind, "pr4_canonical_key_missing", False
    # Impossible identity: control characters / path separators after normalize.
    if any(ch in norm for ch in ("/", "\\", "\x00", "\n", "\r", "\t")):
        return None, kind, "pr4_canonical_identity_corrupt", False
    cross = is_mercado_publico_codigo_shape(norm)
    return norm, kind, None, cross


def parse_as_of_utc(value: str) -> datetime:
    """Require timezone-aware ISO timestamp; normalize to UTC."""
    dt = parse_trusted_utc_timestamp(value)
    if dt is None:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError as exc:
            raise ValueError(f"malformed timezone-aware as_of_utc: {value!r}") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"malformed timezone-aware as_of_utc: {value!r}")
        dt = parsed.astimezone(UTC)
    return dt


def as_of_america_santiago(as_of_utc: datetime) -> datetime:
    return as_of_utc.astimezone(SANTIAGO)


def parse_acquisition_acquired_at(
    raw: str | None, *, as_of_utc: datetime
) -> tuple[datetime | None, str | None]:
    if raw is None or not str(raw).strip():
        return None, "acquisition_timestamp_missing"
    dt = parse_trusted_utc_timestamp(raw)
    if dt is None:
        return None, "acquisition_timestamp_invalid"
    if dt > as_of_utc:
        return None, "acquisition_timestamp_invalid"
    return dt, None


def parse_tender_timestamp_raw(
    raw: str | None,
) -> tuple[datetime | None, str | None]:
    """Parse tender publication/close timestamps to UTC.

    Aware ISO → UTC. Naive ChileCompra wall times → America/Santiago.
    Unparseable → (None, timezone_unresolved).
    """
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    aware = parse_trusted_utc_timestamp(s)
    if aware is not None:
        return aware, None
    santiago_dt = parse_close_at_america_santiago(s)
    if santiago_dt is not None:
        return santiago_dt.astimezone(UTC), None
    return None, "timezone_unresolved"


def utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def rank_class_for_live(
    *, source_kind: str, endpoint_kind: str, release_kind: str | None
) -> str:
    sk = (source_kind or "").casefold()
    ek = (endpoint_kind or "").casefold()
    rk = (release_kind or "").casefold()
    if "lista" in rk or "lista_index" in rk:
        return "ocds_lista_index"
    if "ticket" in sk or "ticket" in ek:
        if "detail" in ek:
            return "ticket_detail"
        return "ticket_summary"
    if rk in {"historical", "compiled"} or "release" in rk:
        return "ocds_release"
    if "record" in rk:
        return "ocds_record"
    if "ocds" in sk:
        return "ocds_release"
    return "unknown"


def rank_score(rank_class: str) -> int:
    return {
        "ticket_detail": SOURCE_RANK_TICKET_DETAIL,
        "ticket_summary": SOURCE_RANK_TICKET_SUMMARY,
        "ocds_release": SOURCE_RANK_OCDS_RELEASE,
        "ocds_record": SOURCE_RANK_OCDS_RECORD,
        "pr4": SOURCE_RANK_PR4,
        "ocds_lista_index": SOURCE_RANK_OCDS_LISTA_INDEX,
        "unknown": SOURCE_RANK_UNKNOWN,
    }.get(rank_class, SOURCE_RANK_UNKNOWN)


def field_capable(rank_class: str, field_name: str) -> bool:
    if rank_class == "ocds_lista_index":
        return field_name in {"canonical_identity"}
    return True


def status_name_matches_expected(name: str | None, expected: frozenset[str]) -> bool:
    """True when status_name equals or is a ChileCompra family prefix of expected.

    Production PR4 names often append legal citations, e.g.
    ``Desierta (o art. 3 ó 9 Ley 19.886)``.
    """
    n = (name or "").strip().casefold()
    if not n:
        return False
    if n in expected:
        return True
    return any(n.startswith(fam) for fam in expected)


def status_internally_inconsistent(
    status_code: str | None, status_name: str | None
) -> bool:
    code = (status_code or "").strip()
    name = (status_name or "").strip()
    if not code or not name:
        return False
    expected = STATUS_CODE_EXPECTED_NAMES.get(code)
    if expected is None:
        return False
    return not status_name_matches_expected(name, expected)


def hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


def closing_bucket_for_delta(delta: timedelta) -> str:
    secs = delta.total_seconds()
    if secs < 0:
        return "not_applicable"
    hours = secs / 3600.0
    if hours < 24:
        return "lt_24h"
    days = hours / 24.0
    if days <= 3:
        return "d1_to_d3"
    if days <= 7:
        return "d4_to_d7"
    return "gt_7d"


def sha256_text(text: str) -> str:
    return sha256_hex(text)


def evidence_acquisition_is_current(
    *,
    acquired_at_utc: str | None,
    as_of_utc: datetime,
    freshness_threshold_hours: int,
) -> tuple[bool, str]:
    dt, err = parse_acquisition_acquired_at(acquired_at_utc, as_of_utc=as_of_utc)
    if err or dt is None:
        return False, err or "acquisition_timestamp_missing"
    age = hours_between(as_of_utc, dt)
    if age > float(freshness_threshold_hours):
        return False, "stale_authoritative_snapshot"
    return True, "current_authoritative_snapshot"
