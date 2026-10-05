"""Owner-run, before go-live (spec §7.1): rebuild each staged `gmail_message` payload from its raw
message and compare it with what the 2026-09 staging stored.

    cd apps/worker
    set -a; . <the cron's environment, outside the repository>; set +a
    uv run python scripts/payload_parity.py --out ~/data/origenlab-v2-migration/audits/gmail-capture-parity-<date>

Reads only: the staged records as `origenlab_worker` (read-only transaction) and each message
through the `gmail.readonly` client. Stdout carries counts and differing key names only, never a
value. `<out>/parity.csv` (mode 600, outside the repository) lists, per differing record, its dedupe
key and the differing key names - no values. Exit 1 if any record differs. Labels are not compared:
they change after staging (a star, a read mark).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from origenlab_worker.capture import PAYLOAD_KEYS, ParseFailed, build_capture, intake_skip_reason
from origenlab_worker.gmail_sync import MAILBOX_ADDRESS

#: Keys the capture computes from the raw message and the staging computed the same way.
COMPARED_KEYS = (
    "gmail_thread_id", "rfc822_message_id", "raw_sha256", "sender", "recipients", "subject_raw",
    "sent_at", "direction_hint", "tier", "quote_number_convention", "intake_class",
)
REPO_ROOT = Path(__file__).resolve().parents[3]


def compare_payload(staged: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> list[str]:
    """Names of what differs. Documents and proposals compare as *staged subset of rebuilt*: the
    staging kept only the attachments the coverage audit selected; the capture keeps every document."""
    diffs = [key for key in COMPARED_KEYS if staged.get(key) != rebuilt.get(key)]
    if set(staged) != PAYLOAD_KEYS:
        diffs.append("staged_key_set")
    staged_docs = {d.get("sha256") for d in staged.get("documents") or []}
    rebuilt_docs = {d.get("sha256") for d in rebuilt.get("documents") or []}
    if not staged_docs <= rebuilt_docs:
        diffs.append("documents")
    if not set(staged.get("proposed_quote_numbers") or ()) <= set(rebuilt.get("proposed_quote_numbers") or ()):
        diffs.append("proposed_quote_numbers")
    return diffs


def read_staged(conn) -> list[tuple[str, dict[str, Any]]]:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(
            "select dedupe_key, payload from evidence.source_record "
            "where kind = 'gmail_message' and payload->>'staging_source_record_sha256' is not null "
            "order by dedupe_key"
        )
        return [(key, dict(payload)) for key, payload in cur.fetchall()]


def main() -> int:
    from origenlab_worker.cli import config_from_env
    from origenlab_worker.database import open_worker_db
    from origenlab_worker.gmail_client import GmailNotFound, GmailReader

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out_dir = args.out.expanduser().resolve()
    if out_dir == REPO_ROOT or REPO_ROOT in out_dir.parents:
        raise SystemExit("--out must be outside the repository")
    config = config_from_env(os.environ, need_storage=False)
    with open_worker_db(config.database) as db:
        staged = read_staged(db.connection)
    gmail = GmailReader(config.gmail)
    per_key: Counter[str] = Counter()
    rows: list[tuple[str, str]] = []
    for dedupe_key, payload in staged:
        gmail_id = str(payload.get("gmail_message_id") or "")
        try:
            raw = gmail.raw(gmail_id)
        except GmailNotFound:
            per_key["gone_from_gmail"] += 1
            rows.append((dedupe_key, "gone_from_gmail"))
            continue
        skipped = intake_skip_reason(raw.label_ids)  # moved to Trash or Spam since it was staged
        if skipped is not None:
            per_key[f"now_{skipped}"] += 1
            rows.append((dedupe_key, f"now_{skipped}"))
            continue
        try:
            rebuilt = build_capture(
                raw.raw, gmail_message_id=gmail_id, gmail_thread_id=raw.thread_id,
                label_ids=raw.label_ids, label_names={}, internal_date_ms=raw.internal_date_ms,
                mailbox_address=MAILBOX_ADDRESS,
            ).payload
        except ParseFailed as exc:
            per_key[f"parse_failed_{exc.code}"] += 1
            rows.append((dedupe_key, f"parse_failed_{exc.code}"))
            continue
        diffs = compare_payload(payload, rebuilt)
        per_key.update(diffs)
        if diffs:
            rows.append((dedupe_key, " ".join(diffs)))
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(out_dir / "parity.csv", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([("dedupe_key", "differs"), *rows])
    print(f"staged={len(staged)} differing={len(rows)} " + " ".join(f"{k}={v}" for k, v in sorted(per_key.items())))
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
