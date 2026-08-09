"""Load the deterministic comparison corpus from local, already-cached data.

Nothing here touches the network, SQLite, Postgres, or Gmail. Tenders come from
the local ChileCompra detail cache; annex evidence comes from an existing #450
evidence bundle directory.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _listado_entry(payload: dict[str, Any]) -> dict[str, Any] | None:
    listado = payload.get("Listado")
    if isinstance(listado, list) and listado and isinstance(listado[0], dict):
        return listado[0]
    return None


def _lifecycle_class(entry: dict[str, Any]) -> str:
    """Coarse lifecycle echo for the aggregator.

    Recognition does not depend on lifecycle; the aggregator only echoes it.
    PR5C owns real lifecycle classification and is intentionally not reproduced
    here.
    """
    code = str(entry.get("CodigoEstado") or "").strip()
    return "active_open" if code == "5" else "closed"


def load_tender(path: Path) -> dict[str, Any] | None:
    """Parse one detail-cache file into a normalized tender row."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = _listado_entry(payload)
    if entry is None:
        return None
    tender_id = str(entry.get("CodigoExterno") or "").strip()
    if not tender_id:
        return None

    comprador = entry.get("Comprador") or {}
    items = (entry.get("Items") or {}).get("Listado") or []
    lines: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        lines.append(
            {
                "ordinal": int(item.get("Correlativo") or index),
                "product": str(item.get("NombreProducto") or ""),
                "description": str(item.get("Descripcion") or ""),
                "category": str(item.get("Categoria") or ""),
                "classification": str(item.get("CodigoCategoria") or ""),
            }
        )

    return {
        "tender_id": tender_id,
        "title": str(entry.get("Nombre") or ""),
        "description": str(entry.get("Descripcion") or ""),
        "buyer_organization": str(comprador.get("NombreOrganismo") or ""),
        "lifecycle_class": _lifecycle_class(entry),
        "lines": lines,
    }


def load_detail_cache(cache_dir: Path) -> tuple[dict[str, Any], ...]:
    """Load every cached tender, in deterministic filename order."""
    if not cache_dir.is_dir():
        return ()
    rows: list[dict[str, Any]] = []
    for path in sorted(cache_dir.glob("*.json")):
        row = load_tender(path)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r["tender_id"])
    return tuple(rows)


class AnexoEvidenceBundleSet:
    """Chunks, attachment rows, and bundle status from a #450 evidence bundle."""

    def __init__(
        self,
        *,
        chunks_by_tender: dict[str, list[dict[str, Any]]],
        attachments_by_tender: dict[str, list[dict[str, Any]]],
        bundle_by_tender: dict[str, dict[str, Any]],
    ) -> None:
        self.chunks_by_tender = chunks_by_tender
        self.attachments_by_tender = attachments_by_tender
        self.bundle_by_tender = bundle_by_tender

    @property
    def tender_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.bundle_by_tender))

    def chunks(self, tender_id: str) -> list[dict[str, Any]]:
        return self.chunks_by_tender.get(tender_id, [])

    def attachments(self, tender_id: str) -> list[dict[str, Any]]:
        return self.attachments_by_tender.get(tender_id, [])

    def bundle(self, tender_id: str) -> dict[str, Any] | None:
        return self.bundle_by_tender.get(tender_id)


def load_anexo_evidence(evidence_dir: Path) -> AnexoEvidenceBundleSet:
    """Load an existing evidence bundle directory produced by the #450 lane.

    Tolerates bundles written before the #450 hardening added structured
    duplicate-extraction provenance; missing `evidence_attachment_id` falls
    back to the attachment's own id.
    """
    chunks_by_tender: dict[str, list[dict[str, Any]]] = {}
    attachments_by_tender: dict[str, list[dict[str, Any]]] = {}
    bundle_by_tender: dict[str, dict[str, Any]] = {}

    if not evidence_dir.is_dir():
        return AnexoEvidenceBundleSet(
            chunks_by_tender={}, attachments_by_tender={}, bundle_by_tender={}
        )

    summary_path = evidence_dir / "summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for entry in summary.get("tender_summaries") or []:
            tender_id = str(entry.get("tender_id") or "")
            if tender_id:
                bundle_by_tender[tender_id] = entry

    manifest_path = evidence_dir / "attachment_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for tender in manifest.get("tenders") or []:
            tender_id = str(tender.get("tender_id") or "")
            rows = [a for a in tender.get("attachments") or [] if isinstance(a, dict)]
            attachments_by_tender[tender_id] = rows

    chunks_path = evidence_dir / "evidence_chunks.jsonl"
    if chunks_path.is_file():
        with chunks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line)
                tender_id = str(chunk.get("tender_id") or "")
                chunks_by_tender.setdefault(tender_id, []).append(chunk)

    return AnexoEvidenceBundleSet(
        chunks_by_tender=chunks_by_tender,
        attachments_by_tender=attachments_by_tender,
        bundle_by_tender=bundle_by_tender,
    )


def select_corpus(
    tenders: Iterable[dict[str, Any]],
    *,
    anexo: AnexoEvidenceBundleSet,
    only_with_anexos: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Choose the tender set to evaluate, deterministically ordered."""
    rows = sorted(tenders, key=lambda r: r["tender_id"])
    if only_with_anexos:
        available = set(anexo.tender_ids)
        rows = [r for r in rows if r["tender_id"] in available]
    return tuple(rows)
