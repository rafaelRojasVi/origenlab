# COMMERCIAL_PROCUREMENT_LIVE_FEED_BRIDGE_PR5B2

**Status:** implemented (read-only review bridge)  
**Planner version:** `procurement_live_feed_bridge_pr5b2_v1`  
**Contract:** `live_feed_bridge_contract_v1`

## Sequence

```text
PR5B / PR5B.1 acquisition contract
↓
PR #323 ChileCompra equipment feed (detail cache)
↓
PR5B.2 live-feed bridge (this document)
↓
PR5C → PR5D → PR5E review packet
↓
(next) human review / calibration — not PR5F/PR5G
```

## Source contract and integration seam

**Selected seam:** `reports/out/active/current/chilecompra_detail_cache/*.json`

These files are the Ticket **detail** envelopes already fetched by
`auto-refresh-chilecompra-equipment` (PR #323). They retain tender identity,
title/description, Comprador, Fechas, CodigoEstado/Estado, and `Items.Listado`
line items.

**Why not CSV / Postgres / queue rows:** those are downstream projections that
coalesce/join lines and drop Correlativo-level fidelity. They are not
authoritative PR5B evidence.

**Why no second acquisition system:** the bridge calls existing
`build_acquisition_snapshot(source_kind="ticket_detail", …)` with offline
cache payloads. Forbidden CLI flags include `--network` / `--ticket`.

**Parser note:** Ticket detail envelopes place `FechaPublicacion` under
`Fechas`. PR5B.2 aligns `_extract_publication_date` with the existing
`Fechas.FechaCierre` close-date pattern so publication provenance is not
silently dropped.

## Completeness limitations

- Cache = keyword prefilter ∩ `max_details` (typically 50), **not** complete
  ChileCompra `activas` coverage.
- Summary-list AcquisitionSnapshots are **not** reconstructed offline.
- `acquired_at_utc` is stamped from the equipment manifest /
  refresh-state generation time when available (`acquired_at_unavailable`
  otherwise). Original HTTP bytes are not re-fetched.
- Feed freshness fail-closed: `BLOCKED_STALE_LIVE_FEED` when age at
  `--as-of-utc` exceeds `--max-feed-age-hours` (default 36) unless
  `--allow-stale-feed`.

## Field mapping (summary)

| PR5B / review field | Source |
|---------------------|--------|
| Tender code | `Listado[0].CodigoExterno` |
| Title / description | `Nombre`/`Titulo`, `Descripcion` |
| Buyer | `Comprador.NombreOrganismo`, `CodigoOrganismo`/`RutUnidad` |
| Publication / close | `FechaPublicacion|Fechas.*`, `FechaCierre|Fechas.*` |
| Status | `CodigoEstado`, `Estado` |
| Lines | `Items.Listado[]` |
| Mercado Público URL | `build_mercado_publico_search_url(codigo)` |
| Acquisition stamp | manifest / refresh-state UTC |

Every cache file is **accepted exactly once** or appears in
`bridge_source_reconciliation.json` with a stable reason code.

## Commercial buckets

Evaluation order: `rejected` → `historical_market_signal` →
`current_opportunity` → `needs_review`.

| Bucket | Meaning |
|--------|---------|
| `current_opportunity` | live-backed + `active_open` + current authoritative currentness + strong relevance |
| `needs_review` | live-backed uncertainty (ambiguous, stale, conflicts, lab-context-only, …) |
| `historical_market_signal` | product-fit but closed/awarded/cancelled |
| `rejected` | negative relevance (maintenance, rental/comodato, consumable, false-positive, unrelated) |

Unknown relevance enums fail closed to `needs_review`.

Separations enforced in every row:

- product fit ≠ current opportunity ≠ actionable lead ≠ outreach authorization  
- `actionable_lead` and `outreach_authorization` remain **false** in this PR

## Operator command

```bash
cd apps/email-pipeline
uv run python scripts/commercial/build_live_commercial_procurement_review.py \
  --sqlite-path "$ORIGENLAB_SQLITE_PATH" \
  --detail-cache-dir reports/out/active/current/chilecompra_detail_cache \
  --equipment-manifest reports/out/active/current/equipment_first_operator_queue_chilecompra_api_YYYYMMDD.manifest.json \
  --refresh-state reports/out/active/current/chilecompra_equipment_auto_refresh_state.json \
  --as-of-utc 2026-08-06T01:00:00Z \
  --out-dir reports/out/active/current/commercial_procurement_live_feed_review_pr5b2_<UTC> \
  --run-context production_dry_run \
  --json-summary
```

Artifacts (gitignored under `reports/out`):

- `summary.json` (aggregate-only)
- `operator_review_packet.json`
- `operator_review.csv`
- `walkthrough.md`
- `bridge_source_reconciliation.json`

## Explicit next boundary

Human review / calibration of current candidates. **Not** PR5F persistence,
PR5G adjudication, dashboard/API exposure, or outreach send.

## Production-derived read-only run (2026-08-06)

Inputs:

- `chilecompra_detail_cache/` (253 JSON files)
- `equipment_first_operator_queue_chilecompra_api_20260806.manifest.json`
- `chilecompra_equipment_auto_refresh_state.json` (`last_successful_refresh_at=2026-08-06T00:14:32Z`)
- production SQLite read-only (`mode=ro` via planners)
- `--as-of-utc 2026-08-06T02:00:00Z`

Results (aggregate):

| Metric | Value |
|--------|-------|
| Accepted detail snapshots | 253 (0 rejected) |
| Line observations | 2446 |
| PR5C `live_snapshot` | 193 |
| PR5C `both` | 0 |
| PR5C `pr4` (historical plane) | 16448 |
| Live-backed review population | 193 |
| `current_opportunity` | 6 |
| `needs_review` | 160 |
| `historical_market_signal` | 14 |
| `rejected` | 13 |
| Actionable leads | 0 |
| Contact / outreach authorization | 0 / 0 |
| Feed age at as_of | ~1.76h (fresh) |

Note: 253 accepted Ticket-detail snapshots coalesce/resolve to **193**
live-backed PR5C tenders (remaining acquisition observations are unresolved or
non-candidate under existing PR5C rules — not silent review drops). Review rows
cover exactly the live-backed population.

Artifact directory (gitignored):

`apps/email-pipeline/reports/out/active/current/commercial_procurement_live_feed_review_pr5b2_20260806T021500Z/`

## Related: independent PR5D prospect-quality review (diagnostic)

A separate, aggregate-only audit of an external **title-only** independent PR5D
review (200 diagnostic-stratified records; `reviewed_not_gold`; sealed
predictions not accessed) is recorded in:

[`COMMERCIAL_PROCUREMENT_PR5D_INDEPENDENT_PROSPECT_QUALITY_REVIEW_2026-08-05.md`](COMMERCIAL_PROCUREMENT_PR5D_INDEPENDENT_PROSPECT_QUALITY_REVIEW_2026-08-05.md)

That packet answers a different question than this live-feed bridge. Do not
treat its four clear title matches as the same population as this document’s
six `current_opportunity` rows unless tender identity is explicitly joined.
PR #434 does not validate classifier accuracy; a separate calibration +
representative holdout remain required before precision/recall/F1 claims.
