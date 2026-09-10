# OrigenLab — build and deployment status

**Purpose.** The single answer to "what is actually built, applied and
deployed right now." One page, facts only.

**Status: non-canonical.** This is the build-state index defined in
[`README.md`](README.md) → *Non-canonical build-state index*. It is not one of
the seven canonical V2 documents and cannot override them.

**This document reports:** the verified real state of every era, slice, app and
environment. **It owns no rule, decision, target, workflow, architecture or
migration policy.** What the system *should* be is owned by
[`README.md`](README.md) and the six canonical V2 documents it indexes (V2),
and by [`architecture/CURRENT_SYSTEM_TRUTH.md`](architecture/CURRENT_SYSTEM_TRUTH.md)
(V1). Where this file and a canonical document disagree about *what is built*,
the canonical document is carrying a build-state claim it should not carry and
is corrected under its own owner — not overridden here.

**Why it exists separately.** Architecture documents describe intent and drift
out of date the moment something ships — `ARCHITECTURE.md` §1 claimed "No
Supabase project, schema, role, bucket or table exists yet" for two days after
32 tables shipped and went green in CI. Status belongs in one place that is
cheap to correct, and no architecture document should restate it.

**Maintenance rule** (owned by [`README.md`](README.md), *Documentation source
of truth*). Any PR that changes what is built, applied or deployed updates this
file — including the `Last verified` line — **in the same PR**. A PR that only
changes design, rules or targets does not touch it.

Last verified: **2026-09-10**, against `origin/main` @ `12f65e6d` plus the hosted role bootstrap
on this branch, measured from a clean local `supabase db reset`.

## 1. Eras

| Era | State |
|---|---|
| **V1** | **Running and authoritative** for every fact it owns today, including all outbound safety |
| **V2** | Architecture **accepted 2026-09-05**. **Local schema foundation only.** No hosted project, no application code, nothing deployed |

## 2. V2 — slice status

Slices and their gates are defined in [`MIGRATION.md`](MIGRATION.md) §5.

| Slice | State | Note |
|---|---|---|
| 0 — local foundation | **DONE** | `supabase/roles.sql` + 18 migrations → 4 roles, 7 schemas, 33 tables, grants, RLS. Proven by `supabase/tests/` and `supabase/scripts/`, enforced by `.github/workflows/supabase.yml` on every push touching `supabase/**` |
| 0 — hosted gates | **NOT STARTED — the tooling now exists and has never been pointed at a project** | **No hosted Supabase project has been adopted, and no database connection has ever been made to one from this repository.** All 11 checks in [`MIGRATION.md`](MIGRATION.md) §5.2 remain unproven against a hosted project; checks 1–9 are proven **locally only**, checks 10–11 have never run. What changed is the tooling: `supabase/scripts/slice0_audit.sh` ([`OPERATIONS.md`](OPERATIONS.md) §4.2) can run the catalogue half read-only against a hosted project, and `supabase/scripts/hosted_role_bootstrap.sh` ([`OPERATIONS.md`](OPERATIONS.md) §4.3) can produce the reviewed role bootstrap such a project would need first — §2.3, §2.4. See §2.5 for the one hosted project that is known to exist |
| 1 — Auth / `platform.*` | NOT STARTED | |
| 2 — CRM identity + V1 row migration | NOT STARTED | |
| 3 — Quotes, lines, FX, snapshot, PDF | NOT STARTED | |
| 4 — Evidence, comms, shadow Gmail, catalog, notices | NOT STARTED | |
| 5 — Wave 1A load, send functions, reconciler | NOT STARTED | |
| 6 — Sender handoff | NOT STARTED | |
| 7 — Rollback window | NOT STARTED | |
| 8 — Decommission V1 | NOT STARTED | |

### 2.1 V2 local foundation — measured

| Item | Value |
|---|---|
| Migrations | 18, under `supabase/migrations/` |
| Schemas | 7 — `crm`, `comms`, `outbound`, `evidence`, `catalog`, `procurement`, `platform` |
| Tables | **33** — `crm` 16, `comms` 4, `outbound` 6, `evidence` 2, `catalog` 2, `procurement` 1, `platform` 2 |
| Roles | 4 — `origenlab_owner` (NOLOGIN), `origenlab_migrator`, `origenlab_api`, `origenlab_worker`; all `NOBYPASSRLS` |
| RLS policies | 127 |
| pgTAP assertions | **391 across 11 files** — 372 across the original ten, unchanged, plus 19 in `100_hosted_role_bootstrap.sql` |
| Foreign keys | 102, **all** index-covered — 81 unconditional, 21 implied-partial |
| `SECURITY DEFINER` functions | **zero** — the closed list of eight arrives in slices 3 and 5 |
| Data API (PostgREST) | **off**; the seven schemas are not exposed |
| Send flags | `outbound.send_control` single row, **both `false`** |
| Rows of business data | **zero** |

### 2.2 What does not exist yet in V2

- **`apps/worker`** — named in [`ARCHITECTURE.md`](ARCHITECTURE.md) §1–§2 as the owner of
  Gmail sync, MIME parsing, PDF rendering, ChileCompra fetching and the single
  send path. Not created.
- **Any application code touching the seven schemas.** Slice 0 has **zero
  consumers**; nothing in `apps/` references it.
- **The eight privileged send/quote functions** of [`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2.
- **The Wave 1A loader.** The bundle itself exists on disk with its `.sha256`
  sidecar; no code loads it.
- **The `ol migrate` / `ol audit` CLI** documented in [`OPERATIONS.md`](OPERATIONS.md) §4
  — marked there as `EXAMPLE — NOT YET IMPLEMENTED`, and it is not implemented. The
  catalogue half of what its four `ol audit` subcommands would check is built, under a
  different name and as a single read-only tool: see §2.3. `ol migrate` is not.
- **An adopted hosted Supabase project.** The tooling to audit one (§2.3) and to bootstrap
  its roles (§2.4) now exists; neither has been pointed at a project. One project is known to
  exist and is *not* adopted — §2.5.
  `supabase/scripts/lib/local_target.sh` still refuses a non-loopback host by design and
  every §4.1 script still resolves its target through it; the hosted path is a separate
  boundary in `supabase/audit/olaudit/target_hosted.py` that shares no code with it and
  refuses to start without an explicit authorisation flag, a reviewed target file at an
  approved path, a clean tracked working tree and an unlinked project.

### 2.3 Slice 0 audit — measured

| Item | Value |
|---|---|
| Entry point | `supabase/scripts/slice0_audit.sh` → `supabase/audit/run_audit.py` (Python standard library only) |
| Modes | `--mode local`; `--mode hosted --simulate`; `--mode hosted --authorize-hosted-connection`; `--verify-report` |
| SQL check files | 15, under `supabase/audit/sql/`, each statically proven to be a single read before any connection is opened |
| Engine checks | 9 — one API-key-type record, seven operator attestations, one derived Data API conclusion |
| Committed baseline | `supabase/audit/baselines/slice0.json`, captured from a clean local `supabase db reset` and reviewed in the change that added it |
| Unit tests | **217** — `python3 -m unittest discover -s supabase/audit/tests -t supabase/audit` (155 audit + 62 hosted bootstrap, §2.4) |
| Failure-injection checks | **53** — `supabase/scripts/audit_failure_tests.sh` |
| Local verdict | `LOCAL_PASS` — 13 required proofs satisfied, `a13` corroborated, `a14` recorded |
| Hosted runs performed | **zero.** No hosted target file has ever existed, and no database connection has ever been made to a hosted project — §2.5 |
| Write capability | **none.** No write mode, no baseline-writing mode, no redaction-disabling flag, and no mutation statement in either mode |

The one place a write is attempted anywhere in this tooling is
`audit_failure_tests.sh` scenario L, the negative test proving the audit's read-only
transaction refuses a mutation with SQLSTATE `25006`. It runs only against the
disposable local database, inside a rollback-only harness.

### 2.4 Hosted role bootstrap — measured

| Item | Value |
|---|---|
| Entry point | `supabase/scripts/hosted_role_bootstrap.sh` → `supabase/audit/run_bootstrap.py` (Python standard library only) |
| Modes | `--environment <classification> --dry-run`. **There is no apply mode and no connection in any mode** |
| Bootstrap file | `supabase/hosted_roles.sql`, 4 roles, 1 membership, 15 analysed statements |
| Static analyser | `supabase/audit/olaudit/bootstrap.py` — closed role set, no password, no platform-role grant, no unrecognised statement shape |
| Approved environments | `staging` (Pro daily backups, seven-day retention, PITR declined). **`production` blocked** — no recorded RPO/PITR decision |
| Unit tests | **217** total — `python3 -m unittest discover -s supabase/audit/tests -t supabase/audit`; 62 of them cover the bootstrap |
| Failure-injection checks | **70** — `supabase/scripts/hosted_bootstrap_failure_tests.sh`, no database required |
| Execution rehearsal | **9 checks** — `supabase/scripts/hosted_bootstrap_rehearsal.sh`, local disposable database only, one always-rolled-back transaction |
| pgTAP | 19 assertions in `supabase/tests/100_hosted_role_bootstrap.sql` |
| Applications performed | **zero.** The bootstrap has never been applied to any project, hosted or otherwise, outside the rolled-back local rehearsal |
| Passwords assigned | **zero.** The file cannot express one; the migrator credential is a separate operator action with a hidden secret input |

### 2.5 The `origenlab-v2` hosted project — known, not adopted

One hosted Supabase project, **`origenlab-v2`**, is known to exist. Its state, stated
precisely because the distinction matters:

- it was **discovered through an authenticated control-plane listing** (organization and
  project listing) — so it is not true that Supabase has never been contacted from this
  work;
- **no database or data-plane connection has been made to it**;
- **no SQL, DDL, DML, storage, Auth, API, migration or project configuration operation has
  been performed against it**;
- it remains **unreconciled, unadopted and unapproved**.

Nothing in this repository is pointed at it. Its **project reference, organisation
identifiers and host name are deliberately not recorded here or anywhere else in tracked
content** — `scripts/security/check-public-repo-hygiene.sh` fails if a project reference or
`db.<ref>.supabase.co` host name ever enters tracked content, and the Slice 0 audit takes a
hosted target only from a git-ignored file at one approved path. The human-facing project
name above is the only hosted identifier this repository records.

Adopting it is a separate decision that has not been taken. Slice 0's hosted gates
([`MIGRATION.md`](MIGRATION.md) §5.2 checks 1–11) are unproven against it or any other
project.

## 3. V1 — running state

| Item | Value |
|---|---|
| Apps | `apps/api` (FastAPI :8001), `apps/dashboard`, `apps/dashboard-proxy`, `apps/email-pipeline`, `apps/web` |
| Alembic migrations | 46, head **`20260902_0046`** |
| Durable CRM | PostgreSQL `commercial.*` |
| Archive | `~/data/origenlab-email/sqlite/emails.sqlite`, **~66 GB**, ~221,752 messages |
| Outbound authority | **V1 SQLite only** — campaign, recipients, send attempts, suppression. There is no Postgres mirror of the campaign ledger |
| Sender | Gmail API only, no SMTP. `origenlab outbound-campaign send` is **dry-run by default**; `--live` additionally requires an OAuth client and token file |

### 3.1 Deployment

| Where | What |
|---|---|
| Render | `origenlab-api` (Docker, 1 GB disk), `origenlab-dashboard` (static), managed Postgres. **No Render cron jobs.** |
| Cloudflare | `apps/dashboard-proxy` Worker at `dashboard.origenlab.cl/api*` |
| Operator machine | `auto-refresh-mail` every 3 min; `auto-mirror-dashboard` every 1 min; systemd user units for the local API |
| GitHub Actions | 7 workflows; **none is scheduled** — all are push/PR path-filtered |

### 3.2 Known operational facts that surprise people

- **The production runtime checkout is 125 commits behind `origin/main`.**
  `/home/rafael/dev/freelance/origenlab` is at `66623060`. That is the tree the
  cron jobs execute.
- **The SQLite→Postgres dashboard mirror is not running, and that is safe.** It
  fail-closes on an Alembic head mismatch: the production tree expects
  `20260901_0042`, the database is at `20260902_0046`. `apps/api` reads the
  durable tables directly, so nothing an operator depends on flows through it.
  [`MIGRATION.md`](MIGRATION.md) §2 decides it **stays paused** and is never
  repaired merely to serve the migration.
- **The Postgres `outbound.*` sidecar mirror is stale.** `/mirror/outbound`
  reads it live and correctly, but its only writer is a parked break-glass
  script. Do not treat its contents as current.
- **V1 has no consent model and no unsubscribe mechanism.** No
  `List-Unsubscribe` header is generated and no unsubscribe endpoint exists.
  `manual_contact_status.active` is explicitly *not* consent — the code says so
  in two places. V2 designs an unsubscribe path
  ([`WORKFLOWS.md`](WORKFLOWS.md) §W10, still `[OPEN]`); it is not built.
- **`apps/email-pipeline/scripts/validate.sh` runs a narrow subset**, not the
  full pytest suite. Use `scripts/check-all.sh` for anything touching pipeline
  logic.

### 3.3 Open — needs an operator check, not a code change

- **Are `ORIGENLAB_COMMERCIAL_OPERATIONS_WRITES_ENABLED` and
  `ORIGENLAB_POSTGRES_WRITE_URL` set on the deployed Render service?** Neither
  appears in `render.yaml`, and `commercial_operations_writes_enabled` defaults
  to `False` in `apps/api/src/origenlab_api/settings.py`. If they were not set
  by hand in the Render dashboard, **every `POST /operations/*` durable write is
  returning 503 in production** and the Cotizaciones and Pipeline boards cannot
  write. Record the answer here once checked.

## 4. Cross-era hazards

- **Schema names collide.** `evidence.*`, `outbound.*` and `catalog.*` exist in
  **both** the V1 Alembic Postgres and the V2 Supabase Postgres, with entirely
  **disjoint** table sets. A `grep` for `outbound.` returns both systems. See
  [`MIGRATION.md`](MIGRATION.md) §2.
- **Two independent migration systems.** V1 is Alembic under
  `apps/email-pipeline/alembic/versions/`; V2 is the Supabase CLI under
  `supabase/migrations/`. They do not communicate.
