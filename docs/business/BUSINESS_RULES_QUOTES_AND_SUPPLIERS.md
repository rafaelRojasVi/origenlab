# Business rules: quotes (cotizaciones) and supplier research

Status: canonical  
Owner: project-maintainers  
Last reviewed: 2026-09-21

Formal **business policy** for OrigenLab commercial work (quotes and supplier research). This doc is **source of truth for policy**; Word templates remain **presentation**. When code or DB schemas exist, they must not contradict this file without an explicit decision and doc update.

**Durable quote schema:** CRM-Q1 (2026-08) shipped `commercial.customer_quote` + revision 1 + the Google Drive workspace (folder + working copy of the master template, provisioned from the dashboard with a transactionally allocated quote number). Google Sheets remains the editing authority for quote content in V1 — no lines, costs, pricing, or cell ingestion; the spreadsheet is not the CRM database, and the CRM stores only safe Drive references and provisioning state. Quote-number activation is an explicit configuration decision (`quote_numbering_not_configured` fail-closed until then). Supplier offers and quote lines remain future work per [`docs/architecture/TARGET_COMMERCIAL_ARCHITECTURE.md`](../architecture/TARGET_COMMERCIAL_ARCHITECTURE.md) — this file no longer proposes a separate schema.

**Related:** `[apps/web/docs/company-scope.md](../../apps/web/docs/company-scope.md)` (tone, contact, cotización prompts, [operational intake checklist](../../apps/web/docs/company-scope.md#datos-a-solicitar-operativo)).

---

## 1. Project-wide truth rule (non-negotiable)

**Do not send, publish, or generate commercial claims that are not confirmed** (or explicitly marked as *pending / not applicable*).

Concretely, unless **confirmed** or **explicitly flagged as unconfirmed**:

- Do **not** state or imply: specific **brands**, **warranties**, **stock**, **lead times**, **technical specifications**, **SLAs**, **exclusivity**, or **partnerships**.
- **Duplicate** master templates; **replace only** bracketed / placeholder fields; do **not** free-form add commercial facts.

This rule applies to: internal checklists, any quote generator, LLM prompts, and DB-backed workflows.

---

## 2. Quote (cotización) policy rules

### 2.1 Template discipline

- Work from a **duplicated** master file; change **only** intended placeholders.
- **Provenance:** retain **intake source**, **template version**, **author**, and **generation timestamp** for every quote. CRM-Q1 records template reference, creating operator, and timestamps on `commercial.customer_quote_revision`; intake-source provenance remains manual until quote intake tooling exists.

### 2.2 "Ready to send" gates

Until enforced in software, these are **manual policy**; implement validation in the durable quote model when it ships.

| Gate             | Requirement                                                                                                                                                                                     |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Minimum intake   | Required client/request fields present (see §3).                                                                                                                                                |
| Commercial terms | **Delivery**, **payment**, **validity**, **taxes (e.g. IVA)**, **warranty**, **installation/startup**: each is either a **confirmed value** or **explicit** "not confirmed" / "not applicable". |
| Technical claims | **Model**, **brand**, **lead time**, **warranty** line items: **confirmed** or explicitly **not confirmed**.                                                                                    |
| Taxes            | **Never inferred**; state inclusion/exclusion of IVA (or equivalent) explicitly.                                                                                                                |

### 2.3 Quote numbering (decision D2b, 2026-09-21)

This subsection is deliberately split three ways, because the three parts
rest on different footing and carry different authority. **Do not read
across the boundaries**: §2.3.1 is weak archive evidence, §2.3.2 is what the
business has actually decided, and §2.3.3 is a single named exception.

Enforced in code by `apps/api/src/origenlab_api/quote_numbering.py` (pure
rendering + guards) and the transactional allocator in
`apps/api/src/origenlab_api/repositories/postgres/customer_quotes.py`.

#### 2.3.1 Historical evidence (what the archive actually shows)

Weak and incomplete. Recorded so that later readers do not mistake the
forward rules below for a description of history.

| Observation | Strength | Source |
| --- | --- | --- |
| A revision letter suffixes the **Drive document stem** with no separator — the filename `CN011728A` | One real artifact | Drive, CRM-Q1A investigation |
| One manual numbering outlier exists: a quotation numbered `01500-26`, far ahead of the real sequence | One real artifact | See §2.3.3 |
| `COT-YYYY-NNN` was the *hypothesised* legacy Labdelivery shape | **Unconfirmed** — matched **zero** rows across 7,900 archived Sent emails | `apps/email-pipeline/src/origenlab_email_pipeline/historical_quote_register/quote_number.py` module docstring |
| Whether historical duplicate quotation numbers exist, and how many | Unknown; not investigated | — |

Nothing here is a rule. The one thing the `CN011728A` artifact does show is
*where* a revision letter sits: immediately after the serial, abutting it
with no separator. Rule 5 in §2.3.2 follows that placement into the human
number (`01235A-26`); a trailing `01235-26 A` would contradict it. What the
archive says nothing about is whether the historical human-facing numbers
carried the letter at all — §2.3.2 decides that forward, it does not report
it.

#### 2.3.2 Owner-approved forward rules (authoritative)

Approved by the owner on 2026-09-21. These govern every number OrigenLab
allocates **from now on**. They are **not retroactive** and no historical
quotation is renamed, renumbered or re-imported to satisfy them.

| # | Rule |
| --- | --- |
| 1 | **One global sequence.** It is never reset annually and never partitioned by year, customer or document type. |
| 2 | **Seed = 1235.** The first allocation seeds `commercial.customer_quote_number_series`; after that the durable row is the counter truth and the environment seed has no further effect. |
| 3 | **The year is display metadata only.** The human number renders as `<padded serial>-<2-digit issue year>` — e.g. `01235-26`. The year never participates in sequence identity. The issue year is the **America/Santiago** business-local year at allocation time. |
| 4 | **Revision 1 has no suffix.** Revisions 2/3/4/… take `A`/`B`/`C`/… |
| 5 | **The suffix applies to both identifiers, immediately after the five-digit serial.** Human number: `01235A-26` — the letter sits *before* the `-YY`, because the revision belongs to the quotation's identity and the year does not. Drive document stem: `CN01235A` (no separator, matching §2.3.1). `01235-26 A` is **not** approved and contradicts the historical evidence. |
| 6 | **Legacy Labdelivery numbering stays in a separate number space.** It is never adopted as an OrigenLab `quote_number` and never enters the series. |
| 7 | **Never `MAX() + 1`.** Allocation is a row-locked `UPDATE … RETURNING` inside the quote-insert transaction — never a scan of existing rows, a browser-supplied number, a timestamp or a random value. |
| 8 | **Reserved serials are skipped, not refused.** Reaching a serial in the reservation set (§2.3.3) advances past it inside the same locked transaction and issues the next one. Quotation creation never stops on this. |
| 9 | **Never import or "correct" historical duplicate quotation numbers.** |

Worked example (serial 1235, issued 2026):

| Revision | Human `quote_number` | Drive `document_number` |
| --- | --- | --- |
| 1 | `01235-26` | `CN01235` |
| 2 | `01235A-26` | `CN01235A` |
| 3 | `01235B-26` | `CN01235B` |

**Undecided, and deliberately left so:** what follows revision `Z` (the 27th
revision). The code fails closed there rather than inventing `AA`. If a
quotation ever reaches 27 revisions, that is a business decision to make
then, not a rendering to guess now.

#### 2.3.3 Reserved serials — the `01500-26` exception

**Reserved serials** are serials the OrigenLab series must never issue. The
set is an **explicit denylist of individual numbers**, never a range and
never derived from a pattern: a range would permanently burn valid future
OrigenLab serials on the strength of a guess, so every entry has to name a
real document. Exactly one serial is reserved today.

| Reserved serial | Why |
| --- | --- |
| **1500** | The customer-facing quotation `01500-26` already exists. It was produced by a **manual numbering error** — numbered far ahead of the real sequence, which at the time of this decision sat at 1234 (next = 1235) — but it is a real document that reached a real customer, so the number must never be issued a second time. |

The owner's decision on the document itself is narrow and explicit:

- The historical quotation is **not renamed, not deleted, not modified**.
  The Drive and Gmail artifacts are left exactly as they are.
- It is **not imported** into the CRM, and the sequence is **not** reshaped
  around it.

The collision is concrete rather than theoretical:
`commercial.customer_quote` is `UNIQUE` on both `quote_number` and
`document_number`, and `document_number` carries no year, so a future
generated `CN01500` would clash with this document's own stem the moment it
were ever adopted.

| Guard | Behaviour |
| --- | --- |
| Allocation reaches serial 1500 | **Skipped atomically**, and 1501 is issued instead. The skip is the same row-locked `UPDATE` run again inside the transaction the lock is already held for, so no concurrent allocator can interleave and a rollback undoes the whole advance rather than leaving a half-skipped series. The counter lands past the reservation (…1499 → 1501 → 1502). 1500 is never returned, rendered or persisted. Quotation creation is **never blocked** by the reservation. |
| An operator tries to adopt `01500-26` | Refused (`adopted_quote_number_reserved_serial`, HTTP 422) — adopting it *is* importing it. Skipping applies to allocation only. |

**Audit.** A skip is recorded as `skipped_reserved_serials` on the
`quote_created` event in the append-only
`commercial.customer_quote_event` trail — the event that records the very
allocation the skip happened in. No separate table was added for it: the
skip is only ever observable as part of an allocation. The field is present
only when a skip actually occurred, so its presence is itself the signal.

A third, related guard falls out of the same reasoning: **only a number the
series has already issued may be adopted.** A serial at or beyond
`next_serial` is refused (`adopted_quote_number_not_yet_issued`, HTTP 409),
because adopting it would booby-trap a future allocation. Adopting genuinely
older numbers (e.g. `01191-24` while the series sits at 1236) stays allowed,
as does adopting a quotation whose number predates this rendering entirely —
the guard is not a general format lock.

---

## 3. Quote intake — what must be collected

Minimum information to start a quote, regardless of tooling:

| Area         | Fields (indicative)                                                                                                                |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| Organization | Company / institution name, RUT (if applicable), city, **region**                                                                  |
| Contact      | Name, **role / area**, email, phone                                                                                                |
| Need         | Equipment type, **preferred brand** (if any), **exact model** (if known), **quantity**                                             |
| Technical    | **Application**, matrix/sample type, **required range / capacity**, accessories/consumables                                        |
| Commercial   | **Target purchase date**, **delivery place**, invoice requirements, **tender vs direct purchase**, **estimated budget** (optional) |
| Services     | Installation, startup, training, support — **requested** vs **quoted** (later)                                                     |

## 4. Quote output structure

Repeatable sections from internal templates and examples:

| Block            | Content                                               |
| ---------------- | ------------------------------------------------------ |
| Header           | Quote **number**, **date**                            |
| Parties          | **Client**, institution/company, **contact**          |
| Reference        | Main reference (e.g. inquiry / RFQ id)                |
| Summary          | Short technical summary                               |
| Lines            | Quote items (description, qty, unit price if applicable, SKU/model **only if confirmed**) |
| Terms            | **Delivery**, **payment**, **validity**               |
| Legal/commercial | **Warranty** (as confirmed), **taxes / IVA**          |
| Services         | **Support**, **installation / startup** applicability |

---

## 5. Supplier (proveedor) research — master vs campaign

### 5.1 Separation (critical)

| Kind                                 | Meaning                                                                                                                      |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| **Supplier master**                  | Relatively stable facts: identity, domain, country, categories, ongoing relationship notes. Canonical identity target: `commercial.organization` with a supplier role. |
| **Supplier research run / campaign** | One sourcing exercise: methodology, scoring, **rankings**, regional quotas, "top 20/50", quick-win lists, **snapshot date**. |

**Rankings and "top N" from a given date are snapshot outputs**, not permanent truth about a supplier. Store them under a **run id**, not as the only record for the supplier.

### 5.2 Scoring dimensions (repeatable methodology)

When ranking suppliers in a campaign, dimensions should be **data**, not only prose:

1. Category fit
2. Export readiness
3. Ease of contact
4. Credibility / documentation
5. Partnership potential for Chile / LATAM

Campaign rows should store **score per dimension** and **total** (or rank), tied to a `research_run_id`.

### 5.3 Supplier prospect fields

**Identity & research:** company name, domain, country, region, covered categories, confidence, **evidence URLs**, **outreach route**, scores by dimension, total score, **excluded** (e.g. already known supplier) flag, free-text notes, **workflow status** (`new`, `shortlisted`, `contacted`, `in_validation`, `rejected`, `active_candidate`).

**Commercial validation (post-outreach):** Chile territory support, LATAM partnership possible, **MOQ**, aftersales/support, spare parts, exclusivity conditions, **QA / due diligence status**.

**Run metadata:** `research_run_id`, date, methodology version, **why prioritized**, **category gap** addressed, **pending diligence** list.

---

## 6. What should stay narrative (not DB columns)

Keep as report/template prose:

- Long executive summaries
- Regional commentary
- Strategic "why this category matters" essays
- Polished CTAs and marketing copy

Store **structured facts** (tables above) in the DB; **generate** narrative from them when needed.

---

## 7. Changelog

| Date       | Change                                                                                                   |
| ---------- | -------------------------------------------------------------------------------------------------------- |
| 2026-03-24 | Initial canonical rules + proposed entities (from internal template / ficha / supplier report analysis). |
| 2026-08-28 | Removed the pre-CRM proposed quote/supplier-offer schema (former §§3–5, 8); the durable schema design now lives in `TARGET_COMMERCIAL_ARCHITECTURE.md`. This file keeps policy only. |
| 2026-08-31 | CRM-Q1: durable `commercial.customer_quote` + revision 1 + Drive workspace shipped (dashboard-first quote creation, transactional numbering, fail-closed activation). Policy unchanged: Sheets edits content in V1, no supplier-cost exposure, no bidirectional sync. |
| 2026-09-21 | **D2b quote-numbering decision** recorded as §2.3: one global sequence seeded at 1235, year as display metadata, A/B/C revision suffixes on both identifiers, legacy Labdelivery kept in a separate number space, and the `01500-26` historical exception reserved rather than corrected. Enforced in `apps/api/src/origenlab_api/quote_numbering.py`. |
| 2026-09-21 | **D2b owner review amendments.** (a) Revision suffix moved to immediately after the serial — `01235A-26`, not `01235-26 A`. (b) Serial 1500 is now **skipped atomically** by the allocator (1499 → 1501) instead of failing closed with a 503, audited on the `quote_created` event; the reservation is generalised to an explicit set, with 1500 its only member. (c) Seed **1235** confirmed as the approved deployment value; still unset in every environment. |
