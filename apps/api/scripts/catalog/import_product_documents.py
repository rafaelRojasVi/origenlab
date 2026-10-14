#!/usr/bin/env python3
"""Record where catalog documents live in Google Drive: a product's own data sheet, a supplier's price list.

The input is a private manifest outside this public repository (it carries Drive file ids):

    {"documents": [
       {"kind": "price_list", "title": "<file name>", "drive_file_id": "<id>", "mime_type": "<type>",
        "as_of": "YYYY-MM-DD" | null, "supplier_name": "<the supplier organization's name>"},
       {"kind": "datasheet", "title": "<file name>", "drive_file_id": "<id>", "mime_type": "application/pdf",
        "model_codes": ["<model as printed>", ...]}]}

    uv run python scripts/catalog/import_product_documents.py plan --manifest <private.json> --out <dir>
    uv run python scripts/catalog/import_product_documents.py apply --target-dsn … --admin-dsn … \\
        --operator-email … --plan <dir>/plan.json --plan-sha256 <sha> --out <dir>
    … verify / rollback as every catalog importer (`_common.py`).

A data sheet becomes one `catalog.product_document` row per model code, matched to an active product
by `model_key` exactly; a price list one row for the supplier whose name matches (case-insensitive)
and that has cost observations. Nothing is guessed: no product, or two makers with the same code,
or no such supplier is `unmatched` — reported, nothing written. A quote PDF (`CN…`, or a filed
`NNNNN-26 rN — …`) is never a data sheet or a price list and is refused at plan time, as is any
Labdelivery-origin entry. Only the Drive file id is stored; the dashboard opens the file in Drive.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from origenlab_api.v2.catalog import importing  # noqa: E402
from origenlab_api.v2.catalog.keys import model_key, refuse_labdelivery  # noqa: E402

import _common  # noqa: E402

IMPORTER = "product_documents"
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,100}$")
_QUOTE_PDF = re.compile(r"^\s*(CN\s*0?\d|\d{4,6}[A-Z]?-\d{2}\s+r\d)", re.IGNORECASE)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def document_items(document: Any) -> list[importing.PlanItem]:
    """One `add_product_document` item per price list, and per model code of a data sheet."""
    if not isinstance(document, dict) or not isinstance(document.get("documents"), list):
        raise ValueError('the manifest must be {"documents": [...]}')
    items: list[importing.PlanItem] = []
    for n, e in enumerate(document["documents"], 1):
        if not isinstance(e, dict):
            raise ValueError(f"document #{n} is not an object")
        kind, title, file_id = e.get("kind"), str(e.get("title") or "").strip(), e.get("drive_file_id")
        if kind not in ("datasheet", "price_list"):
            raise ValueError(f"document #{n}: kind must be datasheet or price_list")
        if not title or len(title) > 255:
            raise ValueError(f"document #{n}: title must be 1–255 characters")
        if not isinstance(file_id, str) or not _DRIVE_ID.match(file_id):
            raise ValueError(f"document #{n}: drive_file_id has the wrong shape")
        if _QUOTE_PDF.search(title):
            raise ValueError(f"document #{n}: a quote PDF is never a data sheet or a price list")
        as_of = e.get("as_of")
        if as_of is not None and (not isinstance(as_of, str) or not _DATE.match(as_of)):
            raise ValueError(f"document #{n}: as_of must be YYYY-MM-DD or null")
        mime = str(e.get("mime_type") or "application/octet-stream").strip()
        refuse_labdelivery(title)
        base = {"kind": kind, "title": title, "drive_file_id": file_id, "mime_type": mime, "as_of": as_of}
        if kind == "price_list":
            name = str(e.get("supplier_name") or "").strip()
            if not name:
                raise ValueError(f"document #{n}: a price list names its supplier_name")
            refuse_labdelivery(name)
            items.append({"action": "add_product_document", "key": f"doc:price_list:{file_id}:{name.casefold()}",
                          "fields": {**base, "supplier_name": name}})
        else:
            codes = e.get("model_codes") or []
            if not isinstance(codes, list):
                raise ValueError(f"document #{n}: model_codes must be a list")
            keys = sorted({k for k in (model_key(str(c)) for c in codes) if k})
            if not keys:
                raise ValueError(f"document #{n}: a data sheet names at least one model code")
            for k in keys:
                items.append({"action": "add_product_document", "key": f"doc:datasheet:{file_id}:{k}",
                              "fields": {**base, "model_key": k}})
    if not items:
        raise ValueError("the manifest lists no document")
    return items


def build_document_plan(path: Path) -> importing.Plan:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    refuse_labdelivery(str(path.resolve()), path.name)
    try:
        document = json.loads(text)
    except ValueError:
        raise ValueError("the manifest is not JSON") from None
    return importing.build_plan(IMPORTER, [importing.input_record(path, "product_documents")],
                                document_items(document))


def _add_plan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", type=Path, required=True, help="the private Drive manifest JSON (outside the repo)")


def main(argv: list[str] | None = None) -> int:
    return _common.main(argv, importer=IMPORTER, description=__doc__ or "", add_plan_arguments=_add_plan_arguments,
                        make_plan=lambda args: build_document_plan(args.manifest), print_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
