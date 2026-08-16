#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: T1 Semantic Benchmark v1. Read-only against local caches only.
# No network calls except to a local Ollama endpoint (127.0.0.1) when
# --no-semantic is not passed. No SQLite/Postgres writes. No Gmail/outreach.
# Does not change T1 extraction or semantic-fallback semantics.
# Rejects --apply/--send/--persist/--network/--ticket/--gmail/--postgres.
# -----------------------------------------------------------------------------
from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "src"))

from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.benchmark import (  # noqa: E402
    KNOWN_CONTROL_TENDER_IDS,
    benchmark_tender,
    build_ollama_client,
    discover_corpus_entries,
    load_adjudication,
    load_bundle,
    score_against_adjudication,
    select_corpus,
    summarize,
    write_outputs,
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

DEFAULT_BUNDLES_DIR = (
    APP_ROOT / "reports/out/active/current/anexo_e2_deferred_52_target/acquired_bundles"
)
DEFAULT_DETAIL_CACHE_DIR = APP_ROOT / "reports/out/active/current/chilecompra_detail_cache"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "T1 Semantic Benchmark v1: compare deterministic ANEXO-T1 extraction "
            "against deterministic + bounded qwen3 semantic fallback over a "
            "locally cached, already-acquired tender corpus. Read-only."
        )
    )
    parser.add_argument(
        "--bundles-dir",
        type=Path,
        default=DEFAULT_BUNDLES_DIR,
        help="directory of pickled TenderAttachmentBundle caches",
    )
    parser.add_argument(
        "--detail-cache-dir",
        type=Path,
        default=DEFAULT_DETAIL_CACHE_DIR,
        help="local ChileCompra detail cache directory (for institution/category metadata)",
    )
    parser.add_argument(
        "--corpus-size",
        type=int,
        default=30,
        help="maximum number of tenders to benchmark (default: 30)",
    )
    parser.add_argument(
        "--tender-id",
        action="append",
        default=[],
        dest="tender_ids",
        help="require this tender in the corpus if cached (repeatable)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="qwen3:14b",
        help="Ollama model name for the semantic fallback (default: qwen3:14b)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="gitignored destination under reports/out/",
    )
    parser.add_argument(
        "--no-semantic",
        action="store_true",
        help="run deterministic-only, skipping the semantic fallback entirely",
    )
    parser.add_argument(
        "--adjudication-file",
        type=Path,
        default=None,
        help="optional JSONL of human gold labels to score precision/recall against",
    )
    return parser


def _reject_forbidden(argv: list[str]) -> None:
    for flag in argv:
        if flag.split("=", 1)[0] in FORBIDDEN_CLI_FLAGS:
            raise SystemExit(f"refusing to run: {flag} is not supported by a read-only lane")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _reject_forbidden(argv)
    args = build_parser().parse_args(argv)

    entries = discover_corpus_entries(
        bundles_dir=args.bundles_dir,
        detail_cache_dir=args.detail_cache_dir,
    )
    if not entries:
        print(f"no cached bundles found under {args.bundles_dir}", file=sys.stderr)
        return 2

    required_ids = tuple(args.tender_ids) + KNOWN_CONTROL_TENDER_IDS
    corpus = select_corpus(
        entries,
        corpus_size=args.corpus_size,
        required_tender_ids=required_ids,
    )
    if not corpus:
        print("selected corpus is empty; nothing to benchmark", file=sys.stderr)
        return 2

    client = None if args.no_semantic else build_ollama_client(model=args.model)
    model_name = None if args.no_semantic else args.model

    all_cases = []
    for entry in corpus:
        bundle = load_bundle(entry)
        cases = benchmark_tender(entry, bundle, client=client, model_name=model_name)
        all_cases.extend(cases)

    cases_tuple = tuple(all_cases)
    summary = summarize(cases_tuple, tender_count=len(corpus))

    adjudication_metrics = None
    if args.adjudication_file is not None:
        adjudication = load_adjudication(args.adjudication_file)
        if adjudication:
            adjudication_metrics = score_against_adjudication(cases_tuple, adjudication)

    paths = write_outputs(
        out_dir=args.out_dir,
        cases=cases_tuple,
        summary=summary,
        corpus=corpus,
        adjudication_metrics=adjudication_metrics,
        model_name=model_name,
    )

    print(f"tenders benchmarked: {summary.tenders}")
    print(f"field evaluations: {summary.field_evaluations}")
    print(f"model calls: {summary.model_calls}")
    print(f"accepted semantic positives: {summary.accepted_semantic_positives}")
    print(f"conflicts: {summary.conflicts}")
    print(f"model errors: {summary.model_errors}")
    print(f"release gates passed: {summary.release_gates['passed']}")
    print(f"report: {paths['report']}")

    return 0 if summary.release_gates["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
