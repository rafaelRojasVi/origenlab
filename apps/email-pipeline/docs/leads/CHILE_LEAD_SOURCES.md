# Chilean Lead Sources for Laboratory and Industrial Equipment Buyers

**Part I** (below): market map, public sources, keyword packs, and outreach framing. **Part II** (end of doc): implementation analysis — how this scope fits the OrigenLab repo and effort levels. *Former standalone doc:* `CHILE_LEAD_SOURCES_IMPLEMENTATION_ANALYSIS.md` now redirects to Part II.

> **Runtime-status note (2026-08):** Part I is market/prospecting research. Part II preserves implementation analysis written against the former Streamlit UI and therefore contains historical UI/path assumptions. Streamlit was removed on 2026-06-04; the active operator UI is [`apps/dashboard`](../../../dashboard/README.md) backed by [`apps/api`](../../../api/README.md). Treat Streamlit references below as historical design context, not current implementation guidance.

## Executive summary

Chile has unusually strong **public and quasi-public signal** for laboratory/industrial equipment demand because (a) most public institutions buy through **public procurement**, (b) many laboratories must appear in **official registries** (accreditation, environmental authorization, sectoral authorizations), and (c) research activity is trackable through national **funding and center** frameworks. The result is that you can build a high-quality prospect list without buying data—then selectively add paid enrichment later.

The highest-leverage engines for new customers in Chile are:

- **Public procurement intelligence**: Use the Mercado Público / ChileCompra ecosystem to identify **who buys** (buyers), **what they buy** (line-item descriptions), and **when** (tender calendars, purchase orders), and make a weekly pipeline of opportunities.
- **Accredited and regulated laboratories**: Lists from INN (accreditation directory), SMA (ETFA registry), SAG (authorized labs), SERNAPESCA (authorized diagnostic labs), and ISPCh (recognized labs) reveal **labs that must continuously maintain/upgrade instrumentation**.
- **Research-funded demand**: Use ANID and national "centers of excellence" frameworks to find institutions running funded science and applied R&D, which correlates with demand for analytical and lab equipment.
- **Sector member directories** (fast for private buyers): SalmonChile, Chilealimentos A.G., and Consejo Minero give a curated list of large producers and suppliers with QA and process labs.

What is **not specified** (and would improve targeting): your exact **SKUs/brands**, service coverage (national vs regional), calibration/maintenance capabilities, and which verticals you prioritize (clinical vs food QA vs mining vs academic). The report therefore uses **equipment-category keywords** rather than SKU-level matching.

## Market map and lead-generation thesis

Your equipment categories map cleanly to a few buyer "lab archetypes" in Chile; this is useful because each archetype appears in **different public sources**, and you can cross-validate.

**Analytical / chemistry labs (HPLC, balances, pH meters)**  
Typical buyers: university chemistry labs, food safety labs, environmental labs, mining analytical labs, contract testing firms. These appear strongly in: accredited/recognized lab lists (INN, ISPCh DS 707), environmental ETFA registry, and procurement.

**Microbiology / life-science labs (centrifuges, autoclaves, microscopes, pH meters)**  
Typical buyers: universities, hospitals/clinics, aquaculture health labs, public labs in agriculture/fisheries, and some private biotech. These appear strongly in: Superintendencia de Salud registry (institutional providers), Sernapesca diagnostics lists, university/center frameworks, procurement.

**Industrial QA labs (balances, pH, moisture, microscopes)**  
Typical buyers: wine, salmon, food processing, water utilities, pulp/paper, mining operations, and contractors. These appear strongly in: sector association directories plus the procurement portals of large private buyers (mining/utilities/industry).

The thesis for building a prospect engine is:

1. Start with sources where the **true buyer** is explicit (procurement + regulated lab registries).  
2. Use your business mart to identify which equipment themes are strongest (e.g., "balanza", "HPLC") and build **keyword packs**.  
3. Build weekly lead lists: **new tenders**, **new/updated labs**, **new funded projects**, **member lists**.  
4. Enrich into domains + roles, then outreach.

## Public and official sources to mine first

The table below prioritizes sources that (a) are official or widely relied upon, (b) contain listable entities (labs/organizations/projects), and (c) create **actionable outreach targets**.

### Top public sources


| Source                                                       | URL                                                                                                                                                                                                    | What data you can extract                                                                                                                     | How to query for equipment-related opportunities (Spanish terms)                                                                                                                                                                                                                  | Access constraints / "scraping" posture                                                                                | Update frequency signals                                                                   | Reliability                                            |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| ChileCompra Datos Abiertos (buyer & supplier profiles)       | `https://datos-abiertos.chilecompra.cl/`                                                                                                                                                               | Buyer "fichas": what they buy, which suppliers, amounts, contracts; downloads of POs/tenders; "Convenio Marco" price lists via CSV downloads. | Start from buyer fichas + download POs/tenders; search within downloads for: "balanza analítica", "centrífuga", "HPLC", "cromatógrafo", "autoclave", "microscopio", "pH-metro", "medidor de humedad", "analizador de humedad", "equipo de laboratorio", "instrumental analítico". | The guide explicitly positions it for analysis; prefer official downloads/API rather than scraping dynamic pages.      | Downloads are designed for ongoing use; API offers "real-time" access.                     | Very high (official procurement source).               |
| API Mercado Público                                          | `https://api.mercadopublico.cl/`                                                                                                                                                                       | Real-time programmatic access to tendering + purchase orders + buyers/suppliers; supports market analysis and alerts.                         | Pull "licitaciones activas" then filter on text fields with equipment keywords; maintain aliases (e.g., "cromatografía", "HPLC", "cromatógrafo líquido", "cromatógrafo gases/GC").                                                                                                | Requires requesting a ticket; ticket request requires login with Clave Única per the official API instructions.        | "Real-time" stated; operational continuity subject to terms/changes.                       | Very high (official).                                  |
| Directorio de Acreditados INN                                | `https://directorio.inn.cl/`                                                                                                                                                                           | Searchable directory of accredited conformity assessment bodies (incl. testing laboratories), with accreditation scheme/area/state.           | Filter "Esquema de Acreditación" → "Laboratorios de ensayo" and relevant "Área" (e.g., "Microbiología para alimentos"); then build a lab list.                                                                                                                                    | Web search UI; no stated public API in the directory interface; use manual export or respectful crawling if compliant. | Continuous as accreditations change; directory supports "vigentes/suspendidos/cancelados." | High (national accreditation authority).               |
| RETFA (Superintendencia del Medio Ambiente)                  | `https://entidadestecnicas.sma.gob.cl/sucursal/registropublico`                                                                                                                                        | Registry of Environmental Technical Entities (ETFA) and branches; names, status changes, and update log.                                      | Identify ETFA labs and contact them (environmental monitoring and analysis often require balances, pH meters, chromatographic methods, etc.). Keywords for follow-on procurement work: "aguas residuales", "muestreo", "análisis", "laboratorio ambiental".                       | Public registry; treat as authoritative list.                                                                          | Public registry shows its own "Última Actualización" date.                                 | High (regulator-operated registry).                    |
| Instituto de Salud Pública de Chile (ISPCh) list (DS 707/96) | `https://www.ispch.gob.cl/ambientes-y-alimentos/laboratorios-reconocidos-segun-ds-707-96-minsal/`                                                                                                      | Named list of labs recognized under DS 707/96; includes regions and lab names; page includes "información actualizada al…" date.              | Treat as a curated prospect list for food/water/environment testing service labs; follow-on keyword pack: "microbiología", "inocuidad", "alimentos", "aguas".                                                                                                                     | Public web list; no API; minimal friction.                                                                             | Page shows a last update date (baseline).                                                  | High (public health authority source).                 |
| SAG (authorized labs + downloadable registries)              | Agrícola: `https://www.sag.gob.cl/ambitos-de-accion/laboratorios-de-analisis-y-ensayos/registros`; Pecuario: `https://www.sag.gob.cl/ambitos-de-accion/laboratorios-de-analisis-y-ensayos-1/registros` | Authorized lab registries, many as downloadable XLSX; each entry has an update timestamp and topic.                                           | Download XLSX registries and extract lab names + regions; equipment keyword pack: "PCR", "ELISA", "microbiología", "residuos", "contaminantes", "fertilizantes", "plaguicidas", "semillas", "agua".                                                                               | Public registries; better to use the downloadable files rather than scraping pages.                                    | Entries show frequent updates (e.g., listings with 2026 timestamps).                       | High (sector regulator lists).                         |
| SERNAPESCA diagnostics list                                  | `https://www.sernapesca.cl/app/uploads/2025/05/laboratorios_autorizados_por_sernapesca_v20240516.pdf`                                                                                                  | Machine-readable PDF table: authorized labs, locations, validity dates, and test types (PCR/RT-PCR/etc.).                                     | Build a list of aquaculture health labs; keyword pack: "diagnóstico", "PCR", "laboratorio", "acuicultura", "sanidad", "IHN", "ISA".                                                                                                                                               | Public PDF; easy to parse into CSV.                                                                                    | The document is dated (e.g., "16 de mayo 2025") and includes validity windows.             | High (official list).                                  |
| Superintendencia de Salud (registro prestadores acreditados) | `https://www.superdesalud.gob.cl/tramites/registro-de-prestadores-acreditados/`                                                                                                                        | Public registry of accredited health providers (clinics, hospitals, medical centers, laboratories) and how to access it online.               | Use it to build a list of institutional buyers with labs; keyword pack for targeting: "laboratorio clínico", "microbiología", "esterilización", "autoclave", "centrífuga".                                                                                                        | Online access indicated as immediate and free.                                                                         | Registry changes with accreditation status; treat as dynamic.                              | High (health regulator).                               |
| INIA (labs + contacts)                                       | `https://www.inia.cl/laboratorios/`                                                                                                                                                                    | Named labs, services, and frequently direct contact emails/phones for labs (agri genetics, soil, etc.).                                       | Target grain/moisture + soil/water labs; keywords: "humedad", "granos", "suelos", "agua", "fertilidad", "calidad".                                                                                                                                                                | Public pages; direct outreach feasible.                                                                                | Institutional pages; update cadence not explicit.                                          | High (public-linked research institute).               |
| ANID funded projects dataset                                 | `https://github.com/ANID-GITHUB/Historico-de-Proyectos-Adjudicados`                                                                                                                                    | A historical CSV of awarded projects (1982–2025) with stated cutoff date; CC0 license.                                                        | Filter the project CSV for keywords strongly correlated with equipment: "cromatografía", "HPLC", "metabolómica", "bioquímica", "microbiología", "biotecnología", "alimentos", "aguas", "contaminantes", "minería", "relaves".                                                     | GitHub download; easiest to process in Python/Pandas.                                                                  | Updated with a defined cutoff (annual-ish release behavior implied by cutoff).             | High (primary dataset from funding agency).            |
| Ministerio de Ciencia (Minciencia) centers plan              | `https://www.minciencia.gob.cl/centros/`                                                                                                                                                               | Explains national centers system; indicates number of centers and categories/framework.                                                       | Use to identify the main categories (Milenio, FONDAP, PIA, regional) then pull center lists to contact operations managers/lab managers.                                                                                                                                          | Web page; no API stated.                                                                                               | Framework-level; updated as policy evolves.                                                | High (ministerial source).                             |
| Iniciativa Científica Milenio list                           | `https://www.iniciativamilenio.cl/institutos-y-nucleos-milenio/`                                                                                                                                       | List of "Institutos y Núcleos Milenio" and links to center pages.                                                                             | Use center names + host institutions to build a target list of labs with active research lines; keyword pack for your equipment: "química", "biología", "inmunología", "oceano", "salud".                                                                                         | Public list; external center PDFs/links.                                                                               | Center rosters change over time; treat as dynamic.                                         | High (program-level primary directory).                |
| CORFO "Centros I+D" registry                                 | `https://sgp.corfo.cl/GIN/ActualizacionCentrosID/Views/publico/centros.aspx`                                                                                                                           | A searchable roster of registered R&D centers with sponsor, region, and research areas.                                                       | Filter by relevant areas: "alimentos", "biotecnología", "salud", "minería", "agua", "materiales".                                                                                                                                                                                 | Web UI.                                                                                                                | Registry changes with center registrations.                                                | High (government agency registry).                     |
| SalmonChile (industry members list)                          | `https://www.salmonchile.cl/quienes-somos-salmonchile/socios-salmonchile/`                                                                                                                             | Member list context: producers, hatcheries, and a long tail of suppliers incl. labs, pharma, logistics.                                       | Build a private-industry prospect list; keyword pack: "laboratorio", "calidad", "sanidad", "inocuidad", "microbiología", "diagnóstico".                                                                                                                                           | Public page; member details may require manual extraction.                                                             | Updates as membership changes.                                                             | Medium-high (industry association, but self-reported). |
| Chilealimentos A.G. (members list)                           | `https://chilealimentos.com/nuestros-asociados/`                                                                                                                                                       | Directory of associated companies and mentions "información de contacto respectiva".                                                          | Use as a curated set of food processing firms (QA labs need balances, pH, moisture, microscopes). Search firm sites for "calidad", "laboratorio", "I+D".                                                                                                                          | Public site; contact details vary.                                                                                     | Changes with membership.                                                                   | Medium-high (industry association).                    |
| Consejo Minero (members list)                                | `https://consejominero.cl/nosotros/socios/`                                                                                                                                                            | Member list of major mining groups operating in Chile; links to each "ficha".                                                                 | Use to build named strategic accounts (mining sites have labs, process monitoring, contractor networks). Keywords: "laboratorio", "metalurgia", "relaves", "control calidad", "agua".                                                                                             | Public page.                                                                                                           | Changes as membership changes.                                                             | Medium-high (industry association).                    |


### Query keyword packs you should standardize

Below are high-yield Spanish keyword packs you can reuse across procurement search, project search in datasets, and web enrichment:

- **Balances**: "balanza analítica", "balanza de precisión", "balanza industrial", "balanza granataria", "báscula", "calibración balanza".
- **Centrifuges**: "centrífuga", "centrífuga refrigerada", "microcentrífuga", "ultracentrífuga", "rotor", "tubos falcon".
- **HPLC / chromatography**: "HPLC", "cromatografía líquida", "cromatógrafo", "detector UV", "columna HPLC", "bomba HPLC".
- **Autoclaves / sterilization**: "autoclave", "esterilizador", "esterilización", "vapor", "bioseguridad".
- **Microscopy**: "microscopio", "microscopía", "lupa estereoscópica", "objetivos", "cámara microscopio".
- **pH / water quality**: "pH-metro", "medidor pH", "conductímetro", "turbidímetro", "calidad de agua".
- **Moisture / grains**: "humedad granos", "medidor de humedad", "analizador de humedad", "secado", "trigo", "maíz".

## Universities and research centers to prioritize

(Table of institutions with contact roles and URLs — see full report for detailed rows.)

## Procurement portals and marketplaces to monitor

(Table of portals — Mercado Público, ChileCompra, API, CENABAST, Codelco, ENAP, SQM, Enel, Aguas Andinas, Arauco, Antofagasta Minerals, CMP, Collahuasi, Anglo American, Red ProNorte, SAWU, Senegocia.)

## Outreach targets and what to say

### Who to contact first

- **First wave**: Accredited/regulated labs (INN; ISPCh DS 707; SMA ETFA; SAG/Sernapesca). Industry associations' member companies.
- **Second wave**: Public procurement buyers (ChileCompra / Mercado Público): universities, public hospitals, public institutes.
- **Third wave**: Strategic accounts: mining and utilities with vendor portals (Codelco, ENAP, SQM, etc.).

### Suggested contact roles/titles (Spanish)

- "Jefe(a) de Laboratorio" / "Director(a) de Laboratorio"
- "Encargado(a) de Calidad" / "Jefe(a) de Aseguramiento de Calidad"
- "Químico(a) Analista" / "Jefe(a) de Microbiología"
- "Ingeniero(a) Biomédico(a)" / "Unidad de Equipos Médicos"
- "Abastecimiento" / "Compras" / "Jefe(a) de Adquisiciones"
- "Encargado(a) de Mantención"

### Email templates in Spanish

(Templates A–D for lab/QA manager, university/research admin, public procurement buyer, and follow-up — see full report.)

## How to operationalize this with your OrigenLab business mart

- **Recommended exports**: organization_master, top equipment demand, quote-heavy relationships (CSV/SQLite).
- **External list ingestion**: Parse into `external_entities.csv` (entity_name, city, region, source, source_url, category); enrich domains; join to `organization_master.domain` for "new vs known".
- **Equipment-fit scoring**: +3 regulated/accredited lab list; +2 industry association; +4 active procurement buying; +1 website keywords.

### Sample outreach CSV schema


| Column                                     | Type    | Description                                                                                    |
| ------------------------------------------ | ------- | ---------------------------------------------------------------------------------------------- |
| lead_id                                    | string  | Stable unique ID (e.g., hash of domain + source).                                              |
| organization_name                          | string  | Company/lab/university name.                                                                   |
| domain                                     | string  | Normalized domain (for joining to mart).                                                       |
| lead_category                              | enum    | public_buyer, private_company, accredited_lab, research_center, hospital_lab, aquaculture_lab. |
| sector                                     | string  | mining, food, wine, aquaculture, health, environment, agriculture, academia.                   |
| region, city                               | string  | Chilean region/city when available.                                                            |
| source_name, source_url                    | string  | e.g., ChileCompra, INN, SMA RETFA, ISPCh DS707, SAG, Sernapesca, SalmonChile.                  |
| evidence_keywords                          | string  | Comma-separated equipment keywords.                                                            |
| suggested_products, suggested_roles        | string  | Which categories to pitch; roles to target.                                                    |
| contact_name, contact_title, contact_email | string  | If known.                                                                                      |
| outreach_status                            | enum    | not_contacted, sent, replied, meeting, quote_sent, won, lost.                                  |
| priority_score                             | integer | Heuristic score (0–10+).                                                                       |
| notes                                      | string  | Free text.                                                                                     |


## Ninety-day action plan

- **30 days**: Normalized external leads CSV (INN, ISPCh, SMA, SAG, Sernapesca); minimal procurement alert loop (Mercado Público API + keyword packs); first outreach sprint (100–200 emails). (~25–40 h data + ~10–15 h outreach.)
- **60 days**: Lead enrichment (domains + 1–2 roles); "External Leads" view in Streamlit; weekly procurement scan + monthly regulated-list refresh. (~40–60 h.)
- **90 days**: Full lead pipeline with tracking; two proven sequences with reply/meeting rates; optional paid enrichment. (~60–100 h.)

## Final notes on compliance and practicality

- Prefer official feeds and downloadable registries over scraping; ChileCompra supports data reuse via downloads and API.
- Use the OrigenLab mart to prioritize sectors/equipment that already show strong commercial signal, then expand with regulated lists and procurement.

---

## Part II — Implementation analysis: repo fit vs email pipeline

This section analyzes how difficult it would be to implement the Chilean lead-sources scope (Part I) **within or alongside** the current OrigenLab email-pipeline project. **Status note:** much of the “easy” layer is now implemented as `lead_master` / `external_leads_raw` / scripts under `scripts/leads/`; treat the tables and effort estimates below as design context and update code paths if they drift.

### 1. What the project already has (strong fit)

| Scope item | Current state | Fit |
|------------|---------------|-----|
| **Business mart** | `organization_master`, `contact_master`, `document_master`, `opportunity_signals` in SQLite; built by `scripts/mart/build_business_mart.py`. | **Strong.** The report’s “export organization_master / top equipment / quote-heavy” and “join external leads to domain” assume exactly this. |
| **Equipment tags** | Keyword-based tags in the mart (balanza, centrifuga, cromatografia_hplc, autoclave, microscopio, phmetro, humedad_granos, etc.) and in the app’s `EQUIPMENT_INFO`. | **Strong.** Aligns with the report’s “keyword packs” and equipment-fit scoring; you can reuse/extend the same tag set. |
| **Domain-centric org model** | `organization_master.domain` as primary key; org name/type guesses. | **Strong.** External lists are “name/location then enrich to domain”; joining on `domain` is the intended design. |
| **Streamlit app** | `apps/business_mart_app.py`: Resumen, Contactos, Organizaciones, Documentos, Oportunidades. | **Good.** Adding an “External Leads” or “Prospectos externos” view is a natural new page/tab. |
| **Export / SQL** | Mart is SQLite; report suggests `sqlite3` + CSV exports. | **Strong.** You can add export scripts or in-app “Export CSV” that match the report’s suggested queries. |
| **Leads pipeline (implemented after this analysis was written)** | `lead_master`, `external_leads_raw`, `lead_matches_existing_orgs`, `lead_outreach_enrichment`; `scripts/leads/`. | **Strong.** Covers file-based ingest, scoring, matching to mart, hunt CSVs, and SQLite enrichment. |

So: **the “internal” side (mart, equipment tags, domains, UI shell, leads tables) is largely in place.** The gap is **optional automation** for each public source (API, scrapers, scheduled downloads) beyond file ingest.

### 2. What would need to be built (by difficulty)

#### 2.1 Easy (weeks, not months)

- **External leads schema and storage**  
  Add a table (e.g. `external_leads` or `external_entities`) with the report’s columns: `lead_id`, `organization_name`, `domain`, `lead_category`, `sector`, `region`, `city`, `source_name`, `source_url`, `evidence_keywords`, `suggested_products`, `suggested_roles`, `contact_*`, `outreach_status`, `priority_score`, `notes`.  
  **Note:** v1 implemented `lead_master` + `external_leads_raw` instead of a single `external_leads` table; same idea.

- **CSV export from mart**  
  Script or app action that exports `organization_master`, top quote orgs, and equipment mention counts to CSV as in the report.  
  **Effort:** Low. A few SQL queries and `pandas.to_csv` or `sqlite3` + file write.

- **“New vs known” join**  
  Query that left-joins external leads to `organization_master` on `domain` and flags `is_new_to_origenlab`.  
  **Effort:** Low. Single SQL view or query; can be exposed in Streamlit. **Implemented** as `lead_matches_existing_orgs`.

- **External Leads view in Streamlit**  
  New section that shows `external_leads` (and optionally the join to mart) with filters (source, sector, priority_score, outreach_status).  
  **Effort:** Low–medium. Same patterns as Contactos/Organizaciones (load DF, filters, table, maybe export button).

- **Keyword pack constants**  
  Central list of Spanish equipment keywords (report’s “keyword packs”) used for scoring and later for procurement/tender search.  
  **Effort:** Trivial. Config or Python dict/list.

#### 2.2 Medium (one to a few months, depending on automation level)

- **Ingestion from “list” sources**  
  - **ANID GitHub CSV:** Download CSV, filter by keyword pack, map to `external_leads` rows (project/institución → org name, optional domain lookup).  
  - **SAG XLSX:** Download from known URLs, parse, map to labs + regions.  
  - **SERNAPESCA PDF:** Parse table (e.g. tabula, camelot, or manual CSV export once), map to labs.  
  - **ISPCh / INN / SMA:** Manual export or one-off scraping → CSV → same loader as above.  
  **Effort:** Medium. Mostly one-off or periodic scripts; variability in format and stability of URLs is the main cost. No dependency on the email pipeline except for the final load into `external_leads` and join to mart.

- **Equipment-fit scoring**  
  Heuristic: +3 regulated list, +2 association, +4 procurement hit, +1 website keywords. Implement as a function over a row (or batch) and persist `priority_score`.  
  **Effort:** Low–medium. Logic is simple; “website keywords” may require a small fetch or manual step unless you add a crawler.

- **Lead enrichment (domain + roles)**  
  From “entity name + source” to “domain” and “1–2 target roles”. Can be semi-manual (spreadsheet + lookup) or automated (e.g. homepage URL from Google/search, then scrape “contacto” or LinkedIn).  
  **Effort:** Medium. Automating it is the bulk; a minimal version is “manual column + script that merges back into `external_leads`”.

#### 2.3 Harder (multi-month or ongoing)

- **Mercado Público / ChileCompra**  
  - **Open data (CSV/downloads):** Download and filter by keyword pack; map buyers to orgs and merge into `external_leads` or a separate “tender_alerts” table. Doable with scripts and cron.  
  - **API (real-time):** Requires Clave Única and ticket; rate limits and terms of use. Building a “weekly digest” or “alert when keyword match” is feasible but has legal/operational dependency (who holds the token, how it’s refreshed).  
  **Effort:** Medium–high. Not technically complex once you have access; access and ops are the main difficulty.

- **Private portals (Codelco, ENAP, SQM, etc.)**  
  Mostly registration and manual monitoring; no need to “implement” inside this repo except to record “we registered” and “we saw tender X” in notes or a simple log.  
  **Effort:** Organizational rather than dev.

- **Fully automated, scalable scraping**  
  INN directory, Minciencia centers, CORFO centers, association member lists, etc., often have no API and varying HTML. Robust scraping (respectful of ToS, handling changes) is a separate project.  
  **Effort:** High if you want reliability and maintenance; medium if you accept one-off or rare refresh.

### 3. Difficulty summary

| Component | Difficulty | Why |
|-----------|------------|-----|
| Schema + load for external leads | **Easy** | Standard CRUD + CSV import; fits current stack. |
| Mart exports (orgs, equipment, quotes) | **Easy** | SQL + CSV; already have mart and SQLite. |
| New vs known join + Streamlit “External Leads” | **Easy** | One join, one new UI section. |
| Keyword packs + equipment-fit scoring | **Easy–Medium** | Simple rules; optional website lookup. |
| Ingestion from ANID/SAG/SERNAPESCA/ISPCh/INN/SMA | **Medium** | Different formats; mostly one-off or periodic scripts. |
| Mercado Público (open data vs API) | **Medium–High** | Open data: scripting. API: access and ops. |
| Enrichment (domain + roles at scale) | **Medium** | Semi-manual is easy; automation is a product. |
| Full scraping of all listed sources | **High** | Many sources, no API, maintenance. |

**Overall:** Implementing the **core** of the scope—external leads table, load from a few key lists (e.g. ANID, SAG, SERNAPESCA, ISPCh), join to mart, priority score, and External Leads view in Streamlit—is **moderate difficulty** and fits well with the existing codebase. The report’s 30–60–90 day plan is plausible: the first 30 days are mostly schema + one or two list ingestions + manual/semi-manual outreach; the next 60 days add more sources, enrichment, and the Streamlit view; the rest is iteration and optional automation (API, scraping).

### 4. Recommended order of work

1. **Define `external_leads` (or equivalent) and a CSV loader**  
   So any list you build (manually or from ANID/SAG/PDF) lands in one place.

2. **Implement “new vs known” query and expose it**  
   Even on a small CSV, so you can see which external leads are already in `organization_master`.

3. **Add External Leads (or “Prospectos”) view in Streamlit**  
   Table + filters + optional CSV export; optionally show `is_new_to_origenlab` and `priority_score`.

4. **Add one or two real ingestions**  
   e.g. ANID CSV + SAG XLSX or SERNAPESCA PDF; run periodically and (re)load into `external_leads`.

5. **Add equipment-fit scoring**  
   So new rows get a `priority_score` and you can sort/filter by it.

6. **Optional: Mercado Público open-data pipeline**  
   Download, keyword filter, attach to buyer orgs; then decide if API is worth the access process.

7. **Keep outreach and enrichment**  
   In spreadsheets or a simple CRM until you need more structure; the mart + external leads already give you the “who” and “how they fit” to drive that process.

### 5. Conclusion

Implementing something like the Chilean lead-sources scope **in this project** is **feasible and of moderate difficulty**. The hardest parts are (a) getting and maintaining access to external sources (especially Mercado Público API), and (b) scaling enrichment and scraping if you want full automation. The existing business mart, equipment tags, and Streamlit app give you a direct path to “external prospect list + join to known orgs + view in app” without changing the core email pipeline.

