# CLAUDE.md — OrigenLab

Entry point for Claude Code sessions in this monorepo. Read this file, then
follow the router below — do not read broadly before it.

## Reading order (don't skip ahead)

1. This file
2. [`README.md`](README.md) — what each app is, quick start
3. [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](docs/architecture/CURRENT_SYSTEM_TRUTH.md) — what's deployed, where truth lives, write paths
4. [`docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md`](docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md) — direction, invariants for new work
5. The README of the app you're touching (below)
6. Code and tests

Do not recursively read all documentation before coding. Search other docs
only when the task specifically needs them (a business-rule question, an
operator runbook, a schema contract). Broad documentation archaeology is
exceptional, not the default.

## Repository purpose

OrigenLab is a **modular monolith** for laboratory-equipment commercial
operations: a public website, an email/tender intelligence pipeline, a
durable commercial CRM, and an operator dashboard. One monorepo, one API
app, one operator dashboard, one durable Postgres boundary. No
microservices.

## Canonical responsibility boundaries

```text
apps/email-pipeline   ingestion, extraction, machine intelligence,
                      rebuildable projections (SQLite-first)

PostgreSQL commercial.*  durable human commercial truth

apps/api              application/business command + read boundary
                      (FastAPI :8001)

apps/dashboard-proxy  authenticated browser/API security boundary
                      (Cloudflare Worker, strict method+path allowlist)

apps/dashboard        operator UI — presentation only, never truth

apps/web              public marketing site — no operator/CRM code
```

## Core rule

**Machine systems propose; the durable CRM records human commercial
truth.** No rebuildable projection may own a durable operator decision.

## Durable vs rebuildable

- **Durable** (survives any rebuild): `commercial.sales_opportunity`,
  `commercial.organization`, `commercial.contact`, `commercial.task`,
  `commercial.activity`, their append-only event tables. Written only via
  `POST /operations/*` on `apps/api` (trusted operator identity,
  `Idempotency-Key`, optimistic concurrency).
- **Rebuildable** (machine projections — may be dropped and rebuilt from
  source): PR2/PR3/PR4 read models (`commercial_identity`,
  `commercial_opportunity`, `commercial_procurement*`), warm cases, the
  Postgres mirror under `/mirror/*`, catalog/lead-intel mirrors.

Full detail: `docs/architecture/CURRENT_SYSTEM_TRUTH.md`.

## Per-app entry points

| App | README | Notes |
|---|---|---|
| `apps/email-pipeline` | [`apps/email-pipeline/README.md`](apps/email-pipeline/README.md) | Stricter safety rules in its own `AGENTS.md` — read first for pipeline work |
| `apps/api` | [`apps/api/README.md`](apps/api/README.md) | Operator reads + `/mirror/*` + durable `/operations/*` commands |
| `apps/dashboard` | [`apps/dashboard/README.md`](apps/dashboard/README.md) | React operator UI |
| `apps/dashboard-proxy` | [`apps/dashboard-proxy/README.md`](apps/dashboard-proxy/README.md) | Trust boundary — never weaken for convenience |
| `apps/web` | [`apps/web/README.md`](apps/web/README.md) | Astro public site, own `CLAUDE.md` |

## Engineering rules

- Do not rewrite shipped Alembic migrations; add corrective migrations
  instead. Downgrades that would drop human data are fail-closed.
- Preserve append-only audit/event semantics, optimistic concurrency, and
  command idempotency where they already exist.
- No durable foreign key into a rebuildable machine projection — attach
  provenance by logical ID instead.
- Human CRM mutations follow one path: dashboard → proxy → API route →
  service → repository → Postgres transaction → append-only event. No
  hidden second writers, no ad-hoc SQL against durable tables.
- Do not create microservices, a second database, an event bus, or a
  generic workflow engine. This product does not need them.
- Prefer extending the existing architecture over a parallel
  implementation. Search callers (imports, scripts, CI, docs) before
  deleting code — see `docs/architecture/COMMERCIAL_RESET_LEDGER.md` for
  the evidence standard used in the last cleanup pass.
- Run the affected app's test suite for anything you touch:
  `apps/email-pipeline/scripts/validate.sh` (via `./scripts/sync_test_env.sh`
  first), `apps/api/scripts/validate.sh`, `apps/dashboard`'s
  `npm run validate`, `apps/dashboard-proxy`'s `npm run validate`.

## Documentation rules

- Do not create a new `.md` file for implementation summaries, PR reports,
  temporary audits, task plans, completion reports, or one-off
  investigation output. Use the commit message, the PR description, or
  `/tmp` instead.
- Create permanent Markdown only for durable architecture, business
  policy, operator procedures, or API/data contracts — and only when no
  existing doc already covers it.
- Never create a `docs/archive/` (or similar) directory to park obsolete
  docs. Git history is the archive. Delete superseded documentation from
  the active tree instead of moving it aside.

## Git safety

- Never push, merge, or open a PR unless explicitly asked.
- Never delete rescue branches, worktrees, or stashes.
- Run `git status` before any destructive operation.
- Full workflow (branch naming, merge strategy, PR process): [`AGENTS.md`](AGENTS.md).
