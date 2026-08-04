"""Normalization helpers — reuse PR4/PR5B/PR5A parsers (no second implementation)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    AWARDED_STATUS_CODES,
    AWARDED_STATUS_NAMES,
    ACTIVE_STATUS_CODE,
    CANCELLED_STATUS_CODES,
    CANCELLED_STATUS_NAMES,
    CANONICAL_KIND_MERCADO_PUBLICO,
    CLOSED_STATUS_CODES,
    CLOSED_STATUS_NAMES,
    COALESCED_TENDER_ID_ALGORITHM,
    IDENTITY_NS_MERCADO_PUBLICO,
    IDENTITY_NS_PR4_CODIGO_EXTERNO,
    IDENTITY_NS_PR4_CODIGO_LICITACION,
    IDENTITY_NS_PR4_NUMERO_ADQUISICION,
    PR4_VERIFIED_TENDER_KEY_KINDS,
    PUBLICADA_STATUS_NAMES,
    SOURCE_RANK_OCDS_LISTA_INDEX,
    SOURCE_RANK_OCDS_RECORD,
    SOURCE_RANK_OCDS_RELEASE,
    SOURCE_RANK_PR4,
    SOURCE_RANK_TICKET_DETAIL,
    SOURCE_RANK_TICKET_SUMMARY,
    SOURCE_RANK_UNKNOWN,
    STATUS_CODE_EXPECTED_NAMES,
    STATUS_MEANING_AWARDED,
    STATUS_MEANING_CANCELLED,
    STATUS_MEANING_CLOSED,
    STATUS_MEANING_OPENISH,
    STATUS_MEANING_UNKNOWN,
    TIMESTAMP_PRECISION_DATE_ONLY,
    TIMESTAMP_PRECISION_MINUTE,
    TIMESTAMP_PRECISION_OFFSET_DATETIME,
    TIMESTAMP_PRECISION_RANK,
    TIMESTAMP_PRECISION_SECOND,
    TIMESTAMP_PRECISION_UNRESOLVED,
)

SANTIAGO = ZoneInfo(AS_OF_TIMEZONE)
UTC = timezone.utc

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_DMY_RE = re.compile(r"^\d{2}[-/]\d{2}[-/]\d{4}$")


def stable_content_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = canonical_json_digest(payload)[:24]
    return f"{prefix}_{digest}"


def coalesced_tender_id(
    *,
    identity_namespace: str,
    canonical_tender_key: str,
) -> str:
    """Stable ID from identity namespace + key only."""
    return stable_content_id(
        "coalesced_tender",
        {
            "algorithm": COALESCED_TENDER_ID_ALGORITHM,
            "identity_namespace": identity_namespace,
            "canonical_tender_key": canonical_tender_key,
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


def pr4_identity_namespace(*, tender_key_kind: str, cross_source_eligible: bool) -> str:
    """Namespace for PR4 signals. Only codigo_externo + MP shape joins Plane B."""
    kind = (tender_key_kind or "").strip()
    if kind == "codigo_externo" and cross_source_eligible:
        return IDENTITY_NS_MERCADO_PUBLICO
    if kind == "codigo_externo":
        return IDENTITY_NS_PR4_CODIGO_EXTERNO
    if kind == "codigo_licitacion":
        return IDENTITY_NS_PR4_CODIGO_LICITACION
    if kind == "numero_adquisicion":
        return IDENTITY_NS_PR4_NUMERO_ADQUISICION
    return IDENTITY_NS_PR4_CODIGO_EXTERNO


def accept_pr4_signal_identity(
    *,
    raw_key: str | None,
    tender_key_kind: str | None,
) -> tuple[str | None, str | None, str | None, bool, str | None]:
    """Return (key, kind, unresolved_reason, cross_source_eligible, identity_namespace).

    Cross-source eligibility requires codigo_externo + exact MP CodigoExterno shape.
    codigo_licitacion / numero_adquisicion never join Plane B by text shape alone.
    """
    kind = (tender_key_kind or "").strip()
    if kind not in PR4_VERIFIED_TENDER_KEY_KINDS:
        if not kind:
            return None, None, "pr4_tender_key_kind_unsupported", False, None
        return None, kind, "pr4_tender_key_kind_unsupported", False, None
    if raw_key is None or not str(raw_key).strip():
        return None, kind, "pr4_canonical_key_missing", False, None
    norm = normalize_pr4_canonical_key(raw_key)
    if not norm:
        return None, kind, "pr4_canonical_key_missing", False, None
    if any(ch in norm for ch in ("/", "\\", "\x00", "\n", "\r", "\t")):
        return None, kind, "pr4_canonical_identity_corrupt", False, None
    mp_shape = is_mercado_publico_codigo_shape(norm)
    # Only codigo_externo may be cross-source eligible.
    cross = bool(mp_shape and kind == "codigo_externo")
    namespace = pr4_identity_namespace(
        tender_key_kind=kind, cross_source_eligible=cross
    )
    return norm, kind, None, cross, namespace


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


@dataclass(frozen=True)
class NormalizedTimestamp:
    raw: str | None
    utc_instant: datetime | None
    santiago_date: date | None
    precision: str
    reason: str | None = None

    @property
    def utc_iso(self) -> str | None:
        if self.utc_instant is None:
            return None
        return self.utc_instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_date_only(s: str) -> date | None:
    if _DATE_ONLY_RE.match(s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    if _DATE_DMY_RE.match(s):
        parts = re.split(r"[-/]", s)
        try:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            return date(y, m, d)
        except ValueError:
            return None
    return None


def normalize_tender_timestamp(raw: str | None) -> NormalizedTimestamp:
    """Parse tender publication/close with precision metadata."""
    if raw is None or not str(raw).strip():
        return NormalizedTimestamp(
            raw=None,
            utc_instant=None,
            santiago_date=None,
            precision=TIMESTAMP_PRECISION_UNRESOLVED,
            reason=None,
        )
    s = str(raw).strip()
    date_only = _parse_date_only(s)
    if date_only is not None and "T" not in s and " " not in s:
        # Interpret ChileCompra date-only as America/Santiago calendar date.
        return NormalizedTimestamp(
            raw=s,
            utc_instant=None,
            santiago_date=date_only,
            precision=TIMESTAMP_PRECISION_DATE_ONLY,
            reason=None,
        )

    aware = parse_trusted_utc_timestamp(s)
    if aware is not None:
        precision = TIMESTAMP_PRECISION_OFFSET_DATETIME
        if aware.second == 0 and "." not in s.split("+")[0].split("-")[-1]:
            # Heuristic: no fractional seconds and minute-aligned → at least minute.
            if re.search(r"T\d{2}:\d{2}(:\d{2})?", s):
                if re.search(r"T\d{2}:\d{2}:\d{2}", s):
                    precision = TIMESTAMP_PRECISION_SECOND
                else:
                    precision = TIMESTAMP_PRECISION_MINUTE
        elif re.search(r"T\d{2}:\d{2}:\d{2}", s):
            precision = TIMESTAMP_PRECISION_SECOND
        return NormalizedTimestamp(
            raw=s,
            utc_instant=aware,
            santiago_date=aware.astimezone(SANTIAGO).date(),
            precision=precision,
            reason=None,
        )

    santiago_dt = parse_close_at_america_santiago(s)
    if santiago_dt is not None:
        precision = TIMESTAMP_PRECISION_SECOND
        if re.search(r"\d{2}:\d{2}$", s) and not re.search(r"\d{2}:\d{2}:\d{2}", s):
            precision = TIMESTAMP_PRECISION_MINUTE
        return NormalizedTimestamp(
            raw=s,
            utc_instant=santiago_dt.astimezone(UTC),
            santiago_date=santiago_dt.astimezone(SANTIAGO).date(),
            precision=precision,
            reason=None,
        )
    return NormalizedTimestamp(
        raw=s,
        utc_instant=None,
        santiago_date=None,
        precision=TIMESTAMP_PRECISION_UNRESOLVED,
        reason="timezone_unresolved",
    )


def parse_tender_timestamp_raw(
    raw: str | None,
) -> tuple[datetime | None, str | None]:
    """Backward-compatible wrapper → (utc_instant_or_none, reason)."""
    nt = normalize_tender_timestamp(raw)
    if nt.precision == TIMESTAMP_PRECISION_DATE_ONLY and nt.santiago_date is not None:
        # Represent date-only as start-of-day Santiago for legacy callers.
        dt = datetime(
            nt.santiago_date.year,
            nt.santiago_date.month,
            nt.santiago_date.day,
            tzinfo=SANTIAGO,
        ).astimezone(UTC)
        return dt, None
    return nt.utc_instant, nt.reason


def timestamps_compatible(a: NormalizedTimestamp, b: NormalizedTimestamp) -> bool | None:
    """Return True compatible, False conflict, None if either missing/unresolved."""
    if a.raw is None or b.raw is None:
        return None
    if a.precision == TIMESTAMP_PRECISION_UNRESOLVED or b.precision == TIMESTAMP_PRECISION_UNRESOLVED:
        return None
    if (
        a.precision == TIMESTAMP_PRECISION_DATE_ONLY
        and b.precision == TIMESTAMP_PRECISION_DATE_ONLY
    ):
        return a.santiago_date == b.santiago_date
    if a.precision == TIMESTAMP_PRECISION_DATE_ONLY or b.precision == TIMESTAMP_PRECISION_DATE_ONLY:
        # date-only vs precise: same Santiago calendar date → compatible.
        if a.santiago_date is None or b.santiago_date is None:
            return None
        return a.santiago_date == b.santiago_date
    # Both precise.
    if a.utc_instant is None or b.utc_instant is None:
        return None
    return a.utc_instant == b.utc_instant


def prefer_higher_precision(
    a: NormalizedTimestamp, b: NormalizedTimestamp
) -> NormalizedTimestamp:
    ra = TIMESTAMP_PRECISION_RANK.get(a.precision, 0)
    rb = TIMESTAMP_PRECISION_RANK.get(b.precision, 0)
    if rb > ra:
        return b
    if ra > rb:
        return a
    # Equal precision: prefer the one with utc instant.
    if a.utc_instant is None and b.utc_instant is not None:
        return b
    return a


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


def normalized_status_meaning(
    status_code: str | None, status_name: str | None
) -> str | None:
    """Map code/name to lifecycle meaning; None if no status evidence."""
    code = (status_code or "").strip()
    name = (status_name or "").strip()
    if not code and not name:
        return None
    if status_internally_inconsistent(code, name):
        return STATUS_MEANING_UNKNOWN
    if code in AWARDED_STATUS_CODES or status_name_matches_expected(
        name, AWARDED_STATUS_NAMES
    ):
        return STATUS_MEANING_AWARDED
    if code in CANCELLED_STATUS_CODES or status_name_matches_expected(
        name, CANCELLED_STATUS_NAMES
    ):
        return STATUS_MEANING_CANCELLED
    if code in CLOSED_STATUS_CODES or status_name_matches_expected(
        name, CLOSED_STATUS_NAMES
    ):
        return STATUS_MEANING_CLOSED
    if code == ACTIVE_STATUS_CODE or status_name_matches_expected(
        name, PUBLICADA_STATUS_NAMES
    ):
        return STATUS_MEANING_OPENISH
    return STATUS_MEANING_UNKNOWN


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


def normalize_buyer_identity(value: str | None) -> str | None:
    """Conservative display-independent buyer identity when source IDs absent."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    return text or None
