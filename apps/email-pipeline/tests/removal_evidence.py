"""Shared targets and report builder for Phase 2 script removal evidence (read-only)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which

REPO = Path(__file__).resolve().parents[1]
MONOREPO = REPO.parents[1]

DEPRECATED_REMOVAL_TARGETS: tuple[dict[str, str], ...] = ()

REMOVED_PHASE5Q_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/tools/flag_reported_non_delivery_from_contacto.py",
        "replacement": (
            "scripts/tools/flag_ndr_bounces_from_contacto.py --include-reported-non-delivery "
            "+ scripts/qa/build_ndr_review_queue.py"
        ),
        "removed_phase": "5Q",
    },
)

REMOVED_PHASE5R_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/qa/build_legacy_contacts_2016_2019_review.py",
        "replacement": (
            "src/origenlab_email_pipeline/lead_research/legacy_contacts_2016_2019.py + "
            "tests/test_legacy_contacts_2016_2019.py"
        ),
        "removed_phase": "5R",
    },
)

REMOVED_PHASE5S_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/ml/test_real_embeddings.py",
        "replacement": "uv sync --group ml; scripts/ml/explore_email_clusters.py if embeddings needed",
        "removed_phase": "5S",
    },
    {
        "path": "scripts/leads/campaigns/apply_deepresearch_top10_contacts_to_sheet.py",
        "replacement": "frozen/manual DR50 sheet workflow",
        "removed_phase": "5S",
    },
    {
        "path": "scripts/dataset/review_marketing_labels_cli.py",
        "replacement": "Tatiana cohort workflow / external labeling",
        "removed_phase": "5S",
    },
)

REMOVED_PHASE5D_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/leads/advanced/export_archive_outreach_candidates.py",
        "replacement": "scripts/leads/build_archive_send_batch.py --audit-only",
        "removed_phase": "5D",
    },
)

REMOVED_PHASE5K_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "src/origenlab_email_pipeline/campaigns/manual_outreach_2026_06_01.py",
        "replacement": "scripts/qa/build_post_send_digest.py + docs/pipeline/POST_SEND_SAFE_LOOP.md",
        "removed_phase": "5K",
    },
    {
        "path": "scripts/qa/build_manual_outreach_2026_06_01_digest.py",
        "replacement": "scripts/qa/build_post_send_digest.py",
        "removed_phase": "5K",
    },
    {
        "path": "scripts/qa/apply_manual_outreach_2026_06_01_corrections.py",
        "replacement": "docs/pipeline/POST_SEND_SAFE_LOOP.md + generic suppression tools",
        "removed_phase": "5K",
    },
)

REMOVED_PHASE5C_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/qa/build_buyer_opportunity_queue.py",
        "replacement": (
            "scripts/qa/build_equipment_first_opportunity_queue.py + "
            "scripts/qa/build_equipment_first_operator_queue.py"
        ),
        "removed_phase": "5C",
    },
)

REMOVED_PHASE5A_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/ops/run_post_send_2026_06_01_refresh.sh",
        "replacement": "docs/pipeline/POST_SEND_SAFE_LOOP.md step-by-step loop",
        "removed_phase": "5A",
    },
    {
        "path": "scripts/ops/run_manual_outreach_2026_06_01_post_send_refresh.sh",
        "replacement": "docs/pipeline/POST_SEND_SAFE_LOOP.md step-by-step loop",
        "removed_phase": "5A",
    },
)

REMOVED_PHASE5B_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/build_lead_account_rollup.py",
        "replacement": "scripts/leads/advanced/build_lead_account_rollup.py",
        "removed_phase": "5B",
    },
    {
        "path": "scripts/match_lead_accounts_to_existing_orgs.py",
        "replacement": "scripts/leads/advanced/match_lead_accounts_to_existing_orgs.py",
        "removed_phase": "5B",
    },
    {
        "path": "scripts/validate_lead_account_rollup.py",
        "replacement": "scripts/leads/advanced/validate_lead_account_rollup.py",
        "removed_phase": "5B",
    },
    {
        "path": "scripts/audit_lead_org_quality.py",
        "replacement": "scripts/leads/advanced/audit_lead_org_quality.py",
        "removed_phase": "5B",
    },
)

REFACTOR_PHASE5P_COMPLETED: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/mart/build_business_mart.py",
        "note": (
            "Done (Phase 5P / Stage 6F1): CLI orchestration → core/mart/build_business_mart_cli.py; "
            "operator script path + SAFETY banner unchanged; tests in test_build_business_mart.py, "
            "test_build_business_mart_phase2.py"
        ),
    },
)

REFACTOR_PHASE3_TARGETS: tuple[dict[str, str], ...] = (
    {
        "path": "scripts/ingest/05_workspace_gmail_imap_to_sqlite.py",
        "note": "Extract IMAP helpers; tests in test_workspace_gmail_imap_ingest.py — future",
    },
    {
        "path": "scripts/qa/export_contacted_lead_overlap_audit.py",
        "note": "Golden CSV columns locked in test_export_contacted_lead_overlap_audit.py — library split done; entrypoint unchanged",
    },
    {
        "path": "scripts/qa/export_email_conversation_intelligence.py",
        "note": "Golden CSV columns locked in test_export_email_conversation_intelligence.py — library split done; entrypoint unchanged",
    },
)

@dataclass(frozen=True, slots=True)
class ReferenceCounts:
    docs: int
    tests: int
    scripts: int
    in_script_map: bool
    test_locked: bool


_SKIP_DIR_NAMES = frozenset(
    {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", "htmlcov"}
)
_TEXT_SUFFIXES = frozenset(
    {
        "",
        ".md",
        ".py",
        ".sh",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".rst",
        ".sql",
    }
)


def _rg_available() -> bool:
    return which("rg") is not None


def _iter_search_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _SKIP_DIR_NAMES.intersection(path.parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in ("Makefile", "AGENTS.md"):
            continue
        out.append(path)
    return out


def _python_grep_files(pattern: str, roots: list[Path]) -> list[str]:
    """Fallback when ripgrep is not installed (e.g. default GitHub Actions runners)."""
    compiled = re.compile(pattern)
    hits: list[str] = []
    for root in roots:
        for path in _iter_search_paths(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if compiled.search(text):
                hits.append(str(path))
    return sorted(set(hits))


def _rg_files(pattern: str, roots: list[Path]) -> list[str]:
    if not _rg_available():
        return _python_grep_files(pattern, roots)
    hits: list[str] = []
    for root in roots:
        if not root.is_dir() and not root.is_file():
            continue
        target = str(root)
        cp = subprocess.run(
            ["rg", "-l", pattern, target],
            capture_output=True,
            text=True,
            check=False,
        )
        if cp.returncode in (0, 1):
            hits.extend(cp.stdout.splitlines())
    return sorted(set(hits))


def reference_counts(rel_path: str) -> ReferenceCounts:
    rel = rel_path.replace("\\", "/")
    base = Path(rel).name
    docs_roots = [REPO / "docs", MONOREPO / "docs", MONOREPO / "apps" / "dashboard" / "docs"]
    test_roots = [REPO / "tests", MONOREPO / "apps" / "api" / "tests"]
    script_roots = [REPO / "scripts", REPO / "Makefile", REPO / "AGENTS.md"]

    docs_hits = _rg_files(re.escape(rel), docs_roots) + _rg_files(re.escape(base), docs_roots)
    test_hits = _rg_files(re.escape(rel), test_roots) + _rg_files(re.escape(base), test_roots)
    script_hits = _rg_files(re.escape(rel), script_roots) + _rg_files(re.escape(base), script_roots)

    map_text = (REPO / "docs/SCRIPT_MAP.md").read_text(encoding="utf-8", errors="replace")
    in_map = rel in map_text or base in map_text

    lock_pat = base
    critical = (REPO / "tests/test_critical_script_paths.py").read_text(encoding="utf-8", errors="replace")
    test_locked = rel in critical or lock_pat in critical or any(
        lock_pat in (REPO / "tests" / p).read_text(encoding="utf-8", errors="replace")
        for p in ("test_lead_compatibility_wrappers.py", "test_ndr_tool_parity.py")
        if (REPO / "tests" / p).is_file()
    )

    return ReferenceCounts(
        docs=len(set(docs_hits)),
        tests=len(set(test_hits)),
        scripts=len(set(script_hits)),
        in_script_map=in_map,
        test_locked=test_locked,
    )


def build_removal_evidence_markdown() -> str:
    lines: list[str] = [
        "# Phase 2 — script removal evidence",
        "",
        "Status: generated reference (read-only audit)",
        "Owner: email-pipeline-maintainers",
        "Last reviewed: 2026-06-02",
        "",
        "**Purpose:** Evidence for Phase 4–5 deprecation/removal. Phase **5A** removed dated post-send shell orchestrators; Phase **5B** removed root lead-account wrappers; Phase **5C** removed legacy buyer opportunity queue builder; Phase **5D** removed archive outreach audit wrapper; Phase **5K** removed 2026-06-01 manual outreach registry and dated QA scripts; Phase **5Q** removed legacy `flag_reported_non_delivery_from_contacto.py` (canonical `--include-reported-non-delivery`); Phase **5R** removed orphan `build_legacy_contacts_2016_2019_review.py` (library + tests remain); Phase **5S** removed three zero-ref LAB scripts (ML smoke, DR50 top-10 patch, interactive marketing labels CLI).",
        "",
        "Regenerate: `uv run pytest tests/test_script_removal_evidence.py::test_generate_removal_evidence_report -q`",
        "",
        "---",
        "",
        "## Deprecated / wrapper removal candidates",
        "",
        "| Path | SCRIPT_MAP | Test-locked | Doc refs | Test refs | Script refs | Replacement | Suggested phase |",
        "|------|------------|-------------|----------|-----------|-------------|-------------|-----------------|",
    ]
    for row in DEPRECATED_REMOVAL_TARGETS:
        rel = row["path"]
        rc = reference_counts(rel)
        lines.append(
            f"| `{rel}` | {'yes' if rc.in_script_map else 'no'} | "
            f"{'yes' if rc.test_locked else 'no'} | {rc.docs} | {rc.tests} | {rc.scripts} | "
            f"{row['replacement']} | {row['suggested_phase']} |"
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5A (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5A_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5B (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5B_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5C (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5C_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5D (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5D_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5K (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5K_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5Q (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5Q_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5R (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5R_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Removed in Phase 5S (2026-06-02)",
            "",
            "| Path | Replacement | Removed phase |",
            "|------|-------------|---------------|",
        ]
    )
    for row in REMOVED_PHASE5S_TARGETS:
        lines.append(
            f"| `{row['path']}` | {row['replacement']} | {row['removed_phase']} |",
        )
    lines.extend(
        [
            "",
            "## Phase 3 refactor targets (keep entrypoints; lock behavior first)",
            "",
            "| Path | Notes |",
            "|------|-------|",
        ]
    )
    for row in REFACTOR_PHASE3_TARGETS:
        lines.append(f"| `{row['path']}` | {row['note']} |")
    lines.extend(
        [
            "",
            "## Completed Phase 5P / Stage 6F1 (mart CLI)",
            "",
            "| Path | Notes |",
            "|------|-------|",
        ]
    )
    for row in REFACTOR_PHASE5P_COMPLETED:
        lines.append(f"| `{row['path']}` | {row['note']} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- **Test-locked yes** → remove only after updating `test_critical_script_paths.py`, "
            "`test_lead_compatibility_wrappers.py`, or other contract tests in the same PR.",
            "- **High doc refs** → update RUNBOOK/SCRIPT_MAP/AGENTS in Phase 1-style doc PR before deletion.",
            "- **Wrappers** → Phase 4 deprecation stderr first; Phase 5 removal when root paths have zero external refs.",
            "",
        ]
    )
    return "\n".join(lines)


def write_removal_evidence_report(path: Path | None = None) -> Path:
    if path is None:
        raise ValueError("write_removal_evidence_report requires an explicit path")
    out = path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_removal_evidence_markdown(), encoding="utf-8")
    return out
