#!/usr/bin/env python3
"""Archive quotation PDFs into their case folders under Cotizaciones/Casos — check by default.

    # 1. Check (default): every read hits the live Drive, every write stays in memory. 0 writes.
    .venv/bin/python scripts/quote_drive_case_archive.py \
        --manifest-dir ~/data/origenlab-v2-migration/audits/quote-drive-case-migration-dryrun-<ts> \
        --expect-manifest-sha <sha of migration_manifest.json> --step legacy \
        --out ~/data/origenlab-v2-migration/audits/quote-drive-case-archive-check-<ts>

    # 2. Execute (only with the owner's explicit approval of that step):
    ... same arguments ... --execute --out …/quote-drive-case-archive-legacy-<ts>

    # Retry after a partial failure: add --journal-from <previous out>/journal.json

Steps: ``legacy`` copies the confirmed PDFs found in Pendientes/Enviadas into their cases;
``gmail`` uploads the confirmed PDFs that are in no Drive folder, from verified local bytes;
``all`` does both. Bytes always come from local files whose SHA-256 equals the manifest's.

Guarantees: Drive principal must be contacto@origenlab.cl; Pendientes/Enviadas are never a write
parent and their recursive fingerprint must be identical before and after; each file is verified
by Drive's sha256Checksum and a fresh download; a case folder / document already present is reused,
never duplicated; items this run creates carry ``origenlab_archive_run`` for an exact rollback
(trash, never delete). Nothing touches the CRM, Gmail, SQLite or the ledgers: the CRM link rows
are written to ``archive_links.jsonl`` (a local ledger) for the CRM import to record later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.append(str(Path.home() / "dev/freelance/origenlab/apps/email-pipeline/.venv/lib/python3.12/site-packages"))

from origenlab_api.v2 import quote_case_archive as qa  # noqa: E402
from origenlab_api.v2.quote_case_workspace import load_inputs  # noqa: E402

AUDITS = Path.home() / "data/origenlab-v2-migration/audits"
CREDS = Path.home() / "secrets/origenlab-drive-credentials.json"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
PRINCIPAL = "contacto@origenlab.cl"
FIELDS = "id,name,mimeType,parents,size,sha256Checksum,trashed,appProperties,webViewLink,modifiedTime,md5Checksum"
COMMON = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class GoogleDrivePort:
    """Drive v3 over an AuthorizedSession. Writes raise unless ``allow_write`` (``--execute``)."""

    def __init__(self, session: Any, *, allow_write: bool) -> None:
        self.s, self.allow_write, self.calls = session, allow_write, []

    def _req(self, method: str, url: str, **kw: Any) -> Any:
        if method != "GET" and not self.allow_write:
            raise SystemExit(f"refused: {method} in check mode")
        self.calls.append(f"{method} {url.split('?')[0].replace(UPLOAD, 'upload').replace(API, '')}")
        r = self.s.request(method, url, timeout=120, **kw)
        return r

    def principal(self) -> str:
        r = self._req("GET", f"{API}/about", params={"fields": "user(emailAddress)"})
        r.raise_for_status()
        return r.json()["user"]["emailAddress"]

    def get(self, file_id: str) -> dict | None:
        r = self._req("GET", f"{API}/files/{file_id}", params={"fields": FIELDS, "supportsAllDrives": "true"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def _list(self, q: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"q": q, "fields": f"nextPageToken,files({FIELDS})", "pageSize": 1000, **COMMON}
            if token:
                params["pageToken"] = token
            r = self._req("GET", f"{API}/files", params=params)
            r.raise_for_status()
            body = r.json()
            out += body.get("files", [])
            token = body.get("nextPageToken")
            if not token:
                return out

    def find_by_property(self, key: str, value: str) -> list[dict]:
        v = value.replace("\\", "\\\\").replace("'", "\\'")
        return self._list(f"appProperties has {{ key='{key}' and value='{v}' }} and trashed = false")

    def children(self, folder_id: str) -> list[dict]:
        return self._list(f"'{folder_id}' in parents and trashed = false")

    def create_folder(self, *, name: str, parent_id: str, app_properties: dict) -> dict:
        r = self._req("POST", f"{API}/files", params={"fields": FIELDS, "supportsAllDrives": "true"},
                      json={"name": name, "mimeType": qa.FOLDER_MIME, "parents": [parent_id], "appProperties": dict(app_properties)})
        r.raise_for_status()
        return r.json()

    def upload_pdf(self, *, name: str, parent_id: str, app_properties: dict, data: bytes) -> dict:
        meta = {"name": name, "parents": [parent_id], "mimeType": qa.PDF_MIME, "appProperties": dict(app_properties)}
        boundary = "origenlab-" + sha(data)[:16]
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                + json.dumps(meta, ensure_ascii=False).encode() + b"\r\n"
                + f"--{boundary}\r\nContent-Type: application/pdf\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode())
        r = self._req("POST", UPLOAD, params={"uploadType": "multipart", "fields": "id", "supportsAllDrives": "true"},
                      data=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        r.raise_for_status()
        return r.json()

    def download(self, file_id: str) -> bytes:
        r = self._req("GET", f"{API}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"})
        r.raise_for_status()
        return r.content


def fingerprint(port: GoogleDrivePort, folder_id: str) -> dict:
    items, frontier = [], [folder_id]
    while frontier:
        for f in port.children(frontier.pop()):
            items.append({k: f.get(k) for k in ("id", "name", "parents", "trashed", "modifiedTime", "md5Checksum",
                                                 "sha256Checksum", "appProperties")})
            if f.get("mimeType") == qa.FOLDER_MIME:
                frontier.append(f["id"])
    items.sort(key=lambda r: r["id"])
    return {"items": len(items), "sha256": sha(json.dumps(items, sort_keys=True, ensure_ascii=False).encode())}


def local_bytes_index(identity_path: Path, inventory_dir: Path) -> dict[str, Path]:
    idn = json.loads(identity_path.read_text(encoding="utf-8"))
    out = {d["sha256"]: AUDITS / d["stored_path"] for d in idn["documents"] if d.get("stored_path")}
    for p in (inventory_dir / "pdf").glob("*.pdf"):
        out.setdefault(p.stem, p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest-dir", type=Path, required=True)
    ap.add_argument("--expect-manifest-sha", required=True)
    ap.add_argument("--step", choices=("legacy", "gmail", "all"), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--execute", action="store_true", help="really write to Drive (owner approval required)")
    ap.add_argument("--journal-from", type=Path, help="journal.json of an interrupted run, to resume safely")
    ap.add_argument("--case", action="append", default=[], help="restrict to these case keys (repeatable)")
    ap.add_argument("--identity", type=Path, default=AUDITS / "quote-document-identity-20260924/quote_document_identity.json")
    args = ap.parse_args()
    if args.out.exists():
        raise SystemExit(f"refused: {args.out} exists")

    mpath = args.manifest_dir / "migration_manifest.json"
    if sha(mpath.read_bytes()) != args.expect_manifest_sha:
        raise SystemExit("refused: migration_manifest.json is not the expected manifest")
    inputs = load_inputs(args.manifest_dir)  # re-hashes every input the dry run read
    manifest, inventory = inputs.manifest, inputs.inventory
    inv_dir = next(Path(p).parent for p in manifest["inputs_sha256"] if p.endswith("inventory.json"))
    names = {c["name"]: c["id"] for c in inventory["cotizaciones_children"]}
    target = qa.ArchiveTarget(PRINCIPAL, names["Casos"], frozenset({names["Pendientes"], names["Enviadas"]}))
    wanted = {"legacy": {"legacy_drive"}, "gmail": {"gmail_bytes"}, "all": {"legacy_drive", "gmail_bytes"}}[args.step]

    local = local_bytes_index(args.identity, inv_dir)
    cases, crm_status = [], {}
    for c in manifest["cases"]:
        docs = [d for d in c["documents"] if d["source"] in wanted]
        if not docs or (args.case and c["case_key"] not in args.case):
            continue
        built = []
        for d in docs:
            p = local.get(d["document_sha256"])
            data = p.read_bytes() if p and p.exists() else b""
            if sha(data) != d["document_sha256"]:
                raise SystemExit(f"refused: no verified local bytes for {d['document_sha256'][:12]}…")
            built.append(qa.ArchiveDocument(d["document_sha256"], d["quote_number"], d["revision"], d["original_filename"],
                                            data, d.get("gmail_message_id"), d.get("legacy_file_id")))
        cases.append(qa.ArchiveCase(c["case_key"], c["opening_quote_number"], c["printed_addressee"], tuple(built)))
        crm_status[c["case_key"]] = qa.CrmStatus(c["crm_status"])

    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(CREDS))
    creds.refresh(Request())  # in memory only
    real = GoogleDrivePort(AuthorizedSession(creds), allow_write=args.execute)
    port: qa.DrivePort = real if args.execute else qa.OverlayDrive(real)
    run_id = f"{args.step}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    journal: dict[str, str] = json.loads(args.journal_from.read_text()) if args.journal_from else {}

    os.umask(0o077)
    args.out.mkdir(parents=True)
    legacy_before = {n: fingerprint(real, names[n]) for n in ("Pendientes", "Enviadas")}
    results, links, refused = [], [], None
    for c in cases:
        try:
            r = qa.archive_case(port, c, target, journal=journal, run_id=run_id if args.execute else None)
        except qa.ArchiveRefused as e:
            refused = {"case_key": c.case_key, "code": e.code, "detail": e.detail,
                       "partial": e.partial and {"folder_id": e.partial.folder_id, "writes": e.partial.writes}}
            break
        finally:
            (args.out / "journal.json").write_text(json.dumps(journal, indent=1) + "\n")
        results.append({"case_key": c.case_key, "folder_id": r.folder_id, "folder_outcome": r.folder_outcome,
                        "writes": r.writes, "files": [f.__dict__ for f in r.files], "all_verified": r.all_verified})
        links += qa.archive_link_rows(c, r, crm_status=crm_status[c.case_key])
    legacy_after = {n: fingerprint(real, names[n]) for n in ("Pendientes", "Enviadas")}

    report = {
        "mode": "execute" if args.execute else "check (overlay: reads live, writes in memory)",
        "step": args.step, "run_id": run_id if args.execute else None,
        "manifest": str(mpath), "manifest_sha256": args.expect_manifest_sha,
        "cases": len(cases), "cases_done": len(results), "refused": refused,
        "folders_created": sum(r["folder_outcome"] == "created" for r in results),
        "folders_reused": sum(r["folder_outcome"] == "reused" for r in results),
        "files_uploaded": sum(f["outcome"] == "uploaded" for r in results for f in r["files"]),
        "files_reused": sum(f["outcome"] == "reused" for r in results for f in r["files"]),
        "all_verified": all(r["all_verified"] for r in results) and refused is None,
        "legacy_before": legacy_before, "legacy_after": legacy_after, "legacy_unchanged": legacy_before == legacy_after,
        "real_write_calls": [c for c in real.calls if not c.startswith("GET")],
        "would_write": getattr(port, "would_write", None), "api_calls": len(real.calls),
        "results": results,
    }
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with (args.out / "archive_links.jsonl").open("w", encoding="utf-8") as fh:
        for row in links:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({k: report[k] for k in ("mode", "step", "cases", "cases_done", "refused", "folders_created",
                                             "folders_reused", "files_uploaded", "files_reused", "all_verified",
                                             "legacy_unchanged", "api_calls")} | {"real_writes": len(report["real_write_calls"])},
                     ensure_ascii=False, indent=1))
    return 0 if report["all_verified"] and report["legacy_unchanged"] and (args.execute or not report["real_write_calls"]) else 1


if __name__ == "__main__":
    sys.exit(main())
