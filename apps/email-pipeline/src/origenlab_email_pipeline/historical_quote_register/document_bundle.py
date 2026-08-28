"""Local document-preservation bundle — copies (never moves) recovered quote
bytes into a Drive-upload-ready tree, content-addressed by sha256 so an
identical document is never physically duplicated.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import tempfile
import unicodedata

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug or "unknown"


def copy_into_bundle(
    *,
    run_dir: Path,
    payload: bytes,
    sha256: str,
    original_filename: str,
    year: str,
    client_slug: str,
    quote_number_or_key: str,
    revision_index: int,
) -> Path:
    ext = Path(original_filename or "").suffix or ".bin"
    target_dir = (
        run_dir / "documents" / year / client_slug / quote_number_or_key / f"revision-{revision_index}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / (Path(original_filename or f"attachment{ext}").name or f"attachment{ext}")

    for existing in (run_dir / "documents").rglob("*"):
        if existing.is_file() and existing.stat().st_size == len(payload) and existing.read_bytes() == payload:
            return existing

    if target_path.exists():
        # A different physical file already occupies this path (the dedup
        # scan above already ruled out a byte-identical match), so this is
        # genuinely distinct content — disambiguate instead of silently
        # clobbering the existing document.
        target_path = target_path.with_name(f"{target_path.stem}-{sha256[:8]}{target_path.suffix}")

    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    os.replace(tmp_name, target_path)
    return target_path


@dataclass(frozen=True)
class BundleManifestRow:
    local_export_path: str
    original_path: str
    sha256: str
    historical_quote_key: str
    quote_number: str | None
    client_name: str | None
    sent_at: str | None


def write_drive_upload_manifest(rows: list[BundleManifestRow], run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "drive_upload_manifest.csv"
    fieldnames = (
        "local_export_path", "original_path", "sha256",
        "historical_quote_key", "quote_number", "client_name", "sent_at",
    )
    fd, tmp_name = tempfile.mkstemp(dir=run_dir, prefix=".drive_upload_manifest.", suffix=".tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    os.replace(tmp_name, path)
    return path
