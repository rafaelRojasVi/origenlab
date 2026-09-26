#!/usr/bin/env python3
"""Dry run of the quotation-evidence review policy over the staged Gmail candidates.

    uv run python scripts/quote_review_policy_dryrun.py

Reads the quote-evidence staging, the document identity report (and its extracted texts,
for the issuer header), and the two decision ledgers. Writes a new report directory:

    policy_spec.json          the rules and the precedent each rests on
    emails.csv                one row per staged Gmail candidate (the 140)
    documents.csv             one row per quotation-document candidate
    ledger_diff.csv           every owner decision next to the policy's verdict
    human_intervention.csv    what still needs a human
    report.md                 the same, for reading
    summary.json              counts, input hashes, and the ledgers' hashes before and after

It never writes to the ledgers, the staging or the identity report — their SHA-256 is taken
before and after, and the run fails if one changed. It opens no database, calls no Gmail or
Drive API, and creates no opportunity or quote. See `origenlab_api.v2.quote_review_policy`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_api.v2 import quote_review_policy as qp
from origenlab_api.v2.quote_confirmation_reconciliation import effective_document_states
from origenlab_api.v2.quote_document_review import (
    DOCUMENT_LEDGER_FILE,
    DocumentDecisionLedger,
    DocumentReview,
    build_document_review,
)
from origenlab_api.v2.quote_evidence_review import (
    QUEUE_FILE,
    SOURCE_RECORDS_FILE,
    DecisionLedger,
    ItemState,
    Staging,
    load_staging,
)

AUDITS = Path.home() / "data/origenlab-v2-migration/audits"
DEFAULT_STAGING = AUDITS / "quote-evidence-staging-20260924"
DEFAULT_IDENTITY = AUDITS / "quote-document-identity-20260924"
DEFAULT_LEDGER = AUDITS / "quote-evidence-review-20260924" / "decisions.jsonl"
DEFAULT_OUT = AUDITS / "quote-review-policy-dryrun-20260924"
IDENTITY_JSON = "quote_document_identity.json"
# OrigenLab's own sender addresses: the only senders whose Labdelivery-headed PDFs P03 may
# recognise as OrigenLab's customer quotations. Exact addresses, never a domain.
OWN_SENDERS = ("contacto@origenlab.cl",)
TEXTS_DIR = "texts"

CONFIDENCE_ES = {qp.CONFIDENCE_HIGH: "alta", qp.CONFIDENCE_MEDIUM: "media", qp.CONFIDENCE_LOW: "baja"}
REASON_ES = {
    qp.R_FOREIGN_ISSUER: "el encabezado no es de OrigenLab (documento de proveedor)",
    qp.R_ALTERNATE_ISSUER: "encabezado Labdelivery no reconocido (no cumple P03)",
    qp.R_ISSUER_UNREAD: "no hay texto para leer el emisor",
    qp.R_SUPPLIER_DIRECTION: "correo en dirección proveedor",
    qp.R_NO_CLIENT: "el PDF no trae bloque de cliente",
    qp.R_GENERIC_CLIENT: "cliente genérico: sólo una dirección de correo",
    qp.R_NO_NUMBER: "sin un único número impreso",
    qp.R_FILENAME_MISMATCH: "el número del archivo contradice el impreso",
    qp.R_CLIENT_CONFLICT: "conflicto de cliente",
    qp.R_VERSION_CHANGE: "mismo correlativo en otro documento (versión o reenvío)",
    qp.R_SERIAL_IN_UNREAD_FILE: "el correlativo aparece en un archivo CN ilegible",
    qp.R_SEVERAL_IN_EMAIL: "varias cotizaciones en el correo",
    qp.R_SEVERAL_IN_THREAD: "varias cotizaciones en el hilo",
    qp.R_SAME_CLIENT_OTHER_THREAD: "mismo cliente en otro hilo",
    qp.R_DIRECTION_UNCLEAR: "dirección del correo no clara",
    qp.R_CN_FILE_NOT_QUOTATION: "archivo CN que no se lee como cotización",
}
OUTCOME_ES = {
    qp.AGREE_AUTOMATABLE: "coincide (automatizable)",
    qp.AGREE_NO_QUOTATION: "coincide (sin cotización)",
    qp.POLICY_STRICTER: "la política lo deja a revisión humana",
    qp.DISAGREE_NUMBER: "DIFERENCIA: número",
    qp.DISAGREE_OPPORTUNITY: "DIFERENCIA: oportunidad",
    qp.DISAGREE_STATUS: "DIFERENCIA: estado",
}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class LoadedRun:
    staging: Staging
    review: DocumentReview
    run: qp.DryRun
    issuers: dict[str, str]
    email_states: dict[int, ItemState]
    doc_states: dict
    inputs_before: dict[str, str | None]
    inputs_after: dict[str, str | None]


def load_run(staging_dir: Path, identity_dir: Path, ledger: Path, doc_ledger_path: Path) -> LoadedRun | None:
    """The inputs read and the policy run over them; None when an input changed meanwhile."""
    inputs = {
        "decisions.jsonl": ledger,
        "document_decisions.jsonl": doc_ledger_path,
        QUEUE_FILE: staging_dir / QUEUE_FILE,
        SOURCE_RECORDS_FILE: staging_dir / SOURCE_RECORDS_FILE,
        IDENTITY_JSON: identity_dir / IDENTITY_JSON,
    }
    before = {k: sha256_file(p) for k, p in inputs.items()}

    staging = load_staging(staging_dir)
    with (identity_dir / IDENTITY_JSON).open(encoding="utf-8") as f:
        report = json.load(f)
    review = build_document_review(staging, report)
    issuers: dict[str, str] = {}
    for c in review.candidates:
        text_path = identity_dir / TEXTS_DIR / f"{c.sha256}.txt"
        issuers[c.sha256] = qp.document_issuer(
            text_path.read_text(encoding="utf-8") if text_path.is_file() else None
        )
    email_states: dict[int, ItemState] = DecisionLedger(ledger).states()
    doc_states = effective_document_states(review, DocumentDecisionLedger(doc_ledger_path), email_states)

    run = qp.dry_run(staging, review, issuers, doc_states, email_states, own_senders=OWN_SENDERS)

    after = {k: sha256_file(p) for k, p in inputs.items()}
    if before != after:
        return None
    return LoadedRun(staging, review, run, issuers, email_states, doc_states, before, after)


def reasons_es(reasons: tuple[str, ...]) -> str:
    return "; ".join(REASON_ES.get(r, r) for r in reasons)


def opportunity_es(value: str) -> str:
    return {
        "create_new": "nueva oportunidad (sólo este documento)",
        qp.OPPORTUNITY_HUMAN: "decisión humana",
        qp.OPPORTUNITY_NONE: "ninguna",
    }.get(value, value)


def existing_document_decision(states: dict, sha: str) -> str:
    st = states.get(sha)
    if st is None or st.latest is None:
        return ""
    e = st.latest
    opp = e.get("opportunity") or {}
    via = " (reconciliada desde correo)" if getattr(st, "source", None) == "email_reconciliation" else ""
    return f"{e['decision']} {e.get('quote_number') or ''} {opp.get('mode') or ''}{via}".strip()


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    ap.add_argument("--identity-dir", type=Path, default=DEFAULT_IDENTITY)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--document-ledger", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    doc_ledger_path = args.document_ledger or args.ledger.with_name(DOCUMENT_LEDGER_FILE)
    if args.out.exists():
        print(f"refusing to overwrite {args.out}; choose another --out", file=sys.stderr)
        return 2

    loaded = load_run(args.staging_dir, args.identity_dir, args.ledger, doc_ledger_path)
    if loaded is None:
        print("an input changed during the run; nothing written", file=sys.stderr)
        return 3
    staging, run, issuers, email_states, doc_states, before, after = (
        loaded.staging, loaded.run, loaded.issuers, loaded.email_states, loaded.doc_states,
        loaded.inputs_before, loaded.inputs_after,
    )

    items = {it.email_id: it for it in staging.items}
    args.out.mkdir(parents=True, mode=0o700)

    (args.out / "policy_spec.json").write_text(json.dumps(
        [{"id": r.id, "title": r.title, "outcome": r.outcome, "precedent": r.precedent} for r in qp.POLICY],
        ensure_ascii=False, indent=1,
    ), encoding="utf-8")

    email_fields = [
        "email_id", "cola", "direccion", "asunto", "documentos_cotizacion", "clasificacion_propuesta",
        "oportunidad_propuesta", "nivel_de_confianza", "automatizable", "numero_propuesto",
        "regla_aplicada", "motivo_de_revision", "decision_correo_existente", "decision_documento_existente",
    ]
    email_rows = []
    for e in run.emails:
        st = email_states.get(e.email_id)
        email_rows.append({
            "email_id": e.email_id, "cola": e.queue, "direccion": e.direction_hint,
            "asunto": items[e.email_id].subject,
            "documentos_cotizacion": " ".join(s[:12] for s in e.documents),
            "clasificacion_propuesta": e.classification,
            "oportunidad_propuesta": opportunity_es(e.proposed_opportunity),
            "nivel_de_confianza": CONFIDENCE_ES[e.confidence],
            "automatizable": "sí" if e.automatable else "no",
            "numero_propuesto": " ".join(e.proposed_quote_numbers),
            "regla_aplicada": " ".join(e.rules),
            "motivo_de_revision": reasons_es(e.reasons),
            "decision_correo_existente": st.latest["decision"] if st and st.latest else "",
            "decision_documento_existente": " | ".join(
                x for x in (existing_document_decision(doc_states, s) for s in e.documents) if x
            ),
        })
    write_csv(args.out / "emails.csv", email_fields, email_rows)

    doc_fields = [
        "sha256", "correo_canonico", "correos", "hilos", "emisor", "numeros_impresos", "cliente",
        "clasificacion_propuesta", "oportunidad_propuesta", "nivel_de_confianza", "automatizable",
        "numero_propuesto", "regla_aplicada", "motivo_de_revision", "evidencia",
        "documentos_relacionados", "decision_existente",
    ]
    doc_rows = []
    for v in sorted(run.documents.values(), key=lambda v: (v.canonical_email_id, v.sha256)):
        doc_rows.append({
            "sha256": v.sha256, "correo_canonico": v.canonical_email_id,
            "correos": " ".join(map(str, v.email_ids)), "hilos": " ".join(v.thread_ids),
            "emisor": v.issuer, "numeros_impresos": " ".join(v.printed_quote_numbers),
            "cliente": " | ".join(v.client_lines), "clasificacion_propuesta": v.classification,
            "oportunidad_propuesta": opportunity_es(v.proposed_opportunity),
            "nivel_de_confianza": CONFIDENCE_ES[v.confidence],
            "automatizable": "sí" if v.automatable else "no",
            "numero_propuesto": v.proposed_quote_number or "",
            "regla_aplicada": " ".join(v.rules), "motivo_de_revision": reasons_es(v.reasons),
            "evidencia": " | ".join(v.evidence),
            "documentos_relacionados": " ".join(s[:12] for s in v.related_documents),
            "decision_existente": existing_document_decision(doc_states, v.sha256),
        })
    write_csv(args.out / "documents.csv", doc_fields, doc_rows)

    diff_fields = [
        "objetivo", "clave", "decision_humana", "numero_humano", "modo_oportunidad_humano", "fuente",
        "clasificacion_politica", "confianza_politica", "numero_politica", "reglas", "motivos",
        "resultado", "nota",
    ]
    write_csv(args.out / "ledger_diff.csv", diff_fields, [{
        "objetivo": c.target, "clave": c.key, "decision_humana": c.human_decision,
        "numero_humano": c.human_quote_number or "", "modo_oportunidad_humano": c.human_opportunity_mode or "",
        "fuente": c.human_source, "clasificacion_politica": c.policy_classification,
        "confianza_politica": CONFIDENCE_ES.get(c.policy_confidence, c.policy_confidence),
        "numero_politica": c.policy_quote_number or "", "reglas": " ".join(c.policy_rules),
        "motivos": reasons_es(c.policy_reasons), "resultado": OUTCOME_ES[c.outcome], "nota": c.note,
    } for c in run.comparison])

    human_fields = ["objetivo", "clave", "correos", "clasificacion", "nivel_de_confianza", "reglas",
                    "motivo_de_revision", "evidencia"]
    write_csv(args.out / "human_intervention.csv", human_fields, [{
        "objetivo": h.target, "clave": h.key, "correos": " ".join(map(str, h.email_ids)),
        "clasificacion": h.classification, "nivel_de_confianza": CONFIDENCE_ES[h.confidence],
        "reglas": " ".join(h.rules), "motivo_de_revision": reasons_es(h.reasons),
        "evidencia": " | ".join(h.evidence),
    } for h in run.human])

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "writes": "this directory only; ledgers, staging and identity report unchanged",
        "inputs_sha256": before,
        "inputs_sha256_after": after,
        "issuers": dict(sorted({i: sum(1 for x in issuers.values() if x == i) for i in set(issuers.values())}.items())),
        **run.summary(),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "report.md").write_text(render_report(run, summary, doc_states, items), encoding="utf-8")
    print(json.dumps(run.summary(), ensure_ascii=False, indent=1))
    print(f"written: {args.out}", file=sys.stderr)
    return 0


def render_report(run: qp.DryRun, summary: dict[str, Any], doc_states: dict, items: dict) -> str:
    out = ["# Dry run de la política de revisión de cotizaciones", "",
           f"Generado {summary['generated_at']}. Sólo lectura: ninguna oportunidad, cotización ni fila creada.", "",
           "## Reglas", ""]
    for r in qp.POLICY:
        out += [f"- **{r.id} — {r.title}.** {r.outcome} _Precedente:_ {r.precedent}"]
    s = run.summary()
    out += ["", "## Resumen", "",
            f"- Documentos: {s['documents']} — por confianza {s['documents_by_confidence']}",
            f"- Correos: {s['emails']} — por confianza {s['emails_by_confidence']}",
            f"- Comparación con los ledgers: {s['ledger_comparison']}",
            f"- Requieren intervención humana (sin decisión): {s['human_intervention']}", "",
            "## Diferencias con decisions.jsonl y document_decisions.jsonl", "",
            "| objetivo | clave | decisión humana | nº humano | modo | política | confianza | nº política | resultado |",
            "|---|---|---|---|---|---|---|---|---|"]
    for c in run.comparison:
        out.append(f"| {c.target} | {c.key[:12]} | {c.human_decision} | {c.human_quote_number or ''} | "
                   f"{c.human_opportunity_mode or ''} | {c.policy_classification} | "
                   f"{CONFIDENCE_ES.get(c.policy_confidence, c.policy_confidence)} | {c.policy_quote_number or ''} | "
                   f"{OUTCOME_ES[c.outcome]} |")
    out += ["", "## Propuestos para automatización (confianza alta)", "",
            "| correo | documento | número | cliente |", "|---|---|---|---|"]
    for v in sorted(run.documents.values(), key=lambda v: v.canonical_email_id):
        if v.automatable:
            decided = existing_document_decision(doc_states, v.sha256)
            out.append(f"| {v.canonical_email_id} | {v.sha256[:12]} | {v.proposed_quote_number} | "
                       f"{v.client_lines[0] if v.client_lines else ''}{' — ya decidido: ' + decided if decided else ''} |")
    out += ["", "## Requieren intervención humana", "",
            "| objetivo | clave | correos | clasificación | confianza | motivo |", "|---|---|---|---|---|---|"]
    for h in run.human:
        out.append(f"| {h.target} | {h.key[:12]} | {' '.join(map(str, h.email_ids))} | {h.classification} | "
                   f"{CONFIDENCE_ES[h.confidence]} | {reasons_es(h.reasons)} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
