# Product catalogue — schema audit v1 (Phase 8A)

**Status:** Design / audit only (2026-05-27). **No Alembic migration applied.**  
**Scope:** OrigenLab operator catalogue — unify website vitrine, commercial deals, supplier quotes, and equipment opportunities without inventing public pricing or inventory.

---

## 1. Business goals

| Goal | Why it matters |
|------|----------------|
| **Single product identity** | Same SKU/model must reconcile across website cards, SERVA deal lines, IKA/CRTOP quotes, and future client quotes. |
| **Operator-safe quoting** | Dashboard shows *what* was quoted, *from whom*, *when*, and *under which terms* — not raw email bodies or bank data. |
| **Website alignment** | Public site (`apps/web`) stays conservative (no list prices); catalogue DB backs operator tools and optional future “request quote” deep links. |
| **Evidence, not invention** | Specs and prices come from manufacturer pages, operator-confirmed deal facts, or redacted supplier quotes — never LLM guesses. |
| **Link deals ↔ products ↔ threads** | SERVA/CEAF lines, RG Energía/IKA, CRTOP reactor, and ChileCompra equipment rows should attach to stable `product_id` where possible. |
| **Prepare Postgres read mirror** | SQLite remains write truth; dashboard/API consume redacted `catalog.*` mirror (same pattern as `commercial.deal`). |

**Non-goals for Phase 8A:** inventory, stock commitment, public ecommerce pricing, automated client quote PDFs, supplier private terms on dashboard.

---

## 2. Source of truth vs derived (read-only)

| Layer | Role | Examples |
|-------|------|----------|
| **Canonical — website editorial** | Curated public product facts (names, slugs, conservative copy, public URLs) | `apps/web/src/data/products.ts`, `brands.ts`, `categories.ts`, `productFamilies.ts` |
| **Canonical — operator catalogue (new)** | Stable `catalog.product` + aliases + categories; human-approved merges | SQLite `catalog_*` tables (Phase 8B) |
| **Canonical — commercial deals** | Executed deal facts + lines + payments (redacted mirror) | `commercial_deal*`, `commercial_product` (deal-ledger v1 design) |
| **Operational evidence (read-only pointers)** | Gmail archive, attachments, extracts | `emails`, `attachments`, `attachment_extracts`, `document_master` |
| **Derived — mart** | Contact/org rollups, heuristic equipment tags | `contact_master`, `organization_master`, `opportunity_signals` |
| **Derived — commercial intel** | Signal facts/rollups (rebuildable) | `commercial_email_signal_fact`, `commercial_*_rollup` |
| **Derived — warm cases** | Promoted triage rows from SQLite queue | `commercial.warm_case` (+ equipment_signal) |
| **Derived — equipment queue** | ChileCompra CSV operator queue | `commercial.equipment_opportunity` |
| **Derived — purchase events** | Buyer PO confirmation (legacy bridge) | `commercial_purchase_events` |

**Rule:** Catalogue **does not** replace deal ledger or email archive. It **indexes** products and links outward. Postgres gets **projections** only (`api.v_catalog_*` or mirror tables), never bodies or payment secrets.

---

## 3. Current state audit

### 3.1 Website catalogue (`apps/web/src/data/`)

| File | Contents |
|------|----------|
| `products.ts` | `Product` interface: `id`, `brandId`, `slug`, optional `sku`, `keySpecs[]`, `categorySlugs`, flags `showOnProductsPage` / `showOnBrandPage` |
| `brands.ts` | SERVA, Ortoalresa (+ legal names, public URLs) |
| `categories.ts` | Three buyer-facing lines: alimentos, control-de-calidad, laboratorio-clinico |
| `productFamilies.ts` | Editorial families (today: `centrifugas` + Ortoalresa slug order) |
| `validate-catalog.mjs` | Integrity: slug uniqueness, Ortoalresa assets, no unsafe “exclusive distributor” copy |

**Website inventory (confirmed products):**

| Brand | Slug / id | SKU / model | Detail page |
|-------|-----------|-------------|-------------|
| SERVA | `blueslick-42500` | `42500` (card) | Brand SKU card only |
| SERVA | `temed-25ml` | — | Brand SKU card only |
| SERVA | `repel-silane-ge17-1332-01` | `GE17-1332-01` | Brand SKU card only |
| Ortoalresa | `biocen-22`, `biocen-22-r`, `digicen-22`, `digicen-22-r`, `consul-22` | — | Full `/productos/centrifugas/{slug}/` with `keySpecs` |

**Gaps vs operator needs:**

- No DB — TypeScript arrays are the only structured catalogue.
- SERVA website SKU `42500` ≠ deal ledger ref `004250001` / `4250001` (normalization required).
- No products for IKA RV10.70, CRTOP OLT-HP-5L, Ollital reactor, Hielscher UP100H, or analytical balances.
- Categories are **commercial lines**, not equipment taxonomy (centrifuge vs balance vs reagent).

### 3.2 Email-pipeline commercial models

| Area | Location | Catalogue relevance |
|------|----------|---------------------|
| Deal ledger design | `docs/commercial/COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md` | Defines `commercial_product` + `commercial_product_alias`; SERVA BlueSlick/TEMED seed |
| Deal promotion | `commercial_deal_promotion.py`, `serva_ceaf_deal_confirmed.py` | Operator-confirmed lines with ref codes |
| Deal mirror | `commercial_deal_mirror_read_model.py` | Redacted `product_line_summaries` for dashboard |
| Purchase events | `commercial_purchase_schema.py` | `product_name` + `ref_code` on items (buyer PO) |
| Warm cases | `warm_case_promotion.py`, `warm_case_classification.py` | Case-specific titles; equipment_signal on promote |
| Equipment queue | `equipment_opportunity_mirror.py`, `equipment_first_licitacion_queue.py` | `equipment_category`: centrifuge, balance, sonicator, incubator, homogenizer, osmometer |
| Mart heuristics | `business_mart.py` → `equipment_tags_from_text` | Spanish tags: `centrifuga`, `balanza`, `incubadora`, … |
| Opportunity signals | `build_business_mart.py` | Entity-level scores, not SKU-level catalogue |

**Existing `commercial_product` (deal ledger v1 design)** is deal-centric (ref_code, category, hazard flags). Phase 8B+ should treat it as a **legacy bridge** or migrate rows into `catalog.product` with `product_commercial_link.link_kind = deal_ledger_product`.

### 3.3 Dashboard (read-only)

| Section | Data source | Product granularity |
|---------|-------------|---------------------|
| Negocios | `GET /mirror/commercial/deals` | Aggregated line summaries |
| Proveedores | Warm cases by supplier domain | Thread-level, not SKU |
| Oportunidades | Equipment CSV mirror + warm cases | Category + description text |
| Pagos y logística | Deal costs/payments (redacted) | Deal-level |

No **Catálogo** section yet (`dashboardNav.ts` has no `catalog` route).

### 3.4 Recent operator cases (redacted summaries)

Evidence lives in SQLite/Postgres warm cases and classification overrides — **not** reproduced here as raw text.

| Case | Parties | Product identity signals | Price / terms (operator-safe) | Link targets |
|------|---------|--------------------------|-------------------------------|--------------|
| **RG Energía / IKA** | Client: RG Energía; supplier: IKA | Model **RV10.70**, part **0003812200** / **3812200**, qty **3** | Supplier reply: **112,00**, stock OK; **currency ambiguous** | Warm case thread hint `rg-energia-ika-rv10.70-3812200`; equipment_signal consumable/part |
| **CRTOP reactor** | Supplier: CRTOP | Model **OLT-HP-5L**, 5 L lab reactor | **USD 10,600 EXW**, qty 1; T/T 100% prepaid; delivery **25–30 working days** after prepayment; quote valid **60 days** | `supplier_quote_received`; specs: 170–190 °C op, 200 °C design, 0.8–1.6 MPa op, 6 MPa design, 316L, 1600 W, 108–240 V |
| **SERVA / CEAF** | Client: CEAF; supplier: SERVA | **BlueSlick™ 250 ml** (`004250001` / `4250001`), **TEMED 25 ml** (`003593002`) | Deal ledger: CLP client net + EUR supplier proforma (see deal mirror) | `commercial_deal` + `commercial_product` seed |
| **Ollital** | Supplier domain `ollital.com` | Reactor **5 L** (thread subject pattern) | Quote terms in email — **not** on public site | Warm case `supplier_reply` |
| **Hielscher** | `hielscher.com` | **UP100H** (ultrasonic processor pattern in ML docs) | Service/quote threads | Equipment tag / warm supplier |
| **Balances** | ChileCompra queue | `equipment_category=balance` | Tender metadata only | `commercial.equipment_opportunity` rows |
| **Ortoalresa** | Website + supplier threads | Slugs: biocen-22, digicen-22-r, … | Public specs on web; quote via OrigenLab | Website `products.ts` + mart `centrifuga` tag |

---

## 4. Recommended catalogue schema (SQLite v1 → Postgres mirror)

Namespace: **`catalog`** (separate from `commercial` deal tables). All timestamps ISO-8601 UTC. JSON columns as TEXT in SQLite.

### 4.1 `catalog.product`

Canonical sellable or quotable item (equipment, reagent, accessory).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `product_key` | TEXT UNIQUE NOT NULL | Stable slug: `serva-blueslick-250ml`, `ika-rv10-70-vapor-tube`, `crtop-olt-hp-5l` |
| `display_name` | TEXT NOT NULL | Operator-facing |
| `brand` | TEXT | SERVA, IKA, CRTOP, Ortoalresa, … |
| `manufacturer_name` | TEXT | When brand ≠ legal entity |
| `product_kind` | TEXT NOT NULL | `equipment` \| `consumable` \| `reagent` \| `accessory` \| `service` |
| `equipment_class` | TEXT | Aligns with queue: `centrifuge`, `balance`, `reactor`, `sonicator`, `incubator`, … NULL for consumables |
| `model_number` | TEXT | e.g. `RV10.70`, `OLT-HP-5L`, `UP100H` |
| `default_unit` | TEXT | `ea`, `ml`, `kit` |
| `website_slug` | TEXT | Nullable FK to web `Product.slug` |
| `website_product_id` | TEXT | Nullable FK to web `Product.id` |
| `public_summary` | TEXT | Short, safe for dashboard (no pricing) |
| `is_active` | INTEGER DEFAULT 1 | |
| `confidence` | TEXT | `operator_confirmed` \| `website_editorial` \| `extracted_needs_review` |
| `created_at` / `updated_at` | TEXT | |

**Indexes:** `product_key`, `brand`, `model_number`, `equipment_class`, `website_slug`.

### 4.2 `catalog.product_alias`

Maps supplier/client/part numbers to `product_id` (many codes → one product).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `product_id` | INTEGER FK NOT NULL | |
| `alias_code` | TEXT NOT NULL | Normalized (see §6) |
| `alias_source` | TEXT NOT NULL | `serva`, `ika`, `crtop`, `ceaf`, `origenlab`, `chilecompra` |
| `alias_kind` | TEXT | `sku`, `part_no`, `supplier_ref`, `client_ref`, `legacy_web_sku` |
| `notes` | TEXT | Operator notes only — not mirrored if sensitive |
| UNIQUE | `(alias_source, alias_code)` | |

### 4.3 `catalog.product_category` (optional v1, recommended)

Normalized taxonomy (orthogonal to website buyer lines).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `category_key` | TEXT UNIQUE | e.g. `electrophoresis_reagent`, `lab_reactor`, `analytical_balance` |
| `parent_category_key` | TEXT | Nullable tree |
| `display_name` | TEXT | Spanish operator label |
| `equipment_class` | TEXT | Optional link to queue vocabulary |

Bridge: `catalog.product_category_map (product_id, category_id, is_primary)`.

### 4.4 `catalog.product_spec`

Structured specs (manufacturer or quote-derived).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `product_id` | INTEGER FK | |
| `spec_group` | TEXT | `operation`, `design`, `electrical`, `material`, `dimensions` |
| `spec_key` | TEXT | e.g. `volume_l`, `operation_temp_c`, `design_pressure_mpa` |
| `spec_value` | TEXT | Display string |
| `spec_value_numeric` | REAL | Nullable for sort/filter |
| `spec_unit` | TEXT | `L`, `°C`, `MPa`, `W`, `V` |
| `source` | TEXT | `manufacturer_datasheet`, `supplier_quote`, `website_editorial`, `operator` |
| `confidence` | TEXT | |
| `valid_from` / `valid_to` | TEXT | Optional supersession |

**CRTOP OLT-HP-5L example rows:** volume 5 L; operation temp 170–190 °C; design temp 200 °C; operation pressure 0.8–1.6 MPa; design pressure 6 MPa; material 316L; power 1600 W; supply 108–240 V AC.

### 4.5 `catalog.supplier_offer`

One supplier quote/email offer (header).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `product_id` | INTEGER FK | Nullable if line-level only |
| `supplier_org_name` | TEXT | CRTOP, IKA, SERVA, … |
| `supplier_domain` | TEXT | `crtopmachine.com`, `ika.net.br`, … |
| `offer_status` | TEXT | `received`, `valid`, `expired`, `superseded`, `needs_review` |
| `quoted_at` | TEXT | Email date |
| `valid_until` | TEXT | e.g. CRTOP 60 days |
| `incoterm` | TEXT | `EXW`, … |
| `payment_terms` | TEXT | Redacted summary only (no bank instructions) |
| `delivery_terms` | TEXT | e.g. 25–30 working days after prepayment |
| `currency` | TEXT | ISO 4217; **nullable if ambiguous** (IKA case) |
| `quantity_offered` | TEXT | |
| `evidence_email_id` | INTEGER | FK → `emails(id)` — SQLite only |
| `evidence_attachment_id` | INTEGER | PDF pointer — not mirrored as text |
| `confidence` | TEXT | |
| `created_at` | TEXT | |

**Never store:** bank details, RUT, SWIFT/IBAN, full PDF text, Gmail URLs in mirror.

### 4.6 `catalog.price_snapshot`

Immutable price observation (append-only).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `product_id` | INTEGER FK NOT NULL | |
| `supplier_offer_id` | INTEGER FK | Nullable |
| `snapshot_kind` | TEXT | `supplier_quote`, `client_quote`, `deal_line`, `website_list` (future) |
| `currency` | TEXT | Required when amount present |
| `amount_decimal` | TEXT | Two decimal places for USD/EUR |
| `amount_minor` | INTEGER | Cents — same rules as deal ledger §4 |
| `amount_clp_integer` | INTEGER | Whole pesos when CLP |
| `quantity` | TEXT | |
| `unit` | TEXT | |
| `incoterm` | TEXT | |
| `price_notes` | TEXT | e.g. “currency unclear — operator verify” |
| `is_public_safe` | INTEGER DEFAULT 0 | **0** for all supplier quotes in v1 |
| `confidence` | TEXT | |
| `observed_at` | TEXT | |
| `created_at` | TEXT | |

**IKA case:** store amount `112.00` with `currency=NULL` and `price_notes='currency ambiguous'` until operator confirms.

**CRTOP case:** USD 10600.00 minor=1060000, incoterm EXW, linked to `supplier_offer`.

### 4.7 `catalog.product_commercial_link`

Joins catalogue to deals, warm cases, equipment opportunities, website.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `product_id` | INTEGER FK NOT NULL | |
| `link_kind` | TEXT | `commercial_deal_line`, `warm_case`, `equipment_opportunity`, `website_product`, `purchase_event_item` |
| `link_ref` | TEXT NOT NULL | e.g. `deal:serva-ceaf-oc-26172-po-174-26:line:1`, `warm_case:rg-energia-ika-rv10.70-3812200`, `web:blueslick-42500` |
| `confidence` | TEXT | |
| `created_at` | TEXT | |
| UNIQUE | `(link_kind, link_ref)` | |

---

## 5. Product identity strategy

1. **`product_key`** is the stable internal identifier (never reuse across distinct physical items).
2. **One product, many aliases** — all supplier/client codes normalize to `catalog.product_alias` (§6).
3. **Website slugs** are optional secondary keys — not every catalogue row needs a public page (IKA part, CRTOP reactor).
4. **Warm case thread hints** (e.g. `rg-energia-ika-rv10.70-3812200`) map to `product_commercial_link.link_ref` until formal case IDs exist in mirror API.
5. **Deal lines** link via `commercial_deal_line.product_id` → same `catalog.product.id` (unify with `commercial_product` during Phase 8B seed).
6. **Equipment opportunities** link at **category + description** level first; refine to `product_id` when operator confirms model.

**Merge policy:** Operator review required when two aliases differ in brand or unit of measure; automated merge only when normalized codes match exactly.

---

## 6. SKU / model normalization rules

| Rule | Example |
|------|---------|
| Strip spaces, dashes, leading zeros **per alias table** (keep raw in evidence) | `0003812200` → alias `3812200` AND retain `0003812200` row |
| Uppercase alphanumeric codes | `olt-hp-5l` → model `OLT-HP-5L` |
| SERVA 7-digit vs 6-digit | `004250001`, `4250001`, web `42500` → one product |
| Do not conflate **model** vs **part number** | Store both `model_number` on product and `part_no` alias kind |
| Website slug ≠ SKU | `blueslick-42500` slug maps to product_key `serva-blueslick-250ml` |
| Currency amounts | Always `amount_decimal` + `amount_minor` pair per deal-ledger rules |
| Hazard / shipping | Copy flags from `commercial_product` design (`is_hazardous`, `requires_special_shipping`) |

**Validation script (Phase 8B):** extend pattern from `apps/web/scripts/validate-catalog.mjs` — check alias uniqueness, orphan links, forbidden public price flags.

---

## 7. Category taxonomy

**Two axes** (do not merge):

| Axis | Purpose | Examples |
|------|---------|----------|
| **Website buyer line** (`categories.ts`) | Marketing / SEO | alimentos, control-de-calidad, laboratorio-clinico |
| **Catalog technical category** (`catalog.product_category`) | Operator search & reporting | `electrophoresis_reagent`, `microcentrifuge`, `analytical_balance`, `lab_reactor`, `ultrasonic_processor` |

**`equipment_class`** aligns with:

- `equipment_first_licitacion_queue.py` categories
- `business_mart.equipment_tags_from_text` (Spanish tags)
- Dashboard `operatorLabels.ts` (centrifuge, balance, incubator, …)

**Mapping table (seed candidates):**

| product_key (proposed) | equipment_class | catalog category_key |
|----------------------|-----------------|----------------------|
| `serva-blueslick-250ml` | — | `electrophoresis_reagent` |
| `serva-temed-25ml` | — | `electrophoresis_reagent` |
| `ika-rv10-70-vapor-tube` | — | `heating_accessory` |
| `crtop-olt-hp-5l` | `reactor` | `lab_reactor` |
| `ollital-reactor-5l` | `reactor` | `lab_reactor` |
| `hielscher-up100h` | `sonicator` | `ultrasonic_processor` |
| `ortoalresa-biocen-22` | `centrifuge` | `microcentrifuge` |
| `balance-analytical-generic` | `balance` | `analytical_balance` |

---

## 8. Price snapshot strategy

| Principle | Implementation |
|-----------|----------------|
| **Append-only** | Never UPDATE amount in place; supersede with new snapshot |
| **Source tagged** | `snapshot_kind` + FK to `supplier_offer` or deal line |
| **Ambiguity explicit** | NULL currency + operator note (IKA) |
| **Public safety** | `is_public_safe=0` default; website stays “cotizar” |
| **No FX invention** | FX only when deal ledger provides evidence-backed rate |
| **Dashboard display** | Show latest snapshot per product + supplier + incoterm; history in secondary panel |

**Not in v1:** competitor price scraping, automatic CLP conversion for quotes.

---

## 9. Spec modelling strategy

| Source | Storage | Dashboard |
|--------|---------|-----------|
| Website `keySpecs[]` | Import to `catalog.product_spec` (`source=website_editorial`) | Read-only on product drawer |
| Ortoalresa PDFs | Existing public URLs in web product; specs copied explicitly | Same |
| Supplier quote (CRTOP) | Spec rows from operator-confirmed extraction | Grouped by `spec_group` |
| Part-only items (IKA tube) | Minimal spec: model + part alias | No invented thermal/pressure specs |

**Conflict rule:** `operator_confirmed` > `manufacturer_datasheet` > `supplier_quote` > `extracted_needs_review`.

---

## 10. Opportunity / deal linking strategy

```
catalog.product
    ├── product_commercial_link → commercial_deal (via deal_key + line_number)
    ├── product_commercial_link → commercial.warm_case (via case_key / thread hint)
    ├── product_commercial_link → commercial.equipment_opportunity (via codigo_licitacion)
    ├── product_commercial_link → website Product.id
    └── supplier_offer → price_snapshot
```

| Source system | Link when | Do not link when |
|---------------|-----------|------------------|
| **Deal ledger** | Line has `ref_code` or `product_id` | Payment-only rows |
| **Warm case** | Classification `supplier_quote_received` or `client_opportunity` with model in subject | `internal_admin`, `payment_admin` |
| **Equipment CSV** | Category + item_description match product_key prefix | Generic SEREMI consumable lines |
| **Mart opportunity_signals** | Use as **discovery** only — too coarse for SKU | — |

**RG Energía / IKA:** one `product_id`, two warm cases (client + supplier) share `link_ref` thread hint.  
**SERVA / CEAF:** two products, one deal, multiple `price_snapshot` from proforma + payments.  
**CRTOP:** one product, one `supplier_offer`, specs + USD EXW snapshot.

---

## 11. Redaction and privacy rules

Aligned with `COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md` §6 and `commercial_deal_mirror_read_model.FORBIDDEN_MIRROR_JSON_KEYS`.

| Never in catalogue mirror / dashboard API | Allowed operator-only in SQLite |
|------------------------------------------|--------------------------------|
| Email bodies, full PDF text | `evidence_email_id` pointer |
| Bank account, SWIFT, IBAN | — |
| RUT / tax IDs | — |
| Gmail URLs, `source_file` paths | Internal evidence FKs |
| `transfer_id`, `operation_id` | Deal payments table (deal schema) |
| Supplier private discount tiers | Aggregated price snapshot only |
| Personal addresses | — |

**Supplier terms on dashboard:** payment/delivery **summaries** only (e.g. “T/T 100% prepaid”, “EXW”) — never wire instructions.

---

## 12. Dashboard recommendations (Phase 8E)

| Page / section | Spanish label | Content |
|----------------|---------------|---------|
| New nav item | **Catálogo** | Search/filter by brand, category, equipment_class |
| List columns | | Nombre, Marca, Categoría, Última oferta proveedor, Vínculos (negocios/casos) |
| **Producto** detail drawer | | `public_summary`, specs grouped, aliases |
| **Ofertas de proveedor** | | `catalog.supplier_offer` list for product |
| **Historial de precios** | | `price_snapshot` timeline (currency + incoterm + confidence) |
| **Oportunidades y negocios vinculados** | | Links to Deals, Warm cases, Equipment rows |
| CTA | | “Abrir negocio” / “Ver caso” (deep link only — GET, read-only) |

**Integration with existing sections:**

- **Proveedores:** add “Productos cotizados” when `product_id` linked.
- **Negocios:** show resolved product names via `product_commercial_link` not raw `description` only.
- **Oportunidades:** optional filter “con producto catalogado”.

---

## 13. Seed candidates (Phase 8B)

| product_key | display_name | Aliases | Primary source |
|-------------|--------------|---------|----------------|
| `serva-blueslick-250ml` | BlueSlick™ 250 ml | `42500`, `4250001`, `004250001` | Web + CEAF deal |
| `serva-temed-25ml` | TEMED 25 ml | `3593002`, `003593002` | Web + CEAF deal |
| `ika-rv10-70-vapor-tube` | IKA RV10.70 vapor tube | `3812200`, `0003812200` | Warm cases RG Energía |
| `crtop-olt-hp-5l` | CRTOP Lab Reactor OLT-HP-5L | `OLT-HP-5L` | Supplier quote case |
| `ollital-reactor-5l` | Ollital 5 L reactor | (subject-derived) | Warm case |
| `hielscher-up100h` | Hielscher UP100H | `UP100H` | Email / equipment patterns |
| `ortoalresa-biocen-22` | Ortoalresa Biocen 22 | web slug `biocen-22` | Website specs |
| `ortoalresa-digicen-22-r` | Ortoalresa Digicen 22 R | web slug `digicen-22-r` | Website |
| `balance-analytical-generic` | Balanza analítica (genérico) | — | Equipment queue category |

**Initial price snapshots (operator-confirmed only):**

- CRTOP: USD 10600.00 EXW, qty 1  
- IKA: amount 112.00, currency TBD  
- SERVA/CEAF: from deal ledger lines (CLP client / EUR supplier) — link, do not duplicate without evidence IDs  

---

## 14. Explicit “do not do yet”

- No inventory quantities or stock commitment fields  
- No warehouse locations or ATP  
- No public ecommerce list prices on website  
- No automated client quote PDF / email generation from catalogue  
- No supplier private terms on dashboard unless redacted to safe summaries  
- No Alembic migration until design approval after this audit  
- No Gmail send/mutate or outreach writes  
- No production Postgres sync without explicit operator approval  

---

## 15. Implementation plan

| Phase | Deliverable | Writes? |
|-------|-------------|---------|
| **8A** | This audit (`docs/catalog/PRODUCT_CATALOG_SCHEMA_AUDIT_V1.md`) | Docs only |
| **8B** | `catalog_seed_v1.json` + `scripts/catalog/build_catalog_sqlite.py` + SQLite DDL in email-pipeline | Local SQLite only |
| **8C** | Alembic `catalog.*` mirror + sync module (redacted) + verify script | Postgres mirror (approved env) |
| **8D** | `GET /mirror/catalog/products`, `GET /mirror/catalog/products/{key}` in `apps/api` | Read API |
| **8E** | Dashboard **Catálogo** page + product drawer (GET-only) | UI only |

**Tests (each phase):**

- 8B: seed normalization, alias collision, forbidden keys in export  
- 8C: mirror redaction parity with deal mirror tests  
- 8D: API contract tests (no bodies, no bank fields)  
- 8E: Vitest drawer renders specs/ prices without raw evidence  

**Relationship to deal ledger:** Implement `catalog.product` first; migrate `commercial_product` rows to aliases or mark `commercial_deal_line.product_id` FK to `catalog.product.id` in a follow-on migration approved separately.

---

## 16. Files inspected (Phase 8A)

| Path | Relevance |
|------|-----------|
| `apps/web/src/data/products.ts` | Product model, SERVA + Ortoalresa SKUs |
| `apps/web/src/data/brands.ts` | Brand metadata |
| `apps/web/src/data/categories.ts` | Buyer-facing categories |
| `apps/web/src/data/productFamilies.ts` | Centrifuge family |
| `apps/web/scripts/validate-catalog.mjs` | Catalogue integrity rules |
| `apps/web/src/components/HomeCommercialLines.astro` | SERVA home cards |
| `docs/commercial/COMMERCIAL_DEAL_LEDGER_SCHEMA_V1.md` | `commercial_product`, money rules, redaction |
| `apps/email-pipeline/src/origenlab_email_pipeline/warm_case_classification.py` | Warm case taxonomy |
| `apps/email-pipeline/src/origenlab_email_pipeline/commercial/commercial_deal_mirror_read_model.py` | Mirror redaction |
| `apps/email-pipeline/src/origenlab_email_pipeline/commercial/commercial_purchase_schema.py` | Purchase line items |
| `apps/email-pipeline/src/origenlab_email_pipeline/warm_case_promotion.py` | RG/CRTOP case keys |
| `apps/email-pipeline/src/origenlab_email_pipeline/warm_case_classification.py` | Operator summaries |
| `apps/email-pipeline/src/origenlab_email_pipeline/business_mart.py` | Equipment tags |
| `apps/email-pipeline/scripts/mart/build_business_mart.py` | `opportunity_signals` |
| `apps/email-pipeline/src/origenlab_email_pipeline/equipment_opportunity_mirror.py` | Tender mirror |
| `apps/dashboard/src/lib/dashboardNav.ts` | Current sections |
| `apps/dashboard/src/lib/supplierEntityGrouping.ts` | Supplier domains |
| `apps/dashboard/src/lib/operatorLabels.ts` | Equipment labels |
| `docs/PROJECT_CONTEXT.md` | Monorepo truth rules |

---

## 17. Decision log (for approval)

| # | Decision | Recommendation |
|---|----------|----------------|
| 1 | Schema namespace | `catalog.*` separate from `commercial.deal` |
| 2 | Source of truth for public copy | Website TS until import pipeline exists |
| 3 | Source of truth for executed deals | Existing deal ledger + catalogue links |
| 4 | Unified product table | `catalog.product` supersedes ad-hoc `commercial_product` over time |
| 5 | Price history | Append-only `catalog.price_snapshot` |
| 6 | First dashboard surface | Catálogo list + drawer (Phase 8E) |

**Next step after approval:** Phase 8B seed file + local SQLite builder (no Postgres migration until 8C sign-off).
