#!/usr/bin/env python3
"""Dry run: reconcile the legacy Drive quotation folders and plan a case-first migration.

    uv run python scripts/quote_drive_case_migration_dryrun.py \
        --inventory ~/data/origenlab-v2-migration/audits/quote-drive-legacy-inventory-<ts> \
        --out ~/data/origenlab-v2-migration/audits/quote-drive-case-migration-dryrun-<ts>

Reads only: the inventory written by the read-only Drive inventory (inventory.json + pdf/),
the document ledger, the identity report, the CRM import plan and emails.sqlite (opened
``mode=ro``). Makes no network call. Writes only into --out, which must not exist.

Outputs: reconciliation.csv / .json (one row per legacy item), migration_manifest.json (cases,
files, collisions, duplicates, folders, rehearsal), rehearsal.json, and a hashes file listing
every input's SHA-256 before and after (proves the run changed none of them).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from origenlab_api.v2 import quote_case_archive as qa
from origenlab_api.v2 import quote_drive_legacy_migration as lm
from origenlab_api.v2.quote_document_identity import extract_fields

AUDITS = Path.home() / "data/origenlab-v2-migration/audits"
DEFAULT_LEDGER = AUDITS / "quote-evidence-review-20260924/document_decisions.jsonl"
DEFAULT_EMAIL_LEDGER = AUDITS / "quote-evidence-review-20260924/decisions.jsonl"
DEFAULT_IDENTITY = AUDITS / "quote-document-identity-20260924/quote_document_identity.json"
DEFAULT_PLAN = AUDITS / "quote-crm-import-dryrun-canonical-tail-20260926T032224Z/intent.json"
DEFAULT_SQLITE = Path.home() / "data/origenlab-email/sqlite/emails.sqlite"


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pdftotext(path: Path) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    done = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=120, check=False)
    return done.stdout.decode("utf-8", errors="replace") if done.returncode == 0 else None


def load_known(ledger: Path, email_ledger: Path, identity: Path, plan: dict, inventory_dir: Path, db: Path,
               shas: set[str]) -> tuple[dict, dict]:
    rows = [json.loads(line) for line in ledger.open(encoding="utf-8")]
    email_dec: dict[str, str] = {}
    for line in email_ledger.open(encoding="utf-8"):
        e = json.loads(line)
        shas_e = e.get("document_sha256") or []
        for one in [shas_e] if isinstance(shas_e, str) else shas_e:
            # Several emails may carry one document: a confirmation outranks a rejection, which outranks leave_pending.
            rank = {"confirm_customer_quotation": 2, "reject_non_quotation": 1}
            if rank.get(e["decision"], 0) >= rank.get(email_dec.get(one, ""), -1):
                email_dec[one] = e["decision"]
    led: dict[str, dict] = {}
    for r in rows:  # later rows supersede earlier ones for the same document
        led[r["document_sha256"]] = r
    idn = {d["sha256"]: d for d in json.loads(identity.read_text(encoding="utf-8"))["documents"]}
    blocked = {b["document_sha256"]: tuple(b["reasons"]) for b in plan["blocked_documents"]}

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sql_hits: dict[str, list[tuple[int, str]]] = {}
    for s in shas:
        sql_hits[s] = conn.execute(
            "select a.email_id, a.filename from attachments a where a.sha256 = ? order by a.email_id", (s,)).fetchall()
    conn.close()

    extracted: dict[str, dict] = {}
    for pdf in sorted((inventory_dir / "pdf").glob("*.pdf")):
        text = pdftotext(pdf) or ""
        f = extract_fields(text)
        extracted[pdf.stem] = {
            "printed_quote_number": f.quote_numbers[0].raw if f.quote_numbers else None,
            "printed_quote_numbers": [q.raw for q in f.quote_numbers],
            "printed_addressee": f.clients[0].addressee.value if f.clients else None,
            "text_chars": len(text.strip()),
            "mentions_quotation": f.mentions_quotation,
        }

    known: dict[str, lm.KnownDocument] = {}
    for s in shas | set(led) | set(blocked) | set(email_dec):
        r, i, x = led.get(s), idn.get(s), extracted.get(s)
        qn = (i or {}).get("quote_number") or {}
        clients = (i or {}).get("clients") or []
        occ = (r or {}).get("occurrences") or []
        email_ids = tuple(sorted({o["email_id"] for o in occ} | {e for e, _ in sql_hits.get(s, [])}))
        if not (r or i or x or s in blocked or email_ids or s in email_dec):
            continue
        known[s] = lm.KnownDocument(
            sha256=s,
            ledger_decision=(r or {}).get("decision"),
            planned_opportunity_id=((r or {}).get("opportunity") or {}).get("planned_opportunity_id"),
            ledger_quote_number=(r or {}).get("quote_number"),
            gmail_message_ids=tuple(dict.fromkeys(o["gmail_message_id"] for o in occ if o.get("gmail_message_id"))),
            email_ids=email_ids,
            original_filename=((r or {}).get("filenames") or [None])[0] or ((i or {}).get("filenames") or [None])[0]
            or (sql_hits.get(s) or [(None, None)])[0][1],
            identity_class=(i or {}).get("document_class") or ("quotation" if x and x["printed_quote_number"] else None),
            printed_quote_number=qn.get("raw") or (x or {}).get("printed_quote_number"),
            printed_addressee=(clients[0]["addressee"]["value"] if clients else None) or (x or {}).get("printed_addressee"),
            blocked_reasons=blocked.get(s, ()),
            email_decision=email_dec.get(s),
            local_bytes=bool(i and i.get("stored_path")) or (inventory_dir / "pdf" / f"{s}.pdf").exists(),
        )
    return known, {"extracted": extracted, "stored_path": {s: d.get("stored_path") for s, d in idn.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--email-ledger", type=Path, default=DEFAULT_EMAIL_LEDGER)
    ap.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    ap.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    ap.add_argument("--expect-ledger-hash", default=None)
    args = ap.parse_args()
    if args.out.exists():
        raise SystemExit(f"refused: {args.out} exists")
    inputs = [args.inventory / "inventory.json", args.ledger, args.email_ledger, args.identity, args.plan]
    before = {str(p): sha_file(p) for p in inputs}
    if args.expect_ledger_hash and before[str(args.ledger)] != args.expect_ledger_hash:
        raise SystemExit("refused: ledger hash differs from --expect-ledger-hash")

    inventory = json.loads((args.inventory / "inventory.json").read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    shas = {f["sha256Checksum"] for t in inventory["trees"].values() for f in t if f.get("sha256Checksum")}
    known, extras = load_known(args.ledger, args.email_ledger, args.identity, plan, args.inventory, args.sqlite, shas)
    cases = lm.plan_cases(plan)
    mplan = lm.build_plan(inventory, known, cases)

    def bytes_for(s: str) -> bytes | None:
        for p in ((AUDITS / extras["stored_path"][s]) if extras["stored_path"].get(s) else None,
                  args.inventory / "pdf" / f"{s}.pdf"):
            if p and p.exists():
                data = p.read_bytes()
                if hashlib.sha256(data).hexdigest() == s:
                    return data
        return None

    run_id = "dryrun-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rehearsal = lm.rehearse(inventory, mplan, bytes_for, run_id=run_id)
    # The legacy copy alone (the step the owner may approve first), rehearsed separately.
    legacy_only_plan = lm.build_plan(inventory, known, cases, include_gmail_only=False)
    rehearsal_legacy_only = lm.rehearse(inventory, legacy_only_plan, bytes_for, run_id=run_id + "-legacy")
    rehearsal["legacy_copy_only"] = {k: rehearsal_legacy_only[k] for k in
                                     ("cases_attempted", "first_run", "rerun", "rollback", "legacy_unchanged",
                                      "documents_without_local_bytes")}

    after = {str(p): sha_file(p) for p in inputs}
    os.umask(0o077)
    args.out.mkdir(parents=True)
    rows = [r.as_dict() for r in mplan.rows]
    cols = list(rows[0])
    with (args.out / "reconciliation.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: ";".join(map(str, v)) if isinstance(v, list) else v for k, v in r.items()})
    manifest: dict[str, Any] = {
        "kind": "drive-case-migration-dry-run", "version": lm.MIGRATION_VERSION, "archive_version": qa.ARCHIVE_VERSION,
        "action": "none — dry run; no Drive, CRM, Gmail or SQLite write; legacy folders untouched",
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs_sha256": before, "inputs_unchanged": before == after,
        "inventory": {"principal": inventory["principal"], "finished_at": inventory["finished_at"],
                      "counts": inventory["counts"], "legacy_fingerprint": inventory["legacy_fingerprint_after"]},
        "summary": mplan.summary,
        "cases": mplan.cases, "gmail_only_documents": mplan.gmail_only,
        "collisions": mplan.collisions, "duplicates": mplan.duplicates,
        "legacy_folders": mplan.folders, "rehearsal": rehearsal,
        "extracted_from_downloads": extras["extracted"],
        "idempotency": {
            "case_folder": f"appProperties.{qa.PROP_CASE_KEY} = case_key (one folder per key; >1 refuses)",
            "file": f"appProperties.{qa.PROP_DOC_SHA} = sha256 (anywhere in Drive; another case refuses)",
            "run_stamp": f"appProperties.{qa.PROP_RUN} = run id on created items only",
            "journal": "case:<key>/doc:<sha> → Drive id, written as each item is created; consulted before search",
        },
        "rollback": ["list items with appProperties.origenlab_archive_run = <run id> (only items that run created)",
                     "trash them (files.update trashed=true) — never delete; Drive keeps trash 30 days",
                     "re-fingerprint Pendientes/Enviadas: must equal the pre-run fingerprint (they were never written)",
                     "CRM: no row to undo — archive links are deferred until the CRM import, and are evidence only"],
    }
    body = json.dumps(manifest, ensure_ascii=False, indent=1) + "\n"
    (args.out / "migration_manifest.json").write_text(body, encoding="utf-8")
    (args.out / "reconciliation.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (args.out / "rehearsal.json").write_text(json.dumps(rehearsal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (args.out / "SHA256SUMS").write_text("".join(f"{sha_file(p)}  {p.name}\n" for p in sorted(args.out.iterdir())
                                                 if p.is_file() and p.name != "SHA256SUMS"), encoding="utf-8")
    print(json.dumps({"summary": mplan.summary, "rehearsal": {k: rehearsal[k] for k in
                      ("cases_attempted", "first_run", "rerun", "rollback", "legacy_unchanged")},
                      "missing_bytes": len(rehearsal["documents_without_local_bytes"]),
                      "inputs_unchanged": before == after}, ensure_ascii=False, indent=1))
    ok = before == after and rehearsal["legacy_unchanged"] and not rehearsal["first_run"]["refused"] \
        and rehearsal["rerun"]["writes"] == 0 and rehearsal["rollback"]["casos_restored"] \
        and not rehearsal["legacy_copy_only"]["first_run"]["refused"] and rehearsal["legacy_copy_only"]["rerun"]["writes"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
