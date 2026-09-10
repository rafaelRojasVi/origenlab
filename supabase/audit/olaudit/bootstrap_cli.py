"""The command line for the hosted role bootstrap. One mode, and it is a dry run.

    supabase/scripts/hosted_role_bootstrap.sh --environment staging --dry-run

**This tool opens no database connection, in any mode.** There is no `--apply`, no host argument, no
connection string, no target file and no credential input. It reads `supabase/hosted_roles.sql`,
proves statically that the file is nothing but role management over the four OrigenLab roles
(`olaudit.bootstrap`), and writes it to stdout. Applying it is a deliberate, separate operator
action performed with `psql` against a project the operator names themselves -- see
docs/OPERATIONS.md §4.3.

That is not a limitation to be lifted later by adding a flag. The reason the bootstrap is safe to
review is that the review artefact and the applied artefact are the same committed bytes; a tool
that could also apply them would need a target, a credential and a connection, and every one of
those is a thing this slice deliberately does not hold.

Two rules govern the output:

* **stdout is SQL and nothing else.** No banner, no timestamp, no comment, no classification --
  the bytes are the committed file, so `--dry-run > plan.sql` and a review of
  `supabase/hosted_roles.sql` cannot disagree, and the stream is safe to pipe.
* **stderr is the plan summary.** The environment classification, the durability posture and the
  role/membership matrix derived from the analysed file. It names no project reference, no
  organisation, no host name and no credential, because the process never has one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bootstrap, redact

EXIT_OK = 0
EXIT_REFUSED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hosted_role_bootstrap.sh",
        description=(
            "Emit the reviewed hosted role bootstrap SQL. Dry run only: this tool never connects "
            "to a database and never applies anything."
        ),
    )
    parser.add_argument(
        "--environment",
        required=True,
        metavar="CLASSIFICATION",
        help=(
            "the required, non-secret environment classification "
            f"({', '.join(bootstrap.APPROVED_ENVIRONMENTS)}). A classification, never a host "
            "name, project reference or connection string; it never reaches the emitted SQL."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="required, and the only mode. Present so the absence of an apply mode is explicit.",
    )
    return parser


def main(argv: list[str] | None = None, *, repo_root: Path | None = None,
         stdout=None, stderr=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    root = repo_root if repo_root is not None else Path(__file__).resolve().parents[3]

    try:
        environment = bootstrap.validate_environment(args.environment)
        sql, summary = bootstrap.dry_run(root, environment)
    except bootstrap.BootstrapError as exc:
        # Redacted on the way out even though the process holds no secret: the refusal path is
        # exactly where an operator's mistyped connection string would otherwise be echoed back.
        print(f"REFUSED: {redact.redact(str(exc))}", file=err)
        return EXIT_REFUSED

    for line in summary:
        print(line, file=err)
    out.write(sql)
    return EXIT_OK
