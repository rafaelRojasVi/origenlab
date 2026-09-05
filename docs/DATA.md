# OrigenLab V2 — data authority and retention

**Purpose.** Which system owns which fact, what is evidence rather than truth,
where bytes live, how long they live, and what is never migrated.

**This document owns:** the authority and trust matrix; the evidence-vs-truth
boundary; provenance and external identifiers; retention classes; the split
between active PostgreSQL, private Storage and the cold archive; Gmail message
identity and ingestion checkpoints; the Wave 1A safety counts and archive
hashes; data-quality and quarantine rules; rebuildable views; backup
principles; and the exhaustive list of what will never enter active Postgres.

**It does not own:** entity definitions ([`DOMAIN.md`](DOMAIN.md)),
transitions ([`WORKFLOWS.md`](WORKFLOWS.md)), roles, grants and storage
mechanics ([`ARCHITECTURE.md`](ARCHITECTURE.md)), the migration procedure
([`MIGRATION.md`](MIGRATION.md)), restore drills
([`OPERATIONS.md`](OPERATIONS.md)).

## 1. Authority and trust matrix

**[V2 DECISION]**, **[PLANNED]** as an implementation. One authority per fact,
one writer per authority.

| Fact | Authority | Only writer | Rebuildable |
|---|---|---|---|
| Who an organization or person is; affiliations | `crm` identity tables | FastAPI promotion, merge and affiliation commands | No |
| Which channel exists, who uses it, who operates it | `crm.contact_point` | FastAPI | No |
| What role an organization plays for OrigenLab | `crm.organization_relationship` | FastAPI | No |
| Opportunity stage, customer organization, outcome | `crm.opportunity` | FastAPI | No |
| Who holds which human role on an opportunity | `crm.opportunity_participant` | FastAPI | No |
| Where an organization is sited, billed or delivered to | `crm.address` | FastAPI | No |
| Quote content, party snapshot, FX, totals, approval, sent proof | `crm.quote_revision`, `crm.quote_line`, the PDF in Storage | FastAPI; the worker writes only `pdf_sha256` and sent-evidence ids, and only through `crm.record_quote_pdf` ([`ARCHITECTURE.md`](ARCHITECTURE.md) §6.2) | No |
| Was this address ever contacted | `outbound.contact_control` kind `prior_contact`, plus accepted attempts | the privileged send functions, the reconciler, the Wave 1A loader — never direct DML from a runtime role | No |
| May we send to this address now, for this purpose | `outbound.send_control` + `contact_control` (purpose-scoped `block`, `cooldown`) + campaign policy + recipient override | admin command, NDR/unsubscribe handlers, send functions | No |
| What a message said | `comms.message` plus the `.eml` in Storage | the worker's Gmail sync | Only while Gmail retains it |
| An operator-relevant interaction | `crm.activity` | FastAPI | No |
| What happened, when, by whom | `crm.domain_event` | every command, the privileged send functions, the loaders — INSERT only | No |
| ChileCompra publications, supplier prices, products | `procurement.notice`, `catalog.*` | worker, FastAPI | Not guaranteed — retained |
| Dashboards, pipelines, funnels | ordinary SQL views | nobody | Yes |

### 1.1 The three audit-shaped tables are disjoint

- `comms.message` is **provider evidence**: written only by the worker sync,
  never edited, never duplicated elsewhere. Ingesting a message writes **no**
  domain event.
- `crm.activity` exists only when an operator or a promotion command
  deliberately links an interaction to a CRM object. A message that nobody
  linked is not an activity.
- `crm.domain_event` records **transitions only**: a closed `aggregate_kind`,
  an `aggregate_id`, a monotonic `seq`, an `event_type` from a closed list
  enforced by a CHECK constraint, a `payload_version`, and a `payload jsonb`
  validated by a `SECURITY INVOKER` function. New event types and payload
  versions require a migration. Event families: identity created/merged/confirmed, affiliation
  opened/closed, address added/superseded, relationship changed, opportunity
  staged/closed/organization set, participant added/linked/primary
  changed/ended, quote revision transitioned/superseded, campaign
  transitioned/approved/override granted, attempt submission/delivery
  changed, contact control added/revoked, send control changed, operator
  changed, migration manifest recorded.

## 2. Evidence versus accepted truth

| | Evidence | Accepted truth |
|---|---|---|
| Where | `evidence.source_record`, `evidence.assertion`, `comms.message`, `procurement.notice`, `catalog.*` | `crm.*`, `outbound.contact_control`, `outbound.send_attempt` |
| Written by | the worker, loaders | FastAPI commands only |
| Becomes truth | only through an operator promotion command | — |
| May be wrong | yes, by design | corrected by a command, with an event |
| Deleted | superseded or quarantined, never silently dropped | never |

A pending assertion is never counted, reported or sent to. **No timer, score
or threshold promotes anything.** Ambiguity is recorded and stopped, never
resolved by picking a candidate.

## 3. Provenance and external identifiers

- Every `crm` row created by promotion keeps `origin_source_record_id` — a
  nullable FK retained forever, even after the source record is superseded.
- Cross-system identity uses `crm.external_identifier` with a closed `scheme`
  and exactly one typed FK subject ([`DOMAIN.md`](DOMAIN.md) §2.6). V1
  identifiers are schemes (`v1_organization`, `v1_contact`, `v1_opportunity`,
  `v1_quote`, `v1_supplier_master`), so every migrated row is traceable to its
  V1 source by exact key.
- V1 task and activity identifiers are recorded in the migration event
  payload, not as external identifiers — they name rows, not subjects.
- Evidence→truth links are logical (`resolved_kind`, `resolved_id`). **No
  durable table takes a foreign key into rebuildable machine output.**
- An address's provenance is the same `origin_source_record_id` and
  `postal_address`-assertion path as every other promoted row
  ([`DOMAIN.md`](DOMAIN.md) §2.8). No address-specific provenance, history or
  geocoding store exists.
- `outbound.contact_control` is keyed by normalized text and a `purpose`,
  deliberately not by FK, so a safety fact survives identity merges and covers
  addresses that are not contact points.

## 4. Retention classes

| Class | Contents | Where | Retention |
|---|---|---|---|
| **Durable commercial truth** | all 32 tables' committed rows | active PostgreSQL | forever; deletion only by an explicit, evented command |
| **Immutable proof** | sent quotation PDFs, their SHA-256, sending evidence | private Storage + `crm.quote_revision` | forever; never overwritten |
| **Communication evidence** | `.eml` bodies, attachments | private Storage, referenced from `comms.*` | forever unless an operator deletes with a reason **[OPEN]** |
| **Machine evidence** | source records, assertions, notices, catalog observations | active PostgreSQL | retained; superseded rows kept |
| **Rebuildable** | dashboards, funnels, pipelines | SQL views | none — recomputed |
| **Cold archive** | the V1 SQLite databases, the PST corpus, the Wave 1A bundle, the final V1 `pg_dump` | offline storage, two verified copies | forever; never imported wholesale |

## 5. Where bytes live

- **Active PostgreSQL** holds rows only — no message bodies, no attachment
  bytes, no PDFs, no archive dumps.
- **Private Storage** holds `.eml` files, attachment bytes, generated and sent
  quotation PDFs, and dry-run reports. Buckets are private with no public
  path. Objects are addressed from database rows; a browser only ever receives
  a short-lived signed URL minted after FastAPI authorizes the request
  ([`ARCHITECTURE.md`](ARCHITECTURE.md)).
- **Cold archive** holds the V1 SQLite files, the Outlook/PST mailbox corpus,
  the Wave 1A bundle and the final V1 PostgreSQL dump. It is offline, hashed,
  and discoverable only through manifests. It is never a query target.

## 6. Gmail message identity and ingestion

**[V2 DECISION]**

- `comms.message` is unique on `(mailbox_id, provider_message_id)`. **The
  Gmail provider message id is the canonical provider identity.**
- `rfc822_message_id_norm` is **nullable and non-unique**. Inbound RFC 822
  Message-IDs may be absent, malformed or reused, so they are never an
  identity key.
- **The only RFC 822 uniqueness is on OrigenLab-minted outbound ids**:
  `outbound.send_attempt.rfc822_message_id` (minted at dispatch, unique) and
  `comms.message.send_attempt_id` (unique, set by the reconciler when it finds
  the Sent copy). A minted id is a reconciliation key for outbound evidence,
  never the identity of an inbound row.
- Gmail `history.list` and `messages.list` replays insert with
  `ON CONFLICT DO NOTHING`; label changes update labels only; a full resync
  rewrites nothing.
- The sync cursor (history id and per-label watermarks) lives on
  `comms.mailbox`. **[V1 FACT]** the V1 pipeline's ingest dedupe compares a
  normalized RFC 822 Message-ID against every stored message id, and does not
  persist IMAP UIDs or Gmail thread ids; the Wave 1A bundle preserves 6,091
  Gmail checkpoint rows and the operator UID/total watermarks so V2's first
  sync can be reconciled against V1's coverage.

## 7. Wave 1A — the V1 outbound safety extract

**[V1 FACT]** — verified from the bundle manifest on 2026-09-05.

The bundle is a read-only extraction from the live V1 SQLite database taken in
one deferred read transaction (`mode=ro`, `PRAGMA query_only=ON`) between
`2026-09-05T04:24:25Z` and `04:24:28Z`, with the process's network calls
stubbed out. It contains **no message bodies, subjects, attachment extracts or
attachment bytes, no full database copy, and no credentials**.

| Item | Value |
|---|---|
| Bundle | `20260905T042425Z_wave1a_v1_safety_bundle.tar.gz` under `~/data/origenlab-v2-migration/` |
| Archive SHA-256 | `776dd73ee6931a006249b893493f9effbb2303493d4d8b580cfea379ffe8631a` |
| `manifest.json` SHA-256 | `201b7fab58c4b17fd3e7495b9a175625f9d5ae6882afe72ef9e19e1432510e48` |
| Source database | the configured V1 runtime SQLite path, 66 GB, `sqlite_master` fingerprint `9148c90c0246fdf11f911233176c2f6b89dd63b4f9b7bf5263cafedf7bd2f5f1` |
| Runtime code at extraction | git `66623060` (the tree production cron executes; 51 commits behind `origin/main` at the time) |
| Per-file integrity | `SHA256SUMS` covers every file including `manifest.json` |

### 7.1 Corrected safety mapping

| V1 source | Count | V2 target |
|---|---|---|
| Recipient ledger — contacted union | **8,577** | `contact_control(kind=prior_contact, scope=address, purpose=all)`, `source = wave1a_union` |
| RFC 2047 decoded addresses absent from that union | **3** | `contact_control(kind=prior_contact, scope=address, purpose=all)`, `source = wave1a_rfc2047_addendum` |
| **Permanent prior-contact total** | **8,580** | — |
| `contact_email_suppression` (700) ∪ manual hard-block addresses (5), deduplicated | **704** | `contact_control(kind=block, scope=address, purpose=all)` |
| `contact_domain_suppression` | **91** | `contact_control(kind=block, scope=domain, purpose=all)` |
| Cooldown rows carried from V1 | **0** | none — V1 has no cooldown concept |
| `outreach_contact_state` in a blocking state | 1,826 | already inside the 8,577 union; no separate rows, flags preserved on the prior-contact row |
| `outbound_campaign` | 1 | one archived `outbound.campaign` |
| `outbound_campaign_recipient` | 1,161 | `outbound.campaign_recipient` |
| `outbound_send_attempt` | 1,127 | `outbound.send_attempt`: **1,126** `accepted` (957 `sent_copy_confirmed`, 112 `bounced`, 57 `pending`) and **1** `rejected`; minted id NULL for all |
| Recipient ledger rows total | 8,675 | = 8,577 contacted + 98 blocked-but-never-contacted |
| Bundle load run | 1 | one `evidence.source_record` of kind `migration_manifest` |

The three decoded addresses are a **separate loader input file**, kept
alongside the bundle and hashed independently. **The immutable bundle is never
edited to include them.**

V1 suppressions do not distinguish an unsubscribe from a bounce or an operator
decision, so **every Wave 1A block loads with `purpose = all`** — the
fail-safe reading, which stops transactional sends too
([`WORKFLOWS.md`](WORKFLOWS.md) §1.6).

### 7.2 Counts kept as archive facts only

| Item | Count | Disposition |
|---|---|---|
| Sent-recipient evidence edges | 13,622 | archive only; aggregate recorded in the manifest record |
| Recipient parse failures | 11,613 (6,941 informational token-level, 4,672 rows that parsed to zero addresses) | archive only |
| Gmail ingest checkpoint rows | 6,091 | reconciliation **input**, read from the archive; never imported as messages |
| `candidate_review_event` | 42 | archive only |
| `supplier_master` / `_evidence` / `_contact_channel` / `_review_state` | 172 / 246 / 99 / 171 | pending evidence, never automatic CRM truth (§8) |
| `supplier_review_state` rows with no matching supplier | 1 | quarantined evidence, never an organization |
| Total V1 emails in the source database | 221,673 | cold archive |

**No individual address, recipient list or person name from Wave 1A appears in
this documentation.** The counts above are the documented interface; the
addresses live in the bundle and in the loaded rows.

### 7.3 Load gate

A Wave 1A load is accepted only when, after loading: 8,580 `prior_contact`
rows, 704 address blocks, 91 domain blocks — every one of them
`purpose = all` — and **zero** cooldown rows exist from V1 input. Loaders
are idempotent, record every input SHA-256 and count in the manifest source
record and one domain event, and fail closed on any mismatch.

## 8. Data quality and quarantine

- A source record whose subject cannot be resolved, or which contradicts an
  existing accepted fact, is **quarantined**: retained, flagged, excluded from
  every count and every read model, and visible only in a review queue.
- The V1 orphan supplier-review row is quarantined evidence. It never becomes
  an organization.
- The 172 V1 supplier candidates and the ~159 historical quote candidates
  enter as **pending** source records with assertions. **Zero automatic
  promotion**: none of the 171 V1 review rows carries a reviewer or a review
  date, so no promotion rule is satisfied. `is_exclusion` on a V1 supplier row
  is an assertion, not a block.
- Gmail message rows are never quarantined — they are provider facts. A
  message that cannot be parsed keeps its raw `.eml` and records a parse
  failure on the row.

## 9. Rebuildable views

Every dashboard, funnel, pipeline board and count is an **ordinary SQL view**
over the 32 tables. **[V2 DECISION]**

- No projection table, mart, mirror or denormalized copy exists.
- A materialized view requires a measured query-time justification, is a
  disposable cache, and **no foreign key may reference a view of any kind**.
- Dropping and recreating every view changes no business fact.

## 10. Backup principles

**[V2 DECISION]**, **[PLANNED]**

1. Database backups with point-in-time recovery cover the 32 tables.
2. **Database backups do not include Storage objects.** Storage buckets are
   therefore backed up **independently**, on their own schedule, to separate
   storage, with a manifest and hashes.
3. A backup is not a backup until a restore drill has passed
   ([`OPERATIONS.md`](OPERATIONS.md)). Drills cover the database *and* a
   bucket restore.
4. The cold archive is held as **two verified copies** on separate media, each
   with its own hash manifest.
5. Every archive is discoverable through hashes and manifests that live
   *outside* the active database, so nothing must be imported to be found
   later.

## 11. What will never be migrated into active Postgres

**[V2 DECISION]** — exhaustive and binding.

- The V1 SQLite database, in whole or as any table-for-table copy.
- The Outlook/PST mailbox corpus and every derived body representation.
- The 11,613 recipient parse failures.
- The 13,622 sent-recipient evidence edges.
- The 6,091 Gmail ingest checkpoint rows as messages.
- Any V1 mart, mirror, projection or read-model table
  (`commercial_identity`, `commercial_opportunity`, `commercial_procurement*`,
  warm cases, the Postgres mirror, catalog and lead-intel mirrors).
- The V1 `outbound.*` Postgres sidecar mirror.
- Any supplier or historical-quote candidate as canonical CRM truth.
- Any address list, contacted set or suppression set other than the compact
  safety facts enumerated in §7.1.

**Single documented exception.** During a real investigation, an operator may
promote **one** archive row at a time into `evidence.source_record` (kind
`v1_parse_failure` or `v1_evidence_edge`, dedupe key `bundle:row`). A recovered
address becomes a pending `contacted_address` assertion, and only an operator
promotion turns it into `prior_contact` with `source = wave1a_investigation`.
The bundle and its manifest are never modified.
