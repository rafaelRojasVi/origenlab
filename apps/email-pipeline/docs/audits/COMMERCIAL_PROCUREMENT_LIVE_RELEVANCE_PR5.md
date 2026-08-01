# Commercial procurement live candidate relevance — PR5A design & audit

**Status:** Design / audit / dry-run planning only (2026-08-01)  
**Branch:** `feat/commercial-procurement-live-relevance-pr5`  
**PR4 gate:** `PR4_PERSISTENCE_VALIDATED_READY_FOR_SEPARATE_PR5_DIRECTION`  
**Main SHA at branch start:** `0ac5d2ae99f19ae22de7d760eb69533adcd34d59`

This document does **not** authorize persistence, production `--apply`, live API fetches with ticket use beyond boolean configuration checks, contact hunting, or PR5B+.

Related artifacts (gitignored reports):

`apps/email-pipeline/reports/out/active/current/commercial_procurement_live_relevance_pr5_<UTC>/`

---

## 1. Objective

Turn official, **current** procurement records into a small, explainable queue of:

1. active product-relevant tenders;
2. tenders requiring account resolution;
3. tenders requiring contact research;
4. tenders that are outreach-ready **only after** verified-contact review.

PR4 remains procurement evidence truth. PR5 is a rebuildable **current-candidate interpretation**.

---

## 2. Production reality (read-only audit)

| Source | Finding |
|--------|---------|
| PR4 SQLite (`commercial_procurement_*`) | **16 448 / 16 448** signals are `historical_tender`. **0** rows with positive active evidence (Publicada/code `5` **and** `close_at` ≥ America/Santiago today). |
| Close dates vs Santiago today | All 16 448 closes are in the past; none missing. |
| Linked accounts | 42 linked signals; **8** unique accounts; **7** accounts have ≥1 PR2 contact. |
| Operator enrichment eligible | **0** (`operator_queue_eligible=1`). |
| Equipment-first API operator queue artifact `…_20260731.csv` | **5** rows with `validity_status=open` (Publicada / code 5); **1** expired. This is the only in-repo source with genuine open tenders at audit time. |
| `chilecompra_api_ticket_configured` | **true** (boolean only; value never logged). |

**Conclusion:** A live relevance plane **cannot** be populated from the current PR4 file-backed corpus alone. PR5D (official live acquisition) is required before non-zero active funnel counts. PR5A still designs the contract and demonstrates Cases A–E with real evidence where available.

Persisted PR4 semantic digest (unchanged by this audit):

`e542b0107214aff4beb242542770c250f83a90fc2304e0fd6f415ca3729e4f9a`

---

## 3. Duplication matrix (summary)

Full matrix: report `EXISTING_PATHS.json` and module `paths.py`.

| Concept | Existing implementations | Canonical candidate | Reuse / refactor / retire |
|---------|--------------------------|---------------------|---------------------------|
| Acquisition | File `fetch_chilecompra.py`; API `chilecompra_api.py`; `Licitacion_Publicada` parser | **API primary**, file fallback | Reuse client; no HTML scrape |
| Tender grain | PR4 verified key; lead line ids; queue `codigo` | **PR4 tender key** | One candidate / tender; lines separate |
| Active/close clocks | PR4 UTC AS_OF; API validity; equipment naive now; mirror Santiago | **America/Santiago active classifier** | Reuse ChileCompra codes; never treat `lead_master.status` as open |
| Equipment tags | Mart Spanish; equipment-first English; web filters | **Canonical `equipment_class` + aliases** | No silent rename of historical fields |
| Account link | PR4→PR2; leads→mart | **PR4→PR2** | Do not auto-merge mart |
| Queue eligibility | PR4 enrichment flag; equipment `next_action`; weekly focus | **PR5 `candidate_outcome_state` + human review** | Keep lanes separate in UI |
| Contacts | PR2; lead_master; hunt enrichment | Ordered search (below) | No invented emails |
| `fit_bucket` / `priority_score` | `leads_score.py` | **Not PR5 truth** | Analytics only |

Nothing is removed or deprecated in PR5A.

---

## 4. Live ChileCompra acquisition (design)

### Official API (canonical lane)

- Base: `https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json`
- Formats: JSON / JSONP / XML via GET
- Query modes (documented): by `codigo`; by `fecha` (ddMMyyyy); by `estado` (e.g. `activas`); combinations; ticket required
- Ticket env: `CHILECOMPRA_API_TICKET` (never commit/print)
- Existing client: `chilecompra_api.py` (redacts tickets from URLs/errors; validity classes `open` / `closes_today` / `expired` / …)
- Stable ids: tender `CodigoExterno` / código licitación; line items in detail payloads
- Status codes already shared: active `5` / Publicada; inactive `{6,7,8,18,19}`

**PR5A does not make authenticated production API calls.**

### Official bulk / Licitacion_Publicada (fallback)

- Grain: semicolon CSV export with line-level rows; equipment-first groups by tender code
- Strengths: reproducible offline corpus; feeds leads file ingest and historical PR4
- Weaknesses: update lag; not a substitute for “open today” without freshness SLA

**Recommendation:** API for live published/open polling; file bulk for backfill and PR4 rebuild inputs. Do not scrape Mercado Público HTML.

---

## 5. Candidate grain

| Plane | Grain |
|-------|-------|
| Source observation | `external_leads_raw` / API row / CSV line |
| Tender | One **candidate** per `canonical_tender_key` (PR4-aligned) |
| Tender line | `commercial_procurement_line_relevance` evidence |
| Buyer account | PR2 `account_id` (logical) |
| Contact | Separate contact-resolution rows |
| Relevance | Classifier decision + line evidence |
| Operator task | `candidate_outcome_state` — not send authority |

A contact is not a tender. An enrichment task is not outreach-ready.

---

## 6. Active-tender classification

Classifier version: `procurement_active_santiago_v1`  
Clock: **America/Santiago** (explicit).

Classes: `active_open`, `active_closing_soon`, `future_scheduled`, `closed`, `awarded`, `cancelled`, `status_conflict`, `date_missing`, `status_unknown`.

**Active eligibility requires positive evidence:**

- verified tender-level identifier;
- accepted source/status (Publicada / code `5`, or documented equivalent);
- parseable close (and publication when required);
- close instant **after** Santiago as-of;
- not cancelled/awarded/closed/inactive codes;
- no source-plane conflict on status/dates.

Closing-soon buckets (analytical only): `lt_24h`, `d1_to_d3`, `d4_to_d7`, `gt_7d`.  
Closing soon **never** alone makes a tender outreach-ready.

Do **not** call a record active because:

- `lead_type` contains tender;
- `lead_master.status=nuevo`;
- equipment tag present;
- stale `active/current` CSV without validity;
- missing close date.

---

## 7. Relevance classification

Classifier version: `procurement_relevance_v1`  
**Does not** use `priority_score` / `fit_bucket` as truth.

Classes: `exact_catalog_product`, `strong_equipment_class`, `compatible_equipment_class`, `laboratory_context_only`, `consumable_or_reagent`, `service_or_maintenance_only`, `rental_or_comodato`, `non_laboratory_false_positive`, `ambiguous`, `unrelated`.

Each decision retains: classifier version, matched spans, positive/negative rules, tender line IDs, equipment class, optional product candidate IDs, confidence, review status.

**Product-level safety:** `exact_catalog_product` only with stable model/SKU/part/unambiguous alias. Words like centrifuge/balance/autoclave/HPLC/microscope are **equipment classes**, not SKUs. Default without catalogue match: `product_resolution_status=equipment_class_only`.

Reuse equipment-first exclusion regexes and mart patterns via a shared alias map (`TAXONOMY_MAPPING.json`).

---

## 8. Contact resolution

Only after: verified tender + active/reviewable + commercially relevant + sufficiently clear buyer.

Search order:

1. PR2 contacts on resolved account  
2. `lead_master` contact fields  
3. `lead_outreach_enrichment`  
4. Observed business email participants for the account  
5. **No** external lookup in PR5A–PR5C  

Statuses: `existing_verified_contact`, `existing_contact_needs_role_review`, `role_known_email_missing`, `contact_research_required`, `ambiguous_contact`, `no_contact_found`, `contact_blocked`.

Rules: no generic mailbox as named person; no invented domain emails; no promotion solely from old mail appearance. Suppression/outreach checks are read-only gates.

---

## 9. Candidate outcome states

| State | Meaning |
|-------|---------|
| `relevant_tender` | Current + relevant (contact optional) |
| `contact_research_candidate` | Current + relevant + clear buyer; no verified suitable contact |
| `outreach_review_candidate` | Current + relevant + verified contact + suppression/outreach pass |
| `not_eligible` | Fails active and/or relevance and/or safety |

**No state authorizes sending.** Human review remains mandatory.

---

## 10. Proposed read-model (not created)

See `PROPOSED_SCHEMA.json` / `schema_design.py`. Candidate tables:

- `commercial_procurement_candidate`
- `commercial_procurement_line_relevance`
- `commercial_procurement_contact_resolution`
- `commercial_procurement_candidate_evidence`
- `commercial_procurement_candidate_conflict`
- `commercial_procurement_candidate_build_meta`

PR4 tables are never rewritten by PR5. Fingerprints: acquisition snapshot, PR4 dependency, PR2 dependency, taxonomy, relevance plan, semantic plan digest (exclude wall-clock). Stale-plan: expected-digest gates; no blind retry. **No apply in PR5A.**

---

## 11. Real-data walkthrough (Cases A–E)

Full redacted tables: report `DATA_WALKTHROUGH.md` / `.json`. Summary:

### Case A — genuine active + relevant

- **Source:** `equipment_first_operator_queue_chilecompra_api_20260731.csv`
- **Evidence:** `validity_status=open`, status Publicada/code 5, equipment_category `centrifuge`, future close
- **Not** present in PR4 SQLite corpus → account resolution deferred; outcome `contact_research_candidate`
- **Not synthetic.**

### Case B — historical equipment

- Real PR4 `historical_tender` with equipment keyword / linked signal
- Equipment classification may be strong; **ineligible** for current queue because closed

### Case C — excluded

- Real raw text hit (`reactivo` / `insumo` / `arriendo` / `comodato` / `mantenimiento`)
- Negative relevance class blocks admission

### Case D — contact research path

- Real PR4 linked account with `contact_n=0` (or unsuitable)
- Demonstrates `contact_research_required`; **not live-eligible** while historical

### Case E — existing contact path

- Real PR4 linked account with PR2 contacts
- Role review + suppression read-only; outreach-review only if also active+relevant (currently historical → not live-eligible)

---

## 12. Contact / relevance funnel (actual counts)

| Stage | Count |
|-------|------:|
| Source observations (`external_leads_raw`) | (see `CURRENT_FUNNEL.json`) |
| Verified PR4 tenders | 16448 |
| Currently active (PR4 positive evidence) | **0** |
| Live open rows in equipment-first API artifact | **5** |
| Active strongly relevant (PR4) | **0** |
| Proposed outreach-review candidates (PR4-active) | **0** |
| Historical linked with PR2 contacts | (see funnel JSON) |

Do not inflate prospects. Zero means zero for the PR4-backed live funnel until PR5D acquisition lands.

---

## 13. Implementation roadmap

1. **PR5A** — this design/audit/walkthrough  
2. **PR5B** — deterministic planner + fixtures  
3. **PR5C** — persistence + gated apply  
4. **PR5D** — official live acquisition + scheduling  
5. **PR6** — targeted external contact enrichment + human review  
6. **PR7** — API/dashboard read exposure  

---

## 14. Safety boundary

- No production SQLite writes  
- No PR2/PR3/PR4 mutation  
- No Gmail/Postgres/dashboard/API data mutation  
- No outreach state changes  
- No send/draft  
- No ticket values in logs/commits  
- Reports remain under gitignored `reports/out/`
