#!/usr/bin/env python3
"""Rebase operator organization confirmations onto a new no-confirmation plan — per opportunity, guarded.

    uv run python scripts/quote_confirmation_rebase.py \
        --confirmations <loader output .jsonl> \
        --old-requests-dir <requests dir the confirmations answer> --old-ledger <its document_decisions.jsonl> \
        --new-requests-dir <requests dir issued over the new plan> --new-ledger <its document_decisions.jsonl> \
        --target-dsn <origenlab_api DSN of the CRM; read-only> \
        [--shadow-plan-sha256 <the full plan this basis belongs to>] --out <new directory>

Each requests dir is the output of `quote_organization_confirmation.py requests` (requests.json +
requests.sha256) and names its dry run (`dry_run_dir`, holding intent.json + intent.sha256). Every
hash is re-checked: requests.json against requests.sha256, intent.json against intent.sha256 and
the requests' plan_sha256, each ledger against its intent's inputs_sha256.

The check itself is `origenlab_api.v2.quote_confirmation_rebase`. Writes only --out (refuses to
overwrite it): rebased_confirmations.jsonl (+ .sha256) with every confirmation that passed,
refused.csv with every one that did not, and rebase_report.json. The input confirmations file,
the ledgers, the plans and the CRM are read only and re-hashed at the end.

Exit codes: 0 all rebased · 1 some refused (the rest are still written) · 2 bad arguments ·
3 an input hash does not hold.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from origenlab_api.v2 import quote_confirmation_rebase as rb
from origenlab_api.v2 import quote_crm_import_plan as qp
from origenlab_api.v2 import quote_review_policy as review_policy
from origenlab_api.v2 import quote_worksheet_resolution as resolution
from origenlab_api.v2.quote_phase3_ledger_apply import APPROVED_GATES_SHA256, APPROVED_POLICY_SHA256

_spec = importlib.util.spec_from_file_location("quote_organization_confirmation_cli",
                                               Path(__file__).resolve().parent / "quote_organization_confirmation.py")
_orgconf_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_orgconf_cli)  # type: ignore[union-attr]

APPROVED = {"review_policy": review_policy.FROZEN_POLICY_SHA256, "resolution_policy": APPROVED_POLICY_SHA256,
            "gates": APPROVED_GATES_SHA256}


class InputRefused(Exception):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_fingerprints() -> dict[str, str]:
    return {"review_policy": review_policy.policy_fingerprint(), "resolution_policy": resolution.policy_fingerprint(),
            "gates": resolution.gates_fingerprint()}


def load_basis(requests_dir: Path, ledger: Path, fingerprints: dict[str, str]) -> tuple[rb.Basis, dict[str, Path]]:
    requests_path = requests_dir / "requests.json"
    requests_sha = sha256_file(requests_path)
    if requests_sha != (requests_dir / "requests.sha256").read_text().strip():
        raise InputRefused(f"{requests_path} does not hash to requests.sha256")
    doc = json.loads(requests_path.read_text(encoding="utf-8"))
    dry = Path(doc["dry_run_dir"])
    intent = json.loads((dry / "intent.json").read_text(encoding="utf-8"))
    plan = qp.plan_sha256(intent)
    if plan != (dry / "intent.sha256").read_text().strip() or plan != doc["plan_sha256"]:
        raise InputRefused(f"{dry}/intent.json hashes to {plan}; intent.sha256 and requests.json must agree")
    if "organization_confirmations.jsonl" in intent["inputs_sha256"]:
        raise InputRefused(f"{dry} is a dry run with confirmations; a basis has none")
    if sha256_file(ledger) != intent["inputs_sha256"]["document_decisions.jsonl"]:
        raise InputRefused(f"{ledger} is not the ledger {dry} was planned from")
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    basis = rb.Basis(plan_sha256=plan, intent=intent, requests_doc=doc, requests_sha256=requests_sha,
                     ledger_rows=rows, fingerprints=fingerprints)
    return basis, {"requests.json": requests_path, "intent.json": dry / "intent.json", "ledger": ledger}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirmations", type=Path, required=True)
    ap.add_argument("--old-requests-dir", type=Path, required=True)
    ap.add_argument("--old-ledger", type=Path, required=True)
    ap.add_argument("--new-requests-dir", type=Path, required=True)
    ap.add_argument("--new-ledger", type=Path, required=True)
    ap.add_argument("--target-dsn", required=True, help="origenlab_api DSN; read-only")
    ap.add_argument("--shadow-plan-sha256")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists():
        print(f"refused: {args.out} exists", file=sys.stderr)
        return 2

    live = live_fingerprints()
    try:
        # The old side was approved when the confirmations were loaded: its fingerprints are the
        # approved constants. The new side is what this code computes now.
        old, old_paths = load_basis(args.old_requests_dir, args.old_ledger, dict(APPROVED))
        new, new_paths = load_basis(args.new_requests_dir, args.new_ledger, live)
    except InputRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    if old.plan_sha256 == new.plan_sha256:
        print("refused: old and new basis are the same plan; nothing to rebase", file=sys.stderr)
        return 2
    crm = _orgconf_cli.read_crm(args.target_dsn)
    for side, basis in (("old", old), ("new", new)):
        if basis.requests_doc["crm_database"] != crm.database:
            print(f"refused: the {side} requests were issued against {basis.requests_doc['crm_database']}, "
                  f"not {crm.database}", file=sys.stderr)
            return 2

    guarded = {"confirmations": args.confirmations, **{f"old.{k}": p for k, p in old_paths.items()},
               **{f"new.{k}": p for k, p in new_paths.items()}}
    before = {k: sha256_file(p) for k, p in guarded.items()}
    confirmations = [json.loads(x) for x in args.confirmations.read_text(encoding="utf-8").splitlines() if x.strip()]
    rebased_at = datetime.now(UTC).isoformat()
    result = rb.rebase_confirmations(confirmations, old, new, crm, approved_fingerprints=APPROVED,
                                     shadow_plan_sha256=args.shadow_plan_sha256, rebased_at=rebased_at)
    after = {k: sha256_file(p) for k, p in guarded.items()}

    args.out.mkdir(parents=True)
    out_file = args.out / "rebased_confirmations.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for r in result.rebased:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    (args.out / "rebased_confirmations.sha256").write_text(sha256_file(out_file) + "\n")
    with (args.out / "refused.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["planned_opportunity_id", "action", "code", "detail"])
        w.writeheader()
        w.writerows(result.refused)
    report: dict[str, Any] = {
        "rebase_version": rb.REBASE_VERSION, "rebased_at": rebased_at,
        "old_plan_sha256": old.plan_sha256, "new_plan_sha256": new.plan_sha256,
        "shadow_plan_sha256": args.shadow_plan_sha256,
        "old_requests_sha256": old.requests_sha256, "new_requests_sha256": new.requests_sha256,
        "crm_database": crm.database, "fingerprints_approved": APPROVED, "fingerprints_live": live,
        "confirmations_in": len(confirmations), "confirmations_in_sha256": before["confirmations"],
        "rebased": len(result.rebased), "refused": len(result.refused),
        "refused_by_code": {c: sum(1 for r in result.refused if r["code"] == c)
                            for c in sorted({r["code"] for r in result.refused})},
        "rebased_confirmations_sha256": sha256_file(out_file),
        "inputs_sha256": before, "inputs_unchanged": before == after,
    }
    (args.out / "rebase_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                                  encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("old_plan_sha256", "new_plan_sha256", "rebased", "refused",
                                             "refused_by_code", "rebased_confirmations_sha256", "inputs_unchanged")},
                     ensure_ascii=False, indent=1))
    if before != after:
        return 3
    return 0 if result.complete else 1


if __name__ == "__main__":
    sys.exit(main())
