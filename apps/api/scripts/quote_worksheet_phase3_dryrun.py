#!/usr/bin/env python3
"""Phase 3 dry run: the owner's worksheet answers (W001–W117) as ledger decisions and a CRM plan.

    uv run python scripts/quote_worksheet_phase3_dryrun.py \
        --db-env-file ~/data/origenlab-v2-local/api.cleanroom.env

Reads the worksheet CSV, the second-pass cases, the staging, the identity report and both decision
ledgers; optionally reads the CRM once in a READ ONLY transaction. Writes a new report directory:

    policy_spec.json        normalisation table, outcome rules, version and fingerprint
    enum_normalization.csv  every recorded answer → its normalised value (and whether it is an extension)
    cases.csv               one row per document or email case: outcome, blockers, planned opportunity
    automatable.csv         cases an apply could record without another human answer
    blocked.csv             cases that need the owner, with the reason
    ledger_intents.jsonl    the exact entries an apply WOULD append (not a ledger; nothing appended)
    crm_plan.json           the CRM promotion plan for the confirmations (quote_crm_promotion)
    idempotency.json        determinism, replay-after-apply, key uniqueness
    enum_table.csv          the normalisation table itself, with how many answers use each form
    run.json                input hashes before/after, what was and was not written
    gates_spec.json         the apply gates G1–G5 (owner decisions of 2026-09-25) and their fingerprint
    blocking_report.csv     every blocked document: blocker, governing rule, whether it holds the ledger / CRM
    crm_holds.csv           opportunities not promoted to the CRM while a member is blocked (G3)
    idempotency_list.csv    every write key: ledger decision ids with the G1 quote identity, CRM qpromo keys
    apply_plan.json         the ordered appends an apply would make, its preconditions and rollback
    diff.json               differences against --compare-with (the previous dry run)

The resolution policy and the gates must both match their frozen fingerprints (exit 4 otherwise).

It never writes a ledger, the worksheet, the staging, emails.sqlite, the CRM, Supabase, Gmail or
Drive. Every input is hashed before and after (exit 3 if one changed). Refuses to overwrite --out.
"""

from __future__ import annotations

import argparse
import copy
import csv
import dataclasses
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import quote_crm_promotion_dryrun as cd
import quote_review_policy_dryrun as dr

from origenlab_api.v2 import quote_apply_candidates as qa
from origenlab_api.v2 import quote_crm_promotion as qc
from origenlab_api.v2 import quote_worksheet_resolution as w
from origenlab_api.v2.quote_document_review import (
    DOCUMENT_LEDGER_FILE,
    DocumentState,
    build_document_decision,
)
from origenlab_api.v2.quote_evidence_review import (
    BASIS_CONFIRMED_PROPOSAL,
    BASIS_OPERATOR_ENTERED,
    DECISION_CONFIRM,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    ReviewRefused,
    build_decision,
    normalize_quote_number,
)

WORKSHEET = dr.AUDITS / "quote-human-review-worksheet-20260925" / "operator_questions.csv"
SECOND_PASS = dr.AUDITS / "quote-second-pass-audit-20260925T154506Z" / "full_second_pass.csv"
DEFAULT_OUT = dr.AUDITS / "quote-phase3-worksheet-dryrun-20260925"
OPERATOR = "Rafael"
# Dry-run entries carry this fixed time so two runs are byte-identical; an apply stamps its own.
DRY_RUN_RECORDED_AT = datetime(2026, 9, 25, tzinfo=UTC)
PROVISIONAL_SEED = cd.PROVISIONAL_SEED
C_OPP_HELD = "oportunidad_con_documento_bloqueado"


def read_cases(path: Path) -> list[w.Case]:
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            same = [part.split()[0] for part in r["same_serial_other_documents"].split("|") if part.strip()]
            out.append(w.Case(
                case_id=r["case_id"], target=r["target"], sha256=r["canonical_sha256"] or None,
                email_id=int(r["canonical_email_id"]) if r["canonical_email_id"] else None,
                sent_at=r["sent_at"], printed_number=r["printed_number_parsed"] or None,
                widened_number=r["printed_number_widened"] or None,
                decisions_needed=tuple(x.strip() for x in r["decisions_needed"].split("|") if x.strip()),
                same_serial_documents=tuple(same),
            ))
    return out


def recorded_from_states(states: dict) -> dict[str, w.Recorded]:
    out = {}
    for sha, st in states.items():
        e = st.latest
        if not e:
            continue
        opp = e.get("opportunity") or {}
        out[sha] = w.Recorded(
            decision=e["decision"], quote_number=e.get("quote_number"),
            opportunity_key=opp.get("opportunity_id") or opp.get("planned_opportunity_id"),
            opportunity_mode=opp.get("mode"), decision_id=e["decision_id"],
        )
    return out


def run_once(rows, cases_raw, index, states, review, staging):
    """Normalise, resolve, validate every intent against the ledger rules. Pure over its inputs."""
    answers = w.normalize_worksheet(rows, index)
    same_serial_resolved = []
    for c in cases_raw:
        refs = []
        for s in c.same_serial_documents:
            try:
                refs.append(index.resolve(s))
            except w.UnknownAnswer:
                pass
        same_serial_resolved.append(w.Case(**{**c.__dict__, "same_serial_documents": tuple(refs)}))
    recorded = recorded_from_states(states)
    by_email: dict[int, list[str]] = {}
    for sha, st in states.items():
        for occ in (st.latest or {}).get("occurrences") or []:
            by_email.setdefault(int(occ["email_id"]), []).append(sha)
    resolution = w.resolve(answers, same_serial_resolved, recorded, by_email)
    modes = {r.opportunity_key: ("existing" if r.opportunity_mode == "existing" else "planned")
             for r in recorded.values() if r.opportunity_key}
    intents = w.ledger_intents(resolution, modes)

    # Validate in apply order with the ledger's own builders, folding each accepted entry in.
    current = {k: copy.deepcopy(v) for k, v in states.items()}
    items = {it.email_id: it for it in staging.items}
    entries, refused = [], {}
    for it in intents:
        try:
            if it.ledger == "decisions.jsonl":
                entry = build_decision(items[int(it.target)], decision="reject_non_quotation", operator=OPERATOR,
                                       reason=it.reason, now=DRY_RUN_RECORDED_AT)
                entry["decision_id"] = it.decision_id
                entry["phase3"] = dict(it.provenance) | {"gates_version": w.GATES_VERSION}
                entries.append(entry)
                continue
            cand = review.candidate(it.target)
            kwargs: dict[str, Any] = {"decision": it.decision, "operator": OPERATOR, "reason": it.reason}
            if it.decision == DECISION_CONFIRM:
                number = normalize_quote_number(it.quote_number or "")
                kwargs |= {
                    "opportunity_mode": it.opportunity_mode,
                    "opportunity_id": None if it.opportunity_mode == "create_new" else it.opportunity_id,
                    "quote_number": number,
                    "quote_number_basis": (BASIS_CONFIRMED_PROPOSAL if cand.proposed_quote_number == number
                                           else BASIS_OPERATOR_ENTERED),
                }
            entry = build_document_decision(cand, current=current, report_generated_at=review.report_generated_at,
                                            now=DRY_RUN_RECORDED_AT, **kwargs)
        except (ReviewRefused, KeyError) as exc:
            refused[it.target] = f"{type(exc).__name__}: {exc}"
            continue
        entry["decision_id"] = it.decision_id
        if entry.get("opportunity") and it.opportunity_mode == "create_new":
            entry["opportunity"]["planned_opportunity_id"] = it.opportunity_id
        entry["phase3"] = dict(it.provenance) | {"gates_version": w.GATES_VERSION}
        st = DocumentState(status=STATUS_CONFIRMED if it.decision == DECISION_CONFIRM else STATUS_REJECTED,
                           latest=entry, history=[entry])
        current[it.target] = st
        entries.append(entry)
    return answers, resolution, intents, entries, refused, current


def blocked_reason(res: w.DocumentResolution, refused: dict[str, str]) -> list[str]:
    out = [f"{b.code}: {b.detail}" for b in res.blockers]
    if res.sha256 in refused:
        out.append(f"ledger_validation_refused: {refused[res.sha256]}")
    return out


def diff_runs(old: Path, new: Path) -> dict[str, Any]:
    """What changed between two dry-run directories: run counts, per-case state, intents, CRM plan."""
    def jl(p: Path) -> list[dict]:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def cases(p: Path) -> dict[str, dict]:
        with (p / "cases.csv").open(encoding="utf-8", newline="") as f:
            return {(r["documento"] or r["case_ids"]): r for r in csv.DictReader(f)}

    ro, rn = (json.loads((d / "run.json").read_text(encoding="utf-8")) for d in (old, new))
    io, inn = jl(old / "ledger_intents.jsonl"), jl(new / "ledger_intents.jsonl")

    def strip(e: dict) -> dict:
        e = copy.deepcopy(e)
        (e.get("phase3") or {}).pop("gates_version", None)
        return e

    ids_o, ids_n = {e["decision_id"]: strip(e) for e in io}, {e["decision_id"]: strip(e) for e in inn}
    co, cn = cases(old), cases(new)
    changed_cases = {k: {f: [co[k][f], cn[k][f]] for f in cn[k] if co[k].get(f) != cn[k][f]}
                     for k in cn if k in co and co[k] != cn[k]}
    po, pn = (json.loads((d / "crm_plan.json").read_text(encoding="utf-8")) for d in (old, new))
    return {
        "comparado_con": str(old),
        "huella_politica": [ro.get("policy_fingerprint_sha256"), rn.get("policy_fingerprint_sha256")],
        "politica_congelada": [ro.get("policy_frozen"), rn.get("policy_frozen")],
        "entradas_sha256_iguales": ro["inputs_sha256_before"] == rn["inputs_sha256_before"],
        "estados": {"antes": ro["states"], "despues": rn["states"]},
        "bloqueos": {"antes": ro["blockers"], "despues": rn["blockers"]},
        "intenciones": {"antes": ro["intents"], "despues": rn["intents"]},
        "decision_ids_solo_antes": sorted(set(ids_o) - set(ids_n)),
        "decision_ids_solo_despues": sorted(set(ids_n) - set(ids_o)),
        "entradas_distintas_mismo_id_sin_gates_version": sorted(
            k for k in set(ids_o) & set(ids_n) if ids_o[k] != ids_n[k]),
        "casos_cambiados": changed_cases,
        "crm": {"antes": ro["crm"].get("promotable_now"), "despues": rn["crm"].get("promotable_now"),
                "items": [len(po), len(pn)],
                "conflictos_nuevos": sorted({c["codigo"] for i in pn for c in i["conflictos"]}
                                            - {c["codigo"] for i in po for c in i["conflictos"]})},
    }


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        wr.writeheader()
        wr.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--worksheet", type=Path, default=WORKSHEET)
    ap.add_argument("--second-pass", type=Path, default=SECOND_PASS)
    ap.add_argument("--staging-dir", type=Path, default=dr.DEFAULT_STAGING)
    ap.add_argument("--identity-dir", type=Path, default=dr.DEFAULT_IDENTITY)
    ap.add_argument("--ledger", type=Path, default=dr.DEFAULT_LEDGER)
    ap.add_argument("--db-env-file", type=Path, default=None,
                    help="read the CRM (read-only) for the promotion plan; without it an empty snapshot is used")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--compare-with", type=Path, default=None,
                    help="a previous dry-run directory to diff against (read only)")
    args = ap.parse_args(argv)
    try:
        w.assert_policy_frozen()
    except w.PolicyChanged as exc:
        print(f"policy not frozen as approved: {exc}", file=sys.stderr)
        return 4
    if args.out.exists():
        print(f"refusing to overwrite {args.out}; choose another --out", file=sys.stderr)
        return 2
    doc_ledger = args.ledger.with_name(DOCUMENT_LEDGER_FILE)
    inputs = {
        "operator_questions.csv": args.worksheet, "full_second_pass.csv": args.second_pass,
        "decisions.jsonl": args.ledger, "document_decisions.jsonl": doc_ledger,
        "operator_review_queue.csv": args.staging_dir / "operator_review_queue.csv",
        "evidence_source_records.json": args.staging_dir / cd.SOURCE_RECORDS_FILE,
        dr.IDENTITY_JSON: args.identity_dir / dr.IDENTITY_JSON,
    }
    before = {k: dr.sha256_file(p) for k, p in inputs.items()}

    with args.worksheet.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    cases_raw = read_cases(args.second_pass)
    loaded = dr.load_run(args.staging_dir, args.identity_dir, args.ledger, doc_ledger)
    if loaded is None:
        print("an input changed while loading; nothing written", file=sys.stderr)
        return 3
    review, staging, states = loaded.review, loaded.staging, loaded.doc_states
    report = json.loads((args.identity_dir / dr.IDENTITY_JSON).read_text(encoding="utf-8"))
    known = {d["sha256"] for d in report["documents"]}
    known |= {s for r in rows for c in ("document_sha256", "context_documents") for s in r[c].split()}
    known |= {c.sha256 for c in cases_raw if c.sha256}
    index = w.DocumentIndex(known)

    answers, resolution, intents, entries, refused, folded = run_once(rows, cases_raw, index, states, review, staging)
    # Determinism: the same inputs again.
    _, resolution2, intents2, entries2, _, _ = run_once(rows, cases_raw, index, states, review, staging)
    digest, digest2 = w.resolution_digest(resolution, intents), w.resolution_digest(resolution2, intents2)
    entries_json = "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in entries)
    entries_json2 = "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in entries2)
    # Replay: as if every accepted entry had been appended, nothing new may be proposed.
    replay_states = {**states, **{k: v for k, v in folded.items() if k not in states}}
    _, _, replay_intents, replay_entries, _, _ = run_once(rows, cases_raw, index, replay_states, review, staging)
    replay_doc_intents = [i for i in replay_intents if i.ledger == "document_decisions.jsonl"]

    # CRM plan over the would-be confirmations (existing ledger rows + intents), read-only snapshot.
    ledger_rows = [json.loads(x) for x in doc_ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    confirm_entries = [e for e in entries if e.get("target") == "quote_document" and e["decision"] == DECISION_CONFIRM]
    staged = {r["payload"]["source_email_id"]: r for r in json.loads(inputs["evidence_source_records.json"].read_text(encoding="utf-8"))}
    docs = {d["sha256"]: d for d in report["documents"]}
    if args.db_env_file:
        snapshot, crm_meta = cd.read_snapshot(cd.read_dsn(args.db_env_file))
    else:
        snapshot, crm_meta = qc.CrmSnapshot(), {"database": None, "note": "sin lectura de CRM (instantánea vacía)"}

    holds = w.crm_holds(resolution)

    def plan(snap: qc.CrmSnapshot):
        return hold(qc.plan_promotion(confirm_entries, ledger_rows + confirm_entries, docs, staged, snap,
                                      approved_seed_next_serial=qa.APPROVED_SEED_NEXT_SERIAL,
                                      provisional_seed_next_serial=PROVISIONAL_SEED))

    def hold(items):
        """G3: an opportunity with a blocked document is not promoted, whatever else is ready."""
        out = []
        for item in items:
            blocked = holds.get(item.opportunity.get("id_planificado") or "")
            if blocked:
                item = dataclasses.replace(item, conflicts=item.conflicts + (qc.Conflict(
                    C_OPP_HELD, True, "G3: " + " ".join(b[:12] for b in blocked)),))
            out.append(item)
        return out

    crm_items = plan(snapshot)
    crm_again = plan(snapshot)
    crm_replan = plan(qc.simulate_apply(crm_items, snapshot))
    crm_idem = qc.idempotency_report(crm_items, crm_replan)
    crm_idem["determinista"] = qc.plan_digest(crm_items) == qc.plan_digest(crm_again)

    after = {k: dr.sha256_file(p) for k, p in inputs.items()}
    if before != after:
        print("an input changed during the run; nothing written", file=sys.stderr)
        return 3

    # ── outputs ──
    args.out.mkdir(parents=True, mode=0o700)
    spec = w.policy_spec() | {"fingerprint_sha256": w.policy_fingerprint(),
                              "frozen": w.FROZEN_POLICY_SHA256 is not None}
    (args.out / "policy_spec.json").write_text(w.canonical_json(spec) + "\n", encoding="utf-8")

    counts: dict[tuple[str, str], int] = {}
    for a in answers:
        rule_raw = next(r.raw for r in w.ANSWER_TABLE if r.decision_type == a.decision_type and r.matches(a.raw) is not None
                        and (r.extension or a.in_answer_options))
        counts[(a.decision_type, rule_raw)] = counts.get((a.decision_type, rule_raw), 0) + 1
    write_csv(args.out / "enum_normalization.csv",
              ["question_id", "decision_type", "raw_answer", "normalized", "param", "in_answer_options", "extension"],
              [{"question_id": a.question_id, "decision_type": a.decision_type, "raw_answer": a.raw,
                "normalized": a.value, "param": a.param[:12] if len(a.param) == 64 else a.param,
                "in_answer_options": a.in_answer_options, "extension": a.extension} for a in answers])
    write_csv(args.out / "enum_table.csv",
              ["decision_type", "raw_form", "normalized", "param", "extension", "answers_using_it", "phase3_semantics"],
              [{"decision_type": r.decision_type, "raw_form": r.raw + ("…)" if "(" in r.raw else ("…" if r.param else "")),
                "normalized": r.value, "param": r.param, "extension": r.extension,
                "answers_using_it": counts.get((r.decision_type, r.raw), 0),
                "phase3_semantics": "no (bloquea)" if r.value in w.NO_PHASE3_SEMANTICS else "sí"}
               for r in w.ANSWER_TABLE])

    intent_by_target = {i.target: i for i in intents}
    accepted = {e["document_sha256"] if e.get("target") == "quote_document" else str(e["email_id"]) for e in entries}
    case_rows, automatable, blocked = [], [], []
    for sha, res in resolution.documents.items():
        reasons = blocked_reason(res, refused)
        recorded_note = ""
        if res.recorded:
            recorded_note = f"{res.recorded.decision} {res.recorded.quote_number or ''}".strip()
        intent = intent_by_target.get(sha)
        state = ("ya_registrado" if res.automatable and not intent and res.recorded
                 else "automatizable" if (res.automatable and sha in accepted)
                 else "bloqueado")
        row = {
            "case_ids": " ".join(res.case_ids), "documento": sha[:12], "correo": res.email_id,
            "enviado": res.sent_at, "resultado": res.outcome, "estado": state,
            "numero": res.quote_number or "", "rol_version": res.version_role or "",
            "canonica": (res.canonical_sha256 or "")[:12],
            "oportunidad": res.group or "", "miembros_oportunidad": " ".join(m[:12] for m in res.group_members),
            "preguntas": " ".join(res.questions), "ledger_actual": recorded_note,
            "modo": intent.opportunity_mode if intent else "", "bloqueos": " | ".join(reasons),
        }
        case_rows.append(row)
        (blocked if state == "bloqueado" else automatable).append(row)
    for e in resolution.emails:
        state = "automatizable" if e.automatable and str(e.email_id) in accepted else (
            "por_documento" if e.outcome == "resolved_per_document" else "bloqueado")
        row = {"case_ids": e.case_id, "documento": "", "correo": e.email_id, "enviado": "",
               "resultado": e.outcome, "estado": state, "numero": "", "rol_version": "", "canonica": "",
               "oportunidad": "", "miembros_oportunidad": " ".join(d[:12] for d in e.documents),
               "preguntas": " ".join(e.questions), "ledger_actual": "",
               "modo": "", "bloqueos": " | ".join(f"{b.code}: {b.detail}" for b in e.blockers)}
        case_rows.append(row)
        if state == "automatizable":
            automatable.append(row)
        elif state == "bloqueado":
            blocked.append(row)
    fields = list(case_rows[0].keys())
    write_csv(args.out / "cases.csv", fields, case_rows)
    write_csv(args.out / "automatable.csv", fields, automatable)
    write_csv(args.out / "blocked.csv", fields, blocked)
    (args.out / "ledger_intents.jsonl").write_text(entries_json + ("\n" if entries_json else ""), encoding="utf-8")
    (args.out / "crm_plan.json").write_text(qc.canonical_json(crm_items) + "\n", encoding="utf-8")

    doc_keys = [i.decision_id for i in intents]
    idem = {
        "determinista_resolucion": digest == digest2, "sha256_resolucion": digest,
        "determinista_entradas": entries_json == entries_json2,
        "decision_ids_unicos": len(doc_keys) == len(set(doc_keys)),
        "entradas_propuestas": len(entries), "rechazadas_por_validacion_ledger": len(refused),
        "repeticion_tras_aplicar": {"intenciones_documento": len(replay_doc_intents),
                                    "entradas_documento": sum(1 for e in replay_entries if e.get("target") == "quote_document")},
        "crm": crm_idem,
    }

    # ── gates, blocking report, CRM holds ──
    gspec = w.gates_spec() | {"fingerprint_sha256": w.gates_fingerprint(),
                              "frozen": w.FROZEN_GATES_SHA256 == w.gates_fingerprint()}
    (args.out / "gates_spec.json").write_text(w.canonical_json(gspec) + "\n", encoding="utf-8")
    blocking_rows = []
    for sha, res in resolution.documents.items():
        for b in res.blockers:
            rule, needs = w.BLOCKER_GATE.get(b.code, ("", ""))
            blocking_rows.append({
                "documento": sha[:12], "correo": res.email_id, "resultado": res.outcome,
                "numero": res.quote_number or "", "oportunidad": res.group or "",
                "bloqueo": b.code, "detalle": b.detail, "regla": rule,
                "bloquea_ledger": b.code not in w.CRM_ONLY_BLOCKERS, "bloquea_crm": True,
                "identidad_g1": " | ".join(w.quote_identity(res) or ()), "falta": needs,
                "preguntas": " ".join(res.questions),
            })
        if sha in refused:
            blocking_rows.append({"documento": sha[:12], "correo": res.email_id, "resultado": res.outcome,
                                  "numero": res.quote_number or "", "oportunidad": res.group or "",
                                  "bloqueo": "ledger_validation_refused", "detalle": refused[sha], "regla": "",
                                  "bloquea_ledger": True, "bloquea_crm": True, "identidad_g1": "",
                                  "falta": "revisar el rechazo del validador", "preguntas": " ".join(res.questions)})
    write_csv(args.out / "blocking_report.csv", list(blocking_rows[0].keys()) if blocking_rows else ["documento"],
              blocking_rows)
    entry_by_sha = {e["document_sha256"]: e for e in entries if e.get("target") == "quote_document"}
    hold_rows = []
    for key, blocked_members in sorted(holds.items()):
        members = resolution.groups[key]
        ready = [m for m in members if m in entry_by_sha]
        recorded_members = [m for m in members if m in resolution.documents and resolution.documents[m].recorded
                            or m not in resolution.documents]
        hold_rows.append({
            "oportunidad": key, "miembros": len(members),
            "bloqueados": " ".join(m[:12] for m in blocked_members),
            "motivos": " | ".join(sorted({b.code for m in blocked_members for b in resolution.documents[m].ledger_blockers})),
            "se_registran_en_ledger": " ".join(m[:12] for m in ready),
            "ya_en_ledger": " ".join(m[:12] for m in recorded_members),
            "promocion_crm": "retenida (G3)",
        })
    write_csv(args.out / "crm_holds.csv", list(hold_rows[0].keys()) if hold_rows else ["oportunidad"], hold_rows)

    # ── idempotency list: every key a write would carry ──
    idem_rows = []
    for n, e in enumerate(entries, start=1):
        is_doc = e.get("target") == "quote_document"
        res = resolution.documents.get(e.get("document_sha256", "")) if is_doc else None
        ident = w.quote_identity(res) if res else None
        opp = e.get("opportunity") or {}
        idem_rows.append({
            "orden": n, "tipo": "ledger", "ledger": "document_decisions.jsonl" if is_doc else "decisions.jsonl",
            "clave": e["decision_id"], "objetivo": e.get("document_sha256") or str(e.get("email_id")),
            "decision": e["decision"], "modo": opp.get("mode") or "",
            "oportunidad": opp.get("opportunity_id") or opp.get("planned_opportunity_id") or "",
            "identidad_g1": " | ".join(ident) if ident else "", "comando": "",
        })
    n = len(idem_rows)
    for item in crm_items:
        for step in item.steps:
            n += 1
            idem_rows.append({"orden": n, "tipo": "crm", "ledger": "", "clave": step.idempotency_key,
                              "objetivo": item.document_sha256, "decision": item.decision_id, "modo": step.status,
                              "oportunidad": item.opportunity.get("id_planificado") or "",
                              "identidad_g1": "", "comando": step.command})
    write_csv(args.out / "idempotency_list.csv", list(idem_rows[0].keys()), idem_rows)
    identities = [w.quote_identity(r) for r in resolution.documents.values()]
    identities = [i for i in identities if i]
    per_opp_number: dict[tuple[str, str], list[str]] = {}
    for num, opp, sha in identities:
        per_opp_number.setdefault((num, opp), []).append(sha)
    idem["identidad_g1"] = {
        "documentos_confirmables": len(identities),
        "claves_unicas": len(set(identities)) == len(identities),
        "numeros_en_varias_oportunidades": sorted({num for num, _, _ in identities
                                                   if len({o for n2, o, _ in identities if n2 == num}) > 1}),
        "numero_con_varios_documentos_en_una_oportunidad": {
            f"{num} @ {opp}": [s[:12] for s in shas] for (num, opp), shas in sorted(per_opp_number.items())
            if len(shas) > 1},
    }
    (args.out / "idempotency.json").write_text(json.dumps(idem, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # ── apply plan (a plan, not an apply) ──
    doc_entries = [e for e in entries if e.get("target") == "quote_document"]
    email_entries = [e for e in entries if e.get("target") != "quote_document"]
    apply_plan = {
        "estado": "PLAN — nada aplicado; requiere aprobación explícita del owner",
        "politica": {"version": w.POLICY_VERSION, "huella": w.policy_fingerprint(),
                     "gates_version": w.GATES_VERSION, "huella_gates": w.gates_fingerprint()},
        "precondiciones": [
            "assert_policy_frozen() pasa (resolución 41ea7f3b… y gates congeladas)",
            "sha256 de cada entrada igual a run.json inputs_sha256_before",
            "document_decisions.jsonl y decisions.jsonl sin filas ajenas desde este dry run",
            "copia <ledger>.bak-pre-phase3-<ts> de ambos ledgers antes de anexar",
            "cada entrada revalidada con build_document_decision / build_decision al anexar; parada ante el primer rechazo",
            "recorded_at = hora del apply; decision_id y oportunidad planificada iguales a este plan",
        ],
        "anexos": {
            "document_decisions.jsonl": {
                "filas_antes": sum(1 for x in doc_ledger.read_text(encoding="utf-8").splitlines() if x.strip()),
                "filas_a_anexar": len(doc_entries),
                "orden": [{"n": i, "decision_id": e["decision_id"], "documento": e["document_sha256"][:12],
                           "decision": e["decision"], "numero": e.get("quote_number"),
                           "modo": (e.get("opportunity") or {}).get("mode"),
                           "oportunidad": ((e.get("opportunity") or {}).get("opportunity_id")
                                           or (e.get("opportunity") or {}).get("planned_opportunity_id"))}
                          for i, e in enumerate(doc_entries, start=1)],
            },
            "decisions.jsonl": {
                "filas_antes": sum(1 for x in args.ledger.read_text(encoding="utf-8").splitlines() if x.strip()),
                "filas_a_anexar": len(email_entries),
                "orden": [{"n": i, "decision_id": e["decision_id"], "correo": e.get("email_id"),
                           "decision": e["decision"]} for i, e in enumerate(email_entries, start=1)],
            },
        },
        "no_se_anexa": {"documentos_bloqueados": sum(1 for r in resolution.documents.values() if not r.automatable),
                        "detalle": "blocking_report.csv"},
        "crm": {"promocionables_hoy": sum(i.promotable_now for i in crm_items), "items": len(crm_items),
                "oportunidades_retenidas_g3": len(holds),
                "nota": "ninguna escritura CRM forma parte de este plan"},
        "reversion": [
            "ledger: preferida sólo-anexar — una fila leave_pending por decision_id de la Fase 3, motivo 'rollback fase 3 <run>'",
            "ledger: restaurar bytes desde .bak-pre-phase3-<ts> sólo si el prefijo coincide con el hash previo y nadie más anexó",
        ],
    }
    (args.out / "apply_plan.json").write_text(json.dumps(apply_plan, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")

    outcome_counts: dict[str, int] = {}
    for r in case_rows:
        outcome_counts[f"{r['estado']}/{r['resultado']}"] = outcome_counts.get(f"{r['estado']}/{r['resultado']}", 0) + 1
    blocker_counts: dict[str, int] = {}
    for res in resolution.documents.values():
        for b in res.blockers:
            blocker_counts[b.code] = blocker_counts.get(b.code, 0) + 1
    for sha in refused:
        blocker_counts["ledger_validation_refused"] = blocker_counts.get("ledger_validation_refused", 0) + 1
    run = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "policy_version": w.POLICY_VERSION, "policy_fingerprint_sha256": w.policy_fingerprint(),
        "policy_frozen": w.FROZEN_POLICY_SHA256 is not None,
        "answers": len(answers), "extensions": [a.question_id for a in answers if not a.in_answer_options],
        "documents": len(resolution.documents), "email_cases": len(resolution.emails),
        "states": dict(sorted(outcome_counts.items())), "blockers": dict(sorted(blocker_counts.items())),
        "intents": {"document_decisions.jsonl": sum(1 for e in entries if e.get("target") == "quote_document"),
                    "decisions.jsonl": sum(1 for e in entries if e.get("target") != "quote_document")},
        "crm": {**crm_meta, "counts": snapshot.as_counts(),
                "items": len(crm_items), "promotable_now": sum(i.promotable_now for i in crm_items)},
        "gates_version": w.GATES_VERSION, "gates_fingerprint_sha256": w.gates_fingerprint(),
        "gates_frozen": w.FROZEN_GATES_SHA256 == w.gates_fingerprint(),
        "sha256_resolucion": digest, "crm_holds_g3": len(holds),
        "inputs_sha256_before": before, "inputs_sha256_after": after,
        "writes": [str(args.out)],
        "not_written": ["operator_questions.csv", "document_decisions.jsonl", "decisions.jsonl", "emails.sqlite",
                        "CRM (crm.*, evidence.*, platform.*)", "Supabase", "Gmail", "Drive"],
    }
    (args.out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.compare_with:
        (args.out / "diff.json").write_text(json.dumps(
            diff_runs(args.compare_with, args.out), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: run[k] for k in ("answers", "extensions", "documents", "email_cases", "states",
                                          "blockers", "intents", "crm")}, ensure_ascii=False, indent=1))
    print(f"determinista={idem['determinista_resolucion'] and idem['determinista_entradas']} "
          f"replay={idem['repeticion_tras_aplicar']} crm_replay_sin_escrituras={crm_idem['repeticion_sin_escrituras']} "
          f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
