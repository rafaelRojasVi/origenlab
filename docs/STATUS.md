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

Last verified: **2026-09-20**, against `origin/main` @ `239c48d9`, measured from a clean local
PostgreSQL 17 carrying the Slice 0 migrations. §2.5's hosted facts are from authenticated
control-plane reads only — **no PostgreSQL session against the hosted project has succeeded from
this repository's tooling**, so nothing below claims to have measured its database through the
Slice 0 audit. §2.5 does now record a database connection made to it by `supabase db push` on
2026-09-08, and withdraws the earlier claim that none had ever been made.

## 1. Eras

| Era | State |
|---|---|
| **V1** | **Running and authoritative** for every fact it owns today, including all outbound safety |
| **V2** | Architecture **accepted 2026-09-05**. **Schema foundation only.** One hosted project exists and carries 15 of this repository's migrations from the 2026-09-08 push recorded in §2.5; it is unadopted, unaudited and unread. No application code, nothing deployed |

## 2. V2 — slice status

Slices and their gates are defined in [`MIGRATION.md`](MIGRATION.md) §5.

| Slice | State | Note |
|---|---|---|
| 0 — local foundation | **DONE** | `supabase/roles.sql` + 19 migrations → 4 roles, 7 schemas, 33 tables, grants, RLS. Proven by `supabase/tests/` and `supabase/scripts/`, enforced by `.github/workflows/supabase.yml` on every push touching `supabase/**` |
| 0 — hosted gates | **BLOCKED — no hosted PostgreSQL audit has yet succeeded** | **No hosted Supabase project has been adopted, and no PostgreSQL session against one has succeeded from this repository's audit tooling.** A database connection *was* made to one — `supabase db push` on 2026-09-08, §2.5 — so any stronger reading of this row is withdrawn. All 11 checks in [`MIGRATION.md`](MIGRATION.md) §5.2 remain unproven against a hosted project; checks 1–9 are proven **locally only**, checks 10–11 have never run. The tooling exists — `supabase/scripts/slice0_audit.sh` ([`OPERATIONS.md`](OPERATIONS.md) §4.2) now carries **two** reviewed hosted routes, the direct one and the Supavisor session-mode one for a project whose IPv6-only direct endpoint is unreachable, and `supabase/scripts/hosted_role_bootstrap.sh` ([`OPERATIONS.md`](OPERATIONS.md) §4.3) can produce the role bootstrap such a project would need first — §2.3, §2.4. Neither has been run against a project. See §2.5 for the one hosted project that exists |
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
| Migrations | 19, under `supabase/migrations/` — the 18 Slice 0 files plus the 2026-09-20 corrective that added the Wave 1B `contact_control.source` labels and made `campaign.recontact_interval_days` optional for an archived campaign ([`DATA.md`](DATA.md) §7.6.4) |
| Schemas | 7 — `crm`, `comms`, `outbound`, `evidence`, `catalog`, `procurement`, `platform` |
| Tables | **33** — `crm` 16, `comms` 4, `outbound` 6, `evidence` 2, `catalog` 2, `procurement` 1, `platform` 2 |
| Roles | 4 — `origenlab_owner` (NOLOGIN), `origenlab_migrator`, `origenlab_api`, `origenlab_worker`; all `NOBYPASSRLS` |
| RLS policies | 127 |
| pgTAP assertions | **402 across 11 files** — 399 as before, plus 3 in `100_hosted_role_bootstrap.sql` pinning the grantor asymmetry the hosted convergence depends on (§2.4) |
| Foreign keys | 102, **all** index-covered — 81 unconditional, 21 implied-partial |
| `SECURITY DEFINER` functions | **zero** — the closed list of eight arrives in slices 3 and 5 |
| Data API (PostgREST) | **off**; the seven schemas are not exposed |
| Send flags | `outbound.send_control` single row, **both `false`** |
| Rows of business data | **zero** — in every real database. The §2.7 importer has loaded the full Wave 1A/Wave 1B safety baseline and all three historical campaigns (**28,666** rows, §2.7) into a *disposable local* database only, which is destroyed after the run |

### 2.2 What does not exist yet in V2

- **`apps/worker`** — named in [`ARCHITECTURE.md`](ARCHITECTURE.md) §1–§2 as the owner of
  Gmail sync, MIME parsing, PDF rendering, ChileCompra fetching and the single
  send path. Not created.
- **Any application code touching the seven schemas.** Slice 0 has **zero
  consumers**; nothing in `apps/` references it.
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
| Unit tests | **304** — `python3 -m unittest discover -s supabase/audit/tests -t supabase/audit` (230 audit + 74 hosted bootstrap, §2.4); 56 of them were added with the Supavisor route and 19 with the psql-invocation boundary (`tests/test_psql_invocation.py`) |
| Failure-injection checks | **80** — `supabase/scripts/audit_failure_tests.sh`, including scenario M (the Supavisor route's trust model, end to end, with no database) and scenario N (a planted `~/.psqlrc` and a hostile libpq environment against the local database) |
| Local verdict | `LOCAL_PASS` — 13 required proofs satisfied, `a13` corroborated, `a14` recorded |
| Hosted runs performed | **zero.** No hosted run has ever completed, on either route, and no PostgreSQL audit of a hosted project has succeeded. This is a statement about the audit tool only — a database connection to the hosted project was made on 2026-09-08 by `supabase db push`, §2.5 |
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
| Unit tests | **304** total — `python3 -m unittest discover -s supabase/audit/tests -t supabase/audit`; **74** of them cover the bootstrap |
| Failure-injection checks | **91** — `supabase/scripts/hosted_bootstrap_failure_tests.sh`, no database required |
| Execution rehearsal | **12 checks** — `supabase/scripts/hosted_bootstrap_rehearsal.sh`, local disposable database only, always-rolled-back transactions |
| pgTAP | **22** assertions in `supabase/tests/100_hosted_role_bootstrap.sql` |
| Applications performed | **`supabase/hosted_roles.sql` itself: zero.** It has never been applied to any project, hosted or otherwise, outside the rolled-back local rehearsal. A prior "no hosted role DDL has ever run" reading of this row is **wrong** — the *local* `supabase/roles.sql` reached the hosted project on 2026-09-08 and the hosted catalogue carries its shape. See the correction below and §2.5 |
| Passwords assigned | **zero.** The file cannot express one; the migrator credential is a separate operator action with a hidden secret input, **not taken** — `origenlab_migrator` still has no password verifier |
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

### 2.5 The `origenlab-v2` hosted project — reached once, provenance established

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

**This document does not claim `supabase/hosted_roles.sql` was ever applied.** It was not: the
catalogue shape is the *local* file's, not the hosted file's.

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

- it is **restored and has reached `ACTIVE_HEALTHY`**, observed through an authenticated
  control-plane read. It is not paused and not missing;
- **control-plane evidence shows 15 of this repository's migrations already applied to it.**
  The three 2026-09-08 `outbound` migrations are **absent**. The repository currently carries
  19 (§2.1), so at least four are not there. Their **provenance is the 2026-09-08 push above**,
  which supersedes the earlier "provenance unknown" reading of this bullet. They remain *not*
  evidence that the hosted schema matches today's `supabase/migrations/`;
- **the four OrigenLab roles are present**, with login flags matching the intended matrix, and
  **none of them carries a password verifier** — so `origenlab_migrator` cannot log in and the
  hosted audit identity still has no credential. That has deliberately not been changed;
- `origenlab_migrator` holds `origenlab_owner` SET-only; the two runtime roles hold no
  membership; `postgres` holds the creator-ADMIN row on all four, which is expected
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4). **`postgres` additionally holds an explicit SET
  relationship on `origenlab_owner`** — the local file's shape. Until the §2.4 convergence
  ships, `supabase/hosted_roles.sql` would raise its own platform-boundary assertion and refuse
  itself on this project;
- **no PostgreSQL audit and no row-count inspection has succeeded against it.** The Slice 0
  audit has never completed a run on it, no check in [`MIGRATION.md`](MIGRATION.md) §5.2 has
  been proven on it, and no table in it has been counted. **It must not be described as
  empty** — 15 repository migrations were applied to it, so its seven schemas and their tables
  exist there. **Nothing has read them**, and no row of V1 data has ever been loaded (§2.6);
- it is on the **Free plan**, with **no backup-retention entitlement**. The Pro-plan staging
  posture in §2.4 is a *decision about a project that would be provisioned*
  ([`OPERATIONS.md`](OPERATIONS.md) §4.3), not a description of this one;
- **PostgREST is running**, exposing `public` and `graphql_public`. **None of the seven
  OrigenLab private schemas is exposed through it** — `crm`, `comms`, `outbound`,
  `evidence`, `catalog`, `procurement` and `platform` are all absent from the Data API's
  exposed set. "PostgREST is running" and "the OrigenLab schemas are reachable over HTTP"
  are different claims and only the first is true;
- it remains **unreconciled, unadopted and unapproved**. **Nothing in this repository is
  pointed at it** — no `supabase/.temp/project-ref` exists in any worktree today, and no
  committed file names it. Adopting it is a separate decision that has not been taken, and it
  is now a *reconciliation* rather than an adoption.

**The complete Slice 0 hosted gate is blocked**, and what blocks it is now a specific,
named thing: the audit has no reviewed route that reaches this project with a credential. Its
direct `db.<project-ref>.supabase.co` endpoint is IPv6-only without the IPv4 add-on. The
reviewed Supavisor session-mode route that answers this is implemented and tested in this
repository (§2.3) and **has never completed a run against a project**. Until it does, the 11
checks of [`MIGRATION.md`](MIGRATION.md) §5.2 stay unproven here — checks 1–9 proven **locally
only**, checks 10–11 never run.

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
- **Nothing has been loaded into V2.** `crm.*`, `outbound.*` and every other V2
  schema still hold **zero** rows of business data (§2.1). There is no loader.
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
| Applied to a real database | **zero times.** The only writes were into a disposable local PostgreSQL 17 carrying the Slice 0 migrations |
| Rows loaded | **28,666** on that disposable database — the full cross-wave safety baseline, the evidence trail and all three campaigns' audience and ledger ([`DATA.md`](DATA.md) §7.6.5) |
| Wave 1A load gate ([`DATA.md`](DATA.md) §7.3) | **green** inside that total — 8,580 `prior_contact` all `marketing`, 704 address blocks, 91 domain blocks, zero `purpose=all` outreach facts, zero cooldown rows |
| Idempotency | proven on the real artifacts — a second apply inserted **0** rows, with `crm.*` still 0 |
| Blocked by schema decisions | **none.** Both gaps the first dry run found were decided and closed on 2026-09-20 — [`DATA.md`](DATA.md) §7.6.4 |
| Rejects | **0** over the real artifacts; every input row mapped |
| Tests | **136** — `uv run pytest tests/test_v2_import.py`. 15 are database-backed and skip unless `ORIGENLAB_V2_TEST_DSN` names a disposable local Slice 0 database |
| Network calls | **zero.** No Gmail, Supabase, Render or Cloudflare client is imported; both send flags are untouched |

**Nothing reads these rows yet.** The importer has no consumer, no dashboard
surface and no operator command; promotion from `evidence.assertion` to `crm.*`
remains slice 2, and the send predicate that would read
`outbound.contact_control` remains slice 5.

**No real, staging or hosted V2 database has been loaded** — the only target
ever opened is the disposable local PostgreSQL 17 above (§2.5 for why no hosted
project is adopted).

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
