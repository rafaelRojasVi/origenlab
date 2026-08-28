# Quality and refactor strategy (OrigenLab email-pipeline)

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-04-24

**Companion:** [`SCRIPT_MAP.md`](SCRIPT_MAP.md), [`CRUD_SAFETY.md`](CRUD_SAFETY.md), [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md), read-only [`plan_source_quality.py`](../scripts/qa/plan_source_quality.py), [`plan_script_consolidation.py`](../scripts/qa/plan_script_consolidation.py).

This document is **governance and intent**, not a second source of behavior. It guides **staged** refactors so the SQLite-first pipeline stays safe, testable, and operator-correct.

---

## 1. Current architecture truth

- **`core/`** is a **stable re-export import surface** (see `core/__init__.py` and `tests/test_core_import_surface.py`). It **does not** by itself move implementation out of the historic top-level package layout.
- **Top-level** modules under `src/origenlab_email_pipeline/` (e.g. `candidate_export_gate.py`, `outbound_core.py`) **remain the implementation** for most logic until an explicit **vertical migration** phase and tests.
- **`scripts/`** are **operator entrypoints**; they should become **thinner** over time by delegating to the library, not re-implementing policy.
- **Lab / pilot / archive-lane** code is **expected**; it must stay **visibly** separated (naming, folders, `SCRIPT_MAP.md`) so daily outbound lanes are not confused with experiments.
- **Runtime is SQLite-first**; **Postgres** is optional (migration, audit) — not primary OLTP.
- **Gmail Sent in SQLite** is the **outbound** delivery truth; scripts and preflight must align with that model.

---

## 2. Refactor rules

- **No big-bang** physical moves: one concern per PR, one vertical or one subsystem.
- **One vertical per PR** (see §4) unless the change is docs-only or test-only.
- **Scripts** should **delegate** to `origenlab_email_pipeline` and, for new work, to **`core.*` re-exports** where they exist; avoid growing monolithic `scripts/…` with duplicated business rules.
- **New** library and script code should **prefer** `from origenlab_email_pipeline.core.…` **where a wrapper exists**; existing `from origenlab_email_pipeline.…` imports stay valid. **No mass rewrites** until Stage 6C+ with dedicated tests.
- **Do not move** policy-critical modules (gate, suppressions, operational trust, core outbound) until **tests and docs** explicitly cover the new paths and the operator contract is updated.
- **No deletion** of scripts or package modules without: read-only **planner** pass where applicable (`plan_script_consolidation.py` / `plan_reports_out_cleanup.py`), `SCRIPT_MAP.md` / `RUNBOOK.md` updates, and `tests/test_critical_script_paths.py` (or replacement) in the **same** change set.

---

## 3. Quality targets

- **Module ownership** is knowable from folder + name; ambiguous files get triaged (see `plan_source_quality.py`).
- **No hidden DB writes**: high-impact paths use **`--apply`**, break-glass banners, and tests where possible; prefer central DB helpers.
- **Dry-run or read-only** defaults for **risky** or planning tools; mutations are **explicit**.
- **No secrets** in logs: env redaction via [`core/safety.py`](../src/origenlab_email_pipeline/core/safety.py) patterns; never print tokens.
- **Generated** `reports/out` and similar: **not** committed; managed with read-only planners and optional **move** archiver (not delete-by-default).
- **Tests** lock **entrypoints** (`--help` contracts) and **SAFETY** break-glass headers; regressions require intentional updates to those tests.

---

## 4. Vertical roadmap

Refactors are **staged**; the tables below are **not** a commitment to order. Use `plan_source_quality.py` to refresh file lists before a vertical PR.

### A. Outbound / campaigns

| | |
|-|-|
| **Current pain** | Large scripts and parallel import paths; gate and CSV contracts touch many files. |
| **Candidate files** (non-exhaustive) | `outbound_core.py`, `candidate_export_gate.py`, `operational_trust/`, `scripts/leads/process_broad_marketing_contacts.py`, `scripts/leads/*campaign*`, `core/outbound/*` re-exports |
| **Risk** | **High** — wrong change breaks send eligibility, suppressions, or post-send state. |
| **Expected benefit** | Thinner scripts, clearer ownership, fewer divergent import paths. |
| **First safe refactor** | **Docs + tests only**, or a **single** script thinned to call **existing** library functions (no policy change), with full `pytest` and a manual lane smoke note in PR. |

### B. `reports/out` and reporting

| | |
|-|-|
| **Current pain** | Generated tree volume; operators need **classification** and optional relocation without delete. |
| **Candidate files** | `client_report_*.py`, `scripts/reports/*`, `plan_reports_out_cleanup.py`, `archive_reports_out_generated.py`, `REPORTING.md` |
| **Risk** | **Medium** — easy to break paths expected by pack / gate. |
| **Expected benefit** | Consistent handoff layout, better planner coverage, less “unknown” clutter. |
| **First safe refactor** | Planner label tweaks or **read-only** `plan_source_quality` / `plan_reports_out_cleanup` outputs; **no** default moves. |

### C. Tatiana / lab isolation

| | |
|-|-|
| **Current pain** | `tatiana_copilot/` and `scripts/tatiana/` are large; risk of entanglement with production outbound. |
| **Candidate files** | `tatiana_copilot/`, `tatiana_review_cohort.py`, `tatiana_voice_cohort.py`, `scripts/tatiana/*`, `scripts/dataset/*`, `scripts/ml/*` (as lab) |
| **Risk** | **Medium** — model/API drift, optional deps. |
| **Expected benefit** | Clearer boundaries; optional packaging or `extras` for lab-only code later. |
| **First safe refactor** | **Boundary doc** + planner bucket coverage only (Stage **6E1**). |
| **Canonical boundary** | [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) — what Tatiana/lab is and is not, vs daily outbound. |

**Stage 6C1 (outbound, narrow):** logic for ``scripts/leads/process_broad_marketing_contacts.py`` was **extracted** to ``origenlab_email_pipeline.core.outbound.broad_marketing_contacts`` (pure row processing, summaries). The **CLI** remains the operator entrypoint; **no** change to default paths, column contracts, or stdout shape intended—verify with tests. Further outbound refactors: one script at a time, same policy.

**Stage 6C2 (outbound, narrow):** ``scripts/qa/export_do_not_repeat_master.py`` was **thinned**; merge, counting, and summary formatting live in ``origenlab_email_pipeline.core.outbound.do_not_repeat_master``. The **CLI** remains the daily entrypoint; **read-only** SQLite; **no** change to output filenames, CSV columns, or summary JSON fields intended (verify with tests).

**Stage 6D1 (reports vertical, narrow):** shared ``reports/out`` **classification** and planning helpers (buckets, ``FileEntry`` aggregation, archiver eligibility) live in ``origenlab_email_pipeline.core.reports_out``. ``plan_reports_out_cleanup.py`` and ``archive_reports_out_generated.py`` remain **operator entrypoints**; JSON/stdout and **dry-run default** for the archiver are unchanged in intent (verify with tests).

**Stage 6E1 (Tatiana / lab, narrow):** **documentation and classification only** — no runtime change to Tatiana or OpenAI call sites. See [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md). `plan_source_quality.py` heuristics include ``tatiana_copilot/``, ``scripts/tatiana/``, ``scripts/dataset/``, ``scripts/ml/``, root ``tatiana_*.py`` modules, and ``openai_chat_generator`` in the path. **Tatiana/lab is not** daily production outbound.

**Stage 6E2 (future):** optional refactors of large Tatiana modules (e.g. split or isolate optional deps) with **tests** — **not** the same change set as 6E1.

**Phase 5P / Stage 6F1 (mart, narrow):** CLI orchestration for ``scripts/mart/build_business_mart.py`` was **extracted** to ``origenlab_email_pipeline.core.mart.build_business_mart_cli`` (``run_build_business_mart_from_argv``). The **operator entrypoint** remains ``scripts/mart/build_business_mart.py`` (path unchanged); the **SAFETY (break-glass)** banner stays on the script. **CLI flags**, **stdout** shape (mode lines, ``Done.``, ``created_at|…``), and ``--rebuild`` **DELETE** semantics for mart tables are unchanged in intent (verify with ``test_build_business_mart.py``, ``test_build_business_mart_phase2.py``). Stage build logic remains in ``core/mart/build_runner.py``.

**Future Stage 6C+ (preview):** pick the **next** vertical or script, apply a **small** internal-only refactor, then re-run the full test suite and readiness scripts.

---

## 5. Planning tools (read-only)

| Tool | Role |
|------|------|
| `plan_source_quality.py` | Text-only scan: size, heuristics, import hints, vertical buckets — **guidance, not authority**. |
| `plan_script_consolidation.py` | Script index vs `SCRIPT_MAP.md` — no file changes. |
| `plan_reports_out_cleanup.py` | `reports/out` tree buckets — no file changes; bucket rules also in `core.reports_out`. |

**Warning:** Planners can be wrong on edge cases. Operator truth remains **code** + **SCRIPT_MAP** + **tests**.

---

## 6. Import convention (Stage 6B)

- Import implementations from the top-level `origenlab_email_pipeline` modules; `core` holds only real shared infrastructure (`core.safety`, `core.step_runner`, `core.reports_out`, `core.research_automation`, `core.mart` / `core.outbound` builders). The re-export facade layer was removed in the 2026-08 commercial platform reset.
- **Existing** `from origenlab_email_pipeline.candidate_export_gate` (and similar) **remain valid** — do not mass-rewrite.
- A **per-vertical** migration in **Stage 6C+** can switch one subtree at a time with tests, not a repo-wide sed.

This keeps **compatibility** while nudging new work toward a **stable** `core.*` story.
