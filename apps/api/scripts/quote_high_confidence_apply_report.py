#!/usr/bin/env python3
"""Application report for the high-confidence quotation documents, under the frozen policy.

    uv run python scripts/quote_high_confidence_apply_report.py
    uv run python scripts/quote_high_confidence_apply_report.py --seed-next-serial 1243 \
        --seed-audit ~/data/origenlab-v2-migration/audits/quote-number-seed-audit-20260924 --out ...

Checks that the review policy still hashes to its frozen, owner-accepted version, reruns it over
the same inputs as the dry run, and writes a new report directory:

    high_confidence_apply_candidates.csv   one row per high-confidence document, with its separation
    high_confidence_review.md              the same, for reading, split into safe and suggestion
    human_review_hold.csv                  every case the policy leaves to a human, unchanged
    policy_freeze.json                     the frozen version, its fingerprint, the code and input hashes
    summary.json                           counts

The seed defaults to the owner-approved 1235. Any other seed is a *proposal*: the report says so, and
refuses it (exit 6) when it is reserved, or not above the highest serial the staged documents print
or the `--seed-audit` directory (a read-only audit of every printed and registered number) found.

It writes nothing else. The ledgers, the staging and the identity report are hashed before and
after, and the run fails if one changed. It opens no database, calls no Gmail or Drive API, and
creates no opportunity or quote. See `origenlab_api.v2.quote_apply_candidates`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import quote_review_policy_dryrun as dr

from origenlab_api.v2 import quote_apply_candidates as qa
from origenlab_api.v2 import quote_review_policy as qp
from origenlab_api.v2.quote_document_review import DOCUMENT_LEDGER_FILE

DEFAULT_OUT = dr.AUDITS / "quote-high-confidence-apply-20260924"
DEFAULT_DRYRUN = dr.AUDITS / "quote-review-policy-dryrun-20260924-v2"
_HERE = Path(__file__).resolve()
CODE_FILES = {
    "quote_review_policy.py": _HERE.parents[1] / "src/origenlab_api/v2/quote_review_policy.py",
    "quote_apply_candidates.py": _HERE.parents[1] / "src/origenlab_api/v2/quote_apply_candidates.py",
    "quote_review_policy_dryrun.py": _HERE.with_name("quote_review_policy_dryrun.py"),
    "quote_high_confidence_apply_report.py": _HERE,
}

SEPARATION_ES = {qa.SEPARATION_SAFE: "confirmación segura", qa.SEPARATION_SUGGESTION: "solo sugerencia"}
ACTION_ES = {
    qa.ACTION_CONFIRM: "confirmar cotización (cuando exista un paso de carga)",
    qa.ACTION_NONE_ALREADY_CONFIRMED: "ninguna: ya confirmada por el dueño",
    qa.ACTION_OPERATOR: "revisión del operador",
}
LEDGER_ES = {
    qa.LEDGER_UNDECIDED: "sin decisión",
    qa.LEDGER_CONFIRMED_AGREES: "confirmada, coincide",
    qa.LEDGER_DIFFERS: "decisión distinta",
}
SUGGESTION_ES = {
    qa.S_LABDELIVERY_SERIES: "encabezado Labdelivery: numeración del espacio heredado de Labdelivery y "
                             "reconocimiento P03 que depende de los remitentes configurados en el script",
    qa.S_AT_OR_BEYOND_SEED: "correlativo ≥ {seed}, la semilla {seed_status} de la serie futura: la serie lo "
                            "volvería a asignar y la adopción lo rechaza",
    qa.S_RESERVED_SERIAL: "correlativo reservado",
    qa.S_LEDGER_DIFFERS: "el ledger tiene otra decisión: la del dueño es final (P12)",
}

CANDIDATE_FIELDS = [
    "separacion", "accion_propuesta", "correo_canonico", "correos_duplicados", "hilos", "enviado",
    "documento_sha256", "archivo", "emisor", "numero_impreso", "clave_numero", "fecha_documento",
    "cliente_impreso", "contacto", "organizacion", "rut", "clasificacion_propuesta", "oportunidad_propuesta",
    "regla_aplicada", "motivo_solo_sugerencia", "estado_ledger", "decision_ledger", "oportunidad_planificada",
    "campos_confirmacion_segura", "campos_solo_sugerencia", "notas",
]


def reason_es(reason: str, seed: dict[str, Any]) -> str:
    return SUGGESTION_ES[reason].format(seed=seed["next_serial"], seed_status=seed["status"])


def candidate_row(a: qa.ApplyCandidate, seed: dict[str, Any]) -> dict[str, Any]:
    return {
        "separacion": a.separation, "accion_propuesta": a.action,
        "correo_canonico": a.canonical_email_id,
        "correos_duplicados": " ".join(map(str, a.duplicate_email_ids)),
        "hilos": " ".join(a.thread_ids), "enviado": a.sent_at, "documento_sha256": a.sha256,
        "archivo": " | ".join(a.filenames), "emisor": a.issuer, "numero_impreso": a.printed_number,
        "clave_numero": a.number_key, "fecha_documento": a.document_date or "",
        "cliente_impreso": a.client_line, "contacto": a.contact or "", "organizacion": a.organization or "",
        "rut": a.rut or "", "clasificacion_propuesta": a.classification,
        "oportunidad_propuesta": "nueva oportunidad sólo para este documento (sugerencia al operador)",
        "regla_aplicada": " ".join(a.rules),
        "motivo_solo_sugerencia": "; ".join(reason_es(r, seed) for r in a.suggestion_reasons),
        "estado_ledger": a.ledger_state, "decision_ledger": a.ledger_decision,
        "oportunidad_planificada": a.planned_opportunity_id or "",
        "campos_confirmacion_segura": " | ".join(a.safe_fields),
        "campos_solo_sugerencia": " | ".join(a.suggestion_fields),
        "notas": "; ".join(a.notes),
    }


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def dryrun_high_set(dryrun_dir: Path) -> set[str] | None:
    path = dryrun_dir / "documents.csv"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return {r["sha256"] for r in csv.DictReader(f) if r["nivel_de_confianza"] == "alta"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staging-dir", type=Path, default=dr.DEFAULT_STAGING)
    ap.add_argument("--identity-dir", type=Path, default=dr.DEFAULT_IDENTITY)
    ap.add_argument("--ledger", type=Path, default=dr.DEFAULT_LEDGER)
    ap.add_argument("--document-ledger", type=Path, default=None)
    ap.add_argument("--compare-dryrun", type=Path, default=DEFAULT_DRYRUN,
                    help="a dry-run report whose high-confidence set this run must reproduce")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed-next-serial", type=int, default=qa.APPROVED_SEED_NEXT_SERIAL,
                    help="first serial of the forward series; anything but the approved seed is a proposal")
    ap.add_argument("--seed-audit", type=Path, default=None,
                    help="a quote-number seed audit directory (resumen.json) the proposed seed must clear")
    args = ap.parse_args(argv)

    try:
        fingerprint = qp.assert_policy_frozen()
    except qp.PolicyChanged as exc:
        print(str(exc), file=sys.stderr)
        return 5
    doc_ledger_path = args.document_ledger or args.ledger.with_name(DOCUMENT_LEDGER_FILE)
    if args.out.exists():
        print(f"refusing to overwrite {args.out}; choose another --out", file=sys.stderr)
        return 2

    loaded = dr.load_run(args.staging_dir, args.identity_dir, args.ledger, doc_ledger_path)
    if loaded is None:
        print("an input changed during the run; nothing written", file=sys.stderr)
        return 3
    seed = seed_record(args.seed_next_serial, args.seed_audit, qa.max_printed_serial(loaded.review, loaded.issuers))
    if seed["refused"]:
        print(f"seed {args.seed_next_serial} refused: {seed['refused']}; nothing written", file=sys.stderr)
        return 6
    candidates = qa.build_apply_candidates(loaded.run, loaded.review, loaded.doc_states,
                                           seed_next_serial=args.seed_next_serial)
    held = qa.held_for_human(loaded.run, candidates)

    expected = dryrun_high_set(args.compare_dryrun)
    reproduced = None
    if expected is not None:
        reproduced = expected == {a.sha256 for a in candidates}
        if not reproduced:
            print(f"the high-confidence set differs from {args.compare_dryrun}; nothing written", file=sys.stderr)
            return 4

    args.out.mkdir(parents=True, mode=0o700)
    write_csv(args.out / "high_confidence_apply_candidates.csv", CANDIDATE_FIELDS,
              [candidate_row(a, seed) for a in candidates])
    hold_fields = ["objetivo", "clave", "correos", "clasificacion", "nivel_de_confianza", "reglas",
                   "motivo_de_revision", "estado"]
    write_csv(args.out / "human_review_hold.csv", hold_fields, [{
        "objetivo": h.target, "clave": h.key, "correos": " ".join(map(str, h.email_ids)),
        "clasificacion": h.classification, "nivel_de_confianza": dr.CONFIDENCE_ES[h.confidence],
        "reglas": " ".join(h.rules), "motivo_de_revision": dr.reasons_es(h.reasons),
        "estado": "permanece en revisión humana",
    } for h in held])

    docs = loaded.run.documents.values()
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "writes": "this directory only; ledgers, staging and identity report unchanged",
        "applied": False,
        "documents": len(loaded.run.documents),
        "documents_by_confidence": loaded.run.summary()["documents_by_confidence"],
        "medium_low_documents": sum(1 for v in docs if not v.automatable),
        "medium_low_documents_decided_by_owner": sum(
            1 for v in docs if not v.automatable and v.sha256 in loaded.doc_states
            and loaded.doc_states[v.sha256].latest is not None),
        "high_confidence_candidates": len(candidates),
        "by_separation": _count(a.separation for a in candidates),
        "by_action": _count(a.action for a in candidates),
        "by_suggestion_reason": _count(r for a in candidates for r in a.suggestion_reasons),
        "max_printed_origenlab_serial": qa.max_printed_serial(loaded.review, loaded.issuers),
        "seed": seed,
        "human_review_hold": len(held),
        "human_review_hold_by_target": _count(h.target for h in held),
        "reproduces_dryrun": {"dir": str(args.compare_dryrun), "same_high_set": reproduced},
    }
    freeze = {
        "policy_version": qp.POLICY_VERSION,
        "policy_fingerprint_sha256": fingerprint,
        "frozen_policy_sha256": qp.FROZEN_POLICY_SHA256,
        "rules": [r.id for r in qp.POLICY],
        "own_senders": list(dr.OWN_SENDERS),
        "approved_seed_next_serial": qa.APPROVED_SEED_NEXT_SERIAL,
        "seed_next_serial_used": seed["next_serial"],
        "seed_status": seed["status"],
        "reserved_serials": sorted(qa.RESERVED_SERIALS),
        "code_sha256": {k: dr.sha256_file(p) for k, p in CODE_FILES.items()},
        "inputs_sha256": loaded.inputs_before,
        "inputs_sha256_after": loaded.inputs_after,
    }
    (args.out / "policy_freeze.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "high_confidence_review.md").write_text(
        render_review(candidates, held, summary, freeze, seed), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"written: {args.out}", file=sys.stderr)
    return 0


def seed_record(next_serial: int, audit_dir: Path | None, staged_max: int | None) -> dict[str, Any]:
    """The seed this report runs with, whether the owner approved it, and why a proposal is refused."""
    approved = next_serial == qa.APPROVED_SEED_NEXT_SERIAL
    rec: dict[str, Any] = {"next_serial": next_serial, "status": "aprobada" if approved else "propuesta, sin aprobar",
                           "approved_next_serial": qa.APPROVED_SEED_NEXT_SERIAL, "staged_max_printed": staged_max,
                           "audit_dir": None, "audit_sha256": None, "audit_max_current_series": None, "refused": None}
    if audit_dir is not None:
        resumen = audit_dir / "resumen.json"
        data = json.loads(resumen.read_text(encoding="utf-8"))
        rec.update(audit_dir=str(audit_dir), audit_sha256=dr.sha256_file(resumen),
                   audit_max_current_series=data["max_current_series_serial"])
    if approved:
        return rec
    ceiling = max((m for m in (staged_max, rec["audit_max_current_series"]) if m is not None), default=None)
    if next_serial in qa.RESERVED_SERIALS:
        rec["refused"] = f"{next_serial} is reserved"
    elif ceiling is not None and next_serial <= ceiling:
        rec["refused"] = f"{next_serial} is not above the highest printed serial {ceiling}"
    return rec


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _cell(value: object) -> str:
    return str(value).replace("|", "/")


def render_review(candidates: list[qa.ApplyCandidate], held: list, summary: dict, freeze: dict,
                  seed: dict[str, Any]) -> str:
    safe = [a for a in candidates if a.separation == qa.SEPARATION_SAFE]
    sugg = [a for a in candidates if a.separation == qa.SEPARATION_SUGGESTION]
    out = [
        "# Revisión de aplicación: documentos de confianza alta", "",
        f"Generado {summary['generated_at']}. **Nada aplicado**: ningún ledger, base de datos, oportunidad "
        "ni cotización fue escrito o creado. Gmail, Drive, SQLite, Supabase y el CRM no se tocaron.", "",
        "## Política congelada", "",
        f"- Versión: `{freeze['policy_version']}` (P01–P13, con la enmienda del dueño a P03).",
        f"- Huella de las reglas: `{freeze['policy_fingerprint_sha256']}`. Es la misma de la corrida "
        "`quote-review-policy-dryrun-20260924-v2`; un cambio en cualquier regla hace fallar este informe "
        "y el test que la fija.",
        f"- Remitentes propios para P03: {', '.join(freeze['own_senders'])}.",
        f"- La corrida reproduce el conjunto de confianza alta del dry run v2: "
        f"{'sí' if summary['reproduces_dryrun']['same_high_set'] else 'no comprobado'}.", "",
        "## Semilla de numeración", "",
        f"- Este informe corre con la semilla **{seed['next_serial']}** ({seed['status']}). La aprobada por el "
        f"dueño es {seed['approved_next_serial']}; la semilla no está aplicada en ningún sistema.",
        f"- Correlativo impreso más alto de OrigenLab en los documentos preparados: {seed['staged_max_printed']}."]
    if seed["audit_dir"]:
        out.append(f"- Auditoría de números: `{seed['audit_dir']}` (resumen.json sha256 `{seed['audit_sha256'][:16]}…`), "
                   f"correlativo más alto de la serie actual: {seed['audit_max_current_series']}.")
    out += ["",
        "## Qué significa cada lado", "",
        "- **Confirmación segura**: el documento prueba por sí mismo, bajo P13, que es una cotización de "
        "OrigenLab a un cliente y cuál es su número impreso. Un paso de carga futuro podría registrar "
        "*sólo eso* (clasificación, número, correo canónico) sin volver a preguntar.",
        "- **Solo sugerencia**: lo decide el operador. Para **todos** los documentos, también los seguros, "
        "la oportunidad (nueva o existente) y el vínculo del cliente impreso con una organización o "
        "contacto del CRM son sugerencias: la política nunca los infiere. Además, un documento entero "
        "queda como sugerencia cuando su número no puede adoptarse tal cual (ver motivos).", "",
        "## Resumen", "",
        f"- Documentos de confianza alta: **{summary['high_confidence_candidates']}** — confirmación segura "
        f"{len(safe)}, solo sugerencia {len(sugg)}.",
        f"- Acciones: {summary['by_action']}.",
        f"- Documentos de confianza media/baja: {summary['medium_low_documents']} "
        f"({summary['medium_low_documents_decided_by_owner']} ya decididos por el dueño, que se respetan).",
        f"- Casos en revisión humana, sin cambios: **{summary['human_review_hold']}** "
        f"({summary['human_review_hold_by_target']}). Ver `human_review_hold.csv`.", "",
        f"## Confirmación segura ({len(safe)})", "",
        "| correo | documento | cliente impreso | número impreso | oportunidad propuesta | regla | ledger | acción |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in safe:
        out.append(f"| {a.canonical_email_id} | `{a.sha256[:12]}` | {_cell(a.client_line)} | {a.printed_number} | "
                   f"{_opp(a)} | {' '.join(a.rules)} | {LEDGER_ES[a.ledger_state]} | {ACTION_ES[a.action]} |")
    out += ["", f"## Solo sugerencia para el operador ({len(sugg)})", "",
            "| correo | documento | cliente impreso | número impreso | oportunidad propuesta | regla | motivo |",
            "|---|---|---|---|---|---|---|"]
    for a in sugg:
        out.append(f"| {a.canonical_email_id} | `{a.sha256[:12]}` | {_cell(a.client_line)} | {a.printed_number} | "
                   f"{_opp(a)} | {' '.join(a.rules)} | {'; '.join(reason_es(r, seed) for r in a.suggestion_reasons)} |")
    top = summary["max_printed_origenlab_serial"]
    if top is not None and top >= seed["next_serial"]:
        out += ["", "**Semilla de numeración desactualizada.** La semilla de este informe es "
                f"{seed['next_serial']} ({seed['status']}, aún sin aplicar), pero el correlativo impreso más alto de "
                f"OrigenLab en la serie actual (0NNNN-AA) es {top}; {seed['next_serial']} mismo ya está "
                "impreso en un documento preparado. Antes de adoptar estos números o de "
                "activar la numeración, el dueño debe fijar una semilla mayor."]
    noted = [a for a in candidates if a.notes]
    out += ["", "## Notas por documento", ""]
    for a in noted:
        out.append(f"- {a.canonical_email_id} `{a.sha256[:12]}` {a.printed_number}: {'; '.join(a.notes)}.")
    out += ["", "## Lo que sigue en revisión humana", "",
            f"{summary['human_review_hold']} casos quedan exactamente como estaban: ninguno se promueve, "
            "ninguno se decide aquí. Ninguno comparte documento ni correo con los candidatos (comprobado).", ""]
    return "\n".join(out) + "\n"


def _opp(a: qa.ApplyCandidate) -> str:
    if a.planned_opportunity_id:
        return f"nueva (ya planificada `{a.planned_opportunity_id[:8]}`)"
    return "nueva, sólo este documento (sugerencia)"


if __name__ == "__main__":
    raise SystemExit(main())
