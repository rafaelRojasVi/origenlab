"""Case workspace — one commercial case with all its quotations, revisions, Drive files and Gmail evidence.

A read-only view for the operator, built from files only (no database, no network):

- the CRM import plan (``intent.json``) — the source of each case's *CRM status* until the CRM
  itself holds the case; the view says so (``crm.source = "import_plan"``);
- the read-only Drive inventory (``inventory.json``) — where each document's bytes are;
- the case migration dry run (``migration_manifest.json``) — collisions and the planned folder action;
- optional upload reports — which archived files had their download hash verified.

Two statuses are kept apart on purpose. ``crm`` says where the opportunity stands and comes only
from the plan (later: the CRM). ``archive`` says where the bytes are and comes only from Drive.
A document is never shown as CRM-ready because it is in Drive: :func:`build_case_workspace`
never reads the archive status when it computes the CRM status or the lifecycle label.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_api.v2 import quote_case_archive as qa
from origenlab_api.v2.quote_drive_legacy_migration import LEGACY_ROOTS, number_key

WORKSPACE_VERSION = "qcase-workspace/2026-09-26.1"

REASON_LABELS_ES = {
    "opportunity_has_blocked_document": "Un documento del caso está bloqueado por el owner (colisión de número u otra regla)",
    "document_left_pending": "Documento retenido por el owner",
    "owner_hold_no_organization": "Retenido por el owner: sin institución",
    "needs_organization_confirmation": "Falta confirmar la institución",
    "ledger_number_written_without_leading_zero": "El ledger escribió el número sin el cero inicial (manda el impreso)",
    "version_across_numbers": "Versión del mismo documento con otro número",
    "canonical_undetermined": "No se pudo determinar la versión canónica",
}

GMAIL_URL = "https://mail.google.com/mail/u/0/#all/{}"


class WorkspaceRefused(ValueError):
    """An input changed since the dry run hashed it, or is missing."""


@dataclass(frozen=True)
class WorkspaceInputs:
    manifest: dict[str, Any]
    plan: dict[str, Any]
    inventory: dict[str, Any]
    verified_file_ids: frozenset[str]
    source_dir: str


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_inputs(directory: str | Path, upload_reports: list[str] | None = None) -> WorkspaceInputs:
    """Load a migration dry-run directory and the inputs it hashed. Any changed input refuses."""
    d = Path(directory)
    manifest_path = d / "migration_manifest.json"
    sums = d / "SHA256SUMS"
    if not manifest_path.exists() or not sums.exists():
        raise WorkspaceRefused(f"{d} is not a case migration dry-run directory")
    expected = dict(reversed(line.split("  ", 1)) for line in sums.read_text().splitlines() if line.strip())
    if expected.get("migration_manifest.json") != _sha(manifest_path):
        raise WorkspaceRefused("migration_manifest.json does not match SHA256SUMS")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_name: dict[str, Path] = {}
    for raw, want in manifest["inputs_sha256"].items():
        p = Path(raw)
        if not p.exists() or _sha(p) != want:
            raise WorkspaceRefused(f"input changed since the dry run: {p.name}")
        by_name[p.name] = p
    verified: set[str] = set()
    for report in upload_reports or []:
        rep = json.loads(Path(report).read_text(encoding="utf-8"))
        for u in rep.get("uploads", []):
            if u.get("drive_sha256_matches") and u.get("downloaded_sha256_matches") and u.get("size_matches"):
                verified.add(u["file"]["id"])
    return WorkspaceInputs(
        manifest=manifest,
        plan=json.loads(by_name["intent.json"].read_text(encoding="utf-8")),
        inventory=json.loads(by_name["inventory.json"].read_text(encoding="utf-8")),
        verified_file_ids=frozenset(verified),
        source_dir=str(d),
    )


def _drive_index(inventory: Mapping[str, Any]) -> tuple[dict, dict, dict]:
    """(case folders by key, archived files by sha, legacy files by sha)."""
    folders, archived, legacy = {}, defaultdict(list), defaultdict(list)
    for f in inventory["trees"].get("Casos", []):
        props = f.get("appProperties") or {}
        if props.get(qa.PROP_KIND) == qa.KIND_CASE:
            folders[props.get(qa.PROP_CASE_KEY)] = f
        elif props.get(qa.PROP_KIND) == qa.KIND_REVISION:
            archived[props.get(qa.PROP_DOC_SHA)].append(f)
    for root in LEGACY_ROOTS:
        for f in inventory["trees"].get(root, []):
            if f.get("sha256Checksum") and not f.get("trashed"):
                legacy[f["sha256Checksum"]].append(f)
    return folders, archived, legacy


def _link(f: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": f["id"], "url": f.get("webViewLink"), "path": f.get("path"), "name": f.get("name"),
            "gmail_message_id": (f.get("appProperties") or {}).get(qa.PROP_GMAIL_MESSAGE)}


def _archive(sha: str, case_key: str, archived: Mapping, legacy: Mapping, verified: frozenset[str]) -> dict[str, Any]:
    mine = [f for f in archived.get(sha, []) if (f.get("appProperties") or {}).get(qa.PROP_CASE_KEY) == case_key]
    legacy_links = [_link(f) for f in legacy.get(sha, [])]
    if mine:
        f = mine[0]
        drive_ok = f.get("sha256Checksum") == sha
        status = (qa.ArchiveStatus.HASH_MISMATCH if not drive_ok else
                  qa.ArchiveStatus.ARCHIVED_VERIFIED if f["id"] in verified else qa.ArchiveStatus.ARCHIVED_UNVERIFIED)
        return {"status": status.value, "file": _link(f), "legacy_files": legacy_links}
    return {"status": (qa.ArchiveStatus.LEGACY_ONLY if legacy_links else qa.ArchiveStatus.NOT_ARCHIVED).value,
            "file": None, "legacy_files": legacy_links}


def _reason(code: str) -> dict[str, str]:
    raw = code.split(":")[-1]
    return {"code": code, "label": REASON_LABELS_ES.get(raw, raw)}


def build_case_workspace(inputs: WorkspaceInputs) -> dict[str, Any]:
    plan, inventory, manifest = inputs.plan, inputs.inventory, inputs.manifest
    folders, archived, legacy = _drive_index(inventory)
    collisions = {c["number"]: c for c in manifest.get("collisions", [])}
    planned_folder = {c["case_key"]: c["folder_action"] for c in manifest.get("cases", []) if c.get("documents")}
    pending_docs = defaultdict(list)
    for p in plan.get("pending_documents", []):
        pending_docs[p["planned_opportunity_id"]].append(p)

    cases = []
    for o in plan["opportunities"]:
        key = o["planned_opportunity_id"]
        crm = qa.crm_status_from_plan(o["status"])
        revisions: list[tuple[str, dict, qa.DocumentRole]] = []
        for q in o["quotes"]:
            for r in q["revisions"]:
                revisions.append((q["quote_number"], r, qa.DocumentRole.QUOTATION))
        for p in pending_docs.get(key, []):
            revisions.append((p["quote_number"], {"document_sha256": p["document_sha256"], "sent_at": None,
                                                  "origin_dedupe_key": None, "pending_code": p["code"]},
                              qa.DocumentRole.PENDING_DOCUMENT))
        by_quote: dict[str, list] = defaultdict(list)
        for quote, r, role in revisions:
            by_quote[quote].append((r, role))
        quotes_out, opening = [], None
        for quote, revs in by_quote.items():
            revs.sort(key=lambda x: (x[0].get("sent_at") or "", x[0]["document_sha256"]))
            out_revs = []
            for i, (r, role) in enumerate(revs, start=1):
                archive = _archive(r["document_sha256"], key, archived, legacy, inputs.verified_file_ids)
                gmail = (r.get("origin_dedupe_key") or "").removeprefix("gmail_message:") or None
                if gmail is None and archive["file"]:  # a pending document carries its Gmail id on the archived file
                    gmail = archive["file"].get("gmail_message_id")
                life = qa.document_lifecycle(role, crm, revision=i)
                out_revs.append({
                    "revision_no": i, "document_sha256": r["document_sha256"], "sent_at": r.get("sent_at"),
                    "role": role.value, "pending_code": r.get("pending_code"),
                    "gmail_message_id": gmail, "gmail_url": GMAIL_URL.format(gmail) if gmail else None,
                    "lifecycle": life.value, "lifecycle_label": qa.LIFECYCLE_LABEL_ES[life],
                    "archive": archive,
                })
                if r.get("sent_at") and (opening is None or r["sent_at"] < opening[1]):
                    opening = (quote, r["sent_at"])
            nk = number_key(quote)
            quotes_out.append({"quote_number": quote, "collision": collisions.get(nk), "revisions": out_revs})
        quotes_out.sort(key=lambda q: min((r["sent_at"] or "~") for r in q["revisions"]))
        opening_number = opening[0] if opening else (quotes_out[0]["quote_number"] if quotes_out else None)
        folder = folders.get(key)
        reasons = [_reason(c) for c in o.get("reasons") or []]
        if crm is qa.CrmStatus.PENDING_ORGANIZATION:
            reasons.insert(0, _reason("needs_organization_confirmation"))
        for p in pending_docs.get(key, []):
            reasons.append(_reason(p["code"]))
        conf = o.get("confirmation") or {}
        all_revs = [r for q in quotes_out for r in q["revisions"]]
        cases.append({
            "case_key": key,
            "opening_quote_number": opening_number,
            "printed_addressee": o.get("printed_addressee"),
            "printed_organization": o.get("printed_organization"),
            # The real Drive name once the folder exists (names are never renamed); otherwise the planned one.
            "folder_name": folder["name"] if folder else
            (qa.case_folder_name(opening_number, o.get("printed_addressee")) if opening_number else None),
            "crm": {
                "status": crm.value, "source": "import_plan", "plan_status": o["status"],
                "organization_confirmed": bool(conf.get("action")),
                "organization_action": conf.get("action"),
                "organization_id": conf.get("organization_id"),
                "reasons": reasons,
                "warnings": [_reason(w.get("code", "")) | {"detail": {k: v for k, v in w.items() if k != "code"}}
                             for w in o.get("warnings") or []],
            },
            "archive": {
                "folder": _link(folder) if folder else None,
                "planned_folder_action": "exists" if folder else planned_folder.get(key, "not_planned"),
                "counts": _count(r["archive"]["status"] for r in all_revs),
            },
            "flags": sorted(
                (["missing_organization"] if not conf.get("action") else [])
                + (["number_collision"] if any(q["collision"] for q in quotes_out) else [])
                + (["has_revisions"] if any(len(q["revisions"]) > 1 for q in quotes_out) else [])
                + (["several_quotes"] if len(quotes_out) > 1 else [])
                + (["pending_document"] if pending_docs.get(key) else [])
            ),
            "quotes": quotes_out,
        })
    cases.sort(key=lambda c: (c["opening_quote_number"] or "~", c["case_key"]))

    unplaced = [
        {k: r.get(k) for k in ("path", "classification", "reason", "printed_quote_number", "sha256", "flags",
                               "lifecycle", "action", "drive_id")}
        | {"url": next((f.get("webViewLink") for t in inventory["trees"].values() for f in t if f["id"] == r["drive_id"]), None)}
        for r in _reconciliation_rows(inputs)
        if r["classification"] != "folder" and not r.get("case_key")
    ]
    return {
        "workspace_version": WORKSPACE_VERSION,
        "source": {"dry_run_dir": inputs.source_dir, "inventory_finished_at": inventory.get("finished_at"),
                   "plan_version": plan.get("plan_version"),
                   "crm_status_source": "import_plan — the CRM holds no quotation yet; statuses are the plan's"},
        "counts": {
            "cases": len(cases),
            "by_crm_status": _count(c["crm"]["status"] for c in cases),
            "by_archive_status": _count(r["archive"]["status"] for c in cases for q in c["quotes"] for r in q["revisions"]),
            "cases_with_collisions": sum("number_collision" in c["flags"] for c in cases),
            "cases_missing_organization": sum("missing_organization" in c["flags"] for c in cases),
            "unplaced_legacy_documents": len(unplaced),
        },
        "collisions": list(collisions.values()),
        "cases": cases,
        "unplaced_legacy_documents": unplaced,
    }


def _reconciliation_rows(inputs: WorkspaceInputs) -> list[dict[str, Any]]:
    p = Path(inputs.source_dir) / "reconciliation.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _count(values) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[str(v)] += 1
    return dict(sorted(out.items()))
