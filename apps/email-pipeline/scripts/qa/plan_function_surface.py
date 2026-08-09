#!/usr/bin/env python3
"""Read-only function / module surface audit planner (stdlib + AST only).

Scans ``src/origenlab_email_pipeline/**/*.py`` and ``scripts/**/*.py`` for LOC,
function/class counts, public surface, CLI entrypoints, and heuristic risk markers.
**Does not** import pipeline modules, execute scripts, or mutate SQLite/Postgres/Gmail.

Output defaults to ``reports/local/function-surface-audit/<timestamp>/``.

**Planning guidance only** — does not prove deletion safety; confirm with owners before moves.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SRC = APP_ROOT / "src" / "origenlab_email_pipeline"
_DEFAULT_SCRIPTS = APP_ROOT / "scripts"
_DEFAULT_OUT_PARENT = APP_ROOT / "reports" / "local" / "function-surface-audit"

RE_SUBPROCESS = re.compile(r"\bsubprocess\.")
RE_SQLITE_WRITE = re.compile(
    r"""(?ix)
    \b(INSERT|UPDATE|DELETE|TRUNCATE|UPSERT)\b
    |\.execute\s*\(
    |\.executemany\s*\(
    |\.commit\s*\(
    """,
)
RE_POSTGRES = re.compile(
    r"(?ix)\bpostgres\b|psycopg|alembic|sqlalchemy.*postgres|ORIGENLAB_POSTGRES|ORIGENLAB_CLOUD_POSTGRES",
)
RE_GMAIL = re.compile(r"(?ix)\bgmail\b|\bimap\b|googleapiclient|Gmail API|\[Gmail\]/")
RE_SEND = re.compile(
    r"(?ix)\bsendmail\b|\bsmtplib\b|send_message|send_inline|MimeText|\.send\s*\(",
)
RE_PURGE = re.compile(
    r"(?ix)(?<![a-z_])purge\b|archive_reports_out|\bDELETE FROM\b|\bDROP TABLE\b",
)
RE_APPLY = re.compile(r"--apply\b")
RE_ARGPARSE = re.compile(
    r"(?m)(^\s*import\s+argparse\b|^\s*from\s+argparse\s+import|ArgumentParser\s*\()",
)
RE_CLICK_TYPER = re.compile(
    r"(?m)(^\s*import\s+(click|typer)\b|^\s*from\s+(click|typer)\s+import)",
)
RE_MAIN_GUARD = re.compile(
    r'if\s+__name__\s*==\s*(["\'])__main__\1',
)


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    path: str
    area: str
    name: str
    qualname: str
    kind: str
    is_public: bool
    is_method: bool
    lineno: int
    end_lineno: int
    loc: int
    likely_bucket: str
    class_name: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    path: str
    area: str
    loc: int
    non_empty_loc: int
    class_count: int
    function_count: int
    public_function_count: int
    private_function_count: int
    public_class_count: int
    has_main_guard: bool
    has_argparse: bool
    has_click_or_typer: bool
    has_subprocess: bool
    has_sqlite_write_markers: bool
    has_postgres_markers: bool
    has_gmail_markers: bool
    has_send_markers: bool
    has_purge_markers: bool
    has_apply_flag: bool
    imports_count: int
    from_imports_count: int
    likely_bucket: str
    risk_bucket: str
    suggested_action: str


@dataclass
class ScanResult:
    modules: list[ModuleInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)


def _basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def _non_empty_loc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def _is_tatiana_lab_path(p: str) -> bool:
    if "tatiana" in p or "tatiana_copilot" in p:
        return True
    if p.startswith("scripts/tatiana/") or p.startswith("scripts/dataset/") or p.startswith("scripts/ml/"):
        return True
    if "openai_chat_generator" in p:
        return True
    return False


def _is_core_infrastructure_path(p: str, base: str) -> bool:
    """Shared config, schema, SQL, and small utilities (audit: active_core)."""
    if base == "__init__.py":
        return True
    if p.endswith("/core/safety.py") or p.endswith("/core/reports_out.py") or p.endswith("/core/step_runner.py"):
        return True
    if p.endswith("/core/config.py") or p.endswith("/core/db.py") or p.endswith("/core/sqlite_migrate.py"):
        return True
    return base in (
        "config.py",
        "db.py",
        "timeutil.py",
        "progress.py",
        "csv_contracts.py",
        "org_normalize.py",
        "canonical_operational_sql.py",
        "freshness_dates.py",
        "bi_views.py",
        "pipeline_meta_schema.py",
        "pipeline_run_recorder.py",
        "sqlite_migrate.py",
        "active_current_manifest.py",
        "operational_scope.py",
        "business_filter_rules.py",
        "cases_review_queue.py",
        "contact_export_queries.py",
        "dr50_payload_loader.py",
        "hunt_csv_alignment.py",
        "lead_export_queries.py",
        "lead_identity_norm.py",
        "leads_schema.py",
        "leads_equipment.py",
        "leads_enrich.py",
        "leads_score.py",
        "leads_match.py",
        "leads_ingest.py",
        "leads_normalize.py",
        "lead_contact_research.py",
        "lead_upstream_reconcile.py",
        "lead_normalize_upsert.py",
        "lead_accounts_schema.py",
        "lead_provenance.py",
        "attachment_report_sql.py",
        "outreach_queue_compare.py",
        "reported_non_delivery_signals.py",
    )


def classify_likely_bucket(rel_posix: str) -> str:
    """Heuristic owner/domain bucket; first match wins."""
    p = rel_posix.replace("\\", "/").lower()
    base = _basename(p)

    if p.startswith("src/origenlab_email_pipeline/operator_cli/") or base == "cli.py":
        return "operator_cli"

    if base in ("_bootstrap.py", "_script_warnings.py"):
        return "scripts_tools"

    if (
        p.startswith("scripts/ingest/")
        or "/ingest/" in p
        or "gmail_imap" in p
        or base.startswith("gmail_")
        or "gmail_workspace" in p
        or base == "canonical_gmail_dedupe.py"
        or "outreach_ingest_sync" in p
        or base == "contacto_gmail_source.py"
        or p.startswith("scripts/maintenance/dedupe_canonical_gmail")
    ):
        return "gmail_ingest"

    if "core/mart/" in p or "business_mart" in p or p.startswith("scripts/mart/"):
        return "mart"

    if "warm_case_" in p or "warm_case" in base:
        return "warm_cases"

    if (
        "postgres_dashboard_api" in p
        or "postgres_mirror" in p
        or "mart_core_postgres" in p
        or "dashboard_postgres" in p
        or "cloud_postgres" in p
        or "_to_postgres" in p
        or "alembic" in p
        or p.startswith("scripts/sync/")
        or p.startswith("scripts/migrate/")
        or (
            p.startswith("scripts/ops/")
            and ("postgres" in p or "dashboard" in p or "mirror" in p)
        )
        or "_mirror" in base
        or "catalog_mirror" in p
    ):
        return "postgres_mirror"

    if "postgres_dashboard_api" in p or "dashboard_api" in p:
        return "dashboard_api"

    if "research_automation" in p or "chilecompra_licitacion_lab" in p:
        return "research_lab"

    if _is_tatiana_lab_path(p):
        return "tatiana_lab"

    if (
        p.startswith("src/origenlab_email_pipeline/campaigns/")
        or "/origenlab_email_pipeline/campaigns/" in p
    ):
        return "campaigns"

    if (
        "commercial_identity" in p
        or "commercial_opportunity" in p
        or "commercial_procurement" in p
        or ("commercial" in p and ("/commercial/" in f"/{p}/" or "commercial_intel" in p))
    ):
        return "commercial_intel"

    if (
        "outbound" in p
        or "suppression" in p
        or "candidate_export" in p
        or "mark_sent" in p
        or "operational_trust" in p
        or "outreach_contact" in p
        or base.startswith("archive_")
        or "manual_html_outreach" in p
        or "email_business_filters" in p
        or "marketing_export_context" in p
        or "next_marketing_queue" in p
        or "marketing_contact_noise" in p
        or "merge_marketing_contact" in p
    ):
        return "outbound_safety"

    if "ndr_" in p or "reported_non_delivery" in p:
        return "ndr"

    if "equipment" in p or "licitacion" in p or "chilecompra" in p:
        return "equipment"

    if (
        p.startswith("src/origenlab_email_pipeline/catalog/")
        or "/origenlab_email_pipeline/catalog/" in p
    ):
        return "catalog"

    if (
        p.startswith("src/origenlab_email_pipeline/read/")
        or "/origenlab_email_pipeline/read/" in p
    ):
        return "read_modules"

    if p.startswith("scripts/reports/") or "client_report" in p:
        return "client_reports"

    if (
        p.startswith("scripts/validation/")
        or p.startswith("src/origenlab_email_pipeline/validation/")
    ):
        return "validation"

    if (
        base in ("parse_mbox.py", "attachment_extract.py", "export_jsonl.py")
        or p.startswith("scripts/ingest/02_")
        or p.startswith("scripts/ingest/04_")
    ):
        return "legacy_ingest"

    if base.startswith("operator_") and (base.endswith("_report.py") or base.endswith("_copy_es.py")):
        return "operator_reports"

    if _is_core_infrastructure_path(p, base):
        return "core_infrastructure"

    if (
        p.startswith("scripts/research/")
        or p.startswith("src/origenlab_email_pipeline/lead_research/")
        or "/lead_research/" in p
        or "lead_master" in p
        or base.startswith("lead_")
        or base.startswith("leads_")
        or p.startswith("scripts/leads/")
        or "/leads/" in f"/{p}/"
    ):
        return "lead_research"

    if "supplier" in p or p.startswith("scripts/catalog/"):
        return "supplier_catalog"

    if (
        p.startswith("scripts/qa/")
        or p.startswith("src/origenlab_email_pipeline/qa/")
        or base.startswith("audit_")
        or base.startswith("export_")
        or base.startswith("plan_")
        or base.startswith("validate_")
        or base.startswith("check_")
        or base == "email_classification_qa.py"
    ):
        return "qa_reports"

    if p.startswith("scripts/tools/") or base.startswith("purge_"):
        return "scripts_tools"

    return "unknown_review"


def _is_report_or_audit_path(p: str) -> bool:
    low = p.lower()
    base = _basename(low)
    if base.startswith(("plan_", "audit_", "export_", "validate_", "check_", "verify_")):
        return True
    if "/scripts/qa/" in low or "/qa/" in low:
        return True
    return False


def _is_planner_script(path: str) -> bool:
    base = _basename(path.lower())
    return base.startswith("plan_") and "scripts/qa/" in path.replace("\\", "/").lower()


def classify_risk_bucket(
    path: str,
    *,
    has_sqlite_write: bool,
    has_postgres: bool,
    has_gmail: bool,
    has_send: bool,
    has_purge: bool,
    has_apply: bool,
) -> str:
    if _is_planner_script(path):
        return "report_or_audit"
    low = path.lower()
    if has_send or has_purge:
        return "send_or_purge"
    if has_gmail and ("ingest" in low or "gmail" in low):
        return "gmail_ingest"
    if has_postgres:
        return "postgres_mirror_or_migration"
    if has_apply and ("outbound" in low or "leads/" in low or "send" in low or "archive" in low):
        return "outbound_apply"
    if has_sqlite_write:
        return "writes_sqlite"
    if _is_report_or_audit_path(path):
        return "report_or_audit"
    if not any((has_sqlite_write, has_postgres, has_gmail, has_send, has_purge, has_apply)):
        return "read_only"
    return "unknown_review"


def suggest_action(likely_bucket: str, risk_bucket: str, path: str) -> str:
    if likely_bucket == "warm_cases":
        return "review_with_tests_before_refactor"
    if risk_bucket in ("send_or_purge", "gmail_ingest", "postgres_mirror_or_migration", "outbound_apply"):
        return "manual_review_break_glass"
    if risk_bucket == "writes_sqlite":
        return "confirm_side_effects_before_change"
    if risk_bucket in ("read_only", "report_or_audit"):
        if likely_bucket == "qa_reports" or "plan_" in _basename(path):
            return "keep_for_planning"
        return "review_for_consolidation"
    return "unknown_review"


def _scan_text_markers(text: str) -> dict[str, bool]:
    return {
        "has_subprocess": bool(RE_SUBPROCESS.search(text)),
        "has_sqlite_write_markers": bool(RE_SQLITE_WRITE.search(text)),
        "has_postgres_markers": bool(RE_POSTGRES.search(text)),
        "has_gmail_markers": bool(RE_GMAIL.search(text)),
        "has_send_markers": bool(RE_SEND.search(text)),
        "has_purge_markers": bool(RE_PURGE.search(text)),
        "has_apply_flag": bool(RE_APPLY.search(text)),
        "has_argparse": bool(RE_ARGPARSE.search(text)),
        "has_click_or_typer": bool(RE_CLICK_TYPER.search(text)),
        "has_main_guard": bool(RE_MAIN_GUARD.search(text)),
    }


class _FunctionExtractor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[dict[str, Any]] = []
        self.classes: list[dict[str, Any]] = []
        self.imports_count = 0
        self.from_imports_count = 0
        self._class_stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports_count += len(node.names)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.from_imports_count += 1
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        end = getattr(node, "end_lineno", node.lineno)
        self.classes.append(
            {
                "name": node.name,
                "lineno": node.lineno,
                "end_lineno": end,
                "is_public": not node.name.startswith("_"),
            }
        )
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node, "function")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node, "async_function")
        self.generic_visit(node)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        end = getattr(node, "end_lineno", node.lineno)
        is_method = bool(self._class_stack)
        class_name = self._class_stack[-1] if is_method else None
        qualname = ".".join(filter(None, [class_name, node.name])) if is_method else node.name
        method_kind = "method" if is_method else kind
        self.functions.append(
            {
                "name": node.name,
                "qualname": qualname,
                "kind": method_kind,
                "is_public": not node.name.startswith("_"),
                "is_method": is_method,
                "lineno": node.lineno,
                "end_lineno": end,
                "loc": max(1, end - node.lineno + 1),
                "class_name": class_name,
            }
        )


def _parse_ast(text: str) -> ast.AST | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def scan_file(path: Path, rel: str, area: str) -> tuple[ModuleInfo, list[FunctionInfo]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""

    loc = len(text.splitlines())
    nonempty = _non_empty_loc(text)
    markers = _scan_text_markers(text)
    likely = classify_likely_bucket(rel)
    risk = classify_risk_bucket(
        rel,
        has_sqlite_write=markers["has_sqlite_write_markers"],
        has_postgres=markers["has_postgres_markers"],
        has_gmail=markers["has_gmail_markers"],
        has_send=markers["has_send_markers"],
        has_purge=markers["has_purge_markers"],
        has_apply=markers["has_apply_flag"],
    )
    action = suggest_action(likely, risk, rel)

    tree = _parse_ast(text)
    fn_count = pub_fn = priv_fn = 0
    class_count = pub_class = 0
    imports_count = from_imports_count = 0
    raw_functions: list[dict[str, Any]] = []
    if tree is not None:
        extractor = _FunctionExtractor()
        extractor.visit(tree)
        raw_functions = extractor.functions
        class_count = len(extractor.classes)
        pub_class = sum(1 for c in extractor.classes if c["is_public"])
        fn_count = len(raw_functions)
        pub_fn = sum(1 for f in raw_functions if f["is_public"])
        priv_fn = fn_count - pub_fn
        imports_count = extractor.imports_count
        from_imports_count = extractor.from_imports_count

    module = ModuleInfo(
        path=rel,
        area=area,
        loc=loc,
        non_empty_loc=nonempty,
        class_count=class_count,
        function_count=fn_count,
        public_function_count=pub_fn,
        private_function_count=priv_fn,
        public_class_count=pub_class,
        has_main_guard=markers["has_main_guard"],
        has_argparse=markers["has_argparse"],
        has_click_or_typer=markers["has_click_or_typer"],
        has_subprocess=markers["has_subprocess"],
        has_sqlite_write_markers=markers["has_sqlite_write_markers"],
        has_postgres_markers=markers["has_postgres_markers"],
        has_gmail_markers=markers["has_gmail_markers"],
        has_send_markers=markers["has_send_markers"],
        has_purge_markers=markers["has_purge_markers"],
        has_apply_flag=markers["has_apply_flag"],
        imports_count=imports_count,
        from_imports_count=from_imports_count,
        likely_bucket=likely,
        risk_bucket=risk,
        suggested_action=action,
    )

    functions = [
        FunctionInfo(
            path=rel,
            area=area,
            name=f["name"],
            qualname=f["qualname"],
            kind=f["kind"],
            is_public=f["is_public"],
            is_method=f["is_method"],
            lineno=f["lineno"],
            end_lineno=f["end_lineno"],
            loc=f["loc"],
            likely_bucket=likely,
            class_name=f["class_name"],
        )
        for f in raw_functions
    ]
    return module, functions


def iter_py_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        out.append(p)
    return sorted(out)


def scan_tree(root: Path, area: str, prefix: str) -> ScanResult:
    result = ScanResult()
    root = root.resolve()
    for p in iter_py_files(root):
        rel = f"{prefix}/{p.relative_to(root).as_posix()}"
        mod, fns = scan_file(p, rel, area)
        result.modules.append(mod)
        result.functions.extend(fns)
    return result


def scan_roots(src_root: Path, scripts_root: Path) -> ScanResult:
    combined = ScanResult()
    for partial in (
        scan_tree(src_root, "src", "src/origenlab_email_pipeline"),
        scan_tree(scripts_root, "script", "scripts"),
    ):
        combined.modules.extend(partial.modules)
        combined.functions.extend(partial.functions)
    return combined


HIGH_RISK_BUCKETS = frozenset(
    {
        "send_or_purge",
        "gmail_ingest",
        "postgres_mirror_or_migration",
        "outbound_apply",
    }
)

BUCKET_NEXT_ACTIONS: dict[str, str] = {
    "warm_cases": "characterize_then_split_later",
    "postgres_mirror": "freeze_unless_mirror_work",
    "gmail_ingest": "freeze_ingest_path",
    "outbound_safety": "freeze_safety_path",
    "tatiana_lab": "park_or_document",
    "research_lab": "park_or_document",
    "qa_reports": "keep_as_read_only_reports",
    "client_reports": "keep_as_read_only_reports",
    "lead_research": "characterize_large_reports",
    "campaigns": "characterize_campaign_oneoffs",
    "equipment": "characterize_equipment_lane",
    "catalog": "document_catalog_owner",
    "read_modules": "keep_with_api_read_path",
    "validation": "review_break_glass_scripts",
    "legacy_ingest": "document_break_glass_only",
    "operator_reports": "keep_with_origenlab_status",
    "core_infrastructure": "no_move_without_tests",
    "ndr": "keep_with_ndr_review",
    "unknown_review": "classify_owner_first",
}


def suggest_bucket_next_action(likely_bucket: str) -> str:
    return BUCKET_NEXT_ACTIONS.get(likely_bucket, "review_bucket_ownership")


def _is_script_entrypoint(mod: ModuleInfo) -> bool:
    return mod.area == "script" and (
        mod.has_main_guard or mod.has_argparse or mod.has_click_or_typer
    )


def build_bucket_summary(modules: list[ModuleInfo]) -> list[dict[str, Any]]:
    by_bucket: dict[str, list[ModuleInfo]] = {}
    for mod in modules:
        by_bucket.setdefault(mod.likely_bucket, []).append(mod)

    rows: list[dict[str, Any]] = []
    for bucket in sorted(by_bucket.keys()):
        group = by_bucket[bucket]
        largest = max(group, key=lambda m: (m.loc, m.path))
        rows.append(
            {
                "likely_bucket": bucket,
                "file_count": len(group),
                "src_file_count": sum(1 for m in group if m.area == "src"),
                "script_file_count": sum(1 for m in group if m.area == "script"),
                "total_loc": sum(m.loc for m in group),
                "total_functions": sum(m.function_count for m in group),
                "public_functions": sum(m.public_function_count for m in group),
                "private_functions": sum(m.private_function_count for m in group),
                "total_classes": sum(m.class_count for m in group),
                "public_classes": sum(m.public_class_count for m in group),
                "script_entrypoint_count": sum(1 for m in group if _is_script_entrypoint(m)),
                "high_risk_file_count": sum(1 for m in group if m.risk_bucket in HIGH_RISK_BUCKETS),
                "sqlite_write_file_count": sum(1 for m in group if m.has_sqlite_write_markers),
                "postgres_file_count": sum(1 for m in group if m.has_postgres_markers),
                "gmail_file_count": sum(1 for m in group if m.has_gmail_markers),
                "send_or_purge_file_count": sum(
                    1 for m in group if m.risk_bucket == "send_or_purge"
                ),
                "largest_file_path": largest.path,
                "largest_file_loc": largest.loc,
                "suggested_next_action": suggest_bucket_next_action(bucket),
            }
        )
    rows.sort(key=lambda r: (-r["total_loc"], r["likely_bucket"]))
    return rows


BUCKET_SUMMARY_FIELDS = [
    "likely_bucket",
    "file_count",
    "src_file_count",
    "script_file_count",
    "total_loc",
    "total_functions",
    "public_functions",
    "private_functions",
    "total_classes",
    "public_classes",
    "script_entrypoint_count",
    "high_risk_file_count",
    "sqlite_write_file_count",
    "postgres_file_count",
    "gmail_file_count",
    "send_or_purge_file_count",
    "largest_file_path",
    "largest_file_loc",
    "suggested_next_action",
]


def _risk_rank(risk: str) -> int:
    order = {
        "send_or_purge": 0,
        "gmail_ingest": 1,
        "postgres_mirror_or_migration": 2,
        "outbound_apply": 3,
        "writes_sqlite": 4,
        "unknown_review": 5,
        "report_or_audit": 6,
        "read_only": 7,
    }
    return order.get(risk, 99)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


MODULE_FIELDS = list(ModuleInfo.__dataclass_fields__.keys())
FUNCTION_FIELDS = list(FunctionInfo.__dataclass_fields__.keys())


def write_reports(result: ScanResult, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    modules = sorted(result.modules, key=lambda m: (m.path,))
    functions = sorted(result.functions, key=lambda f: (f.path, f.lineno))

    module_rows = [asdict(m) for m in modules]
    function_rows = [asdict(f) for f in functions]

    _write_csv(out_dir / "module_inventory.csv", module_rows, MODULE_FIELDS)
    _write_csv(out_dir / "function_inventory.csv", function_rows, FUNCTION_FIELDS)

    risk_rows = [r for r in module_rows if r["risk_bucket"] != "read_only"]
    risk_rows.sort(key=lambda r: (_risk_rank(r["risk_bucket"]), -r["loc"], r["path"]))
    _write_csv(out_dir / "risk_inventory.csv", risk_rows, MODULE_FIELDS)

    largest_files = sorted(module_rows, key=lambda r: (-r["loc"], r["path"]))[:50]
    _write_csv(
        out_dir / "largest_files.csv",
        largest_files,
        MODULE_FIELDS,
    )

    largest_functions = sorted(function_rows, key=lambda r: (-r["loc"], r["path"], r["lineno"]))[:100]
    _write_csv(out_dir / "largest_functions.csv", largest_functions, FUNCTION_FIELDS)

    script_entrypoints = [
        r
        for r in module_rows
        if r["area"] == "script" and (r["has_main_guard"] or r["has_argparse"] or r["has_click_or_typer"])
    ]
    script_entrypoints.sort(key=lambda r: r["path"])
    _write_csv(out_dir / "script_entrypoints.csv", script_entrypoints, MODULE_FIELDS)

    public_surface: list[dict[str, Any]] = []
    for m in modules:
        if m.public_class_count:
            public_surface.append(
                {
                    "path": m.path,
                    "area": m.area,
                    "symbol": "(module classes)",
                    "kind": "public_classes",
                    "count": m.public_class_count,
                    "likely_bucket": m.likely_bucket,
                    "risk_bucket": m.risk_bucket,
                }
            )
    for f in functions:
        if f.is_public:
            public_surface.append(
                {
                    "path": f.path,
                    "area": f.area,
                    "symbol": f.qualname,
                    "kind": f.kind,
                    "count": f.loc,
                    "likely_bucket": f.likely_bucket,
                    "risk_bucket": next(
                        (m.risk_bucket for m in modules if m.path == f.path),
                        "unknown_review",
                    ),
                }
            )
    public_surface.sort(key=lambda r: (r["path"], r["symbol"]))
    _write_csv(
        out_dir / "public_surface.csv",
        public_surface,
        ["path", "area", "symbol", "kind", "count", "likely_bucket", "risk_bucket"],
    )

    bucket_rows = build_bucket_summary(modules)
    _write_csv(out_dir / "bucket_summary.csv", bucket_rows, BUCKET_SUMMARY_FIELDS)

    summary = build_summary(result, out_dir, bucket_rows)
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")

    return build_json_summary(result, out_dir)


def _count_by(modules: list[ModuleInfo], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in modules:
        key = getattr(m, attr)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def build_json_summary(result: ScanResult, out_dir: Path) -> dict[str, Any]:
    modules = result.modules
    src_mods = [m for m in modules if m.area == "src"]
    script_mods = [m for m in modules if m.area == "script"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "file_counts": {
            "src_py": len(src_mods),
            "scripts_py": len(script_mods),
            "total": len(modules),
        },
        "function_counts": {
            "total_functions": sum(m.function_count for m in modules),
            "public_functions": sum(m.public_function_count for m in modules),
            "private_functions": sum(m.private_function_count for m in modules),
            "total_classes": sum(m.class_count for m in modules),
            "public_classes": sum(m.public_class_count for m in modules),
        },
        "likely_bucket_counts": _count_by(modules, "likely_bucket"),
        "risk_bucket_counts": _count_by(modules, "risk_bucket"),
        "marker_counts": {
            "has_main_guard": sum(1 for m in modules if m.has_main_guard),
            "has_argparse": sum(1 for m in modules if m.has_argparse),
            "has_sqlite_write_markers": sum(1 for m in modules if m.has_sqlite_write_markers),
            "has_postgres_markers": sum(1 for m in modules if m.has_postgres_markers),
            "has_gmail_markers": sum(1 for m in modules if m.has_gmail_markers),
            "has_send_markers": sum(1 for m in modules if m.has_send_markers),
            "has_purge_markers": sum(1 for m in modules if m.has_purge_markers),
            "has_apply_flag": sum(1 for m in modules if m.has_apply_flag),
        },
        "largest_files_top10": [
            {"path": m.path, "loc": m.loc, "likely_bucket": m.likely_bucket, "risk_bucket": m.risk_bucket}
            for m in sorted(modules, key=lambda x: (-x.loc, x.path))[:10]
        ],
        "highest_risk_top10": [
            {"path": m.path, "loc": m.loc, "likely_bucket": m.likely_bucket, "risk_bucket": m.risk_bucket}
            for m in sorted(modules, key=lambda x: (_risk_rank(x.risk_bucket), -x.loc, x.path))[:10]
        ],
        "bucket_summary": build_bucket_summary(modules),
    }


def build_summary(
    result: ScanResult,
    out_dir: Path,
    bucket_rows: list[dict[str, Any]] | None = None,
) -> str:
    modules = result.modules
    data = build_json_summary(result, out_dir)
    if bucket_rows is None:
        bucket_rows = data["bucket_summary"]
    lines = [
        "# Function surface audit (read-only planner)",
        "",
        f"- Generated: `{data['generated_at']}`",
        f"- Output dir: `{out_dir}`",
        "",
        "## Counts",
        "",
        f"- Python files (src): **{data['file_counts']['src_py']}**",
        f"- Python files (scripts): **{data['file_counts']['scripts_py']}**",
        f"- Total functions: **{data['function_counts']['total_functions']}** "
        f"(public **{data['function_counts']['public_functions']}**, "
        f"private **{data['function_counts']['private_functions']}**)",
        f"- Total classes: **{data['function_counts']['total_classes']}** "
        f"(public **{data['function_counts']['public_classes']}**)",
        "",
        "## Likely bucket (owner/domain heuristic)",
        "",
    ]
    for k, v in data["likely_bucket_counts"].items():
        lines.append(f"- `{k}`: {v}")
    lines.extend(["", "## Risk bucket", ""])
    for k, v in data["risk_bucket_counts"].items():
        lines.append(f"- `{k}`: {v}")
    lines.extend(["", "## Marker counts", ""])
    for k, v in data["marker_counts"].items():
        lines.append(f"- `{k}`: {v}")
    lines.extend(["", "## Top 10 largest files (by LOC)", ""])
    for row in data["largest_files_top10"]:
        lines.append(
            f"- `{row['path']}` — {row['loc']} LOC — `{row['likely_bucket']}` / `{row['risk_bucket']}`"
        )
    lines.extend(["", "## Top 10 highest-risk files (heuristic)", ""])
    for row in data["highest_risk_top10"]:
        lines.append(
            f"- `{row['path']}` — {row['loc']} LOC — `{row['likely_bucket']}` / `{row['risk_bucket']}`"
        )
    lines.extend(["", "## Bucket summary", ""])
    lines.append(
        "| Bucket | Files | LOC | Functions | Entrypoints | High risk | Largest file | Next action |"
    )
    lines.append("|--------|-------|-----|-----------|-------------|-----------|--------------|-------------|")
    for row in bucket_rows[:20]:
        lines.append(
            f"| `{row['likely_bucket']}` | {row['file_count']} | {row['total_loc']} | "
            f"{row['total_functions']} | {row['script_entrypoint_count']} | "
            f"{row['high_risk_file_count']} | `{row['largest_file_path']}` ({row['largest_file_loc']}) | "
            f"`{row['suggested_next_action']}` |"
        )
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            "- `module_inventory.csv` — per-file metrics",
            "- `function_inventory.csv` — per-function metrics",
            "- `bucket_summary.csv` — aggregated owner/domain buckets",
            "- `risk_inventory.csv` — non-`read_only` files",
            "- `largest_files.csv` / `largest_functions.csv`",
            "- `script_entrypoints.csv` — scripts with `main` / argparse / click",
            "- `public_surface.csv` — public functions and class counts",
            "",
            "## Safety",
            "",
            "This report is **planning guidance only**. It does **not** prove deletion safety.",
            "Do **not** use it alone to delete or move files. Confirm with owners and tests.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--src-dir",
        type=Path,
        default=None,
        help="Package root (default: src/origenlab_email_pipeline)",
    )
    parser.add_argument(
        "--scripts-dir",
        type=Path,
        default=None,
        help="Scripts root (default: scripts/)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Report directory (default: reports/local/function-surface-audit/<timestamp>/)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable summary JSON to stdout (still writes reports unless --stdout-only).",
    )
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="With --json, skip writing CSV/summary files.",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src_root = (args.src_dir or _DEFAULT_SRC).resolve()
    scripts_root = (args.scripts_dir or _DEFAULT_SCRIPTS).resolve()

    if args.out_dir is not None:
        out_dir = args.out_dir.resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = (_DEFAULT_OUT_PARENT / stamp).resolve()

    result = scan_roots(src_root, scripts_root)

    if args.json and args.stdout_only:
        summary = build_json_summary(result, out_dir)
    else:
        summary = write_reports(result, out_dir)

    print("plan_function_surface (read-only planner — not deletion authority)", file=sys.stdout)
    print(f"src-dir: {src_root}", file=sys.stdout)
    print(f"scripts-dir: {scripts_root}", file=sys.stdout)
    print(
        f"files: src={summary['file_counts']['src_py']} "
        f"scripts={summary['file_counts']['scripts_py']} "
        f"total={summary['file_counts']['total']}",
        file=sys.stdout,
    )
    print(
        f"functions: total={summary['function_counts']['total_functions']} "
        f"public={summary['function_counts']['public_functions']}",
        file=sys.stdout,
    )
    if not (args.json and args.stdout_only):
        print(f"wrote reports: {out_dir}", file=sys.stdout)

    if args.json:
        print(json.dumps(summary, indent=2), file=sys.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
