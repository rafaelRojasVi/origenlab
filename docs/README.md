# OrigenLab V2 — canonical documentation

**Purpose.** Entry point for OrigenLab V2. Says what the system is, which
seven documents are canonical, and in what order to read them.

**This document owns:** the canonical-document map, the reading order, the
current phase, the business-separation rule, the documentation
source-of-truth rule, and the status of the non-canonical build-state index
and its maintenance requirement. It owns no domain, data, workflow,
architecture, migration or operational rule — each of those has exactly one
owner below.

## What OrigenLab is

OrigenLab is a Chilean laboratory-equipment commercial business. Its software
is one system that finds demand, records who the counterparties are, tracks
each pursuable sale, produces priced quotations, and sends email under strict
safety controls.

**V1** (current, running) is a monorepo: an Astro marketing site, a
SQLite-first Python email pipeline, a FastAPI operator API over PostgreSQL, a
Cloudflare Worker proxy, and a React operator dashboard. It works, and it is
the authority for outbound safety today.

**V2** (accepted; only the local schema foundation exists) is one Supabase PostgreSQL 17 project with seven
private schemas and **33 application tables** — the current reviewed
foundation, not a permanent budget — one FastAPI command boundary, one Python
worker, and one operator dashboard. SQLite and the PST archives become cold
evidence. See [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Current phase

**For build state, the live answer is [`STATUS.md`](STATUS.md)** — which is
not canonical and owns no rule; see [Non-canonical build-state
index](#non-canonical-build-state-index). The table below records the phase
this documentation set was written in; where the two differ **about what is
built**, `STATUS.md` is the current measurement.

| Item | State |
|---|---|
| V2 architecture decision | **Accepted** (2026-09-05), with binding amendments folded into these seven documents |
| These seven documents | **Written** — Documentation Slice D0; amended by D0.2 (privileged-role semantics) and D0.3 (external CRM benchmark, [`ARCHITECTURE.md`](ARCHITECTURE.md) §13) |
| V2 database, code, Supabase project | **Local foundation only** (Migration Slice 0, local portion, 2026-09-05): `supabase/roles.sql` and the migrations under `supabase/migrations/` create the four roles, the seven schemas and the 33 tables with constraints, grants, RLS and pgTAP proofs against a local PostgreSQL 17 container ([`OPERATIONS.md`](OPERATIONS.md) §4.1). **No hosted Supabase project, bucket, backup or advisor run exists**; no V2 code is deployed |
| V1 | **Running and authoritative** for every fact it owns today |
| Legacy documentation tree | Present, **superseded but not deleted** — see [`MIGRATION.md`](MIGRATION.md) |

Nothing in these documents is deployed. Statements about V2 are decisions and
plans, and are labelled as such.

## Canonical documents and ownership

Every binding concept has exactly one owning document. Other documents link
to the owner instead of restating the rule.

| Owning document | Owns |
|---|---|
| [`DOMAIN.md`](DOMAIN.md) | Glossary; organization / unit / domain / person / affiliation / contact-point semantics; prospect vs lead vs signal vs opportunity vs quote; product / manufacturer / supplier; address and opportunity-participant semantics; identity, merge and evidence-promotion principles; **the 33-table inventory** |
| [`DATA.md`](DATA.md) | Authority and trust matrix; evidence vs accepted truth; provenance and external identifiers; retention classes; active Postgres vs private Storage vs cold archive; Gmail message identity and ingestion checkpoints; Wave 1A counts and hashes; quarantine; rebuildable views; backup principles; **what will never be migrated** |
| [`WORKFLOWS.md`](WORKFLOWS.md) | Every state machine and operator workflow: actor, command, preconditions, state change, durable evidence, failure behaviour; the purpose-scoped send predicate; the quotation party snapshot; the dispatch linearization limit |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Topology; FastAPI command boundary; dashboard / web / worker responsibilities; the seven schemas; one-writer rules; Supabase Auth and JWKS; database roles, grants, RLS, the closed `SECURITY DEFINER` list and the `service_role` boundary; private Storage; Queues and Cron; worker deployment; observability; backups of Storage; the ERD; the external CRM benchmark conclusions |
| [`MIGRATION.md`](MIGRATION.md) | Verified V1 baseline; mirror containment; the Wave 1A bundle; retain / migrate / archive / rebuild / discard classes; the eight implementation slices and their gates; V1→V2 writer and sender handoff; rollback; final dump; deletion gates; proposed legacy disposition |
| [`OPERATIONS.md`](OPERATIONS.md) | Environments; operator roles; deployment; migrations; send control; campaign and quotation checklists; ambiguous-attempt procedure; Gmail sync recovery; backup and restore drills; monitoring; emergency shutdown; rollback execution; credential handling |
| This file | The map above, reading order, phase, separation rule, documentation authority, and the standing of the non-canonical build-state index below |

### Non-canonical build-state index

[`STATUS.md`](STATUS.md) is deliberately **absent from the table above**. It is
not canonical, it is not an eighth owning document, and it is not a candidate to
become one. Precisely:

- It **reports verified current build state only** — which era, slice, app,
  schema and environment is actually built, applied or deployed, and the date
  and commit that state was last verified against.
- It **owns no rule, no decision, no target, no workflow, no architecture and
  no migration policy.** Every one of those has an owner in the table above,
  and `STATUS.md` may only link to that owner, never restate or extend it.
- It **cannot override the seven canonical documents.** If it appears to, the
  disagreement is one of two things and neither is `STATUS.md` winning. A
  disagreement about *what the system should be* means `STATUS.md` has drifted
  outside its scope: the canonical document stands and the offending line is
  deleted. A disagreement about *what is built today* means the canonical
  document is carrying a build-state claim it should not carry: the canonical
  document is corrected, in its own PR, under its own owner.
- It **must be updated in the same PR** that changes a measured build-state
  fact — a migration applied, a slice gate passed, a service deployed, an
  environment variable confirmed, a count re-measured. A PR that changes only
  design does not touch it.

**Why [`CLAUDE.md`](../CLAUDE.md) reads it for orientation while the reading
order below excludes it.** The two lists answer different questions.
`CLAUDE.md` routes an agent to `STATUS.md` early because the first operational
question in any session is *which era am I in, and does the thing I am about to
change exist yet* — getting that wrong wastes the whole session and produces
work against a system that is not there. The reading order below is the
**design** reading order: it teaches what the system is and which statements
are binding. A build-state snapshot teaches neither, so it does not belong in
it. Orientation is not authority: reading `STATUS.md` first never substitutes
for reading the seven, it only says which of them describes something that
exists today.

## Reading order

1. This file.
2. [`DOMAIN.md`](DOMAIN.md) — the vocabulary. Nothing else makes sense first.
3. [`DATA.md`](DATA.md) — who owns which fact, and what is evidence.
4. [`WORKFLOWS.md`](WORKFLOWS.md) — what actually happens, step by step.
5. [`ARCHITECTURE.md`](ARCHITECTURE.md) — how it is built and secured.
6. [`MIGRATION.md`](MIGRATION.md) — only when moving V1 to V2.
7. [`OPERATIONS.md`](OPERATIONS.md) — only when running the system.

A future engineer or coding agent starting with no conversation history should
be able to work from these seven files alone.

## Labelling convention

Every non-obvious claim carries one of four labels:

- **[V1 FACT]** — verified true of the running system today.
- **[V2 DECISION]** — accepted and binding; changing it reopens the architecture.
- **[PLANNED]** — an accepted decision whose implementation does not exist yet.
- **[OPEN]** — an unresolved business decision, with a recommended default.

Unlabelled prose is definition or navigation, not a claim about deployed state.
Nothing planned is ever written as though it were deployed.

## Business separation

OrigenLab is a standalone business with its own data, its own Supabase project,
its own Gmail mailbox and its own repository. It shares no database, no schema,
no credential, no mailbox and no recipient list with Platt/BENKER, Transelec or
any other business. Reference architectures from other projects are
lessons-learned material only: no schema, name, table or policy is imported from
them mechanically. Cross-business data movement is out of scope and is never a
valid reason to relax a rule in these documents.

## Documentation source of truth

- These seven files are the **sole canonical V2 documentation**.
- [`STATUS.md`](STATUS.md) is **not one of them and is not canonical**. It is
  the non-canonical build-state index defined above, and it exists because build
  state is a *fact that changes*: keeping it inside the design documents is what
  let `ARCHITECTURE.md` §1 claim "No Supabase project, schema, role, bucket or
  table exists yet" for two days after 32 tables shipped and went green in CI.
  If it ever starts stating rules, delete it and fold the facts back here.
- **Maintenance requirement (binding).** Any PR that changes what is built,
  applied or deployed updates [`STATUS.md`](STATUS.md) **in that same PR**,
  including the `Last verified` line. Leaving it for a follow-up PR is the
  failure mode this index was created to end. A PR that changes only design,
  rules or targets must **not** touch it.
- Every other Markdown file in this repository — including
  `docs/architecture/`, `docs/data/`, `docs/refoundation/`, `docs/workflows/`,
  `docs/business/`, `docs/commercial/`, `docs/catalog/` and everything under
  `apps/*/docs/` — is **legacy or reference material**, retained until a
  reviewed removal pass. It may describe V1 accurately; it has no authority
  over V2.
- A superseded document can never override these seven. If a legacy document
  contradicts one of them, the canonical document wins and the legacy document
  is listed for removal in [`MIGRATION.md`](MIGRATION.md).
- Do not add another canonical document, a dated variant, a parallel plan, an
  ADR collection or another blueprint. Extend the owning document instead.
  The `STATUS.md` carve-out above is closed and is not a precedent.
- Validate relative links from the monorepo root with `python3 docs/check_doc_links.py`.
