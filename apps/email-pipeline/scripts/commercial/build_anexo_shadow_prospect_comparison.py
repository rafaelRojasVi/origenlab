#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: ANEXO-P1 SHADOW prospect comparison over local cached artifacts.
# No network. No Gmail/SQLite/Postgres writes. No persistence. No outreach.
# Rejects --apply/--send/--persist/--network/--ticket/--gmail/--postgres.
# Does NOT mutate production opportunity queues. Does NOT start PR5F.
# See docs/audits/COMMERCIAL_PROCUREMENT_ANEXO_SHADOW_PROSPECTS_P1.md
# -----------------------------------------------------------------------------
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "src"))

from origenlab_email_pipeline.commercial_procurement_anexo_recognition.corpus import (  # noqa: E402
    load_anexo_evidence,
    load_detail_cache,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects import (  # noqa: E402
    build_summary,
    run_shadow_comparison,
    write_shadow_outputs,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.constants import (  # noqa: E402
    AS_OF_UTC_DEFAULT,
    FORBIDDEN_CLI_FLAGS,
    FROZEN_52_DIGEST,
)

DEFAULT_DETAIL_CACHE = APP_ROOT / "reports/out/active/current/chilecompra_detail_cache"
DEFAULT_TARGET_MANIFEST = (
    APP_ROOT
    / "reports/out/active/current/anexo_e2_deferred_52_target/target_manifest.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ANEXO-P1: measure CURRENT vs SHADOW annex-augmented prospect deltas. "
            "Read-only / shadow-only."
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
        help="existing #450 anexo evidence bundle directory (no redownload)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="gitignored destination under reports/out/",
    )
    parser.add_argument(
        "--target-manifest",
        type=Path,
        default=None,
        help="optional frozen target_manifest.json (defaults to deferred-52)",
    )
    parser.add_argument(
        "--as-of-utc",
        default=AS_OF_UTC_DEFAULT,
        help="as-of timestamp for prospect planner",
    )
    parser.add_argument("--json-summary", action="store_true")
    return parser


def _reject_forbidden(argv: list[str]) -> None:
    for flag in argv:
        if flag.split("=", 1)[0] in FORBIDDEN_CLI_FLAGS:
            raise SystemExit(
                f"refusing to run: {flag} is not supported by a shadow-only lane"
            )


def _load_target_ids(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = payload.get("semantic_digest") or payload.get("target_digest")
    if digest and path.resolve() == DEFAULT_TARGET_MANIFEST.resolve():
        if digest != FROZEN_52_DIGEST:
            raise SystemExit(
                f"frozen-52 digest drift: expected {FROZEN_52_DIGEST}, got {digest}"
            )
    tenders = payload.get("tenders") or payload.get("tender_ids") or []
    ids: list[str] = []
    for row in tenders:
        if isinstance(row, dict):
            ids.append(str(row.get("tender_id") or row.get("codigo_externo") or ""))
        else:
            ids.append(str(row))
    return [i for i in ids if i]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _reject_forbidden(argv)
    args = build_parser().parse_args(argv)

    anexo = load_anexo_evidence(args.anexo_evidence_dir)
    cache = load_detail_cache(args.detail_cache_dir)
    by_id = {t["tender_id"]: t for t in cache}

    manifest = args.target_manifest or DEFAULT_TARGET_MANIFEST
    if not manifest.is_file():
        print(f"target manifest missing: {manifest}", file=sys.stderr)
        return 2
    target_ids = _load_target_ids(manifest)
    tenders = tuple(by_id[i] for i in target_ids if i in by_id)
    missing = [i for i in target_ids if i not in by_id]
    if missing:
        print(f"missing detail-cache tenders: {missing[:5]}...", file=sys.stderr)
        return 2
    if not tenders:
        print("no tenders in corpus; nothing to compare", file=sys.stderr)
        return 2

    result, _current, _shadow = run_shadow_comparison(
        tenders, anexo=anexo, as_of_utc=args.as_of_utc
    )
    paths = write_shadow_outputs(
        result,
        args.out_dir,
        repo_email_pipeline_root=APP_ROOT,
        require_git_ignored=True,
    )
    summary = build_summary(result)
    if args.json_summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"wrote shadow packet to {args.out_dir}")
        print(f"current prospects: {summary['current_prospect_count']}")
        print(f"shadow prospects: {summary['shadow_prospect_count']}")
        print(f"new in-scope: {summary['new_in_scope_opportunities']}")
        print(f"partial capability: {summary['partial_capability_opportunities']}")
        print(f"queue entries: {summary['queue_entries']}")
        for key, path in sorted(paths.items()):
            print(f"  {key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
