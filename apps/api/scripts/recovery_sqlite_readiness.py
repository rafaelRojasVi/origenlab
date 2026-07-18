#!/usr/bin/env python3
"""Isolated recovery-readiness checks against an offline compact SQLite candidate.

Fail-closed admission requires:

* ``--confirm-offline-copy``
* completed compaction ``--manifest``
* non-production path (aliases/samefile/device+inode refused)
* no ``-wal``/``-shm``/``-journal`` (including zero-byte)
* fingerprint agreement with the manifest, frozen before/after checks

Opens through the same application connection factory used by the API
(``mode=ro&immutable=1`` + verified ``PRAGMA query_only=ON``).

Never starts production cutover, never opens production, never mutates Gmail or
Postgres. Does not repeat deep audit or full quick_check.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_API_ROOT = Path(__file__).resolve().parents[1]
_SRC = _API_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from origenlab_api.settings import Settings  # noqa: E402
from origenlab_api.sqlite_ro import (  # noqa: E402
    SqliteAdmissionError,
    admit_immutable_candidate,
    existing_companions,
    fingerprint_path,
    open_operator_sqlite,
)


EXIT_ADMISSION = 2
EXIT_MANIFEST = 3
EXIT_SCHEMA = 4
EXIT_QUERY = 5
EXIT_FINGERPRINT = 6


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_recovery_readiness(
    db: Path,
    *,
    confirm_offline_copy: bool,
    manifest_path: Path,
) -> dict[str, Any]:
    db = db.expanduser()
    manifest_path = manifest_path.expanduser()

    try:
        admission = admit_immutable_candidate(
            db,
            confirm_offline_copy=confirm_offline_copy,
            manifest_path=manifest_path,
        )
    except SqliteAdmissionError as exc:
        msg = str(exc)
        if "manifest" in msg.lower():
            raise SystemExit((EXIT_MANIFEST, msg)) from None
        raise SystemExit((EXIT_ADMISSION, msg)) from None

    fp_before = fingerprint_path(db)
    settings = Settings(
        sqlite_path=db,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        sqlite_compaction_manifest=manifest_path,
        api_backend="sqlite",
        postgres_url=None,
    )

    checks: dict[str, Any] = {}
    try:
        conn = open_operator_sqlite(db, settings=settings)
    except SqliteAdmissionError as exc:
        raise SystemExit((EXIT_ADMISSION, str(exc))) from None
    except sqlite3.Error as exc:
        raise SystemExit((EXIT_QUERY, f"sqlite open failed: {type(exc).__name__}")) from None

    try:
        qonly = int(conn.execute("PRAGMA query_only").fetchone()[0])
        checks["query_only"] = qonly
        if qonly != 1:
            raise SystemExit((EXIT_QUERY, "PRAGMA query_only is not active"))
        checks["encoding"] = str(conn.execute("PRAGMA encoding").fetchone()[0])
        checks["page_size"] = int(conn.execute("PRAGMA page_size").fetchone()[0])
        checks["user_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])
        checks["sqlite_open_uri"] = "mode=ro&immutable=1"

        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected_counts = admission["manifest"]["critical_table_counts"]
        for table in ("emails", "attachments", "email_mart_features"):
            present = table in tables
            checks[f"has_{table}"] = present
            if table == "emails" and not present:
                raise SystemExit((EXIT_SCHEMA, "candidate missing emails table"))
            if not present:
                checks[f"{table}_count"] = None
                continue
            try:
                count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            except sqlite3.Error as exc:
                raise SystemExit(
                    (EXIT_QUERY, f"count failed for {table}: {type(exc).__name__}")
                ) from None
            checks[f"{table}_count"] = count
            expected = expected_counts.get(table)
            if expected is not None and int(expected) != count:
                raise SystemExit(
                    (
                        EXIT_SCHEMA,
                        f"{table} count {count} != manifest evidence {expected}",
                    )
                )

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
            checks["attachments_sample"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM attachments WHERE email_id IN "
                    "(SELECT id FROM emails ORDER BY id DESC LIMIT 20)"
                ).fetchone()[0]
            )

        write_rejected = False
        try:
            conn.execute("CREATE TABLE origenlab_recovery_probe(x INTEGER)")
        except sqlite3.Error:
            write_rejected = True
        checks["write_rejected"] = write_rejected
        if not write_rejected:
            raise SystemExit((EXIT_QUERY, "immutable/query_only open failed to reject writes"))
    finally:
        conn.close()

    after_companions = existing_companions(db)
    fp_after = fingerprint_path(db)
    if after_companions:
        raise SystemExit(
            (EXIT_FINGERPRINT, f"companions appeared after open: {after_companions}")
        )
    if fp_after != fp_before:
        raise SystemExit((EXIT_FINGERPRINT, "candidate fingerprint changed during checks"))

    return {
        "schema_version": 1,
        "completed": True,
        "mode": "recovery_readiness_immutable_ro",
        "captured_at_utc": _iso_now(),
        "confirm_offline_copy": True,
        "database_basename": db.name,
        "sqlite_open_uri": "mode=ro&immutable=1",
        "pragma_query_only": True,
        "admission": {
            "admitted": True,
            "manifest_quick_check": admission["manifest"]["quick_check"],
            "manifest_emails_count": admission["manifest"]["critical_table_counts"].get(
                "emails"
            ),
        },
        "fingerprint_before": fp_before.to_dict(),
        "fingerprint_after": fp_after.to_dict(),
        "fingerprint_unchanged": True,
        "companions_before": [],
        "companions_after": after_companions,
        "checks": checks,
        "notes": [
            "In-process recovery readiness only; not a production cutover.",
            "Candidate opened via API connection factory (immutable + query_only).",
            "Point-in-time compact candidate cannot be swapped into live production.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True, help="Offline compact candidate")
    p.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Completed .compaction.manifest.json for the candidate",
    )
    p.add_argument(
        "--confirm-offline-copy",
        action="store_true",
        help="Acknowledge --db is a verified offline compact candidate",
    )
    p.add_argument("--json", action="store_true", help="Print sanitized JSON to stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_recovery_readiness(
            args.db.expanduser(),
            confirm_offline_copy=bool(args.confirm_offline_copy),
            manifest_path=args.manifest.expanduser(),
        )
    except SystemExit as exc:
        code = EXIT_ADMISSION
        message = str(exc)
        if isinstance(exc.code, tuple) and len(exc.code) == 2:
            code, message = exc.code
        elif isinstance(exc.code, int):
            code = exc.code
            message = message or f"exit {code}"
        print(f"ERROR: {message}", file=sys.stderr)
        return int(code)
    except SqliteAdmissionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ADMISSION

    # Privacy: never print absolute paths
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"completed={report['completed']}")
        print(f"database_basename={report['database_basename']}")
        print(f"emails_count={report['checks']['emails_count']}")
        print(f"fingerprint_unchanged={report['fingerprint_unchanged']}")
        print(f"write_rejected={report['checks']['write_rejected']}")
        print(f"query_only={report['checks']['query_only']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
