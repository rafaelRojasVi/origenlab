# Scripts

**Operator CLI:** see [How to run](#how-to-run). **Script map / tags:** [`docs/SCRIPT_MAP.md`](../docs/SCRIPT_MAP.md) · [`docs/OPERATOR_COMMAND_SURFACE.md`](../docs/OPERATOR_COMMAND_SURFACE.md).

**Environment / safety / refactor planning:** [REPRODUCIBILITY.md](../docs/REPRODUCIBILITY.md) · [CRUD_SAFETY.md](../docs/CRUD_SAFETY.md) · [QUALITY_AND_REFACTOR_STRATEGY.md](../docs/QUALITY_AND_REFACTOR_STRATEGY.md) · [SCRIPT_INVENTORY.md](../docs/SCRIPT_INVENTORY.md) · **Tatiana/lab vs daily ops:** [TATIANA_LAB_BOUNDARY.md](../docs/TATIANA_LAB_BOUNDARY.md) (Stage 6E1) — read-only: [`qa/check_reproducibility.py`](qa/check_reproducibility.py), [`qa/plan_reports_out_cleanup.py`](qa/plan_reports_out_cleanup.py) (inspect `reports/out` layout), [`qa/plan_script_consolidation.py`](qa/plan_script_consolidation.py) (inspect `scripts/` sprawl before delete/wrap/deprecate), [`qa/plan_source_quality.py`](qa/plan_source_quality.py) (heuristic `src/` + `scripts/` line counts, `tatiana_lab` bucket; planning only). **Move-only `reports/out` archiver (dry-run default, break-glass):** [`tools/archive_reports_out_generated.py`](tools/archive_reports_out_generated.py) — use after the planner; `--apply` + `--archive-slug` to execute. Planner/archiver share path **classification** via `origenlab_email_pipeline.core.reports_out` (Stage 6D1). **Imports:** new code should prefer `origenlab_email_pipeline.core.*` where a re-export exists; no mass rewrites (see `QUALITY_AND_REFACTOR_STRATEGY.md`).

## Quick navigation

| Kind | What to use | Notes |
|------|-------------|--------|
| **Stable operational entrypoints** | Ingest ([`ingest/`](ingest/)), [`mart/build_business_mart.py`](mart/build_business_mart.py), [`commercial/build_commercial_intel_v1.py`](commercial/build_commercial_intel_v1.py), [`leads/run_leads_operational_stack.sh`](leads/run_leads_operational_stack.sh), QA ([`qa/publish_gate.py`](qa/publish_gate.py) and table below) | Procedures and ordering: [`docs/RUNBOOK.md`](../docs/RUNBOOK.md), **script map:** [`docs/SCRIPT_MAP.md`](../docs/SCRIPT_MAP.md), schema map: [`docs/pipeline/SCHEMA_OWNERSHIP.md`](../docs/pipeline/SCHEMA_OWNERSHIP.md) |
| **Lead-account (canonical implementations)** | [`leads/advanced/build_lead_account_rollup.py`](leads/advanced/build_lead_account_rollup.py), [`leads/advanced/match_lead_accounts_to_existing_orgs.py`](leads/advanced/match_lead_accounts_to_existing_orgs.py), [`leads/advanced/validate_lead_account_rollup.py`](leads/advanced/validate_lead_account_rollup.py), [`leads/advanced/audit_lead_org_quality.py`](leads/advanced/audit_lead_org_quality.py) | **Use `scripts/leads/advanced/…`** in docs and commands. Root wrappers removed Phase 5B. Detail: [`docs/leads/LEAD_ACCOUNT_LAYER.md`](../docs/leads/LEAD_ACCOUNT_LAYER.md). |
| **One-off / exploratory** | [`tools/`](tools/), [`validation/`](validation/) (phase checks), [`ml/`](ml/), some [`dataset/`](dataset/) | Not the main weekly path; useful for debugging or optional ML |

## How to run

```bash
cd apps/email-pipeline
uv run origenlab --help
uv run origenlab status
uv run origenlab daily-health
uv run origenlab refresh-safety
uv run origenlab validate-csvs
uv run origenlab check-readiness
uv run origenlab post-send-digest
uv run origenlab export-dnr
uv run origenlab ndr-review
uv run origenlab audit-overlap
uv run origenlab gmail-ingest
uv run origenlab gmail-ingest-folders
uv run origenlab mirror-dashboard
uv run origenlab mirror-dashboard --apply
uv run origenlab refresh-dashboard
uv run origenlab refresh-dashboard --apply --no-mirror
uv run origenlab refresh-dashboard --apply --mirror-dry-run
```

Module fallback: `uv run python -m origenlab_email_pipeline.cli …`. Extra flags after ``--`` append to sync script (`mirror-dashboard -- --only mart`). Requires `ORIGENLAB_POSTGRES_URL`, `ALEMBIC_DATABASE_URL`, or `ORIGENLAB_CLOUD_POSTGRES_URL`. Other workflows: [`docs/RUNBOOK.md`](../docs/RUNBOOK.md).

## Lead-account layer (rollup + mart match)

**Canonical implementations** live under **`scripts/leads/advanced/`** (prefer these paths in **new** documentation and when typing commands from scratch):

- [`leads/advanced/build_lead_account_rollup.py`](leads/advanced/build_lead_account_rollup.py) — full rollup rebuild  
- [`leads/advanced/match_lead_accounts_to_existing_orgs.py`](leads/advanced/match_lead_accounts_to_existing_orgs.py) — match accounts → `organization_master`  
- [`leads/advanced/validate_lead_account_rollup.py`](leads/advanced/validate_lead_account_rollup.py) — sanity checks  
- [`leads/advanced/audit_lead_org_quality.py`](leads/advanced/audit_lead_org_quality.py) — org_name quality audit  

Paths such as `scripts/leads/build_lead_account_rollup.py` (directly under `leads/` without `advanced/`) **do not exist** for this family. Root-level `scripts/build_lead_account_rollup.py` (and three siblings) were **removed in Phase 5B** — use `scripts/leads/advanced/…` ([`docs/SCRIPT_MAP.md`](../docs/SCRIPT_MAP.md#lead-account-scripts-canonical)).

**Volume marketing (broad contacts):** [`leads/process_broad_marketing_contacts.py`](leads/process_broad_marketing_contacts.py) is the **volume marketing** contact processor (DeepSearch volume lane). The **CLI** remains the operator entrypoint; core processing lives in `origenlab_email_pipeline.core.outbound.broad_marketing_contacts`. The script **writes generated CSV outputs only** and does **not** mutate SQLite; it still loads a **read-only** gate context and DNR sidecar inputs for safety.

**Do-not-repeat master (read-only):** [`qa/export_do_not_repeat_master.py`](qa/export_do_not_repeat_master.py) builds `do_not_repeat_master.{csv,txt}` and `do_not_repeat_summary.json` under `active/current/`. The **CLI** is the entrypoint; merge/summary logic lives in `origenlab_email_pipeline.core.outbound.do_not_repeat_master`. **Read-only** on SQLite; no schema writes.

**Deep Research automation (review-ready only):** [`research/run_deep_research_prospecting.py`](research/run_deep_research_prospecting.py) now supports two research modes while preserving existing behavior: `--research-mode heavy` (Deep Research, default, weekly/manual/off-peak) and `--research-mode light` (daily, non deep-research model with `web_search`). Both modes keep local exclusion + strict validation + broad marketing processing into a timestamped `research_automation/` run folder, then stop before any live send/post-send actions. Guardrails: `--max-candidates` (truncate/fail), `--max-send-ready` (review over-limit flag), `--fail-on-over-limit`. Sector controls: `--sector`, `--day-rotation`, and `--daily-mode` (weekday mapping + daily warning context). Canonical exclusion source is `current/do_not_repeat_master.csv`; auxiliary overlap artifacts are `outreach_contacted_all.csv` and `all_known_marketing_contacts_dedup.csv`. Optional read-only drift check: `--run-contacted-coverage-check` (`--strict-contacted-coverage` to fail on validator non-zero). Context hardening: compact seed artifacts + size caps (`--max-seed-email-sample`, `--max-seed-institutions`, `--max-seed-domains`) avoid `context_length_exceeded` from full raw CSV attachments. Rate-limit hardening: bounded retries (`--max-retries`, `--initial-backoff-seconds`, `--max-backoff-seconds`) plus optional narrow fallback (`--fallback-sector`). For smaller real runs, `--tpm-safe` applies conservative defaults (`max-candidates=40`, `max-send-ready=15`, `max-seed-email-sample=100`, `max-seed-institutions=150`, `max-seed-domains=150`) unless explicitly overridden; `--tiny-run` applies stricter first-run defaults (`max-candidates=20`, `max-send-ready=10`, `max-seed-email-sample=50`, `max-seed-institutions=80`, `max-seed-domains=80`, `max-retries=2`). The CLI prints a preflight size summary before API submission (prompt chars, compact seed counts/sizes, sector, guardrails) and warns when `broad`/`water_env` runs with larger caps may exceed TPM on lower tiers. Operational recommendation: run heavy broad weekly/off-peak, run light Tue-Sat daily rotation, and keep no automated send. Progress UX: phase/status lines show real Responses background statuses (no fake percentage), elapsed time, retry count, sector, and out-dir; use `--verbose-progress` for full event logs.

## Where to read

| Need | Doc |
|------|-----|
| Commands and workflows | [docs/RUNBOOK.md](../docs/RUNBOOK.md#m-eprun-path) (incl. [publish gate QA](../docs/RUNBOOK.md#m-eprun-publish-qa)) |
| Data flow and layout | [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) |
| Leads / accounts | [docs/leads/LEAD_PIPELINE.md](../docs/leads/LEAD_PIPELINE.md), [docs/leads/LEAD_ACCOUNT_LAYER.md](../docs/leads/LEAD_ACCOUNT_LAYER.md) |

## Folder map

| Directory | Role |
|-----------|------|
| `ingest/` | PST → mbox → SQLite → JSONL |
| `mart/` | Business mart, batch overview, open report |
| `reports/` | Client report, `run_all_reports.py`, `run_all.sh` |
| `validation/` | Phase checks, attachment text extraction |
| `ml/` | Embeddings, clusters, `email_ml_explore` |
| `dataset/` | Voice cohort metrics, Tatiana/Vivanco DB audit, stratified review CSV export |
| `tools/` | Inspect DB, dedupe, env checks |
| `pipeline/` | Cross-layer runs (e.g. aligned stack) |
| `leads/` | Lead scoring, matching, audits; operator sidecars such as [`leads/mark_outreach_state.py`](leads/mark_outreach_state.py) (`outreach_contact_state`) |
| `research/` | Deep Research automation entrypoints (review-ready only; no send) |
| `qa/` | Operational trust / publication gate ([`publish_gate.py`](qa/publish_gate.py) y scripts relacionados; ver tabla abajo) |

<a id="m-scripts-qa"></a>
## `scripts/qa/` — operational trust (publication gate)

Run from **`apps/email-pipeline/`**. Exit code **`0`** = no **critical** check failed in that script; **`1`** = at least one critical check failed. Non-critical failures print `FAIL` without failing the process alone (see [`operational_trust`](../src/origenlab_email_pipeline/operational_trust/__init__.py) `TrustCheck.critical`).

| Script | Purpose | Main inputs | Main outputs | When to run | Blocking? |
|--------|---------|-------------|--------------|-------------|-----------|
| [`publish_gate.py`](qa/publish_gate.py) | Runs verify → audit → evidence (unless `--skip-evidence-http`) | Same as substeps; `--db`, `--max-pack-age-hours`, evidence flags | stdout + scorecard files from audit step | Before external handoff of client pack + operational lead CSVs | **Yes** (aggregate) |
| [`verify_client_pack_consistency.py`](qa/verify_client_pack_consistency.py) | Pack vs DB + top20 vs hunt/readiness/DB (hunt/readiness **cohort partition** is only in [`audit_operational_trust.py`](qa/audit_operational_trust.py) / full gate) | [`reports/out/client_pack_latest/summary.json`](../reports/out/README.md), `ORIGENLAB_SQLITE_PATH`, [`reports/out/active/`](../reports/out/README.md) CSVs in [`operational_trust.leads_active_paths`](../src/origenlab_email_pipeline/operational_trust/__init__.py) | stdout | After pack build or when pack/DB/active CSVs change | **Yes** (critical checks) |
| [`audit_operational_trust.py`](qa/audit_operational_trust.py) | Scorecard: cohort, readiness nulls, hunt taxonomy, pack freshness, audit MD DB path, merged vs current hunt IDs | `active/` CSVs, pack `summary.json`, `docs/generated/CONTACT_READINESS_AUDIT.md` (gitignored), SQLite path for provenance | [`reports/out/active/operational_trust_scorecard.json`](../reports/out/README.md), `docs/generated/operational_trust_scorecard.md` (gitignored) | Same as gate / CI spot checks | **Yes** (critical checks) |
| [`check_evidence_links.py`](qa/check_evidence_links.py) | `http(s)` URL format + live HEAD/GET with thresholds | `source_url` in top20; hunt columns `url_fuente`, `url_contacto_compras`, `url_transparencia_oirs`, `url_pagina_laboratorio`, `url_perfil_comprador`, `url_evidencia_*` | stdout | Full publication validation (or skip via gate flag for internal-only) | **Yes** if run and thresholds exceeded; also fails if **no** URLs are collected (checked count 0) |
| [`export_candidate_audit.py`](qa/export_candidate_audit.py) | Read-only CSV: sample **`lead_master`** + **`contact_master`** rows through [`candidate_export_gate`](../src/origenlab_email_pipeline/candidate_export_gate.py) with **per-path noise strictness** matching Cola vs `export_marketing_from_contact_master` (`eligible`, `reject_reasons`, `*_hit` flags) | SQLite (`--db`), limits | CSV path (`--out`) | Spot checks, leakage review, parity baselines | **No** (informational; not the publish gate) |
| [`print_outbound_run_summary.py`](qa/print_outbound_run_summary.py) | Human-readable **`outbound_run`** (lane, gmail, sqlite, Sent folders, counts, artifact paths) from `archive_outreach_build_summary.json` or lead `*_outbound_summary.json` | `--json` path | stdout | After canonical archive/lead runs; operator trust / handoff | **No** |
| [`plan_reports_out_cleanup.py`](qa/plan_reports_out_cleanup.py) | **Read-only** plan for `reports/out`: bucket labels (`active_current`, `active_workspace_misc`, `client_pack_latest`, tmp/lab/archive/reference, …), file counts, sizes, largest files; optional `--json-out` to a file **outside** the tree | `--reports-out-dir` (default `reports/out`) | stdout (+ optional JSON path) | Before any future cleanup of generated outputs; never part of the daily send lane | **No** (informational) |
| [`plan_script_consolidation.py`](qa/plan_script_consolidation.py) | **Read-only** classifies each `scripts/**/*.py` vs [`SCRIPT_MAP.md`](../docs/SCRIPT_MAP.md), lists wrapper candidates, `unknown`, break-glass hits, doc/test refs; optional JSON | `--scripts-dir`, `--map` | stdout (+ optional JSON) | Before deprecating, re-homing, or deleting scripts; never part of the daily send lane | **No** (informational) |
| [`plan_source_quality.py`](qa/plan_source_quality.py) | **Read-only** text scan of `src/origenlab_email_pipeline` and `scripts/`: line counts, heuristic **vertical** buckets, subprocess/SQLite-keyword hints, `core.*` vs top-level import hints; optional JSON | `--src-dir`, `--scripts-dir`, `--top`, `--json-out` | stdout (+ optional JSON) | Refactor / ownership planning only (see [`QUALITY_AND_REFACTOR_STRATEGY.md`](../docs/QUALITY_AND_REFACTOR_STRATEGY.md)); not authority | **No** (informational) |

`tools/` — not part of the publication gate; listed here for operator adjacency to planners above.

| Script | Purpose | Main inputs | Main outputs | When to run | Blocking? |
|--------|---------|-------------|--------------|-------------|-----------|
| [`archive_reports_out_generated.py`](tools/archive_reports_out_generated.py) | **Move** selected files under `reports/out` to `archive/manual_cleanup/…` (same bucket labels as `plan_reports_out_cleanup`); **default dry-run** | `--reports-out-dir`, include flags, optional `--json-out` | stdout (+ optional JSON); **`--apply`** + **`--archive-slug`** to move (no deletes) | After a read-only `plan_reports_out_cleanup` pass when you want to relocate generated clutter | n/a (filesystem side effects only) |

**Docs:** [RUNBOOK §4](../docs/RUNBOOK.md#m-eprun-publish-qa), [RUNBOOK — cold export gate](../docs/RUNBOOK.md#m-eprun-cold-export-gate), [REPORTING — QA leads](../docs/REPORTING.md#m-eprep-leads-qa), [ARCHITECTURE — trust layer](../docs/ARCHITECTURE.md#m-eparch-qa-trust), [ARCHITECTURE — export eligibility](../docs/ARCHITECTURE.md#m-eparch-export-gate).
