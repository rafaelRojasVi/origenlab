# OrigenLab V2 — data authority and retention

**Purpose.** Which system owns which fact, what is evidence rather than truth,
where bytes live, how long they live, and what is never migrated.

**This document owns:** the authority and trust matrix; the evidence-vs-truth
boundary; provenance and external identifiers; retention classes; the split
between active PostgreSQL, private Storage and the cold archive; Gmail message
identity and ingestion checkpoints; the Wave 1A and Wave 1B safety counts,
archive hashes and the measured cross-wave safety baseline; data-quality and
quarantine rules; rebuildable views; backup principles; and the exhaustive
list of what will never enter active Postgres.

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
| Was this address ever contacted | `outbound.contact_control` kind `prior_contact` (always `marketing`), plus accepted attempts | the privileged send functions, the reconciler, the Wave 1A loader — never direct DML from a runtime role | No |
| May we send to this address now, for this purpose | `outbound.send_control` + `contact_control` (`block`, `prior_contact`, `cooldown`, each purpose-scoped — truth table in [`WORKFLOWS.md`](WORKFLOWS.md) §1.6) + campaign policy + recipient override | admin command, NDR/unsubscribe handlers, send functions | No |
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
| **Durable commercial truth** | all 33 tables' committed rows | active PostgreSQL | forever; deletion only by an explicit, evented command |
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
| Runtime code at extraction | git `66623060` (the tree production cron executes; older than the `774cc36c` baseline at extraction) |
| Per-file integrity | `SHA256SUMS` covers every file including `manifest.json` |

### 7.1 Corrected safety mapping

| V1 source | Count | V2 target |
|---|---|---|
| Recipient ledger — contacted union | **8,577** | `contact_control(kind=prior_contact, scope=address, purpose=marketing)`, `source = wave1a_union` |
| RFC 2047 decoded addresses absent from that union | **3** | `contact_control(kind=prior_contact, scope=address, purpose=marketing)`, `source = wave1a_rfc2047_addendum` |
| **Permanent prior-contact total** | **8,580** | — |
| `contact_email_suppression` (700) ∪ manual hard-block addresses (5), deduplicated | **704** | `contact_control(kind=block, scope=address)`; `purpose` from the recorded V1 `suppression_reason_code` per the truth table — bounce, non-delivery, invalid-address and explicit do-not-contact codes → `all`; an explicit marketing-unsubscribe code → `marketing`; any other or missing code → `all`, flagged for operator review |
| `contact_domain_suppression` | **91** | `contact_control(kind=block, scope=domain, purpose=marketing)` — campaign, supplier and domain exclusions are marketing-only; `all` only where the recorded `suppression_reason_text` explicitly states a global operational block |
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

**[V1 FACT]** the address suppressions carry a `suppression_reason_code`; the
domain suppressions carry only free `suppression_reason_text`. The loader sets
each block's `purpose` from that recorded reason exactly as the truth table in
[`WORKFLOWS.md`](WORKFLOWS.md) §1.6 prescribes, and **fails closed on doubt: an
address suppression whose reason cannot be safely classified loads as `block`
/ `all` and is flagged for operator review** — never silently as `marketing`.
Reviewing such a row is an admin command that may narrow it to `marketing`
with a reason and an event; nothing widens or narrows a block automatically.
The historical-contact populations — the recipient ledger, Gmail Sent
evidence, accepted campaign recipients and `outreach_contact_state` — are
outreach facts and load only as `prior_contact` / `marketing`; they never
become blocks and never stop a transactional delivery.

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
rows, all `purpose = marketing`; 704 address `block` rows and 91 domain
`block` rows whose `purpose` follows the recorded reason per the truth table,
every address row lacking a safely classifiable reason loaded as `all` and
flagged for review; no `prior_contact` or `cooldown` row with `purpose = all`;
and **zero** cooldown rows from V1 input. The loader records the per-purpose
and flagged-for-review counts, every input SHA-256 and every count in the
manifest source record and one domain event, is idempotent, and fails closed
on any mismatch.

### 7.4 The RFC 2047 addendum — recorded evidence gap

**[V1 FACT]** §7.1 says the three RFC 2047 decoded addresses are "a separate
loader input file, kept alongside the bundle and hashed independently". **That
file was never created.** Only the archive, its `.sha256` sidecar and the
extracted bundle directory exist.

The addresses were not lost. The Wave 1A bundle preserves them at
`reports/parse_failure_summary.json` →
`rfc2047_diagnostic.recovered_addresses_not_in_contacted_union`, the
diagnostic-only decode of the zero-address Sent rows that the bundle README
documents. The addendum is therefore **deterministically re-derivable from the
immutable bundle alone** — no production read is needed, and the bundle is
never edited.

`apps/email-pipeline/scripts/migration/derive_wave1a_rfc2047_addendum.py`
performs that derivation: it verifies every file it reads against the bundle's
own `SHA256SUMS`, re-checks each address as absent from
`derived/recipient_ledger.jsonl.gz`, writes
`<bundle name>_rfc2047_addendum.jsonl` plus an independent `.sha256` sidecar
outside the repository, and prints no address. The derived count is the fact;
an operator expectation can only produce a warning.

The correction this records is documentation-only: §7.1's "kept alongside the
bundle" is the *intent*; the file is produced on demand from the bundle, and
its SHA-256 is recorded in the load run rather than pinned here.

### 7.5 Wave 1B — the September 2026 campaign extract

**[V1 FACT]** — verified from the bundle manifest and its own reconciliation
report on 2026-09-20. The authorized production extraction ran once, and every
value below is measured, not estimated. The bundle is private, owner-only and
outside Git; **nothing has been loaded into V2**.

**Wave 1A is immutable.** Its bundle, hashes and every count in §7, §7.1 and
§7.2 stand exactly as recorded on 2026-09-05 and are never revised, replaced,
regenerated or relabelled. Wave 1B is a **separate, additive** bundle with its
own timestamp, hashes, counts and provenance.

| Item | Value |
|---|---|
| Tool | `apps/email-pipeline/scripts/migration/extract_wave1b_v1_safety_bundle.py` |
| Campaigns | `septiembre18-2026-final`, `septiembre18-2026-wave2` |
| Delta baseline | the Wave 1A snapshot instant, `2026-09-05T04:24:25Z` |
| Bundle | `20260920T171109Z_wave1b_v1_safety_bundle.tar.gz` under `~/data/origenlab-v2-migration/` |
| Archive SHA-256 | `823ec73671809115751c0b354857a667b19382f7ce26db2beb40b02c76304a93` |
| `manifest.json` SHA-256 | `496d832bb01cc81ef93c25a742df0e15e69124b7ec21bdab6f6bfc96c21c0eb1` |
| `content_digest_sha256` | `32080fa414b096c62b913b9b14b42e80ce6ec6b7e53fbc56b7a982320fd74eae` |
| Source database fingerprint | `sqlite_master` `c5d3c2faa0439fb32698d3c8fc3cecf964cdce4727f1c434214a446849d5fcad`, WAL journal mode |
| Extraction (UTC) | `2026-09-20T17:11:09Z`, one deferred read transaction; `PRAGMA data_version` unchanged across the window, no external commit observed, zero changes on the connection |
| Per-file integrity | `SHA256SUMS` covers every data file; `manifest.json` is covered by `content_digest_sha256` and the archive sidecar |
| Free-text policy | no unrestricted operator free text exported (presence flag + one-way digest only) |
| `complete` | `true` — the Sent-history baseline is complete, with no incomplete reasons |

Contents: the campaign identity and lifecycle rows (the subject never leaves
the source database — only its SHA-256 travels), every campaign recipient and
its state, every send attempt and its state, the unresolved `in_flight`
attempts, the remaining candidates, and the post-snapshot **deltas** for
address suppression, domain suppression, outreach contact state, manual
contact status, the three prior-contact sources and their deduplicated union
(§7.5.1). Same exclusions as Wave 1A: no bodies, subjects, attachment bytes,
credentials or database copy — plus two Wave 1B additions.

**No Gmail network call.** The Sent evidence is the already-ingested `emails`
rows in the V1 SQLite database, read through the live outbound gate's own
functions (§7.5.1). The extractor opens no mailbox and no hosted service.

**[V1 FACT] Unrestricted operator free text is not exported.** V1 mixes
structured safety fields with columns nobody constrained — an operator note, a
justification, an evidence paragraph, a raw Gmail API error string — which may
carry third-party names, addresses or quoted message content. Wave 1B exports
only the structured fields a V2 loader needs to reproduce V1 behaviour. Each
free-text column travels as two derived fields instead:

| Column | Table |
|---|---|
| `notes` | `outreach_contact_state` |
| `reason`, `evidence` | `manual_contact_status` |
| `suppression_reason_text` | `contact_email_suppression`, `contact_domain_suppression` |
| `error_detail` | `outbound_send_attempt` (it is `str(exc)` from the Gmail call) |

`<column>_present` records that a value existed; `<column>_sha256` is SHA-256
over the original normalized to Unicode NFC, trimmed, with internal whitespace
collapsed. **The digest proves two values were equal; it cannot reconstruct the
content** — SHA-256 is one-way and the bundle carries no candidate dictionary.
It is provenance, never a recoverable copy, and the original is never written,
logged or printed.

Closed reason codes with defined operational semantics —
`suppression_reason_code`, `block_reason`, `selection_reason`, `error_code` —
travel verbatim, because §7.1's `purpose` truth table reads them. They travel
**only** while the observed value is inside its declared vocabulary; any other
value is unconstrained text and is digested like a note. The full policy is
bundled at `reports/reconciliation.json` → `free_text_policy`.

**Private by construction.** The bundle carries real contact addresses, so the
output root and bundle directory are `0700` and every file, tar member, the
archive and its checksum sidecar are `0600`. Content is written to an
owner-only `O_EXCL` temporary file in the destination directory and then
atomically renamed, so no artifact ever exists at a wider mode and no partial
file appears under a final name. An operator-supplied output root is refused if
it is a symlink, is not owned by the running user, or is group- or
world-writable; otherwise the root **itself** is tightened to `0700` and
nothing inside it is touched. The same rules cover the §7.4 RFC 2047 addendum
and its sidecar.

| Wave 1B measured fact | Count |
|---|---|
| `outbound_campaign` | **2** — `septiembre18-2026-final`, `septiembre18-2026-wave2` |
| `outbound_campaign_recipient` | **2,320** |
| `outbound_send_attempt` | **2,014** |
| Unresolved `in_flight` attempts | **0** |
| Remaining candidates | **279** |
| Δ `contact_email_suppression` | **332** |
| Δ `contact_domain_suppression` | **0** |
| Δ `outreach_contact_state` | **0** |
| Δ `manual_contact_status` | **1** |
| Δ `campaign_accepted` prior-contact addresses | **2,000** |
| Δ `sent_history` prior-contact addresses | **1,538** |
| Δ `outreach_state` prior-contact addresses | **0** |
| Δ combined prior-contact addresses (deduplicated) | **2,075** |

Per campaign:

| Campaign | Recipients | `sent` | `candidate` | `inactive` | Attempts | `accepted` | `failed` | `in_flight` |
|---|---|---|---|---|---|---|---|---|
| `septiembre18-2026-final` | 1,020 | 1,000 | 0 | 20 | 1,011 | 1,000 | 11 | 0 |
| `septiembre18-2026-wave2` | 1,300 | 1,000 | 279 | 21 | 1,003 | 1,000 | 3 | 0 |

No recipient is in `blocked`, `bounced`, `replied`, `reserved` or `selected` in
either campaign. The **279 remaining candidates** are the `candidate`-state
recipients of `septiembre18-2026-wave2`: selected, never attempted, and still
paused. **Zero unresolved `in_flight` attempts** — every attempt reached a
terminal result, so no send is in an unknown state.

**Sent preflight and freshness.** The Sent-history baseline is `complete: true`
with no incomplete reasons and **zero Gmail network calls**: 5,097 canonical
Gmail Sent rows were scanned in the already-ingested `emails` table, 1,518 of
them dated after the Wave 1A snapshot, yielding 1,538 post-snapshot recipients
against a whole-history universe of 8,574 parsed recipients. The newest
ingested Sent row is `2026-09-17T18:24:48Z`, later than the newest campaign
send attempt, so the ingest is not stale. The source database and the operator
manifest were fingerprinted before and after the read: the database inode,
size and mtime and the manifest's SHA-256 are identical on both sides. The
ingest cron was observed, never paused, stopped or modified.

**The suppression and manual-control deltas are a different control class** and
are never folded into the prior-contact union. All **332** new address
suppressions carry a structured bounce reason code — **187** `bounce_other` and
**145** `bounce_no_such_user` — so this delta is bounce-driven by its own
recorded codes, not by inference. There are **no** new domain suppressions and
**no** new outreach-state rows. The single new `manual_contact_status` row is a
`hold`, not a bounce.

**What the 2,075 are, and are not.** They are **post-snapshot prior-contact
evidence**: addresses that V1 records as already written to after
`2026-09-05T04:24:25Z`, by the campaign machinery, by ordinary mail from the
operator's mailbox, or both. They are **not** "new prospects", not leads, not
CRM identities and not blocks. They load as `prior_contact` / `marketing` and
never stop a transactional delivery. 880 of them are new relative to the Wave
1A safety baseline and 1,195 were already in it; §7.5.3 measures which evidence
route each figure came from.

Every count is measured from the database inside the read transaction and
re-measured independently in `reports/reconciliation.json`; each of the ten
recorded checks has `extracted == measured`. No expected value — 2,000, 279 or
any other — is treated as truth anywhere in the tool; an operator expectation
can only raise a warning, and the run recorded no warnings.

#### 7.5.1 The combined outbound-safety baseline

**[V1 FACT] The prior-contact delta has three sources, not one.** An earlier
draft of this section defined it as accepted campaign attempts plus recipients
in `sent` / `bounced` / `replied`. That definition is **insufficient**: it sees
only addresses the campaign machinery touched and omits the ordinary mail an
operator sends by hand from `contacto@origenlab.cl` after the Wave 1A snapshot.
Those recipients are prior contacts too — the live cold-export gate already
blocks them under `candidate_export_gate.REASON_SENT_HISTORY`.

Wave 1B therefore reports five things separately, and derives the fifth from
the three that are prior-contact sources:

| # | Quantity | Bundle file |
|---|---|---|
| 1 | campaign execution facts for the two September campaigns | `exact/*.jsonl` |
| 2 | `campaign_accepted` — accepted attempts and recipients in `sent`/`bounced`/`replied` after the snapshot | `delta/campaign_prior_contact.jsonl` |
| 3 | `sent_history` — recipients of canonical Gmail **Sent** rows dated after the snapshot | `delta/sent_history_prior_contact.jsonl` |
| 4 | `outreach_state` — post-snapshot `outreach_contact_state` rows in `contacted`/`replied`/`snoozed` | `delta/outreach_prior_contact.jsonl` |
| 5 | suppressions and manual holds | `delta/contact_*_suppression.jsonl`, `delta/manual_contact_status.jsonl` |
| — | the deduplicated combined prior-contact delta | `delta/combined_prior_contact.jsonl` |

**combined prior-contact delta = deduplicate( `campaign_accepted` ∪
`sent_history` ∪ `outreach_state` )** over the normalized migration address.
Every entry records its `source_categories`, so an address reached by more than
one route stays traceable after the load; suppressions and manual holds are a
different control class and are never folded into this union.

**The Sent evidence is read through the live outbound gate, not re-implemented.**
The extractor imports the gate's own functions, so it cannot drift away from
what the gate blocks on — a change to either moves both:

| Reused | What it fixes |
|---|---|
| `marketing_export_context.sent_history_where` | the one definition of "a Sent row of this mailbox": the `source_file` LIKE pattern and the `folder IN` set |
| `marketing_export_context.load_sent_recipient_norms` | the canonical full Sent-recipient universe |
| `business_mart.emails_in` | address extraction and lowercasing |
| `outbound_sent_preflight.probe_sent_history` / `evaluate_sent_history_preflight` | the fail-closed rule |
| `outbound_core.resolve_outbound_gmail_user` / `resolve_outbound_sent_folders`, `DEFAULT_GMAIL_USER_FALLBACK`, `DEFAULT_SENT_FOLDERS` | mailbox identity and folder selection |
| `candidate_export_gate.normalize_export_email` | the one normalized migration address form |

**It fails closed, with no override.** Unlike the export CLIs there is no
`--allow-empty-sent-history` equivalent, because a migration baseline that
silently omits prior contacts is worse than no baseline. The run is refused
when the `emails` table is absent, the mailbox resolves to nothing, no ingested
row matches the Sent folder labels, zero recipients parse, a Sent row carrying
recipients has a missing or unparsable `date_iso` (it cannot be placed on
either side of the snapshot), or the dated read and the canonical read
disagree about which addresses exist. `date_iso` is compared as an instant in
UTC, never as a string; a naive value is read as UTC, the standard reading of
RFC 5322 `-0000`.

The bundle records the mailbox identity and the folder labels that were read —
no credential and no private path — at `reports/sent_history.json`, together
with the list of canonical functions reused and `gmail_network_calls: 0`. When
the newest ingested Sent row predates the newest campaign send attempt the
ingest is stale, and the tool says so: `complete: false` with the reason, plus
a run warning. **The tool never claims the combined baseline is complete when
canonical Sent evidence is missing or stale.**

`reports/reconciliation.json` → `combined_prior_contact` carries the raw
per-source totals, every pairwise and three-way overlap, the count of addresses
with more than one source, and the deduplicated total. The union is
re-verified independently: a duplicated address in it, or a source address
missing from it, refuses the run.

**[V1 FACT] The measured within-Wave-1B arithmetic.** The three raw sources
total **3,538** addresses before deduplication — 2,000 `campaign_accepted`,
1,538 `sent_history`, 0 `outreach_state`. Their only non-empty overlap is
`campaign_accepted ∩ sent_history` = **1,463**; `campaign_accepted ∩
outreach_state`, `sent_history ∩ outreach_state` and the three-way intersection
are all **0**. The deduplicated combined delta is therefore **2,075**, and
1,463 of its entries carry more than one `source_category`. The 75 addresses by
which `sent_history` exceeds that overlap are the ordinary hand-sent mail an
earlier draft of this section would have missed.

The combined baseline is **derived**, never asserted, and it never overwrites
either wave's raw facts. It is stated as four distinct quantities per control
class, so a reader can always tell a raw fact from a deduplicated total:

| Quantity | Meaning | Value |
|---|---|---|
| Raw Wave 1A | the §7.1 count, unchanged | 8,580 prior contact · 704 address blocks · 91 domain blocks |
| Raw Wave 1B | the Wave 1B delta count for the same class | 2,075 prior contact · 332 address suppressions · 0 domain suppressions |
| Overlap | addresses/domains present in **both** waves | **1,195** prior contact (§7.5.2) · **0** address suppressions · **0** domain suppressions (§7.5.4) |
| Combined (deduplicated) | raw 1A + raw 1B − overlap | **9,460** prior contact (§7.5.2) · **1,032** address suppressions · **91** domain suppressions (§7.5.4) |

The Wave 1A *address block* count of 704 in §7.1 is the loader's figure — the
700 suppression rows plus the 5 manual hard blocks it folds in. §7.5.4 measures
the suppression **tables** and leaves manual status separate, so its Wave 1A
address figure is 700, not 704. Both are correct about different things.

Wave 1B rows load with their own `source` labels
(`wave1b_prior_contact`, `wave1b_block`), so provenance stays separable after
the load; the per-row `source_categories` keep the three prior-contact routes
distinguishable inside that label. The §7.3 Wave 1A load gate is unchanged: it governs the Wave 1A load
and is not restated in terms of the combined totals.

#### 7.5.2 The measured cross-wave safety baseline

**[V1 FACT]** — measured on 2026-09-20 by
`apps/email-pipeline/scripts/migration/reconcile_wave1a_wave1b_safety.py` from
the immutable artifacts alone. No database, mailbox or hosted service was
opened, neither bundle was modified, and no address appears in the report.

`8,580 + 2,075 = 10,655` is an **upper bound, not a total**: the two waves
share addresses, and the only honest way to learn how many is to reconstruct
both sets and intersect them. The reconciliation does exactly that — it rebuilds
Wave 1A's safety set from `derived/recipient_ledger.jsonl.gz` plus the §7.4
addendum, rebuilds Wave 1B's delta from `delta/combined_prior_contact.jsonl`,
and compares each reconstructed size against the count its own manifest and
reconciliation report record before measuring anything.

```text
wave1a_safety     = deduplicate( wave1a_contacted_union U wave1a_rfc2047_addendum )
cross_wave_safety = deduplicate( wave1a_safety U wave1b_combined_prior_contact )
```

| Quantity | Count |
|---|---|
| Wave 1A contacted union (§7.1) | **8,577** |
| Wave 1A RFC 2047 addendum (§7.4) | **3** |
| **Wave 1A safety, combined** | **8,580** |
| Wave 1B combined prior-contact delta (§7.5.1) | **2,075** |
| **Cross-wave intersection (measured)** | **1,195** |
| Wave 1A only | **7,385** |
| Wave 1B only | **880** |
| **Final deduplicated cross-wave safety union** | **9,460** |

The three parts partition the union (7,385 + 1,195 + 880 = 9,460), and
inclusion-exclusion holds (8,580 + 2,075 - 1,195 = 9,460). The measured overlap
is 1,195 addresses — **1,195 fewer** than the naive sum would have loaded as
distinct rows.

**1,195 is not a campaign recontact count.** It is the overlap of a
*deduplicated union of three evidence routes* with the Wave 1A safety set. An
address reached only by ordinary hand-sent mail lands in it exactly like one a
September campaign sent to. The per-route measurement is §7.5.3, and the
campaign figure there is **1,158**, not 1,195.

| Item | Value |
|---|---|
| Reconciliation report | `<wave 1A bundle>__<wave 1B bundle>_cross_wave_safety_reconciliation_v2.json` under `~/data/origenlab-v2-migration/`, `0600` beside both bundles |
| Report SHA-256 | `ed23ff6f33a175f4e51b2fc8cf10a5f03971469bc857240e18f9791ccd182c46` |
| Superseded report | the `v1` report, SHA-256 `45c961034a4677064cffcb91617c0167036cb34dd8d25d82bb1d1790b0c3184b`, is **retained unchanged** beside it — it carried the same eight combined counts and none of the source-specific ones |
| Address normalization | `candidate_export_gate.normalize_export_email` — the same one form Wave 1B used |
| Inputs verified | Wave 1A: 26 files against its own `SHA256SUMS`, plus the §7 `manifest.json` and archive hashes; Wave 1B: 18 files, plus its recomputed `content_digest_sha256` and archive sidecar; the addendum against its own sidecar |
| Invariants | 12, all holding; a failure refuses the run |

**This is the migration safety baseline, not CRM identity truth.** The 9,460
addresses are prior-contact **evidence** for the outbound send gate: what V1
can prove was already written to. They are not people, not organizations, not
`crm` identities and not a suppression list, and nothing here promotes any of
them to CRM truth — that remains an operator promotion command (§2). Blocks and
manual holds are a separate control class and are excluded from this union by
construction. The number is derived and re-derivable; it never overwrites
either wave's raw facts, and §7's Wave 1A counts stand exactly as recorded.

**Not yet loaded.** The baseline is measured and available; no row from either
wave has been written to V2.

#### 7.5.3 Which evidence route produced the overlap

**[V1 FACT]** — measured 2026-09-20, same run and same artifact as §7.5.2.

§7.5.2's 1,195 is a single number about a deduplicated union of three routes.
Read alone it invites one wrong conclusion — that 1,195 addresses the September
campaigns sent to had already been contacted before the snapshot. Each route is
therefore intersected with the Wave 1A safety set in its own right:

| Wave 1B route | Addresses | Already in Wave 1A safety | New relative to Wave 1A |
|---|---|---|---|
| `campaign_accepted` | 2,000 | **1,158** | 842 |
| `sent_history` | 1,538 | **973** | 565 |
| `outreach_state` | 0 | 0 | 0 |
| **combined (deduplicated)** | **2,075** | **1,195** | **880** |

**The exact figure is 1,158**: that many of the 2,000 September accepted
campaign recipients were already in the pre-September Wave 1A safety baseline.
The remaining 842 were not.

**These rows do not add up, and must not be added.** 1,158 + 973 = 2,131, which
exceeds both the combined 1,195 and the 2,075 delta, because 1,463 addresses
carry *both* a campaign and a Sent-history source. Each per-route figure is a
separate measurement of the same union, never a share of it.

An exact partition of the 2,075 by which routes hold each address, so evidence
reached twice is never counted twice:

| Source membership | Addresses | Already in Wave 1A safety | New relative to Wave 1A |
|---|---|---|---|
| campaign evidence only | 537 | 222 | 315 |
| Sent-history evidence only | 75 | 37 | 38 |
| both campaign and Sent-history | 1,463 | 936 | 527 |
| any cell involving `outreach_state` | 0 | 0 | 0 |
| **total** | **2,075** | **1,195** | **880** |

Per campaign, from the `campaign_ids` each `delta/campaign_prior_contact.jsonl`
row carries:

| Campaign | Accepted addresses | Already in Wave 1A safety | New relative to Wave 1A |
|---|---|---|---|
| `septiembre18-2026-final` | 1,000 | 696 | 304 |
| `septiembre18-2026-wave2` | 1,000 | 462 | 538 |

**The two campaigns are disjoint**: zero addresses appear in both, so here the
two rows do sum — 696 + 462 = 1,158 and 1,000 + 1,000 = 2,000. That is a
measured property of these two campaigns, not a rule.

#### 7.5.4 The cross-wave suppression baseline

**[V1 FACT]** — measured 2026-09-20, same run and same artifact as §7.5.2.

Suppressions and manual holds are a **different control class** from prior
contact and are never folded into the §7.5.2 union. They are reconciled the
same way, from the `contact_email_suppression` and `contact_domain_suppression`
rows of each bundle.

| Address suppressions | Count |
|---|---|
| Wave 1A (`exact/contact_email_suppression`) | **700** |
| Wave 1B delta (`delta/contact_email_suppression`) | **332** |
| Intersection | **0** |
| Wave 1A only | 700 |
| Wave 1B only | 332 |
| **Final deduplicated address-suppression union** | **1,032** |

**The intersection is zero.** Every one of the 332 new suppressions is an
address V1 had not suppressed before the snapshot, so here — and only here —
the naive sum is the total.

Aggregate reason-code matrix, counted from the code each row actually records.
No code is mapped to a purpose, a scope or a cause by this measurement; the
§7.1 truth table does that for the loader.

| `suppression_reason_code` | Wave 1A | Wave 1B delta |
|---|---|---|
| `bounce_no_such_user` | 374 | 145 |
| `bounce_other` | 238 | 187 |
| `bounce_access_denied` | 2 | 0 |
| `manual_do_not_contact` | 86 | 0 |
| **total** | **700** | **332** |

| Domain suppressions | Count |
|---|---|
| Wave 1A | **91** |
| Wave 1B delta | **0** |
| Intersection | **0** |
| **Final deduplicated domain-suppression union** | **91** |

V1 domain suppressions carry only free reason text and never a reason code, so
this class has no reason matrix.

**Manual contact status is kept separate and mapped by nobody here.** Wave 1A
holds 9 rows — 5 `inactive`, 4 `active`; the Wave 1B delta holds 1, a `hold`.
§7.1 records that `inactive` and `hold` are the V1 *manual hard-block* statuses
the Wave 1A loader folds into its 704 address blocks, but that is a **loader**
mapping applied with an explicit source vocabulary. The reconciliation counts
these rows by recorded status and stops: it does not reinterpret a manual hold
as a suppression, and the 1,032 above is the suppression-table union alone.

### 7.6 The Wave 1A/1B → V2 import

**[V1 FACT]** — measured 2026-09-20 against `main` @ `56812a0b` by
`apps/email-pipeline/scripts/migration/import_waves_into_v2.py`, from the
verified artifacts of §7, §7.4 and §7.5 alone. **V2 PostgreSQL has not been
populated outside a disposable local test database**, and no hosted project was
contacted (§7.5.2, [`STATUS.md`](STATUS.md) §2.5).

This is the first code that writes to a V2 schema. It writes to exactly three
tables, and **never to `crm.*`**.

#### 7.6.1 Where the historical data lands, and why not in `crm.*`

The artifacts are an outbound **safety** baseline — what V1 can prove it already
wrote to — not CRM identity truth (§7.5.2). The mapping therefore honours five
rules that are already canonical, rather than inventing a sixth:

| Rule | Where it comes from | How the import obeys it |
|---|---|---|
| An email address is a contact point, not a person | [`DOMAIN.md`](DOMAIN.md) §2.5 | no `crm.person` row is ever planned |
| A recipient is not a prospect or a lead | §7.5.2 | no `crm.organization_relationship` row is ever planned |
| An address with no known owner stays unresolved | [`DOMAIN.md`](DOMAIN.md) §7 #25 | it becomes an `evidence.assertion` of kind `contacted_address`, `resolution = 'unresolved'` |
| An accepted delivery is an outbound event, not consent | [`WORKFLOWS.md`](WORKFLOWS.md) §1.6 | every prior-contact row is `purpose = marketing`; `crm.contact_point` has no consent column and the import adds none |
| Promotion to CRM truth is an operator command | [`MIGRATION.md`](MIGRATION.md) §5 slice 2 | the importer asserts `crm.*` row count is unchanged by an apply, and refuses if it moved |

`evidence.assertion` with `resolution = 'unresolved'` is the canonical state for
"an address was contacted and we do not know whose it is". Creating 9,460
`crm.contact_point` rows instead would assert an identity nobody confirmed, and
would have to be undone by hand before slice 2 could run.

#### 7.6.2 Field-level mapping

Every mapping is deterministic and idempotent: the same artifacts always produce
the same plan, and the idempotency key is the natural key the database already
enforces, so a re-run conflicts instead of duplicating.

| Source artifact | Source field | Destination | Transformation | Idempotency key | Unresolved / null | Conflict behaviour | Status |
|---|---|---|---|---|---|---|---|
| 1A `derived/recipient_ledger` (`in_contacted_union`) | `email_norm` | `outbound.contact_control` `kind=prior_contact, scope=address, purpose=marketing, source=wave1a_union` | `candidate_export_gate.normalize_export_email` | `(scope, value_norm, kind, purpose)` | unnormalizable → reject, never repaired | union label wins over addendum | **implemented** |
| 1A RFC 2047 addendum (§7.4) | `address` | same, `source=wave1a_rfc2047_addendum` | same | same | same | keeps its own label, so provenance stays separable | **implemented** |
| 1B `delta/combined_prior_contact` | `address`, `source_categories` | same, `source=wave1b_prior_contact` | same; the union is read as measured, never recomputed | same | same | an address in both waves yields **one** row; both waves recorded on the assertion | **implemented** (§7.6.4 gap 1 closed) |
| 1A `exact/contact_email_suppression` | `email`, `suppression_reason_code` | `outbound.contact_control` `kind=block, scope=address, source=wave1a_suppression` | `purpose` from the §7.1 truth table | same | missing/unknown code → `purpose=all` **and** `needs_review` | never silently `marketing` | **implemented** |
| 1A `exact/manual_contact_status` (`inactive`/`hold`) | `email_norm`, `status` | same | folded in as a hard block | same | — | a suppression row already present wins; the manual status never widens or narrows it | **implemented** |
| 1B `delta/contact_email_suppression`, `delta/manual_contact_status` | same | same, `source=wave1b_block` | same | same | same | Wave 1A wins the label on the measured-zero intersection (§7.5.4) | **implemented** (§7.6.4 gap 1 closed) |
| 1A `exact/contact_domain_suppression` | `domain_norm` | `outbound.contact_control` `kind=block, scope=domain, purpose=marketing` | lower-cased, shape-checked | same | unnormalizable → reject | — | **implemented** |
| every verified artifact | name + SHA-256 | `evidence.source_record` `kind=migration_manifest` | canonical JSON payload | `dedupe_key` | — | — | **implemented** |
| the cross-wave union | address | `evidence.assertion` `kind=contacted_address` | records waves, `source_categories`, supplier classification | `(source_record_id, kind, value_norm)` | `resolution='unresolved'` always | both waves recorded on one row | **implemented** |
| 1A/1B `outbound_campaign_recipient` | `institution_name` | `evidence.assertion` `kind=organization_name` | trimmed, whitespace-collapsed, lower-cased | same | absent → no row; **never** an invented name | distinct names only | **implemented** |
| 1A `exact/supplier_master` | `trade_name`, `domain_norm` | `evidence.assertion` `kind=supplier_candidate` | — | same | — | pending evidence, never automatic CRM truth (§7.2, §8) | **implemented** |
| 1A/1B `exact/outbound_campaign` | `sender_email`, `sender_name` | `comms.mailbox` | normalized; `is_production_sender=false`, `authorization_state='unauthorized'` | `address_norm` | — | — | **implemented** (§7.6.4 gap 2 closed) |
| 1A/1B `exact/outbound_campaign` | `campaign_id`, `name`, `target_attempt_count` | `outbound.campaign`, `status='archived'` | `max_sends ← target_attempt_count` | `campaign_id` | `recontact_interval_days` has **no V1 source** | — | **implemented** (§7.6.4 gap 2 closed) |
| 1A/1B `exact/outbound_campaign_recipient` | `email_norm`, `state` | `outbound.campaign_recipient` | `sent→sent`, `bounced→bounced`, `replied→replied`, `candidate`/`selected`→`snapshotted`, `inactive`/`blocked`→`excluded` | `(campaign_id, address_norm)` | identity columns stay `NULL` | an unmapped state is a **reject**, never a guess | **implemented** (§7.6.4 gap 2 closed) |
| 1A/1B `exact/outbound_send_attempt` | `email_norm`, `result`, `attempted_at` | `outbound.send_attempt` | `accepted→(accepted, pending)`, `failed`/`rejected`→`(rejected, n/a)`; minted id `NULL` (§7.1) | `(campaign_id, address_norm, v1 attempt id)` | missing `attempted_at` → `accepted_at` NULL, state preserved | an unmapped result is a **reject** | **implemented** (§7.6.4 gap 2 closed) |

**Never mapped:** message bodies, subjects (only a SHA-256 travels for Wave 1B),
attachment bytes, operator free text (§7.5 exports a presence flag and a one-way
digest instead), and the archive-only populations of §7.2.

#### 7.6.3 The import safety contract

| Guarantee | How it is enforced |
|---|---|
| Dry-run by default | without `--apply` **no database connection is opened at all** |
| Local only | `migration/v2_import/target.py` refuses any non-loopback host, any hosted-provider marker and any host-less DSN. **There is no override flag**, and a test asserts none exists |
| Verified input | every bundle is checked against its own `SHA256SUMS`, every sidecar against its `.sha256`, and every archive and manifest against the hash this document pins. A symlinked, foreign-owned or group/world-writable artifact refuses |
| Fails closed on count drift | the planned union is reconciled against the independently measured §7.5.2 report; a mismatch refuses rather than loading a safety set of the wrong size |
| Fails closed on schema drift | the apply path refuses unless the target carries the Slice 0 tables **and** the `contact_control_source_check` vocabulary it compiled against |
| Transactional | one transaction; an injected mid-batch failure leaves zero rows (proven by test) |
| Idempotent | `ON CONFLICT DO NOTHING` on the natural key; a second run inserts **0** |
| Non-destructive | no `TRUNCATE`, `DROP`, `DELETE` or `UPDATE` exists in the apply path (proven by an AST test over its SQL) |
| `crm.*` untouched | the row count is measured before and after; a change refuses the transaction |
| PII-safe output | the aggregate report is re-checked for address-shaped values before it is returned; recipient-level rejects go to a `0600` artifact outside Git and are never printed |
| No network | the package imports no HTTP, Gmail, Supabase or cloud client, and touches no send flag (both proven by test) |

#### 7.6.4 Two structural gaps, found and closed

The importer's first dry run measured two blockers. Both were additive, both sat
in a frozen Slice 0 migration, and both were decided by the owner on 2026-09-20
and closed by
`supabase/migrations/20260920190000_slice0_wave1b_source_labels_and_archived_recontact_interval.sql`.
The reasoning is recorded here because the argument outlives the blocker.

**Gap 1 — `outbound.contact_control.source` had no Wave 1B labels.**
§7.5.1 requires that "Wave 1B rows load with their own `source` labels
(`wave1b_prior_contact`, `wave1b_block`)". `contact_control_source_check` was a
closed CHECK listing only the four `wave1a_*` labels plus the runtime handlers,
so the loader had no correct label: a Wave 1B row under a `wave1a_*` label would
misattribute its provenance, which is the one thing that sentence exists to
prevent. It blocked 1,213 rows. **Closed** by adding both labels. The vocabulary
stays closed — a further wave is still a migration.

**Gap 2 — `outbound.campaign.recontact_interval_days` had no V1 source.**
It was `NOT NULL CHECK (>= 1)`, and V1 has no recontact-interval concept at all:
§7.1 records zero cooldown rows carried from V1 for exactly this reason. Any
value would have been invented and would read as recorded V1 policy in every
later query. It blocked all 3 campaigns and therefore 3,481 recipients and 3,141
attempts. **Closed** by a fourth `archived`/`cancelled` carve-out, beside the
approval, content and audience-criteria shapes the table already makes for these
same historical campaigns. A campaign that can still send is unaffected: the
value remains mandatory for it.

**Two things the model turned out to lack a key for**, handled in the loader
rather than by a third migration:

- `outbound.campaign` has no natural-key unique constraint, so `ON CONFLICT` is
  a no-op there. The apply path looks a campaign up on `(mailbox_id, name)`
  before writing; without that a second run created a second campaign and a
  second copy of its whole audience and ledger.
- `outbound.send_attempt` has no natural key either — a migrated V1 row mints no
  RFC 822 id (§7.1), and one recipient may legitimately have several attempts.
  The ledger is reconciled **per recipient by count**: if V1 records M attempts
  and the table holds N, only the missing M − N are written. That is idempotent
  without inventing a key and without collapsing genuine repeat attempts.

**One mapping fails closed rather than inventing an instant.**
`send_attempt_accepted_shape` requires `accepted_at`, so an accepted V1 attempt
carrying no `attempted_at` cannot be represented without fabricating a date. It
is **rejected** and kept in the private reject artifact — the same rule the Wave
1B extractor applies to an undateable Sent row (§7.5.1). Over the real artifacts
this rejects **0** rows; every recorded attempt is dated.

#### 7.6.5 Measured dry-run reconciliation

Every figure below is the importer's own count over the real artifacts, and each
reconciled class is checked against the independently measured §7.5.2 report.

| Class | Count | Reconciles to |
|---|---|---|
| Input prior-contact union | **9,460** | §7.5.2 `cross_wave_safety_union` |
| — Wave 1A sourced | 8,580 | §7.5.2 `wave1a_safety_combined` |
| — Wave 1B only | 880 | §7.5.2 `wave1b_only` |
| Cross-wave overlap (conflicts) | **1,195** | §7.5.2 `cross_wave_intersection` |
| Input address-suppression union | **1,032** | §7.5.4 |
| Manual hard blocks folded in | 5 | §7.1 / §7.5.4 |
| Address blocks total | 1,037 | 1,032 ∪ 5 |
| Domain-suppression union | **91** | §7.5.4 |
| Existing matched contact points | **0** | `crm.*` is empty |
| New unresolved contact points | **9,460** | — |
| Existing matched organizations | **0** | `crm.*` is empty |
| Unresolved organization identities | 1,816 | distinct `institution_name` |
| Supplier/provider contacts | **289** | §7.6.6 |
| Prospect/customer-classified contacts | **0** | nothing qualifies without an operator |
| Unclassified contacts | 9,171 | 9,460 − 289 |
| Campaigns | 3 | §7.1 (1) + §7.5 (2) |
| Campaign memberships | 3,481 | 1,161 + 2,320 |
| Accepted deliveries | 3,126 | 1,126 + 2,000 |
| Failed attempts | 15 | 1 + 14 |
| Candidate-only memberships | **279** | §7.5 — still paused, never converted |
| Manual-review records | 0 | every observed reason code is classifiable |
| Rejects | **0** | every input row mapped |
| Rows written by an apply | **28,666** | the sum of the applicable tables below |
| `crm.*` rows | **0** | asserted before and after |

**A prior-contact row and its `contacted_address` assertion are two relational
representations of one fact, never two contacts** — the classes are equal at
9,460 by construction, and the reconciliation asserts it.

**Applied to the disposable local database**, the Wave 1A subset loads and the
the §7.3 load gate is green: 8,580 `prior_contact` rows all `purpose=marketing`
(8,577 `wave1a_union` + 3 `wave1a_rfc2047_addendum`), 704 address blocks, 91
domain blocks, zero `prior_contact` or `cooldown` with `purpose = all`, and zero
cooldown rows. A second apply inserted **0** rows.

#### 7.6.6 The supplier failure mode, handled generically

A provider contact must not become a marketing prospect merely because campaign
history exists. That is decided by **role and eligibility, never by an address
literal**: the importer indexes `supplier_master.domain_norm` and
`supplier_contact_channel.value_normalized` from the Wave 1A bundle and
classifies every prior-contact address with
`marketing_supplier_domains.is_supplier_email_domain` — the same function the
live outbound gate uses. **289** of the 9,460 classify as supplier/provider, and
`outbound_v2.eligibility` independently refuses them with `policy_supplier`; a
test asserts both agree, and that any address under a recorded supplier domain
classifies, not only the ones already seen.

Supplier evidence loads as `evidence.assertion` of kind `supplier_candidate`,
`resolution = 'unresolved'` — pending evidence, never automatic CRM truth
(§7.2, §8).

#### 7.6.7 What has to happen before any real V2 data load

1. **Review this import against a local Supabase stack**, not only the plain
   PostgreSQL container the counts above came from — the Slice 0 evidence suite
   ([`OPERATIONS.md`](OPERATIONS.md) §4.1) is the gate that exercises roles, RLS
   and grants, and CI runs it on every change under `supabase/**`.
2. Then a **reviewed staging load** — still not production, which remains blocked
   on the undecided RPO/PITR posture ([`OPERATIONS.md`](OPERATIONS.md) §4.3).
   Adopting a hosted project is itself a separate, untaken decision
   ([`STATUS.md`](STATUS.md) §2.5).
3. **Nothing reads these rows yet.** The evidence and safety rows have no
   consumer: promotion from `evidence.assertion` to `crm.*` is the slice 2
   operator command ([`MIGRATION.md`](MIGRATION.md) §5), and the send predicate
   that would read `outbound.contact_control` is slice 5.
4. Dashboard contact cards and the campaign/activity timeline are a **later**
   step. **This slice does not make the dashboard CRM complete**, and nothing
   here reads or writes an operator-facing surface.

## 8. Data quality and quarantine

- A source record whose subject cannot be resolved, or which contradicts an
  existing accepted fact, is **quarantined**: retained, flagged, excluded from
  every count and every read model, and visible only in a review queue.
- The V1 orphan supplier-review row is quarantined evidence. It never becomes
  an organization.
- The 172 V1 supplier candidates and the historical quote candidates enter as
  **pending** source records with assertions. **Zero automatic promotion**:
  none of the 171 V1 review rows carries a reviewer or a review date, so no
  promotion rule is satisfied. `is_exclusion` on a V1 supplier row is an
  assertion, not a block.
- **[V1 FACT] Historical quote candidates — measured census, 2026-09-09.** A
  read-only census over all Sent candidates **supersedes the earlier "~159"
  estimate**, which was never measured:

  | Population | Count |
  |---|---|
  | Total Sent candidates | 9,831 |
  | Explicit customer-quote candidates | **207** |
  | Ambiguous | 4,351 |
  | Supplier RFQ | 442 |
  | Classified `internal_only` | 4,831 |

  **The `internal_only` figure is not what it appears to be.** Of it, **4,668
  rows are legacy-mbox messages whose recipient headers contain no `@`-shaped
  token at all** — the PST→mbox conversion emitted display names with the SMTP
  address stripped, for the era 2017-04-24 → 2020-03-13. Only ~98 are genuinely
  internal. The V1 exporter conflates "internal" with "unparseable" in one
  silent skip and counts neither, so **no count derived from it is a count of
  internal mail**. Those addresses are likely recoverable from the message body
  or the on-disk mbox original.

  **Consequence for this migration: the number of historical quote candidates
  entering as evidence is not yet settled**, and no export should be run until
  the exporter separates and counts those two populations. `207` is the floor,
  not the answer.
- Gmail message rows are never quarantined — they are provider facts. A
  message that cannot be parsed keeps its raw `.eml` and records a parse
  failure on the row.

## 9. Rebuildable views

Every dashboard, funnel, pipeline board and count is an **ordinary SQL view**
over the 33 tables. **[V2 DECISION]**

- No projection table, mart, mirror or denormalized copy exists.
- A materialized view requires a measured query-time justification, is a
  disposable cache, and **no foreign key may reference a view of any kind**.
- Dropping and recreating every view changes no business fact.

## 10. Backup principles

**[V2 DECISION]**, **[PLANNED]**

1. Database backups with point-in-time recovery cover the 33 tables.
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
promotion turns it into `prior_contact` / `marketing` with
`source = wave1a_investigation`.
The bundle and its manifest are never modified.
