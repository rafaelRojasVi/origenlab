"""Assembles one RegisterRow per send event and writes the two CSVs.
Pure data assembly — no DB access here, so these tests stay fast and the
column contract (matching the task's required CSV schema) stays in one place.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
import os
from pathlib import Path
import tempfile

FIELDNAMES = (
    "historical_quote_key", "quote_number", "quote_number_confidence",
    "client_name", "client_email", "client_domain",
    "sent_email_id", "sent_message_id", "sent_at", "sent_subject",
    "attachment_id", "attachment_filename", "attachment_sha256", "attachment_saved_path",
    "response_state", "first_client_reply_at", "latest_client_reply_at",
    "latest_reply_email_id", "latest_reply_subject",
    "outcome_state", "supersedes_historical_quote_key", "superseded_by_historical_quote_key",
    "days_without_reply",
    "source_quote_signal", "matching_method", "confidence", "review_required", "review_reason",
)


@dataclass(frozen=True)
class RegisterRow:
    historical_quote_key: str
    quote_number: str | None
    quote_number_confidence: str
    client_name: str | None
    client_email: str
    client_domain: str
    sent_email_id: int
    sent_message_id: str | None
    sent_at: str | None
    sent_subject: str | None
    attachment_id: int | None
    attachment_filename: str | None
    attachment_sha256: str | None
    attachment_saved_path: str | None
    response_state: str
    first_client_reply_at: str | None
    latest_client_reply_at: str | None
    latest_reply_email_id: int | None
    latest_reply_subject: str | None
    outcome_state: str
    supersedes_historical_quote_key: str | None
    superseded_by_historical_quote_key: str | None
    days_without_reply: int | None
    source_quote_signal: str
    matching_method: str
    confidence: str
    review_required: bool
    review_reason: str | None


assert tuple(f.name for f in fields(RegisterRow)) == FIELDNAMES, "RegisterRow fields must match FIELDNAMES 1:1"


def _write_csv_atomic(path: Path, rows: list[RegisterRow]) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def write_register_csvs(rows: list[RegisterRow], run_dir: Path) -> tuple[Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    main_path = run_dir / "historical_customer_quotes.csv"
    review_path = run_dir / "historical_customer_quotes_review.csv"
    _write_csv_atomic(main_path, rows)
    _write_csv_atomic(review_path, [r for r in rows if r.review_required])
    return main_path, review_path
