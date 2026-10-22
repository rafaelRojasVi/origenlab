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
| Durable database | Supabase `origenlab-v2` (PostgreSQL 17), **in production**. Migration head `20261021200000` (66 files in `supabase/migrations/`), applied by the release automation |
| Release | `OL_RELEASE_AUTOMATION_ENABLED=true` since 2026-10-13: a merge to `main`, once CI passes, runs `production-db-migrations` end to end — encrypted backup → migrations → worker → API → dashboard at the merge SHA → postflight. Render auto-deploy stays off |
| API | Render web service `origenlab` (`render.yaml` calls it `origenlab-api`), Python runtime, root `apps/api`, Starter, Oregon |
| Worker | Render Background Worker `origenlab-mail-triage`, starter, root `apps/worker`: a supervisor with triage, capture (every minute: gmail-sync → drive-file → quote-read) and tender children. 279–362 MB since 2026-10-21 (#813) |
| Legacy cron | `origenlab-gmail-sync` **suspended, not deleted**, kept for rollback ([`runbooks/MAIL_WORKER_CUTOVER.md`](runbooks/MAIL_WORKER_CUTOVER.md)) |
| Switches on Render | `ORIGENLAB_V2_COMMANDS_ENABLED`, `ORIGENLAB_V2_CRM_AUTHORING_ENABLED` and `ORIGENLAB_V2_QUOTING_ENABLED` on |
| Dashboard | `dashboard.origenlab.cl` behind the `dashboard-proxy` Worker; `hosted-smoke` checks every proxied GET every 30 minutes |
| Website | `origenlab.cl` on cPanel, deployed by `web-deploy` on merge |
| Open gate | Data API (PostgREST) still enabled on `origenlab-v2` with no exposed schema — the Postgres log shows `schema "pg_pgrst_no_exposed_schemas" does not exist` every ~32 s. Disabling it in the Supabase dashboard closes `t01`/`d01` ([`OPERATIONS.md`](OPERATIONS.md) §1.2) |

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

Compact index of the 150 build increments. Each entry was a measured or merged change; full
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

### 2.7.75 Production release engineering audit, 2026-10-08 — built; not activated

Measured read-only against PR #679 and production on **2026-10-08**. Supabase project is
ACTIVE_HEALTHY, PostgreSQL 17.6, **50** ledger versions, head **20261007160000**, and the
assertion constraint permits `delivery_failure`. The migrator has ledger USAGE/SELECT/INSERT
and SET access to the owner. The organization subscription is **Pro**; available managed
recovery points and a production-data restore remain unverified. Four Render services
still auto-deploy from main; cron is `*/10 * * * *`, latest observed capture success
13:50:38 UTC. Worker heartbeat and aggregate sweeps are live; 7,979 historical failed
jobs remain unchanged, and the final aggregate eligibility query returned healthy.

PR #679 now has real PG17 migration rollback/ledger/lock tests, consistent snapshot and
age-encryption tests, a restoration of the entire currently reviewed **50-migration**
application/queue schema with synthetic data, strict event/merge/CI provenance checks,
explicit PG17 tools and read-only postflight. Local verification: **26** offline tests
and **10** database tests passed. GitHub also reproduced and caught a runner-default
pg_dump version defect; explicit PG17 PATH selection fixed it. Exact final CI runs are
linked from PR #679. Nothing merged, deployed, enabled, sent, replayed or deleted in
production. Activation remains blocked by the mutable-main cron deployment race and
uncompleted one-time recovery/configuration gates; policy owner is OPERATIONS §15.


### 2.7.76 — Built: consolidated periodic mail capture, not activated (2026-10-08)

PR #679 now supervises capture and triage as separate processes in the existing Render
worker deployment. Capture runs every ten minutes on a dedicated Procrastinate queue,
with whole-cycle session lock, bounded in-cycle retries/timeouts and existing Gmail/Drive
idempotency. The new scheduler flag defaults OFF; the existing start command remains a
compatible alias. Triage receives no capture OAuth credentials. RLS-masked duplicate-key
handling was independently reproduced and fixed without widening permissions.

The controller never deploys the cron; it requires the legacy cron suspended and deploys
worker/API/dashboard at explicit commit IDs. Postflight requires candidate-SHA aggregate
sweep/capture logs and a fresh mailbox cursor. Read-only plan/backup work before cutover.
Local isolated PG17 tests include actual worker-role dedupe, overlap/cancellation and
orphan behavior, plus the full worker suite and ten encrypted release/restore tests.
Final-head CI evidence is recorded in PR #679. No production config, release, secret,
mail automation flag or historical queue job was changed. Approved cutover, actual-data
restore, authenticated smoke and shared 512 MB capacity observation remain pending.
See [MAIL_WORKER_CUTOVER.md](runbooks/MAIL_WORKER_CUTOVER.md).

### 2.7.77 CRM request context and false-positive correction, 2026-10-08 — built, not deployed

Read-only production inspection found five `lead` cases, all with active Gmail evidence links and no participant or quote. The reported PUCV request was already linked to one of them; its deterministic `requester:v1` reading was `none`, so its institution remains unresolved. The board and drawer previously hid the useful email context behind «Sin institución» and quote-only links. No production records, automation flags, archived holds, or messages were changed.

- Board: unconfirmed cases display their email subject or case title, the email-header display name when available, and a Gmail link even without a quotation. Header names are evidence, never confirmed CRM identities; bare addresses cannot enter that name field. Search includes inbound subject/name.
- Drawer: last received/sent subjects, dates, header display names and shared-mailbox Gmail links. No raw body reading, sending, or extra capture work.
- «No es una solicitud»: explicit confirmation through the existing versioned, idempotent `advance-case-stage` command, closes as `abandoned` with a reason; preserves evidence and history. The generic abandoned label avoids calling a rejected supplier offer «sin respuesta». Existing role/feature gates apply.
- Planner: multilingual reply prefixes no longer open new cases from RFQ keywords; received English quotations without explicit request wording require review. Explicit requests continue to follow R7. This does not retrospectively close the existing five cases.
- Archive backlog: retained separately. Historical archive snapshot reasons are not new decisions. No bulk import or hold override was performed.
- Validation: dashboard 652 tests and production build pass; PostgreSQL 17 isolated database, API case-command boundary/workspace/mail rules: 198 tests pass, including closure without an institution and preservation of the origin link. GitHub Actions results are recorded in the PR after pushing.

Remaining operator work: identify and confirm the requesting institution/contact, then close only individually reviewed false positives. API/dashboard production rollout needs approval and the migration-first deployment cutover remains governed by PR #679.


### R7 numbered quotation follow-ups — branch verification 2026-10-08

PR #683 now holds a new-thread inbound request mentioning a printed quotation reference (year-bearing number, CN-prefixed number, or captured attachment CN token) as an R7 human-review proposal. No case, institution, quotation, or evidence link is created by this hold, even when the quotation is absent from the CRM. Existing R1 thread links and R2 registered attachment-number matches retain priority. This prevents a follow-up about an unregistered sent quotation from automatically creating another commercial transaction; it does not reconcile existing duplicates or choose their institution.

Validation: both new failure regressions reproduce on the unchanged PR head; the changed branch passes 68 mail-planner, automation, route and cross-thread candidate unit tests (one dependency deprecation warning). Database-backed tests and authenticated browser reconciliation are separate gates. No production data, batch application, business automation flags or deployments were changed by this verification.

### 2.7.78 Dashboard navigation: Historial and admin-only Datos, 2026-10-09 — merged and deployed (PR #697)

| | |
|---|---|
| What | Sidebar of eight: Hoy · Oportunidades · Organizaciones · Personas · Catálogo · Marketing · Historial · Datos. «Revisión» no longer exists: its email suggestions (Aprobar / Corregir / Rechazar) are on «Hoy» under «Sugerencias del correo»; its technical tabs (Bloqueos técnicos, No importadas, Evidencia, Estado de los datos, Acciones automáticas) plus Proveedores and Archivo Drive are «Datos», shown to `role = admin` only (anyone else sees «Sólo administración»; nothing is read). Each Datos tab has its own address `#/crm/datos/<tab>`; old links `#/crm/revision` → Historial, `#/crm/proveedores` and `#/crm/drive` → that Datos tab. Earlier rows that say «Revisión → …» describe the layout before this entry. |
| API | New read `GET /v2/workspace/history` (`v2/history.py`): completed `platform.command_receipt` rows of the last 60 days as fixed Spanish sentences, reading only the case id, institution id, new stage and quote number from the response body (notes, reasons and addresses never leave the API); unknown command names are left out. Mounted with the workspace reads (`ContactRedactingRoute`). |
| Proxy | `GET /v2/workspace/history` added to `ALLOWED_UPSTREAM_PATHS` as an exact path. |
| Not done | The Hoy proposal inbox is not part of this entry (owner decision 2026-10-09: a worker-filled proposal queue, separate plan). |

### 2.7.79 Hoy, board and case panel cleanup, 2026-10-09 — merged and deployed (PR #698)

| | |
|---|---|
| Hoy | Each case shows once: a blocked case a client wrote on is only in «Te toca responder», with its blocker and «Resolver»; «Bloqueados» keeps only the others. The greeting carries the counts (no second row of tiles) and no longer repeats the top bar's Gmail / Drive / Sitio web links. «Estado del sistema» link removed (the sidebar has Datos). «Instituciones por confirmar» shows three before «Ver N más»; empty «Otras tareas» and «Personas por agregar» are not drawn. |
| Correos sin caso | Replaces «Sugerencias del correo»: one compact row per email (subject, sender, date, class; «Abrir ↗», «Revisar», «Descartar» = a `rejected` verdict). Not asked about, only counted: emails on a case, from a registered supplier or with a supplier-offer intent, automatic senders/classes, Labdelivery forwards, and older messages of the same thread (`sortInbox`). «Ver revisados» lists past verdicts. API: `GET /v2/workspace/triage-readings` items gain `sender_is_supplier`. |
| Board | The card's day badge counts from the case's last movement — the same date as the «Últimos 30 días» bands; client silence stays on the status line. Names wrap at word boundaries. *(Superseded by §2.7.87: one clock, no badge.)* |
| Case panel | Actions at the top: «Registrar seguimiento», «Cambiar estado», «Marcar ganada» when it applies (and «No es una solicitud» on a case without a quotation); the rest under «Más…». Removed: «Estado actual sin verificar» and «Al día» badges, the explanatory footnotes, sha256, import origin and the internal id. |
| Follow-up | PR #699 (same day): the quote-number box and the exchange rates moved to «Hoy»'s side column (sticky on wide screens), «Correos sin caso» closes the main column; nothing sits under both columns any more. |

### 2.7.80 Fresh quotes are not «sin decidir»; won-case replies in «Te toca responder», 2026-10-09 — merged and deployed (PR #700)

| | |
|---|---|
| Problem | Every registered quote — the Gmail sync's included — carries `origin = 'historical_import'`, so the three quotes sent on 8 Oct showed as «Enviada · sin decidir» and «Decidir 3 casos históricos» proposed a follow-up task for the day after sending. A client writing on a won case (the payment, the delivery date) was never listed: «Hoy» skipped closed cases. |
| Rule 1 | `stageBasis` (dashboard) ignores the import origin while the newest revision is younger than 14 days (`RECENT_QUOTE_DAYS`): the quote is «Enviada», joins «Seguimientos» from day 3 on the 3 · 14 · 30 rhythm, and only becomes «sin decidir» after two weeks with no task and no reply. |
| Rule 2 | `repliesToAnswer` includes a won case whose last inbound email is later than `closed_at` (`wroteAfterWinning`); the row reads «Escribió tras ganar el …», offers only «Abrir respuesta» (tasks are refused on closed cases, so answering the email is what takes it off the list). Lost cases stay out: a client writing back there is a new request. |
| Not changed | The API still stores every registered quote as `historical_import`; a distinct origin for the sync's quotes is a later change. |

### 2.7.81 «Atendido» on a won-case reply, 2026-10-10 — merged (PR #701)

`apps/api` read + `apps/dashboard`: no command, proxy or schema change. Reuses `add-note`
(CRM authoring, §2.7.4x) and the case notes read.

| | |
|---|---|
| Problem | A client writing on a won case (payment receipt, «¿plazos de entrega?») showed under «Te toca responder» with «Abrir respuesta» as the only control: a task is refused on a closed case, so the row left only once an email went out on the thread. Answered by phone, or needing no answer, it stayed. |
| API | `GET /v2/workspace/pipeline` cards gain `last_note` — the newest active `crm.note` with `subject_kind = 'opportunity'` on an open or won case: `created_at` (ISO UTC), `body` (first 160 chars), `author` — or null. A ninth query in the same pipelined round trip (`_SQL_PIPELINE_NOTES`). |
| Rule | `repliesToAnswer` also skips a reply on a **won** case when a note on it is newer than the client's email (`notedAfter`), alongside the task rule of §2.7.70. On an open case only a task answers: an internal note never hides a client's email. The client's next email puts the case back. |
| Button | «Atendido» on a won-case reply row (sales/admin with `crm_authoring_enabled`): `add-note` on the case, body «Atendido: el cliente escribió el … tras ganar el caso; respondido por otro medio o sin respuesta pendiente.» The row leaves; the note is in the case drawer like any other. Open cases keep «No requiere respuesta» (a task) — unchanged. |
| Evidence | `apps/dashboard` `npm run validate`: **700 passed** + build; `apps/api` `scripts/validate.sh`: **3883 passed**, 676 skipped (the pipeline statement-count guard documents ≤ 11: 2 setup + 9 data queries, still one round trip) |
### 2.7.82 «Archivo Drive» counts what the Drive cron filed, 2026-10-10 — merged (PR #702)

`apps/api` read only: no command, proxy, dashboard or schema change.

| | |
|---|---|
| Problem | The Drive cron (`drive-file`, §2.7.71) files each new quote PDF and records it as a `drive_file` source record; the case cards read those records. «Archivo Drive» (`GET /v2/workspace/drive-archive`) read only the September boot ledgers, so every quote filed since 7 Oct was listed under «Revisiones CRM sin PDF en Drive» (11 on 10 Oct) and its folder was missing from the page, although the PDF was in Drive and linked on the card. The cron itself reported `candidates: 0` every ten minutes: nothing was pending. |
| Fix | `drive_archive` joins each revision's `drive_file` record and builds the archive from `drive_links_from_records` (ledgers + records, the same union the cards use; `drive_archive_from` is the pure part). A revision counts as «sin PDF en Drive» only when neither names its hash. `ledgers` gains `crm` for the cron's records. |
| Evidence | `apps/api` `scripts/validate.sh`: **3883 passed**, 676 skipped; new `test_drive_archive_counts_what_the_cron_filed_not_only_the_boot_ledgers` (invented hashes and ids) |

### 2.7.83 R2 reads the quote number from the subject when no PDF names one, 2026-10-10 — merged (PR #703)

`apps/api` rules only (`v2/mail_rules.py`): no command, proxy, dashboard or schema change. The automatic R1/R2 run (§2.7.59) and «Aplicar» use the same planner.

| | |
|---|---|
| Problem | A client answering about a quote on a fresh Gmail thread without an attachment («Consulta_Compra de productos_ cotización N°01259-26», 9 Oct) stayed in «Correos sin caso» as an R7 review, although the number named exactly one open case. R2 read quote numbers from PDF file names (`cn_tokens`) only. |
| Rule | When no attached PDF names a quote **and the thread is not already a case's**, R2 also reads the subject (`subject_quote_keys`, `_SUBJECT_QUOTE_NUMBER`): a CN token («CN01259», «CN12395» with its zero restored, as a PDF token) or a number with its year («01259-26», «cotización N°01259-26»). A bare correlative («Cotización 1259») is not read: it stays an R7 review as before. Four or five digits, up to three letters; a sixth digit (a supplier's own number) disqualifies, and so does a `-`/`_` right before it («OC-01259-26»); a purchase-order subject (`_po_subject`) is never read as a quote number. A PDF's number always outranks the subject's («Re: Cotización 01198-26» carrying CN01240 sends 01240); a thread R1 already knows keeps its link whatever its inherited title says. The rest is unchanged: exactly one open case → auto link; several → proposal; a closed or unknown number → R7/R8 as before. |
| Evidence | `apps/api` `scripts/validate.sh`: **3887 passed**, 676 skipped; seven new rule tests (fictitious numbers and addresses); the subject test fails on the old planner |
### 2.7.84 Triage: «REMOVER» with a signature and an absence notice under «RE:» are noise, not quote requests, 2026-10-10 — merged (PR #704)

`apps/worker` rules only (`triage_rules.py`): no command, proxy, dashboard, API or schema change. The W10 suppression grammar (`unsubscribe_replies.classify_reply`) is unchanged.

| | |
|---|---|
| Problem | Two replies to the Cyber campaign (9 Oct) were read as «Solicitud de cotización» and sat in «Correos sin caso»: a body «REMOVER» followed by the sender's signature (the W10 grammar refuses it as not standalone, and the subject was «RE: Cyber …», not the bare word), and an out-of-office text whose subject was only «RE: Cyber …» with no `Auto-Submitted` header. |
| Rule 1 | `unsubscribe` when the reply's first non-empty line is a bare BAJA/REMOVER form and more text follows (`body:baja_first_line`): an unsubscribe reading for a person to confirm. Suppression itself still goes through W10, which keeps refusing the non-standalone body — nothing is suppressed by this rule. |
| Rule 2 | `auto_reply` when the reply's own opening (quoted history cut, first 300 chars) contains an absence phrase («fuera de la oficina», «out of office», «me encuentro de vacaciones», …) and no quote-request word precedes it (`body:<phrase>`): a person who asks for a price and then mentions a trip stays a person. |
| Evidence | `apps/worker` `scripts/validate.sh`: **430 passed**, 51 skipped; seven new `test_real_mail_cases` rows (fictitious addresses) |


### 2.7.85 «¿Marcar ganada?» and «Abrir caso» from what the triage read, 2026-10-10 — built, not deployed

`apps/dashboard` + one `apps/dashboard-proxy` allowlist entry: no API, worker or schema change. Owner decisions 2026-10-10: both parts, and a purchase order on a case with two sent revisions asks which one.

| | |
|---|---|
| Problem | The triage worker had read 6 purchase orders and 53 quote requests (audit 2026-10-10) and none of them fed anything: a purchase order on a case was counted «en un caso» and hidden from «Correos sin caso»; a quote request without a case offered only «Revisar» and «Descartar», because `open-commercial-case` was not reachable from the browser. Winning or opening meant finding the case in the Tablero by hand. |
| «¿Marcar ganada?» | `wonProposal` (`triage.ts`): a `purchase_order` reading (class or intent) whose thread is on exactly one case that the board shows at «Cotizando» or «Conversación» with at least one sent, unreplaced revision becomes a proposal row at the top of «Correos sin caso»: «OC recibida · ‹institución› · ¿marcar ganada 01253-26 r1?». **Marcar ganada** records the `approved` verdict and then runs `markCaseWon` — the drawer's own flow (`advance-case-stage` → «Conversación» when needed, then `record-case-won`), one idempotency key per step, kept across a retry so no step is recorded twice. Two or more revisions: radio buttons, the button enables on a choice. **Descartar** records a `rejected` verdict. A won or closed case, or one with nothing sent, stays counted «en un caso»: nothing is proposed that the API would refuse. |
| «Abrir caso» | On a `quote_request` reading (class or intent) that no case holds: `open-commercial-case` with the email as `origin_source_record_id`, the subject minus reply prefixes as the title (`openCaseTitle`), then the `approved` verdict. The case opens at «Solicitada» without an institution and lands in «Instituciones por confirmar». If the opening is refused nothing is reviewed and the row stays; if the verdict is refused after the case opened, the toast says so and the board reloads. |
| Proxy | `CASE_COMMAND_POST_PATHS` gains `/v2/commands/open-commercial-case` (11 exact paths), same Origin / JSON / `Idempotency-Key` guard and 16 KiB limit; `record-case-interest` stays refused. README row updated. |
| Wiring | `OverviewPage` passes the pipeline cards and `reloadPipeline` to `TriagePanel`, so a won or opened case shows on the board behind the panel without a page reload. |
| Evidence | `apps/dashboard` `npm run validate`: **717 passed** + build; new `TriagePanel.test.tsx` (8) and `triage.test.ts` (+10: `wonProposal`, `isOpenableRequest`, `openCaseTitle`, `sortInbox` with cards). `apps/dashboard-proxy` `npm run validate`: **439 passed**; the open-case path forwards with its key and is refused cross-site or without a key. |


### 2.7.86 «Posibles BAJAS según la lectura del correo»: a triage `unsubscribe` reading confirmed into the W10 suppression, 2026-10-10 — merged, applied and deployed (PR #707)

`supabase/` + `apps/api` + `apps/dashboard-proxy` + `apps/dashboard`. Owner decision 2026-10-10 (chat): the readings go into the Bajas page as a list a person confirms one by one — never an automatic suppression. The standing rule holds: no triage reading feeds the rules or the grammar.

| | |
|---|---|
| Problem | The triage reads a «REMOVER» subject, or a «BAJA» first line followed by a signature, as class `unsubscribe` (§2.7.84) and records only a `message_triage` assertion. The reply grammar refuses those bodies, Claude never reads an unsubscribe, and `GET /v2/workspace/triage-readings` lists only what reached the model — so the address stayed in every audience unless an operator typed the reply into the API-only apply tool. The audit of 2026-10-10 found 2 such «REMOVER» replies on the September waves. |
| Schema | `20261010120000_slice5_w10_triage_reading_confirmed.sql`: `create or replace` of the helper `outbound.add_unsubscribe_contact_control` (the shipped W10 body, SECURITY INVOKER behind the unchanged SECURITY DEFINER wrapper) with one more basis, `triage_reading_confirmed` (reading id, note, policy version). The function proves the id is a `message_triage` assertion of class `unsubscribe` on a `gmail_message` capture record whose `comms.message` is inbound and whose `from` participant is exactly the address (else `22023`); a request already promoted or linked for that record and address answers `already_resolved`. It then writes what a confirmed «BAJA» writes: the permanent `(block, marketing, unsubscribe)` control or the link to an existing marketing block, one resolved `unsubscribe_request` on the capture record (value: `basis`, `triage_assertion_id`, `triage_version`, `triage_reasons`, `review_note`) and one `contact_control.added` / `evidence_linked` event with `triage_assertion_id`. No `grammar_version`: no grammar accepted the body, a person did. No new function, FK or definer; the triage reading itself is untouched. |
| API | Read: `suppressions()` gains `triage_readings` (and `summary.triage_readings`): inbound `message_triage` readings of class `unsubscribe` whose From has no marketing block and no `unsubscribe_request` other than a dismissed one — address, subject, reasons, `observed_at`, `gmail_message_id`; never the body. Command: `POST /v2/commands/confirm-triage-unsubscribe` (`assertion_id`, `expected_address`, `note`, `Idempotency-Key`; sales/admin; mounted with the other W10 commands behind `ORIGENLAB_V2_UNSUBSCRIBE_APPLY_ENABLED`). The API refuses a reading that is not an unsubscribe (`404 reading_not_found`) and a From other than the one confirmed (`409 reading_address_mismatch`) before calling the function. |
| Proxy | `MARKETING_COMMAND_POST_PATHS` gains the exact path (nine non-sending marketing commands), under the 8 KB review-body limit, same Origin / JSON / key guard. Preview and apply stay unlisted. |
| Dashboard | Bajas page: stat «Leídas por el triaje» and a panel «Posibles BAJAS según la lectura del correo» — one row per reading (masked for a viewer by the API), the reason in words, «Abrir ↗» to the email in the shared mailbox, and **Confirmar BAJA** (sales/admin, only when the API mounts the commands) opening a note form with one Idempotency-Key kept across a retry. A refusal is shown and the row stays. No dismiss: a reading nobody confirms simply stays listed. |
| Applied | 2026-10-10, before the merge (schema first): the owner ran a plan/apply psql script as `origenlab_migrator` over the session pooler (`verify-full`, the Supabase CA), one transaction = ledger guard + the file + its `schema_migrations` row. Ledger **50 → 51**, head `20261010120000`; the function body carries the new basis. The password lives in a mode-600 file on the owner's machine, never in the chat. |
| Deployed | Merge `cb117d32`: Render API, dashboard and `origenlab-mail-triage` live on it; `dashboard-proxy-deploy` run 38057914092 succeeded. `ORIGENLAB_V2_UNSUBSCRIBE_APPLY_ENABLED` unchanged. |
| Not done | The two September «REMOVER» replies wait for an operator's «Confirmar BAJA» on Bajas. pgTAP ran only in CI for this change. |
| Evidence | `apps/api` `scripts/validate.sh` with the disposable cluster (migration applied): the new database test seeds a captured «REMOVER» email, a quote request and an outbound message, lists exactly the first, refuses the wrong address, the non-unsubscribe reading and a viewer, confirms once (+1 control, +1 assertion, +1 event, +1 receipt), proves the request sits on the capture record with the reading left `unresolved`, answers `already_resolved` on the second confirmation (+1 receipt only) and shows the owner cannot delete the block. In-process: five W10 POSTs, body validation, role and switch gates. `apps/dashboard-proxy` `npm run validate`: **447 passed**. `apps/dashboard` `npm run validate`: **719 passed** + build. |

### 2.7.87 One clock per case on the Tablero, 2026-10-10 — merged and deployed (PR #706)

`apps/dashboard` + one `apps/api` read change. Owner finding 2026-10-10, confirmed in the sandbox: a card carried three unlabelled clocks — a «13 d» badge counting record edits (and the «Últimos 30 días» bands grouped by it), the quote date, and a status line counting client silence from the quote — so a case moved to Conversación yesterday sat under «Últimos 30 días» saying «Sin respuesta · 154 d», and a follow-up from May read as calmly as one from yesterday.

| | |
|---|---|
| The clock | `lastTouch` (`caseDisplay.ts`): the last commercial touch after the latest quote — the client's newest email; else OrigenLab's newest email; else the quote; else nothing. **A note is never a touch** (2026-10-12: a note is a reminder to oneself, «Mandar correo esta semana», and had hidden a case from «Seguimientos»); «Registrar seguimiento» is now a reminder — a note plus a «Seguimiento de …» task for the chosen day, replacing the earlier one — and «Seguimientos» shows the case's newest note under it. `updated_at` is not a touch. `activityTimestamp` (sorting, bands) now reads it; creation is the clock only for a case with no quote and no mail, so an imported case does not sit with the import's week. |
| The card | The badge is gone. The status line says who moved last and for how long: «Respondió 02 oct · te toca», «Seguimiento 07 may · 156 d sin respuesta» (or «Seguimiento anotado …»), «Sin respuesta · 97 d»; tone by the follow-up rhythm from that touch, so an old follow-up turns red like an old quote. |
| The bands | «Hoy», «Esta semana», «Semana pasada», «Este mes», «Más de un mes», «Más de tres meses», «Más de un año», by the same clock as the line, so band and line cannot contradict. |
| Hoy, Decidir | `followUpsDue` counts the rhythm from the same touch, so a logged follow-up resets it and completes a due «Seguimiento …» task like an email does. `proposeDecision` counts the 45 silent days from the last touch («35 días sin respuesta desde el seguimiento», «Seguimiento hace 12 días»), not from the quote alone. |
| API | `suggest_next_action` names the same touch: «Responder al correo del 02-10-2026 sobre 012092A-26» after a client reply, «Hacer seguimiento de … (último contacto 04-10-2026)» after OrigenLab's email or note, «(enviada 08-05-2026)» only when nothing followed the quote. `test_v2_crm_workspace_next_action.py` (4). |
| Gaps left | The drawer's «Correos del caso · Último enviado» is mail only, by design. Hosted capture started 2026-09-25: a follow-up written between a May quote and that date exists nowhere in V2; a note can record it but does not move the clock. |
| Evidence | `apps/dashboard` `npm run validate`: **719 passed** + build; `apps/api` `scripts/validate.sh` in process. `PipelineBoard.test.tsx`: no badge; finer bands; a logged follow-up moves a card up and counts the silence from it; a note after a reply still says «te toca»; a stage edit alone does not lift a case; an imported case bands by its quote. Sandbox (same commit as production, clean-room data) screenshots before and after. |


### 2.7.88 «¿Reabrir?» on Hoy: a reply on a closed case reopens it as a new case that references it, 2026-10-10 — merged and deployed (PR #709); superseded by §2.7.90

`apps/api` + `apps/dashboard-proxy` + `apps/dashboard`; no schema change (`crm.opportunity.reopened_from_opportunity_id` existed since Slice 0, unused). Owner decisions 2026-10-10: propose «¿Reabrir?» in Hoy; 90-day window; older replies open a new case too, linked to the old one.

| | |
|---|---|
| Rule kept | WORKFLOWS §1.1: a terminal stage is never revived; reopening is a **new** case that references the old one. The stage guard trigger and `advance_case_stage` are untouched. |
| API | `POST /v2/commands/reopen-commercial-case` (`opportunity_id` of the closed case, `origin_source_record_id` = the reply, `stage` ∈ {`lead`, `negotiating`}, `note`; sales/admin, `Idempotency-Key`; mounted with the other case commands). One transaction: the new case at `lead` with the old title, `reopened_from_opportunity_id`, the reply as `origin` evidence, the old case's confirmed requesting institution carried over (supplier exception included); for `negotiating`, four recorded `opportunity.staged` moves along §1.1. Refusals by name: `case_not_found`, `case_is_not_closed`, `case_already_reopened` (the new case is still open), `source_record_not_found` / `source_record_quarantined`, `stage_requires_a_confirmed_requesting_institution`. The closed case is read, never written — not even an event: the domain-event type vocabulary is a closed list, and the reference lives in the new case's column and `opportunity.created` payload. `GET /v2/workspace/triage-readings` cases gain `closed_at` and `close_reason`. Ten case commands now. |
| Proxy | `CASE_COMMAND_POST_PATHS` gains the exact path (twelve), same guard and 16 KiB limit. |
| Hoy | `reopenProposal` (`triage.ts`): a person's email (not an automatic class, not a supplier offer) whose thread is on cases that are **all** closed `lost` or `abandoned` — never `won`, whose client is answered from «Te toca responder» — becomes a row «Volvió a escribir · ‹institución› · caso perdido hace N d · ¿reabrir en «Conversación»?». Target «Conversación» within 90 days of closing when the case had a confirmed requester (from the board's card, or trusted to the API without cards), else «Solicitada». **Reabrir** runs the command first, then the `approved` verdict (as «Abrir caso»); **Descartar** records `rejected` and the case stays closed. Proposals rank first with «¿Marcar ganada?». |
| Correction (same day) | «Volvió a escribir» means **after** the closing: an email already on the thread when the case was closed is not proposed (`sent_at` must postdate `closed_at`). Found in production within hours: a client's 8 Oct email asking for bank details sat on a case closed «sin respuesta» on 9 Oct, and Hoy proposed reopening on an email the closer had in front of them. The closing was the mistake there; the recovery is Reabrir → «Registrar cotización» on the quote email → «Marcar ganada». |
| Gaps left | A reopened case starts without the closed case's quotes; winning it means registering the quote again from its email. The case drawer does not yet show «Reabierto como …» on the closed case or «Reabre el caso …» on the new one (`reopened_from_opportunity_id` is not in the pipeline cards). The reply email is the new case's origin, not linked to the closed case. |
| Evidence | `apps/api` `scripts/validate.sh` with the disposable cluster: three new database tests (reopen at «Conversación» with the requester carried over and four moves, refused while the new case is open; reopen at «Solicitada» and the named refusal without a requester; only a closed case on an existing, clean document) + validation + the route table at ten. `apps/dashboard-proxy` `npm run validate`: **448 passed**. `apps/dashboard` `npm run validate`: `triage.test.ts` (+2), `TriagePanel.test.tsx` (+2), policy list. |


### 2.7.89 «Vencidas» on Hoy: overdue tasks escalate, 2026-10-10 — merged and deployed (PR #710); the panel folded into «Tareas» by §2.7.90

`apps/dashboard` only. Owner decisions 2026-10-10: a row in Hoy at 3 days overdue under the task's owner; from 7 days for every profile; a daily email per profile (not built, see below).

| | |
|---|---|
| Rule | `overdueTasks` (`today.ts`): open, non-follow-up tasks on open cases overdue by **3 days or more that belong to the signed-in profile** (`task.owner` = the profile's display name, else the operator's), plus **anyone's overdue by 7 days or more** (`escalated`). Most overdue first. Follow-up tasks stay in «Seguimientos», whose red «¿Cerrar?» rhythm already escalates them. |
| Page | A «Vencidas» panel above «Te toca responder», red count, the same «Hecho» / «+1 semana» as any task; an escalated row says «de ‹perfil›». Those tasks leave «Otras tareas de hoy», so nothing shows twice. Without a profile name (an older session) only the escalated ones show. |
| Not built | The daily email per profile with its overdue tasks: it needs a worker cron and the Google sending setup the campaign test send waits on (§2.7.47); nothing sends today. |
| Evidence | `apps/dashboard` `npm run validate`; `TodayBody.test.tsx` (+2: the list rule with own / others' / follow-up / closed-case tasks; the page order, the owner label, no duplicate in «Otras tareas»). |


### 2.7.90 Simplification: a closed case reopens as the same case; Hoy is three lists, 2026-10-10 — applied; merged with its PR

`supabase/` + `apps/api` + `apps/dashboard-proxy` + `apps/dashboard`. Owner decision 2026-10-10 after the first day of «¿Reabrir?» in production: the system had grown more rules than a team of three needs; reopening should be one click on the same case, and Hoy should be shorter.

| | |
|---|---|
| Why | §2.7.88 reopened by creating a new case referencing the closed one, because the stage guard made `lost`/`abandoned` terminal. In production that lost the quotes (a client asking for bank details to pay a 50 % deposit on a case closed a day earlier needed: reopen, register the quote again, win) and Hoy proposed reopening five cases an admin had just discarded with «Deshacer» (`discarded_by_correction`, labelled «sin respuesta»). |
| Rule | `20261010180000_slice7_reopen_closed_case.sql`: the stage guard lets `lost` and `abandoned` move to any open stage; `won` stays terminal (the 2026-10-05 correction is still its only way out); a case with `close_reason = 'discarded_by_correction'` is refused by name; the institution CHECK from `qualified` on is unchanged. WORKFLOWS §1.1 and DOMAIN §3 rewritten. |
| API | `advance_case_stage` accepts reopening (`opportunity.staged` with `reopened: true` and the old `close_reason`; `closed_at`/`close_reason` cleared); refuses `won` (`case_is_closed`) and a discarded case (`case_was_discarded`). **`reopen-commercial-case` removed** (route, body, handler, tests; back to nine case commands). `reopened_from_opportunity_id` is kept, no longer written. |
| Undo of the email rules | `_correct_case_stage` now checks itself that the latest `opportunity.staged` into the current `won`/`lost` is a worker event before taking it back (`stage_correction_refused`, 409): the guard used to refuse that for it, and now lets any lost case reopen. A person's later decision still stands. The case card's `stage_machine.is_terminal` means «nothing may follow» (only `won`). |
| Proxy | The reopen path removed; eleven case paths. |
| Dashboard | Drawer: on a lost/abandoned case «Cambiar estado» becomes a primary «Reabrir» with the open columns. Board: a lost card can be dragged back to an open column; a won or discarded one cannot. Hoy «¿Reabrir?»: one `advance-case-stage` on the same case to «Conversación» (confirmed requester) or «Solicitada», only for an email **after** the closing, only within 90 days of it; a case an undo discarded, or one closed more than 90 days ago, no longer holds its thread — its email shows as new mail with «Abrir caso». |
| Hoy | Main column: «Te toca responder», «Seguimientos», **one «Tareas» list** (overdue first — own from 3 days, anyone's from 7 with the owner named — then due today; replaces «Vencidas» + «Otras tareas de hoy») and «Correos sin caso». Side column: number box, rates, «por decidir», «Instituciones por confirmar», «Bloqueados», and «N personas por agregar» as a count linking to Personas (the list lives there). |
| Applied | 2026-10-10, before the merge (schema first): the owner ran the plan/apply psql script as `origenlab_migrator` over the session pooler; ledger **51 → 52**, head `20261010180000`; the guard body carries the reopening rows. |
| Evidence | `apps/api` `scripts/validate.sh` with a disposable cluster carrying the migration; case-command database tests (same-case reopen keeps the requester and writes one marked event; institution rule and closed→closed refused; a discarded case refused by the API and by the database as owner). pgTAP 062 (69), 063 (47), 076 (11) green against the cluster; the whole suite green except `100_hosted_role_bootstrap` #22, which fails only because the throwaway cluster gives the roles passwords. `apps/dashboard-proxy` and `apps/dashboard` `npm run validate`. |


### 2.7.91 Triage v2: the reply's own words, wider lost signals, newest reading wins, 2026-10-11 — migration applied to hosted; code not merged

`apps/worker` + `apps/api` + `supabase/` + docs. From the 2026-10-11 audit of «Correos sin caso» (25 rows): twelve were supplier mail with no registered supplier (a registry gap, no code — «Agregar proveedor» in Datos → Proveedores hides them), and most of the rest were misread by the rules — production triage is rules only (no model key on Render).

| | |
|---|---|
| Problem | v1 read the quote word anywhere: in a «RE: Solicitud de cotización» subject and in the quoted history under a reply. A client declining for budget, a deposit receipt, a delivery address and a supplier's price list all became `quote_request` with «Abrir caso» offered. «Anulación OC …» read as a purchase order because of the OC number. The lost list (R6's seven phrases) missed «se salen de mi presupuesto» and «ya fueron gestionadas por otro proveedor». And a bare «REMOVER» reply from 8 Oct, triaged before PR #704, stayed «Otro (persona)»: nothing re-reads old mail unless `TRIAGE_VERSION` is bumped — and bumping it would have (a) listed every message twice in «Correos sin caso» (the API read every version) and (b) refused every new hard-bounce block (`outbound.add_contact_control` pinned the literal `triage:v1`). |
| Rules (`triage_rules.py`, `TRIAGE_VERSION = 2`) | Phrases are read on the reply's **own** text (`reply_text`; a pure forward is read whole) and on the subject; a quote word in a «RE:/RV:/Fwd:» subject no longer counts; a price-list subject (`lista de precios`, `price list`) never does. `_TRIAGE_LOST_PHRASES` = R6's list + budget («se sale(n) de mi/nuestro presupuesto», «fuera de presupuesto», «excede/supera el presupuesto», «no contamos con presupuesto»), «gestionada(s)/gestionado(s) por otro proveedor», «dejar/deja/dejamos/queda sin efecto», «fue/ha sido/queda anulada(o)» — the triage only proposes; R6 (auto link + lost) keeps its narrow list. A cancellation subject («anulacion», «anulada», «sin efecto») is `lost` (`subject:cancelled`) **before** an OC number is an order. CN and OC numbers are still taken from the whole message and file names: they link (a reply under a quoted CN is `quote_followup`), they do not classify. |
| API | `GET /v2/workspace/triage-readings` lists one reading per message — the newest `triage:v<N>` by number («v10» after «v2») — and a verdict on an older version still counts as the message reviewed. «Posibles BAJAS» (`crm_workspace`) reads the newest version too. `requester:v1` and `case_match:v1` rows (same kind, other shape) are never mistaken for a reading. |
| Schema | `20261011120000_slice4_hard_bounce_any_triage_version.sql`: `outbound.add_contact_control` byte-for-byte the 20261007160000 body except that the evidence's `triage_value_norm` must match `^triage:v[0-9]+$` instead of equalling `triage:v1`; the re-proof against the bounce assertion of that exact version is unchanged. **Must be applied to hosted before the worker deploys** (the worker names `triage:v2` from its first bounce). |
| Applied | 2026-10-11, before the merge (schema first): the owner ran the plan/apply psql script as `origenlab_migrator` over the session pooler (`verify-full`, the Supabase CA), one transaction = ledger guard + the file + its `schema_migrations` row. Ledger **52 → 53**, head `20261011120000`. The script's after-check failed with «permission denied for schema outbound»: the migrator is NOINHERIT and must `SET ROLE origenlab_owner` to read the function — a script defect, after the commit. |
| Effect once live | The sweep re-reads the last 14 days under v2 within minutes (rules only, no model cost): the «REMOVER» reply becomes a «Posible BAJA», the budget decline and the annulled OC become `lost`, the replies on quote threads become follow-ups; «Correos sin caso» shrinks to the senders that still need a person. |
| Found, not fixed here | `origenlab-mail-triage` on Render is still at PR #707 (`cb117d32`) while `main` is at #711: PRs #709/#711 changed `apps/api/**`, which the worker's `buildFilter` lists, and no build ran — check the service's auto-deploy setting. Five deals with quotes sent since 24 Sep but no CRM case, and a PO naming its quote as a bare «NNNNN-26»: the context matcher (§2.7.92, unpushed). The 74 PDFs of «No importadas» are the 2026-09-26 Drive archive (57 lack a confirmed institution): a confirm-from-filename tool, not clicks. |
| Evidence | `apps/worker` `scripts/validate.sh`: **445 passed**, 51 skipped (no cluster); against the disposable cluster `test_triage_database.py` 8 passed, including the no-such-user bounce that becomes a global block under `triage:v2` (the migration applied in the chain). `apps/api`: full suite against the same cluster **4454 passed**, 121 skipped (TLS and V1-Alembic only, 0 V2 skips); `test_v2_triage_review.py` 9 (+1: newest version listed, «v10» after «v2», a verdict on the older reading keeps the message reviewed). pgTAP: every file green against the cluster except `010_inventory` #17/#18 (the `pgtap` extension installed in `public` for the run) and `100_hosted_role_bootstrap` #22 (the throwaway cluster gives the roles passwords) — both artefacts of the harness. `check-public-repo-hygiene.sh` passed. |


### 2.7.93 Case panel: money, equipment lines, Gmail and Drive first; «Solicitada» absorbs «En estudio», 2026-10-11 — merged (#713, c68758f6) and deployed 2026-10-11; the case amount is replaced by §2.7.95

`apps/api` + `apps/dashboard`; no schema change. Owner request 2026-10-11: «Solicitada» and «En estudio» are the same moment; the case panel was hard to read and did not show what was quoted or for how much; Gmail and Drive should be at the top; «Confirmar institución» read as a warning.

| | |
|---|---|
| API | `_SQL_PIPELINE_REVISIONS` joins each revision's `quote_document` evidence by the PDF's SHA-256 (`quote_document:<sha256>`, the catalog quote-history import of §2.7.61) and its `evidence.document_line` rows. Each revision card gains `contents` (`currency`, `net_total` — the document's own, else the sum of non-optional lines, `net_source` says which —, `tax_total` and `gross_total` at 19 % for CLP only, and the read lines: item, kind, brand, model, description, qty, unit price, line total, optional, check status); each case gains `money`, the neto of its vigente revisions per currency plus a count of vigente revisions never read. Amounts travel as decimal strings. Read-only: nothing is written, and a revision whose PDF nobody read says so (`contents: null`). |
| Panel | Top to bottom: state; three link buttons (Gmail = the case's newest email, «Carpeta Drive», «PDF <número>» = the vigente PDF); «Cotizado, neto» large with IVA and total small and the count of vigente quotations; the actions; the next step; one block per quotation (number, revision, status, neto; up to eight equipment lines with brand, quantity × unit price and line total, «Ver N líneas más»; neto/IVA/total; that revision's PDF and email; older revisions folded); contact with what is missing; the newest email each way; notes. The institution prompt is one neutral line: «Institución sugerida por el correo: X. ¿Es quien pide la cotización?» with «Sí, confirmar» and «Cambiar» — same command, same version check. |
| Board | Each card shows the neto of its vigente quotations beside the quote number. |
| Contact links (same day) | «Contacto» shows «Empresa» and «Persona» as links: the institution opens its own panel in Organizaciones (`#/crm/organizaciones/<id>`, now honoured: the page opens that row even when it is not in the current list, through a new `id` filter on `GET /v2/organizations`, 404 for a malformed id); a CRM person opens Personas (`#/crm/personas/<id>`, the case card's contact gains `person_id`); a contact that is only an email recipient opens a Gmail search for that address in contacto@. Less text: no «(del correo)», no «Falta» list (the badge says it), no brand or «1 ×» on single-unit lines, no «PDF sin copia» / «Sin correo vinculado» placeholders, one line per email, a shorter institution question («¿Institución correcta?»). Board: six columns fill the width; the card footer wraps. |
| Cadenas (same day) | «Correos del caso» shows the case's emails grouped by Gmail cadena instead of only the newest email each way: `GET /v2/workspace/opportunities/{id}/threads` (new; proxy allowlist + README) reads every `gmail_message` evidence on the case's threads — the same thread rule as the card's last contact — and groups them (`case_threads`): newest activity first, subject once without «RE:/Fwd:», the party (cliente / proveedor when an outside domain is a registered supplier or manufacturer / interno), count, last date, «Abrir» for the cadena; inside, one line per email, newest first, the last three and «Ver todo (N)», an arrow for sent or received, the sender by name or domain (never the address) and «PDF» when it carries one. Dates are ordered as instants across time zones. Supplier cadenas about the same deal appear only once their evidence is linked to the case (context matcher, §2.7.92, unpushed). |
| Money per state (same day) | Each Tablero column shows the CLP neto of its cases' vigente quotations and «+ N sin leer» for cases whose quote PDFs nobody read yet (quotes registered after the 2026-10-06 history load), so a column's figure never pretends to be complete. The first column is labelled «Solicitada / En estudio». The panel's big number says «Suma de las N cotizaciones vigentes, neto» when it adds more than one (alternatives a client was offered are all summed), and each quotation folds: one quotation opens expanded, several start folded with number, revision, date, status and neto in the header. |
| Stages | One «Solicitada» column for `lead`, `qualifying` and `qualified`; «En estudio» is gone from the board, «Cambiar estado» and the labels. The API and the database keep the three stages: the confirmed-institution rule still starts at `qualified`, so «Enviada» and «Conversación» still ask for it first. |
| Not yet | Quotes sent since the 2026-10-06 history load (e.g. those registered from Gmail) have no read contents: the panel says «Este PDF aún no se ha leído». The quote reader (§2.7.94, same PR) reads them once it is switched on. |
| Evidence | `apps/dashboard` `npm run validate`: **736 passed** (59 files; +6 in `CaseQuotes.test.tsx`: es-CL money format, neto/IVA/total and the unread count, equipment lines with optional and folded revisions, the top links, «Ver N líneas más»; seven drawer and board tests moved to the new copy and the five-column board). `apps/api`: 3897 passed without a cluster, +3 pure tests for `quote_contents` and `case_money`; the revised pipeline SQL ran against a scratch copy of the clean room carrying the 150-document quote-history plan (66 of 69 revisions matched, 42 of 44 cases with money) behind the local sandbox dashboard. `check-public-repo-hygiene.sh` passed; every fixture value is invented. |

### 2.7.94 Quote reader: registered quote PDFs read into lines, 2026-10-11 — merged (#713), migration applied to hosted, switched on 2026-10-12

`apps/worker` + `apps/api` + `apps/dashboard` + `apps/dashboard-proxy` + `supabase/`. Piece 1 of the connected-quotes plan (owner decisions 2026-10-11; spec and plan private in `~/data`). Until now only the one-off 2026-10-06 history import read quote PDFs: 14 of 81 registered revisions in the real data had no lines, so no money in the panel or on the Tablero.

| | |
|---|---|
| Worker | `quote_reader.py` + `origenlab-worker quote-read` after `drive-file` in the cron (OPERATIONS §8.12): Claude PDF input + Plantilla cross-check on `pdftotext -layout`, the history import's line and withholding rules (now shared in `history_check`: `document_lines`, `identities_of`, `withheld`), refusals recorded as `quote_read` observations. Model off writes nothing. |
| Schema | `20261012100000_slice7_quote_reader.sql`: worker SELECT + INSERT on `evidence.document_line` (two RLS policies; never UPDATE/DELETE), `quote_read` in the assertion vocabulary. Inventory 51 tables, **178 policies**, migrations **54**. Owner applies to hosted before the worker deploys. |
| API / Datos | `GET /v2/workspace/quote-readings` (proxy allowlist); Datos → «Acciones automáticas» → «Cotizaciones no leídas» with the reason in Spanish and the case link. |
| Measured | On the 130 history PDFs with stored text: pypdf's layout text gave the parser the same rows for 17, pdftotext for 130 (pypdf dropped). Template-only reading agreed on the neto for 2 of 120 (missed rows, optional lines summed, REF codes as models) — hence model off writes nothing. The model's gate (≥ 95 % neto and lines on the history PDFs) is owner-run and pending. |
| Real API (2026-10-11) | First runs exposed three defects, fixed: the answer schema had 17 union-typed parameters (API limit 16; four fields are now plain strings); dates were transcribed in words (the prompt asks YYYY-MM-DD, an unreadable date is stored null); a quote priced per item had no line totals (total = unit price × quantity). The evaluation now gates on the money (neto + line totals) and reports exact model-code matches beside it. 20 history quotes (18 with a PDF): 18 read, line totals **98.5 %**, neto **17/18 (94.4 %)** — just under the 95 % gate (one 5-line quote misread on 2 of 3 runs); exact code matches 74.6 % (dashes, suffixes; matters for margin). Before switching on: one run over all 118 history quotes with a PDF. |
| Not yet | Applying the migration; the full-set evaluation; setting the Anthropic key and flags on the cron; poppler on Render is unverified — a model without `pdftotext` is a configuration refusal (exit 3, `pdftotext_missing`), and each run logs `text_layer`. |
| Final review (fixed) | A fresh whole-branch review found: one failing PDF (a permanent API 4xx, a value the database rejects) stalled the run and failed the cron every ten minutes — now caught per PDF, recorded `request_failed` / `write_failed`, transient errors only counted; refusals were keyed per email — now per PDF (`…:<sha256>`); unrecorded skips held the batch and the order ignored «newest first» — `hash_mismatch` / `attachment_not_in_eml` recorded, candidates newest first; a PDF with no text layer (scanned, or a Labdelivery letterhead the model missed) could be written — now `no_text_layer`, the prompt names Labdelivery, a model without poppler is a config refusal; a missing neto or one line without a total silently disabled the neto check — now the neto is the sum of the lines with totals and the check always runs; a bad model id failed every run — now exit 3 `model_invalid`; amounts beyond the columns and NUL bytes are rejected before the database. |
| Evidence | worker validate + DB tests on a disposable cluster (reader 32 incl. 8 DB), api workspace tests, proxy 454, dashboard 731, every pgTAP file green on the cluster except the two known harness artefacts, audit tests 352, clean-room static 13/13; hygiene passed. |
| Switched on (2026-10-12) | Full evaluation, 118 history quotes, 95 with a PDF: 95 read, neto **98.9 %**, line totals **99.7 %**, exact model codes 86.8 % — gate passed. Migration applied to hosted by the owner's psql route (ledger **54**, two worker policies on `evidence.document_line`, `quote_read` allowed). Render cron: `pdftotext` 22.12.0 is present on the native Python runtime; the service's command and build filter had never been synced from `render.yaml` (it still ran only gmail-sync + drive-file, build filter empty) — the owner set the cron command (`--cron-command`), the four build-filter paths, the Anthropic key, `ORIGENLAB_WORKER_QUOTE_READ_MODEL=claude-opus-5-5` and `ORIGENLAB_WORKER_QUOTE_READ_ENABLED=true`. First two runs: 10 read, 1 refused (`not_origenlab_quote`); 3 of the 14 unread revisions were not candidates (no stored PDF/email; uncounted, a known minor). |

### 2.7.95 Case amount = the last quotation sent, not a sum; send times shown, 2026-10-12 — merged (#714, fa417697) and deployed 2026-10-12

`apps/api` + `apps/dashboard`; no schema change. Owner request 2026-10-12: a case with several quotations should not show their sum — the last one sent is the one that counts — and when things were sent matters.

| | |
|---|---|
| API | `case_money` returns one entry: the neto of the case's last sent vigente revision (by `sent_at`, then revision number), with `quote_number`, `revision_no`, `sent_at` and `quote_count` (vigente quotations on the case); currency `unread` when that revision's PDF was never read. The card, the Tablero column total and the panel all use it, so a column now adds one quotation per case. |
| Panel | «Última cotización enviada <número> · <fecha, hora>» (one quotation: «Cotizado, neto»), the neto large, IVA and total of that revision, and «Las otras N cotizaciones del caso no se suman.»; the quotation list is newest sent first, the newest open and marked «Última», the rest folded; «PDF <número>» at the top is the last quotation's. Quotation, revision and every email in «Correos del caso» show the day and the hour. |
| Evidence | `apps/api` validate 3904 passed (682 skipped without a cluster); +2 pure tests replace the sum test. `apps/dashboard` validate **744 passed** (+3 several-quotation tests, times in cadenas). Checked in the local sandbox on a read-only copy of hosted: a three-quotation case shows only its last quotation's neto with its send time. |

### 2.7.96 Triage v3: the model reads what v2 read with it off, 2026-10-12 — merged (#715, 53e2a645) and deployed; model on in production

`apps/worker`; no schema change. The triage worker had never had the model on: in a read-only copy of hosted, 88 inbound emails (supplier replies, orders, quote requests) carried `model_state: off` and showed as «Otro (persona)» in «Correos sin caso». A reading is stored once per version, so switching the model on alone would only reach new mail. `TRIAGE_VERSION` 2 → 3 with the rules unchanged; order: the model on (Anthropic key, `ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED=true`, `ORIGENLAB_WORKER_TRIAGE_MODEL=claude-opus-5-5`) before this deploys, then a one-off `triage-once --since-days 30` for mail older than the sweep's 14 days. OPERATIONS §8.11 records the rule. Evidence: worker validate 480 passed.

### 2.7.97 A case's purchase order: attached, stored privately, shown with its quotation, 2026-10-12 — built, not applied, not deployed

`supabase/` + `apps/api` + `apps/dashboard-proxy` + `apps/dashboard`. Owner request 2026-10-12: «en ganada attach the oc too, upload it to the database so it's there with the cotización», and attach the OCs of the cases already won.

| | |
|---|---|
| Schema | `20261012120000_slice7_case_documents.sql`: `crm.opportunity_document` (DOMAIN §7 #56): kind `purchase_order`, the quote revision it closes, the object in the private bucket `crm-documents` (`opportunities/<case>/<sha256>.<ext>`), source `upload` or `email` (the captured message); never deleted or rewritten, removed once with its reason. Events `opportunity.document_attached` / `document_removed`. Inventory **52 tables, 182 policies, 164 foreign keys (131 unconditionally covered), 35 functions, 55 migrations**. |
| API | `POST /v2/commands/attach-case-document` (multipart, ≤ 20 MiB, the bytes decide PDF/JPEG/PNG), `attach-case-document-from-email` (reads the message's `.eml` from `mail`, copies the part whose SHA-256 the capture recorded), `remove-case-document`, `GET /v2/workspace/opportunities/{id}/documents/{doc}/url` (signed, 10 minutes). Attaching takes no case version. Cards carry `documents`; the session says `case_documents_enabled`. `scripts/attach_case_documents.py` backfills emailed OCs onto won cases (plan, `--apply`). |
| Proxy | The two JSON commands with the case commands; the upload beside the image upload (≤ 20 MiB + 64 KiB); the link read. |
| Dashboard | «Marcar ganada» gains an optional «Orden de compra» (a case email's PDF, likely OCs first, or a file); the OC is attached first, then the win; a failed attach records no win. The panel's «Orden de compra» block (number, the quotation it closes, who and when, «Abrir», «Quitar», «Adjuntar OC» on won or negotiating cases); an «OC Nº …» badge on the quotation; an «OC» chip on the board card. |
| Backfill found | Of the 4 won cases in a read-only copy of hosted, 3 have their OC in a captured email (one on another thread, a Nubox notification) — plan verified against that copy; the 4th's OC (May) predates the Gmail capture and is uploaded by hand. |
| Not yet | Hosted migration (54 → 55, before the deploy); the Storage secret key, the bucket and the two Render API variables; the deploy; the backfill job; the hand upload. |
| Evidence | pgTAP 092 (20) and every pgTAP file on a disposable cluster (only the known 100 harness artefact); audit unit tests 352; clean-room static 13/13; API validate with the cluster 4493+ passed, +29 new (pure rules, routes, DB commands, card, session); proxy 465; dashboard 753 (+9). |


### 2.7.98 A closed case shows no notes, 2026-10-12 — built, not deployed

`apps/api` + `apps/dashboard`; no schema change. Owner 2026-10-12 on a won case showing «Hay más de una revisión vigente»: «whatever, it's already closed, remove it». A won, lost or abandoned case now carries no attention notes and is «al día» (2026-10-09 already made it never block); the panel no longer offers «Elegir revisión vigente» on it. Evidence: api workspace tests 43 passed (the closed-case test now asserts no notes), dashboard validate 753 passed (one test replaced: the disabled prompt on a closed case is gone, not disabled).

### 2.7.99 The history cases never loaded: a loader, 2026-10-12 — built, rehearsed, not run on hosted

`apps/api/scripts/load_missing_cases.py`. The 2026-09-25 history import held back the cases whose organization nobody confirmed; 28 cases (54 quote numbers, 57 PDFs) never reached the CRM, among them three sales (one paid in July–August). The owner reviewed the list on 2026-10-12 (each thread read for an OC, payment, invoice or a stated loss) and approved loading all 28: 3 won against the quotation they name, 5 lost with the customer's reason, 20 sent. The quote emails are staged first by owner-run SQL as `gmail_message` evidence with a `document_reference` per PDF (the import's shape; the email → cases rules never act on staged records); the loader then runs the dashboard's own commands as a named operator — organization (reuse, confirm, or register), open the case, requesting institution, stages, `record_historical_quotation` per PDF in send order, then won or lost — with keys per case and step, and stops at a case an earlier apply finished. Rehearsed on a read-only copy of hosted: 28/28 loaded, a second apply changed nothing (90 cases, 131 quotes). The plan and the staging SQL hold client data and stay outside the repository.

### 2.7.100 An email domain given to the organization it belongs to, 2026-10-12 — built, rehearsed, not run on hosted

`apps/api/scripts/move_domain_to_organization.py`. Owner 2026-10-12: a quote to `uc.cl` opened its case under a new institution «uc.cl» (rule R4: the domain was on no organization), while the CRM already holds the Pontificia Universidad Católica de Chile. The tool moves the domain to the right organization (`remove-organization-domain`, `add-organization-domain`), so R3 matches it from then on; on each case where the stray is the requesting institution it steps the case back to `qualifying` (a case from `qualified` on must keep a requester), demotes the stray to «mentioned», names the right institution, and walks the case back to its stage; a stray left with no case is archived. Each step is the dashboard's own command, keyed so a rerun replays. Rehearsed on a read-only copy of hosted: the `uc.cl` case moved to the PUC at `quoting`, the stray archived, a rerun changed nothing.

### 2.7.101 One way to run the tests, locally and in CI, 2026-10-12 — built, not merged

`make check` runs only the suites a change needs (vs `origin/main`), `make check-all` all of them; the api, worker, email-pipeline, dashboard, proxy and web workflows call the same targets (`supabase.yml` keeps its own steps; `make test-supabase` is their database-free half). Python suites run in parallel (pytest-xdist; 60 s per-test timeout). `make db-up` starts the disposable V2 cluster and a V1 Postgres 16 at the Alembic head, and a run with database tests skipped fails. Measured on a 16-core machine: everything 155 s (api alone 53 s)  `make lint` (ruff bug rules, gitleaks, file checks) runs first and as a pre-commit hook.(was ~6.5 min with the 688 V2 and 142 V1 database tests skipped). New in CI: the api's 142 V1 `commercial.*` Postgres tests and email-pipeline's V2 evidence/import proofs, which no job ran before. Tests that share server-wide state (role memberships, the fixed V2 test database) carry `serial_db` and run alone after the parallel phase.

### 2.7.102 Production is checked every 30 minutes, 2026-10-12 — merged and running

`make smoke` / the `hosted-smoke` workflow (every 30 minutes and on demand): all 64 GET routes the dashboard Worker forwards are requested without a session through `dashboard.origenlab.cl` and must answer 401 (public ones 200 or a redirect; `test-send-history` may be 404 while `ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED` is off), each within 2 s. `/health` now reports `version` (Render's `RENDER_GIT_COMMIT`); the check fails if production runs a commit not on main and warns when it is behind main on `apps/api` or `supabase/migrations`. Applied migrations and worker liveness are checked with the `OL_PROD_*` secrets of the main-only `origenlab-release` environment (created 2026-10-13; a `plan` run of production-db-migrations then reported 55/55 migrations applied). `RENDER_API_KEY` was added to that environment on 2026-10-13. Until #731 (2026-10-13) the worker check looked for `triage:v1` readings while triage writes `triage:v3`, so every run failed with a false «waiting for triage»; it now reads the versions from the worker's source, and the check passes. Read-only: GETs only, no credential, no response body printed. Render auto-deploy is off on all four services since 2026-10-13 (it had been on: every merge redeployed the API with ~1–2 min of 502s); releases are migration first, then a manual Render deploy, then `make smoke`. On demand, `OL_SMOKE_SESSION=<your session cookie> make smoke` also reads every route as you (200 and JSON, 404 for the placeholder ids, ≤ 3 s). There is deliberately no long-lived synthetic login: sessions stay capped at 7 days (`auth_session_lifetime`), so the authenticated pass is not scheduled (owner decision 2026-10-13).

### 2.7.103 Production backup restored from a real encrypted artifact; daily backups on, 2026-10-13

A manual `backup_only` run of production-db-migrations (run 38404933821) produced a 9.0 MB
encrypted artifact; it was decrypted offline, checksum-verified and restored into a
disposable PostgreSQL 17 container in **7 s** — 8 schemas, ~161,700 rows, a 55-migration
ledger matching production. Details and what the drill does not cover:
[`runbooks/PRODUCTION_DB_MIGRATIONS.md`](runbooks/PRODUCTION_DB_MIGRATIONS.md) →
*Drill record, 2026-10-13*. `OL_BACKUP_AUTOMATION_ENABLED=true` since then: a backup-only
run every day at 06:15 UTC, artifacts kept 30 days. `OL_RELEASE_AUTOMATION_ENABLED` stays
off until the mail-worker cutover ([`runbooks/MAIL_WORKER_CUTOVER.md`](runbooks/MAIL_WORKER_CUTOVER.md)).

### 2.7.104 Automatic releases on; the mail worker captures, the legacy cron is suspended, 2026-10-13

`OL_RELEASE_AUTOMATION_ENABLED=true` since 2026-10-13: a merge to main, once its CI passes, runs
production-db-migrations end to end (encrypted backup → migrations → worker → API → dashboard at
the merge SHA → postflight). First complete run: **38414668262** for `296cf6be` — all three
services live on that SHA, postflight passed (candidate-SHA triage sweep and capture cycle, fresh
mailbox cursor, heartbeat, no eligible evidence gaps). Render auto-deploy stays off; nothing else
deploys.

Mail-worker cutover ([`runbooks/MAIL_WORKER_CUTOVER.md`](runbooks/MAIL_WORKER_CUTOVER.md)) done the
same day: the worker gained the cron's seven `ORIGENLAB_WORKER_DRIVE_*` and two
`ORIGENLAB_WORKER_QUOTE_READ_*` settings (copied value-for-value; the two `DATABASE_CA_PEM` values
differ and the worker keeps its own, which it already verified), `ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED=true`,
and its capture cycle now runs gmail-sync → drive-file → quote-read (#733 — the cycle predated the
cron's `&& quote-read`). `origenlab-gmail-sync` is **suspended, not deleted**, for rollback (runbook
→ *Failure and recovery*). Capture was paused about 14:00–14:40 UTC while the first releases were
refused.

The first three automatic releases stopped on defects in the release controller itself, each before
the step it guarded, each fixed with a test that fails on the old code: #734 read `merged_by` from
the commit→pulls list, which never carries it; #735 the post-backup recheck lacked
`OL_RELEASE_SOLO_MODE`; #736 API readiness read the onrender.com host, which the API's host guard
answers 403 — it now reads `dashboard.origenlab.cl/api/health` and requires the released SHA.
#731 fixed the smoke's stale `triage:v1` worker check the same day.

### 2.7.105 Mail is checked every minute; Drive and quotes follow new mail, 2026-10-13 — built, deploys on merge

The worker's capture cycle (`capture_queue.CAPTURE_CRON`) checks Gmail **every minute** instead of
every 10. Drive filing and quote reading follow in the same cycle when the check stored new mail,
on the first cycle of a worker process (so a release postflight sees a complete cycle), or after
10 minutes without one (`FOLLOWUP_EVERY`); a quiet minute runs the Gmail check alone and logs
`drive_mode`/`quote_mode: skipped`, which postflight never accepts as its capture proof.
Measured read-only first: on normal days (9–13 Oct) a full cycle is ~15 s (Gmail 6 s, Drive 5 s,
quotes 7 s) and inbound mail reached the database 3–7 min after arrival (p90 ~9 min), triage
< 1 min — the ten-minute poll was nearly all of the delay. Unchanged: the session lock and one
waiting job keep a slow cycle from overlapping the next; postflight windows still allow 15/20
minutes. Not addressed: campaign bursts (6 and 8 Oct), where single Gmail runs took 18–24 min
because messages are fetched one at a time.

### 2.7.106 R9: a quote sent to a free-mail customer reaches the case its PDF names, 2026-10-13 — built, deploys on merge

Found on a real send: contacto@ sent `CN09876-<institución> (SIGLA).pdf` to a gmail.com address
on a new thread, and no rule could tell which case it was for (R1 needs a known thread, R2 a known
number, R3/R4 a recipient domain; free-mail is never an institution), so the case never reached
«Enviada». New rule **R9** (`mail_rules._quote_file_rule`) reads the institution from the
Cotizador's file name and, when it names exactly one institution with exactly one open case,
links the email, walks the case to `quoting` and records the quote — automatically, like R1 on a
thread (DOMAIN.md §3.6.6, owner-approved 2026-10-13); undo is R1's. Every other outcome is a
proposal naming its candidates. When the PDF names an institution the CRM does not hold, **R10**
offers the same steps as R4 — an institution «por confirmar» (named as the PDF names it, no
domain), its case in «Enviada» and the quote — applied only with «Aplicar». Evidence: mail-rule
unit tests (11 new) and real-database tests of R9 and R10 apply and undo. File names are split on dashes («<persona> - <institución>»); R10 creates only from the one segment that carries a legal form, a kind of institution or a «(SIGLA)», so a person never becomes an institution.

### 2.7.107 «Operaciones» replaces «Datos», visible to everyone, 2026-10-13 — built, deploys on merge

One read, `GET /v2/workspace/operations` (any signed-in operator, read-only, no migration), gives
seven Render-style cards — Correo, Clasificación, Lector de cotizaciones, Archivo Drive, Reglas
automáticas, Versión, Respaldo y publicación — each `ok`/`warn`/`bad`/`info` with a Spanish line and
its recent activity, plus the last 7 days of rule actions and two counts. The page has five tabs:
Estado, Lo que hizo el sistema (Deshacer for an admin), Reglas (R1–R10 explained, which act alone),
Por decidir (interim counts and links) and Más (Proveedores, Archivo Drive, the old technical views
for an admin). No body, address or subject in the response. Not yet: backup, release and smoke status
(they live in GitHub) and the worker heartbeat (not granted to the API role) — delivery 2, an
operation-run log; the single decision list — delivery 3.
Corrected the same day: Clasificación counts mail by its received time (the triage sweep's
14-day window), and Cotizaciones / Archivo Drive count only revisions whose originating email file
is stored (the workers' own candidate rule) — the first production view showed 5 / 11 / 2 «pending»
that no worker could ever process (an OC backfill's old mail; historical-import revisions).

### 2.7.108 «Cotizaciones enviadas sin caso» with «Abrir caso», 2026-10-13 — built, deploys on merge

Operaciones → Por decidir lists the quotes contacto@ sent in the last 60 days (a CN/COT PDF) whose
email no case holds — neither linked as evidence nor a case's origin — and whose number is not a
CRM quote: by number, date and Gmail thread only (the PDF name, which can hold a person's name,
never leaves the API). «Abrir caso» (sales/admin, case commands enabled) opens «Cotización
NNNNN-YY» on that email and goes to it; the institution and «Registrar cotización» follow with the
existing case buttons. Covers what R1–R10 cannot place (a free-mail customer named only as a person).

### 2.7.109 «Completar con la cotización del correo»: one click from a sent quote to «Enviada», 2026-10-13 — built, deploys on merge

New case command `complete-case-from-sent-quote` (sales/admin, `Idempotency-Key`, mandatory note):
from a Gmail message contacto@ sent with exactly one quote PDF, in **one transaction** — open a case
on it when none is named, take an existing institution or create one «por confirmar» (the shared
writer R4/R10 use, now `register_machine_organization`; an active institution with the same name is
reused), set it as requesting institution, walk the case along the ordinary edges to `quoting`, and
record the quote with `record_case_quotation` (number read from the PDF's CN token and sending year,
never typed). Any refusal rolls everything back. The dashboard offers it in the case panel (an open
case with no quote whose emails carry one unrecorded quote PDF) and in Operaciones → Por decidir
(replacing «Abrir caso»), with the institution prefilled from the PDF name (institution part only).
The proxy allows the POST. Not included: creating the contact person from the email.


### 2.7.110 Catálogo v2: instant search, one section per supplier, Drive data sheets and price lists, 2026-10-13 — built, migration not applied

Spec `~/data/origenlab-v2-migration/specs/2026-10-13-catalogo-v2-design.md` (private). Table #57
`catalog.product_document` (migration `20261013120000`; inventory **53** tables, **185** policies,
**168** foreign keys, **56** migrations): a product's own data sheet or a supplier's price list as a
Google Drive file id, never deleted (retired); events `product.document_recorded/_retired`,
`organization.price_list_recorded/_retired`. API: `GET /v2/catalog/index[?version=]` (every active
product as a positional, cost-free row; `{version, unchanged}` when current), `GET
/v2/catalog/images/urls?ids=` (≤ 60 signed URLs), product detail `documents` (price lists redacted
for viewers). Proxy allows both reads. Dashboard «Catálogo»: the index is searched in the browser
(MiniSearch behind an exact model-code path; < 50 ms on 10,500 items, measured in tests), supplier
rail and kind chips with live counts, supplier/kind/search in the address, windowed list of real
links with keyboard navigation, thumbnails signed in batches (one 503 → placeholders, never a broken
image), «Descargas» in the product panel. Importer
`scripts/catalog/import_product_documents.py` (OPERATIONS §14.6). **Not applied on hosted; no
documents loaded** — owner steps: apply the migration (psql-as-migrator + ledger row), then plan →
apply → verify the private manifest (10 entries: 8 price lists, 2 Hielscher data sheets).

### 2.7.111 One maker per brand: duplicate Hielscher and Ortoalresa merged, 2026-10-14 — built, migration not applied

Migration `20261014100000` adds `catalog.product.merged_into_product_id` (a merged product is
inactive; event `product.merged`; foreign keys **169**, migrations **57**). Tool
`scripts/catalog/merge_makers.py` (OPERATIONS §14.7): confirms the kept maker, moves or merges the
duplicate's products, marks the duplicate maker merged; rollback refused (backup). Catalog reads
follow merges: a product's cost history includes the observations of products merged into it,
suppliers, index rows and price lists resolve to the kept organization. Planned hosted run:
«HIELSCHER» and «Hielscher Ultrasonics GmbH» into the CRM's confirmed «Hielscher Ultrasonics» (it
carries two cases), «Ortoalresa (Alvarez Redondo, S.A.)» into «Ortoalresa»; 16 duplicated models.

### 2.7.112 One email, one row; quotes sent on a case's thread but never recorded, 2026-10-14 — built, deploys on merge

Hoy «Te toca responder» lists a client email once even when it is the newest client email of
several cases (they share a Gmail thread): the row names the other cases («También en …») and «No
requiere respuesta» schedules the follow-up on each. Operaciones → Por decidir adds «Cotizaciones
enviadas en un caso, sin registrar»: a quote PDF contacto@ sent on an email already linked to one
open case whose number no case records (a second quote sent on a thread the rules had linked before
R1 learned to record one); «Registrar en el caso» records it there through
`complete-case-from-sent-quote` with the case named. `GET /v2/workspace/operations` adds
`sent_quotes_unrecorded_on_case` and its count. Found on Arcegon: 01251-26 (4 + 8 balances) sent on
the thread of 1176-26 and never recorded.

### 2.7.113 «Ventas y cotizaciones» on the product panel, 2026-10-14 — built, deploys on merge

`GET /v2/catalog/price-history` (and the product panel) now names, for each quoted price, the CRM
case holding the quote of that printed number, its institution (through any merge) and the
outcome: «Vendida» only for the quote the case was won with (`won_quote_id`), «Ganó otra
cotización», «Perdida», «Cerrada», «Abierta», or «Sin caso». A `summary` adds times quoted, times
sold and at what price, the last price, and the single-unit range and median in one currency.
The panel shows the four numbers, a dot plot of price over time (sold filled in the brand teal,
quoted hollow, the median dashed; native tooltips), and the list with a link to each case. Sell
prices only, never costs: every role sees them. No schema change.

### 2.7.114 Forwarded requests become Solicitudes, 2026-10-14 — built, migration not applied

Spec `~/data/origenlab-v2-migration/specs/2026-10-14-forwarded-requests-design.md` (private).
Migration `20261014140000` adds `evidence.source_record` kind `forwarded_request` (no table, policy
or grant change; migrations **58**). Worker: `forwarded_request.py` reads, for an email we sent with
a forward prefix, the customer out of a marketplace contact block or the innermost forwarded hop;
stored as that record after the triage, isolated in its own `try`, off with
`ORIGENLAB_WORKER_FORWARD_READING=off`; `forward-backfill` reads past forwards only (dry run by
default). API: `GET /v2/workspace/forwarded-requests` (copies collapsed), `create-request-from-forward`
(a lead on the record, institution existing / «por confirmar» / none, contact as a note, our email
copies linked — never a Labdelivery-mailbox email), `dismiss-forwarded-request`. Hoy panel
«Solicitudes reenviadas». Measured before: 19 forwards since 1 Jul, 9 never became anything.

### 2.7.115 Gross margin per quote, and a faster product panel, 2026-10-14 — built, deploys on merge

Each quoted price in «Ventas y cotizaciones» now carries, for sales and admin, the supplier cost
in force that day (`margins.py`: the newest observation on or before it, current before stale;
before the first, the earliest flagged `after_quote`), converted at that day's stored EUR/USD
rate (`PricingFx.rates_for(..., fetch=False)`: the newest stored rate within 7 days, never a
provider call while the page waits), and the **gross** margin `(price − cost) / price` — before
freight, customs and transport. The summary adds median, last, min and max margin. `cost`,
`margin` and `costs` are cost fields: a viewer gets none. A day without a stored rate shows no
margin, never a guess. The 43 quote days of Apr–Oct 2026 were filled into hosted
`catalog.fx_rate` from mindicador day by day (its year endpoint failed on 14 Oct).

The panel: the margin first, a chart of one stem per quote from cost to price (sold filled in the
brand teal), a tooltip with how the cost was converted and the margin, click to open the case;
the six newest quotes with «Ver las N», a quote read twice shown once.

Speed: `GET /v2/catalog/products/{id}` now carries the price history with its margins, so the
panel makes one request instead of two in a row; the nine side queries of a product go out in one
psycopg pipeline, and the read-only set-up is one statement. Measured from a workstation against
hosted (70 ms per round trip): the product read went from ~1.1 s to ~0.39 s, and the separate
price-history request (~0.8 s) is gone. No schema change.

### 2.7.116 Exchange rates from the Banco Central, 2026-10-14 — built, deploys on merge; history loaded

The API reads the BDE with its API key token (`ORIGENLAB_V2_BDE_TOKEN`, preferred over user and
password), reads the bank's Latin-1 replies, and has the euro series by default
(`F072.CLP.EUR.N.O.D`). The 2026 history (197 business days each of EUR and USD, 2 Jan – 14 Oct)
was loaded into hosted `catalog.fx_rate` from the BDE on 14 Oct (provider `bde`, new rows only),
after mindicador answered 15 of 43 needed days; every quote of the model checked now has its
margin (§2.7.115). The token on Render is an owner step (`docs/OPERATIONS.md` §14.1).

### 2.7.117 Hoy acts by itself: forwards, body quote numbers, AI-read requests, «Quitar», 2026-10-14 — built, deploys on merge

Owner, 2026-10-14: Hoy was «super messy»; the system should act and a person remove what is wrong.
Worker: the triage stores the quote numbers of the sender's own text as `message_triage`
`quote_refs:v1` (tokens only; a phone-shaped «2299-1234» refused by year), and `quote-refs-backfill`
reads past mail (dry run by default). Rules (`mail_rules.py`, DOMAIN.md «Who acts»): R2 files by a
body number and keeps a won case's later mail without reopening it; R7 opens a request on the AI
reading's `quote_request` and never on a supplier's offer; **R11** joins a forwarded request to its
customer's open case or opens one; the timer now runs R1, R2, R7, R9, R11. API:
`GET /v2/workspace/automatic-actions` and `POST /v2/commands/remove-automatic-action` (sales or
admin, automatic actions only), both on the proxy allowlist. Hoy: «Te toca responder», «Hecho
automáticamente» (Abrir caso · Quitar), the forwards still waiting, due tasks, and «Más» folded
(follow-ups, emails with no case, people, cases to decide, institutions, blocked). No schema change.
Hosted the same day, through the CRM commands: `ohaus.com`, `soviquim.cl` and `hielscher.com` added
to OHAUS, Soviquim and Hielscher Ultrasonics (the last also classified `manufacturer`), so their
mail is supplier mail. Dry run on hosted before merge: 7 automatic actions (3 bank-transfer emails
filed on their won case, 4 forwards placed), 8 supplier offers no longer opening a request.

### 2.7.118 The context matcher places emails on their case, and learns from «Quitar», 2026-10-15 — built, deploys on merge

The context matcher built 2026-10-10 on `feat/context-matcher-v1` (never merged; spec private at
`~/data/origenlab-v2-migration/specs/2026-10-10-context-matcher-design.md`) is now on main: the
worker's triage stores `message_triage` `case_match:v1` for mail a person wrote about a deal —
layer 1 scores the cases by the quote number on the PDF, the thread, a quote number in the text,
the institution's domain or name, the product and the people; when layer 1 is not sure and the
model is on, Claude picks among the cases it is shown, with the operators' earlier links and
rejections as examples. «Correos sin caso» shows «Parece del caso X» with «Vincular» / «No es de
este caso». New on 2026-10-15: rule **R12** (automatic) links the email when the matcher is sure
(layer 1 «exact» or a «high» pick), before R7 would open a second request — also a supplier's mail
about a client's deal; a won case keeps it closed; it lists on Hoy with «Quitar». The emails people
take off a case now count as rejections in the examples the AI reads. No schema change.

### 2.7.119 Case memory: running machine summary per case, institution and person, 2026-10-15 — built, deploys on merge

Migration `20261015120000` (`supabase/migrations/20261015120000_slice7_case_memory.sql`) adds
`evidence.subject_memory`: an append-only table the triage worker writes after each HUMAN_CLASSES
email it processes. Each row holds a `memory` JSONB with `summary` (≤ 600 chars in Spanish),
`facts` (≤ 15 key-value facts keyed `quote|amount|product|person|institution|date|other`),
`open_questions` (≤ 3), and `next_step` (action, why, confidence). Organisation and person
memories carry `summary` + `facts` only (no next_step). Version numbers are per-subject; the table
is append-only (no UPDATE/DELETE grants; triggers as defence-in-depth).

The writer runs in the triage worker after `record_forward_reading` and `record_quote_refs`,
isolated in `try/except` so a failure never breaks the triage. It is gated behind
`ORIGENLAB_WORKER_MEMORY_ENABLED=true` (default off). The model defaults to `claude-haiku-5-5`
and can be overridden via `ORIGENLAB_WORKER_MEMORY_MODEL`. A backfill CLI
(`origenlab-worker memory-backfill --since-days 60 [--apply]`) writes the first memory for past
emails already matched to a case.

The context matcher's `dossier()` now includes `memory_summary` and `memory_next_step` when
available. The triage model's `user_content()` includes `memory_summary` and `memory_next_step`
for each linked case.

API: `GET /v2/workspace/cases/{id}/memory` (through `ContactRedactingRoute`) returns the latest
case, institution and person memories. Proxy allowlist: `/v2/workspace/cases/…/memory`. Dashboard:
«Lo que sabemos» section in the OpportunityDrawer (hidden when no memory exists). pgTAP: 17 tests
in `supabase/tests/096_case_memory.sql`.

The release on merge applies the migration to hosted. The writer stays off until
`ORIGENLAB_WORKER_MEMORY_ENABLED=true` is set on the Render worker.

### 2.7.120 A sent quote lands on the client's open request (R13); R3/R4 automatic, 2026-10-15 — built, deploys on merge

Found on 15 Oct: quotes sent on a new thread never reached the request the client had made (a
client's sent quote would have opened a second case), and quotes to new clients waited for an
admin's «Aplicar». Rule **R13** files such a quote on
the client's open request without a quote — matched by a recipient who wrote on it or asked through
a forwarded request, the institution's domain, or the institution the PDF names — walks it to
«Enviada» and records the quote; two candidates stay a proposal. R3 and R4 are now automatic, so a
quote to a client with no open request opens its case by itself; all three undo with «Quitar». The
snapshot counts a forwarded request's customer as a person of its case. No schema change.

### 2.7.121 The Cotizador's quote authoring (PR A: engine, commands, reads), 2026-10-15 — built, deploys on merge; inactive until `quote_number_seed` is set

Spec `~/data/origenlab-v2-migration/specs/2026-10-15-cotizador-design.md` (private); the design is in
[`DOMAIN.md`](DOMAIN.md) §7.9 and [`WORKFLOWS.md`](WORKFLOWS.md) §W3a–W3b. What exists now is the
server half only: **no dashboard editor and no quote PDF yet** (later PRs), so nothing is visible to
an operator beyond the API and the Worker's allowlist.

- **Schema.** Migration `20261016100000_slice7_quote_authoring.sql`, applied to production by the
  release on merge: columns on `crm.quote_line` and `crm.quote_revision`, two triggers
  (`quote_line_authored_draft_only`, `quote_revision_authored_frozen`), `origenlab_api` DELETE on
  quote lines, seven new `catalog.cost_parameter` keys, events `quote_revision.opened` and
  `quote_revision.draft_saved`. **No new table (54).** Inventory: migrations **60**, policies
  **189**, foreign keys **172** (139 unconditionally covered), functions **37**. The Slice 0 evidence
  set moved in the same change (gap checker, pgTAP, replay, chain and clean-room expectations).
- **Engine.** `apps/api/src/origenlab_api/quoting/` — the Plantilla Maestra's pricing in `Decimal`
  (unit cost rounded to the peso before expenses), the profile and DHL-calculation import expenses,
  the packaged DHL Express Chile 2026 rate card. A parity runner against the owner's costing sheets
  (private, not committed) matched 75 of 75 checked values across three sheets.
- **API.** `apps/api/src/origenlab_api/v2/quotes/`: `GET /v2/quotes/numbering`, `GET /v2/quotes/{id}`,
  `POST /v2/quotes/preview` (a read), and the commands `create-quote`, `save-quote-draft`,
  `approve-quote-revision`, `void-quote-revision`, `create-quote-revision`; all behind
  `ORIGENLAB_V2_QUOTING_ENABLED` (already `true` on Render), sales and admin only. Proxy: those exact
  paths; `save-quote-draft` and the preview accept 256 KiB bodies.
- **The recipient comes from the case's emails.** The hosted CRM has no
  `crm.opportunity_participant` row (no V2 command wrote one), so approval's `no_recipient` would
  refuse every real case. The workspace now lists `case.contacts` — the participants, then the
  people on the case's emails and forwarded requests, without OrigenLab or Labdelivery addresses —
  and `save-quote-draft` takes `recipient` (`{participant_id}`, `{person_id}`, `{contact_point_id}`
  or `{email, name?}`): a pick not yet on the case becomes a confirmed `quote_recipient` participant
  (event `participant.added`), and an address the CRM lacks an `unattributed` contact point (event
  `contact_point.created`), in the save's transaction. This is the first V2 command that writes
  participants. No schema change: `origenlab_api` already had INSERT on both tables.
  [`WORKFLOWS.md`](WORKFLOWS.md) §W3-Cotizador.
- **Behaviour changes to know.** `set-cost-parameter` now takes `value_text` for the five printed
  `quote_default_*` texts (a numeric body now hashes with `value_text: null`, so its request digest changes; this matters only for a retry of an old `Idempotency-Key` across the deploy). Approval writes a party snapshot
  of a different shape than the one WORKFLOWS §W3b first proposed; §W3b was amended to the written
  shape. `set-cost-parameter` (and the cost-parameter importer, which validates with it) refuses a
  `quote_number_seed` that is not a whole number 1–99999 and a `quote_default_validity_days` that
  is not a whole number 1–365. The approval response carries `negotiated_costs_skipped`.
- **Not active until an owner step.** `create-quote` is refused `quote_numbering_not_configured`
  until `quote_number_seed` is set to the number Hoy shows as «Siguiente», and the profile amounts
  and default texts are best set at the same time: [`OPERATIONS.md`](OPERATIONS.md) §14.8.
- **Known and deferred.** Approving a copied revision without saving first freezes the source
  revision's exchange-rate snapshots; the workspace flags it (`needs_saving`, with the `saved`
  figures approval would freeze), and the editor must show it. DHL-calculation duties treat every
  EU or US origin as qualifying for the free-trade rate (`duty_rate_fta`), without checking the
  certificate of origin, so the duty may be underestimated. `dhl_calc` charges the strictest
  dangerous-goods tier and uses DHL's published 70 kg / 120 cm surcharge thresholds, until a product
  carries a tier. A real Excel quote more than 200 serials ahead of everything else is not counted by
  the numbering (the unique key still refuses a duplicate). Logistics lines (DHL labels carry supplier
  names) must never be printed on the client's PDF. Carried to the next PRs (editor, PDF and send):
  an Excel PDF sent for a number the Cotizador minted can land on the authored quote through R1/R13;
  the mail rules' "the case has a quote" predicates also see authored drafts and voided revisions;
  the existing case lists show a draft's `grand_total` and need a status filter; `create-quote` does
  not move the case to «quoting».

### 2.7.122 The Cotizador screen (PR B: the dashboard editor), 2026-10-16 — built, deploys with the dashboard on merge; inactive in practice until `quote_number_seed` is set

Stacked on PR #761 (§2.7.121): it needs those endpoints, so it merges after it. Dashboard, plus one
API read change (the workspace's recipient candidates leave out automatic senders) — no schema change.
The screen is `#/crm/cotizar/<quoteId>` (now a tab at `#/crm/cotizador/<quoteId>`, §2.7.123) ([`WORKFLOWS.md`](WORKFLOWS.md) §W3-Cotizador, «The screen»):
Cliente (recipient picker), Productos (catalog search, kit and mention chips, free lines with a cost,
markup or typed price, each line in two tiers so it fits beside the rail), Gastos de importación
(Perfil, DHL, Manual), Condiciones, and a right rail with totals, the margin band, the exchange-rate
override, Avisos and the price history (client types in Spanish). The draft autosaves 1.2 s after an
edit and the preview recomputes 300 ms after one; a change by someone else shows «Otra persona cambió
este borrador» with «Ver cambios», and a draft approved or voided in another tab freezes the same way
(«Ver estado actual»); an incomplete draft is held back with the reason in Spanish; every refusal is
said in Spanish by its code; approve saves first and lists its reasons; void takes a reason and saves
the draft's pending edits first; an approved or sent revision opens read-only, with «Nueva revisión».
«Vista cliente» is in the header, for the draft and an issued revision. Recipient candidates leave out
automatic senders (no-reply, mailer-daemon, notifications, `*.google.com`).

- **Entry points.** Hoy «Cotizar» / «Continuar cotización», the Solicitud card, and the case drawer
  («Nueva cotización», «Abrir borrador», «Abrir en Cotizador»). A case whose latest revision is an
  authored draft still counts as «por cotizar». Each click creates one quote, even after a network
  error. Hoy's number box now reads `GET /v2/quotes/numbering` instead of guessing.
- **Not active until an owner step.** `create-quote` still refuses `quote_numbering_not_configured`
  until `quote_number_seed` is set ([`OPERATIONS.md`](OPERATIONS.md) §14.8); until then Cotizar shows
  that refusal in plain Spanish and creates nothing.
- **Not built yet.** The quote PDF and the send (PR C): an approved quote stays «Aprobada» and nothing
  is emailed. Vista cliente is a screen view, not the PDF.
- **Known and deferred.** Row-menu keyboard handling and focus management in the product table;
  a network failure after a server commit can show a false conflict on the next save; the list and
  length caps the API enforces are not mirrored in the editor (a refusal shows instead); a case whose
  newest revision is void is not «por cotizar».

### 2.7.123 The Cotizador module: many quotes open as tabs, quotes started from a client, company preferences, 2026-10-16 — built, deploys on merge; inactive in practice until `quote_number_seed` is set

API and dashboard; no schema change.
([`WORKFLOWS.md`](WORKFLOWS.md) §W3-Cotizador, «The module» and «A quote can start from a client»)

- **API.**
  - `GET /v2/quotes` is the list (status filter, search, cursor, counts).
  - `GET /v2/quotes/clients` gives client suggestions with their open cases.
  - The quote workspace gains `params` (default markups, margin band, price deviation).
  - `create-quote` accepts a `client` instead of a case: it uses the client's single open case, refuses `case_choice_needed` with the candidates when there are several, or opens a case (`stage = lead`, no origin document) when there are none. A new institution is registered as `register-organization` registers one.
  - `quote_number_seed` is settable by admin only (`403 role_may_not_set_numbering`).
  - A command refusal may carry `details` (the candidates), in the error envelope's `details`.
  - The list fills a historical quote's products and neto from its read PDF (`amount_source: "pdf_read"`, the case panel's reading, now `v2/quote_documents.py`), and its search matches the read lines.
  - An address held with no person and no institution is the client of the one institution its domain names, in the suggestions and in `create-quote` alike (one SQL fragment). Removed domains are never shown or searched. `create-quote` answers the `organization_name` it used.
  - The two GETs are on the dashboard Worker's allowlist.
- **Dashboard.**
  - «Cotizador» is in the menu between Oportunidades and Organizaciones.
  - Up to eight quotes are open as tabs. They persist per operator on this computer, and each keeps its own autosave.
  - The «Todas» list.
  - The «Nueva cotización» dialog.
  - The «Preferencias» page saves each value with a reason and shows its history.
  - `#/crm/cotizar/<id>` redirects to `#/crm/cotizador/<id>`.
- **Fixed on the way.** The dashboard read a quote refusal's generic code (`conflict`,
  `validation_error`) instead of the command's own code, which sits in the envelope's
  `details.code`. That broke three things, all present since §2.7.122:
  - The specific Spanish refusal texts never showed.
  - «Cotizar» did not recognise `command_in_progress`, so a click during a create still running could make a second quote.
  - The list's `bad_cursor` notice never showed.
  - The same bug, outside the Cotizador, was live too: the case commands (`caseRefusalCode`, so `isStaleRefusal` never matched and a stale drawer did not refetch), CRM authoring and marketing (`refusalOf`) read only the old `{detail}` shape. All of them now use one parser (`apps/dashboard/src/api/refusalEnvelope.ts`).
- **Not built.**
  - The quote PDF and the send (PR C) are not built.
  - There is no `default_markup_other` key: an «Otro» item uses the accessory markup, and Preferencias says so.
  - The price-deviation warning: `price_deviation_warn_pct` is stored and read into the workspace's `params`, but nothing compares a price with its history yet. Preferencias says so.

### 2.7.124 Two quotes sent together on a case's thread are both recorded (R1), 2026-10-16 — built, deploys on merge

Rule R1 used to stop at «un humano decide» when one reply on a case's own Gmail thread carried two
or more new CN PDFs (the 2026-10-16 case: alternatives T18 and T10 sent together), so neither
reached «Enviadas». Now each new number becomes its own sent quote on that case in one automatic
action ([`DOMAIN.md`](DOMAIN.md) §3.6.6); numbers already on the case are only linked; a doubtful
number (its correlative under another year), no requesting institution, or a closed/won stage
still leaves the email to a person. «Deshacer» voids every quote the action recorded. API only.


### 2.7.125 Catálogo: filter «Con ficha», 2026-10-16 — built, deploys on merge

A «Con ficha» toggle next to the Catálogo filters keeps only products with an active data sheet
(not a price list, not a retired sheet). It filters the cost-free index in the browser on the
`has_datasheet` flag the index already carried, narrows the supplier and kind counts, lives in the
address as `&ficha=1` and is cleared by «Limpiar filtros». Dashboard only: no API or proxy change.

### 2.7.126 Model aliases: a model as printed reaches its catalog product, 2026-10-16 — built, migration not applied

Table #59 `catalog.product_model_alias` (migration `20261016200000`; [`DOMAIN.md`](DOMAIN.md)
§7.10): the other names a product's model is printed under, so a quote line or an email that prints
«XQ100» links to the product stored as the maker's item number. One active alias per maker and key,
never a model of the same maker, retired never deleted (guard trigger
`catalog.product_model_alias_guard`); events `product.model_alias_added/_retired`. Inventory: tables
**55** (`catalog` 8), policies **193**, foreign keys **176** (143 unconditionally covered),
functions **38**, migrations **61**; the Slice 0 evidence set moved in the same change.

- **Reads** follow an active alias wherever a line or text is linked by model key: catalog search,
  product price history / costs / kits, the Cotizador workspace's principal history and kits, the
  documents importer's data sheets, and the worker's mail-triage product candidates. An alias key
  that names two products links neither.
- **Importer** `scripts/catalog/import_model_aliases.py` (`add_model_alias`;
  [`OPERATIONS.md`](OPERATIONS.md) §14.9).
- **Not yet.** The migration is not applied on hosted (it must be, before the API and worker deploy:
  the worker's connection probe now requires SELECT on the table); no alias is loaded. The private
  manifests are prepared outside the repository. The dashboard's in-browser catalog index does not
  carry aliases yet, so typing a printed model in «Catálogo» or the Cotizador's product search still
  finds only the stored model; the server search (`/v2/catalog/products?q=`) does.

### 2.7.127 Products created from sent quotes (importer), 2026-10-16 — built; plan written, not applied

`apps/api/scripts/catalog/import_quote_products.py` ([`OPERATIONS.md`](OPERATIONS.md) §14.3) creates
the catalog products that sent quotes name but the catalog lacked, as the owner approved them on
2026-10-16: their maker organizations (reused when one exists, else kind `manufacturer`), the
`manufacturer` role on every maker and on the existing makers that lacked it, each new maker's
domain (`shared`), and the products (`content_origin = 'import'`, Spanish name, «desde
cotización» as the `product.created` note). No cost is written; model aliases are not part of it.
Two plan actions join the catalog plan model (`add_org_role`, `add_org_domain`; other importers'
plans keep their bytes) and rollback now also removes the roles and domains a manifest wrote. The
hosted apply is the owner's step; until it runs, the catalog has none of these products.

### 2.7.128 Quote reader v2: the old template footer no longer blocks reading; a neto with its optional lines is accepted, 2026-10-16 — built, deploys on merge

Owner decisions 2026-10-16, for the worker's quote reader (`READER_VERSION` is now
`quote_read:v2`) and the quote-history importer:

- **Labdelivery refuses only as the issuer.** OrigenLab's old quote template printed a page footer
  (the old address line, then `Celular: (09) … www.labdelivery.cl`) under an OrigenLab header, and
  any mention of Labdelivery refused the whole document. `keys.labdelivery_issued(text)` now exempts
  that footer only when it is the one real footer — pinned by the SHA-256 of its folded text, the
  plaintext kept out of the public repository — and the page names OrigenLab elsewhere; Labdelivery
  anywhere else (letterhead, issuer, title, signature, a line, a variant or letter-spaced
  spelling, another address in the footer's shape) still refuses. The reader
  (`not_origenlab_quote`) and the importer (`skipped_labdelivery_letterhead`) both use it, and the
  model's prompt counts only the letterhead, issuer or signature and is told never to copy the
  footer.
- **Nothing of the footer is stored.** Every string written — each line's and the document
  payload's — is refused by `is_labdelivery`, and a document whose strings repeat the footer's
  address, street, an address segment with a digit (street and number, office), phone label, a 6+-digit number of it or any 7 consecutive digits of one is refused too: the reader's new code
  `footer_copied` («El lector copió el pie de página antiguo en una línea»), the importer's
  `skipped_footer_copied`.
- **A neto that includes the optional lines is accepted.** When the printed neto is more than 1 %
  off the non-optional lines but within 1 % of all lines with the optional ones, the document is
  read with the neto as printed and its `quote_document` payload carries `net_includes_optional:
  true` (absent otherwise); the run's log line counts them, `quote_contents` returns it and the
  case panel notes «El neto impreso incluye ítems opcionales» under the Neto. Any other mismatch is
  still `net_mismatch`.
- **Re-read.** The candidates key a refusal by reader version and ISO week. The first v2 runs
  re-read v1's `not_origenlab_quote` and `net_mismatch` refusals (at most `--limit` per run); v1's
  other refusals keep blocking for their weeks, so no tokens go to re-reading what v2 did not
  change. Operaciones counts pending PDFs the same way, and «Cotizaciones no leídas» lists only
  the newest refusal of each PDF. No migration or new route; worker, importer script, API reads and
  the case panel.

### 2.7.129 A client connected to an institution while quoting; «Asignar institución» creates one, 2026-10-16 — built, deploys on merge

- **«Nueva cotización»**: for a person or an address the CRM knows no institution of, an optional
  «Institución» field (an institution held, or «+ Nueva institución «texto»» with its kind; «sin
  institución la cotización no se puede aprobar»). `create-quote`'s `client.institution`
  (`WORKFLOWS.md` §3 quotes) connects it: the case's requesting institution (a new case, or an
  existing one without one), the person's affiliation. A bare address (no person) is not
  attributed — no operator command does that outside evidence review — only its case; a new
  institution takes the address's domain.
- **«Asignar institución solicitante»** (`AssignInstitution`, the case actions; the Cotizador's
  «Agregar institución» once #770 merges) offers «+ Crear institución «texto»» after a search:
  `add-case-organization` with `new_organization` (same name or domain → that one; several →
  `organization_name_ambiguous`, listed to choose from).
- No migration, no new route, no proxy change (same paths). Tests: API DB tests in
  `test_v2_quote_commands.py` and `test_v2_case_command_boundary.py`; vitest
  `NewQuoteDialog.test.tsx`, `AssignInstitution.test.tsx`.

### 2.7.130 Cotizador «Vista previa PDF»: the client's quote, printed by the browser, 2026-10-17 — built, deploys with the dashboard on merge

- **«Vista previa PDF»** is on for a draft, an approved and a sent revision (`QuotePrintView`, a
  full-screen A4 document over the page). «Imprimir / Guardar PDF» calls `window.print()`; the
  print stylesheet (`quotePrint.css`) hides the app and prints the document only, its table header
  repeating on each page. The window title («Cotización CN01272 — <institución>») names the file.
- **What it prints** (`printableQuote`): number, date, «Válida hasta», institution and RUT,
  recipient, the item lines (description, model from the catalog index when known, note,
  quantity, unit price, total), Neto / IVA / Total as the API stored or previewed them, the
  conditions that are set, «Aprobada por», and contacto@origenlab.cl · www.origenlab.cl. An issued
  revision prints its stored lines and `party_snapshot`; a draft prints the live editor, marked
  «BORRADOR», and cannot be printed while its prices are missing or updating. Import-expense
  (`logistics`) lines, costs, recargos, margins, suppliers and exchange rates are never copied
  into the view.
- No API change, no migration, no proxy change. Tests: vitest `QuotePrintView.test.tsx`,
  `CotizadorPage.test.tsx`.

### 2.7.131 Selective production release: skip what a change does not need, 2026-10-17 — built, takes effect on the first release after merge

- **Trigger.** `production-db-migrations` starts on a successful main-push `secret-scan` run
  (was: `supabase`, ~3.5 min). `release_context.py` refuses any other trigger workflow.
- **CI.** `check_release_ci.py` waits for every listed workflow at the SHA, now including `lint`
  and `repo-scripts`; `supabase` binds only when the release plan says so (something it tests
  changed since what is live, an unknown base, or a pending migration). A finished red run of any
  listed workflow, `supabase` included, refuses.
- **Backup and migrate** run only when `hosted_migrations.py plan` (now also in release
  preflight, read-only) reports a pending migration; exactly `0` skips both.
- **Deploy** (`render_release.py plan|release`, `release_plan.py`): only the services whose code
  changed since the commit each one runs; unknown paths and pending migrations deploy all three;
  a service whose live commit is unknown or not an ancestor deploys itself and binds `supabase`. Order and the stale-main check before each service are
  unchanged; the API's `/health` is checked at its expected commit before the dashboard.
- **Postflight** checks each service at its expected commit (kept services at their live one)
  and the worker at the revision it runs. **`release_result.py`** replaces the inline result
  check: a skip is success only with exactly `0` pending.
- **Expected:** a dashboard-only change live about 4-5 min after merge (was ~10); with a
  migration about 10-11 min. No migration, no service or Render setting changed; the
  `supabase` workflow still runs on every main push. Tests: 55 offline release tests
  (`SelectiveRelease`). Details: release runbook → *Selective release*.

### 2.7.132 Cotizador «Rentabilidad» and «Economía de la cotización», 2026-10-17 — built, deploys on merge

- **API** `GET /v2/quotes/economics?from=YYYY-MM&to=YYYY-MM` (this year by default, at most 36
  months; sales and admin, a viewer gets 403 `role_may_not_view_costs`; a bad period is 422
  `bad_period`): every authored quote at its current revision, void excluded, with sale, product
  cost, import costs split freight / agent / customs / transport / other, gross margin $ and %,
  `cost_coverage` (`full` | `partial` | `none`), and the totals by month and for the range —
  approved and sent only (drafts listed and counted apart), the margin over full-coverage quotes
  only, with their share of the sale. Priced by the engine from the stored lines as approval
  prices them (`v2/quotes/economics.py`); three statements in one pipeline. Imported quotes are
  not included (owner, 2026-10-17: their import costs were never recorded).
- The engine's `computed` block (preview, save, workspace) and the workspace's `current` now carry
  `economics` for the rail's card. Proxy: `/v2/quotes/economics` allowed, GET only.
- **Dashboard:** the Cotizador's fixed pane `#/crm/cotizador/rentabilidad` (period filter,
  summary with the coverage note, by month, the quotes with their split and «Abrir cotización»)
  and the rail card «Economía de la cotización», never printed.
- No migration. Tests: API `test_v2_quote_economics.py`, `test_v2_quote_routes.py`; proxy
  `allowlist.test.ts`; vitest `RentabilidadPage.test.tsx`, `SummaryRail.test.tsx`,
  `QuotePrintView.test.tsx`, `CotizadorModulePage.test.tsx`, `QuoteListPane.test.tsx`,
  `quoteApi.test.ts`, `shellRoute.test.ts`.

### 2.7.133 Web quote form, 2026-10-18 — built, not deployed

| App | What |
|---|---|
| Worker | TRIAGE_VERSION 4: `x-origenlab-origin` in MACHINE_HEADERS, `reply_to` in MailText, web-quote rule (before outbound check) returns `quote_request` with Reply-To domain as sender_domain |
| API | `build_web_quote_message` (Reply-To, X-OrigenLab-Origin header, plain text); `v2/web_quote_form.py` (WebQuoteFormRequest, WebQuoteFormCaps 40/24h + 3/24h, 10-min TTL idempotency set, `POST /v2/web/quote-request`); global cap re-seeded at start-up from the form mails captured in `comms.message` (no V1 SQLite); mail-rules snapshot reads the web-form marker (triage reason `header:x-origenlab-origin`) and the Reply-To participant, so a captured form mail is planned as R7 from the enquirer; `ORIGENLAB_V2_WEB_QUOTE_FORM_ENABLED`; uses same `ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE` as «Enviar prueba» |
| Proxy | `web_form.ts`: `handleWebFormRequest` (Origin=origenlab.cl, honeypot `_trap`, timing < 3 s, body ≤ 16 kB, rate-limit binding `WEB_FORM_RATE_LIMIT` 5/10 min); new route `origenlab.cl/api/web/*` in `wrangler.toml` |
| Web | `/contacto/` redesigned with form as section 01; `/contacto/enviado/` noindex success page; `/privacidad/` updated with form data paragraph; `technicalProviders` adds Google Workspace, Render, Supabase |
| Auth | Same token file as «Enviar prueba» (`ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE`). Request it with `scripts/gmail_send_authorize.py` (default `--scopes web-form` = gmail.compose + gmail.settings.basic, also serves «Enviar prueba» and future projects; `--scopes send` keeps the old gmail.send-only token). Token was never provisioned in production (see §2.7.47). Owner must create it before enabling. |
| Not done | not deployed, token not created. To ship: merge → apply migrations first (this change adds none) → the release on merge deploys the API and the triage worker (TRIAGE_VERSION 4) → deploy the dashboard-proxy Worker → `make smoke` → the owner runs `gmail_send_authorize.py`, uploads the token as the Render secret file and sets `ORIGENLAB_V2_WEB_QUOTE_FORM_ENABLED=true` → upload the web `dist/` to cPanel |

### 2.7.136 A web-form case reads the visitor as its client, 2026-10-19 — built, deploys on merge

- Seen on the first real form case: «Sin contacto», its email «Interno · Enviado por OrigenLab», and
  the Cotizador «Para» offered nobody — the case reads took the From (contacto@) as the sender. Now
  the reads use R7's predicate (From on an own domain + triage reason `header:x-origenlab-origin` +
  a `reply_to` participant; `mail_rules.WEB_FORM_REPLY_TO_SQL` / `web_form_enquirer`) and treat such
  an email as **received from the Reply-To**: the case's people (`quotes/reads._CASE_PEOPLE_SQL` →
  `case.contacts`), «Correos del caso» (`opportunity_threads` → `case_threads`: «Cliente»,
  received), «último contacto» (`_SQL_PIPELINE_CONTACT` → `last_contacts`, so Hoy's Cotizar/Responder
  wait from it; OrigenLab's own From name is never shown as the client's) and the card's
  «Contacto» (new source `web_form` when the case has no participant and no sent quote). Read side
  only: no migration, the capture and R7 unchanged.
  The visitor stays the card's contact when they later reply from their own address; contacto@'s
  answer on the thread stays outbound (the case reads as answered on Hoy).
- R7's title for a web-form mail no longer repeats the prefix («Solicitud de cotización — sitio
  web — <Institución>»); other cases' titles are unchanged, and existing titles are not rewritten.
- Dashboard: the case's «No es una solicitud» is a one-step confirmation («Cerrar» / «Cancelar»)
  sending the same `advance-case-stage` → `abandoned`, close reason «No es una solicitud», as the
  «Perdida» form does for that chip; «Cambiar estado» → «Perdida» keeps the full form.
- Tests: API `test_v2_mail_rules.py`, `test_v2_crm_workspace.py`, `test_v2_quote_reads.py`,
  `test_v2_mail_rules_db.py`; vitest `CaseActions.test.tsx`.

### 2.7.134 Cotizador «Datos del cliente» on the draft, 2026-10-19 — built, deploys on merge

- Owner, 2026-10-19: the client's name and details could not be changed on a quote, so revisions
  were approved again and again hoping they would show. Now the draft's «Cliente» card has
  «Datos del cliente» — nombre del contacto, cargo, unidad / depto., teléfono, institución (razón
  social), RUT, dirección, ciudad / región — each blank field showing (as placeholder) and printing
  what the CRM knows. Saved with the draft (`QuoteDraft.client`), copied into a new revision,
  frozen into `party_snapshot` at approval (institution name and RUT, recipient name, and a new
  `client` block); the PDF prints them, and prints an address-only contact's address once.
- **Schema.** Migration `20261019100000_slice7_quote_client_details.sql`: nullable
  `crm.quote_revision.client_details` (JSON object). No table, policy, function or foreign key;
  the existing `quote_revision_authored_frozen` guard freezes it once issued. Migrations **62**.
- Nothing is written to `crm.person` / `crm.organization` from here: that stays the Cotizador
  client-data project (spec 2026-10-18 §2).
- «Vista previa PDF» toolbar says how to keep the browser's own date/URL line off the PDF
  (margins «Predeterminado», «Encabezados y pies de página» unticked): `@page { margin: 0 }`
  cannot win once the print dialog's margins were changed.
- Tests: API `test_v2_quote_commands.py`; pgTAP `097_quote_authoring.sql`; vitest
  `ClientSection.test.tsx`, `QuotePrintView.test.tsx`.

### 2.7.135 The system makes the obvious decisions itself (R14, R15, answered mail), 2026-10-19 — built, deploys on merge

Owner, 2026-10-19 (spec `2026-10-19-auto-obvious-decisions-design.md`, items 1, 3 and 2a): Operaciones
→ «Por decidir» asked people to click what the system already knew. Two new rules run on the same
automatic timer, switch, receipts (`mail-rule:<subject>:<rule>`, `actor_kind = 'worker'`) and
«Deshacer» as R1–R13, and list in «Lo que hizo el sistema». Nothing marks a case lost or closes one.

- **R14 — a sent quote already on its case.** contacto@'s outbound email, sent within the last 60
  days (Operaciones' own window: a backfilled old email never records an old quote or moves a stage),
  linked to exactly one open case (by an earlier rule or a person) with exactly one CN PDF whose number no case records — the
  «Cotizaciones enviadas sin registrar» rows. R1's gates: the case has a requesting institution, an
  open stage, and the correlative is not the CRM's under another year; then the case walks to
  «Enviada» and `record_historical_quotation` records the quote (no link: the email is there). It is
  the one rule the planner runs on an email another rule already acted on, once per email (undone or
  not). «Deshacer» = R1's: voids the revision, walks the stage back, leaves the email linked. Left to
  a person: no institution, another-year correlative, two new PDFs, a closed case.
- **R15 — confirm an institution a sent quote settled.** An institution «por confirmar» that R4, R10
  or «Completar con la cotización» created (read from the receipt of its `organization.created`
  event; never R11's forward company, a person or an import), not a supplier, no free-mail domain,
  with a quote on its case recorded ≥ 3 days ago and still sent (a case an undo discarded does not
  count). One named after its domain is renamed first (`update-organization`) to the institution
  segment of its quote PDFs (`institution_segment`) — only when exactly one distinct segment exists,
  it says *which* institution (two meaningful words, one not a kind/legal-form marker: never
  «Laboratorio», «Laboratorio Chile», «Centro (CEN)») and no other institution has that name;
  otherwise it stays for a person. Then `confirm-organization-record`. Keyed by the institution id.
  **Machine confirmation is visible as such:** run as the system, the confirm leaves
  `confirmed_by_operator_id` empty (the existing check allows it), its `organization.confirmed` event
  is `actor_kind = 'worker'` with `confirmed_from: mail_rule`, and the institution card
  (`organization_authoring.confirmed_by_system`) says «Confirmada por el sistema (cotización
  enviada)» instead of a person's name. Not listed on «Hoy» (Operaciones only). «Deshacer» runs a
  new server-side handler `unconfirm-organization-record` through the email-rules undo — **no route,
  not on the proxy**: back to `machine_proposed`, the old name while the rule's name still stands,
  one `organization.updated` event; already «por confirmar» → nothing.
- **Kill flags.** `ORIGENLAB_V2_AUTO_RULE_R14_ENABLED` and `ORIGENLAB_V2_AUTO_RULE_R15_ENABLED`,
  default **false**: the first deploy changes nothing; the owner reads R14/R15 in the «Aplicar»
  preview, then sets a flag true on the API and the timer runs that rule while the switch is on.
- **Answered mail leaves «Correos sin caso» (2a, read side only).** `GET /v2/workspace/triage-readings`
  adds `answered_at` (contacto@'s first later outbound message on the same thread and mailbox, to a
  recipient outside origenlab.cl who is not a registered supplier — forwarding the request to a
  supplier for a price is not an answer); the
  dashboard does not ask about an email older than 14 days that has one, and counts it under «No se
  muestran» as «ya respondido». Nothing is written. Item 2b (auto-link on matcher «propose») and item 4
  (people) are not built.
- No migration: `organization.updated` / `organization.confirmed` are existing event types and
  `machine_proposed` is allowed by the existing check. Tests: API `test_v2_mail_rules_obvious.py`,
  `test_v2_mail_rules_obvious_db.py`, `test_v2_mail_rules_auto.py`, `test_v2_crm_authoring.py`,
  `test_v2_triage_review.py`; vitest `operations.test.ts`, `triage.test.ts`,
  `OperacionesPage.test.tsx`, `webSuggestions.test.tsx`.

### 2.7.137 Cotizador: what each line costs against what it sells for; expense types chosen, not guessed; «DHL calculado» withdrawn, 2026-10-19 — built, deploys on merge

Owner, 2026-10-19 (mockup «Lo que cuesta y lo que se cobra»): on a real quote the operator typed
manual expenses with free names (a courier and a person), and «Economía de la cotización» put all of
them in «Otros gastos» because the split guessed from the name. «DHL calculado» never worked: catalog
products carry no weight or origin and no DHL rate parameters are loaded, so it always fell back to
the profile.

- **Cost strip under each line** (editor and the approved revision's «Productos»): «Me cuesta» (the
  line's cost in pesos × quantity plus the import expenses the engine allocated to it, with «equipo
  $ + gastos $») → the margin pill and «gano $» → «Le cobro (neto)» with «+ IVA =». Every number is
  the API's: `computed.line_economics` (preview, save, workspace draft) and, for an issued authored
  revision, `current.line_economics` priced from the stored lines as approval froze them
  (`v2/quotes/economics.line_economics`; null for an imported quote). The pill is green inside the
  Preferencias margin range, amber outside, red at a loss. Same audience as the rest of the
  Cotizador (sales and admin; the API refuses a viewer), hidden in «Vista cliente», never in
  `printableQuote` or the print view (tested).
  No false margin: a line with a cost and no price shows «Falta precio» and its own cost only (no
  share of the expenses, as «Economía» counts only priced lines); import expenses with no principal
  line (`principal_missing`) give no line a margin (`expenses_unallocated`) and the section says
  once «Elige el equipo principal para repartir los gastos de importación». Money in whole pesos,
  the margin to four decimals, server-side.
- **Expense «Tipo».** A manual expense is now Tipo (Flete, Agente de aduana, Derechos y aduana,
  Transporte local, Otro) + optional Detalle + amount. **No migration:** the type is stored in the
  label as «Tipo — Detalle» (or the type alone), and `quoting.expenses.expense_category` reads that
  type first, whatever the detail says. An older free label opens with the type the guesser reads in
  it (a dashboard port, `expenseKind.ts`, checked against the API with the same vectors,
  `expenseKindVectors.json`) and is kept as typed until the row is changed. A detail too long to fit
  after the type (an old 101–120-character label) is cut with «…» so the row still saves. The guesser now also
  reads «dhl», «envío/envio», «fedex», «ups», «courier» as freight (whole words for the short ones;
  «Despacho DHL» stays the agent's).
- **«DHL calculado» withdrawn from the editor** (only Perfil and Manual). The API still accepts,
  stores and re-prices `expense_method = 'dhl_calc'`. A draft saved with it opens unchanged — its
  lines read-only as computed, a note saying the method is no longer offered — and nothing changes
  until the operator picks Perfil or «Editar montos» (which copies its lines into Manual, typed by
  their names). Chosen over converting on open: opening a quote writes nothing.
- Existing approved quotes are not re-split: their stored labels are read with the extended guesser,
  so a courier name already moves from «Otros gastos» to «Flete»; a person's name stays «Otros»
  until a new revision chooses its type.
- No migration, no proxy change. Tests: API `test_v2_quote_economics.py`; vitest
  `expenseKind.test.ts`, `CostStrip.test.tsx`, `ExpensesSection.test.tsx`, `ProductsSection.test.tsx`,
  `QuotePrintView.test.tsx`, `CotizadorPage.test.tsx`, `draftEditor.test.ts`.

### 2.7.138 Cotizador «Gastos ingresados» on an approved or sent quote, 2026-10-19 — built, deploys with the dashboard on merge

Owner, 2026-10-19: an issued quote showed its import costs only as a total and a split by type.
Under «Economía de la cotización», an authored revision that is no longer a draft now lists every
expense line (`line_kind` logistics or fee) as the operator typed it: a «Tipo — Detalle» label shows
the detail with the type as a small tag, an older free label is shown as typed; how its amount was
set (Perfil / Manual / DHL calculado); its amount in whole pesos (a foreign one also in its currency,
put in pesos at the stored rate exactly as the engine prices a frozen revision); and the total, which
equals «Gastos de importación» (the card says so if it ever does not). No API change: the workspace's
`current.lines` already carries the stored expense lines. Internal: same gate as Economía (the
Cotizador's API refuses a viewer; hidden in «Vista cliente»), never in `printableQuote` or the print
view (tested); not shown for an imported quote. Tests: vitest `enteredExpenses.test.ts`,
`SummaryRail.test.tsx`, `QuotePrintView.test.tsx`.

### 2.7.139 Licitaciones fase 1: tender cases, bidder data, anexos, bid preparation, model spend, 2026-10-19 — merged (#802), applied and deployed 2026-10-20, switched on

Spec «Licitaciones con IA» §6.10 (tenders in their own space). Migration `20261020100000`
([`DOMAIN.md`](DOMAIN.md) §7.11): `crm.opportunity.kind` (`sale` | `tender`, existing cases `sale`,
never changed — guard `crm.opportunity_kind_fixed`) and `tender_status`; `crm.opportunity_document`
also holds `tender_annex` (Word/Excel too); tables #60 `crm.bidder_profile` (append-only), #61
`crm.bidder_document` (retired once — guard `crm.bidder_document_retire_once`), #62
`evidence.bid_preparation` (one queued/running row per case and phase), #63
`platform.model_usage` (append-only); the API inserts `procurement.notice`; the worker reads
`catalog.product_document`; six events and the aggregate kinds `bidder` and `notice`. Inventory:
tables **59** (`crm` 24, `evidence` 6, `platform` 7), policies **209**, foreign keys **184** (151
unconditionally covered), functions **40**, migrations **63**; the Slice 0 evidence set moved in the
same change (pgTAP `099_tender_bid_preparation.sql`, 69 assertions).

- **Built on the branch (all of Tasks 1-18):**
  - API (`v2/tenders/`, router `/v2/tenders`): `open-tender-case` (code or link; Mercado Público detail by `ORIGENLAB_V2_CHILECOMPRA_TICKET`; buyer created `machine_proposed`), `set-tender-status` (`adjudicada` refused until phase 3), bidder profile and documents, `attach-tender-annexes` (zip unpacked), `remove-tender-annex` («Quitar anexo», with a reason), `prepare-tender-bid` (phases `read`, `fill`), `create-tender-offer-quote` (a Cotizador draft with CN number). Tenders are excluded from every general sale read (`case_kind.SALE_CASES`); tender quotes stay out of the Cotizador list, Rentabilidad and the price history.
  - Worker (`tender/`, own supervised child of `origenlab-mail-triage`, off unless `ORIGENLAB_WORKER_BID_ENABLED=true`): extraction, streaming read (summary with citations, checklist, items), form filling with `[completar: …]`, offer form (per-line IVA adds up to the quote), spec sheets, one zip. Model spend recorded in `platform.model_usage`, capped at US$60 a month.
  - Dashboard (`crm/tenders/`, `#/crm/licitaciones`): list, «Nueva licitación», the tender page with annexes, reading, outputs and estado, bidder preferences, Hoy «Licitación cierra mañana», spend in «Operaciones». The proxy allowlists the tender paths.
  - Docs: [`WORKFLOWS.md`](WORKFLOWS.md) W7a, [`OPERATIONS.md`](OPERATIONS.md) §8.14.
- **Phase-1 limits.** A tender never sets `closed_at`: `perdida` (like `postulada`) is an estado, not
  a closing, so the case stays open and its Mercado Público code stays reserved to it
  (`open-tender-case` answers `tender_case_exists` for the same code). Closing a tender arrives with
  award capture (phase 3). A tender offer is approved without a recipient («Atención»): its print
  names the bidder and the buying institution.
- **Live, 2026-10-20.** #802 merged (8c02824e); `production-db-migrations` backed up, applied
  `20261020100000` and deployed (release run green). The owner's steps are done: the `crm-documents`
  bucket is at 50 MB **and its allowed types now include Word, Excel and zip** (it allowed only PDF,
  JPEG and PNG); `ORIGENLAB_V2_CHILECOMPRA_TICKET` is on the API; `ORIGENLAB_WORKER_BID_ENABLED=true`
  on `origenlab-mail-triage` (the tender child started, its first sweep ran clean). No tender has
  been prepared yet: memory of RapidOCR on the starter plan and the models' real cost are
  unmeasured. Out of scope in phase 1: API fetch of the bases, relevance, Compra Ágil,
  award capture.
- **Tests.** API `test_v2_tender*`, worker `test_tender_*`, dashboard `crm/tenders/*.test.*`, pgTAP
  `099_tender_bid_preparation.sql`.

### 2.7.140 Licitaciones «En Mercado Público»: open tenders that fit the catalog, 2026-10-20 — merged (#803) and deployed 2026-10-20

Phase 1 opened a tender only from a code the operator already had. «Licitaciones» now opens on
**«En Mercado Público»**: every open Mercado Público tender that fits what OrigenLab sells, with
«Preparar» (opens it through `open-tender-case`, code filled in) or «Abrir» when a case exists.
No schema change: the worker already had `select, insert, update` on `procurement.notice`.

- **Worker** (`tender/discovery.py`, task `tender_discovery` on the tender child, every 15 minutes,
  queueing lock `tender-discovery`): one list call (`licitaciones.json?estado=activas`), a broad title
  gate, upsert into `procurement.notice`, then up to 60 details per run (buyer, items, estimate; the
  list's head goes to `head_history`). A notice seen in the last two days that left a whole list
  (≥ 500 rows) gets `disappeared_at`, never one a case was opened from. Needs the ticket on
  `origenlab-mail-triage` (`ORIGENLAB_V2_CHILECOMPRA_TICKET`, else `CHILECOMPRA_API_TICKET`);
  without it the run logs `no_ticket`. No model is called.
- **Matching** (`origenlab_api.v2.tenders.discovery`, shared by worker and API): tier `catalogo` when
  the title, description or an item names a catalog family (balanzas y humedad, centrífugas,
  homogeneizadores, ultrasonido, agitación y calefacción, osmómetros, electroforesis); tier
  `laboratorio` for other laboratory equipment (UNSPSC segment 41 or named); laboratory services
  («exámenes», reagents) are not shown once the detail says so.
- **API** `GET /v2/tenders/discovered` (any signed-in operator; public notice data only): open notices
  seen in the last two days, catalog first, then by closing. Proxy allowlists the exact path.
- **Dashboard** (`crm/tenders/DiscoveredTenders.tsx`): «En Mercado Público» | «Mis licitaciones»;
  family chips with counts, search, «nueva» for the last 24 h, items that matched, estimate, closing
  in Santiago time, «Ver en Mercado Público».
- **Live.** #803 merged (92237742) and released; the ticket is on `origenlab-mail-triage`. First run
  2026-10-20 14:00 UTC: 4,627 open tenders listed, 90 kept, 60 details read, 0 failed (about four
  minutes, the details one by one); the rest on the next run.
- **Tests.** API `test_v2_tender_discovery*.py`, worker `test_tender_discovery_database.py` and
  `test_task_queue.py`, dashboard `LicitacionesPage.test.tsx`, proxy `allowlist.test.ts`.


### 2.7.141 Licitaciones: the whole catalog in the match, a notice's own page, the dashboard's look, 2026-10-20 — merged (#804) and deployed 2026-10-20

- **The catalog itself** (`CatalogVocabulary` in `origenlab_api.v2.tenders.discovery`): every live
  equipment product's type (first word of `name_es`, or `category_es`; an everyday or vague first word
  takes the next one — «contador de colonias», «procesador ultrasónico»; parts and bundles give none),
  its maker (four letters or more, or an all-capitals three like «IKA») and its model number. Read
  fresh on every sweep and every read; a notice that names one and no curated family is «Otros de
  tu catálogo», and the words that matched come back as `terms` («Calza con»). The worker now also
  needs `select` on `catalog.product` and `crm.organization` (already granted).
- **A notice's page** `GET /v2/tenders/discovered/{notice_id}` (proxy: exact UUID path), dashboard
  `#/crm/licitaciones/mp-<id>`: «Abrir en Mercado Público ↗» first, «Preparar oferta» (sales, admin)
  or «Abrir la licitación»; a stat line; every date of the head on one track (passed filled, next
  marked); every item with its category and what it matched; the head's facts (type, buying unit,
  contract length, delivery address, renewable, subcontracting, the buyer's complaint count).
- **The list** is the dashboard's table (as «Mis licitaciones»), a stat line (catalog, lab, closing
  this week, new today, estimate open), family chips, search, sort; «Mercado Público ↗» on every row;
  rows rise in (`crm-rise`), a pulsing dot within two days, all off under reduced motion.
- **Not yet.** Documents are linked, not downloaded; history, winners and prices («Mercado») are
  the next pieces.
- **Precision fix (same day, after the first live look).** A kindergarten's «material de enseñanza
  no fungible» showed as «Centrífugas» and «Agitadores» (toy science sets) and «Calza con: material ·
  programas». Now an item is read for a match only when its UNSPSC is segment 41 (laboratory) or 42
  (medical equipment), or it has no code; common procurement words («material», «programa», «insumo»,
  …) are never catalog types; a catalog type in the title counts only with laboratory context (a
  curated family, a laboratory item, «laboratorio»); a model or a maker always counts; singular and
  plural are one word in «Calza con».

### 2.7.142 Licitaciones: every new tender read, its match stored, «Nos interesa» / «No es nuestro», 2026-10-20 — built, deploys on merge

Migration `20261020180000` ([`DOMAIN.md`](DOMAIN.md) §7 #64): `procurement.notice.relevance` (the
worker's match, machine evidence) and `crm.notice_review` (a person's verdict, append-only), event
`notice.reviewed`.

- **Layer 1 — nothing missed for its title.** The sweep inserts **every** new open tender, not only
  matching titles, and reads each one's detail (150 per run, matching titles first, then the newest;
  the ~4,600 backlog takes about eight hours, then a few new ones per run). Each read stores its match
  in `relevance` (tier, families, catalog words, matched items, version, the catalog's fingerprint);
  an item coded 41/42 can make a tender ours whatever its title says. A notice read and found not
  ours is not refreshed again unless a person reviewed it. Up to 500 stored matches per run are
  re-made when the match version or the catalog changes (no API call).
- **The list reads the stored match**: no re-matching per page view. A notice not read yet shows on a
  candidate title alone (`detail: false`).
- **Layer 4 — the operator's verdict.** `review-notice` (sales, admin; Idempotency-Key; event
  `notice.reviewed` with the previous verdict): «Nos interesa» pins the notice first and marks it,
  «No es nuestro» moves it to «Descartadas» (where «Sí, nos interesa» brings it back). On the list
  and on the notice's page. The verdicts are the labelled examples the next layers (embeddings, a model
  as judge) will learn from.
- **Not yet.** Deploys on merge with the migration (`production-db-migrations`); nothing manual.

### 2.7.143 A Cotizador quote emailed by contacto@ is «sent» and its case «Enviada» (R16), 2026-10-21 — built, deploys on merge

Rule **R16** (`mail_rules.py`, step `quote_sent.record_authored_quote_sent`), on the automatic timer, no
deploy flag. A quote approved in the Cotizador stayed «aprobada» and its case «Solicitada» after
Tatiana emailed its PDF: the rules recorded only numbers no case held, so a Cotizador number was
skipped, and two PDFs in one email (Ygaero, CN01274A + CN01275) were left to a person.

- **When.** An outbound email from contacto@ carrying a PDF whose file name names the number and
  revision letter (the browser's «Cotización CN01274A — ….pdf» or a hand-renamed «CN01274A-….pdf»;
  the email's year, else the year before; never a «(borrador)» print) of a revision that is
  `authored`, approved, not superseded, not sent, and approved before the email left.
- **Several cases.** Production (read-only, 2026-10-21): Ygaero's reply on the request's thread
  carried 01274A (request case) and 01275 (a second case the Cotizador opened), and R1 left it to a
  person; Golden Omega's reply sat on a lost case's thread. R16 moves each case its PDFs name and links
  the email there; a closed case among them, an email a person put elsewhere, or an open case of its
  thread no PDF names leave it to a person.
- **What.** Each such revision `approved → sent` (`sent_at`, `sent_message_id` = the captured
  `comms.message`, `pdf_sha256` = the attachment), event `quote_revision.transitioned`; the case walks
  to «Enviada». The PDF is not read; the worker's Drive filing and PDF reader select revisions by
  `origin_source_record_id`, which a Cotizador revision never has, so neither touches it.
- **Catch-up.** It also reads emails other rules already acted on (once per email), so quotes sent
  before the merge move by themselves on the first passes.
- **Migration `20261021100000`.** `quote_revision_sent_message_key` becomes `unique (sent_message_id,
  quote_id)`: one email may send several quotes. No table, policy or grant changes.
- **Not undoable** from Hoy/Historial (`sent_quote_is_final`): a wrong one is voided in the Cotizador.
  R15 now reads emailed historical quotes only, as before.
- **Not yet.** Deploys on merge with the migration (`production-db-migrations`); nothing manual.

### 2.7.144 A Cotizador quote's sent PDF is filed in its case's Drive folder, 2026-10-21 — built, deploys on merge

`origenlab-worker drive-file` (`drive_filing.py`) found a revision's email only through
`origin_source_record_id`, which a Cotizador revision never has, so the PDF Tatiana emailed was never
filed. It now also follows `sent_message_id` (set by R16, §2.7.143) to that email's capture and takes
the attachment by the revision's `pdf_sha256`, into the case folder as for any recorded quote. The
quote reader (`quote_reader.py`) keeps its own selection — revisions recorded from an email — so a
Cotizador PDF is filed, never read, and no model call is made for it. No migration, no new grant (the
worker already reads `comms.message`). Deploys with the worker on merge; the next cron run files the
quotes R16 marked sent.

### 2.7.145 A bounce someone deleted still blocks its address, 2026-10-21 — built, deploys on merge

The OHAUS campaign's bounce notices were being deleted from contacto@ (whole conversations, our
sent copy included) before the next capture run; the capture skips Trash (V1 intake rule), so 114 of
~128 dead addresses never reached `outbound.contact_control` (blocked by hand the same day, V1 and V2,
`bounces-20261021`). Now a delivery report — sender `MAILER-DAEMON@…` / `postmaster@…` — in the Trash
is captured and staged like any inbound mail (`capture.intake_skip_reason`, and the shared evidence
rule `manifest._require_stageable_gmail_payload`, which keeps refusing Spam, drafts and every other
Trash message). A contacto@ Gmail filter (same day) also sends bounce notices straight to «Rebotes»,
out of the inbox, so nobody needs to delete them.

### 2.7.146 The mail worker imports only what each process runs, 2026-10-21 — built, deploys on merge

From 2026-10-20 13:00, when the tender child went live, `origenlab-mail-triage` sat at ~420 MB
(266 MB before) and peaked over starter's 512 MB: about 25 restarts in a day. Each of its four
long-running processes imported the whole CLI — ~1,000 modules, ~83 MB, FastAPI and the V1 pipeline
included — because `origenlab_api/v2/__init__.py` imported every router and `cli.py` every command.
Now: the v2 package resolves its names on first use; `origenlab-worker` is `entry.main`, which starts
the supervisor and its children without `cli` (supervisor ~13 MB, capture scheduler ~35 MB — its
connector moved to `queue_connector.py` — triage and tender ~48 MB at start); and the tender
child's 15-minute discovery runs as `tender-discovery` in a process that exits, so the ~4,800-tender
list is not kept. The plan was raised to standard on Render the same day as a stopgap and returned
to **starter** at 16:33Z once this was live: 279–362 MB over the next 20 minutes, no restarts.

### 2.7.147 Several equipment lines share a quote's import expenses; no IVA per line; the PDF's later pages keep their margin, 2026-10-21 — built, deploys on merge

Owner (Tatiana quoting four imported OHAUS balances on one quote, CN01277): the import expenses all
landed on one «equipo principal». Each Cotizador line now has a «Lleva gastos» tick (it replaces
«Hacer principal»); the ticked lines share the expenses by cost — quantity × unit cost in pesos —
with the rounding remainder on the costliest, and one ticked line takes them all as before. The
first tick starts from the line the engine chose, so ticking a second shares between the two. The
marks are the lines' `is_principal` under the `manual` rule, so approval re-prices them unchanged.
- **Migration `20261021200000`.** Drops `quote_line_one_principal_per_revision` (the partial unique
  index allowing one principal item per revision); a principal is still always an item line.
- The line's cost strip no longer shows «+ IVA =» (the line's price read twice); IVA stays in the
  quote's total.
- The printed quote repeats the sheet's padding and green line on every page
  (`box-decoration-break: clone`): a second page used to start at the paper's top edge.

### 2.7.148 «Sin IVA» on a Cotizador quote, 2026-10-21 — built, deploys on merge

Owner: some quotes go without IVA. The totals card has a «Sin IVA» tick (the draft's `without_iva`);
saved, the revision's `tax_rate` is 0, so IVA is 0 and the total is the net; unticked, it takes the
`iva_rate` parameter again. A draft reloads ticked when its rate is 0, and approval re-prices with
the stored rate as before (no migration: `tax_rate` already allowed 0). The PDF then prints only
«Total CLP sin IVA» (no Neto/IVA rows, and a tender offer no per-line IVA columns).

### 2.7.149 A particular's quote reads «Persona natural» on the Cotizador, 2026-10-22 — built, deploys on merge

Since #816 a quote with no institution (not a tender) is approved, the recipient being the client;
the Cliente card still said «Sin institución · RUT pendiente» with «Agregar institución» as its only
action, so the owner could not tell (#818). It now reads «Persona natural (particular)», the person
from «Para» or what «Datos del cliente» typed (name, institution name, RUT), and that the quote is
issued in the person's name; adding an institution is a secondary link. Dashboard only.

### 2.7.150 A persona natural is their own client record, 2026-10-22 — built, deploys on merge

A quote for a particular was approved without an institution (#816), but its case could never reach
«Enviada»: `opportunity_organization_required_from_qualified` requires the requesting institution
from «Calificada» on, so R16 rolled back (CN01281A, emailed 22 Oct, stayed «Solicitada»). Owner's
choice: the person becomes their own client record — `crm.organization.kind = 'individual'`, the kind
the history import already gave six particulars — instead of relaxing the constraint. «Es persona
natural» on the quote's Cliente card (name prefilled from «Datos del cliente» or the recipient) and
«Es persona natural (particular)» in «Nueva cotización» register it (or reuse one of that name) as
the requesting institution; the card then shows the person and «Persona natural (particular)». A
persona natural is found by name only and takes no email domain, so a particular writing from a
company's address never claims that company's domain. No migration (`kind` is free text).

### 2.7.151 Request cases opened by R7/R11 add the sender as a participant; «+ Nueva» no longer duplicates them, 2026-10-22 — built, deploys on merge

When a customer emails contacto@ asking for a quote, R7 opens a
`lead`/`qualified` request case but wrote zero rows to
`crm.opportunity_participant`. `client_open_cases` (the read that «+ Nueva»
uses before opening a new case) searches open cases by organization, person,
or contact_point_id through `crm.opportunity_participant`; with no participant
row it could not find the request case and opened a duplicate instead.

| What | Where |
|---|---|
| New helper | `V2CaseCommandRepository._add_case_sender_participant` (resolvies or creates the `crm.contact_point`, then inserts a `quote_recipient` participant, idempotent; reuses `_create_contact_point`) |
| R7 (both paths) | `mail_rules.py` emits `ADD_CASE_SENDER_PARTICIPANT` after `open_commercial_case`; `mail_rules_repository.py` dispatches it |
| R11 (forwarded) | `forwarded_requests.create_request_from_forward` calls the helper after opening the case |
| Pipeline display | `_SQL_PIPELINE_PARTICIPANTS` in `crm_workspace.py` changed to INNER JOIN on `crm.person` so a bare contact-point participant does not replace the web-form contact on the pipeline card |

API only; no migration, proxy, or dashboard change.

### 2.7.152 Database performance indexes + worker memory, 2026-10-22 — built, not yet deployed

**Migration `20261022100000_perf_indexes.sql`** adds four indexes and the `pg_trgm` extension:

| Index | Table | Kind | Motivation |
|---|---|---|---|
| `message_provider_thread_idx` | `comms.message(provider_thread_id)` | B-tree partial | Worker triage joins on thread id alone (no mailbox filter); existing `message_thread_idx` leads with `mailbox_id` and is not used |
| `source_record_gmail_thread_idx` | `evidence.source_record((payload->>'gmail_thread_id'))` | B-tree expression partial | `CASE_CARDS_SQL`, `_LINKED_CASES`, `_TENDER_THREAD` all filter on this payload key |
| `product_model_key_trgm_idx` | `catalog.product(model_key)` | GIN trgm | Catalog reads use leading-wildcard `LIKE`; trigram index supports it |
| `organization_name_trgm_idx` | `crm.organization(lower(name))` | GIN trgm | SQL requester-match pre-filter (`word_similarity`) added to triage.py |

`pg_trgm` installed via a DO-block role-step (the extension schema belongs to the Supabase connection role, not `origenlab_owner`).

**Worker changes** (`apps/worker/src/origenlab_worker/triage.py`, `render.yaml`):
- `CASE_CARDS_SQL`: `LIMIT 300` added to cap the case snapshot read.
- `_KNOWN_QUOTE_NUMBERS`: capped at 2000 rows.
- `requester_match()`: SQL `word_similarity` pre-filter (`_REQUESTER_MATCH_SQL`) on `crm.organization` reduces the Python candidate set; falls back to the unfiltered query for very short bodies.
- `render.yaml`: `uv run --no-sync origenlab-worker` → `.venv/bin/origenlab-worker` (bypasses uv startup overhead); `MALLOC_ARENA_MAX=2` added to both the cron and triage worker services.
