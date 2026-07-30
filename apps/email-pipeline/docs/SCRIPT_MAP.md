# Email pipeline — script map (canonical operator index)

Status: canonical  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-04 (post-#93 refactor checkpoint — script surface classification table + `origenlab` CLI index; see [`audits/CODEBASE_SIMPLIFICATION_AUDIT_20260602.md`](audits/CODEBASE_SIMPLIFICATION_AUDIT_20260602.md))

**This document is the canonical operator map** for outbound and campaign work (tags, break-glass, removed paths). **Run commands via the unified CLI first** — behavior lives in code and [`RUNBOOK.md`](RUNBOOK.md).

```bash
cd apps/email-pipeline
uv run origenlab --help
# status · daily-health · refresh-safety · validate-csvs · check-readiness · post-send-digest
# export-dnr · ndr-review · contact-universe-review · audit-overlap  (+ build-mart · gmail-ingest-help — see OPERATOR_COMMAND_SURFACE)
# fallback: uv run python -m origenlab_email_pipeline.cli <subcommand>
```

Detail and script fallbacks: [`OPERATOR_COMMAND_SURFACE.md`](OPERATOR_COMMAND_SURFACE.md).

**Active operator UI (2026-06-04):** **`apps/dashboard`** + **`apps/api`** (:8001 mirror). **No** Streamlit launch surfaces — retired in #75–#83; not listed as active commands below.

**Full-tree planner (read-only):** `uv run python scripts/qa/plan_script_consolidation.py` — classifies all **180** `scripts/**/*.py` files into buckets (`daily`, `audit_readonly`, `break_glass`, `lab_archive`, …). **Does not change files.** Use before deprecating or moving entrypoints.

---

## Canonical classification table (operator index)

**Columns:** `script path` | `category` | `entrypoint / importers` | `reads` | `writes` | `risk` | `recommended command` | `notes`

**Category legend**

| Category | Meaning |
|----------|---------|
| **active_operator_command** | Normal daily/weekly ops when labeled in RUNBOOK |
| **read_only_qa_report** | Audits, planners, health — no production writes by default |
| **import_ingest** | Loads external data into SQLite (Gmail, CSV, workbook) |
| **write_apply_send_purge_dangerous** | `--apply`, send, purge, rebuild, or Postgres load |
| **break_glass_manual** | Intentional high-blast-radius; dry-run default where implemented |
| **superseded_by_origenlab** | Prefer `uv run origenlab <subcommand>`; script path is advanced fallback |
| **parked_legacy** | Historical, lab, or removed — not for new operator work |
| **unknown_review** | Triage with owner before move/delete |

### Unified CLI (`origenlab`) — prefer these first

Mapped from [`operator_cli/constants.py`](../src/origenlab_email_pipeline/operator_cli/constants.py) `SUBCOMMAND_SCRIPTS`. Passthrough flags after `--` where supported.

| script path | category | entrypoint / importers | reads | writes | risk | recommended command | notes |
|-------------|----------|------------------------|-------|--------|------|---------------------|-------|
| `scripts/qa/operator_status.py` | superseded_by_origenlab | `origenlab status` | SQLite, `reports/out` | — | low | `uv run origenlab status` | READY / CAUTION / BLOCKED; not send approval |
| `scripts/qa/run_daily_health_report.py` | superseded_by_origenlab | `origenlab daily-health` | SQLite, mirror JSON hints | `reports/out` only | low | `uv run origenlab daily-health` | Not full post-send loop |
| `scripts/qa/refresh_outbound_safety_memory.py` | superseded_by_origenlab | `origenlab refresh-safety` | SQLite | `reports/out` exports | medium | `uv run origenlab refresh-safety` | Anti-repeat chain; stops on hard failure |
| `scripts/qa/validate_campaign_csvs.py` | superseded_by_origenlab | `origenlab validate-csvs` | CSV files | — | low | `uv run origenlab validate-csvs -- --file …` | Contract validation |
| `scripts/qa/check_outbound_readiness.py` | superseded_by_origenlab | `origenlab check-readiness` | SQLite, env | — | low | `uv run origenlab check-readiness` | Pre-send readiness |
| `scripts/qa/build_post_send_digest.py` | superseded_by_origenlab | `origenlab post-send-digest` | SQLite | `reports/out` only | low | `uv run origenlab post-send-digest` | After `audit_contacted_universe` |
| `scripts/qa/export_do_not_repeat_master.py` | superseded_by_origenlab | `origenlab export-dnr` | SQLite | `reports/out` only | low | `uv run origenlab export-dnr` | Volume lane DNR input |
| `scripts/qa/build_ndr_review_queue.py` | superseded_by_origenlab | `origenlab ndr-review` | SQLite (read) | `reports/out` only | low | `uv run origenlab ndr-review` | **No** suppression apply |
| `scripts/leads/build_contact_universe_review.py` | superseded_by_origenlab | `origenlab contact-universe-review` | SQLite, active/current CSVs, archive research/campaign CSVs, campaign JSON | `reports/out` only | low | `uv run origenlab contact-universe-review --json` | Read-only contact universe review; no Gmail/Postgres/mirror/send mutations; inventory metadata only by default; supports `--focus-domain` |
| `scripts/qa/export_contacted_lead_overlap_audit.py` | superseded_by_origenlab | `origenlab audit-overlap` | SQLite | `reports/out` only | low | `uv run origenlab audit-overlap` | Pre-send overlap |
| `scripts/qa/audit_module_facades.py` | superseded_by_origenlab | `origenlab audit-facades` | `src/` scan | — | low | `uv run origenlab audit-facades` | Read-only facade audit |
| `scripts/qa/audit_institution_grouping.py` | superseded_by_origenlab | `origenlab audit-institution-grouping` | SQLite mart | `reports/out` only | low | `uv run origenlab audit-institution-grouping` | Institution/domain grouping — **not** send safety |
| `scripts/qa/audit_email_mart_feature_scan.py` | superseded_by_origenlab | `origenlab audit-email-mart-feature-scan` | SQLite emails + features | — | low | `uv run origenlab audit-email-mart-feature-scan` | **Read-only** parity audit; no mart table writes |
| `scripts/mart/build_business_mart.py` | superseded_by_origenlab | `origenlab build-mart` | SQLite raw | SQLite mart (**rebuild deletes**) | **high** | `uv run origenlab build-mart -- --help` first | Break-glass `--rebuild` |
| `scripts/mart/build_email_mart_features.py` | superseded_by_origenlab | `origenlab build-email-mart-features` | SQLite raw | SQLite `email_mart_features` | medium | `uv run origenlab build-email-mart-features` | **Dry-run** default; **`--apply`** writes missing/stale rows; **`--missing-only`** inserts new rows only; **`daily-core --apply`** runs missing-only apply before feature-backed mart rebuild |
| `scripts/commercial/build_commercial_intel_v1.py` | superseded_by_origenlab | `origenlab build-commercial-intel` | SQLite | SQLite `commercial_*` | **high** | `uv run origenlab build-commercial-intel` | `--rebuild` break-glass |
| `scripts/ingest/05_workspace_gmail_imap_to_sqlite.py` | superseded_by_origenlab | `origenlab gmail-ingest` / `gmail-ingest-folders` | Gmail IMAP | SQLite `emails` | **medium** | `uv run origenlab gmail-ingest` | Rejects `--replace-source`; Sent required for gate |
| `scripts/sync/sync_dashboard_postgres_mirror.py` | superseded_by_origenlab | `origenlab mirror-dashboard` | SQLite | Postgres mirror (**`--apply`**) | **high** | `uv run origenlab mirror-dashboard` (dry-run default) | Parked mirror path; not send truth |
| *(orchestrator)* | active_operator_command | `origenlab refresh-dashboard` | Multi-step | SQLite + reports + optional PG | **high** | `uv run origenlab refresh-dashboard` (plan default) | `--apply` runs ingest→mart→commercial→safety→mirror |
| *(orchestrator)* | active_operator_command | `origenlab auto-refresh-mail` | Gmail IMAP probe + optional daily-core | state file + optional SQLite | medium | `uv run origenlab auto-refresh-mail --once` | Debounced; `--apply` runs `daily-core --apply` — see [`pipeline/MAIL_AUTO_REFRESH.md`](pipeline/MAIL_AUTO_REFRESH.md) |
| *(orchestrator)* | active_operator_command | `origenlab auto-mirror-dashboard` | manifest + mail state gates + optional mirror | state file + optional Postgres | **high** | `uv run origenlab auto-mirror-dashboard --once` | Separate ~15m loop; `--apply` requires `--allow-non-scratch-postgres` — [`pipeline/DASHBOARD_AUTO_MIRROR.md`](pipeline/DASHBOARD_AUTO_MIRROR.md) |
| *(orchestrator)* | read_only_qa_report | `origenlab operator-automation-status` | local state files + user crontab | — | low | `uv run origenlab operator-automation-status` | Read-only health for loops A+B; `--json`; `--skip-cron-inspection` |
| `scripts/operator/run_auto_refresh_mail.sh` | active_operator_command | cron wrapper | — | optional SQLite via CLI | medium | `scripts/operator/run_auto_refresh_mail.sh` | ~3m cron; calls `auto-refresh-mail --once --apply` — [`pipeline/OPERATOR_CRON.md`](pipeline/OPERATOR_CRON.md) |
| `scripts/operator/run_auto_mirror_dashboard.sh` | active_operator_command | cron wrapper | — | optional Postgres via CLI | **high** | `scripts/operator/run_auto_mirror_dashboard.sh` | ~15m cron; calls `auto-mirror-dashboard --once --apply --allow-non-scratch-postgres` |

### Daily outbound lanes (scripts — no `origenlab` wrapper yet)

| script path | category | entrypoint / importers | reads | writes | risk | recommended command | notes |
|-------------|----------|------------------------|-------|--------|------|---------------------|-------|
| `scripts/qa/prepare_outbound_campaign_workspace.py` | active_operator_command | RUNBOOK daily outbound | — | `reports/out/active/current/` | low | `uv run python scripts/qa/prepare_outbound_campaign_workspace.py` | **Not** `prepare_active_workspace.py` |
| `scripts/leads/process_broad_marketing_contacts.py` | active_operator_command | RUNBOOK volume lane | SQLite | `reports/out` CSVs | medium | `uv run python scripts/leads/process_broad_marketing_contacts.py` | Shared gate; no send |
| `scripts/leads/run_current_campaign_pipeline.py` | active_operator_command | RUNBOOK precision lane | SQLite, CSVs | SQLite with `--apply` | **high** | `uv run python scripts/leads/run_current_campaign_pipeline.py --stage …` | `process-reviewed --apply` writes research |
| `scripts/leads/mark_sent_batch_contacted.py` | active_operator_command | Post-send loop | SQLite | `outreach_contact_state` | medium | `uv run python scripts/leads/mark_sent_batch_contacted.py …` | Sidecar only |
| `scripts/leads/import_lead_contact_research_csv.py` | import_ingest | Precision lane | CSV | `lead_contact_research` | **high** | Dry-run first; `--apply` to write | Primary DeepSearch import path |
| `scripts/leads/export_next_marketing_recipients.py` | active_operator_command | Lead lane export | SQLite | `reports/out` | medium | `uv run python scripts/leads/export_next_marketing_recipients.py` | Gate + preflight |
| `scripts/leads/build_archive_send_batch.py` | active_operator_command | Archive lane | SQLite | `reports/out` | **high** | Audit-only default; `--build-batch` for full CSVs; apply needs approval | Alternate lane — not daily mental model |
| `scripts/research/run_deep_research_prospecting.py` | active_operator_command | Research cadence | SQLite, OpenAI | `reports/out` artifacts | medium | `uv run python scripts/research/run_deep_research_prospecting.py` | Stops before send |

### Read-only QA / planners / audits

| script path | category | entrypoint / importers | reads | writes | risk | recommended command | notes |
|-------------|----------|------------------------|-------|--------|------|---------------------|-------|
| `scripts/qa/plan_reports_out_cleanup.py` | read_only_qa_report | SCRIPT_MAP, CRUD_SAFETY | `reports/out` tree | — | low | `uv run python scripts/qa/plan_reports_out_cleanup.py` | Plan before any cleanup |
| `scripts/qa/plan_script_consolidation.py` | read_only_qa_report | This doc, audits | `scripts/` | — | low | `uv run python scripts/qa/plan_script_consolidation.py` | 180 scripts bucketed |
| `scripts/qa/plan_source_quality.py` | read_only_qa_report | Refactor planning | `src/`, `scripts/` | — | low | `uv run python scripts/qa/plan_source_quality.py` | Heuristic LOC scan |
| `scripts/qa/plan_function_surface.py` | read_only_qa_report | Refactor / cleanup planning | `src/`, `scripts/` | `reports/local` only | low | `uv run python scripts/qa/plan_function_surface.py` | AST + size/risk planner — **not** deletion authority |
| `scripts/qa/plan_import_surface.py` | read_only_qa_report | Refactor / cleanup planning | `src/`, `scripts/`, `tests/`, `docs/` | `reports/local` only | low | `uv run python scripts/qa/plan_import_surface.py` | Import/reference planner — **not** deletion authority |
| `scripts/reports/generate_client_report.py` | read_only_qa_report | Client pack / PDF reports | SQLite mart | `reports/out` | low | `uv run python scripts/reports/generate_client_report.py` | Large report generator — not send path |
| `scripts/reports/build_leads_client_pack.py` | read_only_qa_report | Client pack assembly | SQLite, CSV inputs | `reports/out` | low | `uv run python scripts/reports/build_leads_client_pack.py` | Works with generate_client_report |
| `scripts/reports/run_all_reports.py` | read_only_qa_report | Report batch driver | SQLite | `reports/out` | low | `uv run python scripts/reports/run_all_reports.py` | Orchestrates report scripts |
| `scripts/reports/generate_business_filter_report.py` | read_only_qa_report | Filter diagnostics | SQLite | `reports/out` | low | `uv run python scripts/reports/generate_business_filter_report.py` | Read-only report |
| `scripts/reports/build_ml_report.py` | read_only_qa_report | ML/Tatiana lab report | SQLite / exports | `reports/out` | low | `uv run python scripts/reports/build_ml_report.py` | **Lab boundary — not daily outbound** — see [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) |
| `scripts/qa/check_reproducibility.py` | read_only_qa_report | REPRODUCIBILITY.md | env, optional DB RO | — | low | `uv run python scripts/qa/check_reproducibility.py` | New machine checks |
| `scripts/qa/audit_prospectos_safety_drift.py` | read_only_qa_report | Post-send loop | SQLite | `reports/out` | low | `uv run python scripts/qa/audit_prospectos_safety_drift.py` | Drift ≠ send failure |
| `scripts/qa/audit_institution_grouping.py` | read_only_qa_report | Institution explorer prep | SQLite mart | `reports/out` only | low | `uv run origenlab audit-institution-grouping` | Domain/org grouping — **not** send safety |
| `scripts/leads/audit_contacted_universe.py` | read_only_qa_report | Post-send loop | SQLite | exclusion CSVs in `reports/out` | low | Before `post-send-digest` | Rebuilds exclusion sets |
| `scripts/validate_supplier_workbook.py` | read_only_qa_report | Supplier import prep | `.xlsx` | — | low | `uv run python scripts/validate_supplier_workbook.py -x …` | No DB writes |

### Import / ingest (non-Gmail)

| script path | category | entrypoint / importers | reads | writes | risk | recommended command | notes |
|-------------|----------|------------------------|-------|--------|------|---------------------|-------|
| `scripts/import_supplier_workbook.py` | import_ingest | Supplier ops | `.xlsx` | SQLite supplier tables | medium | `uv run python scripts/import_supplier_workbook.py …` | Library: `supplier_workbook.py` |
| `scripts/leads/import_operator_outreach_blocklist.py` | import_ingest | Suppression ops | CSV | suppressions | **high** | Explicit operator approval | Sidecar writes |
| `scripts/ingest/02_mbox_to_sqlite.py` | import_ingest | Legacy ingest | mbox | SQLite `emails` (destructive patterns) | **high** | Break-glass / migration only | Not daily Workspace path |
| `scripts/ingest/04_imap_to_sqlite.py` | import_ingest | Legacy IMAP | IMAP | SQLite | **high** | Prefer `05_workspace_gmail_imap_to_sqlite.py` | Older ingest |

### Postgres mirror / migrate (parked optional path)

| script path | category | entrypoint / importers | reads | writes | risk | recommended command | notes |
|-------------|----------|------------------------|-------|--------|------|---------------------|-------|
| `scripts/migrate/sqlite_*_to_postgres.py` | write_apply_send_purge_dangerous | EXPERIMENTAL_PARKED | SQLite | Postgres **truncate/load** | **critical** | Scratch Postgres first | Never send approval |
| `scripts/sync/sync_*_postgres_mirror.py` | write_apply_send_purge_dangerous | `mirror-dashboard`, ops shell | SQLite | Postgres mirror | **high** | `origenlab mirror-dashboard` dry-run | Read-only mirror for dashboard |
| `scripts/qa/verify_*_postgres_mirror.py` | read_only_qa_report | Mirror QA | Postgres mirror | — | low | After sync only | Parity checks |

### Break-glass / send / purge (manual only)

| script path | category | entrypoint / importers | reads | writes | risk | recommended command | notes |
|-------------|----------|------------------------|-------|--------|------|---------------------|-------|
| `scripts/qa/send_inline_html_email_via_gmail_api.py` | break_glass_manual | Optional send | SQLite, templates | **Gmail send** | **critical** | Dry-run / build-only unless intentional send | Real mail |
| `scripts/tools/purge_*_from_sqlite.py` | break_glass_manual | Maintenance | SQLite | **DELETE** rows | **critical** | `--apply` required | Multiple tables |
| `scripts/tools/flag_ndr_bounces_from_contacto.py` | break_glass_manual | POST_SEND_SAFE_LOOP | SQLite, Gmail rows | suppressions with `--apply` | **high** | Dry-run default; allowlist apply | Prefer `--emails-file` + `--only-code` |
| `scripts/tools/archive_reports_out_generated.py` | break_glass_manual | reports/out hygiene | `reports/out` | **moves** files with `--apply` | medium | Dry-run default | No deletes |
| `scripts/maintenance/dedupe_canonical_gmail_messages.py` | break_glass_manual | Gmail hygiene | SQLite | **DELETE** dup emails | **high** | `--apply --ack-sqlite-backup` | Dry-run default |

### Parked / legacy / removed (do not use for new work)

| script path | category | entrypoint / importers | reads | writes | risk | recommended command | notes |
|-------------|----------|------------------------|-------|--------|------|---------------------|-------|
| `scripts/tatiana/*`, `scripts/dataset/*`, `scripts/ml/*`, `scripts/leads/campaigns/*` | parked_legacy | [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) | varies | reports/lab | low–medium | See lab docs | **Lab boundary — not daily outbound** |
| `scripts/reports/build_ml_report.py` | parked_legacy | [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) | SQLite / exports | `reports/out` | low | `uv run python scripts/reports/build_ml_report.py` | **Lab boundary — not daily outbound** (also in read-only QA table below) |
| `scripts/leads/advanced/prepare_active_workspace.py` | parked_legacy | Lead hunt / REPORTING | `reports/out/active/` | archives/moves (**`--apply`**) | medium | Hunt workflows only; plan-only default | **Not** outbound `current/` prep |
| `scripts/leads/advanced/export_leads_spanish_csvs.py` | parked_legacy | Spanish `_es` CSV helper (plan-only default) | CSV inputs | `reports/out` `*_es.csv` (**`--write-outputs`**) | low | Plan-only default; **`--write-outputs`** to write | **Not** daily outbound; **`--export` = input path**; not send approval |
| `scripts/leads/advanced/run_contact_hunt_web_server.py` | parked_legacy | Local CSV HTTP server (localhost default) | `reports/out/leads_*.csv` | — (serve only) | low | **`LEADS_WEB_PASS`** or **`--pass`**; **`--lan`** for LAN | **Not** daily outbound / not `apps/api`; not send approval |
| *(removed)* `scripts/qa/build_legacy_contacts_2016_2019_review.py` | parked_legacy | — | — | — | — | Library `legacy_contacts_2016_2019.py` | Removed Phase 5R |
| *(removed)* `business_mart_app.py`, `streamlit_*` UI | parked_legacy | — | — | — | — | **`apps/dashboard` + `apps/api`** | Removed 2026-06-04 (#75–#77) |
| `scripts/_bootstrap.py`, `scripts/_script_warnings.py` | parked_legacy | Imported by scripts | — | — | low | *(internal)* | Not operator entrypoints |

**Remaining scripts (~75 maintenance / 17 lab):** see planner output and folder tables below. **Do not delete** without doc/test updates and explicit approval.

---

**Reproducibility, safety, inventory:** [REPRODUCIBILITY.md](REPRODUCIBILITY.md) (machine setup) · [CRUD_SAFETY.md](CRUD_SAFETY.md) (read/create/update/delete rules) · [SCRIPT_INVENTORY.md](SCRIPT_INVENTORY.md) (group-level script classification) · read-only [check_reproducibility.py](../scripts/qa/check_reproducibility.py) · read-only [plan_reports_out_cleanup.py](../scripts/qa/plan_reports_out_cleanup.py) (scan `reports/out` before any cleanup; does not change files; buckets include `active_current`, `active_workspace_misc`, `client_pack_latest`, tmp/lab/archive/reference, etc.) · [archive_reports_out_generated.py](../scripts/tools/archive_reports_out_generated.py) (optional **move** of selected generated files into `archive/manual_cleanup/…`; **dry-run** default, `--apply` + `--archive-slug` to execute; no deletes) · read-only [plan_script_consolidation.py](../scripts/qa/plan_script_consolidation.py) (classify `scripts/` sprawl before deprecating, wrapping, or deleting entrypoints; does not change files) · read-only [plan_source_quality.py](../scripts/qa/plan_source_quality.py) (heuristic `src/` + `scripts/` size/vertical scan; planning only) · read-only [plan_function_surface.py](../scripts/qa/plan_function_surface.py) (AST size/risk + bucket summaries; planning only) · read-only [plan_import_surface.py](../scripts/qa/plan_import_surface.py) (import/reference edges; **use both before file moves**; neither proves deletion safety) · unknown-review classification [audits/UNKNOWN_REVIEW_SURFACE_CLASSIFICATION_20260605.md](audits/UNKNOWN_REVIEW_SURFACE_CLASSIFICATION_20260605.md) (planner v2 smoke — **not deletion authority**) · [`QUALITY_AND_REFACTOR_STRATEGY.md`](QUALITY_AND_REFACTOR_STRATEGY.md) (refactor rules; **new** code should **prefer** `core.*` imports where re-exports exist; no mass rewrites yet).

**Stage 6D1 (reports / `reports/out`):** path **classification** and planner aggregations are shared in [`core/reports_out.py`](../src/origenlab_email_pipeline/core/reports_out.py); [`plan_reports_out_cleanup.py`](../scripts/qa/plan_reports_out_cleanup.py) and [`archive_reports_out_generated.py`](../scripts/tools/archive_reports_out_generated.py) remain the **operator entrypoints**; archiver **dry-run** default and move-only semantics are unchanged in intent.

**Canonical `reports/out` cleanup flow (plan → move → verify):**

1. [`scripts/qa/plan_reports_out_cleanup.py`](../scripts/qa/plan_reports_out_cleanup.py) — **read-only** scan; buckets (`active_current`, `client_pack_latest`, tmp/lab/archive, …); **no file changes**.
2. [`scripts/tools/archive_reports_out_generated.py`](../scripts/tools/archive_reports_out_generated.py) — **dry-run** default; **`--apply`** + `--archive-slug` **moves** selected generated files to `archive/manual_cleanup/…` (**no deletes**).
3. [`scripts/qa/check_reports_out_active_hygiene.py`](../scripts/qa/check_reports_out_active_hygiene.py) — warn/fail if `reports/out/active/` has unexpected generated artifacts outside `current/`.

Detail: [`RUNBOOK.md`](RUNBOOK.md#m-eprun-reports-out-cleanup) · [`CRUD_SAFETY.md`](CRUD_SAFETY.md).

**Stage 6E1 (Tatiana / lab):** **boundary doc only** — see [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md). Lab / Tatiana / `scripts/ml` are **not** the daily outbound lanes. **Parked Postgres/API/pilots index:** [`EXPERIMENTAL_PARKED.md`](EXPERIMENTAL_PARKED.md). Source-quality planner (`plan_source_quality.py`) labels the `tatiana_lab` bucket for the paths listed there. Future **6E2** may refactor large Tatiana modules; 6E1 does **not** move or change implementation.

**Lead-account scripts:** use **`scripts/leads/advanced/*`** only in new docs, runbooks, and agent prompts. Root-level compatibility wrappers were **removed in Phase 5B** (2026-06-02). Pure env redaction utilities: [`core/safety.py`](../src/origenlab_email_pipeline/core/safety.py).

**Contracts (tests, not a second truth):** [`test_operator_entrypoint_contracts.py`](../tests/test_operator_entrypoint_contracts.py) runs ``--help`` on the **named** daily/ingest/QA/planner entrypoints (including the reports-out archive tool) and asserts top-of-file warnings on the break-glass set (aligned to tables below). [`test_lead_compatibility_wrappers.py`](../tests/test_lead_compatibility_wrappers.py) locks canonical lead-account paths under `scripts/leads/advanced/`. Regressions require updating those tests for intentional path/contract changes; **deleting** scripts is still a **separate** approved change.

**Canonical campaign workspace:** fresh inputs and outputs for the two outbound lanes belong in **`reports/out/active/current/`**. Other paths under `reports/out/active/` (and most of `reports/out/archive/`) are **evidence, history, or ad-hoc exports** — not the default place to pick up “today’s” CSV for DeepSearch or send lists. Keep only intentional root reference files in `active/` (`outreach_contacted_all.csv`, `all_known_marketing_contacts_dedup.csv`) because some scripts use them as default auxiliary inputs. (Stage 6C1) Volume marketing **processing** helpers for ``process_broad_marketing_contacts`` live in ``core.outbound.broad_marketing_contacts``; the **script** remains the supported entrypoint (CSV contracts unchanged). (Stage 6C2) **Do-not-repeat master** merge/summary formatting for ``export_do_not_repeat_master.py`` lives in ``core.outbound.do_not_repeat_master``; the **script** remains the daily entrypoint; **read-only** on SQLite; output filenames and JSON/CSV contract unchanged.

**Rule:** Broad **volume marketing** rows must **not** go into **`lead_contact_research`** unless each row has a real **`lead_id`**. Use **`reviewed_marketing_contacts.csv`** → **`process_broad_marketing_contacts.py`** → **`send_ready_marketing.csv`**.

---

## Mental model: core / ops / lab / break-glass

Use this to separate *what you run daily* from *what can hurt you*:

| Bucket | What it is | Where it lives |
|--------|------------|----------------|
| **Core** | Policy and business logic: gate, CSV contracts, outbound preflight, state, suppressions, Gmail helpers | `src/origenlab_email_pipeline/` (Python package) — **not** run directly; imported by scripts and tests. **Re-export import surface (Stage 2A / 2B):** [`src/origenlab_email_pipeline/core/`](../src/origenlab_email_pipeline/core/) mirrors many modules under `core.outbound`, `core.gmail`, `core.leads` (``leads_schema``, ``lead_contact_research``, …), etc., without moving implementation yet. |
| **Ops** | Thin operator entrypoints: ingest, validate CSVs, export send lists, mark contacted, campaign wrapper | `scripts/**/*.py` (and shell drivers) — **normal daily work** when labeled below |
| **Lab** | Pilots, Tatiana drafting, ML exploration, niche campaign tooling — **not** the two daily lanes | `scripts/tatiana/`, `scripts/dataset/`, `scripts/ml/`, much of `scripts/leads/campaigns/`, some `leads/advanced/` |
| **Break-glass** | Can **send mail**, **purge SQLite**, **rebuild** large derived tables, **`--apply`** side effects, or **truncate/load Postgres** — use only with intent | Called out in [Break-glass scripts](#break-glass-scripts) |

**Runtime source of truth today:** **SQLite** (`ORIGENLAB_SQLITE_PATH` or default under `ORIGENLAB_DATA_ROOT`). **Gmail Workspace Sent** ingested into **`emails`** is required for outbound safety (shared gate + preflight). **Postgres** is **optional** (migration loaders, Alembic, optional outbound audit) — **not** the primary OLTP for daily lanes.

**Postgres env URLs:** commented template in [`.env.example`](../.env.example). **Alembic** resolves `ALEMBIC_DATABASE_URL` before `ORIGENLAB_POSTGRES_URL`; **migrate scripts** and **`--write-postgres-audit`** resolve `--postgres-url`, then `ORIGENLAB_POSTGRES_URL`, then `ALEMBIC_DATABASE_URL` — full table in [`RUNBOOK.md`](RUNBOOK.md#m-eprun-postgres-optional). **Always trial migrate loaders on scratch Postgres first** (they can truncate/delete target tables).

---

## Two workspace prep stories (do not confuse)

| Script | Purpose | When to use |
|--------|---------|-------------|
| [`scripts/qa/prepare_outbound_campaign_workspace.py`](../scripts/qa/prepare_outbound_campaign_workspace.py) | Initializes / archives **`reports/out/active/current/`** and campaign manifest for **volume + precision outbound lanes** | **Before** a new campaign round in `active/current/` |
| [`scripts/leads/advanced/prepare_active_workspace.py`](../scripts/leads/advanced/prepare_active_workspace.py) | Cleans **`reports/out/active/`** for **legacy weekly lead focus** (shortlist, hunt, deepsearch CSV hygiene; archives extras) | **Lead pipeline / REPORTING** workflows — **plan-only by default**; pass **`--apply`** to move/write. Daily outbound prep: **`prepare_outbound_campaign_workspace.py`**. |

If you only care about the **two daily outbound lanes**, prefer **`prepare_outbound_campaign_workspace.py`**. If you are maintaining **hunt sheets + unified active CSVs**, you may still need **`prepare_active_workspace.py`** — read both docstrings before picking one.

---

## Lead-account scripts (canonical)

**Operator rule:** In **new** docs, runbooks, and agent prompts, use **`scripts/leads/advanced/…`** only.

| Tag | Path | Notes |
|-----|------|--------|
| `OPS_MAINT` / break-glass | [`scripts/leads/advanced/build_lead_account_rollup.py`](../scripts/leads/advanced/build_lead_account_rollup.py) | Rebuilds `lead_account_*`; break-glass DELETE pattern on rollup |
| `OPS_MAINT` | [`scripts/leads/advanced/match_lead_accounts_to_existing_orgs.py`](../scripts/leads/advanced/match_lead_accounts_to_existing_orgs.py) | Match accounts → `organization_master` |
| `OPS_MAINT` | [`scripts/leads/advanced/validate_lead_account_rollup.py`](../scripts/leads/advanced/validate_lead_account_rollup.py) | Rollup sanity checks |
| `OPS_MAINT` | [`scripts/leads/advanced/audit_lead_org_quality.py`](../scripts/leads/advanced/audit_lead_org_quality.py) | Org name quality audit |

**Removed Phase 5B (2026-06-02):** root wrappers `build_lead_account_rollup.py`, `match_lead_accounts_to_existing_orgs.py`, `validate_lead_account_rollup.py`, and `audit_lead_org_quality.py` under `scripts/` — use the `scripts/leads/advanced/…` paths above.

Detail: [`leads/LEAD_ACCOUNT_LAYER.md`](leads/LEAD_ACCOUNT_LAYER.md) · [`../scripts/README.md`](../scripts/README.md#lead-account-rollup-and-mart-matching).

---

## Daily lanes

### Volume marketing lane

1. Export do-not-repeat lists for DeepSearch.
2. Run DeepSearch; save reviewed output as **`reports/out/active/current/reviewed_marketing_contacts.csv`**.
3. Validate CSV shape, then process through the shared gate → **`send_ready_marketing.csv`** (and split files).
4. **Human send** (manual or optional Gmail API script — see [Break-glass scripts](#break-glass-scripts)).
5. Mark contacted + ingest **Sent** so the next run sees Gmail truth.

Canonical commands:

```bash
cd apps/email-pipeline
uv run python scripts/qa/export_do_not_repeat_master.py
uv run python scripts/qa/validate_campaign_csvs.py \
  --file reports/out/active/current/reviewed_marketing_contacts.csv \
  --kind marketing_contacts --strict
uv run python scripts/leads/process_broad_marketing_contacts.py
# Review send_ready_marketing.csv — then send (manual or optional Gmail API)
uv run python scripts/leads/mark_sent_batch_contacted.py \
  --batch-file ... --source ... --updated-by ...
uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --folder "[Gmail]/Enviados"
```

Use the real **`--batch-file` / `--source` / `--updated-by`** values from your run; see [`RUNBOOK.md`](RUNBOOK.md#m-eprun-daily-outbound). Discover Sent folder labels with `05_workspace_gmail_imap_to_sqlite.py --list-folders` if `"[Gmail]/Enviados"` does not match your mailbox.

### Precision lead lane

1. **Prepare** campaign workspace and queue.
2. DeepSearch saves **`reports/out/active/current/reviewed_deepsearch.csv`** (must include **`lead_id`**).
3. **Process reviewed** (imports into **`lead_contact_research`** when run with **`--apply`**).
4. Review **`send_ready.csv`**, send, then **post-send** / mark contacted + Sent ingest.

Canonical commands:

```bash
cd apps/email-pipeline
uv run python scripts/leads/run_current_campaign_pipeline.py --stage prepare \
  --campaign-slug YOUR_SLUG --queue-limit 50 --operator you@example.com
# DeepSearch → reports/out/active/current/reviewed_deepsearch.csv
uv run python scripts/leads/run_current_campaign_pipeline.py --stage process-reviewed --apply \
  --operator you@example.com
# Review send_ready.csv — send manually or via your usual path
uv run python scripts/leads/run_current_campaign_pipeline.py --stage post-send \
  --source YOUR_SLUG --operator you@example.com
```

Dry-run import first if your wrapper allows it without `--apply`; see [`RUNBOOK.md`](RUNBOOK.md) and `run_current_campaign_pipeline.py --help`.

---

## Classification legend (scripts)

| Tag | Meaning |
|-----|---------|
| **OPS_DAILY** | On the two daily outbound lanes or required the same week (ingest Sent, validate CSVs). |
| **OPS_CORE** | Infrastructure operators need regularly (blocklist, suppressions, workspace prep) — not always every send. |
| **OPS_AUDIT** | Read-only or hygiene; debugging trust, overlap, readiness. |
| **OPS_MAINT** | Lead pipeline, mart rebuild, commercial rebuild, validation phases — **not** the two lanes. |
| **OPS_MIGRATE** | SQLite → Postgres or pre-checks — **optional** path. |
| **LAB** | Tatiana / ML / pilot / niche campaign tooling. |
| **CONSOLIDATE** | Overlaps another script’s job; docs pick a primary story. |
| **ARCHIVE_LANE** | Archive (`contact_master`) batch lane — still supported, not “daily mental model”. |
| **BREAK_GLASS** | Can send, purge, rebuild destructively, or **`--apply`** with high blast radius — see table below. |
| **DEPRECATED** | Historical, superseded, or one-off wave tooling — **retained** for audit/replay; **not** for new operator work. |

Legacy tags **KEEP_CORE** / **KEEP_AUDIT** in older prose map loosely to **OPS_CORE** / **OPS_AUDIT**.

**Operator health commands:** when to run `make doctor`, `make audit`, `operator_status.py`, `check_outbound_readiness.py`, `run_daily_health_report.py`, and `GET /operator/status` — see [`RUNBOOK.md`](RUNBOOK.md#m-eprun-operator-health-matrix).

---

## NDR suppression tooling (canonical vs legacy)

**Canonical post-send path:** [`scripts/tools/flag_ndr_bounces_from_contacto.py`](../scripts/tools/flag_ndr_bounces_from_contacto.py) — dry-run default; **targeted** apply only after human review:

- Default scan: **NDR / Mailer-Daemon** (`bounce_ndr`) only.
- Optional: `--include-reported-non-delivery` — inbound human replies (e.g. «no recibimos su correo»); dry-run labels matches as **`human_reported_non_delivery`** (not `bounce_ndr`). Replaces removed `flag_reported_non_delivery_from_contacto.py` (Phase **5Q**).
- Preferred NDR apply: `--emails-file PATH --only-code CODE --apply` (allowlist must match scan evidence).
- **Broad `--apply`** without `--emails-file` / `--only-code` = **break-glass** (all planned recipients from scan).

**Human-review helper (read-only):** [`scripts/qa/build_ndr_review_queue.py`](../scripts/qa/build_ndr_review_queue.py) — batches + suggested allowlists under `reports/out/active/current/ndr_review_queue_*`; **does not** write suppressions.

**Removed Phase 5Q:** `scripts/tools/flag_reported_non_delivery_from_contacto.py` — use canonical `--include-reported-non-delivery` above instead.

Procedure: [`pipeline/POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md#ndr-apply-rules).

---

## Ops — daily lane scripts (OPS_DAILY / OPS_CORE)

| Path | Tag | Role | Typical outputs / notes |
|------|-----|------|-------------------------|
| `scripts/qa/export_do_not_repeat_master.py` | OPS_DAILY | Merge “do not repeat” emails for DeepSearch + volume processor | `reports/out/active/current/do_not_repeat_master.{csv,txt}`, `do_not_repeat_summary.json` |
| `scripts/qa/export_outreach_contacted_all.py` | OPS_DAILY | Export auxiliary contacted-all list (Sent + blocking outreach state) | `reports/out/active/outreach_contacted_all.csv` |
| `scripts/qa/refresh_outbound_safety_memory.py` | OPS_DAILY | Run canonical anti-repeat auxiliary refresh + strict checks (stops on first hard failure) | Combined step runner for contacted-all, all-known, DNR, strict coverage, hygiene, readiness |
| `scripts/qa/validate_contacted_csv_coverage.py` | OPS_DAILY | Strict overlap of auxiliary contacted CSVs vs Sent / gate inputs (`--strict` in refresh chain) | stdout / exit code; invoked by `refresh_outbound_safety_memory.py` |
| `scripts/research/run_deep_research_prospecting.py` | OPS_DAILY | Automated research automation (heavy weekly/off-peak, light daily) → review-ready volume batch (no send) | Writes timestamped `research_automation/<ts>/` artifacts, validates/processes, **stops before send**; `--research-mode heavy|light`; supports `--sector`, `--day-rotation`, `--daily-mode`; optional read-only `--run-contacted-coverage-check`; guardrails: `--max-candidates`, `--max-send-ready`, `--fail-on-over-limit`; compact-seed caps: `--max-seed-email-sample`, `--max-seed-institutions`, `--max-seed-domains`; presets: `--tpm-safe`, `--tiny-run`; rate-limit controls: `--max-retries`, `--initial-backoff-seconds`, `--max-backoff-seconds`, optional `--fallback-sector`; output mode: `--research-output-mode direct_csv|evidence_first`; **heavy is fail-closed true Deep Research only (`o4-mini-deep-research`/`o3-deep-research`)**. Warning: `web_search + gpt-4o-mini` is not Deep Research heavy mode. |
| `scripts/qa/validate_campaign_csvs.py` | OPS_DAILY | CSV contracts (`marketing_contacts`, `reviewed_deepsearch`, `send_ready`, etc.) | stdout / exit code; optional `--json-out` |
| `scripts/leads/process_broad_marketing_contacts.py` | OPS_DAILY | Validate, gate, split volume contacts | `marketing_*.csv`, `send_ready_marketing.csv`, `marketing_contacts_summary.json` |
| `scripts/leads/run_current_campaign_pipeline.py` | OPS_DAILY | Orchestrates precision lane (prepare / process-reviewed / post-send) | Files under `active/current/` |
| `scripts/qa/prepare_outbound_campaign_workspace.py` | OPS_DAILY | Initializes/archives **`active/current`** + campaign manifest | Placeholder / manifest files |
| `scripts/leads/export_lead_contact_research_queue.py` | OPS_DAILY | Exports **`research_queue.csv`** for lead DeepSearch | `active/current/research_queue.csv` (when used with pipeline) |
| `scripts/leads/import_lead_contact_research_csv.py` | OPS_CORE | Applies reviewed DeepSearch into **`lead_contact_research`** | DB writes (precision lane); **dry-run unless `--apply`** |
| `scripts/leads/export_next_marketing_recipients.py` | OPS_DAILY | **`send_ready.csv`** from `lead_master` + shared gate | Lead send list |
| `scripts/leads/mark_sent_batch_contacted.py` | OPS_DAILY | Post-send **`outreach_contact_state`** updates | Sidecar only |
| `scripts/ingest/05_workspace_gmail_imap_to_sqlite.py` | OPS_DAILY | Gmail → **`emails`** (Sent / inbox) | Required for Sent-history truth. Default: append + optional `--skip-duplicate-message-id`. **`--replace-source`** = **BREAK_GLASS** — deletes existing rows for that mailbox `source_file` before reinsert; see [Gmail ingest `--replace-source`](#gmail-ingest-replace-source) |

**Optional send (BREAK_GLASS):** `scripts/qa/send_inline_html_email_via_gmail_api.py` — can send real mail; not auto-run. See below.

Research automation prompt templates: `prompts/deep_research_netnew_chile_marketing.txt` (heavy) and `prompts/light_research_netnew_chile_marketing.txt` (light). Planning + scheduling handoff: `docs/DEEP_RESEARCH_AUTOMATION_PLAN.md`, `scripts/research/cron_example.txt`.

**Core modules (not scripts):** `candidate_export_gate.py`, `marketing_export_context.py`, `outbound_core.py`, `outreach_contact_state.py`, `next_marketing_queue.py`, `csv_contracts.py`, `outbound_sent_preflight.py` — package **Core** infrastructure.

---

<a id="debug--audit-scripts-keepaudit--keepdebug"></a>

## Ops — audit & debug (OPS_AUDIT)

| Path | Tag | Role |
|------|-----|------|
| `scripts/qa/export_contacted_lead_overlap_audit.py` | OPS_AUDIT | Pre-import / pre-send overlap vs Sent, state, suppressions, lead/research |
| `scripts/qa/export_gate_audit_csv.py` | OPS_AUDIT | Per-candidate gate flags for lead (or archive) lane |
| `scripts/qa/export_outreach_volume_rollup.py` | OPS_AUDIT | Saturation metrics rollup (counts by source) |
| `scripts/qa/export_supplier_domain_false_positive_audit.py` | OPS_AUDIT | Supplier domain vs institutional false-positive hints |
| `scripts/qa/check_outbound_readiness.py` | OPS_AUDIT | Readiness / config checks |
| `scripts/leads/approve_reviewed_deepsearch_rows.py` | OPS_AUDIT | Promote manual-review rows to import (precision lane helper) |
| `scripts/leads/backfill_contacted_from_gmail_sent.py` | OPS_AUDIT | Backfill **`outreach_contact_state`** from Sent — **dry-run default; `--apply` writes** |
| `scripts/qa/print_outbound_run_summary.py` | OPS_AUDIT | Pretty-print outbound summary JSON |
| `scripts/qa/export_candidate_audit.py` | OPS_AUDIT | Sample rows through gate (informational) |
| `scripts/qa/check_reports_out_active_hygiene.py` | OPS_AUDIT | Warn/fail when `reports/out/active/` contains unexpected generated artifacts outside `current/` |
| `scripts/qa/build_equipment_first_opportunity_queue.py` | OPS_AUDIT | Equipment-first filter from `Licitacion_Publicada.csv` → `equipment_first_opportunity_queue_*.csv` (read-only on Gmail; writes reports only) |
| `scripts/qa/build_equipment_first_operator_queue.py` | OPS_AUDIT | Canonical operator queue + aligned `buyer_opportunity_ab_queue_*.csv` from equipment-first opportunity CSV (read-only cross-check vs DNR/Sent in SQLite) |
| `scripts/qa/operator_status.py` | OPS_AUDIT | **Read-only** operator snapshot: SQLite/Sent freshness, DNR files, canonical `active/current`, manifest warnings, verdict READY/CAUTION/BLOCKED |
| `scripts/qa/build_equipment_deepsearch_vetted_queue.py` | OPS_AUDIT | Gate `equipment_deep_research_opportunities_*.csv` → vetted queue (equipment-first + DNR/Sent/state); fails clearly if input missing |
| `scripts/qa/validate_sqlite_archive_for_postgres.py` | OPS_MIGRATE | Read-only / pre-migrate validation |
| `scripts/qa/audit_canonical_contacto_gmail.py` | OPS_AUDIT | Read-only: canonical Gmail vs legacy labdelivery vs other `emails` metrics |
| `scripts/qa/audit_email_classification_quality.py` | OPS_AUDIT | Read-only: heuristic commercial-type QA on canonical Gmail (keyword audit; not production labels) |
| `scripts/qa/audit_canonical_gmail_duplicates.py` | OPS_AUDIT | Read-only: duplicate `message_id` analysis within canonical Gmail rows |
| `scripts/qa/audit_sqlite_storage.py` | OPS_AUDIT | Read-only SQLite size/freelist (+ Sent diagnostics by default); `--storage-only` for constant-time metadata; optional expensive `--include-dbstat`; **never VACUUM** — see [`SQLITE_STORAGE_MAINTENANCE.md`](SQLITE_STORAGE_MAINTENANCE.md) |
| `scripts/qa/audit_sqlite_deep.py` | OPS_AUDIT | Read-only **deep forensic** audit; production `--light-only` = constant-time PRAGMAs + schema inventory; heavy phases (quick_check/FK/COUNTs, dbstat, body bytes, SHA-256 dupes, usefulness) require `--confirm-offline-copy` and refuse configured production SQLite; `--full-integrity-check` opt-in only — see [`SQLITE_STORAGE_MAINTENANCE.md`](SQLITE_STORAGE_MAINTENANCE.md) |
| `scripts/maintenance/backup_sqlite_online.py` | OPS_MAINT | **Online Backup API** via `Connection.backup()` (never `cp`/`rsync`); default **preflight** (zero writes); `--apply` required to write; `.partial` + final manifest completion marker; same-FS refused by default; capacity check; orphan detection; planned dest `/mnt/d/origenlab-sqlite-offline` — see [`SQLITE_STORAGE_MAINTENANCE.md`](SQLITE_STORAGE_MAINTENANCE.md) |
| `scripts/maintenance/compact_sqlite_offline.py` | OPS_MAINT | **Offline `VACUUM INTO` candidate** from verified snapshot; default preflight; requires `--confirm-offline-copy --apply` to write; refuses production; immutable source; no-clobber publish; never in-place VACUUM / never production swap / never deletes source — see [`SQLITE_STORAGE_MAINTENANCE.md`](SQLITE_STORAGE_MAINTENANCE.md) |
| `scripts/maintenance/run_sqlite_maintenance_with_exit_marker.sh` | OPS_MAINT | Wrapper that always records numeric `AUDIT_RESUME_EXIT=<rc>` (including `0`) for systemd-supervised offline jobs |
| `scripts/maintenance/rehearse_sqlite_writable_restore.py` | OPS_MAINT | **Synthetic writable-restore rehearsal** only (scratch-root + marker + ≤64 MiB); operations `preflight` (zero-write) / `build-fixture --apply` / `rehearse --apply`; refuses production and offline/compact shapes — see [`SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md`](SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md) |
| `scripts/maintenance/orchestrate_sqlite_production_cutover.py` | OPS_MAINT | **Staged production cutover orchestrator** (fail-closed); default `plan_preflight` is zero-write; each stage needs its own `--apply` + production auth; `renameat2(RENAME_EXCHANGE)` only — see [`SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md`](SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md) |
| `scripts/maintenance/dedupe_canonical_gmail_messages.py` | **BREAK_GLASS** | **DELETE** duplicate canonical Gmail `emails` — dry-run default; `--apply --ack-sqlite-backup` |
| `scripts/qa/publish_gate.py` | OPS_AUDIT | Publication / trust gate (broader than outbound) |
| `scripts/qa/run_daily_health_report.py` | OPS_AUDIT | **Read-only** combined health snapshot (NDR dry-run, drift, mirror JSON hints); verdict READY / REVIEW_NEEDED / BLOCKED; output `daily_health_report_*` under `active/current/` |
| `scripts/qa/build_ndr_review_queue.py` | OPS_AUDIT | **Read-only** NDR human-review batches + suggested allowlists; no `--apply` |
| `scripts/qa/build_post_send_digest.py` | OPS_AUDIT | **Read-only** post-send digest (run **after** `audit_contacted_universe.py` in post-send loop) |
| `scripts/qa/audit_prospectos_safety_drift.py` | OPS_AUDIT | Raw `lead_research_prospect` vs safety sidecars — drift ≠ send failure |
| `scripts/qa/audit_commercial_truth.py` | OPS_AUDIT | **Read-only** commercial truth audit (PR1): requires `--sqlite-path` + `--output-dir`; no production fallback; gitignored reports — see [`COMMERCIAL_TRUTH_AUDIT_PR1.md`](audits/COMMERCIAL_TRUTH_AUDIT_PR1.md) |
| `scripts/qa/audit_commercial_procurement_link.py` | OPS_AUDIT | **Read-only** PR4 procurement ↔ PR2 account-link audit: requires `--sqlite-path`, `--output-dir`, and `--as-of-date YYYY-MM-DD`; `mode=ro` + `query_only`; no table writes; no Gmail, Postgres, suppression, outreach, classification, or deal mutation; gitignored reports — see [`COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md`](audits/COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md) |
| `scripts/commercial/build_commercial_identity_read_model.py` | OPS_MAINT / break-glass | **PR2** deterministic account/contact identity read model (`commercial_identity_*`); requires `--sqlite-path`; dry-run default; `--run-context` metadata label; `--apply` uses **transaction contract B** (additive schema may remain; DELETE+INSERT data replacement is atomic with `foreign_keys=ON`); stores `identity_fingerprint` for PR3 gating — **not** production without approval; see [`COMMERCIAL_IDENTITY_READ_MODEL_PR2.md`](audits/COMMERCIAL_IDENTITY_READ_MODEL_PR2.md) |
| `scripts/commercial/build_commercial_opportunity_read_model.py` | OPS_MAINT / break-glass | **PR3** deterministic opportunity-stage read model (`commercial_opportunity_*`); requires `--sqlite-path`; dry-run default (in-memory PR2 identity); `--apply` requires matching persisted PR2 identity fingerprint; contract B; does not mutate deals/identity — **not** production without approval; see [`COMMERCIAL_OPPORTUNITY_STAGE_READ_MODEL_PR3.md`](audits/COMMERCIAL_OPPORTUNITY_STAGE_READ_MODEL_PR3.md) |
| `scripts/commercial/build_commercial_procurement_read_model.py` | OPS_AUDIT / apply | **PR4** deterministic procurement planner + transactional persistence (`commercial_procurement_*`); requires `--sqlite-path`, `--as-of-date`, `--run-context`; **default dry-run** (`mode=ro` + `query_only`, no production DDL); `--apply` under contract `B_schema_additive_data_atomic` with stale-plan checks; `production_apply` requires four `--expected-*` fingerprints/digests from an approved dry-run; never mutates PR2/PR3/Gmail/Postgres — see [`COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md`](audits/COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md) |
| `scripts/qa/audit_institution_grouping.py` | OPS_AUDIT | Institution/domain grouping — **not** send safety; prefer `origenlab audit-institution-grouping` |
| `scripts/qa/smoke_dashboard_api_readiness.py` | OPS_AUDIT | **Read-only** HTTP smoke against deployed `apps/api` (:8001); debugging / deploy check only |

**Overlap note:** **`export_do_not_repeat_master.py`** (operator *input list*) vs **`export_outreach_volume_rollup.py`** (*metrics*). Different jobs; do not delete one thinking it replaces the other.

---

## Ops — campaign wave tooling (OPS_MAINT / dated)

**Not** the two daily outbound lanes. Use only when running a **named campaign wave** with explicit operator approval. Outputs are usually under `reports/out/` (gitignored).

| Path | Tag | Role |
|------|-----|------|
| `scripts/qa/build_presentacion_origenlab_review.py` | OPS_MAINT | Presentation campaign — review queue / human triage artifacts |
| `scripts/qa/build_presentacion_origenlab_quality.py` | OPS_MAINT | Presentation campaign — quality scoring / gate-style checks on cohort |
| `scripts/qa/build_presentacion_batch1_presend_audit.py` | OPS_MAINT | Presentation batch 1 — **pre-send** audit (read-only reports) |
| `scripts/qa/build_presentacion_prospectos_merge.py` | OPS_MAINT | Merge presentation prospectos inputs with lead-research overlay (reports) |
| `scripts/qa/build_cyber_outreach_campaign.py` | OPS_MAINT | Cyber-day outreach campaign package builder (files + gate audit; not daily lane) |
| `scripts/qa/build_cyber_campaign_context_audit.py` | OPS_MAINT | Cyber campaign context / evidence audit (read-only) |

**Removed Phase 5K (2026-06-02):** `manual_outreach_2026_06_01.py`, `build_manual_outreach_2026_06_01_digest.py`, and `apply_manual_outreach_2026_06_01_corrections.py` — use [`build_post_send_digest.py`](../scripts/qa/build_post_send_digest.py) and [`POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md) instead.

---

## Ops — Postgres mirror verify (OPS_MIGRATE / parked)

**Optional** Postgres path only ([`EXPERIMENTAL_PARKED.md`](EXPERIMENTAL_PARKED.md)). Verifiers are **read-only** on mirror tables; they do **not** load mirrors (use `scripts/sync/*` + `scripts/ops/refresh_render_dashboard_once.sh`).

| Path | Tag | Role |
|------|-----|------|
| `scripts/qa/verify_dashboard_postgres_mirror.py` | OPS_MIGRATE | Dashboard mart mirror parity checks |
| `scripts/qa/verify_outbound_sidecar_postgres_mirror.py` | OPS_MIGRATE | Outbound sidecar mirror parity |
| `scripts/qa/verify_lead_research_postgres_mirror.py` | OPS_MIGRATE | Lead research mirror parity |
| `scripts/qa/verify_catalog_postgres_mirror.py` | OPS_MIGRATE | Catalog mirror parity |
| `scripts/qa/verify_commercial_deals_postgres_mirror.py` | OPS_MIGRATE | Commercial deals mirror parity |
| `scripts/catalog/build_catalog_sqlite.py` | OPS_MIGRATE | Build SQLite `catalog_*` from seed (opt-in before catalog Postgres sync; [`REFRESH_RENDER_DASHBOARD_ONCE.md`](REFRESH_RENDER_DASHBOARD_ONCE.md)) |
| `scripts/sync/sync_lead_research_postgres_mirror.py` | OPS_MIGRATE | SQLite `lead_research_*` → Postgres `lead_intel.*` (opt-in mirror load) |
| `scripts/sync/load_equipment_opportunity_mirror.py` | OPS_MIGRATE | Equipment-first operator queue CSV → Postgres `commercial.equipment_opportunity*` (opt-in; dry-run default) |
| `scripts/ops/cloud_postgres_url.py` | OPS_CORE | **Read-only** CLI: validate/redact Postgres URL for ops shell scripts (`validate`, `host-db`, `shell-prepare`) |

---

## Scripts infrastructure (internal — not operator entrypoints)

Shared helpers imported by other `scripts/` CLIs; not daily outbound or mirror operator paths.

| Path | Tag | Role |
|------|-----|------|
| `scripts/_bootstrap.py` | INFRASTRUCTURE | `sys.path` bootstrap for script entrypoints |
| `scripts/_script_warnings.py` | INFRASTRUCTURE | Phase 4 stderr deprecation banners (`print_wrapper_deprecation_warning`) |

---

## Deprecated & historical paths (DEPRECATED)

**Retained on disk** for audit, tests, and replay — **do not delete** in Phase 1. Prefer canonical replacements in new runbooks and agent prompts.

| Path | Tag | Replacement / notes |
|------|-----|---------------------|
| `scripts/tools/flag_reported_non_delivery_from_contacto.py` | REMOVED (5Q) | **`flag_ndr_bounces_from_contacto.py --include-reported-non-delivery`** + [`build_ndr_review_queue.py`](../scripts/qa/build_ndr_review_queue.py) |

**Removed Phase 5D (2026-06-02):** `scripts/leads/advanced/export_archive_outreach_candidates.py` — use [`build_archive_send_batch.py`](../scripts/leads/build_archive_send_batch.py) `--audit-only`.

**Removed Phase 5C (2026-06-02):** `scripts/qa/build_buyer_opportunity_queue.py` — use `build_equipment_first_opportunity_queue.py` + `build_equipment_first_operator_queue.py`.

**Removed Phase 5A (2026-06-02):** `run_post_send_2026_06_01_refresh.sh` and `run_manual_outreach_2026_06_01_post_send_refresh.sh` — use [`POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md) step-by-step instead.

**Removed Phase 5R (2026-06-02):** `scripts/qa/build_legacy_contacts_2016_2019_review.py` — use [`legacy_contacts_2016_2019.py`](../src/origenlab_email_pipeline/lead_research/legacy_contacts_2016_2019.py) (library) and [`test_legacy_contacts_2016_2019.py`](../tests/test_legacy_contacts_2016_2019.py) for behavior contracts.

**Removed Phase 5S (2026-06-02):** zero-ref LAB scripts — `scripts/ml/test_real_embeddings.py` (`uv sync --group ml` / [`explore_email_clusters.py`](../scripts/ml/explore_email_clusters.py) for embeddings), `scripts/leads/campaigns/apply_deepresearch_top10_contacts_to_sheet.py` (frozen/manual DR50 sheet workflow), `scripts/dataset/review_marketing_labels_cli.py` (Tatiana cohort workflow / external labeling).

---

## Lab scripts (LAB)

| Area | Examples |
|------|----------|
| Tatiana / drafting | `scripts/tatiana/*` |
| Dataset / cohort exports | `scripts/dataset/*` |
| ML / embeddings exploration | `scripts/ml/*` |
| Niche campaign reconciliations | `scripts/leads/campaigns/*` (e.g. DR50 payload flows) |
| DR50 ready-8 hunt patch | [`scripts/leads/campaigns/apply_ready8_contact_patch.py`](../scripts/leads/campaigns/apply_ready8_contact_patch.py) — **plan-only default**; **`--apply`** to write hunt/top20/plan; not daily outbound / not send approval |
| ML lab report | `scripts/reports/build_ml_report.py` |

These are **not** the volume or precision daily lanes and are **not send approval**; see [`dataset/TATIANA_PILOT_WORKFLOW.md`](dataset/TATIANA_PILOT_WORKFLOW.md) and [`RUNBOOK.md`](RUNBOOK.md). **Scope / safety:** [`TATIANA_LAB_BOUNDARY.md`](TATIANA_LAB_BOUNDARY.md) — **lab boundary — not daily outbound** (Tatiana vs production outbound, Gmail Sent truth, OpenAI, `reports/out`). **Parked index (Postgres/API + pilots):** [`EXPERIMENTAL_PARKED.md`](EXPERIMENTAL_PARKED.md). **Reduction planning:** [`audits/REDUCTION_SHORTLIST_20260607.md`](audits/REDUCTION_SHORTLIST_20260607.md).

---

<a id="one-time-maintenance--alternate-lanes"></a>

## Archive lane & maintenance (ARCHIVE_LANE / OPS_MAINT / CONSOLIDATE)

| Path | Tag | Role |
|------|-----|------|
| `scripts/leads/build_archive_send_batch.py` | ARCHIVE_LANE | **`contact_master`** / archive send batch lane |
| `scripts/leads/precheck_archive_shortlist_commercial.py` | ARCHIVE_LANE | Archive commercial precheck |
| `scripts/leads/build_manual_html_outreach_batch.py` | CONSOLIDATE | Manual HTML package (files only); overlaps “send prep” with API sender |
| `scripts/leads/mark_outreach_state.py` | OPS_CORE | Manual **`outreach_contact_state`** edits — **dry-run default**; **`--apply`** + `--updated-by`/`--operator`, `--source`/`--source-artifact`, `--reason` to write ([CRUD_SAFETY](CRUD_SAFETY.md#phase-2c-pilot-mark_outreach_statepy-implemented)) |
| `scripts/leads/import_operator_outreach_blocklist.py` | OPS_CORE | Blocklist → suppressions |
| `scripts/leads/add_manual_contact_suppressions.py` | OPS_CORE | Manual suppression adds |
| `scripts/qa/export_all_known_marketing_contacts.py` | OPS_CORE | Known-marketing dedup export across active/archive/reference sources (includes contacted-all by default) |
| `scripts/leads/advanced/prepare_active_workspace.py` | CONSOLIDATE | **Different** from `prepare_outbound_campaign_workspace.py` — see [Two workspace prep stories](#two-workspace-prep-stories-do-not-confuse) |
| `scripts/leads/advanced/export_marketing_from_contact_master.py` | ARCHIVE_LANE | Exploratory `contact_master` export — **audit-only default**; **`--export --out`** to write CSVs; not daily outbound / not send approval |
| `scripts/leads/advanced/export_leads_spanish_csvs.py` | CONSOLIDATE / parked | Spanish `_es` CSV helper — **plan-only default** + **`--write-outputs`** ([#125](audits/REDUCTION_SHORTLIST_20260607.md)); not daily ops / not send approval |
| `scripts/leads/advanced/run_contact_hunt_web_server.py` | CONSOLIDATE / parked | Local CSV server — **localhost default** + **`--lan`** ([#126](audits/REDUCTION_SHORTLIST_20260607.md)); password required; not operator API |
| `scripts/qa/sync_outreach_batch_from_ingested_bounces.py` | BREAK_GLASS | Bounce-driven sync — review evidence; **`--apply`** mutates state |

Many other `scripts/leads/*.py` (scoring, ChileCompra fetch, dedupe, mart match) are **OPS_MAINT** — see [`RUNBOOK.md`](RUNBOOK.md) and [`scripts/README.md`](../scripts/README.md).

---

## Post-send safe loop (procedure + scripts)

**Canonical steps:** [`pipeline/POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md) — run after new Sent mail, NDRs, or suppression changes. Mirror-only is **not** sufficient when Gmail evidence changed.

| Path | Role | Mutates? | Notes |
|------|------|----------|--------|
| [`scripts/ingest/05_workspace_gmail_imap_to_sqlite.py`](../scripts/ingest/05_workspace_gmail_imap_to_sqlite.py) | Read-only Gmail IMAP → `emails` | SQLite insert | No Gmail send; use `--skip-duplicate-message-id`; no `--replace-source` in safe loops |
| [`scripts/tools/flag_ndr_bounces_from_contacto.py`](../scripts/tools/flag_ndr_bounces_from_contacto.py) | NDR scan; optional human-reported inbound (`--include-reported-non-delivery`); optional suppression apply | SQLite **only with `--apply`** | **Dry-run default.** NDR apply: `--emails-file PATH --only-code CODE --apply`. **Broad `--apply` without filters = break-glass.** Human-reported matches labeled `human_reported_non_delivery` in dry-run. Delay DSN subjects skipped. Exact-email only — not domain suppression. |
| [`scripts/leads/audit_contacted_universe.py`](../scripts/leads/audit_contacted_universe.py) | Rebuild exclusion CSVs from SQLite | Writes `reports/out/` only | Run before post-send digest |
| [`scripts/qa/refresh_outbound_safety_memory.py`](../scripts/qa/refresh_outbound_safety_memory.py) | Safety exports + validation chain | SQLite read; `reports/out/` writes | Daily / post-send |
| [`scripts/qa/build_post_send_digest.py`](../scripts/qa/build_post_send_digest.py) | Post-send digest CSV/MD/JSON | **Read-only** analysis | Output under `reports/out/active/current/` (gitignored) — not source of truth |
| [`scripts/ops/refresh_render_dashboard_once.sh`](../scripts/ops/refresh_render_dashboard_once.sh) | Postgres mirror refresh | Postgres mirror load | Post-send: `RUN_GMAIL_INGEST=0`, `RUN_LEAD_RESEARCH_MIRROR=1`, `RUN_OUTBOUND_SIDECAR_MIRROR=1` |
| [`scripts/qa/audit_prospectos_safety_drift.py`](../scripts/qa/audit_prospectos_safety_drift.py) | Raw `lead_research_prospect` vs safety sidecars | **Read-only** | Report under `reports/out/`; optional `--strict`; drift ≠ send failure |
| [`scripts/qa/audit_institution_grouping.py`](../scripts/qa/audit_institution_grouping.py) | Institution/domain grouping audit | **Read-only** | `uv run origenlab audit-institution-grouping`; presentation/strategy only — **not** send safety; gitignored reports |
| [`scripts/qa/operator_status.py`](../scripts/qa/operator_status.py) | Operator READY / freshness | **Read-only** | LISTO / mirror_ok ≠ send approval |
| [`scripts/qa/run_daily_health_report.py`](../scripts/qa/run_daily_health_report.py) | Daily health summary (NDR dry-run, drift, mirror JSON) | **Read-only** | Output under `reports/out/active/current/daily_health_report_*` (gitignored); verdict READY / REVIEW_NEEDED / BLOCKED |
| [`scripts/qa/build_ndr_review_queue.py`](../scripts/qa/build_ndr_review_queue.py) | Build NDR human-review batches + suggested allowlists | **Read-only** | Output under `reports/out/active/current/ndr_review_queue_*`; no suppression apply |

---

<a id="gmail-ingest-replace-source"></a>

## Gmail ingest: `--replace-source` (BREAK_GLASS)

**Script:** [`scripts/ingest/05_workspace_gmail_imap_to_sqlite.py`](../scripts/ingest/05_workspace_gmail_imap_to_sqlite.py)

| Mode | Behavior |
|------|----------|
| **Normal daily ingest** (no `--replace-source`) | Fetches messages and **inserts** into `emails`. Use **`--skip-duplicate-message-id`** in post-send / safe loops to avoid re-processing the same `message_id`. |
| **`--replace-source`** | **Deletes all existing `emails` rows** whose `source_file` matches the ingested mailbox label (`gmail:<user>/<folder>`) **before** inserting fetched messages. Use only for an **intentional refresh** of that folder’s SQLite copy (e.g. repair after a bad partial run). |

**Not used in:** [`POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md) (explicit: no `--replace-source` in safe loops).

**Not the same as:** full-table wipe (`02_mbox_to_sqlite.py` deletes **all** emails) or mart rebuild (`build_business_mart.py`).

---

## Break-glass scripts (BREAK_GLASS)

**File headers:** scripts in this table include a prominent **`SAFETY` / `SAFETY (break-glass)`** comment block at the top of the source file (in addition to this doc).

**Before running:** read `--help`, prefer dry-run defaults, and confirm **`ORIGENLAB_SQLITE_PATH`**. Do not use **`--apply`**, **send**, or **migrate** flags unless you intend to mutate production or target databases.

| Path | Why break-glass |
|------|-----------------|
| `scripts/qa/send_inline_html_email_via_gmail_api.py` | **Sends real email** via Gmail API when not in dry-run / build-only modes |
| `scripts/tools/purge_contact_emails_from_sqlite.py` | **Deletes** rows across many tables; **`--apply`** required to execute |
| `scripts/tools/purge_email_domain_from_sqlite.py` | **Domain-level purge**; **`--apply`** |
| `scripts/tools/purge_mailbox_from_sqlite.py` | **Mailbox purge**; **`--apply`** |
| `scripts/mart/build_business_mart.py` | Rebuild pattern **deletes** mart tables before rebuild |
| `scripts/commercial/build_commercial_intel_v1.py` | Can **delete** / rebuild commercial facts |
| `scripts/commercial/build_commercial_identity_read_model.py` | **`--apply`** DELETE+rebuild `commercial_identity_*` data (dry-run default; explicit `--sqlite-path`; contract B: additive schema may remain, data replacement atomic + `foreign_keys=ON`) |
| `scripts/commercial/build_commercial_opportunity_read_model.py` | **`--apply`** DELETE+rebuild `commercial_opportunity_*` data (dry-run default; explicit `--sqlite-path`; requires matching PR2 identity fingerprint; contract B; does not mutate `commercial_deal*` / `commercial_identity_*`) |
| `scripts/commercial/build_commercial_procurement_read_model.py` | **`--apply` implemented** (fixture / separately gated production); default remains dry-run; production apply not executed in this PR |
| `scripts/migrate/sqlite_archive_to_postgres.py` | **TRUNCATE** / load on Postgres target (**EXPERIMENTAL_PARKED** — see [`EXPERIMENTAL_PARKED.md`](EXPERIMENTAL_PARKED.md)) |
| `scripts/sync/sync_dashboard_postgres_mirror.py` | Postgres dashboard mirror load (**EXPERIMENTAL_PARKED**; not send truth) |
| `scripts/ops/refresh_operational_dashboard_stack.py` | Optional mart + mirror stack (**DASHBOARD_ONLY**; not CORE_DAILY) |
| `scripts/migrate/sqlite_document_master_to_postgres.py` | **DELETE** / load on Postgres target (**EXPERIMENTAL_PARKED**) |
| `scripts/migrate/sqlite_outbound_sidecars_to_postgres.py` | **DELETE** / load on Postgres target (**EXPERIMENTAL_PARKED**) |
| `scripts/migrate/sqlite_mart_core_to_postgres.py` | **DELETE** / load on Postgres `mart.contact_master`, `organization_master`, `opportunity_signals` (**EXPERIMENTAL_PARKED**) |
| `scripts/maintenance/dedupe_canonical_gmail_messages.py` | **DELETE** duplicate canonical Gmail `emails` — dry-run default; `--apply --ack-sqlite-backup` |
| `scripts/leads/advanced/build_lead_account_rollup.py` | **DELETE** + rebuild `lead_account_*` |
| `scripts/qa/sync_outreach_batch_from_ingested_bounces.py` | **`--apply`** updates suppressions / state |
| `scripts/ingest/05_workspace_gmail_imap_to_sqlite.py` | **`--replace-source`** deletes existing rows for that Gmail `source_file` before reinsert — [details](#gmail-ingest-replace-source) |
| `scripts/tools/flag_ndr_bounces_from_contacto.py` | **`--apply`** writes `contact_email_suppression`; broad apply = all scan matches; prefer `--emails-file` + `--only-code` ([`POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md)) |
| `scripts/validation/extract_attachment_text.py` | May **delete** `attachment_extracts` during rebuild patterns |
| `scripts/tools/archive_reports_out_generated.py` | **`--apply`** **moves** files under `reports/out` to `archive/manual_cleanup/…` (no deletes) |

---

## Tests (pointer)

Outbound / campaign regression tests live under `tests/` (e.g. `test_run_current_campaign_pipeline.py`, `test_process_broad_marketing_contacts.py`, `test_validate_campaign_csvs.py`, `test_export_gate_audit_csv.py`). **Do not remove** tests when editing docs.

---

<a id="do-not-remove-safety-critical"></a>

## Do not remove (safety-critical)

- **Gate policy:** `candidate_export_gate.py` + `GateContext` inputs — do not change policy lightly.
- **SQLite sidecar:** `outreach_contact_state` — operator memory for “already contacted”.
- **Gmail Sent in SQLite:** `emails` rows for configured Sent folders — blocker truth for exports.
- **Suppressions:** `contact_email_suppression`, `contact_domain_suppression`, and import CLIs.
- **CSV validation:** `validate_campaign_csvs.py`, `csv_contracts.py`.
- **Do-not-repeat master:** `export_do_not_repeat_master.py` — volume lane input to DeepSearch.
- **Post-send marking:** `mark_sent_batch_contacted.py` (and pipeline `post-send` where used).
- **Precision research persistence:** `import_lead_contact_research_csv.py` — primary path for **`lead_contact_research`** from reviewed DeepSearch.

---

## Related docs

- [`OPERATOR_COMMAND_SURFACE.md`](OPERATOR_COMMAND_SURFACE.md) — Phase 6A operator quick index (start here)
- [`pipeline/POST_SEND_SAFE_LOOP.md`](pipeline/POST_SEND_SAFE_LOOP.md) — canonical post-send / NDR procedure
- [`RUNBOOK.md`](RUNBOOK.md) — full procedures, mailbox ingest, Docker, publish gate
- [`OUTBOUND_SOURCE_OF_TRUTH.md`](OUTBOUND_SOURCE_OF_TRUTH.md) — lane semantics
- [`scripts/README.md`](../scripts/README.md) — folder map and QA table
- Postgres planning: [`pipeline/POSTGRES_SCHEMA_TARGET_V1.md`](pipeline/POSTGRES_SCHEMA_TARGET_V1.md), [`pipeline/POSTGRES_ARCHIVE_DATA_MIGRATION_PLAN_V1.md`](pipeline/POSTGRES_ARCHIVE_DATA_MIGRATION_PLAN_V1.md)
