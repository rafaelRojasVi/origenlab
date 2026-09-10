# OrigenLab V2 — operations

**Purpose.** How to run V2 safely: deploy it, migrate it, send with it, and
recover it.

**This document owns:** environment separation; operator roles; the deployment
process; migration execution; send-control operations; the campaign activation
and quotation send checklists; the ambiguous-attempt procedure; Gmail sync
recovery; backup and restore drills; monitoring and alerts; emergency
shutdown; rollback execution; and credential handling.

**It does not own:** why the states exist ([`WORKFLOWS.md`](WORKFLOWS.md)),
what the roles may write ([`ARCHITECTURE.md`](ARCHITECTURE.md)), the cutover
plan ([`MIGRATION.md`](MIGRATION.md)).

> **Every command block below marked `EXAMPLE — NOT YET IMPLEMENTED` describes
> a procedure that does not exist yet.** The commands show the intended shape of
> each procedure so it can be built and reviewed; none of them can be run today,
> and none should be presented to an operator as available. The exceptions are
> §4.1, the local schema foundation, and §4.2, the Slice 0 audit — both exist and
> run today.

## 1. Environments

**[V2 DECISION]**, **[PLANNED]**

| Environment | Database | Storage | Gmail | Send flags |
|---|---|---|---|---|
| `local` | a developer's own project or container | a developer's own bucket | **none** — the Gmail client is not constructible | both hard-false; the send path is not wired |
| `staging` | a separate Supabase project | separate private buckets | a **non-production** mailbox | both false; may be enabled only against the non-production mailbox |
| `production` | the OrigenLab project | the production private buckets | the single production sender | both false by default; changed only by an admin command |

Rules:

- **Separate Supabase projects. Never separate schemas in one project.**
- **No environment ever points at another environment's database, bucket or
  mailbox.** A staging deployment that can reach the production mailbox is an
  incident, not a configuration choice.
- Production data is never copied into staging. Staging is seeded
  synthetically.
- OrigenLab shares no environment, credential or mailbox with any other
  business ([`README.md`](README.md)).

## 2. Operator roles

| Role | May |
|---|---|
| `viewer` | read everything the dashboard exposes |
| `sales` | run every CRM command: create and advance opportunities, edit and submit quote revisions, create tasks and activities, promote evidence, freeze a campaign audience |
| `admin` | everything `sales` may, plus: change send control, approve quote revisions and campaigns, grant recontact overrides, revoke blocks, resolve ambiguous attempts, authorize retries, merge identities, and manage operators |

- `status ∈ {active, disabled}`. A disabled operator is refused at the command
  boundary even with a valid, unexpired token.
- **Admin commands require `aal2`** — a second factor in the current session.
- **[OPEN]** whether the approver of a quote revision must differ from its
  author. Recommended default: required once two `sales` operators exist.

## 3. Deployment

**Order matters. Migrations first, then the worker, then the API, then the
dashboard.**

```bash
# EXAMPLE — NOT YET IMPLEMENTED
ol deploy plan --env production        # show the migration and image diff
ol migrate up --env production         # origenlab_migrator, SET ROLE owner
ol deploy worker --env production      # drain the queue, then replace
ol deploy api --env production
ol deploy dashboard --env production
ol verify --env production             # health, JWKS, queue depth, send flags
```

- **Send flags are never changed by a deployment.** A deploy that would alter
  `outbound.send_control` is a bug.
- The worker is drained before replacement so no attempt is left `dispatching`
  by a restart. An attempt caught mid-flight becomes `ambiguous` and is handled
  by §7 — it is never silently retried.
- Roll forward, never roll back a migration. A mistake is corrected by a new
  migration.

## 4. Migrations

- **Application objects are owned by `origenlab_owner`, a `NOLOGIN` role.**
  Migrations connect as `origenlab_migrator` and run DDL under an explicit
  `SET ROLE origenlab_owner`. No runtime role creates, alters or drops
  anything, and no runtime role may inherit or assume the owner
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6).
- Shipped migrations are never rewritten; corrections are new migrations.
- A downgrade that would drop human data fails closed.
- A new table is **deny-by-default**: it has RLS enabled and no policy for any
  runtime role until one is added deliberately
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6).
- **A `permission denied` or an unexpectedly empty result is fixed by the
  missing grant or the missing RLS policy — never by widening a role and never
  by wrapping the statement in a `SECURITY DEFINER` function.** The definer
  list is closed ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2); adding to it is
  an architecture decision with its own review, not a migration detail.
- After every migration: run the database linter **and the Supabase security
  advisors**, confirm the Data API is still off, confirm **no OrigenLab-created
  role gained `BYPASSRLS`** and no runtime role can assume `origenlab_owner`,
  confirm every `SECURITY DEFINER` function still matches the closed list —
  right owner, pinned `search_path`, a `session_user` assertion rather than a
  `current_user` one, `EXECUTE` revoked from `PUBLIC`, `anon`, `authenticated`
  and `service_role` — confirm the schema, object and default privileges for
  those four are still revoked, and confirm both send flags are unchanged.
- **`service_role` is expected to carry `BYPASSRLS`.** It is Supabase-managed,
  its attribute is never altered here, and the audit must not treat it as an
  OrigenLab role or report it as drift. What the audit does check is the
  boundary that actually contains it: revoked grants, revoked `EXECUTE`,
  matching default privileges, and unexposed schemas
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4). Revocation is a **necessary
  independent boundary**, not a reason to call the role safe.

```bash
# EXAMPLE — NOT YET IMPLEMENTED
ol migrate status --env production
ol migrate up --env production
ol audit roles --env production        # fails if any OrigenLab-created role has
                                       # BYPASSRLS, or a runtime role can assume
                                       # origenlab_owner. Supabase-managed
                                       # service_role keeps its platform
                                       # BYPASSRLS and is reported, not failed
ol audit grants --env production       # fails if PUBLIC/anon/authenticated/
                                       # service_role hold schema USAGE, an
                                       # object privilege or EXECUTE, or if the
                                       # owner's default privileges no longer
                                       # match
ol audit exposure --env production     # fails if a private schema is exposed
ol audit definers --env production     # fails on any SECURITY DEFINER function
                                       # off the closed list, wrongly owned, with
                                       # an unpinned search_path, asserting
                                       # current_user rather than session_user,
                                       # or executable by PUBLIC/anon/
                                       # authenticated/service_role
```

**(impl)** The four `ol audit` subcommands above are still the intended shape of the
per-migration audit and are not built. What *is* built is the slice 0 audit,
[§4.2](#m-ops-slice0-audit) — one read-only tool that discharges the catalogue half of
those four (roles, grants, exposure and definers) against a live local or hosted database,
and reports what it cannot answer. `ol migrate` remains unimplemented.

### 4.1 Local foundation (implemented — Migration Slice 0, local portion)

The reproducible local foundation lives under `supabase/`: `config.toml` (PostgreSQL 17,
database only, Data API off), `roles.sql` (the idempotent cluster-role bootstrap the CLI runs
before migrations), `migrations/` (eighteen ordered migrations: schemas and default
privileges, then the original 32 tables schema by schema, then grants, then RLS policies, then the
revocation of the owner's database-level `CREATE`, then the covering indexes for every
foreign key, then the outbound corrections — frozen campaign content and audience criteria,
the reply table, the tightened recipient address shape), `tests/` (pgTAP, 372 assertions
across ten files) and `scripts/`. Requirements:
Docker, the Supabase CLI and `psql`. No hosted project is involved and nothing here holds a
credential: the three `LOGIN` roles are created without a password.

```bash
supabase start                            # PostgreSQL 17 container; runs roles.sql, then migrations
supabase db reset --local                 # replay roles.sql + migrations from scratch
supabase test db --local                  # pgTAP: inventory, roles, predefined-role boundary, grant
                                          # boundary, matrix, RLS, constraints, SECURITY DEFINER
                                          # semantics, no leftovers, foreign-key index coverage
supabase/scripts/verify_direct_logins.sh  # MIGRATION.md §5.2 checks 6-9 with real LOGIN connections;
                                          # throw-away passwords, cleared fail-closed on exit
supabase/scripts/replay_evidence.sh       # two resets; every CLI exit status enforced, every dump and
                                          # catalogue asserted complete, then compared byte for byte
supabase/scripts/evidence_tool_failure_tests.sh
                                          # failure injection: proves the two scripts above actually
                                          # fail on a failed reset, a failed or empty dump, a failed
                                          # local-status discovery and unequal migration lists
supabase db lint --local -s crm,comms,outbound,evidence,catalog,procurement,platform \
  --level warning --fail-on warning
supabase db advisors --local --type all --level info --fail-on warn
supabase stop                             # keeps the data volume; never `--no-backup`
```

**Every script that opens a `psql` connection resolves its target through
`supabase/scripts/lib/local_target.sh` and nothing else.** Four independent facts must agree
before a connection is opened:

1. `supabase/config.toml` names this project (`origenlab`) and this database port (54322);
2. **`linked_project` is null** — no `supabase/.temp/project-ref` exists and `config.toml`
   declares no hosted `project_ref`. Absence of the link is the proof; the guard never reaches
   the network to ask;
3. the running `supabase_db_origenlab` container carries `com.supabase.cli.workdir` equal to
   **this working tree**. Two checkouts of this monorepo share the project id, so the id alone
   cannot tell them apart — a stack started from another worktree is refused, not reused;
4. the URL `supabase status` reports parses, is a **loopback** host, and is on that same port.

The guard also unsets the inherited `PG*` libpq environment and the `SUPABASE_*` family
(`SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_URL`, `SUPABASE_PROJECT_REF`, …), so neither `PGHOST`
nor a hosted access token can redirect a command or serve as a fallback; the names that were
set are reported, never their values. If any of the four fails it exits non-zero **before** a
connection is attempted. `evidence_tool_failure_tests.sh` proves it: with `supabase status`
made to fail and hostile `PG*` variables set (D), with a planted `project-ref` (F), and with
`docker` reporting a foreign working tree (G), the scripts refuse and `psql` is never invoked.

**Stop the stack when the evidence run is finished.** `supabase stop` keeps the data volume
and releases the published ports (54322 for PostgreSQL, 54321 for Kong), which are bound on
all interfaces while the stack runs. **Never `supabase stop --no-backup`** — it deletes the
data volumes.

Rules that hold for every later migration:

- Create the file with `supabase migration new <descriptive_name>`; never write a timestamp
  by hand, never edit a migration that has been applied anywhere but locally.
- Begin with `set role origenlab_owner;` and end with `reset role;`, so every object is
  owned by the owner and the CLI can still record the migration as itself. The exception is
  a migration whose whole purpose is something the owner cannot do to itself — M14, which
  revokes the owner's database-level `CREATE`, runs as the connection role throughout and
  says so in its header.
- A new table is created with `enable row level security` in the same migration and gets
  its grants and named policies in explicit statements; the pgTAP matrix in
  `supabase/tests/040_grant_matrix.sql` and `050_rls.sql` must be extended in the same
  change, or the suite fails.
- A new function is `SECURITY INVOKER` unless it is on the closed list
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2), pins `search_path = pg_catalog`, and has
  `EXECUTE` revoked from `PUBLIC`, `anon`, `authenticated` and `service_role` explicitly,
  even though the owner's default privileges already do so.
- A new schema is exceptional. `origenlab_owner` holds no `CREATE` on the database between
  migrations ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4 point 6); a migration that adds one
  re-grants the privilege in its first statement and revokes it in its last, in the same
  reviewed change. Leaving it granted fails `supabase/tests/030_grant_boundary.sql`.
- Never alter, revoke or grant a Supabase-managed platform role or a PostgreSQL predefined
  role ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.5). Their memberships are asserted, not
  managed; a new one appearing in the catalogue fails `supabase/tests/020_roles.sql` and is a
  question for the provider, not something to repair by migration.

<a id="m-ops-slice0-audit"></a>
### 4.2 The Slice 0 audit (implemented — read-only, local and hosted)

`supabase/scripts/slice0_audit.sh` re-runs the catalogue half of the
[`MIGRATION.md`](MIGRATION.md) §5.2 obligations against a live database and writes a
sanitised report. It is the tool the **hosted provisioning gate** needs: §4.1 proves the
foundation locally, and §5.2 requires the same checks against the hosted project before
slice 1.

```bash
supabase/scripts/slice0_audit.sh --mode local
supabase/scripts/slice0_audit.sh --mode hosted --simulate
supabase/scripts/slice0_audit.sh --mode hosted --authorize-hosted-connection
supabase/scripts/slice0_audit.sh --verify-report supabase/.audit/reports/slice0-audit-local.json
```

**The audit has no write mode.** It issues no `INSERT`, `UPDATE`, `DELETE`, DDL,
sequence change or any other intentional mutation against any database, in either mode.
It cannot regenerate its own baselines, and it has no flag that disables redaction. Its
read-only boundary rests on five things that hold together, not on any one of them:

1. `default_transaction_read_only`, `statement_timeout`,
   `idle_in_transaction_session_timeout` and `lock_timeout` are set through the libpq
   connection options, so they are in force when the first statement is parsed;
2. every check runs inside **one explicit `begin read only` transaction**, which refuses
   writes at the transaction level whatever privileges the session holds;
3. every statement comes from a **fixed, reviewed query file** under `supabase/audit/sql/`
   — there is no operator SQL, no template and no dynamic SQL built at run time;
4. each of those files is **statically rejected** before a connection is opened unless it
   parses to exactly one `select`, `with` or `show` with no forbidden token, no dollar
   quoting and no psql meta-command;
5. `s01` reads `transaction_read_only` and `default_transaction_read_only` **back from the
   server** rather than assuming them.

The negative test — a write attempted inside that transaction and refused with SQLSTATE
`25006` — lives in `supabase/scripts/audit_failure_tests.sh` and runs **only against the
disposable local database, inside a rollback-only harness**. A hosted audit never attempts
a mutation to see what happens.

#### The two target boundaries

**Local mode** resolves its target through
`supabase/scripts/lib/local_target.sh` and nothing else, exactly as every other script in
§4.1 does; `supabase/scripts/lib/resolve_local_target.sh` is a one-way bridge that runs
that guard and forwards the URL it produced. The engine then refuses a non-loopback host
a second time in the process that builds the connection. **Local mode cannot reach a
remote host.**

**Hosted mode** shares no code path with it. It refuses to start unless *all* of these
hold, and it refuses **before** a connection is attempted in every case:

- `--authorize-hosted-connection` was passed. A hosted connection is never a default.
- **The project is not linked.** No `supabase/.temp/project-ref`, no `project_ref` in
  `config.toml`. Hosted mode takes its target only from the reviewed target file, so link
  state is not a shortcut it may take — it is a reason to stop.
- **The entire tracked working tree is clean.** A hosted report names a commit; a dirty
  tree would make that name wrong. Untracked ignored files are permitted only at the three
  approved paths below.
- The target file exists at `supabase/.audit/hosted_target.env`, is a regular file (not a
  symlink) with mode `600`, and declares the supported route: a direct
  `db.<project-ref>.supabase.co` connection on 5432. The Supavisor pooler is out of scope
  — it requires the login name to carry the project reference, which conflicts with the
  pinned audit identity.
- The login role is **`origenlab_migrator`**, the configured hosted audit identity for this
  initial audit. It is not a fifth role and no migration creates one. The credential stays
  outside this repository: the target file names an **environment variable**, and there is
  no key that accepts a password inline.
- `sslmode` is exactly `verify-full` with a CA file that exists. **There is no downgrade
  path**: a missing CA file refuses the run rather than falling back to `require`.
- The host resolves, immediately before the connection, to addresses that are **all**
  globally routable. Loopback, private, link-local, CGNAT, multicast, documentation and
  reserved answers are refused, and one bad answer among good ones refuses the whole
  target. The resolved address is then **pinned**: libpq is given `PGHOSTADDR` alongside
  `PGHOST`, so it does not resolve the name a second time and `verify-full` still checks
  the certificate against the name. That is what closes the time-of-check/time-of-use
  window.

The child environment is **built, not inherited** — `PATH`, `HOME`, `LANG` and the libpq
variables the target needs, and nothing else. An inherited `PGHOST`, `PGSERVICE`,
`SUPABASE_DB_URL` or `SUPABASE_ACCESS_TOKEN` cannot redirect or re-authenticate the
connection because it is not in the child's environment at all. The password is passed as
`PGPASSWORD` in that environment and never appears in an argument vector; the process
table shows no target.

#### Three approved local paths, all git-ignored

| Path | What |
|---|---|
| `supabase/.audit/hosted_target.env` | the hosted target, mode `600`. Names the project and the credential's environment variable; **never a credential** |
| `supabase/.audit/attestation.json` | the operator attestation. Template: `supabase/audit/fixtures/attestation.example.json` |
| `supabase/.audit/reports/` | the generated reports |

Nothing else may exist under `supabase/.audit/`; a hosted run refuses if anything does.
`scripts/security/check-public-repo-hygiene.sh` fails if any of them is ever tracked, and
if a hosted project reference appears in tracked content.

#### What the audit proves, corroborates, attests and records

Fifteen SQL checks and nine engine checks, each naming the obligation it discharges. The
four kinds are not interchangeable:

| Kind | Meaning |
|---|---|
| **proof** | the catalogue answers the question completely; a violation is a finding |
| **corroborate** | the catalogue supports an answer it cannot settle; never a pass on its own |
| **attest** | only an operator can answer it; recorded with its evidence, never called proven |
| **record** | reported, never failed on — the provider's own catalogue, which this design neither confers nor may revoke ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.5) |

| Check | Obligation | Kind |
|---|---|---|
| `s01` | the audit's own session is read-only, PostgreSQL 17, the expected identity pair | proof |
| `a01` | §5.2 check 1 — every OrigenLab-created role is `NOBYPASSRLS`; the owner's membership graph | proof |
| `a02` | §5.2 check 2 and §6.5 — no OrigenLab or Data-API-facing role is inside `pg_read_all_data`/`pg_write_all_data`; `service_role`'s platform `BYPASSRLS` and the hosted platform catalogue are **recorded** | proof + record |
| `a03` | §5.2 check 3 — no schema `USAGE`/`CREATE` for `PUBLIC`, `anon`, `authenticated`, `service_role`, `authenticator`, read both from the ACL and through `has_schema_privilege` | proof |
| `a04` | §5.2 check 3 — no table, sequence or **column** privilege for those roles | proof |
| `a05` | §5.2 checks 4 and 9 — no `EXECUTE` for those roles; the `SECURITY DEFINER` inventory against the closed list | proof |
| `a06` | §5.2 check 5 — the owner's default privileges, compared as an exact set | proof |
| `a07` | §6.4 point 6 — `origenlab_owner` holds no `CREATE` on the database | proof |
| `a08` | seven schemas, thirty-three tables, owned by the owner, RLS enabled — compared name by name | proof |
| `a09` | the 127 RLS policies, including their predicates | proof |
| `a10` | all 102 foreign keys index-covered | proof |
| `a11` | both send flags false | proof |
| `a12` | zero rows of business data | proof |
| `a13` | the Data API — SQL corroboration only | corroborate |
| `a14` | installed extensions | record |
| `c01` | §5.2 check 10 (partial) — API-key **types**, `--cli-metadata` only | record |
| `t01`–`t07` | Data API toggle, private buckets, no privileged key in any deployment, backups, both restore drills, security advisors | attest |
| `d01` | the Data API is off — `a13` **and** `t01`, never either alone | attest |

Reading `a05` and `a06` correctly matters: a NULL `proacl` grants `EXECUTE` to `PUBLIC`,
and an *absent* default-privilege row is a finding rather than a neutral fact. Both are
expanded through `acldefault()` and compared as exact sets, so a hosted project that never
revoked the owner's function default fails `a06` even though nothing was added.

**A legacy `service_role` key does not fail the audit.** `c01` records which key types
exist and never requests, displays or persists a value — no `--reveal`, no raw output kept.
The gate that matters is that no privileged key is *configured or exposed* in the
applications, Render, Cloudflare, GitHub or a public client, and this audit cannot inspect
those secret stores. That is `t03`, an operator-attested deployment check.

#### The verdict, and what it is worth

| Verdict | Means |
|---|---|
| `PASS` | a **real hosted run that connected**, every required check satisfied. The only verdict with `gate_eligible: true` |
| `INCOMPLETE` / `FAIL` | a real hosted run that was not complete, or found something |
| `LOCAL_PASS`, `LOCAL_FAIL`, `LOCAL_INCOMPLETE` | a local run. §5.2 requires these checks against the **hosted** project; a local verdict is not that |
| `SIMULATED_PASS`, `SIMULATED_FAIL`, `SIMULATED_INCOMPLETE` | a replay of a committed fixture, with `simulated: true` and `hosted_contacted: false`. It contacted nothing and proves nothing about any database |

**A simulated or canned run never emits a bare `PASS`, even when every check holds**, and
neither does a local run. A missing or unevaluable required check makes a run
`INCOMPLETE`; incompleteness is never absorbed into a pass, and the report names what was
missing. `gate_eligible` states the conclusion outright rather than leaving it to be
inferred.

#### Reports

Two artefacts per run under `supabase/.audit/reports/`, JSON and Markdown, built from one
structure so they cannot disagree, with sorted keys and checks in registry order: the same
inputs and the same clock produce identical bytes. Both are redacted and then **re-read by
the leak assertions before anything is written** — a report that cannot be proven free of
credentials, keys, JWTs, connection strings, Supabase host names, project references and
non-loopback addresses is not written at all. A real report carries only the *fingerprint*
of a project reference, never the reference.

`--verify-report` runs those assertions again against a file on disk. CI runs the local
audit, verifies both artefacts that way, and uploads **only** those two sanitised files —
never raw child output, a temporary working directory or a failure capture. If the
verification fails, nothing is uploaded.

#### What this audit does not answer

Named in every report, so silence is never mistaken for coverage:

- **the applied-migration list.** `supabase_migrations.schema_migrations` is
  platform-owned and unreadable by the pinned audit identity, which holds no privilege of
  its own. What the migrations *produced* is compared object by object instead (`a08`,
  `a09`, `a10`) — stronger evidence than a version list;
- **`supabase db lint` and `supabase db advisors` against the hosted project**, which need
  a connection string on the command line or a linked project, and hosted mode permits
  neither. Carried as `t07` until a route exists that needs no link state;
- **buckets, backups and both restore drills** (`t02`, `t04`, `t05`, `t06`);
- **the secret stores of Render, Cloudflare, GitHub and the browser bundles**, which decide
  §5.2 check 10 (`t03`);
- **the behavioural direct-login proofs of §5.2 checks 6–9.** They need real `LOGIN`
  connections as `origenlab_api` and `origenlab_worker` with passwords set for the run;
  `verify_direct_logins.sh` does that against the disposable local database, and this audit
  will not set a password on a hosted role.

#### Tests

```bash
python3 -m unittest discover -s supabase/audit/tests -t supabase/audit
supabase/scripts/audit_failure_tests.sh
```

The failure-injection suite proves the audit fails closed: a check file containing a
mutation is refused with `psql` never invoked; catalogue drift is `LOCAL_FAIL`; a run
missing observations is `INCOMPLETE` and names them; a fully satisfied simulated run is
`SIMULATED_PASS` and never `PASS`; a hostile inherited libpq and Supabase environment does
not move a local run off loopback; hosted mode refuses without authorisation and refuses
link state; every reserved address class is refused; no `sslmode` below `verify-full` is
accepted; a poisoned report fails `--verify-report`; and the read-only transaction really
refuses a write.

<a id="m-ops-hosted-bootstrap"></a>
### 4.3 The hosted role bootstrap (implemented — dry run only, no connection)

`supabase/hosted_roles.sql` is the hosted counterpart of the local `roles.sql` of
[§4.1](#4-migrations): the reviewed statement of the four OrigenLab roles on a hosted
Supabase project. `supabase/scripts/hosted_role_bootstrap.sh` is the tool that proves the
file is what it claims to be and prints it.

```bash
supabase/scripts/hosted_role_bootstrap.sh --environment staging --dry-run
```

**The tool opens no database connection, in any mode.** There is no apply mode, no host
argument, no connection string, no target file and no credential input — and that is not a
gap to be closed later by adding a flag. The reason the bootstrap is safe to review is that
the artefact reviewed and the artefact applied are the same committed bytes; a tool that
could also apply them would need a target, a credential and a connection, and this slice
deliberately holds none of the three. `stdout` is the committed file byte for byte, with no
banner, timestamp or generated comment, so `--dry-run > plan.sql` and a review of
`supabase/hosted_roles.sql` cannot disagree. The plan summary goes to `stderr`.

#### What the bootstrap may touch, and what proves it

The file may create and converge exactly four roles — `origenlab_owner`,
`origenlab_migrator`, `origenlab_api`, `origenlab_worker` — and nothing else.
`supabase/audit/olaudit/bootstrap.py` proves that statically, before the file is printed,
piped or shown, and without any database. It is the write-side counterpart of
`olaudit.sqlbank` and shares no code with it, so a relaxation in either cannot widen the
other. It refuses the whole file on any of:

- **a password.** The token `password` — and `encrypted`, and `valid until` — may not appear
  in the file's code. Assigning a credential is not expressible here at all;
- **a Supabase-managed or PostgreSQL predefined role named by any statement.** `postgres`,
  `service_role`, `anon`, `authenticated`, `supabase_admin`, `pg_read_all_data` and the rest
  can never be created, altered, granted a membership, or granted anything;
- **a fifth role**, or a missing one;
- **a second membership**, or the owner membership carrying anything but
  `INHERIT FALSE, SET TRUE, ADMIN FALSE`;
- **a positive `SUPERUSER`, `BYPASSRLS`, `REPLICATION`, `CREATEDB` or `CREATEROLE`
  attribute**, or a role declared both a way and its negation, which would make the applied
  result depend on statement order;
- **any DDL noun but `role`**, any DML verb, `DROP`, dynamic execution, a psql meta-command
  or named dollar quoting;
- **a statement shape the analyser does not recognise.** Every occurrence of `create`,
  `alter`, `grant` and `revoke` in the file's code must be accounted for by a matched
  statement. This is what makes the analysis complete rather than merely suggestive: a
  statement the parser does not understand refuses the file instead of being skipped.

#### The role and membership matrix

| Role | Login | Inherit | Superuser | BypassRLS | Replication | Membership |
|---|---|---|---|---|---|---|
| `origenlab_owner` | **NOLOGIN** | INHERIT | NO | NO | NO | member of nothing; owns every application object |
| `origenlab_migrator` | LOGIN | **NOINHERIT** | NO | NO | NO | `origenlab_owner`, `SET` only — `INHERIT FALSE, SET TRUE, ADMIN FALSE` |
| `origenlab_api` | LOGIN | INHERIT | NO | NO | NO | **member of no role** |
| `origenlab_worker` | LOGIN | INHERIT | NO | NO | NO | **member of no role** |

No password is assigned to any of them by the bootstrap. `origenlab_api` and
`origenlab_worker` get no direct-login credential in this slice at all.

#### The one deliberate local/hosted divergence

`supabase/roles.sql` additionally grants the CLI's `postgres` login the same SET-only,
non-inheriting membership in `origenlab_owner`, because the Supabase CLI applies local
migrations as `postgres` against a disposable container. **`supabase/hosted_roles.sql`
grants nothing to any platform role.** On a hosted project, migrations connect as
`origenlab_migrator`, which holds that membership itself, so the bootstrap does not depend
on altering the platform `postgres` role and never makes it an owner of anything.

**A creator-admin row is a platform fact, not a grant.** PostgreSQL 16 and later record the
role creator's implicit `ADMIN OPTION` as an ordinary `pg_auth_members` row, so the login that
applies either bootstrap file holds one such row on **all four** roles — `admin_option` true,
`set_option` and `inherit_option` both false. It is administrative only: it confers no
`SET ROLE` and inherits nothing. Neither file grants it and neither could suppress it, so the
hosted file's fail-closed assertion is stated in terms of the two options that actually confer
privilege — **no identity outside the OrigenLab set may `INHERIT` or `SET ROLE` to an OrigenLab
role** — rather than as the false claim that no membership row exists. An assertion written the
other way would have refused itself the first time an operator applied it.

`supabase/tests/100_hosted_role_bootstrap.sql` proves membership closure in both directions
against that reality: no OrigenLab role is a member of any platform or predefined role, no
outside identity inherits or may assume the migrator or either runtime role, the four
creator-admin rows carry exactly `ADMIN`/no `SET`/no `INHERIT`, and the local `postgres`
`SET`-on-owner row — the one row `supabase/hosted_roles.sql` does not create — is pinned to
exactly its permitted options.

#### The environment classification

The run requires `--environment`, and the argument is a **classification, never an address**.
Requiring an explicit target and forbidding target disclosure are not in tension once the
required argument identifies which environment's rules apply and identifies no endpoint: a
short, non-secret, closed-set word, validated against the allowlist below. Anything that is
not one — a host name, a project reference, a connection string — is refused, and **is not
echoed back in the refusal**.

The classification never reaches the emitted SQL, which is byte-identical whichever
environment it is reviewed for; `bootstrap.assert_environment_absent` proves that after the
fact rather than assuming it. **The plan summary on `stderr` does record it**, and this
sentence is the documentation requirement that permits it: an operator reviewing a plan must
be able to see which environment's rules were applied, and the classification is not a
secret. Nothing else about the target appears anywhere — this repository stores no project
reference, organisation identifier or host name, and the tool never learns one because it
never connects.

| Classification | Durability posture | State |
|---|---|---|
| `staging` | **Pro plan daily backups, seven-day retention. PITR deliberately declined** — staging carries no durable human commercial truth and is rebuildable from migrations, so the decision is *declined*, not *unmade* | **approved** |
| `production` | **No RPO or PITR decision has been recorded** | **blocked** |

#### The staging provisioning posture

The decided shape of the staging project for this phase, recorded here so the bootstrap is
reviewed against a known environment rather than an assumed one. **None of it has been
provisioned**: no project has been created, adopted or billed from this repository, and
nothing here initiates a plan, compute size, address or backup setting — see
[`STATUS.md`](STATUS.md) §2.5.

| Item | Decision for this phase |
|---|---|
| Plan | **Pro** |
| Compute | **Micro** |
| Address | **Dedicated IPv4** |
| Region | **`sa-east-1`** |
| Spend | **Spend cap on** |
| Backups | **Daily, seven-day retention** |
| PITR | **Declined** — see the table above |
| Credential storage | **The operator's password manager**, and nowhere else; the `origenlab_migrator` password reaches the audit only through the environment variable its git-ignored target file names |

The region, compute size, address type and spend cap are provisioning choices with no
representation in this repository's code: nothing reads them, no check enforces them, and
changing one changes nothing here. They are recorded as the decision, and the project's own
settings remain the only authority on what was actually provisioned.

**Production is blocked until its recovery-point objective and its PITR requirement are
decided explicitly and recorded here in a reviewed change.** Staging's posture is not a
precedent for it: production holds durable human commercial truth. A production requirement
is never weakened to obtain a passing staging audit, and the tool refuses `--environment
production` outright rather than emitting SQL under an undecided posture.

#### Applying it, and the credential

Applying the bootstrap and assigning the migrator's password are **two separate operator
actions**, neither performed by this repository:

1. review the dry-run output, then apply the reviewed file to the project with `psql` as the
   project's `postgres` login. That login is not a superuser on Supabase; it holds
   `CREATEROLE` and receives `ADMIN OPTION` on every role it creates, which is why the file
   sets `NOSUPERUSER` / `NOBYPASSRLS` / `NOREPLICATION` only at `CREATE ROLE` and asserts
   them fail-closed on every run;
2. **assign the `origenlab_migrator` password separately, with a hidden secret input** —
   never on a command line, never in a file in this repository, never in the bootstrap. The
   credential then lives only in the operator's secret store, and is supplied to the Slice 0
   audit ([§4.2](#m-ops-slice0-audit)) through the environment variable its target file names.

`origenlab_api` and `origenlab_worker` receive no direct-login password during this slice.

#### Tests

```bash
python3 -m unittest discover -s supabase/audit/tests -t supabase/audit
supabase/scripts/hosted_bootstrap_failure_tests.sh
supabase/scripts/hosted_bootstrap_rehearsal.sh   # needs the local stack; see below
supabase test db --local        # includes supabase/tests/100_hosted_role_bootstrap.sql
```

**The rehearsal is the one thing that proves the file runs.** Static analysis proves what
`supabase/hosted_roles.sql` may touch; it cannot prove the SQL is valid, that the `DO` blocks
execute, or that the fail-closed assertions are right about the catalogue — and a wrong
assertion there is a file that refuses itself on the hosted project, which is the worst place
to find out. `supabase/scripts/hosted_bootstrap_rehearsal.sh` applies it to the **disposable
local database only**, inside one explicit transaction that is **always rolled back**, resolving
its target through `supabase/scripts/lib/local_target.sh` like every other script in
[§4.1](#4-migrations) so it cannot be pointed at a hosted project. It takes no arguments.

The transaction first revokes the local-only `postgres` `SET`-on-owner grant to put the session
in hosted-like shape — `postgres` may revoke only what it granted, so the creator-admin row
survives, which is exactly a fresh hosted project's shape. It then proves the file executes,
that a second application in the same transaction still succeeds, and that the role and
membership catalogue is byte-identical afterwards. Two negative halves follow: a platform
identity given `SET ROLE` on a runtime role makes the bootstrap refuse, and the hosted file
applied to the local database **as-is** refuses on the very grant `supabase/roles.sql` adds —
which is what makes the local/hosted divergence real rather than stylistic. The two files are
not interchangeable.

The failure-injection suite plants a malformed bootstrap file over
`supabase/hosted_roles.sql`, runs the real entry point, and restores the file **from a
pristine byte copy taken before the first plant — never with `git checkout`**. That
distinction is deliberate: a git restore silently does nothing when the file is untracked,
which would leave a planted credential in the working tree for someone to commit by
accident. Every restore is verified with `cmp`, an unconditional trap repeats it on any exit
path including an interrupt, and the suite ends by proving the file is byte-identical to its
own pre-suite bytes — not to `HEAD`, so the proof holds on a tree that already carries
unrelated modifications. Every scenario asserts the
same three things: a non-zero exit, **an empty stdout** — a refusal emits no SQL at all — and
a diagnostic naming the reason. It needs no running stack and no Docker; `psql`, `supabase`
and `pg_dump` are shimmed onto `PATH` only so the suite can prove none of them was ever
invoked.


## 5. Send control

`outbound.send_control` is one row with two independent flags:
`marketing_enabled` and `transactional_enabled`. **Both default to false.**

- Only an admin command changes a flag, and **every change requires a reason**
  and writes a domain event.
- **Turning a flag off takes effect immediately** for reservations and for the
  final check before the provider call. In-flight messages already handed to
  Gmail cannot be recalled ([`WORKFLOWS.md`](WORKFLOWS.md) §2.1).
- There is one sender path. **There is no break-glass script, no manual send
  utility and no second sender.** If sending is blocked, the answer is to fix
  the blocking condition or send by hand from Gmail and link the message.

```bash
# EXAMPLE — NOT YET IMPLEMENTED
ol send-control show --env production
ol send-control set --flag transactional --on  --reason "quote #1042 to cliente"
ol send-control set --flag marketing     --off --reason "bounce rate above threshold"
```

## 6. Checklists

### 6.1 Campaign activation

Every line must be true before `activate_campaign`.

1. Campaign is `approved`, and the approval event exists.
2. A dry run was executed **after** the audience was frozen, and its report is
   in Storage.
3. The dry run's exclusion counts were read by a human, and each
   `exclusion_reason` total is understood.
4. Every recontact override is intentional: count, reason and grantor match
   what was approved. **No override lifts a block or an active cooldown.**
5. Budget (`max_sends`) is set and is what was approved.
6. The sending mailbox is the production sender and is authorized.
7. `marketing_enabled` is **true**, changed by an admin with a reason, within
   this session.
8. Zero unresolved `ambiguous` attempts older than the agreed deadline.
9. Someone is watching for the first 15 minutes and knows how to run §9.

### 6.2 Quotation send

1. The revision is `approved`; its totals recompute from the stored inputs;
   its party snapshot is present, and the PDF shows the snapshot — the current
   address and participant rows are not consulted.
2. `pdf_sha256` is present and matches the object in Storage.
3. The snapshot's recipient address and its domain have **no `purpose = all`
   block**. (A `marketing` block from an unsubscribe, prior contact and
   cooldown do not apply to a transactional quotation.)
4. `transactional_enabled` is true.
5. After acceptance: the revision is `sent`, `sent_attempt_id` is set, and a
   permanent `prior_contact` fact now exists for that address.
6. If the customer is emailed by hand instead, use `link_sent_message` — the
   attachment hash must equal `pdf_sha256`.

## 7. Ambiguous attempt procedure

An `ambiguous` attempt means **OrigenLab does not know whether Gmail took the
message.** It holds the address lock and the campaign budget on purpose.

1. **Do not retry.** Nothing retries automatically, and neither should you.
2. Let the reconciler run. It searches the sender mailbox for the minted RFC
   822 id across all labels.
3. If it found the Sent copy, the attempt is already `accepted` +
   `sent_copy_confirmed`. Nothing to do.
4. If it did not, the attempt carries `search_evidence` and
   `needs_human = true`. **Read the evidence** — history id, searched-at,
   grace elapsed — and check the mailbox yourself.
5. Resolve with a verdict and a reason:
   `resolve_ambiguous(attempt, accepted|not_dispatched, reason)`. The command
   requires the recorded evidence to be present.
6. **Only after a resolution to `not_dispatched`** — or another explicitly
   documented safe resolution compatible with the one-open-attempt invariant —
   may `authorize_retry(attempt, reason)` create a **new** attempt. The
   original row is never edited except in its resolution fields.
7. Unresolved rows block campaign completion. **[OPEN]** deadline; recommended
   default is 7 days.

```bash
# EXAMPLE — NOT YET IMPLEMENTED
ol attempts list --state ambiguous --older-than 24h
ol attempts show <attempt-id>                        # prints search_evidence
ol attempts resolve <attempt-id> --verdict not_dispatched --reason "..."
ol attempts retry   <attempt-id> --reason "..."
```

## 8. Gmail sync recovery

| Symptom | Action |
|---|---|
| Cursor age above threshold | check worker liveness and queue depth before touching the cursor |
| History id rejected by Gmail (too old) | run a **full label resync**. This is safe: `(mailbox, provider_message_id)` is unique, replays insert with `ON CONFLICT DO NOTHING`, and a resync rewrites nothing |
| Duplicate-looking messages | expected when RFC 822 ids repeat; identity is the provider id ([`DATA.md`](DATA.md) §6). Do not deduplicate by RFC 822 id |
| Message missing after a resync | record it as absent; it is evidence, not a failure to repair |
| Attachment missing in Storage | re-fetch from Gmail; the row keeps its hash |

```bash
# EXAMPLE — NOT YET IMPLEMENTED
ol gmail status --mailbox contacto
ol gmail resync --mailbox contacto --full --reason "history id expired"
```

**Never repair a sync by editing `comms.message` by hand.**

## 9. Emergency shutdown

In order, fastest first:

1. **`ol send-control set --flag marketing --off --reason "<incident>"`** and
   the same for `transactional`. This stops every new reservation and every
   dispatch at the final check.
2. Pause the affected campaign.
3. Stop the worker only if step 1 is insufficient. Stopping the worker leaves
   `dispatching` rows that become `ambiguous` and need §7 — step 1 is cleaner.
4. If an address must never be contacted again, add a `block` with
   `purpose = all`. A block takes effect for every attempt that has not yet
   begun its provider call; it **cannot recall a message already handed to
   Gmail**.
5. Record what happened. The domain event stream is the incident record.

**Never** disable a constraint, edit `outbound.send_attempt` directly, or grant
a runtime role extra privileges to work around an incident.

## 10. Backups and restore drills

| Item | Frequency | Verification |
|---|---|---|
| Database backup with point-in-time recovery | continuous | restore drill |
| **Independent Storage bucket backup** | daily | bucket restore drill |
| Cold archive (SQLite, PST, Wave 1A, final V1 dump) | once, then immutable | `sha256sum -c` against the manifest on **both** copies |

**Database backups do not include Storage objects.** A restore drill that only
restores the database is not a restore drill.

Drill procedure:

1. Restore the database to a scratch project at a chosen point in time.
2. Restore the bucket backup into that project's Storage.
3. Verify: the 33 tables exist; row counts are plausible; a sample quotation
   revision's `pdf_sha256` matches the restored object byte-for-byte and its
   party snapshot is intact; the domain event stream is contiguous.
4. Confirm **both send flags are false** in the restored copy.
5. Record the drill as a domain event in production. **[OPEN]** cadence;
   recommended default is quarterly and after any schema change touching
   quotes or outbound.

## 11. Monitoring and alerts

| Alert | Condition | Severity |
|---|---|---|
| Ambiguous attempts | any attempt `ambiguous` for more than 24 h | **page** |
| Open attempt stuck | any attempt `dispatching` past its lease | **page** |
| Send flag changed | any write to `outbound.send_control` | **page** — expected changes are still worth seeing |
| Bounce rate | hard bounces above the agreed rate within a campaign | **page**, and pause the campaign |
| Block added at volume | an unusual number of new blocks in a window | investigate |
| Gmail cursor age | above threshold | investigate |
| Queue depth or oldest message age | above threshold | investigate |
| Failed migration or failed loader | any | investigate |
| Restore drill overdue | past cadence | investigate |
| Runtime role privileges | any **OrigenLab-created** role gains `BYPASSRLS`, a runtime role becomes able to assume `origenlab_owner` or issues `SET ROLE`, or a private schema becomes exposed | **page** |
| Grant boundary drift | `PUBLIC`, `anon`, `authenticated` or `service_role` regains schema `USAGE`, an object privilege or `EXECUTE`, or the owner's default privileges stop matching ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4) | **page** |
| Privileged-function drift | a `SECURITY DEFINER` function appears off the closed list, changes owner, loses its pinned `search_path`, replaces its `session_user` assertion with a `current_user` one, or becomes executable by `PUBLIC`, `anon`, `authenticated` or `service_role` | **page** |

## 12. Rollback execution

The decision and its ordering are owned by [`MIGRATION.md`](MIGRATION.md) §8.
The operator sequence is:

1. Set **both V2 flags false** with a reason.
2. Wait for zero `reserved` and zero `dispatching`; resolve every `ambiguous`
   attempt by hand (§7).
3. Export every V2 `accepted` attempt into V1's suppression and contact-state
   inputs **through V1's own operator path**.
4. **Only then** restore the V1 marketing sender, by reversing the sender
   quarantine. Quotations go out by hand from Gmail until V2 returns.
5. Record the rollback as a domain event.

**V2 is always disabled before V1 is restored.** Both enabled at once is the
one state that can send the same message twice.

## 13. Credentials

- **No credential, token, key, connection string or password appears in this
  repository or in these documents.** They live in the deployment platform's
  secret store and in the operator's password manager.
- The dashboard holds **only** the Supabase publishable key, used only for
  sign-in, refresh and MFA. **A project secret key never reaches a browser.**
- Storage access from FastAPI and the worker uses a current `sb_secret_...`
  key from server environment only, and **only for the Storage API**. Treat
  that key as what it is: it resolves to `service_role` and bypasses RLS, so
  the restriction is a rule of use, not a scope of the key. The API and the
  worker hold separate keys and rotate them independently. S3 access keys are
  not issued.
- **No Supabase secret key or legacy service-role key is used as an
  application-data or database credential**, and no legacy JWT `service_role`
  key is configured anywhere in V2. FastAPI and the worker reach application
  data over direct PostgreSQL connections as `origenlab_api` and
  `origenlab_worker`. No secret key appears in dashboard or web runtime
  configuration, in a browser bundle, or in source control
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4).
- The Gmail OAuth credential for the production sender is held by the worker
  only. **A V1 Gmail credential is never revoked without written proof that it
  serves no ingestion path** ([`MIGRATION.md`](MIGRATION.md) §7).
- Rotation: rotate a key by adding the new one, deploying, then removing the
  old one. Record every rotation. **[OPEN]** rotation cadence; recommended
  default is annually and immediately on any suspected exposure.
- If a secret is ever committed, treat it as compromised: rotate first, then
  clean history.
