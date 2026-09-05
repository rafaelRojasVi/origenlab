# OrigenLab V2 — architecture

**Purpose.** How the system is built, how it is secured, and where each
responsibility lives.

**This document owns:** the topology; the FastAPI command boundary; dashboard,
web and worker responsibilities; the seven private schemas; the one-writer
rules; Supabase Auth and JWKS verification; database roles, grants, RLS and
the `SECURITY DEFINER` doctrine;
private Storage; Queues and Cron; worker deployment; observability; the
independent Storage backup; and the diagrams.

**It does not own:** what the tables mean ([`DOMAIN.md`](DOMAIN.md)), who owns
which fact ([`DATA.md`](DATA.md)), the transitions
([`WORKFLOWS.md`](WORKFLOWS.md)), the cutover ([`MIGRATION.md`](MIGRATION.md)),
the runbooks ([`OPERATIONS.md`](OPERATIONS.md)).

Everything here is **[V2 DECISION]** and **[PLANNED]**. No Supabase project,
schema, role, bucket or table exists yet.

## 1. Topology

```mermaid
flowchart TB
  BROWSER["Operator browser<br/>React dashboard"]
  WEB["apps/web — Astro<br/>public marketing site"]
  API["apps/api — FastAPI<br/>the only business command boundary"]
  WORKER["apps/worker — Python<br/>Gmail · MIME · PDF · ChileCompra · LLM"]
  PG[("Supabase PostgreSQL 17<br/>7 private schemas · 30 tables")]
  ST[("Private Storage<br/>eml · attachments · PDFs")]
  AUTH["Supabase Auth<br/>ES256 JWT + JWKS"]
  GMAIL["Gmail API<br/>one production mailbox"]
  CC["ChileCompra"]

  BROWSER -- "sign in / refresh / MFA<br/>publishable key only" --> AUTH
  BROWSER -- "HTTPS + user JWT" --> API
  API -- "verify signature via JWKS" --> AUTH
  API -- "SQL as origenlab_api" --> PG
  API -- "signed URLs, uploads<br/>server-side secret key" --> ST
  WORKER -- "SQL as origenlab_worker" --> PG
  WORKER -- "objects" --> ST
  WORKER -- "read + send" --> GMAIL
  WORKER -- "fetch notices" --> CC
  WEB -. "no operator or CRM code" .-> WEB
```

**The dashboard never connects to PostgreSQL.** Every data path is FastAPI
with the operator's JWT.

## 2. Responsibilities

| Component | Owns | Never does |
|---|---|---|
| `apps/web` (Astro, static) | the public marketing site | touch operator, CRM or outbound data |
| `apps/dashboard` (React) | presentation, operator interaction | hold truth, compute money, call the database, hold a secret |
| `apps/api` (FastAPI) | **the only business command boundary**: authentication checks, validation, idempotency, transactions, domain events, signed-URL minting | ingest mail, call Gmail, render PDFs, run long jobs |
| `apps/worker` (Python) | Gmail sync and the single send path, MIME parsing, PDF rendering, ChileCompra fetching, LLM classification, reconciliation | write `crm.*` — except `quote_revision.pdf_sha256` and sent-evidence ids, and only through `crm.record_quote_pdf` ([§6.2](#m-arch-definer)) |
| PostgreSQL | every durable fact; the closed list of privileged send functions; the constraints that make the invariants true | store bodies, attachments or PDFs; hold a browser identity; authorize a human |
| Storage | `.eml`, attachment bytes, quotation PDFs, dry-run reports | be public; be the authority for a fact |

## 3. Schemas and tables

Seven **private** schemas — `crm`, `comms`, `outbound`, `evidence`, `catalog`,
`procurement`, `platform` — holding **exactly 30 application tables**
(`crm` 14, `comms` 4, `outbound` 5, `evidence` 2, `catalog` 2, `procurement` 1,
`platform` 2). The inventory, with each table's unique responsibility, is
owned by [`DOMAIN.md`](DOMAIN.md) §7.

`public` holds nothing. Supabase-managed `auth`, `storage`, `pgmq` and
migration-metadata objects are outside the 30 and are not application tables.

### 3.1 One-writer rules

| Table group | Sole writer |
|---|---|
| `crm.*` (except the two quote columns below) | FastAPI commands as `origenlab_api` |
| `crm.quote_revision.pdf_sha256`, sent-evidence ids | the worker, through `crm.record_quote_pdf` — one privileged function, those columns only ([§6.2](#m-arch-definer)) |
| `comms.*` | the worker's Gmail sync; FastAPI only for participant resolution |
| `outbound.send_attempt`, `outbound.contact_control` | the send/reconcile functions, the NDR handlers, the Wave 1A loader, admin commands — **never direct DML from a runtime role** |
| `outbound.send_control` | one admin function |
| `outbound.campaign`, `campaign_recipient` | FastAPI for lifecycle and freeze; the send functions for recipient state |
| `evidence.*`, `catalog.*`, `procurement.*` | the worker and loaders; FastAPI for operator resolution |
| `platform.*` | FastAPI |
| `crm.domain_event` | every command and function — **INSERT only; no UPDATE, no DELETE grant exists** |

No table has a second writer. There is no ad-hoc SQL path against a durable
table.

## 4. The FastAPI command boundary

- Every mutation is a `POST` command with a trusted operator identity, an
  `Idempotency-Key` recorded in `platform.command_receipt`, and — where
  concurrent edits are possible — an `expected_version`.
- One command is one database transaction, and every transaction that changes
  state writes exactly one `crm.domain_event`.
- Reads are ordinary SQL views ([`DATA.md`](DATA.md) §9).
- FastAPI never calls Gmail, never renders a PDF, and never runs a long job.
  It enqueues work ([§8](#m-arch-queues)).

## 5. Authentication and authorization

**Supabase Auth establishes identity. FastAPI authorizes.**

1. Supabase Auth issues **ES256 (asymmetric) JWTs**. Sign-ups are disabled;
   operators are invited by an admin command that also inserts
   `platform.operator`.
2. The dashboard holds only the **publishable key**, and uses it only for sign
   in, refresh and MFA.
3. FastAPI verifies signature, `iss`, `aud` and `exp` against the project's
   **JWKS endpoint**, cached and refetched once on an unknown `kid`. No legacy
   shared JWT secret.
4. FastAPI then loads `platform.operator` by `sub`: `role ∈ {admin, sales,
   viewer}` and `status ∈ {active, disabled}`, cached in-process for at most
   60 seconds **(impl)**. **Sensitive commands** — send control, approvals,
   block revocation, ambiguous resolution, merges, operator management — read
   the operator row **inside the command transaction**, bypassing the cache.
5. Admin commands additionally require `aal = 'aal2'` in the JWT.

**No custom access-token hook is required for V2.** Hook claims are computed
at issuance and go stale until the next refresh; reading `platform.operator`
live is correct and simpler.

<a id="m-arch-roles"></a>
## 6. Database roles, grants and RLS

**[V2 DECISION]** — and this is the part most easily got wrong, so it is
stated exactly.

| Role | Attributes | Purpose |
|---|---|---|
| `origenlab_owner` | **`NOLOGIN`**, `NOSUPERUSER`, `NOBYPASSRLS`; **owns every application object**, including every privileged function | an ownership identity only. Nothing ever connects as it |
| `origenlab_migrator` | LOGIN, **`NOINHERIT`**, `NOSUPERUSER`, `NOBYPASSRLS`, member of `origenlab_owner` | DDL and data-fixing migrations, run under an explicit `SET ROLE origenlab_owner`. Never serves a request |
| `origenlab_api` | LOGIN, `NOSUPERUSER`, **`NOBYPASSRLS`**, `NOCREATEDB`, `NOCREATEROLE`; **not a member of `origenlab_owner`** | FastAPI runtime |
| `origenlab_worker` | LOGIN, `NOSUPERUSER`, **`NOBYPASSRLS`**, `NOCREATEDB`, `NOCREATEROLE`; **not a member of `origenlab_owner`** | worker runtime |
| `postgres` | — | never used at runtime |
| `anon`, `authenticated`, `service_role` | Supabase-managed | **no grants at all** on the seven schemas: `USAGE` on those schemas is revoked, and `EXECUTE` is revoked on every application function |

**No role in this system is granted `BYPASSRLS`** — not the API role, not the
worker role, not the owner, not now, not as a convenience later. **A runtime
role can neither inherit nor `SET ROLE` to `origenlab_owner`**; only the
migrator may assume it, and only to run DDL.

### 6.1 How grants, RLS and privileged functions divide the work

Three layers do three different jobs and are never substituted for one
another:

| Layer | Protects | Mechanism | Executes as |
|---|---|---|---|
| **A — ordinary table operations** | almost everything either runtime role does | per-table, per-verb `GRANT`s, database constraints, and an explicit named RLS policy per role per table | the runtime role itself, `SECURITY INVOKER` |
| **B — narrowly scoped privileged commands** | the handful of writes a caller must be unable to make directly | the closed list of `SECURITY DEFINER` functions in [§6.2](#m-arch-definer) | `origenlab_owner`, deliberately outside the caller's RLS |
| **C — operator (user-level) authorization** | which human may issue which command | FastAPI, against the verified JWT and `platform.operator` (§5) | not in the database at all |

Layer A is the default and carries the overwhelming majority of writes. Layer
B is an exception list, not a pattern. Layer C never reaches the database: **a
database session is a server role, never a person**, so no application
function consults `auth.uid()`, `auth.jwt()` or any browser identity — FastAPI
has already authorized the operator and passes only validated command context
as ordinary arguments.

How the database-side mechanisms interact — precisely:

1. **RLS is `ENABLE`d on all 30 tables** and is **not** `FORCE`d. Because it
   is not forced, the object owner (`origenlab_owner`) is exempt. That single
   ownership exemption is what makes migrations workable and what gives layer
   B its power; it is the only exemption in the design, and it belongs to a
   role nothing can log in as.
2. **Both runtime roles lack `BYPASSRLS`, so RLS does apply to them.** Each
   table a runtime role may touch carries an **explicit named policy** for
   that role. A table with no policy for a role is unreachable by that role —
   so a table added by a future migration is deny-by-default until someone
   deliberately opens it.
3. **Grants are the primary least-privilege mechanism**, per table and per
   verb. There is no `GRANT ALL ON ALL TABLES IN SCHEMA`. `origenlab_api`
   holds DML on `crm.*`, `platform.*` and campaign lifecycle tables plus SELECT
   elsewhere; `origenlab_worker` holds DML on `comms.*`, `evidence.*`,
   `procurement.*` and `catalog.*` plus SELECT elsewhere.
4. **A permission error or an empty RLS result is fixed by the missing grant
   or the missing policy** — never by widening a role and never by wrapping
   the statement in a definer function ([§6.2](#m-arch-definer)).
5. **What RLS is actually buying here, stated honestly:** it is a
   deny-by-default backstop, not the authorization model. Authorization is
   grants (layer A), the closed definer list (layer B), and FastAPI's operator
   checks (layer C). Row-level predicates are meaningful only where a row
   belongs to one operator; elsewhere the policy is a role gate. RLS also
   guarantees that if the Data API were ever switched on by accident,
   Supabase's built-in roles would still see nothing. It does **not** protect
   the writes performed inside a definer function
   ([§6.2](#m-arch-definer)).
6. **The Data API (PostgREST) is off** and the application schemas are never
   added to the exposed-schema list. Turning it on is a decision, not a
   default.

<a id="m-arch-definer"></a>
### 6.2 Privileged functions — the `SECURITY DEFINER` doctrine

**`SECURITY INVOKER` is the default.** Every function, view and trigger in the
seven schemas is `SECURITY INVOKER` unless it appears in the closed list
below. Read-side helpers — `outbound.dispatch_allowed`
([`WORKFLOWS.md`](WORKFLOWS.md) §2), the `crm.domain_event` payload validator
and the quote-totals check trigger ([`DATA.md`](DATA.md) §1.1) — are
`SECURITY INVOKER` and stay fully subject to the caller's grants and RLS.

**`SECURITY DEFINER` is never introduced to make a permission error or an RLS
error go away.** It is allowed only where a **narrowly scoped atomic command
must write tables that its callers intentionally cannot modify directly** — in
this system, the critical outbound state transitions and the two
worker-written quote columns. Nothing else qualifies, and the list is closed:

| Function (private schema) | Writes | `EXECUTE` granted to |
|---|---|---|
| `outbound.reserve_attempts(campaign_id)` | `send_attempt`, `campaign_recipient` | `origenlab_worker` |
| `outbound.begin_dispatch(attempt_id)` | `send_attempt`, `campaign_recipient` | `origenlab_worker` |
| `outbound.finish_attempt(attempt_id, outcome, provider_ids, reason)` | `send_attempt`, `contact_control`, `campaign_recipient` | `origenlab_worker` |
| `outbound.resolve_ambiguous(attempt_id, verdict, reason)` | the resolution fields of one `send_attempt` | `origenlab_api` |
| `outbound.authorize_retry(attempt_id, reason)` | one new `send_attempt` | `origenlab_api` |
| `outbound.set_send_control(flag, value, reason)` | `send_control` | `origenlab_api` |
| `outbound.add_contact_control(kind, normalized_address, reason, …)` | `contact_control` | `origenlab_api` (admin block and revoke) **and** `origenlab_worker` (hard bounce, complaint, unsubscribe) |
| `crm.record_quote_pdf(revision_id, pdf_sha256, sent_evidence_ids)` | only `quote_revision.pdf_sha256` and the sent-evidence ids | `origenlab_worker` |

Each writes exactly one `crm.domain_event`. Every one of them satisfies all of
the following, and a proposed definer function that misses any line is
rejected:

1. It lives in one of the seven **private, never-exposed** schemas. **A
   definer function is never created in `public`** — `public` holds nothing
   (§3) and is not on the exposed-schema list either way.
2. It is **owned by `origenlab_owner`**, the `NOLOGIN` owner role — never by a
   runtime login and never by `postgres`. That ownership, not `BYPASSRLS`, is
   what lets the function write past the caller's policies.
3. It pins a **fixed, safe `search_path`** on the function itself
   (`SET search_path = pg_catalog`): no `public`, no `$user`, never the
   session's value.
4. **Every referenced object is schema-qualified** — in the body, and in the
   argument and return types.
5. It contains **no uncontrolled dynamic SQL.** Static statements only; where
   a statement must be assembled, every identifier comes from a fixed literal
   list in the function, never from an argument.
6. `EXECUTE` is **revoked from `PUBLIC`, `anon`, `authenticated` and
   `service_role`** in the same migration that creates it — PostgreSQL grants
   `EXECUTE` to `PUBLIC` by default — and granted **only** to the specific
   role named in the table above.
7. It **validates its caller and its arguments** before writing: it asserts
   that `current_user` is a role permitted to call it and raises otherwise,
   and it checks every argument for existence, current state, enum membership
   and row ownership.
8. It **exposes the smallest possible operation**: one state transition on one
   aggregate. No general-purpose update surface, no free-form predicate or
   SQL fragment argument, no "and also do X" flag.
9. It is covered by three kinds of test: **authorization** (the wrong runtime
   role, `anon`, `authenticated`, `service_role` and `PUBLIC` are all
   refused), **RLS bypass** (the function touches only its declared tables and
   columns, and the calling role still cannot reach those tables directly),
   and **state transition** (every legal transition, every rejected one, and
   the concurrent cases).
10. It is cleared by the **Supabase and PostgreSQL security advisors** —
    definer-function, mutable-`search_path` and schema-exposure checks —
    before it reaches production ([`MIGRATION.md`](MIGRATION.md) §5 slice 0,
    [`OPERATIONS.md`](OPERATIONS.md) §4).

**What this deliberately does not claim.** Inside a definer function the
caller's RLS policies **do not apply** — crossing that boundary is the entire
point of the mechanism, and no part of this design pretends otherwise. The
function *is* the boundary: its caller check, its argument validation, its
single narrow operation and its tests are the whole of the protection on the
rows it writes. That is why the list above is short, closed, and reviewed
line by line rather than extended for convenience.

### 6.3 Connections

FastAPI and the worker are persistent processes and use pooled connections.
**(impl)** the session pooler (port 5432, IPv4, `role.<projectref>` username)
supports prepared statements and long transactions and is the starting
choice; transaction mode is not used; the direct connection with an IPv4
add-on is the fallback. **Pooler versus direct connection is an implementation
detail that must be verified live against the project before deployment** —
neither is a decision this document freezes. Migrations connect as
`origenlab_migrator` and run their DDL under an explicit
`SET ROLE origenlab_owner` ([§6](#m-arch-roles)).

## 7. Storage

- **All buckets are private. There are zero storage policies and zero public
  paths.**
- FastAPI and the worker reach Storage with the **project secret key** from
  server environment only. **A secret key never reaches the dashboard**, never
  appears in a client bundle, and never appears in these documents.
- A browser receives a **signed URL valid for at most 10 minutes**, minted
  only after FastAPI has authorized that specific operator for that specific
  object. Uploads go through FastAPI.
- S3 access keys are not issued.
- **Database backups do not include Storage objects.** Buckets are therefore
  backed up **independently**, to separate storage, with their own manifest
  and hashes, and the restore drill covers a bucket restore
  ([`OPERATIONS.md`](OPERATIONS.md)).

<a id="m-arch-queues"></a>
## 8. Queues, cron and worker deployment

- **`pgmq` queues** carry work from FastAPI to the worker: render a PDF, sync a
  mailbox, fetch notices, reconcile attempts, dispatch a reserved attempt.
- **`pg_cron`** schedules recurring jobs by enqueueing onto those queues: Gmail
  sync, ChileCompra refresh, the reconciler, the Storage backup job, the
  cooldown sweep.
- **The worker is a long-running process** on a container host, one replica for
  the send path. Its concurrency is bounded by the queue, and the one-open-
  attempt index makes a second replica safe but unnecessary.
- **Heavy worker jobs never run as Edge Functions.** Edge Functions are not on
  any critical path — not sending, not ingesting, not rendering, not
  reconciling. IMAP/Gmail sync, MIME parsing, PDF rendering and LLM calls all
  exceed what that runtime should carry.
- **There is no SQLite tier, no mirror, no mart and no projection table in
  V2.**

## 9. Observability

| Signal | Source |
|---|---|
| Every state transition | `crm.domain_event` — the primary audit and the primary debugging tool |
| Send safety | `outbound.send_attempt` counts by `submission_state` and `delivery_state`; open attempts older than the lease |
| Queue health | `pgmq` depth and oldest-message age per queue |
| Sync health | `comms.mailbox` cursor age |
| Application logs | structured JSON from FastAPI and the worker, with a correlation id per command and per attempt |
| Alerts | see [`OPERATIONS.md`](OPERATIONS.md) |

Nothing is observed by reading a projection table, because there are none.

## 10. Entity relationships

```mermaid
erDiagram
  ORGANIZATION ||--o{ ORGANIZATION : parent
  ORGANIZATION ||--o{ ORGANIZATION_DOMAIN : owns
  ORGANIZATION ||--o{ ORGANIZATION_RELATIONSHIP : plays
  PERSON ||--o{ AFFILIATION : holds
  ORGANIZATION ||--o{ AFFILIATION : hosts
  PERSON o|--o{ CONTACT_POINT : uses
  ORGANIZATION o|--o{ CONTACT_POINT : operates
  ORGANIZATION o|--o{ EXTERNAL_IDENTIFIER : identified_by
  PERSON o|--o{ EXTERNAL_IDENTIFIER : identified_by
  OPPORTUNITY o|--o{ EXTERNAL_IDENTIFIER : identified_by
  QUOTE o|--o{ EXTERNAL_IDENTIFIER : identified_by
  ORGANIZATION o|--o{ OPPORTUNITY : target_org
  PERSON o|--o{ OPPORTUNITY : target_person
  CONTACT_POINT o|--o{ OPPORTUNITY : target_channel
  OPPORTUNITY ||--o{ TASK : has
  OPPORTUNITY ||--o{ ACTIVITY : has
  MESSAGE o|--o{ ACTIVITY : linked_by
  OPPORTUNITY ||--o{ QUOTE : quoted_by
  QUOTE ||--|{ QUOTE_REVISION : revised_as
  QUOTE_REVISION ||--|{ QUOTE_LINE : contains
  QUOTE_REVISION o|--o| OPPORTUNITY : won_with
  MAILBOX ||--o{ MESSAGE : holds
  MESSAGE ||--|{ MESSAGE_PARTICIPANT : addresses
  MESSAGE ||--o{ ATTACHMENT : carries
  CAMPAIGN ||--|{ CAMPAIGN_RECIPIENT : freezes
  CAMPAIGN_RECIPIENT ||--o{ SEND_ATTEMPT : attempted_by
  QUOTE_REVISION o|--o{ SEND_ATTEMPT : sent_by
  SEND_ATTEMPT o|--o| MESSAGE : sent_copy
  SEND_ATTEMPT o|--o{ SEND_ATTEMPT : retry_of
  SOURCE_RECORD ||--|{ ASSERTION : yields
  SOURCE_RECORD o|--o{ AFFILIATION : provenance
  PRODUCT ||--o{ SUPPLIER_PRODUCT : priced_by
  NOTICE o|--o{ OPPORTUNITY : promoted_to
```

`outbound.send_control` and `outbound.contact_control` are deliberately absent
from the diagram: `send_control` is a single global row, and `contact_control`
is keyed by **normalized text, not by a foreign key**, so a safety fact
survives every identity merge and covers addresses that were never contact
points ([`DATA.md`](DATA.md) §3).

## 11. The send path

```mermaid
sequenceDiagram
  participant Cron as pg_cron
  participant Q as pgmq
  participant W as worker
  participant DB as PostgreSQL functions
  participant G as Gmail API

  Cron->>Q: enqueue campaign tick
  W->>DB: reserve_attempts(campaign)
  Note over DB: campaign row FOR UPDATE<br/>full predicate over snapshotted recipients<br/>→ attempts 'reserved'
  DB-->>W: attempt ids
  W->>DB: begin_dispatch(attempt)
  Note over DB: re-evaluate COMPLETE predicate<br/>set 'dispatching' + lease + minted RFC id<br/>fail → 'skipped', recipient released
  DB-->>W: minted id
  W->>DB: re-check COMPLETE predicate (< 5 s old)
  W->>G: users.messages.send
  Note right of G: linearization point —<br/>a block committed after this<br/>cannot recall the message
  alt provider ids returned
    G-->>W: message id
    W->>DB: finish_attempt(accepted) → prior_contact + cooldown
  else timeout / reset / crash
    W->>DB: attempt stays 'dispatching' → lease expiry → 'ambiguous'
    Note over DB: never retried automatically<br/>address lock and budget stay held
  end
```

The predicate, its three enforcement points and the honest statement of the
race window are owned by [`WORKFLOWS.md`](WORKFLOWS.md) §2.

## 12. Deliberate absences

| Not in V2 | Why |
|---|---|
| Data API / PostgREST exposure | every path is FastAPI with the user's JWT |
| Edge Functions on any critical path | the work is too heavy and too stateful |
| A SQLite runtime tier, mirror, mart or projection table | one database, one truth, views for everything else |
| A second database, an event bus, a generic workflow engine, microservices | the product does not need them |
| `BYPASSRLS` on any role, runtime or otherwise | least-privilege grants and RLS, plus a closed list of narrowly scoped `SECURITY DEFINER` functions owned by a `NOLOGIN` role ([§6.2](#m-arch-definer)) |
| `SECURITY DEFINER` as a cure for a permission or RLS error | the fix is the missing grant or the missing policy; the definer list is closed |
| A definer function in `public`, or one owned by a runtime login | private schemas only, `origenlab_owner` only |
| `auth.uid()` inside an application function | the database session is a server role; FastAPI authorizes the operator and passes validated arguments |
| A break-glass sender or any second send path | one sender path ([`WORKFLOWS.md`](WORKFLOWS.md) §2) |
| A browser-held database or secret key | the dashboard holds only the publishable key |
| A shared package | none until two consumers actually exist |
