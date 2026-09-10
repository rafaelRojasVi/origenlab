"""Deterministic, sanitised reports -- and the assertion that they are sanitised.

Two artefacts per run, JSON and Markdown, both built from the same structure so they cannot
disagree. Determinism means: keys sorted, checks in registry order, no set iteration reaching the
output, and no value that varies between two runs of the same inputs except the ones the caller
passes in explicitly (the timestamp and the commit). Given the same observations and the same
clock, the bytes are identical -- which is what makes a report reviewable as a diff.

**Nothing is written until it is proven clean.** Both texts are rendered, both are redacted, and
both are then re-read by the leak assertions in `olaudit.redact`. If either still matches a
credential, key, JWT, connection string, Supabase host name, project reference or non-loopback
address, `write` raises and no file is created. A report that cannot be proven clean does not exist
and therefore cannot be uploaded.

`verify_file` runs the same assertions against a report already on disk. CI calls it before it
uploads anything, so an artefact is uploaded only after it has passed the check a second time, in
a separate process, from the bytes that would actually be published.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from . import redact
from .checks import Result
from .verdict import ATTESTED, CORROBORATED, ERROR, FAIL, NOT_RUN, PASS, RECORDED, Outcome

REPORT_VERSION = 1
TOOL = "origenlab-slice0-audit"

STATUS_ORDER = (PASS, ATTESTED, CORROBORATED, RECORDED, NOT_RUN, FAIL, ERROR)

# The fold over required checks, reported in its own words. `PASS` as a bare value belongs to a
# single check; the run-level conclusion is `verdict`, which always carries its mode. Without this
# a reader grepping a simulated report for PASS would find one and have to work out that it was
# the fold rather than the conclusion.
BASE_LABEL = {
    "PASS": "ALL_REQUIRED_CHECKS_SATISFIED",
    "FAIL": "A_REQUIRED_CHECK_FAILED",
    "INCOMPLETE": "REQUIRED_CHECKS_INCOMPLETE",
}

# What this audit cannot answer from a database session, in either mode. Named in every report so
# a reader is never left to infer that silence means coverage.
REMAINING_HOSTED_ONLY = (
    "The applied-migration list. `supabase_migrations.schema_migrations` is owned by the platform "
    "and is unreadable by the configured audit identity, which holds no privilege of its own. What "
    "the migrations produced is compared instead, object by object, in a08, a09 and a10.",
    "`supabase db lint` and `supabase db advisors` against the hosted project. Both need a "
    "connection string on the command line or a linked project, and hosted mode permits neither. "
    "Recorded as the operator attestation t07 until a route exists that needs no link state.",
    "Storage buckets, backup schedule and both restore drills: outside a PostgreSQL session "
    "(attestations t02, t04, t05, t06).",
    "The secret stores of Render, Cloudflare, GitHub and the browser bundles, which decide "
    "MIGRATION.md §5.2 check 10 (attestation t03).",
    "The behavioural direct-login proofs of MIGRATION.md §5.2 checks 6 to 9. They need real LOGIN "
    "connections as origenlab_api and origenlab_worker with passwords set for the run; "
    "`supabase/scripts/verify_direct_logins.sh` does that against the local disposable database, "
    "and this audit will not set a password on a hosted role.",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(
    *,
    results: list[Result],
    outcome: Outcome,
    target_description: dict,
    attestation_description: dict,
    run_started_at: dt.datetime,
    repo_head: str,
    tree_clean: bool,
    baseline_path: Path,
    scrubbed_env_names: list[str],
    sql_files: list[tuple[str, str]],
) -> dict:
    """Assemble the report structure. Pure: no clock, no filesystem beyond hashing inputs."""
    counts = {status: 0 for status in STATUS_ORDER}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    return {
        "report_version": REPORT_VERSION,
        "tool": TOOL,
        "run": {
            "mode": outcome.mode,
            "simulated": outcome.simulated,
            "hosted_contacted": outcome.hosted_contacted,
            "started_at_utc": run_started_at.astimezone(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "repo_head": repo_head,
            "tracked_tree_clean": tree_clean,
            "inherited_env_names_not_passed_to_the_child": sorted(scrubbed_env_names),
        },
        "target": target_description,
        "attestation": attestation_description,
        "verdict": {
            "verdict": outcome.verdict,
            "required_checks_outcome": BASE_LABEL.get(outcome.base, outcome.base),
            "gate_eligible": outcome.gate_eligible,
            "exit_code": outcome.exit_code,
            "blocking_checks": list(outcome.blocking),
            "incomplete_required_checks": list(outcome.incomplete),
        },
        "counts": {status: counts[status] for status in STATUS_ORDER},
        "checks": [
            {
                "id": result.check_id,
                "title": result.title,
                "obligation": result.obligation,
                "kind": result.kind,
                "required": result.required,
                "status": result.status,
                "findings": list(result.findings),
                "notes": list(result.notes),
                "summary": result.summary,
            }
            for result in results
        ],
        "sql_bank": [{"file": name, "sha256": digest} for name, digest in sql_files],
        "baseline": {
            "file": baseline_path.name,
            "sha256": _sha256(baseline_path),
        },
        "remaining_hosted_only": list(REMAINING_HOSTED_ONLY),
    }


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _verdict_sentence(report: dict) -> str:
    verdict = report["verdict"]
    run = report["run"]
    if run["simulated"]:
        return (
            f"**{verdict['verdict']}** — this run replayed a committed fixture. It contacted no "
            "database, proves nothing about any project, and is not eligible to satisfy a "
            "migration gate."
        )
    if run["mode"] == "local":
        return (
            f"**{verdict['verdict']}** — this run audited the local Supabase stack over loopback. "
            "MIGRATION.md §5.2 requires these checks against the hosted project; a local verdict "
            "is not that, and this run is not eligible to satisfy a migration gate."
        )
    if verdict["gate_eligible"]:
        return (
            f"**{verdict['verdict']}** — a hosted run that connected, with every required check "
            "satisfied. Eligible to be cited for the slice 0 hosted gate alongside the "
            "hosted-only items listed below."
        )
    return (
        f"**{verdict['verdict']}** — not eligible to satisfy a migration gate. "
        "See the blocking and incomplete checks below."
    )


def render_markdown(report: dict) -> str:
    run = report["run"]
    verdict = report["verdict"]
    lines: list[str] = []
    add = lines.append

    add("# OrigenLab — Slice 0 audit report")
    add("")
    add(_verdict_sentence(report))
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| Mode | `{run['mode']}` |")
    add(f"| Simulated | `{str(run['simulated']).lower()}` |")
    add(f"| Hosted contacted | `{str(run['hosted_contacted']).lower()}` |")
    add(f"| Gate eligible | `{str(verdict['gate_eligible']).lower()}` |")
    add(f"| Started (UTC) | {run['started_at_utc']} |")
    add(f"| Commit | `{run['repo_head']}` |")
    clean = run["tracked_tree_clean"]
    add(f"| Tracked tree clean | `{'unknown' if clean is None else str(clean).lower()}` |")
    add(f"| Baseline | `{report['baseline']['file']}` @ `{report['baseline']['sha256'][:12]}` |")
    add("")

    target = report["target"]
    add("## Target")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| Host class | {target['host_class']} |")
    add(f"| Host | `{target['host_shown']}` |")
    add(f"| Port | {target['port']} |")
    add(f"| Login role | `{target['user']}` |")
    add(f"| TLS | `{target['sslmode']}` |")
    add(f"| Address pinned before connect | `{str(target['address_pinned_before_connect']).lower()}` |")
    add(f"| Statement timeout | {target['statement_timeout_ms']} ms |")
    add("")
    for note in target.get("guard_notes", []):
        add(f"- {note}")
    if target.get("guard_notes"):
        add("")

    attestation = report["attestation"]
    add("## Operator attestation")
    add("")
    if attestation.get("present"):
        add(f"- Attested by **{attestation['attested_by']}** at {attestation['attested_at_utc']}")
        add(f"- Project fingerprint `{attestation['project_ref_fingerprint']}`")
        add(f"- Items claimed: {', '.join(attestation['items_claimed'])}")
    else:
        add("- None supplied. Every attested item below is unproven and counts as incomplete.")
    add("")

    add("## Checks")
    add("")
    add("| ID | Status | Required | Kind | Check | Obligation |")
    add("|---|---|---|---|---|---|")
    for check in report["checks"]:
        add(
            f"| `{check['id']}` | `{check['status']}` | "
            f"{'yes' if check['required'] else 'no'} | {check['kind']} | "
            f"{check['title']} | {check['obligation']} |"
        )
    add("")

    findings = [c for c in report["checks"] if c["findings"]]
    if findings:
        add("## Findings")
        add("")
        for check in findings:
            add(f"### `{check['id']}` — {check['title']}")
            add("")
            for finding in check["findings"]:
                add(f"- {finding}")
            add("")

    notes = [c for c in report["checks"] if c["notes"]]
    if notes:
        add("## Recorded, not failed on")
        add("")
        for check in notes:
            add(f"### `{check['id']}` — {check['title']}")
            add("")
            for note in check["notes"]:
                add(f"- {note}")
            add("")

    add("## Not covered by this audit")
    add("")
    for item in report["remaining_hosted_only"]:
        add(f"- {item}")
    add("")

    add("## Counts")
    add("")
    add("| Status | Checks |")
    add("|---|---|")
    for status in STATUS_ORDER:
        add(f"| `{status}` | {report['counts'].get(status, 0)} |")
    add("")

    return "\n".join(lines) + "\n"


def write(report: dict, out_dir: Path, secrets: tuple[str, ...] = ()) -> tuple[Path, Path]:
    """Render, redact, prove clean, then write. Nothing is created if the proof fails."""
    json_text = redact.redact(render_json(report), secrets)
    markdown_text = redact.redact(render_markdown(report), secrets)

    redact.assert_clean(json_text, secrets, what="the JSON report")
    redact.assert_clean(markdown_text, secrets, what="the Markdown report")

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"slice0-audit-{report['run']['mode']}"
    if report["run"]["simulated"]:
        stem += "-simulated"
    json_path = out_dir / f"{stem}.json"
    markdown_path = out_dir / f"{stem}.md"
    json_path.write_text(json_text, encoding="utf-8")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    json_path.chmod(0o600)
    markdown_path.chmod(0o600)
    return json_path, markdown_path


def verify_file(path: Path) -> list[redact.Leak]:
    """Re-run the leak assertions against a report already on disk. Empty means safe to upload."""
    return redact.find_leaks(path.read_text(encoding="utf-8"))
