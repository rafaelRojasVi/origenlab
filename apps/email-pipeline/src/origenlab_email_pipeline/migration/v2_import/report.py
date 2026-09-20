"""Reporting, split by who is allowed to see what.

Two outputs, deliberately different in kind:

* the **aggregate report** — counts, classifications, artifact hashes and structural gaps.
  It contains no address, no domain, no personal name and no private path, so it is safe on
  a console, in a test, in CI and in a commit message.
* the **reject artifact** — the recipient-level rows the mapping could not use. It carries
  addresses, so it is written `0600` into the operator's private migration root outside
  Git, and never printed.

The split is enforced here rather than by convention: :func:`build_aggregate_report`
cannot emit a recipient-level value because it never receives one, and
:func:`assert_report_is_pii_safe` re-checks the finished structure before it is returned.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.migration.bundle import (
    assert_output_root_private,
    write_private_bytes,
)
from origenlab_email_pipeline.migration.v2_import.plan import ImportPlan, Reject

#: Anything shaped like an email address must never reach an aggregate report.
_ADDRESS_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class ReportRefused(Exception):
    """A report failed its own privacy check, or could not be written privately."""


def build_aggregate_report(plan: ImportPlan, *, mode: str, target: str | None) -> dict[str, Any]:
    """Build the PII-safe aggregate report for one run.

    Args:
        plan: the mapped plan.
        mode: ``'dry-run'`` or ``'apply'``.
        target: the redacted database target, or ``None`` for a dry run.

    Returns:
        A JSON-serializable report carrying counts only.
    """
    report: dict[str, Any] = {
        "tool": "migration.v2_import",
        "mode": mode,
        "target": target,
        "artifacts": [identity for identity in _artifact_entries(plan)],
        "counts": dict(sorted(plan.counts.items())),
        "classifications": dict(sorted(plan.classifications.items())),
        "planned_rows": {
            name: {
                "rows": table.count,
                "applicable": table.applicable,
                "blocked_by": table.blocked_by,
            }
            for name, table in sorted(plan.tables.items())
        },
        "structural_gaps": [
            {
                "key": gap.key,
                "table": gap.table,
                "requirement": gap.requirement,
                "blocked_rows": gap.blocked_rows,
                "why_not_representable": gap.why_not_representable,
            }
            for gap in plan.gaps
        ],
        "rejects": {
            "total": len(plan.rejects),
            "by_reason": _reject_histogram(plan.rejects),
        },
        "invariants": {
            "crm_rows_planned": plan.counts.get("crm_rows", 0),
            "no_person_created_from_an_address": True,
            "no_prospect_created_from_a_recipient": True,
            "prior_contact_rows_all_marketing_purpose": True,
            "promotion_to_crm_is_an_operator_command": True,
        },
        "reconciliation_source": {
            "tool": plan.reconciliation.get("tool"),
            "tool_version": plan.reconciliation.get("tool_version"),
            "normalizer": plan.reconciliation.get("normalizer"),
        },
    }
    assert_report_is_pii_safe(report)
    return report


def _artifact_entries(plan: ImportPlan) -> Iterable[dict[str, Any]]:
    """Artifact identities, taken from the source records the plan built."""
    for row in plan.table("evidence.source_record").rows:
        payload = json.loads(row.columns["payload"])
        yield {k: v for k, v in payload.items() if k != "path"}


def _reject_histogram(rejects: Iterable[Reject]) -> dict[str, int]:
    """Count rejects by ``origin`` and ``reason`` — never by address."""
    histogram: dict[str, int] = {}
    for reject in rejects:
        key = f"{reject.origin}: {reject.reason}"
        histogram[key] = histogram.get(key, 0) + 1
    return dict(sorted(histogram.items()))


def assert_report_is_pii_safe(report: dict[str, Any]) -> None:
    """Refuse a report that carries anything shaped like an address.

    Raises:
        ReportRefused: a string anywhere in the structure matches an address shape.
    """
    def walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            if _ADDRESS_SHAPE.search(node):
                raise ReportRefused(
                    f"the aggregate report carries an address-shaped value at {path}; "
                    "recipient-level data belongs only in the private reject artifact"
                )
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(key, f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(report, "report")


def write_reject_artifact(plan: ImportPlan, output_dir: Path, *, stamp: str | None = None) -> Path | None:
    """Write recipient-level rejects to a private `0600` file, or return ``None``.

    Args:
        plan: the mapped plan.
        output_dir: the operator's private migration root. It is refused if it is a
            symlink, is not owned by the running user, or is group- or world-writable.
        stamp: an explicit UTC stamp for the filename. Supplied by tests so the path is
            deterministic; production passes ``None`` and the current instant is used.

    Returns:
        The path written, or ``None`` when there were no rejects.

    Raises:
        ReportRefused: the output root is not private.
    """
    if not plan.rejects:
        return None
    try:
        root = assert_output_root_private(output_dir)
    except Exception as exc:  # the bundle module raises its own OutputPathError
        raise ReportRefused(f"reject artifact refused: {exc}") from exc

    when = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"{when}_v2_import_rejects.jsonl"
    payload = "".join(
        json.dumps(
            {
                "origin": reject.origin,
                "reason": reject.reason,
                "address": reject.address,
                "detail": reject.detail,
            },
            sort_keys=True,
        )
        + "\n"
        for reject in plan.rejects
    ).encode("utf-8")
    return write_private_bytes(target, payload)


def render_console(report: dict[str, Any]) -> str:
    """Render the aggregate report for a terminal. Carries no address by construction."""
    lines = [
        f"mode: {report['mode']}",
        f"target: {report['target'] or '(none — dry run)'}",
        "",
        "counts:",
    ]
    lines += [f"  {k:46s} {v}" for k, v in report["counts"].items()]
    lines += ["", "planned rows:"]
    for name, entry in report["planned_rows"].items():
        state = "applicable" if entry["applicable"] else f"BLOCKED ({entry['blocked_by']})"
        lines.append(f"  {name:34s} {entry['rows']:>7}  {state}")
    if report["structural_gaps"]:
        lines += ["", "structural gaps (no migration written by this tool):"]
        for gap in report["structural_gaps"]:
            lines.append(f"  - {gap['key']} → {gap['table']}")
            lines.append(f"    blocks: {gap['blocked_rows']}")
    lines += ["", f"rejects: {report['rejects']['total']}"]
    return "\n".join(lines)


__all__ = [
    "ReportRefused",
    "assert_report_is_pii_safe",
    "build_aggregate_report",
    "render_console",
    "write_reject_artifact",
]
