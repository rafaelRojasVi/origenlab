# Commercial procurement — PR5B.1 real source-contract validation

**Status:** Draft PR checkpoint (validation + OCDS wire correction)
**Branch:** `test/commercial-procurement-real-contract-pr5b1`
**Base SHA:** `94a7668a6eb060f85fa579ff109616bb8ac5adf4` (PR #421 merge)
**Capture UTC:** `2026-08-01T19:00:30Z` (day `2026-08-01`)

This document records **source-contract** findings only. It does **not** classify
commercial relevance, produce operator candidates, authorize bulk acquisition,
or start PR5C.

## Authorization consumed

| Lane | Budget | Attempted | Completed |
|------|--------|-----------|-----------|
| Authenticated Ticket API | 4 | 4 | 3 |
| Public OCDS probes | 4 | 4 | 4 |

- Ticket only from `CHILECOMPRA_API_TICKET` (never CLI / config / output).
- Failed requests count toward budget (3rd detail → HTTP 429).
- No automatic retries.
- Raw captures gitignored under
  `reports/out/active/current/commercial_procurement_real_contract_validation_20260801T190030Z/`.

## Answers to the PR5B.1 questions

| # | Question | Finding |
|---|----------|---------|
| 1 | Ticket summary vs committed contract | **Matched** envelope (`Cantidad`, `FechaCreacion`, `Version`, `Listado` list). Parser complete; `source_reported_total=4357`. |
| 2 | Ticket detail vs committed contract | **Matched** for 2/3 selected codes (items present). 3rd detail unavailable (`HTTP 429`) → budget exhausted, no substitute code. |
| 3 | Envelope / field types | Ticket top-level: int/str/list as modeled. OCDS live listing is **not** a release package — see Case C. |
| 4 | OCDS zero- vs one-based | **Zero-based offset** (`pagination.offset` echoes path). |
| 5 | Final position inclusive/exclusive | Second path param is **limit (count)**, not an inclusive end index. |
| 6 | Advertised max 1000 | **`limit <= 1000`**, not `end-start` or `end-start+1` of the old 1-indexed model. |
| 7 | Ticket/OCDS tender-code candidates | **`real_cross_source_pair_not_observed`** in this bounded run (lista index returns OCID stubs only; no overlapping Ticket codes captured). |
| 8 | Parser fix required? | **Yes** — OCDS range planner/builder + lista-index envelope parser (versioned OCDS query contract). |
| 9 | Sanitized representation without weakening redaction? | **Yes** — committed fixtures under `tests/fixtures/commercial_procurement_acquisition_live_contract/`. |

## Contract versioning (smallest honest boundary)

| Constant | Value | Why |
|----------|-------|-----|
| `ACQUISITION_CONTRACT_VERSION` | `commercial_procurement_acquisition_v1` | Normalized observation fields unchanged → **semantic digests preserved**. |
| `QUERY_CONTRACT_VERSION` (Ticket) | `acquisition_query_v1` | Ticket query semantics unchanged. |
| `OCDS_QUERY_CONTRACT_VERSION` | `acquisition_query_v2` | Range wire meaning changed; do not reinterpret v1 OCDS range IDs. |
| `OCDS_RANGE_SEMANTICS` | `zero_based_offset_limit_v1` | Explicit field on range query identity. |
| Source fingerprint | `acquisition_source_fingerprint_v2` | Unchanged algorithm; OCDS fingerprints move with query identity. |
| Semantic digest | `acquisition_normalized_semantic_digest_v1` | Unchanged. |

## Raw response digests (SHA-256 of response bytes)

| Capture | Digest |
|---------|--------|
| ticket_summary | `63c45406e132205c234f678f31af062d0f3724fb571bc5cf878bcc2d5ea5107a` |
| live_detail_selection_001 | `26050cea60b7bb3a575efc6d247464fdf26daa3fcd49fa591146dbea40e76aad` |
| live_detail_selection_002 | `162c417c0b8258c6b16f5a6578d561edc6332b37d6ff20787892cc8c1c233f39` |
| live_detail_selection_003 | `f9dca0c4efe483155995b11bd8f97136d48f9d1906aa0ed45e582c42ac1dcdb7` (429 body) |
| ocds 0/0 | `16d0b2963f9c0ede10cc0b8f84643a37de89b51cfb459bafa54797f2ce18f585` |
| ocds 1/1 | `8869d2b3928a6e7471c6b47afa582378a7255e89100acda44cfc4e5708af9a21` |
| ocds 0/9 | `35f59c52563d1dccef363e82599aefff7c022d926383959a500f80703bd3a5a0` |
| ocds 1/10 | `d1c4b73a04ed373700c1acc5b247824f55945d114d5a97740c40c402e7171949` |

## Committed sanitized fixture digests

| Fixture | Digest |
|---------|--------|
| `ticket_summary_live_shape_v1` | `346792ab13a6d0b147204d41564b1f2698325591f114927f8f8a5d0ca6b7d5e7` |
| `ticket_detail_items_live_shape_v1` | `2274e41d4c975ac03b39cf5e99b2fe6cc916c3b94ce9ddcab6fe4ffa774bd814` |
| `ticket_detail_alternate_live_shape_v1` | `920c26ff5cd77b1aeb7e7da793b5c4942a2d2fc010190073935b19c060f00aba` |
| `ocds_range_live_shape_v1` | `3b8a56493e7c5edde332ae14608c7fccbe6ec5b1317c81ad17557f40018ec02d` |

---

## Case A — live Ticket summary

```
authenticated read-only request (estado=activas)
    ↓
raw bytes digest 63c45406… / canonical c22a0421…
    ↓
sanitized envelope (Cantidad/FechaCreacion/Version/Listado)
    ↓
PR5B parse_ticket_summary_payload
    ↓
source/tender observations (valid codes only)
    ↓
contract comparison → matched top-level types
```

| Stage | Redacted source value | Normalized value | Provenance | Result |
|-------|-----------------------|------------------|------------|--------|
| HTTP | `ticket_licitaciones_summary` | status 200 | request ledger ordinal 1 | ok |
| Envelope | `Cantidad` | `4357` | `source_reported_total` | matched |
| Envelope | `Listado` | list | summary Listado parser | matched |
| Selection pool | valid codes only | 3740 usable / 617 excluded from detail pool | identity helper | deterministic |
| Snapshot | fingerprint + semantic | PR5B models | snapshot builder | complete |

| Artifact/model | Fingerprint contribution | Contract finding |
|----------------|--------------------------|------------------|
| AcquisitionPage | raw + parser digests | complete |
| AcquisitionSnapshot | source_reported_total=4357 | matched Ticket contract |
| Detail selection | sorted normalized codes → tokens `live_detail_selection_00N` | reproducible from summary digest |

Terminal output never printed buyer names, tender codes, or bodies.

---

## Case B — live Ticket detail

```
redacted selection token (live_detail_selection_001/002)
    ↓
code-detail request (codigo in-memory only)
    ↓
detail envelope + Items
    ↓
line/item observations
    ↓
compatibility adapter
```

| Stage | Redacted source value | Normalized value | Provenance | Result |
|-------|-----------------------|------------------|------------|--------|
| Select | `live_detail_selection_001` | first normalized code | summary digest order | ok |
| HTTP 001/002 | detail endpoint | 200 | ledger 2–3 | ok |
| HTTP 003 | detail endpoint | 429 | ledger 4; budget consumed | `detail_validation` partial |
| Items | `Items.Listado` present | line observations | detail line parser | matched (both successes) |
| Attachments | metadata only | not followed | policy | no download |
| Adapter | compatibility row | existing ChileCompra row shape | adapter | ok |

| Artifact/model | Fingerprint contribution | Contract finding |
|----------------|--------------------------|------------------|
| Detail snapshots 001/002 | items-bearing shapes | committed as items + alternate fixtures |
| Detail 003 | error page only | no invented substitute code |

---

## Case C — live OCDS range semantics (2026/07)

Probes (isolated path builder; literal `/{offset}/{limit}`):

| Probe | HTTP | Package | Count | pagination | First/last token |
|-------|------|---------|-------|------------|------------------|
| `0/0` | 200 | `error_envelope` (body status 404) | 0 | n/a | — |
| `1/1` | 200 | `lista_index_pagination` | 1 | offset=1,limit=1,total=8004 | `ocds_obs_047407d74c78` |
| `0/9` | 200 | `lista_index_pagination` | 9 | offset=0,limit=9,total=8004 | `ocds_obs_0011d6a56a83` … `ocds_obs_f289a00a240e` |
| `1/10` | 200 | `lista_index_pagination` | 10 | offset=1,limit=10,total=8004 | `ocds_obs_0011d6a56a83` … `ocds_obs_8b2b9a1db280` |

Live envelope shape:

```json
{
  "creationDate": "…",
  "version": "1.2",
  "pagination": { "offset": 0, "limit": 9, "total": 8004 },
  "data": [{ "ocid": "…", "urlTender": "…", "urlAward": "…" }]
}
```

**Conclusion:** `zero_based_offset_limit`
**PR5B correction required:** yes

Evidence:

- path params echo `pagination.offset` / `pagination.limit`;
- returned `data.length` equals path limit (not inclusive end index);
- `0/0` is empty/error (limit 0), not “position zero inclusive”;
- advertised max 1000 = max **limit**.

Merged PR5B assumption (`start>=1`, inclusive `[start,end]`, width=`end-start+1` as path end) was **wrong for the live listing API**.

Corrected planner stores inclusive span coordinates over a 0-based listing and emits `/{offset}/{limit}` with `limit = end - start + 1`. Months larger than 1000 plan as `0–999` + `1000–1000` (and similar).

`urlTender` / `urlAward` were **not** followed.

---

## Case D — sanitizer proof

```
raw source digest
    ↓
live_contract_sanitizer_v1 (stable synthetic codes/OCIDs/buyers; URLs redacted)
    ↓
committed fixture digest
    ↓
identifier leak assertions (tests + FIXTURE_ORIGIN)
```

| Stage | Redacted source value | Normalized value | Provenance | Result |
|-------|-----------------------|------------------|------------|--------|
| Origin | `origin=live_response_sanitized` | day `2026-08-01` | FIXTURE_ORIGIN | ok |
| Shape | field names/types/nesting | preserved | sanitizer | ok |
| Identity | real codes/OCIDs/buyers | synthetic | sanitizer | removed |
| URLs / contacts | live URLs/emails/phones/RUTs | redacted/absent | sanitizer | removed |
| Raw tree | gitignored `raw/` | untracked | git status proof | ok |

---

## Case E — Ticket/OCDS canonical candidate comparison

**Live:** `real_cross_source_pair_not_observed`

Lista-index rows expose OCID stubs (and optional suffix → Ticket-shaped candidate when `ocds-70d2nz-` prefix is present) but this bounded capture did not retain a verified overlapping Ticket `CodigoExterno` for a production-derived pair.

**Synthetic (not live evidence):** existing PR5B walkthrough Case E still demonstrates parser-equal candidates with `coalesced=false` on fixtures — labelled synthetic only.

---

## Safety / no-mutation proof

From `RUN_MANIFEST.json`:

- `authenticated_request_authorized=true`
- `authenticated_request_budget=4` / attempted `4` / completed `3`
- `public_request_budget=4` / attempted `4` / completed `4`
- `ticket_used_for_request=true` (Ticket calls only)
- `ticket_persisted=false` / `ticket_logged=false` / `ticket_hashed=false`
- `credential_url_persisted=false`
- `attachment_downloaded=false`
- `production_apply=false` / `production_sqlite_mutation=false`
- `postgres_mutation=false` / `gmail_mutation=false` / `dashboard_mutation=false`
- `outreach_mutation=false` / `scheduler_changed=false` / `pr5c_started=false`

Production SQLite was not opened. No Gmail / Postgres / API / dashboard / outreach / scheduler mutation.

## PR5C boundary

PR5B.1 stops at source-contract validation (+ OCDS wire correction).
No relevance classification, candidate planning, coalescence, account/contact resolution, or outreach.

## CLI

```bash
# Plan only (no network):
uv run python scripts/commercial/validate_live_procurement_source_contracts.py \
  --out-dir reports/out/active/current/commercial_procurement_real_contract_validation_PLAN/

# Live (requires both flags; ticket from env only):
uv run python scripts/commercial/validate_live_procurement_source_contracts.py \
  --execute-live-contract-validation \
  --confirm-read-only-contract-validation \
  --ticket-summary-limit-details 3 \
  --authenticated-request-budget 4 \
  --public-request-budget 4 \
  --timeout-seconds 30 \
  --run-context pr5b1_live_contract \
  --out-dir reports/out/active/current/commercial_procurement_real_contract_validation_<UTC>/
```
