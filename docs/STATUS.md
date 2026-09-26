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

Last verified: **2026-09-26**, against the merge base with `origin/main` (`e884c3a9`) plus this branch, measured from the
local PostgreSQL 17 carrying the Slice 0 migrations. §2.5's hosted facts are measurements taken by the
Slice 0 audit itself on 2026-09-21, over the reviewed Supavisor session route inside a server-
confirmed read-only transaction, together with authenticated control-plane reads; the earlier
statement here that no PostgreSQL session against the hosted project had ever succeeded is
superseded by that run and withdrawn. §2.5 also records the `supabase db push` connection of
2026-09-08. **§2.8 records that the hosted phase has since been frozen**, and that every hosted
number in §2.5 is therefore a frozen snapshot rather than a live reading.

## 1. Eras

| Era | State |
|---|---|
| **V1** | **Running and authoritative** for every fact it owns today, including all outbound safety |
| **V2** | Architecture **accepted 2026-09-05**. **Schema foundation only.** One hosted project exists, `origenlab-v2`; as of 2026-09-21 it is **audited, role-converged and reconciled to all 19 migrations**, and proven to hold no business data (§2.5). The remaining Slice 0 gate items are plan-and-configuration attestations, not unknowns. No application code, nothing deployed |

## 2. V2 — slice status

Slices and their gates are defined in [`MIGRATION.md`](MIGRATION.md) §5.

| Slice | State | Note |
|---|---|---|
| 0 — local foundation | **DONE** | `supabase/roles.sql` + 19 migrations → 4 roles, 7 schemas, 33 tables, grants, RLS. Head has since moved past slice 0: 23 migrations, 36 tables, 139 policies (§2.1). Proven by `supabase/tests/` and `supabase/scripts/`, enforced by `.github/workflows/supabase.yml` on every push touching `supabase/**` |
| 0 — hosted gates | **BLOCKED — on attestation items only; the audit itself has now succeeded** | **The hosted Slice 0 audit completed against `origenlab-v2` on 2026-09-21** over the Supavisor session-mode route, and every SQL proof passed. `supabase/hosted_roles.sql` was applied under its atomic contract the same day and the four absent migrations were reconciled, so the project now carries all 19 (§2.5). Checks 1–9 of [`MIGRATION.md`](MIGRATION.md) §5.2 are therefore proven **against the hosted project**, not merely locally. **Checks 10–11 remain unproven**, and with them the eight attestation items `t01`–`t07` and `d01`: the project is on the Free plan with no backup entitlement, so no restore drill can be evidenced, and its Data API is running rather than off. Those are plan-and-configuration decisions for the owner. The project is **reconciled but still not adopted** — no committed file names it and no application code reads it. See §2.5 |
| 1 — Auth / `platform.*` | NOT STARTED | |
| 2 — CRM identity + V1 row migration | NOT STARTED | |
| 3 — Quotes, lines, FX, snapshot, PDF | **SCHEMA ONLY** | The three commercial-case tables — `crm.opportunity_organization`, `crm.opportunity_interest`, `crm.opportunity_evidence` — were built locally on 2026-09-22 and are **empty** (§2.7.16). No command, no quote work, nothing wired |
| 4 — Evidence, comms, shadow Gmail, catalog, notices | NOT STARTED | |
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
| Source records | **35 = 31 `gmail_message` + 4 `migration_manifest`.** All three numbers are asserted; the total alone would not distinguish 35 real records from 28 real ones and 7 fixtures |
| Staged from Gmail | **31 records and 37 assertions** — the 20 records / 26 assertions of the September manifest plus the 11 records / 11 assertions of R1 (§2.7.11) — each reapplied **exactly once** from local manifests in `~/data/origenlab-v2-local/evidence/`. All `pending` / `unresolved`; nothing has been reviewed. **No Gmail connection was made**; the manifests are files, and the staging tool imports no Google client |
| Fixture residue | **zero.** Four probes assert it by name: `dedupe_key like 'pytest-%'` 0, `email_norm like 'pytest-%'` 0, `platform.command_receipt` 0, `crm.domain_event` with a non-null `command_receipt_id` 0 |
| Send flags | one `outbound.send_control` row, **both `false`** |
| Verification | **41 probes, all exact**, `supabase/cleanroom/verify.sql` compared against `supabase/cleanroom/expected_counts.json` by `compare.py`. Exact in both directions: an undeclared probe and an unmeasured probe both fail. `verify` is read-only — the whole file runs inside `begin read only`. **Passing as of 2026-09-22 against the post-R1 baseline** |
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

- **`apps/worker`** — named in [`ARCHITECTURE.md`](ARCHITECTURE.md) §1–§2 as the owner of
  Gmail sync, MIME parsing, PDF rendering, ChileCompra fetching and the single
  send path. Not created.
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
| Approved environments | `staging` (Pro daily backups, seven-day retention, PITR declined). **`production` blocked** — no recorded RPO/PITR decision |
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

### 2.7 Wave 1A/1B → V2 importer — measured

The first code in this repository that writes to a V2 schema. Full contract and
mapping: [`DATA.md`](DATA.md) §7.6.

| Item | Value |
|---|---|
| Entry point | `apps/email-pipeline/scripts/migration/import_waves_into_v2.py` → `origenlab_email_pipeline.migration.v2_import` |
| Default mode | **dry-run.** Without `--apply` **no database connection is opened at all** |
| Target boundary | **literal loopback address only**, via `migration/v2_import/target.py`: a URI query string, a fragment, a multi-host list, a Unix socket, a host *name* (including `localhost`), a hosted-provider marker or a host-less DSN all refuse, the DSN handed to the driver is rebuilt from the validated parts, and the libpq `PG*` routing environment is removed for the connection. **No override flag exists**, and a test asserts none does |
| Write role | `set local role origenlab_owner`, the same step every migration takes. The seven tables are owned by that role and grant nothing to the Supabase CLI's `postgres` login, so a login holding a SET membership of it is required (`supabase/roles.sql`) |
| Idempotency identity | **provenance-scoped.** A campaign is identified by `origin_source_record_id` plus `(mailbox_id, name)`, the send ledger is reconciled as a multiset scoped to that origin, and every reused row is read back and compared field by field. An existing row that differs from the plan refuses the run instead of being adopted |
| Tables written | 7 — `evidence.source_record`, `evidence.assertion`, `outbound.contact_control`, `comms.mailbox`, `outbound.campaign`, `outbound.campaign_recipient`, `outbound.send_attempt`. **`crm.*` is never written**, and the row count is asserted unchanged across an apply |
| Applied to a real database | **zero hosted times.** Since 2026-09-21 it is also applied to the **persistent local development database** (§2.1), which is a real database in the sense that it is not discarded after the run — and is still local, loopback-only and frozen out of the hosted project |
| Rows loaded | **28,666** — the full cross-wave safety baseline, the evidence trail and all three campaigns' audience and ledger ([`DATA.md`](DATA.md) §7.6.5) |
| Wave 1A load gate ([`DATA.md`](DATA.md) §7.3) | **green** inside that total — 8,580 `prior_contact` all `marketing`, 704 address blocks, 91 domain blocks, zero `purpose=all` outreach facts, zero cooldown rows |
| Idempotency | proven on the real artifacts — a second apply inserted **0** rows, with `crm.*` still 0 |
| Blocked by schema decisions | **none.** Both gaps the first dry run found were decided and closed on 2026-09-20 — [`DATA.md`](DATA.md) §7.6.4 |
| Rejects | **0** over the real artifacts; every input row mapped |
| Tests | **136** — `uv run pytest tests/test_v2_import.py`. 15 are database-backed and skip unless `ORIGENLAB_V2_TEST_DSN` names a disposable local Slice 0 database |
| Network calls | **zero.** No Gmail, Supabase, Render or Cloudflare client is imported; both send flags are untouched |

**Promotion from `evidence.assertion` to `crm.*` now exists** — §2.7.1. The send
predicate that would read `outbound.contact_control` remains slice 5, and no
dashboard surface reads any of it yet.

**No hosted V2 database has been loaded** — every target ever opened is a local,
loopback-only PostgreSQL 17 (§2.5 and §2.8 for why no hosted project is used).

### 2.7.1 Evidence → CRM promotion — measured 2026-09-21

The first code that writes `crm.*`. It is the slice 2 identity pass, run under the
conservative identity defaults the operator approved.

| Item | Value |
|---|---|
| Entry point | `apps/email-pipeline/scripts/migration/promote_evidence_into_crm.py` → `origenlab_email_pipeline.migration.v2_promote` |
| Default mode | **dry-run**; `--apply` is required to write |
| Target boundary | the importer's own loopback guard, unchanged and unweakened |
| Tables written | 4 — `crm.organization`, `crm.contact_point`, `crm.domain_event`, and the `resolution` of `evidence.assertion`. **`crm.person` is never written** |
| Identifiers | deterministic UUIDv5 from the evidence, so the same input mints the same `crm` id in every environment and a hosted replay reconciles row by row rather than only by count |
| Rows created | **1,812** organizations, **9,460** contact points, **22,544** `crm.domain_event` rows |
| `crm.person` rows | **zero**, asserted inside the transaction — a non-zero count rolls the whole promotion back |
| Contact points | 695 `shared_mailbox`, 8,765 `unattributed`; **none carries a person or an organization**, also asserted inside the transaction |
| Organizations | all `kind = 'unknown'`, all `confirmation = 'machine_proposed'` |
| Review queue | **4** assertions `ambiguous` (2 near-duplicate name clusters), **172** `supplier_candidate` left `unresolved`, and every created row `machine_proposed` |
| Audit | every event `actor_kind = 'migrator'`; no operator is impersonated and no `actor_operator_id` is set |
| Idempotency | proven — a second `--apply` wrote **0** rows and **0** events |
| Tests | **40** — `uv run pytest tests/test_v2_promote.py`; the policy is pure functions and needs no database |
| Network calls | **zero**; both send flags untouched |

**Why no person was created.** The Wave 1A/1B recipient rows carry `email`,
`email_norm` and `institution_name` and no human name at all. `crm.person.display_name`
is NOT NULL, so creating a person would mean deriving a real person's name from an
address local part — an identity inference, not evidence. 2,860 addresses have the shape
of a personal name and are recorded as `unattributed` contact points with the reason
stored on the assertion, so an operator can name them from other sources.

**Why no contact point was attached to an organization.** [`DOMAIN.md`](DOMAIN.md) §2.2
makes a domain a routing hint and never an identity key on its own, so a role address is
not joined to an organization because it shares its mail domain.

**`kind = 'unknown'` is a recorded decision, not a guess.** [`DOMAIN.md`](DOMAIN.md) §2.1
leaves the closed list of organization kinds **[OPEN]** and the evidence carries no kind.
`unknown` is an explicit absence marker; the operator approved it on 2026-09-21 and every
row stays `machine_proposed` until reclassified.

### 2.7.2 V2 durable read boundary — measured 2026-09-21

The first application code that *reads* the V2 durable core. `docs/STATUS.md` §2.2 said
Slice 0 had "zero consumers"; it now has one.

| Item | Value |
|---|---|
| Location | `apps/api/src/origenlab_api/v2/` — a separate surface inside the V1 operator API, not a new service |
| Routes | **14** `GET` — the seven listings (`/v2/contacts`, `/v2/organizations`, `/v2/prospects`, `/v2/opportunities/active`, `/v2/tasks/due`, `/v2/review/summary`, `/v2/quotes/followup`), `/v2/evidence`, the two card reads `/v2/contacts/{id}` and `/v2/organizations/{id}` (§2.7.6), `/v2/evidence/records` (§2.7.7), the two case reads `/v2/cases` and `/v2/cases/{id}` (§2.7.18), and `/v2/organizations/{id}/cases` (§2.7.20). The count said 11 while the two case routes were already shipped and is corrected here |
| Write routes | **zero on the read router**, asserted by a test over its own methods. The four commands of §2.7.8 live on a separate router and are not mounted unless switched on |
| Mounted when | **only** if `ORIGENLAB_V2_DATABASE_URL` is set. Unset, the router is absent entirely and `/v2/*` is a 404 |
| Connection role | `origenlab_api` — no membership in `origenlab_owner`, so RLS constrains these reads as it will in production |
| Transaction | `set transaction read only` then `set local statement_timeout`, both inside the transaction. A write returns **SQLSTATE 25006**, proven against a real database |
| Identity | one port, two adapters — a JWKS verifier (the target; **present and unconfigured**) and a local development adapter that **refuses to construct** unless the V2 DSN is a literal loopback address. JWKS wins whenever configured |
| Authorization | every route requires a resolved, **active** `platform.operator` with role `viewer`, `sales` or `admin`; no identity is 401 |
| Google ID-token signature | **since 2026-09-26, committed locally, not pushed.** `v2/google_jwks.py`: the callback verifies the ID token's RS256 signature against Google's published JWKS (constant URL, keys chosen by `kid` from Google's set only, `alg` must be `RS256`, unknown `kid` refetches once per minute, cache bounded by `Cache-Control` and 24 h) **before** any claim is read. An unreachable or unparseable key set refuses the sign-in (`signature_unverified`); nothing falls back to the unverified claims. `cryptography>=43` added to `apps/api`. `test_v2_google_jwks.py` + `test_v2_google_auth.py` **131 passed** |
| Contact addresses by role | **since 2026-09-26, committed locally, not pushed.** Every `/v2` GET router (`routes.py`, `cockpit_routes.py`, `crm_workspace_routes.py`, `quote_case_workspace_routes.py`, `quote_import_review_routes.py`) is built on `ContactRedactingRoute` (`v2/contact_redaction.py`): for a `viewer` every email address in the JSON answer is masked to `***@dominio`, every phone channel to `***`, and the answer carries `X-OrigenLab-Redaction: contact-addresses`; `sales` and `admin` read the addresses as recorded. The walk is value-based, so a new field or a `Nombre <dirección>` string is caught without being listed. A route that recorded no operator is redacted (fail closed). The import-review quotation PDF is **403** for a viewer. The search is scoped with the answer: `GET /v2/contacts?q=` for a `viewer` matches only `crm.person.display_name` and `crm.organization.name` (`V2Repository.contacts(search_addresses=False)`, the fail-closed default; the route sets it from `sees_contact_addresses(role)`), so a viewer cannot confirm a masked address by searching for it; `sales` and `admin` still match `value_norm`. `GET /v2/evidence?q=` is scoped the same way (`V2Repository.evidence(search_addresses=False)`, fail-closed default, set by the route from the role): a viewer's search skips the address kinds `contact_address`, `contacted_address` and `postal_address` **and** any `value_norm` carrying an `@`, while the unsearched listing still returns every masked row; `sales` and `admin` search the whole trail. The email mask covers an address at a bare host too (`user@host`, `root@localhost`, `a@srv-01`), not only a dotted TLD; a bare `@handle` with no local part is left alone. The dashboard says what the search can find for the role: a viewer's search box on `#/crm-v2` (`searchPlaceholder` in `lib/crmV2Browser.ts`) and on `#/crm` Personas offers a name search and never mentions addresses; `sales` and `admin` keep the address placeholder. `test_v2_contact_redaction.py` **33 passed** in process (route class on every mounted `/v2` GET, name-only contact search, kind-scoped evidence search, bare-host mask) plus **8** database-backed evidence proofs in a disposable `origenlab_test_<hex>` database; one contact proof in `test_v2_read_boundary.py`. The database-backed ones run when `ORIGENLAB_V2_TEST_DSN` is set. The V1 read routes `/contacts/*` and `/mirror/*` on `apps/api` have no role model and no redaction; the owner decided on 2026-09-26 to **close them in the dashboard proxy** rather than mask them (§2.7.26), so V2 is the only browser surface for contact data |
| Paging | every listing bounded — default 50, maximum 200. Every child list on a card is bounded too, at 100, and the card reports the true count beside each capped list |
| Tests | **57** — `apps/api/tests/test_v2_read_boundary.py`; 14 are database-backed and skip unless `ORIGENLAB_V2_TEST_DSN` is set. The case reads add **26** in `test_v2_case_read_boundary.py`, 3 of which (§2.7.20) build their own disposable database. The durable connections of §2.7.21 add **10** in `test_v2_crm_connections_read.py`, all database-backed on their own disposable database. The command boundary has its own 64 (§2.7.8) |
| Measured against the local database | `/v2/contacts` 9,460 · `/v2/organizations` 1,812 · `/v2/review/summary` 4 ambiguous, 172 unresolved, 1,812 + 9,460 machine-proposed |
| Returning zero today | `/v2/prospects`, `/v2/opportunities/active`, `/v2/tasks/due`, `/v2/quotes/followup` — see below |

**Why four endpoints return zero.** `crm.opportunity`, `crm.task` and `crm.quote` are empty.
Those are V1's *durable* rows, which live in the V1 PostgreSQL `commercial.*` schema and have
not been migrated. The `commercial_*` tables in the local SQLite are the **rebuildable**
machine projections ([`CLAUDE.md`](../CLAUDE.md) → *Durable vs rebuildable*) and are not a
substitute for them. The endpoints are correct and the data is genuinely absent; a V1 durable
migration is the next step and needs a V1 dump the repository does not have.

**Local credentials.** `supabase/scripts/dev_db.sh api-login` generates a password for
`origenlab_api` **on the development container only** and writes the DSN to
`~/data/origenlab-v2-local/api.env`, mode `0600`, outside Git. `supabase/roles.sql` still
assigns no password to any role and no credential is in tracked content.

### 2.7.3 Four CRM cards on the V2 durable core — measured 2026-09-21

The first operator-facing surface fed by V2 rather than by a rebuildable mirror.

| Item | Value |
|---|---|
| Surface | the four "Trabajo comercial" cards on `apps/dashboard` → *Qué revisar hoy* |
| Previous source | `commercialWorkQueue`, a V1 read model |
| New source | `/v2/tasks/due`, `/v2/quotes/followup`, `/v2/review/summary` (§2.7.2) |
| Legacy mirror dependency in that grid | **removed**. The six-card "Colas prioritarias" grid below it still uses warm-case and lead-intel mirrors and is out of scope |
| Proxy | seven exact `/v2/*` GET paths added to `apps/dashboard-proxy`; **not** a `/v2/.+` wildcard, and none is POST-writable |
| Operator identity | the **same** `X-OriginLab-Operator-Email` the proxy rebuilds from Cloudflare Access — a V2-specific header name would have been forwarded straight from the browser |
| Measured, live | *Seguimientos vencidos* 0 · *Para hoy* 0 · **Revisión humana 4** · *Cotizaciones por seguir* 0 |
| Screenshots | `~/data/origenlab-v2-local/screenshots/`, outside Git |
| Tests | 9 in `v2CardSummary.test.ts`, 3 rewritten in `TodaySummaryPage.test.tsx`, 4 in the proxy allowlist suite |

**Three cards read zero, and say why.** `crm.task` and `crm.quote` are empty because V1's
durable rows have not been migrated, so each of those cards renders *"Sin datos: falta migrar
el histórico V1"* instead of a bare `0`. A zero that looks like a clear workload when the
table is merely unread is the failure mode this avoids.

**"Revisión humana" counts 4, not 11,276.** It reports the assertions the migration stopped
and asked about — a number an operator can drive to zero — and carries the
machine-proposed backlog in its hint rather than summing the two.

### 2.7.4 Marketing linkage — measured 2026-09-21

The marketing history arrives keyed by **address**; promotion creates the canonical channel
rows. This stage joins the two, so a campaign, a delivery and an attribution can be read from
a canonical identity instead of from a string.

| Item | Value |
|---|---|
| Entry point | `promote_evidence_into_crm.py --apply --link-marketing` → `migration/v2_promote/marketing.py` |
| Column written | `outbound.campaign_recipient.contact_point_id`, and nothing else |
| Recipients linked | **3,252** of 3,481 |
| Recipients with no canonical channel | **229** — snapshotted into a frozen audience but never contacted, so no `contacted_address` assertion and therefore no channel exists. Correct, not a gap |
| Send attempts now reachable from identity | **3,138** of 3,141 |
| Suppressions matching a canonical channel | 10,396 — **reported, never written** |
| `person_id` / `organization_id` on recipients | **still zero**, asserted inside the transaction |
| `outbound.contact_control` | **untouched**, and a test proves it |
| Domain events written | **zero** — see below |
| Idempotency | proven — a second run linked **0** |

**`outbound.contact_control` is deliberately not given an identity column.** A suppression
is a fact about an *address*: it must keep working for an address whose owner is unknown, was
never promoted, or is later merged. The table has no identity column by design and this stage
does not add one.

**No `crm.domain_event` is written.** The event stream records human commercial truth. This
linkage is a deterministic join over data that already exists, re-derivable from the address
at any time, and carries no decision anybody made.

### 2.7.6 Contacts + Organizations slice — measured 2026-09-21

The first **searchable** operator surface over the durable core, and the first evidence
path that is not a V1 migration. Three parts, all local.

#### The schema addition

| Item | Value |
|---|---|
| Migration | `20260921120000_slice2_gmail_drive_evidence_kinds.sql` — additive only |
| `evidence.source_record.kind` | 7 → **9**; adds `gmail_message`, `drive_file` |
| `evidence.assertion.kind` | 7 → **8**; adds `document_reference` |
| Tables, columns, grants, RLS policies | **unchanged** — 33 tables, 127 policies, no column added, no grant widened |
| pgTAP | **6** new assertions in `061_constraints_comms_outbound_evidence.sql`; the suite is 408 across 11 files, all pass on a clean `supabase db reset --local` |

A Gmail message needs no new assertion kind: the facts a message yields are addresses and
names, which `contact_address` and `organization_name` already describe. Only the
provenance differed, and that is what the two `source_record` kinds record.

#### The staging tool

| Item | Value |
|---|---|
| Entry point | `apps/email-pipeline/scripts/migration/stage_gmail_drive_evidence.py` → `origenlab_email_pipeline.migration.v2_evidence_stage` |
| Default mode | **dry-run.** Without `--apply` no database connection is opened at all, and `--database-url` is not even required |
| Input | a local JSON manifest (`manifest_version: 2` since 2026-09-22 — see §2.7.11) an operator produced out-of-band. **No Google client is imported anywhere in the package** and a test asserts it, so everything that can reach the database is a file a human can read, diff and refuse first |
| Network calls | **zero.** No Gmail, Drive, Supabase, Render or Cloudflare client; both send flags untouched |
| Target boundary | the Wave 1A/1B importer's own loopback guard, unchanged and unweakened. No override flag exists and a test asserts none does |
| Tables written | 2 — `evidence.source_record` (`review_status = 'pending'`) and `evidence.assertion` (`resolution = 'unresolved'`). **Nothing else** |
| `crm.*` and `outbound.*` | **never written.** The `crm` row count is taken before and after inside the staging transaction and any change rolls the whole pass back |
| What a manifest may assert | a `gmail` record: `contact_address`, `organization_name`. A `drive` record: `document_reference`, `organization_name`. `contacted_address` and `affiliation` are refused from both — the first is a claim about our own outbound history that only the send ledger may make, the second a relationship no message header records |
| Resolution vocabulary | **absent from the manifest by design.** There is no way to express "confirmed", "this is person X" or "merge these". Staging proposes; `promote_evidence_into_crm.py` decides |
| Reporting | counts and kinds only — a test asserts no address, document name or organization name reaches the report, which is what gets pasted into commits and CI logs |
| Idempotency | proven against a real database — a second apply created **0** records and **0** assertions; a record re-staged with a different `source_uri` is refused rather than overwritten |
| Rows staged into any database | **20 source records and 26 assertions**, into the local `origenlab_dev` only, on 2026-09-21 (§2.7.7). Before that run the tool had been exercised only by its own tests |
| Tests | **54 collected** — `uv run pytest tests/test_v2_evidence_stage.py`; 2 are database-backed and skip unless `ORIGENLAB_V2_TEST_DSN` is set |

**No Gmail credential exists in this repository**, and the acquisition step — whatever
produces a manifest — is deliberately outside this tool. A first manifest has since been
produced from a real mailbox by hand and staged; §2.7.7 records it. No Drive manifest
exists.

#### Re-acquiring a manifest at version 2

| Item | Value |
|---|---|
| Entry point | `apps/email-pipeline/scripts/migration/reacquire_gmail_manifest_v2.py` → `…migration.v2_evidence_stage.reacquire` |
| What it does | rewrites a `manifest_version` 1 Gmail manifest as version 2, taking `intake_class` and `gmail_labels` from a **separate acquisition file** rather than inventing them. Everything else — external ids, source URIs, `acquired_at`, observations, order — is carried through unchanged |
| Network calls | **zero.** It reads two local files and writes a third; it imports no Google client, so it cannot send, draft, label, archive, trash or modify anything. Acquiring the labels stays a separate, read-only, out-of-band step |
| What refuses the whole pass | a manifest id the acquisition never looked at; an acquisition message the manifest does not hold; a sender or `message_date` that no longer matches; a label classifying as `metadata_only`, `excluded_spam` or `excluded_trash`; a duplicated id; a version 1 payload that already declares the version 2 fields; an `--out` inside the working tree; an existing `--out` without `--force` |
| Last check | the output is parsed by the **staging loader** before it is written, so a file this step blesses and staging then refuses cannot exist |
| Tests | **30** — `uv run pytest tests/test_v2_evidence_reacquire.py`; no database, no network |
| Used once | 2026-09-22, on the September sweep (§2.1). 20 records, 26 observations, all `primary_evidence`; all 20 senders and dates re-matched exactly |

#### The operator surface

| Item | Value |
|---|---|
| Page | `apps/dashboard/src/pages/CrmV2Page.tsx`, section id `crm-v2` |
| Cards | 4, searchable and paged — Contactos, Organizaciones, Prospectos, Evidencia |
| Detail | a drawer card for a contact and for an organization, each showing identity, relationships, the evidence trail with its provenance, and — for a contact — the campaign history and the controls on its address |
| New API routes behind it | `/v2/evidence`, `/v2/contacts/{id}`, `/v2/organizations/{id}` (§2.7.2) |
| Proxy | 3 exact paths added to `apps/dashboard-proxy`; the two card paths match a **UUID-shaped** segment, never `.+`, and none is POST-writable |
| Sidebar | **not in it.** The eight-item Cotizaciones-first primary IA is unchanged; `crm-v2` is a registry + hash-route section like `today` and `deals`, reached from the *Revisión humana* card on Inicio |
| `contacts` section | **untouched.** It reads the V1 lead-intel mirror and is what operators use today; the two hold different rows and replacing one with the other before the V1 durable migration would drop data |
| Writes | **zero.** No command client is imported and the proxy allows no POST under `/v2`. Confirming an organization, naming a person or merging two identities are durable commands the V2 command boundary does not carry yet |
| Measured, live | Contactos **9.460** · Organizaciones **1.812** · Prospectos **0** (renders *"falta migrar el histórico V1"*, not a bare zero) · Evidencia **11.448** |
| Tests | 8 in `CrmV2Page.test.tsx`, 15 in `crmV2Browser.test.ts`, 2 added to the proxy allowlist suite |

**Counts come from the response `total`, never from `items.length`.** Every listing is
capped at 200 and every card child list at 100, so counting the array would silently
understate 9,460 contacts as 50 and a large institution's channels as 100.

**Every absence on a card is explained rather than left blank.** No person, no organization,
no affiliation and no domain are each the *common* case in the migrated data, and each is
the result of a recorded decision ([`DOMAIN.md`](DOMAIN.md) §2.2, §2.7.1) rather than
missing data. A blank field would read as a gap to fill in.

### 2.7.7 First real Gmail staging + the review workspace — measured 2026-09-21

The staging tool of §2.7.6 has been run once against a real mailbox, and the dashboard has
gained the surface an operator works that queue from. Both are local.

#### The staged batch

| Item | Value |
|---|---|
| Manifest | 20 inbound commercial/quotation messages, September 2026, produced by hand from a **read-only** Gmail sweep. It lives outside Git at `~/data/origenlab-v2-local/evidence/` (`0600`) and is referenced by no repository path |
| Gmail side effects | **none.** Nothing was sent, labelled, archived, trashed or drafted; the staging tool itself opened no network connection, as always |
| Written | **20** `evidence.source_record` (`kind = 'gmail_message'`, all `review_status = 'pending'`) and **26** `evidence.assertion` (all `resolution = 'unresolved'`) — 20 `contact_address`, 6 `organization_name` |
| `crm.*` | **33,816 before, 33,816 after**, counted inside the staging transaction. No person, organization, affiliation, opportunity, permission, campaign or quote was created |
| `outbound.*` | untouched; both send flags still `false` |
| Organization names | asserted for **4 of 20** records only — the ones whose own text names an institution. For the other 16 the sole signal is the sender's domain, which is a hint, not an observation, and is therefore recorded in the record payload as `from_domain` rather than as a claim |
| Already in the CRM | **17 of 20** addresses already exist as a `crm.contact_point` from the historical import; all 17 are `machine_proposed` / `unattributed` with **no person and no organization**. 3 addresses are new |
| Dry run first | yes — the pass was reported with no `--apply` before it was applied |

#### The read boundary addition

| Item | Value |
|---|---|
| Route | `GET /v2/evidence/records` — the same queue as `/v2/evidence`, grouped **one row per source record** instead of one row per assertion |
| Why a second shape | `/v2/evidence` answers *where did this fact come from*. A reviewer asks *what am I being asked to decide*, which is a question about a message: its sender, its subject, what it asserts, and what of that already exists |
| Facts it joins | the matching `crm.contact_point` for each asserted address, the identically-named `crm.organization` for each asserted name, and the organization reached through `crm.organization_domain` for the sender's domain |
| What it does not do | no promotion, no scoring, no ranking, and **no matching by resemblance** — a domain that merely looks like an organization's name is an interpretation, and a test asserts `domain_organization` can only come from a registered domain. `crm.organization_domain` currently holds **0** rows, so it is null for every record |
| Filters | `source_kind` and `review_status`, both validated against the database's closed vocabularies — a value the schema cannot hold is a **422**, never an empty page |
| Write routes | still **zero** across `/v2`, asserted by the same test over the router's own methods |
| Proxy | one exact path added, `/^\/v2\/evidence\/records$/`. Not `/v2/evidence/.+`: a per-record sub-resource — the shape a promote command would take — stays refused, and a test asserts it |

#### The operator surface

| Item | Value |
|---|---|
| Page | `apps/dashboard/src/pages/EvidenceReviewPage.tsx`, section id `revision`, hash route `#/revision` |
| Shape | the five-step flow strip (evidencia → contacto/institución revisados → prospecto opcional → marketing con permiso → cotización), four top cards, the review queue with a per-record detail, then Marketing and Cotizaciones as explicitly unavailable sections |
| Per record it shows | sender, subject, date, whether the address already exists as a channel, the domain hint, every organization name the message states, every ambiguity the facts imply, and the record's provenance |
| The distinction it is built around | **"the address exists" is never rendered as "a person is confirmed"**, and **"domain hint" is never rendered as "confirmed organization"**. Both are separate chips with separate wording, and both are unit-tested |
| Actions | **preview only.** Every affordance renders `disabled` with its reason attached; a test asserts not one of them is enabled. There is no command client imported and no POST allowed under `/v2` |
| Marketing section | marked unavailable: an inbound email is not permission to send, permission is explicit and per address, and nothing in this queue grants it |
| Quotes section | marked unavailable: a quote attaches to an already-reviewed contact and organization, and none exists |
| Sidebar | **not in it**, same rule as `crm-v2`. It is a registry + hash-route section, reached from the *Revisión humana* card on Inicio, which now points here |
| First triage (added 2026-09-22) | `apps/dashboard/src/lib/evidenceTriage.ts` sorts the queue into four batches — *Correspondencia comercial*, *Respuesta automática de contraparte*, *Aviso de proveedor o seguridad*, *Sin clasificar* — from the sender, the sender's domain and the subject, all fields the boundary already returned. **No API change, no column, no migration** |
| What the triage is | a **view**, proven to be a partition: every record lands in exactly one batch, every batch's count is on screen at all times including the ones being hidden, and `Todo` shows the page unfiltered. Nothing is removed, quarantined or marked. It is recomputed on render and **written nowhere** |
| What the triage shows | every verdict carries the observed fact that produced it — the subject marker matched, the mailbox that refuses replies, the platform domain — rendered under *Por qué se sugirió* on the open record, so a reviewer can disagree with something specific |
| Default batch | *Correspondencia comercial*. Measured against the real clean-room queue on 2026-09-22: **20 commercial · 4 counterparty auto-replies · 7 vendor/security notices · 0 unclassified**, of 31 |
| The triage does not | rank, score, promote, recommend an action, or enable any affordance. A test asserts every preview action is still `disabled` with the triage on screen |
| What the surface will not say (2026-09-22) | it never presents **settling who uses an address** as something that exists or can be done. `crm.person` is empty and no command writes it, so the queue headline reads *Dirección conocida, sin titular registrado*, the chip reads *Sin titular registrado*, and step 2 of the flow strip is *Dirección / institución revisadas*. **Dirección, institución and evidencia stay three separate readings**: a known address is a channel, an institution is attributed only by exact name or registered domain, and the message is provenance. Two tests in `EvidenceReviewPage.test.tsx` assert the rendered page matches none of `/confirmar (la )?persona/i`, `/persona confirmada/i`, `/identificar (a )?(la )?persona/i` |
| Tests | **37** in `EvidenceReviewPage.test.tsx`, 19 in `evidenceReview.test.ts`, **32 in `evidenceTriage.test.ts`**, **37 in `evidenceCommands.test.ts`**, 9 in `test_v2_read_boundary.py`, 1 in the nav suite, and the proxy allowlist suite extended |

**What is still unconfirmed after all of this.** All 20 records are `pending` and all 26
assertions `unresolved`. No person name was staged at all — the manifest format has no kind
for one. 16 of 20 records carry no organization claim. `crm.organization_domain` is empty, so
every domain-to-institution guess in the queue is unevidenced. Promotion remains the job of
`promote_evidence_into_crm.py` and, eventually, of a V2 command boundary that does not exist.

### 2.7.8 The V2 human-review command boundary — built 2026-09-22, unwired

The review workspace of §2.7.7 could describe a decision and not record one. `apps/api` now
has the command boundary that records it. **No real review decision has been executed**, and
the twenty staged Gmail records are untouched: all twenty are still `pending` and all
twenty-six assertions still `unresolved`, verified by checksum before and after the work.

#### The five commands

| Command | Durable fact it records |
|---|---|
| `POST /v2/commands/keep-evidence-pending` | a named operator read this record and judged the evidence insufficient |
| `POST /v2/commands/confirm-organization` | an asserted name **is** one existing organization, matched on exact folded name |
| `POST /v2/commands/create-organization` | an asserted name is an organization nobody had recorded |
| `POST /v2/commands/attach-contact-address` | an asserted address is a mailbox an organization operates |
| `POST /v2/commands/attribute-sender-organization` | **one** asserted name is the sender's institution **and** this address is its mailbox — in one transaction (§2.7.13) |

| Item | Value |
|---|---|
| Location | `apps/api/src/origenlab_api/v2/{commands,command_repository,command_routes}.py` — a **second router**, so the read boundary's "no POST, PATCH or DELETE" test stays true |
| Mounted when | `ORIGENLAB_V2_DATABASE_URL` **and** `ORIGENLAB_V2_COMMANDS_ENABLED` (default **false**). Off, every command path is a 404 |
| Every write carries | the evidence record id, the operator from the **verified identity** (never the body), a non-blank `note`, an `Idempotency-Key`, and one `crm.domain_event` per change |
| Authorization | `sales` or `admin`. A resolved `viewer` is **403**, an unresolved caller **401** — reading the queue is not deciding it |
| Idempotency | `platform.command_receipt`, claimed with `insert … on conflict do nothing`. Same key + same digest replays the stored response; same key + different digest is **409** |
| Concurrency | compare-and-set throughout: an assertion resolves only `where resolution = 'unresolved'`, a contact point attaches only `where person_id is null and organization_id is null`, an organization confirms only `where version = %s` (the version the operator was shown) |
| Rollback | one transaction per command. A refusal rolls back **everything including the receipt**, so the key stays free — proven against a real database |
| Evidence | never destroyed. `origenlab_api` holds no INSERT or DELETE on `evidence.*` and UPDATE only on the resolution and review columns, so a command **cannot** rewrite a `value_norm` or a `payload` even if it tried. A test asserts Postgres refuses all five statements |
| No fuzzy matching | the only comparison in the boundary is equality between an asserted name and an organization's name. A near miss is `organization_name_is_not_an_exact_match`, refused |
| An organization cannot be born from a domain | `create-organization` takes **no name**; it reads one from the assertion. `name_display` may only restore letter case, checked by folding |
| Out of scope, with no route at all | merges, person creation, affiliations, prospects, marketing permission, campaigns, quotes, sending, and any hosted Supabase endpoint. A test asserts none of those words appears in a path |
| Migration | `20260922090000_slice2_evidence_review_decision_event.sql` — one new event type, `source_record.review_noted`. Additive: no grant widens, no policy relaxes, no send flag moves |
| Tests | **83** — `apps/api/tests/test_v2_command_boundary.py`; 53 in process, 30 database-backed. Was 64 before §2.7.13 |

#### Why the dashboard still cannot execute anything

`apps/dashboard-proxy` allows **no POST under `/v2`**, and this work does not open it. Building
the boundary and letting a browser through it are separate decisions and only the first has
been taken; a test names the four command paths and asserts the Worker forwards neither the
method nor the path. The workspace gained `evidenceCommands.ts` instead — a **preview** that
computes what each decision would record, which preconditions hold and which do not, and
renders every button `disabled`. It imports no client and knows no URL.

#### How the database-backed tests avoid the real evidence

They create a **disposable database** (`origenlab_test_<hex>`), apply the whole migration
chain into it, run as the unprivileged `origenlab_api` login, and drop it afterwards. They
cannot reach the staged records because the database they run in did not exist a moment
earlier. This needs `ORIGENLAB_V2_TEST_DSN` (a maintenance login) **and**
`ORIGENLAB_V2_API_TEST_DSN` (the runtime role); without both they skip.

#### What is still not decided

No command has been run against real evidence. `crm.organization_domain` is still empty, so
no record in the queue has an evidenced institution. There is still no command for creating a
person, which is what a named personal address in the queue would eventually need — but
attaching one no longer requires claiming it is a shared desk: `individual_owner_unknown`
(§2.7.14) records the institution and says nothing about the owner.

### 2.7.9 Fixture residue in `origenlab_dev`, and the clean room — 2026-09-22

**What happened.** A database-backed test run wrote into the persistent development database.
Measured read-only on 2026-09-22 before anything was changed, inside `begin read only`:

| Residue | Count | How it is identified |
|---|---|---|
| `evidence.source_record` | **7** | `dedupe_key like 'pytest-v2-command:%'`, kind `gmail_message`, 5 `pending` + 2 `reviewed` |
| `evidence.assertion` | **14** | belonging to those 7 records; 5 `promoted`, 9 `unresolved` |
| `platform.operator` | **7** | `pytest-v2-command-<hex>@example.cl`, all "Pytest Operator" / `sales` / `active` |
| `platform.command_receipt` | **9** | every row in the table; 5 `keep_evidence_pending`, 2 `create_organization`, 2 `attach_contact_address` |
| `crm.organization` | **3** | `Instituto pytest-v2-command <hex>`, all `confirmed` by a fixture operator |
| `crm.contact_point` | **2** | `contacto-<hex>@pytest-v2-command.example`, `shared_mailbox`, attached to those organizations |
| `crm.domain_event` | **16** | stream positions 22553–22568 — exactly the rows carrying a non-null `command_receipt_id` |

All of it was written between **12:58:22 and 12:58:23 UTC**.

**The schema drifted from its own ledger too.** The same run applied migration
`20260922090000`'s DDL without writing its ledger row: `crm.domain_event`'s
`domain_event_type_check` includes `source_record.review_noted`, while
`supabase_migrations.schema_migrations` records 20 migrations with head `20260921120000`. A
database that does not describe itself cannot be reproduced from its own ledger, which is why
§2.1's "21 of 21" claim is withdrawn above.

**What was not touched.** The twenty real staged Gmail records are intact: 20
`gmail_message` source records with non-fixture dedupe keys, all still `pending`, with 26
assertions all `unresolved`. Both send flags are still `false`. The append-only audit trigger
is untouched.

**The response is a rebuild, not a repair.** `origenlab_dev` is **quarantined** — left exactly
as measured, and written to by nothing. `origenlab_clean` (§2.1) is built beside it from the
migration chain and the reproducible historical load, and the twenty Gmail records are
reapplied from the same local manifest, exactly once, with no Gmail connection. A repair would
have meant deleting rows from an append-only audit stream and trusting that the list of rows to
delete was complete; a rebuild has to prove nothing about what was removed, only what the
reviewed inputs produce.

**How the same thing is prevented.** `origenlab_dev` and `origenlab_clean` are both in
`PROTECTED_DATABASES` (`apps/api/tests/protected_databases.py`,
`apps/email-pipeline/tests/protected_databases.py`). A `ORIGENLAB_V2_TEST_DSN` or
`ORIGENLAB_V2_API_TEST_DSN` naming either is refused at import time, before any test runs — the
common case is sourcing `api.env`, which points at `origenlab_dev`, and running pytest. The
disposable-database fixture additionally asks the **server** which database it reached and
asserts it is the one it just created, which does not depend on parsing a DSN and is the check
that holds when a name-swap goes wrong.

| Item | Value |
|---|---|
| Clean-room tooling | `supabase/scripts/cleanroom_db.sh`, `supabase/scripts/lib/local_target.sh` (guard), `supabase/cleanroom/` (baseline, probes, comparison) |
| Refusal tests | **25** — `supabase/scripts/cleanroom_failure_tests.sh`. Connects to nothing |
| Guard tests | **33** — `apps/api/tests/test_v2_target_boundary.py` (20) and `tests/test_protected_databases.py` (13) |
| Shared build steps | `ol_bootstrap_platform_objects_into` and `ol_apply_migrations_into` now live in `lib/local_target.sh`; `dev_db.sh` delegates to them, so the development and clean-room databases replay the chain through one implementation |
| Network calls | **zero.** No Gmail, Drive or hosted Supabase connection was made at any point |

### 2.7.10 Mailbox source inventory and intake classification — 2026-09-22

**What is built.** A read-only inventory of the V1 SQLite email archive that reports, for every
identity lane and every intake class, how large it is, how it splits between sent and received,
and what that class is permitted to do in V2 intake. It opens SQLite `mode=ro` with
`query_only=ON`, writes nothing, and **emits aggregate counts only — no address, subject,
message-id or body reaches its output**, so the report is safe to paste into a ticket. It
brackets its own read with a row count and reports `stable_snapshot=false` if the every-three-
minutes ingest wrote while it ran.

**The intake rules it encodes** (owner decision, 2026-09-22):

| Class | Rule |
|---|---|
| `primary_evidence` | Inbox and Sent are the primary commercial evidence lane |
| `archived` | Live archived non-draft mail is a candidate for the next evidence replay — never automatic |
| `metadata_only` | Drafts are metadata only: never correspondence, a quote, consent, or an opportunity |
| `excluded_spam` | Spam is inventoried separately and excluded from automatic intake |
| `excluded_trash` | Trash is inventoried separately as historical/purged material and excluded from automatic promotion |

The legacy commercial identity remains a **distinct** lane. Mail from it arriving in the current
mailbox is classified `cross_identity` — evidence for an operator to review, never an automatic
merge into the current identity.

**What the first run established, at the level this file is allowed to state.** The current
OrigenLab Gmail history is **near-complete locally**: everything that passed through the two
folders the routine ingest covers is present, and the local archive additionally retains
correspondence that has since been removed upstream. The gaps that remain fall entirely inside
classes intake excludes by rule, plus archived mail held as a replay candidate. The historical
lanes carry no material belonging to the current identity. **The measurement itself — mailbox
addresses, folder-level counts and date spans — is deliberately not in this repository**; it is
a private operator artifact outside the tree.

| Item | Value |
|---|---|
| Module | `apps/email-pipeline/src/origenlab_email_pipeline/qa/mailbox_intake_inventory.py` |
| Audit | `apps/email-pipeline/scripts/qa/audit_mailbox_intake_inventory.py` |
| Tests | **41** — `apps/email-pipeline/tests/test_audit_mailbox_intake_inventory.py`, including a read-only-connection proof, a byte-for-byte no-mutation check, and an assertion that no address reaches the output. Every fixture identity is fictional (RFC 2606 `.test`/`.invalid`); the production vocabulary is derived from `business_filter_rules.INTERNAL_DOMAINS` and asserted to partition it |
| Writes | **zero.** No database, no Postgres, no network |
| Wired into intake | **No.** Classification only; nothing promotes, replays or imports on its result yet |

### 2.7.11 R1 — the archived replay batch, applied to the clean room 2026-09-22

The first evidence replay defined by §2.7.10's intake rules has been built and applied. It
ran against **`origenlab_clean` only**; `origenlab_dev` stays quarantined (§2.7.9) and the
hosted project stays frozen (§2.8).

**What the batch turned out to be.** The archived backlog is not the correspondence it was
assumed to be: the overwhelming majority of it is delivery-status notifications generated by
our own outbound sends, auto-archived by a filter so they never reached the two folders the
routine ingest covers. That is the whole explanation of the gap §2.7.10 reported, and it
means the batch is far smaller than the candidate count suggested. **Twelve** messages in
the backlog are not postmaster traffic; **eleven** of those were staged.

**A second refusal was added, and it is a rule, not a filter.** A bounce is refused by the
loader outright. Its sender is a mail-delivery subsystem, so staging it would assert a
postmaster as a `contact_address` — true of the header, useless to the business — while the
fact it really carries, *which of our own sends failed*, is a claim about outbound history
that the manifest contract already reserves to the send ledger. The refusal is stated once,
in the manifest validator, and it aborts the whole pass rather than dropping a record.

| Item | Value |
|---|---|
| Manifest | outside Git, `0600`, now held in `~/data/origenlab-v2-local/evidence/` so the clean-room build replays it with the rest (§2.1). Built from a **read-only** session-connector sweep: nothing was sent, labelled, archived, trashed, drafted, deleted or downloaded |
| Candidates -> staged | **12 -> 11**, with **1** rejected at manifest-build time as postmaster traffic |
| Written | **11** `evidence.source_record` (`gmail_message`, all `pending`) and **11** `evidence.assertion` (`contact_address`, all `unresolved`) |
| Organization names | **none asserted.** Not one message names an institution in its own words; the sender's domain is a hint and is recorded in the payload as such, never as a claim |
| `evidence.source_record` | 24 -> **35** |
| `evidence.assertion` | 11,474 -> **11,485** |
| `crm.*` | **33,816 before, 33,816 after**, counted inside the staging transaction. No person, organization, contact point, prospect, permission, campaign, quote or task was created |
| `outbound.*` | untouched; both send flags still `false` |
| Idempotency | **proven.** A second, identical replay reported `0 created, 11 already staged` for records and for assertions, with `crm.*` unchanged again. Zero semantic duplicates |
| The twenty of §2.7.7 | **unchanged**, by content digest taken before and after: still 20 records, all `pending`, and 26 assertions, all `unresolved` |
| Promotion | **none.** The batch is a review queue and nothing else |

**The manifest format is now version 2.** Every Gmail record must declare its
`intake_class` *and* the Gmail labels it actually carries, and the labels win: a draft, a
Spam or a Trash message is refused on the evidence rather than on the operator's
declaration, so an optimistic or mistyped class cannot let one through. A version 1 file is
refused rather than upgraded in place — the two fields are facts only the acquisition step
can supply, and guessing them is what the rule exists to prevent. That is exactly how the
September manifest was brought forward on 2026-09-22: not edited, but **re-acquired**
read-only and rewritten against what the mailbox actually reported (§2.1, and "Re-acquiring a
manifest at version 2").

| Item | Value |
|---|---|
| Refusals | drafts, Spam, Trash (by label), a non-stageable declared class, a missing label list, and a postmaster sender |
| Tests | **52** in `apps/email-pipeline/tests/test_v2_evidence_stage.py` (was 32), including every refusal above and a test that the stageable classes are §2.7.10's vocabulary rather than a second copy of it |
| Proven against real data | yes — the same batch with the bounce put back was refused by name before any connection was opened |
| Drive replay (R2) | **not started.** Unchanged from §2.7.10: the duplicated quote numbers, the undeclared revision suffixes and the unmeasured Gmail-to-Drive attachment overlap all still block it |

### 2.7.12 Operator identity is configuration — closed 2026-09-22

Three real personal mailboxes were constants in this **public** repository: two in
`warm_case_sender_rules.py` and one in `supabase/scripts/cleanroom_db.sh`.

The two in the classifier could not simply be deleted. They are **business-rule keys** —
`looks_like_internal_admin_thread` decides "internal admin note" versus "client thread" by
comparing a sender against them exactly — so removing them would have changed what the
pipeline classifies. They now reach the rule by **role**:

| Item | Value |
|---|---|
| Module | `apps/email-pipeline/src/origenlab_email_pipeline/operator_identity.py` |
| Roles | `payments_operator`, `commercial_operator` — a closed set; an unknown key is refused, not ignored |
| Source | a JSON file outside Git, `~/data/origenlab-v2-local/operator_identity.json`, or wherever `ORIGENLAB_OPERATOR_IDENTITY_FILE` points |
| Default | **fictitious**, `@example.invalid`. An unconfigured checkout matches neither rule, which is visible through `OperatorIdentity.is_configured` rather than silent |
| Absence vs. malformation | no file at all is the documented default and is accepted; a file that was *named* and is missing, unreadable or malformed is **refused**, so a typo cannot look like a working configuration |
| Example | `apps/email-pipeline/config/operator_identity.example.json`, versioned, every address reserved |
| Tests | 12 in `test_operator_identity.py`, plus 2 added to `test_public_repo_privacy_hygiene.py` |

**The guard's exemption list is now empty.** `_DEFERRED_ALLOWLIST` in
`test_public_repo_privacy_hygiene.py` held those two files; it holds nothing, and
`test_privacy_allowlist_is_empty` fails if it grows back. Emptying it is what surfaced the
third address — the clean room's seeded operator — which no entry had ever covered.

**Consequence to know.** The two exact-value rules do not fire until the identity file
exists. That is inherent to taking the addresses out of Git, not a regression: the rule is
intact and is waiting for its input.

### 2.7.13 Attributing a sender, atomically — built 2026-09-22, unwired

The six-case review of 2026-09-22 found a product gap, not a bug. A message that names a
manufacturer **and** the university the equipment is for names two institutions and has one
sender. `confirm-organization` and `create-organization` both refuse such a record — *one
assertion at a time* — which is a correct rule and a useless outcome: the operator can see
which name belongs to the sender and had no way to say so. Two of the queue's records are in
exactly that state.

Attributing a sender was also **two commands**: create-or-confirm, then attach. Two
transactions, two receipts, and a state to stop in — an institution nobody can reach.

| Item | Value |
|---|---|
| Command | `attribute_sender_organization`, `POST /v2/commands/attribute-sender-organization` |
| What the operator names | the evidence record, **one** `organization_name` assertion, the `contact_address` assertion of the sender, and `target` — `existing` (with the id and the version they were shown) or `new` |
| In one transaction | create **or** confirm the institution · attach the address under the `usage` the operator chose (§2.7.14), `confirmed` · resolve **exactly those two** assertions · one receipt · one `crm.domain_event` per change |
| What it leaves alone | every other assertion of the same message stays `unresolved`, and the record therefore stays `pending`. The response returns `assertions_left_unresolved`, so that is a number the operator reads rather than a property they trust |
| Refusals it inherits | it runs the **same three steps** the single commands run, extracted rather than copied, so exact-name matching, the version compare-and-set, `organization_already_exists`, `contact_point_belongs_to_a_person` and `contact_point_attached_elsewhere` all behave identically |
| The refusal the case turns on | `contact_point_attached_elsewhere` — once the sender's mailbox belongs to one institution, the *other* institution the same message names cannot take it |
| Atomicity, proven | a test makes the second half fail and asserts the organization the first half would have created **does not exist**, no assertion moved, and the idempotency key is still free |
| A request that says two things | refused, not tidied: `target: existing` without a version, `target: existing` carrying a `name_display`, `target: new` carrying an `organization_id`, and the two assertion ids being the same, each have their own code |
| Still out of scope | no person, no `crm.organization_domain`, no prospect, permission, campaign, quote, task or send. A database-backed test counts all six tables before and after and asserts they did not move |
| Rehearsed against the clean room | `supabase/scripts/rehearse_attribution.py` — reads the **real** records read-only, replays each into its own disposable `origenlab_test_<hex>`, runs the real command as `origenlab_api`, drops the room, and fingerprints the clean room before and after. It now runs **each decision under both relationships**, because `usage` has no default. **4 records, 6 decisions × 2 relationships = 12 rehearsals, 2026-09-22: the 6 `individual_owner_unknown` accepted, the 6 `shared_mailbox` refused because every one of these senders writes from a named address; fingerprint unchanged** |
| Why the committed fixtures are fictitious | the repository is public and the senders are real people. The *shape* is committed under reserved `.example` values; the *real values* are read at run time by the rehearsal and never written down |

**The dashboard shows the exact request and still sends nothing.** `evidenceCommands.ts`
gained a fifth preview and two fields on every preview: `request`, the request body itself
rendered as JSON — not a description of it, because prose can agree with what the operator
meant while the body disagrees with both — and `leavesUnresolved`, which names the
institutions this decision deliberately does not settle. The workspace gained a radio group
for *which institution is the sender's*; it does not preselect one even when the message
names only one. **Every button is still `disabled`**, `apps/dashboard-proxy` still allows no
POST under `/v2`, and a test now names all five command paths and asserts the Worker forwards
none of them.

**What is still not decided.** No command has been run against real evidence. All 31 staged
records are `pending`, all their assertions `unresolved`, and `platform.command_receipt` is
empty — `cleanroom_db.sh verify` passed at 38 probes after this work, exactly as before it
(41 since §2.7.16 added a probe per commercial-case table).

### 2.7.14 What an address is to an institution — built 2026-09-22, unwired

**The defect.** `crm.contact_point` had four `usage` shapes and only one of them could hold
"this institution operates this address and there is no person": `shared_mailbox`. So that is
what the command boundary wrote, on every address — including the four in the queue, each of
which is one named individual's mailbox at their employer. On those it asserts that several
people read that person's mailbox. Nobody decided that; the vocabulary decided it, and the
CRM would then have read it back as its own belief about a real person. Every one of the
four eligible records writes from a named address, so the defect applied to all of them.

(The real addresses are deliberately not written here. They are read at run time by the
rehearsal, for the reason `rehearse_attribution.py` states at its own selection query: this
repository is public and the senders are real people.)

**The value that was missing.**

| Item | Value |
|---|---|
| Migration | `supabase/migrations/20260922140000_slice2_contact_point_individual_owner_unknown.sql` — additive: one `usage` value and its shape rule. No row changes, no grant widens, no policy relaxes |
| The claim it makes | this institution operates this address, and one individual owns it whom nobody has recorded. Shape: `person_id IS NULL AND organization_id IS NOT NULL` |
| The claim it refuses to make | it creates no person and names nobody. It is not `shared_mailbox` (which asserts a shared desk) and not `unattributed` (which cannot hold the institution) |
| Where it goes next | to `work` when a person is recorded — a promotion of knowledge, not a correction of a false claim |
| pgTAP | 4 new assertions in `supabase/tests/060_constraints_crm.sql` (105 in that file, 412 across the suite): the shape lives, both halves of it refuse, and the vocabulary stays closed |

**The rule at the boundary.** `usage` now has **no default** on
`attach_contact_address` and `attribute_sender_organization`: a request that does not say
what the address is to the institution is refused, because the two reachable values make
opposite claims about a human being. And `shared_mailbox` on an address whose local part is
not a recognised role is refused unless the request carries
`shared_mailbox_override_note` — a sentence, not a checkbox, saying how the operator knows it
is a desk. The check runs **inside the command's transaction**, against the address in the
assertion, not against a string the caller supplied. The note is written into the
`contact_point.created` / `.confirmed` event payload.

**What the workspace shows.** A second radio group — *what is this address to the
institution?* — with neither option preselected and the neutral one's consequences written
next to it ("No crea ninguna persona, no dice de quién es la casilla y no afirma que sea
compartida"). The justification field appears only for the combination that needs it. The
preview's earlier caution ("no parece un buzón de mesa") stays a caution, because the
operator now has a truthful value to choose instead.

**Every button is still `disabled`** and `apps/dashboard-proxy` still allows no POST under
`/v2`. Nothing was decided; `crm.*` is unchanged.

| Evidence | Result |
|---|---|
| `apps/api` pytest | 1,272 passed, 152 skipped (`validate.sh` mode); 158 passed with a migrated disposable database, including 4 new database-backed proofs of the refusal, the neutral row and the override |
| `apps/dashboard` vitest | 1,229 passed across 126 files |
| pgTAP `060_constraints_crm.sql` | 105/105 on a freshly migrated disposable database |
| Clean-room rehearsal | 12 rehearsals over the 4 real records; fingerprint unchanged |

### 2.7.15 Identity, commercial role and person — separated in the surface, 2026-09-22

**The confusion this prevents.** An operator choosing which of two named institutions the
sender belongs to is settling **identity**. They are not settling what that institution is to
OrigenLab commercially, and they are not settling who the person is. The three used to be
invisible to each other in the workspace, which is how "this is the institution" quietly
reads as "this is a prospect".

| Item | Value |
|---|---|
| Module | `apps/dashboard/src/lib/commercialRole.ts` — annotation only. No client, no URL, no write |
| `supplier` | an exact match against the six approved brands. A **lookup**, sourced to the business's brand review of 2026-09-06 |
| `requesting` | **inferred** — this message names a supplier brand and this name is not it. Labelled as a reading of the message, never as a recorded fact |
| `unknown` | no supplier brand is named, so nothing is shown. Silence rather than a guess |
| Where the list comes from | `apps/web/src/data/brands.ts` (`APPROVED_BRAND_IDS`), the closed list the public site already validates |
| Why a copy, and how it cannot rot | the operator dashboard does not build against the marketing site. `apps/web/scripts/validate-brands.mjs` reads the dashboard's copy and **exits 1** if the two lists disagree — proven by injecting a wrong name |
| What it never writes | `crm.organization_relationship` has no command, so the role cannot be written from this workspace at all. The preview says so, and a test asserts the request body carries no role field |
| What it never implies | no prospect, no opportunity, no marketing permission. A supplier is never shown as prospect or customer |

**Evidence:** `apps/dashboard` 1,250 passed across 127 files; `apps/web` `npm run validate`
exits 0 with the new gate included. Every button is still `disabled` and the proxy still
allows no POST under `/v2`.

### 2.7.16 The commercial case — schema built 2026-09-22, no command, empty

**What a case could not say.** The commercial case *is* `crm.opportunity`
([`DOMAIN.md`](DOMAIN.md) §3.6) — no second table and no second lifecycle were created. What
the opportunity row could not hold was three facts: every institution named on the case and
what each is *to this case* (`organization_id` is one nullable pointer, and
`organization_relationship` records what an institution is to OrigenLab over all time), what
the case is seeking (nothing below `quote_line` held it, and most cases end before a quote),
and which evidence is the reason to believe any of it (`origin_source_record_id` holds exactly
one record; `crm.activity` records that an interaction happened, not that a belief is
justified).

| Item | Value |
|---|---|
| Migration | `supabase/migrations/20260922170000_slice3_crm_commercial_case.sql` — three tables, five guard functions, nine triggers, grants and RLS. Additive: no existing table, column, constraint, grant or policy changed |
| Inventory | **33 → 36**, `crm` 16 → 19. `tests/010_inventory.sql`, `scripts/verify_chain.sh` and `scripts/replay_evidence.sh` moved in the same change; policies 127 → 139; foreign keys 102 → 118, all still index-covered |
| Where the machine stops | a machine may only ever propose `mentioned`. Every other role, `manufacturer` included, needs a named operator. **(impl)** `confirmation <> 'machine_proposed' OR role = 'mentioned'` — a CHECK, so a machine-decided part is unrepresentable rather than merely discouraged |
| The requesting institution | at most one current per case (partial unique index), always `confirmed` with the operator named (CHECK), and it must equal `crm.opportunity.organization_id` in **both directions** — a DEFERRABLE INITIALLY DEFERRED constraint trigger on both tables, so one command may write the pair in either order. `qualified` therefore now means *an operator decided who is asking*, enforced by the database rather than by the code |
| The supplier exception | a registered supplier or manufacturer is **refused** as requesting institution by a trigger, overridable only by the all-or-none triple (operator, non-blank motive, timestamp), which is confined to that role and **can never be rewritten or erased**. It opens no `prospect` or `customer` relationship and touches no marketing permission |
| Marketing separation | no foreign key runs between the three tables and `outbound.*` in either direction, and none of them carries a consent, opt-in, subscription, audience or campaign column. Both are asserted, not assumed |
| Commands | **none in this change**, by the stated rule that an event nothing can emit is a promise rather than a contract. Six commands and the five event types they emit landed the same day — §2.7.17 |
| Rows | **zero in all three, and zero anywhere.** No case, institution, interest, evidence link or exception was inserted — the clean room's three new probes assert it on every build |

| Evidence | Result |
|---|---|
| pgTAP | **475 across 12 files, all pass** on a fresh `supabase db reset --local` — including the new `062_constraints_crm_commercial_case.sql` at **63 assertions** |
| Guards are load-bearing | each of the eight new rules was re-checked by dropping it in a rolled-back transaction and watching the previously-refused statement succeed. A guard nobody has made fail is a guard nobody has tested |
| `verify_chain.sh --database postgres` | all checks pass: 36 tables, `crm=19`, 139 policies, 0 foreign keys without a covering index, 0 `SECURITY DEFINER` |
| `supabase db lint` / `db advisors` | lint clean; advisors report 122 findings, **all `INFO`** (unused index on an empty database), `--fail-on warn` exits 0 |
| Clean room | **rebuilt from nothing, twice in succession**, both runs ending at **41 probes, all exact** — migrations 23, head `20260922170000`, the three new tables `0`, and every historical count unchanged (`crm.organization` 1,812, `crm.contact_point` 9,460, `crm.domain_event` 22,544, `assertion` 11,485, `contact_control` 10,588). The declared baseline in `supabase/cleanroom/expected_counts.json` moved to migrations 24, head `20260922200000` with §2.7.17, and no count changed because that migration adds no table — **the rebuild that would confirm it has not been run on this branch**; `verify` was not re-run either, and the clean room was only read |
| `cleanroom_failure_tests.sh` | 29 passed, 0 failed |
| Untouched | `origenlab_dev` (quarantined, never opened) and the hosted project (frozen, §2.8). The Slice 0 audit baseline `supabase/audit/baselines/slice0.json` deliberately stays at 33 tables: it describes the frozen hosted foundation, not this branch's head |

### 2.7.17 The commercial case, as six commands — built 2026-09-22, unwired

The tables of §2.7.16 could hold a case and nothing could write one. These are the six
commands that do, and the two database rules they are not allowed to be the only enforcer of.

| Item | Value |
|---|---|
| Migration | `supabase/migrations/20260922200000_slice3_commercial_case_commands.sql` — **no table, no column**; the inventory stays at **36** and `crm` at 19 |
| Boundary | `apps/api/src/origenlab_api/v2/case_commands.py` (the request shapes and their refusals), `case_command_repository.py` (the transactional half), `case_command_routes.py` (six `POST /v2/commands/*`) |
| Shared, not copied | `command_core.py` — the transaction, the receipt, the event stream and the dispatch were extracted from `command_repository.py` and are now one implementation for both command families. Two copies of *"a refused command writes nothing at all"* is one more than can be kept honest |

**The six commands.**

| Command | What it records | The refusal it turns on |
|---|---|---|
| `open_commercial_case` | a case at `lead`, **and** the document that is the reason it exists, in one transaction | a case with nothing behind it is not refused by a check — the origin record is a required field, so it is unrequestable |
| `link_case_evidence` | one typed subject (record, assertion, message or notice) and what it means for the case, `contradicts` included | `evidence_already_linked`; a subject that does not exist is a 404, because this command creates no evidence |
| `add_case_organization` | an institution and its part on this case; for `requesting_institution`, `crm.opportunity.organization_id` moves with it | `supplier_exception_required` — and `supplier_exception_is_not_needed` for a motive nobody needed, which would read later as a supplier OrigenLab sold to |
| `set_case_organization_role` | a `machine_proposed` reading confirmed in place, **or** the current reading closed and a new part opened — both in one transaction | `role` is not in the runtime role's UPDATE grant, so a part **cannot** be edited even by this code; `case_organization_role_unchanged` refuses a confirmation that decides nothing |
| `record_case_interest` | what the case is seeking: product, manufacturer, model text, a quantity | a manufacturer the catalogue contradicts; and `extra="forbid"` makes a request carrying `unit_price` or `currency` a 422 rather than a dropped field |
| `advance_case_stage` | the move, with `closed_at` and the motive when it ends the case | `stage_requires_a_confirmed_requesting_institution`, `closing_a_case_needs_a_motive`, `won_requires_a_quote`, `case_is_closed` |

**Two rules the database now holds too.**

| Rule | Why it is not only in Python |
|---|---|
| The stage machine ([`WORKFLOWS.md`](WORKFLOWS.md) §1.1) | it was a table in a document and a Slice 0 note saying *Slice 2 command trigger*. `crm.opportunity_stage_guard` carries it: a case is opened at `lead` and at no other stage, moves only along the table, and once terminal is refused every target. `advance_case_stage` refuses first, by name. A test reads the transition table **out of the migration** and compares it pair by pair with the Python one, so the two statements of one rule cannot disagree quietly |
| The audit vocabulary | `crm.domain_event` has a closed `aggregate_kind` list, a closed `event_type` list and a validator tying one to the other. Three aggregate kinds and **five** event types were added — `case_organization.added` / `.role_confirmed` / `.ended`, `case_interest.added`, `case_evidence.linked` — and not one more: `case_interest.withdrawn` and `case_evidence.unlinked` stay out because no command writes them. The four `opportunity.*` types are reused rather than duplicated into a `case.*` family, because a case **is** an opportunity |

**Optimistic concurrency stayed at the boundary.** A trigger requiring `version` to advance on
every `crm.opportunity` UPDATE was written and then removed: it would have put the owner,
every migration and every future data repair under an application's bookkeeping rule. Every
case command reads a version, shows it, and writes under `WHERE version = %s`; a test drives
two **live, overlapping transactions** at one case and asserts exactly one wins.

<a id="m-status-abandon-defect"></a>
**A defect found by running the commands, not by reading the constraint.**
`opportunity_organization_required_from_qualified` read `stage IN ('lead', 'qualifying') OR
organization_id IS NOT NULL` — which caught `lost` and `abandoned` too. Those are exits
available from `lead` itself, not stages onward from `qualified`, and a case that never found
out who was asking is precisely the case an operator abandons. **As shipped, such a case
could be opened and then never closed**: its only reachable states were `lead` and
`qualifying`, forever. Nothing had noticed because nothing had ever moved a case. The rule
now names the two exits; `won` keeps the requirement. Corrected in the same migration, with
the failing move as a test.

**A second correction, smaller and in the same family.**
`opportunity_organization_validity` demanded `valid_to > valid_from`, so a role row opened
today could not be closed today — and §3.6.1's own correction procedure is *close `valid_to`
and open the right row*, which an operator almost always performs within the hour. The bound
is now `>=`. A zero-length `daterange` overlaps nothing, so the exclusion constraint is
unaffected, and the withdrawn reading stays readable forever.

| Evidence | Result |
|---|---|
| `apps/api` pytest | **1,333 passed, 183 skipped** in `validate.sh` mode; **206 passed** for the two V2 boundary modules with a migrated disposable database, of which **92** are the new case suite (**31** of them database-backed) |
| Slice 0 evidence suite ([`OPERATIONS.md`](OPERATIONS.md) §4.1) | run whole on a fresh `supabase db reset --local`: **pgTAP 530 across 13 files, all pass**; `verify_direct_logins.sh` **51 passed, 0 failed**; `replay_evidence.sh` — two resets reproduce identical schema, catalogue and migration list (7 schemas, 36 tables, 139 policies); `evidence_tool_failure_tests.sh` **30 passed, 0 failed**; `db lint` clean; `db advisors --fail-on warn` exits 0 with no `warn` or `error`; `cleanroom_failure_tests.sh` **29 passed, 0 failed** |
| pgTAP, by file | the new `063_commercial_case_commands.sql` at **47**; `062` 63 → **69**; `060` 105 → **107**; `010` unchanged at 29. The three older files moved because two shipped rules changed under them, not because an assertion was weakened — each edit replaced a statement the new triggers now refuse first with one that exercises the same CHECK by moving a case instead of inserting one already at that stage |
| `verify_chain.sh --database postgres` | all checks pass: ledger matches all **24** migrations, 36 tables, `crm=19`, **139** policies, 0 foreign keys without a covering index, **0 SECURITY DEFINER**, and the trigger inventory now names `crm.opportunity.opportunity_stage_guard` and `opportunity_never_deleted` |
| `slice0_audit.sh --mode local` | **LOCAL_FAIL, unchanged in kind.** It blocks on a04, a05, a08, a09, a10 — tables 33 vs 36, policies 127 vs 139, foreign keys 102 vs 118, functions now 9 vs 3. This is the **deliberate** drift §2.7.16 recorded: `supabase/audit/baselines/slice0.json` describes the frozen hosted foundation, not this branch's head. This change moves only a05, from 8 to 9, inside a check that was already blocking. `audit_failure_tests.sh` (76/4) fails only its two LOCAL_PASS controls, for the same reason |
| Guards are load-bearing | each of the five new or corrected rules was re-checked by dropping it in a rolled-back transaction and watching the previously-refused statement succeed: the stage guard (a terminal case revives), the never-deleted trigger (a case deletes), the corrected organization rule (under the **old** text, a case with no institution could not be abandoned), the relaxed validity bound (under the old bound, a same-day close was refused), and the event validator (a case event files under the wrong aggregate) |
| `apps/dashboard` vitest | 1,250 passed across 127 files — **unchanged**; no dashboard code was touched |
| `apps/dashboard-proxy` vitest | 132 passed. The allowlist test now names **all eleven** V2 command paths and asserts the Worker forwards neither the method nor the path |
| Atomicity, proven | a test makes `open_commercial_case`'s **second** half fail and asserts the case the first half would have created does not exist, and the key is still free. A second test forces a refusal in a two-write command and asserts the row count, the event count, the receipt count, `organization_id` and `version` are all byte-identical |
| Marketing separation, measured | a test counts **thirteen** tables — `outbound.campaign`, `campaign_recipient`, `contact_control`, `send_attempt`, `crm.person`, `affiliation`, `organization_relationship`, `organization_domain`, `contact_point`, `quote`, `task`, `activity`, `opportunity_participant` — before and after running every command in the vocabulary, and asserts none moved. A second test reads the repository source and asserts it never names `outbound.` at all |
| Simulation | `supabase/scripts/simulate_commercial_case.py` — one whole case in a disposable database: a fictitious university as requesting institution, **Hielscher as supplier *and* manufacturer on the same case and never its customer**, an `origin` link plus a `supports_interest` link, one `UP200Ht` at quantity 1 with no price, a `mentioned` institution later corrected to `purchasing_agent`, ending at `qualified`. **10 receipts, 13 events, 1 refusal** (`supplier_exception_required`). The clean room was fingerprinted before and after, read only: **unchanged** |

**What is still not decided.** No command has been run against real evidence.
`crm.opportunity`, `crm.opportunity_organization`, `crm.opportunity_interest` and
`crm.opportunity_evidence` are **empty**, `platform.command_receipt` is empty, all 31 staged
records are `pending`. `apps/dashboard-proxy` allows no POST under `/v2`, every button is
`disabled`, and the routes are mounted only behind `ORIGENLAB_V2_COMMANDS_ENABLED`. Nothing
touched `origenlab_dev` (quarantined, never opened), `origenlab_clean` (read only, fingerprint
unchanged), the hosted project (frozen, §2.8), Gmail, Drive, any campaign or any send.

### 2.7.18 The commercial case, as a screen — built 2026-09-22, read-only

§2.7.17 left six commands in `apps/api` and no way to look at what they would write. This is
the reading half: two GET routes, one section in the dashboard, and every one of the six
actions rendered **disabled** with the reason attached.

| Item | Value |
|---|---|
| Migration | **none**. No table, no column, no policy: the inventory stays at **36**, `crm` at 19, the ledger at **24** migrations with head `20260922200000` |
| Read routes | `GET /v2/cases` and `GET /v2/cases/{id}` in `apps/api/src/origenlab_api/v2/routes.py`, served by `V2Repository.cases()` / `.case_card()` — inside `begin read only`, as `origenlab_api` |
| Proxy | two literal paths added to the allowlist, GET-only. **No POST under `/v2` was added**, so none of the six commands is reachable from a browser |
| Dashboard | section `casos` (`#/casos`), `pages/CommercialCasePage.tsx`, with `lib/commercialCase.ts` (the vocabulary) and `lib/caseCommands.ts` (the six previews) as pure, unit-tested modules |

**The stage machine is served, not copied.** `GET /v2/cases/{id}` attaches
`stage_machine` — `origenlab_api.v2.case_commands.STAGE_TRANSITIONS` verbatim, the same table
`crm.opportunity_stage_guard` enforces. The dashboard reads it. A third statement of the rule
in a browser would be the one nobody re-read, and this is the change that stops it existing.

**What the card refuses to flatten.** `crm.opportunity_organization` never rewrites a part —
changing one closes a row and opens another — so the card returns closed rows beside current
ones, with `is_current` carrying the distinction, and counts the two separately. Withdrawn
interests and unlinked evidence are returned on the same principle. A card showing only the
current state would make the audit trail invisible exactly where it matters.

**A case with no institution renders as a state, not a gap.** The list reports
`requesting_organization_*` as null and the screen says *nobody has said who is asking*,
naming it as legitimate until `qualified`. Nothing on the screen picks an institution, a
role, a reading or a stage: every preview computes from what an operator has chosen, and on
an untouched screen that is nothing — so all six are blocked, each stating what it still
needs.

| Evidence | Result |
|---|---|
| `apps/api` `validate.sh` | **1,346 passed, 193 skipped** |
| New API suite | `tests/test_v2_case_read_boundary.py` — **23 passed** with a migrated disposable database (**13** of them run without one). The database-backed ones build the case by running the **real commands** and then read it back, so the two halves are proven to agree rather than a query being asserted against rows shaped to make it pass |
| `apps/dashboard` `npm run validate` | **1,319 passed across 130 files**, build clean. 56 of them are new: `commercialCase.test.ts` (19), `caseCommands.test.ts` (37) |
| Page tests | `CommercialCasePage.test.tsx` — **12 passed**, including that all seven action affordances on screen are `disabled`, that every preview is `blocked`, that no request body is ever rendered, and that a failed load is never drawn as "there is nothing" |
| `apps/dashboard-proxy` `npm run validate` | **132 passed**. The allowlist now names ten listing paths and three card shapes; `/v2/cases/{id}/organizations`, `/v2/cases/{id}/stage`, `/v2/cases/open` and an uppercase UUID are each refused |
| Clean room | rebuilt twice from scratch with this code and verified: **41 probes, all exact**, byte-identical across both builds and a third standalone `verify`. 24 migrations, head `20260922200000`; `crm.opportunity*` all **0**; both `outbound.send_control` flags false |
| Fixtures | every case fixture is invented, in `apps/dashboard/src/lib/__fixtures__/commercialCase.ts` and the API test's own `world`. No real institution appears, and the API tests run in a database the harness created moments earlier and drops afterwards |

**What is still not decided.** Nothing changed about what has been executed: `crm.opportunity`
and its three tables are still **empty**, `platform.command_receipt` is still empty, all 31
staged records are still `pending`. The screen has no form yet — no title field, no
institution picker, no stage selector — because wiring inputs to previews that cannot be sent
would be building the half that is cheap and leaving the decision that is expensive. Nothing
touched `origenlab_dev` (quarantined, never opened), the hosted project (frozen, §2.8), Gmail,
Drive, any campaign or any send.

### 2.7.19 Contacto 360 e Institución 360 — the operator surface, 2026-09-23

§2.7.18's `#/casos` proved the commercial-case model renders. It was not an operating CRM:
the case was the centre of the screen and roughly half the surface was the six blocked
commands, their request bodies and their audit explanations. This change is **UI only** —
no migration, no command, no API route, no proxy path — and it moves the entry point to the
contact and the institution.

| Item | Value |
|---|---|
| Migration | **none**. Inventory stays at **36** tables, `crm` at 19, ledger at **24** migrations, head `20260922200000` |
| API | **unchanged**. No route added, none altered. The joins these screens show are composed in the browser from `GET /v2/cases` and `GET /v2/quotes/followup` — **superseded the next day by §2.7.20**, which replaced the case half of that with a real read route, because the browser-side join could only ever find the cases an institution was *asking* in |
| Proxy | **unchanged**. Still no POST under `/v2`; the allowlist was not touched — §2.7.20 later added one GET path to it |
| New sections | `contactos` (`#/contactos`) and `instituciones` (`#/instituciones`), each with a `?id=<uuid>` detail view. **Both are in the sidebar**, which therefore goes from 8 items to 10 — an owner decision of 2026-09-23 |
| New dashboard files | `pages/Contact360Page.tsx`, `pages/Institution360Page.tsx`, `lib/crm360.ts`, `lib/v2DeepLink.ts`, `lib/useV2Relations.ts` (replaced by `useV2FollowUpQuotes.ts` + `useV2OrganizationCases.ts` in §2.7.20), `components/v2/{V2Chip,V2Panel,V2TechnicalDetails,V2CaseSummaryCard}.tsx` |
| Rewritten | `pages/CommercialCasePage.tsx` — a six-line summary card per case, with the six command previews, the request bodies, "qué registraría", the stage machine and the raw ids moved into one closed «Detalles técnicos» drawer |
| `crm-v2` | kept as the technical console, minus its card drawer: every contact and organization name in it now navigates to the corresponding 360 screen instead of opening a second, smaller copy of the card |
| `contacts` ("Clientes") | **untouched and still in the sidebar.** It reads the V1 lead-intel mirror and holds rows the V2 core will not carry until the V1 durable migration lands |

**A channel with no recorded owner renders as a pending channel.** The heading of such a card
is the address itself, under an amber «canal pendiente · nadie identificado todavía» band and
the reason no person is named. Deriving a name from a local part would be an identity
inference drawn as a fact, and this is the screen where that would be easiest to do by
accident.

**Nothing on these screens reports marketing permission, because nothing records one.**
`outbound.contact_control` holds blocks, cooldowns and the fact of prior contact and no
permission at all, so the neutral state is *sin permiso registrado* and never *se puede
contactar*; prior contact is shown with the sentence that says it is not consent. The state
is labelled as belonging to the **address**, not to the person.

**The browser-side joins claim only what a row supports.** Cases attach to a contact or an
institution through `requesting_organization_id`; quotes attach through `opportunity_id`
alone, never by matching an institution name. Both lists page and neither endpoint accepts an
institution filter, so when one page does not cover the total the screen says so rather than
letting a short list read as "none". A case card drawn without the case's own card reports
the list's counts instead of asserting absences it never measured.

**The case half of that was wrong, and §2.7.20 replaced it.** `requesting_organization_id`
is the *only* institution `/v2/cases` carries, so the join found an institution's cases only
where it was the one asking — a supplier or a manufacturer read as uninvolved in the very
deals it was named on. The quote half stands: it is joined on a real key and its coverage is
still declared.

| Evidence | Result |
|---|---|
| `apps/dashboard` `npm run validate` | **1,371 passed across 134 files**, build clean (was 1,319 across 130) |
| New unit suites | `crm360.test.ts` **16**, `v2DeepLink.test.ts` **5**; `commercialCase.test.ts` grew 19 → **25** for the summary |
| New page suites | `Contact360Page.test.tsx` **11**, `Institution360Page.test.tsx` **9** |
| Rewritten page suites | `CommercialCasePage.test.tsx` **15** (was 12) — including that the drawer holding the six commands starts closed and contains all of them; `CrmV2Page.test.tsx` **8**, now asserting navigation to the 360 screens and the absence of the drawer |
| `apps/dashboard-proxy` `npm run validate` | **132 passed**, unchanged — nothing in the allowlist moved |
| Fixtures | `lib/__fixtures__/crm360.ts`, all invented, every address under the reserved `.invalid` TLD. No real address is in Git |
| Local run | the six screens were driven against `origenlab_clean` through a local `apps/api` and `vite`, and screenshotted. Every `/v2/*` request returned 200; the only errors in that run were V1 mirror endpoints the V1 data provider calls, which this environment does not serve |

**What is still not decided, and what was not touched.** No command became reachable: the
proxy still admits no POST under `/v2`, and the six previews remain disabled. `crm.quote` is
still empty, so every "cotizaciones" panel reads zero and says why. Nothing touched
`origenlab_dev` (quarantined), the hosted project (frozen, §2.8), Gmail, Drive, any campaign
or any send, and no row was written to the clean room.

### 2.7.20 An institution's own cases, and the V1 chrome above a V2 screen — 2026-09-23

Two defects found by looking at §2.7.19's screens rather than at its tests.

**1. Institución 360 showed only the cases an institution was asking in.** The screen
crossed one page of `GET /v2/cases` in the browser on `requesting_organization_id`, which is
the only institution that list carries. An institution that *supplies*, *manufactures*,
*pays* or is *named* on a case appeared in none of them: on the clean room's one real case,
Universidad Austral de Chile showed the case and Hielscher Ultrasonics — its supplier **and**
its manufacturer on that same case — showed "no aparece pidiendo en ningún caso". That is a
false negative about a commercial relationship, produced by a join that could not have
answered the question.

**2. The shell's V1 status chrome sat above the V2 screens.** "Estado: BLOQUEADO" is the V1
daily core run's verdict and "SQLite local" is which store the V1 operator mirror is served
from. Neither is a fact about a page that reads the durable core over `/v2`, and this local
stack serves no V1 mirror at all — so an operator read *BLOQUEADO* above a screen that was
working exactly as designed.

| Item | Value |
|---|---|
| Migration | **none**. Inventory stays at **36** tables, ledger at **24**, head `20260922200000` |
| Data | **none written anywhere.** No command, no POST, no Gmail, no Drive, nothing touched `origenlab_dev` (quarantined) or the hosted project (frozen, §2.8) |
| New API route | **one** `GET` — `/v2/organizations/{id}/cases`. Read boundary routes **11 → 14** in §2.7.2 (the count there had also not been updated for the two case routes of §2.7.18) |
| What it reads | `crm.opportunity_organization` by `organization_id` — a recorded part row and nothing else. No name match and no mail domain: a domain is a routing hint, never an identity key ([`DOMAIN.md`](DOMAIN.md) §2.2) |
| Shape | one row per case, in `/v2/cases`' own shape, plus `roles` — **a list**, current and closed, each carrying `role`, `confirmation`, `valid_from`, `valid_to` and `is_current`. One institution routinely holds two parts on one case; a single label would have to pick one and be wrong |
| Missing organization | **404**, not an empty page. "No such institution" and "this institution is in no cases" send an operator in opposite directions |
| Proxy | **one GET path added** — `/^\/v2\/organizations\/<uuid>\/cases$/`, spelled out rather than widened to `/<uuid>/.+`. Still **no POST under `/v2`**, and the case commands remain unreachable from the browser |
| Dashboard | `lib/useV2Relations.ts` split into `lib/useV2OrganizationCases.ts` (the new route) and `lib/useV2FollowUpQuotes.ts` (quotes only, still a browser-side join on `opportunity_id`, still declaring its coverage) |
| Applied to | Institución 360 **and** Contacto 360 — the contact screen reached its cases through its institution and carried the same defect |
| V2 chrome | `isV2ReadOnlySection` in `lib/dashboardNav.ts`. On `contactos`, `instituciones`, `casos`, `crm-v2` and `revision` the shell drops the verdict chip, the backend chip and the "Actualizar" button (it reloads the V1 payload, which none of these pages read) and shows «Datos locales de revisión» and «Sólo lectura». On every V1 section the chrome is unchanged |

| Evidence | Result |
|---|---|
| `apps/api` full suite | **1,349 passed, 195 skipped** |
| `test_v2_read_boundary.py` | **57** (was 51): a 404 for an unknown institution, the bounded page, and a malformed id that never reaches a query |
| `test_v2_case_read_boundary.py` | **26** (was 23), including **3 database-backed** ones that build their own `origenlab_test_<hex>`, drive the **real commands**, and then read back: that a case where the institution was first `mentioned` and then made `requesting_institution` returns **both** parts with the closed one marked; that an institution which merely manufactures the catalogued product is in **zero** cases; and that an absent id is `None` |
| `apps/dashboard` `npm run validate` | **1,380 passed across 134 files**, build clean (was 1,371) |
| Suites grown | `crm360.test.ts` 16 → **19**, `Institution360Page.test.tsx` 9 → **13**, `DashboardApp.test.tsx` 15 → **17** (the chrome is asserted present on a V1 section and absent on all three V2 ones) |
| `apps/dashboard-proxy` `npm run validate` | **132 passed**; the new path is asserted allowed, and `/quotes`, a nested id and an uppercase id are asserted refused |
| Against the clean room | Hielscher Ultrasonics now returns the case with `roles` = `supplier` + `manufacturer`, both current; UACh returns the same case as `requesting_institution`. Verified through the API **and** through the dev proxy, then screenshotted at 1440 px and at 390 px |

**Pre-existing and not touched.** Below roughly 700 px the shell's sidebar does not collapse
by itself, so the content column is squeezed to about 130 px on any section — V1 and V2
alike, and identically before this change. Collapsing the sidebar by hand renders correctly.
That is a shell IA decision, not part of either defect above.

### 2.7.21 Durable connections on the organization list and the two cards — 2026-09-24

The two 360 cards now reach the whole commercial picture through the durable core, and the
organization list is ranked by it. **Every connection is a foreign key someone recorded** — no
name match and no mail domain, because a domain is a routing hint and never an identity key
([`DOMAIN.md`](DOMAIN.md) §2.2).

| Item | Value |
|---|---|
| Migration | **none**. Inventory stays at **36** tables, ledger at **24**, head `20260922200000` |
| Data | **none written anywhere.** Read-only code; no Gmail, no Drive, nothing touched `origenlab_dev`, `origenlab_clean` or the hosted project (frozen, §2.8) |
| New routes | **none**. The shapes of `GET /v2/organizations`, `/v2/organizations/{id}` and `/v2/contacts/{id}` grew; the proxy allowlist is unchanged |
| An organization reaches a case through | `crm.opportunity.organization_id` (the confirmed requesting institution) **or** a *current* `crm.opportunity_organization` row (`valid_to is null`), whatever its role. A closed part row is history on the case card and does not count as a connection today. One organization holding two parts on one case, or both the column and a part row, is **one** case |
| A contact point reaches a case through | a *current* `crm.opportunity_participant` row naming that contact point, **or** naming the durable person `crm.contact_point.person_id` records for it. Never from the address, its local part or its domain |
| Organization list ranking | `GET /v2/organizations` is ordered by case count, then open cases, then latest case activity (nulls last), then confirmed people, then channels, then name and id. Each row now carries `confirmed_people_count`, `case_count`, `open_case_count`, `interest_count`, `quote_count`, `activity_count` and `last_activity_at` beside the existing `contact_point_count`. It was ordered by name only |
| Organization card | adds `cases` (each with the `roles` this organization holds on it), `interests` (withdrawn ones excluded; catalogued product and maker where recorded), `quotes` (the **latest** revision only, plus `revision_count`; a quote with no revision is still listed), `activities`, `case_evidence` (still-linked links only) and a `connection_summary`. It also adds `address_controls` (on the email contact points recorded against it), `domain_controls` (on the domains in `crm.organization_domain`, not domains parsed out of addresses) and `marketing` (the campaign-recipient rows of its own contact points) |
| Contact card | adds the same `cases` / `interests` / `quotes` / `activities` / `case_evidence` / `connection_summary` block, with the participant `roles` per case |
| Bounded lists, truthful counts | every new list is capped at `CARD_CHILD_LIMIT` (**100**); every count in `connection_summary` and in `counts` is computed over the same connection set **without** the cap, so a list of 100 beside a count of 340 tells the truth. The list row and the card it opens use the same predicate, so their numbers agree |

| Evidence | Result |
|---|---|
| `test_v2_crm_connections_read.py` | **10 passed**, all database-backed, in their own `origenlab_test_<hex>` (created, migrated and dropped by `v2_command_harness`), read as the `origenlab_api` login. The fixture holds one institution, a decoy with the same name stem and the same mail domain, a maker, a withdrawn interest, a closed part row, a two-revision quote (void then draft) and a draft campaign with two recipients. They assert that the institution ranks first with the right counts, that the decoy, the maker and an address on the institution's domain but recorded against no organization reach nothing of the institution's, that **marketing is non-empty** for the institution's desk and empty for the decoy, and that reading every card changes no table (content digest before and after) |
| Focused V2 read suites | `test_v2_crm_connections_read.py` + `test_v2_read_boundary.py` + `test_v2_case_read_boundary.py`: **84 passed, 9 skipped**. The 9 skips are `test_v2_read_boundary.py` checks that need a populated target database, not the disposable one |
| `apps/api` `scripts/validate.sh` | **1,353 passed, 215 skipped**, with `ORIGENLAB_POSTGRES_URL`, `ORIGENLAB_V2_DATABASE_URL`, `ALEMBIC_DATABASE_URL` and both test DSNs unset. The new suite skips there without its DSNs |
| Not covered yet | marketing is covered by one draft campaign with `snapshotted` recipients only — no sent, bounced or replied state. No fixture yet holds a list longer than the cap, so the "list ≤ cap ≤ count" rule is asserted structurally. The dashboard has not been changed to show the new fields (done in §2.7.22) |

### 2.7.22 List filters, and the dashboard reads the connections — 2026-09-24

The three V2 lists can be narrowed on the server, and `#/instituciones`, `#/contactos` and
`#/casos` now show what §2.7.21 made the API return. Still read-only; still no name or domain
inference.

| Item | Value |
|---|---|
| Migration / data / new routes | **none** / **none written** / **none**. Only query parameters on existing paths, so the proxy allowlist (exact paths) is unchanged |
| `GET /v2/organizations` | `has` (repeatable: `contacts`, `people`, `cases`, `open_cases`, `interests`, `quotes`; every one must hold) and `active_within_days`. Both filter the same CTE metrics the row reports, and the total is counted over the same joins. Unknown `has` → 422. **Commercial side**: `segment` = `customers` (requesting institution on a case, or a current `customer` relationship), `suppliers` (supplier or manufacturer on a case, or a current `supplier`/`manufacturer` relationship), `others` (end user, purchasing agent, funder or mentioned); omitted = all; unknown → 422. Segments overlap when roles coexist. Rows gain `cases_as_<role>` for all seven case roles (distinct cases, current part rows; asking also counts `opportunity.organization_id`) and `relationship_roles`; the response gains `facets` (count per segment under the same filters). Order leads with the segment's own count, requesting cases under "all" |
| `GET /v2/contacts` | `q` now matches the address **or** the recorded person's name **or** the recorded institution's name, each through the row's own foreign key. `identity` = `person` / `organization_mailbox` / `unattributed` (from `person_id` and `organization_id` only) and `with_cases`. Rows gain `case_count` (current participant rows, the card's predicate) and `address_control_count`. Order: recorded person first, then an institution's mailbox, then unattributed, then address |
| `GET /v2/cases` | `stage` repeatable (`lead`+`qualifying` is the prospect view), `organization_id` (any current part), `organization_q`, `interest_q` (non-withdrawn interests: product name or model number, model text, description), `quote_state` (`none`, `any`, or the latest revision's status), `active_within_days`. Rows gain `participants` (every current part with its exact role), `interest_labels`, `quote_count`, `latest_quote_status`, `last_activity_at` |
| Dashboard | Instituciones opens on **Clientes / instituciones solicitantes**, with tabs for *Proveedores y fabricantes*, *Otras instituciones participantes* and *Todas*, each with its server count; each card shows "Papel en casos" (Pide, Proveedor, Fabricante, Financia, Mencionada — plus end user / purchasing agent when held — and total) and any recorded relationship. Ranked cards with initials avatar (no logo lookup), kind, confirmation, seven counts, last activity, filter chips. Institución 360: people and affiliations, requested equipment, durable quotes (latest revision), commercial activity, case evidence, address/domain controls and campaigns. Contactos: identity tabs, search across person/institution, case and control counts. Contacto 360: cases it *participates* in (the person's role) kept apart from its institution's cases, so an absent requesting institution never reads as "no cases". Casos: filter bar; the summary card shows every part by exact role (incl. `mentioned`), origin, durable quote state and last activity. Empty states say "not recorded yet" |
| Evidence | `test_v2_crm_connections_read.py` **18 passed** (8 new: each filter, the segments — a supplier/manufacturer never listed as a customer, a recorded supplier-and-customer in both, facets — per-role counts over current parts only, the contact search refusing a shared domain, row counts, case row fields, and filtered reads writing nothing) in a disposable database. `apps/api` `scripts/validate.sh`: **1,458 passed, 217 skipped**. `apps/dashboard` `npm run validate`: **1,425 passed, 0 failed**, build ok. The three `DashboardApp` shell suites (29 tests) had failed since `3acac9c9` because the Google-login `AuthGate` rendered *No se pudo verificar la sesión*: they did not answer `/auth/session`. They now stub that one request as a signed-in operator (`src/test/mockAuthSession.ts`); every other request still reaches the suite's own mocks, and neither `AuthGate` nor the Google login changed. Re-measured 2026-09-24 against `origenlab_clean` (read-only session): `/v2/organizations` 1,813 (2 with a case: 1 customer — Universidad Austral de Chile — and 1 supplier-and-manufacturer, 0 others), `/v2/contacts` 9,460 (0 confirmed persons), `/v2/cases` 1; interests, quotes and activity are 0 |

### 2.7.23 Historical quotations — schema, commands, import planner and cockpit reads, 2026-09-25

Built locally, **committed locally, not pushed**. The migration was **installed into `origenlab_clean` on 2026-09-26**
(owner-approved; DDL and ledger row in one transaction, after a `pg_dump -Fc` backup; ledger 24 → 25,
`supabase/cleanroom/expected_counts.json` moved with it). Not applied to `origenlab_dev` or any hosted
project. `crm.quote` is still **0** in every persistent database: the import itself has not run.

| Item | Value |
|---|---|
| Migration | `20260925200000_slice3_historical_quotation_import.sql` — ledger **25**, tables still **36**. `crm.quote.number_origin` (`minted` globally unique / `printed_historical` unique per opportunity, gate G1); `crm.quote_revision.origin` (`historical_import`: document hash, `sent_at`, evidence record required; totals/approval/snapshot/currency optional); trigger `quote_revision_historical_guard` (immutable, never deleted, only `sent → void` and one supersession); assertion `resolved_kind` gains `quote` / `quote_revision`; event `quote_revision.historical_recorded`. Foreign keys 118 → **119** (33 implied-partial) |
| Commands | `record_historical_quotation`, `void_historical_quote_revision` (`apps/api/src/origenlab_api/v2/quote_import_*.py`). **Unwired**: no route, no proxy entry |
| Evidence staging | `stage_gmail_drive_evidence.py` now accepts a Gmail `document_reference`, only as `sha256:<hex>` of an attachment listed in the record's own payload |
| Import planner | `apps/api/scripts/quote_crm_import.py` — dry run by default; `--apply` refuses every database but `origenlab_test_<8 hex>` |
| Evidence | pgTAP **548 / 14 files** pass (new `064_historical_quotation_import.sql`); `verify_chain.sh` and `verify_direct_logins.sh` (51) pass on the CLI cluster; `apps/api` `validate.sh` **1,748 passed, 243 skipped**; with test DSNs set, the V2 DB-backed suites pass |
| Rehearsal | against a disposable copy of `origenlab_clean` only, with **simulated** organization confirmations: 344 commands, 583 events; the second dry run predicts 0 writes and the second apply replays all 344 with 0 new events |

### 2.7.26 V1 read routes closed in the dashboard proxy, 2026-09-26

Local only, **committed locally, not pushed, not deployed** — the Worker in Cloudflare still forwards both
prefixes until this change is deployed. Owner decision 2026-09-26: V1 stays disabled in the
browser until it is decommissioned or its routes are migrated behind the V2 role model
([`OPERATIONS.md`](OPERATIONS.md) §2.1).

| Item | Value |
|---|---|
| Removed from `apps/dashboard-proxy/src/allowlist.ts` | `/^\/contacts\/[^/]+$/` and `/^\/mirror\/.+/`. Both now answer **403 `path_not_allowed`** without reaching upstream; POST answers 405 |
| Why | upstream they are gated only by the shared API key — no operator identity, no role, no redaction — so any person past Cloudflare Access read contact addresses unmasked, including a `viewer` whom `/v2` masks (§2.7.2, *Contact addresses by role*) |
| Still reachable through the proxy | `/health`, `/operator/*`, `/cases/warm`, `/opportunities/commercial*`, `/operations/*`, the named `/v2/*` reads and `/auth/*`. **Not** `/v2/workspace/*` or `/v2/cockpit/*`, which stay unlisted |
| `apps/api` | **unchanged.** The V1 routes still exist and still answer a direct caller holding the API key; closing them is a browser-boundary decision, not a decommission |
| V1 panel effect | Catálogo, Proveedores, Prospectos, the lead-intel "Clientes" list, the commercial-deals and Gmail-interaction mirror audits, and the V1 contact drilldown panel show a load error. No dashboard code was changed |
| Evidence | `apps/dashboard-proxy` `npm run validate` **165 passed** (was 153): every former `/contacts/*` and `/mirror/*` smoke path asserted refused on GET, with a query string, and on POST; `handleRequest` returns 403 and never calls `fetch` for five V1 paths, 405 for POST; all 18 `/v2/workspace/*` and `/v2/cockpit/*` paths asserted refused. `npm run typecheck` clean |

### 2.7.5 Local V2 — the reconciled picture, 2026-09-21

One table for "what is actually in the local V2 database and does it add up". The per-stage
detail is §2.7 through §2.7.4; this is the reconciliation across them.

| Table | Rows | Reconciles as |
|---|---|---|
| `evidence.source_record` | 24 | the four migration manifests + the 20 Gmail records of §2.7.7 |
| `evidence.assertion` | 11,474 | = 11,272 promoted + 4 ambiguous + 172 unresolved (migration) + 26 unresolved (Gmail, §2.7.7) |
| `crm.organization` | 1,812 | part of the 11,272 promoted |
| `crm.contact_point` | 9,460 | the rest of the 11,272 promoted |
| `crm.person` | **0** | no display name exists in the evidence |
| `crm.domain_event` | 22,544 | 11,272 `assertion.promoted` + 9,460 `contact_point.created` + 1,812 `organization.created` |
| `outbound.campaign` | 3 | the three archived V1 campaigns |
| `outbound.campaign_recipient` | 3,481 | = 3,252 linked + 229 never contacted |
| `outbound.send_attempt` | 3,141 | 3,138 reachable from a canonical identity |
| `outbound.contact_control` | 10,588 | 10,396 overlap a canonical channel |
| `comms.mailbox` | 1 | |
| `outbound.send_control` | 1 | both flags `false` |

**Review queue.** 4 ambiguous assertions (two near-duplicate organization-name pairs), 172
`supplier_candidate` left unresolved, 1,812 + 9,460 `machine_proposed` rows awaiting
confirmation, and 2,860 personal-shaped addresses recorded as `unattributed` with no person
created. There are **no duplicates to resolve**: nothing was merged, so nothing needs
un-merging.

**Idempotency, measured.** Every stage was run twice against the real artifacts. Second runs:
import **0 rows**, promotion **0 rows and 0 events**, marketing linkage **0 links**.

**API contract, measured** (§2.7.2). All seven routes: **401** with no identity, **200** with
an active operator. `limit > 200` → **422**; `offset < 0` → **422**; an unlisted `/v2` path →
**404**. Totals served: contacts 9,460, organizations 1,812, review summary populated, and
prospects / active opportunities / tasks due / quotes follow-up all **0** for the reason in
§2.7.2.

**Durability.** Checkpoints of each stage are held outside Git under
`~/data/origenlab-v2-local/checkpoints/` (`0700`/`0600`, `.sha256` + `.meta.json`), and a
checkpoint → restore round trip has been exercised at full volume: 19 migrations replayed and
28,666 rows reinstated with exact counts.

**Screenshots** of the four CRM cards are in `~/data/origenlab-v2-local/screenshots/`,
outside Git.

**What is still missing, and why.** Email-capture evidence and quote-linkage evidence do not
exist yet: V1 persists no Gmail message or thread id ([`DATA.md`](DATA.md) §6), and V1's
durable opportunities, tasks and quotes have never been migrated. Both are recorded in
[`OPERATIONS.md`](OPERATIONS.md) §1.2 under *What this runbook does not yet cover*. Since
2026-09-21 the first of those has a path *and* a first batch: the offline staging tool of
§2.7.6 has staged 20 real Gmail records (§2.7.7). They are review candidates, not linkage —
no message is attached to a quote, a person or an opportunity, and `crm.*` is unchanged by
them.

### 2.8 Hosted phase — frozen 2026-09-21

**State: frozen.** The operator closed the hosted phase on 2026-09-21 and moved all V2 work
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
| Render | `origenlab-api` (Docker, 1 GB disk), `origenlab-dashboard` (static), managed Postgres. **No Render cron jobs.** |
| Cloudflare | `apps/dashboard-proxy` Worker at `dashboard.origenlab.cl/api*` |
| Operator machine | `auto-refresh-mail` every 3 min; `auto-mirror-dashboard` every 1 min; systemd user units for the local API |
| HostGator (cPanel) | the public site `origenlab.cl`, served from the cPanel document root behind a Cloudflare proxy. Files still arrive by **manual upload** |
| GitHub Actions | 8 workflows; **none is scheduled** — all are push/PR path-filtered or `workflow_dispatch` |
| GitHub Actions → cPanel | `web-deploy`, two jobs. `build` runs `npm ci` and `npm run validate` and publishes the validated `dist/` as an artifact; it holds no secret and no environment. `deploy` mirrors that artifact into the cPanel public folder over SSH + rsync, and is **`workflow_dispatch` only, in the `production` environment** — a push to `main` never creates it, so no push can open an SSH connection. `apply=false` stops after the rsync dry run. **Never run against the real host:** the `production` environment, its five secrets (`CPANEL_HOST`, `CPANEL_PORT`, `CPANEL_USER`, `CPANEL_SSH_KEY`, `CPANEL_WEB_ROOT`) and the confirmed document root do not exist yet, so every run stops before opening a connection |

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
