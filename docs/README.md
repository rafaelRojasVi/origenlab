# OrigenLab V2 — canonical documentation

**Purpose.** Entry point for OrigenLab V2. Says what the system is, which
seven documents are canonical, and in what order to read them.

**This document owns:** the canonical-document map, the reading order, the
current phase, the business-separation rule, and the documentation
source-of-truth rule. It owns no domain, data, workflow, architecture,
migration or operational rule — each of those has exactly one owner below.

## What OrigenLab is

OrigenLab is a Chilean laboratory-equipment commercial business. Its software
is one system that finds demand, records who the counterparties are, tracks
each pursuable sale, produces priced quotations, and sends email under strict
safety controls.

**V1** (current, running) is a monorepo: an Astro marketing site, a
SQLite-first Python email pipeline, a FastAPI operator API over PostgreSQL, a
Cloudflare Worker proxy, and a React operator dashboard. It works, and it is
the authority for outbound safety today.

**V2** (accepted, not built) is one Supabase PostgreSQL 17 project with seven
private schemas and exactly **30 application tables**, one FastAPI command
boundary, one Python worker, and one operator dashboard. SQLite and the PST
archives become cold evidence. See [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Current phase

| Item | State |
|---|---|
| V2 architecture decision | **Accepted** (2026-09-05), with binding amendments folded into these seven documents |
| These seven documents | **Written** — Documentation Slice D0 |
| V2 database, code, Supabase project | **Not created.** No schema, migration, role, bucket or table exists |
| V1 | **Running and authoritative** for every fact it owns today |
| Legacy documentation tree | Present, **superseded but not deleted** — see [`MIGRATION.md`](MIGRATION.md) |

Nothing in these documents is deployed. Statements about V2 are decisions and
plans, and are labelled as such.

## Canonical documents and ownership

Every binding concept has exactly one owning document. Other documents link
to the owner instead of restating the rule.

| Owning document | Owns |
|---|---|
| [`DOMAIN.md`](DOMAIN.md) | Glossary; organization / unit / domain / person / affiliation / contact-point semantics; prospect vs lead vs signal vs opportunity vs quote; product / manufacturer / supplier; identity, merge and evidence-promotion principles; **the 30-table inventory** |
| [`DATA.md`](DATA.md) | Authority and trust matrix; evidence vs accepted truth; provenance and external identifiers; retention classes; active Postgres vs private Storage vs cold archive; Gmail message identity and ingestion checkpoints; Wave 1A counts and hashes; quarantine; rebuildable views; backup principles; **what will never be migrated** |
| [`WORKFLOWS.md`](WORKFLOWS.md) | Every state machine and operator workflow: actor, command, preconditions, state change, durable evidence, failure behaviour; the dispatch linearization limit |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Topology; FastAPI command boundary; dashboard / web / worker responsibilities; the seven schemas; one-writer rules; Supabase Auth and JWKS; database roles, grants, RLS and the closed `SECURITY DEFINER` list; private Storage; Queues and Cron; worker deployment; observability; backups of Storage; the ERD |
| [`MIGRATION.md`](MIGRATION.md) | Verified V1 baseline; mirror containment; the Wave 1A bundle; retain / migrate / archive / rebuild / discard classes; the eight implementation slices and their gates; V1→V2 writer and sender handoff; rollback; final dump; deletion gates; proposed legacy disposition |
| [`OPERATIONS.md`](OPERATIONS.md) | Environments; operator roles; deployment; migrations; send control; campaign and quotation checklists; ambiguous-attempt procedure; Gmail sync recovery; backup and restore drills; monitoring; emergency shutdown; rollback execution; credential handling |
| This file | The map above, reading order, phase, separation rule, documentation authority |

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
- Every other Markdown file in this repository — including
  `docs/architecture/`, `docs/data/`, `docs/refoundation/`, `docs/workflows/`,
  `docs/business/`, `docs/commercial/`, `docs/catalog/` and everything under
  `apps/*/docs/` — is **legacy or reference material**, retained until a
  reviewed removal pass. It may describe V1 accurately; it has no authority
  over V2.
- A superseded document can never override these seven. If a legacy document
  contradicts one of them, the canonical document wins and the legacy document
  is listed for removal in [`MIGRATION.md`](MIGRATION.md).
- Do not add an eighth canonical document, a dated variant, a parallel plan, an
  ADR collection or another blueprint. Extend the owning document instead.
- Validate relative links from the monorepo root with `python3 docs/check_doc_links.py`.
