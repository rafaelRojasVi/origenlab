#!/usr/bin/env python3
"""Read-only mailbox source/intake inventory (SQLite → JSON; never writes).

Opens the archive with URI ``mode=ro`` and ``PRAGMA query_only=ON``. Runs no
VACUUM/ANALYZE/REINDEX/checkpoint and no mutation of any kind.

Emits aggregate counts per identity lane × intake class. **No email address,
subject, message-id or body ever appears in the output** — the audit is safe to
paste into a ticket or a status note.

    uv run python scripts/qa/audit_mailbox_intake_inventory.py
    uv run python scripts/qa/audit_mailbox_intake_inventory.py --db /path/to/emails.sqlite

Intake rules (what each class is allowed to do) are documented in
``origenlab_email_pipeline.qa.mailbox_intake_inventory``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.qa.mailbox_intake_inventory import (  # noqa: E402
    AUTOMATIC_INTAKE_CLASSES,
    REPLAY_CANDIDATE_CLASSES,
    connect_read_only,
    build_inventory,
)


def _resolve_db(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    from origenlab_email_pipeline.config import load_settings

    return Path(load_settings().resolved_sqlite_path())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None, help="SQLite archive (default: configured path)")
    ap.add_argument("--json-out", type=Path, default=None, help="Also write the report to this path")
    args = ap.parse_args(argv)

    db = _resolve_db(args.db)
    if not db.is_file():
        print(f"SQLite archive not found: {db}", file=sys.stderr)
        return 2

    conn = connect_read_only(db)
    try:
        rows_before = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        inventory = build_inventory(conn)
        rows_after = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    finally:
        conn.close()

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "database": str(db),
        "read_only": True,
        # The archive is written by a cron ingest; an unequal bracket means the
        # inventory was taken across a write and the counts are not a clean snapshot.
        "rows_at_start": rows_before,
        "rows_at_end": rows_after,
        "stable_snapshot": rows_before == rows_after,
        "automatic_intake_classes": sorted(AUTOMATIC_INTAKE_CLASSES),
        "replay_candidate_classes": sorted(REPLAY_CANDIDATE_CLASSES),
        **inventory.as_dict(),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if report["stable_snapshot"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
