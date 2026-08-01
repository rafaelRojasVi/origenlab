# Commercial procurement live candidate relevance — PR5A design & audit

**Status:** Corrected design / audit / dry-run planning only
**Branch:** `feat/commercial-procurement-live-relevance-pr5`
**PR:** [#420](https://github.com/rafaelRojasVi/origenlab/pull/420) (draft)
**PR4 gate:** `PR4_PERSISTENCE_VALIDATED_READY_FOR_SEPARATE_PR5_DIRECTION`

This document does **not** authorize persistence, production `--apply`, authenticated ChileCompra requests, contact hunting, or PR5B+.

---

## 1. Objective

Turn official, **current** procurement records into a small, explainable queue of:

1. active product-relevant tenders;
2. tenders requiring account resolution;
3. tenders requiring contact research;
4. tenders that are outreach-ready **only after** verified-contact review.

### Evidence planes

| Plane | Role |
|-------|------|
| **A — persisted PR4** | Historical/file-backed procurement evidence (`commercial_procurement_*`), stable `procurement_id`, existing account resolution |
| **B — live acquisition snapshot** | Versioned official API/OCDS observations with snapshot id + fingerprint; may be live-only |

PR5 candidates coalesce either or both planes. PR4 is **not** the sole evidence truth for live-only rows.

---

## 2. Open-tender terminology (corrected)

PR5A makes **no authenticated API call**, so:

| Classification | Meaning |
|----------------|---------|
| `live_verified_open` | Revalidated against a current authoritative API response at the audit instant (**count must be 0 in PR5A**) |
| `recent_artifact_declared_open` | Artifact row passes strict status/date/provenance/freshness checks |
| `stale_artifact_declared_open` | Would be declared-open but artifact older than documented threshold (48h) |
| `artifact_declared_open_unverified_provenance` | Status/date pass but provenance insufficient |
| `artifact_not_open` | Fails open checks (including close ≤ as_of) |
| `status_or_date_conflict` | Contradictory status/code/name signals |
| `date_unparseable` | Close date missing/unparseable |

Do **not** call artifact-only rows “genuine live active” or “current active tender.”

`recent_artifact_declared_open` requires all of:

1. `validity_status=open`
2. `chilecompra_status_code=5`
3. status name Publicada or absent without contradiction
4. close_date parses
5. naive ChileCompra datetimes interpreted as **America/Santiago**
6. `close_at` **strictly greater** than as_of
7. valid artifact provenance
8. freshness within 48h

Shared helper: `artifact_open.classify_artifact_row_open` / `pick_best_open_row`.

---

## 3. Production reality (read-only)

See regenerated report under:

`apps/email-pipeline/reports/out/active/current/commercial_procurement_live_relevance_pr5_<UTC>/`

Expected pattern at correction time:

- **PR4 active (positive evidence):** 0 (all historical)
- **live_verified_open:** 0
- **recent_artifact_declared_open:** may be nonzero if the newest operator-queue artifact passes strict checks
- **Current status independently revalidated:** false
- **Outreach-review candidates (PR4-active):** 0
- `chilecompra_api_ticket_configured`: boolean only

---

## 4. Candidate outcomes

| State | Meaning |
|-------|---------|
| `relevant_tender` | Current + relevant (contact optional) |
| `account_resolution_required` | Current + relevant; buyer/account not resolved (typical live-only Case A) |
| `contact_research_candidate` | Account clear; no verified suitable contact |
| `outreach_review_candidate` | Verified contact + suppression/outreach pass; human review still required |
| `not_eligible` | Fails active and/or relevance and/or safety |

Contact search **does not run** until the account is resolved.

Historical Cases D/E: final `candidate_outcome_state=not_eligible`; `hypothetical_contact_path` may explain what would happen if active.

---

## 5. Contact table grain

- `commercial_procurement_contact_resolution` — **exactly one** summary row per candidate (includes no-contact)
- `commercial_procurement_contact_candidate` — **zero or more** considered contacts

---

## 6. Exclusions vs conflicts

Routine negatives (`consumable_or_reagent`, `service_or_maintenance_only`, `rental_or_comodato`, `non_laboratory_false_positive`, `unrelated`) emit **relevance evidence** + `not_eligible_reason`.

Conflicts only for contradictory/unresolved evidence (status/date conflicts, strong equipment vs consumable unresolved, incompatible classes, ambiguous exact product aliases).

---

## 7. Active lifecycle vs urgency

- `active_status_class`: `active_open` | `future_scheduled` | `closed` | `awarded` | `cancelled` | `status_conflict` | `date_missing` | `status_unknown`
- `closing_soon_bucket`: `lt_24h` | `d1_to_d3` | `d4_to_d7` | `gt_7d` | `not_applicable`

No `active_closing_soon` lifecycle class.

---

## 8. Taxonomy

- Canonical: `ultrasonic_processor`, `ultrasonic_bath` (not `sonicator`)
- `sonicator` = source alias requiring contextual resolution
- `shaker`, `vortex_mixer`, `magnetic_stirrer` distinct from `homogenizer` (equipment-first homogenizer regex currently risks absorbing agitador/vortex)

Completeness checked by `validate_taxonomy_mapping_completeness`.

---

## 9. Acquisition lanes (docs only; no auth requests)

Composed strategy:

1. **Ticket Mercado Público API** — active discovery + code detail
2. **Official OCDS** — reconciliation / durable snapshots (docs cite ≤1000 records/request)
3. **Bulk official downloads** — historical backfill

Rate limits: **not found in official documentation** (do not invent).

---

## 10. Cases A–E

| Case | Source | Final outcome |
|------|--------|---------------|
| A | Strict recent artifact-declared open + relevant; live-only vs PR4 | `account_resolution_required` |
| B | PR4 historical equipment | `not_eligible` |
| C | Real exclusion keyword | `not_eligible` (+ evidence, not conflict) |
| D | PR4 linked, no suitable contact | `not_eligible` (+ hypothetical contact path) |
| E | PR4 linked with contacts | `not_eligible` (+ hypothetical outreach-review path) |

Production audit selects B–E inside a pinned RO SQLite transaction (not `--seeds-json`). Fixture seeds only via `--fixture-seeds-json`.

---

## 11. Implementation sequence

1. **PR5A** — corrected design/audit (this PR)
2. **PR5B** — acquisition source contract, ticket/OCDS parsers + captured fixtures
3. **PR5C** — deterministic candidate planner
4. **PR5D** — additive persistence + gated apply
5. **PR5E** — production acquisition scheduling/refresh
6. **PR6** — targeted external contact enrichment
7. **PR7** — API/dashboard exposure

---

## 12. Safety

- No production SQLite writes
- No PR2/PR3/PR4 mutation
- No authenticated ChileCompra requests
- No ticket values in logs/commits
- No Gmail/Postgres/dashboard/outreach mutation
- Reports remain gitignored under `reports/out/`
