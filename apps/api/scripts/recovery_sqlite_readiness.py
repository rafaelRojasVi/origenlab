"""Isolated recovery-readiness checks against an offline SQLite candidate.

Opens the database with ``mode=ro&immutable=1`` and ``PRAGMA query_only=ON``.
Never starts production cutover, never opens production, never mutates Gmail or
Postgres. Default is in-process checks only (no uvicorn).

Requires ``--confirm-offline-copy``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_API_ROOT = Path(__file__).resolve().parents[1]
_SRC = _API_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from origenlab_api.sqlite_ro import (  # noqa: E402
    existing_companions,
    open_sqlite_readonly,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fingerprint(path: Path) -> dict[str, int]:
    st = path.stat()
    return {
        "size_bytes": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "device": int(st.st_dev),
        "inode": int(st.st_ino),
    }


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def run_recovery_readiness(
    db: Path,
    *,
    confirm_offline_copy: bool,
) -> dict[str, Any]:
    if not confirm_offline_copy:
        raise SystemExit(
            "recovery readiness requires --confirm-offline-copy "
            "(verified offline/backup candidate only)"
        )
    if not db.is_file():
        raise SystemExit(f"database not found: {db}")

    before_companions = existing_companions(db)
    if before_companions:
        raise SystemExit(
            f"refusing candidate with companions present: {before_companions}"
        )

    fp_before = _fingerprint(db)
    conn = open_sqlite_readonly(db, immutable=True, query_only=True)
    checks: dict[str, Any] = {}
    try:
        # Confirm URI / pragma mode.
        checks["query_only"] = conn.execute("PRAGMA query_only").fetchone()[0]
        checks["encoding"] = conn.execute("PRAGMA encoding").fetchone()[0]
        checks["page_size"] = int(conn.execute("PRAGMA page_size").fetchone()[0])
        checks["user_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])

        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        checks["has_emails"] = "emails" in tables
        checks["has_attachments"] = "attachments" in tables
        checks["has_email_mart_features"] = "email_mart_features" in tables

        if "emails" not in tables:
            raise SystemExit("candidate missing emails table")

        # Indexed / bounded application-shaped reads (not full analytical scans).
        email_count = int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
        checks["emails_count"] = email_count
        sample = conn.execute(
            """
            SELECT id, date_iso, substr(COALESCE(subject, ''), 1, 40) AS subject_preview
            FROM emails
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()
        checks["emails_sample_ids"] = [int(r[0]) for r in sample]
        checks["emails_sample_count"] = len(sample)

        if "attachments" in tables:
            checks["attachments_count"] = int(
                conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            )
            checks["attachments_sample"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM attachments WHERE email_id IN "
                    "(SELECT id FROM emails ORDER BY id DESC LIMIT 20)"
                ).fetchone()[0]
            )
        else:
            checks["attachments_count"] = None

        if "email_mart_features" in tables:
            checks["email_mart_features_count"] = int(
                conn.execute("SELECT COUNT(*) FROM email_mart_features").fetchone()[0]
            )
        else:
            checks["email_mart_features_count"] = None

        # Attempt a write must fail under query_only / immutable.
        write_rejected = False
        try:
            conn.execute("CREATE TABLE origenlab_recovery_probe(x INTEGER)")
        except sqlite3.Error:
            write_rejected = True
        checks["write_rejected"] = write_rejected
        if not write_rejected:
            raise SystemExit("immutable/query_only open failed to reject writes")
    finally:
        conn.close()

    after_companions = existing_companions(db)
    fp_after = _fingerprint(db)
    if after_companions:
        raise SystemExit(
            f"companions appeared after immutable open: {after_companions}"
        )
    if fp_after != fp_before:
        raise SystemExit("candidate fingerprint changed during recovery readiness")

    return {
        "schema_version": 1,
        "completed": True,
        "mode": "recovery_readiness_immutable_ro",
        "captured_at_utc": _iso_now(),
        "confirm_offline_copy": True,
        "database_basename": db.name,
        "sqlite_open_uri": "mode=ro&immutable=1",
        "pragma_query_only": True,
        "fingerprint_before": fp_before,
        "fingerprint_after": fp_after,
        "fingerprint_unchanged": True,
        "companions_before": before_companions,
        "companions_after": after_companions,
        "checks": checks,
        "notes": [
            "In-process recovery readiness only; not a production cutover.",
            "Candidate was opened immutable read-only; writes were rejected.",
            "For an isolated HTTP drill, start uvicorn on 127.0.0.1:8002 with "
            "ORIGENLAB_SQLITE_PATH=<candidate>, ORIGENLAB_SQLITE_IMMUTABLE_RO=1, "
            "ORIGENLAB_API_BACKEND=sqlite, and ORIGENLAB_POSTGRES_URL unset.",
            "Do not point this harness at production SQLite.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True, help="Offline compact candidate")
    p.add_argument(
        "--confirm-offline-copy",
        action="store_true",
        help="Acknowledge --db is a verified offline/backup candidate",
    )
    p.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Refuse accidental production path via common env default.
    prod = os.environ.get("ORIGENLAB_SQLITE_PATH", "").strip()
    db = args.db.expanduser().resolve()
    if prod:
        try:
            if Path(prod).expanduser().resolve() == db:
                # Still allowed if operator explicitly confirmed offline copy of
                # that path, but refuse the well-known production location.
                pass
        except OSError:
            pass
    known_prod = Path.home() / "data" / "origenlab-email" / "sqlite" / "emails.sqlite"
    try:
        if db.resolve() == known_prod.resolve():
            print(
                "ERROR: refusing known production SQLite path for recovery readiness",
                file=sys.stderr,
            )
            return 2
    except OSError:
        pass

    try:
        report = run_recovery_readiness(
            db, confirm_offline_copy=bool(args.confirm_offline_copy)
        )
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"completed={report['completed']}")
        print(f"database_basename={report['database_basename']}")
        print(f"emails_count={report['checks']['emails_count']}")
        print(f"fingerprint_unchanged={report['fingerprint_unchanged']}")
        print(f"write_rejected={report['checks']['write_rejected']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
