"""Deterministic open-tender classification for PR5A artifact rows.

PR5A never makes authenticated ChileCompra calls, so ``live_verified_open``
is unreachable here. Artifact rows may be ``recent_artifact_declared_open``
only after strict status, date, provenance, and freshness checks.

Freshness is anchored to a **trusted acquisition / generation timestamp**, not
file ``mtime``. The 48-hour window is an analytical threshold for “recent
artifact-declared open” — it is **not** proof of current API status.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.equipment_first_licitacion_queue import parse_close_date

AS_OF_TIMEZONE = "America/Santiago"
SANTIAGO = ZoneInfo(AS_OF_TIMEZONE)
UTC = ZoneInfo("UTC")

# Analytical freshness window for "recent" artifact-declared open rows.
# Not proof of live ChileCompra status — PR5A does not revalidate via API.
ARTIFACT_RECENT_MAX_AGE = timedelta(hours=48)
ARTIFACT_RECENT_MAX_AGE_HOURS = 48
# Generated/published timestamp vs filesystem mtime disagreement beyond this
# downgrades provenance (mtime may have been touched without re-acquisition).
GENERATED_VS_MTIME_MAX_DELTA = timedelta(hours=24)

OPEN_CLASSIFICATION_VALUES = (
    "live_verified_open",
    "recent_artifact_declared_open",
    "stale_artifact_declared_open",
    "artifact_declared_open_unverified_provenance",
    "artifact_not_open",
    "status_or_date_conflict",
    "date_unparseable",
)

ACTIVE_CHILECOMPRA_STATUS_CODE = "5"
INACTIVE_STATUS_CODES = frozenset({"6", "7", "8", "18", "19"})
INACTIVE_STATUS_NAMES = frozenset(
    {"cerrada", "desierta", "adjudicada", "revocada", "suspendida"}
)
PUBLICADA = "publicada"

_FILENAME_DATE_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})")

# Precedence for effective artifact timestamp (trusted → fallback).
TRUSTED_TIMESTAMP_KEYS: tuple[tuple[str, str], ...] = (
    ("api_checked_at_utc", "api_checked_at_utc"),
    ("queried_at_utc", "queried_at_utc"),
    ("generated_at_utc", "generated_at_utc"),
    ("published_at_utc", "published_at_utc"),
    ("created_at_utc", "source_acquisition_created_at_utc"),
    ("acquired_at_utc", "source_acquisition_acquired_at_utc"),
)

SOURCE_QUERY_ALLOWLIST = frozenset(
    {
        "estado",
        "fecha",
        "source_kind",
        "row_count",
        "source_output_rows",
        "endpoint_kind",
        "endpoint_path",
    }
)

SECRET_PARAM_NAMES = frozenset(
    {
        "ticket",
        "token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "authorization",
        "auth",
        "password",
        "passwd",
    }
)

EQUIPMENT_PRIORITY: dict[str, int] = {
    "centrifuge": 0,
    "balance": 1,
    "ultrasonic_processor": 2,
    "sonicator": 2,  # legacy source alias priority band
    "incubator": 3,
    "homogenizer": 4,
    "osmometer": 5,
    "lab_ultrasonic_processor": 2,
}


@dataclass(frozen=True)
class ArtifactProvenance:
    path: str
    sha256: str
    size_bytes: int
    mtime_utc: str
    filename_date: str | None
    generated_at_utc: str | None
    effective_artifact_timestamp_utc: str | None
    effective_timestamp_source: str
    generated_vs_mtime_delta_seconds: float | None
    timestamp_validation_status: str
    source_query_metadata: dict[str, Any]
    manifest_status: str  # canonical | stale | absent | unknown
    artifact_age_seconds: float | None
    provenance_status: str  # valid | insufficient | missing
    freshness_status: str  # recent | stale | unknown

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OpenClassification:
    open_class: str
    validity_status: str
    status_code: str
    status_name: str
    close_raw: str
    close_at_america_santiago: str | None
    as_of_america_santiago: str
    reasons: tuple[str, ...]
    provenance: ArtifactProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        return d


def _ensure_aware_santiago(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SANTIAGO)
    return dt.astimezone(SANTIAGO)


def parse_close_at_america_santiago(raw: str | None) -> datetime | None:
    """Parse naive ChileCompra close strings as America/Santiago wall time."""
    parsed = parse_close_date(raw or "")
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(SANTIAGO)
    return parsed.replace(tzinfo=SANTIAGO)


def filename_date_iso(path: Path) -> str | None:
    m = _FILENAME_DATE_RE.search(path.name)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def parse_trusted_utc_timestamp(raw: str | None) -> datetime | None:
    """Parse a trusted timestamp strictly as timezone-aware UTC.

    Naive strings are rejected (not assumed local or Santiago).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def _normalize_endpoint_path(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.scheme and not parsed.netloc and parsed.path.startswith("/"):
        return parsed.path
    if parsed.scheme or parsed.netloc:
        return urlunparse(("", "", parsed.path or "", "", "", ""))
    return None


def _is_secret_key(key: str) -> bool:
    k = key.strip().lower().replace("-", "_")
    if k in SECRET_PARAM_NAMES:
        return True
    return any(s in k for s in ("ticket", "token", "secret", "api_key", "authorization"))


def sanitize_source_query_metadata(raw: Any) -> dict[str, Any]:
    """Allowlist structural fields; recursively redact secrets and URL tickets."""

    def scrub_string(value: str, *, parent_key: str = "") -> str | None:
        if _is_secret_key(parent_key):
            return None
        lower = value.lower()
        # Drop any string that embeds credential-like query material.
        if any(
            marker in lower
            for marker in (
                "ticket=",
                "token=",
                "api_key=",
                "apikey=",
                "secret=",
                "authorization=",
                "ticket%3d",
                "token%3d",
                "api_key%3d",
            )
        ):
            if "://" in value or value.startswith("/"):
                path = _normalize_endpoint_path(
                    value if "://" in value else f"https://placeholder.local{value}"
                )
                return path
            return None
        if "://" in value or (
            "?" in value and any(s in lower for s in SECRET_PARAM_NAMES)
        ):
            try:
                path = _normalize_endpoint_path(value)
                return path
            except ValueError:
                return None
        return value

    def scrub(value: Any, *, parent_key: str = "") -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                key = str(k)
                if _is_secret_key(key):
                    continue
                if key.lower() in {"query", "url", "request_url", "href", "endpoint"}:
                    if isinstance(v, str):
                        path_or_none = scrub_string(v, parent_key=key)
                        if path_or_none and path_or_none.startswith("/"):
                            out["endpoint_path"] = path_or_none
                    continue
                scrubbed = scrub(v, parent_key=key)
                if scrubbed is None:
                    continue
                if key in SOURCE_QUERY_ALLOWLIST or key == "endpoint_path":
                    out[key] = scrubbed
                elif isinstance(scrubbed, (dict, list)) and scrubbed:
                    out[key] = scrubbed
            return out
        if isinstance(value, list):
            return [scrub(v, parent_key=parent_key) for v in value]
        if isinstance(value, str):
            return scrub_string(value, parent_key=parent_key)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)

    if not isinstance(raw, dict):
        return {}
    scrubbed = scrub(raw)
    if not isinstance(scrubbed, dict):
        return {}
    allowed: dict[str, Any] = {}
    for k, v in scrubbed.items():
        if k in SOURCE_QUERY_ALLOWLIST or k == "endpoint_path":
            allowed[k] = v
        elif k in {"parse_error"} and v is True:
            allowed[k] = True
    return allowed


def resolve_effective_artifact_timestamp(
    *,
    source_meta: dict[str, Any],
    filename_date: str | None,
    mtime: datetime | None,
    as_of_utc: datetime,
) -> tuple[datetime | None, str, str, float | None]:
    """Return (effective_utc, source, validation_status, generated_vs_mtime_delta_s)."""
    reasons: list[str] = []
    trusted: list[tuple[str, datetime]] = []
    for key, source_label in TRUSTED_TIMESTAMP_KEYS:
        raw = source_meta.get(key)
        if not raw:
            continue
        parsed = parse_trusted_utc_timestamp(str(raw))
        if parsed is None:
            reasons.append(f"malformed:{key}")
            continue
        if parsed > as_of_utc:
            reasons.append(f"future:{key}")
            continue
        trusted.append((source_label, parsed))

    published = None
    pub_raw = source_meta.get("published_at_utc")
    if pub_raw:
        published = parse_trusted_utc_timestamp(str(pub_raw))

    # Prefer first successful key in precedence order.
    effective: datetime | None = None
    source = "missing"
    if trusted:
        source, effective = trusted[0]
        # Reject source acquisition timestamp after publication when both present.
        if published is not None and effective > published and source != "published_at_utc":
            reasons.append("source_timestamp_after_publication")
            effective = None
            source = "invalid"

    gen_vs_mtime: float | None = None
    if mtime is not None and effective is not None:
        gen_vs_mtime = abs((effective - mtime).total_seconds())
        if gen_vs_mtime > GENERATED_VS_MTIME_MAX_DELTA.total_seconds():
            reasons.append("excessive_generated_vs_mtime_disagreement")

    if filename_date and effective is not None:
        try:
            fd = datetime.fromisoformat(filename_date).date()
            if abs((effective.date() - fd).days) > 1:
                reasons.append("filename_date_inconsistent_with_trusted_timestamp")
        except ValueError:
            reasons.append("filename_date_unparseable")

    if effective is None and filename_date and "malformed" not in ",".join(reasons):
        try:
            # Filename date is date-only → reduced precision/confidence (noon UTC).
            fd = datetime.fromisoformat(filename_date).replace(tzinfo=UTC)
            # Use start of day UTC as reduced-precision anchor.
            effective = fd
            source = "filename_date_reduced_precision"
            reasons.append("filename_date_reduced_precision")
        except ValueError:
            reasons.append("filename_date_unparseable")

    if effective is None and mtime is not None:
        # mtime alone is an unverified fallback — never qualifies as recent declared open.
        source = "mtime_unverified_fallback"
        effective = mtime
        reasons.append("mtime_only_unverified")

    if reasons and any(
        r.startswith("malformed:")
        or r.startswith("future:")
        or r
        in {
            "source_timestamp_after_publication",
            "excessive_generated_vs_mtime_disagreement",
            "filename_date_inconsistent_with_trusted_timestamp",
            "mtime_only_unverified",
        }
        for r in reasons
    ):
        if "mtime_only_unverified" in reasons and source == "mtime_unverified_fallback":
            status = "mtime_only_unverified"
        elif any(r.startswith("future:") for r in reasons) and not trusted:
            status = "future_timestamp_rejected"
        elif any(r.startswith("malformed:") for r in reasons) and not trusted:
            status = "malformed_timestamp"
        elif "excessive_generated_vs_mtime_disagreement" in reasons:
            status = "generated_mtime_disagreement"
        elif "filename_date_inconsistent_with_trusted_timestamp" in reasons:
            status = "filename_date_inconsistent"
        elif "source_timestamp_after_publication" in reasons:
            status = "source_after_publication"
        else:
            status = "downgraded:" + ",".join(reasons)
    elif effective is None:
        status = "missing"
    elif source.startswith("filename_date"):
        status = "filename_date_reduced_precision"
    else:
        status = "ok"

    return effective, source, status, gen_vs_mtime


def inspect_artifact_provenance(
    path: Path,
    *,
    as_of: datetime,
    manifest: dict[str, Any] | None = None,
) -> ArtifactProvenance:
    """Collect provenance/freshness metadata for an operator-queue artifact."""
    as_of_s = _ensure_aware_santiago(as_of)
    as_of_utc = as_of_s.astimezone(UTC)
    data = path.read_bytes() if path.is_file() else b""
    digest = hashlib.sha256(data).hexdigest() if data else ""
    st = path.stat() if path.is_file() else None
    mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC) if st else None

    companion = Path(str(path) + ".manifest.json")
    if not companion.is_file():
        companion = path.with_suffix(path.suffix + ".manifest.json")
    if not companion.is_file():
        companion = path.with_name(path.stem + ".manifest.json")

    source_meta_raw: dict[str, Any] = {}
    if companion.is_file():
        try:
            loaded = json.loads(companion.read_text(encoding="utf-8"))
            source_meta_raw = loaded if isinstance(loaded, dict) else {"parse_error": True}
        except (json.JSONDecodeError, OSError):
            source_meta_raw = {"parse_error": True}

    fname_date = filename_date_iso(path)
    (
        effective,
        ts_source,
        ts_validation,
        gen_vs_mtime,
    ) = resolve_effective_artifact_timestamp(
        source_meta=source_meta_raw if isinstance(source_meta_raw, dict) else {},
        filename_date=fname_date,
        mtime=mtime,
        as_of_utc=as_of_utc,
    )

    age = (as_of_utc - effective).total_seconds() if effective is not None else None

    # Prefer a display generated_at from trusted keys (not mtime).
    generated_at: str | None = None
    for key, _ in TRUSTED_TIMESTAMP_KEYS:
        if source_meta_raw.get(key):
            parsed = parse_trusted_utc_timestamp(str(source_meta_raw[key]))
            if parsed is not None:
                generated_at = parsed.isoformat().replace("+00:00", "Z")
                break

    source_query_metadata = sanitize_source_query_metadata(source_meta_raw)

    manifest_status = "absent"
    if manifest:
        name = path.name
        canonical = [str(x) for x in (manifest.get("canonical_files") or [])]
        stale = [str(x) for x in (manifest.get("stale_files") or [])]
        publish = manifest.get("chilecompra_api_publish") or {}
        published_queue = str(publish.get("published_queue") or "")
        source_manifest = str(publish.get("source_manifest") or "")
        if name in canonical or name == published_queue:
            manifest_status = "canonical"
        elif name in stale:
            manifest_status = "stale"
        elif companion.name == source_manifest or name.replace(
            "chilecompra_api_", ""
        ) in canonical:
            if published_queue and Path(published_queue).name in canonical:
                manifest_status = "canonical"
            else:
                manifest_status = "unknown"
        else:
            manifest_status = "unknown"

    has_sha = bool(digest)
    trusted_ok = (
        effective is not None
        and ts_source
        not in {
            "mtime_unverified_fallback",
            "missing",
            "invalid",
        }
        and ts_validation
        in {
            "ok",
            "filename_date_reduced_precision",
        }
    )

    if not path.is_file() or not has_sha:
        provenance_status = "missing"
    elif ts_validation in {
        "mtime_only_unverified",
        "malformed_timestamp",
        "future_timestamp_rejected",
        "generated_mtime_disagreement",
        "filename_date_inconsistent",
        "source_after_publication",
        "missing",
    }:
        provenance_status = "insufficient"
    elif manifest_status == "stale":
        provenance_status = "insufficient"
    elif not trusted_ok:
        provenance_status = "insufficient"
    elif manifest_status in {"absent", "unknown"} and ts_source.startswith(
        "filename_date"
    ):
        provenance_status = "insufficient"
    else:
        provenance_status = "valid"

    # Freshness follows trusted acquisition age — never mtime alone.
    # Disagreement may still mark provenance insufficient, but a clearly stale
    # trusted timestamp must not become "recent" via a freshly touched mtime.
    if (
        effective is None
        or ts_source == "mtime_unverified_fallback"
        or age is None
        or age < 0
    ):
        freshness_status = "unknown"
    elif age > ARTIFACT_RECENT_MAX_AGE.total_seconds():
        freshness_status = "stale"
    elif provenance_status == "valid" and ts_validation in {
        "ok",
        "filename_date_reduced_precision",
    }:
        freshness_status = "recent"
    else:
        # Trusted age would be recent, but provenance was downgraded.
        freshness_status = "unknown"

    return ArtifactProvenance(
        path=str(path),
        sha256=digest,
        size_bytes=int(st.st_size) if st else 0,
        mtime_utc=mtime.isoformat().replace("+00:00", "Z") if mtime else "",
        filename_date=fname_date,
        generated_at_utc=generated_at,
        effective_artifact_timestamp_utc=(
            effective.isoformat().replace("+00:00", "Z") if effective else None
        ),
        effective_timestamp_source=ts_source,
        generated_vs_mtime_delta_seconds=gen_vs_mtime,
        timestamp_validation_status=ts_validation,
        source_query_metadata=source_query_metadata,
        manifest_status=manifest_status,
        artifact_age_seconds=age,
        provenance_status=provenance_status,
        freshness_status=freshness_status,
    )


def classify_artifact_row_open(
    row: dict[str, Any],
    *,
    as_of: datetime,
    provenance: ArtifactProvenance | None = None,
    live_api_revalidated: bool = False,
) -> OpenClassification:
    """Classify one queue/API artifact row. Never invents live verification."""
    as_of_s = _ensure_aware_santiago(as_of)
    validity = str(row.get("validity_status") or "").strip().lower()
    code = str(row.get("chilecompra_status_code") or "").strip()
    name = str(row.get("chilecompra_status") or row.get("status_name") or "").strip()
    name_l = name.lower()
    close_raw = str(row.get("close_date") or row.get("close_at") or "").strip()
    reasons: list[str] = []

    close_at = parse_close_at_america_santiago(close_raw)
    close_iso = close_at.isoformat() if close_at else None

    base = dict(
        validity_status=validity,
        status_code=code,
        status_name=name,
        close_raw=close_raw,
        close_at_america_santiago=close_iso,
        as_of_america_santiago=as_of_s.isoformat(),
        provenance=provenance,
    )

    if validity == "open" and code and code != ACTIVE_CHILECOMPRA_STATUS_CODE:
        reasons.append("validity_open_but_status_code_not_5")
        return OpenClassification(
            open_class="status_or_date_conflict", reasons=tuple(reasons), **base
        )
    if code in INACTIVE_STATUS_CODES and validity == "open":
        reasons.append("inactive_status_code_with_validity_open")
        return OpenClassification(
            open_class="status_or_date_conflict", reasons=tuple(reasons), **base
        )
    if name_l in INACTIVE_STATUS_NAMES and (
        validity == "open" or code == ACTIVE_CHILECOMPRA_STATUS_CODE
    ):
        reasons.append("inactive_status_name_conflicts_with_open_or_code_5")
        return OpenClassification(
            open_class="status_or_date_conflict", reasons=tuple(reasons), **base
        )
    if code == ACTIVE_CHILECOMPRA_STATUS_CODE and name and name_l != PUBLICADA:
        reasons.append("code_5_with_non_publicada_status_name")
        return OpenClassification(
            open_class="status_or_date_conflict", reasons=tuple(reasons), **base
        )

    if validity != "open":
        reasons.append("validity_status_not_open")
        return OpenClassification(
            open_class="artifact_not_open", reasons=tuple(reasons), **base
        )
    if code != ACTIVE_CHILECOMPRA_STATUS_CODE:
        reasons.append("status_code_not_5")
        return OpenClassification(
            open_class="artifact_not_open", reasons=tuple(reasons), **base
        )
    if name and name_l != PUBLICADA:
        reasons.append("status_name_present_but_not_publicada")
        return OpenClassification(
            open_class="status_or_date_conflict", reasons=tuple(reasons), **base
        )

    if not close_raw:
        reasons.append("close_date_missing")
        return OpenClassification(
            open_class="date_unparseable", reasons=tuple(reasons), **base
        )
    if close_at is None:
        reasons.append("close_date_unparseable")
        return OpenClassification(
            open_class="date_unparseable", reasons=tuple(reasons), **base
        )
    if close_at <= as_of_s:
        reasons.append("close_at_not_strictly_after_as_of")
        return OpenClassification(
            open_class="artifact_not_open", reasons=tuple(reasons), **base
        )

    reasons.append("status_and_close_pass_declared_open_checks")

    if live_api_revalidated:
        return OpenClassification(
            open_class="live_verified_open",
            reasons=tuple(reasons + ["live_api_revalidated"]),
            **base,
        )

    if provenance is None or provenance.provenance_status in {
        "missing",
        "insufficient",
    }:
        reasons.append(
            f"provenance_status={provenance.provenance_status if provenance else 'none'}"
        )
        return OpenClassification(
            open_class="artifact_declared_open_unverified_provenance",
            reasons=tuple(reasons),
            **base,
        )

    if provenance.effective_timestamp_source == "mtime_unverified_fallback":
        reasons.append("mtime_alone_cannot_qualify_recent_declared_open")
        return OpenClassification(
            open_class="artifact_declared_open_unverified_provenance",
            reasons=tuple(reasons),
            **base,
        )

    if provenance.freshness_status == "stale":
        reasons.append("artifact_freshness_stale")
        return OpenClassification(
            open_class="stale_artifact_declared_open",
            reasons=tuple(reasons),
            **base,
        )
    if provenance.freshness_status != "recent":
        reasons.append(f"freshness_status={provenance.freshness_status}")
        return OpenClassification(
            open_class="artifact_declared_open_unverified_provenance",
            reasons=tuple(reasons),
            **base,
        )

    reasons.append("provenance_valid_and_recent")
    return OpenClassification(
        open_class="recent_artifact_declared_open",
        reasons=tuple(reasons),
        **base,
    )


def is_selectable_declared_open(classification: OpenClassification) -> bool:
    return classification.open_class == "recent_artifact_declared_open"


def _fit_score(row: dict[str, Any]) -> float:
    for key in ("fit_score", "score"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def _tender_key(row: dict[str, Any]) -> str:
    for key in ("codigo_licitacion", "codigo", "canonical_tender_key", "tender_key"):
        val = str(row.get(key) or "").strip()
        if val:
            return val.lower()
    return ""


def _line_id(row: dict[str, Any]) -> str:
    for key in (
        "source_record_id",
        "line_id",
        "unspsc_code",
        "item_description",
        "line_description",
        "title",
    ):
        val = str(row.get(key) or "").strip()
        if val:
            return val.lower()
    return ""


def pick_best_open_row(
    rows: list[dict[str, Any]],
    *,
    as_of: datetime,
    provenance: ArtifactProvenance | None = None,
) -> tuple[dict[str, Any] | None, OpenClassification | None, list[OpenClassification]]:
    """Select the best recent_artifact_declared_open row with stable tie-breakers."""
    classified: list[tuple[dict[str, Any], OpenClassification]] = []
    all_classes: list[OpenClassification] = []
    for row in rows:
        oc = classify_artifact_row_open(
            row, as_of=as_of, provenance=provenance, live_api_revalidated=False
        )
        all_classes.append(oc)
        if is_selectable_declared_open(oc):
            classified.append((row, oc))

    if not classified:
        return None, None, all_classes

    def sort_key(item: tuple[dict[str, Any], OpenClassification]) -> tuple:
        row, oc = item
        cat = str(row.get("equipment_category") or "").strip().lower()
        return (
            0 if oc.open_class == "recent_artifact_declared_open" else 1,
            EQUIPMENT_PRIORITY.get(cat, 99),
            -_fit_score(row),
            _tender_key(row),
            _line_id(row),
        )

    classified.sort(key=sort_key)
    best_row, best_oc = classified[0]
    return best_row, best_oc, all_classes


def summarize_open_classes(classes: list[OpenClassification]) -> dict[str, int]:
    out = {k: 0 for k in OPEN_CLASSIFICATION_VALUES}
    for c in classes:
        out[c.open_class] = out.get(c.open_class, 0) + 1
    return out
