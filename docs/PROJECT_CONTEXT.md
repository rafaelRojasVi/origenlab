# Project Context

Status: canonical  
Owner: project-maintainers  
Last reviewed: 2026-08-28

> Architecture truth (durable CRM vs machine mirrors, write paths, ownership):
> [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](./architecture/CURRENT_SYSTEM_TRUTH.md) ·
> [`docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md`](./architecture/TARGET_COMMERCIAL_ARCHITECTURE.md)

This is the primary monorepo context document for coding agents and contributors.

<a id="m-proj-what"></a>
## What this repo is

OrigenLab monorepo with these active applications:

- [`apps/web`](../apps/web/): public marketing website (Astro, static deployment).
- [`apps/email-pipeline`](../apps/email-pipeline/): email archive ingestion, enrichment, and reporting pipeline (Python + uv, **no FastAPI**), including **human-in-the-loop drafting assistance** for OrigenLab / Labdelivery-style commercial email (Tatiana copilot — eval and pilot workflows; **not** an autonomous sender).
- [`apps/api`](../apps/api/): **only** operator HTTP API (FastAPI on **:8001**) — operator/dashboard read routes, Postgres mirror reporting under **`GET /mirror/*`**, and the durable commercial CRM commands under **`POST /operations/*`** (trusted operator identity, idempotency, optimistic concurrency).
- [`apps/dashboard`](../apps/dashboard/): operator React UI (multi-section: Hoy, Bandeja, Negocios, Prospectos, Clientes, Licitaciones, Pagos, Proveedores, Catálogo, Sistema) — reads operator routes **and** selected `/mirror/*` lists; performs durable CRM mutations only through the allowlisted `/operations/*` commands.
- [`apps/dashboard-proxy`](../apps/dashboard-proxy/): Cloudflare Worker trust boundary at `dashboard.origenlab.cl/api*` — strict method+path allowlist; injects upstream auth; the browser never holds the API token.

**Operator automation (read-only health):** debounced Gmail → SQLite (`auto-refresh-mail`) and SQLite → Postgres/dashboard (`auto-mirror-dashboard`) loops with tracked cron wrappers — see [`OPERATOR_CRON.md`](../apps/email-pipeline/docs/pipeline/OPERATOR_CRON.md). Status: `uv run origenlab operator-automation-status`; also `GET /operator/automation-status` and the dashboard Today automation card (API skips live crontab inspection). Public-repo guardrails: [`SECURITY_PUBLIC_REPO.md`](./SECURITY_PUBLIC_REPO.md).

**Supabase:** not currently implemented. If introduced later, treat it as a **hosted Postgres read mirror** for dashboard/reporting unless a formal source-of-truth migration is explicitly approved. It does not replace SQLite for send/outreach safety.

<a id="m-proj-business"></a>
## Business goal

Support OrigenLab's commercial operation by:

- presenting clear, trustworthy public website content for quotation flows.
- extracting business signal from historical email archives into operational reports.

**Cold-outreach candidate lists (email-pipeline):** Current model is two-lane outbound with one shared gate: archive-first warm revival (archive-derived contacts) plus lead-based curated prospecting (`lead_master`). Shared policy is enforced in [`candidate_export_gate.py`](../apps/email-pipeline/src/origenlab_email_pipeline/candidate_export_gate.py), including suppression, Sent-folder history, and operator `outreach_contact_state` blockers (`contacted` / `replied` / `snoozed`). Current sender/blocker context is `contacto@origenlab.cl`; historical relationship evidence still comes from archive/mart tables. `contact_master` remains exploratory (not CRM truth), so outbound stays human-reviewed and batch-controlled. Canonical guidance: [`apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md`](../apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md).

**Commercial truth rules** (quotes, suppliers, what may be claimed): [`docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md`](./business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md). Policy there is **canonical** for the monorepo; templates and LLM prompts must align.

**Commercial email drafting (Tatiana):** Implemented under [`apps/email-pipeline`](../apps/email-pipeline/) as retrieval + guarded LLM draft *suggestions* with mandatory human review for eval and pilot batches. It reuses signals from the historical archive cohorts and must stay aligned with the same business rules above. Entry points: [`apps/email-pipeline/docs/dataset/TATIANA_DRAFTING_COPILOT.md`](../apps/email-pipeline/docs/dataset/TATIANA_DRAFTING_COPILOT.md), [`apps/email-pipeline/docs/dataset/TATIANA_PILOT_WORKFLOW.md`](../apps/email-pipeline/docs/dataset/TATIANA_PILOT_WORKFLOW.md). Public-facing business copy remains owned by [`apps/web/docs/company-scope.md`](../apps/web/docs/company-scope.md) and site data — the copilot does not replace those sources of truth.

<a id="m-proj-start"></a>
## Where to start (agent-first)

1. Read this file.
2. Choose app domain:
   - Web → [apps/web/docs/APP_CONTEXT.md](../apps/web/docs/APP_CONTEXT.md)
   - Email pipeline → [apps/email-pipeline/docs/APP_CONTEXT.md](../apps/email-pipeline/docs/APP_CONTEXT.md)
   - Operator API + mirror → [apps/api/README.md](../apps/api/README.md)
   - Dashboard → [apps/dashboard/README.md](../apps/dashboard/README.md)
3. Open only the canonical doc for your task type (under that app’s `docs/`, e.g. [apps/web/docs/RUNBOOK.md](../apps/web/docs/RUNBOOK.md#m-webrun-local) or [apps/email-pipeline/docs/RUNBOOK.md](../apps/email-pipeline/docs/RUNBOOK.md#m-eprun-path)):
   - run/procedure → `RUNBOOK.md` (in that app’s `docs/`)
   - architecture/data flow → `ARCHITECTURE.md`
   - business/scope constraints → `BUSINESS_CONTEXT.md`
   - outbound/source-of-truth (email-pipeline) → [`OUTBOUND_SOURCE_OF_TRUTH.md`](../apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md)

<a id="m-proj-rules"></a>
## Canonical source rules

- Business facts should come from canonical app data/docs, not duplicated agent overlays.
- Run procedures should come from app runbooks.
- Historical docs are reference-only.

<a id="m-proj-precedence"></a>
## Precedence

**Factual project truth** (what the repo is, how it runs, where data lives):

1. This file ([`docs/PROJECT_CONTEXT.md`](./PROJECT_CONTEXT.md))
2. That app’s [`APP_CONTEXT.md`](../apps/web/docs/APP_CONTEXT.md) (web) or [`APP_CONTEXT.md`](../apps/email-pipeline/docs/APP_CONTEXT.md) (email-pipeline), plus other **canonical app docs** in the same `docs/` folder (`RUNBOOK.md`, `ARCHITECTURE.md`, `BUSINESS_CONTEXT.md`; email-pipeline also [`DATA_LOCATIONS.md`](../apps/email-pipeline/docs/DATA_LOCATIONS.md#m-epdata-root)), and domain subfolders as in [`DOCUMENTATION_MAP.md`](./DOCUMENTATION_MAP.md#m-docmap-mapping)
3. That app’s [`README.md`](../apps/web/README.md) (web) or [`README.md`](../apps/email-pipeline/README.md) (email-pipeline)
4. Executable source: code, scripts, [`apps/email-pipeline/pyproject.toml`](../apps/email-pipeline/pyproject.toml) / [`apps/web/package.json`](../apps/web/package.json), schema modules, [`apps/email-pipeline/.env.example`](../apps/email-pipeline/.env.example)

**Policy and agent behavior** (safety rules, tone, “do not invent”, editor overlays):

- [`apps/web/AGENTS.md`](../apps/web/AGENTS.md), [`.cursor/rules`](../apps/web/.cursor/rules), [`.claude/agents`](../apps/web/.claude/agents), [`.claude/skills`](../apps/web/.claude/skills) — **must not override** factual statements in the layers above. If policy and facts conflict, **facts win**; adjust policy or the doc, do not invent implementation.

**Last:**

5. Historical, archive, and generated snapshots (`Status: historical` / [`generated/`](../apps/email-pipeline/docs/generated/) / dated audits)

<a id="m-proj-trust"></a>
## What not to trust as current truth

- Dated audits, one-off plans, and machine snapshots.
- Any doc explicitly marked `Status: historical` or `Status: generated` without recent regeneration.

<a id="m-proj-nav"></a>
## Navigation

- Documentation map: [`DOCUMENTATION_MAP.md`](./DOCUMENTATION_MAP.md#m-docmap-entry) · [link check & linking conventions](./DOCUMENTATION_MAP.md#m-docmap-link-check)
