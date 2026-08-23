"""CLI adapters for PR2 commercial-identity and PR3 commercial-opportunity refreshes.

Thin wrappers only. All identity/opportunity resolution, fingerprint gating, and
transaction-contract logic lives in ``commercial_identity`` and
``commercial_opportunity`` (PR2/PR3) — this module does not duplicate it. It adds:

1. operator_cli registration for the two existing "break-glass" build scripts
   (``build-commercial-identity`` / ``build-commercial-opportunity``), preserving
   their exact safety model (explicit --sqlite-path, no production fallback,
   dry-run default, explicit --run-context).
2. An explicit sequencing command (``refresh-commercial-opportunity-models``)
   that runs PR2 then PR3, fail-fast, because PR3's identity fingerprint gate
   requires a current PR2 snapshot (see ARCH-2A-P0 verification report).
3. A durable, append-only JSONL run record (ARCH-2A-P1 phase 3) — the smallest
   history of manual runs beyond stdout/build_meta.

Production apply remains fully operator-driven: every command below requires an
explicit --sqlite-path with no fallback, and --apply is never implied by a default.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.cli_modes import (
    add_apply_dry_run_flags,
    resolve_apply_dry_run_mode,
)
from origenlab_email_pipeline.commercial_identity import (
    CommercialIdentityPathError,
    require_explicit_sqlite_path,
    run_identity_build,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
    SCHEMA_VERSION as COMMERCIAL_IDENTITY_SCHEMA_VERSION,
    VALID_RUN_CONTEXTS,
)
from origenlab_email_pipeline.commercial_opportunity import (
    IdentitySnapshotError,
    SourceSchemaError,
    StaleBuildPlanError,
    run_opportunity_build,
)
from origenlab_email_pipeline.commercial_opportunity.constants import (
    BUILD_CONTRACT as COMMERCIAL_OPPORTUNITY_BUILD_CONTRACT,
    SCHEMA_VERSION as COMMERCIAL_OPPORTUNITY_SCHEMA_VERSION,
)
from origenlab_email_pipeline.config import (
    canonical_production_sqlite_path,
    load_settings,
)
from origenlab_email_pipeline.pipeline_run_recorder import get_git_describe

RUN_LOG_FILENAME = "commercial_identity_opportunity_runs.jsonl"

MODEL_COMMERCIAL_IDENTITY = "commercial_identity"
MODEL_COMMERCIAL_OPPORTUNITY = "commercial_opportunity"


# --------------------------------------------------------------------------- #
# Durable run record (append-only JSONL; smallest repo-consistent history)
# --------------------------------------------------------------------------- #


def commercial_identity_opportunity_run_log_path(
    reports_dir: Path | None = None,
) -> Path:
    base = reports_dir or load_settings().resolved_reports_dir()
    return base / "active" / "current" / RUN_LOG_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _classify_sqlite_path(path: Path) -> dict[str, Any]:
    """Describe the target DB without leaking a full machine-specific path."""
    resolved = Path(path).expanduser().resolve()
    try:
        production_path = canonical_production_sqlite_path()
        classification = (
            "production" if resolved == production_path else "non_production"
        )
    except Exception:
        classification = "unknown"
    info: dict[str, Any] = {
        "sqlite_path_basename": resolved.name,
        "sqlite_path_classification": classification,
    }
    try:
        stat = resolved.stat()
        info["sqlite_size_bytes"] = stat.st_size
        info["sqlite_mtime_utc"] = (
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except OSError:
        info["sqlite_size_bytes"] = None
        info["sqlite_mtime_utc"] = None
    return info


def _truncate(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def append_commercial_identity_opportunity_run_record(
    *,
    model: str,
    started_at: str,
    finished_at: str,
    run_context: str,
    applied: bool,
    status: str,
    sqlite_path: Path,
    schema_version: str,
    build_contract: str | None,
    summary: dict[str, Any] | None,
    error_summary: str | None,
    log_path: Path | None = None,
) -> Path:
    """Append one JSONL record. Never overwrites prior history."""
    metrics = (summary or {}).get("metrics") or {}
    counts = (
        (summary or {}).get("written") or (summary or {}).get("planned_writes") or {}
    )
    conflict_count = next(
        (v for k, v in counts.items() if k.endswith("_conflict")),
        None,
    )
    record: dict[str, Any] = {
        "model": model,
        "started_at": started_at,
        "finished_at": finished_at,
        "run_context": run_context,
        "applied": applied,
        "status": status,
        "schema_version": schema_version,
        "build_contract": build_contract,
        "transaction_contract": (summary or {}).get("transaction_contract"),
        "identity_fingerprint": (summary or {}).get("identity_fingerprint"),
        "identity_fingerprint_match_status": (summary or {}).get(
            "identity_fingerprint_match_status"
        ),
        "opportunity_source_fingerprint": (summary or {}).get(
            "opportunity_source_fingerprint"
        ),
        "counts": counts,
        "conflict_count": conflict_count,
        "canonical_opportunity_count": metrics.get("canonical_opportunity_count"),
        "canonical_account_count": metrics.get("canonical_account_count"),
        "canonical_contact_count": metrics.get("canonical_contact_count"),
        "error_summary": _truncate(error_summary, 500) if error_summary else None,
        "git_sha": get_git_describe(fallback="unknown"),
        **_classify_sqlite_path(sqlite_path),
    }
    path = log_path or commercial_identity_opportunity_run_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------------------- #
# build-commercial-identity (PR2 standalone)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CommercialIdentityBuildOptions:
    sqlite_path: Path
    apply: bool
    run_context: str
    json_summary: bool = False
    log_path: Path | None = None


def parse_build_commercial_identity_args(
    argv: list[str],
) -> CommercialIdentityBuildOptions:
    parser = argparse.ArgumentParser(
        prog="build-commercial-identity",
        description=(
            "Build the commercial account/contact identity read model (PR2). "
            "Operator-triggered only — not part of daily-core automation."
        ),
    )
    parser.add_argument(
        "--sqlite-path",
        "--db",
        type=Path,
        dest="sqlite_path",
        required=True,
        help="Explicit SQLite path (required; no ORIGENLAB_SQLITE_PATH / settings fallback).",
    )
    add_apply_dry_run_flags(
        parser,
        apply_help="Write commercial_identity_* data (DELETE+rebuild, contract B).",
    )
    parser.add_argument(
        "--run-context",
        choices=sorted(VALID_RUN_CONTEXTS),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
        help=f"Run-context label (metadata only). Default: {RUN_CONTEXT_LOCAL_FIXTURE}.",
    )
    parser.add_argument(
        "--json-summary", action="store_true", help="Print JSON summary to stdout."
    )
    ns = parser.parse_args(argv)
    mode = resolve_apply_dry_run_mode(parser, ns)
    return CommercialIdentityBuildOptions(
        sqlite_path=ns.sqlite_path,
        apply=mode.apply,
        run_context=ns.run_context,
        json_summary=ns.json_summary,
    )


def _print_build_summary(summary: dict[str, Any], *, json_summary: bool) -> None:
    if json_summary:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    print(f"mode={summary.get('mode')}")
    print(f"run_context={summary.get('run_context')}")
    print(f"identity_fingerprint={summary.get('identity_fingerprint')}")
    if "identity_fingerprint_match_status" in summary:
        print(
            f"identity_fingerprint_match_status={summary.get('identity_fingerprint_match_status')}"
        )
    counts = summary.get("written") or summary.get("planned_writes") or {}
    print("counts:")
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count}")
    print(
        f"written: {summary.get('written')}"
        if summary.get("applied")
        else "dry-run: no SQLite writes performed"
    )


def run_build_commercial_identity(options: CommercialIdentityBuildOptions) -> int:
    started_at = _now_iso()
    summary: dict[str, Any] | None = None
    error_summary: str | None = None
    status = "error"
    exit_code = 1
    try:
        sqlite_path = require_explicit_sqlite_path(options.sqlite_path)
        summary = run_identity_build(
            sqlite_path=sqlite_path,
            apply=options.apply,
            run_context=options.run_context,
        )
        status = "success"
        exit_code = 0
    except CommercialIdentityPathError as exc:
        print(f"build-commercial-identity: error: {exc}", file=sys.stderr)
        status = "path_error"
        error_summary = str(exc)
        exit_code = 2
    except Exception as exc:  # unexpected: still record, then surface non-zero
        print(f"build-commercial-identity: unexpected error: {exc}", file=sys.stderr)
        status = "error"
        error_summary = str(exc)
        exit_code = 1
    finished_at = _now_iso()
    append_commercial_identity_opportunity_run_record(
        model=MODEL_COMMERCIAL_IDENTITY,
        started_at=started_at,
        finished_at=finished_at,
        run_context=options.run_context,
        applied=bool(summary and summary.get("applied")),
        status=status,
        sqlite_path=options.sqlite_path,
        schema_version=COMMERCIAL_IDENTITY_SCHEMA_VERSION,
        build_contract=None,
        summary=summary,
        error_summary=error_summary,
        log_path=options.log_path,
    )
    if summary is not None:
        _print_build_summary(summary, json_summary=options.json_summary)
    return exit_code


def print_build_commercial_identity_help() -> None:
    print(
        "build-commercial-identity — PR2 account/contact identity read model\n\n"
        "Example (dry-run):\n"
        "  uv run origenlab build-commercial-identity --sqlite-path /path/to/emails.sqlite\n\n"
        "Example (apply, non-production):\n"
        "  uv run origenlab build-commercial-identity --sqlite-path /tmp/scratch.sqlite --apply\n\n"
        "Operator-triggered only. Not part of daily-core automation. Requires an explicit "
        "--sqlite-path; there is no production fallback."
    )


# --------------------------------------------------------------------------- #
# build-commercial-opportunity (PR3 standalone)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CommercialOpportunityBuildOptions:
    sqlite_path: Path
    apply: bool
    run_context: str
    json_summary: bool = False
    log_path: Path | None = None


def parse_build_commercial_opportunity_args(
    argv: list[str],
) -> CommercialOpportunityBuildOptions:
    parser = argparse.ArgumentParser(
        prog="build-commercial-opportunity",
        description=(
            "Build the commercial opportunity stage/evidence read model (PR3). "
            "Requires a current PR2 identity snapshot — run build-commercial-identity "
            "first, or use refresh-commercial-opportunity-models. Operator-triggered "
            "only — not part of daily-core automation."
        ),
    )
    parser.add_argument(
        "--sqlite-path",
        "--db",
        type=Path,
        dest="sqlite_path",
        required=True,
        help="Explicit SQLite path (required; no ORIGENLAB_SQLITE_PATH / settings fallback).",
    )
    add_apply_dry_run_flags(
        parser,
        apply_help="Write commercial_opportunity_* data (DELETE+rebuild, contract B).",
    )
    parser.add_argument(
        "--run-context",
        choices=sorted(VALID_RUN_CONTEXTS),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
        help=f"Run-context label (metadata only). Default: {RUN_CONTEXT_LOCAL_FIXTURE}.",
    )
    parser.add_argument(
        "--json-summary", action="store_true", help="Print JSON summary to stdout."
    )
    ns = parser.parse_args(argv)
    mode = resolve_apply_dry_run_mode(parser, ns)
    return CommercialOpportunityBuildOptions(
        sqlite_path=ns.sqlite_path,
        apply=mode.apply,
        run_context=ns.run_context,
        json_summary=ns.json_summary,
    )


def run_build_commercial_opportunity(options: CommercialOpportunityBuildOptions) -> int:
    started_at = _now_iso()
    summary: dict[str, Any] | None = None
    error_summary: str | None = None
    status = "error"
    exit_code = 1
    try:
        # PR3's own require_explicit_sqlite_path / gate logic runs inside
        # run_opportunity_build; no path pre-check is duplicated here.
        summary = run_opportunity_build(
            sqlite_path=options.sqlite_path,
            apply=options.apply,
            run_context=options.run_context,
        )
        status = "success"
        exit_code = 0
    except IdentitySnapshotError as exc:
        print(f"build-commercial-opportunity: error: {exc}", file=sys.stderr)
        status = "gate_rejected"
        error_summary = str(exc)
        exit_code = 3
    except SourceSchemaError as exc:
        print(f"build-commercial-opportunity: error: {exc}", file=sys.stderr)
        status = "schema_error"
        error_summary = str(exc)
        exit_code = 4
    except StaleBuildPlanError as exc:
        print(f"build-commercial-opportunity: error: {exc}", file=sys.stderr)
        status = "gate_rejected"
        error_summary = str(exc)
        exit_code = 5
    except CommercialIdentityPathError as exc:
        print(f"build-commercial-opportunity: error: {exc}", file=sys.stderr)
        status = "path_error"
        error_summary = str(exc)
        exit_code = 2
    except Exception as exc:  # unexpected: still record, then surface non-zero
        print(f"build-commercial-opportunity: unexpected error: {exc}", file=sys.stderr)
        status = "error"
        error_summary = str(exc)
        exit_code = 1
    finished_at = _now_iso()
    append_commercial_identity_opportunity_run_record(
        model=MODEL_COMMERCIAL_OPPORTUNITY,
        started_at=started_at,
        finished_at=finished_at,
        run_context=options.run_context,
        applied=bool(summary and summary.get("applied")),
        status=status,
        sqlite_path=options.sqlite_path,
        schema_version=COMMERCIAL_OPPORTUNITY_SCHEMA_VERSION,
        build_contract=COMMERCIAL_OPPORTUNITY_BUILD_CONTRACT,
        summary=summary,
        error_summary=error_summary,
        log_path=options.log_path,
    )
    if summary is not None:
        _print_build_summary(summary, json_summary=options.json_summary)
    return exit_code


def print_build_commercial_opportunity_help() -> None:
    print(
        "build-commercial-opportunity — PR3 opportunity stage/evidence read model\n\n"
        "Example (dry-run):\n"
        "  uv run origenlab build-commercial-opportunity --sqlite-path /path/to/emails.sqlite\n\n"
        "Requires a current PR2 identity snapshot (fails closed — exit 3/5 — if the "
        "persisted identity fingerprint is missing or stale). Run build-commercial-identity "
        "first, or use refresh-commercial-opportunity-models to run both in sequence."
    )


# --------------------------------------------------------------------------- #
# refresh-commercial-opportunity-models (PR2 -> PR3 sequencing, fail-fast)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RefreshCommercialOpportunityModelsOptions:
    sqlite_path: Path
    run_context: str
    json_summary: bool = False
    log_path: Path | None = None


def parse_refresh_commercial_opportunity_models_args(
    argv: list[str],
) -> RefreshCommercialOpportunityModelsOptions:
    parser = argparse.ArgumentParser(
        prog="refresh-commercial-opportunity-models",
        description=(
            "Sequenced PR2 -> PR3 production refresh: build-commercial-identity, then "
            "(only on success) build-commercial-opportunity. Fail-fast — PR3 never runs "
            "if PR2 fails. Always applies; there is no dry-run mode for the combined "
            "sequence (dry-run each step individually via the standalone commands)."
        ),
    )
    parser.add_argument(
        "--sqlite-path",
        "--db",
        type=Path,
        dest="sqlite_path",
        required=True,
        help="Explicit SQLite path (required; no ORIGENLAB_SQLITE_PATH / settings fallback).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        required=True,
        help="Required. This command only ever applies both steps; there is no dry-run mode.",
    )
    parser.add_argument(
        "--confirm-sequenced-apply",
        action="store_true",
        required=True,
        help=(
            "Required alongside --apply: explicit operator confirmation that a combined "
            "PR2+PR3 production_apply sequence is intended (matches the extra-confirmation "
            "convention used by mirror-dashboard --allow-non-scratch-postgres)."
        ),
    )
    parser.add_argument(
        "--run-context",
        choices=sorted(VALID_RUN_CONTEXTS),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
        help=(
            f"Run-context label applied to BOTH steps (metadata only). "
            f"Default: {RUN_CONTEXT_LOCAL_FIXTURE} — not production. Use production_apply "
            "explicitly for a live production refresh."
        ),
    )
    parser.add_argument(
        "--json-summary", action="store_true", help="Print JSON summary to stdout."
    )
    ns = parser.parse_args(argv)
    return RefreshCommercialOpportunityModelsOptions(
        sqlite_path=ns.sqlite_path,
        run_context=ns.run_context,
        json_summary=ns.json_summary,
    )


def run_refresh_commercial_opportunity_models(
    options: RefreshCommercialOpportunityModelsOptions,
) -> int:
    print(
        "refresh-commercial-opportunity-models: step 1/2 build-commercial-identity (PR2)"
    )
    identity_rc = run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=options.sqlite_path,
            apply=True,
            run_context=options.run_context,
            json_summary=options.json_summary,
            log_path=options.log_path,
        )
    )
    if identity_rc != 0:
        print(
            "refresh-commercial-opportunity-models: PR2 failed "
            f"(exit {identity_rc}); PR3 NOT executed. Sequence INCOMPLETE — "
            "not a success.",
            file=sys.stderr,
        )
        return identity_rc

    print(
        "refresh-commercial-opportunity-models: step 2/2 build-commercial-opportunity (PR3)"
    )
    opportunity_rc = run_build_commercial_opportunity(
        CommercialOpportunityBuildOptions(
            sqlite_path=options.sqlite_path,
            apply=True,
            run_context=options.run_context,
            json_summary=options.json_summary,
            log_path=options.log_path,
        )
    )
    if opportunity_rc != 0:
        print(
            "refresh-commercial-opportunity-models: PR3 failed "
            f"(exit {opportunity_rc}) after PR2 succeeded. Sequence INCOMPLETE — "
            "PR2 identity was refreshed but PR3 opportunity was not. Re-run "
            "build-commercial-opportunity once the cause is fixed.",
            file=sys.stderr,
        )
        return opportunity_rc

    print(
        "refresh-commercial-opportunity-models: sequence COMPLETE — "
        "PR2 and PR3 both applied successfully."
    )
    return 0


def print_refresh_commercial_opportunity_models_help() -> None:
    print(
        "refresh-commercial-opportunity-models — sequenced PR2 -> PR3 production refresh\n\n"
        "Example (production):\n"
        "  uv run origenlab refresh-commercial-opportunity-models \\\n"
        "    --sqlite-path /path/to/emails.sqlite --apply --confirm-sequenced-apply \\\n"
        "    --run-context production_apply\n\n"
        "Runs build-commercial-identity (PR2), and only on success, "
        "build-commercial-opportunity (PR3). Fails fast: PR3 never runs if PR2 fails, and "
        "a PR3 failure after a successful PR2 is reported as an incomplete sequence, never "
        "as success. Both steps' summaries are recorded durably regardless of outcome."
    )


__all__ = [
    "CommercialIdentityBuildOptions",
    "CommercialOpportunityBuildOptions",
    "RefreshCommercialOpportunityModelsOptions",
    "append_commercial_identity_opportunity_run_record",
    "commercial_identity_opportunity_run_log_path",
    "parse_build_commercial_identity_args",
    "parse_build_commercial_opportunity_args",
    "parse_refresh_commercial_opportunity_models_args",
    "print_build_commercial_identity_help",
    "print_build_commercial_opportunity_help",
    "print_refresh_commercial_opportunity_models_help",
    "run_build_commercial_identity",
    "run_build_commercial_opportunity",
    "run_refresh_commercial_opportunity_models",
]
