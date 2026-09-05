# OrigenLab V2 — migration and cutover

**Purpose.** How the running V1 system becomes V2 without losing a
commercial fact and without ever sending an email twice.

**This document owns:** the verified V1 baseline; mirror containment; the
Wave 1A bundle location and hash; the retain / migrate / archive / rebuild /
discard classes; the eight ordered slices and their gates; the V1→V2 writer
handoff; the sender handoff and rollback; the final PostgreSQL dump; the
two-copy archive requirement; the deletion gates; and the proposed legacy
disposition.

**It does not own:** the Wave 1A count mapping ([`DATA.md`](DATA.md) §7), the
target model ([`DOMAIN.md`](DOMAIN.md), [`ARCHITECTURE.md`](ARCHITECTURE.md)),
the transitions ([`WORKFLOWS.md`](WORKFLOWS.md)), the runbooks
([`OPERATIONS.md`](OPERATIONS.md)).

## 1. Verified V1 baseline

**[V1 FACT]** — verified 2026-09-05 in this worktree and from the Wave 1A
manifest.

| Item | Value |
|---|---|
| Design branch | `docs/origenlab-refoundation-v1` at `28c31097`, one commit ahead of `origin/main` `774cc36c` |
| Shipped Alembic head | `20260902_0046` |
| Production runtime tree | `66623060`, **51 commits behind** `origin/main` — this is the tree the production cron jobs execute |
| Durable CRM | `commercial.*` on PostgreSQL: organization, contact, sales_opportunity, task, activity, customer_quote (+revision, Drive workspace, events, number series), command_idempotency |
| Durable write path | dashboard → Cloudflare Worker proxy → `apps/api` `POST /operations/*` → service → repository → transaction + append-only event |
| Durable write flag | `commercial_operations_writes_enabled`, **default false** in `apps/api` settings |
| Machine tier | `apps/email-pipeline`, SQLite-first, ~66 GB live database, cron loops |
| Outbound authority | V1 SQLite: suppression, outreach state, campaign, recipients, send attempts |
| Other worktrees | `feat/historical-quote-register-v1` at `42dee698`; the `main` checkout at `66623060` |
| Tracked Markdown files | 176 |

**Until slice 5 passes, V1 remains authoritative for outbound safety and no V2
email may be sent.**

## 2. Mirror containment

**[V1 FACT]** The SQLite→PostgreSQL dashboard mirror fail-closes on an Alembic
head mismatch: the sync refuses to run when the database's Alembic version is
not the head its own code expects. The production runtime tree expects
`20260901_0042`; the database is at `20260902_0046`. **The mirror is therefore
not running, and that is safe** — `apps/api` reads the durable tables
directly, so nothing an operator depends on flows through it.

**[V2 DECISION] The broken mirror stays paused and is never repaired merely to
serve the migration.** Migration reads come from the V1 durable tables and the
Wave 1A bundle. The V1 `outbound.*` PostgreSQL sidecar mirror — whose only
writer is a parked break-glass script — is likewise never revived; it is
discarded ([`DATA.md`](DATA.md) §11).

## 3. The Wave 1A bundle

| Item | Value |
|---|---|
| Location | `~/data/origenlab-v2-migration/20260905T042425Z_wave1a_v1_safety_bundle.tar.gz` (plus the extracted directory and a `.sha256` sidecar) |
| Archive SHA-256 | `776dd73ee6931a006249b893493f9effbb2303493d4d8b580cfea379ffe8631a` |
| `manifest.json` SHA-256 | `201b7fab58c4b17fd3e7495b9a175625f9d5ae6882afe72ef9e19e1432510e48` |
| Per-file integrity | `SHA256SUMS`, verified with `sha256sum -c SHA256SUMS` |
| Contents | exact outbound/suppression/supplier-review tables, a derived recipient ledger, Gmail ingest checkpoints, parse-failure and reconciliation reports, and the source schema — **no bodies, no attachments, no credentials** |

**The counts and the V1→V2 safety mapping are owned by
[`DATA.md`](DATA.md) §7 and are not repeated here.**

**The bundle is immutable.** The three RFC 2047-decoded addresses discovered
after extraction are preserved as a **separate, separately hashed loader input
file** stored beside the bundle. The archive is never edited to include them.

## 4. Disposition classes

| Class | Meaning | Contents |
|---|---|---|
| **MIGRATE** | becomes a row in the 30 tables | durable `commercial.*` CRM rows (~11 rows across organization, contact, sales_opportunity, task, activity); V1 customer quotes as `adopted`; the compact Wave 1A safety set; the archived V1 campaign, its 1,161 recipients and 1,127 attempts; one migration-manifest source record |
| **EVIDENCE** | enters as pending `evidence.*`, never as CRM truth | 172 V1 supplier candidates with their evidence and channels; ~159 historical quote candidates; the one orphan supplier-review row, **quarantined** |
| **RECONCILE** | read from the archive as input, never imported | the 6,091 Gmail ingest checkpoint rows and the operator watermarks |
| **ARCHIVE** | cold storage, two verified copies | the V1 SQLite databases and their cutover snapshots; the Outlook/PST corpus; the Wave 1A bundle; the final V1 `pg_dump` |
| **REBUILD** | recreated from source in V2, not copied | Gmail messages and attachments (re-synced); ChileCompra notices (re-fetched); the product catalog; every dashboard, funnel and pipeline view |
| **DISCARD** | never enters V2 in any form | every V1 mart, mirror, projection and read model; the `outbound.*` sidecar mirror; the 13,622 evidence edges; the 11,613 parse failures; the six body representations; every address list other than the compact safety set |

The exhaustive discard list and its single documented investigation exception
are owned by [`DATA.md`](DATA.md) §11.

## 5. Ordered slices and gates

**[PLANNED]** — nothing below has been executed.

| # | Slice | Gate |
|---|---|---|
| 0 | Supabase project on PostgreSQL 17; seven schemas; 30 tables; RLS enabled; least-privilege **OrigenLab-created roles, all `NOBYPASSRLS`**; `origenlab_owner` (`NOLOGIN`) owning every object; grants and default privileges revoked from `PUBLIC`, `anon`, `authenticated` and `service_role`; private buckets; `pg_cron`; `pgmq`; the independent bucket-backup job | every check in **[§5.2](#m-mig-slice0-gates)** passes; database linter and **Supabase security advisors clean**; Data API off; application schemas not exposed; no runtime role can assume `origenlab_owner`; no `SECURITY DEFINER` function exists yet outside the closed list ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2); **both send flags false**; a database restore drill **and** a bucket restore drill both pass |
| 1 | Supabase Auth; `platform.*`; FastAPI JWKS verification; dashboard login; MFA for admin | sign-ups off; a disabled operator is rejected; the V1 proxy still live in parallel |
| 2 | CRM identity, affiliation, opportunity, task, activity, events; migrate the durable V1 rows | V1 `commercial_operations_writes_enabled = false`; per-table row and event counts match exactly; **no V1 writer remains** |
| 3 | Quotes, lines, FX, the totals trigger, PDF rendering, Drive workspace; migrate V1 quotes as `adopted` | frozen-revision and totals tests pass; a PDF hash round-trips |
| 4 | Evidence, comms, **shadow** Gmail sync, catalog, ChileCompra notices | checkpoint reconciliation against the Wave 1A set; **zero writes to `outbound.*`** |
| 5 | Wave 1A safety load; the send functions; the reconciler; **dry-run only** | the [`DATA.md`](DATA.md) §7.3 load gate is green; a test proves **no code path can send without a `dispatching` attempt row**; each privileged send function passes its authorization, RLS-bypass and state-transition tests, is owned by `origenlab_owner` with a pinned `search_path`, asserts its permitted `session_user` rather than `current_user`, and has `EXECUTE` revoked from `PUBLIC`, `anon`, `authenticated` and `service_role` with matching default privileges ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2, §6.4); the [§5.2](#m-mig-slice0-gates) checks 6–9 are re-run against the new functions |
| 6 | **Sender handoff** (§7) | V1 sender proven absent **before** any V2 flag becomes true |
| 7 | Rollback window: 30 days of V2 sending | zero unresolved `ambiguous` attempts older than 7 days |
| 8 | Delete V1 sender code; stop V1 cron; archive SQLite and PST; drop V1 PostgreSQL after a hashed `pg_dump` | archives verified on **two** copies |

### 5.1 Reconciliation gates

| Gate | Check |
|---|---|
| Row equality | for every MIGRATE table, V2 count = V1 count, matched by `external_identifier` scheme `v1_*` |
| Safety set | exactly 8,580 `prior_contact`, 704 address blocks, 91 domain blocks, **zero** cooldown rows from V1 input |
| Attempt shape | 1,126 `accepted` (957 `sent_copy_confirmed`, 112 `bounced`, 57 `pending`) and 1 `rejected`; minted id NULL on all |
| Gmail coverage | every Wave 1A checkpoint message id is either present in `comms.message` after the shadow sync or explicitly recorded as absent from Gmail |
| Quote integrity | every migrated `sent` revision has a `pdf_sha256` and sending evidence; every total recomputes |
| Idempotency | re-running any loader changes nothing and writes no second event |
| Fail-closed | any mismatch aborts the loader; no partial state is committed |

<a id="m-mig-slice0-gates"></a>
### 5.2 Slice 0 — privileged-role, grant and definer proofs

Slice 0 does not pass on assertion. Each line below is a check the slice-0 SQL
must actually prove, against the live project, before slice 1 begins. The
semantics they encode are owned by [`ARCHITECTURE.md`](ARCHITECTURE.md) §6,
§6.2 and §6.4 — this table is the proof obligation, not a second definition.

| # | Must be proven |
|---|---|
| 1 | `pg_roles.rolbypassrls` is **false** for every OrigenLab-created role — `origenlab_owner`, `origenlab_migrator`, `origenlab_api`, `origenlab_worker` |
| 2 | `service_role` is recognised as a **Supabase-managed `BYPASSRLS` role** and is **not** treated as an OrigenLab role: the check records its platform attribute rather than failing on it, and never proposes altering it |
| 3 | `PUBLIC`, `anon`, `authenticated` and `service_role` hold **no schema `USAGE` and no object privilege** on any of the seven OrigenLab application schemas |
| 4 | Those same roles hold **no `EXECUTE`** on any application function |
| 5 | **Default privileges owned by `origenlab_owner`** preserve checks 3 and 4 — a table, sequence or function created by a later migration does not silently regain access |
| 6 | Connecting **directly as `origenlab_worker`** permits only the worker's functions |
| 7 | Connecting **directly as `origenlab_api`** permits only the API's functions |
| 8 | **Cross-role and ungranted function calls fail** — the wrong runtime role and any role without `EXECUTE` are both refused |
| 9 | Inside a `SECURITY DEFINER` call, **`current_user` is `origenlab_owner` while `session_user` is the direct runtime login**. Proven by a **transactional or migration-test fixture that is rolled back** — **no permanent diagnostic function is created** for this check |
| 10 | **No secret or legacy service-role key** is present in API, worker, dashboard or web runtime configuration as an application-data or database credential; the only permitted server-side secret-key use is the Storage API ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4, §7) |
| 11 | **Supabase and PostgreSQL security advisors pass** before production |

Checks 3–5 are not redundant with RLS. `service_role` bypasses RLS by platform
attribute; it does **not** bypass an ordinary object grant, so the revocations
and their default privileges are the boundary that actually holds it out
([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.4).

## 6. V1 → V2 writer handoff

Dual writers are prevented **structurally**, not by convention:

1. Before slice 2's data move, V1's durable writes are disabled by setting
   `commercial_operations_writes_enabled = false`. The V1 API then refuses
   every `/operations/*` command.
2. The migration copies rows only after that flag is proven false and the
   V1 proxy's POST allowlist is confirmed inert.
3. V2 becomes the only writer at the moment the dashboard is repointed. There
   is no window in which both accept commands.
4. Every migrated row carries its V1 identifier as a
   `crm.external_identifier` (`v1_*` scheme), so a later diff is exact rather
   than approximate.

## 7. Sender handoff — one-way and verified

**[V2 DECISION]** Slice 6, in this order:

| Step | Action | Recorded as |
|---|---|---|
| a | Assert **both V2 send flags are false** and record the assertion | domain event |
| b | **Quarantine V1's sender**: add an explicit CLI/send guard that refuses to run, remove the executable entrypoints, and comment out the cron lines | diff hashes in the event |
| c | **Prove absence**: capture crontab, systemd timers, the process list and the Gmail token inventory | one domain event holding all four captures |
| d | Enable **only** `transactional_enabled`; the first live send is one quotation to an internal address | attempt row + event |
| e | Enable `marketing_enabled` only after a V2 dry run and a campaign approval | event |

**Credential rule.** **Do not revoke a V1 Gmail credential unless it is proven
to be sender-only and independent from Gmail ingestion.** V1's ingest and
sender may share an OAuth client; revoking it would silently break ingestion.
**The preferred mechanism is the explicit send guard and the removal of
executable entrypoints (step b), not credential revocation.** Revocation is
allowed only after a written proof that the credential serves no read path.

**Before enabling V2, prove that no V1 scheduled, running or usable sender
remains** — step (c) is the gate, and its event is the evidence.

## 8. Rollback

**Rollback always disables V2 before deliberately restoring V1.** Never the
reverse, and never both enabled.

1. Set **both V2 send flags false**.
2. Drain: wait for zero `reserved` and zero `dispatching` attempts; resolve
   every `ambiguous` attempt by hand ([`WORKFLOWS.md`](WORKFLOWS.md) §W9).
3. Export every V2 `accepted` attempt into V1's suppression and contact-state
   inputs **through V1's own operator path**, so a resumed V1 cannot recontact
   a V2-era address.
4. Only then deliberately restore the V1 marketing sender by reversing step
   7(b). Transactional sending has no V1 equivalent: quotations go out by hand
   from Gmail and are ingested as messages.
5. Record the rollback as a domain event.

**V1 sender code is physically deleted only after the rollback window closes**
(slice 7), never before.

## 9. Final dump, archives and deletion gates

- The V1 PostgreSQL database is dropped **only after** a final `pg_dump` is
  archived with its SHA-256 recorded in a manifest.
- SQLite and PST files are **never deleted**. They move to cold storage as
  **two verified copies** on separate media, each with a hash manifest.
- Every archive stays discoverable through hashes and manifests kept **outside**
  the active database.
- **Deletion gates, all required:**
  1. Slice 7 complete — 30 days of V2 operation.
  2. Zero unresolved `ambiguous` attempts older than 7 days.
  3. Reconciliation gates (§5.1) green on a re-run.
  4. Archive manifests verified on both copies.
  5. For code: a caller search across imports, scripts, CI and documentation
     comes back empty, to the evidence standard the repository already uses.
- Git history is the archive for deleted code and documentation. **No
  `docs/archive/` directory is ever created.**

## 10. Proposed legacy disposition

**Nothing in this section is deleted in Documentation Slice D0.** This is a
proposal, grouped by category, to be executed under the gates in §9.

### 10.1 Documentation — superseded by the seven canonical files

| Group | Paths | Proposal |
|---|---|---|
| Architecture truth and direction | `docs/architecture/CURRENT_SYSTEM_TRUTH.md`, `TARGET_COMMERCIAL_ARCHITECTURE.md` | superseded by [`ARCHITECTURE.md`](ARCHITECTURE.md) + [`DATA.md`](DATA.md); delete after slice 8 |
| Point-in-time audits and ledgers | `docs/architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md`, `COMMERCIAL_RESET_LEDGER.md`, `docs/SECURITY_AUDIT_RENDER_DASHBOARD.md`, `docs/catalog/PRODUCT_CATALOG_SCHEMA_AUDIT_V1.md` | historical; git history is the archive; delete after slice 8 |
| Design-only schema documents | `docs/commercial/COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md` | never fully built; superseded by [`DOMAIN.md`](DOMAIN.md); delete after slice 8 |
| Business rules | `docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md` | content absorbed into [`DOMAIN.md`](DOMAIN.md) §4 and [`WORKFLOWS.md`](WORKFLOWS.md) §W3/W8; delete after slice 3 |
| Re-foundation pass output | `docs/refoundation/REFOUNDATION_PLAN.md`, `SYSTEM_INVENTORY.md`, `docs/data/DATA_AUTHORITY_MAP.md`, `docs/data/SQLITE_REGISTER.md` | **retain until slice 8** — they are the V1 inventory this migration reads; delete once the archive manifests are verified |
| Workflow examples and template | `docs/workflows/*.md`, `docs/templates/WORKFLOW_TEMPLATE.md` | superseded by [`WORKFLOWS.md`](WORKFLOWS.md); delete after slice 5 |
| Documentation meta | `docs/DOCUMENTATION_MAP.md`, `docs/PROJECT_CONTEXT.md` | superseded by [`README.md`](README.md); delete after slice 8, together with the last legacy file it indexes |
| Proxy and access security | `docs/CLOUDFLARE_ACCESS_DASHBOARD_SECURITY.md` | delete when Cloudflare Access and the proxy are retired (after slice 1) |
| Release and publication | `docs/RELEASE_PROCESS.md`, `docs/PUBLIC_RELEASE_CHECKLIST.md`, `docs/SECURITY_PUBLIC_REPO.md`, `docs/dashboard/PRODUCTION_DASHBOARD_SMOKE_CHECKLIST.md` | superseded by [`OPERATIONS.md`](OPERATIONS.md); delete after slice 8 |
| App documentation trees | `apps/email-pipeline/docs/**` (~89 files), `apps/api/docs/**` (~20), `apps/web/docs/**` (~31), `apps/dashboard/docs/**` (~5) | delete with the code they document, slice by slice; `apps/web/docs/**` is **retained** because `apps/web` is retained |

At the last count there are **176 tracked Markdown files**. The target is the
seven canonical files plus the per-app READMEs that survive.

### 10.2 Code and infrastructure

| Group | Proposal |
|---|---|
| `apps/api` command layer, idempotency, quote service, Drive provider | **rewrite** onto the V2 schema; keep FastAPI |
| `apps/dashboard` | **retain**; add Supabase Auth; repoint to the V2 API |
| `apps/web` | **retain unchanged** |
| `apps/dashboard-proxy` and Cloudflare Access | retain until slice 1 passes, then delete |
| Gmail IMAP and MIME parsing, the ChileCompra client, the historical-quote register, the Wave 1A tooling | **rewrite** as `apps/worker` adapters |
| SQLite runtime, marts, PR2/PR3 identity and opportunity builders, the procurement projection chain, warm cases, the deal ledger, catalog build scripts, mirror sync | retain read-only until the slice 7 gate, then delete |
| The V1 campaign sender, archive lanes, do-not-repeat scripts, **the break-glass Gmail script**, the `outbound.*` sidecar mirror | delete after slice 5 reconciles — **the break-glass script is deleted first**, and no equivalent enters V2 |
| Local API systemd units and cron loops | delete after slice 7 |
| The Alembic tree | freeze at slice 2; delete after the V1 PostgreSQL decommission |
| Generated reports, `docs/generated`, `docs/audits`, `apps/api/docs/archive`, untracked runtime report directories | delete from git; artifacts move to Storage or the cold archive |
| Local branches | keep every `rescue/*` and `backup/*`; prune merged branches only with evidence |
| The quote-history worktree | remove once the register lands in `apps/worker` |
| SQLite backup files | per the V1 SQLite register: cold-archive the cutover snapshots; the 0-byte failed backup is deleted **only on explicit operator confirmation** |

## 11. Open migration decisions

| # | Decision | Recommended default |
|---|---|---|
| 1 | Quote numbering across the cutover | continue the V1 serial; a sequence with gaps is acceptable |
| 2 | Recontacting the 8,580 legacy addresses | never, except a per-recipient approved override with a reason; a V2-era cooldown of 180 days after each accepted send |
| 3 | Promotion of the 172 supplier candidates | none automatic; a review queue; batch promotion only for records an operator marks approved |
| 4 | Archive promotion scope | only messages tied to a historical quotation or an existing customer |
| 5 | Retiring Cloudflare Access and the proxy | after MFA enforcement passes slice 1 |
| 6 | Worker hosting and plan | a container background worker; PostgreSQL plan with point-in-time recovery |
| 7 | Disposition of the undocumented V1 SQLite snapshots | operator judgement, recorded before slice 8 |
