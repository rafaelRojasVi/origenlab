#!/usr/bin/env python3
"""Read-only identity audit of the staged quotation PDFs: what each document itself says.

    uv run python scripts/quote_document_identity.py
    uv run python scripts/quote_document_identity.py --focus 706492 706499 706528

Reads the quote-evidence staging and the verified document bytes it points at, extracts
each PDF's text locally (OCR only for PDFs without a text layer), and writes a JSON report,
a CSV report and one extracted-text file per document into a new output directory. It opens
no database, calls no Gmail or Drive API, creates no opportunity or quote, and never touches
the staging files or the decision ledger. See `origenlab_api.v2.quote_document_identity`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from origenlab_api.v2.quote_document_identity import (
    CSV_FIELDS,
    METHOD_PDFTOTEXT,
    METHOD_PYMUPDF,
    Extraction,
    audit_documents,
    build_report,
    extract_text,
    pymupdf_text,
    report_csv_rows,
)
from origenlab_api.v2.quote_evidence_review import load_staging

AUDITS = Path.home() / "data/origenlab-v2-migration/audits"
DEFAULT_STAGING = AUDITS / "quote-evidence-staging-20260924"
DEFAULT_OUT = AUDITS / "quote-document-identity-20260924"
REPORT_JSON = "quote_document_identity.json"
REPORT_CSV = "quote_document_identity.csv"
TEXTS_DIR = "texts"


def pdftotext_layout(path: Path) -> str | None:
    """`pdftotext -layout`: keeps the columns apart, so the issuer header never runs into the
    client block. None when poppler is not installed or the file cannot be read."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    done = subprocess.run(
        [exe, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True, timeout=120, check=False,
    )
    return done.stdout.decode("utf-8", errors="replace") if done.returncode == 0 else None


def tesseract_ocr(path: Path) -> str:
    """Render each page with PyMuPDF and OCR it with tesseract (spa+eng). Raises on failure."""
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError("tesseract is not installed")
    import fitz

    chunks: list[str] = []
    with fitz.open(path) as doc, tempfile.TemporaryDirectory() as tmp:
        for n, page in enumerate(doc):
            png = Path(tmp) / f"p{n}.png"
            page.get_pixmap(dpi=300).save(png)
            done = subprocess.run(
                [exe, str(png), "stdout", "-l", "spa+eng"],
                capture_output=True, timeout=300, check=False,
            )
            if done.returncode != 0:
                raise RuntimeError(done.stderr.decode("utf-8", errors="replace").strip()[:200])
            chunks.append(done.stdout.decode("utf-8", errors="replace"))
    return "\n".join(chunks)


TEXT_LAYERS = ((METHOD_PDFTOTEXT, pdftotext_layout), (METHOD_PYMUPDF, pymupdf_text))


def _write_private(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(data)


def run(staging_dir: Path, documents_root: Path, out_dir: Path, focus: list[int], *, ocr: bool = True) -> dict:
    staging_dir, out_dir = staging_dir.resolve(), out_dir.resolve()
    if out_dir == staging_dir or staging_dir in out_dir.parents:
        raise SystemExit("refusing: the output directory must be outside the staging directory")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"refusing: {out_dir} is not empty; reports are never overwritten")

    staging = load_staging(staging_dir)
    texts: dict[str, str] = {}

    def extractor(path: Path) -> Extraction:
        ex = extract_text(path, text_layers=TEXT_LAYERS, ocr=tesseract_ocr if ocr else None)
        texts[path.name] = ex.text
        return ex

    documents = audit_documents(staging, documents_root, extractor=extractor)
    report = build_report(
        staging, documents, generated_at=datetime.now(UTC).isoformat(), focus_email_ids=focus
    )

    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (out_dir / TEXTS_DIR).mkdir(mode=0o700)
    for d in documents.values():
        if d.stored_path and Path(d.stored_path).name in texts:
            _write_private(out_dir / TEXTS_DIR / f"{d.sha256}.txt", texts[Path(d.stored_path).name])
    _write_private(out_dir / REPORT_JSON, json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    rows = report_csv_rows(report)
    fd = os.open(out_dir / REPORT_CSV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    ap.add_argument(
        "--documents-root", type=Path, default=None,
        help="base for the records' stored_path (default: the staging directory's parent)",
    )
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--focus", type=int, nargs="*", default=[], help="email ids to print")
    ap.add_argument("--no-ocr", action="store_true", help="never OCR; textless PDFs stay insufficient")
    args = ap.parse_args(argv)

    report = run(
        args.staging_dir,
        args.documents_root or args.staging_dir.parent,
        args.out_dir,
        args.focus,
        ocr=not args.no_ocr,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1), file=sys.stderr)
    for f in report["focus"]:
        print(
            f"{f['email_id']}: {f.get('classification') or '—'}  "
            f"review={' '.join(f.get('review_reasons') or []) or 'none'}  "
            f"numbers={' '.join(f.get('document_quote_numbers') or []) or '—'}",
            file=sys.stderr,
        )
    print(args.out_dir, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
