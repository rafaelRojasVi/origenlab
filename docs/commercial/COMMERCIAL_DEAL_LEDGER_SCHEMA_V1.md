# Commercial deal ledger — schema design v1

Status: historical
Owner: project-maintainers
Last reviewed: 2026-09-01
Canonical replacement: [`docs/architecture/CURRENT_SYSTEM_TRUTH.md`](../architecture/CURRENT_SYSTEM_TRUTH.md) (`commercial.deal` — historical deal ledger mirror)

This is a point-in-time design document, not current system truth. Where it disagrees with
`CURRENT_SYSTEM_TRUTH.md`, trust `CURRENT_SYSTEM_TRUTH.md`. Content below is preserved unmodified
as historical design record.

---

**Status:** Design only (2026-05-26, rev. **v1.1** field provenance + money minor units). **No migrations applied.**  
**Schema version constant (Phase 1):** `commercial_deal_schema_version = "1.1.0"`.
**Worked example:** SERVA Electrophoresis GmbH → CEAF (OC 26172 / PO 174-26).  
**Prior art:** read-only preview under `apps/email-pipeline/reports/out/active/current/commercial_deals_preview/`.

---

## 1. Design principles

| Principle | Rule |
|-----------|------|
| **Ledger ≠ archive** | `emails`, `attachments`, `attachment_extracts`, `document_master` remain **evidence**. The ledger stores **confirmed commercial facts** and **pointers** to evidence. |
| **Reproducible deals** | Every executed deal is a row in `commercial_deal` with a stable `deal_key` (slug), not a one-off script constant. |
| **Evidence on every fact** | Document/email pointers live in `commercial_deal_evidence`; **field-level** provenance in `commercial_deal_field_evidence` (which value, which parser, which source). |
| **Field-level provenance** | Every material column (amounts, doc numbers, statuses) should be answerable via `commercial_deal_field_evidence` — e.g. why `supplier_amount_paid_eur=218.00`. |
| **Reproducible promotion** | Deals record `schema_version`, preview path + SHA-256, `parser_version`, `confirmed_facts_version` at insert/review time. |
| **Confidence is explicit** | `confidence` ∈ `operator_confirmed` \| `extracted_high` \| `extracted_low` \| `needs_review`. Operator-confirmed wins over extraction. |
| **No invented margin** | `margin_status` stays `needs_review` until **all** cost components needed for net margin are confirmed in CLP (or FX-locked). |
| **Net sale for margin** | Gross margin uses **client sale net (ex-IVA)**. Bank transfer gross (IVA-inclusive) is **cashflow**, stored on payments. |
| **Multi-currency** | Store **native currency per row**; no silent FX. FX rate rows are optional and evidence-backed. |
| **Buy + sell on one deal** | One deal ties **client** (sold to) and **supplier** (bought from). Lines are tagged `side` = `client` \| `supplier`. |
| **Costs are typed** | Product/supplier invoice lines → `commercial_deal_line`. Logistics, bank, import, handling surcharges → `commercial_deal_cost`. |
| **Timeline is append-only** | `commercial_deal_event` records status transitions and milestones; do not rewrite history. |
| **Human review** | `commercial_deal_review` captures operator sign-off, corrections, and blockers. |
| **Catalog optional** | `commercial_product` / `commercial_product_alias` normalize SKUs across deals; lines may still carry free-text before catalog backfill. |
| **Legacy coexistence** | `commercial_purchase_events` remains buyer-PO confirmation; new deals may set `legacy_purchase_event_id` for CEAF-style bridges. |
| **SQLite = truth** | All writes go to local SQLite first. Postgres is **read mirror** after explicit approval. |
| **Public surface is redacted** | Dashboard/API (post–Cloudflare Access) expose only **redacted** projections — never raw evidence bodies or payment secrets. |

---

## 2. ERD (text)

```
commercial_product ──< commercial_product_alias
        │
        │ (optional FK)
        ▼
commercial_deal ──┬──< commercial_deal_line
                  ├──< commercial_deal_cost ──> commercial_deal_document (optional FK)
                  │                        └──> commercial_deal_payment (optional FK)
                  ├──< commercial_deal_payment
                  ├──< commercial_deal_document
                  ├──< commercial_deal_event
                  ├──< commercial_deal_evidence
                  ├──< commercial_deal_field_evidence ──> commercial_deal_evidence
                  └──< commercial_deal_review

Archive (read-only pointers, not ledger):
  emails ──< attachments
       └── (referenced by deal_evidence.email_id, deal_document.source_attachment_id)

Legacy bridge:
  commercial_purchase_events ── (optional) ── commercial_deal.legacy_purchase_event_id
```

**Cardinality**

- One **deal** → many lines, costs, payments, documents, events, evidence rows, **field_evidence** rows, reviews.
- One **evidence** row (email/attachment/preview) may support **many fields** via `commercial_deal_field_evidence`.
- **Costs** may optionally point at the **document** (proforma) or **payment** (Wise voucher) they were derived from.
- **Product** catalog is shared across deals; lines may reference `product_id` or only `ref_code` + description.

---

## 3. Enums

### 3.1 `deal_status` (rollup on `commercial_deal`)

Single coarse lifecycle field for dashboards. Finer detail lives in `commercial_deal_event`.

| Value | Meaning |
|-------|---------|
| `draft` | Deal shell created; not commercially closed |
| `quoted` | Client quote sent; no PO yet |
| `client_po_received` | Client PO / order received |
| `client_invoiced` | OrigenLab invoice issued |
| `client_paid` | Client payment received (may be partial — see payments) |
| `supplier_po_sent` | PO placed with supplier |
| `supplier_invoiced` | Supplier proforma/invoice received |
| `supplier_paid` | Payment to supplier initiated/completed |
| `logistics_pending` | Awaiting shipment / DHL / freight resolution |
| `in_transit` | Shipment released |
| `delivered` | Delivered to client (operator-confirmed) |
| `closed` | Commercially complete |
| `cancelled` | Deal cancelled |
| `needs_review` | Blocked on operator |

**Composite shorthand (display only, not stored):**  
`paid_by_client__supplier_payment_sent__logistics_pending` → map to `deal_status=logistics_pending` with events proving client_paid + supplier_paid.

### 3.2 `margin_status`

| Value | Meaning |
|-------|---------|
| `not_computed` | Default; insufficient cost data |
| `needs_review` | Facts present but FX/logistics/import CLP unknown |
| `computed` | All required costs confirmed; `margin_net_clp` populated |
| `blocked` | Operator blocked margin (dispute, refund, etc.) |

### 3.3 `reconciliation_status` (supplier payment vs proforma)

| Value | Meaning |
|-------|---------|
| `not_applicable` | No supplier invoice yet |
| `pending` | Awaiting payment or invoice |
| `reconciled_excluding_supplier_freight` | Paid amount = invoice total − quoted supplier freight (SERVA pattern) |
| `reconciled_full` | Paid amount = invoice total |
| `mismatch` | Operator must resolve |
| `needs_review` | Automated check inconclusive |

### 3.4 `freight_status`

| Value | Meaning |
|-------|---------|
| `not_applicable` | |
| `quoted_on_supplier_invoice` | Freight line on supplier doc, not wired |
| `dhl_account_or_external_freight` | Client/OrigenLab arranges freight (SERVA case) |
| `included_in_supplier_payment` | Freight paid with goods |
| `delivered` | |
| `needs_review` | |

### 3.5 `line_side`

| Value | Meaning |
|-------|---------|
| `client` | Sold to client (revenue) |
| `supplier` | Bought from supplier (COGS component) |

### 3.6 `line_kind`

| Value | Meaning |
|-------|---------|
| `product` | SKU / reagent / equipment |
| `shipping` | Envío / freight line on client quote |
| `handling` | Handling fee lines |
| `discount` | |
| `other` | |

### 3.7 `cost_kind` (`commercial_deal_cost`)

| Value | Meaning |
|-------|---------|
| `supplier_product` | Rolled up from supplier lines (optional duplicate of line sum) |
| `supplier_handling` | |
| `supplier_freight_quoted` | Quoted on proforma, excluded from wire |
| `logistics_dhl` | Actual DHL / carrier cost |
| `logistics_import` | Import duties, customs broker |
| `bank_fee` | Transfer fees |
| `fx_spread` | Wise/card FX vs mid |
| `other` | |

### 3.8 `payment_direction`

| Value | Meaning |
|-------|---------|
| `inbound` | Client → OrigenLab |
| `outbound` | OrigenLab → supplier / carrier / bank |

### 3.9 `payment_method`

`bank_transfer` \| `wise` \| `card` \| `check` \| `other`

### 3.10 `document_type`

`client_po` \| `client_quote` \| `client_invoice` \| `supplier_po` \| `supplier_proforma` \| `supplier_invoice` \| `payment_voucher` \| `payment_confirmation` \| `logistics_doc` \| `other`

### 3.11 `event_type` (`commercial_deal_event`)

| Value | Typical actor |
|-------|----------------|
| `deal_created` | system |
| `client_quote_sent` | origenlab |
| `client_po_received` | client |
| `client_invoice_sent` | origenlab |
| `client_payment_received` | client |
| `client_bank_details_requested` | client |
| `supplier_po_sent` | origenlab |
| `supplier_invoice_received` | supplier |
| `supplier_payment_sent` | origenlab |
| `supplier_payment_confirmed` | supplier |
| `logistics_pending` | supplier / origenlab |
| `shipment_released` | supplier |
| `delivery_estimate_communicated` | origenlab |
| `delivered` | carrier / origenlab |
| `deal_closed` | operator |
| `deal_cancelled` | operator |
| `margin_review_requested` | system / operator |
| `note` | anyone |

### 3.12 `confidence`

`operator_confirmed` \| `extracted_high` \| `extracted_low` \| `needs_review`

### 3.13 `review_outcome`

`approved` \| `rejected` \| `needs_more_evidence` \| `snoozed`

---

## 4. Money and currency rules

1. **Currency column** — ISO 4217 (`CLP`, `EUR`, `USD`) on every monetary row.
2. **CLP amounts** — `INTEGER` whole pesos only (`amount_integer` / `*_clp` columns). No sub-peso.
3. **EUR / USD (and other decimal currencies)** — **Always store both:**
   - `amount_decimal TEXT` — canonical display string, two fractional digits (`218.00`, `268.47`, `363.00`).
   - `amount_minor INTEGER` — minor units (cents): `21800`, `26847`, `36300`.
   - **Conversion:** `amount_minor = round(float(amount_decimal) * 100)` with half-up; validators must keep pair consistent.
   - **Examples:**

     | Currency | amount_decimal | amount_minor |
     |----------|----------------|--------------|
     | EUR | `218.00` | `21800` |
     | USD | `268.47` | `26847` |
     | EUR | `363.00` | `36300` |

   - JPY-style zero-decimal currencies are out of scope for v1 (OrigenLab deals today are CLP/EUR/USD).
4. **IVA (Chile)** — On client side store:
   - `net_amount` (ex-IVA)
   - `iva_amount`
   - `iva_rate` (e.g. `0.19`)
   - `gross_amount` (= net + iva) on **payment** or deal rollup, not double-counted in margin.
5. **No FX on ledger without evidence** — `fx_rate_value`, `fx_rate_date`, `fx_evidence_id` when converting for margin.
6. **Margin formula (when safe)**

   ```
   margin_net_clp = client_sale_net_clp
                    - supplier_costs_clp_equiv
                    - logistics_and_import_clp
                    - bank_and_fx_clp
   ```

   Set `margin_status=computed` only when every term is `operator_confirmed` or locked FX.

7. **Reconciliation (supplier)** — Store explicit check:

   `supplier_amount_paid + supplier_freight_quoted == supplier_invoice_total`  
   → `reconciliation_status=reconciled_excluding_supplier_freight` (SERVA).

---

## 5. Table definitions (SQLite v1)

> Types: SQLite affinity. `TEXT` timestamps are ISO-8601 UTC with offset where known.  
> `JSON` columns are `TEXT` with JSON object/array.

### 5.1 `commercial_deal`

Deal header: parties, reference numbers, rollup status, margin gate.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_key` | TEXT UNIQUE NOT NULL | Stable slug, e.g. `serva-ceaf-oc-26172-po-174-26` |
| `title` | TEXT | Human label |
| `deal_status` | TEXT NOT NULL | §3.1 |
| `margin_status` | TEXT NOT NULL DEFAULT `not_computed` | §3.2 |
| `reconciliation_status` | TEXT | §3.3 |
| `freight_status` | TEXT | §3.4 |
| **Client** | | |
| `client_org_name` | TEXT NOT NULL | |
| `client_domain` | TEXT | e.g. `ceaf.cl` |
| `client_contact_email` | TEXT | Primary |
| `client_po_number` | TEXT | |
| `client_invoice_number` | TEXT | |
| `client_quote_number` | TEXT | |
| `client_project_code` | TEXT | e.g. ANID code |
| **Supplier** | | |
| `supplier_org_name` | TEXT | |
| `supplier_domain` | TEXT | e.g. `serva.de` |
| `supplier_contact_email` | TEXT | |
| `supplier_customer_code` | TEXT | OrigenLab code at supplier |
| `supplier_po_number` | TEXT | |
| `supplier_invoice_number` | TEXT | Proforma/invoice no. |
| **Rollup amounts (native currency)** | | Denormalized for query; source of truth = lines/payments |
| `client_sale_net_clp` | INTEGER | Ex-IVA |
| `client_iva_amount_clp` | INTEGER | |
| `client_iva_rate` | REAL | 0.19 |
| `client_sale_gross_clp` | INTEGER | Net + IVA |
| `client_payment_received_clp` | INTEGER | Cash in (often = gross) |
| `supplier_invoice_total_decimal` | TEXT | e.g. `363.00` |
| `supplier_invoice_total_minor` | INTEGER | e.g. `36300` |
| `supplier_amount_paid_decimal` | TEXT | e.g. `218.00` |
| `supplier_amount_paid_minor` | INTEGER | e.g. `21800` |
| **Reproducibility** | | Set on promotion / review |
| `schema_version` | TEXT NOT NULL | e.g. `1.1.0` |
| `source_preview_path` | TEXT | Path to preview JSON used to seed |
| `source_preview_sha256` | TEXT | Hash of preview file at promotion time |
| `parser_version` | TEXT | e.g. `deal_field_parsers@2026-05-26` |
| `confirmed_facts_version` | TEXT | e.g. `serva_ceaf_deal_confirmed@2026-05-26` |
| **Margin (only when computed)** | | |
| `margin_net_clp` | INTEGER | NULL until safe |
| `margin_computed_at` | TEXT | |
| `margin_notes` | TEXT | |
| **Meta** | | |
| `confidence` | TEXT NOT NULL DEFAULT `needs_review` | Deal-level worst-case or operator lock |
| `legacy_purchase_event_id` | INTEGER FK → `commercial_purchase_events(id)` | Nullable bridge |
| `notes_json` | TEXT DEFAULT `{}` | |
| `created_at` | TEXT NOT NULL | |
| `updated_at` | TEXT NOT NULL | |

**Indexes:** `deal_key`, `deal_status`, `client_domain`, `supplier_domain`, `client_po_number`, `supplier_po_number`.

---

### 5.2 `commercial_deal_line`

Product/service lines per deal (client sale and/or supplier cost).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | |
| `line_number` | INTEGER NOT NULL | Per-deal ordering |
| `side` | TEXT NOT NULL | `client` \| `supplier` |
| `line_kind` | TEXT NOT NULL DEFAULT `product` | §3.6 |
| `product_id` | INTEGER FK → `commercial_product(id)` | Nullable |
| `ref_code` | TEXT | e.g. `004250001`, `4250001` |
| `description` | TEXT NOT NULL | |
| `brand` | TEXT | e.g. SERVA |
| `quantity` | TEXT | `1`, `2x`, etc. |
| `unit` | TEXT | `ea`, `ml`, … |
| `currency` | TEXT NOT NULL | |
| `unit_amount_decimal` | TEXT | Foreign unit price |
| `unit_amount_minor` | INTEGER | Foreign unit minor units |
| `line_net_amount` | INTEGER | CLP net for client lines |
| `line_amount_decimal` | TEXT | EUR/USD line total string |
| `line_amount_minor` | INTEGER | EUR/USD line total minor units |
| `iva_rate` | REAL | Client lines only |
| `iva_amount` | INTEGER | |
| `confidence` | TEXT NOT NULL | |
| `evidence_id` | INTEGER FK → `commercial_deal_evidence(id)` | |
| `created_at` | TEXT NOT NULL | |

**Unique:** `(deal_id, side, line_number)`.

---

### 5.3 `commercial_deal_cost`

Non-SKU costs: logistics, bank, import, quoted freight not wired, FX spread.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | |
| `cost_kind` | TEXT NOT NULL | §3.7 |
| `description` | TEXT | |
| `currency` | TEXT NOT NULL | |
| `amount_integer` | INTEGER | CLP whole pesos |
| `amount_decimal` | TEXT | Required for EUR/USD |
| `amount_minor` | INTEGER | Required for EUR/USD (pair must match §4) |
| `document_id` | INTEGER FK → `commercial_deal_document(id)` | e.g. SERVA proforma A2602545 |
| `payment_id` | INTEGER FK → `commercial_deal_payment(id)` | e.g. Wise voucher row |
| `is_estimated` | INTEGER DEFAULT 0 | 1 = quoted/not yet paid |
| `excluded_from_supplier_wire` | INTEGER DEFAULT 0 | 1 = SERVA freight 145 |
| `confidence` | TEXT NOT NULL | |
| `evidence_id` | INTEGER FK | Legacy single pointer; prefer `field_evidence` |
| `created_at` | TEXT NOT NULL | |
| `updated_at` | TEXT NOT NULL | |

**SERVA examples:** freight EUR 145 → `document_id` = proforma doc; Wise fee row → `payment_id` = outbound Wise payment.

---

### 5.4 `commercial_deal_payment`

Client inbound and supplier/logistics outbound payments.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | |
| `direction` | TEXT NOT NULL | §3.8 |
| `payment_method` | TEXT | §3.9 |
| `paid_at` | TEXT | Event time |
| `currency` | TEXT NOT NULL | |
| `amount_gross_integer` | INTEGER | CLP bank transfer |
| `amount_net_integer` | INTEGER | CLP ex-IVA (client inbound) |
| `iva_amount_integer` | INTEGER | |
| `amount_decimal` | TEXT | EUR/USD wire (required if not CLP) |
| `amount_minor` | INTEGER | Paired minor units |
| `secondary_currency` | TEXT | e.g. USD when wire is EUR |
| `secondary_amount_decimal` | TEXT | Wise USD `268.47` |
| `secondary_amount_minor` | INTEGER | `26847` |
| `transfer_id` | TEXT | **Operator-only**; redact in public |
| `operation_id` | TEXT | **Operator-only** |
| `counterparty_email` | TEXT | |
| `subject` | TEXT | e.g. FACTURA 6 |
| `confidence` | TEXT NOT NULL | |
| `evidence_id` | INTEGER FK | |
| `created_at` | TEXT NOT NULL | |

**Rule:** Never expose `transfer_id` / `operation_id` in public API — mask to last 4.

---

### 5.5 `commercial_deal_document`

Logical documents (PO, invoice, voucher) linked to archive attachments.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | |
| `document_type` | TEXT NOT NULL | §3.10 |
| `doc_number` | TEXT | |
| `filename` | TEXT | |
| `issued_at` | TEXT | |
| `currency` | TEXT | |
| `amount_decimal` | TEXT | Total on doc |
| `amount_minor` | INTEGER | Paired minor units |
| `source_email_id` | INTEGER FK → `emails(id)` | Nullable |
| `source_attachment_id` | INTEGER FK → `attachments(id)` | Nullable |
| `extract_status` | TEXT | From `attachment_extracts` |
| `confidence` | TEXT NOT NULL | |
| `evidence_id` | INTEGER FK | |
| `created_at` | TEXT NOT NULL | |

---

### 5.6 `commercial_deal_event`

Append-only timeline.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | |
| `event_type` | TEXT NOT NULL | §3.11 |
| `event_at` | TEXT NOT NULL | |
| `actor_email` | TEXT | |
| `counterparty_email` | TEXT | |
| `subject` | TEXT | |
| `summary` | TEXT NOT NULL | No PII in public export |
| `payload_json` | TEXT DEFAULT `{}` | Amounts, doc refs — redact secrets |
| `source_email_id` | INTEGER FK | |
| `source_attachment_id` | INTEGER FK | |
| `confidence` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |

**Index:** `(deal_id, event_at)`.

---

### 5.7 `commercial_deal_evidence`

Pointer + metadata for a **source artifact** (email, attachment, operator note, preview file). **No email bodies.**  
Does **not** by itself prove which ledger column was derived from the artifact — use §5.8.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | |
| `evidence_kind` | TEXT | `email` \| `attachment` \| `operator_note` \| `preview_json` |
| `email_id` | INTEGER FK | |
| `attachment_id` | INTEGER FK | |
| `filename` | TEXT | |
| `email_subject` | TEXT | Truncate in public |
| `email_date_iso` | TEXT | |
| `extract_snippet` | TEXT | Max 400 chars; **omit in public** |
| `operator_note` | TEXT | |
| `source_path` | TEXT | Local preview path, not URL |
| `confidence` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |

---

### 5.8 `commercial_deal_field_evidence`

**Field-level provenance** — links a specific stored value to its evidence and extraction method.

Answers: *Why is `supplier_amount_paid_eur` = 218.00? Why is `client_sale_net_clp` = 1,260,000?*

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | Denormalized for query |
| `entity_table` | TEXT NOT NULL | e.g. `commercial_deal`, `commercial_deal_cost`, `commercial_deal_payment` |
| `entity_id` | INTEGER NOT NULL | PK of row in `entity_table` |
| `field_name` | TEXT NOT NULL | Column name, e.g. `supplier_amount_paid_decimal`, `client_sale_net_clp` |
| `extracted_value` | TEXT | Raw string from parser/OCR/email snippet |
| `normalized_value` | TEXT | Value written to ledger (string form) |
| `evidence_id` | INTEGER FK → `commercial_deal_evidence(id)` | Nullable if operator-only |
| `confidence` | TEXT NOT NULL | §3.12 |
| `parser_name` | TEXT | e.g. `deal_field_parsers.parse_supplier_payment_eur` |
| `parser_version` | TEXT | e.g. `2026-05-26` or package hash |
| `operator_confirmed` | INTEGER NOT NULL DEFAULT 0 | 1 = human sign-off overrides extract |
| `created_at` | TEXT NOT NULL | |

**Indexes:** `(deal_id, entity_table, entity_id)`, `(deal_id, field_name)`, `(evidence_id)`.

**Unique (soft):** `(entity_table, entity_id, field_name, created_at)` — allow history; latest wins in UI by `created_at DESC`.

**Promotion rule:** When seeding from `serva_ceaf_deal_confirmed.py`, insert `field_evidence` rows with `operator_confirmed=1`, `parser_name=operator_confirmed`, `evidence_id` pointing at preview JSON evidence row.

**Example rows (SERVA/CEAF):**

| entity_table | field_name | normalized_value | evidence |
|--------------|------------|------------------|----------|
| `commercial_deal` | `client_sale_net_clp` | `1260000` | CEAF quotation / OC attachment |
| `commercial_deal` | `supplier_amount_paid_decimal` | `218.00` | Wise confirmation PDF |
| `commercial_deal_cost` | `amount_decimal` (freight) | `145.00` | SERVA proforma A2602545 |

---

### 5.9 `commercial_deal_review`

Operator review / sign-off.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `deal_id` | INTEGER FK NOT NULL | |
| `reviewer` | TEXT DEFAULT `operator` | |
| `outcome` | TEXT NOT NULL | §3.13 |
| `reason_code` | TEXT | |
| `reason_text` | TEXT NOT NULL | |
| `fields_reviewed_json` | TEXT | List of column names |
| `schema_version` | TEXT | Schema at review time |
| `source_preview_path` | TEXT | Optional re-seed audit |
| `source_preview_sha256` | TEXT | |
| `parser_version` | TEXT | |
| `confirmed_facts_version` | TEXT | |
| `created_at` | TEXT NOT NULL | |

---

### 5.10 `commercial_product`

Canonical SKU catalog (optional v1 seed).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `ref_code` | TEXT UNIQUE NOT NULL | Normalized (zero-padded policy TBD) |
| `brand` | TEXT | |
| `name` | TEXT NOT NULL | |
| `category` | TEXT | e.g. `electrophoresis_reagent`, `lab_consumable` |
| `subcategory` | TEXT | e.g. `chemical_reagent`, `buffer` |
| `is_hazardous` | INTEGER DEFAULT 0 | 1 = hazardous goods |
| `requires_special_shipping` | INTEGER DEFAULT 0 | 1 = carrier restrictions / DG notes |
| `unit` | TEXT | |
| `is_active` | INTEGER DEFAULT 1 | |
| `created_at` | TEXT NOT NULL | |
| `updated_at` | TEXT NOT NULL | |

**SERVA seed (v1):**

| ref_code | name | category | subcategory | is_hazardous | requires_special_shipping |
|----------|------|----------|-------------|--------------|---------------------------|
| `004250001` | BlueSlick™ 250 ml | `electrophoresis_reagent` | `lab_consumable` | 0 | 0 |
| `003593002` | TEMED 25 ml | `electrophoresis_reagent` | `chemical_reagent` | 0 | 1 (candidate — operator verify) |

---

### 5.11 `commercial_product_alias`

Supplier/client codes mapping to canonical product.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `product_id` | INTEGER FK NOT NULL | |
| `alias_code` | TEXT NOT NULL | e.g. `004250001`, `4250001` |
| `alias_source` | TEXT | `serva` \| `ceaf` \| `origenlab` |
| `UNIQUE(alias_source, alias_code)` | | |

---

## 6. Redaction and security policy

Aligned with [`deal_preview_redaction.py`](../../apps/email-pipeline/src/origenlab_email_pipeline/commercial/deal_preview_redaction.py) and [`SECURITY_AUDIT_RENDER_DASHBOARD.md`](../SECURITY_AUDIT_RENDER_DASHBOARD.md).

| Data class | Operator SQLite / local tools | Postgres mirror | Dashboard / public API |
|------------|------------------------------|-----------------|-------------------------|
| Email bodies | Archive only | **Not mirrored** | Never |
| Attachment extract full text | Archive | Snippet policy TBD | Never |
| Bank account numbers | Payments table | Omit or hash | **Never** |
| RUT / tax IDs | Avoid in ledger; if stored, encrypt or keep local-only | Omit | **Never** |
| Personal addresses | Not in deal header | Omit | **Never** |
| `transfer_id`, `operation_id` | Full in SQLite | Encrypted column or omit | Last 4 only |
| Gmail URLs / `source_file` paths | Evidence metadata | Internal ops only | Never |
| `client_contact_email` | Allowed | Domain-level only in public? | Prefer domain + role |

**API rule (future):** Serve `commercial_deal_public_v` view or application-layer `redact_deal_for_public()` — same rules as `*.public.json` preview.

**Cloudflare Access:** Required before any deal endpoint on Render.

---

## 7. SQLite-first / Postgres mirror strategy

| Layer | Role |
|-------|------|
| **SQLite** (`ORIGENLAB_SQLITE_PATH`) | Operational ledger writes, operator scripts, promotion from preview |
| **Postgres `commercial.*`** | Read-only mirror for dashboard; populated by `sync_commercial_deal_*` (to be written) after Alembic |
| **Archive Postgres** | `archive.emails` / attachments — no bodies on Render |

**Orchestration:** Add `SchemaLayer.COMMERCIAL_DEAL` to [`sqlite_migrate.py`](../../apps/email-pipeline/src/origenlab_email_pipeline/sqlite_migrate.py) **after** approval — runs `ensure_commercial_deal_tables()` only; does not rebuild mart.

**Sync order (future):**

1. `commercial_deal`
2. Children: lines, costs, payments, documents, events, evidence, field_evidence, reviews
3. Products (dimension table, upsert)

**Do not** TRUNCATE deals on sync; upsert by `deal_key` / child natural keys.

---

## 8. SERVA → CEAF worked example (row mapping)

**`commercial_deal`**

| Field | Value |
|-------|-------|
| `deal_key` | `serva-ceaf-oc-26172-po-174-26` |
| `deal_status` | `logistics_pending` |
| `margin_status` | `needs_review` |
| `reconciliation_status` | `reconciled_excluding_supplier_freight` |
| `freight_status` | `dhl_account_or_external_freight` |
| `client_org_name` | Centro de Estudios Avanzados en Fruticultura CEAF |
| `client_domain` | `ceaf.cl` |
| `client_po_number` | `26172` |
| `client_invoice_number` | `6` |
| `supplier_org_name` | SERVA Electrophoresis GmbH |
| `supplier_domain` | `serva.de` |
| `supplier_customer_code` | `310471` |
| `supplier_po_number` | `174-26` |
| `supplier_invoice_number` | `A2602545` |
| `client_sale_net_clp` | 1260000 |
| `client_iva_amount_clp` | 239400 |
| `client_iva_rate` | 0.19 |
| `client_sale_gross_clp` | 1499400 |
| `client_payment_received_clp` | 1499400 |
| `supplier_invoice_total_decimal` / `_minor` | `363.00` / `36300` |
| `supplier_amount_paid_decimal` / `_minor` | `218.00` / `21800` |
| `schema_version` | `1.1.0` |
| `margin_net_clp` | NULL |
| `confidence` | `operator_confirmed` |

**`commercial_deal_line` (client)**

| line | ref_code | description | line_net_amount CLP |
|------|----------|-------------|---------------------|
| 1 | 4250001 | BlueSlick™ 250 ml | 695000 |
| 2 | 3593002 | TEMED 25 ml | 545000 |
| 3 | E01 | Envío | 20000 |

**`commercial_deal_line` (supplier)** — optional detail lines mirroring EUR proforma products.

**`commercial_deal_cost`**

| cost_kind | amount_decimal | amount_minor | document_id | payment_id | excluded_from_wire |
|-----------|----------------|--------------|-------------|------------|-------------------|
| `supplier_freight_quoted` | 145.00 | 14500 | proforma A2602545 | — | 1 |
| `supplier_handling` | 70.00 | 7000 | proforma | — | 0 |
| `supplier_product` (rollup) | 148.00 | 14800 | proforma | — | 0 |
| `fx_spread` (future) | — | — | — | Wise payment | — |

**`commercial_deal_payment`**

| direction | method | amount | notes |
|-----------|--------|--------|-------|
| `inbound` | `bank_transfer` | 1499400 CLP gross; net 1260000; IVA 239400 | FACTURA 6, 2026-05-22 |
| `outbound` | `wise` | 218.00 EUR; secondary 268.47 USD | transfer_id stored; redact public |

**`commercial_deal_event` (minimum)**

`client_po_received` → `client_invoice_sent` → `client_payment_received` → `supplier_po_sent` → `supplier_invoice_received` → `supplier_payment_sent` → `logistics_pending`

**`commercial_product` / alias**

| ref_code | category | subcategory | requires_special_shipping |
|----------|----------|-------------|---------------------------|
| `004250001` | electrophoresis_reagent | lab_consumable | 0 |
| `003593002` | electrophoresis_reagent | chemical_reagent | 1 (candidate) |

Aliases: `4250001` / `3593002` (CEAF quote codes).

**`commercial_deal_field_evidence` (samples)**

| field_name | normalized_value | operator_confirmed |
|------------|------------------|-------------------|
| `client_sale_net_clp` | 1260000 | 1 |
| `client_iva_amount_clp` | 239400 | 1 |
| `supplier_amount_paid_decimal` | 218.00 | 1 |
| `supplier_invoice_total_decimal` | 363.00 | 1 |
| `reconciliation_status` | reconciled_excluding_supplier_freight | 1 |

---

## 9. Relationship to existing tables

| Existing | Relationship |
|----------|----------------|
| `commercial_purchase_events` | CEAF OC promotion → set `legacy_purchase_event_id`; do not duplicate long-term |
| `commercial.warm_case` | Operator queue only; may link `deal_id` later (FK nullable) |
| `document_master` | Evidence for `commercial_deal_document` |
| Preview JSON | Seed source for first `INSERT` via promotion script (`--apply` only) |

---

## 10. Migration plan (not executed)

### Phase 0 — Design sign-off (current)

- [x] Schema doc v1
- [x] Read-only SERVA/CEAF preview
- [ ] Operator review of preview vs this doc

### Phase 1 — SQLite DDL only (local) — **plan only, not started**

See **§13 Phase 1 implementation plan** for file-level deliverables.  
**Not in Phase 1:** `sqlite_migrate.py` wiring, SERVA seed, promotion, Postgres, API.

### Phase 2 — Promotion from preview (SQLite writes)

1. `scripts/commercial/promote_deal_from_preview.py --deal-key … --dry-run` (default).
2. `--apply` inserts deal + children idempotently by `deal_key`.
3. Link `legacy_purchase_event_id` for CEAF if `commercial_purchase_events` row exists.

### Phase 3 — Tests

See §11.

### Phase 4 — Postgres mirror (read-only, redacted)

**Source of truth:** SQLite `commercial_deal*` (operator ledger). **Postgres** holds a **denormalized read model** only — never used for sends, outreach, or write-back.

#### 4.1 Postgres read model: `commercial.deal`

Single table (not a full copy of all 11 SQLite tables). Populated by `commercial_deal_postgres_mirror.sync_commercial_deals` (opt-in).

| Column | Type | Source |
|--------|------|--------|
| `deal_key` | TEXT PK | `commercial_deal.deal_key` |
| `sync_run_id` | BIGINT | `reporting.dashboard_sync_run.id` when run via orchestrator |
| `client_org_name` | TEXT | header |
| `supplier_org_name` | TEXT | header |
| `deal_status` | TEXT | header |
| `margin_status` | TEXT | header |
| `reconciliation_status` | TEXT | header |
| `freight_status` | TEXT | header |
| `client_sale_net_clp` | BIGINT | header |
| `client_iva_amount_clp` | BIGINT | header |
| `client_sale_gross_clp` | BIGINT | header |
| `client_payment_received_clp` | BIGINT | header |
| `supplier_invoice_total_decimal` | TEXT | header |
| `supplier_invoice_total_minor` | INTEGER | header |
| `supplier_amount_paid_decimal` | TEXT | header |
| `supplier_amount_paid_minor` | INTEGER | header |
| `margin_net_clp` | BIGINT | header (NULL unless computed) |
| `margin_pct` | DOUBLE PRECISION | derived from `margin_notes` JSON at sync; **`margin_notes` never stored** |
| `updated_at` | TEXT | header |
| `product_line_summaries` | JSONB | aggregated from `commercial_deal_line` + `commercial_product` |
| `cost_summaries_by_type` | JSONB | aggregated from `commercial_deal_cost` (totals by `cost_kind` + `currency`) |
| `payment_summaries_masked` | JSONB | from `commercial_deal_payment` (amounts only; IDs/emails omitted) |
| `margin_blockers` | JSONB | `remaining_margin_blockers` when `margin_status != 'computed'` |
| `synced_at` | TIMESTAMPTZ | write timestamp |

**Indexes:** `deal_key` (PK), `updated_at DESC`, `margin_status`.

#### 4.2 Redaction invariants (sync + API)

Never read from SQLite or expose in Postgres/API:

- Raw email bodies, full attachment text, `extract_snippet`, `operator_note` on evidence
- `transfer_id`, `operation_id`, bank account numbers, RUTs (full)
- `source_preview_path`, `source_preview_sha256`, `notes_json`, `operator_private_json`, `legacy_purchase_event_id`
- Gmail URLs, `source_path`, `source_file`, `source_email_id`, `source_attachment_id`
- `client_contact_email`, `supplier_contact_email`, domains, PO/invoice numbers (dashboard-safe rollup only)
- Cost `description` (may contain operator notes); payment `subject`, `counterparty_email`
- Product `ref_code`, line `description` (identifiers / free text)

**Product line summary JSON** (per line): `side`, `line_kind`, `product_name`, `category`, `quantity`, `unit`, `currency`, `line_net_amount` (CLP client lines only).

**Cost summary JSON** (per `cost_kind` + `currency`): `cost_kind`, `currency`, `total_amount_integer`, `total_amount_decimal`, `total_amount_minor`, `row_count`.

**Payment summary JSON** (per payment): `direction`, `payment_method`, `paid_at`, `currency`, amount fields; no transfer/operation IDs.

#### 4.3 Sync (explicit opt-in)

| Entry | Command |
|-------|---------|
| Standalone | `uv run python scripts/sync/sync_commercial_deals_postgres_mirror.py --sqlite-db … --postgres-url …` |
| Dashboard orchestrator | `sync_dashboard_postgres_mirror.py --include-commercial-deals` (default **off**) |
| Dry-run | `--dry-run` on either script |
| Verify | `uv run python scripts/qa/verify_commercial_deals_postgres_mirror.py` |

Sync semantics: `DELETE FROM commercial.deal` then insert all safe rows from SQLite (full replace per run). Skips when SQLite ledger tables missing or empty.

Alembic: `20260526_0018_commercial_deal_mirror.py` (`upgrade head` before sync).

#### 4.4 API contract (Phase 4b — mirror only)

| Route | Response |
|-------|----------|
| `GET /mirror/commercial/deals?limit=1–100` | `{ table_available, items[], total, limit, disclaimer }` |
| `GET /mirror/commercial/deals/{deal_key}` | `{ table_available, deal \| null, disclaimer }` |

DTO keys match `commercial.deal` safe columns only. `data_source: postgres_mirror`, `read_only: true`. Cloudflare Access on dashboard does **not** relax redaction.

### Phase 5 — Dashboard UI (read-only mirror list)

**Route:** `GET /mirror/commercial/deals` only (via `apps/dashboard/src/api/mirrorCommercialClient.ts`). Do **not** use `GET /mirror/commercial/purchase-events` or deal detail in Today UI.

**UI:** `CommercialDealsTable` on Today — allowlisted scalar fields, labels *Postgres mirror · Read-only · Redacted commercial view*, empty state when `table_available=false` or `items=[]`.

**Production order (before operators expect rows):**

1. `alembic upgrade head` (`20260526_0018`)
2. Explicit sync: `sync_commercial_deals_postgres_mirror.py` or `sync_dashboard_postgres_mirror.py --include-commercial-deals` (default **off**)
3. `verify_commercial_deals_postgres_mirror.py --scan-jsonb`
4. Deploy API + dashboard when approved

Operator full ledger remains SQLite CLI (`inspect_commercial_deal`, `list_commercial_deals`).

---

## 11. Tests plan

| Area | Tests |
|------|-------|
| **DDL** | `test_commercial_deal_schema.py`: all 11 tables exist; FK enforcement; `foreign_keys=ON` |
| **Money pairs** | EUR/USD rows: `amount_decimal` + `amount_minor` consistent (218.00 ↔ 21800) |
| **Field evidence** | Insert + query by `(entity_table, entity_id, field_name)` |
| **Promotion** | Dry-run produces same row counts as preview; `--apply` idempotent second run |
| **SERVA regression** | Net 1,260,000 × 1.19 = 1,499,400; 363 − 145 = 218 reconciliation |
| **Margin gate** | `margin_status` stays `needs_review` without `wise_payment_cost_clp` + logistics |
| **Redaction** | Public DTO has no `INT_EMP`, full Wise id, RUT regex, 16–20 digit accounts |
| **Evidence** | No `body` columns in deal tables; evidence rows only store snippet length ≤ 400 |
| **Legacy bridge** | Promoting CEAF sets `legacy_purchase_event_id` without duplicating OC |
| **Mirror** | Fake Postgres conn: upsert deal by `deal_key`; counts match SQLite |

Run:

```bash
cd apps/email-pipeline
uv run pytest tests/test_commercial_deal_*.py -q
```

---

## 12. Later phases — file map (not Phase 1)

| Phase | File | Purpose |
|-------|------|---------|
| 2 | `commercial_deal_promotion.py` | Build insert plans from preview JSON |
| 2 | `promote_deal_from_preview.py` | CLI `--dry-run` / `--apply` |
| 1b | `sqlite_migrate.py` | `SchemaLayer.COMMERCIAL_DEAL` (after DDL tests pass) |
| 4 | Alembic + `commercial_deal_postgres_mirror.py` | Postgres mirror |

### 12.1 Postgres (Phase 4 only)

| File | Purpose |
|------|---------|
| `alembic/versions/YYYYMMDD_HHMM_commercial_deal_ledger.py` | `commercial.deal`, `.deal_line`, … |
| `src/.../commercial_deal_postgres_mirror.py` | Upsert sync |
| Extend `dashboard_postgres_sync.py` | Optional slice after purchase_events |

### 12.5 Seed / promotion (Phase 2)

```bash
# Dry-run (proposed)
uv run python scripts/commercial/promote_deal_from_preview.py \
  --deal-key serva-ceaf-oc-26172-po-174-26

# Apply (explicit approval)
uv run python scripts/commercial/promote_deal_from_preview.py \
  --deal-key serva-ceaf-oc-26172-po-174-26 --apply
```

---

## 13. Phase 1 implementation plan (DDL + tests only)

**Scope:** Add schema module, dry-run applier, and unit tests. **Do not** run against production SQLite, seed SERVA/CEAF, or wire `sqlite_migrate` until tests pass and operator approves `--apply` on a dev copy.

### 13.1 Deliverables

| # | Artifact | Path |
|---|----------|------|
| 1 | Schema DDL + helpers | `apps/email-pipeline/src/origenlab_email_pipeline/commercial/commercial_deal_schema.py` |
| 2 | Dry-run / optional apply CLI | `apps/email-pipeline/scripts/commercial/apply_commercial_deal_schema_dry_run.py` |
| 3 | Unit tests | `apps/email-pipeline/tests/test_commercial_deal_schema.py` |

### 13.2 `commercial_deal_schema.py` — contents

**Constants**

```python
COMMERCIAL_DEAL_SCHEMA_VERSION = "1.1.0"
COMMERCIAL_DEAL_TABLE_NAMES: tuple[str, ...]  # 11 tables, creation order
```

**`COMMERCIAL_DEAL_DDL`** — single `executescript` string creating tables in FK-safe order:

1. `commercial_product`
2. `commercial_product_alias`
3. `commercial_deal`
4. `commercial_deal_evidence`
5. `commercial_deal_document`
6. `commercial_deal_payment`
7. `commercial_deal_line`
8. `commercial_deal_cost`
9. `commercial_deal_event`
10. `commercial_deal_field_evidence`
11. `commercial_deal_review`

All definitions must match **§5** (including `amount_minor`, reproducibility columns, `document_id`/`payment_id` on costs, product catalog fields).

**Functions**

| Function | Behavior |
|----------|----------|
| `ensure_commercial_deal_tables(conn)` | `executescript(COMMERCIAL_DEAL_DDL)` + `commit()` |
| `commercial_deal_tables_exist(conn)` | True iff all 11 tables present in `sqlite_master` |
| `list_commercial_deal_tables()` | Returns ordered table names (for dry-run print) |

**Helpers (same module, tested)**

| Function | Behavior |
|----------|----------|
| `decimal_to_minor(amount_decimal: str) -> int` | `218.00` → `21800`; raises on bad format |
| `minor_to_decimal(amount_minor: int, scale: int = 2) -> str` | `21800` → `218.00` |
| `validate_decimal_minor_pair(decimal: str, minor: int) -> bool` | Consistency check for tests |

No imports from Gmail, postgres mirror, or promotion.

### 13.3 `apply_commercial_deal_schema_dry_run.py` — behavior

**Default (no args):** Read-only plan

- Print `COMMERCIAL_DEAL_SCHEMA_VERSION` and table list (11 names).
- Open `:memory:` SQLite, run `ensure_commercial_deal_tables`, run `PRAGMA foreign_key_check`, exit 0.
- Print `DRY-RUN: no file on disk was modified`.

**Flags**

| Flag | Effect |
|------|--------|
| `--sqlite-db PATH` | Target file for optional apply |
| `--apply` | Call `ensure_commercial_deal_tables` on **PATH** only (additive `CREATE IF NOT EXISTS`) |
| `--json-out PATH` | Write `{"schema_version","tables","applied":bool}` |

**Safety banner in docstring:** Not for production until operator backup + explicit approval. No seed, no data migration.

**Example commands (after implementation):**

```bash
cd apps/email-pipeline

# Plan only — always safe
uv run python scripts/commercial/apply_commercial_deal_schema_dry_run.py

# Apply DDL to dev copy only — explicit
uv run python scripts/commercial/apply_commercial_deal_schema_dry_run.py \
  --sqlite-db ~/data/origenlab-email/sqlite/emails.sqlite --apply
```

### 13.4 `test_commercial_deal_schema.py` — cases

| Test | Assert |
|------|--------|
| `test_ensure_creates_all_tables` | `:memory:` DB; `commercial_deal_tables_exist` is True; count = 11 |
| `test_foreign_keys_valid` | After ensure, `PRAGMA foreign_key_check` empty on :memory: |
| `test_decimal_minor_roundtrip` | 363.00 ↔ 36300, 268.47 ↔ 26847, 218.00 ↔ 21800 |
| `test_chilean_vat_net_gross` | `1260000 * 1.19` → 1499400 via helper or integer math |
| `test_deal_has_reproducibility_columns` | `PRAGMA table_info(commercial_deal)` includes `schema_version`, `source_preview_sha256`, … |
| `test_cost_has_document_and_payment_fk_columns` | `document_id`, `payment_id` exist on `commercial_deal_cost` |
| `test_field_evidence_columns` | `entity_table`, `field_name`, `parser_version`, `operator_confirmed` exist |
| `test_product_catalog_columns` | `category`, `is_hazardous`, `requires_special_shipping` on `commercial_product` |
| `test_dry_run_script_exit_zero` | Subprocess `apply_commercial_deal_schema_dry_run.py` without `--apply` → 0 |
| `test_no_gmail_or_postgres_imports` | `commercial_deal_schema` source must not reference imap, alembic, postgres |

**Explicitly not in Phase 1 tests:** SERVA seed rows, promotion idempotency, mirror sync.

### 13.5 Phase 1 completion checklist

- [ ] `commercial_deal_schema.py` merged
- [ ] `apply_commercial_deal_schema_dry_run.py` merged
- [ ] `test_commercial_deal_schema.py` — all green: `uv run pytest tests/test_commercial_deal_schema.py -q`
- [ ] Design doc §5 matches DDL (this file)
- [ ] Operator approves `--apply` on **backup copy** of SQLite
- [ ] **Then** (Phase 1b, separate PR): wire `SchemaLayer.COMMERCIAL_DEAL` in `sqlite_migrate.py`

### 13.6 Out of scope for Phase 1

- Gmail / ingest changes  
- `promote_deal_from_preview.py` / SERVA seed  
- `commercial_purchase_events` backfill  
- Postgres Alembic / Render / dashboard routes  
- `extract_serva_ceaf_deal_preview.py` changes (preview already sufficient for Phase 2)

---

## 14. Open questions (v1.2)

1. **Normalize ref codes** — Always store 7-digit SERVA form (`004250001`) vs accept both?
2. **Partial payments** — Single client payment row sufficient for v1?
3. **Multi-supplier deals** — v1 assumes one supplier per deal; split deals if needed.
4. **FX official source** — Wise PDF vs Banco Central for margin CLP conversion?
5. **Encrypt `transfer_id` at rest** — Or keep only in operator JSON export outside DB?

---

## 15. References (code)

- Preview: `apps/email-pipeline/scripts/commercial/extract_serva_ceaf_deal_preview.py`
- Confirmed facts: `apps/email-pipeline/src/.../commercial/serva_ceaf_deal_confirmed.py`
- Purchase legacy: `commercial_purchase_schema.py`
- Redaction: `deal_preview_redaction.py`
