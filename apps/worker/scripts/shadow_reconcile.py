"""Shadow week (spec §7.3): every message V1 captured from contacto@ since go-live is in
`comms.message`, or is explained.

    cd apps/worker
    set -a; . <the cron's environment, from your password manager, outside the repository>; set +a
    uv run python scripts/shadow_reconcile.py \
        --sqlite ~/data/origenlab-email/sqlite/emails.sqlite \
        --since 2026-10-12T15:00:00Z \
        --out ~/data/origenlab-v2-migration/audits/gmail-shadow-2026-10-19

Reads V1's SQLite read-only (`mode=ro`; V1's cron keeps writing it) and `comms.message` as
`origenlab_worker` inside a read-only transaction. Matches by normalized RFC 822 Message-ID.

Stdout: counts only. Files in `--out` (mode 600, never inside the repository):
  undated.csv             V1 id, folder, raw date_iso of rows with no usable Date that count (below).
  missing.csv             V1 id, folder, direction, date - no subject, no address.
  missing_message_ids.txt RFC 822 Message-IDs V1 has and comms.message lacks (a Message-ID carries a
                          domain, so it stays out of stdout and out of missing.csv).
  extra_message_ids.txt   Message-IDs in comms.message that no V1 row of the window carries.

The V2 side is read from `--since` minus 2 days (a Date header and Gmail's internal date can differ),
while V1 rows start exactly at `--since`. So `extra_in_v2` may include boundary rows: mail that
arrived in the 2 days before go-live, or whose V1 Date header falls before `--since`. Treat an
extra as real only if its message arrived after go-live; the rest is margin, not a defect.

Sent from the Gmail web UI: such a message starts as an autosaved draft, and history mode skips
drafts. If one is never captured it appears as `missing_sent`. V1 has no direction column, only the
`folder` it ingested, so the split is by folder: a folder matching V1's sent markers (Enviados,
Sent) is `sent`, an inbox folder is `received`, anything else (archive, unclassified) is `other`.
Rows without a Message-ID have no direction split of their own beyond the same folder rule. Read
`missing_sent` first: it is the signal that web-UI sends are not arriving. Exit 1 while anything
is unexplained.

A V1 row whose `date_iso` is missing or unparseable cannot be placed against go-live. It is `undated`
(printed, listed in `undated.csv`, exit 1) unless its folder explains it (draft, spam, trash) or its id
is at or below `--since-v1-id`. V1's `emails` table has no ingestion timestamp, so that id is the only
bound: right before `--init`, read V1's highest id (`select max(id) from emails` on the SQLite file,
read-only) and pass it as `--since-v1-id` on every run of the week. Without it every undated row of
the lane counts, and one historical row fails the gate forever. A `date_iso` without an offset is read
as UTC.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from origenlab_worker.capture import SKIPPED_INTAKE, normalize_rfc822_id
from origenlab_worker.gmail_sync import MAILBOX_ADDRESS
from origenlab_worker.v1_reuse import classify_intake_folder

V1_SOURCE_LIKE = f"gmail:{MAILBOX_ADDRESS}/%"
REPO_ROOT = Path(__file__).resolve().parents[3]
_SENT_MARKERS = ("enviados", "sent")


@dataclass(frozen=True)
class V1Row:
    email_id: int
    folder: str
    message_id: str | None
    date_iso: str | None


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def direction_of(folder: str | None) -> str:
    """`sent`, `received` or `other`, from V1's folder (V1 stores no direction)."""
    if classify_intake_folder(folder) != "primary_evidence":
        return "other"
    low = (folder or "").lower()
    if any(marker in low for marker in _SENT_MARKERS):
        return "sent"
    return "received"


def read_v1_rows(sqlite_path: Path, since: datetime) -> tuple[list[V1Row], list[V1Row]]:
    """`(rows, undated)`: V1 rows from contacto@'s Gmail lanes whose Date header is at or after
    `since`, and the rows that have no usable Date header at all.

    A Date without an offset is read as UTC. A missing or unparseable one cannot be placed against
    go-live, so it is returned as undated instead of silently leaving the gate. Which undated rows
    matter is decided by `undated_in_scope`.
    """
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select id, folder, message_id, date_iso from emails "
            "where source_file like ?",  # dates are judged in Python: no SQL string compare can place one
            (V1_SOURCE_LIKE,),
        ).fetchall()
    finally:
        conn.close()
    out: list[V1Row] = []
    undated: list[V1Row] = []
    for email_id, folder, message_id, date_iso in rows:
        when = _parse(date_iso)
        if when is None:
            undated.append(V1Row(int(email_id), folder or "", message_id, date_iso))
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= since:
            out.append(V1Row(int(email_id), folder or "", message_id, date_iso))
    return out, undated


def undated_in_scope(undated: Iterable[V1Row], since_v1_id: int | None) -> list[V1Row]:
    """The undated rows that could be mail from after go-live. V1's `emails` table has no ingestion
    timestamp, so the only bound is the id: with `since_v1_id` (V1's last `emails.id` at go-live) only
    ids above it count; without it every undated row counts. A row whose folder V1 maps to draft, spam
    or trash is explained by that folder and never counts."""
    return [
        row for row in undated
        if SKIPPED_INTAKE.get(classify_intake_folder(row.folder)) is None
        and (since_v1_id is None or row.email_id > since_v1_id)
    ]


def reconcile(v1_rows: Iterable[V1Row], v2_ids: set[str]) -> tuple[Counter[str], list[V1Row]]:
    """`present`, `explained_<draft|spam|trash>`, `no_message_id` or `missing` per V1 row."""
    counts: Counter[str] = Counter()
    missing: list[V1Row] = []
    for row in v1_rows:
        norm = normalize_rfc822_id(row.message_id)
        if norm is None:
            counts["no_message_id"] += 1
            missing.append(row)
        elif norm in v2_ids:
            counts["present"] += 1
        elif (reason := SKIPPED_INTAKE.get(classify_intake_folder(row.folder))) is not None:
            counts[f"explained_{reason}"] += 1
        else:
            counts["missing"] += 1
            missing.append(row)
    return counts, missing


def missing_by_direction(missing: Iterable[V1Row]) -> Counter[str]:
    return Counter(f"missing_{direction_of(row.folder)}" for row in missing)


def extra_ids(v1_rows: Iterable[V1Row], v2_ids: set[str]) -> list[str]:
    """Message-IDs in `comms.message` that no V1 row of the window carries."""
    known = {n for n in (normalize_rfc822_id(r.message_id) for r in v1_rows) if n}
    return sorted(v2_ids - known)


def read_v2_ids(conn, since: datetime) -> set[str]:
    """Normalized Message-IDs of the contacto@ mailbox from `since - 2 days` (a deliberate margin:
    the V2 window starts early, so extras at the boundary are expected)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(
            """
            select m.rfc822_message_id_norm
              from comms.message m join comms.mailbox b on b.id = m.mailbox_id
             where b.address_norm = %s and m.rfc822_message_id_norm is not null
               and m.internal_date >= %s
            """,
            (MAILBOX_ADDRESS, since - timedelta(days=2)),
        )
        return {row[0] for row in cur.fetchall()}


def _private_dir(out_dir: Path) -> Path:
    out_dir = out_dir.expanduser().resolve()
    if out_dir == REPO_ROOT or REPO_ROOT in out_dir.parents:
        raise SystemExit("--out must be outside the repository")
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return out_dir


def _private_file(path: Path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", newline="", encoding="utf-8")


def write_missing(out_dir: Path, rows: list[V1Row]) -> Path:
    path = _private_dir(out_dir) / "missing.csv"
    with _private_file(path) as fh:
        writer = csv.writer(fh)
        writer.writerow(["v1_email_id", "folder", "direction", "date_iso"])
        writer.writerows((r.email_id, r.folder, direction_of(r.folder), r.date_iso) for r in rows)
    return path


def write_undated(out_dir: Path, rows: list[V1Row]) -> Path:
    path = _private_dir(out_dir) / "undated.csv"
    with _private_file(path) as fh:
        writer = csv.writer(fh)
        writer.writerow(["v1_email_id", "folder", "date_iso"])
        writer.writerows((r.email_id, r.folder, r.date_iso) for r in rows)
    return path


def write_ids(out_dir: Path, name: str, ids: Iterable[str]) -> Path:
    path = _private_dir(out_dir) / name
    with _private_file(path) as fh:
        fh.writelines(f"{i}\n" for i in ids)
    return path


def main() -> int:
    from origenlab_worker.cli import config_from_env
    from origenlab_worker.database import open_worker_db

    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--since", required=True, help="go-live, ISO 8601 with offset (the --init run's time)")
    parser.add_argument("--since-v1-id", type=int, default=None,
                        help="V1's highest emails.id at go-live: undated rows at or below it are history")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    since = _parse(args.since)
    if since is None or since.tzinfo is None:
        raise SystemExit("--since must be ISO 8601 with an offset, e.g. 2026-10-12T15:00:00Z")
    _private_dir(args.out)  # refuse a path inside the repository before touching anything
    v1_rows, undated_rows = read_v1_rows(args.sqlite.expanduser(), since)
    undated_rows = undated_in_scope(undated_rows, args.since_v1_id)
    undated = len(undated_rows)
    with open_worker_db(config_from_env(os.environ, need_storage=False).database) as db:
        v2_ids = read_v2_ids(db.connection, since)
    counts, missing = reconcile(v1_rows, v2_ids)
    extra = extra_ids(v1_rows, v2_ids)
    path = write_missing(args.out, missing)
    write_undated(args.out, undated_rows)
    write_ids(args.out, "missing_message_ids.txt",
              sorted({n for r in missing if (n := normalize_rfc822_id(r.message_id))}))
    write_ids(args.out, "extra_message_ids.txt", extra)
    split = missing_by_direction(missing)
    print(f"v1_rows={len(v1_rows)} undated={undated} " + " ".join(f"{k}={v}" for k, v in sorted({**counts, **split}.items()))
          + f" extra_in_v2={len(extra)} (v2 window starts 2 days before --since; boundary extras are margin) detail={path.parent}")
    return 1 if missing or undated else 0


if __name__ == "__main__":
    sys.exit(main())
