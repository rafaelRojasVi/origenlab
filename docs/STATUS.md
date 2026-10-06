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

Last verified: **2026-10-02** (§2.7.41 added 2026-10-03 from the branch's own `npm run validate`, nothing deployed; §2.7.49 added 2026-10-04 from the branch's own validation, nothing deployed; §2.7.51 added 2026-10-05 from the branch's own validation, nothing applied or deployed), against `origin/main` (`7c6a7fef`, the merge of #620) for the
hosting and GitHub facts of §2.4, §2.7.40, §2.8, §3.1 and §3.3 — read from the GitHub API, the
public DNS and HTTP edge, the repository, and (later the same day) the Render API, the FastAPI
Cloud CLI and the Cloudflare DNS table, all read-only, with nothing deployed, provisioned,
purchased or mutated. Local database counts were last measured 2026-09-30 (§2.7.39) and are not re-measured
here. Earlier sections as last measured 2026-09-26–29, measured from the
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
| Refusal tests | **30** — `supabase/scripts/cleanroom_failure_tests.sh`. Connects to nothing |
| Contract tests | `supabase/scripts/cleanroom_verify_tests.sh` — static **13** (no database; in CI), chain **9** (disposable database, full chain, seven injected faults; in CI), restored copy **3** (development container only). §2.7.39 |
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
| Cockpit reads | nine `GET /v2/cockpit/*` routes (KPIs, work queue, opportunities, quotations, timeline, evidence drawer, search). Mounted in `apps/api`; **not** in the proxy allowlist, no dashboard screen yet |
| Evidence | pgTAP **548 / 14 files** pass (new `064_historical_quotation_import.sql`); `verify_chain.sh` and `verify_direct_logins.sh` (51) pass on the CLI cluster; `apps/api` `validate.sh` **1,748 passed, 243 skipped**; with test DSNs set, the V2 DB-backed suites pass |
| Rehearsal | against a disposable copy of `origenlab_clean` only, with **simulated** organization confirmations: 344 commands, 583 events; the second dry run predicts 0 writes and the second apply replays all 344 with 0 new events |

### 2.7.24 Case-first Drive archive for quotations — built 2026-09-26; step A executed, step B not run

Built locally, **committed locally, not pushed**. No schema change: the design reuses `crm.external_identifier`
(`drive_folder`), `evidence.source_record` (`drive_file`) and `evidence.assertion`
(`document_reference` → `quote_revision`). The CRM still holds **0** quotations; nothing here writes it.

| Item | Value |
|---|---|
| Drive, measured 2026-09-26 (read-only inventory) | `Cotizaciones/Casos` exists with **4** case folders / 4 PDFs (the 2026-09-25 Gmail tail, uploaded and hash-verified by the owner-approved case upload). Legacy `Pendientes` (20 items) and `Enviadas` (218 items: 107 folders, 111 PDFs) **untouched** — recursive fingerprints identical before/after every run |
| Archiver | `apps/api/src/origenlab_api/v2/quote_case_archive.py` — one folder per case keyed by `origenlab_case_key`, one file per document keyed by `origenlab_document_sha256`; Drive checksum + fresh-download verification; journal for retries; run stamp for exact rollback (trash, never delete); legacy parents refused; wrong account refused; `OverlayDrive` check mode. CRM status and archive status are separate types; archive status never feeds the lifecycle label |
| Legacy reconciliation + migration planner | `v2/quote_drive_legacy_migration.py` + `scripts/quote_drive_case_migration_dryrun.py` — dry run only. 238 legacy items: 93 confirmed PDFs → 55 cases; 20 blocked (13 owner-blocked, 2 confirmed only at email level and **missing from the import plan**, 4 unreviewed, 1 unknown); 14 not archived (brochures, spreadsheets, 1 reject); 7 number collisions. A further 45 confirmed PDFs are in **no** Drive folder (Gmail only) |
| Executor | `scripts/quote_drive_case_archive.py` — `--step legacy|gmail|all`, check mode by default (reads live Drive, writes in memory). **Step A executed 2026-09-26 12:58Z (owner-approved)**, run `legacy-20260926T125804Z`: 55 case folders + 93 PDFs (148 writes, nothing else), every file fresh-download verified, legacy fingerprints identical to the pre-run inventory, idempotency rerun 0 writes. `Casos` now holds **59** case folders. Step B (45 Gmail-only PDFs) **not run**; CRM links recorded only in a local `archive_links.jsonl`, not in the CRM |
| Case workspace | `v2/quote_case_workspace.py` + `GET /v2/cockpit/case-archive` (mounted only with `ORIGENLAB_V2_CASE_ARCHIVE_DIR`; files only, no DB); dashboard `#/archivo` (registry only, not sidebar; not in the proxy allowlist) |
| Evidence | new tests: archive 54, legacy migration 17, workspace 10, dashboard lib 4 + nav 1. `apps/api` `validate.sh` **1,919 passed, 244 skipped**; `apps/dashboard` `npm run validate` **1,436 passed**, build ok |

### 2.7.25 CRM workspace — read-only dashboard over the imported CRM, built 2026-09-26

Built locally, **committed locally, not pushed**, read only. Nothing here writes the CRM, Drive or Gmail.

| Item | Value |
|---|---|
| `origenlab_clean`, measured read-only 2026-09-26 after the quotation import | `crm.opportunity` **44** (43 `quoting`, 1 `lead`), `crm.quote` **65** (all `printed_historical`), `crm.quote_revision` **69** (all `sent`), `crm.organization` 1,821 (9 confirmed), `crm.contact_point` 9,460 (0 linked to an organization or person), `crm.person` / `affiliation` / `task` / `activity` / `external_identifier` / `comms.message` / `catalog.product` / `outbound.campaign_reply` **0**, `outbound.campaign` 3 |
| API | `v2/crm_workspace.py` + `crm_workspace_routes.py`: six `GET /v2/workspace/*` routes (overview, pipeline, providers, marketing, drive, review); viewer role or above, read-only transaction. Each counted entity carries a provenance (`imported` / `partial` / `not_imported` / `no_write_path`) so zero is never shown as "empty" when it means "not imported" |
| Drive links | not in the CRM. `ORIGENLAB_V2_DRIVE_ARCHIVE_LEDGERS` names the executed archive runs' `archive_links.jsonl` (and the first run's verified upload report); matched to revisions by exact `pdf_sha256` only; two ledgers disagreeing about one document refuse startup. Measured: **69 / 69** CRM revisions resolve to a Drive file; 144 archived PDFs, 75 not in the CRM |
| Dashboard | `#/crm/*` (`apps/dashboard/src/crm/`): Resumen, Oportunidades (cards, board, drawer with revision history + Drive/Gmail links), Organizaciones, Personas, Proveedores, Archivo Drive, Marketing, Revisión. Shares only `AuthGate` with the V1 panel; every write control is rendered disabled |
| Proxy | `/v2/workspace/*` and `/v2/cockpit/*` are **not** in the dashboard-proxy allowlist — local review through the Vite dev proxy only |
| Evidence | `apps/api` 18 new tests, `validate.sh` **2,014 passed, 245 skipped**; `apps/dashboard` 12 new tests, `npm run validate` **1,448 passed**, build ok |

### 2.7.26 V1 read routes closed in the dashboard proxy, 2026-09-26

Local only, **committed locally, not pushed, not deployed** — the Worker in Cloudflare still forwards both
prefixes until this change is deployed. Owner decision 2026-09-26: V1 stays disabled in the
browser until it is decommissioned or its routes are migrated behind the V2 role model
([`OPERATIONS.md`](OPERATIONS.md) §2.1).

| Item | Value |
|---|---|
| Removed from `apps/dashboard-proxy/src/allowlist.ts` | `/^\/contacts\/[^/]+$/` and `/^\/mirror\/.+/`. Both now answer **403 `path_not_allowed`** without reaching upstream; POST answers 405 |
| Why | upstream they are gated only by the shared API key — no operator identity, no role, no redaction — so any person past Cloudflare Access read contact addresses unmasked, including a `viewer` whom `/v2` masks (§2.7.2, *Contact addresses by role*) |
| Still reachable through the proxy | `/health`, `/auth/*` and the named `/v2/*` reads on GET, and the named `/v2/commands/*` POSTs (marketing, CRM authoring) plus the auth POSTs, with no identity of its own (V1 paths removed 2026-10-03, §2.7.41) |
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

### 2.7.27 One dashboard: the CRM is the shell, 2026-09-26 — built locally, not deployed

- **One shell, one sign-in, CRM only.** `crm/CrmApp.tsx` is the only frame and its eight
  sections (Resumen, Oportunidades, Organizaciones, Personas, Proveedores, Archivo Drive,
  Marketing, Revisión) are the whole navigation. `#/`, an empty hash and unknown hashes open
  `#/crm/resumen`; earlier-panel hashes redirect to the matching CRM section, or Resumen
  (`crm/shellRoute.ts`); a bookmark that selected a case (`?opportunity=<uuid>`, `?id=<uuid>`)
  opens it in Oportunidades when it exists. The earlier panel's pages ("Panel anterior (V1)" and "Consolas V2"),
  its V1 data context and its write helpers are **deleted** from the dashboard; the backend
  routes, databases and jobs they read are untouched. The dashboard's only non-GET request is
  `POST /auth/logout`.
- **Honest labels.** An open imported case in `quoting` shows «Cotización enviada ·
  histórico» and «Estado actual sin verificar»; next steps are marked *sugerencia*; every
  Drive link carries «registro local» (it comes from an archive ledger, not a live Drive read).
- **Operator roster tool** `apps/api/scripts/operator_roster.py`: approves named
  `@origenlab.cl` accounts as `platform.operator` rows from a roster file outside the repo.
  Plan is read-only and prints the exact `--confirm-changes <N>` its own pending count needs;
  apply refuses any other count; operators missing from the roster are left alone. Tests (19)
  write only to a disposable `origenlab_test_<hex>` database. **Not run against any real database.**
  `origenlab_clean` already holds two active admin rows — a development placeholder and one
  `@origenlab.cl` account — and none of the three requested individual accounts.
  **Corrected 2026-09-29:** the "three individual accounts" this tool was written for are
  not the production input. Production signs in with **one** Google principal,
  `contacto@origenlab.cl`, and the three people are **profiles** behind it — Rafael (admin),
  Tatiana (admin) and Karla (sales) — each with a PIN and **no individual email address**
  (§2.7.38, `profile_roster.py`). Before any profile is provisioned, production also needs the
  shared account's Google issuer + `sub`, verified through the read-only directory procedure in
  `apps/api/docs/PRODUCTION_AUTH.md`. No individual `@origenlab.cl` operator account is owed.
- **Not changed:** the proxy (see the separate proxy branch), the API routes, any database.

### 2.7.28 Remote V2 database connection, 2026-09-27 — code only, not deployed

| | |
|---|---|
| What | `apps/api` can open a remote V2 database, **only** with `ORIGENLAB_V2_DATABASE_REMOTE=true` plus an exact expected host and a CA file (`v2/remote_database.py`, `apps/api/docs/PRODUCTION_AUTH.md` → *Remote V2 database*). `sslmode=verify-full` always; login must be `origenlab_api`; port 6543 (Supavisor transaction mode) refused by name; a startup probe refuses any other session role, any elevated attribute, and membership in owner/migrator/`postgres` or in any role holding an elevated attribute |
| Unchanged | loopback-only is still the default; the development header login and the import/rehearsal tools stay loopback-only |
| OAuth | production `ORIGENLAB_AUTH_PUBLIC_BASE_URL` must be exactly `https://<dashboard>/api`; callback `https://dashboard.origenlab.cl/api/auth/google/callback` |
| Evidence | `test_v2_remote_database.py` 40 passed, 5 of them against a disposable PostgreSQL 17 serving a throwaway-CA certificate (right CA passes; wrong CA and wrong host name refused by libpq; superuser session and membership in an unnamed BYPASSRLS role refused by the probe). **Ordinary PostgreSQL only — never run against a hosted pooler** |
| Not done | no remote database chosen or provisioned, no credential set, nothing deployed |

### 2.7.29 Campaign drafts and equipment-interest audiences, 2026-09-27 — built locally, not deployed

| | |
|---|---|
| Migration | `20260927120000_slice5_campaign_draft_authoring.sql`: `outbound.campaign.preheader` (≤ 255, non-blank); trigger `outbound.campaign_content_draft_only` (subject, preheader and bodies change only while `draft`); events `campaign.draft_created`, `campaign.draft_content_saved`. No table added (inventory stays 36); audience criteria stay frozen-or-absent; no grant, policy or send flag changes |
| Write path | `POST /v2/commands/{create,save}-campaign-draft` — sales/admin, `Idempotency-Key`, compare-and-set on `version`, one event per change; HTML with a script, frame, form, inline handler, meta refresh, `<base>` or script URL is refused, not cleaned. Mounted only with `ORIGENLAB_V2_CAMPAIGN_DRAFTS_ENABLED=true` (separate from `ORIGENLAB_V2_COMMANDS_ENABLED`; default off). The proxy allows no POST under `/v2`, and its suite now pins both paths as refused |
| Reads | `GET /v2/workspace/marketing/{taxonomy,audience,campaigns/{id}}`; `/marketing` now reports `preheader`, `has_html`, `version`, the storage table and database, and whether drafts are enabled |
| Taxonomy | `apps/api/src/origenlab_api/v2/equipment_taxonomy.json`, generated from `apps/web/src/data` by `apps/api/scripts/export_equipment_taxonomy.mjs` (drift-checked in pytest): 6 brands, 6 families, 24 models, every image VERIFIED on the site and served from origenlab.cl |
| Audience | relevance and sending eligibility are separate fields; no score. Interests come from `crm.opportunity_interest`, the quotation Gmail message a historical revision was recorded from, and case titles; bases `purchased` / `requested_quotation` / `requested_information` / `inferred_relevance`, each with source and date. Person-level (recipient/participant) and institution-level (requesting institution) kept apart. Exclusions: invalid destination, address/domain block, active cooldown, supplier/manufacturer, unreviewed `supplier_candidate` domain; deduplicated by address |
| Dashboard | Marketing: campaign cards with sandboxed thumbnails (or «Contenido no importado»), draft editor (subject, preheader, HTML, four OrigenLab templates, desktop/mobile preview under a CSP that loads images only from origenlab.cl), a persistence banner that always says whether the draft is only in the tab or saved (table, database, version), and «Audiencias por equipo». The audience selection is not persisted — the schema records an audience only at freeze, which is unbuilt |
| Clean-room reading (read-only, 2026-09-27) | `crm.opportunity_interest`, `crm.person`, `crm.opportunity_participant`, `crm.quote_line`, `catalog.product` are **0**. Brand mentions exist only in quotation evidence: Hielscher 23 records → 15 cases / 13 institutions; Löser 6 → 3 / 3; IKA 5 → 3 / 3; Adam Equipment 4 → 2 / 2; Ortoalresa and SERVA none. The 3 campaigns carry no HTML (all «Contenido no importado») |
| Evidence | pgTAP 572 across 15 files on a disposable PostgreSQL 17 (new `065_campaign_draft_authoring.sql`, 16) — all pass except `100_hosted_role_bootstrap` #22, which fails only because that test cluster gave `origenlab_api` a password. `apps/api/scripts/validate.sh`: 2185 passed. The drafts, audience, taxonomy and workspace modules with disposable-database DSNs: 74 passed. The whole suite with those DSNs shows 17 failures (`test_v2_read_boundary.py`, `test_v2_crm_connections_read.py`) that are identical on the unmodified base. Dashboard 186, proxy 165. Browser preview run against a seeded fictitious disposable database: draft created, v1 row and `campaign.draft_created` event recorded, zero recipients |
| Not done | no real CRM write, no campaign sent or frozen, nothing deployed; drafts not enabled in any running API |

### 2.7.30 Audience freeze, 2026-09-27 — built locally, not deployed

| | |
|---|---|
| Migration | `20260927180000_slice5_campaign_audience_freeze.sql`: `outbound.campaign.audience_policy_version` + `audience_sha256` (with a key `(id, content_sha256, audience_policy_version)`); trigger `campaign_freeze_facts_write_once` (content, criteria, fingerprints, policy and `recontact_interval_days` never rewritten once frozen; never back to `draft`); eleven snapshot columns on `outbound.campaign_recipient` (`frozen_at`, `frozen_inclusion`, `frozen_reasons`, `frozen_notes`, `relevance`, `interest_evidence`, `evidence_observed_at`, `identity_review`, `campaign_version`, `content_sha256`, `policy_version`), FK to the campaign's freeze key plus its covering index; trigger `campaign_recipient_snapshot_guard` (snapshot rows inserted only while draft, never rewritten or deleted; legacy rows untouched); event `campaign.audience_frozen`. Tables stay 36; foreign keys 119 → **120**; functions 11 → 13 |
| Write path | `POST /v2/commands/freeze-campaign-audience` — sales/admin, `Idempotency-Key`, `confirmed: true`, compare-and-set on `version`, and `expected_preview_sha256`: the command recomputes the audience in one repeatable-read transaction and refuses `audience_changed` when it differs from what the operator confirmed. Mounted only with `ORIGENLAB_V2_AUDIENCE_FREEZE_ENABLED=true` (own switch, default off). Writes no send attempt and no contact control |
| Reads | `GET /v2/workspace/marketing/campaigns/{id}/freeze-preview` (the would-be snapshot, every reason, what stops it, the send blockers) and `/recipients` (the frozen snapshot); `/marketing` reports `freeze_enabled` |
| Policy | `marketing-audience/2026-09-27.v1`: six canonical lines (Hielscher, Ortoalresa, IKA, Adam Equipment, Löser, SERVA) pinned to the taxonomy; a line without evidence is «Sin información»; exclusions `invalid_address` (terminal; also a stricter well-formedness check), `block`, `block_domain`, `prior_contact`, `cooldown`, `policy_supplier`, `manual_hold` (unreviewed supplier candidate, identity excluded on review, operator-excluded), `already_in_audience`; destinations the database cannot hold are counted, not stored; no contact point / several institutions / institution mismatch require an operator decision with a note |
| Dashboard | «Congelar audiencia…» on a saved, unchanged draft: criteria → review (coverage per line, reasons, identity decisions, optional exclusions) → final confirmation (content and policy versions, fingerprints, counts, acknowledgement checkbox) → «Congelar audiencia». No Send button. A BAJA blocker is shown on every freeze and snapshot screen; a frozen campaign opens read-only with «Nueva versión (borrador nuevo)» |
| Proxy | committed separately (937ac545), **not deployed**: the six Marketing reads (GET) and the three Marketing commands (POST) by exact path; each command refused before forwarding without an allowed `Origin`, with `Sec-Fetch-Site: cross-site`, a non-JSON body, an ill-formed `Idempotency-Key` or a body over 3.5 MB; roles stay upstream (reads any active operator, addresses masked for `viewer`; commands sales/admin). No send/approve/activate/override path is listed |
| Evidence | pgTAP 599 across 16 files on a disposable PostgreSQL 17 (new `066_campaign_audience_freeze.sql`, 27) — all pass except `100_hosted_role_bootstrap` #22. That failure was the fixture's, not the guard's: the throwaway-container script set a password on `origenlab_api` before pgTAP ran; on a cluster where `roles.sql` alone created the roles, #22 passes (re-proven in §2.7.31). `apps/api/scripts/validate.sh`: 2199 passed. Freeze, drafts, audience, taxonomy and workspace modules with disposable-database DSNs: 91 passed. Dashboard `npm run validate`: 190 passed, build passes |
| Blocked | **sending**: BAJA/unsubscribe processing is unsupported until an inbound-reply processor and a durable suppression ledger are proven; no approval, dry run, send command or Gmail client exists |
| Not done | no audience frozen in any real database, nothing deployed; the freeze switch is not enabled in any running API. The contradictory unwired `apps/email-pipeline/.../outbound_v2/freeze.py` was retired (13bd69e9): the CRM command is the only freeze |

### 2.7.31 W12 recontact review, 2026-09-27 — built locally, not deployed

| | |
|---|---|
| Migration | `20260927200000_slice5_w12_recontact_review.sql`: `outbound.campaign_recipient.recontact_review` (one audited decision per recipient: `approve` / `keep_excluded`, note, operator, `individual` / `bulk`, W12 policy version, the prior-contact facts shown); checks that an approval lifts `prior_contact` only (never block, domain block, supplier, invalid/bounced, duplicate or cooldown), that `keep_excluded` keeps `prior_contact`, and that on a snapshot row the override triple is exactly the approval (same operator, note as reason); `campaign_recipient_snapshot_guard` replaced to seal the decision and the triple; three W12 notes. Tables 36, foreign keys 120 and functions 13 unchanged |
| Write path | inside `POST /v2/commands/freeze-campaign-audience` (`recontact_decisions`), behind `ORIGENLAB_V2_RECONTACT_REVIEW_ENABLED` (default off, effective only with the freeze switch). Off: policy `…v1`, prior contact excluded, any recontact decision refused. On: policy `marketing-audience/2026-09-27.v2-w12`; recomputed and revalidated in the freeze transaction; one `campaign.override_granted` event per approved recipient. No grant after the freeze exists or is possible |
| Dashboard | a W12 panel on the freeze review: last contact date (or «fecha desconocida»), campaign or source, destination; per-row decision with a mandatory note; a bulk selection applied only after reviewing its exact list, sent as one decision per recipient. The frozen snapshot shows each decision and note |
| Evidence | pgTAP 623 across 17 files, all pass, on a throwaway PostgreSQL 17 where no OrigenLab role had a password (new `067_w12_recontact_review.sql`, 24, each check pinned to its constraint name). `100_hosted_role_bootstrap` #22 proven to depend only on the fixture: 22/22 without a role password, #22 fails once one is set. API `test_v2_audience_freeze.py` 28 passed as `origenlab_api` against disposable databases; marketing/workspace/redaction/taxonomy modules 246. Dashboard `npm run validate` 192 passed, build passes. Proxy 179. Pipeline `tests/outbound_v2` + `test_v2_import` 214 |
| Not done | switch not enabled in any running API; no real CRM write, nothing frozen, sent, pushed or deployed. Sending stays blocked by BAJA processing (W10) |

### 2.7.32 Supplier directory and equipment interests on CRM cards, 2026-09-27 — built locally, not deployed

| | |
|---|---|
| Providers | `GET /v2/workspace/providers` adds `directory`: the six catalogue brands (Hielscher, Ortoalresa, IKA, Adam Equipment, Löser, SERVA) from `equipment_taxonomy.json`, the same taxonomy Marketing uses — curated by the website, not detected. A machine candidate (`evidence.assertion` `supplier_candidate`) whose domain or trade name names a brand is shown as a hint with its own review state; nothing is promoted. The dashboard lists the directory first and the candidates collapsed under «Candidatos por revisar» |
| Interests | new `GET /v2/workspace/equipment-interests`: `marketing_audience.compose` regrouped per line (the taxonomy's six families), institution and destination; each person is `crm_person` (a `crm.person` is linked) or `address_only`. Pipeline cards carry `contact.address_ref` so a masked address still joins its evidence: an HMAC-SHA-256 of the normalized address under a key derived from `ORIGENLAB_AUTH_SESSION_SECRET` (a random per-process key when unset), never an unkeyed digest a viewer could recompute from a guessed address. The same ref replaces the old unkeyed `addr:` destination key; a CRM contact point keeps its UUID key (`cp:`). Organization cards and drawer and quotation-recipient cards show «Intereses observados» with source and date, «Sin información» only when the read succeeded and found nothing; a line opens a drawer with CRM people, address-only evidence and institutions apart. Both reads are in the dashboard-proxy allowlist as two exact GET paths (any other method 405, neighbouring paths 403); the rest of `/v2/workspace/*` outside Marketing stays unlisted |
| Data behind it | on `origenlab_clean`, as measured read-only earlier on 2026-09-27 for the campaign-drafts work and not re-read here: `crm.person`, `crm.opportunity_interest`, `crm.opportunity_participant` are 0, so every interest shown there is quotation evidence or a case title (`recorded_in_crm` false) and every person is `address_only`; institutions are real `crm.organization` rows reached through their cases |
| Pipeline | `outbound_v2.eligibility.RecontactOverride`, the `override`/`overrides` parameters, the verdict's override fields and `OVERRIDABLE_REASONS` retired: no caller outside their tests, and they cleared `prior_reply` and `cooldown`, which W12 forbids. `test_campaign_safety.py` refuses their return. Stale migration comments are corrected in [`WORKFLOWS.md`](WORKFLOWS.md) §W12, not rewritten |
| Evidence | API `scripts/validate.sh`: 2219 passed, 257 skipped (database-gated); interests/audience/freeze/workspace modules with disposable-database DSNs on a throwaway PostgreSQL 17: 74 passed. Dashboard `npm run validate` 196 passed, build passes. Proxy 179. Pipeline `tests/outbound_v2` + `test_v2_import` 209 |
| Not done | nothing pushed, deployed or written to a real database; the Worker with the two new paths is not deployed |

### 2.7.33 Campaign history and planning calendar, 2026-09-27 — built locally, not deployed

| | |
|---|---|
| Schema | migration `20260927220000_slice5_campaign_planning.sql`: `outbound.campaign.planned_for_date` (a day in America/Santiago), `planned_for_at` (UTC instant, when a time was chosen), `planning_version` (the planning compare-and-set token, separate from the content `version`). Triggers `campaign_planning_guard` (planning moves only while `draft` or `audience_frozen`, never in the same statement as a status change, and a time must fall on the planned Santiago day) and `campaign_planning_absent_at_insert`. Events `campaign.planning_set` and `campaign.planning_cleared`. No function reads the planned date; no grant widens |
| Write path | `POST /v2/commands/set-campaign-planning` — sales/admin, `Idempotency-Key`, `expected_planning_version`; set, change or clear (null day); an unchanged plan writes nothing; past days, days beyond 1,100 ahead and wall times skipped by a clock change are refused. Time-zone arithmetic is done by PostgreSQL. Mounted only with `ORIGENLAB_V2_CAMPAIGN_PLANNING_ENABLED=true` (own switch, default off). Writes no send attempt, recipient, job or contact control and changes no status |
| Reads | `GET /v2/workspace/marketing` adds per campaign: planning, `origin` (`imported_v1` / `native_v2`), sender mailbox, `send_batches` (accepted attempts grouped by the Santiago day they were accepted on), `attempts_without_date`, and `equipment_lines` (the frozen criterion, else a catalogue brand named in the name or subject, labelled by source); `authoring.planning_enabled`. New `GET /v2/workspace/marketing/campaigns/{id}/archive`: the frozen content with `html` only when `content_frozen_at` is set and `content_sha256` recomputes, otherwise `html_state` (`not_archived`, `not_frozen`, `no_html`, `fingerprint_mismatch`); real batches, recipient and attempt counts; opens and clicks `null` |
| Dashboard | Marketing overview on top («Último envío: hace N días», «Próxima campaña: en N días», counts of sent, drafts, frozen, planned; no opens, clicks or replies); a «Calendario» tab — month grid (agenda list on phones), previous/next/«Hoy», status and equipment-line filters, relative dates, V1 marker for imported campaigns; a campaign detail with the sent-HTML archive (desktop, mobile, read-only raw HTML; sandboxed iframe, CSP, sanitizer — the sanitizer now also drops `target`, `ping` and `download`) and a planning panel labelled «Planificación interna · no programa el envío», editable by sales/admin only |
| Proxy | exactly one new GET (`…/campaigns/<uuid>/archive`) and one new POST (`/v2/commands/set-campaign-planning`, under the marketing Origin/`Sec-Fetch-Site`/JSON/`Idempotency-Key` guard with its own 4 KB body limit) |
| Data behind it | on `origenlab_clean`, read-only 2026-09-27: the three imported campaigns are `archived`, imported (`origin_source_record_id` set), with a mailbox recorded and no subject, preheader, body or fingerprint — every one shows «HTML enviado no archivado». Real batches (Santiago days): Hielscher Sonicadores Laboratorio 600 on 09-02 + 526 on 09-03, 1 rejected undated; Septiembre 2026 — Fiestas Patrias FINAL 227 on 09-15 + 773 on 09-16, 11 rejected; Septiembre 2026 - Wave 2 1,000 on 09-17, 3 rejected. No replies, opens or clicks recorded. `origenlab_clean` does not carry the slice-5 migrations |
| Evidence | pgTAP 649 across 18 files (new `068_campaign_planning.sql`, 26) on a throwaway `supabase/postgres` 17 container; API planning module with disposable-database DSNs 29 passed (incl. the regression that planning writes no send attempt, recipient or job and opens no network connection); dashboard `npm run validate` 218 passed, build passes; proxy 214 + typecheck; browser check at 1440×1000 and 390×844 against a fictional seeded database — no horizontal overflow, a planning save wrote one event and one receipt and nothing else |
| Not done | nothing pushed, deployed or written to a real database; the planning switch is off everywhere; the Worker with the two new paths is not deployed |

### 2.7.34 W10 «BAJA» unsubscribe, 2026-09-27 — built locally, not deployed

| | |
|---|---|
| Schema | migration `20260927230000_slice5_w10_unsubscribe.sql`. No new table and no competing model: an unsubscribe is `outbound.contact_control(block, marketing, reason unsubscribe, source unsubscribe_handler)`; the reply is `evidence.source_record` (`gmail_message`, key `gmail_unsubscribe:<sha256(Message-ID)>`, body and `body_sha256`) and an `evidence.assertion` of the new kind `unsubscribe_request` resolved to the control — or left `unresolved` as a **review hold** when the sender cannot be proven. **First entry of the closed SECURITY DEFINER list**: `outbound.add_contact_control`, implemented for `(block, marketing, unsubscribe)` only; it owns the complete unsubscribe transaction (evidence + control or hold + one event, or a review's confirmation), asserts `session_user = origenlab_api`, an active sales/admin operator and that operator's open receipt of the right command (`apply-unsubscribe-replies` / `resolve-unsubscribe-review` / `dismiss-unsubscribe-review`, the last for an active **admin** only), recomputes the body hash, re-proves the basis it is given (a known address, or outbound lineage: `In-Reply-To` names a recorded outbound `comms.message` whose to/cc/bcc include exactly this address) and refuses one it cannot; owner `origenlab_owner`, `search_path = pg_catalog`, relations schema-qualified, no dynamic SQL; EXECUTE revoked from PUBLIC/anon/authenticated/service_role, granted to `origenlab_api` only. Trigger function `outbound.unsubscribe_permanent` (seven triggers): an unsubscribe block, and any block a «BAJA» was linked to, is never updated or deleted; unsubscribe evidence is inserted only inside that function and never changed, except that function deciding a held request once — resolved to its control, or dismissed (`rejected`, decision time and admin recorded, every other field as received; its record `pending → reviewed`). A dismissed request can still be confirmed later (`rejected → promoted/linked`, `reviewed → promoted`; the event carries `overrides_dismissal`); a confirmed request is never dismissed. `outbound.marketing_contact_refusals(recipient)` — send-time contract for §2 clauses 4-6 against live controls and holds (`unsubscribe_pending_review`). Events `contact_control.evidence_linked`, `assertion.unsubscribe_review_opened`, `assertion.unsubscribe_review_dismissed`; frozen notes `unsubscribed`, `unsubscribe_pending_review`; partial index `assertion_unsubscribe_request_control_idx`. Tables 36, foreign keys 120 and RLS policies 139 unchanged; functions 15 → **18** (no function or FK beyond these three); no table grant widens |
| Commands | `POST /v2/unsubscribe/preview` (sales/admin; a read-only transaction; one outcome per record and the batch `input_sha256`), `POST /v2/commands/apply-unsubscribe-replies` (sales/admin, `Idempotency-Key`, `expected_input_sha256` and `expected_plan_sha256`) and `POST /v2/commands/resolve-unsubscribe-review` (sales/admin, `Idempotency-Key`, the request id, its address and a note; confirms one hold — or one an admin dismissed — as the permanent unsubscribe; idempotent — `already_resolved`) and `POST /v2/commands/dismiss-unsubscribe-review` (**admin only**, `Idempotency-Key`, the request id, its address, the `review_sha256` served by the suppressions read and a non-blank explanation; dismisses one *pending* hold as a false positive with one `assertion.unsubscribe_review_dismissed` event; a confirmed, already-decided, foreign or stale review is refused; no control is touched). All three commands are mounted only with `ORIGENLAB_V2_UNSUBSCRIBE_APPLY_ENABLED`, default off. A changed batch is 409 `input_hash_mismatch`, a changed outcome (e.g. a sender that became known) 409 `plan_changed`, any malformed record 422, any database failure 503/409 with nothing written. Records are already-fetched replies in the pipeline's `emails` shape (`message_id`, `source`, `from_header`, `received_at`, `subject`, `in_reply_to`, `body_text`); nothing calls Gmail |
| Grammar and sender policy | grammar `baja-reply/2026-09-27.v2` — body only; the reply's own text before quoted history or a `--` signature, trimmed, zero-width removed, NFKC, case-folded, must be exactly `baja`, `baja.`, `remover` or `remover.` (v2 added the V1 templates' `REMOVER`); everything else is left for a human. Sender policy `unsubscribe-sender/2026-09-27.v2`: own mailboxes ignored; a known address is suppressed; else proven outbound lineage to exactly that recipient is suppressed (no person, contact point or organization created); else the «BAJA» is held for review (`lineage_missing` / `recipient_mismatch`). Both versions are stored with the evidence, the request and the event ([`WORKFLOWS.md`](WORKFLOWS.md) §W10) |
| Enforcement | audience preview and freeze: reason `unsubscribed` → frozen `block` + note `unsubscribed`; a hold → frozen `block` + note `unsubscribe_pending_review` (exact address only); W12 lifts neither. `…/recipients` reports each recipient's live `send_time_refusals` and `suppressed_since_freeze`. Send blocker renamed `unsubscribe_sync_not_automatic` |
| Reads / UI / proxy | `GET /v2/workspace/marketing/suppressions` (masked for viewer, never a body) now also lists the review holds with their reason and names the grammar and sender-policy versions; Marketing tab «Bajas» says Gmail replies are not synchronized automatically and, only while the API mounts the review commands, offers per hold **Confirmar BAJA** (sales/admin, a note) and **Descartar (falso positivo)** (admin, an explanation, quoting `review_sha256`), one `Idempotency-Key` per opened form so a retry replays; nothing for a viewer; frozen snapshot marks «BAJA posterior al congelamiento». Proxy lists that one GET; plus the two review POSTs (`resolve-unsubscribe-review`, `dismiss-unsubscribe-review`) under the marketing-command guard with an 8 KB body limit; preview and apply are never forwarded |
| Evidence | pgTAP 745 across 19 files, all pass (`069_w10_unsubscribe.sql`, 96: catalogue, EXECUTE only for `origenlab_api`, no dynamic SQL, qualified relations, direct writes refused, holds decided once and never re-pointed, a confirmed request never dismissed, a dismissed hold stops refusing while a permanent unsubscribe or another hold still refuses, a dismissed request can be confirmed later and never the reverse, send-time refusals) on a throwaway `supabase/postgres` 17.6.1.165 container; `verify_direct_logins.sh` 71/71 there (the closed SECURITY DEFINER list asserted exactly — one definer, owner, `search_path`, ACL, EXECUTE only for `origenlab_api` — and nine injected deviations, each rolled back, all turn it red); the Slice 0 audit engine run against the same container is accepted by `assert_declared_local_head_gap.py` (a05 names exactly `outbound.add_contact_control`); audit unit tests 322. API `scripts/validate.sh` 2371 passed, 292 skipped; with disposable-database DSNs the whole suite 2541 passed + the DSN-database modules (`test_v2_read_boundary`, `test_v2_crm_connections_read`) against a migrated database 65 passed, 2 failed — the `test_v2_crm_connections_read` contact-search tests, which fail identically on the PR #598 head. W10 module 145 with the database (lineage suppression without identity creation, mismatched and missing lineage held, duplicate hold, idempotent resolution, admin-only dismissal of a pending hold with replay, confirmation after a dismissal, stale/foreign/confirmed refusals, forged basis/operator/receipt refused, a fault injected at the last write leaves nothing), as the real `origenlab_api` login. Dashboard `npm run validate` 220 + build; proxy 219; pipeline pytest subset of `validate.sh` + `tests/outbound_v2` + `test_v2_import` 381 and the README-pinning doc tests 32 (its operational CLI steps not run) |
| Not done | the apply switch is off everywhere; nothing applied to `origenlab_clean` or any real database; no Gmail synchronization, no `List-Unsubscribe`, no approval or send path; nothing deployed |

### 2.7.35 Campaign safety blocks (pause) and the September hold, 2026-09-27 — built locally, not deployed

| | |
|---|---|
| Schema | migration `20260928100000_slice5_campaign_block.sql`. Table **#37 `outbound.campaign_block`** (scope `campaign` / `all_campaigns` / `legacy_campaign`, mandatory reason, optional reference, placed and lifted by whom and when, `version` 1 → 2; **no expiry column**; one active block per target). Trigger function `outbound.campaign_block_guard`: never deleted; inserted active only by an active **admin** in `platform.operator` (or by a migration as `origenlab_owner`); lifted exactly once by an active admin with a reason; immutable afterwards; the database is the clock. `outbound.campaign_hold_refusals(campaign)` → `all_campaigns_blocked` / `campaign_blocked` / `campaign_paused` / `campaign_unknown`; `outbound.marketing_contact_refusals` appends them. Trigger function `outbound.campaign_hold_guard` on `outbound.campaign`, `outbound.campaign_recipient`, `outbound.send_attempt` and `crm.domain_event` refuses, while held: the audience freeze, approval, activation, `campaign.dry_run_recorded` / `campaign.approved` / `campaign.audience_frozen` events, reserving a recipient, creating or dispatching a marketing attempt — never the outcome of an attempt already in flight. Aggregate `campaign_block`, events `campaign_block.placed` / `campaign_block.lifted`. API grant: SELECT, INSERT and a column UPDATE on the lift columns; worker SELECT. Tables 36 → **37**, policies 139 → **143**, functions 18 → **21**, foreign keys 120 → **123** (all index-covered); still one SECURITY DEFINER (no new definer) |
| September wave-2 hold | seeded by the migration as an active `legacy_campaign` block on V1 campaign `septiembre18-2026-wave2`, reference `incident_hold_september_2026`, `placed_by_kind = migrator`, no address. No seed event (a migration writes no `crm.domain_event` row — audit check a12); the row carries its own provenance. It refuses nothing in V2 today — no V2 campaign row carries that key; the import loads V1 campaigns as `archived` — it is the durable, admin-liftable record of the hold. **Not applied to `origenlab_clean` or any real database** |
| Commands | `POST /v2/commands/block-campaign` (`scope`, `campaign_id`, `expected_block_version`, `reason`, optional `reference`) and `POST /v2/commands/unblock-campaign` (`block_id`, `expected_version`, `reason`): **admin only** (sales/viewer 403 at the route; the guard refuses a non-admin again → 403 `role_may_not_block`), `Idempotency-Key`, one receipt and one event each; `stale_block_version`, `already_blocked`, `stale_version`, `block_already_lifted`, 404s. Mounted only with `ORIGENLAB_V2_CAMPAIGN_BLOCKS_ENABLED`, **default off**; enforcement and the read are not behind it |
| Enforcement in the API | freeze preview lists `campaign_held` first (never part of `preview_sha256`); `freeze-campaign-audience` refuses it (409) and a database refusal after its checks is 409 `refused_by_database`. Dry run, approval and send are not built: the database triggers are what will refuse them |
| Reads / UI / proxy | `GET /v2/workspace/marketing/campaign-blocks` (active, recently lifted, per-campaign refusals and `block_version`, `may_decide`); `hold` on every campaign in `/marketing` and `/marketing/campaigns/{id}`; `campaign_holds` on `/marketing/audience`. A viewer gets status only (reason and operators removed). Dashboard: `crm/marketing/CampaignHolds.tsx` — banner for global and V1 holds, «Bloqueo de seguridad» panel on each campaign, «Bloqueada» badge; block/lift forms need a reason and a confirmation and appear only for `may_decide`. Proxy forwards exactly that GET and the two POSTs (Origin / JSON / `Idempotency-Key` guard, 16 KB body) |
| V1 ledger | `apps/email-pipeline`: `reserve_next_batch` and `send_campaign_batch` refuse any campaign whose `outbound_campaign.status` is not `active` (`CampaignNotActiveError`) before reading a recipient, dry-run or live; the sender re-reads the status before each live Gmail call, so a pause committed mid-batch stops the batch. The September campaign is still `active` in the V1 SQLite ledger (contained by its 279 blocked recipients); setting it `paused` is an owner-authorized real-data write, not done |
| Evidence | on a throwaway `supabase/postgres:17.6.1.165` container: pgTAP **803 across 20 files**, all pass (`071_campaign_block.sql`, 58); direct logins **76/76**; Slice 0 audit engine → gap checker accepts the declared delta (a04 38, a05 21/1 definer, a08 37, a09 143, a10 123/123/90), audit unit tests 324; API 2416 passed in process and, DB-enabled, 2579 passed + the 17 `test_v2_read_boundary` / `test_v2_crm_connections_read` failures that fail identically on `origin/main` (they query the maintenance database); `test_v2_campaign_blocks.py` 38 incl. 7 DB; dashboard 226; proxy 229; pipeline full suite 6463 passed (validate subset 172). `replay_evidence.sh`, `evidence_tool_failure_tests.sh`, `audit_failure_tests.sh`, `db lint` and `db advisors` need the CLI stack of the main checkout — CI only |
| Integration, 2026-09-28 | rebased onto the Marketing Studio (2.7.36, PR #603) instead of merged into it; the migration was renamed from `20260927234500` to `20260928100000` so it follows the archived-campaign guard already applied to `origenlab_clean` rather than landing out of order; it writes no campaign row, so that guard never fires on it. Combined head: functions **22** (gap checker, pgTAP 010/064 pins), tables 37. pgTAP **830 across 21 files**, all pass, on a throwaway `supabase/postgres` 17.6.1.165 container with `roles.sql` applied as `postgres` (a non-superuser) and the full chain replayed; dashboard 278 + build, proxy 229, API 2,417 in process, pipeline ledger/import 171 |
| Not done | the block switch is off everywhere; nothing applied to any real or hosted database; no status machine (nothing sets `paused`), no dry run, approval or send path; nothing deployed |

### 2.7.36 Marketing Studio: campaign history from PostgreSQL, 2026-09-28 — applied to `origenlab_clean`, not deployed

| | |
|---|---|
| Schema | migration `20260928090000_slice5_archived_campaign_immutable.sql`: INVOKER trigger function `outbound.archived_campaign_immutable` on `campaign`, `campaign_recipient`, `send_attempt`. An archived campaign and its attempts are never updated or deleted (owner included); its recipients are never deleted and are updated only in their CRM identity links (`contact_point_id`, `person_id`, `organization_id`), so `v2_promote.marketing` still links them; a campaign is born archived, and rows are added to one, only as `origenlab_owner` (the importer). `campaign_reply` stays insertable. Functions 18 → **19** (gap checker, pgTAP 010/064 pins); no table, FK, policy or grant changes |
| Reads | `GET /v2/workspace/marketing/campaigns/{id}/history/recipients` (server pages ≤ 100; `total` = audience/included/sent/unsent/excluded/blocked/rejected/bounced/responses, `reason`, `identity`, `q`), `…/history/replies`, `…/history/audit`. One recipient base and one SQL predicate per total, shared by the card totals in `/marketing`, the `/archive` summary and the list — a total and its rows reconcile by construction. Recipients and attempts reported apart; accepted ≠ delivered. A `viewer` searches CRM names only (never the address) and reads addresses masked in the read itself; the list is never ordered by address. Replies: stored replies, «BAJA» with send lineage, «BAJA» held for review (by address), «BAJA» by address counted, unassociated aggregate; nothing stored → «Respuestas no sincronizadas desde Gmail». Audit says whether this database enforces the guard |
| Redaction fix | the value-based mask matched only an ASCII local part, leaving everything before an `ñ` readable to a viewer (8 real campaign addresses); the local part is now every non-delimiter character, Unicode and apostrophes included |
| Dashboard / proxy | campaign detail tabs Resumen · HTML · Destinatarios · Respuestas · Auditoría; six clickable card totals; historical cards open history only (no edit/reopen/resend/planning); right drawer per recipient with interest evidence; an `origenlab_test_*` source shows a «datos inventados» warning. Proxy forwards the three GETs |
| Importer | `migration.v2_import.supplemental_campaign`: loads a V1 campaign only if archived, never attempted and with a never-sent audience; `mode=ro` source, hashed manifest as origin, dry run by default, idempotent. V1 `septiembre18-2026` (archived, 1,007 recipients all `inactive`, 0 attempts) is the one campaign missing from PostgreSQL; dry run: 1 source record + 1 campaign + 1,007 excluded recipients |
| Data behind it (read-only, before the apply) | `origenlab_clean`: 3 campaigns / 3,481 recipients / 3,141 attempts / 10,588 contact controls / 23,093 events, matching V1 per campaign (recipients 1,161 / 1,020 / 1,300; attempts 1,127 / 1,011 / 1,003). Not in PostgreSQL: subjects (Wave 1B carries only a digest), Gmail message ids, rejection times and details, per-attempt reconciliation (V1 `confirmed_sent` 981, `no_evidence` 58), the sent HTML (only in V1 Sent copies, linkable by subject and time only), and any reply |
| Evidence | pgTAP 771 on a throwaway container (new `072`, 27) — `100` #22 fails only because that cluster had an `origenlab_api` password set; API 2,399 in process + history/unsubscribe/planning 194 with a disposable database; dashboard 231 + build; proxy 219; pipeline 6,463 (with a disposable database). Browser check 1440×900 and 390×844 on a disposable copy of `origenlab_clean`: no horizontal overflow; 3,481 viewer rows, 0 unmasked |
| Applied to `origenlab_clean` (owner-approved, 2026-09-27) | backup taken first (`pg_restore --list` OK); the six slice-5 migrations applied (ledger head `20260928090000`, 31 entries) with every count unchanged; then the owner ran the supplemental import: 4 campaigns / 4,488 recipients / 3,141 attempts / 97 source records, contact controls 10,588 and events 23,093 unchanged. All four are archived; edit, reopen, extend and delete refused as `origenlab_owner` (rollback-only proof) |
| Viewer hardening | one rule, `crm/marketing/authoring.ts`: only a signed-in `sales` or `admin` may author. A viewer, an unknown role or an unconfirmed session sees no «Nueva campaña», «Editar», «Abrir», «Editar borrador», «Abrir campaña» or planning form, and the editor and freeze screen render only a read-only notice. An archived campaign with zero attempts reads «Nunca enviada» |
| Browser check, 2026-09-27 | admin at 1280 and 390 on the dashboard → API → `origenlab_clean`: 4 archived campaigns, 4,488 recipients, every total equals its filtered list, `septiembre18-2026` 1,007 excluded / 0 attempts, no actions, no overflow. Viewer on a disposable copy (dropped afterwards): 0 unmasked of 4,488; 80 address probes returned 0 rows. Dashboard 272 + build, proxy 219, API 2,385 in process |
| Not done | nothing pushed to `main` or deployed; PR #601 (campaign pause/blocks) is not merged into this branch |

### 2.7.37 Dashboard proxy lists the rest of the CRM workspace reads, 2026-09-28 — not deployed

`apps/dashboard-proxy/src/allowlist.ts` adds `GET /v2/workspace/{overview,pipeline,drive,review}` and
`GET /v2/cockpit/work-queue` by exact path (`providers` and `marketing` were already listed), so all seven CRM reads are proxied; every other method
answers 405, every neighbouring path 403, and only the dashboard session cookie goes upstream.
The API enforces the session (401) and the viewer mask; `apps/api/tests/test_v2_proxied_workspace_reads.py`
pins both for exactly these seven paths under production settings (Google sign-in on, header login
off): 401 without a session, with a forged cookie, or with only the Cloudflare Access operator
header; a viewer gets masked addresses; an admin does not. Every other `/v2/cockpit/*` path and all
of `/v2/commands/*` stay refused. Built 2026-09-26 (37ae5132) and rebased onto `main` 2026-09-28. **Not deployed.**

### 2.7.38 Shared Workspace login with operator profiles, 2026-09-28 (PIN attempt boundary 2026-09-29) — built locally, not applied, not deployed

| | |
|---|---|
| Model | One Google identity (**principal**, `platform.auth_principal` #38, pinned to the Google account's issuer + `sub`) → per-person **operator** (`platform.operator`, new `sign_in_kind = 'shared_profile'`, **no email address**) chosen through **`platform.operator_profile`** (#39) with a server-verified PIN (Argon2id PHC, pepper as Argon2's secret input); **`platform.auth_event`** (#40) append-only audit; **`platform.auth_session`** (#41) one revocable row per dashboard session, shared or individual (keyed hash of the cookie's identifier only). `google_account` operators otherwise unchanged. Migrations `20260928180000` (tables #38–#40), `20260928190000` (pinned issuer + subject), `20260928191000` (#41), `20260928192000` (runtime INSERT/UPDATE on `platform.operator` revoked), `20260928193000` (`lockout.cleared` audit event, migrator only), `20260928194000` (PIN throttle written only by the SECURITY DEFINER `platform.record_pin_attempt`; runtime UPDATE on both throttle tables revoked), `20260928195000` (individual Google-account sessions are `platform.auth_session` rows too: `sign_in_kind`, `account_operator_id`), `20260929100000` (`record_pin_attempt` dropped; the PIN decided in the database by the SECURITY DEFINER pair `platform.begin_pin_attempt` / `platform.finish_pin_attempt` against a nonce-bound HMAC proof, with the attempt record on `auth_principal`; `operator_profile.pin_hash` unreadable by the runtime role; the four PIN outcome events — new `principal.locked` among them — writable only by `finish_pin_attempt`, one per attempt); no seed |
| Schema counts | tables 37 → **41** (`platform` 2 → 6), policies 143 → **148** (+9 sign-in, −2 runtime writes on `platform.operator`, −2 runtime throttle UPDATE policies), functions 22 → **30** (five INVOKER triggers: three version guards, the session guard, the audit-actor guard; the INVOKER `platform.hmac_sha256`; and the second and third closed-list SECURITY DEFINERs, `platform.begin_pin_attempt` and `platform.finish_pin_attempt`, which replaced `platform.record_pin_attempt`), foreign keys 123 → **131** (98 unconditionally covered); SECURITY DEFINER 1 → **3**. The runtime role's `SELECT` on `platform.operator_profile` is column-level (every column but `pin_hash`). Gap checker (now also declaring the two removed baseline policies, and robust to the audit's 12-entry truncation), pgTAP 010/040/050/063/064/080/090, `verify_chain.sh`, `replay_evidence.sh` and the evidence-tool fixture moved in the same changes |
| API | principal session → `profile_required` on every `/v2` read, workspace and command route; `GET /auth/profiles`, `POST /auth/profile/select`, `POST /auth/profile/clear`; logout audited. The callback admits a token only as the principal that is both its address and its pinned issuer + `sub` (a recreated account is refused); **production refuses an unpinned principal**. Every request — shared **or individual** Google session — requires the session's `platform.auth_session` row live (not revoked, not expired); an individual session also binds the operator's `version`, so disabling it or changing its role ends the next request; logout revokes it before clearing the cookie (if it cannot: 503 `logout_not_recorded`, **no cookie cleared**, the dashboard stays signed in and says «No se pudo cerrar la sesión de forma segura. Intenta nuevamente.»; a retry revokes the same row), select/clear rotate it with the same expiry. Throttle in the database (5 → profile lock, 10 → principal lock, 15 min doubling to 24 h). **The PIN is decided by the database**: the API derives Argon2id at the parameters and salt `begin_pin_attempt` returns (a decoy for an unknown, foreign or unusable profile) and sends only an HMAC of the output bound to the one-use attempt; `finish_pin_attempt` verifies it against the stored verifier and writes the verdict, counters, lock and exactly one audit event. A malformed PIN is a counted failure. Protects against a SQL-only attacker, **not** against a compromised API host (pepper + session secret), as documented. Session binds principal/issuer/subject/versions/auth time; re-checked per request. Refusals share status, body and cookies; no `/auth/*` answer carries `Server-Timing` / `X-Process-Time-Ms` (API omits, Worker drops); timing is documented as **not** constant. Off unless `ORIGENLAB_PROFILE_LOGIN_ENABLED`; startup refuses a missing/weak pepper. Local-only `POST /auth/dev/principal-session` (loopback, non-production, reserved test domain). As the migrator only: `scripts/profile_roster.py` and `operator_roster.py` provision; `scripts/profile_auth_admin.py` clears a lockout (`lockout.cleared` event) and prunes ended sessions |
| Proxy / dashboard | Worker allows exactly those three routes with an Origin / `Sec-Fetch-Site` / JSON / 1 KiB guard (logout gains the Origin guard); never `/auth/dev/*`. Dashboard: «¿Quién está usando el CRM?» selector, PIN dialog, one generic error, header profile + role, «Cambiar perfil», «Cerrar sesión»; any 401 re-checks the session |
| Evidence | **2026-09-28 hardening pass** (throttle definer, unified revocable sessions, no auth timing headers, honest logout, read-only subject procedure), measured at the branch head: the full `.github/workflows/supabase.yml` sequence run against a **throwaway Supabase CLI stack started from a scratch clone of this branch** — identical except its `project_id` and local ports (and the matching pins in `local_target.sh` / `audit_failure_tests.sh`), so it could not touch the other checkout's stack: pgTAP **960 across 24 files**, all pass (new `075_pin_throttle_definer.sql`); replay, hosted-bootstrap rehearsal 37, hosted-bootstrap failure injection 91, direct-login proofs **96** (closed list now two definers, with six new injections), evidence-tool failure injection 30, audit failure injection 80, audit unit tests 337; the real Slice 0 audit's local mode concludes `LOCAL_FAIL` on exactly the declared gap and `assert_declared_local_head_gap.py` accepts it; report verification, `db lint` and advisors clean. `local_db_failure_tests.sh` 21/22: case O2 needs a checkpointable dev database, and on this machine the only one belongs to another worktree, which the guard correctly refuses (CI has none and skips it). API **2,896** passed with a disposable database, **2,616** in process; dashboard 303 + build; proxy 318 + typecheck. gitleaks v8.28.0 over the branch patch: no leaks. CodeQL not available locally |
| Evidence, PIN attempt boundary (2026-09-29) | Before: `record_pin_attempt('record_success', …)` called directly as the real `origenlab_api` login cleared both failure counters with no PIN (200 failures in 50 × (4 + 1), never locked, 0 audit rows), and the role could `SELECT pin_hash`. After, at the branch head, the same scratch-clone CI sequence (`project_id` and ports shifted; `audit_failure_tests.sh`'s port pin shifted to match): pgTAP **989 across 24 files**, all pass (`075_pin_attempt_definers.sql` rewritten, RFC 4231 vectors for the helper); replay; hosted-bootstrap rehearsal 37, failure injection 91; start retry 7; direct-login proofs **117** (three definers, twelve new injections including the restored `record_pin_attempt`); evidence-tool failure injection 30; audit failure injection 80; audit unit tests 338; the real Slice 0 audit's local mode concludes `LOCAL_FAIL` and `assert_declared_local_head_gap.py` accepts it (its definer order corrected to the audit's own — found only by the real audit); report verification, `db lint` and advisors clean (lint found an unused loop declaration, fixed). `local_db_failure_tests.sh` 21/22, the same O2 environment refusal as above; clean-room failure injection 29. API **2,912** passed with a disposable database (new `test_v2_pin_attempt_boundary.py`: 29 attacks as the real runtime login — 50 reset cycles, one-use/pairing/replay, verifier unreadable through `select *`, views, `pg_stats`, results and errors, forged proofs, forged events, exact thresholds under concurrency, final-event-write failure injection leaving nothing, no secret in logs/responses/auth tables/server log, and an inserted `auth_session` row authenticating nothing without the session secret), **2,616** in process; two injected regressions (verifier re-granted, event guard disabled) turn 6 of the new tests red. Dashboard 303 (one run hit a flaky logout-toast test in `ProfileSelector.test.tsx`, untouched here; four isolated reruns and a full rerun passed); proxy 318. gitleaks v8.28.0 over the 19 branch commits: no leaks. CodeQL not available locally Follow-up (69395103): `finish_pin_attempt` takes no `previous_operator_id` — nothing in the database could verify it against the caller's session — so `profile.selected` records it NULL, pinned by the subject-shape check; the same scratch-clone sequence re-run: pgTAP 989 / 24 files, direct-login 117, audit failure injection 80, evidence-tool 30, rehearsal 37, declared gap accepted, lint/advisors clean, `local_db_failure_tests.sh` 21/22 (O2 as above); API focused suites (PIN boundary, profile login/security, Google auth) 369 passed; audit unit tests 338 |
| Operator inputs for production | **one** Google principal, `contacto@origenlab.cl` (not three operator emails); three profiles — Rafael (admin), Tatiana (admin), Karla (sales) — each with a PIN and no individual address; the shared account's Google issuer + `sub`, verified by the read-only Admin SDK `users.get` procedure (`apps/api/docs/PRODUCTION_AUTH.md`) **before** `profile_roster.py --apply`. None of this exists yet |
| `origenlab_clean` before this PR | measured read-only on 2026-09-29, ahead of merge (this branch did not connect to it): `crm.organization` **1,821** · `crm.contact_point` **9,460** · `crm.opportunity` **44** · `crm.quote` **65** · `outbound.campaign` **4** · `outbound.campaign_recipient` **4,488** · `outbound.send_attempt` **3,141**. None of the eight sign-in migrations is in its ledger (no row at or after `20260928180000`); they follow only after merge, a refreshed `main`, a fresh backup, and an apply from canonical `main` |
| Not done | merged as PR #614 (`cecbcc1d`) and **applied to `origenlab_clean` on 2026-09-30** (§2.7.39); not applied to any hosted database; no real principal, profile or PIN provisioned; switch off everywhere; not deployed |

### 2.7.39 Sign-in migrations applied to `origenlab_clean`; the clean-room verification contract repaired, 2026-09-30 — local only, nothing provisioned

| | |
|---|---|
| Apply | from canonical `main` (`cecbcc1d`, PR #614), owner-approved, after a backup (`~/data/origenlab-v2-migration/backups/origenlab_clean-pre-migration-20260928100000-20260929T234759Z.dump`, restore rehearsed first on a disposable copy). `ol_apply_migrations_into` printed **9 applied, 31 already recorded**: the campaign block (`20260928100000`) and the eight sign-in migrations (`20260928180000` … `20260929100000`) |
| Measured after, read-only | ledger **40** rows, head **`20260929100000`**, every version on disk and none extra; **41** tables (`catalog` 2, `comms` 4, `crm` 19, `evidence` 2, `outbound` 7, `platform` 6, `procurement` 1); **148** policies, **131** foreign keys, **30** functions; exactly **3** SECURITY DEFINERs (`outbound.add_contact_control`, `platform.begin_pin_attempt`, `platform.finish_pin_attempt`), each owned by `origenlab_owner`, `search_path = pg_catalog`, `EXECUTE` for `origenlab_api` alone; `platform.record_pin_attempt` absent; `origenlab_api` has no table-level `SELECT` on `platform.operator_profile`, no `SELECT` on `pin_hash`, no `UPDATE` on `auth_principal` or `operator_profile`, no `INSERT`/`UPDATE` on `platform.operator` |
| Data | **every pre-apply count preserved** (`crm.organization` 1,821 · `crm.contact_point` 9,460 · `crm.opportunity` 44 · `crm.quote` 65 · `crm.domain_event` 23,093 · `outbound.contact_control` 10,588 · campaigns / recipients / attempts 4 / 4,488 / 3,141); the two existing `platform.operator` rows unchanged on every pre-existing column, `sign_in_kind = 'google_account'`; **one** active `outbound.campaign_block` (`legacy_campaign` · `septiembre18-2026-wave2` · `incident_hold_september_2026` · placed by the migrator, not lifted); `platform.auth_principal`, `platform.operator_profile`, `platform.auth_session`, `platform.auth_event` **all empty**; send flags both `false` |
| Not done | **no profile, PIN, principal or Google identity has been provisioned** — `profile_roster.py --apply` has not run, no `contacto@origenlab.cl` subject was read; nothing hosted, nothing deployed, no Google access |
| Why the verify contract was repaired | `cleanroom_db.sh verify` failed **21 of 41** probes against the applied database. Audit: **2** were chain drift (`migrations.count` 25, `migrations.head` `20260925200000` — the fixture was last re-declared at the 25th migration and nothing checked it against `supabase/migrations/`), **19** were counts moved by owner decisions taken since 2026-09-24 (reviews with receipts 316, the historical quotation import: 44 opportunities / 65 quotes, the campaign import: 4 campaigns / 4,488 recipients, the Gmail tail staging: 92 messages / 5 manifests, operators 2). The nineteen are not drift and were **not** re-declared: a fixture rewritten to today's numbers is stale at the next decision and proves nothing about the next rebuild |
| The repair | **two contracts** ([`OPERATIONS.md`](OPERATIONS.md) §4.1 “Verify it”). `verify` now checks the **data-bearing** contract — `supabase/cleanroom/data_bearing.sql` / `expected_data_bearing.json`, 41 probes: the ledger equals the files on disk (a missing or phantom version is named), the inventory, the grant boundary, the closed definer list and shape, the sign-in privilege boundary, send flags, no residue; **no business count**. `verify --fresh-rebuild` keeps the exact **fresh-rebuild** fixture, moved only on what the chain decides (`migrations.count` 40, `migrations.head`, plus `outbound.campaign_block.active` 1 and the four sign-in tables 0 — 46 probes); `build` runs both at its end. Shared implementation in `supabase/scripts/lib/cleanroom_verify.sh`; a scratch-database guard (`ol_require_dev_scratch_database`, `origenlab_test_<8 hex>` only) in `lib/local_target.sh` |
| Evidence | `cleanroom_verify_tests.sh` from the labelled working tree against the development container: **static 13** (S1 the fixture's chain probes equal the 40 files on disk; S2 both contracts emitted exactly, every probe reasoned, no business count in the data-bearing file; S3 `compare.py` names a missing migration, a readable `pin_hash`, a fourth definer, an unmeasured and an undeclared probe; S4 the scratch guard refuses `origenlab_clean`, `origenlab_dev` and an empty name, and its psql wrapper refuses before it), **chain 9** on a disposable database with the full chain and no rows (C1 data-bearing passes; C2 fresh still fails; C3–C9 a deleted ledger row, a phantom one, `EXECUTE` on `begin_pin_attempt` revoked, `SELECT (pin_hash)` granted, `UPDATE` on `platform.operator` granted, RLS off on `auth_session`, an unpinned definer `search_path` — each fails closed by probe name), **restored copy 3** (R1 data-bearing passes on a `pg_dump`/`pg_restore` copy of `origenlab_clean` made inside the container; R2 fresh fails on it naming `crm.opportunity`; R3 `origenlab_clean`'s ledger and `tup_inserted/updated/deleted` identical before and after: `40|20260929100000|74463|13106|210`). `cleanroom_db.sh verify` on `origenlab_clean`: **data-bearing PASSED 41/41**; `--fresh-rebuild` FAILED 19 of 46, all nineteen decision-driven. `cleanroom_failure_tests.sh` **30 passed**. CI now runs `--static` before the stack and `--chain --cluster cli` after the replay proof |
| Boundaries | `origenlab_clean` opened **read-only** throughout (verify SQL inside `begin read only`; the copy made with `pg_dump`); every scratch database dropped on exit; `origenlab_dev` never opened; no Gmail, Drive or hosted Supabase connection; nothing provisioned, nothing deployed |

### 2.7.40 Recovered campaign HTML and CRM authoring, 2026-09-30 — merged to `main` 2026-10-02 (#619, `cb60c20d`); nothing applied to `origenlab_clean`, nothing deployed

| | |
|---|---|
| Schema | migration `20260930120000_slice6_campaign_content_archive_and_crm_authoring.sql`: **#42 `outbound.campaign_content`** (one immutable row per campaign × content kind × normalized-HTML variant: subject, preheader, HTML, raw and normalized SHA-256, send window, attribution method/confidence/policy, unmatched attempts) and **#43 `outbound.campaign_content_message`** (per-message provenance: RFC Message-ID, V1 Gmail id and attempt id, sent time, folder, the linked V2 send attempt — no address); both INSERT-only as `origenlab_owner` (`outbound.campaign_content_immutable`). **#44 `crm.note`** (person / organization / opportunity; revised by a new row, archived never edited; `crm.note_guard`; a merge may repoint `subject_id` alone). **#45 `crm.organization_product_line`** (a real organization ↔ one of the six curated lines, closed by `valid_to`). `crm.person` gains `title`, `status` active/archived + archive triple; `crm.organization` the same; `crm.contact_point` `status` active/inactive, `version`, `note`, deactivation pair; `organization_domain` and `external_identifier` a soft-removal triple (the exclusive-domain unique index ignores removed rows); person, organization, contact_point, domain, identifier and product line are never deleted. Vocabulary: aggregate `note`, 21 new event types (`organization.domain_restored` among them: a soft-removed domain returns on its own row, never as a duplicate). Tables 41 → **45** (crm 21, outbound 9), policies 148 → **160**, functions 30 → **32**, foreign keys 131 → **147** (all index-covered), SECURITY DEFINER still **3** |
| Recovery | `migration.v2_import.campaign_content_recovery` (pipeline): reads the V1 archive read-only; precedence (1) the attempt's Gmail id against the archive — **0 joins, by design: V1 stored the Gmail resource id, the archive keeps the RFC Message-ID**; (2)+(3) recipient lineage corroborated by sender, exact subject and a ±900 s window, unique nearest copy; subject alone never matches; a differing second candidate or a copy claimed by two campaigns refuses the campaign as ambiguous. Policy `campaign-content-attribution/2026-09-30.v1`. Plans first (no address in the plan), applies only with exact `--expect-contents` / `--expect-messages` as `origenlab_owner`, a second run inserts nothing |
| Measured on the real archive (read-only) | `hielscher-sonicators-2026` 1,126 accepted / **1,044 matched** (Δt ≤ 4 s) / 82 unmatched (80 with no Sent copy at all, 2 with only a pre-ledger copy); **two variants**: 67031c8795ae… 49 messages 09-02 13:08–13:20 and 9fd0008bc973… 995 messages 09-02 13:33 → 09-03, differing only in the hero image; 875 pre-ledger copies (08-31/09-01) stay unattributed. `septiembre18-2026-final` **1,000/1,000**, one variant fd0467489199…; `septiembre18-2026-wave2` **1,000/1,000**, the **same** variant (shared subject; the send windows do not overlap, attribution is per attempt). `septiembre18-2026` 0 attempts, **no draft anywhere** (no Gmail draft, no artifact, V1 keeps only the subject) → «Nunca enviada» + «HTML no recuperado»; no «Borrador histórico» can be claimed. Preheaders recovered for both templates. One stray 56-recipient trashed September copy matches no attempt |
| Rehearsal on a disposable copy of `origenlab_clean` (`pg_dump`/`pg_restore`, `origenlab_test_93eb8ad9`) | migration applied; apply with `--expect-contents 4 --expect-messages 3044` wrote 3 manifests, **4 content rows, 3,044 message links, every one bound to a V2 send attempt**; `outbound.campaign` and `outbound.send_attempt` byte-identical before and after (`to_jsonb` digest); `crm.domain_event` unchanged; second apply **0 / 0 / 0**, `already_present` ×3; no address in any output |
| Commands | 28 under `/v2/commands/*`, mounted only with `ORIGENLAB_V2_CRM_AUTHORING_ENABLED=true` (**default off**): create/update/archive/restore/merge person, add/update/deactivate contact point, link/unlink person–organization, register/update/archive/restore organization, add/remove identifier, add/remove/**restore** domain, classification, product line, confirm/reject supplier candidate, add/revise/archive note. `add-organization-domain` restores a soft-removed row of the same organization instead of duplicating it (`restored: true`, same `domain_id`), refuses a live duplicate (`domain_already_present`) and an exclusive claim another organization holds (`exclusive_domain_taken`); `restore-organization-domain` refuses a row of another organization by name (`domain_of_other_organization`) — a restore never moves a domain. Sales/admin; **archive, restore and merge are admin-only**; archive-note author-or-admin; `Idempotency-Key` receipts, compare-and-set on `version`, exactly one `crm.domain_event` per change, a mandatory reason, no `DELETE` anywhere; a machine candidate changes only through an explicit confirm/reject; merge requires the preview digest and `confirmed: true` |
| Reads | `GET /v2/workspace/people/{id}`, `…/people/merge-preview`, `…/organizations/{id}/authoring` (references and «por qué no se puede eliminar»; viewer masked); `/providers` adds the six lines with their linked real organizations and each candidate's review state; `/marketing` and `…/archive` serve the recovered content only when its stored hash recomputes, labelled `sent_html_archived` / `historical_draft` / `not_recovered` / `ambiguous_attribution`, with every variant and its provenance |
| Proxy / dashboard | the 28 commands and three reads by exact path under the marketing-command guard (64 KiB); evidence-bound commands stay refused. HTML tab: «HTML enviado archivado» / «Borrador histórico» / «HTML no recuperado» / «Atribución ambigua», variant switcher, provenance line, the existing sandbox + sanitizer, whose image allowlist is exactly `https://origenlab.cl` and `https://www.origenlab.cl` (credentials, ports, http and every other host refused); no edit or resend on archived campaigns. «Dominios» lists soft-removed rows under «Eliminados (n)» with «Restaurar» for sales/admin. Proveedores: the header, badge and every list row read the candidate's `review.state` through one helper — the header once counted a field the API no longer sends and showed «Sin revisar 0» over 172 unreviewed rows. «Nuevo contacto», «Nueva organización», «Agregar proveedor», «Agregar nota» for sales/admin; person drawer, organization authoring, candidate review, note revisions; every destructive step through a ConfirmDialog stating what changes with a reason. `crmAuthoringApi.ts` is the only module besides `marketingApi.ts` allowed to name a command path |
| Evidence (re-run 2026-09-30 after the domain restore, the www images and the count fix) | pgTAP **26 files, 1,103 ok** on a throwaway `supabase/postgres` 17 container built from the committed chain (`073` 33, `074` 82); the single failure is `100` #22, the documented password-fixture noise. API **3,093 passed / 120 skipped / 0 failed** with database DSNs, the whole suite (39 DB-backed authoring command tests, five of them the domain restore). Pipeline **6,473 passed**. Dashboard **377** + build; proxy **351** + typecheck. Running the handlers against PostgreSQL found real defects the in-process fixtures hid — among them the archive read that returned before consulting the content table for every imported campaign, and variants shipped without their HTML |
| Browser, on the disposable copy (no invented fixtures; second pass 2026-09-30 as `origenlab_api`) | viewer and admin at 1440 and 390 on personas, organizaciones, proveedores, oportunidades, marketing and all four campaign HTML tabs: **0 non-GET requests as viewer, 0 unmasked addresses, 0 horizontal overflow**; Hielscher renders two variants with the sender mailbox masked inside the preview, both September campaigns «HTML enviado archivado», the never-sent one «Nunca enviada» + «HTML no recuperado», no edit or resend button anywhere; Proveedores reads Candidatos 172 = Sin revisar 172 = list length for both roles; every preview frame keeps `sandbox=""` and `referrerpolicy="no-referrer"` in desktop and mobile. Admin: «Nuevo contacto» and «Nueva organización» each wrote one row, one receipt and the expected events; the person drawer shows «Archivar persona», «Fusionar» and «Por qué no se puede eliminar»; the case drawer shows «Agregar nota» |
| Known gaps | replies stay «Respuestas no sincronizadas desde Gmail». Not a dashboard gap but a fact about the live site: the Hielscher variants reference `https://www.origenlab.cl/assets/email/*`, which the site now answers with a 301 to the apex and a 404 HTML page — the preview requests them from the allowed origin and the browser refuses the non-image answer; September's apex `/products/*` images are served (200). The disposable copy `origenlab_test_93eb8ad9` predates the in-place vocabulary edit of migration `20260930120000` and must be recreated before any restore rehearsal; the `origenlab_clean` plan (`--expect-contents 4 --expect-messages 3044` after the migration) is unchanged |
| Merge | PR #619 merged to `main` on 2026-10-02 at `01d95fe1` with a merge commit, `cb60c20d`; every `main` workflow green on that commit (api, dashboard, dashboard-proxy, email-pipeline, supabase, secret-scan). Merging is not deploying: Render and the cPanel job are untouched; the FastAPI Cloud GitHub App (§3.1) redeploys on `main` pushes outside the workflows, and **no FastAPI Cloud check run or deployment existed for `cb60c20d` when this was verified** (latest remains `c4cc0488`, 2026-09-30) |
| Required before `ORIGENLAB_V2_CRM_AUTHORING_ENABLED=true` | three review findings from #619 — **fixed 2026-10-04, §2.7.46**; as first recorded, they did not block a read-only hosted deployment, which never mounts the commands: (1) **identifier soft-remove / re-add** — `crm.external_identifier` keeps a global `unique (scheme, value_norm)` that a soft-removed row still occupies, so re-adding surfaces a database unique violation as a 500 instead of a named conflict or a restore, unlike domains; (2) **organization aggregate concurrency** — sub-entity commands bump `crm.organization.version` with `set version = version + 1 where id = …` and no `and version = <expected>` compare-and-set, so the aggregate has no proven optimistic-concurrency guard; (3) **classification duplicates** — `add-organization-classification` accepts a duplicate active role relationship with no deterministic conflict or idempotency outcome. Tracked in the Phase A readiness PR description; fixes land in their own reviewed PR |
| Not done | **nothing applied to `origenlab_clean`**, nothing deployed, provisioned or sent; the authoring switch is off everywhere; no Gmail write, no hosted connection |

### 2.7.41 Session-only browser boundary, 2026-10-03 — built, not deployed

Owner decision 2026-10-03 ([`MIGRATION.md`](MIGRATION.md) §11 row 5): Cloudflare Access is retired from `dashboard.origenlab.cl` at Phase B; the dashboard's Google Workspace sign-in with the shared-account profile selector is the only sign-in; MFA is Google 2-step verification on the shared account; `api.origenlab.cl` keeps its Access application.

| | |
|---|---|
| Worker allowlist | `apps/dashboard-proxy/src/allowlist.ts` carries `/health`, `/auth/*` and the named `/v2/*` reads on GET, and the named `/v2/commands/*` POSTs (marketing, CRM authoring) plus the auth POSTs, with no identity of its own. Removed: every V1 GET (`/operator/*`, `/cases/warm`, `/opportunities/*`, `/operations/*`) and every V1 POST (the durable `/operations/*` commands and the tender annex upload). V1 GETs answer **403 `path_not_allowed`**, V1 POSTs **405 `method_not_allowed`**; none is forwarded. Upstream they were gated only by the shared API key (§2.7.26) and the current dashboard calls none of them |
| Operator header | the Worker no longer reads the Cloudflare Access email. A browser-sent `X-OriginLab-Operator-Email` is still deleted; nothing replaces it, so identity reaches the API only as the `__Host-` session cookie |
| Still carried | the Access **service token** to `api.origenlab.cl` and `X-OriginLab-API-Key`; both are Worker secrets, unchanged |
| Evidence | `apps/dashboard-proxy` `npm run validate`: typecheck clean, **336 tests in 5 files**, all passing; `apps/dashboard` `npm run validate`: 383 tests in 31 files, build ok |
| Docs | [`MIGRATION.md`](MIGRATION.md) decision 5 and §10; [`OPERATIONS.md`](OPERATIONS.md) §1.2 steps 8–10; `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md` rewritten for the API application only; `apps/dashboard-proxy/README.md`; `apps/api/docs/PRODUCTION_AUTH.md` *Production activation* |
| Not done | **the Worker in Cloudflare still carries the previous allowlist and still rebuilds the operator header from Access until step 9 (`npx wrangler deploy`) runs.** The Access application on `dashboard.origenlab.cl` is untouched (step 10, after an end-to-end sign-in). Merging does not deploy the Worker; it does redeploy the dashboard static site, unchanged. Nothing provisioned, no Render variable set |

### 2.7.42 V2 connection pool and batched reads, 2026-10-04 — merged (#625, `dfd06ce3`) and deployed to Render 2026-10-04

| | |
|---|---|
| Why | The Render API runs in Oregon and the hosted database in `sa-east-1`. Every V2 repository opened a new verify-full TLS connection per call (two per authenticated request: session, then page) and sent one statement per round trip. Production logs 2026-10-04: `/v2/workspace/pipeline` ≈ 5 s, `/v2/workspace/overview` ≈ 3 s |
| API | One `psycopg_pool` per process (min 1, max 4, idle 300 s, lifetime 3600 s, a check per checkout) behind the repositories' unchanged `connect(dsn, autocommit=False)` shape; same TLS options, same startup refusals; opened by the lifespan or on first use. Every `SET` in `apps/api` is transaction-scoped (audited, and a test fails on a session-level one). Overview's 14 counts are one statement; the workspace pipeline and the read setup go out in psycopg pipeline mode. One log line per request: method, path, status, milliseconds; nothing else |
| Evidence | Full API suite against a disposable cluster (`scripts/disposable_test_cluster.sh`): **3110 passed, 0 failed, no V2 DSN skip**; two tests prove pipeline-mode results and that transaction-local settings do not survive into the next checkout |
| Not done | Real timings not yet measured: the per-request log line never reached Render's logs as first deployed (an unconfigured logger inherits the root's WARNING) — fixed in §2.7.45; read the numbers after that deploy. The per-checkout check costs one round trip; revisit with those numbers |
### 2.7.43 Resumen as a follow-up list, exchange rates, page memory, 2026-10-04 — merged (#626, `67c69deb`) and deployed (Render API + dashboard; Worker `d4c1e304`) 2026-10-04

| | |
|---|---|
| Dashboard | `#/crm/resumen`: dólar observado, euro and UF in pesos with a converter; open cases grouped by days since their latest revision was sent (7 or less, 8 to 30, more than 30), each row opening the case, its Drive PDF and its Gmail thread; a line saying how far the imported data reaches. Read-only. The data-health counts moved to Revisión → «Estado de los datos», read only when that tab opens. `useResource` keeps each page's last answer in memory (never browser storage) per operator, role and profile; `AuthGate` clears it whenever no session is confirmed. Archivo Drive is ordered by year and five-digit correlative, not as text |
| API | `GET /v2/workspace/fx`: the Banco Central figures as mindicador.cl republishes them — or, when it fails, findic.cl (same figures, same shape; added 2026-10-05 after mindicador.cl reset connections for an afternoon) — cached an hour per process; each source failure is logged (`fx source <host> failed: <type>`); a failed refresh keeps the last figures marked stale; with none, 503 and no retry for a minute. Display only: a quote records its own `fx_rate`/`fx_as_of`/`fx_source` ([`WORKFLOWS.md`](WORKFLOWS.md)) |
| Proxy | `GET /v2/workspace/fx` by exact path |
| Evidence | `apps/dashboard` `npm run validate`: **421 tests in 36 files**, build ok; `apps/dashboard-proxy`: 336 tests; API full suite against a disposable cluster: **3104 passed, 0 failed** |
| Deployed | API and dashboard redeployed on merge; the Worker deployed from `main` (`46e9c408`) as version `d4c1e304` on 2026-10-04: `/api/v2/workspace/fx` answers 401 without a session (forwarded), `/fx/` and V1 paths still 403 |
### 2.7.44 Cyber as a prepared campaign in the Marketing page, via the V1 lane, 2026-10-04 — merged (#627, `46e9c408`) and deployed 2026-10-04

The Cyber OrigenLab campaign (5–9 Oct 2026) is sent by the old V1 lane (the timer on the owner's PC) and is not in the V2 database. This slice shows it as a prepared campaign in progress — its plan per day and its email — without a database write or a new route. Interim: removed once its real results are imported.

| | |
|---|---|
| Declaration | `apps/api/src/origenlab_api/v2/v1_lane_campaigns.json`: key `cyber-2026-10`, 5 send days at 09:30, promo until 2026-10-11, `clients_per_day` 1,000 / 1,000 / 1,000 / 1,067 / 528 (the runner's wave sizes, counts only) and one `audience_rule` sentence. Validated strictly by a test on the committed file; at runtime a bad file logs a warning and yields nothing |
| Email | never in the repository (it carries contact addresses): read from `ORIGENLAB_V2_V1_LANE_CONTENT_DIR` as `v1-lane-<key>.html` (on Render, a secret file under `/etc/secrets`); missing, oversized (> 256 KiB) or non-UTF-8 → no preview, never a failed read |
| API | `GET /v2/workspace/marketing` carries `v1_lane_campaigns` (plan, total, rule, email); no new route, **no proxy change** |
| Dashboard | Marketing → Campañas: a card per V1-lane campaign (Programada / En curso / Envío terminado · resultados por importar), each day with its planned clients and honest status (a past day reads «Programada · resultado en el registro V1» — never «enviada»), the total, the audience rule, the email in the sandboxed preview. Calendar: a «Canal V1» chip per day with its count. Header: «Programada / En curso por el canal V1: …» until the promo ends |
| Never-sent history | historical V1 campaigns with no attempt and no send are hidden from the list and its count behind «Mostrar N campaña(s) nunca enviada(s)»; the record stays (owner decision 2026-10-04) |
| Evidence | `apps/api/scripts/validate.sh`: **2786 passed**, 474 skipped; `apps/dashboard` `npm run validate`: **407 tests in 33 files**, build ok (one pre-existing timing-sensitive ProfileSelector test failed once under load, passed alone 3/3 and in two full runs); `apps/dashboard-proxy`: 336, unchanged |
| Deployed | API and dashboard redeployed on merge; the secret file `v1-lane-cyber-2026-10.html` and `ORIGENLAB_V2_V1_LANE_CONTENT_DIR=/etc/secrets` set on the Render service before the deploy. **Remove the declaration entry once Cyber's results are imported** |

### 2.7.45 The per-request timing line reaches the server log, 2026-10-04 — built, not deployed

| | |
|---|---|
| Defect | §2.7.42's `RequestLoggingMiddleware` logged at INFO on `origenlab_api.request_logging`, which nothing configured: uvicorn configures only its own loggers, so the line inherited the root's WARNING and was dropped. Its tests pinned the level themselves and passed |
| Fix | the logger gets its own stderr handler at INFO (uvicorn's `INFO:     ` prefix); still propagates, so `caplog` keeps working. A new test runs a fresh process with uvicorn's `LOGGING_CONFIG` and reads stderr: `GET /ping 200 …ms`, no query string |
| Evidence | `apps/api/scripts/validate.sh`: **2813 passed**, 476 skipped |

### 2.7.46 CRM authoring gates closed, and the switch made visible to the dashboard, 2026-10-04 — built, not deployed

| | |
|---|---|
| Trigger | registering a supplier from «Nueva organización» on the hosted dashboard answered `http_404`. The Render log shows `POST /v2/commands/register-organization 404` in 5–11 ms: the authoring router is not mounted because `ORIGENLAB_V2_CRM_AUTHORING_ENABLED` is unset, as §2.7.40 requires until its three findings are fixed. The dashboard offered the editor on the operator's role alone |
| Gate 1 — identifier re-add | `add-organization-identifier` reads the `(scheme, value_norm)` holder under `FOR UPDATE` before writing. The same organization's soft-removed row is restored in place (`restored: true`, same `identifier_id`; the event stays `organization.identifier_added`, with `restored: true` in its payload — no new event type, no migration). A live duplicate is 409 `identifier_already_present`; any other holder, live or removed, is 409 `identifier_taken` — a removed identifier is never moved — and a unique violation at the insert maps to the same code |
| Gate 2 — aggregate concurrency | `_live_organization_active` takes the organization row `FOR UPDATE` and checks `expected_version`; every organization version bump goes through `_bump_organization_version` (`… where id = … and version = <expected>`, 409 `stale_version` on zero rows), including the contact-point and supplier-candidate paths. A test holds a rival bump uncommitted while each of four sub-entity commands runs from the same version: before, all four stacked their change on top; now each is refused and only the rival's bump lands |
| Gate 3 — duplicate classification | `add-organization-classification` refuses a same-role row in force on or after `valid_from` with 409 `classification_already_present`. Before, the duplicate reached `organization_relationship_no_overlap_same_role` and surfaced as a 500 |
| Found while testing | `remove-organization-classification` on the day the role opened violated `organization_relationship_validity` (`valid_to > valid_from`) as a 500; it is now 422 `valid_to_not_after_valid_from`. Recording a same-day close needs that CHECK relaxed — a migration, not taken |
| Switch visibility | the workspace reads looked up `app.state.v2_crm_authoring_enabled`, which nothing assigns (`main._mount_crm_authoring` sets `crm_authoring_enabled`), so `authoring.enabled` was false even with the switch on; the read tests set the unused name by hand. `GET /auth/session` now carries `crm_authoring_enabled`, and the dashboard's `mayAuthorCrm` and `isAdmin` require it: with the switch off no CRM editor, «Nueva organización» included, is offered |
| Evidence | `apps/api/scripts/validate.sh` with disposable-database DSNs (`disposable_test_cluster.sh`): **3,182 passed**, 120 skipped, 0 failed; 13 new API tests (four identifier and classification refusals against PostgreSQL, the four-command race, the same-day close, the flag name, the session switch × 3) each failed before the change. Dashboard `npm run validate`: **451 passed** + build; six new gate and parser tests failed without the change. Public-repo hygiene check passed |
| Not done | nothing deployed; the switch is still unset on Render. Turning authoring on is setting `ORIGENLAB_V2_CRM_AUTHORING_ENABLED=true` on the Render service after this merges ([`OPERATIONS.md`](OPERATIONS.md) §1.2 step 8) |

### 2.7.47 «Enviar prueba»: an admin-only test send of a campaign's email, 2026-10-04 — built, not deployed

An admin sends the stored email of a campaign (V2 campaigns with HTML, and the V1-lane cards such as Cyber) to an address they choose, from `contacto@origenlab.cl`, subject prefixed `[PRUEBA]`, before the real send.

| | |
|---|---|
| API | `v2/gmail_send.py` (gmail.send-only client; token file `{client_id, client_secret, refresh_token, address}`, address must be `contacto@origenlab.cl`), `v2/campaign_test_send.py` (admin-only command, 10/h and 30/24h across all operators from `platform.command_receipt`, two-phase receipt, history), `POST /v2/commands/send-campaign-test` (429 detail carries `next_allowed_at`), `GET /v2/workspace/marketing/test-send-history`. The recipient is one plain ASCII mailbox (no name, comment, encoded word or group; alphabetic TLD; ≤ 254) and the built `To` must parse back to exactly it; an address-scope block for purpose `all`/`marketing` (every unsubscribe) or an unresolved «BAJA» held for review is refused with 422 `test_recipient_blocked` before any receipt (WORKFLOWS.md §W10). A failed send keeps Google's HTTP status and error code as `error_detail` (never its message text, the recipient or a token). Mounted by `_mount_campaign_test_send` only when `ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED` is true and `ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE` holds a valid token; a bad token leaves the feature off with a warning. The `gmail_send` write guard allows only the client, the command and the mount; `gmail.googleapis.com` may appear only in the client |
| Proxy | the POST (4 KiB body) and the history GET, by exact path |
| Dashboard | `TestSendPanel` on V2 campaigns with HTML and on V1-lane cards; admin only, the API's address rule, history (a failure shows its `error_detail`) and remaining allowance |
| Authorization | `apps/api/scripts/gmail_send_authorize.py`: one-time local OAuth as `contacto@`, refuses any other account, writes the token file mode 600. Procedure: `apps/api/README.md` «Campaign test sends» |
| Evidence | after merging `main` (#631, #632): `apps/api/scripts/validate.sh` **2911 passed**, 502 skipped; the full API suite against a fresh disposable cluster (`disposable_test_cluster.sh`) **3293 passed**, 120 skipped, 0 failed, no skip for a missing `ORIGENLAB_V2_*TEST_DSN`; dashboard `npm run validate` **498 passed** + build; proxy `npm run validate` **337 passed**. The script's `token_payload` is unit-tested; the OAuth flow was never run and no real email was sent |
| Not done | token not authorized; Render secret file and both variables not set; API and Worker not redeployed |

### 2.7.48 CRM part A — confirm an institution, web suggestions, suggested people, 2026-10-04 — built, not deployed

| | |
|---|---|
| Trigger | owner, 2026-10-04: 32 of 41 institutions «Propuesta» with no way to confirm them, 33 with `kind = unknown`, zero CRM persons although the quote emails name the people (spec `2026-10-04-crm-part-a-confirm-complete.md`) |
| Confirm | new authoring command `POST /v2/commands/confirm-organization-record` {`organization_id`, `expected_version`, optional `note`} (sales/admin, Idempotency-Key): `machine_proposed` → `confirmed`, `confirmed_by_operator_id`, version + 1, one `organization.confirmed` event (`confirmed_from: crm_authoring`). Already confirmed → `already_confirmed: true`, no event, no bump, checked before the version. Not the evidence-bound `confirm-organization`, which stays unlisted on the Worker |
| create-person | an address the CRM already holds with no owner is claimed (`unattributed` → `personal`; `individual_owner_unknown` → `work`, institution kept; event `contact_point.updated` with `claimed_by_person_id`); another person's address 409 `contact_point_taken`, a shared mailbox 409 `shared_mailbox`, a deactivated one 409 `contact_point_inactive`. Before, each of these was a unique-violation 500 |
| Web suggestions | `ORIGENLAB_V2_ORG_SUGGESTIONS_FILE` (unset by default; never a path in the repository): a reviewed JSON built by `apps/api/scripts/build_org_suggestions.py` from the research run, read per request into `web_suggestions` on the organization read; a bad file is a warning and `null`. RUT in normal form with its check digit verified; `rut_source` `official` only when shown by the institution's own site or a `gob.cl` / `sii.cl` page. A free-mail `email_domain` (`gmail.com`, …) is refused: the builder drops and counts it, the loader reads it as `null`. `build_org_suggestions.py --check PATH` re-validates a hand-edited file. Procedure: `apps/api/README.md` «CRM web suggestions (secret file)» |
| Suggested people | `GET /v2/workspace/person-suggestions` and `person_suggestions` on the organization read, computed per request from the quote revisions' Gmail evidence: display name, else the person a PDF file name names when its institution is the case's; OrigenLab/Labdelivery, nameless desk mailboxes, and addresses on a person / shared / deactivated excluded. A file-name person is used only when the message has exactly one nameless recipient; institution words («Universidad …», «Mesa de Ayuda») and the institution's own name are never a person's name; a malformed sender header is no name. The computation is isolated: if it fails the card still answers 200 with `person_suggestions: []` and one warning (exception class only). Viewer: addresses masked |
| Proxy | POST `/v2/commands/confirm-organization-record`, GET `/v2/workspace/person-suggestions`, exact paths |
| Dashboard | institution card: «Confirmar institución» / «Confirmada por … el …»; «Sugerencias de la web» with per-field «Aplicar», «ya aplicado», «verificar en SII antes de aplicar» for a directory RUT, «Aplicar todo y confirmar» (high confidence only; stops at the first refusal and names it); «Personas sugeridas» on the card and on Personas — «Crear persona» is one `create-person`, «Ocultar» is stored in this browser as an opaque reference only |
| Schema | none: `organization.confirmed` and `contact_point.updated` were already in `domain_event_type_check` |
| Evidence | after merging `main` (#633): `apps/api/scripts/validate.sh` with disposable-cluster DSNs exported **3418 passed**, 120 skipped, 0 failed (skips: V1 Alembic-head `ORIGENLAB_TEST_POSTGRES_URL` tests and the TLS-server tests; no V2 DSN skip); dashboard `npm run validate` **536 passed** (44 files) + build; proxy `npm run validate` **345 passed** (5 files); public-repo hygiene passed; no customer name in the branch diff; gitleaks not installed locally (CI runs it) |
| Not done | Worker not deployed (the two new proxy paths); the suggestions file not uploaded as a Render secret file and `ORIGENLAB_V2_ORG_SUGGESTIONS_FILE` unset, API not redeployed; the owner's manual check (confirm two institutions, apply suggestions to two, create two people) not run |

### 2.7.49 Gmail capture into hosted V2 (Phase 4a), 2026-10-04 — built, not deployed

| | |
|---|---|
| What | `apps/worker` (new; Python 3.12, uv; package `origenlab_worker`): `origenlab-worker gmail-sync [--init] [--dry-run]` captures contacto@ Inbox and Sent into `comms.message` (+ participants, attachment metadata, the `.eml` in the private bucket `mail`) and `pending` `gmail_message` evidence with the 21 keys of the staged records; drafts, spam and trash skipped; a draft sent later and a message moved out of spam are captured (history also lists `labelAdded`; a message that gained `SENT` or `INBOX` is read again); campaign copies kept as messages without evidence (R1, owner-approved 2026-10-04); one transaction per message, the cursor after the window, which in history mode is the profile `historyId` read before the list |
| Reuse | V1 `classify_intake_folder`, `_require_stageable_gmail_payload`, `walk_attachments`, `recipients_header`, `date_iso_from_msg`, `message_from_bytes`, `classify_send_direction`, through `origenlab_worker/v1_reuse.py` (uv path dependency on `apps/email-pipeline`). The build therefore installs email-pipeline's OCR stack (about 650 MB venv) that the worker never imports (run-time RSS about 36 MB for a normal message; it grows with the message, see the Render row); slice 8 moves the seam |
| Safety | `gmail.readonly` re-checked on every token refresh; `origenlab_worker` over `verify-full`; on every connect a probe of role, grants and policies that refuses any role membership, a port other than 5432 (6543 by name), any `crm.*`/`outbound.*` write other than `INSERT` on `outbound.campaign_reply` (the designed 4c reply-proposal lane, which 4a never writes; column-level grants, `TRIGGER` and view/matview writes included), and any executable `SECURITY DEFINER` function (extension-owned, trigger and no-`USAGE`-schema functions not counted; nothing skipped by schema name); a session advisory lock (a holder past 30 minutes that is not idle is reported as `locked_by_stuck_session`, exit 1, never ended); one JSON log line with no subject, address, id or exception text — its `error` is a Storage code, a Gmail kind, a database class with its SQLSTATE, or a class name; a paused cron opens no database session; no secret reaches a `repr` |
| API, proxy, dashboard | `GET /v2/workspace/mail-sync` (state and last sync; no address) listed by the proxy; banner «Sincronización de correo detenida / atrasada»; the work queue lists every other kind before pending evidence and returns per-kind `counts`, which Revisión shows as each group's total |
| Render | `render.yaml` declares the cron `origenlab-gmail-sync` (`*/10 * * * *`, Oregon, Standard, root `apps/worker`) — **not created** on Render; `buildFilter` also rebuilds it when `apps/email-pipeline/**` or `render.yaml` change, and `UV_PYTHON_DOWNLOADS=never` is its one plain (non-secret) value. Sizing: Standard stays. A message near `MAX_RAW_BYTES` (50 MiB) peaks at about 451 MB RSS and one around 24 MiB at about 238 MB, so Starter (512 MB) risks an out-of-memory kill that stalls every run on that message; downsizing is safe only if `MAX_RAW_BYTES` is lowered to match |
| Docs | ARCHITECTURE §7 (the one Storage S3 key) and §8 (D1: a Render Cron Job until a queue exists); OPERATIONS §8 (runbook: exit codes, run modes, counters, StorageConflict recovery, owner checks), §11, §13; MIGRATION §5.1 (Gmail coverage proven from go-live) |
| Evidence | `apps/worker/scripts/validate.sh`: **318 passed, 1 skipped** (the skip: this cluster has no extension-owned `SECURITY DEFINER` function the worker can execute — the first hosted `--init --dry-run` is that proof); worker suite on a disposable cluster (`apps/api/scripts/disposable_test_cluster.sh`): **318 passed, 1 skipped** (the same one; no DSN skip, the end-to-end test included); `apps/api/scripts/validate.sh` with the cluster DSNs: **3432 passed**, 120 skipped, 0 failed (skips: V1 Alembic-head `ORIGENLAB_TEST_POSTGRES_URL` tests and the TLS-server tests, as in §2.7.48; no V2 DSN skip); `apps/dashboard` `npm run validate`: **543 passed** (45 files) + build; `apps/dashboard-proxy` `npm run validate`: **351 passed** (5 files); public-repo hygiene check: passed |
| Not done | the owner's setup (OPERATIONS.md §8.7, with its prerequisite that the fase-1 hosted load has created the `comms.mailbox` row): Google client and consent, the `origenlab_worker` password, bucket and S3 key, the cron service, `--init --dry-run` then `--init`, the switch. Owner checks open: Render's cron overlap behaviour; S3 keys on Pro covering every bucket; a project upload limit of at least 60 MB; whether Supavisor session mode releases advisory locks on disconnect; `payload_parity.py` showing 0 differences on the 92 staged records before go-live. The shadow week has not started and must confirm that mail sent from the Gmail web UI (an autosaved draft) is captured, by either route the code handles (a new `messageAdded`, or the draft id gaining `SENT`). Cron plan decided: keep `standard` (a message at the cap peaks at about 451 MB RSS, over Starter's 512 MB margin). Known limit (R7): label removals and other label changes after capture are not tracked |

### 2.7.50 Quote-number box counts the numbers Gmail already shows, 2026-10-05 — built, not deployed

| | |
|---|---|
| Problem | The Resumen's quote-number box built «último / siguiente» from the CRM cards and the Drive archive only. Since the Gmail capture (§2.7.49) went live, emails show quote numbers as sent that neither source holds, so the box suggested a number already used |
| API | `GET /v2/workspace/mail-quote-numbers` (read-only, pooled `_read`, every operator role): the distinct `payload.proposed_quote_numbers` of `evidence.source_record` rows of kind `gmail_message` with `review_status <> 'rejected'`, each with `first_seen_at` (the message's `sent_at`, else `acquired_at` when that is not an ISO timestamp) and `messages`; the 500 newest. Numbers, dates and counts only. `origenlab_api` already holds `SELECT` on `evidence.source_record` (slice 0 runtime grants); no grant changed |
| Proxy | the exact GET path allowlisted beside `mail-sync`; neighbours refused; README route table updated |
| Dashboard | `knownQuoteNumbers` takes Gmail as a third source («Gmail» in the label): a `CN01247` token takes the Santiago year of the day first seen and joins a CRM/Drive entry with the same year and correlative, so `lastAndNext` and `findUses` count it. «Siguiente» and «Copiar» wait for all three reads. If the Gmail read fails, the box answers from CRM + Drive and says «los números de Gmail no se pudieron leer», never that a number is unused |
| Evidence | `apps/api/scripts/validate.sh` with disposable-cluster DSNs: **3446 passed**, 120 skipped (the same V1 Alembic-head and TLS-server skips as §2.7.49; no V2 DSN skip); `apps/dashboard` `npm run validate`: **551 passed** (45 files) + build; `apps/dashboard-proxy` `npm run validate`: **357 passed** (5 files); public-repo hygiene check: passed |
| Not done | not deployed (Render API, dashboard, Worker). Until the Worker carries the new path the box shows the «Gmail no se pudieron leer» note. Only numbers in attachment file names are seen (the capture's `cn_tokens`); a number written only in a subject or body is still unknown |

### 2.7.51 Catalog 1a backend: products, supplier costs, exchange rates, cost parameters, quote-document lines, 2026-10-05 — built locally, not applied, not deployed

| | |
|---|---|
| Schema | two migrations, `20261005134832_slice7_catalog_products_suppliers.sql` and `20261005140628_slice7_catalog_pricing_inputs_document_lines.sql`: `catalog.product` gains Spanish content, specs, physical data, a generated `model_key` (unique per manufacturer) and a content origin; `catalog.supplier_product` gains price kinds and the wider observation key; new tables `catalog.product_image`, `catalog.supplier_terms`, `catalog.fx_rate`, `catalog.cost_parameter` (five public keys seeded) and `evidence.document_line`; `evidence.source_record` kind `quote_document`; `crm.note` subjects include products; nine new domain events. Inventory #50–#54 and the changed #26, #27, #44 are in [`DOMAIN.md`](DOMAIN.md) §7.5 |
| API | `/v2/catalog/*` reads, nine `/v2/commands/*` catalog commands and the multipart `add-product-image`, mounted only behind `ORIGENLAB_V2_QUOTING_ENABLED` (default `false`); exchange rates from Banco Central (BDE, else mindicador) stored in `catalog.fx_rate`; `GET /v2/workspace/fx` falls back to the newest stored rate when every source fails and writes USD and EUR through after a successful mindicador fetch (findic is shown, never stored); the published DHL Chile import tariff as data; no price calculation (catalog 1b) |
| Tools | `apps/api/scripts/catalog/`: four importers (price lists, supplier documents and costing sheets, quote history, cost parameters), each plan / apply / verify / rollback, loopback-only, disposable database or the clean room; and the enrichment tool (Claude, dry-run by default). Runbooks in [`OPERATIONS.md`](OPERATIONS.md) §14 |
| Proxy and dashboard | proxy allowlist for the catalog paths and a bounded multipart upload; no dashboard screen |
| Evidence | `apps/api/scripts/validate.sh` with disposable-cluster DSNs: **4012 passed**, 120 skipped (the V1 Alembic-head and TLS-server skips of §2.7.49); `supabase test db --local`: 27 files, **1157 assertions**, PASS; `supabase/audit` unit tests 352; `apps/dashboard-proxy` `npm run validate` **377 passed** + typecheck; `apps/dashboard` `npm run validate` **552 passed** (45 files) + build |
| Not done | **not applied to any database but disposable test clusters**: neither migration is on `origenlab_clean` or the hosted project (so the clean-room expected counts already name them, and the next clean-room rebuild is their first); not deployed (Render API, Worker); the `catalog` bucket, the dedicated Storage key and the BDE credentials do not exist ([`OPERATIONS.md`](OPERATIONS.md) §14.1–14.2); the euro series of BDE is unconfirmed and the BDE response parse has not run against the live service; catalog data was loaded into hosted on 2026-10-06 (§2.7.61); no dashboard screen reads or writes the catalog |
### 2.7.52 Email → cases rules (4b-auto): preview, apply, undo, 2026-10-05 — built, not applied, not deployed

| | |
|---|---|
| What | Spec `2026-10-05-gmail-auto-cases-design.md` (owner-approved): rules R1–R8 turn captured `gmail_message` evidence into case links, new cases with their quote, won/lost moves, or proposals. `apps/api/src/origenlab_api/v2/mail_rules.py` plans (pure, deterministic, idempotent per email); `mail_rules_repository.py` reads the snapshot and applies each action in **one transaction under one receipt** (`apply_mail_rule`, key `mail-rule:<evidence>:<rule>`) through the existing handlers (`open_commercial_case`, `add_case_organization`, `advance_case_stage`, `link_case_evidence`, `record_historical_quotation`, and for undo `void_historical_quote_revision`, `archive-organization`) plus four new ones: institution «por confirmar» (`machine_proposed`, named by its domain), win against a sent quote revision, unlink an evidence link, correct a won/lost stage |
| Rules | R5/R6 are checked before R1 (both need a linked thread). Two or more candidate cases/institutions/new numbers → proposal. Free-mail and supplier domains never create anything; Labdelivery mentions and campaign threads are R8. The capture stores no body, so R5 (purchase order) and R6 (otro proveedor) read the subject and attachment names only — phrase lists are named constants. A Gmail token missing its leading zero reads as the 0xxxx series (the dashboard's rule); the recorded quote number is the canonical `01239-26`, with the raw token in `printed_quote_numbers` |
| Attribution | Applied events are `actor_kind = 'worker'` with no `actor_operator_id` and `payload.attribution` = rule, reasons, email, `applied_by_operator_id`; the receipt and the rows (`owner_operator_id`, `confirmed_by_operator_id`, …) name the admin who pressed «Aplicar». Undo events are the operator's, `attribution.undoes_receipt_id` |
| Undo | R1/R2 → unlink; R3/R4 → void the quote revision, abandon the case as `discarded_by_correction` (origin link and timeline kept), and for R4 archive the institution if still `machine_proposed`; R5 → won corrected back to `negotiating` (then `quoting` if it came from there) + unlink; R6 → lost corrected back to the previous stage + unlink. An undone action is never re-planned |
| Schema | migration `20261005120000_slice7_mail_auto_case_corrections.sql`: event types `case_evidence.unlinked`, `opportunity.stage_corrected`; `crm.opportunity_stage_guard` replaced in place — a transaction that sets `origenlab.case_stage_correction` to the case id may move it from `won`/`lost` back to the `from_stage` of its latest `opportunity.staged` event, nothing else. Tables 45, policies 160, functions 32, SECURITY DEFINER 3 — unchanged; clean-room chain 41 → **42**, head `20261005120000`. `record_historical_quotation` accepts a `gmail_message` record that carries no assertions at all (live capture) and skips the promotion |
| API, proxy, dashboard | `GET /v2/workspace/mail-rules/preview` (mounted with V2), `POST /v2/commands/apply-mail-rules` and `POST /v2/commands/undo-mail-rule-action` (only behind `ORIGENLAB_V2_COMMANDS_ENABLED`); all three **admin only**. The proxy lists the three exact paths (POSTs behind the Origin/JSON/key guard, 128 KiB). Revisión gains an admin-only tab «Acciones automáticas» (Vista previa grouped by rule with reasons, Aplicar, applied actions with Deshacer + motivo) |
| Review fixes (merge gate, 2026-10-05) | only live 4a captures are planned (a `comms.message`, no staging hash, no assertions, not rejected); R5 needs an `OC*`/«orden de compra» attachment and a sender of the case institution (or free-mail on the thread), never a supplier or origenlab.cl — R6 the same sender rule — else a proposal; R3/R4 need contacto@ and a customer-bound hint; R4 claims its domain per pass; R1 defers to an attached quote number of another case; yearless CRM numbers and a correlative in another year are proposals; `printed_quote_numbers` is the token alone and the canonical number carries `quote_number_derivation` (`number_origin` stays `printed_historical`: no other existing value fits); apply takes the previewed pairs, ≤ 10 per call (the dashboard batches with progress), refuses a changed plan, and serialises per email with `pg_advisory_xact_lock`; undo R4 keeps an institution another open case uses, else removes its domain and archives it; the stage guard also requires the latest `opportunity.staged` to be a worker event; refused corrections are 409. DOMAIN.md §3.6.6 and ARCHITECTURE.md §3.1 amended |
| Golden set / dry run (read-only, `origenlab_clean`) | 61 staged records with one case link, each planned as if fresh: **54** auto to the same case (R1 24, R2 30), **7** proposals that include the right case, **0** wrong. Live dry run: `origenlab_clean` holds **0** live 4a captures, so **0** actions (the 31 non-staged records there are pre-4a manifests without sender/recipients and are out of scope) |
| Evidence | `apps/api/scripts/validate.sh` with disposable-cluster DSNs: **3508 passed**, 120 skipped (the same V1 Alembic-head and TLS-server skips as §2.7.50; no V2 DSN skip) — 41 planner, 12 database, 5 route, 4 quote-import tests new; `apps/dashboard` `npm run validate`: **558 passed** (46 files) + build; `apps/dashboard-proxy` `npm run validate`: **367 passed** (5 files); pgTAP, all 27 files run with psql on a fresh supabase/postgres 17 database carrying the full chain: **1117 ok**, 1 not ok (100 #22 — the disposable cluster sets role passwords; environmental), new file 076 (14) and 063 #10 retargeted; `cleanroom_verify_tests.sh --static` 13 passed; audit unit tests 339 OK; public-repo hygiene passed. `supabase test db --local` was not run: the CLI stack was held by another worktree |
| Not done | not run against `origenlab_clean` or hosted; the migration is not applied anywhere; not deployed. Worker-side run (spec §5: inside the 4a cron, `ORIGENLAB_WORKER_AUTO_CASES_ENABLED`), learned rules and the circuit breaker (§4), «Mover», the contact-point-per-sender (§3), the case-timeline «Sistema (correo)» rendering and the Resumen badge (§6), and the golden-set check against the 61 staged records (§8) are **not built**. the spec's §5 is amended in `~/data` (API-run first, worker-run later) |

### 2.7.53 §2.7.51 and §2.7.52 merged, applied to hosted and deployed; event-type union fix, 2026-10-05

| | |
|---|---|
| Merged | #642 (catalog 1a) and #643 (email → cases) into `main` at `e628bdce`; Render `origenlab` and `origenlab-dashboard` auto-deployed `e628bdce` |
| Hosted apply | the three `20261005…` migrations applied to `origenlab-v2` with `psql --single-transaction` as `origenlab_migrator` through the session pooler (`supabase db push` cannot: the CLI login role may not `set role origenlab_owner`, and the migrator has no access to `supabase_migrations`); the owner then inserted the three ledger rows from the SQL Editor — ledger **44**, head `20261005140628` |
| Defect found after merge | the two catalog migrations, written on a parallel branch, rebuild `crm.domain_event_type_check` from their own list and drop `case_evidence.unlinked` and `opportunity.stage_corrected`, which `20261005120000` had added: «Deshacer» of a rules-engine link and a stage correction are refused (pgTAP 076 #1–2 on `main`). `20261005210000_slice7_domain_event_type_union.sql` rebuilds the CHECK as the union. Local: pgTAP 28 files **1171 PASS**, replay evidence PASS (50 tables, 173 policies), clean-room static 13/13, chain 9/9 |
| Not done | the corrective migration is not yet applied to hosted (same psql route; ledger becomes 45); the dashboard-proxy Worker is not redeployed, so the catalog and mail-rules paths are still refused at the proxy |

### 2.7.54 Case writes part A — the case drawer's actions work, 2026-10-05 — built (merged and deployed: §2.7.56)

| | |
|---|---|
| What | The Oportunidades drawer's «Acciones» stop being three disabled buttons: «Cambiar etapa», «Marcar ganada», «Registrar seguimiento» and, for an institution «por confirmar», «Confirmar institución» (with a link to the institution's page to rename it). «Nueva revisión» stays disabled |
| API | new human case command `POST /v2/commands/record-case-won` {`opportunity_id`, `opportunity_version`, `quote_id`, `revision_no`, `note`}: `negotiating` → `won` against a sent, not-superseded revision on that case (409 `case_not_negotiating` / `quote_revision_not_current` / `case_version_conflict`, 404 `quote_revision_not_on_case`). Same `Deciding` (sales/admin), `Idempotency-Key`, receipt and `ORIGENLAB_V2_COMMANDS_ENABLED` switch as the other case commands (off → 404). The writer moved from `MailRulesRepository._record_case_won` to `v2/case_won.py`, shared with rule R5: a person's win is `actor_kind = 'operator'`, the rule's stays the system's. `advance-case-stage` still refuses `won` and now points to the new command. `/auth/session` adds `case_commands_enabled`; `GET /v2/workspace/opportunities/{id}/notes` returns the case's notes (person/organization note shape); the pipeline card carries the case `version` and `organization.version` |
| Proxy | `CASE_COMMAND_POST_PATHS` = `advance-case-stage` and `record-case-won` only, behind the Origin / JSON / `Idempotency-Key` guard (body ≤ 16 KiB); `open-commercial-case`, `link-case-evidence`, `add-case-organization`, `set-case-organization-role`, `record-case-interest` stay refused. GET `/v2/workspace/opportunities/<uuid>/notes` listed |
| Dashboard | `crm/caseCommands.ts` owns the two paths (pinned by `noWritePolicy`). Stage choices come from the stage table for the card's stage; lost/abandoned need a motive. «Marcar ganada» picks a sent, current revision (latest by default); from `quoting` it sends `advance-case-stage` → `negotiating` then `record-case-won`, shows both receipts, and if only the first lands says the case stayed at «Negociando». Enabled only with `case_commands_enabled` and role sales/admin; the follow-up note and «Confirmar institución» need `crm_authoring_enabled`. Refusals are shown in Spanish; every write (and a stale-version refusal) refetches the pipeline |
| Schema | none: `opportunity.staged` / `opportunity.closed` exist, and `origenlab_api` already wrote `won_quote_id` through R5 |
| Evidence | `apps/api/scripts/validate.sh` with disposable-cluster DSNs: **4103 passed**, 120 skipped (the V1 Alembic-head and TLS-server skips of §2.7.49; no V2 DSN skip) — new: 8 database tests for the win and the case-notes read, route/switch/session tests; `apps/dashboard` `npm run validate`: **574 passed** (48 files) + build; `apps/dashboard-proxy` `npm run validate`: **408 passed** (6 files) + typecheck; public-repo hygiene passed |
| Not done | not deployed. To ship: merge → Render auto-deploys the API and the dashboard → the owner deploys the dashboard-proxy Worker (until then the two commands and the notes read are refused at the proxy and the drawer says so). The actions are live only where `ORIGENLAB_V2_COMMANDS_ENABLED=true` (and, for the note and the institution, `ORIGENLAB_V2_CRM_AUTHORING_ENABLED=true`). No rename field in the drawer (rename stays on the institution page); no «Nueva revisión» |

### 2.7.55 Case writes part B — «Elegir revisión vigente», «Registrar cotización», «Nueva revisión», 2026-10-06 — built (merged, applied and deployed: §2.7.56)

| | |
|---|---|
| What | The Oportunidades drawer gains the quote acts part A left disabled. A case blocked by «Hay más de una revisión vigente» (`canonical_undetermined`) offers «Elegir revisión vigente»; «Registrar cotización» and «Nueva revisión» are enabled at «Cotizando»/«Negociando». Built on PR #646 (part A, not yet merged) |
| API | two new human case commands, same `Deciding` (sales/admin), `Idempotency-Key`, non-blank note, case-version compare-and-set (409 `case_version_conflict`) and `ORIGENLAB_V2_COMMANDS_ENABLED` switch as `record-case-won`. `POST /v2/commands/resolve-current-revision` {`opportunity_id`, `opportunity_version`, `quote_id`, `revision_no`, `note`}: every other current revision of the quote gets `superseded_by_revision_no` = the chosen one + `superseded_at` and a `quote_revision.superseded` event; nothing voided; case `version + 1` (`v2/current_revision.py`). `POST /v2/commands/record-case-quotation` {…, `quote_number`, `source_record_id`, `document_sha256`, `supersedes_revision_no`?}: a quote already sent, from a `gmail_message` record already linked to the case that carries the document and a `sent_at`; `printed_historical` quote, `sent` `historical_import` revision; a number already a quote on another case is refused (409 `number_on_other_opportunity`); with `supersedes_revision_no` it is «Nueva revisión»; case `version + 1`. The writer that `record_historical_quotation` and rules R3/R4 use moved from `V2QuoteImportRepository._record_historical_quotation` to `v2/case_quotation.py` (`record_quotation`), shared by all three, with no SQL duplicated; `case_command_repository.py` still names no `crm.quote`. New read `GET /v2/workspace/opportunities/{id}/mail-documents`: the case's linked Gmail messages and their documents (file name, SHA-256, capture tokens, whether already a revision) |
| Schema | migration `20261006120000_slice7_historical_current_revision_choice.sql`: `quote_revision_supersession_forward` lets a `historical_import` revision be superseded by any other revision of its quote (never itself) — its number is import order, not send order — and `crm.quote_revision_historical_guard` is replaced in place to require the superseding revision to be current (`sent`/`approved`, not superseded, read `for share`), so no cycle. V2-authored revisions keep the forward rule. No table, column, grant, policy, event type or function added (functions 33 unchanged); clean-room chain 45 → **46**, head `20261006120000`. pgTAP `077` (10) |
| Proxy | `CASE_COMMAND_POST_PATHS` = `advance-case-stage`, `record-case-won`, `resolve-current-revision`, `record-case-quotation` (same Origin / JSON / key guard, ≤ 16 KiB); `open-commercial-case`, `link-case-evidence`, `add-case-organization`, `set-case-organization-role`, `record-case-interest`, `record-historical-quotation` stay refused. GET `/v2/workspace/opportunities/<uuid>/mail-documents` listed |
| Dashboard | `crm/caseCommands.ts` owns the four paths (`noWritePolicy`). «Elegir revisión vigente»: one radio per current revision (number, send date, file), nothing preselected. «Registrar cotización»: pick an unrecorded document from the case's linked messages, type the printed number (the file name's token is shown as a hint, never filled in). «Nueva revisión»: pick the sent, current revision it replaces; the number is that quote's. Refusals in Spanish; every write refetches the pipeline |
| Evidence | disposable PostgreSQL 17 cluster (`supabase/postgres:17.6.1.165`): `apps/api/scripts/validate.sh` **4139 passed**, 120 skipped (the same V1 Alembic-head and TLS skips as §2.7.54; no V2 DSN skip) — new: 13 database tests (`test_v2_case_quote_commands.py`), boundary/route/switch tests; `apps/dashboard` `npm run validate` **582 passed** (48 files) + build; `apps/dashboard-proxy` `npm run validate` **424 passed** (6 files) + typecheck; pgTAP, all 29 files with psql on the cluster carrying the full chain: **1180 ok**, 1 not ok (100 #22, role passwords set by the disposable cluster — environmental, as §2.7.52); `cleanroom_verify_tests.sh --static` 13 passed; audit unit tests 352 OK; public-repo hygiene passed. `supabase test db --local`, `replay_evidence.sh` and the gap checker against a local audit were not run (no Supabase CLI here); CI's Slice 0 job runs them |
| Not done | not merged (stacked on #646), the migration is not applied anywhere, not deployed. To ship: merge #646 then this → Render auto-deploys the API and the dashboard → apply `20261006120000` to hosted (same psql route as §2.7.53; ledger + 1) **before** an operator uses «Elegir revisión vigente» on an older revision → the owner deploys the dashboard-proxy Worker. Live only with `ORIGENLAB_V2_COMMANDS_ENABLED=true`. No linking of a new Gmail message from the drawer (the message must already be linked to the case) |

### 2.7.56 §2.7.54 and §2.7.55 merged, applied to hosted, proxy deployed, 2026-10-06

| | |
|---|---|
| Merged | #645 (contact name with a comma) at `83a5a1f`; #647 (case writes part B, carrying #646's part A commits, so #646 closed as merged with it) at `855e5ec`. Render `origenlab` (API) and `origenlab-dashboard` both live on `855e5ec` (deploys finished 12:11:51 and 12:09:27 UTC, read with the Render CLI) |
| Hosted apply | `20261006120000_slice7_historical_current_revision_choice.sql` applied to `origenlab-v2` with `psql -X --no-psqlrc -v ON_ERROR_STOP=1 --single-transaction` as `origenlab_migrator` through the session pooler (`sslmode=verify-full`); the owner inserted the ledger row from the SQL Editor — ledger **46**, head `20261006120000`. `pg_get_constraintdef` of `quote_revision_supersession_forward` returns the `historical_import` exception. `20261005210000` (§2.7.53's corrective migration) was already in the ledger |
| Proxy | `origenlab-dashboard-proxy` deployed from `main` with `wrangler deploy` (validate 424 passed; version `352906e6`), route `dashboard.origenlab.cl/api*`, secrets unchanged. This also ships the paths §2.7.53 listed as still refused at the proxy (catalog, mail rules) and part A's `advance-case-stage` / `record-case-won` / case notes |
| Switches | `GET /auth/session` through the deployed proxy, as an admin: `case_commands_enabled: true` and `crm_authoring_enabled: true`, i.e. `ORIGENLAB_V2_COMMANDS_ENABLED` and `ORIGENLAB_V2_CRM_AUTHORING_ENABLED` are on |
| Not done | no operator has yet exercised the drawer actions on hosted |

### 2.7.57 Oportunidades «Tablero» readable with one crowded column, 2026-10-06 — built, not deployed

| | |
|---|---|
| What | `apps/dashboard` only (`crm/pages/PipelineBoard.tsx`). Each board column scrolls on its own (`max-h-[70vh]`) and shows its count; a column with more than 8 cases is grouped by the age of its latest sent revision (≤ 30 / 90 / 180 / 365 days, older, none), newest group open and the rest collapsed. A board card leads with the client, then quote number · revision · send date, then **one** status line (the blocking reason, else «histórico», «sin Drive», «sin Gmail», «sin contacto», «sin cotización») instead of the stage/status chips and the Drive/Gmail row; the full suggestion is the card's tooltip. The «Tarjetas» view is unchanged. No API or proxy change |
| Not built | a per-column total in CLP: the pipeline read carries no amounts, and historical revisions hold no parsed totals |
| Evidence | `apps/dashboard` `npm run validate`: **589 passed** (49 files) + build; new `PipelineBoard.test.tsx` (7) |

### 2.7.58 «Último contacto»: Resumen ages a case from the last email OrigenLab sent, 2026-10-06 — built, not deployed

| | |
|---|---|
| What | A follow-up sent from contacto@ on a case's Gmail thread now resets that case's clock in Resumen, with nobody recording it. A case is aged from the newer of its latest quote sent and the newest **outbound** email on its threads; a client who wrote after that is marked «Respondió · te toca» and listed first in its group. Read-side only: no schema, no write, no rule change |
| API | `GET /v2/workspace/pipeline` cards gain `last_contact` {`outbound`, `inbound`}: each the newest `gmail_message` evidence (`at`, `subject`, Gmail `url`) on a thread tied to the case — the thread of an email linked to it (`crm.opportunity_evidence`, by a person or rules R1/R2) or of the email a quote revision was recorded from. Quarantined and rejected records are skipped. Direction is `mail_rules.mail_direction`, now shared with the email → cases planner (capture direction, else `direction_hint`, else the sender's domain). One more query in the pipeline's single pipelined round trip |
| Dashboard | `followUps.ts` ages from the last touch and flags replies; each Resumen row says «desde tu correo» when the email reset it, links «Seguimiento» (our last email) or «Respuesta» (theirs), and its tooltip names both dates. Also: `AuthGate` attaches its 401 listener in a layout effect, so a refusal right after the dashboard mounts is never lost (the `ProfileSelector` mid-session test failed under load because of exactly that gap) |
| Limits | only threads already tied to a case count: an email on a brand-new thread (a follow-up written as a fresh message) is not seen until it is linked. Inbound/outbound only — no body is read |
| Evidence | `apps/api/scripts/validate.sh` with disposable-cluster DSNs: **4142 passed**, 120 skipped (the usual V1 Alembic-head and TLS skips; no V2 DSN skip) — new: `last_contacts` unit test, an end-to-end pipeline test on PostgreSQL 17; the pipeline statement budget is 9 (2 setup + 7 data, still one round trip); `apps/dashboard` `npm run validate`: **593 passed** (49 files) + build, the full suite twice more under load after the `AuthGate` fix |

### 2.7.59 Email → cases R1/R2 run automatically, with a stop switch, 2026-10-06 — built, not deployed

| | |
|---|---|
| What | Rules R1 (same Gmail thread as an email already on exactly one open case) and R2 (a quote number held by exactly one open case) now link new email by themselves while an admin has switched the automatic run on. R3–R6 (open a case, register an institution, win, lose) still wait for «Aplicar». Owner-approved 2026-10-06; [`DOMAIN.md`](DOMAIN.md) §3.6.6 records it |
| API | `v2/mail_rules_auto.py`: a daemon thread started with the API where `ORIGENLAB_V2_COMMANDS_ENABLED` mounts the commands, every `ORIGENLAB_V2_AUTO_MAIL_RULES_INTERVAL_SECONDS` (default 300; **0** never starts it). Each pass re-plans and applies the `auto` R1/R2 actions (at most 100) through `MailRulesRepository.apply_planned` — the same receipt key, per-email advisory lock and system attribution as «Aplicar», plus `attribution.trigger = 'automatic'` — on behalf of the admin who switched it on; if that operator is no longer an active admin the pass does nothing and the tab says why. New admin-only `POST /v2/commands/set-auto-mail-rules` {`enabled`, `note`}: one `set_auto_mail_rules` receipt per flip; the newest is the state; none = off; flipping to the current state is 409 `auto_mail_rules_unchanged`. `GET /v2/workspace/mail-rules/preview` gains `automatic` (switch, who and when, timer, this process's last pass); applied rows gain `automatic` |
| Schema | none: the switch is `platform.command_receipt` rows, which `origenlab_api` already inserts |
| Proxy | `MAIL_RULES_COMMAND_POST_PATHS` + `set-auto-mail-rules` (same Origin / JSON / key guard, ≤ 128 KiB) |
| Dashboard | Revisión → «Acciones automáticas» gains «Vincular correos automáticamente»: Activo / Detenido / Activo, sin actuar, who switched it and why, the last pass, «Activar» / «Detener» with a required reason. Applied rows say «automática, a nombre de …»; «Deshacer» works the same |
| Evidence | `apps/api/scripts/validate.sh` with disposable-cluster DSNs: **4318 passed**, 121 skipped (the V1 Alembic-head and TLS skips; no V2 DSN skip) — new: one database test (off by default; only an admin switches, with a note; R1 and R2 linked and R4 left; nothing twice; undo; off stops the next link; a demoted admin stops the run until another admin switches it on), route tests, timer tests; `apps/dashboard` `npm run validate` **597 passed** (49 files) + build; `apps/dashboard-proxy` `npm run validate` **426 passed** (6 files) + typecheck |
| Not done | not deployed. To ship: merge → Render auto-deploys the API and the dashboard → the owner deploys the dashboard-proxy Worker (until then «Activar» is refused at the proxy) → an admin switches it on in Revisión. Runbook: [`OPERATIONS.md`](OPERATIONS.md) §8.9 |

### 2.7.60 Recorded quote PDFs filed in the case's Drive folder, 2026-10-06 — built, not deployed

| | |
|---|---|
| What | A quote revision recorded from a captured email («Registrar cotización», «Nueva revisión», rules R3/R4) now gets its PDF put in `Cotizaciones/Casos/<case folder>` in contacto@'s Drive with nobody downloading or uploading, and the case card links it. Recommended 2026-10-06 when the owner asked for the better way, not yet confirmed: keep the case-first layout of §2.7.24 (one folder per case, «Caso <serial> — <institución>», files «<número> r<n> — <archivo>», identity in `appProperties`, never in names) and contacto@'s Drive |
| Worker | `origenlab-worker drive-file` (`apps/worker/src/origenlab_worker/drive_filing.py`), run after `gmail-sync` in the same cron: newest first, at most 20 per run; reads the `.eml` from Storage, takes the part whose SHA-256 is the revision's `pdf_sha256`, and files it through `origenlab_api.v2.quote_case_archive.archive_case` (wrong account, legacy parents and name/key conflicts refused; every upload checked by Drive's checksum and a fresh download). The case folder is the one an earlier record or a sibling revision's Drive file names, else the folder whose opening quote number is the case's, else a new one keyed by the CRM case id. One `evidence.source_record` of kind `drive_file` (`drive_file:<sha256>`, `reviewed`) per document. `drive_client.py`: Drive v3 over `urllib`, exactly the `drive` scope (anything wider refused), no rename/move/trash/delete. `drive-ledger-import`: records the 2026-09-26 archive's `archive_links.jsonl` so earlier cases keep their folders. Paused until `ORIGENLAB_WORKER_DRIVE_FILING_ENABLED=true` |
| API | `GET /v2/workspace/pipeline` reads each revision's `drive_file` record in the revisions query (no new statement) beside the ledgers (`drive_links_from_records`; a ledger link wins for the same document): the drawer's PDF and folder links and Resumen's Drive links now come from the database too |
| Schema | none: `evidence.source_record` kind `drive_file` exists (§2.7.6) and `origenlab_worker` already inserts evidence |
| Render | `render.yaml`: the cron's start command runs `drive-file` after `gmail-sync`; seven new cron variables (`sync: false`); `buildFilter` adds `apps/api/**` (the worker now depends on `apps/api` for the archive) |
| Evidence | `apps/worker/scripts/validate.sh` with disposable-cluster DSNs: **330 passed**, 1 skipped (the same one as §2.7.49) — new: filing end to end as `origenlab_worker` with an in-memory Drive (filed once, recorded, no `crm.*`/`outbound.*` row, the API's card links the file and folder, a second revision into the same folder), a case the laptop archive filed keeps its folder after `drive-ledger-import`, a wrong account writes nothing; the Drive client's scope refusals; CLI pause and configuration. `apps/api/scripts/validate.sh` with disposable-cluster DSNs on the merged code: **4319 passed**, 121 skipped, 0 failed (new: `drive_links_from_records`) |
| Not done | not deployed. Owner setup ([`OPERATIONS.md`](OPERATIONS.md) §8.10): the Drive consent's three values, the `Casos` / `Pendientes` / `Enviadas` folder ids and the start command on the cron, `drive-ledger-import` once from the laptop, then the switch. The «Archivo Drive» page still lists the ledgers only. Historical revisions without a captured `.eml` (step B of §2.7.24, 45 Gmail-only PDFs) are not filed by this |

### 2.7.61 Catalog loaded into the hosted database, 2026-10-06 — applied and verified

The catalog 1a importers (§2.7.51) were applied to `origenlab-v2` by the owner, each plan's
`apply` then its read-only `verify`, all under one operator. Every `verify` found every planned
row present with no mismatch and the plan hash the `apply` used.

| Importer | Plan sha256 | Applied (UTC) | Rows | Verify |
|---|---|---|---|---|
| `price_lists` | `27e7f9e2…` | 13:32 | **13,846**: 6,597 products, 7,246 price observations, 2 organizations, 1 supplier terms | 13,846 present, 0 mismatched |
| `supplier_documents` | `0ac0ea5d…` | 14:42 | **93**: 44 products, 46 price observations, 3 organizations | 93 present, 0 mismatched |
| `quote_history` | — | after 14:42 | **150** quote documents (evidence records with their lines) | 150 present, 0 mismatched |
| `cost_parameters` | — | not run | no plan exists; `catalog.cost_parameter` holds the 5 migration-seeded public values only | — |

| | |
|---|---|
| Events | one `source_record.migration_manifest_recorded` per load (importer, plan hash, counts), read back from hosted `crm.domain_event`; plus one event per catalog row written (`product.created`, `product.cost_recorded`, `organization.created`, `organization.supplier_terms_set`) — 13,847 and 94. Quote documents are evidence and carry no per-row event, so `quote_history` recorded 1 |
| Route | `--hosted-target --authorize-hosted-connection --authorize-supavisor-session-route` (`apps/api/scripts/catalog/_common.py`, [`OPERATIONS.md`](OPERATIONS.md) §14.5): PR #623's Supavisor session route with `verify-full`; catalog rows written by `origenlab_api.<project ref>`, the manifest by the migrator as `origenlab_owner`; operator named by id. The code ran from an unpushed branch and was reviewed only afterwards, in the PR that merged it (`tests/test_catalog_import_hosted.py`: refusals, login swap, redaction, apply → verify → rollback with both hosted logins mapped onto a disposable database). Rollback on hosted may be refused (exit 12, nothing deleted); the pre-load `pg_dump` is the way back |
| Not done | cost parameters beyond the seeds (set per key with `set-cost-parameter`, §14.3); the `catalog` bucket and product images; `ORIGENLAB_V2_QUOTING_ENABLED` on Render, so `/v2/catalog/*` is still unmounted and no screen shows the catalog |

### 2.7.62 CRM redesign phase 1: buttons, dialogs and confirmations, 2026-10-06 — built, not deployed

First phase of the Oportunidades/Revisión redesign (owner-approved 2026-10-06: states Solicitada · En estudio · Enviada · Conversación · Ganada · Perdida · En pausa; Enviada → Conversación automatic and undoable; follow-up on day 3 and 14, close suggested at 30). `apps/dashboard` only.

| | |
|---|---|
| Shared parts (`crm/ui.tsx`) | `Button` (primary / secondary / danger / quiet; `busy` disables it, shows a spinner and a «…ndo» label, sets `aria-busy`), `Spinner`, `toast()` + `<Toaster/>` mounted once in the CRM shell; `ConfirmDialog` uses them and cannot be dismissed while saving; `Drawer` takes `busy` and shows «Actualizando…» |
| Fixes | «Registrar seguimiento» opened the note form by itself on every later drawer (the signal now belongs to one case and is dropped on close); the drawer pulled focus back to ✕ after every refresh (`onClose` read through a ref); nothing showed a refresh after a save (`useResource` returns `refreshing`, and a reload keeps the rows on screen while a new read still starts from «loading»); «Archivar nota» could be pressed twice; «Confirmar institución» showed the write-disabled reason when only the version was missing; «Marcar ganada» used a new key on every call (keys now belong to the form and are renewed only after a refusal); «Acciones automáticas» Activar / Detener / Confirmar showed no working state and Cancelar stayed clickable. Case actions, notes and the email-rules tab use `Button` and confirm with a toast. The side-menu note no longer says only Marketing writes |
| Evidence | `apps/dashboard` `npm run validate`: **605 passed** (49 files) + build — new: the note form opens once on its own case, focus stays put through a refresh and «Actualizando…» shows, a refused win retries with a new key, archive is one press, `Button` busy, toast, `useResource` refreshing and no stale rows on a new read |
| Not done | the rest of the dashboard's buttons (Marketing, Organizaciones, Personas, Proveedores) still use their own classes; phases 2–5 (states, «Hoy», «Ordenar», interest and Drive on the case) |

### 2.7.63 CRM redesign phase 2: six states, «Perdida» with a reason, «En pausa hasta…», draggable Tablero, 2026-10-06 — built, not deployed

Second phase of the redesign in §2.7.62. No migration: `crm.task`, its grants and RLS and the `task.*` event types have existed since Slice 0.

| | |
|---|---|
| API | The three W11 task commands, mounted with the case commands behind `ORIGENLAB_V2_COMMANDS_ENABLED`, same `Deciding` (sales/admin), `Idempotency-Key` and non-blank note (`v2/task_commands.py`, `task_command_repository.py`, `task_command_routes.py`). `POST /v2/commands/create-task` {`opportunity_id`, `title`, `due_at` (zone required), `note`}: an open case only (409 `case_is_closed`), owner = the operator, `task.created` event; the case's stage and version do not move. `POST /v2/commands/complete-task` / `cancel-task` {`task_id`, `task_version`, `note`}: compare-and-set on the task version (409 `task_version_conflict`), only while `open` (409 `task_is_not_open`); `cancel_reason` = the note. `GET /v2/workspace/pipeline` cards gain `open_tasks` (earliest due first, with the owner's name), and `next_action` is the earliest open task (`source: "task"`, `due_at`) when there is one — an eighth query in the same pipelined round trip |
| Proxy | `CASE_COMMAND_POST_PATHS` += `create-task`, `complete-task`, `cancel-task` (7 paths, same guard, ≤ 16 KiB) |
| Dashboard | The stages read as six states: Solicitada (`lead`, `qualifying`), En estudio (`qualified`), Enviada (`quoting`), Conversación (`negotiating`), Ganada, Perdida (`lost`, `abandoned`). «En pausa» is not a stage: an open case whose earliest open task is due later. The drawer's «Cambiar estado» replaces «Cambiar etapa»: one click per target; a move walks the stage table in as many `advance-case-stage` steps as it needs (`stagePath`), each with its receipt; «Perdida» is one click on a reason («Sin respuesta» → `abandoned`, the rest → `lost`, detail optional) and cancels the case's open tasks; «En pausa» takes a date and a reason and writes the task «Retomar: <motivo>» due 09:00 that day; a paused case shows «En pausa hasta …» with «Retomar ahora» (cancels the task). Tablero: seven columns (the six states + En pausa); for sales/admin a card can be dragged to another column — the drop records nothing by itself: it opens the same form in a dialog («Ganada» opens «Marcar ganada» in the drawer). The drawer shows «Próxima tarea» when the case has one |
| Evidence | `apps/api` `scripts/validate.sh`: **4349 passed**, 121 skipped (task commands against PostgreSQL: written with their event, the case never moves, a closed case takes none, stale version and second close refused, a refusal writes nothing, replay writes once). `apps/dashboard-proxy` `npm run validate`: 426 passed. `apps/dashboard` `npm run validate`: **615 passed** + build |
| Not done | «Nueva solicitud» (opening a case from the browser stays refused at the proxy); phase 3 «Hoy» (today's tasks, institutions and people to add, the day-3/14/30 follow-up rhythm, automatic Enviada → Conversación) |

### 2.7.64 Tablero cards redesigned and «Decidir casos» for the historical stage, 2026-10-06 — built, not deployed

`apps/dashboard` only, after the owner's review of §2.7.63 in production.

| | |
|---|---|
| Cards (`PipelineBoard.tsx`, `caseDisplay.ts`) | A readable institution name when the CRM only holds a domain or a slug (from the quote title or the PDF's file name; the CRM name stays in the tooltip), the contact, a «N d» badge for days since the quote was sent (coloured by the 3 · 14 · 30 rhythm), quote · revision · date, the model the PDF's name prints (`UP400St`, `T10`…), one line on where the case stands (paused until, blocker, «Respondió 02 oct · te toca», «Seguimiento …», «Sin respuesta · N d») and direct Drive / Gmail links. Cards rise in, lift on hover and tilt while dragged (off under reduced motion). No more «histórico» on every card |
| Tablero | All cards shown — the collapsed age groups are gone; «Ordenar»: más recientes, más antiguas, respondieron primero, institución; by date, thin dividers mark the age bands. The Tablero is now the default view |
| «Decidir casos» | From the notice «N de M casos muestran «Enviada · histórico»» (sales/admin): every historical case with a proposed decision — the client wrote after the quote → Conversación; 45+ days without an answer → Perdida · Sin respuesta; otherwise a follow-up task due now. Each row can be changed (En pausa with date and reason, Ganada against the newest sent revision) or left out; «Aplicar N decisiones» runs the existing commands one case at a time and reports each. A case with an open task no longer counts as historical (`stageBasis`) |
| Evidence | `apps/dashboard` `npm run validate`: **619 passed** + build |

### 2.7.65 CRM redesign phase 3: «Hoy» replaces Resumen, 2026-10-06 — built, not deployed

`apps/dashboard` only: no API, proxy or schema change. The first page (route `resumen`, menu «Hoy»).

| | |
|---|---|
| Top | Next quote number and the day's dólar / euro / UF with the converter, side by side (unchanged components) |
| Tareas de hoy | Open `crm.task` rows due by tonight, overdue first; «Hecho» = `complete-task`; «+1 semana» = the same task a week later (`create-task`) then `cancel-task` of this one |
| Te toca responder | Open cases whose client wrote after OrigenLab's last email; «Abrir respuesta» (Gmail) and, for a case still «Enviada», «Pasar a Conversación» (`advance-case-stage` → `negotiating`, note «El cliente respondió el …») |
| Seguimientos | The 3 · 14 · 30-day rhythm counted from OrigenLab's last touch (quote or later email): primer seguimiento (3–13), segundo (14–29), «¿Cerrar?» (30+) with «Cerrar sin respuesta» (the «Perdida» form with «Sin respuesta» chosen). Cases with an open task, a reply, or a stage that is only the historical import's trace are left out |
| Aside | Cases still to decide (link to «Decidir casos»), machine-proposed institutions of open cases with «Confirmar» (`confirm-organization`), «Personas por agregar» (the existing person suggestions, first five), blocked cases, link to Revisión for the email actions |
| Removed | `crm/followUps.ts` and its test: the old «Hacer seguimiento / Esta semana / Más de un mes» grouping is replaced by `crm/today.ts` |
| Evidence | `apps/dashboard` `npm run validate`: **617 passed** + build — new: today lists (tasks due, rhythm, replies, institutions), «Hecho», «+1 semana», «Pasar a Conversación», «Cerrar sin respuesta», viewer sees no write button |
| Not done | automatic Enviada → Conversación from the email rules (still one click here); the labdelivery mailbox; «Ordenar», interests and Drive on the case (phases 4–5) |

### 2.7.66 Design review fixes on Hoy and the Tablero, 2026-10-06 — built, not deployed

From a design critique of the shipped screens rendered with invented data (`apps/dashboard` only).

| Finding | Fix |
|---|---|
| «Hoy»: the quote-number box squeezed beside the exchange rates, its «¿Ya existe este número?» field spilling over the dólar card | The two are stacked full width again, as on Resumen |
| Tablero: seven columns wider than the screen, Ganada and Perdida off to the right; names cut to «Aeroservicios N…» | Five working columns share the width and Ganada / Perdida are narrower with compact cards (name, quote, how it ended); names wrap to two lines; dates drop the year when it is this one («24 sept») |
| «Hoy» on a phone: rows squeezed the case name to «C…» beside the buttons | The text keeps 12rem and the buttons wrap below |
| Count badges carried a «!» glyph that read as an error | Plain counts |
| «Personas por agregar» unreadable in the narrow aside | Moved to the main column |
| Oportunidades: a disabled «Nueva oportunidad» with «Escritura desactivada…» under it | Removed (opening a case from the browser is not a command the proxy allows) |
| Evidence | `apps/dashboard` `npm run validate`: **618 passed** + build |

### 2.7.67 «Hoy» as one traffic-light follow-up list, the product on every card, a foldable menu, 2026-10-06 — built, not deployed

`apps/dashboard` only: no API, proxy or schema change.

| Change | What it does |
|---|---|
| «Hoy» order | «Te toca responder» first, then «Seguimientos», then «Otras tareas de hoy» |
| One follow-up list | A case whose «Seguimiento …» task is due today («Decidir casos», «+1 semana») now sits in «Seguimientos» with its «Hecho» / «+1 semana» buttons and «Programado · Vence hoy». It used to be a second list, «Tareas de hoy», that hid those cases from «Seguimientos». «Otras tareas de hoy» keeps only the other due tasks («Retomar: …», a call) |
| Traffic light | «Seguimientos» is green from day 3, yellow from day 14 and red from day 30 since OrigenLab's last touch: a coloured stripe, day chip and group header on each row, and a legend |
| Product | `quoteProduct` reads what the quote is for from the quote email's subject («Cotización Balanzas Ohaus» → «Balanzas Ohaus»), adding the model the PDF name ends with («Sonicador · UP400St»). Campaign, supplier-form and empty subjects fall back to the model. Shown on every Tablero card (closed ones too) and on each «Seguimientos» row |
| Menu | On wide screens the section list folds into a rail of icons («Contraer menú»); the choice is kept in the browser. «Oportunidades» uses the full screen width |
| Gmail | Every follow-up row has «Responder en Gmail»: it opens the case's thread, so the reply stays in it and the count restarts once the email is captured. A case with no thread gets «Nuevo correo» (compose, addressed when the address is not masked). Every Gmail link (cards, Hoy, the drawer, «Decidir casos») now opens `contacto@origenlab.cl` (`authuser`) instead of the API's `mail/u/0`, which opened whichever account the browser lists first |
| Evidence | `apps/dashboard` `npm run validate`: **627 passed** + build |

### 2.7.68 «Seguimientos» simpler, and a written follow-up clears itself, 2026-10-06 — built, not deployed

`apps/dashboard` only: no API, proxy or schema change.

| Change | What it does |
|---|---|
| Row | Days (a round chip in the traffic-light colour), the case, what the quote is for, the quote number and who. One button, «Responder en Gmail» (icon only on a phone); the rest behind «⋯»: «Recordar en 1 semana» (any row: writes a «Seguimiento …» task a week out, cancelling the due one), «Ya le escribí» (a row a task put there) and «Cerrar sin respuesta». Five rows per colour before «Ver N más» |
| No «Hecho» needed | A «Seguimiento …» task counts as done once OrigenLab's email on the case's thread is dated on or after the task's day: the row leaves «Hoy» when the sync captures the reply, and the case comes back on the rhythm three days later if the client stays silent. The task itself stays open in the CRM until someone completes it |
| Day 3 first | A scheduled follow-up waits for day 3 like any other: a quote sent today or yesterday is not chased today |
| Evidence | `apps/dashboard` `npm run validate`: **629 passed** + build |

### 2.7.69 Mail triage on a Procrastinate queue, 2026-10-06 — built, not applied, not deployed

| | |
|---|---|
| What | A cheap filter in front of the model for captured mail: rules settle machine mail (our own, bounces, automatic replies, invitations, bulk, no-reply); purchase orders, lost signals, CN follow-ups, quote requests and any other person are matched against the catalog and, when enabled, read by Claude for products, lead status, urgency and a Spanish summary. Every reading is recorded as `evidence.assertion` proposals; nothing is decided. Owner decision D2 ([`ARCHITECTURE.md`](ARCHITECTURE.md) §8): the jobs run on Procrastinate in this database, not `pgmq` |
| Schema | `20261006180000_slice4_procrastinate_triage_queue.sql` — schema `procrastinate` (Procrastinate 3.10.0 vendored verbatim; four tables, 16 worker policies, every function INVOKER with a pinned `search_path`, worker-only grants); outside the seven schemas, so the inventory (50 tables, 173 policies) is unchanged. `20261006180100_slice4_triage_assertion_kinds.sql` — `evidence.assertion.kind` gains `message_triage` and `product_mention`; no grant or policy changes. Migrations: **48**, head `20261006180100` |
| Worker | `mail_text.py` (reply text from the `.eml`), `triage_rules.py` (classes, `TRIAGE_VERSION = 1`), `catalog_match.py` (exact `model_key`, then Spanish full text), `triage_model.py` (proposes the case **stage** in `crm.opportunity.stage` keys — the Tablero's columns — from the cases the Gmail thread is linked to, each move checked against `case_commands.STAGE_TRANSITIONS`; never moves a case; default `claude-opus-5-5` at low effort, structured output, cached system prompt, refusal fallback; the message sent as untrusted data; catalog ids kept only if offered), `triage.py` (one message, one transaction), `task_queue.py` (`triage_message` with a bounded retry for model outages only; `sweep_untriaged` every minute, 14 days back), `triage_cli.py` (`triage-worker`, `triage-once`). Every queue connection runs the worker's probe for the queue's grants; the triage connection for its own (SELECT on comms/evidence/catalog, INSERT on `evidence.assertion`) |
| Render | `render.yaml` declares the Background Worker `origenlab-mail-triage` (starter, Oregon, root `apps/worker`) — **not created**. Observed through the Render API on 2026-10-06: the cron `origenlab-gmail-sync` **exists and runs** every 10 minutes (last success 21:20 UTC) with start command `origenlab-worker gmail-sync` only — the `drive-file` step of §2.7.60 is not in the deployed command |
| Evidence | `apps/worker/scripts/validate.sh` locally: **377 passed**, 45 skipped (database tests; no local Docker) — new: rules, text extraction, catalog tokens, request and answer validation, one-message flow with fakes, queue wiring on the in-memory connector, the vendored schema equal to the pinned library's, the Render block. New database tests (`test_triage_database.py`) and pgTAP `supabase/tests/078_triage_queue.sql` (24 assertions) run in CI only |
| Evaluation on real mail | 2026-10-06, read-only, the 325 captured messages with evidence in hosted V2 (2026-09-25 → 10-06), on database fields only (subject, sender, labels, attachment names — no bodies or machine headers): **54 (17 %) would reach the model**; 184 bounces (the Cyber campaign), 48 our own, 36 quote requests, 14 tender alerts, 14 other people, 10 notifications, 8 bulk, 5 automatic replies, 4 purchase orders, 2 «REMOVER». The run found five misreadings, fixed in this change with regression tests: «REMOVER» replies, «Ausencia…» notices, a scanner's «Undelivered Mail» bounce, Wherex tender alerts (now `tender_notice`, not for the model) and an «Anulación OC …» subject. Catalog: 6,641 products, all with `model_key`, none with `name_es`; no subject model number matched, and single-word full-text matches were accessories, so a text candidate now needs two matching words. Not yet measured: body reading and the model stage (need the `.eml` from Storage: `scripts/triage_export.py` + `scripts/triage_eval.py`) |
| API | `mail_rules_repository._EVIDENCE_SQL` treated *any* assertion as «not a live capture»: the triage's readings would have hidden every new email from the email → cases rules (R1/R2 automatic linking and «Aplicar»). It now ignores `message_triage` / `product_mention`; DB test `test_a_triage_reading_does_not_hide_a_live_email_but_another_assertion_still_does` |
| Review | `20261006180200_slice4_triage_review.sql` — `evidence.triage_review` (DOMAIN.md §7 #55, append-only verdicts: approved / corrected with the corrected fields / rejected, optional note) and the `assertion.triage_reviewed` event; inventory **51 tables, 176 policies, 159 foreign keys**, migrations **49**, head `20261006180200`. API `v2/triage_review.py`: `GET /v2/workspace/triage-readings` (any operator; only readings the model was asked for) and `POST /v2/commands/review-triage` (sales/admin, receipt + event; moves no case). Proxy: one exact GET and one exact POST. Dashboard: Revisión → «Correos (sugerencias)» — «Aprobar» also walks the thread's one case to the suggested stage through `advance-case-stage` (`moveCase`), «Corregir» with a stage likewise, «Rechazar» with a note. Evidence, 2026-10-06, locally with Docker: `supabase test db --local` **1217 assertions, 30 files, PASS** (078: 36), `verify_direct_logins.sh`, `replay_evidence.sh`, `evidence_tool_failure_tests.sh`, `audit_failure_tests.sh` (80/80), `cleanroom_verify_tests.sh --chain`, `db lint` (incl. `procrastinate`) and `db advisors --fail-on warn` all pass; worker on a disposable cluster **438 passed** (1 pre-existing skip, no DSN skip); API on a disposable cluster **4357 passed**, 121 skipped (V1 Alembic and TLS, no V2 DSN skip); proxy 427; dashboard 634 + build |
| Not done | not applied to any database; not deployed. To ship: merge → apply the three migrations to hosted (§4) → create the worker from `render.yaml` with the cron's database and Storage values → `ORIGENLAB_WORKER_TRIAGE_ENABLED=true` (rules only) → the Anthropic key and `ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED=true`. Runbook: [`OPERATIONS.md`](OPERATIONS.md) §8.11. Dashboard review: see «Review» above |

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
| Render | Web service **`origenlab`** (the real name; `render.yaml` calls it `origenlab-api`), **Python runtime with root dir `apps/api`, not the Docker runtime `render.yaml` declares**, Starter plan, Oregon, 1 GB disk at `/var/data`, health-check path empty, **auto-deploys every `main` push** (live on `cb60c20d` since 2026-10-02 20:58Z). `origenlab-dashboard` (static, auto-deploys `main`). Managed Postgres `origenlab-dashboard-prod` (**PostgreSQL 18**, basic-256mb, Oregon). `api.origenlab.cl` is a **verified custom domain** of the web service. **No Render cron jobs.** Read 2026-10-02 over the Render API |
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
  full pytest suite. Use `scripts/check-all.sh` for anything touching pipeline
  logic.

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

