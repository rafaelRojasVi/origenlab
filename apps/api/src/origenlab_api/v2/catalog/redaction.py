"""Remove cost-bearing fields for the viewer role (spec D8)."""
from __future__ import annotations

import copy
from typing import Any

COST_FIELDS = frozenset({"cost", "price", "list_price", "map_price", "discount_pct", "default_discount_pct",
                         "packing_pct", "margin", "markup", "unit_cost", "cost_history"})


def _strip(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in COST_FIELDS}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def redact_costs(payload: Any, role: str) -> Any:
    if role != "viewer":
        return payload
    return _strip(copy.deepcopy(payload))
