#!/usr/bin/env python3
"""CRM promotion dry run for recorded quotation document decisions. Writes nothing to any CRM.

    uv run python scripts/quote_crm_promotion_dryrun.py \
        --db-env-file ~/data/origenlab-v2-local/api.cleanroom.env

Selects the ledger rows a safe-apply run recorded (`--apply-run`, default the 2026-09-24 run of 19
documents), reads the CRM once in a READ ONLY transaction, and writes a new report directory:

    plan.json          per document: proposed opportunity, suggested organization and contact,
                       evidence, conflicts, the ordered commands and whether each is blocked
    plan.csv           one row per document
    plan.md            the same, for reading
    idempotencia.json  determinism, key uniqueness, and a replay after a hypothetical apply
    run.json           input hashes before/after, CRM counts, what was and was not written

It never writes a ledger, the staging, the identity report, emails.sqlite, the CRM or Supabase.
The database connection is opened read-only (`default_transaction_read_only=on` and
`read_only=True`); the DSN is read from the env file and never printed. Every input file is hashed
before and after, and the run fails (exit 3) if one changed. Refuses to overwrite `--out`.
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
from origenlab_api.v2 import quote_crm_promotion as qc
from origenlab_api.v2.quote_document_review import DOCUMENT_LEDGER_FILE

DEFAULT_APPLY_RUN = dr.AUDITS / "quote-safe-apply-run-20260924"
DEFAULT_OUT = dr.AUDITS / "quote-crm-promotion-dryrun-20260924"
DEFAULT_ENV_FILE = Path.home() / "data/origenlab-v2-local/api.cleanroom.env"
PROVISIONAL_SEED = 1243  # owner, 2026-09-24, provisional; the code constant stays 1235
SOURCE_RECORDS_FILE = "evidence_source_records.json"

CSV_FIELDS = [
    "orden", "numero", "correo_canonico", "duplicados", "documento_sha256", "id_planificado",
    "titulo_sugerido", "institucion_impresa", "destinatario_impreso", "rut", "base_coincidencia",
    "candidatos_crm", "accion_organizacion", "persona_impresa", "dominios_destinatario",
    "contact_point_existente", "accion_contacto", "fuente_en_v2", "conflictos_bloqueantes",
    "avisos", "pasos", "pasos_bloqueados", "requiere_aprobacion_manual", "promocionable_hoy", "applied",
]


def read_dsn(env_file: Path) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("ORIGENLAB_V2_DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{env_file} has no ORIGENLAB_V2_DATABASE_URL")


def read_snapshot(dsn: str) -> tuple[qc.CrmSnapshot, dict[str, Any]]:
    import psycopg

    with psycopg.connect(dsn, options="-c default_transaction_read_only=on", autocommit=False) as conn:
        conn.read_only = True
        cur = conn.cursor()
        read_only = cur.execute("show transaction_read_only").fetchone()[0]
        if read_only != "on":
            raise SystemExit("the connection is not read-only; nothing read")
        db = cur.execute("select current_database()").fetchone()[0]
        snap = qc.CrmSnapshot()
        snap.organizations = [
            qc.CrmOrganization(str(i), n, ln, k, c) for i, n, ln, k, c in cur.execute(
                "select id, name, legal_name, kind, confirmation from crm.organization "
                "where merged_into_organization_id is null order by id")
        ]
        snap.contact_points = {
            v: qc.CrmContactPoint(str(i), v, u, oi and str(oi), pi and str(pi))
            for i, v, u, oi, pi in cur.execute(
                "select id, value_norm, usage, organization_id, person_id from crm.contact_point "
                "where kind = 'email' order by id")
        }
        snap.source_records = {k: str(i) for k, i in cur.execute(
            "select dedupe_key, id from evidence.source_record")}
        for key, kind, aid in cur.execute(
            "select s.dedupe_key, a.kind, a.id from evidence.assertion a "
            "join evidence.source_record s on s.id = a.source_record_id order by a.id"):
            snap.assertions.setdefault((key, kind), []).append(str(aid))
        snap.quote_numbers = {n: str(o) for n, o in cur.execute(
            "select quote_number, opportunity_id from crm.quote")}
        snap.receipts = {k: c for k, c in cur.execute(
            "select idempotency_key, command_name from platform.command_receipt")}
        snap.operators = [n for (n,) in cur.execute(
            "select display_name from platform.operator where status = 'active'")]
        conn.rollback()
    return snap, {"database": db, "transaction_read_only": read_only}


def load_inputs(apply_run: Path, doc_ledger: Path, staging: Path, identity: Path):
    result = json.loads((apply_run / "result.json").read_text(encoding="utf-8"))
    wanted = [d["decision_id"] for d in result["documentos"] if d.get("decision_id")]
    rows = [json.loads(line) for line in doc_ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {r["decision_id"]: r for r in rows}
    missing = [w for w in wanted if w not in by_id]
    if missing:
        raise SystemExit(f"{len(missing)} decision ids of the apply run are not in the ledger; nothing written")
    selected = [by_id[w] for w in wanted]
    staged = {r["payload"]["source_email_id"]: r
              for r in json.loads((staging / SOURCE_RECORDS_FILE).read_text(encoding="utf-8"))}
    report = json.loads((identity / dr.IDENTITY_JSON).read_text(encoding="utf-8"))
    docs = {d["sha256"]: d for d in report["documents"]}
    return result, rows, selected, staged, docs


def csv_row(item: qc.PromotionItem) -> dict[str, Any]:
    d = item.as_dict()
    org, contact, opp, ev = d["organizacion_sugerida"], d["contacto_sugerido"], d["oportunidad_propuesta"], d["evidencia"]
    return {
        "orden": item.order, "numero": item.quote_number, "correo_canonico": ev["correo_canonico"],
        "duplicados": " ".join(map(str, ev["correos_duplicados"])), "documento_sha256": item.document_sha256,
        "id_planificado": opp["id_planificado"], "titulo_sugerido": opp["titulo_sugerido"],
        "institucion_impresa": org["institucion_impresa"] or "", "destinatario_impreso": org["destinatario_impreso"] or "",
        "rut": org["rut_impreso"] or "", "base_coincidencia": org["base_coincidencia"],
        "candidatos_crm": " | ".join(f"{c['nombre']} ({c['confirmation']})" for c in org["candidatos_crm"]),
        "accion_organizacion": org["accion_sugerida"], "persona_impresa": contact["persona_impresa"] or "",
        "dominios_destinatario": " ".join(r["dominio"] for r in contact["destinatarios"]),
        "contact_point_existente": " ".join("sí" if r["contact_point_id"] else "no" for r in contact["destinatarios"]),
        "accion_contacto": contact["accion_sugerida"], "fuente_en_v2": "sí" if ev["source_record_en_v2"] else "no",
        "conflictos_bloqueantes": " ".join(c.code for c in item.conflicts if c.blocking),
        "avisos": " ".join(c.code for c in item.conflicts if not c.blocking),
        "pasos": len(item.steps), "pasos_bloqueados": sum(s.status == qc.STEP_BLOCKED for s in item.steps),
        "requiere_aprobacion_manual": "sí", "promocionable_hoy": "sí" if item.promotable_now else "no",
        "applied": "false",
    }


def render_md(items, idem, run) -> str:
    out = [
        "# Dry-run de promoción CRM — 19 decisiones documentales",
        "",
        f"Generado {run['generated_at']}. **Nada aplicado.** Sin escrituras en CRM, Supabase, ledgers, "
        f"emails.sqlite, Gmail ni Drive. CRM leído en transacción de sólo lectura: `{run['crm']['database']}`.",
        "",
        f"- Documentos: {len(items)} · promocionables hoy: {sum(i.promotable_now for i in items)} · "
        f"todos requieren aprobación manual · todos `applied=false`.",
        f"- Idempotencia: resumen determinista {'sí' if idem['determinista'] else 'NO'}; "
        f"claves con comandos distintos: {len(idem['claves_con_comandos_distintos'])}; "
        f"repetición tras aplicar hipotéticamente: {idem['escrituras_en_repeticion_tras_aplicar']} escrituras.",
        "",
        "## Resumen",
        "",
        "| # | número | correo | cliente impreso | organización CRM | contacto | bloqueantes | avisos |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i in items:
        d = i.as_dict()
        org, contact = d["organizacion_sugerida"], d["contacto_sugerido"]
        client = org["institucion_impresa"] or org["destinatario_impreso"] or "—"
        if contact["persona_impresa"]:
            client = f"{contact['persona_impresa']} – {client}"
        cands = ", ".join(c["nombre"] for c in org["candidatos_crm"]) or "—"
        orgcell = f"{org['base_coincidencia']}: {cands}"
        cps = ", ".join(f"{r['dominio']}{' (existe)' if r['contact_point_id'] else ''}" for r in contact["destinatarios"]) or "—"
        out.append(
            f"| {i.order} | {i.quote_number} | {d['evidencia']['correo_canonico']} | {client} | {orgcell} | {cps} | "
            f"{', '.join(c.code for c in i.blocking)} | {', '.join(c.code for c in i.conflicts if not c.blocking)} |"
        )
    out += ["", "## Detalle por documento", ""]
    for i in items:
        d = i.as_dict()
        opp, org, ev = d["oportunidad_propuesta"], d["organizacion_sugerida"], d["evidencia"]
        out += [
            f"### {i.order}. {i.quote_number} — correo {ev['correo_canonico']}",
            "",
            f"- **Oportunidad propuesta:** {opp['titulo_sugerido']} · modo `{opp['modo_ledger']}` · "
            f"id planificado `{opp['id_planificado']}` · lead → quoting",
            f"- **Organización sugerida:** impresa «{org['institucion_impresa'] or '—'}», destinatario «{org['destinatario_impreso'] or '—'}», "
            f"RUT {org['rut_impreso'] or '—'} · {org['base_coincidencia']} · acción: {org['accion_sugerida']}",
            f"- **Contacto sugerido:** {d['contacto_sugerido']['persona_impresa'] or 'sin persona impresa'} · "
            + ", ".join(f"{r['dominio']} ({'contact_point existente, ' + r['usage'] if r['contact_point_id'] else 'sin contact_point'}"
                        f"{', correo gratuito' if r['correo_gratuito'] else ''})" for r in d["contacto_sugerido"]["destinatarios"]),
            f"- **Evidencia:** `{ev['archivo']}` · «{ev['linea_numero']}» · «{ev['linea_cliente']}» · "
            f"sha256 `{i.document_sha256[:16]}…` · Gmail `{ev['gmail_message_id']}` hilo `{ev['gmail_thread_id']}` · "
            f"enviado {ev['enviado']} · duplicados {ev['correos_duplicados'] or '—'} · decision_id `{i.decision_id}`",
            "- **Conflictos:**",
            *[f"  - {'⛔' if c.blocking else '⚠'} `{c.code}` — {c.detail}" for c in i.conflicts],
            f"- **Aprobación manual:** sí — {'; '.join(i.manual_approval)}",
            "- **Pasos:** " + " → ".join(f"{s.command} [{s.status}]" for s in i.steps),
            "",
        ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply-run", type=Path, default=DEFAULT_APPLY_RUN)
    ap.add_argument("--document-ledger", type=Path,
                    default=dr.DEFAULT_LEDGER.with_name(DOCUMENT_LEDGER_FILE))
    ap.add_argument("--staging-dir", type=Path, default=dr.DEFAULT_STAGING)
    ap.add_argument("--identity-dir", type=Path, default=dr.DEFAULT_IDENTITY)
    ap.add_argument("--db-env-file", type=Path, default=DEFAULT_ENV_FILE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    if args.out.exists():
        print(f"refusing to overwrite {args.out}; choose another --out", file=sys.stderr)
        return 2
    inputs = {
        "document_decisions.jsonl": args.document_ledger,
        "decisions.jsonl": dr.DEFAULT_LEDGER,
        "apply_run/result.json": args.apply_run / "result.json",
        SOURCE_RECORDS_FILE: args.staging_dir / SOURCE_RECORDS_FILE,
        dr.IDENTITY_JSON: args.identity_dir / dr.IDENTITY_JSON,
    }
    before = {k: dr.sha256_file(p) for k, p in inputs.items()}

    result, rows, selected, staged, docs = load_inputs(
        args.apply_run, args.document_ledger, args.staging_dir, args.identity_dir)
    applied_before = [r["decision_id"] for r in rows if r.get("applied") is not False]
    snapshot, crm_meta = read_snapshot(read_dsn(args.db_env_file))

    def plan(snap: qc.CrmSnapshot):
        return qc.plan_promotion(selected, rows, docs, staged, snap,
                                 approved_seed_next_serial=qa.APPROVED_SEED_NEXT_SERIAL,
                                 provisional_seed_next_serial=PROVISIONAL_SEED)

    items = plan(snapshot)
    again = plan(snapshot)  # same inputs, same snapshot: must be byte-identical
    replan = plan(qc.simulate_apply(items, snapshot))
    idem = qc.idempotency_report(items, replan)
    idem["determinista"] = qc.plan_digest(items) == qc.plan_digest(again)
    idem["sha256_plan"] = qc.plan_digest(items)
    idem["sha256_plan_repetido"] = qc.plan_digest(again)

    after = {k: dr.sha256_file(p) for k, p in inputs.items()}
    rows_after = [json.loads(line) for line in args.document_ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    idem["entradas_sin_cambios"] = before == after
    idem["applied_false_antes"] = not applied_before
    idem["applied_false_despues"] = all(r.get("applied") is False for r in rows_after)
    idem["filas_ledger_antes_despues"] = [len(rows), len(rows_after)]
    if before != after:
        print("an input changed during the run; nothing written", file=sys.stderr)
        return 3

    run = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "selection": {"apply_run": str(args.apply_run), "decision_ids": len(selected),
                      "apply_run_at": result.get("run_at"), "policy_version": result.get("policy_version")},
        "seed": {"approved_in_code": qa.APPROVED_SEED_NEXT_SERIAL, "provisional": PROVISIONAL_SEED},
        "crm": {**crm_meta, "counts": snapshot.as_counts()},
        "inputs_sha256_before": before, "inputs_sha256_after": after,
        "writes": [str(args.out)],
        "not_written": ["document_decisions.jsonl", "decisions.jsonl", "emails.sqlite", "CRM (crm.*)",
                        "evidence.*", "platform.*", "Supabase", "Gmail", "Drive"],
    }

    args.out.mkdir(parents=True)
    (args.out / "plan.json").write_text(qc.canonical_json(items) + "\n", encoding="utf-8")
    with (args.out / "plan.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(csv_row(i) for i in items)
    (args.out / "idempotencia.json").write_text(json.dumps(idem, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (args.out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (args.out / "plan.md").write_text(render_md(items, idem, run), encoding="utf-8")

    print(f"{len(items)} documentos; promocionables hoy {sum(i.promotable_now for i in items)}; "
          f"determinista={idem['determinista']}; repetición sin escrituras={idem['repeticion_sin_escrituras']}; "
          f"entradas sin cambios={idem['entradas_sin_cambios']}; informe en {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
