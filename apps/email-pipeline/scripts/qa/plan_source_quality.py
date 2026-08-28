#!/usr/bin/env python3
"""Read-only **source quality** plan: scan library + scripts (text only; no imports, no execution).

Heuristic vertical buckets, line counts, and import-hint flags. **Not** authoritative for refactors;
use with [`docs/QUALITY_AND_REFACTOR_STRATEGY.md`](../../docs/QUALITY_AND_REFACTOR_STRATEGY.md) and [`docs/TATIANA_LAB_BOUNDARY.md`](../../docs/TATIANA_LAB_BOUNDARY.md) for the ``tatiana_lab`` bucket.
Does not read SQLite, Gmail, or secrets; does not write outside optional ``--json-out`` path.

Phase 8C extends vertical buckets per ``docs/audits/PHASE8_POST_7C_TREE_CLEANUP_AUDIT_20260603.md`` §3 (planner-only).

Skips Python under ``reports/local/`` and ``reports/out/`` (generated audit snapshots and report artifacts).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SRC = APP_ROOT / "src" / "origenlab_email_pipeline"
_DEFAULT_SCRIPTS = APP_ROOT / "scripts"

# Generated/local report trees — not maintained source (audit snapshots, JSON-out copies).
_GENERATED_REPORT_PATH_PREFIXES: tuple[str, ...] = (
    "reports/local/",
    "reports/out/",
)

RE_SUBPROCESS = re.compile(r"\bsubprocess\.")
RE_SQLITE_MUTATION = re.compile(
    r"(\b(INSERT|UPDATE|DELETE|TRUNCATE)\b|\.execute\s*\(|\.executemany\s*\(|\.commit\s*\()",
    re.IGNORECASE,
)
RE_IMPORT_CORE = re.compile(
    r"^\s*(from\s+origenlab_email_pipeline\.core\b|import\s+origenlab_email_pipeline\.core\b)",
    re.MULTILINE,
)
# Top-level package imports (not core.…)
RE_IMPORT_TOPLEVEL = re.compile(
    r"^\s*from\s+origenlab_email_pipeline\.(?!core\b)([a-zA-Z0-9_]+)",
    re.MULTILINE,
)
RE_IMPORT_PKG = re.compile(r"^\s*import\s+origenlab_email_pipeline\s*(#|$)", re.MULTILINE)
RE_IMPORT_FROM_PKG = re.compile(
    r"^\s*from\s+origenlab_email_pipeline\s+import\b",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class FileScan:
    path: str
    line_count: int
    vertical: str
    has_subprocess: bool
    has_sqlite_mutation_keywords: bool
    has_core_import_hint: bool
    has_toplevel_import_hint: bool


def _is_tatiana_lab_path(p: str) -> bool:
    """Heuristic Tatiana / lab / ML exploration (matches Stage 6E1 boundary doc).

    Includes ``tatiana_copilot/``, root ``tatiana_*.py``, ``scripts/tatiana|dataset|ml/``,
    paths containing ``tatiana``, and the large OpenAI chat generator module name.
    """
    if "tatiana" in p or "tatiana_copilot" in p:
        return True
    if (
        p.startswith("scripts/tatiana/")
        or p.startswith("scripts/dataset/")
        or p.startswith("scripts/ml/")
    ):
        return True
    if "openai_chat_generator" in p:
        return True
    if "tatiana_review_cohort" in p or "tatiana_voice_cohort" in p:
        return True
    return False


def _basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def _is_excluded_generated_report_py(path: Path, *, app_root: Path = APP_ROOT) -> bool:
    """Return True for Python files under generated ``reports/local`` or ``reports/out`` trees."""
    resolved = path.resolve()
    root = app_root.resolve()
    try:
        rel = resolved.relative_to(root).as_posix().lower()
    except ValueError:
        posix = resolved.as_posix().lower()
        return "/reports/local/" in posix or "/reports/out/" in posix
    return any(rel.startswith(prefix) for prefix in _GENERATED_REPORT_PATH_PREFIXES)


def classify_vertical(rel_posix: str) -> str:
    """Heuristic single bucket; first matching rule wins (Phase 6F / 8C taxonomy)."""
    p = rel_posix.replace("\\", "/").lower()
    base = _basename(p)

    if p.startswith("scripts/qa/plan_") or "/scripts/qa/plan_" in p:
        return "planners"

    if p.startswith("scripts/qa/verify_") and "postgres_mirror" in p:
        return "postgres_verify"

    if base.startswith("purge_") and p.startswith("scripts/tools/"):
        return "purge_break_glass"
    if base == "archive_reports_out_generated.py":
        return "purge_break_glass"

    if (
        p.startswith("src/origenlab_email_pipeline/operator_cli/")
        or base == "cli.py"
        or base == "operator_status_report.py"
    ):
        return "operator_cli"

    if p.startswith("scripts/qa/") and (
        base.startswith("export_")
        or base.startswith("validate_")
        or base.startswith("audit_")
    ):
        return "qa_exports"

    if p.startswith("scripts/qa/") and (
        base.startswith("build_cyber_") or base.startswith("build_presentacion_")
    ):
        return "campaign_scripts"

    if (
        p.startswith("scripts/research/")
        or base == "research_automation.py"
        or "core/research_automation.py" in p
        or base == "verify_research_candidate_evidence.py"
        or base == "audit_research_candidate_evidence.py"
    ):
        return "research_lab"

    if (
        base.startswith("equipment_")
        and ("_queue.py" in base or base == "equipment_opportunity_mirror.py")
    ) or base == "load_equipment_opportunity_mirror.py":
        return "equipment_first"

    if (
        base == "mart_core_postgres_migrate.py"
        or base == "dashboard_postgres_sync.py"
        or "_postgres_mirror" in base
        or p.startswith("scripts/sync/")
        or p.startswith("scripts/migrate/")
        or "/scripts/sync/" in f"/{p}/"
        or "/scripts/migrate/" in f"/{p}/"
        or "_to_postgres" in p
        or "validate_sqlite_archive_for_postgres" in p
        or "postgres_outbound_audit" in p
    ):
        return "postgres_mirror"

    if (
        base == "db.py"
        or base == "parse_mbox.py"
        or base == "attachment_extract.py"
        or base == "canonical_operational_sql.py"
        or base == "reports_out.py"
        or "core/reports_out.py" in p
    ):
        return "core_infrastructure"

    if p.startswith("scripts/tools/check_") or (
        base == "inspect_sqlite.py" and p.startswith("scripts/tools/")
    ):
        return "tooling"

    if "streamlit" in p:
        return "removed_ui_module"

    if _is_tatiana_lab_path(p):
        return "tatiana_lab"

    if "postgres_dashboard_api" in p:
        return "postgres_api"

    if (
        p.startswith("src/origenlab_email_pipeline/catalog/")
        or "/origenlab_email_pipeline/catalog/" in p
        or p.startswith("scripts/catalog/")
    ):
        return "catalog"

    if (
        "core/mart/" in p
        or "business_mart" in p
        or p.startswith("scripts/mart/")
        or "/scripts/mart/" in p
    ):
        return "mart"

    if (
        p.startswith("src/origenlab_email_pipeline/validation/")
        or "/origenlab_email_pipeline/validation/" in p
        or p.startswith("scripts/validation/")
    ):
        return "validation"

    if (
        p.startswith("src/origenlab_email_pipeline/campaigns/")
        or "/origenlab_email_pipeline/campaigns/" in p
    ):
        return "campaigns"

    if (
        p.startswith("src/origenlab_email_pipeline/qa/")
        or "/origenlab_email_pipeline/qa/" in p
    ):
        return "qa"

    if (
        p.startswith("src/origenlab_email_pipeline/ingest/")
        or "/origenlab_email_pipeline/ingest/" in p
        or p.startswith("scripts/ingest/")
    ):
        return "ingest"

    if "warm_case_" in p:
        return "warm_cases"

    if "ndr_" in p or "reported_non_delivery_" in p:
        return "ndr"

    if "commercial" in p and ("/commercial/" in f"/{p}/" or "commercial_intel" in p):
        return "commercial"

    if (
        "client_report" in p
        or p.startswith("scripts/reports/")
        or "/scripts/reports/" in f"/{p}/"
        or "build_leads_client" in p
        or "generate_client_report" in p
        or "open_client_report" in p
    ):
        return "reports"

    if (
        "archive_outreach" in p
        or "archive_send_batch" in p
        or "archive_shortlist" in p
        or "build_archive" in p
    ):
        return "archive_lane"

    if any(
        x in p
        for x in (
            "outbound_",
            "/outbound",
            "operational_trust",
            "candidate_export",
            "csv_contracts",
            "suppression",
            "gmail_",
            "mark_sent",
            "process_broad",
            "next_marketing",
            "outreach_contact",
            "outreach_ingest",
            "email_business_filters",
            "business_filter_rules",
            "marketing_contact_noise",
        )
    ):
        return "outbound"

    if "lead" in p or "leads_" in p or "/leads/" in f"/{p}/" or p.startswith("scripts/leads/"):
        return "leads"

    if "supplier" in p:
        return "suppliers"

    if base == "email_classification_qa.py":
        return "qa"

    if base == "cases_review_queue.py":
        return "qa"

    return "unknown"


def _scan_text(path: Path, rel: str) -> FileScan:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FileScan(
            path=rel,
            line_count=0,
            vertical="unknown",
            has_subprocess=False,
            has_sqlite_mutation_keywords=False,
            has_core_import_hint=False,
            has_toplevel_import_hint=False,
        )
    n = len(raw.splitlines())
    v = classify_vertical(rel)
    sub = bool(RE_SUBPROCESS.search(raw))
    sql = bool(RE_SQLITE_MUTATION.search(raw))
    core = bool(RE_IMPORT_CORE.search(raw))
    top = (
        bool(RE_IMPORT_TOPLEVEL.search(raw))
        or bool(RE_IMPORT_PKG.search(raw))
        or bool(RE_IMPORT_FROM_PKG.search(raw))
    )
    return FileScan(
        path=rel,
        line_count=n,
        vertical=v,
        has_subprocess=sub,
        has_sqlite_mutation_keywords=sql,
        has_core_import_hint=core,
        has_toplevel_import_hint=top,
    )


def iter_py_files(root: Path, *, app_root: Path = APP_ROOT) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if _is_excluded_generated_report_py(p, app_root=app_root):
            continue
        out.append(p)
    return sorted(out)


def scan_tree(root: Path, label: str, *, app_root: Path = APP_ROOT) -> list[FileScan]:
    out: list[FileScan] = []
    root = root.resolve()
    for p in iter_py_files(root, app_root=app_root):
        rel = p.relative_to(root)
        if label == "src":
            rprefix = f"src/origenlab_email_pipeline/{rel.as_posix()}"
        else:
            rprefix = f"scripts/{rel.as_posix()}"
        out.append(_scan_text(p, rprefix))
    return out


def vertical_counts(scans: list[FileScan]) -> dict[str, int]:
    d: dict[str, int] = {}
    for s in scans:
        d[s.vertical] = d.get(s.vertical, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    p.add_argument(
        "--src-dir",
        type=Path,
        default=None,
        help="Root of origenlab_email_pipeline package (default: …/src/origenlab_email_pipeline)",
    )
    p.add_argument(
        "--scripts-dir",
        type=Path,
        default=None,
        help="Root of scripts/ (default: …/scripts)",
    )
    p.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write full JSON report (no changes under src/scripts).",
    )
    p.add_argument(
        "--top",
        type=int,
        default=20,
        help="How many largest files to list per category.",
    )
    return p


def _print_section(title: str) -> None:
    print(f"--- {title} ---")


def run() -> int:
    args = build_parser().parse_args()
    src_root = (args.src_dir or _DEFAULT_SRC).resolve()
    scripts_root = (args.scripts_dir or _DEFAULT_SCRIPTS).resolve()
    top_n = max(1, int(args.top))

    src_scans = scan_tree(src_root, "src")
    script_scans = scan_tree(scripts_root, "scripts")
    all_scans = src_scans + script_scans

    print("plan_source_quality (read-only, heuristic — not authority)", file=sys.stdout)
    print(f"src-dir: {src_root}", file=sys.stdout)
    print(f"scripts-dir: {scripts_root}", file=sys.stdout)
    print(f"python files: src={len(src_scans)} scripts={len(script_scans)} total={len(all_scans)}", file=sys.stdout)

    v_all = vertical_counts(all_scans)
    _print_section("vertical ownership (file count, heuristic)")
    for k, c in v_all.items():
        print(f"  {k}: {c}", file=sys.stdout)

    sc_sub = sum(1 for s in all_scans if s.has_subprocess)
    sc_sql = sum(1 for s in all_scans if s.has_sqlite_mutation_keywords)
    sc_core = sum(1 for s in all_scans if s.has_core_import_hint)
    sc_top = sum(1 for s in all_scans if s.has_toplevel_import_hint)
    _print_section("import / keyword hints (text scan)")
    print(
        f"  files with core import hint: {sc_core} | with top-level pkg import hint: {sc_top}",
        file=sys.stdout,
    )
    print(
        f"  files with subprocess. usage: {sc_sub} | sqlite/mutation-like keywords: {sc_sql}",
        file=sys.stdout,
    )

    for label, scans in (("source modules (src/)", src_scans), ("scripts (scripts/)", script_scans)):
        _print_section(f"largest {label} by line count (top {top_n})")
        for s in sorted(scans, key=lambda x: (-x.line_count, x.path))[:top_n]:
            print(
                f"  {s.line_count:5d}  {s.vertical:16s}  {s.path}",
                file=sys.stdout,
            )

    _print_section("suggested first refactor candidates (low precision — triage in PR)")
    print(
        "  1) Thin one campaign/outbound script by delegating to existing library helpers; run pytest.",
        file=sys.stdout,
    )
    print("  2) Improve reporting path docs; run plan_reports_out_cleanup (read-only).", file=sys.stdout)
    print(
        "  3) Tatiana/lab: see docs/TATIANA_LAB_BOUNDARY.md; isolate optional deps in a later stage; no behavior change.",
        file=sys.stdout,
    )
    print(
        "  WARNING: this report is planning guidance only. Confirm with owners before moves.",
        file=sys.stdout,
    )

    if args.json_out is not None:
        path = args.json_out.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "src_dir": str(src_root),
            "scripts_dir": str(scripts_root),
            "file_counts": {
                "src_py": len(src_scans),
                "scripts_py": len(script_scans),
                "total": len(all_scans),
            },
            "vertical_counts": v_all,
            "import_keyword_hints": {
                "with_core_import_hint": sc_core,
                "with_toplevel_import_hint": sc_top,
                "with_subprocess": sc_sub,
                "with_sqlite_mutation_like_keywords": sc_sql,
            },
            "all_src_scans": [asdict(s) for s in sorted(src_scans, key=lambda x: (x.path,))],
            "all_script_scans": [asdict(s) for s in sorted(script_scans, key=lambda x: (x.path,))],
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"wrote json report: {path}", file=sys.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
