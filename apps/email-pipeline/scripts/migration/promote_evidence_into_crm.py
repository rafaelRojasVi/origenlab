#!/usr/bin/env python3
"""Promote loaded V2 evidence into `crm.*` under the conservative identity defaults.

**Dry-run by default.** Without `--apply` nothing is written; the tool reads the assertions,
computes the complete deterministic plan and prints PII-safe aggregate counts.

This is the step after `import_waves_into_v2.py`. That tool lands evidence in `evidence.*`
and `outbound.*` and writes nothing to `crm.*` on purpose. This one decides what that
evidence actually establishes about real people and organizations — and, far more often
than not, decides that it establishes nothing and routes the question to an operator.

What it promotes:

* every `contacted_address` assertion becomes a `crm.contact_point` — the channel, and only
  the channel. A recognised role local part (`ventas@`, `contacto@`, `compras@`, `info@` and
  their kin) is recorded as a `shared_mailbox`; everything else is `unattributed`.
* every `organization_name` assertion whose folded name is unique becomes a
  `crm.organization` with `kind = 'unknown'` and `confirmation = 'machine_proposed'`.

What it never does, in any mode:

* create a `crm.person`. The migration evidence carries no display name for anybody, and
  deriving one from an address local part would be an identity inference, not evidence.
* attach a contact point to an organization by email domain. `docs/DOMAIN.md` §2.2 makes a
  domain a routing hint and never an identity key on its own.
* merge two organizations or two people. Similar names go to review together, never into
  one row.
* create a consent, permission or subscription fact. `crm.contact_point` has no consent
  column: email presence is never marketing permission.
* open Gmail, Drive, Supabase, Render or Cloudflare; send email; or touch campaign or
  sender state.

Everything it declines to decide is recorded as `evidence.assertion.resolution =
'ambiguous'` with a mandatory note, which is the operator's review queue. Rows it does
create are `confirmation = 'machine_proposed'`, which is the same queue by another name.

Examples:
    # Dry run — no writes.
    uv run python scripts/migration/promote_evidence_into_crm.py \
        --database-url postgresql://postgres:postgres@127.0.0.1:54332/origenlab_dev

    # Apply.
    uv run python scripts/migration/promote_evidence_into_crm.py \
        --database-url postgresql://postgres:postgres@127.0.0.1:54332/origenlab_dev --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if str(REPO_SRC) not in sys.path:  # pragma: no cover - script bootstrap
    sys.path.insert(0, str(REPO_SRC))

from origenlab_email_pipeline.migration.v2_import.apply import (  # noqa: E402
    ApplyRefused,
    _require_psycopg,
)
from origenlab_email_pipeline.migration.v2_import.target import (  # noqa: E402
    TargetRefused,
    assert_local_target,
    neutralized_libpq_environment,
)
from origenlab_email_pipeline.migration.v2_promote.apply import (  # noqa: E402
    apply_promotion,
    read_assertions,
)
from origenlab_email_pipeline.migration.v2_promote.plan import build_plan  # noqa: E402
from origenlab_email_pipeline.migration.v2_promote.report import (  # noqa: E402
    build_report,
    render_console,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="a loopback PostgreSQL DSN carrying the Slice 0 schema and the loaded evidence",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the promotion. Without this flag the plan is computed and discarded.",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the report as JSON instead of text."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        target = assert_local_target(args.database_url)
    except TargetRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3

    psycopg = _require_psycopg()
    with neutralized_libpq_environment(), psycopg.connect(
        target.dsn, autocommit=True
    ) as conn:
        assertions = read_assertions(conn)

    plan = build_plan(assertions)

    applied = None
    if args.apply:
        try:
            applied = apply_promotion(plan, target)
        except ApplyRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 5

    report = build_report(
        plan,
        mode="apply" if args.apply else "dry-run",
        target=target.redacted(),
        applied=applied,
    )
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_console(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
