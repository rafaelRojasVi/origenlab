"""Deterministic field-path / type matrix and contract comparison."""

from __future__ import annotations

from typing import Any, Literal

JsonType = Literal["object", "array", "string", "integer", "number", "boolean", "null"]
Handling = Literal[
    "yes",
    "digest-only",
    "intentionally_ignored",
    "unsupported",
]
DiffClass = Literal[
    "compatible_optional_field",
    "compatible_unknown_field_digest_only",
    "type_drift",
    "envelope_drift",
    "required_field_missing",
    "parser_rejection",
    "source_semantics_drift",
    "documentation_only_difference",
    "matched",
]


def json_type_name(value: Any) -> JsonType:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def iter_field_paths(
    payload: Any, *, prefix: str = "$"
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        t = json_type_name(node)
        row: dict[str, Any] = {
            "path": path,
            "json_type": t,
            "nullable": t == "null",
            "repeated": False,
            "min_cardinality": None,
            "max_cardinality": None,
        }
        if isinstance(node, list):
            row["repeated"] = True
            row["min_cardinality"] = len(node)
            row["max_cardinality"] = len(node)
            rows.append(row)
            for i, item in enumerate(node[:5]):  # bound expansion
                walk(item, f"{path}[{i}]")
            if node:
                # Representative element path without index for aggregation.
                walk(node[0], f"{path}[]")
            return
        if isinstance(node, dict):
            rows.append(row)
            for key in sorted(node.keys()):
                walk(node[key], f"{path}.{key}")
            return
        rows.append(row)

    walk(payload, prefix)
    # Deduplicate by path keeping first.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["path"] in seen:
            continue
        seen.add(r["path"])
        out.append(r)
    return sorted(out, key=lambda r: r["path"])


# Known PR5B handling map (conservative).
_PR5B_HANDLING: dict[str, Handling] = {
    "$.Cantidad": "yes",
    "$.FechaCreacion": "digest-only",
    "$.Version": "digest-only",
    "$.Listado": "yes",
    "$.Listado[].CodigoExterno": "yes",
    "$.Listado[].Nombre": "yes",
    "$.Listado[].CodigoEstado": "yes",
    "$.Listado[].Estado": "yes",
    "$.Listado[].FechaCierre": "yes",
    "$.Listado[].Items": "yes",
    "$.Listado[].Items.Listado": "yes",
    "$.Listado[].Items.Cantidad": "digest-only",
    "$.releases": "yes",
    "$.records": "yes",
    "$.records[].releases": "yes",
    "$.records[].compiledRelease": "yes",
    "$.uri": "digest-only",
    "$.publisher": "digest-only",
    "$.publishedDate": "digest-only",
    "$.version": "digest-only",
}


def classify_pr5b_handling(path: str) -> Handling:
    if path in _PR5B_HANDLING:
        return _PR5B_HANDLING[path]
    # Strip concrete indexes for lookup.
    generic = path
    for i in range(10):
        generic = generic.replace(f"[{i}]", "[]")
    if generic in _PR5B_HANDLING:
        return _PR5B_HANDLING[generic]
    if path.startswith("$.Listado") or path.startswith("$.releases") or path.startswith(
        "$.records"
    ):
        return "digest-only"
    return "unsupported"


def build_field_type_matrix(payload: Any) -> list[dict[str, Any]]:
    rows = iter_field_paths(payload)
    for row in rows:
        handling = classify_pr5b_handling(row["path"])
        row["pr5b_handling"] = handling
        row["represented_in_pr5b_model"] = handling
    return rows


def compare_contracts(
    *,
    live_matrix: list[dict[str, Any]],
    reference_matrix: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    live_by = {r["path"]: r for r in live_matrix}
    ref_by = {r["path"]: r for r in reference_matrix}
    paths = sorted(set(live_by) | set(ref_by))
    diffs: list[dict[str, Any]] = []
    for path in paths:
        live = live_by.get(path)
        ref = ref_by.get(path)
        if live and ref:
            if live["json_type"] == ref["json_type"]:
                classification: DiffClass = "matched"
            else:
                classification = "type_drift"
            diffs.append(
                {
                    "path": path,
                    "live_type": live["json_type"],
                    "reference_type": ref["json_type"],
                    "pr5b_handling": live.get("pr5b_handling"),
                    "classification": classification,
                }
            )
        elif live and not ref:
            handling = live.get("pr5b_handling")
            if handling in {"digest-only", "intentionally_ignored", "unsupported"}:
                classification = "compatible_unknown_field_digest_only"
            else:
                classification = "compatible_optional_field"
            diffs.append(
                {
                    "path": path,
                    "live_type": live["json_type"],
                    "reference_type": None,
                    "pr5b_handling": handling,
                    "classification": classification,
                }
            )
        else:
            diffs.append(
                {
                    "path": path,
                    "live_type": None,
                    "reference_type": ref["json_type"] if ref else None,
                    "pr5b_handling": classify_pr5b_handling(path),
                    "classification": "required_field_missing"
                    if path in {"$.Cantidad", "$.Listado", "$.releases", "$.records"}
                    else "documentation_only_difference",
                }
            )
    return diffs
