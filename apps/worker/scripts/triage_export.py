"""Owner-run: copy recent captured mail out of the hosted V2 database, read-only, so the triage can
be evaluated offline (`triage_eval.py`) before it is enabled.

    cd apps/worker
    set -a; . <the cron's environment, outside the repository>; set +a
    uv run python scripts/triage_export.py --out ~/data/origenlab-v2-local/triage-eval --since-days 30

Reads only, as `origenlab_worker` in a read-only transaction: `comms.message` (+ its source record,
thread, sender and recipients) for parsed messages, the `.eml` of each from Storage (`get`),
`catalog.product`, and the commercial cases a message could belong to — open, or closed in the last
120 days — with their institution, its domains, quote numbers, the addresses on the case's threads
and what the case asked for (`cases.jsonl`, for the context-matcher truth set). Writes
`<out>/messages.jsonl`, `<out>/catalog.jsonl`, `<out>/cases.jsonl` and
`<out>/eml/<source_record_id>.eml`, every file mode 600, and refuses an `<out>` inside the
repository: this is customer mail. Stdout carries counts only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from origenlab_worker.database import TRIAGE_REQUIRED_POLICIES, TRIAGE_REQUIRED_PRIVILEGES, open_verified_connection
from origenlab_worker.drive_filing import storage_key
from origenlab_worker.storage import S3EmlStore, StorageError
from origenlab_worker.triage import CASE_CARDS_SQL as _CASES, case_row
from origenlab_worker.triage_cli import triage_config_from_env

REPO_ROOT = Path(__file__).resolve().parents[3]

_MESSAGES = """
select sr.id::text, m.direction, m.labels, m.subject, m.eml_storage_path, m.internal_date,
       m.provider_thread_id,
       (select p.address_norm from comms.message_participant p
         where p.message_id = m.id and p.role = 'from' limit 1) as sender,
       coalesce((select array_agg(distinct p.address_norm) from comms.message_participant p
                  where p.message_id = m.id and p.role in ('to', 'cc')), '{}') as recipients
  from comms.message m
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id
 where sr.kind = 'gmail_message' and m.parse_status = 'parsed' and m.eml_storage_path is not null
   and m.internal_date >= now() - make_interval(days => %s)
 order by m.internal_date desc
 limit %s
"""
_CATALOG = "select id::text, model_number, model_key, coalesce(name_es, name), category_es from catalog.product"


def _private_write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def check_out_dir(out: Path) -> Path:
    resolved = out.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit("refused: --out must be outside the repository (this is customer mail)")
    return resolved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--since-days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=2000)
    args = ap.parse_args(argv)
    out = check_out_dir(args.out)
    (out / "eml").mkdir(parents=True, exist_ok=True, mode=0o700)
    config = triage_config_from_env({**os.environ, "ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED": "false"})
    store = S3EmlStore(config.storage.client())
    with open_verified_connection(config.database, application_name="origenlab-worker-triage-export",
                                  privileges=TRIAGE_REQUIRED_PRIVILEGES, policies=TRIAGE_REQUIRED_POLICIES) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(_MESSAGES, (max(1, args.since_days), max(1, args.limit)))
            messages = cur.fetchall()
            cur.execute(_CATALOG)
            catalog = cur.fetchall()
            cur.execute(_CASES)
            cases = cur.fetchall()
    rows, missing = [], 0
    for sid, direction, labels, subject, path, at, thread_id, sender, recipients in messages:
        try:
            raw = store.get(storage_key(path))
        except StorageError:
            missing += 1
            continue
        _private_write(out / "eml" / f"{sid}.eml", raw)
        rows.append({"source_record_id": sid, "direction": direction, "labels": list(labels or []),
                     "subject": subject, "internal_date": at.isoformat(), "thread_id": thread_id,
                     "sender": sender, "recipients": list(recipients or [])})
    _private_write(out / "messages.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode())
    _private_write(out / "catalog.jsonl", "".join(
        json.dumps({"id": i, "model_number": n, "model_key": k, "name": name, "category": cat}, ensure_ascii=False) + "\n"
        for i, n, k, name, cat in catalog).encode())
    _private_write(out / "cases.jsonl", "".join(json.dumps(case_row(c), ensure_ascii=False) + "\n" for c in cases).encode())
    print(json.dumps({"event": "triage_export", "messages": len(rows), "eml_missing": missing,
                      "catalog_products": len(catalog), "cases": len(cases)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
