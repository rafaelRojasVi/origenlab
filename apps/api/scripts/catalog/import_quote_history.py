#!/usr/bin/env python3
"""Load OrigenLab's quote history as cross-checked document lines (`evidence.document_line`).

The inputs are private files outside this public repository, produced by the quote-economics
study (`EXTRACT_SPEC.md` there): one AI extraction per quote PDF and the PDF's layout text.

    out/extract/<sha256>.json   {"sha256", "is_origenlab_issued_quote", "quote_number", "date", "client_type",
                                 "currency", "subtotal_net", "template", "lines": [{"item", "parent_item", "kind",
                                 "brand", "model", "description", "qty", "unit_price", "line_total", "optional"}],
                                 … client and condition fields, which are never read into the plan}
    txt/<sha256>.txt            pdftotext -layout of the same PDF

    uv run python scripts/catalog/import_quote_history.py plan --out ~/data/…/catalog-import-<ts> \\
        --extractions <dir or file.json> [--extractions …] --texts <txt dir>
    … apply / verify / rollback as every catalog importer (`_common.py`).

What each PDF becomes: one `evidence.source_record` (kind `quote_document`, `dedupe_key =
'quote_document:' || sha256`, `payload_sha256` = the PDF's sha256, review_status `pending`) whose
payload is `history_check.quote_document_payload` — quote number, ISO date, client *type*,
currency, net total, template, file hash — and its AI lines as `evidence.document_line`
(`extractor = 'ai_v1+template_v1'`), each with the `check_status` of the cross-check against the
layout text (`verified`, `disputed`, `single_source`). Lines the text has and the AI does not are
counted (`template_only_lines`), not written: a document line is what the AI read, checked.

`disputed.csv` (quote number, line, AI total, template total) is written to `--out` beside the plan
for the owner's review through `review-document-line`; it is never printed.

**No client identity.** The client's institution, unit, contact, city, conditions and the
extraction's free-text issues never enter the plan. A line string (brand, model, description,
item labels) that names the client, or holds an e-mail address or a phone number, is withheld
(written as null) and counted (`strings_withheld`).

Refusals (exit 11, naming the file's basename and the field): a malformed extraction, a date that
is neither ISO nor `DD-MM-YYYY` / `DD/MM/YYYY`, an extraction whose `sha256` is not its file name,
a missing text file, and anything of Labdelivery origin in a path, the extraction JSON or a string
the plan would write. A layout text Labdelivery issued — its name anywhere but the old OrigenLab
template's footer line (`keys.labdelivery_issued`, owner decision 2026-10-16) — is skipped and
counted (`skipped_labdelivery_letterhead`), never written; so is a document the extraction says
OrigenLab did not issue. That footer is never read into the plan: only the extraction's lines are,
each string still refused. Verified lines are counted by why they matched (`verified_<reason>`;
`evidence.document_line` has no column for it). A document already present (by `dedupe_key`) is never
touched; apply reports it `already_present`, or `present_different` when it says something else.

**Who writes what** (the Task 11 split): the runtime role `origenlab_api` holds no INSERT on
`evidence.source_record`, so the quote documents are inserted by `--admin-dsn` as `origenlab_owner`
in the manifest's own transaction, each payload naming the manifest (`origin_source_record_id`) —
that is how verify and rollback know which documents this plan wrote. Their lines are inserted by
`--target-dsn` (`origenlab_api`, under its grants and RLS) in the catalog transaction. A failed
apply leaves the documents with the manifest; a rerun adds their lines, rollback removes them.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from origenlab_api.v2.catalog import importing  # noqa: E402
from origenlab_api.v2.catalog.history_check import (  # noqa: E402
    LINE_KINDS,
    cross_check,
    document_lines,
    parse_template_lines,
    quote_document_payload,
)
from origenlab_api.v2.catalog.keys import (  # noqa: E402
    LabdeliveryRefused,
    labdelivery_issued,
    model_key,
    refuse_labdelivery,
)

import _common  # noqa: E402

IMPORTER = "quote_history"
EXTRACTOR = "ai_v1+template_v1"
DISPUTED_CSV = "disputed.csv"
DISPUTED_COLUMNS = ("quote_number", "line", "ai_total", "template_total")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _Bad(ValueError):
    """A malformed input; the message starts with the file's basename and the field."""


def _fail(name: str, field: str, why: str) -> _Bad:
    return _Bad(f"{name}: {field}: {why}")


# ------------------------------------------------------------------ values

def _refuse_labdelivery(name: str, *parts: str) -> None:
    """The Labdelivery refusal, naming the file (a hash-named basename) so it can be set aside."""
    try:
        refuse_labdelivery(*parts)
    except LabdeliveryRefused as exc:
        raise LabdeliveryRefused(f"{name}: {exc}") from None


# ------------------------------------------------------------------ one document

def _lines(name: str, extraction: dict[str, Any], counts: Counter[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(document lines as written, the AI lines as the cross-check reads them) — the shared
    `history_check.document_lines`, its errors prefixed with the file's basename."""
    try:
        written, checked, held = document_lines(extraction, extraction["currency"], extractor=EXTRACTOR)
    except ValueError as exc:
        raise _Bad(f"{name}: {exc}") from None
    counts["strings_withheld"] += held
    return written, checked


def _plain(value: Decimal) -> str:
    """An amount for the owner's CSV: no trailing zeros, no exponent."""
    return importing.decimal_text(value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize())


def _document(name: str, input_sha256: str, extraction: dict[str, Any], text: str, counts: Counter[str],
              disputed: list[dict[str, str]]) -> importing.PlanItem:
    sha = extraction["sha256"]
    try:
        payload = quote_document_payload(extraction, sha)
    except ValueError as exc:
        raise _Bad(f"{name}: {exc}") from None
    lines, ai = _lines(name, {**extraction, "currency": payload["currency"]}, counts)
    template = parse_template_lines(text) if payload["currency"] == "CLP" else []
    for c in cross_check(ai, template):
        if c.ai_index is None:
            counts["template_only_lines"] += 1
            continue
        line = lines[c.ai_index]
        line["check_status"] = c.check_status
        counts[f"lines_{c.check_status}"] += 1
        if c.reason is not None:
            counts[f"verified_{c.reason}"] += 1
        if c.check_status == "disputed":
            disputed.append({"quote_number": payload["printed_quote_number"] or "", "line": str(line["line_no"]),
                             "ai_total": _plain(c.ai_total), "template_total": _plain(c.template_total)})
    counts["lines"] += len(lines)
    if payload["quote_date"] is None:
        counts["documents_without_date"] += 1
    return {"action": "add_quote_document", "key": f"quote_document:{sha}",
            "source": {"input": input_sha256, "ref": "document"},
            "fields": {"dedupe_key": f"quote_document:{sha}", "file_sha256": sha, "payload": payload,
                       "lines": lines}}


def build_quote_history_plan(extractions: list[Path], text_dir: Path) -> tuple[importing.Plan, list[dict[str, str]]]:
    """The plan, and the disputed lines for `disputed.csv` (never part of the plan)."""
    hashed = sorted(((importing.input_record(Path(p), "quote_extraction"), Path(p)) for p in extractions),
                    key=lambda h: h[0]["file_sha256"])
    records: list[importing.PlanInput] = []
    items: list[importing.PlanItem] = []
    disputed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    counts: Counter[str] = Counter({k: 0 for k in (
        "lines", "lines_verified", "lines_disputed", "lines_single_source", "template_only_lines",
        "verified_model_total_match", "verified_item_total_match", "verified_total_match",
        "strings_withheld", "documents_without_date", "skipped_not_origenlab_quote",
        "skipped_labdelivery_letterhead")})
    for record, path in hashed:
        name = path.name
        _refuse_labdelivery(name, str(path.resolve()), name)
        if not _SHA256.match(path.stem):
            raise _fail(name, "sha256", "not the 64-hex sha256 the file is named by")
        text_path = text_dir / f"{path.stem}.txt"
        if not text_path.is_file():
            raise _fail(name, "text", "no layout text for this document in --texts")
        _refuse_labdelivery(text_path.name, str(text_path.resolve()), text_path.name)
        text = text_path.read_text(encoding="utf-8")
        records += [record, importing.input_record(text_path, "quote_text")]
        if labdelivery_issued(text):
            # Labdelivery's letterhead or issuer in the document itself: skipped, never written, and
            # its extraction never read (owner rulings 2026-10-05 and 2026-10-16 — the old OrigenLab
            # template's footer alone is not Labdelivery's). A written string of Labdelivery origin
            # in any other document still refuses.
            counts["skipped_labdelivery_letterhead"] += 1
            skipped.append({"path_sha256": record["path_sha256"], "reason": "labdelivery_letterhead"})
            continue
        raw = path.read_text(encoding="utf-8")
        _refuse_labdelivery(name, raw)
        try:
            extraction = json.loads(raw)
        except ValueError:
            raise _Bad(f"{name}: not JSON") from None
        if not isinstance(extraction, dict):
            raise _fail(name, "document", "not an object")
        if extraction.get("sha256") != path.stem:
            raise _fail(name, "sha256", "not the 64-hex sha256 the file is named by")
        issued = extraction.get("is_origenlab_issued_quote")
        if issued is False:
            counts["skipped_not_origenlab_quote"] += 1
            skipped.append({"path_sha256": record["path_sha256"], "reason": "not_origenlab_quote"})
            continue
        if issued is not True:
            raise _fail(name, "is_origenlab_issued_quote", "not true or false")
        items.append(_document(name, record["file_sha256"], extraction, text, counts, disputed))
    plan = importing.build_plan(IMPORTER, records, items, extra_counts=counts, skipped=skipped)
    return plan, disputed


def disputed_csv(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=DISPUTED_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


# ------------------------------------------------------------------ the CLI

def _expand(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        out += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return out


def _add_plan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--extractions", type=Path, action="append", default=[],
                   help="AI extraction JSON (<sha256>.json), or a directory of them")
    p.add_argument("--texts", type=Path, required=True, help="directory of the layout texts (<sha256>.txt)")


def _make_plan(args: argparse.Namespace) -> importing.Plan:
    paths = _expand(args.extractions)
    if not paths:
        raise ValueError("no quote extraction given")
    plan, disputed = build_quote_history_plan(paths, args.texts)
    # Refuse before writing anything: the CSV goes beside a plan that will be written.
    importing.refuse_labdelivery_in_plan(plan)
    _common.write_new(_common.prepare_out(args.out), DISPUTED_CSV, disputed_csv(disputed))
    return plan


def main(argv: list[str] | None = None) -> int:
    return _common.main(argv, importer=IMPORTER, description=__doc__ or "", add_plan_arguments=_add_plan_arguments,
                        make_plan=_make_plan)


if __name__ == "__main__":
    raise SystemExit(main())
