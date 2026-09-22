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

Last verified: **2026-09-21**, against `origin/main` @ `a3961aa4` plus this branch, measured from the
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
| 0 — local foundation | **DONE** | `supabase/roles.sql` + 19 migrations → 4 roles, 7 schemas, 33 tables, grants, RLS. Proven by `supabase/tests/` and `supabase/scripts/`, enforced by `.github/workflows/supabase.yml` on every push touching `supabase/**` |
| 0 — hosted gates | **BLOCKED — on attestation items only; the audit itself has now succeeded** | **The hosted Slice 0 audit completed against `origenlab-v2` on 2026-09-21** over the Supavisor session-mode route, and every SQL proof passed. `supabase/hosted_roles.sql` was applied under its atomic contract the same day and the four absent migrations were reconciled, so the project now carries all 19 (§2.5). Checks 1–9 of [`MIGRATION.md`](MIGRATION.md) §5.2 are therefore proven **against the hosted project**, not merely locally. **Checks 10–11 remain unproven**, and with them the eight attestation items `t01`–`t07` and `d01`: the project is on the Free plan with no backup entitlement, so no restore drill can be evidenced, and its Data API is running rather than off. Those are plan-and-configuration decisions for the owner. The project is **reconciled but still not adopted** — no committed file names it and no application code reads it. See §2.5 |
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
| Migrations | **20**, under `supabase/migrations/` — the 18 Slice 0 files, the 2026-09-20 corrective that added the Wave 1B `contact_control.source` labels and made `campaign.recontact_interval_days` optional for an archived campaign ([`DATA.md`](DATA.md) §7.6.4), and the 2026-09-21 slice 2 additive that opened the `gmail_message` / `drive_file` provenance kinds and the `document_reference` assertion kind (§2.7.6) |
| Schemas | 7 — `crm`, `comms`, `outbound`, `evidence`, `catalog`, `procurement`, `platform` |
| Tables | **33** — `crm` 16, `comms` 4, `outbound` 6, `evidence` 2, `catalog` 2, `procurement` 1, `platform` 2 |
| Roles | 4 — `origenlab_owner` (NOLOGIN), `origenlab_migrator`, `origenlab_api`, `origenlab_worker`; all `NOBYPASSRLS` |
| RLS policies | 127 |
| pgTAP assertions | **408 across 11 files** — 402 as before, plus 6 in `061_constraints_comms_outbound_evidence.sql` pinning the staged Gmail/Drive vocabulary and its `pending` / `unresolved` defaults (§2.7.6) |
| Foreign keys | 102, **all** index-covered — 81 unconditional, 21 implied-partial |
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
| Database | `origenlab_dev`; chain applied **20 of 20**, head `20260921120000` |
| Structure | 7 schemas, 33 tables, 127 policies, 102 index-covered foreign keys, zero `SECURITY DEFINER` — identical to the CLI cluster's |
| Rows of business data | **not zero — local only.** Measured 2026-09-21: `crm.*` **33,816** (`contact_point` 9,460 · `organization` 1,812 · `domain_event` 22,544; `person`, `opportunity`, `quote`, `task`, `affiliation` all **zero**), `outbound.*` **17,214** (`contact_control` 10,588 · `campaign_recipient` 3,481 · `send_attempt` 3,141 · `campaign` 3 · `send_control` 1), `evidence.*` **11,498** (`assertion` 11,474 · `source_record` 24), `comms.mailbox` 1, `platform.operator` 1, `catalog.*` and `procurement.*` zero |
| Of which staged from Gmail | **20 `evidence.source_record`** of kind `gmail_message`, all `review_status = 'pending'`, and their **26 `evidence.assertion`**, all `resolution = 'unresolved'` (20 `contact_address`, 6 `organization_name`). Staged 2026-09-21 from a local manifest by `stage_gmail_drive_evidence.py`; `crm.*` was counted before and after inside the staging transaction and did not change (§2.7.7) |
| Where these rows came from | the §2.7 historical importer and the §2.7.7 staging pass, both run against **this loopback database only**. No hosted project holds any of it |
| Send flags | one `outbound.send_control` row, **both `false`** — unchanged by every load above |
| Checkpoints | `~/data/origenlab-v2-local/checkpoints/`, outside Git, `0700`/`0600`, each with `.sha256` and `.meta.json` |
| Build template | `origenlab_template` in the **CLI** cluster; disposable `origenlab_test_<8 hex>` databases are cloned from it |

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
- **the schema matches this repository's head**: 33 tables, 127 RLS policies, **no table
  without RLS**, **zero `SECURITY DEFINER` functions**, and every foreign key index-covered;
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
| Routes | **11** `GET` — the seven listings (`/v2/contacts`, `/v2/organizations`, `/v2/prospects`, `/v2/opportunities/active`, `/v2/tasks/due`, `/v2/review/summary`, `/v2/quotes/followup`), `/v2/evidence`, the two card reads `/v2/contacts/{id}` and `/v2/organizations/{id}` (§2.7.6), and `/v2/evidence/records` (§2.7.7) |
| Write routes | **zero**, asserted by a test over the router's own methods |
| Mounted when | **only** if `ORIGENLAB_V2_DATABASE_URL` is set. Unset, the router is absent entirely and `/v2/*` is a 404 |
| Connection role | `origenlab_api` — no membership in `origenlab_owner`, so RLS constrains these reads as it will in production |
| Transaction | `set transaction read only` then `set local statement_timeout`, both inside the transaction. A write returns **SQLSTATE 25006**, proven against a real database |
| Identity | one port, two adapters — a JWKS verifier (the target; **present and unconfigured**) and a local development adapter that **refuses to construct** unless the V2 DSN is a literal loopback address. JWKS wins whenever configured |
| Authorization | every route requires a resolved, **active** `platform.operator` with role `viewer`, `sales` or `admin`; no identity is 401 |
| Paging | every listing bounded — default 50, maximum 200. Every child list on a card is bounded too, at 100, and the card reports the true count beside each capped list |
| Tests | **51** — `apps/api/tests/test_v2_read_boundary.py`; 12 are database-backed and skip unless `ORIGENLAB_V2_TEST_DSN` is set |
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
| Input | a local JSON manifest (`manifest_version: 1`) an operator produced out-of-band. **No Google client is imported anywhere in the package** and a test asserts it, so everything that can reach the database is a file a human can read, diff and refuse first |
| Network calls | **zero.** No Gmail, Drive, Supabase, Render or Cloudflare client; both send flags untouched |
| Target boundary | the Wave 1A/1B importer's own loopback guard, unchanged and unweakened. No override flag exists and a test asserts none does |
| Tables written | 2 — `evidence.source_record` (`review_status = 'pending'`) and `evidence.assertion` (`resolution = 'unresolved'`). **Nothing else** |
| `crm.*` and `outbound.*` | **never written.** The `crm` row count is taken before and after inside the staging transaction and any change rolls the whole pass back |
| What a manifest may assert | a `gmail` record: `contact_address`, `organization_name`. A `drive` record: `document_reference`, `organization_name`. `contacted_address` and `affiliation` are refused from both — the first is a claim about our own outbound history that only the send ledger may make, the second a relationship no message header records |
| Resolution vocabulary | **absent from the manifest by design.** There is no way to express "confirmed", "this is person X" or "merge these". Staging proposes; `promote_evidence_into_crm.py` decides |
| Reporting | counts and kinds only — a test asserts no address, document name or organization name reaches the report, which is what gets pasted into commits and CI logs |
| Idempotency | proven against a real database — a second apply created **0** records and **0** assertions; a record re-staged with a different `source_uri` is refused rather than overwritten |
| Rows staged into any database | **20 source records and 26 assertions**, into the local `origenlab_dev` only, on 2026-09-21 (§2.7.7). Before that run the tool had been exercised only by its own tests |
| Tests | **34** — `uv run pytest tests/test_v2_evidence_stage.py`; 2 are database-backed and skip unless `ORIGENLAB_V2_TEST_DSN` is set |

**No Gmail credential exists in this repository**, and the acquisition step — whatever
produces a manifest — is deliberately outside this tool. A first manifest has since been
produced from a real mailbox by hand and staged; §2.7.7 records it. No Drive manifest
exists.

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
| Tests | 13 in `EvidenceReviewPage.test.tsx`, 19 in `evidenceReview.test.ts`, 9 added to `test_v2_read_boundary.py`, 1 added to the nav suite, and the proxy allowlist suite extended |

**What is still unconfirmed after all of this.** All 20 records are `pending` and all 26
assertions `unresolved`. No person name was staged at all — the manifest format has no kind
for one. 16 of 20 records carry no organization claim. `crm.organization_domain` is empty, so
every domain-to-institution guess in the queue is unevidenced. Promotion remains the job of
`promote_evidence_into_crm.py` and, eventually, of a V2 command boundary that does not exist.

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
