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

### 1.1 Hosted freeze — `origenlab-v2` (in force from 2026-09-21)

**[V2 DECISION]**

**The hosted phase is closed. All V2 work happens against local PostgreSQL 17.**
`origenlab-v2` is frozen: it is neither adopted nor decommissioned, and nothing in this
repository may reach it.

While the freeze is in force, none of the following may be performed against any hosted
Supabase project, by an operator or by any tool in this repository:

- opening a database connection of any kind, including the read-only Slice 0 audit route;
- applying a migration, or writing to the migration ledger;
- loading contact, organization, campaign, quote or message data;
- rotating, setting or issuing any secret, key or role password;
- deploying application or dashboard configuration.

**The freeze is a posture, not a gate change.** No gate of [`MIGRATION.md`](MIGRATION.md)
§5.2 is weakened, satisfied or deferred by it, and in particular **the backup gate stands
unchanged**. The open items recorded against the hosted project stay open and stay counted.

**The two manual security controls were offered and deliberately not taken.** Disabling the
Data API and disabling the legacy JWT keys on `origenlab-v2` both remain **open gate items**
(`t01`/`d01` and the exposed-JWT-secret blocker of [`STATUS.md`](STATUS.md) §2.5). They are
Supabase Dashboard actions: this repository has no dashboard access, and under the freeze it
may not open the connection that would verify either one. They are therefore recorded as
open, not as attested, and not as satisfied.

**Local work continues under the same rules as hosted work.** Local PostgreSQL 17 is still
the Supabase-compatible schema: `supabase/roles.sql`, the migration chain, the grants, the
RLS policies and the pgTAP suite are applied and proven there exactly as §4.1 describes. The
point of the freeze is to avoid the hosted project, not to abandon the target architecture.
Code and migrations written under the freeze **must remain replayable against hosted
Supabase without modification**; a local-only shortcut that would need rewriting at cutover
is a defect, not a convenience.

**Reopening is an explicit operator decision.** Nothing reopens the hosted phase implicitly —
not a green local suite, not a finished slice, not a passing audit. When the operator
reopens it, the cutover sequence is:

1. upgrade `origenlab-v2` to a plan carrying a backup entitlement;
2. verify backup availability on the project;
3. rerun the hosted Slice 0 audit (§4.2);
4. apply the already-proven migration chain;
5. replay the deterministic promotion and import;
6. reconcile counts against the local run;
7. deploy API and dashboard configuration.

Steps 1 and 2 are not reorderable and not skippable: **the backup gate is never weakened to
obtain a passing audit.**

### 1.2 The hosted cutover runbook

**[V2 DECISION]**

Nothing here runs while §1.1 is in force. The operator reopens the phase explicitly; this is
what happens next, in order. It expands the seven steps above into the commands and checks
that actually discharge them.

#### Step 1 — backup entitlement

Upgrade `origenlab-v2` to a plan carrying a backup entitlement. [`STATUS.md`](STATUS.md) §2.5
records the project on the Free plan with **no backup retention and no platform backup
taken**, so this is a change of plan, not of setting.

#### Step 2 — verify backup availability

Confirm from the platform that a backup exists and can be listed, **before any write**. A
plan that grants the entitlement is not evidence that a backup was taken. If steps 1 and 2
cannot both be completed, the cutover stops here.

#### Step 3 — the two manual security controls

Both still open (§1.1, [`STATUS.md`](STATUS.md) §2.8), both Supabase Dashboard actions:

- disable the Data API — closes `t01` and `d01`;
- disable the legacy JWT keys, and rotate the exposed JWT secret.

#### Step 4 — rerun the hosted Slice 0 audit

```bash
supabase/scripts/slice0_audit.sh --mode hosted --authorize-hosted-connection
supabase/scripts/slice0_audit.sh --verify-report supabase/.audit/reports/<report>.json
```

Read-only. The verdict moves off `INCOMPLETE` only once the attestations `t01`–`t07` and
`d01` are recorded with evidence (§4.2).

#### Step 5 — apply the proven migration chain

The same 19 files proven locally, already recorded in the hosted ledger at 19 of 19
([`STATUS.md`](STATUS.md) §2.5). Nothing in the chain is hosted-specific.

**The local bootstrap is deliberately not part of it.** `dev_db.sh create` emulates the
platform's `extensions` schema and its `btree_gist` install so M01 can run on a database we
created ourselves. On a hosted project the platform has already done that, so the step is
simply not run — which is exactly what keeps the chain replayable against hosted Supabase
without modification.

#### Step 6 — replay the deterministic promotion and import

```bash
uv run python scripts/migration/import_waves_into_v2.py \
    --migration-root ~/data/origenlab-v2-migration --apply --database-url <hosted>

uv run python scripts/migration/promote_evidence_into_crm.py \
    --database-url <hosted> --apply --link-marketing
```

Both tools mint **deterministic identifiers** from the evidence, so hosted rows carry the
same `crm` ids as the local ones and reconcile **row by row**, not merely by count.

**Both refuse a non-loopback DSN today, by design and with no override flag.** Hosted replay
therefore needs a reviewed change to their target boundary — deliberately a separate,
reviewed change rather than a flag somebody can set under time pressure.

#### Step 7 — reconcile against the local run

The local figures to match, measured 2026-09-21 ([`STATUS.md`](STATUS.md) §2.7.1, §2.7.4):

| Table | Rows |
|---|---|
| `evidence.source_record` | 4 |
| `evidence.assertion` | 11,448 — 11,272 promoted, 4 ambiguous, 172 unresolved |
| `outbound.campaign` | 3 |
| `outbound.campaign_recipient` | 3,481 — 3,252 linked, 229 with no canonical channel |
| `outbound.send_attempt` | 3,141 — 3,138 reachable from an identity |
| `outbound.contact_control` | 10,588 |
| `comms.mailbox` | 1 |
| `crm.organization` | 1,812 — all `kind = 'unknown'`, all `machine_proposed` |
| `crm.contact_point` | 9,460 — 695 `shared_mailbox`, 8,765 `unattributed` |
| `crm.person` | **0** |
| `crm.domain_event` | 22,544 — every one `actor_kind = 'migrator'` |

And the invariants, each asserted inside the migration's own transaction so a violation rolls
the whole thing back rather than being discovered afterwards:

- `crm.person` is zero;
- no contact point and no campaign recipient carries a person or an organization;
- both `outbound.send_control` flags are `false`.

#### Step 8 — deploy API and dashboard configuration

Names and value shapes only; values are Render secrets and are never written in the repository.

| Variable | Value |
|---|---|
| `ORIGENLAB_V2_DATABASE_URL` | the hosted DSN over the Supavisor **session** pooler, port 5432, as `origenlab_api.<project-ref>` (the API refuses port 6543) |
| `ORIGENLAB_V2_DATABASE_REMOTE` | `true` |
| `ORIGENLAB_V2_DATABASE_EXPECTED_HOST` | the pooler host name |
| `ORIGENLAB_V2_DATABASE_SSLROOTCERT` | path of the Supabase CA PEM that `verify-full` requires against the session pooler — its chain terminates at CN `Supabase Root 2021 CA` (verified 2026-10-03 with an `openssl s_client -starttls postgres` handshake against the pooler; the system trust store refuses it) — downloaded from the Supabase dashboard, Database settings → "Download certificate", and delivered to Render as a secret file |
| `ORIGENLAB_GOOGLE_AUTH_ENABLED` | `true` — the Google Workspace OIDC session adapter is the production identity ([`apps/api/docs/PRODUCTION_AUTH.md`](../apps/api/docs/PRODUCTION_AUTH.md)) |
| `ORIGENLAB_GOOGLE_CLIENT_ID` / `ORIGENLAB_GOOGLE_CLIENT_SECRET` | the production Internal OAuth client |
| `ORIGENLAB_GOOGLE_WORKSPACE_DOMAIN` | `origenlab.cl` |
| `ORIGENLAB_AUTH_PUBLIC_BASE_URL` | `https://dashboard.origenlab.cl/api` |
| `ORIGENLAB_AUTH_SESSION_SECRET` | random, at least 32 bytes |
| `ORIGENLAB_AUTH_SESSION_TTL_SECONDS` | `43200` |
| `ORIGENLAB_PROFILE_LOGIN_ENABLED` | `true` — the shared-account profile selector |
| `ORIGENLAB_PROFILE_PIN_PEPPER` | random, at least 32 bytes — the same value the roster tool uses |
| `ORIGENLAB_V2_JWKS_URL` | **unset — must remain unset** (corrected 2026-10-02, see below) |
| `ORIGENLAB_DEV_LOGIN_ENABLED` | unset |
| `ORIGENLAB_V2_CRM_AUTHORING_ENABLED` | `true` once the operator turns CRM authoring on (the 28 `/v2/commands/*` people/organization/note commands, sales and admin); unset (default `false`) keeps the hosted deployment read-only and every authoring path a 404. The three #619 review findings that gated it are fixed ([`STATUS.md`](STATUS.md) §2.7.46). `GET /auth/session` reports the switch as `crm_authoring_enabled`, and the dashboard offers no CRM editor while it is off |
| `ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED` | `true` once the owner has authorized the Gmail send token (`apps/api/README.md` «Campaign test sends»); unset (default `false`) leaves «Enviar prueba» unmounted. Mounted only together with a readable, valid token file below — a bad file logs «campaign test send disabled» and the feature stays off |
| `ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE` | `/etc/secrets/gmail-send-token.json` — the Render secret file holding the `gmail.send` refresh token for `contacto@origenlab.cl` (`{client_id, client_secret, refresh_token, address}`); it cannot read mail. Revoking is deleting the file and both variables, and removing the app's access in the `contacto@` Google account |

**`ORIGENLAB_V2_JWKS_URL` must remain unset for the current deployment architecture.** An
earlier revision of this step instructed the operator to set it to the project's JWKS. That
was wrong, and the code proves it: `build_identity_port` in
`apps/api/src/origenlab_api/v2/identity.py` chooses the JWKS adapter **whenever the variable
is set, ahead of every other adapter**, and that adapter's `resolve` raises
`IdentityMisconfigured` on **every request** — it exists as a target for Supabase Auth, which
has not started ([`STATUS.md`](STATUS.md) §2, slice 1). Setting the variable would therefore
break every authenticated production request. The production identity today is the Google
OIDC session adapter, selected when no JWKS URL is set and Google sign-in is on; the
development header adapter is refused independently whenever `ORIGENLAB_ENV=production`, so
leaving the JWKS URL unset does not open it. When Supabase Auth replaces Google sign-in, this
step changes in a reviewed PR that also lands the adapter implementation — never by setting
the variable first.

The dashboard build needs no change. The proxy lists the named `/v2` GET paths and `/auth/*`;
the Worker source no longer derives an operator header from Cloudflare Access (built 2026-10-03; not deployed until step 9).

`origenlab_api` needs a password on the hosted project. `supabase/roles.sql` and
`supabase/hosted_roles.sql` both assign none by design; it is a separate operator action with
a hidden secret input (§4.3, §13).

#### Step 9 — the browser boundary becomes session-only

Deploy `apps/dashboard-proxy` from `main` (`npm run validate`, then `npx wrangler deploy` from
`apps/dashboard-proxy`). After this deploy the Worker forwards `/health`, `/auth/*` and the
named `/v2/*` reads on GET, the named `/v2/commands/*` POSTs (marketing, CRM authoring) plus the auth POSTs, with no identity of its own, and never derives an operator header.

#### Step 10 — remove Cloudflare Access from the dashboard hostname

Run this step only when all of the following hold:

- an operator has signed in end to end through the still-present Access screen
  (Google → profile → PIN → dashboard);
- (a) 2-step verification is enforced on the shared account `contacto@origenlab.cl`, checked in
  Google Workspace admin — the decision's MFA depends on it;
- (b) the shared account is pinned and the roster applied
  ([`apps/api/docs/PRODUCTION_AUTH.md`](../apps/api/docs/PRODUCTION_AUTH.md) §Provisioning);
- (c) the `api.origenlab.cl` Access application's own policy (service-token Service Auth) has been
  confirmed independent of the dashboard application, so deleting the dashboard application
  cannot strip the API's protection.

Then, in Cloudflare Zero Trust → Access → Applications, delete the application for
`dashboard.origenlab.cl`. Keep the `api.origenlab.cl` application. Verify from outside:
`GET /api/operator/status` → 403 `path_not_allowed`; `GET /api/health` → 200 through the Worker;
`GET /api/v2/workspace/overview` with no cookie → 401; a fresh browser reaches the Google button
directly, and a full Google → profile → PIN sign-in completes (not just the Google button).
Rollback: re-create the Access application (minutes) and `npx wrangler rollback`.

MFA for this sign-in is Google 2-step verification enforced on the shared Workspace account;
Cloudflare Access no longer adds a second factor on the dashboard hostname (decision:
[`MIGRATION.md`](MIGRATION.md) §11 row 5, 2026-10-03). Neither step has been executed; the Worker
in Cloudflare still carries the previous allowlist ([`STATUS.md`](STATUS.md) §2.7.41).

#### What this runbook does not yet cover

Recorded so it is not mistaken for completeness:

- **V1 durable rows.** Opportunities, tasks, activities and quotes are still in V1's
  PostgreSQL `commercial.*` and have never been migrated. Four `/v2` endpoints and three of
  the four CRM cards read zero because of it ([`STATUS.md`](STATUS.md) §2.7.2, §2.7.3).
- **Gmail capture.** V1 persists no Gmail message or thread id, so a V2 capture worker needs
  real Gmail API credentials rather than a shadow of V1 ([`DATA.md`](DATA.md) §6).
- **Quotes.** Blocked behind the V1 durable migration.

## 2. Operator roles

| Role | May |
|---|---|
| `viewer` | read everything the dashboard exposes **except contact addresses**: every email address and phone channel in a `/v2` answer is masked (`***@dominio`) by the API's route class (`contact_redaction.py`), the answer carries `X-OrigenLab-Redaction: contact-addresses`, a quotation PDF is refused (403) because a file cannot be masked, and `q=` on `/v2/contacts` searches only the recorded person's and institution's names — never the address — so a masked hit cannot confirm what the mask hides |
| `sales` | run every CRM command: create and advance opportunities, edit and submit quote revisions, create tasks and activities, promote evidence, freeze a campaign audience |
| `admin` | everything `sales` may, plus: change send control, approve quote revisions and campaigns, grant recontact overrides, revoke blocks, resolve ambiguous attempts, authorize retries, merge identities, and manage operators |

- `status ∈ {active, disabled}`. A disabled operator is refused at the command
  boundary even with a valid, unexpired token.
- **Admin commands require `aal2`** — a second factor in the current session.
- **[OPEN]** whether the approver of a quote revision must differ from its
  author. Recommended default: required once two `sales` operators exist.

### 2.1 V1 read routes are closed to the browser

The roles above exist only in V2. The V1 read routes `/contacts/*` and `/mirror/*` on
`apps/api` have no operator identity, no role and no redaction: upstream they are gated by
the shared `X-OriginLab-API-Key` alone, so anyone past Cloudflare Access would read every
contact address as recorded, whatever their role. **The dashboard proxy therefore refuses
both prefixes** — `apps/dashboard-proxy/src/allowlist.ts` no longer lists them, a request
answers **403 `path_not_allowed`** and is never forwarded (POST answers 405). V2 `/v2/*` is
the only browser surface for CRM, contacts and evidence.

Since 2026-10-03 the same holds for every other V1 prefix (`/operator/*`, `/cases/warm`,
`/opportunities/*`, `/operations/*`, and the V1 POST commands), and the proxy no longer derives
an operator identity from Cloudflare Access: the dashboard session cookie is the only identity
it carries (§1.2 steps 9 and 10; built, not deployed — [`STATUS.md`](STATUS.md) §2.7.41).

- **What stops working in the V1 panel:** every screen fed by the mirror (Catálogo,
  Proveedores, Prospectos, the lead-intel "Clientes" list, the commercial-deals and Gmail
  interaction audits) and the V1 contact drilldown panel. They show a load error; nothing
  else in the V1 panel changes — until 2026-10-03 (§2.7.41), when `/operator/*`,
  `/cases/warm`, `/opportunities/*` and `/operations/*` also leave the allowlist and the
  whole V1 panel shows load errors.
- **The API routes themselves still exist** and still answer a caller holding the API key
  directly. Closing them in the proxy is a browser-boundary decision, not a decommission.
- **Stays closed until** either V1 is decommissioned ([`MIGRATION.md`](MIGRATION.md) §5) or
  the V1 routes are migrated behind the V2 role model (resolved operator, `viewer` masking).
  Re-listing a prefix without one of the two reopens the unmasked read and is refused in
  review; `src/allowlist.test.ts` and `src/index.test.ts` pin every V1 prefix as refused.
- **CRM workspace reads, by name:** `GET /v2/workspace/{overview,pipeline,providers,drive,review}`,
  `GET /v2/workspace/equipment-interests`, the Marketing reads under `/v2/workspace/marketing*`
  and `GET /v2/cockpit/work-queue` are listed, exactly — the reads the dashboard's CRM
  (`#/crm/*`) needs (`apps/dashboard-proxy/README.md`). Upstream they require the dashboard
  session (401 without one) and mask contact addresses for `viewer`; the Worker forwards only
  the session cookie and refuses every other method (405). `src/v2WorkspaceReads.test.ts` pins it.
- **Still not exposed:** every other `/v2/cockpit/*` path — the other cockpit reads, the
  quotation PDF, `case-archive` and `import-review`. Each is a separate decision; the proxy
  suite pins their paths as refused. `/v2/commands/*` is listed only for the named Marketing
  and CRM-authoring commands, POST only; the case commands are not listed.

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
before migrations), `migrations/` (twenty-three ordered migrations: schemas and default
privileges, then the original 32 tables schema by schema, then grants, then RLS policies, then the
revocation of the owner's database-level `CREATE`, then the covering indexes for every
foreign key, then the outbound corrections — frozen campaign content and audience criteria,
the reply table, the tightened recipient address shape, the Wave 1B `contact_control.source`
labels and the archived-campaign `recontact_interval_days` carve-out — then the Slice 2
additions: the Gmail/Drive evidence kinds, the `source_record.review_noted` event, and the
`contact_point.usage` value `individual_owner_unknown`, then the three commercial-case tables
of Slice 3), `tests/` (pgTAP, 475
assertions across twelve files) and `scripts/`. Requirements:
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

#### Two clusters: the disposable one and the persistent one

Everything above runs against the Supabase CLI's cluster, and **that cluster is disposable by
design**. `supabase db reset` drops *every* non-system database in it, not only the project
database — measured on 2026-09-21, when a reset removed a second database placed there and its
build template outright. Nothing that must survive may live in it.

The persistent development database therefore runs in **its own container**:

| | CLI cluster (`supabase_db_origenlab`) | Development container (`origenlab_dev_db`) |
|---|---|---|
| Lifecycle | disposable; `supabase db reset` owns it | persistent; nothing resets it |
| Holds | the pgTAP suite's target, the replay evidence, `origenlab_test_*` databases | `origenlab_dev` — the local development instance of the V2 durable core |
| Image | the CLI's `supabase/postgres` | the **same** pinned `supabase/postgres` image |
| Port | 54322, bound on all interfaces by the CLI | **54332, bound on 127.0.0.1 only** |
| Durability | none; rebuilt from the chain | checkpoints outside Git |

Two clusters, still **exactly one durable database per era**: `origenlab_dev` is the local
development instance of the V2 durable core, and everything in the CLI's cluster is a test
fixture.

```bash
supabase/scripts/dev_db.sh up                 # start the container, apply roles.sql
supabase/scripts/dev_db.sh create             # create origenlab_dev
supabase/scripts/dev_db.sh migrate            # apply supabase/migrations/ in order; idempotent
supabase/scripts/dev_db.sh status             # ledger, tables, rows, send flags
supabase/scripts/dev_db.sh checkpoint --label <text>
supabase/scripts/dev_db.sh list
supabase/scripts/dev_db.sh restore <file.sql.gz> --force
supabase/scripts/dev_db.sh down               # stop it; the volume survives
```

**Checkpoints are the durability boundary**, and they live at
`~/data/origenlab-v2-local/checkpoints/` — directory `0700`, files `0600`, each with a
`.sha256` sidecar and a `.meta.json` recording the migration head and per-table row counts.
That root is outside Git, referenced by no repository path and covered by no `.gitignore`
exemption. It is a **sibling of `~/data/origenlab-v2-migration/` and never the same
directory**, so a checkpoint can never be confused with a migration bundle.

**A checkpoint carries data only.** Structure always comes from replaying
`supabase/migrations/`, so every restore re-proves that the chain replays, and ownership,
grants, RLS and policies are produced by the reviewed migrations rather than reconstructed by
`pg_restore` — which cannot reproduce them anyway, because every application object is owned by
`origenlab_owner` and the connection role holds that membership `SET`-only. It is also the same
shape as the eventual hosted cutover: apply the proven chain, then replay the data.

Three consequences worth knowing before you use it:

- A restore **replaces** `origenlab_dev` and is refused without `--force`. Take a checkpoint
  first.
- A checkpoint with no `.sha256` sidecar, or one that does not match it, is **refused**.
- `pg_dump` and `pg_restore` run **inside** the container. The host's client tools are
  whatever the distribution ships, and a PostgreSQL 16 `pg_dump` refuses a 17.6 server
  outright.

#### Disposable databases for tests

Tests that need their own database — migration, integration, rollback, concurrency,
deterministic replay — mint one rather than borrowing `postgres` or `origenlab_dev`:

```bash
db="$(supabase/scripts/disposable_db.sh mint)"     # prints origenlab_test_<8 hex>
trap 'supabase/scripts/disposable_db.sh drop "$db"' EXIT
supabase/scripts/disposable_db.sh sweep            # remove all of them
```

They are cloned from `origenlab_template`, which is rebuilt automatically whenever its ledger
drifts from `supabase/migrations/` in either head or count, so it cannot serve a stale schema.
`drop` and `sweep` check the `origenlab_test_<8 hex>` pattern **before anything else**, so
neither can ever remove `postgres`, `origenlab_dev` or the template.

<a id="m-ops-cleanroom"></a>
#### The clean-room database — `origenlab_clean`

**Why it exists.** On 2026-09-22 a database-backed test run wrote into `origenlab_dev`: seven
fixture `evidence.source_record` rows (`dedupe_key like 'pytest-%'`), seven fixture
`platform.operator` rows (`email_norm like 'pytest-%'`), nine `platform.command_receipt` rows,
and the three organizations, two contact points and sixteen `crm.domain_event` rows their
commands produced. The same run applied migration `20260922090000`'s DDL **without** recording
its ledger row, so `origenlab_dev` carries 21 migrations' worth of schema and a ledger that
names 20.

`origenlab_dev` also holds twenty real staged Gmail records ([`STATUS.md`](STATUS.md) §2.7.7),
so it is neither trustworthy nor disposable. It is therefore **quarantined**: left exactly as
it is, read-only, until an operator decides what to do with it. Nothing in this procedure
writes to it, drops it or restores over it.

The clean room is where V2 work happens instead. `origenlab_clean` is a second database in the
same development container, rebuilt from nothing but `supabase/migrations/` and the
reproducible historical load — so everything in it traces to an input a human can read and
refuse, and the ledger always describes the schema.

**Build it.**

```bash
supabase/scripts/dev_db.sh up                      # the container, if it is not running
supabase/scripts/cleanroom_db.sh build             # refuses if origenlab_clean already exists
supabase/scripts/cleanroom_db.sh build --force     # replace it
```

`build` runs, in order: **preflight** → create → platform-emulating `extensions` schema and an
empty ledger → the full migration chain, one transaction and one ledger row per file → one
seeded `platform.operator` → `import_waves_into_v2.py --apply` → `promote_evidence_into_crm.py
--apply` → `stage_gmail_drive_evidence.py --apply` once per manifest → `verify`. Any step that
refuses stops the build; nothing half-loaded is left behind a successful exit.

**Preflight comes before the `DROP`, and it validates rather than merely looks.** Every input
is put through the same tool that will later replay it, in that tool's dry-run mode — which
opens no database connection at all: `import_waves_into_v2.py` re-verifies both bundles'
manifests and every file hash, and `stage_gmail_drive_evidence.py` parses each staging manifest
under the full version 2 rules. A missing file, an edited artifact, an unreadable manifest or a
message intake excludes by rule therefore refuses **with the existing database untouched**.
This is not belt-and-braces: on 2026-09-22 the old preflight checked that a manifest file
*existed*, the build dropped the database, and staging refused three steps later against a
half-loaded clean room (docs/STATUS.md §2.1).

**Two inputs live outside Git**, by design, and `build` refuses at the door if either is
absent rather than producing a partial database:

| Input | Path | What it is |
|---|---|---|
| Wave 1A/1B safety bundles | `~/data/origenlab-v2-migration/` | the historical load's artifacts |
| Gmail staging manifests | `~/data/origenlab-v2-local/evidence/` | every `*.json` in the directory, staged in sorted order: the September sweep (20 messages) and the R1 archived replay batch (11). A batch is in the baseline when its manifest is in this directory |

The manifests are **replayed, never re-fetched**. `stage_gmail_drive_evidence.py` imports no
Google client and holds no credential; its only input is those files.

**Re-acquiring a manifest at version 2.** Version 2 requires each Gmail record's
`intake_class` and its real `gmail_labels`, and those are facts only a fresh look at the mailbox
can supply — which is the whole point of the rule, so they are never filled in from memory. When
a manifest predates the rule, the labels are re-acquired **read-only** (no send, no draft, no
label, no archive, no trash, no modify; bodies are never requested) into a private acquisition
file, and the manifest is rewritten against it:

```bash
cd apps/email-pipeline && uv run python scripts/migration/reacquire_gmail_manifest_v2.py \
  --manifest    ~/data/origenlab-v2-local/superseded/<name>.manifest-v1.json \
  --acquisition ~/data/origenlab-v2-local/acquisition/<name>-labels.json \
  --out         ~/data/origenlab-v2-local/evidence/<name>.v2.json
```

The tool makes no network call of its own — it reads two local files and writes a third. It
keeps every external id, source URI, `acquired_at` and observation exactly as they were, adds
the two required fields and nothing else, re-checks the sender and date the old manifest claimed
against what was re-acquired, and refuses the **whole** pass on any disagreement: a missing id,
an extra id, a sender or date that moved, or a message that has since been drafted, spammed or
trashed. It refuses to write inside the working tree, and the result is validated by the staging
loader before it is written. The superseded version 1 file moves **out** of
`~/data/origenlab-v2-local/evidence/`, because that directory *is* the baseline. Done for the
September sweep on 2026-09-22 (docs/STATUS.md §2.1).

**The seeded operator is fictitious.** `build` inserts one `platform.operator` row so the
command boundary can resolve an identity at all. Its address defaults to
`operador.local@example.invalid` — undeliverable, and safe to have in a public repository,
which the previous hard-coded personal address was not. Nothing reads the value; `verify`
counts the row. To build with your own instead, put it in the environment rather than in a
file:

```bash
OL_CLEAN_OPERATOR_EMAIL=you@yourdomain.cl supabase/scripts/cleanroom_db.sh build --force
```

**Rehearsing a review decision against the real records.** `attribute_sender_organization`
(docs/STATUS.md §2.7.13) is the one command whose inputs are hard to reproduce by hand: two
institutions named in one message, one of which already exists under exactly that name. To
check it against the rows that actually exist, without deciding anything:

```bash
apps/api/.venv/bin/python supabase/scripts/rehearse_attribution.py
```

It opens `origenlab_clean` **read only**, replays each eligible record into its own
disposable `origenlab_test_<hex>` database, runs the real command there as `origenlab_api`,
drops the room, and fingerprints the clean room before and after — a rehearsal that changed
it fails rather than being discovered later by `verify`. It needs
`cleanroom_db.sh api-login` to have been run, because it deliberately does not run as
`postgres`. It records nothing: after it, `verify` still passes at 41 probes.

**Simulating a whole commercial case.** The six case commands (docs/STATUS.md §2.7.17) are
easier to argue about as a finished case than as six rule lists. This runs one end to end —
a requesting institution, Hielscher as supplier *and* manufacturer on the same case, one
evidence link and one equipment interest — and prints it:

```bash
apps/api/.venv/bin/python supabase/scripts/simulate_commercial_case.py
```

Every fixture in it is **invented** (reserved `.invalid` domains; the only real name is
Hielscher, which is on the public approved-brand list and is there precisely to show that an
approved supplier is never turned into a customer by appearing on a case). Every row it
writes goes into a disposable `origenlab_test_<hex>` database it creates and drops, and the
commands run there as `origenlab_api`. It reads nothing from any real database. It
fingerprints `origenlab_clean` before and after, read only, and fails loudly if the two
differ — so "this did not happen in the clean room" is measured rather than promised. Like
the rehearsal, it needs `cleanroom_db.sh api-login`. `--keep` leaves the disposable database
for inspection.

**The database name is a literal.** `build --force` drops a database, so the name comes from
the `OL_CLEAN_DBNAME` constant in `supabase/scripts/lib/local_target.sh` and from nowhere else
— not an argument, not an environment variable, not a config file. The guard refuses to
resolve to anything but `origenlab_clean`, it refuses `origenlab_dev` by name before doing
anything else, and it **discards the development database's DSN**, so inside a clean-room
script `ol_psql_dev` cannot connect at all. `build --force` prints the exact name on its own
line immediately before the `DROP`.

**Verify it.** Read-only, safe at any time — both probe files run inside `begin read only`.
There are **two contracts**, because a clean room stops being fresh the moment an operator
decides something in it, and `origenlab_clean` has carried decisions since 2026-09-24:

```bash
supabase/scripts/cleanroom_db.sh verify                   # the data-bearing contract (default)
supabase/scripts/cleanroom_db.sh verify --fresh-rebuild   # the exact fresh baseline; `build` runs it last
supabase/scripts/cleanroom_db.sh status
```

| Contract | Files | Asserts | True when |
|---|---|---|---|
| **data-bearing** (`verify`) | `supabase/cleanroom/data_bearing.sql` → `expected_data_bearing.json` | the ledger equals the files in `supabase/migrations/` (a missing or phantom version is **named**); the 7 schemas, 41 tables, 148 policies, 4 roles and their attributes; the PUBLIC/anon/authenticated/service_role boundary; the closed SECURITY DEFINER list and its shape; the sign-in privilege boundary (`pin_hash` unreadable, no runtime write on `platform.operator` or the throttle columns, sessions revocable and nothing else, audit append-only); send flags off; no pytest residue. **Counts no business row** | always — a fresh build, and one carrying 44 opportunities, both pass; 41 probes |
| **fresh-rebuild** (`verify --fresh-rebuild`) | `supabase/cleanroom/verify.sql` → `expected_counts.json` | exact row counts of the historical load and of what the chain seeds (one campaign block, empty sign-in tables), one operator, zero commands | immediately after `build`, and only then; 46 probes |

Both comparisons are **exact in both directions**: a probe declared and not measured fails, and
a probe measured and not declared fails, so the SQL and its expectation cannot drift apart in
silence. Neither file may be re-declared to today's business counts: a count that moves with a
human decision is not a contract, and `cleanroom_verify_tests.sh` S2 refuses a business count
in the data-bearing file. What the fresh file *must* follow is the chain — S1 fails CI the
moment a migration lands without `migrations.count` / `migrations.head` moving with it, which
is the check that was missing when the fixture sat at 25 of 40 migrations.

**Point the API at it.**

```bash
supabase/scripts/cleanroom_db.sh api-login          # writes ~/data/origenlab-v2-local/api.cleanroom.env
set -a; . ~/data/origenlab-v2-local/api.cleanroom.env; set +a
```

That is the whole switch. `ORIGENLAB_V2_DATABASE_URL` is the only thing that selects a V2
database, and `apps/dashboard` reaches it solely through `apps/api`, so there is no second
knob and no dashboard setting to keep in step. **The default is unchanged**: `dev_db.sh
api-login` still writes `api.env` for `origenlab_dev`, nothing sources either file by itself,
and selecting the clean room is an explicit act.

`ORIGENLAB_V2_DATABASE_URL` is now **validated, not trusted**. `apps/api` refuses to start on
a value that is not a literal-loopback DSN, carries a query string or fragment, or names a
hosted provider — the same rule `migration/v2_import/target.py` applies, so the two boundaries
agree exactly rather than approximately.

**A test may never open it.** `origenlab_clean` and `origenlab_dev` are both in
`PROTECTED_DATABASES`; a `ORIGENLAB_V2_TEST_DSN` or `ORIGENLAB_V2_API_TEST_DSN` naming either
is refused at import time, and the disposable-database fixture asks the *server* which database
it reached before running a statement. The second check is the one that holds when a DSN is
rewritten or a name-swap goes wrong, which is what this is for.

**Tests.**

```bash
supabase/scripts/cleanroom_failure_tests.sh        # 30 refusal scenarios, connects to nothing
supabase/scripts/cleanroom_verify_tests.sh         # the two contracts: static + chain + restored copy
cd apps/api && uv run pytest tests/test_v2_target_boundary.py tests/test_protected_databases.py
```

`cleanroom_verify_tests.sh` has three parts. `--static` opens no database (CI runs it before
the stack starts). `--chain` mints a disposable database carrying the full chain and no rows,
proves the data-bearing contract passes on it and the fresh one still fails, then injects seven
faults one clone at a time — a deleted ledger row, a phantom one, `EXECUTE` on
`begin_pin_attempt` revoked, `SELECT (pin_hash)` granted, `UPDATE` on `platform.operator`
granted, RLS off on `auth_session`, a definer's `search_path` unpinned — and proves each fails
closed **by probe name** (CI runs this against its own stack with `--cluster cli`). `--restored`
runs only from the working tree that started the development container: it dumps
`origenlab_clean` with the container's own `pg_dump` (read-only) into a scratch
`origenlab_test_<8 hex>` in the same container, proves the data-bearing contract passes on the
copy and the fresh one fails on it, and proves `origenlab_clean`'s ledger and write counters are
identical before and after. Every scratch is dropped on exit; nothing opens `origenlab_dev`.

**Throwing it away** is routine, because rebuilding is the recovery procedure — there is no
checkpoint, no restore and nothing to lose:

```bash
supabase/scripts/cleanroom_db.sh drop --force
```

#### Verifying an applied chain

```bash
supabase/scripts/verify_chain.sh --dev                    # the persistent database
supabase/scripts/verify_chain.sh --database postgres      # the CLI's project database
supabase/scripts/verify_chain.sh --database "$db" --json  # a minted test database
```

Read-only. It measures the ledger against the files on disk, then schemas, tables per schema,
roles and their attributes, the `PUBLIC`/`anon`/`authenticated`/`service_role` grant boundary,
default privileges, the M14 database-`CREATE` revocation, RLS coverage, the policy count,
foreign-key index coverage, object ownership, the `SECURITY DEFINER` count, the user-trigger
list and the send flags. It **complements pgTAP rather than repeating it**: pgTAP proves
semantics against a freshly reset `postgres`, this proves inventory on databases pgTAP was not
run against.

`supabase/scripts/local_db_failure_tests.sh` proves this tooling fails closed — 22 scenarios
covering rejected database names, the psql helpers refusing to run before their guard, a
planted `project-ref`, every attempt to drop a protected database, both `--force` gates and
three unverifiable checkpoints. It needs no database and runs in CI.

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

#### Starting the stack in CI

CI does not call `supabase start` directly. `.github/workflows/supabase.yml` starts the stack
with `supabase/scripts/start_local_db.sh`, which absorbs one transient registry failure and
nothing else:

- **At most two attempts** — the first, and one retry.
- **The retry is taken only when the last five lines of the failed attempt contain
  `failed to pull docker image from all registries`** — the CLI's final verdict after it has
  exhausted every registry. Registry noise earlier in the log (`toomanyrequests`,
  `Retrying after`, `download failed`) does not count: the CLI prints it while recovering on its
  own, and a later migration, health-check or startup failure must not be mistaken for a
  transient one. Any other failure exits non-zero at once, so a genuine failure can never be
  retried into a green run.
- **Before the retry it runs `supabase stop --no-backup`**, then waits
  `OL_START_RETRY_DELAY` seconds (default 20). This is the one sanctioned use of
  `--no-backup`, and it is sanctioned only there: the runner is ephemeral and the stack never
  came up, so there is no data volume worth keeping and a half-created one must not leak into
  the second attempt. Locally the rule above stands.

`supabase/scripts/start_local_db_retry_tests.sh` proves that contract. It runs the real script
against a fake `supabase` CLI placed first on `PATH`, needs no Docker and no network, and runs in
CI before the real start. Each of its seven scenarios asserts the exit code, the exact sequence
of CLI calls and the diagnostic:

| # | Scenario | Expected |
|---|---|---|
| 1 | success on the first attempt | exit 0; one `start` |
| 2 | image-pull failure, then success | exit 0; `start`, `stop --no-backup`, `start` |
| 3 | genuine failure on the first attempt | non-zero; one `start`, no retry |
| 4 | image-pull failure, then a genuine failure | non-zero; `start`, `stop --no-backup`, `start` |
| 5 | image-pull failure on both attempts | non-zero; exactly two `start`s, never a third |
| 6 | pull noise earlier in the log, then a genuine failure | non-zero; one `start`, no retry |
| 7 | genuine failure, where a retry would have succeeded | non-zero; one `start`, no retry |

An attempt a scenario does not script succeeds loudly, so an unwanted retry shows up as a false
pass rather than hiding.

```bash
supabase/scripts/start_local_db_retry_tests.sh   # 7 scenarios, connects to nothing
```

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
supabase/scripts/slice0_audit.sh --mode hosted --authorize-hosted-connection \
                                 --authorize-supavisor-session-route
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
  symlink) with mode `600`, and **declares its route** in `OL_HOSTED_ROUTE` — one of the two
  below, and the file must carry that route's exact key set, no more and no fewer.
- **The declared route was authorised on this command line.** `direct` always is. The
  Supavisor session-mode route additionally requires
  `--authorize-supavisor-session-route`, and the flag alone is not enough: the reviewed
  target file must declare it too. **There is no fallback.** An unreachable direct endpoint
  is a failed run, not a silent reroute; no code path tries one route and then the other.
- The audit identity is **`origenlab_migrator`**, the configured hosted audit identity for
  this initial audit. It is not a fifth role and no migration creates one. The credential
  stays outside this repository: the target file names an **environment variable**, and
  there is no key that accepts a password inline.

#### The two hosted routes

The direct endpoint is IPv6 unless the project holds the IPv4 add-on, so an IPv4-only
operator network cannot reach it. The official shared Supavisor pooler is IPv4 on every
plan, and the **dedicated** pooler is a paid-plan feature this project does not have. That
is the whole reason the second route exists — not convenience.

| | `direct` | `supavisor-session` |
|---|---|---|
| Host | `db.<project-ref>.supabase.co` | `aws-<cluster>-<region>.pooler.supabase.com` |
| Port | 5432 | 5432 (session mode) |
| Login | `origenlab_migrator` | `origenlab_migrator.<project-ref>` |
| Where the project is named | the host name | **the login name only** |
| Extra target-file keys | none | `OL_HOSTED_POOLER_CLUSTER`, `OL_HOSTED_POOLER_REGION` |
| Authorisation | `--authorize-hosted-connection` | that, **and** `--authorize-supavisor-session-route` |

**Port 6543 is refused by name.** It is Supavisor's *transaction* mode — since
2025-02-28 it serves transaction mode only — and transaction mode does not keep session
state between transactions, so it cannot carry this audit's `SET LOCAL ROLE` and its
transaction-local timeouts across the check bank. A route that cannot establish the
read-only contract is refused, never degraded to.

**The pooler login is a stricter identity check, not a looser one.** Supavisor routes a
connection to a tenant by the project reference in the user name, and the pooler host
names no project — so the login *is* this route's target-identity binding. It is required
to equal `origenlab_migrator.<the project reference the target file declares>` exactly.
A login for another project, for `postgres`, for another role, or the bare role name is
refused. The cluster index and the region are declared separately and proven against the
host name, so a target that is right about one and wrong about the other is refused as
ambiguous rather than resolved by precedence.

**The project reference in that login is treated as a secret.** It is added to the
target's secret set, `describe()` reports `origenlab_migrator.[project-ref-redacted]` and
never the raw login, and `olaudit.redact` carries a `<role>.<project-ref>` pattern as the
safety net for text the process never knew it held.
`scripts/security/check-public-repo-hygiene.sh` fails if that shape ever enters tracked
content.

**How the read-only contract survives a pooler.** A pooler sits between `psql` and
PostgreSQL and decides for itself what it forwards of the libpq startup `options` string.
The audit still sends it — if Supavisor rejects it the connection fails and the route is
refused, which is the correct fail-closed answer — but it does not *rely* on it. On this
route the transaction re-establishes everything and then proves it:

1. `begin read only`, before any audit query;
2. `set local statement_timeout`, `lock_timeout` and
   `idle_in_transaction_session_timeout` — **transaction-local**, because the backend
   behind a pooler outlives this connection and must not inherit anything this audit set;
3. `set local role origenlab_owner`, for the same reason;
4. a guard statement reads `transaction_read_only` **back from the server** and raises in
   the same statement if it is not `on`, so `ON_ERROR_STOP` aborts the run **before the
   first check file is parsed**. `olaudit.psqlrun` then asserts the same fact in Python
   from the guard's own output, and refuses the run if the guard produced nothing, an
   unrecognised shape, or an unset timeout;
5. the reviewed check bank — the same files, already statically proven single reads;
6. `reset role; rollback;`. **The pooler script contains no `COMMIT` on any path**: it
   rolls back explicitly on success, and on any error `ON_ERROR_STOP` aborts the script and
   `psql` disconnects with the transaction still open, which the server rolls back.

If the connection default `default_transaction_read_only` does not survive the pooler, that
is **recorded as a note on `s01`, not required** — what the audit needs is the transaction,
which `begin read only` establishes and which `s01` requires on every route unconditionally.
On the direct route it stays a hard requirement, unchanged.
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
| `supabase/.audit/hosted_target.env` | the hosted target, mode `600`. Names the route, the project and the credential's environment variable; **never a credential** |
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
- **a Supabase-managed or PostgreSQL predefined role named by any statement, outside one
  closed exception.** `postgres`, `service_role`, `anon`, `authenticated`, `supabase_admin`,
  `pg_read_all_data` and the rest can never be created, altered, granted a membership, or
  granted anything. The exception is one-way and removes privilege only: the file may
  **revoke** the `SET` and `INHERIT` options on `origenlab_owner` from `postgres`, and
  nothing else about a platform identity. It is governed by its own allowlist in
  `bootstrap.py` (`PERMITTED_OPTION_REVOCATIONS`), which the file must match **exactly** —
  same options, same role, same member, no more and no fewer. **`ADMIN` may not be
  revoked**: the role creator's ADMIN OPTION is tolerated on purpose
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4), and converging past the policy is as much a
  deviation from it as falling short. There is no counterpart shape that could *confer*
  anything on a managed role;
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

#### The one convergence the hosted file performs on a platform identity

The divergence above is not merely refused, it is **converged**. The hosted `origenlab-v2`
catalogue carries `postgres -> origenlab_owner` with `SET` — the shape the *local*
`supabase/roles.sql` creates and the hosted file deliberately does not — because that local
file reached the project through `supabase db push --include-roles` on 2026-09-08
([`STATUS.md`](STATUS.md) §2.5). Without a convergence, the hosted file's own
platform-boundary assertion would refuse the file on the very project it exists to
bootstrap. So it revokes the two privilege-bearing options, and only those:

```sql
revoke set option for     origenlab_owner from postgres;
revoke inherit option for origenlab_owner from postgres;
```

`postgres` may revoke only the grants it made itself. The `SET` row was granted by
`postgres`; the creator-`ADMIN` row was granted by `supabase_admin`. The statement therefore
*cannot* reach the administrative relationship §6.4 keeps, which is why the exception can be
narrow rather than merely promised — `supabase/tests/100_hosted_role_bootstrap.sql` asserts
that asymmetry from the catalogue rather than restating it here. Revoking an option that is
not held raises a `WARNING`, never an error, so the pair is a no-op on a fresh project and
on every application after the first; the `INHERIT` revoke is already a no-op on the shape
actually observed, which holds `SET` but not `INHERIT`. The `REVOKE ... OPTION FOR` grammar
requires **PostgreSQL 16 or later**; the hosted project and the local container are both
PostgreSQL 17.

**The managed-role policy, stated without a blanket:**

- **no Supabase-managed role is created or `ALTER`ed;**
- **no object privilege is granted to a managed role;**
- **exactly the two reviewed `SET`/`INHERIT` option revocations on
  `postgres -> origenlab_owner` are permitted;**
- **`ADMIN`-option revocation is prohibited**, and `postgres`'s intentionally retained ADMIN
  relationship is **not** a policy violation.

#### How PostgreSQL 17 records the revocation, and how to audit it

`REVOKE SET OPTION FOR` / `REVOKE INHERIT OPTION FOR` do not necessarily delete the
`pg_auth_members` row. On PostgreSQL 17 the row granted by `postgres` survives the
convergence with every option false, measured locally on 2026-09-20:

```text
 role            | member   | grantor  | admin | inherit | set
-----------------+----------+----------+-------+---------+-----
 origenlab_owner | postgres | postgres | f     | f       | f
```

That row is **vestigial**: it confers no membership capability at all. A test or audit that
asserted "no `pg_auth_members` row exists for `postgres` on `origenlab_owner`" would fail
against a correctly converged database, and would also miss the second row — the
`supabase_admin`-granted creator-ADMIN row, which is supposed to remain. **Judge the
`admin_option` / `inherit_option` / `set_option` values and the behaviour, never the
presence or absence of a row.**

One caveat about behavioural checks, measured on PostgreSQL 17 rather than assumed.
`pg_has_role(…, 'MEMBER')` returns **true for `postgres` both before and after the
convergence**, because it answers "is a member in any way", and the retained creator-ADMIN
row satisfies it. It is *not* a test of `SET ROLE` capability. The actual capability does
change, and the honest tests for it are the `set_option` column and an attempted
`SET ROLE`:

| Check, as `postgres` | Before convergence | After convergence |
|---|---|---|
| `SET ROLE origenlab_owner` | succeeds | **`ERROR: permission denied to set role "origenlab_owner"`** |
| `set_option` on the `postgres`-granted row | `t` | `f` |
| `pg_has_role('postgres','origenlab_owner','MEMBER')` | `t` | `t` — **unchanged, and therefore useless as evidence** |
| `pg_has_role('postgres','origenlab_owner','USAGE')` | `f` | `f` |

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
| `production` | **Decided 2026-10-02** — RPO **24 hours**; **Pro plan daily backups required**; **PITR declined initially**; an independent logical `pg_dump` before and after every material data-bearing cutover — see *The production durability posture* below | **decided here; the tool still refuses it** — `DURABILITY["production"]` in `supabase/audit/olaudit/bootstrap.py` carries `decided=False` until a separate reviewed change mirrors this record |

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

#### The production durability posture

**[V2 DECISION]** — recorded 2026-10-02 by the owner, in the reviewed change the bootstrap's
own refusal asks for. This is the initial V2 production backup posture; staging's posture
was not its precedent, because production holds durable human commercial truth.

| Item | Decision |
|---|---|
| Recovery-point objective (RPO) | **24 hours** |
| Platform backups | **Supabase Pro daily backups — required.** Step 1 of §1.2 upgrades `origenlab-v2` to Pro for this entitlement; step 2 verifies a backup exists before any write |
| PITR | **Declined initially.** Not unmade: declined, with the revisit condition below |
| Independent logical backup | **An independent logical `pg_dump` is required before and after every material data-bearing cutover**, under the existing runbook contracts — the clean-room `pg_dump` procedures of §4.1 (the `--restored` verification copy, and the dump taken before the 2026-09-30 slice-1 apply recorded in [`STATUS.md`](STATUS.md) §2.7.39), the cold-archive manifest and restore drill of §10, and the slice gates of [`MIGRATION.md`](MIGRATION.md) §5.2 |
| Revisit | **PITR is reconsidered when V2 becomes the sole durable CRM writer** (V1 `commercial.*` decommissioned, [`MIGRATION.md`](MIGRATION.md) §5 slice 8), or earlier if the operational value of a sub-day recovery point justifies its cost |

**What this record does and does not do.** It discharges the *decision* the production
bootstrap requires: `bootstrap.py` refuses `--environment production` with the instruction
to "record the decision in docs/OPERATIONS.md §4.3 in a reviewed change before bootstrapping
production", and this is that record. It does **not** change the tool: `DURABILITY["production"]`
still carries `decided=False`, so `hosted_role_bootstrap.sh --environment production` still
refuses, and failure-injection check L still proves that it does. Mirroring this record into
the tool — flipping `decided`, carrying this summary, and re-pointing check L — is a
**separate reviewed tooling change**, not something done alongside the decision itself and
never by weakening the guard. Until it lands, production bootstrapping remains refused on
purpose. A production requirement is never weakened to obtain a passing staging audit, and
the tool never emits SQL under an undecided posture.

#### Applying it, and the credential

Applying the bootstrap and assigning the migrator's password are **two separate operator
actions**, neither performed by this repository:

1. review the dry-run output, then apply the reviewed file to the project with `psql` as the
   project's `postgres` login, **under the application contract below and in no other way**.
   That login is not a superuser on Supabase; it holds
   `CREATEROLE` and receives `ADMIN OPTION` on every role it creates, which is why the file
   sets `NOSUPERUSER` / `NOBYPASSRLS` / `NOREPLICATION` only at `CREATE ROLE` and asserts
   them fail-closed on every run;
2. **assign the `origenlab_migrator` password separately, with a hidden secret input** —
   never on a command line, never in a file in this repository, never in the bootstrap. The
   credential then lives only in the operator's secret store, and is supplied to the Slice 0
   audit ([§4.2](#m-ops-slice0-audit)) through the environment variable its target file names.

`origenlab_api` and `origenlab_worker` receive no direct-login password during this slice.

#### The application contract: one transaction, or nothing

The bootstrap is **all or nothing.** It creates four roles, converges their attributes,
converges one membership, revokes two options from a platform identity, and only then asserts
the platform boundary. A half-applied run of that sequence leaves the project in a state no
reviewed file describes — roles that exist with unconverged attributes, or a convergence that
landed while the assertion guarding it never ran — and there is neither a repair procedure for
that state nor any evidence that would tell an operator which half happened. So the application
is **required to be atomic**, and the requirement is stated as an invocation rather than as an
intention.

**Any hosted application of `supabase/hosted_roles.sql` MUST be exactly this, and nothing
else:**

<!-- ol:hosted-apply-contract -->
```bash
env -i \
  PATH=/usr/bin:/bin \
  HOME="$HOME" \
  LANG=C \
  PGHOST="$OL_HOSTED_HOST" \
  PGPORT="$OL_HOSTED_PORT" \
  PGUSER="$OL_HOSTED_USER" \
  PGDATABASE="$OL_HOSTED_DATABASE" \
  PGSSLMODE=verify-full \
  PGSSLROOTCERT="$OL_HOSTED_SSLROOTCERT" \
  PGPASSWORD="$OL_HOSTED_PASSWORD" \
  psql -X --no-psqlrc -v ON_ERROR_STOP=1 --single-transaction \
       -f supabase/hosted_roles.sql
```

**The operation must fail and roll back completely if any statement or any final assertion
fails.** The file's two fail-closed assertion blocks — the attribute assertion after the
`ALTER ROLE` convergence, and the platform-boundary assertion at the end — execute inside the
same transaction as the statements they guard, and the boundary one is the *last* thing in the
file — so a violation discovered at the end undoes the whole convergence rather than reporting
on a change that has already landed.

Element by element, with what a run without it actually risks:

| Element | Why it is in the contract |
|---|---|
| `psql -X`, `--no-psqlrc` | The same switch, written both ways because the contract names both and a reader should not have to know they are one. No `~/.psqlrc` and no `PSQLRC` file is read, so no operator convenience setting — a `\set ON_ERROR_STOP off`, an `AUTOCOMMIT` change, an `\i` — can alter the meaning of the reviewed bytes. A startup file also runs *before* `--single-transaction` opens its transaction, so anything it did would not be rolled back with the rest |
| `-v ON_ERROR_STOP=1`, **on the command line** | Set as a `psql` variable in the argument vector: not inside the SQL file, not in a startup file, not in an environment variable. The file **cannot** carry it — `\set` is a psql meta-command and `bootstrap.py` refuses meta-commands outright — so the command line is the only place it can come from. Without it `psql` continues past a failed statement and then reaches the end of the file and issues `COMMIT`, which under `--single-transaction` means committing a transaction that skipped statements or is already in the aborted state |
| `--single-transaction` (`-1`) | `psql` wraps the whole `-f` file in one `BEGIN` … `COMMIT`. **The file contains no transaction control of its own, and may not:** the static analyser recognises no `begin`, `commit` or `rollback` statement shape, so an unguarded run would autocommit each statement separately. This flag is the only thing that makes the sequence atomic. With `ON_ERROR_STOP=1`, a failure aborts `psql` before the `COMMIT` and the server discards the open transaction |
| `verify-full` with the validated Supabase CA | The same TLS rule as the Slice 0 audit ([§4.2](#m-ops-slice0-audit)): exactly `verify-full`, with a CA file that exists. **There is no downgrade path** — a missing or unvalidated CA file is a refused operation, never a fallback to `require`. Applying role DDL over a connection whose peer was not authenticated is not a lesser version of this procedure |
| A clean, explicitly constructed child environment (`env -i`) | The environment is **built, not inherited.** An inherited `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS`, `PGHOST`, `PGHOSTADDR`, `PGSSLMODE`, `PGPASSFILE`, `SUPABASE_DB_URL` or `SUPABASE_ACCESS_TOKEN` cannot redirect the connection, re-authenticate it, downgrade its TLS or inject session settings, because none of them is in the child's environment at all |
| No connection string and no password in `argv` | The credential travels as `PGPASSWORD` inside that constructed environment. Never `-d 'postgres://…'`, never `--password`, never a `.pgpass` reached through `HOME`. The process table shows no target and no secret |
| The reviewed committed SQL file, directly | `-f supabase/hosted_roles.sql` — the committed bytes. Not a here-document, not a generated plan, not `hosted_role_bootstrap.sh --dry-run` piped into `psql`, not a file edited to skip a statement. The artefact reviewed and the artefact applied must be the same bytes; that is the whole reason the review is worth anything |

**`hosted_role_bootstrap.sh` does not gain an apply mode to enforce this, and will not.** An
apply mode would need a target, a credential and a connection, and this slice deliberately
holds none of the three — adding them to enforce atomicity would trade the property that makes
the tool safe to review for one that `psql` already provides. The tool stays a static
review/dry-run tool. The contract is an operator procedure, and it is proven by rehearsal
rather than by a flag.

**Proven, not asserted.** `supabase/scripts/hosted_bootstrap_rehearsal.sh` extracts the block
above from this document by its `ol:hosted-apply-contract` anchor, checks that it still carries
every element of the contract, and then runs **that invocation** — the documented one, not a
paraphrase of it — against the disposable local database with failures injected, proving that a
statement that fails after an earlier role statement succeeded takes the earlier one with it.
If this documented block and the rehearsal ever disagree, the rehearsal fails.

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

**The rehearsal no longer arranges a shape that makes the file pass**, and that is the point
of it. A freshly reset local database carries `postgres -> origenlab_owner` with `SET` —
byte for byte the shape the hosted `origenlab-v2` catalogue was measured to carry
([`STATUS.md`](STATUS.md) §2.5) — so the file is applied straight onto the real observed
divergence and its own `revoke set option for origenlab_owner from postgres` is what removes
it. `postgres` may revoke only what it granted, so the `supabase_admin`-granted creator-admin
rows survive, and the rehearsal asserts that they do. It then proves the file executes, that
a second application in the same transaction still succeeds with an unchanged post-state, and
that the catalogue is byte-identical after the rollback.

**Rows are proven by equality, never by an absolute.** The digest taken three times around
the two applications counts every row of every table in the seven schemas, and the claim is
that the three counts match — not that they are zero. They are not zero: migration
`20260905230814` seeds the `outbound.send_control` kill-switch singleton, and the CRM import
will add far more. A rehearsal asserting a constant there would be asserting the calendar.

Two negative halves follow: a platform identity given `SET ROLE` on a *runtime* role makes the
bootstrap refuse — the boundary assertion is not one that cannot fail — and the rollback is
shown to restore the local `postgres` `SET` membership the hosted file removed, so the two
files remain distinguishable and the rehearsal leaves nothing behind.

**The rehearsal then tests the application contract itself**, because "the SQL runs" and "the
operator is required to apply it atomically" are different claims and only the first was ever
proven. It extracts the invocation documented above by its `ol:hosted-apply-contract` anchor,
asserts it still carries `--single-transaction`, command-line `-v ON_ERROR_STOP=1`, `-X`,
`--no-psqlrc`, `verify-full`, a CA, a built environment and the committed file — and no
connection string, password or inline SQL in `argv` — and then **runs that invocation** against
the local disposable database. Three localisations, each asserted to apply exactly once, adapt
it to a container that speaks no TLS; every flag the contract turns on is executed verbatim.
The environment it runs in is deliberately hostile: `$HOME` holds a `.psqlrc` that would switch
`ON_ERROR_STOP` off and create a role, and `PGHOST`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS`,
`PGSSLMODE`, `PGPASSFILE` and `PSQLRC` are all exported pointing somewhere wrong. Failures are
then injected two ways — a statement that fails *after* a plain `create role` succeeded, and a
membership planted so the file's own boundary assertion fires at the very end, after the
convergence has already been performed — and each run must leave the role catalogue
**byte-identical** to where it started. Removing `--single-transaction` from the documented
block turns those checks red and names the damage, which is what makes them worth having; the
psqlrc check carries its own negative control, since the same startup file *does* run, and
commits outside the transaction, when `-X` is dropped.

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

## 8. Gmail capture (Phase 4a) and its recovery

The capture is the Render Cron Job `origenlab-gmail-sync`: `uv run --no-sync origenlab-worker gmail-sync`
from `apps/worker`, every 10 minutes ([`ARCHITECTURE.md`](ARCHITECTURE.md) §8). A run reads Gmail
`history.list` from `comms.mailbox.history_id` (a message that arrived, or that gained `SENT` or
`INBOX`: a draft sent later, a message moved out of spam), skips drafts, spam and trash, uploads each new
message's `.eml` to the private bucket `mail`, commits the message (+ participants, attachment
metadata) and its `pending` `gmail_message` evidence one message at a time, and only then moves
the cursor. Campaign copies (outbound with `List-Unsubscribe`) are kept as messages without evidence
(owner-approved 2026-10-04).

The worker's database login has no direct write in `crm.*` or `outbound.*` except `INSERT` on
`outbound.campaign_reply`, the designed 4c reply-proposal lane (4a never writes it). It may EXECUTE
exactly one closed-list privileged function, `outbound.add_contact_control`, whose worker branch
accepts only proven single-recipient Batch-A no-such-user evidence. Every connect proves this from
the inside and refuses: any role membership; a port other than 5432 (6543 is refused by name); any
other `crm`/`outbound` write privilege, including column-level grants, `TRIGGER`, and view and
materialized-view writes; and any other `SECURITY DEFINER` function the worker can
execute (extension-owned and trigger functions and functions in schemas without `USAGE` are not
counted; nothing is skipped by schema name). **The first hosted `--init --dry-run` is the proof**
that Supabase's own extension functions do not trip the definer probe; read its log line before
anything else (§8.7). If a connect is refused and the log's `error` code does not say enough, §8.4 has
the queries that show what the worker's login can actually reach.

**Known limit (R7).** A draft that is later sent, and a message moved out of spam, are captured:
history also lists `labelAdded`, and a message that gained `SENT` or `INBOX` is read again. Label
*removals* and every other label change (a star, a custom label, a move to Trash) are not tracked, and
the labels already stored in `comms.message.labels` are not updated.

### 8.1 Reading a run

One JSON line per run in the cron's Render log — `event`, `mode`, `exit`, the counters below, `error`,
`elapsed_ms`, `max_rss_mb`. It never carries a subject, an address, a Gmail id or an exception's text;
`error` is a fixed code or a class name, by what failed. A Storage error is its code
(`stored_object_differs`, `storage_check_http_403`, `storage_head_http_500`,
`storage_too_large_below_cap`); a Gmail error is its kind (`http_503`, `network`, `invalid_json`,
`too_many_pages`, or an authorization kind below); a database error is its class
and SQLSTATE (`UniqueViolation:23505`, `OperationalError`); a configuration refusal is its code
(`database_url_has_options`); anything else is its class name only.

**Counters.** `seen` messages listed for the window; `stored` messages written to `comms.message`
(a dry run: **would** store; `evidence` stays 0); `evidence` pending evidence rows created;
`bulk_sends` campaign copies — a **subset of `stored`**, kept without evidence; `duplicates` already
captured, refused by the unique keys; `skipped_draft`/`_spam`/`_trash` never downloaded; `gone`
deleted from Gmail between the list and the read; `too_large` messages over 50 MiB (by Gmail's estimate or by the bytes actually downloaded), kept as
`parse_failed` rows without an `.eml` but **counted only here, not in `parse_failed`**;
`parse_failed` messages whose `.eml` is kept and that produced no evidence.

**Modes.** `history` (normal), `resync` (Gmail expired the cursor; the run re-listed from
`last_synced_at − 1 day`), `init_baseline`, `init_resumed` (an existing cursor kept), `locked`,
`paused`, `not_authorized`, `wrong_account` (a mode, not an error code: the token is for another
account; exit 2), `no_mailbox`, `already_initialized`, `config_refused`,
`usage` (a bad command line), `failed` (before a mode was known). `history`, `resync` and `init`
carry the suffix **`_dry_run`** (`history_dry_run`, `resync_dry_run`, `init_dry_run`) when `--dry-run`
is on; a dry run writes nothing and never moves the cursor. A run that fails mid-way reports the
stage it reached (`start`, `profile`, `init`).

| `mode`, exit | Meaning | Action |
|---|---|---|
| `history`, 0 | normal run | none |
| `resync`, 0 | Gmail had expired the cursor (about a week); the run re-listed from `last_synced_at − 1 day` and refused what it already had | none |
| `locked`, 0 | another run holds the lock | none, unless it lasts 30 min (§8.4) |
| `locked`, 1, `locked_by_stuck_session` | the lock is held by a session of the worker's login that is older than 30 minutes and is **not idle** (`idle in transaction`, `active`): it is never ended automatically | §8.4 |
| `paused`, 0 | `ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED` is not `true`; **a paused cron opens no database session** | intended? |
| `not_authorized`, 0 | the mailbox is `unauthorized` or `revoked` | §8.3 |
| any, 1 | Gmail, the database or Storage failed mid-run; the cursor did not move. `error` is the code or class (see above): `terminated` (Render's SIGTERM), `interrupted` (Ctrl-C), `stored_object_differs` (§8.5), `storage_too_large_below_cap` (Storage refused a message Gmail sizes under 50 MiB: the bucket's or the project's upload limit is below 60 MB — raise it; the run must not turn evidence into a `too_large` row), `empty_history_id` (Gmail returned no history id: nothing was captured, retry), `UniqueViolation:23505` and other database classes | Render e-mails it; the next run retries the same window |
| `init_dry_run` or `init`, 1, `storage_check_http_403` | the Storage pre-flight (`--init`, `--init --dry-run`) was refused listing bucket `mail`: the S3 key id or secret is wrong, or the key was deleted. It is exit **1**, not 3 | fix `ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` (§8.7 step 4) and rerun |
| `init_dry_run` or `init`, 1, `storage_check_http_404` | the pre-flight found no bucket `mail` (or the endpoint or region points at another project) | create the private bucket `mail`, or correct `ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT` / `_REGION`, and rerun |
| any, 2 | `invalid_grant` (consent revoked; the mailbox is now `revoked`), `scope_not_readonly`, `token_refresh_failed` (Google refused the OAuth client or the refresh request: a wrong or deleted client id or secret, or a malformed token), `unauthorized` (a 401 that survived a fresh token) — the last two are client problems and do not revoke the mailbox | §8.3 |
| `wrong_account`, 2 | the consent is not contacto@'s | §8.3 |
| `usage`, 3 | the command line is not `gmail-sync [--init] [--dry-run]` | fix the cron's command |
| `resync`, 3, `no_resync_baseline` | the cursor expired and `last_synced_at` is empty: resyncing from "now" would skip mail silently, so the run stops | set `last_synced_at`, as `origenlab_owner`, to the newest `comms.message.internal_date` for the mailbox, or to the go-live time if there is none. Earlier is always safe; later loses mail; never `now()` |
| `no_mailbox`, 3 | there is no `comms.mailbox` row for contacto@origenlab.cl: the fase-1 hosted data load has not created it (or has not run) | run the fase-1 load ([`MIGRATION.md`](MIGRATION.md)); the worker never creates the row |
| `config_refused`, `already_initialized`, 3 | a setting is refused (the code names it), or `--init` was left in the command / the mailbox is already authorized | fix the setting or the command (§8.3 for `already_initialized`) |

The dashboard says «Sincronización de correo detenida desde HH:MM» when the mailbox is `revoked`
(or paused after it ran) and «atrasada» when no run completed for 30 minutes.

### 8.2 Pause and resume

- Pause: `ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED=false` on the cron; the next run logs `paused` and opens
  no database session. The banner turns «atrasada» after 30 minutes — expected.
- Resume: set it back to `true`. The capture continues from the cursor; after more than about a
  week Gmail has expired it and the run resyncs by itself.
- Stop everything: Render → the cron job → Suspend.

### 8.3 Consent revoked, wrong account, rotation

1. From `apps/worker`, as contacto@: `uv run --group bootstrap python scripts/gmail_readonly_authorize.py --client-secrets <client file> --out <a path outside the repository>` (the script refuses a path inside it).
2. Replace `ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN` (and the client values if the client changed) on the cron.
3. What happens next depends on the mailbox's state, which the banner and the last log line tell you:
   - **`revoked`** (the run logged `invalid_grant`; the banner says «detenida»): set the cron's command to
     `uv run --no-sync origenlab-worker gmail-sync --init`, Trigger Run (`init_resumed`: the cursor is kept,
     nothing is skipped), then set the command back.
   - **Still `authorized`** (a planned rotation, or `wrong_account` — neither revokes the mailbox): do **not**
     use `--init`. It answers `already_initialized` (exit 3), by design, so a forgotten `--init` cannot hide.
     The next scheduled run (or Trigger Run) simply uses the new token; expect `"mode": "history", "exit": 0`.
     If you do want the `--init` pre-flight (`--init --dry-run` checks Gmail, the probe and the bucket), the
     mailbox must be `revoked` first — the owner step is, as the project's `postgres` login:
     `set role origenlab_owner; update comms.mailbox set authorization_state = 'revoked', updated_at = now() where address_norm = 'contacto@origenlab.cl';`
     then `--init` as above (the cursor is kept, so nothing is skipped). Never clear `history_id`.

Database password: `\password origenlab_worker` as the project's `postgres` login, then update
`ORIGENLAB_WORKER_DATABASE_URL`. S3 key: create a new key, update the two variables, delete the old
one. Record every rotation (§13).

### 8.4 A stuck lock

`locked` (exit 0) for 30 minutes means a holder session outlived its run. The next run ends a holder
that has been **idle** for 30 minutes by itself. A holder of the worker's login that is past 30
minutes in any other state (`idle in transaction`, `active`) is never ended — it may be mid-write — and
it does not free itself either, so the run reports it: exit 1, `locked_by_stuck_session`, until you
act. To look, as the project's `postgres` login:

```sql
select a.pid, a.state, a.state_change, a.application_name
  from pg_locks l join pg_stat_activity a using (pid)
 where l.locktype = 'advisory' and a.usename = 'origenlab_worker';
```

If the holder is `idle in transaction` or `active` for far longer than a message takes (minutes), check
Render's log for a run that is genuinely alive; if none is, end it: `select pg_terminate_backend(<pid>);`.
Every write is idempotent and the cursor moves only after the window, so ending a dead run loses nothing.
Whether Supavisor's session mode releases advisory locks on disconnect is an owner check (§8.7); the
stale-holder rule covers either answer.

**Why did the connect refuse (`worker_can_execute_security_definer`, `worker_can_write_crm_or_outbound`,
`worker_role_member_of_privileged_role`, `worker_grant_missing`, `worker_policy_missing`)?** The probe
runs on every connect and its code names the check, not the object. To see what it saw, as the
project's `postgres` login:

```sql
-- executable SECURITY DEFINER functions (expected: outbound.add_contact_control only)
select n.nspname || '.' || p.proname
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where p.prosecdef and n.nspname not in ('pg_catalog', 'information_schema')
   and has_function_privilege('origenlab_worker', p.oid, 'EXECUTE')
   and has_schema_privilege('origenlab_worker', n.oid, 'USAGE')
   and p.prorettype not in ('trigger'::regtype, 'event_trigger'::regtype)
   and not exists (select 1 from pg_depend d
                    where d.classid = 'pg_proc'::regclass and d.objid = p.oid and d.deptype = 'e')
 order by 1;

-- role memberships and attributes (expected: no rows; all five attributes false)
select m.rolname from pg_roles m
 where m.rolname <> 'origenlab_worker' and pg_has_role('origenlab_worker', m.oid, 'MEMBER') order by 1;
select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication
  from pg_roles where rolname = 'origenlab_worker';

-- crm/outbound write privileges (expected: only outbound.campaign_reply | INSERT)
select n.nspname || '.' || c.relname, p.privilege
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 cross join (values ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), ('TRIGGER')) as p(privilege)
 where n.nspname in ('crm', 'outbound') and c.relkind in ('r', 'p', 'v', 'm')
   and (case when p.privilege in ('INSERT', 'UPDATE')
             then has_any_column_privilege('origenlab_worker', c.oid, p.privilege)
             else has_table_privilege('origenlab_worker', c.oid, p.privilege) end)
 order by 1, 2;

-- the policies the capture writes through. The result must include these 8 (more rows are normal):
--   comms.mailbox: origenlab_worker_select, origenlab_worker_update
--   comms.message: origenlab_worker_select, origenlab_worker_insert
--   comms.message_participant: origenlab_worker_insert; comms.attachment: origenlab_worker_insert
--   evidence.source_record: origenlab_worker_select, origenlab_worker_insert
select schemaname, tablename, policyname from pg_policies where 'origenlab_worker' = any(roles) order by 1, 2, 3;
```

### 8.5 StorageConflict: an object with different bytes under the same key

The capture never replaces an object. If `mail/contacto@origenlab.cl/<yyyy>/<mm>/<gmail id>.eml`
already holds bytes that differ from what Gmail returns for that message, the run fails (exit 1,
`stored_object_differs`) and **every later run stops at the same message** — the cursor cannot move
past it — until someone acts. Gmail's raw bytes for a message never change, so the stored object is
the anomaly (an interrupted or corrupted upload, or a key reused by hand). The message has no
`comms.message` row yet (the row is written after the upload), so no row names either version.

All of this runs locally from `apps/worker` with the cron's environment exported in a shell from the
password manager (never a file in the repository). The dashboard can inspect an object but cannot copy
one, so the copy needs the S3 key.

1. **Do not delete the object.** Fetch the same message with `format=raw` through the worker's own
   read-only client and note its size and hash (nothing is printed or written but those two):

```bash
uv run python - <<'EOF'
import hashlib, os
from origenlab_worker.cli import config_from_env
from origenlab_worker.gmail_client import GmailReader

raw = GmailReader(config_from_env(os.environ, need_storage=False).gmail).raw("<gmail id>").raw
print(len(raw), hashlib.sha256(raw).hexdigest())
EOF
```

   Then compare with the stored object's size and hash (the same snippet, reading
   `StorageConfig.from_env(os.environ).client().get_object(Bucket="mail", Key="<key>")["Body"].read()`).
2. **Keep both.** Copy the existing object to a quarantine key in the same bucket
   (`quarantine/<original key>.<first 8 hex of its sha256>`) with the S3 key — `aws s3 cp --endpoint-url
   <the S3 endpoint> s3://mail/<original key> s3://mail/<quarantine key>` with `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION` set from the cron's `ORIGENLAB_WORKER_STORAGE_S3_*`
   values, or boto3: `StorageConfig.from_env(os.environ).client().copy_object(Bucket="mail",
   Key="<quarantine key>", CopySource={"Bucket": "mail", "Key": "<original key>"})`. Confirm the copy's
   hash equals the original's, and record the key, both hashes and the reason in the
   rotation/incident log (§13).
3. **Free the original key** (Supabase dashboard → Storage → `mail`, or `delete_object` with the same
   key). The quarantine copy now preserves the original bytes, so nothing is lost either way: the next
   run uploads what Gmail returns now, the message commits, and the window completes. If the stored
   object was the right one and it is *Gmail's* bytes that changed, that is an incident: record it first
   (both hashes, the message's date), then free the key the same way — the quarantine copy is what keeps
   the earlier bytes.

### 8.6 Shadow week

Daily during the first week, as the owner, with the cron's environment exported in a shell from the
password manager (never a file in the repository), from `apps/worker`:
`uv run python scripts/shadow_reconcile.py --sqlite <V1 emails.sqlite> --since <go-live, UTC> --out <directory outside the repository>`.
Exit 0: every message V1 ingested from contacto@ since go-live is in `comms.message` or explained
(draft, spam, trash). Exit 1 lists the rest in `<out>/missing.csv` (V1 id, folder, date — no subject,
no address); the script splits missing ids into sent and received by V1 folder. A V1 row whose
`date_iso` is missing or unparseable cannot be placed against go-live: it is printed as `undated=N`
and also makes the exit code 1 (a `date_iso` without an offset is read as UTC), unless its folder
explains it (draft, spam, trash) or its id is at or below `--since-v1-id`. V1's `emails` table has no
ingestion timestamp, so right before `--init` read V1's highest id (`select max(id) from emails` on the
SQLite file, read-only) and pass it as `--since-v1-id` on every run of the week; without it every undated
row of the lane counts, and one historical row fails the gate forever. The undated ids are written to
`<out>/undated.csv` (V1 id, folder, date text). The script needs the
database and Gmail settings only; it never reads the S3 secret.

The week must also confirm one thing the code cannot: that mail **sent from the Gmail web UI**
(composed as an autosaved draft) is captured. The capture handles both ways Gmail can do it — a new
`messageAdded` with its own id, or the same draft id gaining `SENT` (`labelAdded`, which history now
lists) — so a Sent-side entry in `missing.csv` would mean a third behaviour.

### 8.7 First-time setup and owner checks

Nothing here is code. Values go to the password manager and to Render's environment — never into the
repository, a file under it, a log or a chat. In this section **«Cloudflare Worker (dashboard proxy)»**
is `apps/dashboard-proxy`; **«`apps/worker` cron»** is the Render Cron Job `origenlab-gmail-sync`.

**Prerequisite: the fase-1 hosted data load must have created the `comms.mailbox` row** for
contacto@origenlab.cl ([`MIGRATION.md`](MIGRATION.md)). Without it every run answers `no_mailbox`
(exit 3): the worker never creates the row.

1. **Google Cloud** (owner, as Workspace admin), in the project of the dashboard's Internal OAuth
   client: enable the **Gmail API**; OAuth consent screen (already *Internal*) → Data access → add
   `https://www.googleapis.com/auth/gmail.readonly`; Credentials → Create OAuth client ID → type
   **Desktop app**, name `origenlab-gmail-capture` → download its JSON, outside the repository.
2. **Consent as contacto@** (owner, local): `cd apps/worker && uv sync --group bootstrap && uv run
   --group bootstrap python scripts/gmail_readonly_authorize.py --client-secrets <client JSON> --out
   <a mode-600 file outside the repository>` (the script refuses a path inside it). Sign in as
   contacto@origenlab.cl; expect `written … (mode 600) for contacto@origenlab.cl — scope gmail.readonly`.
   The token is written to that file only — never printed, never in CI. Move the client JSON into the
   password manager and delete the download.
3. **`origenlab_worker` password on hosted** (owner): the role has none until you set it. Connect as the
   project's `postgres` login over the session pooler with `sslmode=verify-full` and the Supabase CA (as
   for the `origenlab_api` password, §1.2), then `\password origenlab_worker` — a hidden prompt; psql
   sends only the SCRAM verifier. Generate the password in the password manager.
4. **Storage** (owner, Supabase dashboard): Storage → New bucket `mail`, **Public off**, file-size limit
   **at least 60 MB**; Storage → Settings → raise the project's upload limit to at least 60 MB (the
   capture refuses messages over 50 MiB itself; a limit below that makes runs fail with
   `storage_too_large_below_cap`). Storage → Settings → S3 Connection → enable → New access key (name
   it for the capture) → copy the key id and secret (shown once), the endpoint and the region.
   **Owner check:** S3 keys exist on Pro and are project-wide (every bucket, bypassing Storage RLS). If
   they are not available, stop here: 4a has no other Storage credential path, by design.
5. **The shape of each value** (Render environment of the `apps/worker` cron; all of them secrets):

   | Variable | Shape |
   |---|---|
   | `ORIGENLAB_WORKER_DATABASE_URL` | `postgresql://origenlab_worker.<project ref>:<URL-encoded password>@<session pooler host>:5432/postgres` — port 5432, no query string (6543 and any option are refused) |
   | `ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST` | the pooler host exactly as named in the URL (a hostname, never an IP) |
   | `ORIGENLAB_WORKER_DATABASE_CA_PEM` | the text of the Supabase CA certificate (PEM, not a path); the one the API already uses |
   | `ORIGENLAB_WORKER_GMAIL_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` | the three values in step 2's file |
   | `ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT` | `https://<project ref>.supabase.co/storage/v1/s3` |
   | `ORIGENLAB_WORKER_STORAGE_S3_REGION` | the project's region, e.g. `sa-east-1` |
   | `ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` | the key from step 4 |
   | `ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED` | `false` until step 8 |

6. **Payload parity** (owner, local, **before `--init`**): export the same variables in a shell from the
   password manager (the S3 values are not needed), then from `apps/worker`: `uv run python
   scripts/payload_parity.py --out <directory outside the repository>`. Expect `staged=92 differing=0`,
   or only `now_trash` / `now_spam` / `gone_from_gmail` (messages moved or deleted since staging).
   Any computed key in `parity.csv` (`sender`, `sent_at`, `documents`, `staged_key_set`) means: stop and
   fix the capture before go-live.
7. **Create the `apps/worker` cron** (owner): Render → New → Blueprint sync of `render.yaml` (or New → Cron
   Job from the repository with the fields that file declares: name `origenlab-gmail-sync`, Python,
   Oregon, root `apps/worker`, build `uv sync --frozen --no-dev`, command `uv run --no-sync
   origenlab-worker gmail-sync`, schedule `*/10 * * * *`). Set the variables of step 5; failure e-mails
   on. **Owner check:** note Render's documented behaviour when a run is still active at the next
   schedule (skip or queue) in STATUS — the advisory lock covers either.
8. **Pre-flight, then go-live** (owner): set the cron's command to `… gmail-sync --init --dry-run`,
   Trigger Run: the log line must say `"mode": "init_dry_run", "exit": 0` (Gmail token, scope and
   account, the database probe, the bucket — nothing written). This hosted run is also the proof that
   Supabase's own extension functions do not trip the definer probe. A refusal is a code: exit 3 names a
   refused setting (`worker_policy_missing`, `login_not_worker`); exit 1 `storage_check_http_403` is a
   wrong S3 key and `storage_check_http_404` a missing bucket; exit 2 `wrong_account` / `scope_not_readonly` means redo step 2. For a refused probe,
   §8.4 lists what the login can reach. Then the command `… gmail-sync --init`, Trigger Run:
   `"mode": "init_baseline"`; write down the run's UTC time (the shadow week's `--since`) and V1's highest `emails.id` at that moment (its `--since-v1-id`, §8.6). Set the
   command back to `uv run --no-sync origenlab-worker gmail-sync` — a leftover `--init` exits 3 on every
   run, so it cannot go unnoticed — and set `ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED=true`. The next run logs
   `"mode": "history", "exit": 0`.
9. **Deploy the Cloudflare Worker (dashboard proxy)** (operator): the API and the dashboard redeploy on
   merge; the proxy does not. From `main`: `cd apps/dashboard-proxy && npm run validate && npx wrangler
   deploy`. Without it the sync banner stays silent (its read is not reachable). Check: `GET
   /api/v2/workspace/mail-sync` answers 401 without a session; signed in, no banner while the state is `ok`.
   Send one invented test mail to contacto@ and see a new `gmail_message` group count in Revisión within
   10 minutes.
10. **Shadow week** (owner): §8.6 daily. Seven days of exit 0 is the slice-4 coverage gate for 4a
    ([`MIGRATION.md`](MIGRATION.md) §5.1 as amended); record the result in STATUS §2.7.49 with the counts.
11. **Size the instance** — see the sizing note below; do not downsize on a quiet week alone.

**Deploy order** (agrees with the steps above): (1) Google client, consent, password, bucket and S3 key
(steps 1–4); (2) create the `apps/worker` cron, paused (steps 5 and 7); (3) payload parity (step 6), then
`--init --dry-run`, then `--init` (step 8); (4) enable the switch (step 8); (5) deploy the Cloudflare
Worker (dashboard proxy) (step 9) — the banner is silent until then, so it may come earlier or later
without harm.

**Owner checks the code cannot settle:** Render's behaviour when a run is still active at the next
schedule (skip or queue; the advisory lock covers either); that Storage S3 keys exist on Pro and cover
every bucket; a project upload limit of at least 60 MB; whether Supavisor session mode releases advisory
locks on disconnect; the first hosted `--init --dry-run` (the definer probe).

**Sizing.** `render.yaml` says `plan: standard` and that stays. A normal run peaks around 36 MB, but
memory grows with the message: a message near `MAX_RAW_BYTES` (50 MiB) peaks at about **451 MB** RSS, and
one around 24 MiB at about 238 MB. On Starter (512 MB) a message that large risks an out-of-memory kill,
which stalls **every** later run on that message (the cursor cannot pass it). Downsizing is safe only if
`MAX_RAW_BYTES` (`apps/worker/src/origenlab_worker/gmail_sync.py`) is lowered to match what the smaller
plan can hold. The build is heavy: `apps/worker` installs email-pipeline's OCR stack (onnxruntime,
opencv, rapidocr, pymupdf; about 650 MB venv) at build time but never imports it at run time. Slice 8
moves that seam.

### 8.8 Recovery

| Symptom | Action |
|---|---|
| Banner «atrasada» | read the last run's log line (§8.1) before touching anything |
| History id rejected by Gmail (too old) | automatic: the run logs `resync`. Safe: `(mailbox, provider_message_id)` is unique, replays insert with `ON CONFLICT DO NOTHING`, a resync rewrites nothing |
| Duplicate-looking messages | expected when RFC 822 ids repeat; identity is the provider id ([`DATA.md`](DATA.md) §6). Do not deduplicate by RFC 822 id |
| Message missing after a resync | record it as absent; it is evidence, not a failure to repair |
| `.eml` missing in Storage | the row keeps `eml_sha256`; fetch `format=raw` again and upload under the same key |
| `stored_object_differs` (a different object under the key) | §8.5 |
| `parse_failed` rows | the `.eml` is kept (except `too_large`); no evidence was created; nothing to repair in 4a |

**Never repair a sync by editing `comms.message` by hand.**

### 8.9 Automatic email → case links (R1/R2)

The API applies rules R1 (same Gmail thread) and R2 (same quote number) by itself, every
`ORIGENLAB_V2_AUTO_MAIL_RULES_INTERVAL_SECONDS` (default 300), while an admin has switched it on
([`DOMAIN.md`](DOMAIN.md) §3.6.6; `apps/api/src/origenlab_api/v2/mail_rules_auto.py`).

- **On / off:** Revisión → «Acciones automáticas» → «Vincular correos automáticamente» →
  «Activar» / «Detener», with a reason. Off until an admin turns it on. The newest
  `set_auto_mail_rules` receipt is the state; it survives restarts and deploys.
- **Hard stop** (dashboard unreachable): set `ORIGENLAB_V2_AUTO_MAIL_RULES_INTERVAL_SECONDS=0` on
  the API and redeploy; the timer is never started. `ORIGENLAB_V2_COMMANDS_ENABLED=false` also
  removes it, with every other command.
- **A wrong link:** «Deshacer» on the row in «Acciones aplicadas» (marked «automática»). An undone
  email is never linked again by the rules.
- **«Activo, sin actuar»:** the admin who switched it on is no longer an active admin; another
  admin switches it on in their own name.
- The tab's «Última pasada» line is this API process's memory: it is empty for up to one interval
  after a deploy.

### 8.10 Quote PDFs filed in Drive (`drive-file`)

Right after each capture, the cron runs `origenlab-worker drive-file`
(`apps/worker/src/origenlab_worker/drive_filing.py`): every quote revision recorded from a
captured email — «Registrar cotización», «Nueva revisión», or rules R3/R4 — gets its PDF put in
`Cotizaciones/Casos/<case folder>` in contacto@'s Drive, with the layout, names, double
verification and legacy-folder refusal of the 2026-09-26 case archive (STATUS §2.7.24). Each
filed document is recorded as an `evidence.source_record` of kind `drive_file`; the case card
links it. Nothing is renamed, moved or deleted in Drive, and no CRM row is written.

**First-time setup (owner, once):**

1. The Drive consent: the values in `~/secrets/origenlab-drive-credentials.json` (the file the case
   archive used; else run `apps/api/scripts/authorize_drive_user.py --expected-email
   contacto@origenlab.cl`). Its `client_id`, `client_secret` and `refresh_token` become
   `ORIGENLAB_WORKER_DRIVE_CLIENT_ID`, `_CLIENT_SECRET`, `_REFRESH_TOKEN` on the cron
   `origenlab-gmail-sync`. The token must carry exactly the `drive` scope; anything wider is
   refused (`scope_not_drive`).
2. `ORIGENLAB_WORKER_DRIVE_CASOS_FOLDER_ID` = the id of `Cotizaciones/Casos` (from its Drive URL);
   `ORIGENLAB_WORKER_DRIVE_PROTECTED_FOLDER_IDS` = the ids of `Pendientes` and `Enviadas`, comma
   separated.
3. The cron's **Start Command**: `uv run --no-sync origenlab-worker gmail-sync && uv run --no-sync
   origenlab-worker drive-file` (`render.yaml` declares it; a service created by hand keeps its
   own until changed).
4. Once, from the laptop, record the case archive's existing links so earlier revisions keep their
   folders: `uv run origenlab-worker drive-ledger-import <…>/archive_links.jsonl --dry-run`, then
   without `--dry-run`, with the cron's database values in the environment.
5. `ORIGENLAB_WORKER_DRIVE_FILING_ENABLED=true`. The next run's log line (`"event":
   "drive_file"`) reports `filed`, `reused` and `refused` with stable codes.

**Pause:** `ORIGENLAB_WORKER_DRIVE_FILING_ENABLED=false` — the step logs `paused`, opens nothing.

| Refusal code | Meaning / action |
|---|---|
| `scope_not_drive`, `invalid_grant` (exit 2) | the consent is wrong or revoked: redo step 1 |
| `wrong_account` | the token is not contacto@: redo step 1 with the right account |
| `casos_invalid` | the folder id is not the tagged `Casos` root: check step 2 |
| `case_name_taken`, `case_key_conflict`, `document_filed_elsewhere` | Drive disagrees with the CRM about a case: look at the case's folder by hand; nothing was written |
| `attachment_not_in_eml`, `eml_hash_mismatch` | the stored email does not hold that PDF: leave it, file by hand |
| `drive_http_*`, `storage_*` | transient: the next run retries |

### 8.11 Mail triage (`origenlab-mail-triage`)

The Render **Background Worker** `origenlab-mail-triage` runs `origenlab-worker triage-worker`: a
Procrastinate worker on queue `triage`, whose jobs live in the `procrastinate` schema of the same
database ([`ARCHITECTURE.md`](ARCHITECTURE.md) §8, D2). It never touches Gmail and never writes
`crm.*`. Every minute its periodic sweep finds captured messages of the last 14 days without a
reading of the current version and defers one `triage_message` job each. A job:

1. reads the message's `.eml` from the `mail` bucket (read only);
2. runs the cheap rules (`triage_rules.py`): our own mail, bounces, automatic replies, invitations,
   bulk and no-reply mail stop here; purchase orders, lost signals, CN follow-ups, quote requests
   and any other person go on;
3. matches product candidates in `catalog.product` (exact model number, then the Spanish
   full-text index);
4. only for the classes that go on, and only when the model stage is enabled, asks Claude for the
   products, the urgency, a Spanish summary and the **stage** the case is in after this email — in
   the CRM's own keys, the Tablero's columns (`lead` «Solicitada» … `negotiating` «Conversación»,
   `won`, `lost`), or `not_a_case` / `unclear` — given the cases the email's Gmail thread is already
   linked to and their current stage. Each proposed move is checked against the API's transition
   table (`transition_allowed`); nothing moves a case: the stage stays an operator's
   «Cambiar estado» (`triage_model.py`);
5. records one `evidence.assertion` of kind `message_triage` (`value_norm = triage:v1`) and one
   `product_mention` per product, all `unresolved`, all `on conflict do nothing`.

**First-time setup (owner, once):**

1. Apply `20261006180000_slice4_procrastinate_triage_queue.sql` and
   `20261006180100_slice4_triage_assertion_kinds.sql` to the hosted project through the reviewed
   migration path (§4). The worker refuses to start without the queue's grants and policies
   (`worker_grant_missing`, `worker_policy_missing`).
2. Render → New → Background Worker from this repository, or sync the `render.yaml` blueprint
   (service `origenlab-mail-triage`, plan starter). Copy the database and Storage values from the
   cron `origenlab-gmail-sync` (`ORIGENLAB_WORKER_DATABASE_*`, `ORIGENLAB_WORKER_STORAGE_S3_*`).
   No Gmail or Drive credential belongs on this service.
3. Without the model first: `ORIGENLAB_WORKER_TRIAGE_ENABLED=true`, leave
   `ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED` unset. Every reading then says `model_state: off`. Check
   the log lines (`"event": "triage_sweep"`, `"event": "triage_message"`) and the classes.
4. The model: `ORIGENLAB_WORKER_ANTHROPIC_API_KEY` (a key for this worker alone) and
   `ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED=true`. `ORIGENLAB_WORKER_TRIAGE_MODEL` overrides the
   default `claude-opus-5-5` (for example `claude-haiku-4-5`, cheaper and without effort or the
   refusal fallback).

**Backfill or smoke test without the queue:** with the same environment on a laptop,
`uv run origenlab-worker triage-once --dry-run` counts what is pending;
`triage-once --since-days 60 --limit 500` triages it in one pass and prints the counts by class
and by model state.

**Pause:** `ORIGENLAB_WORKER_TRIAGE_ENABLED=false` — the worker idles and logs `paused` hourly
(Render restarts a worker that exits). Model only: `ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED=false`.
Stop everything: Render → the worker → Suspend. Waiting jobs stay in `procrastinate_jobs`; deleting
them loses nothing, because the next sweep re-enqueues whatever has no reading.

**Review (every operator, daily):** Revisión → «Correos (sugerencias)» lists the readings the
model was asked for — never the noise — with the email, the products, the suggested stage and the
stage of the case the Gmail thread is on. «Aprobar» records the verdict and, when the thread is on
exactly one case and the board can walk there, moves it to the suggested stage («Aprobar y pasar a
…»); «Ganada» is never moved this way (it needs «Marcar ganada» with its revision). «Corregir»
records the right class, stage, intent or products (and moves the case when a stage is chosen);
«Rechazar» records that the reading was wrong. A note is optional and is what makes a correction
useful later. Every verdict is kept (`evidence.triage_review`, append-only; a second verdict
supersedes the first without rewriting it). `scripts/triage_eval.py` measures the triage against
them before a rule change.

**Re-triage after a rule change:** bump `TRIAGE_VERSION` in `triage_rules.py`. New readings are
stored beside the old ones (`triage:v2`), never over them.

| Log value | Meaning / action |
|---|---|
| `model_state: ran` | the model read the message |
| `model_state: off` / `not_needed` | the model stage is disabled / the rules settled it |
| `model_state: model_refused`, `truncated`, `not_json`, `closed_value_unknown`, `summary_missing` | the answer was unusable; recorded, not retried |
| `model_state: api_error_4xx` | a permanent API refusal (bad request, key revoked); recorded, not retried — check the key |
| a job failing with `ModelUnavailable` | network, 429 or 5xx: retried 5 times over about 17 minutes, then re-enqueued by the sweep |
| `outcome: storage_error` | the `.eml` could not be read; nothing written, the next sweep retries |
| `worker_*` refusal at start | the session is not exactly `origenlab_worker` with the queue and triage grants: fix the role, never widen it |

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
| Database backup — production posture per §4.3: **Supabase Pro daily backups, RPO 24 hours; PITR declined initially** | daily | restore drill |
| Independent logical `pg_dump` | before and after every material data-bearing cutover (§4.3) | `pg_restore --list` and a row-count reconciliation against the source |
| **Independent Storage bucket backup** | daily | bucket restore drill |
| Cold archive (SQLite, PST, Wave 1A, final V1 dump) | once, then immutable | `sha256sum -c` against the manifest on **both** copies |

**Database backups do not include Storage objects.** A restore drill that only
restores the database is not a restore drill.

Drill procedure:

1. Restore the database to a scratch project from a chosen daily backup (or from the
   independent `pg_dump` of §4.3 — drill both sources at least once).
2. Restore the bucket backup into that project's Storage.
3. Verify: the 37 tables exist; row counts are plausible; a sample quotation
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
| Gmail capture late or stopped | dashboard banner when no run completed for 30 min or the mailbox is `revoked`; Render's failure e-mail on any non-zero exit | investigate |
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
- Storage access from FastAPI uses a current `sb_secret_...`
  key from server environment only, and **only for the Storage API**. Treat
  that key as what it is: it resolves to `service_role` and bypasses RLS, so
  the restriction is a rule of use, not a scope of the key. The worker's Gmail
  capture holds the one Storage S3 access key
  ([`ARCHITECTURE.md`](ARCHITECTURE.md) §7); it is project-wide and bypasses
  Storage RLS, so it lives only in that cron's Render environment.
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
  The Gmail capture holds a separate `gmail.readonly` refresh token from a
  Workspace Internal OAuth client; every refresh re-checks the scope, so V1's
  full-scope desktop token cannot stand in for it.
- Rotation: rotate a key by adding the new one, deploying, then removing the
  old one. Record every rotation. **[OPEN]** rotation cadence; recommended
  default is annually and immediately on any suspected exposure.
- If a secret is ever committed, treat it as compromised: rotate first, then
  clean history.

## 14. Catalog and quoting inputs (catalog 1a)

Built 2026-10-05, **not applied to any hosted project and not deployed**
([`STATUS.md`](STATUS.md) §2.7.51). Everything below that touches a database targets a
disposable cluster or the local clean room; hosted loading is not available
(§14.5).

### 14.1 Switch and exchange-rate credentials

- **`ORIGENLAB_V2_QUOTING_ENABLED`** (default `false`) mounts `/v2/catalog/*` and the catalog
  commands; off, they answer 404. It needs a V2 database. Turn it on only after the catalog
  migrations are applied to that database.
- **Banco Central (BDE) credentials**: `ORIGENLAB_V2_BDE_USER`, `ORIGENLAB_V2_BDE_PASSWORD`,
  `ORIGENLAB_V2_BDE_SERIES_USD` (default `F073.TCO.PRE.Z.D`) and `ORIGENLAB_V2_BDE_SERIES_EUR`
  (default empty: the euro series is not confirmed from an official source, so BDE declines EUR
  and mindicador answers). Without a user and password, mindicador is the only source.
- **The BDE web service takes the user and password in the URL query string.** No proxy, load
  balancer, CDN or access log between the API and `si3.bcentral.cl` may record that URL, and
  none must be added without checking. The API silences its own HTTP client's request logging
  (`quiet_http_logs`); that is the only guard. The BDE response format has not been verified
  against the live service; the first real call is an owner check (a wrong parse falls back to
  mindicador, never to a guessed rate).

### 14.2 Owner steps: the `catalog` Storage bucket

Product images need these, in the Supabase Dashboard, by the owner. Nothing here is automated.

1. Create a **private** bucket named exactly `catalog` (not public; no storage policies).
2. Create a **dedicated `sb_secret_` key** for FastAPI's Storage use (ARCHITECTURE §7, §13). Do not reuse the Gmail capture's S3 key. Never a legacy JWT service-role key.
3. Set `ORIGENLAB_V2_STORAGE_URL` (`https://<project-ref>.supabase.co`) and
   `ORIGENLAB_V2_STORAGE_SECRET_KEY` in the API's server environment only.
4. **Back up Storage separately.** Database backups do not contain Storage objects (§10): add
   the `catalog` bucket to the independent bucket backup and its restore drill. A product image
   is addressed by content hash, so a restored bucket is verifiable against
   `catalog.product_image.sha256`.

Until 1-3 are done, the image routes answer 503 `storage_unavailable` and the rest of the
catalog works.

### 14.3 The importers

Four private importers and one parameter importer live in `apps/api/scripts/catalog/`:
`import_price_lists.py`, `import_supplier_documents.py`, `import_quote_history.py`,
`import_cost_parameters.py`. Their inputs are **private files outside the repository** (price
lists, supplier documents, costing sheets, the quote-economics study's extractions); plans and
reports go to a directory outside any git checkout (`--out` inside one is refused). Nothing they
print is a price, a client name or a local path: counts, statuses, item keys and basenames only.

Every importer has the same four subcommands (run from `apps/api`; arguments differ only in
`plan`):

1. **plan** (no database): `uv run python scripts/catalog/<importer>.py plan --out <dir> <inputs>`
   writes `plan.json` and prints its sha256. **Always run plan first and read its counts**
   (items by outcome, skipped by reason, withheld strings, disputed lines) before anything
   else. A malformed input refuses the whole plan, naming the file and field.
2. **apply**: `… apply --target-dsn <origenlab_api login> --admin-dsn <owner-capable login>
   --operator-email <operator> --plan <dir>/plan.json --plan-sha256 <sha> --out <dir>`.
   Both DSNs must name a literal loopback IP and one database: a disposable
   `origenlab_test_<8 hex>` database, or `origenlab_clean` with `--allow-cleanroom-production`.
   The target login is proven to be `origenlab_api` with no elevated membership. A rerun writes
   nothing new; a row an operator has since changed is reported `present_different` or
   `kept_later_value` and is never overwritten.
3. **verify** (read-only): `… verify --target-dsn … --plan … --plan-sha256 … --out …`. Exit 13
   means a row this plan wrote is missing or different.
4. **rollback**: `… rollback --target-dsn … --admin-dsn … --plan … --plan-sha256 … --out …
   --confirm-delete-loaded-rows` removes what the plan wrote, as `origenlab_owner`. Rollback
   deletes rows, and the manifest of a failed apply stays until rollback removes it.

Exit codes: 0 done, 2 bad arguments, 11 refused, 12 apply or rollback failed (rolled back),
13 verify found a difference.

Order for a first load into a disposable database or the clean room: cost parameters, price
lists, supplier documents and costing sheets, then quote history. Rehearse in a disposable
database before the clean room.

**Cost parameters** (`import_cost_parameters.py`) read `{"parameters": [{"key", "value",
"unit", "reason"}]}`, validate exactly as `set-cost-parameter` does and write by its SQL, so each
becomes a new append-only row with the operator and a `cost_parameter.set` event naming the key.
A key whose current value already equals the planned one is skipped. The first apply always
writes a row per key, even where the value equals what the previous run left.

**Monthly carrier fuel surcharge.** `dhl_fuel_surcharge_pct` changes monthly. An operator sets it
each month with `POST /v2/commands/set-cost-parameter` (key `dhl_fuel_surcharge_pct`, a fraction,
and a reason such as the month). The old value stays in the history.

### 14.4 The enrichment tool

`apps/api/scripts/catalog/enrich_products.py` proposes Spanish content and manufacturer images
with Claude (`uv sync --extra enrich`; `ANTHROPIC_API_KEY` in the environment, never printed).
Sources are text files the operator extracted by model key
(`<MODEL_KEY>.datasheet.txt`, `<MODEL_KEY>.page.txt`).

1. **Dry-run first.** Without `--apply` nothing is written and no image is downloaded; the
   answers are validated and written to `<out>/enrich-report.json`. Read the report.
2. **Measure about 20 products before the full run.** The default `--limit` is 20 (maximum
   500). Run `--select quoted --limit 20`, check the cost and the quality of the proposals,
   then widen. Each product is one synchronous request; a run of hundreds takes a long time
   and costs real money.
3. **Apply** with `--apply` into a disposable database, or the clean room with
   `--allow-cleanroom-production`. The database target is loopback-only; hosted is refused.
4. **`--allow-remote-storage`.** Images are stored through the `catalog` bucket named by
   `ORIGENLAB_V2_STORAGE_URL`. The tool refuses a non-loopback Storage URL unless this flag is
   given, because uploading into the hosted bucket is a separate decision from the database
   target. Give it only when the owner has created the bucket (§14.2) and means to upload
   real manufacturer images there. Without a Storage configuration the tool writes content
   only.
5. What apply writes is a proposal: `content_origin = machine`, images `proposed`. An
   operator confirms each in the dashboard (WORKFLOWS W8a). Exit codes: 0 done, 2 bad
   arguments, 11 refused before any write, 12 at least one item failed (that item rolled back).

### 14.5 Hosted loading

The four importers (`import_cost_parameters`, `import_price_lists`, `import_supplier_documents`,
`import_quote_history`) take a hosted mode for `apply`, `verify` and `rollback`: `--hosted-target
--authorize-hosted-connection --authorize-supavisor-session-route` in place of the two DSNs
(`apps/api/scripts/catalog/_common.py`). It reuses PR #623's route: the target file
`supabase/.audit/hosted_target.env` (Supavisor session mode, `verify-full` with the Supabase CA,
address pinned), the migrator login for the manifest (`SET LOCAL ROLE origenlab_owner`), and the
same host with the login `origenlab_api.<project ref>` for the catalog rows, its password read from
`ORIGENLAB_API_DB_PASSWORD` (the secret Render holds). A missing flag, an `--authorize-*` flag
without `--hosted-target`, a DSN given alongside, or a missing password is a refusal before any
connection. Without the flags the loopback-only rules are unchanged. Printed lines are redacted of
host, logins, address and passwords. The enrichment tool stays loopback-only.

Each load: `plan` (local, no database), read its counts, `apply` with `--plan-sha256`, then `verify`
with the same plan and hash (exit 0). Name the operator with `--operator-email`, or with
`--operator-id` for a shared-login profile, which has no email. **Take a `pg_dump` before the first
apply:** rollback needs `session_replication_role`, which the hosted migrator may be refused; it
then fails closed (exit 12, nothing deleted), and a hosted mistake is undone from that dump.

Loaded on 2026-10-06 through this route: `price_lists`, `supplier_documents`, `quote_history`, each
verified with no mismatch ([`STATUS.md`](STATUS.md) §2.7.61). `cost_parameters` has no plan; the
five seeded values stand until an operator sets more (§14.3). Each load records one
`source_record.migration_manifest_recorded` event with its importer, plan hash and counts; to see
what a database already holds:

```sql
select recorded_at, payload->>'importer', payload->>'plan_sha256'
  from crm.domain_event
 where event_type = 'source_record.migration_manifest_recorded'
 order by recorded_at;
```

## 15. Production release contract (PR #679; built, activation blocked)

Schema and code release are one guarded operation: independently approved main merge,
exact-SHA successful CI, encrypted consistent application/queue snapshot, atomic
DDL-plus-ledger, sequential pinned mail-worker/API/dashboard rollout, then read-only verification.
Every SQL file must support the old running worker/API throughout the rollout. Each file
is atomic; a multi-file batch is a committed prefix, not one transaction. Recovery rolls
forward from that prefix and never automatically reverses schemas or mutates queue jobs.

The dedicated `origenlab-release` environment admits **only the main branch**, and the
existing public website `production` environment and `web-deploy.yml` stay separate.
Repository release and scheduled-backup variables default OFF. Runtime metadata access
cannot prove that GitHub bypass actors are absent: trusted one-time setup verifies that
configuration with the owner's CLI; every actual release independently verifies current
collaborator approval of its exact merged PR, so an unreviewed direct/bypass push cannot
release production. A single maintainer needs an independent collaborator under this
policy; an owner self-approval is not substituted for review.

**PR #679 now removes the mutable cron from the release path.** A consolidated
`mail-worker` (compatible alias `triage-worker`) supervises isolated triage/capture
processes and a ten-minute capture queue. Its scheduler defaults OFF. The controller
requires the retained legacy cron suspended and deploys only the three exact-commit
worker/API/dashboard services. This is built, not live: production still runs the cron.
Follow the [approved mail-worker cutover](runbooks/MAIL_WORKER_CUTOVER.md) after real
production-data restoration, protection/secrets gates and explicit rollout approval.
The architecture closes the cron commit race in code; it does not prove shared-resource
capacity, real recovery or a successful production rollout. The detailed audit/state
machine remain in the [production release runbook](runbooks/PRODUCTION_DB_MIGRATIONS.md).
