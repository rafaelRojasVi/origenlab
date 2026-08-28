# Critical Surface Index

**Status:** planning / ownership guide (read-only)  
**Owner:** email-pipeline maintainers  
**Last reviewed:** 2026-06-13

## Purpose

This document maps **high-risk and high-importance** files in `apps/email-pipeline`. It helps humans and agents understand **what must not break** before changing code, running refactors, or interpreting planner output.

Recent read-only planners (local runs, 2026-06-13) scanned roughly **485 Python files** (299 `src/` + 186 `scripts/`), **~2942 functions**, and many side-effect markers (SQLite writes, Gmail, Postgres, `--apply`). Those numbers are **heuristic snapshots**, not live inventory authority.

## Safety statement

- **This is not deletion authority.** Low import counts, low doc references, or “unknown” planner buckets do **not** prove a file is unused.
- **Break-glass paths** (send, purge, Gmail ingest with destructive flags, Postgres mirror `--apply`, broad NDR apply) need **explicit operator intent** and extra review.
- **SQLite remains operational truth** for outbound safety; Postgres mirror and dashboard reads are **reporting**, not send approval.
- Planner PRs and doc PRs should **not** change runtime behavior.

See also: [`../CRUD_SAFETY.md`](../CRUD_SAFETY.md) · [`../OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md) · [`../EXPERIMENTAL_PARKED.md`](../EXPERIMENTAL_PARKED.md).

## Source planners (risk maps, not authority)

| Planner | Command | What it shows |
|---------|---------|---------------|
| Source quality | `uv run python scripts/qa/plan_source_quality.py` | LOC, vertical buckets, subprocess/SQLite keyword hints |
| Script consolidation | `uv run python scripts/qa/plan_script_consolidation.py` | Script sprawl, doc/test refs, wrapper candidates |
| Function surface | `uv run python scripts/qa/plan_function_surface.py` | Per-module functions, CLI markers, risk heuristics |
| Import surface | `uv run python scripts/qa/plan_import_surface.py` | Import graph, facade pairs, zero-import scripts |

Outputs land under `reports/local/*` (gitignored). Re-run after major tree changes; compare summaries, do not treat CSV/JSON as policy.

---

## Tier 0 — dangerous side effects

Files that can **send mail**, **delete SQLite data**, **mutate Gmail-derived truth**, or **write Postgres mirror** state. Default posture: **dry-run / plan-only** unless the operator deliberately passes apply/send flags.

| File | Why sensitive | Guardrail |
|------|---------------|-----------|
| [`scripts/qa/send_inline_html_email_via_gmail_api.py`](../../scripts/qa/send_inline_html_email_via_gmail_api.py) | Sends **real email** via Gmail API when not in dry-run / build-only modes | Break-glass only; see [`../SCRIPT_MAP.md`](../SCRIPT_MAP.md#break-glass-scripts) |
| [`src/origenlab_email_pipeline/gmail_send.py`](../../src/origenlab_email_pipeline/gmail_send.py) | MIME / inline-image helpers used on the **send path** | No direct CLI; changes affect break-glass send script behavior |
| [`scripts/tools/purge_email_domain_from_sqlite.py`](../../scripts/tools/purge_email_domain_from_sqlite.py) | **Domain-level SQLite purge** across many tables | `--apply` required; irreversible without backup |
| [`scripts/tools/purge_contact_emails_from_sqlite.py`](../../scripts/tools/purge_contact_emails_from_sqlite.py) | **Email-level SQLite purge** | `--apply` required; irreversible without backup |
| [`scripts/tools/purge_mailbox_from_sqlite.py`](../../scripts/tools/purge_mailbox_from_sqlite.py) | **Mailbox-scoped SQLite purge** | `--apply` required; irreversible without backup |
| [`scripts/ingest/05_workspace_gmail_imap_to_sqlite.py`](../../scripts/ingest/05_workspace_gmail_imap_to_sqlite.py) | Gmail IMAP → SQLite `emails`; **`--replace-source`** deletes rows for a mailbox source | Prefer `uv run origenlab gmail-ingest`; never `--replace-source` in safe loops |
| [`scripts/leads/backfill_contacted_from_gmail_sent.py`](../../scripts/leads/backfill_contacted_from_gmail_sent.py) | Writes **`outreach_contact_state`** from Sent history | Dry-run default; `--apply` updates operator memory |
| [`scripts/sync/sync_dashboard_postgres_mirror.py`](../../scripts/sync/sync_dashboard_postgres_mirror.py) | Loads **dashboard Postgres mirror** from SQLite | Dry-run default; `--apply` writes Postgres; parked lane — [`../EXPERIMENTAL_PARKED.md`](../EXPERIMENTAL_PARKED.md) |
| [`scripts/sync/sync_commercial_deals_postgres_mirror.py`](../../scripts/sync/sync_commercial_deals_postgres_mirror.py) | Commercial deals **Postgres mirror** sync | Dry-run default; `--apply` writes Postgres |
| [`scripts/sync/sync_catalog_postgres_mirror.py`](../../scripts/sync/sync_catalog_postgres_mirror.py) | Catalog **Postgres mirror** sync | Dry-run default; `--apply` writes Postgres |

**Related policy:** [`../RUNBOOK.md`](../RUNBOOK.md) · [`../SCRIPT_MAP.md`](../SCRIPT_MAP.md) · operator CLI `origenlab` subcommands (dry-run defaults where documented).

---

## Tier 1 — business correctness

Core logic for **who may be contacted**, **what counts as contacted**, **mart shape**, and **mirror semantics**. Bugs here skew exports, dashboard views, and operator trust — usually without an obvious crash.

| File | Business meaning | Change risk | Suggested review |
|------|------------------|-------------|------------------|
| [`src/origenlab_email_pipeline/warm_case_sender_rules.py`](../../src/origenlab_email_pipeline/warm_case_sender_rules.py) | Role / hold rules for **warm-case** and personalized holds | Wrong rule → misclassified send eligibility | `tests/test_warm_case_sender_rules.py`; [`../RUNBOOK.md`](../RUNBOOK.md) warm-case notes |
| [`src/origenlab_email_pipeline/core/outbound/broad_marketing_contacts.py`](../../src/origenlab_email_pipeline/core/outbound/broad_marketing_contacts.py) | Normalization / selection for **broad marketing** contact processing | Drift breaks export parity with gate expectations | `tests/test_broad_marketing_contacts_core.py` |
| [`src/origenlab_email_pipeline/core/mart/contact_org_builder.py`](../../src/origenlab_email_pipeline/core/mart/contact_org_builder.py) | **Contact / org mart** construction from SQLite mail graph | Mart shape drift affects downstream exports and audits | `tests/test_contact_org_builder_profile.py`, `tests/test_build_business_mart.py` |
| [`src/origenlab_email_pipeline/dashboard_postgres_sync.py`](../../src/origenlab_email_pipeline/dashboard_postgres_sync.py) | **Dashboard mirror** table mapping and sync orchestration | Wrong mapping → stale or misleading operator dashboard | `tests/test_sync_dashboard_postgres_mirror.py` |
| [`src/origenlab_email_pipeline/leads/contact_universe_review.py`](../../src/origenlab_email_pipeline/leads/contact_universe_review.py) | Read-only **contact universe** classification across CSV/SQLite/Gmail/sidecars | Wrong bucket → bad operator triage lists | `tests/test_contact_universe_review.py` |
| [`src/origenlab_email_pipeline/leads/contacted_universe_audit.py`](../../src/origenlab_email_pipeline/leads/contacted_universe_audit.py) | **Contacted-universe** audit rows and overlap semantics | Affects overlap / DNR audit CSVs operators rely on | `tests/test_audit_contacted_universe.py` |
| [`src/origenlab_email_pipeline/lead_research/institution_grouping_audit.py`](../../src/origenlab_email_pipeline/lead_research/institution_grouping_audit.py) | **Institution grouping** for lead research / Clientes views | Domain/email grouping errors → wrong institution scope | `tests/test_institution_grouping_audit.py` |

---

## Tier 2 — large / complex modules (understand before refactor)

Largest modules from `plan_source_quality` / function-surface runs (~1k+ LOC). Size alone is not a defect; it signals **high coupling** and **thin test coverage risk**.

| File | Approx LOC | Why complex | Refactor posture |
|------|------------|-------------|------------------|
| [`src/origenlab_email_pipeline/core/research_automation.py`](../../src/origenlab_email_pipeline/core/research_automation.py) | ~1680 | OpenAI / subprocess orchestration, Tatiana-adjacent lab automation | **Lab boundary** — [`../TATIANA_LAB_BOUNDARY.md`](../TATIANA_LAB_BOUNDARY.md); characterize before extract |
| [`src/origenlab_email_pipeline/commercial/commercial_deal_promotion.py`](../../src/origenlab_email_pipeline/commercial/commercial_deal_promotion.py) | ~1630 | Commercial deal promotion / ledger-adjacent rules | `tests/test_commercial_deal_promotion.py`; small PRs only |
| [`src/origenlab_email_pipeline/leads/contacted_universe_audit.py`](../../src/origenlab_email_pipeline/leads/contacted_universe_audit.py) | ~1240 | Large audit pipeline; many inputs and CSV contracts | Add regression tests per audit finding; avoid drive-by cleanup |
| [`src/origenlab_email_pipeline/mart_core_postgres_migrate.py`](../../src/origenlab_email_pipeline/mart_core_postgres_migrate.py) | ~1180 | SQLite → Postgres mart loaders (break-glass migrate family) | Parked Postgres lane; no casual edits |
| [`src/origenlab_email_pipeline/dashboard_postgres_sync.py`](../../src/origenlab_email_pipeline/dashboard_postgres_sync.py) | ~1180 | Mirror sync orchestration across many tables | Pair changes with mirror verify tests |
| [`src/origenlab_email_pipeline/lead_research/institution_grouping_audit.py`](../../src/origenlab_email_pipeline/lead_research/institution_grouping_audit.py) | ~1165 | Institution rollup + sidecar overlays | Keep aligned with dashboard institution scope docs |
| [`src/origenlab_email_pipeline/leads/contact_universe_review.py`](../../src/origenlab_email_pipeline/leads/contact_universe_review.py) | ~1120 | Multi-source contact classification CLI core | Extend `tests/test_contact_universe_review.py` when behavior moves |
| [`src/origenlab_email_pipeline/warm_case_sender_rules.py`](../../src/origenlab_email_pipeline/warm_case_sender_rules.py) | ~1050 | Many role/hold branches | Extract only with characterization tests |

LOC figures are from `wc -l` on 2026-06-13; re-check after large edits.

---

## Recommended workflow before modifying critical files

1. **Identify tier** — Tier 0 needs operator intent; Tier 1/2 need domain owner context.
2. **Run focused tests** — e.g. `uv run pytest tests/test_<area>.py -q` for the rows above; avoid full-suite-only signal for localized edits.
3. **Read runbooks** — [`../RUNBOOK.md`](../RUNBOOK.md), [`../SCRIPT_MAP.md`](../SCRIPT_MAP.md), and any app-specific `docs/APP_CONTEXT.md` / handoff notes.
4. **Prefer characterization tests** before refactors on Tier 1/2 modules (lock observable outputs, not private helpers).
5. **Keep PRs small** — one vertical or one script family per PR when touching Tier 0–1.
6. **Docs / planner PRs stay read-only** — no behavior changes bundled with index or planner tweaks.

## Related docs

- [`../QUALITY_AND_REFACTOR_STRATEGY.md`](../QUALITY_AND_REFACTOR_STRATEGY.md) — staged refactor policy
- [`../DEPENDENCY_GROUPS.md`](../DEPENDENCY_GROUPS.md) — optional install groups (`gmail`, `postgres`, `lab`, `ml`, …)
