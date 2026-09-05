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

> **Every command block below is `EXAMPLE — NOT YET IMPLEMENTED`.** V2 does not
> exist. The commands show the intended shape of each procedure so it can be
> built and reviewed; **none of them can be run today**, and none should be
> presented to an operator as available.

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
  advisors**, confirm the Data API is still off, confirm no role gained
  `BYPASSRLS` and no runtime role can assume `origenlab_owner`, confirm every
  `SECURITY DEFINER` function still matches the closed list — right owner,
  pinned `search_path`, `EXECUTE` revoked from `PUBLIC`, `anon`,
  `authenticated` and `service_role` — and confirm both send flags are
  unchanged.

```bash
# EXAMPLE — NOT YET IMPLEMENTED
ol migrate status --env production
ol migrate up --env production
ol audit roles --env production        # fails if any role has BYPASSRLS, or a
                                       # runtime role can assume origenlab_owner
ol audit exposure --env production     # fails if a private schema is exposed
ol audit definers --env production     # fails on any SECURITY DEFINER function
                                       # off the closed list, wrongly owned, with
                                       # an unpinned search_path, or executable
                                       # by PUBLIC/anon/authenticated/service_role
```

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

1. The revision is `approved`; its totals recompute from the stored inputs.
2. `pdf_sha256` is present and matches the object in Storage.
3. The recipient address has **no block**. (Prior contact and cooldown do not
   apply to a transactional quotation.)
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
4. If an address must never be contacted again, add a `block`. A block takes
   effect for every attempt that has not yet begun its provider call; it
   **cannot recall a message already handed to Gmail**.
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
3. Verify: the 30 tables exist; row counts are plausible; a sample quotation
   revision's `pdf_sha256` matches the restored object byte-for-byte; the
   domain event stream is contiguous.
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
| Runtime role privileges | any role gains `BYPASSRLS`, a runtime role becomes able to assume `origenlab_owner`, or a private schema becomes exposed | **page** |
| Privileged-function drift | a `SECURITY DEFINER` function appears off the closed list, changes owner, loses its pinned `search_path`, or becomes executable by `PUBLIC`, `anon`, `authenticated` or `service_role` | **page** |

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
- Storage access from FastAPI and the worker uses the project secret key from
  server environment only. S3 access keys are not issued.
- The Gmail OAuth credential for the production sender is held by the worker
  only. **A V1 Gmail credential is never revoked without written proof that it
  serves no ingestion path** ([`MIGRATION.md`](MIGRATION.md) §7).
- Rotation: rotate a key by adding the new one, deploying, then removing the
  old one. Record every rotation. **[OPEN]** rotation cadence; recommended
  default is annually and immediately on any suspected exposure.
- If a secret is ever committed, treat it as compromised: rotate first, then
  clean history.
