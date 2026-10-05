"""The catalog importers' plan model — pure: no database, no file system beyond hashing an input.

An importer turns private inputs (price lists, supplier documents, quote PDFs, cost parameters)
into a **plan**: a list of items, each one action on one natural key with the fields to write.
The plan is the thing the owner reviews and approves by its sha256; apply, verify and rollback
(`apps/api/scripts/catalog/_common.py`) refuse any plan whose bytes do not hash to that value.

**A plan never carries a local path.** Each input is recorded as the sha256 of its resolved path
and of its bytes, so a plan can be shared without saying where the private files live. Money is
serialised as strings (`Decimal` in, `str` out), never as JSON floats.

**Nothing of Labdelivery origin is planned** (spec S5): every string an item would write is
checked by `refuse_labdelivery_in_plan`, at plan time and again before apply.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, TypedDict

from origenlab_api.v2.catalog.keys import refuse_labdelivery

PLAN_VERSION = 1
#: Dependency order: an item may only reference items of an earlier action.
ACTION_ORDER = ("create_org", "create_product", "add_observation", "set_terms", "set_cost_parameter")
Action = Literal["create_org", "create_product", "add_observation", "set_terms", "set_cost_parameter"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NON_ALNUM = re.compile(r"[\W_]+")


class PlanRefused(ValueError):
    """The plan is not the approved one, or is not a plan this build can apply."""


class PlanItem(TypedDict):
    action: Action
    key: str
    fields: dict[str, Any]


class PlanInput(TypedDict):
    path_sha256: str
    file_sha256: str
    kind: str


class Plan(TypedDict, total=False):
    importer: str
    version: int
    inputs: list[PlanInput]
    items: list[PlanItem]
    counts: dict[str, int]
    conflicts: list[str]


# ------------------------------------------------------------------ hashing and serialisation

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_record(path: Path, kind: str) -> PlanInput:
    """An input as the plan records it: hashes only, never the path itself."""
    resolved = Path(path).resolve()
    return {"path_sha256": sha256_bytes(str(resolved).encode("utf-8")),
            "file_sha256": sha256_bytes(resolved.read_bytes()), "kind": kind}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_text(value)
    raise TypeError(f"not serialisable in a plan: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def plan_bytes(plan: Plan) -> bytes:
    """The bytes written to `plan.json`, and the ones its sha256 is taken over."""
    return (canonical_json(plan) + "\n").encode("utf-8")


def plan_sha256(plan: Plan) -> str:
    return sha256_bytes(plan_bytes(plan))


def decimal_text(value: Decimal) -> str:
    """A plain decimal string (no exponent), as written to a plan."""
    return format(value, "f")


def to_decimal(value: Any) -> Decimal | None:
    """A plan or parser value as a Decimal; None and blank stay None. Floats go through `str`."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError("not a number") from exc


def manifest_dedupe_key(plan_sha: str) -> str:
    return f"catalog-import:{plan_sha}"


def manifest_payload(plan: Plan, plan_sha: str) -> dict[str, Any]:
    """What the manifest source record carries: counts and input hashes — never items or values."""
    return {"importer": plan["importer"], "version": plan["version"], "plan_sha256": plan_sha,
            "counts": plan["counts"], "inputs": plan["inputs"]}


# ------------------------------------------------------------------ names

def norm_name(name: str) -> str:
    """An organization's display name folded for matching: accents, case, punctuation, spacing."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", stripped.casefold()).strip()


# ------------------------------------------------------------------ building and loading

def build_plan(importer: str, inputs: Sequence[PlanInput], items: Iterable[PlanItem],
               extra_counts: Mapping[str, int] | None = None) -> Plan:
    """Order the items by action, merge exact duplicates, keep the first of conflicting ones.

    Two items with one key and the same fields are one item (`duplicates_merged`). Two with one
    key and different fields keep the first in input order and are reported by key only
    (`conflicting_duplicates`, `conflicts`) — the values never enter the report.
    """
    seen: dict[str, PlanItem] = {}
    merged = 0
    conflicts: list[str] = []
    for item in items:
        if item["action"] not in ACTION_ORDER:
            raise PlanRefused(f"unknown plan action {item['action']!r}")
        key = item["key"]
        if key in seen:
            if canonical_json(seen[key]) == canonical_json(item):
                merged += 1
            else:
                if key not in conflicts:
                    conflicts.append(key)
            continue
        seen[key] = {"action": item["action"], "key": key, "fields": dict(item["fields"])}
    ordered = sorted(seen.values(), key=lambda i: (ACTION_ORDER.index(i["action"]), i["key"]))
    counts: dict[str, int] = {a: 0 for a in ACTION_ORDER}
    for item in ordered:
        counts[item["action"]] += 1
    counts = {k: v for k, v in counts.items() if v}
    counts["duplicates_merged"] = merged
    counts["conflicting_duplicates"] = len(conflicts)
    for k, v in (extra_counts or {}).items():
        counts[k] = int(v)
    plan: Plan = {"importer": importer, "version": PLAN_VERSION, "inputs": list(inputs), "items": ordered,
                  "counts": counts, "conflicts": sorted(conflicts)}
    # Round-trip through JSON so the plan in memory is exactly the plan on disk (Decimal → str).
    return json.loads(plan_bytes(plan))


def load_plan(data: bytes, expected_sha256: str, *, importer: str | None = None) -> Plan:
    """The plan in `data`, only if its bytes hash to the approved value."""
    if not _SHA256.match(expected_sha256 or ""):
        raise PlanRefused("--plan-sha256 must be 64 lowercase hex characters")
    actual = sha256_bytes(data)
    if actual != expected_sha256:
        raise PlanRefused(f"plan sha256 is {actual}, expected {expected_sha256}")
    try:
        plan = json.loads(data)
    except ValueError as exc:
        raise PlanRefused("the plan is not JSON") from exc
    if not isinstance(plan, dict) or plan.get("version") != PLAN_VERSION:
        raise PlanRefused(f"the plan is not a version {PLAN_VERSION} catalog import plan")
    if importer is not None and plan.get("importer") != importer:
        raise PlanRefused(f"the plan is for importer {plan.get('importer')!r}, not {importer!r}")
    for item in plan.get("items", []):
        if item.get("action") not in ACTION_ORDER:
            raise PlanRefused(f"unknown plan action {item.get('action')!r}")
    return plan  # type: ignore[return-value]


# ------------------------------------------------------------------ Labdelivery

def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def refuse_labdelivery_in_plan(plan: Plan) -> None:
    """Every key and every string field an item would write (spec S5)."""
    for item in plan.get("items", []):
        refuse_labdelivery(item["key"], *_strings(item["fields"]))


# ------------------------------------------------------------------ verify

def _same(planned: Any, actual: Any) -> bool:
    if planned is None or actual is None:
        return planned is None and actual is None
    if isinstance(actual, Decimal) or isinstance(planned, Decimal):
        try:
            return to_decimal(planned) == to_decimal(actual)
        except ValueError:
            return False
    return planned == actual


def compare(planned: Mapping[str, Any], actual: Mapping[str, Any] | None,
            fields: Iterable[str]) -> tuple[str, list[str]]:
    """`missing` (no row), `present` (every named field equal) or `different` (and which fields)."""
    if actual is None:
        return "missing", []
    diffs = [f for f in fields if not _same(planned.get(f), actual.get(f))]
    return ("different" if diffs else "present"), diffs
