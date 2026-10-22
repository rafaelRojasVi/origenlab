# CRUD & mutation safety (email-pipeline)

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-04-25

**Purpose:** Rules for **Create / Read / Update / Delete** style operations and anything that **mutates** data (SQLite, Postgres, Gmail, or filesystem outputs). This is **policy documentation**; behavior remains in code and in [`RUNBOOK.md`](RUNBOOK.md).

**Companion:** [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md), [`SCRIPT_MAP.md`](SCRIPT_MAP.md) (break-glass and `--apply`), [`SCRIPT_INVENTORY.md`](SCRIPT_INVENTORY.md), [`QUALITY_AND_REFACTOR_STRATEGY.md`](QUALITY_AND_REFACTOR_STRATEGY.md) (import conventions: **new** code should prefer `core.*` re-exports; no mass rewrites), [`AGENTS.md`](../AGENTS.md) (agent hard rules), `reports/out/active/current/manageability_improvement_plan_20260518.md` (local report `reports/out/active/current/manageability_improvement_plan_20260518.md`, not in the repo) (proposed standard mutation CLI contract: `--dry-run` default, `--apply`, `--operator`, `--source-artifact`, `--reason`).

**Read-only status before mutations:** `uv run python scripts/qa/operator_status.py` (see `manifest.json` (local report `reports/out/active/current/manifest.json`, not in the repo)).

---

## 1. Runtime truth

- **SQLite** (typically `ORIGENLAB_SQLITE_PATH` or `~/data/origenlab-email/sqlite/emails.sqlite` via settings) is the **primary runtime** store for mail archive, marts, leads, outbound sidecars, and suppressions.
- **Gmail Sent** messages ingested into the **`emails`** table (correct `source_file` and Sent **folder** labels) are **outbound safety truth** for the shared export gate and preflight.
- **Postgres** is **optional** — migration loaders, Alembic, optional outbound audit. It is **not** the day-to-day OLTP for the two outbound lanes as of this document.

---

## 2. Read-only operations (default safe for review)

- **Audits** that only SELECT and write CSV/stdout (e.g. gate audit, overlap exports).
- **Exports** of send lists, research queues, do-not-repeat lists (files under `reports/out/…`).
- **Validation:** `validate_campaign_csvs.py`, contract tests, `--help` on any CLI.
- **Readiness:** `check_outbound_readiness.py`, `check_reproducibility.py` (no DB write; may open SQLite **read-only** to inspect schema).
- **Reports layout planning:** `plan_reports_out_cleanup.py` (read-only scan of `reports/out`; optional JSON report path; does not touch the tree). Bucket rules are implemented in `origenlab_email_pipeline.core.reports_out` and imported by the planner and archiver; behavior is unchanged from pre–Stage 6D1 in intent.
- **Script sprawl planning:** `plan_script_consolidation.py` (read-only scan of `scripts/*.py` vs `SCRIPT_MAP.md`; optional JSON; does not edit scripts).
- **Ingest of documentation** (reading markdown, not mutating live mail).

**Rule:** Prefer read-only paths when exploring a new issue.

---

## 3. Create operations (new rows, new files)

Examples: insert into `outreach_contact_state`, add `contact_email_suppression` / domain suppressions, **import** into `lead_contact_research` from reviewed CSV, create new `reports/out/active/current/*` campaign files.

**Rules**

- **Validate input first** (`validate_campaign_csvs`, schema checks) before any `--apply` import.
- **Prefer dry-run** when the CLI offers it (default is often dry-run for imports and backfills).
- **Log / manifest** outputs (paths, run IDs, `summary.json` where used) so runs are auditable.
- **Never infer “contacted”** (or other blocking state) without **source evidence** (e.g. reviewed manifest, explicit operator action, or Gmail Sent alignment per runbook). Do not “guess” from old CSVs alone.

---

## 4. Update operations (existing state)

Examples: lead scoring, upstream reconcile, **post-send** `outreach_contact_state` updates, research row updates, mart/commercial **rebuilds** that replace derived rows.

**Rules**

- **Explicit operator and source** (`--updated-by`, `--source`, campaign slug) where the CLI provides them.
- **Reviewed CSV or send manifest** as the batch boundary when updating from a campaign; no silent bulk updates without a named artifact.
- **Preserve evidence** (paths to CSV, message IDs, timestamps) so a later audit can answer *why* a row changed.

---

## 5. Delete operations (highest risk)

Examples: `purge_*.py`, `build_business_mart` / `build_commercial_intel` **rebuild** patterns, **Postgres** `TRUNCATE` / `DELETE` in migration loaders, `extract_attachment_text` **delete** of `attachment_extracts`, `dedupe_emails_by_message_id`.

**Rules**

- **Break-glass only** — not part of the [daily lanes](SCRIPT_MAP.md#daily-lanes).
- **Backup first** the SQLite file (or Postgres dump) when operating on a database you care about.
- **Dry-run first** when the tool supports it; read the printed counts.
- **Never** rely on a hidden default that deletes: destructive tools require **explicit** flags or subcommands.
- **Review** [`SCRIPT_MAP.md` — Break-glass scripts](SCRIPT_MAP.md#break-glass-scripts); file **SAFETY** headers on those scripts are mandatory reading.

---

## 6. `--apply` flag policy

- CLIs that mutate state should use **`--apply`** to mean “really write” and default to **dry-run** or print-only when feasible (see each script’s `--help` and [SCRIPT_MAP](SCRIPT_MAP.md)).
- **`--apply` is always risky** — it appears in the break-glass / import sections of `SCRIPT_MAP.md`.
- **Daily lanes** document where `--apply` is **expected** (e.g. precision `process-reviewed` and import paths); do not add `--apply` in scripts that are not part of that runbook.

### Phase 2C pilot: `mark_outreach_state.py` (implemented)

[`scripts/leads/mark_outreach_state.py`](../scripts/leads/mark_outreach_state.py) is the first **operator sidecar** CLI on the shared mutation contract:

| Behavior | Detail |
|----------|--------|
| **Default** | Dry-run / preview only — prints `contact_email_norm`, `old_state`, `new_state`, `source`, `updated_by`, `reason` (and optional `notes`). **No** `commit`. |
| **Writes** | Require **`--apply`** plus **`--updated-by`** or **`--operator`**, **`--source`** or **`--source-artifact`**, and **`--reason`**. |
| **Aliases** | `--operator` ↔ `--updated-by`; `--source-artifact` ↔ `--source` (must not conflict if both are set). |
| **Scope** | SQLite `outreach_contact_state` only — does **not** ingest Sent or mutate Gmail. |

**Operator flow:** run without `--apply` and review stdout → re-run the same args with **`--apply`** when correct. Regression tests: [`test_mark_outreach_state_cli.py`](../tests/test_mark_outreach_state_cli.py).

---

## 7. `reports/out/` policy

- **Default:** almost everything under `reports/out/` is **gitignored**; only `README.md` and `.gitkeep` are tracked. Treat paths as **local and possibly sensitive**.
- **`active/current/`** — the **current** campaign inputs/outputs for the two outbound lanes (see [SCRIPT_MAP](SCRIPT_MAP.md)).
- **`active/`** (other than `current/`) — other batches, overlap exports, or evidence; not assumed “today’s” files.
- **`archive/`** — **historical** campaign moves, dated slugs, old sends.
- **`reference/`** — small, long-lived comparison or evidence CSVs (optional; team convention). **Only** put material here **intentionally** as long-lived evidence; it is not a general dump.
- **`tmp/`** (if used) — **scratch**; safe to delete locally once not needed.
- **Full `full_*` report runs** — timestamped HTML/JSON; keep or prune per disk and policy.

**Cleanup and deletion policy**

- **Any** cleanup of `reports/out` (delete, move, or rename) must start with a **read-only** pass using [`plan_reports_out_cleanup.py`](../scripts/qa/plan_reports_out_cleanup.py) so you see bucket counts, largest files, and a proposed review map before doing anything. The planner labels include **`active_current`** (under `active/current/`), **`active_workspace_misc`** (other `active/…` trees), **`client_pack_latest`**, and the existing tmp/lab/archive/reference buckets so “unknown” is not overloaded.
- **Controlled moves (no delete):** [`archive_reports_out_generated.py`](../scripts/tools/archive_reports_out_generated.py) uses the **same path buckets** as the planner; it **defaults to dry-run** and only **moves** selected files into `reports/out/archive/manual_cleanup/YYYY-MM-DD_<slug>/` when run with **`--apply`** and a non-empty **`--archive-slug`**. It does **not** delete, does not follow symlinks, and does not write outside the chosen `reports/out` root. **Never** use **`--allow-active-current`** or **`--allow-reference`** during an active campaign without verifying the selection. Run the planner first; treat this as break-glass (see [`SCRIPT_MAP.md`](SCRIPT_MAP.md) break-glass table).
- **Deletion or ad-hoc moving** of paths in `reports/out` beyond the archive tool is still a **separate explicit decision**; the planner only **inspects** and can write an optional **JSON** report to a path you choose — it does **not** change `reports/out`.
- **Do not delete** `active/current` contents **during an active campaign** unless the runbook or operator explicitly retires that workspace (treat it as the canonical working set until archived).
- For cleanup planning, never delete in git what was never committed; on disk, use `archive` / `tmp` / `reference` conventions above before bulk removal (any future automation should follow the same order: **plan first**, then an explicit **apply** step in a different tool or stage).

**Script entrypoint and consolidation policy**

- **Deletion, consolidation, or re-homing** of a script in `apps/email-pipeline/scripts/` must start with a **read-only** pass using [`plan_script_consolidation.py`](../scripts/qa/plan_script_consolidation.py) so you see primary bucket tags, doc/test references, and wrapper/duplicate *candidates* before any change.
- **Wrappers and deprecation** (thin root scripts, alternate paths) should be **designed, documented, and sometimes dual-path tested** before **deleting** an entrypoint; see [`test_critical_script_paths.py`](../tests/test_critical_script_paths.py) and [`SCRIPT_MAP.md`](SCRIPT_MAP.md).
- **Break-glass** scripts (purge, send, large rebuilds, `migrate/`, `extract_attachment_text`, etc.) must **not** be deleted or “hidden” behind defaults without a **documented replacement**, operator guidance in `SCRIPT_MAP.md`, and **regression tests** for any behavior that remains.
- **Daily lane** scripts and paths listed as **OPS_DAILY** in `SCRIPT_MAP.md` must **not** be **renamed** (or have their public path removed) until **`SCRIPT_MAP.md`**, [`RUNBOOK.md`](RUNBOOK.md) where needed, and **tests** that assert paths are all updated in the same change.

**Shared helpers:** [`core/safety.py`](../src/origenlab_email_pipeline/core/safety.py) provides **value-free** env presence labels (`<set>` / `<unset>`) and optional `require_apply_for_mutation` / break-glass **string** helpers for future CLIs—**not** wired into existing mutation scripts by default. The [`plan_script_consolidation.py`](../scripts/qa/plan_script_consolidation.py) tool now classifies former “unknown” small scripts (e.g. bootstrap, supplier workbook validate, ChileCompra extract) and **compatibility root wrappers** explicitly; **deletion** of any path remains a **separate** approved change with tests, not a planner output alone.

**Regression tests:** [`test_operator_entrypoint_contracts.py`](../tests/test_operator_entrypoint_contracts.py) locks the **operator surface** in CI: `scripts` daily/planner `uv run … --help` exit 0, break-glass files still carry `SAFETY` / `BREAK-GLASS` (or equivalent) in the file header, and the four lead-account **root** compatibility shims still document `scripts/leads/advanced/…`. Changing or removing a listed path requires updating that test and the docs in the same change; **future deletion** of a wrapper is still a dedicated stage, not a silent cleanup.
