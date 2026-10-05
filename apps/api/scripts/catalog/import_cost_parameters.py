#!/usr/bin/env python3
"""Load confidential costing parameters (negotiated rates, cost profiles, markups, margin targets).

The values live in a private JSON file outside this public repository:

    {"parameters": [{"key": "<a catalog.cost_parameter key>", "value": <number>, "unit": "<text>",
                     "reason": "<why, as an operator would write it>"}]}

    uv run python scripts/catalog/import_cost_parameters.py plan --parameters <private.json> --out <dir>
    uv run python scripts/catalog/import_cost_parameters.py apply --target-dsn … --admin-dsn … \\
        --operator-email … --plan <dir>/plan.json --plan-sha256 <sha> --out <dir>
    … verify / rollback as every catalog importer (`_common.py`).

Each parameter is validated exactly as `POST /v2/commands/set-cost-parameter` validates it (the
closed key vocabulary of `catalog.cost_parameter`, a value >= 0, a reason) and written by that
command's SQL: under the key's advisory lock, a new append-only row with a wall-clock
`valid_from`, `set_by_operator_id` = the operator, and a `cost_parameter.set` event that names the
key — never the value. A key whose current value already equals the planned one is
`already_present`, so a rerun writes nothing. `unit` is checked and kept in the private plan only;
the table has no unit column.

**Nothing here prints a value**: stdout and stderr carry key names and counts only. The plan and
reports under `--out` do hold the values — that directory is refused inside the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import ValidationError  # noqa: E402

from origenlab_api.v2.catalog import importing  # noqa: E402
from origenlab_api.v2.catalog.keys import PARAMETER_KEYS, refuse_labdelivery  # noqa: E402
from origenlab_api.v2.catalog.routes import SetCostParameterBody  # noqa: E402

import _common  # noqa: E402

IMPORTER = "cost_parameters"


def parameter_items(document: Any) -> list[importing.PlanItem]:
    """One `set_cost_parameter` item per parameter; a refusal names the key, never the value."""
    if not isinstance(document, dict) or not isinstance(document.get("parameters"), list):
        raise ValueError('the parameters file must be {"parameters": [...]}')
    items: list[importing.PlanItem] = []
    seen: set[str] = set()
    for n, entry in enumerate(document["parameters"], 1):
        if not isinstance(entry, dict) or set(entry) != {"key", "value", "unit", "reason"}:
            raise ValueError(f"parameter #{n} must have exactly key, value, unit and reason")
        key = entry["key"]
        if key not in PARAMETER_KEYS:
            raise ValueError(f"parameter #{n}: {str(key)[:60]!r} is not a cost parameter key")
        if key in seen:
            raise ValueError(f"parameter {key} appears twice")
        seen.add(key)
        if isinstance(entry["value"], bool) or not isinstance(entry["value"], (int, float, str, Decimal)):
            raise ValueError(f"parameter {key}: the value is not a number")
        if not isinstance(entry["unit"], str) or not entry["unit"].strip():
            raise ValueError(f"parameter {key}: unit must be non-blank text")
        try:
            body = SetCostParameterBody(key=key, value=entry["value"], reason=entry["reason"])
        except ValidationError as exc:
            fields = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")})
            raise ValueError(f"parameter {key}: invalid {', '.join(fields) or 'entry'}") from None
        refuse_labdelivery(body.reason, entry["unit"])
        items.append({"action": "set_cost_parameter", "key": f"param:{key}",
                      "fields": {"key": key, "value": body.value, "unit": entry["unit"].strip(),
                                 "reason": body.reason}})
    if not items:
        raise ValueError("the parameters file lists no parameter")
    return items


def build_parameter_plan(path: Path) -> importing.Plan:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    refuse_labdelivery(str(path.resolve()), path.name, text)
    try:
        document = json.loads(text, parse_float=Decimal)
    except ValueError:
        raise ValueError("the parameters file is not JSON") from None
    return importing.build_plan(IMPORTER, [importing.input_record(path, "cost_parameters")], parameter_items(document))


def _add_plan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--parameters", type=Path, required=True, help="the private parameters JSON (outside the repo)")


def main(argv: list[str] | None = None) -> int:
    return _common.main(argv, importer=IMPORTER, description=__doc__ or "", add_plan_arguments=_add_plan_arguments,
                        make_plan=lambda args: build_parameter_plan(args.parameters), print_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
