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

Last verified: **2026-10-22** against main `e3db0940`.

## 0. Production now

What runs today. §1–§4 below keep the measured history behind it; where an older row there
says "frozen", "not deployed" or "not created", this section is the current state.

| Item | State (2026-10-22) |
|---|---|
| Durable database | Supabase `origenlab-v2` (PostgreSQL 17), **in production**. Migration head `20261022100000` (67 files in `supabase/migrations/`), applied by the release automation |
| Release | `OL_RELEASE_AUTOMATION_ENABLED=true` since 2026-10-13: a merge to `main`, once CI passes, runs `production-db-migrations` end to end — encrypted backup → migrations → worker → API → dashboard at the merge SHA → postflight. Render auto-deploy stays off |
| API | Render web service `origenlab` (`render.yaml` calls it `origenlab-api`), Python runtime, root `apps/api`, Starter, Oregon |
| Worker | Render Background Worker `origenlab-mail-triage`, starter, root `apps/worker`: a supervisor with triage, capture (every minute: gmail-sync → drive-file → quote-read) and tender children. 279–362 MB since 2026-10-21 (#813) |
| Legacy cron | `origenlab-gmail-sync` **suspended, not deleted**, kept for rollback ([`runbooks/MAIL_WORKER_CUTOVER.md`](runbooks/MAIL_WORKER_CUTOVER.md)) |
| Switches on Render | `ORIGENLAB_V2_COMMANDS_ENABLED`, `ORIGENLAB_V2_CRM_AUTHORING_ENABLED` and `ORIGENLAB_V2_QUOTING_ENABLED` on |
| Dashboard | `dashboard.origenlab.cl` behind the `dashboard-proxy` Worker; `hosted-smoke` checks every proxied GET every 30 minutes |
| Website | `origenlab.cl` on cPanel, deployed by `web-deploy` on merge |
| Data API | **Disabled** on `origenlab-v2` by the owner on 2026-10-22 (Supabase dashboard → Integrations → Data API) — closes `t01`/`d01`. Supabase keeps PostgREST polling with no exposed schema, so the Postgres log may still show `schema "pg_pgrst_no_exposed_schemas" does not exist` every ~32 s: platform noise, no schema reachable |
| Open gate | Legacy JWT keys on `origenlab-v2` not yet disabled; the exposed JWT secret not yet rotated ([`OPERATIONS.md`](OPERATIONS.md) §1.2, §2.5 below) |

## 1. Eras

| Era | State |
|---|---|
| **V1** | **Running and authoritative** for every fact it owns today, including all outbound safety |
| **V2** | Architecture **accepted 2026-09-05**. **Schema foundation only.** One hosted project exists, `origenlab-v2`; as of 2026-09-21 it is **audited, role-converged and reconciled to all 19 migrations**, and proven to hold no business data (§2.5). The remaining Slice 0 gate items are plan-and-configuration attestations, not unknowns. No application code, nothing deployed |

## 2. V2 — slice status

Slices and their gates are defined in [`MIGRATION.md`](MIGRATION.md) §5.

| Slice | State | Note |
|---|---|---|
| 0 — local foundation | **DONE** | `supabase/roles.sql` + 19 migrations → 4 roles, 7 schemas, 33 tables, grants, RLS. Head has since moved past slice 0: 31 migrations, 37 tables, 143 policies (§2.1, §2.7.35). Proven by `supabase/tests/` and `supabase/scripts/`, enforced by `.github/workflows/supabase.yml` on every push touching `supabase/**` |
| 0 — hosted gates | **BLOCKED — on attestation items only; the audit itself has now succeeded** | **The hosted Slice 0 audit completed against `origenlab-v2` on 2026-09-21** over the Supavisor session-mode route, and every SQL proof passed. `supabase/hosted_roles.sql` was applied under its atomic contract the same day and the four absent migrations were reconciled, so the project now carries all 19 (§2.5). Checks 1–9 of [`MIGRATION.md`](MIGRATION.md) §5.2 are therefore proven **against the hosted project**, not merely locally. **Checks 10–11 remain unproven**, and with them the eight attestation items `t01`–`t07` and `d01`: the project is on the Free plan with no backup entitlement, so no restore drill can be evidenced, and its Data API is running rather than off. Those are plan-and-configuration decisions for the owner. The project is **reconciled but still not adopted** — no committed file names it and no application code reads it. See §2.5 |
| 1 — Auth / `platform.*` | **PARTIAL — built locally, schema applied to `origenlab_clean`** | Shared Workspace sign-in with PIN-verified operator profiles and revocable sessions: four `platform` tables, API, proxy and dashboard (§2.7.38). The eight sign-in migrations were **applied to `origenlab_clean` on 2026-09-30** from canonical `main` (§2.7.39); the four tables are **empty** — no principal, profile, PIN or Google identity has been provisioned. Not applied to any hosted database, not deployed. Supabase Auth itself not started |
| 2 — CRM identity + V1 row migration | NOT STARTED | |
| 3 — Quotes, lines, FX, snapshot, PDF | **SCHEMA ONLY** | The three commercial-case tables — `crm.opportunity_organization`, `crm.opportunity_interest`, `crm.opportunity_evidence` — were built locally on 2026-09-22 and are **empty** (§2.7.16). No command, no quote work, nothing wired |
| 4 — Evidence, comms, shadow Gmail, catalog, notices | **PARTIAL — 4a Gmail capture running on Render (observed 2026-10-06); mail triage built, not applied, not deployed** | §2.7.49, §2.7.69. Catalog loaded (§2.7.61); notices not started |
| 5 — Wave 1A load, send functions, reconciler | NOT STARTED | |
| 6 — Sender handoff | NOT STARTED | |
| 7 — Rollback window | NOT STARTED | |
| 8 — Decommission V1 | NOT STARTED | |

### 2.1 V2 local foundation — measured

| Item | Value |
|---|---|
| Migrations | **23**, under `supabase/migrations/` — the 18 Slice 0 files, the 2026-09-20 corrective that added the Wave 1B `contact_control.source` labels and made `campaign.recontact_interval_days` optional for an archived campaign ([`DATA.md`](DATA.md) §7.6.4), the three 2026-09-21/22 slice 2 additives (the `gmail_message` / `drive_file` provenance kinds and the `document_reference` assertion kind (§2.7.6), the `source_record.review_noted` event, and the `contact_point.usage` value `individual_owner_unknown` (§2.7.14)), and the 2026-09-22 slice 3 migration that created the three commercial-case tables (§2.7.16) |
| Schemas | 7 — `crm`, `comms`, `outbound`, `evidence`, `catalog`, `procurement`, `platform` |
| Tables | **36** — `crm` 19, `comms` 4, `outbound` 6, `evidence` 2, `catalog` 2, `procurement` 1, `platform` 2 |
| Roles | 4 — `origenlab_owner` (NOLOGIN), `origenlab_migrator`, `origenlab_api`, `origenlab_worker`; all `NOBYPASSRLS` |
| RLS policies | 139 |
| pgTAP assertions | **475 across 12 files** — 412 as before, plus the 63 of `062_constraints_crm_commercial_case.sql` (§2.7.16) |
| Foreign keys | 118, **all** index-covered — 86 unconditional, 32 implied-partial |
| `SECURITY DEFINER` functions | **zero** — the closed list of eight arrives in slices 3 and 5 |
| Data API (PostgREST) | **off**; the seven schemas are not exposed |
| Send flags | `outbound.send_control` single row, **both `false`** |
| Rows of business data | **zero in every hosted database.** They are **not** zero locally: the persistent local development database `origenlab_dev` now holds the imported historical CRM/outbound baseline **plus 20 pending Gmail source records and their 26 unresolved assertions** — see the table below for the measured counts. Nothing was loaded into the hosted project, which remains proven empty of business data (§2.5) |

#### Local development database — measured 2026-09-21

Built under the hosted freeze (§2.8) so V2 work has somewhere durable to run.

| Item | Value |
|---|---|
| Container | `origenlab_dev_db`, pinned image `public.ecr.aws/supabase/postgres:17.6.1.165` |
| Port | **54332, published on 127.0.0.1 only** — stricter than the CLI's stack, which binds on all interfaces |
| Database | `origenlab_dev`; **quarantined 2026-09-22** (§2.7.9). Ledger records **20** migrations, head `20260921120000`, while the schema already carries migration `20260922090000`'s DDL — the constraint `domain_event_type_check` includes `source_record.review_noted`. The earlier claim here of "21 of 21, head `20260922090000`" was measured against the schema, not the ledger, and is withdrawn: this database does not describe itself |
| Structure | 7 schemas, 33 tables, 127 policies, 102 index-covered foreign keys, zero `SECURITY DEFINER`. **No longer identical to the CLI cluster's**: this database is quarantined, so the three migrations since 2026-09-22 — including the commercial case (§2.7.16) — were never applied to it. The clean room, not this, is what the chain is measured against |
| Rows of business data | **not zero — local only, and 2026-09-21's numbers no longer describe it**: a test run added fixture residue on 2026-09-22 (§2.7.9), so `crm.organization` is 1,815 not 1,812, `crm.contact_point` 9,462 not 9,460, `crm.domain_event` 22,560 not 22,544, `evidence.source_record` 31 not 24, `evidence.assertion` 11,488 not 11,474, `platform.operator` 8 not 1 and `platform.command_receipt` 9 not 0. The reproducible figures below are what the clean-room database carries. Measured 2026-09-21: `crm.*` **33,816** (`contact_point` 9,460 · `organization` 1,812 · `domain_event` 22,544; `person`, `opportunity`, `quote`, `task`, `affiliation` all **zero**), `outbound.*` **17,214** (`contact_control` 10,588 · `campaign_recipient` 3,481 · `send_attempt` 3,141 · `campaign` 3 · `send_control` 1), `evidence.*` **11,498** (`assertion` 11,474 · `source_record` 24), `comms.mailbox` 1, `platform.operator` 1, `catalog.*` and `procurement.*` zero |
| Of which staged from Gmail | **20 `evidence.source_record`** of kind `gmail_message`, all `review_status = 'pending'`, and their **26 `evidence.assertion`**, all `resolution = 'unresolved'` (20 `contact_address`, 6 `organization_name`). Staged 2026-09-21 from a local manifest by `stage_gmail_drive_evidence.py`; `crm.*` was counted before and after inside the staging transaction and did not change (§2.7.7) |
| Where these rows came from | the §2.7 historical importer and the §2.7.7 staging pass, both run against **this loopback database only**. No hosted project holds any of it |
| Send flags | one `outbound.send_control` row, **both `false`** — unchanged by every load above |
| Checkpoints | `~/data/origenlab-v2-local/checkpoints/`, outside Git, `0700`/`0600`, each with `.sha256` and `.meta.json` |
| Build template | `origenlab_template` in the **CLI** cluster; disposable `origenlab_test_<8 hex>` databases are cloned from it |

#### Clean-room database — measured 2026-09-22, post-R1

Built because `origenlab_dev` is quarantined (§2.7.9). It lives in the **same container**, as a
second database: the container is already proven loopback-only and labelled to this working
tree, and a second cluster would be a second thing to keep alive for no added isolation that
matters here.

| Item | Value |
|---|---|
| Database | `origenlab_clean`, in container `origenlab_dev_db` on 127.0.0.1:54332 |
| Entry point | `supabase/scripts/cleanroom_db.sh` — `build`, `verify`, `status`, `api-login`, `drop`. **No `restore` and no checkpoint reading**: rebuilding *is* the recovery procedure |
| Built from | `supabase/migrations/` (21 of 21, head `20260922090000`, one ledger row per file) → one seeded operator → `import_waves_into_v2.py --apply` → `promote_evidence_into_crm.py --apply` → `stage_gmail_drive_evidence.py --apply` once **per manifest** in `~/data/origenlab-v2-local/evidence/`, in sorted order. Nothing else has ever written to it |
| Name safety | the database name is the constant `OL_CLEAN_DBNAME` in `supabase/scripts/lib/local_target.sh` and is never taken from an argument or the environment. The guard refuses to resolve to anything but `origenlab_clean`, refuses `origenlab_dev` by name first, and **discards the development DSN**, so `ol_psql_dev` cannot connect inside a clean-room script. `build --force` prints the literal name immediately before the `DROP` |
| Rows of business data | `crm.*` **33,816** (`contact_point` 9,460 · `organization` 1,812 · `domain_event` 22,544; `person`, `opportunity`, `quote`, `task`, `affiliation`, `organization_domain` all **zero**), `outbound.*` **17,214** (`contact_control` 10,588 · `campaign_recipient` 3,481 · `send_attempt` 3,141 · `campaign` 3 · `send_control` 1), `evidence.*` **11,520** (`assertion` 11,485 · `source_record` 35), `comms.mailbox` 1, `platform.operator` **1**, `platform.command_receipt` **0**, `catalog.*` and `procurement.*` zero. **R1 (§2.7.11) added evidence only**: `crm.*` is the same 33,816 it was before the batch |
| Rows, 2026-09-30 | the counts above are the 2026-09-22 fresh baseline. Measured read-only on 2026-09-30, after the slice-1 apply (§2.7.39): ledger **40** rows, head **`20260929100000`**; **41** tables; `crm.organization` **1,821** · `crm.contact_point` **9,460** · `crm.opportunity` **44** · `crm.quote` **65** · `crm.domain_event` **23,093** · `outbound.contact_control` **10,588** · campaigns / recipients / attempts **4 / 4,488 / 3,141** — every pre-apply count preserved; `outbound.campaign_block` **1** active row (`legacy_campaign`, `septiembre18-2026-wave2`, `incident_hold_september_2026`, placed by the migrator); `platform.auth_principal`, `platform.operator_profile`, `platform.auth_session`, `platform.auth_event` all **0**; `platform.operator` 2, both `google_account`; send flags both `false` |
| Source records | **35 = 31 `gmail_message` + 4 `migration_manifest`.** All three numbers are asserted; the total alone would not distinguish 35 real records from 28 real ones and 7 fixtures |
| Staged from Gmail | **31 records and 37 assertions** — the 20 records / 26 assertions of the September manifest plus the 11 records / 11 assertions of R1 (§2.7.11) — each reapplied **exactly once** from local manifests in `~/data/origenlab-v2-local/evidence/`. All `pending` / `unresolved`; nothing has been reviewed. **No Gmail connection was made**; the manifests are files, and the staging tool imports no Google client |
| Fixture residue | **zero.** Four probes assert it by name: `dedupe_key like 'pytest-%'` 0, `email_norm like 'pytest-%'` 0, `platform.command_receipt` 0, `crm.domain_event` with a non-null `command_receipt_id` 0 |
| Send flags | one `outbound.send_control` row, **both `false`** |
| Verification | **Two contracts since 2026-09-30** (§2.7.39; [`OPERATIONS.md`](OPERATIONS.md) §4.1 “Verify it”). `verify` = the **data-bearing** contract, `data_bearing.sql` against `expected_data_bearing.json`: ledger ≡ `supabase/migrations/`, inventory, grant and sign-in privilege boundaries, send flags, no residue; counts no business row — **41 probes, passing on `origenlab_clean` as of 2026-09-30**. `verify --fresh-rebuild` = the **fresh-rebuild** contract, `verify.sql` against `expected_counts.json`: exact counts, true only right after `build` — **46 probes**; on `origenlab_clean` today it fails exactly the 19 decision-driven counts, by design. Both exact in both directions, both read-only |
| Seeded operator | one `platform.operator` row so the command boundary can resolve an identity. Its address is **fictitious and undeliverable** (`operador.local@example.invalid`) as of 2026-09-22; it used to be the developer's own mailbox, hard-coded in a public repository. Override it per build with `OL_CLEAN_OPERATOR_EMAIL=…`, which is environment, not tracked. `verify` counts the row and never reads its address, so the change moves no probe |
| Checkpoints | **none, deliberately.** It is rebuilt, not restored |
| Rebuildable right now | **Yes, and proven.** Rebuilt twice in immediate succession on 2026-09-22 from the same inputs; both runs ended at **38 probes, all exact**, with identical counts; rebuilt twice again on the same day after the commercial-case migration, both runs at **41 probes** (§2.7.16) |
| Preflight | every input is validated **before** the `DROP`, by the same tool that will replay it, in that tool's dry-run mode (no connection is opened): both Wave bundles' manifests and file hashes, and every staging manifest under the full version 2 rules. A refusal leaves the existing database untouched — `cleanroom_failure_tests.sh` M1–M4 prove the DROP announcement is never printed |

**`build --force` was blocked on 2026-09-22, and is not any more.** Three things were found
while re-declaring the baseline, and all three are now closed:

1. **The build never replayed R1.** It staged one hard-coded manifest path, so a rebuild would
   have produced the September twenty and silently lost the eleven. Fixed: the build stages
   every `*.json` in `~/data/origenlab-v2-local/evidence/` in sorted order, and the R1 manifest
   was placed there. Membership of that directory is what puts a batch in the baseline.
2. **The September manifest was `manifest_version` 1, and R1 made version 1 a refusal**
   (§2.7.11). Fixed by re-acquisition, not by editing the file: the labels were re-read from the
   mailbox **read-only** — one `messages.get` per id at `METADATA_ONLY`, no body requested, and
   no send, draft, label, archive, trash or modify call made — into a private acquisition file,
   and `reacquire_gmail_manifest_v2.py` rewrote the manifest against it. All twenty ids, source
   URIs, `acquired_at` values and 26 observations are unchanged; the senders and dates the old
   manifest claimed were re-checked against the mailbox and **all twenty matched exactly**. All
   twenty carry `INBOX`, so all twenty classify `primary_evidence`. The version 2 file is
   `~/data/origenlab-v2-local/evidence/gmail-2026-09-21-commercial.v2.json`; the version 1 file
   moved to `~/data/origenlab-v2-local/superseded/`, out of the directory that *is* the baseline.
3. **Preflight checked existence, not validity** — which is why (2) was a live hazard rather
   than an inconvenience: the drop happened first, and the refusal came three steps later
   against a half-loaded database. Fixed: preflight now dry-runs the real tools over every
   input before anything is dropped, and four failure tests (M1–M4) prove the DROP announcement
   is never reached.

**Rebuilt twice, 2026-09-22.** Two consecutive `build --force` runs from the same inputs each
ended at 38 probes, all exact: 31 `gmail_message` all `pending`, 37 Gmail assertions all
`unresolved`, `crm.person` / `opportunity` / `quote` / `task` / `affiliation` /
`organization_domain` all zero, `platform.command_receipt` 0, and both `send_control` flags
false. The 1,812 organizations, 3 campaigns and 3,141 send attempts are the **historical** load
replayed, not new work: no campaign was created, no quote written and nothing sent. The first
ten R1 records remain `pending` — no review decision has been executed against this database.

**Selecting it.** `supabase/scripts/cleanroom_db.sh api-login` writes
`~/data/origenlab-v2-local/api.cleanroom.env`. Sourcing it points `apps/api` at
`origenlab_clean`; `apps/dashboard` reaches the database only through the API, so that one
variable is the whole switch. **The default is unchanged** — `dev_db.sh api-login` still writes
`api.env` for `origenlab_dev`, and nothing sources either file by itself.

`ORIGENLAB_V2_DATABASE_URL` is now validated rather than trusted: `apps/api` refuses to start
on a value that is not a literal-loopback DSN, carries a query string or fragment, or names a
hosted provider — the same rule `migration/v2_import/target.py` applies.

**Why a second container rather than a second database.** `supabase db reset` drops every
non-system database in the CLI's cluster, not only the project database. That was measured, not
assumed: a reset on 2026-09-21 removed a persistent database placed there and its build template
outright. The CLI's cluster stays disposable; the development database lives beside it.
Procedure and rules: [`OPERATIONS.md`](OPERATIONS.md) §4.1.

#### Local evidence suite — measured 2026-09-21

Every gate below was run on this date, at `origin/main` @ `3c8dbf78` plus this branch.

| Check | Result |
|---|---|
| `supabase db reset --local` | PASS |
| `supabase test db --local` | **408 assertions, 11 files, all pass** |
| `verify_direct_logins.sh` | **51 proofs, 0 failed** |
| `replay_evidence.sh` | PASS — two resets reproduce identical schema, catalogue and migration list |
| `evidence_tool_failure_tests.sh` | **30 passed, 0 failed** |
| `audit_failure_tests.sh` | **80 passed, 0 failed** |
| `hosted_bootstrap_failure_tests.sh` | **91 passed, 0 failed** |
| `local_db_failure_tests.sh` | **22 passed, 0 failed** |
| audit unit tests | **308 passed** |
| `supabase db lint --local` | no schema errors |
| `supabase db advisors --local` | **zero SECURITY findings**; 118 INFO, all `unused_index` on an empty database |
| `verify_chain.sh` | 20 checks pass on `origenlab_dev` **and** on the CLI's `postgres` |

### 2.2 What does not exist yet in V2

- **`apps/worker`** — created for the Gmail capture (§2.7.49); PDF rendering, ChileCompra fetching and the send path are still not built.
- ~~**Any application code touching the seven schemas.**~~ **No longer true as of
  2026-09-21** — `apps/api` now carries a read-only `/v2/*` boundary over them (§2.7.2).
  There is still **no write path**: every durable V2 write is a migration tool, never an
  application.
- **The eight privileged send/quote functions** of [`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2.
- **Historical CRM data — measured 2026-09-24, read-only.** V2 holds **no historical
  quote and no V1 CRM row**. In `origenlab_clean`: `crm.quote` 0, `crm.person` 0,
  `crm.affiliation` 0, `crm.external_identifier` 0, `comms.message` 0, `crm.opportunity`
  1 (the single case opened on 2026-09-22); the 9,460 `crm.contact_point` rows carry **0**
  `organization_id` and **0** `person_id`, because they come from one migration manifest
  with no per-row contact→organization pairing. `evidence.source_record` holds 31
  `gmail_message` rows (keyed by Gmail API id, no RFC 822 Message-ID) and 4 migration
  manifests. **The V1 `commercial.*` dump the migration needs does not exist locally**:
  no `pg_dump` of `commercial.*` was found under `~/data`, the home directory or the
  Windows Downloads/Documents folders, `~/data/origenlab-prod-backups/` is empty, and
  the Wave 1A/1B bundles carry no `commercial` table. The only exact contact→organization
  key (`commercial.contact.organization_id`) and every V1 quote/opportunity identifier
  therefore remain unreachable. Historical quote material exists only outside V2 and
  unreconciled: 1,663 `document_master` quote documents in `emails.sqlite` (each with an
  attachment SHA-256, none with extracted text or a saved file), 4,598 files in the local
  `cotizaciones` folder (a 5-file sample matched no attachment hash) and the Drive
  workspaces. Nothing is imported or inferred from names, domains, filenames or subjects;
  a reconciliation design awaits the owner, and no importer exists.
- **A consumer for the imported rows.** §2.7's importer now maps the *full* Wave
  1A/Wave 1B safety baseline and all three historical campaigns; both schema
  blockers its first dry run measured were decided and closed on 2026-09-20
  ([`DATA.md`](DATA.md) §7.6.4), so no input class is blocked any more. What does
  not exist is anything that **reads** what it writes: promotion from
  `evidence.assertion` into `crm.*` is slice 2, the send predicate over
  `outbound.contact_control` is slice 5, and no dashboard surface shows either.
- **Any load into a real V2 database.** No hosted project has been adopted
  (§2.5), no staging database exists, and no row from either wave has reached
  either. The only writes performed are into a disposable local database that
  is destroyed after the run.
- **The `ol migrate` / `ol audit` CLI** documented in [`OPERATIONS.md`](OPERATIONS.md) §4
  — marked there as `EXAMPLE — NOT YET IMPLEMENTED`, and it is not implemented. The
  catalogue half of what its four `ol audit` subcommands would check is built, under a
  different name and as a single read-only tool: see §2.3. `ol migrate` is not.
- **An adopted hosted Supabase project.** The tooling to audit one (§2.3) and to bootstrap
  its roles (§2.4) now exists; neither has been run against a project. One project exists,
  is restored and `ACTIVE_HEALTHY`, carries 15 applied migrations whose provenance is now
  established (the 2026-09-08 `supabase db push`, §2.5), and is *not* adopted and *not*
  audited — §2.5.
  `supabase/scripts/lib/local_target.sh` still refuses a non-loopback host by design and
  every §4.1 script still resolves its target through it; the hosted path is a separate
  boundary in `supabase/audit/olaudit/target_hosted.py` that shares no code with it and
  refuses to start without an explicit authorisation flag, a reviewed target file at an
  approved path, a clean tracked working tree and an unlinked project.

### 2.3 Slice 0 audit — measured

| Item | Value |
|---|---|
| Entry point | `supabase/scripts/slice0_audit.sh` → `supabase/audit/run_audit.py` (Python standard library only) |
| Modes | `--mode local`; `--mode hosted --simulate`; `--mode hosted --authorize-hosted-connection`; the same plus `--authorize-supavisor-session-route`; `--verify-report` |
| Hosted routes | **2, explicitly selected, never fallen back to** — `direct` (`db.<ref>.supabase.co`:5432, login `origenlab_migrator`) and `supavisor-session` (`aws-<cluster>-<region>.pooler.supabase.com`:5432, login `origenlab_migrator.<ref>`). The pooler route needs both a declaration in the reviewed target file and its own authorisation flag. Supavisor transaction mode (6543) is refused by name: it cannot carry the audit's transaction-local role and timeouts. [`OPERATIONS.md`](OPERATIONS.md) §4.2 |
| SQL check files | 15, under `supabase/audit/sql/`, each statically proven to be a single read before any connection is opened |
| Engine checks | 9 — one API-key-type record, seven operator attestations, one derived Data API conclusion |
| Committed baseline | `supabase/audit/baselines/slice0.json`, captured from a clean local `supabase db reset` and reviewed in the change that added it |
| Unit tests | **308** — `python3 -m unittest discover -s supabase/audit/tests -t supabase/audit` (234 audit + 74 hosted bootstrap, §2.4); 56 of them were added with the Supavisor route and 19 with the psql-invocation boundary (`tests/test_psql_invocation.py`) |
| Failure-injection checks | **80** — `supabase/scripts/audit_failure_tests.sh`, including scenario M (the Supavisor route's trust model, end to end, with no database) and scenario N (a planted `~/.psqlrc` and a hostile libpq environment against the local database) |
| Local verdict | `LOCAL_PASS` — 13 required proofs satisfied, `a13` corroborated, `a14` recorded |
| Hosted runs performed | **one, completed 2026-09-21**, on the `supavisor-session` route against `origenlab-v2`. Every SQL proof passed — `s01`, `a01`–`a12`, `a13` corroborated, `a14` recorded. The verdict is `INCOMPLETE`, not `PASS`, because the eight attestation-backed items (`t01`–`t07`, `d01`) have no `supabase/.audit/attestation.json` and are therefore `NOT_RUN`. §2.5 |
| Write capability | **none.** No write mode, no baseline-writing mode, no redaction-disabling flag, and no mutation statement in either mode |

The one place the tooling itself attempts a write is `audit_failure_tests.sh`
scenario L, the negative test proving the audit's read-only transaction refuses a
mutation with SQLSTATE `25006`. Scenario N plants a write in a malicious `~/.psqlrc`
and proves the audit never executes it — its positive control first shows the same
file does create the marker when `-X` is absent. Both run only against the disposable
local database; L is rollback-only, and N drops its marker on both paths.

### 2.4 Hosted role bootstrap — measured

| Item | Value |
|---|---|
| Entry point | `supabase/scripts/hosted_role_bootstrap.sh` → `supabase/audit/run_bootstrap.py` (Python standard library only) |
| Modes | `--environment <classification> --dry-run`. **There is no apply mode and no connection in any mode** |
| Bootstrap file | `supabase/hosted_roles.sql`, 4 roles, 1 membership, 2 platform-role option revocations, **17** analysed statements |
| Static analyser | `supabase/audit/olaudit/bootstrap.py` — closed role set, no password, no platform-role grant, no unrecognised statement shape; one closed, privilege-removing exception (`PERMITTED_OPTION_REVOCATIONS`, matched exactly) |
| Approved environments | `staging` (Pro daily backups, seven-day retention, PITR declined). **`production`: decision recorded 2026-10-02** in [`OPERATIONS.md`](OPERATIONS.md) §4.3 (RPO 24 h, Pro daily backups required, PITR declined initially, independent `pg_dump` around every data-bearing cutover) — **the tool still refuses `--environment production`**: `DURABILITY["production"]` carries `decided=False` and failure check L proves the refusal; mirroring the record into the tool is a separate reviewed change, not yet made |
| Unit tests | **308** total — `python3 -m unittest discover -s supabase/audit/tests -t supabase/audit`; **74** of them cover the bootstrap |
| Failure-injection checks | **91** — `supabase/scripts/hosted_bootstrap_failure_tests.sh`, no database required |
| Execution rehearsal | **37 checks** — `supabase/scripts/hosted_bootstrap_rehearsal.sh`, local disposable database only, always-rolled-back transactions |
| Application contract | **atomic, documented and rehearsed** — `psql -X --no-psqlrc -v ON_ERROR_STOP=1 --single-transaction` over `verify-full` TLS in a clean child environment ([`OPERATIONS.md`](OPERATIONS.md) §4.3). **25 of the 37 rehearsal checks** extract that exact invocation from the document and run it against the local disposable database with failures injected, proving a failure after a successful role statement rolls that statement back and that the file's own assertions abort inside the same transaction. Still **no apply mode** in any tool |
| pgTAP | **22** assertions in `supabase/tests/100_hosted_role_bootstrap.sql` |
| Applications performed | **one, 2026-09-21** — `supabase/hosted_roles.sql` applied to `origenlab-v2` under the documented atomic contract, as the project's `postgres` login over the Supavisor session route. It converged `postgres -> origenlab_owner` from `set_option` true to false and left the `supabase_admin`-granted creator-ADMIN row intact, verified from the catalogue afterwards (§2.5). Before that it had never been applied anywhere outside the rolled-back local rehearsal; the *local* `supabase/roles.sql` had reached the project on 2026-09-08 and its shape is what this application converged |
| Passwords assigned | **by this file, zero — it still cannot express one.** The separate operator action was taken on 2026-09-21: `origenlab_migrator` now carries a SCRAM-SHA-256 verifier, computed on the operator's machine and sent as a pre-computed verifier so no plaintext crossed the wire or could reach a server log. The secret itself lives outside this repository. `origenlab_owner`, `origenlab_api` and `origenlab_worker` still have no verifier |
| Staging provisioning posture | **decided, not provisioned** — Pro plan, Micro compute, dedicated IPv4, `sa-east-1`, spend cap on, daily backups at seven-day retention, PITR declined, credential in the operator's password manager ([`OPERATIONS.md`](OPERATIONS.md) §4.3). No project has been created, adopted or billed from this repository — §2.5 |

**The convergence this file now carries.** `supabase/hosted_roles.sql` revokes the `SET` and
`INHERIT` options on `origenlab_owner` from `postgres`, and nothing else about a platform
identity ([`OPERATIONS.md`](OPERATIONS.md) §4.3). It exists because the hosted catalogue carries
`postgres -> origenlab_owner` with `SET` — the *local* file's shape, left by the 2026-09-08 push
(§2.5) — which the hosted file's own platform-boundary assertion would otherwise refuse. Measured
locally on 2026-09-20, against a clean `supabase db reset`:

| Proof | Result |
|---|---|
| `SET ROLE origenlab_owner` as `postgres`, before | succeeds |
| `SET ROLE origenlab_owner` as `postgres`, after | **`ERROR: permission denied to set role`** |
| `postgres` inherits `origenlab_owner`, after | no — `inherit_option` false, `pg_has_role(… 'USAGE')` false |
| `postgres` creator-`ADMIN` rows on all four roles | **retained**, all four, granted by `supabase_admin` |
| `origenlab_migrator` on `origenlab_owner` | unchanged — `SET` true, `INHERIT` false, `ADMIN` false |
| `origenlab_api` / `origenlab_worker` owner membership | none, before and after |
| any other non-OrigenLab role with effective `SET`/`INHERIT` on an OrigenLab role | none |
| non-OrigenLab role-attribute digest | **identical** before and after |
| second application | same catalogue and same effective state |
| rollback | restores the local `postgres` `SET` membership exactly |

**PostgreSQL 17 leaves a vestigial row, and audits must expect it.** After the revocations the
`postgres`-granted membership row survives with every option false —
`origenlab_owner | postgres | postgres | admin=f inherit=f set=f`. It confers no capability. A
check asserting that *no* `pg_auth_members` row exists would fail against a correctly converged
database. Tests and future audits judge the `SET`/`INHERIT`/`ADMIN` option values and the
behaviour, never the presence of a row. `pg_has_role(…, 'MEMBER')` is specifically **not**
evidence here: it reads `true` for `postgres` both before and after, because the retained
creator-ADMIN row satisfies it.

### 2.5 The `origenlab-v2` hosted project — audited and reconciled

One hosted Supabase project, **`origenlab-v2`**, exists. Its state, stated precisely because
every distinction below has been got wrong at least once — and **corrected on 2026-09-20**:
earlier revisions of this section asserted that no database connection, SQL, DDL or migration
operation had ever been performed against it. **Those assertions are contradicted by the hosted
catalogue and by the operator's own local records, and they are withdrawn.**

#### What happened, reconstructed

Reconstructed from the operator's shell history, the Supabase CLI trace log and a retained
Claude Code transcript — all local, all outside this repository:

| When (UTC) | What | Evidence |
|---|---|---|
| 2026-09-02 13:10 | `supabase login --no-browser` | shell history; `~/.supabase/access-token` |
| 2026-09-07 13:10:38 | `supabase link --project-ref <ref>` in the then-existing worktree `~/dev/origenlab-v2-hosted-slice0`, branch `feat/origenlab-v2-hosted-slice0` | Claude Code transcript, with the resulting `supabase/.temp/project-ref` read back and matched |
| 2026-09-08 12:55:11 | `supabase db push --linked --include-roles --skip-vault --dry-run`, guarded to require the string `Would create custom roles supabase/roles.sql` in its output | shell history |
| 2026-09-08 12:59:46 | **`supabase db push --linked --include-roles --skip-vault`** — the same command without `--dry-run`, run under `set -e` after the dry-run guard passed, against `origin/main` `80b446aa` (15 migrations) | shell history |
| 2026-09-08 13:31–13:34 | the worktree, its branch and therefore `supabase/.temp/project-ref` were removed | shell history |
| 2026-09-10 | this document was written asserting no hosted connection had ever been made | git history |

**`--include-roles` is what carried `supabase/roles.sql`** — the *local* bootstrap — to the
hosted project. That single fact accounts for every measured deviation: the four roles present,
no password verifier on any of them (the file assigns none), and `postgres` holding a SET-only
membership in `origenlab_owner` (the local file's one platform grant, the one the hosted file
omits and now converges away, §2.4). The same push carried the 15 migrations of that commit,
which is consistent with the 15 since observed on the project and with the three 2026-09-08
`outbound` migrations being absent from it.

**Nothing above claims `supabase/hosted_roles.sql` was applied on 2026-09-08.** It was not —
the catalogue shape that day was the *local* file's. It was first applied on **2026-09-21**,
and what it did to that shape is recorded in the measured state below.

#### What is fact, and what is inference

An authorised hosted push command — `supabase db push --linked --include-roles --skip-vault` —
was **invoked** on 2026-09-08. **That invocation is fact**, recorded in shell history, run under
`set -e` after a dry-run guard that would have aborted on a missing `Would create custom roles`
line.

The current hosted catalogue and migration state are **consistent with that push having
completed successfully**: the four roles are present in exactly the shape `supabase/roles.sql`
creates, none carries a password verifier, and the 15 migrations of `80b446aa` are the 15
observed on the project. **Successful completion nevertheless remains a strong inference, not a
record, because the push's own stdout was not retained.** No remediation or rollback follows it
in any log. This document does not upgrade that inference to proof, and **no later statement
here may rely on it as if it were one.**

The 2026-09-10 "nothing has ever been connected" claim was not a fabrication: by then the linked
worktree, the branch and the project-ref file had all been deleted, so the surviving tree
genuinely contained no trace of the push. **The deletion of that evidence is what made the wrong
claim writable, and that is the process failure to fix** — not the push, which was explicitly
authorised and guarded at the time.

#### The project's measured state

**Read on 2026-09-21 by the Slice 0 audit itself**, over the reviewed Supavisor
session-mode route as `origenlab_migrator`, TLS `verify-full` against the validated
Supabase CA, inside one `begin read only` transaction the server confirmed. The bullets
below are measurements, not inferences.

- it is **`ACTIVE_HEALTHY`**, PostgreSQL **17.6**, `sa-east-1`, and it is the only project
  in the organisation carrying the name `origenlab-v2`. Name, organisation, region and
  status were re-resolved from the control plane immediately before each connection;
- **the migration ledger is reconciled: 19 of 19.** It held 15 — this repository's first
  fifteen, in order, with **no unknown version and no drift**. The four absent ones
  (`20260908120000`, `20260908120100`, `20260908120200`, `20260920190000`) were applied on
  2026-09-21 in canonical order and recorded. Each ran as `origenlab_migrator` under
  `--single-transaction` with command-line `ON_ERROR_STOP=1`; the ledger row was written
  separately as `postgres`, because `supabase_migrations` is owned by `postgres` and the
  migrator holds no privilege on it;
- **the role graph matches the intended matrix**, verified from the catalogue after the
  bootstrap: four roles, none `SUPERUSER`, `BYPASSRLS`, `CREATEROLE`, `CREATEDB` or
  `REPLICATION`; `origenlab_owner` `NOLOGIN`; `origenlab_migrator` `NOINHERIT` holding
  `origenlab_owner` `SET`-only; the two runtime roles holding no membership;
- **`postgres` can no longer `SET ROLE` to `origenlab_owner`.** Its `postgres`-granted row
  survives with every option false — the vestigial shape `REVOKE ... OPTION FOR` leaves on
  PostgreSQL 17 — and its `supabase_admin`-granted creator-ADMIN row is untouched, which
  §6.4 of [`ARCHITECTURE.md`](ARCHITECTURE.md) tolerates by design;
- **it holds no business data, and that is now proven rather than assumed.** Every table in
  the seven schemas was counted: **one row in total**, the `outbound.send_control`
  kill-switch singleton seeded by `20260905230814`. No contact, organisation, opportunity,
  campaign, quote or message exists there. Both send flags are `false`;
- **the schema matched this repository's head on the day of the audit**: 33 tables, 127 RLS
  policies, **no table without RLS**, **zero `SECURITY DEFINER` functions**, and every foreign
  key index-covered. Head has since moved to 36 tables and 139 policies (§2.1); the hosted
  project is frozen (§2.8) and carries none of it, which is a deliberate gap, not drift;
- **no OrigenLab private schema is exposed through the Data API.** PostgREST serves
  `public` and `graphql_public` only; `crm`, `comms`, `outbound`, `evidence`, `catalog`,
  `procurement` and `platform` are all absent from its exposed set;
- it is on the **Free plan**, with **no backup-retention entitlement** and no backup taken by
  the platform. The Pro-plan staging posture in §2.4 remains a *decision about a project that
  would be provisioned*, not a description of this one. A private logical `pg_dump` of the
  seven schemas plus the ledger was taken before the migration writes and is held outside Git
  under the operator's `~/data/origenlab-v2-migration/backups/`, mode `600`.

**What the audit does not yet prove.** The verdict is `INCOMPLETE`, not `PASS`. Every SQL
check passed, but eight items — `t01`–`t07` and `d01` — are operator attestations with no
`supabase/.audit/attestation.json` behind them, so they are `NOT_RUN` rather than satisfied.
Three of them are known today to be *unsatisfiable on this project as it stands*: there is no
backup entitlement (`t04`), so no restore drill can be evidenced (`t05`, `t06`), and the Data
API is running rather than off (`t01`, `d01`). **The Slice 0 hosted gate is therefore still
closed**, but what closes it is now a short list of plan-and-configuration decisions rather
than an inability to reach or read the project.

**The exposed JWT secret remains an open production blocker** and has deliberately **not**
been rotated. Rotation waits on evidence about Auth consumers that this audit does not
collect.

Its **project reference, host name, organisation identifier and credentials are deliberately
not recorded here or anywhere else in tracked content.**
`scripts/security/check-public-repo-hygiene.sh` fails if a project reference enters tracked
content in either of the two shapes it takes — a `db.<ref>.supabase.co` host name or a
Supavisor `<role>.<ref>` login — and the Slice 0 audit takes a hosted target only from a
git-ignored file at one approved path. The human-facing project name above is the only
hosted identifier this repository records.

### 2.6 V1 migration evidence — measured

The four V1 → V2 migration-evidence artifacts exist, are private and are
**outside Git**: they live under the operator's `~/data/origenlab-v2-migration/`
root, which no repository path references and no `.gitignore` exemption covers.

| Artifact | State |
|---|---|
| Wave 1A safety bundle + `.sha256` | present since 2026-09-05; **content unchanged**, permissions hardened 2026-09-20 (below); hashes still those [`DATA.md`](DATA.md) §7 records |
| Wave 1A RFC 2047 addendum + `.sha256` | derived from the Wave 1A bundle; 3 addresses ([`DATA.md`](DATA.md) §7.4) |
| Wave 1B September bundle + `.sha256` | extracted once, 2026-09-20 ([`DATA.md`](DATA.md) §7.5) |
| Cross-wave reconciliation report `v2` + `.sha256` | measured 2026-09-20, SHA-256 `ed23ff6f…` ([`DATA.md`](DATA.md) §7.5.2–§7.5.4) |
| Cross-wave reconciliation report `v1` + `.sha256` | superseded, **retained unchanged**, SHA-256 `45c96103…` |

- **The Wave 1B extractor has now run once, successfully.**
  `apps/email-pipeline/scripts/migration/extract_wave1b_v1_safety_bundle.py`
  completed a single deferred read transaction against the live V1 SQLite
  database with no warnings, and every field that was `pending` in
  [`DATA.md`](DATA.md) §7.5 is now measured.
- **Nothing was loaded into V2 by the extraction itself.** That remained true when
  this section was written. It is no longer true of the *local* database: the
  §2.7 importer and the §2.7.7 staging pass have since loaded the historical
  baseline and 20 pending Gmail records into `origenlab_dev` (§2.1). No hosted
  project holds any of it.
- **No hosted project was contacted.** The extraction and the reconciliation
  opened no Supabase, PostgreSQL, Render or Cloudflare endpoint, and made
  **zero** Gmail network calls — the Sent evidence is already-ingested SQLite
  rows read through the live outbound gate's own functions.
- **No sender or campaign state changed.** Both September campaigns, their
  recipients and their send attempts were read, never written; the 279
  remaining candidates stay paused; the V1 ingest cron was observed, never
  paused, stopped or modified.
- **Both V2 send flags remain `false`** — the `outbound.send_control` single row
  is unchanged (§2.1).
- **The measured cross-wave safety baseline is available but not imported.**
  9,460 deduplicated prior-contact addresses and 1,032 deduplicated address
  suppressions, measured rather than summed ([`DATA.md`](DATA.md) §7.5.2,
  §7.5.4). Of the 2,000 September accepted campaign recipients, **1,158** were
  already in the pre-September Wave 1A baseline (§7.5.3). It is migration
  safety evidence, not CRM identity truth, and nothing reads it yet.
- **No outbound policy changed.** No eligibility rule, gate, suppression row or
  campaign state was written by any of this; the reconciliation is read-only
  and the hardening changes only filesystem modes.
- **Wave 1A permissions are hardened.** On 2026-09-20 the Wave 1A bundle, its
  `.tar.gz` and that archive's `.sha256` were tightened from `0755`/`0644` to
  `0700`/`0600` by
  `apps/email-pipeline/scripts/migration/harden_wave1a_artifact_permissions.py`
  — 5 directories and 29 files. Every checksum was verified before and after and
  **no digest moved**; `mtime` is unchanged on every path; the enclosing
  migration root and the Wave 1B bundle were not touched. Zero paths inside the
  boundary remain readable or writable by group or other. **Wave 1A's content,
  hashes and §7 counts are unchanged — only the mode moved.**

### 2.7 Feature and fix history

Compact index of the 153 build increments. Each entry was a measured or merged change; full
prose is in git history (see commit referenced by the PR). Entries are in build order; §2.7.5
(local V2 reconciled picture) was committed out of order between §2.7.25 and §2.7.26.

| # | Date | PR(s) | Summary |
|---|------|-------|---------|
| §2.7.1 | 2026-09-21 |  | Evidence → CRM promotion — measured 2026-09-21 |
| §2.7.2 | 2026-09-21 |  | V2 durable read boundary — measured 2026-09-21 |
| §2.7.3 | 2026-09-21 |  | Four CRM cards on the V2 durable core — measured 2026-09-21 |
| §2.7.4 | 2026-09-21 |  | Marketing linkage — measured 2026-09-21 |
| §2.7.6 | 2026-09-21 |  | Contacts + Organizations slice — measured 2026-09-21 |
| §2.7.7 | 2026-09-21 |  | First real Gmail staging + the review workspace — measured 2026-09-21 |
| §2.7.8 | 2026-09-22 |  | The V2 human-review command boundary — built 2026-09-22, unwired |
| §2.7.9 | 2026-09-22 |  | Fixture residue in `origenlab_dev`, and the clean room — 2026-09-22 |
| §2.7.10 | 2026-09-22 |  | Mailbox source inventory and intake classification — 2026-09-22 |
| §2.7.11 | 2026-09-22 |  | R1 — the archived replay batch, applied to the clean room 2026-09-22 |
| §2.7.12 | 2026-09-22 |  | Operator identity is configuration — closed 2026-09-22 |
| §2.7.13 | 2026-09-22 |  | Attributing a sender, atomically — built 2026-09-22, unwired |
| §2.7.14 | 2026-09-22 |  | What an address is to an institution — built 2026-09-22, unwired |
| §2.7.15 | 2026-09-22 |  | Identity, commercial role and person — separated in the surface |
| §2.7.16 | 2026-09-22 |  | The commercial case — schema built 2026-09-22, no command, empty |
| §2.7.17 | 2026-09-22 |  | The commercial case, as six commands — built 2026-09-22, unwired |
| §2.7.18 | 2026-09-22 |  | The commercial case, as a screen — built 2026-09-22, read-only |
| §2.7.19 | 2026-09-23 |  | Contacto 360 e Institución 360 — the operator surface |
| §2.7.20 | 2026-09-23 |  | An institution's own cases, and the V1 chrome above a V2 screen — 2026-09-23 |
| §2.7.21 | 2026-09-24 |  | Durable connections on the organization list and the two cards — 2026-09-24 |
| §2.7.22 | 2026-09-24 |  | List filters, and the dashboard reads the connections — 2026-09-24 |
| §2.7.23 | 2026-09-25 |  | Historical quotations — schema, commands, import planner and cockpit reads |
| §2.7.24 | 2026-09-26 |  | Case-first Drive archive for quotations — built 2026-09-26; step A executed, step B not run |
| §2.7.25 | 2026-09-26 |  | CRM workspace — read-only dashboard over the imported CRM, built 2026-09-26 |
| §2.7.26 | 2026-09-26 |  | V1 read routes closed in the dashboard proxy |
| §2.7.5 | 2026-09-21 |  | Local V2 — the reconciled picture |
| §2.7.27 | 2026-09-26 |  | One dashboard: the CRM is the shell |
| §2.7.28 | 2026-09-27 |  | Remote V2 database connection |
| §2.7.29 | 2026-09-27 |  | Campaign drafts and equipment-interest audiences |
| §2.7.30 | 2026-09-27 |  | Audience freeze |
| §2.7.31 | 2026-09-27 |  | W12 recontact review |
| §2.7.32 | 2026-09-27 |  | Supplier directory and equipment interests on CRM cards |
| §2.7.33 | 2026-09-27 |  | Campaign history and planning calendar |
| §2.7.34 | 2026-09-27 | #598 | W10 «BAJA» unsubscribe |
| §2.7.35 | 2026-09-27 | #603 | Campaign safety blocks (pause) and the September hold |
| §2.7.36 | 2026-09-28 | #601 | Marketing Studio: campaign history from PostgreSQL |
| §2.7.37 | 2026-09-28 |  | Dashboard proxy lists the rest of the CRM workspace reads |
| §2.7.38 | 2026-09-28 | #614 | Shared Workspace login with operator profiles |
| §2.7.39 | 2026-09-30 | #614 | Sign-in migrations applied to `origenlab_clean`; the clean-room verification contract repaired |
| §2.7.40 | 2026-09-30 | #619 | Recovered campaign HTML and CRM authoring |
| §2.7.41 | 2026-10-03 |  | Session-only browser boundary |
| §2.7.42 | 2026-10-04 |  | V2 connection pool and batched reads |
| §2.7.43 | 2026-10-04 |  | Resumen as a follow-up list, exchange rates, page memory |
| §2.7.44 | 2026-10-04 |  | Cyber as a prepared campaign in the Marketing page, via the V1 lane |
| §2.7.45 | 2026-10-04 |  | The per-request timing line reaches the server log |
| §2.7.46 | 2026-10-04 |  | CRM authoring gates closed, and the switch made visible to the dashboard |
| §2.7.47 | 2026-10-04 | #631 #632 | «Enviar prueba»: an admin-only test send of a campaign's email |
| §2.7.48 | 2026-10-04 | #633 | CRM part A — confirm an institution, web suggestions, suggested people |
| §2.7.49 | 2026-10-04 |  | Gmail capture into hosted V2 (Phase 4a) |
| §2.7.50 | 2026-10-05 |  | Quote-number box counts the numbers Gmail already shows |
| §2.7.51 | 2026-10-05 |  | Catalog 1a backend: products, supplier costs, exchange rates, cost parameters, quote-document lines |
| §2.7.52 | 2026-10-05 |  | Email → cases rules (4b-auto): preview, apply, undo |
| §2.7.53 | 2026-10-05 | #642 #643 | §2.7.51 and §2.7.52 merged, applied to hosted and deployed; event-type union fix |
| §2.7.54 | 2026-10-05 |  | Case writes part A — the case drawer's actions work |
| §2.7.55 | 2026-10-06 | #646 | Case writes part B — «Elegir revisión vigente», «Registrar cotización», «Nueva revisión» |
| §2.7.56 | 2026-10-06 | #645 #647 #646 | §2.7.54 and §2.7.55 merged, applied to hosted, proxy deployed |
| §2.7.57 | 2026-10-06 |  | Oportunidades «Tablero» readable with one crowded column |
| §2.7.58 | 2026-10-06 |  | «Último contacto»: Resumen ages a case from the last email OrigenLab sent |
| §2.7.59 | 2026-10-06 |  | Email → cases R1/R2 run automatically, with a stop switch |
| §2.7.60 | 2026-10-06 |  | Recorded quote PDFs filed in the case's Drive folder |
| §2.7.61 | 2026-10-06 | #623 | Catalog loaded into the hosted database |
| §2.7.62 | 2026-10-06 |  | CRM redesign phase 1: buttons, dialogs and confirmations |
| §2.7.63 | 2026-10-06 |  | CRM redesign phase 2: six states, «Perdida» with a reason, «En pausa hasta…», draggable Tablero |
| §2.7.64 | 2026-10-06 |  | Tablero cards redesigned and «Decidir casos» for the historical stage |
| §2.7.65 | 2026-10-06 |  | CRM redesign phase 3: «Hoy» replaces Resumen |
| §2.7.66 | 2026-10-06 |  | Design review fixes on Hoy and the Tablero |
| §2.7.67 | 2026-10-06 |  | «Hoy» as one traffic-light follow-up list, the product on every card, a foldable menu |
| §2.7.68 | 2026-10-06 |  | «Seguimientos» simpler, and a written follow-up clears itself |
| §2.7.69 | 2026-10-06 |  | Mail triage on a Procrastinate queue |
| §2.7.70 | 2026-10-06 |  | «Te toca responder» stops at a decided reply, and «No requiere respuesta» |
| §2.7.71 | 2026-10-07 | #668 | Mail triage and Drive filing switched on in hosted |
| §2.7.72 | 2026-10-07 |  | «Catálogo»: the catalog made visible, read-only |
| §2.7.73 | 2026-10-07 |  | «En pausa» replaces a follow-up already due |
| §2.7.74 |  | #619 #620 | Catalog: every supplier in the filter, products by kind, Soviquim — built, not deployed; three… |
| §2.7.75 | 2026-10-08 | #679 | Production release engineering audit |
| §2.7.76 | 2026-10-08 | #679 | Built: consolidated periodic mail capture, not activated (2026-10-08) |
| §2.7.77 | 2026-10-08 | #679 #683 | CRM request context and false-positive correction |
| §2.7.78 | 2026-10-09 |  | Dashboard navigation: Historial and admin-only Datos |
| §2.7.79 | 2026-10-09 | #699 | Hoy, board and case panel cleanup |
| §2.7.80 | 2026-10-09 |  | Fresh quotes are not «sin decidir»; won-case replies in «Te toca responder» |
| §2.7.81 | 2026-10-10 |  | «Atendido» on a won-case reply |
| §2.7.82 | 2026-10-10 |  | «Archivo Drive» counts what the Drive cron filed |
| §2.7.83 | 2026-10-10 |  | R2 reads the quote number from the subject when no PDF names one |
| §2.7.84 | 2026-10-10 |  | Triage: «REMOVER» with a signature and an absence notice under «RE:» are noise, not quote… |
| §2.7.85 | 2026-10-10 |  | «¿Marcar ganada?» and «Abrir caso» from what the triage read |
| §2.7.86 | 2026-10-10 |  | «Posibles BAJAS según la lectura del correo»: a triage `unsubscribe` reading confirmed into the W10… |
| §2.7.87 | 2026-10-10 |  | One clock per case on the Tablero |
| §2.7.88 | 2026-10-10 |  | «¿Reabrir?» on Hoy: a reply on a closed case reopens it as a new case… |
| §2.7.89 | 2026-10-10 |  | «Vencidas» on Hoy: overdue tasks escalate |
| §2.7.90 | 2026-10-10 |  | Simplification: a closed case reopens as the same case; Hoy is three lists |
| §2.7.91 | 2026-10-11 | #704 #707 #711 #709 | Triage v2: the reply's own words, wider lost signals, newest reading wins |
| §2.7.93 | 2026-10-11 |  | Case panel: money, equipment lines, Gmail and Drive first; «Solicitada» absorbs «En estudio» |
| §2.7.94 | 2026-10-11 |  | Quote reader: registered quote PDFs read into lines |
| §2.7.95 | 2026-10-12 |  | Case amount = the last quotation sent, not a sum; send times shown |
| §2.7.96 | 2026-10-12 |  | Triage v3: the model reads what v2 read with it off |
| §2.7.97 | 2026-10-12 |  | A case's purchase order: attached, stored privately, shown with its quotation |
| §2.7.98 | 2026-10-12 |  | A closed case shows no notes |
| §2.7.99 | 2026-10-12 |  | The history cases never loaded: a loader |
| §2.7.100 | 2026-10-12 |  | An email domain given to the organization it belongs to |
| §2.7.101 | 2026-10-12 |  | One way to run the tests, locally and in CI |
| §2.7.102 | 2026-10-12 | #731 | Production is checked every 30 minutes |
| §2.7.103 | 2026-10-13 |  | Production backup restored from a real encrypted artifact; daily backups on |
| §2.7.104 | 2026-10-13 | #733 #734 #735 #736 | Automatic releases on; the mail worker captures, the legacy cron is suspended |
| §2.7.105 | 2026-10-13 |  | Mail is checked every minute; Drive and quotes follow new mail |
| §2.7.106 | 2026-10-13 |  | R9: a quote sent to a free-mail customer reaches the case its PDF names |
| §2.7.107 | 2026-10-13 |  | «Operaciones» replaces «Datos», visible to everyone |
| §2.7.108 | 2026-10-13 |  | «Cotizaciones enviadas sin caso» with «Abrir caso» |
| §2.7.109 | 2026-10-13 |  | «Completar con la cotización del correo»: one click from a sent quote to «Enviada» |
| §2.7.110 | 2026-10-13 |  | Catálogo v2: instant search, one section per supplier, Drive data sheets and price lists |
| §2.7.111 | 2026-10-14 |  | One maker per brand: duplicate Hielscher and Ortoalresa merged |
| §2.7.112 | 2026-10-14 |  | One email, one row; quotes sent on a case's thread but never recorded |
| §2.7.113 | 2026-10-14 |  | «Ventas y cotizaciones» on the product panel |
| §2.7.114 | 2026-10-14 |  | Forwarded requests become Solicitudes |
| §2.7.115 | 2026-10-14 |  | Gross margin per quote, and a faster product panel |
| §2.7.116 | 2026-10-14 |  | Exchange rates from the Banco Central |
| §2.7.117 | 2026-10-14 |  | Hoy acts by itself: forwards, body quote numbers, AI-read requests, «Quitar» |
| §2.7.118 | 2026-10-15 |  | The context matcher places emails on their case, and learns from «Quitar» |
| §2.7.119 | 2026-10-15 |  | Case memory: running machine summary per case, institution and person |
| §2.7.120 | 2026-10-15 |  | A sent quote lands on the client's open request (R13); R3/R4 automatic |
| §2.7.121 | 2026-10-15 |  | The Cotizador's quote authoring (PR A: engine, commands, reads) |
| §2.7.122 | 2026-10-16 | #761 | The Cotizador screen (PR B: the dashboard editor) |
| §2.7.123 | 2026-10-16 |  | The Cotizador module: many quotes open as tabs, quotes started from a client, company preferences |
| §2.7.124 | 2026-10-16 |  | Two quotes sent together on a case's thread are both recorded (R1) |
| §2.7.125 | 2026-10-16 |  | Catálogo: filter «Con ficha» |
| §2.7.126 | 2026-10-16 |  | Model aliases: a model as printed reaches its catalog product |
| §2.7.127 | 2026-10-16 |  | Products created from sent quotes (importer) |
| §2.7.128 | 2026-10-16 |  | Quote reader v2: the old template footer no longer blocks reading; a neto with its… |
| §2.7.129 | 2026-10-16 | #770 | A client connected to an institution while quoting; «Asignar institución» creates one |
| §2.7.130 | 2026-10-17 |  | Cotizador «Vista previa PDF»: the client's quote, printed by the browser |
| §2.7.131 | 2026-10-17 |  | Selective production release: skip what a change does not need |
| §2.7.132 | 2026-10-17 |  | Cotizador «Rentabilidad» and «Economía de la cotización» |
| §2.7.133 | 2026-10-18 |  | Web quote form |
| §2.7.136 | 2026-10-19 |  | A web-form case reads the visitor as its client |
| §2.7.134 | 2026-10-19 |  | Cotizador «Datos del cliente» on the draft |
| §2.7.135 | 2026-10-19 |  | The system makes the obvious decisions itself (R14, R15, answered mail) |
| §2.7.137 | 2026-10-19 |  | Cotizador: what each line costs against what it sells for; expense types chosen, not guessed;… |
| §2.7.138 | 2026-10-19 |  | Cotizador «Gastos ingresados» on an approved or sent quote |
| §2.7.139 | 2026-10-19 | #802 | Licitaciones fase 1: tender cases, bidder data, anexos, bid preparation, model spend |
| §2.7.140 | 2026-10-20 | #803 | Licitaciones «En Mercado Público»: open tenders that fit the catalog |
| §2.7.141 | 2026-10-20 |  | Licitaciones: the whole catalog in the match, a notice's own page, the dashboard's look |
| §2.7.142 | 2026-10-20 |  | Licitaciones: every new tender read, its match stored, «Nos interesa» / «No es nuestro» |
| §2.7.143 | 2026-10-21 |  | A Cotizador quote emailed by contacto@ is «sent» and its case «Enviada» (R16) |
| §2.7.144 | 2026-10-21 |  | A Cotizador quote's sent PDF is filed in its case's Drive folder |
| §2.7.145 | 2026-10-21 |  | A bounce someone deleted still blocks its address |
| §2.7.146 | 2026-10-21 |  | The mail worker imports only what each process runs |
| §2.7.147 | 2026-10-21 |  | Several equipment lines share a quote's import expenses; no IVA per line; the PDF's later… |
| §2.7.148 | 2026-10-21 |  | «Sin IVA» on a Cotizador quote |
| §2.7.149 | 2026-10-22 | #816 #818 | A particular's quote reads «Persona natural» on the Cotizador |
| §2.7.150 | 2026-10-22 | #816 | A persona natural is their own client record |
| §2.7.151 | 2026-10-22 |  | Request cases opened by R7/R11 add the sender as a participant; «+ Nueva» no longer… |
| §2.7.152 | 2026-10-22 | #826 | `20261022100000`: indexes on `comms.message(provider_thread_id)`, Gmail thread id, catalog `model_key` (pg_trgm); worker case cards capped at 300 |
| §2.7.153 | 2026-10-23 | — | `20261023100000`: «Prospectos» fase 1 — `prospect_finding` evidence kind, `crm.prospect_review` (#65, append-only), worker `prospects-load`, `/v2/prospect-findings` reads with read-time dedupe, `review-prospect` / `contact-prospect`, dashboard module; nothing loaded yet |

**Key milestones (highlighted in the table above):**

| When | What |
|------|------|
| 2026-09-21 | §2.7.1–§2.7.7: Evidence promotion, V2 read boundary, CRM cards, marketing, contacts/orgs, Gmail staging |
| 2026-09-22 | §2.7.8–§2.7.18: Command boundary, clean room, mailbox inventory, commercial case schema and commands |
| 2026-09-23–26 | §2.7.19–§2.7.27: CRM surface (360 cards, connections, workspace), Drive archive, dashboard shell |
| 2026-09-27–28 | §2.7.28–§2.7.34: Campaigns, audience freeze, recontact review, supplier directory, marketing studio |
| 2026-09-29 — 2026-10-03 | §2.7.35–§2.7.42: Profile selector, hosted load, connection pool, speed improvements |
| 2026-10-04–06 | §2.7.43–§2.7.74: Worker (Phase 4a), catalog 1a, mail-rule cases, case writes, catalog load |
| 2026-10-07–08 | §2.7.71–§2.7.77: Mail triage live, catalog visible, release engineering audit |
| 2026-10-09–12 | §2.7.78–§2.7.100: Hoy redesign, «Atendido», triage v2/v3, quote reader, OC attachment, test infra |
| 2026-10-13 | §2.7.101–§2.7.108: Smoke cron, daily backups, automatic releases, worker captures mail every minute |
| 2026-10-14–18 | §2.7.109–§2.7.133: «Operaciones», Cotizador (full module), catalog v2, Forwarded requests, margins |
| 2026-10-19–22 | §2.7.134–§2.7.151: Web quote form, Licitaciones, Cotizador expenses/sin IVA/persona natural |

### 2.8 Hosted phase — frozen 2026-09-21 (history)

**Superseded.** The hosted phase reopened in October 2026 and `origenlab-v2` is production —
see §0. The table below is the state as measured at the freeze.

**State then: frozen.** The operator closed the hosted phase on 2026-09-21 and moved all V2 work
to local PostgreSQL 17. The decision, its scope and the conditions for reopening are owned by
[`OPERATIONS.md`](OPERATIONS.md) §1.1 — this section only reports what is measured.

| Item | Measured state |
|---|---|
| `origenlab-v2` | exists, `ACTIVE_HEALTHY`, **frozen**; neither adopted nor decommissioned |
| Hosted connections since the freeze | **zero** |
| Hosted migrations applied since the freeze | **zero**; the ledger stands at 19 of 19 (§2.5) |
| Hosted rows of business data | **one** — the `outbound.send_control` singleton, unchanged (§2.5) |
| Data API disablement | **NOT TAKEN — open gate item** (`t01`, `d01`) |
| Legacy JWT key disablement | **NOT TAKEN — open gate item**; the exposed JWT secret is still unrotated |
| Slice 0 hosted gate | **still closed**, verdict `INCOMPLETE`, unchanged by the freeze |
| Backup entitlement | still absent — Free plan, no platform backup (§2.5) |
| Adoption for production data | **not taken, 2026-10-02.** No committed file names the project, no application configuration points at it, no Pro upgrade has been purchased. The production durability posture it would need is now recorded ([`OPERATIONS.md`](OPERATIONS.md) §4.3); the bootstrap tool still refuses `production` (§2.4) |

**The two manual security controls were offered to the operator and declined for now.** They
are recorded here as open, and they are not attested, not satisfied and not deferred out of
the gate count. Both are Supabase Dashboard actions that this repository cannot take and,
under the freeze, cannot verify.

**The freeze weakens no gate.** Every item that was open against the hosted project before
2026-09-21 is open after it, on the same terms.

**Where V2 work happens now.** Local PostgreSQL 17 via the supported Supabase CLI stack,
[`OPERATIONS.md`](OPERATIONS.md) §4.1 and §1.1. The local foundation measured in §2.1 is the
working target; the migration chain, roles, grants, RLS and pgTAP suite are applied and
proven there.

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
| Render | Web service **`origenlab`** (the real name; `render.yaml` calls it `origenlab-api`), **Python runtime with root dir `apps/api`, not the Docker runtime `render.yaml` declares**, Starter plan, Oregon, 1 GB disk at `/var/data`, health-check path empty, **auto-deploys every `main` push** (live on `cb60c20d` since 2026-10-02 20:58Z). `origenlab-dashboard` (static, auto-deploys `main`). Managed Postgres `origenlab-dashboard-prod` (**PostgreSQL 18**, basic-256mb, Oregon). `api.origenlab.cl` is a **verified custom domain** of the web service. `hosted-smoke` cron every 30 min. Auto-deploy **off** since 2026-10-13; releases are automated (§2.7.102/104). Read 2026-10-02 over the Render API; release engineering updated 2026-10-13 |
| FastAPI Cloud | **a second, stale deployment of `apps/api` exists, and nothing reaches it.** App `origenlab` (us-east-1, directory `apps/api`) at `https://origenlab.fastapicloud.dev`, deployed by the `fastapi-cloud[bot]` GitHub App into the GitHub environment `Production – origenlab`. Read 2026-10-02 over its CLI: **240 deployments**, the last successful one `c4cc0488` (2026-09-30); **#619 (`cb60c20d`, 15 files under `apps/api`) and #620 produced no deployment, so its auto-deploy has stopped and it runs code from 2026-09-30.** **Zero custom domains** — `api.origenlab.cl` is not on it (§3.3). Seven environment variables: `ORIGENLAB_API_ALLOWED_HOSTS` (= its own hostname), `ORIGENLAB_API_AUTH_TOKEN`, `ORIGENLAB_API_BACKEND`, `ORIGENLAB_API_CORS_ORIGINS`, `ORIGENLAB_API_DISABLE_DOCS`, `ORIGENLAB_ENV`, `ORIGENLAB_POSTGRES_URL` — the last is marked secret and its target could not be read; **no write variables, no V2 variables**. Its `/health` answers 200 with `postgres_configured: true`, so it holds a live database credential. No connected integration resources; plan not exposed by the CLI (log retention is capped at one day). One day of logs: a cold start, this audit's probes and one scanner, **no real traffic**. It has no Cloudflare Access in front of it. The desired architecture is **one production API origin, Render**; disconnecting FastAPI Cloud (delete the app, uninstall the GitHub App) is the owner's call and **had not been taken** when this was written — nothing was deleted or disconnected |
| Cloudflare | `apps/dashboard-proxy` Worker at `dashboard.origenlab.cl/api*`. DNS for `origenlab.cl` is on Cloudflare (Full setup, 22 records, no Tunnels on the account). **`api.origenlab.cl` is a proxied CNAME to `origenlab.onrender.com`** and **`dashboard.origenlab.cl` a proxied CNAME to `origenlab-dashboard.onrender.com`** (read from the DNS table by the owner, 2026-10-02); both sit behind Cloudflare Access (an unauthenticated `GET /health` on `api.origenlab.cl` answers `302` to the Access login). The apex and `www` are proxied A/CNAME records to the cPanel host; the cPanel service records (`cpanel`, `mail`, `webmail`, `whm`, `webdisk`, …) are **DNS-only and expose that host's IP** — Cloudflare flags it as a partially exposed origin. Not changed. **Decision 2026-10-03:** Access leaves the dashboard hostname at Phase B ([`MIGRATION.md`](MIGRATION.md) decision 5); not yet executed when this PR merged. `api.origenlab.cl` keeps its Access application |
| Operator machine | `auto-refresh-mail` every 3 min; `auto-mirror-dashboard` every 1 min; systemd user units for the local API |
| HostGator (cPanel) | the public site `origenlab.cl`, served from the cPanel document root behind a Cloudflare proxy. Files still arrive by **manual upload** |
| GitHub Actions | 8 workflows; **none is scheduled** — all are push/PR path-filtered or `workflow_dispatch` |
| GitHub Actions → cPanel | `web-deploy`, two jobs. `build` runs `npm ci` and `npm run validate` and publishes the validated `dist/` as an artifact; it holds no secret and no environment. `deploy` mirrors that artifact into the cPanel public folder over SSH + rsync, and is **`workflow_dispatch` only, in the `production` environment** — a push to `main` never creates it, so no push can open an SSH connection. `apply=false` stops after the rsync dry run. **The GitHub `production` environment exists (created 2026-09-22, no protection rules) and holds the five secrets** `CPANEL_HOST`, `CPANEL_PORT`, `CPANEL_USER`, `CPANEL_SSH_KEY`, `CPANEL_WEB_ROOT` (set 2026-09-22; values unread). **The apply step has never run:** the `deploy` job was dispatched twice on 2026-09-22 and both runs failed at *Prepare SSH key* / *Dry run*, before the `--apply` step; every `main` push since runs `build` only. The site is therefore **still the manually uploaded tree on HostGator/cPanel behind Cloudflare**, and the real document root remains unconfirmed |

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
  full pytest suite. `make test-email-pipeline` runs the full suite in parallel.

### 3.3 Open — needs an operator check, not a code change

- **Answered 2026-10-02 — Render write variables.** Both are set on the Render
  web service `origenlab`: `ORIGENLAB_COMMERCIAL_OPERATIONS_WRITES_ENABLED` is
  `true`, and `ORIGENLAB_POSTGRES_WRITE_URL` points at the Render managed
  Postgres `origenlab-dashboard-prod` — the same host and database as the read
  URL, under a different database user. Neither appears in `render.yaml`; they
  were set by hand. `POST /operations/*` is therefore configured to write, and
  the earlier 503 worry was unfounded. The service also carries the Drive
  quotation variables and the quotation-numbering seed by hand (names read,
  values not).
- **Answered 2026-10-02 — `api.origenlab.cl` reaches Render.** The Cloudflare
  record is a proxied CNAME to `origenlab.onrender.com`, Render lists the
  hostname as a verified custom domain, and FastAPI Cloud lists no custom
  domain. The target state — one production API origin, Render — is already the
  real state; FastAPI Cloud is unreachable by name.
- **Answered 2026-10-02 — FastAPI Cloud (§3.1).** Seven variables, one secret
  `ORIGENLAB_POSTGRES_URL` whose target the CLI hides, no write and no V2
  variables, zero custom domains, auto-deploy stopped at `c4cc0488`, no real
  traffic. Its plan is readable only on its billing page. **Open owner action:**
  delete the app and uninstall the `fastapi-cloud` GitHub App, so that no second
  copy of the API holds a production database credential. Not done as of this
  entry.
- **Open — `render.yaml` drifts from the real service.** The blueprint declares
  a Docker runtime named `origenlab-api` with a health-check path; the running
  service is a Python-runtime service named `origenlab`, root dir `apps/api`,
  with no health-check path, on PostgreSQL 18. Nothing applies the blueprint
  today; reconcile it (or delete it) in a reviewed change before anyone runs
  `render blueprint launch`.

## 4. Cross-era hazards

- **Schema names collide.** `evidence.*`, `outbound.*` and `catalog.*` exist in
  **both** the V1 Alembic Postgres and the V2 Supabase Postgres, with entirely
  **disjoint** table sets. A `grep` for `outbound.` returns both systems. See
  [`MIGRATION.md`](MIGRATION.md) §2.
- **Two independent migration systems.** V1 is Alembic under
  `apps/email-pipeline/alembic/versions/`; V2 is the Supabase CLI under
  `supabase/migrations/`. They do not communicate.

