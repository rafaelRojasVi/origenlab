#!/usr/bin/env python3
"""Organization confirmation for the quotation CRM import: issue the request sheet, load the decisions.

Two subcommands, both read-only against every database and every ledger; each writes only a new
--out directory and refuses to overwrite one.

Issue the requests from a dry run of `quote_crm_import.py` (run without --confirmations):

    uv run python scripts/quote_organization_confirmation.py requests \
        --dry-run-dir ~/data/…/quote-crm-import-dryrun-<ts> \
        --target-dsn <origenlab_api DSN of the CRM the import targets> \
        --out ~/data/…/quote-organization-confirmation-<ts>

  writes organization_confirmation_requests.csv (one row per opportunity, decision columns
  blank), requests.json + requests.sha256, review_report.md and review_report.json.

Load the operator's filled sheet:

    uv run python scripts/quote_organization_confirmation.py load \
        --requests-dir <the requests --out> --sheet <filled CSV> \
        --target-dsn <same DSN> --out <new directory>

  writes organization_confirmations.jsonl (only when the whole sheet is accepted),
  organization_pending.jsonl, load_problems.csv and load_report.json. The confirmations file is
  what `quote_crm_import.py --confirmations` takes. Nothing here writes the CRM.

Exit codes: 1 sheet refused · 2 bad arguments · 3 an input changed since the dry run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from origenlab_api.v2 import quote_crm_import_plan as qp
from origenlab_api.v2 import quote_organization_confirmation as oc

AUDITS = Path.home() / "data/origenlab-v2-migration/audits"
DEFAULT_LEDGER = AUDITS / "quote-evidence-review-20260924/document_decisions.jsonl"
DEFAULT_EMAIL_LEDGER = AUDITS / "quote-evidence-review-20260924/decisions.jsonl"
DEFAULT_IDENTITY = AUDITS / "quote-document-identity-20260924/quote_document_identity.json"
DEFAULT_STAGING = AUDITS / "quote-evidence-staging-20260924/evidence_source_records.json"
DEFAULT_BLOCKING = AUDITS / "quote-phase3-worksheet-dryrun-20260925-frozen/blocking_report.csv"

CATEGORY_TITLES = {
    oc.C_EXACT: "Coincidencia exacta en el CRM",
    oc.C_CREATE: "La organización debe crearse",
    oc.C_PERSON: "Identidad sólo de persona",
    oc.C_DUPLICATE: "Candidatos a organización duplicada",
    oc.C_BLOCKED: "Oportunidades bloqueadas por documentos",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dbname(dsn: str) -> str:
    return dsn.rsplit("/", 1)[-1].split("?", 1)[0]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def read_crm(dsn: str) -> oc.CrmReading:
    import psycopg

    reading = oc.CrmReading(database=dbname(dsn))
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        conn.read_only = True
        cur = conn.cursor()
        if cur.execute("show transaction_read_only").fetchone()[0] != "on":
            raise SystemExit("the CRM connection is not read-only; nothing read")
        reading.organizations = [
            oc.CrmOrganization(str(i), n, ln, c, int(v)) for i, n, ln, c, v in cur.execute(
                "select id, name, legal_name, confirmation, version from crm.organization "
                "where merged_into_organization_id is null order by id")]
        for value, org in cur.execute("select value_norm, organization_id from crm.external_identifier "
                                      "where scheme = 'rut' and organization_id is not null"):
            key = oc.rut_key(value)
            if key:
                reading.rut_owners.setdefault(key, []).append(str(org))
        reading.operators = {str(i): {"email": e, "role": r, "status": s, "display_name": d} for i, e, r, s, d in cur.execute(
            "select id, email_norm, role, status, display_name from platform.operator")}
        conn.rollback()
    return reading


# ─── requests ──────────────────────────────────────────────────────────────────────────────


def cmd_requests(args: argparse.Namespace) -> int:
    intent_path = args.dry_run_dir / "intent.json"
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    recorded = (args.dry_run_dir / "intent.sha256").read_text().strip()
    plan_sha = qp.plan_sha256(intent)
    if plan_sha != recorded:
        print(f"refused: intent.json hashes to {plan_sha}, intent.sha256 says {recorded}", file=sys.stderr)
        return 3
    if "organization_confirmations.jsonl" in intent["inputs_sha256"]:
        print("refused: requests are issued from a dry run without --confirmations", file=sys.stderr)
        return 2
    paths = {"document_decisions.jsonl": args.ledger, "decisions.jsonl": args.email_ledger,
             "quote_document_identity.json": args.identity, "evidence_source_records.json": args.staging,
             "blocking_report.csv": args.blocking}
    now_hashes = {k: sha256_file(p) for k, p in paths.items()}
    if now_hashes != intent["inputs_sha256"]:
        changed = sorted(k for k in paths if now_hashes[k] != intent["inputs_sha256"].get(k))
        print(f"refused: inputs changed since the dry run: {', '.join(changed)}", file=sys.stderr)
        return 3
    if args.out.exists():
        print(f"refused: {args.out} exists", file=sys.stderr)
        return 2

    ledger_rows = [json.loads(x) for x in args.ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    identity = json.loads(args.identity.read_text(encoding="utf-8"))
    identity_docs = {d["sha256"]: d for d in identity["documents"]}
    staged = json.loads(args.staging.read_text(encoding="utf-8"))
    staged_by_email = {int(r["payload"]["source_email_id"]): r for r in staged}
    pdf_paths = {}
    for sha, d in identity_docs.items():
        p = AUDITS / (d.get("stored_path") or "")
        if d.get("stored_path") and p.is_file():
            pdf_paths[sha] = str(p)
    crm = read_crm(args.target_dsn)

    doc = oc.build_requests(intent, plan_sha, ledger_rows, identity_docs, staged_by_email, crm, pdf_paths)
    doc["generated_at"] = datetime.now(UTC).isoformat()
    doc["dry_run_dir"] = str(args.dry_run_dir)
    doc["inputs_sha256"] = now_hashes

    args.out.mkdir(parents=True)
    write_json(args.out / "requests.json", doc)
    requests_sha = sha256_file(args.out / "requests.json")
    (args.out / "requests.sha256").write_text(requests_sha + "\n")
    rows = oc.request_csv_rows(doc)
    write_csv(args.out / "organization_confirmation_requests.csv", rows,
              list(oc.REQUEST_COLUMNS) + list(oc.DECISION_COLUMNS))
    report = review_report(doc, requests_sha, crm)
    (args.out / "review_report.md").write_text(report, encoding="utf-8")
    write_json(args.out / "review_report.json", {
        "plan_sha256": plan_sha, "requests_sha256": requests_sha, "summary": doc["summary"],
        "by_category": {c: [r["planned_opportunity_id"] for r in doc["requests"] if r["category"] == c]
                        for c in oc.CATEGORIES},
        "operators_registered": [{"operator_id": i, **o} for i, o in sorted(crm.operators.items())],
    })
    print(json.dumps({"plan_sha256": plan_sha, "requests_sha256": requests_sha, "summary": doc["summary"]},
                     ensure_ascii=False, indent=1))
    return 0


def review_report(doc: dict[str, Any], requests_sha: str, crm: oc.CrmReading) -> str:
    s = doc["summary"]
    out = [
        "# Confirmación de instituciones — revisión (sólo lectura)", "",
        f"- Plan: `{doc['plan_sha256']}` ({doc['plan_version']}), dry run `{doc['dry_run_dir']}`",
        f"- Hoja de solicitudes: `requests.json` sha256 `{requests_sha}`",
        f"- CRM leído en READ ONLY: `{crm.database}`, {len(crm.organizations)} organizaciones vivas, "
        f"{sum(len(v) for v in crm.rut_owners.values())} RUT registrados",
        "- Nada se decidió: las columnas de decisión están vacías. Una coincidencia de nombre es una sugerencia.", "",
        "| Grupo | Oportunidades | Cotizaciones | Revisiones | Documentos | Correos de evidencia |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for c in oc.CATEGORIES:
        x = s[c]
        out.append(f"| {CATEGORY_TITLES[c]} (`{c}`) | {x['opportunities']} | {x['quotes']} | {x['revisions']} "
                   f"| {x['documents']} | {x['evidence_records']} |")
    out.append(f"| **Total** | {s['total_opportunities']} | | | | |")
    out += ["", "En las bloqueadas, las cifras son lo que moverían una vez resuelto el bloqueo; el plan actual no les prepara comandos ni evidencia.", ""]
    for c in oc.CATEGORIES:
        rs = [r for r in doc["requests"] if r["category"] == c]
        out += [f"## {CATEGORY_TITLES[c]} — {len(rs)}", ""]
        if not rs:
            out += ["(ninguna)", ""]
            continue
        out += ["| Oportunidad | Institución impresa | RUT | Cotizaciones | Acción recomendada | Detalle |",
                "|---|---|---|---|---|---|"]
        for r in rs:
            who = r["printed_organization"] or f"— ({r['printed_addressee']})"
            detail = r["recommendation_detail"]
            if r["exact_crm_matches"]:
                detail += f" Exactas: {r['exact_crm_matches']}."
            if r["other_crm_candidates"]:
                detail += f" Candidatos: {r['other_crm_candidates']}."
            out.append(f"| `{r['planned_opportunity_id'][:8]}` | {_cell(who)} | {r['printed_ruts'] or '—'} "
                       f"| {r['quote_numbers']} | `{r['recommended_action']}` | {_cell(detail)} |")
        out.append("")
    out += [
        "## Cómo decidir", "",
        "Completar en `organization_confirmation_requests.csv` sólo las columnas de decisión; no editar las demás "
        "(el cargador rechaza la hoja entera si una cambia). Una fila en blanco queda pendiente.", "",
        "| Columna | Valor |", "|---|---|",
        "| `decision` | `confirm_existing` · `create_from_evidence` · `leave_pending` |",
        "| `organization_id`, `organization_version` | sólo con `confirm_existing`: UUID y versión vigentes |",
        "| `organization_kind` | sólo con `create_from_evidence`, obligatorio: `institution` o `unknown` |",
        "| `name_display` | opcional con `create_from_evidence` (p. ej. sin «Razón social:») |",
        "| `decision_note` | obligatorio: una frase |",
        "| `decided_by_email` | correo en minúsculas del operador en `platform.operator` |",
        "| `decided_by_operator_id` | UUID de `platform.operator.id` |",
        "| `decided_by_role` | `admin` o `sales` (el rol registrado; `viewer` no escribe) |",
        "| `decided_at` | ISO-8601 con zona, p. ej. `2026-09-26T10:00:00-03:00` |", "",
        "Operadores registrados hoy en " + f"`{crm.database}`:",
    ]
    for i, o in sorted(crm.operators.items()):
        out.append(f"- `{i}` {o['email']} — {o['display_name']}, {o['role']}, {o['status']}")
    if not crm.operators:
        out.append("- (ninguno)")
    return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


# ─── load ──────────────────────────────────────────────────────────────────────────────────


def cmd_load(args: argparse.Namespace) -> int:
    requests_path = args.requests_dir / "requests.json"
    recorded = (args.requests_dir / "requests.sha256").read_text().strip()
    requests_sha = sha256_file(requests_path)
    if requests_sha != recorded:
        print(f"refused: requests.json is {requests_sha}, requests.sha256 says {recorded}", file=sys.stderr)
        return 3
    if args.out.exists():
        print(f"refused: {args.out} exists", file=sys.stderr)
        return 2
    doc = json.loads(requests_path.read_text(encoding="utf-8"))
    crm = read_crm(args.target_dsn)
    if crm.database != doc["crm_database"]:
        print(f"refused: the requests were issued against {doc['crm_database']}, not {crm.database}", file=sys.stderr)
        return 2
    sheet_sha = sha256_file(args.sheet)
    with args.sheet.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    expected = list(oc.REQUEST_COLUMNS) + list(oc.DECISION_COLUMNS)
    if header != expected:
        print(f"refused: the sheet header is not the issued one ({len(header)} vs {len(expected)} columns)",
              file=sys.stderr)
        return 1
    result = oc.load_decisions(rows, doc, requests_sha, crm, now=datetime.now(UTC))
    missing = sorted({r["planned_opportunity_id"] for r in doc["requests"]}
                     - {(row.get("planned_opportunity_id") or "").strip() for row in rows})

    args.out.mkdir(parents=True)
    write_csv(args.out / "load_problems.csv", result.problems, ["line", "planned_opportunity_id", "code", "detail"])
    if result.accepted:
        with (args.out / "organization_confirmations.jsonl").open("w", encoding="utf-8") as f:
            for c in result.confirmations:
                f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
        with (args.out / "organization_pending.jsonl").open("w", encoding="utf-8") as f:
            for c in result.pending:
                f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    report = {
        "loader_version": oc.LOADER_VERSION, "generated_at": datetime.now(UTC).isoformat(),
        "requests_sha256": requests_sha, "plan_sha256": doc["plan_sha256"], "sheet": str(args.sheet),
        "sheet_sha256": sheet_sha, "crm_database": crm.database, "accepted": result.accepted,
        "rows": len(rows), "blank_rows": len(result.blank), "missing_rows": missing,
        "confirmations": len(result.confirmations), "leave_pending": len(result.pending),
        "confirmations_by_action": {a: sum(1 for c in result.confirmations if c["action"] == a)
                                    for a in (oc.CONFIRM_EXISTING, oc.CREATE_FROM_EVIDENCE)},
        "problems": len(result.problems), "problems_by_code": _count(p["code"] for p in result.problems),
        "confirmations_sha256": sha256_file(args.out / "organization_confirmations.jsonl") if result.accepted else None,
    }
    write_json(args.out / "load_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if result.accepted else 1


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rq = sub.add_parser("requests")
    rq.add_argument("--dry-run-dir", type=Path, required=True)
    rq.add_argument("--target-dsn", required=True)
    rq.add_argument("--out", type=Path, required=True)
    rq.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    rq.add_argument("--email-ledger", type=Path, default=DEFAULT_EMAIL_LEDGER)
    rq.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    rq.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    rq.add_argument("--blocking", type=Path, default=DEFAULT_BLOCKING)
    ld = sub.add_parser("load")
    ld.add_argument("--requests-dir", type=Path, required=True)
    ld.add_argument("--sheet", type=Path, required=True)
    ld.add_argument("--target-dsn", required=True)
    ld.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    return cmd_requests(args) if args.cmd == "requests" else cmd_load(args)


if __name__ == "__main__":
    sys.exit(main())
