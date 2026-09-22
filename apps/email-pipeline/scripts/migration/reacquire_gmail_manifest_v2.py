#!/usr/bin/env python3
"""Rewrite a version 1 Gmail staging manifest as version 2, from re-acquired label facts.

**This tool makes no network call and opens no database.** It reads two local files —
the version 1 manifest and an acquisition file recording, per message id, the labels the
mailbox really carries — and writes a third. Acquiring the labels is a separate,
read-only step an operator performs out-of-band; nothing here can send, label, archive,
trash or otherwise modify a message, because nothing here can reach a mailbox at all.

Why this exists: manifest version 2 requires `payload.intake_class` and
`payload.gmail_labels` on every Gmail record, so that a draft, a Spam or a Trash message
is refused on the evidence it carries rather than on the operator's say-so. The September
2026 manifest predates that rule, so the clean-room build could no longer replay it — and
a baseline that cannot be replayed is not a baseline. The two fields cannot be guessed,
so they are re-acquired and the file is rewritten with them.

Nothing else changes. Same external ids, same source URIs, same acquired_at, same
observations, in the same order. The sender and date the old manifest recorded are
re-checked against the mailbox, and any disagreement refuses the whole pass.

The output holds Gmail ids, subjects and addresses, so it must be written outside the
repository: this tool refuses a destination inside the working tree.

Examples:
    # Upgrade, into the private manifest directory the clean room stages from.
    uv run python scripts/migration/reacquire_gmail_manifest_v2.py \
        --manifest ~/data/origenlab-v2-local/evidence/gmail-2026-09-21-commercial.json \
        --acquisition ~/data/origenlab-v2-local/acquisition/gmail-2026-09-22-labels.json \
        --out ~/data/origenlab-v2-local/evidence/gmail-2026-09-21-commercial.v2.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if str(REPO_SRC) not in sys.path:  # pragma: no cover - script bootstrap
    sys.path.insert(0, str(REPO_SRC))

from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (  # noqa: E402
    ManifestRefused,
    parse_manifest,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.reacquire import (  # noqa: E402
    AcquisitionRefused,
    load_acquisition,
    summarize,
    upgrade_manifest,
)

#: The working tree. An output file under it would put Gmail ids, subjects and addresses
#: one `git add` away from a commit, so it is refused rather than warned about.
REPO_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--manifest", required=True, help="the version 1 manifest to upgrade")
    parser.add_argument(
        "--acquisition",
        required=True,
        help="a local JSON file recording the labels each message really carries",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="where to write the version 2 manifest. Must be outside the working tree.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite --out if it already exists. Without it an existing file refuses.",
    )
    parser.add_argument("--json", action="store_true", help="print the summary as JSON.")
    return parser


def _refuse(message: str) -> int:
    print(f"refused: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    out_path = Path(args.out).expanduser()
    try:
        resolved_out = out_path.resolve()
    except OSError as exc:  # pragma: no cover - unresolvable path
        return _refuse(f"--out cannot be resolved ({exc})")
    if resolved_out == REPO_ROOT or REPO_ROOT in resolved_out.parents:
        return _refuse(
            f"--out is inside the working tree ({REPO_ROOT}). A staging manifest carries "
            "Gmail ids, subjects and addresses and never lives in Git."
        )
    if out_path.exists() and not args.force:
        return _refuse(f"{out_path} already exists; re-run with --force to replace it")

    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_file():
        return _refuse(f"no such manifest file: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _refuse(f"{manifest_path}: unreadable manifest ({exc})")

    try:
        acquisition = load_acquisition(args.acquisition)
        upgraded = upgrade_manifest(raw, acquisition, source_path=str(manifest_path))
    except AcquisitionRefused as exc:
        return _refuse(str(exc))

    # The output is validated by the loader that will replay it, not by this tool's own
    # idea of what it produced. A file this step blessed and the staging step then
    # refuses is the failure that made the clean room unrebuildable in the first place.
    try:
        parse_manifest(upgraded, source_path=str(out_path))
    except ManifestRefused as exc:
        return _refuse(f"the upgraded manifest would not load: {exc}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".partial")
    tmp_path.write_text(json.dumps(upgraded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.chmod(0o600)
    tmp_path.replace(out_path)

    summary = summarize(upgraded)
    summary["out"] = str(out_path)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"wrote {out_path} (mode 0600)")
        print(f"  manifest_version : {summary['manifest_version']}")
        print(f"  records          : {summary['records']}")
        print(f"  observations     : {summary['observations']}")
        print(f"  intake_class     : {summary['intake_class']}")
        print(f"  label vocabulary : {', '.join(summary['label_vocabulary'])}")
        print(f"  acquired_at      : {summary['acquired_at']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
