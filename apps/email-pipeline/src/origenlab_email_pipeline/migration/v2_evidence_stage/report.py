"""PII-safe reporting for a staging pass.

A staging manifest is full of real addresses and real document names. The report is the
part that gets pasted into a commit message, a terminal someone screenshots, or a CI log,
so it carries **counts and kinds only** — never a value, never an address, never a file
name. The manifest itself, on disk and outside Git, is where the values live.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from origenlab_email_pipeline.migration.v2_evidence_stage.apply import StageResult
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import Manifest


def build_report(
    manifest: Manifest, *, mode: str, target: str, applied: StageResult | None
) -> dict[str, Any]:
    by_kind = Counter(
        observation.kind for record in manifest.records for observation in record.observations
    )
    report: dict[str, Any] = {
        "mode": mode,
        "target": target,
        "manifest": {
            "path": manifest.path,
            "provider": manifest.provider,
            "source_record_kind": manifest.source_kind,
            "records": len(manifest.records),
            "observations": manifest.observation_count,
            "observations_by_kind": dict(sorted(by_kind.items())),
            "note": manifest.note,
        },
        "writes_crm": False,
        "writes_outbound": False,
        "resolves_anything": False,
    }
    if applied is not None:
        report["applied"] = applied.to_report()
    return report


def render_console(report: dict[str, Any]) -> str:
    manifest = report["manifest"]
    lines = [
        f"Gmail/Drive evidence staging — {report['mode']}",
        f"  target            {report['target']}",
        f"  manifest          {manifest['path']}",
        f"  provider          {manifest['provider']} -> {manifest['source_record_kind']}",
        f"  records           {manifest['records']}",
        f"  observations      {manifest['observations']}",
    ]
    for kind, count in manifest["observations_by_kind"].items():
        lines.append(f"    {kind:<22}{count}")
    if manifest.get("note"):
        lines.append(f"  note              {manifest['note']}")

    applied = report.get("applied")
    if applied is None:
        lines += [
            "",
            "  Nothing was written. Re-run with --apply to stage these as review candidates.",
        ]
    else:
        lines += [
            "",
            f"  source records    {applied['source_records_created']} created, "
            f"{applied['source_records_already_present']} already staged",
            f"  assertions        {applied['assertions_created']} created, "
            f"{applied['assertions_already_present']} already staged",
            f"  crm.* rows        {applied['crm_rows_before']} before, "
            f"{applied['crm_rows_after']} after — unchanged by construction",
        ]
        for note in applied["notes"]:
            lines.append(f"  note              {note}")
    lines += [
        "",
        "  Every record is review_status='pending' and every assertion is",
        "  resolution='unresolved'. Nothing is a contact, an organization or a",
        "  permission to send until an operator promotes it.",
    ]
    return "\n".join(lines)


__all__ = ["build_report", "render_console"]
