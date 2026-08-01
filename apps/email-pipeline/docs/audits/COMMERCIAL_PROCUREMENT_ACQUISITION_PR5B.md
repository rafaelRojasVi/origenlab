# Commercial procurement acquisition — PR5B

**Status:** Merged (#421); OCDS wire semantics corrected in PR5B.1
**Branch (original):** `feat/commercial-procurement-acquisition-pr5b`
**Base (original):** `9a78aebd06a8065e249284ae54fabdd46ff8069c` (PR5A merge)
**Live contract follow-up:** [`COMMERCIAL_PROCUREMENT_REAL_CONTRACT_VALIDATION_PR5B1.md`](COMMERCIAL_PROCUREMENT_REAL_CONTRACT_VALIDATION_PR5B1.md)

This document does **not** authorize authenticated Mercado Público requests,
production `--apply`, PR5 candidate planning, relevance classification,
account/contact resolution, scheduling changes, or PR5C.

## 1. Objective

Establish a versioned, source-neutral **acquisition snapshot contract** and
deterministic parsers for:

| Lane | Endpoint kind | Role |
|------|---------------|------|
| A | `ticket_licitaciones_summary` | Active discovery (`estado=activas`) |
| B | `ticket_licitacion_detail` | Code detail + items |
| C (page) | `ocds_lista_agno_mes_range` | OCDS monthly **page** (≤1000 **limit**) |
| C (month) | `ocds_lista_agno_mes_month` | OCDS **month-scope** identity (null range) |

Bulk official downloads remain the PR4 historical/backfill lane.

## 2. Existing operational flow (unchanged)

```
ticket API → summary keyword prefilter → bounded detail lookups / detail cache
→ equipment-first classifier → CSV / manifest publication
→ typed equipment opportunity publication
```

Auto-refresh is **not** routed through PR5B in this PR.

## 3. Package highlights

- **AcquisitionSnapshot** — one sanitized query scope with typed
  `source_reported_total: int | None` (not derived from page[0], diagnostics,
  page count, or observation counts)
- **AcquisitionRun** / **PartialDetailRunResult** — composite run; failed attempts
  are pages/attempts only (`snapshot_id=null`), never fake snapshots
- Source-native vs source-neutral canonical tender candidates
- OCDS records **policy B**; `relatedProcesses` at **release** level
- Nested `records[].releases` / `compiledRelease` type validation with
  rejected-entry digests (no raw payloads)
- Live `listaOCDSAgnoMes` **index listing** envelope (`pagination` + `data[{ocid,…}]`)
  in addition to full release/record packages
- Ticket summary strict Listado validation + partial_page_failure retention
- Malformed pages retain `acquired_at_utc` as operational provenance
- Zero-record OCDS months (`total=0`, empty plan/pages, explicit year/month) →
  complete empty month via `build_ocds_month_query` with `source_reported_total=0`
- Planned-range continuity + child source/endpoint/range metadata checks
- `snapshot_manifest()` reads `snapshot.source_reported_total`

### OCDS range wire contract (PR5B.1)

Live measurement conclusion: **`zero_based_offset_limit`**.

| Layer | Semantics |
|-------|-----------|
| Planned span | inclusive `[start, end]` over **0-based** listing coordinates |
| Wire path | `/{year}/{month}/{offset}/{limit}` with `offset=start`, `limit=end-start+1` |
| Max page | `limit <= 1000` |
| Query identity | `OCDS_QUERY_CONTRACT_VERSION=acquisition_query_v2` + `ocds_range_semantics` |
| Ticket queries | remain `acquisition_query_v1` |

Do not reinterpret pre-correction OCDS range query IDs as offset/limit.

### `source_reported_total` population

| Snapshot | Value |
|----------|-------|
| Ticket summary | envelope `Cantidad` when a valid int |
| Ticket detail | source `Cantidad` when valid; else documented detail-result total `1` |
| OCDS single range | caller-/package-/pagination-supplied total only; otherwise `null` |
| OCDS month | assembler `source_reported_total` argument |
| Zero-record month | exactly `0` |

### Source fingerprint decision (`acquisition_source_fingerprint_v2`)

Authoritative snapshot-level `source_reported_total` **is included** in the
source fingerprint. A meaningful total change alters the fingerprint. Generated
observation counts are never substituted for the total.

## 4. Fixture origin vs completeness

`fixture_origin=synthetic_official_shape` is separate from content completeness.
Live-sanitized shapes live under
`tests/fixtures/commercial_procurement_acquisition_live_contract/`.

## 5. Walkthrough fingerprints

Regenerate after OCDS query v2:

```bash
uv run python scripts/commercial/build_commercial_procurement_acquisition_snapshot.py \
  --source-kind walkthrough \
  --out-dir reports/out/pr5b1_walkthrough_v2/
```

Gitignored: `reports/out/pr5b1_walkthrough_v2/`

| Case | Key | Digest |
|------|-----|--------|
| A | source | `ebb3aaa73471f9e3ecf9da022be313d634641899f7be2a13d286c55857ffc00b` |
| A | semantic | `038fd66b7dbd71af84b6283ab71489810dc1ae9e86d127f48114c2c27d55a010` |
| A | malformed_raw | `da4030d94d7e021763a73475c7e0f434f7ce361882886e3c02ca576b275047d5` |
| B | source | `9fd9cc3c632b3744e351d64380e75d6acd39deff26e3a11ce4844badd0af6579` |
| B | semantic | `9fc4a53a1308c240518da5b82fd5a93902889c91e1421447dd67d12981ffe0e9` |
| C | monthly_source | `5d7890bfebe57d65a9ff3173477c154257757c8eb1cdc5fb269e0c82c191638e` |
| C | monthly_semantic | `3568508d622b4f9048947c71191a33c77fd743bd50ba1fff4f774b82c374a7bf` |
| C | records_source | `6170c5064f6048d827ea3620697c25548fe4fe77f68d02bb71df677a1f18e816` |
| C | records_semantic | `a87f8e50e1e61ee5468ba75c6576d0005001b11959298f64b67eaaa92ab51304` |
| C | single_source | `cae2b483127e6d0edd82236b229b6800c8c5882dbfedc08342a068ffa39fd56e` |
| D | run_source | `692dd8f3343c373014f233df3eefe19d97b409a443502677ff422b2f3f9cde9a` |
| E | shared candidate | `9999-1-le26` (parser-equal; coalesced=false) |

Ticket semantic digests are unchanged vs merged PR5B (normalized evidence unchanged).
OCDS monthly/single **source** fingerprints moved with `acquisition_query_v2` +
0-based span identity; semantic digests unchanged.

Case C uses `ocds_lista_agno_mes_month` for the month snapshot; child pages remain
range queries. Months larger than 1000 records plan as `0–999` + `1000–1000`
(and similar) without violating page **limit**.

Case D shows `summary_snapshot`, `detail_success_snapshot`,
`failed_detail_attempt` + `failed_page`, and `AcquisitionRun` — no fake failed
snapshot.

## 6. Safety

- Default walkthrough/fixture paths: `authenticated_request_performed=false`
- Live PR5B.1 validation (separate authorization): see PR5B.1 audit
- No production SQLite / PR2 / PR3 / PR4 mutation from parsers
- No Gmail / Postgres / dashboard / outreach / scheduler changes
- Reports remain gitignored

## 7. PR5C boundary

PR5C may consume these snapshots for coalescence. PR5B emits evidence only.

## 8. CLI

```bash
uv run python scripts/commercial/build_commercial_procurement_acquisition_snapshot.py \
  --source-kind walkthrough \
  --out-dir reports/out/pr5b1_walkthrough_v2/
```
