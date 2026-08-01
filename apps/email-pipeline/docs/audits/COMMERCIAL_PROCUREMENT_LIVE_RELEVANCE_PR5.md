# Commercial procurement live candidate relevance — PR5A design & audit

**Status:** Final safety/semantic hardening — design / audit / dry-run planning only
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
| `recent_artifact_declared_open` | Artifact row passes strict status/date/**trusted provenance**/freshness checks |
| `stale_artifact_declared_open` | Would be declared-open but trusted acquisition age exceeds documented threshold (48h) |
| `artifact_declared_open_unverified_provenance` | Status/date pass but provenance insufficient (including **mtime-only**) |
| `artifact_not_open` | Fails open checks (including close ≤ as_of) |
| `status_or_date_conflict` | Contradictory status/code/name signals |
| `date_unparseable` | Close date missing/unparseable |

Do **not** call artifact-only rows “genuine live active” or “current active tender.”

### Trusted freshness (not mtime)

`effective_artifact_timestamp_utc` precedence:

1. `api_checked_at_utc`
2. `queried_at_utc`
3. `generated_at_utc`
4. `published_at_utc`
5. other documented source-acquisition timestamps
6. filename date (reduced precision/confidence)
7. **mtime only as unverified fallback** — **cannot** qualify `recent_artifact_declared_open`

Trusted timestamps must be timezone-aware UTC. Future / malformed / publication-order / filename disagreement / excessive generated-vs-mtime delta / stale manifest → downgrade.

The **48-hour** window is an **analytical** threshold for “recent artifact-declared open.” It is **not** proof of current API status.

`source_query_metadata` is allowlisted (`estado`, `fecha`, `source_kind`, `row_count`, `endpoint_kind` / normalized `endpoint_path`). Tickets, tokens, and secret query params are recursively redacted.

---

## 3. Production reality (read-only)

See regenerated report under:

`apps/email-pipeline/reports/out/active/current/commercial_procurement_live_relevance_pr5_<UTC>/`

Expected pattern:

- **PR4 active (`procurement_context=tender_active`):** 0 (all historical)
- **live_verified_open:** 0
- **recent_artifact_declared_open:** may be nonzero if trusted acquisition timestamp + strict checks pass
- **Current status independently revalidated:** false
- **Case D:** real zero-contact linked account **or** explicitly `unavailable`
- All report JSON/Markdown: **identifier-redacted** only (`redacted_selection_ids`)
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

### Contact resolution statuses

| Status | Meaning |
|--------|---------|
| `contact_resolution_deferred` | Prerequisite not satisfied (e.g. account unresolved); **search not run** |
| `no_contact_found` | All allowed sources searched; no suitable contact |
| `contact_research_required` | Internal search exhausted; external/human research is next |

Case A emits `final_contact_status=contact_resolution_deferred`, empty `search_stages_completed`, `next_action=resolve_account`. Do **not** use `no_contact_found` when no search ran.

Historical Cases D/E: final `candidate_outcome_state=not_eligible`; `hypothetical_contact_path` may explain what would happen if active.

---

## 5. Contact table grain

- `commercial_procurement_contact_resolution` — **exactly one** summary row per candidate (includes deferred / no-contact)
- `commercial_procurement_contact_candidate` — **zero or more** considered contacts

---

## 6. Exclusions vs conflicts

Routine negatives emit **relevance evidence** + `not_eligible_reason` (not conflicts).

---

## 7. Active lifecycle vs urgency

- `active_status_class`: `active_open` | `future_scheduled` | `closed` | `awarded` | `cancelled` | `status_conflict` | `date_missing` | `status_unknown`
- `closing_soon_bucket`: `lt_24h` | `d1_to_d3` | `d4_to_d7` | `gt_7d` | `not_applicable`

PR4 active funnel count uses persisted `procurement_context=tender_active` (not date-only lexical close comparison).

---

## 8. Taxonomy

- Canonical: `ultrasonic_processor`, `ultrasonic_bath` (not `sonicator`)
- `sonicator` = `context_required` alias with candidate classes + disambiguation rules
- equipment-first homogenizer regex hits = `context_required` across homogenizer / shaker / vortex_mixer / magnetic_stirrer
- Exact aliases map to exactly one canonical; ambiguity is machine-readable in `CONTEXT_REQUIRED_ALIASES`

---

## 9. Acquisition lanes (docs only; no auth requests)

1. Ticket Mercado Público API — active discovery + code detail
2. Official OCDS — reconciliation / durable snapshots
3. Bulk official downloads — historical backfill

Rate limits: **not found in official documentation**.

---

## 10. Cases A–E

| Case | Source | Final outcome |
|------|--------|---------------|
| A | Strict recent artifact-declared open + relevant; live-only | `account_resolution_required` + deferred contact |
| B | PR4 historical equipment | `not_eligible` |
| C | Real exclusion keyword | `not_eligible` (+ evidence) |
| D | PR4 linked, **exactly zero** PR2 contacts (or unavailable) | `not_eligible` (+ hypothetical) |
| E | PR4 linked with contacts | `not_eligible` (+ hypothetical) |

Production B–E selected inside pinned RO SQLite. Fixtures only via `--fixture-seeds-json`.

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
- No raw production identifiers in report artifacts
- No Gmail/Postgres/dashboard/outreach mutation
- Reports remain gitignored under `reports/out/`
