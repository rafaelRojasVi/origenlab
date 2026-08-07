"""Offline ChileCompra detail-cache → PR5B AcquisitionSnapshot adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.chilecompra_api import _as_str, _codigo_externo
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionSnapshot,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.constants import (
    BRIDGE_CONTRACT_VERSION,
    FIXTURE_ORIGIN,
    REJECT_REASON_CODES,
)


@dataclass(frozen=True)
class BridgeSourceRecord:
    """One detail-cache file attempt."""

    source_path: str
    source_basename: str
    source_digest: str
    tender_code: str | None
    status: str  # accepted | rejected
    reason_code: str | None
    snapshot_id: str | None = None
    line_count: int = 0
    completeness_status: str | None = None


@dataclass(frozen=True)
class BridgeAdapterResult:
    accepted: tuple[AcquisitionSnapshot, ...]
    records: tuple[BridgeSourceRecord, ...]
    snapshot_paths: tuple[Path, ...]
    input_paths: dict[str, str]
    coverage: dict[str, Any]


def _file_digest(path: Path) -> str:
    raw = path.read_bytes()
    # Content-addressed: digest of parsed JSON when possible, else length+name.
    try:
        payload = json.loads(raw.decode("utf-8"))
        return canonical_json_digest(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return canonical_json_digest(
            {"basename": path.name, "byte_length": len(raw), "unreadable": True}
        )


def list_detail_cache_files(cache_dir: Path) -> list[Path]:
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"detail cache dir missing: {cache_dir}")
    return sorted(p for p in cache_dir.glob("*.json") if p.is_file())


def _tender_code_from_payload(payload: dict[str, Any]) -> str | None:
    listado = payload.get("Listado")
    if isinstance(listado, list) and listado:
        first = listado[0]
        if isinstance(first, dict):
            return _codigo_externo(first) or None
    if isinstance(listado, dict):
        return _codigo_externo(listado) or None
    return _as_str(payload.get("CodigoExterno")) or None


def adapt_detail_cache_file(
    path: Path,
    *,
    acquired_at_utc: str | None,
    materialized_at_utc: str | None,
) -> tuple[AcquisitionSnapshot | None, BridgeSourceRecord]:
    """Parse one cached Ticket detail envelope into an AcquisitionSnapshot."""
    basename = path.name
    try:
        raw = path.read_bytes()
        source_digest = _file_digest(path)
    except OSError:
        return None, BridgeSourceRecord(
            source_path=str(path.resolve()),
            source_basename=basename,
            source_digest="",
            tender_code=None,
            status="rejected",
            reason_code="cache_file_unreadable",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, BridgeSourceRecord(
            source_path=str(path.resolve()),
            source_basename=basename,
            source_digest=source_digest,
            tender_code=None,
            status="rejected",
            reason_code="cache_json_invalid",
        )
    if not isinstance(payload, dict):
        return None, BridgeSourceRecord(
            source_path=str(path.resolve()),
            source_basename=basename,
            source_digest=source_digest,
            tender_code=None,
            status="rejected",
            reason_code="cache_json_invalid",
        )
    if "Listado" not in payload:
        return None, BridgeSourceRecord(
            source_path=str(path.resolve()),
            source_basename=basename,
            source_digest=source_digest,
            tender_code=None,
            status="rejected",
            reason_code="missing_listado",
        )
    tender_code = _tender_code_from_payload(payload)
    if not tender_code:
        return None, BridgeSourceRecord(
            source_path=str(path.resolve()),
            source_basename=basename,
            source_digest=source_digest,
            tender_code=None,
            status="rejected",
            reason_code="missing_codigo_externo",
        )
    try:
        snap = build_acquisition_snapshot(
            source_kind="ticket_detail",
            payload=payload,
            fixture_origin=FIXTURE_ORIGIN,
            acquired_at_utc=acquired_at_utc,
            http_status=200,
            tender_code=tender_code,
            materialized_at_utc=materialized_at_utc,
            extra_diagnostics={
                "bridge_contract_version": BRIDGE_CONTRACT_VERSION,
                "detail_cache_basename": basename,
                "detail_cache_digest": source_digest,
                "acquired_at_provenance": (
                    "equipment_refresh_manifest_generated_at_utc"
                    if acquired_at_utc
                    else "acquired_at_unavailable"
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001 — map to stable reject
        return None, BridgeSourceRecord(
            source_path=str(path.resolve()),
            source_basename=basename,
            source_digest=source_digest,
            tender_code=tender_code,
            status="rejected",
            reason_code="parse_error",
            completeness_status=str(type(exc).__name__),
        )
    if snap.completeness_status not in {"complete", "partial"}:
        return None, BridgeSourceRecord(
            source_path=str(path.resolve()),
            source_basename=basename,
            source_digest=source_digest,
            tender_code=tender_code,
            status="rejected",
            reason_code="snapshot_incomplete",
            snapshot_id=snap.snapshot_id,
            line_count=len(snap.line_observations),
            completeness_status=snap.completeness_status,
        )
    return snap, BridgeSourceRecord(
        source_path=str(path.resolve()),
        source_basename=basename,
        source_digest=source_digest,
        tender_code=tender_code,
        status="accepted",
        reason_code=None,
        snapshot_id=snap.snapshot_id,
        line_count=len(snap.line_observations),
        completeness_status=snap.completeness_status,
    )


def adapt_detail_cache_directory(
    cache_dir: Path,
    *,
    staging_dir: Path,
    acquired_at_utc: str | None,
    materialized_at_utc: str | None,
    manifest_path: Path | None = None,
    refresh_state_path: Path | None = None,
) -> BridgeAdapterResult:
    """Adapt every JSON in the detail cache; write accepted snapshots to staging."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    files = list_detail_cache_files(cache_dir)
    accepted: list[AcquisitionSnapshot] = []
    records: list[BridgeSourceRecord] = []
    snapshot_paths: list[Path] = []
    seen_codes: dict[str, str] = {}

    for path in files:
        snap, rec = adapt_detail_cache_file(
            path,
            acquired_at_utc=acquired_at_utc,
            materialized_at_utc=materialized_at_utc,
        )
        if snap is None:
            records.append(rec)
            continue
        code = rec.tender_code or ""
        if code in seen_codes:
            records.append(
                BridgeSourceRecord(
                    source_path=rec.source_path,
                    source_basename=rec.source_basename,
                    source_digest=rec.source_digest,
                    tender_code=code,
                    status="rejected",
                    reason_code="duplicate_tender_code",
                    snapshot_id=rec.snapshot_id,
                    line_count=rec.line_count,
                    completeness_status=rec.completeness_status,
                )
            )
            continue
        seen_codes[code] = rec.source_basename
        out_path = staging_dir / f"acquisition_snapshot_{code.replace('/', '_')}.json"
        out_path.write_text(
            json.dumps(snap.to_dict(), indent=2, sort_keys=True, ensure_ascii=True)
            + "\n",
            encoding="utf-8",
        )
        accepted.append(snap)
        snapshot_paths.append(out_path)
        records.append(rec)

    accepted_n = sum(1 for r in records if r.status == "accepted")
    rejected_n = sum(1 for r in records if r.status == "rejected")
    if accepted_n + rejected_n != len(records):
        raise RuntimeError("bridge source accounting broken")
    if len(files) != len(records):
        raise RuntimeError("bridge file accounting broken")

    by_reason: dict[str, int] = {}
    for r in records:
        if r.reason_code:
            by_reason[r.reason_code] = by_reason.get(r.reason_code, 0) + 1
    for code in REJECT_REASON_CODES:
        by_reason.setdefault(code, 0)

    coverage = {
        "detail_cache_file_count": len(files),
        "accepted_snapshots": accepted_n,
        "rejected_or_unresolved": rejected_n,
        "line_observations_total": sum(len(s.line_observations) for s in accepted),
        "tender_observations_total": sum(len(s.tender_observations) for s in accepted),
        "reject_reason_counts": dict(sorted(by_reason.items())),
        "coverage_note": (
            "Detail-cache subset only (equipment keyword prefilter ∩ max_details). "
            "Not complete ChileCompra activas coverage."
        ),
    }
    input_paths = {
        "detail_cache_dir": str(cache_dir.resolve()),
        "staging_dir": str(staging_dir.resolve()),
    }
    if manifest_path is not None:
        input_paths["equipment_manifest"] = str(manifest_path.resolve())
    if refresh_state_path is not None:
        input_paths["auto_refresh_state"] = str(refresh_state_path.resolve())

    return BridgeAdapterResult(
        accepted=tuple(accepted),
        records=tuple(records),
        snapshot_paths=tuple(snapshot_paths),
        input_paths=input_paths,
        coverage=coverage,
    )
