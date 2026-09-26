#!/usr/bin/env python3
"""Phase 3 ledger apply: append the frozen dry run's rows to the two decision ledgers.

    # default: dry run — checks everything, writes no ledger
    uv run python scripts/quote_phase3_ledger_apply.py

    # apply — only with explicit owner approval
    uv run python scripts/quote_phase3_ledger_apply.py --apply \
        --expect-plan-sha256 <sha256 of ledger_intents.jsonl> --out <new run directory>

The rows are those of `<plan-dir>/ledger_intents.jsonl` (the frozen Phase 3 dry run). Before the
first write, and in a dry run as well, it checks:

* the resolution (41ea7f3b…) and gates (98b8ab7a…) fingerprints, in code and in the plan files;
* the sha256 of every input against the plan's `run.json`: worksheet, second pass, staging,
  identity report; each ledger must be the audited file, or the audited file followed by the first
  plan rows in plan order (rerun or resumed stop) — nothing else;
* the frozen resolution recomputed over the audited ledgers reproduces the plan row for row;
* the 20 retained documents (13 number collisions, CN01200/CN01201, both 01242-26, the generic
  clients, W114) are exactly what the resolution blocks, and no row touches them;
* G1 (one number, one opportunity) and G2 (the number is the printed one) on every row;
* no row targets a document or email that already carries a decision (no overwrite).

`--apply` additionally refuses while the review server (`quote_evidence_review.py`) runs, copies
each ledger it will append to as `<ledger>.bak-pre-phase3-<ts>`, appends one row at a time (the
ledger's hash is checked before each append), and verifies afterwards that the audited prefix is
intact and the appended bytes are exactly the planned rows. Rows carry `recorded_at` = apply time;
decision ids and planned opportunity ids are the plan's. A second run finds every row present and
writes nothing — no backup, no ledger byte, no --out directory.

Never writes the worksheet, staging, emails.sqlite, the CRM, Supabase, Gmail or Drive.
Exit: 0 ok · 2 usage/--out exists · 3 input hash mismatch · 4 fingerprint · 5 plan refused ·
6 server running · 7 stopped mid-apply (see the report).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import quote_crm_promotion_dryrun as cd
import quote_review_policy_dryrun as dr
import quote_worksheet_phase3_dryrun as p3

from origenlab_api.v2 import quote_phase3_ledger_apply as ap
from origenlab_api.v2 import quote_worksheet_resolution as w
from origenlab_api.v2.quote_confirmation_reconciliation import effective_document_states
from origenlab_api.v2.quote_document_review import DocumentDecisionLedger, build_document_review
from origenlab_api.v2.quote_evidence_review import DecisionLedger, load_staging

DEFAULT_PLAN_DIR = dr.AUDITS / "quote-phase3-worksheet-dryrun-20260925-frozen"
PLAN_FILES = ("ledger_intents.jsonl", "run.json", "policy_spec.json", "gates_spec.json", "apply_plan.json",
              "blocking_report.csv")
REVIEW_SERVER = "quote_evidence_review.py"


class _RowsLedger(DocumentDecisionLedger):
    """A ledger read from rows already in memory (the audited prefix). Never appends."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(Path("/nonexistent"))
        self._rows = rows

    def entries(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def append(self, entry: dict[str, Any]) -> None:  # pragma: no cover - guard
        raise RuntimeError("read-only")


class _EmailRowsLedger(DecisionLedger):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(Path("/nonexistent"))
        self._rows = rows

    def entries(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def append(self, entry: dict[str, Any]) -> None:  # pragma: no cover - guard
        raise RuntimeError("read-only")


def parse_rows(lines: list[bytes]) -> list[dict[str, Any]]:
    return [json.loads(x) for x in lines if x.strip()]


def review_server_pids() -> list[int]:
    """Processes running the review server, which can append to both ledgers."""
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(part.endswith(REVIEW_SERVER.encode()) for part in cmd):
            out.append(int(d.name))
    return sorted(out)


def append_rows(path: Path, rows: list[dict[str, Any]], expected_sha256: str) -> tuple[list[dict[str, Any]], str | None]:
    """Append rows one by one; before each, the file must still hash to what this run left.
    Returns the rows written and the stop reason (None when all were written)."""
    written: list[dict[str, Any]] = []
    for row in rows:
        if dr.sha256_file(path) != expected_sha256:
            return written, f"{path.name} cambió fuera de esta ejecución antes de la fila {row['decision_id']}"
        data = ap.serialize_row(row)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        written.append(row)
        expected_sha256 = dr.sha256_file(path) or ""
    return written, None


def backup(path: Path, ts: str) -> Path:
    dest = path.with_name(f"{path.name}.bak-pre-phase3-{ts}")
    data = path.read_bytes()
    with dest.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if dr.sha256_file(dest) != ap.sha256_bytes(data):
        raise OSError(f"backup {dest} does not match {path}")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="the default: check and report, write no ledger")
    mode.add_argument("--apply", action="store_true", help="append the rows (needs --expect-plan-sha256 and --out)")
    parser.add_argument("--plan-dir", type=Path, default=DEFAULT_PLAN_DIR)
    parser.add_argument("--expect-plan-sha256", default=None,
                        help="sha256 of <plan-dir>/ledger_intents.jsonl the owner approved")
    parser.add_argument("--out", type=Path, default=None, help="new directory for the run report")
    parser.add_argument("--worksheet", type=Path, default=p3.WORKSHEET)
    parser.add_argument("--second-pass", type=Path, default=p3.SECOND_PASS)
    parser.add_argument("--staging-dir", type=Path, default=dr.DEFAULT_STAGING)
    parser.add_argument("--identity-dir", type=Path, default=dr.DEFAULT_IDENTITY)
    parser.add_argument("--ledger", type=Path, default=dr.DEFAULT_LEDGER, help="decisions.jsonl (the document "
                        "ledger is its sibling document_decisions.jsonl)")
    args = parser.parse_args(argv)
    applying = bool(args.apply)
    if applying and (not args.expect_plan_sha256 or not args.out):
        print("--apply needs --expect-plan-sha256 and --out", file=sys.stderr)
        return 2
    if args.out and args.out.exists():
        print(f"refusing to overwrite {args.out}", file=sys.stderr)
        return 2

    # 1. Fingerprints: code and plan files.
    try:
        ap.assert_approved()
    except (w.PolicyChanged, ap.ApplyRefused) as exc:
        print(f"STOP (fingerprint): {exc}", file=sys.stderr)
        return 4
    plan_hashes = {f: dr.sha256_file(args.plan_dir / f) for f in PLAN_FILES}
    run = json.loads((args.plan_dir / "run.json").read_text(encoding="utf-8"))
    pspec = json.loads((args.plan_dir / "policy_spec.json").read_text(encoding="utf-8"))
    gspec = json.loads((args.plan_dir / "gates_spec.json").read_text(encoding="utf-8"))
    fp_problems = [
        name for name, got, want in (
            ("run.json policy", run.get("policy_fingerprint_sha256"), ap.APPROVED_POLICY_SHA256),
            ("run.json gates", run.get("gates_fingerprint_sha256"), ap.APPROVED_GATES_SHA256),
            ("policy_spec.json", pspec.get("fingerprint_sha256"), ap.APPROVED_POLICY_SHA256),
            ("gates_spec.json", gspec.get("fingerprint_sha256"), ap.APPROVED_GATES_SHA256),
        ) if got != want
    ]
    if fp_problems:
        print(f"STOP (fingerprint): plan files differ: {', '.join(fp_problems)}", file=sys.stderr)
        return 4
    if args.expect_plan_sha256 and plan_hashes["ledger_intents.jsonl"] != args.expect_plan_sha256:
        print(f"STOP: ledger_intents.jsonl is {plan_hashes['ledger_intents.jsonl']}, "
              f"not the approved {args.expect_plan_sha256}", file=sys.stderr)
        return 3

    # 2. Input hashes against the plan.
    doc_ledger = args.ledger.with_name(ap.DOCUMENT_LEDGER)
    ledger_paths = {ap.DOCUMENT_LEDGER: doc_ledger, ap.EMAIL_LEDGER: args.ledger}
    inputs = {
        "operator_questions.csv": args.worksheet, "full_second_pass.csv": args.second_pass,
        "operator_review_queue.csv": args.staging_dir / "operator_review_queue.csv",
        "evidence_source_records.json": args.staging_dir / cd.SOURCE_RECORDS_FILE,
        dr.IDENTITY_JSON: args.identity_dir / dr.IDENTITY_JSON,
    }
    base_sha = run["inputs_sha256_before"]
    before = {k: dr.sha256_file(p) for k, p in inputs.items()}
    mismatch = [k for k, v in before.items() if v != base_sha[k]]
    raw = {name: p.read_bytes() for name, p in ledger_paths.items()}
    lines = {name: data.splitlines(keepends=True) for name, data in raw.items()}
    prefix: dict[str, int] = {}
    for name in ap.LEDGER_ORDER:
        n = ap.audited_prefix(lines[name], base_sha[name])
        if n is None:
            mismatch.append(f"{name} (no empieza con el ledger auditado {base_sha[name][:8]}…)")
        elif raw[name] and not raw[name].endswith(b"\n"):
            mismatch.append(f"{name} (no termina en salto de línea)")
        else:
            prefix[name] = n
    if mismatch:
        print("STOP (hash): " + "; ".join(mismatch), file=sys.stderr)
        return 3
    base_rows = {name: parse_rows(lines[name][:prefix[name]]) for name in ap.LEDGER_ORDER}
    extra_rows = {name: parse_rows(lines[name][prefix[name]:]) for name in ap.LEDGER_ORDER}

    # 3. Recompute the frozen resolution over the audited ledgers.
    with args.worksheet.open(encoding="utf-8", newline="") as f:
        ws_rows = list(csv.DictReader(f))
    cases_raw = p3.read_cases(args.second_pass)
    staging = load_staging(args.staging_dir)
    report = json.loads((args.identity_dir / dr.IDENTITY_JSON).read_text(encoding="utf-8"))
    review = build_document_review(staging, report)
    email_states = _EmailRowsLedger(base_rows[ap.EMAIL_LEDGER]).states()
    states = effective_document_states(review, _RowsLedger(base_rows[ap.DOCUMENT_LEDGER]), email_states)
    known = {d["sha256"] for d in report["documents"]}
    known |= {s for r in ws_rows for c in ("document_sha256", "context_documents") for s in r[c].split()}
    known |= {c.sha256 for c in cases_raw if c.sha256}
    _, resolution, _, recomputed, refused, _ = p3.run_once(ws_rows, cases_raw, w.DocumentIndex(known),
                                                           states, review, staging)
    plan_entries = parse_rows((args.plan_dir / "ledger_intents.jsonl").read_bytes().splitlines(keepends=True))
    resolved_numbers = {sha: r.quote_number for sha, r in resolution.documents.items()
                        if r.outcome in w.CONFIRM_OUTCOMES}
    plan = ap.plan_apply(plan_entries, recomputed, resolved_numbers, base_rows, extra_rows)
    plan.problems += ap.exclusion_problems(s for s, r in resolution.documents.items() if not r.automatable)
    plan.problems += [f"validador del ledger rechaza {k[:12]}: {v}" for k, v in sorted(refused.items())]

    # 4. Nothing moved while reading.
    again = {k: dr.sha256_file(p) for k, p in inputs.items()}
    again_ledgers = {name: dr.sha256_file(p) for name, p in ledger_paths.items()}
    ledger_before = {name: ap.sha256_bytes(raw[name]) for name in ap.LEDGER_ORDER}
    if again != before or again_ledgers != ledger_before:
        print("STOP (hash): an input changed while reading; nothing written", file=sys.stderr)
        return 3

    summary: dict[str, Any] = {
        "modo": "apply" if applying else "dry-run",
        "politica": {"version": w.POLICY_VERSION, "huella": w.policy_fingerprint(),
                     "gates_version": w.GATES_VERSION, "huella_gates": w.gates_fingerprint()},
        "plan_dir": str(args.plan_dir), "plan_sha256": plan_hashes,
        "entradas_sha256": before,
        "ledgers": {
            name: {"sha256_auditado": base_sha[name], "sha256_antes": ledger_before[name],
                   "filas_auditadas": prefix[name], "filas_antes": len(base_rows[name]) + len(extra_rows[name]),
                   "filas_del_plan": len(plan.ledgers[name].already) + len(plan.ledgers[name].to_append),
                   "ya_presentes": len(plan.ledgers[name].already),
                   "a_anexar": len(plan.ledgers[name].to_append),
                   "problemas": plan.ledgers[name].problems}
            for name in ap.LEDGER_ORDER
        },
        "retenidos": {s[:12]: list(r) for s, r in sorted(ap.EXCLUDED_DOCUMENTS.items())},
        "problemas": plan.problems,
        "escrituras_previstas": plan.writes,
    }
    if not plan.ok:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        print("STOP (plan refused): nothing written", file=sys.stderr)
        return 5

    if not applying:
        summary["nota"] = "dry run: ningún ledger escrito; el hash posterior depende de recorded_at (hora del apply)"
        if args.out:
            args.out.mkdir(parents=True, mode=0o700)
            (args.out / "dry_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                                                   encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0

    # ── apply ──
    if plan.writes == 0:
        summary["resultado"] = "0 filas: todo el plan ya está en los ledgers; nada escrito, sin backup ni --out"
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    pids = review_server_pids()
    if pids:
        print(f"STOP: the review server is running (pid {', '.join(map(str, pids))}); stop it first", file=sys.stderr)
        return 6
    now = datetime.now(UTC)
    ts = now.strftime("%Y-%m-%dT%H%M%SZ")
    args.out.mkdir(parents=True, mode=0o700)
    backups = {}
    for name in ap.LEDGER_ORDER:
        if plan.ledgers[name].to_append:
            dest = backup(ledger_paths[name], ts)
            backups[name] = {"ruta": str(dest), "sha256": dr.sha256_file(dest)}
    appended: dict[str, list[dict[str, Any]]] = {}
    stop = None
    for name in ap.LEDGER_ORDER:
        rows = [ap.stamp(e, now) for e in plan.ledgers[name].to_append]
        appended[name], stop = append_rows(ledger_paths[name], rows, ledger_before[name])
        if stop:
            break

    after_raw = {name: p.read_bytes() for name, p in ledger_paths.items()}
    checks: dict[str, Any] = {}
    for name in ap.LEDGER_ORDER:
        expect = raw[name] + b"".join(ap.serialize_row(r) for r in appended.get(name, []))
        checks[f"{name}: prefijo intacto"] = after_raw[name][:len(raw[name])] == raw[name]
        checks[f"{name}: bytes anexados == filas del plan"] = after_raw[name] == expect
    checks["entradas sin cambios"] = {k: dr.sha256_file(p) for k, p in inputs.items()} == before
    summary |= {
        "aplicado_en": now.isoformat(), "backups": backups, "parada": stop, "comprobaciones": checks,
        "ledgers_despues": {
            name: {"sha256_despues": ap.sha256_bytes(after_raw[name]),
                   "filas_despues": sum(1 for x in after_raw[name].splitlines() if x.strip()),
                   "filas_anexadas": len(appended.get(name, []))}
            for name in ap.LEDGER_ORDER
        },
    }
    anexadas = []
    for name in ap.LEDGER_ORDER:
        start = len(base_rows[name]) + len(extra_rows[name])
        for i, r in enumerate(appended.get(name, []), start=1):
            anexadas.append({"ledger": name, "fila": start + i, "decision_id": r["decision_id"],
                             "objetivo": ap.target_of(r), "decision": r["decision"],
                             "numero": r.get("quote_number") or "",
                             "modo": (r.get("opportunity") or {}).get("mode") or "",
                             "oportunidad": ap.opportunity_key(r) or "",
                             "sha256_fila": ap.sha256_bytes(ap.serialize_row(r))})
    (args.out / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with (args.out / "appended_rows.csv").open("x", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(anexadas[0]) if anexadas else ["ledger"], lineterminator="\n")
        wr.writeheader()
        wr.writerows(anexadas)
    (args.out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 7 if stop or not all(checks.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
