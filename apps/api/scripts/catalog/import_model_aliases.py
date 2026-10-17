#!/usr/bin/env python3
"""Record the other names a product's model is printed under (catalog.product_model_alias, DOMAIN.md §7 #59).

A quote line or an email prints «XQ100» while the catalog stores the maker's item number
(«30000001») as the model, or another spelling. An alias makes every read that links a line or a
text to a product by model key find it. The input is a private manifest outside this public
repository:

    {"aliases": [
       {"maker": "<the manufacturer organization's name>",
        "model_number": "<the product's model_number exactly as stored>",
        "alias": "<the model as printed>", "reason": "<why: where it was seen>"}]}

    uv run python scripts/catalog/import_model_aliases.py plan --manifest <private.json> --out <dir>
    uv run python scripts/catalog/import_model_aliases.py apply --target-dsn … --admin-dsn … \\
        --operator-email … --plan <dir>/plan.json --plan-sha256 <sha> --out <dir>
    … verify / rollback / --hosted-target as every catalog importer (`_common.py`).

The plan needs no database: it checks each entry's shape and refuses a manifest in which one maker
maps one alias to two models, or an alias that is the product's own model. At apply, the product is
the one product of the maker (by folded name, not merged) whose `model_number` is exactly the one
given; no maker, two makers of that name, no such product or a merged or inactive one is
`unmatched` — reported, nothing written. An alias the maker already gives to another product, or
that is a model of the same maker, is `present_different` and never written (the database refuses
it too); an alias this manifest wrote and an operator retired since is `kept_later_value`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from origenlab_api.v2.catalog import importing  # noqa: E402
from origenlab_api.v2.catalog.keys import model_key, refuse_labdelivery  # noqa: E402

import _common  # noqa: E402

IMPORTER = "model_aliases"
ALIAS_MAX = 120
REASON_MAX = 500


def _text(entry: dict[str, Any], name: str, n: int, *, limit: int, required: bool = True) -> str | None:
    value = entry.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"alias #{n}: {name} must be a non-empty string")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"alias #{n}: {name} is longer than {limit} characters")
    return value


def alias_items(document: Any) -> list[importing.PlanItem]:
    """One `add_model_alias` item per entry, keyed by maker, model_number and alias key."""
    if not isinstance(document, dict) or not isinstance(document.get("aliases"), list):
        raise ValueError('the manifest must be {"aliases": [...]}')
    items: list[importing.PlanItem] = []
    claimed: dict[tuple[str, str], str] = {}
    for n, e in enumerate(document["aliases"], 1):
        if not isinstance(e, dict):
            raise ValueError(f"alias #{n} is not an object")
        maker = _text(e, "maker", n, limit=255)
        model_number = _text(e, "model_number", n, limit=255)
        alias = _text(e, "alias", n, limit=ALIAS_MAX)
        reason = _text(e, "reason", n, limit=REASON_MAX, required=False)
        key = model_key(alias)
        if not key:
            raise ValueError(f"alias #{n}: the alias has no letter or digit")
        if key == model_key(model_number):
            raise ValueError(f"alias #{n}: the alias is the product's own model")
        refuse_labdelivery(maker, alias, reason)
        folded = importing.norm_name(maker)
        if not folded:
            raise ValueError(f"alias #{n}: the maker has no letter or digit")
        earlier = claimed.setdefault((folded, key), model_number)
        if earlier != model_number:
            raise ValueError(f"alias #{n}: the maker maps alias {key} to two models")
        items.append({"action": "add_model_alias", "key": f"alias:{folded}:{model_number}:{key}",
                      "fields": {"maker": maker, "model_number": model_number, "alias": alias, "reason": reason}})
    if not items:
        raise ValueError("the manifest lists no alias")
    return items


def build_alias_plan(path: Path) -> importing.Plan:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    refuse_labdelivery(str(path.resolve()), path.name)
    try:
        document = json.loads(text)
    except ValueError:
        raise ValueError("the manifest is not JSON") from None
    return importing.build_plan(IMPORTER, [importing.input_record(path, "model_aliases")], alias_items(document))


def _add_plan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", type=Path, required=True, help="the private alias manifest JSON (outside the repo)")


def main(argv: list[str] | None = None) -> int:
    return _common.main(argv, importer=IMPORTER, description=__doc__ or "", add_plan_arguments=_add_plan_arguments,
                        make_plan=lambda args: build_alias_plan(args.manifest))


if __name__ == "__main__":
    raise SystemExit(main())
