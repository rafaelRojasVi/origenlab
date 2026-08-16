#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# MANUAL / OPERATOR-INVOKED UTILITY. NOT PRODUCTION AUTO-PUBLICATION.
#
# Publishes T1 (ANEXO) tender terms from ALREADY-ACQUIRED, locally cached
# TenderAttachmentBundle pickles that an operator points it at explicitly
# (--bundles-dir is required; there is no default). Performs no ChileCompra/
# Mercado Publico network acquisition of its own -- it is a pure glue step
# between "bundles already on disk" (produced by the T1 benchmark corpus
# tooling, discover_corpus_entries() in commercial_procurement_anexo_tender_terms
# .benchmark, a prior --publish-tender-terms run's bundle snapshot cache, or
# hand-populated by an operator) and publish_tender_terms(...).
#
# The production path for CURRENT tenders is
# auto-refresh-chilecompra-equipment's opt-in --publish-tender-terms flag
# (operator_cli/chilecompra_auto_refresh.py): after a successful same-run
# --publish-institution-prospects (W1) publication, it calls
# acquire_and_publish_current_tender_terms(...)
# (commercial_procurement_anexo_tender_terms/current_tender_annex_publish.py),
# which builds a TenderAttachmentBundle per current_opportunity_queue
# tender_code via build_tender_bundle() and publishes them in-process. That
# flag defaults to false and is not wired into the tracked cron wrapper, so
# it must still be enabled explicitly per run.
#
# THIS script remains a separate manual replay/debug/recovery path: it
# reads only bundles an operator already has cached on disk, so it can
# re-publish from a previously-acquired snapshot without re-acquiring
# anything. It is not, and must not become, a production acquisition owner.
#
# No SQLite/Postgres writes. No Gmail/outreach. No new acquisition owner.
# -----------------------------------------------------------------------------
"""Publish T1 tender terms from cached (already-acquired) bundle pickles.

THIS IS A MANUAL / OPERATOR-INVOKED UTILITY, NOT PRODUCTION AUTO-PUBLICATION.

It publishes T1 terms from already-cached benchmark/validation bundle
pickles that you supply explicitly via --bundles-dir. The production path
for current tenders is auto-refresh-chilecompra-equipment's opt-in
--publish-tender-terms flag, which acquires and publishes T1 in-process for
the current run's W1 opportunity queue after a successful
--publish-institution-prospects publication. This script instead replays
from bundles already sitting on disk -- useful for debug/recovery/
re-publication without re-acquiring, or for the benchmark corpus -- and is
not itself wired into that (or any) production run.

Why this script exists, and why it stays manual-only:

  Beyond the auto-refresh CLI's same-run acquisition, there remains a
  useful, separate case: republishing from bundles that are already on
  disk (a benchmark corpus, a prior run's bundle snapshot cache under
  current_bundle_snapshot/, or a hand-populated directory) without paying
  for a fresh acquisition. The only local artifact that already holds
  real, already-acquired TenderAttachmentBundle objects for that purpose
  is a pickle-cache directory that discover_corpus_entries() (in
  commercial_procurement_anexo_tender_terms.benchmark) already knows how to
  enumerate. This script is the smallest safe glue on top of that: it
  reuses discover_corpus_entries() read-only against a bundles directory
  the operator names explicitly, unpickles the cached bundles (no
  attachment bytes are downloaded, no network calls of any kind), and calls
  the validated publish_tender_terms(...) entry point exactly as
  apps/api's reader expects it (fail-closed, atomic, contract-versioned).

Usage (operator must name the bundles directory explicitly -- there is no
default, so this can never silently read a stale/unintended directory):
    uv run python scripts/commercial/publish_tender_terms_from_cached_bundles.py \\
        --bundles-dir reports/out/active/current/anexo_e2_deferred_52_target/acquired_bundles \\
        --out-dir reports/out/active/current/tender_terms

Rejects any acquisition/mutation flag by construction: no --apply/--send/
--persist/--network/--ticket/--gmail/--postgres/--outreach flags exist on
this parser at all.
"""
from __future__ import annotations

import argparse
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "src"))

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (  # noqa: E402
    TenderAttachmentBundle,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.benchmark import (  # noqa: E402
    discover_corpus_entries,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.production_publish import (  # noqa: E402
    TENDER_TERMS_DIRNAME,
    publish_tender_terms,
)


def _default_out_dir() -> Path:
    return APP_ROOT / "reports" / "out" / "active" / "current" / TENDER_TERMS_DIRNAME


def _load_bundles(bundles_dir: Path, tender_ids: tuple[str, ...] | None) -> list[TenderAttachmentBundle]:
    entries = discover_corpus_entries(
        bundles_dir=bundles_dir,
        detail_cache_dir=bundles_dir.parent / "chilecompra_detail_cache",
    )
    if tender_ids:
        wanted = set(tender_ids)
        entries = tuple(e for e in entries if e.tender_id in wanted)
        missing = wanted - {e.tender_id for e in entries}
        if missing:
            raise SystemExit(f"ERROR: requested tender_ids not found in cache: {sorted(missing)}")

    bundles: list[TenderAttachmentBundle] = []
    for entry in entries:
        loaded = pickle.loads(entry.bundle_path.read_bytes())
        if not isinstance(loaded, TenderAttachmentBundle):
            raise SystemExit(f"ERROR: {entry.bundle_path} did not unpickle to a TenderAttachmentBundle")
        bundles.append(loaded)
    return bundles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--bundles-dir",
        type=Path,
        required=True,
        help=(
            "MANUAL/DEV USE ONLY: directory of already-cached TenderAttachmentBundle "
            "*.pkl files to publish from (e.g. a benchmark corpus acquired_bundles/ "
            "directory). Required -- there is no default, and this directory is never "
            "acquired or refreshed by this script."
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=_default_out_dir())
    parser.add_argument(
        "--tender-id",
        action="append",
        dest="tender_ids",
        default=None,
        help="Restrict publication to this tender_id (repeatable). Default: all cached bundles.",
    )
    parser.add_argument(
        "--as-of-utc",
        default=None,
        help="ISO-8601 UTC timestamp for the publication (default: now).",
    )
    args = parser.parse_args(argv)

    if not args.bundles_dir.is_dir():
        print(f"ERROR: bundles directory not found: {args.bundles_dir}", file=sys.stderr)
        return 2

    tender_ids = tuple(args.tender_ids) if args.tender_ids else None
    bundles = _load_bundles(args.bundles_dir, tender_ids)
    if not bundles:
        print("ERROR: no cached bundles found to publish", file=sys.stderr)
        return 2

    as_of_utc = args.as_of_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = publish_tender_terms(bundles=bundles, as_of_utc=as_of_utc, out_dir=args.out_dir)
    print(result.to_dict())
    return 0 if result.result == "applied" else 1


if __name__ == "__main__":
    raise SystemExit(main())
