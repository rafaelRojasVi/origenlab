#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: ANEXO-E2 read-only comparison over existing local artifacts.
# No network. No Gmail/SQLite/Postgres writes. No persistence. No outreach.
# Rejects --apply/--send/--persist/--network/--ticket/--gmail/--postgres.
# Does not change PR5D/PR5E production decisions or the opportunity queues.
# -----------------------------------------------------------------------------
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "src"))

from origenlab_email_pipeline.commercial_procurement_anexo_recognition import (  # noqa: E402
    build_summary,
    load_anexo_evidence,
    load_detail_cache,
    run_comparison,
    select_corpus,
    write_comparison_outputs,
)

FORBIDDEN_CLI_FLAGS = (
    "--apply",
    "--send",
    "--persist",
    "--network",
    "--ticket",
    "--gmail",
    "--postgres",
    "--outreach",
    "--schedule",
)

DEFAULT_DETAIL_CACHE = APP_ROOT / "reports/out/active/current/chilecompra_detail_cache"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ANEXO-E2: compare baseline (API/item only) against augmented "
            "(API/item + annex evidence) equipment recognition. Read-only."
        )
    )
    parser.add_argument(
        "--detail-cache-dir",
        type=Path,
        default=DEFAULT_DETAIL_CACHE,
        help="local ChileCompra detail cache directory",
    )
    parser.add_argument(
        "--anexo-evidence-dir",
        type=Path,
        required=True,
        help="existing #450 anexo evidence bundle directory",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="gitignored destination under reports/out/",
    )
    parser.add_argument(
        "--only-with-anexos",
        action="store_true",
        help="restrict the corpus to tenders that have an annex bundle",
    )
    parser.add_argument("--json-summary", action="store_true")
    return parser


def _reject_forbidden(argv: list[str]) -> None:
    for flag in argv:
        if flag.split("=", 1)[0] in FORBIDDEN_CLI_FLAGS:
            raise SystemExit(f"refusing to run: {flag} is not supported by a read-only lane")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _reject_forbidden(argv)
    args = build_parser().parse_args(argv)

    anexo = load_anexo_evidence(args.anexo_evidence_dir)
    tenders = select_corpus(
        load_detail_cache(args.detail_cache_dir),
        anexo=anexo,
        only_with_anexos=args.only_with_anexos,
    )
    if not tenders:
        print("no tenders in corpus; nothing to compare", file=sys.stderr)
        return 2

    result = run_comparison(tenders, anexo=anexo)
    paths = write_comparison_outputs(
        result, args.out_dir, repo_email_pipeline_root=APP_ROOT
    )

    summary = build_summary(result)
    if args.json_summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"tenders evaluated: {summary['tenders_evaluated']}")
        print(f"tenders with anexos: {summary['tenders_with_anexos']}")
        print(f"annex claims: {summary['annex_claims']}")
        print(f"new annex positives: {summary['new_annex_positive_claims']}")
        print(f"change classes: {summary['change_class_counts']}")
        print(f"walkthrough: {paths['walkthrough']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
