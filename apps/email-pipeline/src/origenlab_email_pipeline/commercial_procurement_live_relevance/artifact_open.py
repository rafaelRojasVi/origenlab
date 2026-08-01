"""Deterministic open-tender classification for PR5A artifact rows.

PR5A never makes authenticated ChileCompra calls, so ``live_verified_open``
is unreachable here. Artifact rows may be ``recent_artifact_declared_open``
only after strict status, date, provenance, and freshness checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.equipment_first_licitacion_queue import parse_close_date

AS_OF_TIMEZONE = "America/Santiago"
SANTIAGO = ZoneInfo(AS_OF_TIMEZONE)

# Documented freshness window for "recent" artifact-declared open rows.
ARTIFACT_RECENT_MAX_AGE = timedelta(hours=48)

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


def inspect_artifact_provenance(
    path: Path,
    *,
    as_of: datetime,
    manifest: dict[str, Any] | None = None,
) -> ArtifactProvenance:
    """Collect provenance/freshness metadata for an operator-queue artifact."""
    as_of_s = _ensure_aware_santiago(as_of)
    data = path.read_bytes() if path.is_file() else b""
    digest = hashlib.sha256(data).hexdigest() if data else ""
    st = path.stat() if path.is_file() else None
    mtime = (
        datetime.fromtimestamp(st.st_mtime, tz=ZoneInfo("UTC")) if st else None
    )
    age = (as_of_s.astimezone(ZoneInfo("UTC")) - mtime).total_seconds() if mtime else None

    companion = Path(str(path) + ".manifest.json")
    if not companion.is_file():
        companion = path.with_suffix(path.suffix + ".manifest.json")
    if not companion.is_file():
        # chilecompra pattern: foo.csv + foo.manifest.json
        companion = path.with_name(path.stem + ".manifest.json")

    source_meta: dict[str, Any] = {}
    generated_at: str | None = None
    if companion.is_file():
        try:
            source_meta = json.loads(companion.read_text(encoding="utf-8"))
            for key in (
                "generated_at_utc",
                "published_at_utc",
                "queried_at_utc",
                "api_checked_at_utc",
                "created_at_utc",
            ):
                if source_meta.get(key):
                    generated_at = str(source_meta[key])
                    break
        except (json.JSONDecodeError, OSError):
            source_meta = {"parse_error": True}

    manifest_status = "absent"
    if manifest:
        name = path.name
        canonical = [str(x) for x in (manifest.get("canonical_files") or [])]
        stale = [str(x) for x in (manifest.get("stale_files") or [])]
        # Also accept chilecompra_api publish pointer
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
            # chilecompra_api CSV may feed the published non-api basename
            if published_queue and Path(published_queue).name in canonical:
                manifest_status = "canonical"
            else:
                manifest_status = "unknown"
        else:
            manifest_status = "unknown"

    has_sha = bool(digest)
    has_time = mtime is not None
    has_identity = bool(filename_date_iso(path) or generated_at or source_meta)
    if not path.is_file() or not has_sha or not has_time:
        provenance_status = "missing"
    elif not has_identity and manifest_status in {"absent", "unknown"}:
        provenance_status = "insufficient"
    elif manifest_status == "stale":
        provenance_status = "insufficient"
    else:
        provenance_status = "valid"

    if age is None:
        freshness_status = "unknown"
    elif age <= ARTIFACT_RECENT_MAX_AGE.total_seconds():
        freshness_status = "recent"
    else:
        freshness_status = "stale"

    return ArtifactProvenance(
        path=str(path),
        sha256=digest,
        size_bytes=int(st.st_size) if st else 0,
        mtime_utc=mtime.isoformat().replace("+00:00", "Z") if mtime else "",
        filename_date=filename_date_iso(path),
        generated_at_utc=generated_at,
        source_query_metadata={
            k: source_meta[k]
            for k in (
                "source_output_rows",
                "published_at_utc",
                "generated_at_utc",
                "query",
                "estado",
                "fecha",
                "row_count",
                "source_kind",
                "api_checked_at_utc",
            )
            if k in source_meta
        }
        if source_meta
        else {},
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

    # Conflicts / parse failures first
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

    # Row looks declared-open on status/date. Apply provenance + freshness.
    reasons.append("status_and_close_pass_declared_open_checks")

    if live_api_revalidated:
        # Unreachable in PR5A audits; kept for contract completeness.
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
