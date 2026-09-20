#!/usr/bin/env python3
"""Import the verified Wave 1A / Wave 1B safety artifacts into a local V2 database.

**Dry-run by default.** Without `--apply` this tool opens no database at all: it verifies
every artifact, computes the complete deterministic plan, reconciles it against the
measured cross-wave report and prints PII-safe aggregate counts.

With `--apply` it writes — and only ever to a disposable loopback PostgreSQL carrying the
Slice 0 V2 schema. `migration.v2_import.target` accepts a **literal loopback address** and
nothing else: a URI query string, a fragment, a multi-host list, a Unix socket, a host
*name* (including `localhost`), a hosted-provider marker and a host-less DSN all refuse,
the DSN is rebuilt from the parts that were checked, and the libpq `PG*` environment is
removed for the connection. There is no override flag. No hosted Supabase project has been
adopted (`docs/STATUS.md` §2.5) and nothing here changes that.

The seven V2 tables are owned by `origenlab_owner` and grant nothing to the Supabase CLI's
`postgres` login, so the apply path takes `set local role origenlab_owner` inside its one
transaction — the same step every migration takes. Connect as a login holding a SET
membership of that role (`supabase/roles.sql` grants one to `postgres` and to
`origenlab_migrator`).

What it never does, in any mode: create a `crm.person`, `crm.organization`,
`crm.contact_point` or prospect relationship; open Gmail, Drive, Supabase, Render or
Cloudflare; send email; or alter campaign or sender state.

Examples:
    # Dry run — no database, no writes.
    uv run python scripts/migration/import_waves_into_v2.py \
        --migration-root ~/data/origenlab-v2-migration

    # Apply to the disposable local database.
    uv run python scripts/migration/import_waves_into_v2.py \
        --migration-root ~/data/origenlab-v2-migration \
        --apply --database-url postgresql://postgres:postgres@127.0.0.1:54322/postgres
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if str(REPO_SRC) not in sys.path:  # pragma: no cover - script bootstrap
    sys.path.insert(0, str(REPO_SRC))

from origenlab_email_pipeline.migration.v2_import.artifacts import (  # noqa: E402
    InputRefused,
    load_inputs,
)
from origenlab_email_pipeline.migration.v2_import.plan import (  # noqa: E402
    MappingRefused,
    build_plan,
)
from origenlab_email_pipeline.migration.v2_import.report import (  # noqa: E402
    ReportRefused,
    build_aggregate_report,
    render_console,
    write_reject_artifact,
)
from origenlab_email_pipeline.migration.v2_import.target import (  # noqa: E402
    TargetRefused,
    assert_local_target,
)

WAVE1A_BUNDLE = "20260905T042425Z_wave1a_v1_safety_bundle"
WAVE1B_BUNDLE = "20260920T171109Z_wave1b_v1_safety_bundle"
RECONCILIATION = (
    f"{WAVE1A_BUNDLE}__{WAVE1B_BUNDLE}_cross_wave_safety_reconciliation_v2.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--migration-root",
        required=True,
        type=Path,
        help="the operator's private migration root holding both bundles (outside Git)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write to the database. Without this flag nothing is written and no "
        "connection is opened.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="a loopback PostgreSQL DSN. Required with --apply; refused otherwise.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the aggregate report as JSON instead of text.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.migration_root.expanduser()

    if args.apply and not args.database_url:
        print("refused: --apply requires --database-url", file=sys.stderr)
        return 2
    if args.database_url and not args.apply:
        print(
            "refused: --database-url was given without --apply. This tool is dry-run by "
            "default and will not open a connection it was not asked to write to.",
            file=sys.stderr,
        )
        return 2

    # The target is validated before a single artifact is read, so an operator who points
    # at the wrong database finds out immediately rather than after a long verification.
    target = None
    if args.apply:
        try:
            target = assert_local_target(args.database_url)
        except TargetRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 3

    try:
        inputs = load_inputs(
            wave1a_bundle=root / WAVE1A_BUNDLE,
            wave1a_archive=root / f"{WAVE1A_BUNDLE}.tar.gz",
            wave1a_addendum=root / f"{WAVE1A_BUNDLE}_rfc2047_addendum.jsonl",
            wave1b_bundle=root / WAVE1B_BUNDLE,
            wave1b_archive=root / f"{WAVE1B_BUNDLE}.tar.gz",
            reconciliation=root / RECONCILIATION,
        )
        plan = build_plan(inputs)
    except (InputRefused, MappingRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4

    applied = None
    if args.apply and target is not None:
        from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused, apply_plan

        try:
            applied = apply_plan(plan, target)
        except ApplyRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 5

    try:
        report = build_aggregate_report(
            plan,
            mode="apply" if args.apply else "dry-run",
            target=target.redacted() if target else None,
        )
        if applied is not None:
            report["applied"] = applied.to_report()
        reject_path = write_reject_artifact(plan, root)
    except ReportRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 6

    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_console(report))
    if reject_path is not None:
        # The path, never the contents: the artifact is 0600 and holds addresses.
        print(f"\nrecipient-level rejects written privately to {reject_path.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
