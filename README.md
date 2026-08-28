# OrigenLab

<p align="center">
  <strong>Commercial operations monorepo</strong> — public website, email intelligence pipeline, operator API with a durable commercial CRM core, and dashboard.
</p>

<p align="center">
  <a href="https://github.com/rafaelRojasVi/origenlab/actions/workflows/email-pipeline.yml"><img alt="email-pipeline CI" src="https://github.com/rafaelRojasVi/origenlab/actions/workflows/email-pipeline.yml/badge.svg" /></a>
  <a href="https://github.com/rafaelRojasVi/origenlab/actions/workflows/api.yml"><img alt="api CI" src="https://github.com/rafaelRojasVi/origenlab/actions/workflows/api.yml/badge.svg" /></a>
  <a href="https://github.com/rafaelRojasVi/origenlab/actions/workflows/dashboard.yml"><img alt="dashboard CI" src="https://github.com/rafaelRojasVi/origenlab/actions/workflows/dashboard.yml/badge.svg" /></a>
  <a href="https://github.com/rafaelRojasVi/origenlab/actions/workflows/web.yml"><img alt="web CI" src="https://github.com/rafaelRojasVi/origenlab/actions/workflows/web.yml/badge.svg" /></a>
  <a href="https://github.com/rafaelRojasVi/origenlab/actions/workflows/secret-scan.yml"><img alt="secret scan" src="https://github.com/rafaelRojasVi/origenlab/actions/workflows/secret-scan.yml/badge.svg" /></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" />
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg" />
</p>

---

## What this is

OrigenLab combines a **public marketing site** with **operator tooling** for commercial email intelligence, outbound safety, and commercial triage. Gmail and archive signals land in **SQLite** on the operator machine; **Postgres** holds rebuildable machine mirrors **plus the durable human CRM** (`commercial.sales_opportunity`, tasks, activities, organizations, contacts). Machine systems propose; the durable CRM records human commercial truth. The API/dashboard never send mail or mutate outreach state; durable CRM writes flow only through the allowlisted `/operations/*` commands. Canonical architecture: [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](docs/architecture/CURRENT_SYSTEM_TRUTH.md).

This public repository holds **code, tests, and documentation only**. Mail exports, SQLite files, generated reports, and client collateral stay outside Git by design.

| Write path | Surfaces |
|------------|----------|
| Machine: [`apps/email-pipeline`](apps/email-pipeline/) — ingest, mart, safety, explicit `--apply` workflows. Human CRM: `POST /operations/*` on [`apps/api`](apps/api/) | [`apps/api`](apps/api/) `:8001` · [`apps/dashboard`](apps/dashboard/) `:5173` (reads + allowlisted CRM commands via [`apps/dashboard-proxy`](apps/dashboard-proxy/)) |

Full topology: [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) · outbound rules: [`apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md`](apps/email-pipeline/docs/OUTBOUND_SOURCE_OF_TRUTH.md)

## Operator surfaces

The active operator UI is [`apps/dashboard`](apps/dashboard/) (Streamlit was retired). Sections read machine evidence and the durable CRM; the only writes are the allowlisted `/operations/*` CRM commands and the tender annex import.

| Section | Role |
|---------|------|
| **Hoy (Today)** | Durable commercial work queue + daily summary, automation health, queue counts |
| **Bandeja de revisión** | Warm-case triage (machine evidence) |
| **Negocios** | Machine-proposed opportunity intake + durable CRM commands + historical deal ledger |
| **Prospectos / Clientes / Catálogo / Proveedores / Pagos** | Machine-evidence views (mirror) |
| **Licitaciones / equipos** | W1 actionable tender queue + T1 term intelligence + annex import |
| **Sistema** | Service status, backend/mirror context |

Handoff and freeze rules: [`apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md)

## Architecture

```mermaid
flowchart LR
  Gmail[Gmail / ChileCompra / archives] --> MailRefresh[auto-refresh-mail + tender workers]
  MailRefresh --> SQLite[(SQLite operational truth)]
  SQLite --> DailyCore[daily-core + read models]
  DailyCore --> Reports[reports / safety state]
  DailyCore --> Mirror[auto-mirror-dashboard]
  Mirror --> PGM[(Postgres machine mirrors)]
  PGD[(Postgres durable CRM commercial.*)] <--> API[FastAPI operator API]
  PGM --> API
  API --> Proxy[dashboard-proxy allowlist]
  Proxy --> Dashboard[React operator dashboard]
  Web[Astro public website] -. separate .- Dashboard
```

| Layer | Role |
|-------|------|
| Gmail / ChileCompra / archives | External sources (not in Git) |
| SQLite | Machine operational truth — ingest, outbound safety, Sent memory |
| Postgres mirrors | Rebuildable machine projections (warm cases, deals, catalog, leads, PR3 opportunities) |
| Postgres durable CRM | Human commercial truth — sales opportunities, tasks, activities, organizations, contacts |
| API / proxy / dashboard | Reads + allowlisted `/operations/*` CRM commands; mirror data is **not** send approval |
| `apps/web` | Public marketing site (separate from operator stack) |

Two debounced cron loops keep ingest and publish separate: Gmail → SQLite (~3 min) and SQLite → Postgres/dashboard (every minute; default 60s cooldown). Runbook: [`apps/email-pipeline/docs/pipeline/OPERATOR_CRON.md`](apps/email-pipeline/docs/pipeline/OPERATOR_CRON.md)

## Applications

| App | Path | Stack | Writes? |
|-----|------|-------|---------|
| **Web** | [`apps/web/`](apps/web/) | Astro, Tailwind, TypeScript | No operational data |
| **Email pipeline** | [`apps/email-pipeline/`](apps/email-pipeline/) | Python 3.12, `uv`, SQLite | Yes — local SQLite/reports/mirrors when explicitly applied |
| **Operator API** | [`apps/api/`](apps/api/) | FastAPI `:8001` | Durable CRM commands under `/operations/*` only (+ tender annex import) |
| **Dashboard** | [`apps/dashboard/`](apps/dashboard/) | React, Vite `:5173` | Via allowlisted API commands only |
| **Dashboard proxy** | [`apps/dashboard-proxy/`](apps/dashboard-proxy/) | Cloudflare Worker | Trust boundary — strict method+path allowlist |

**Default ports:** API `:8001` · Dashboard `:5173` · Web `:4321`

## Source-of-truth boundaries

- **SQLite** — machine operational truth for ingest, outbound safety, and send decisions.
- **Postgres mirrors** — rebuildable machine projections published by `auto-mirror-dashboard`.
- **Postgres durable CRM** (`commercial.*` durable tables) — human commercial truth; written only via `POST /operations/*` with trusted operator identity.
- **API / dashboard** — reads plus allowlisted CRM commands; never treat mirror responses as send approval.
- **Send / outreach** — human-reviewed batches via email-pipeline scripts; **no autonomous send path**.
- **Generated datasets** — `reports/out`, SQLite, and mail exports stay out of Git.

## Validation and audits

| Check | Command | Notes |
|-------|---------|-------|
| **Active operator stack** | [`./scripts/validate-active-stack.sh`](scripts/validate-active-stack.sh) | email-pipeline + API + dashboard; no send/purge |
| **Public-repo hygiene** | [`./scripts/security/check-public-repo-hygiene.sh`](scripts/security/check-public-repo-hygiene.sh) | Tracked files only; no network |
| **Remote response audit** | `apps/api/scripts/remote_response_audit.py` | Live GET contract checks behind Cloudflare Access; skips without CF credentials |
| **Remote latency audit** | `apps/api/scripts/remote_latency_audit.py` | Warm-run latency budgets; cold probe advisory |

Per-app CI: `./scripts/validate.sh` inside each app. Heavier monorepo sweep: [`./scripts/check-all.sh`](scripts/check-all.sh)

Before changing repo visibility: [`docs/PUBLIC_RELEASE_CHECKLIST.md`](docs/PUBLIC_RELEASE_CHECKLIST.md)

## Quick start

**Website**

```bash
cd apps/web && npm ci && npm run dev
```

**Operator API + dashboard**

```bash
cd apps/api && uv sync && uv run uvicorn origenlab_api.main:app --host 127.0.0.1 --port 8001
cd apps/dashboard && npm ci && npm run dev   # expects API on :8001
```

**Email pipeline — read-only status**

```bash
cd apps/email-pipeline && uv sync && uv run origenlab operator-automation-status
```

Do not run `--apply`, send, purge, or mirror workflows from this README. See app runbooks for operator procedures.

## Security

This repository is **public**. Do not commit `.env`, SQLite databases, mail archives, `reports/out`, keys, or client collateral.

| Control | Location |
|---------|----------|
| Coordinated disclosure | [`SECURITY.md`](SECURITY.md) |
| Public-repo guide | [`docs/SECURITY_PUBLIC_REPO.md`](docs/SECURITY_PUBLIC_REPO.md) |
| Secret scan (gitleaks) | [`.github/workflows/secret-scan.yml`](.github/workflows/secret-scan.yml) |
| Dependabot | [`.github/dependabot.yml`](.github/dependabot.yml) |

## Documentation

| Topic | Doc |
|-------|-----|
| Canonical system truth | [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](docs/architecture/CURRENT_SYSTEM_TRUTH.md) |
| Target commercial architecture | [`docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md`](docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md) |
| Monorepo architecture | [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) |
| Documentation map | [`docs/DOCUMENTATION_MAP.md`](docs/DOCUMENTATION_MAP.md) |
| Release process | [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) |
| Email pipeline | [`apps/email-pipeline/docs/README.md`](apps/email-pipeline/docs/README.md) |
| Operator API | [`apps/api/README.md`](apps/api/README.md) |
| Dashboard handoff | [`apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md`](apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md) |
| Web app | [`apps/web/docs/README.md`](apps/web/docs/README.md) |
| Contributing | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

## License

MIT — see [`LICENSE`](LICENSE).
