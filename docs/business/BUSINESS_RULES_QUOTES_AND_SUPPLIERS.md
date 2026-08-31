# Business rules: quotes (cotizaciones) and supplier research

Status: canonical  
Owner: project-maintainers  
Last reviewed: 2026-08-28

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
