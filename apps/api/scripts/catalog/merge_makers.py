#!/usr/bin/env python3
"""Merge a maker loaded under a second name into the first (catalog cleanup, 2026-10-14).

The 2026-10-06 supplier-documents import created makers from the names printed on offers and
order confirmations instead of matching the price-list makers, so some brands appear twice and
some models exist twice. Input (a small JSON, no secret):

    {"merges": [{"keep": "<maker name to keep>", "drop": "<duplicate maker name>",
                 "rename_to": "<optional new display name for the kept maker>"}]}

    uv run python scripts/catalog/merge_makers.py plan --merges <file> --out <dir>
    … apply / verify as every catalog importer (`_common.py`); hosted mode as OPERATIONS §14.5.

Apply, per merge, in the plan's transaction: the kept maker is confirmed (a merge target is
confirmed) and optionally renamed; each active product of the duplicate maker either moves to the
kept maker or, when the kept maker already carries that model, is switched off and pointed at its
twin (`merged_into_product_id`); the duplicate maker is marked merged. Each change is an event.
Nothing is deleted and no cost observation is rewritten: the catalog reads follow the merge.
A name that does not match exactly one organization is `unmatched` and nothing changes.

**Rollback is refused**: a merge rewrites mutable rows rather than inserting owned ones, so the
undo is the pre-apply backup the release takes (or the dump of OPERATIONS §14.5).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from origenlab_api.v2.catalog import importing  # noqa: E402
from origenlab_api.v2.catalog.keys import refuse_labdelivery  # noqa: E402

import _common  # noqa: E402

IMPORTER = "maker_merges"
_FIELDS = {"keep", "drop", "rename_to"}


def merge_items(document: Any) -> list[importing.PlanItem]:
    if not isinstance(document, dict) or not isinstance(document.get("merges"), list):
        raise ValueError('the merges file must be {"merges": [...]}')
    items: list[importing.PlanItem] = []
    for n, e in enumerate(document["merges"], 1):
        if not isinstance(e, dict) or not {"keep", "drop"} <= set(e) or set(e) - _FIELDS:
            raise ValueError(f"merge #{n} must have keep and drop (and optionally rename_to), nothing else")
        keep, drop = str(e["keep"]).strip(), str(e["drop"]).strip()
        rename = str(e.get("rename_to") or "").strip() or None
        if not keep or not drop or keep.casefold() == drop.casefold():
            raise ValueError(f"merge #{n}: keep and drop must be two different, non-blank names")
        refuse_labdelivery(keep, drop, rename or "")
        fields: dict[str, Any] = {"keep": keep, "drop": drop}
        if rename:
            fields["rename_to"] = rename
        items.append({"action": "merge_maker", "key": f"merge:{drop.casefold()}->{keep.casefold()}", "fields": fields})
    if not items:
        raise ValueError("the merges file lists no merge")
    return items


def build_merge_plan(path: Path) -> importing.Plan:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except ValueError:
        raise ValueError("the merges file is not JSON") from None
    return importing.build_plan(IMPORTER, [importing.input_record(path, "maker_merges")], merge_items(document))


def _add_plan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--merges", type=Path, required=True, help="the merges JSON")


def main(argv: list[str] | None = None) -> int:
    return _common.main(argv, importer=IMPORTER, description=__doc__ or "", add_plan_arguments=_add_plan_arguments,
                        make_plan=lambda args: build_merge_plan(args.merges), print_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
