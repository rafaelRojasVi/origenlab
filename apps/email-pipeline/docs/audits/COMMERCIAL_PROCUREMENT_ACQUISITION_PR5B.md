# Commercial procurement acquisition — PR5B

**Status:** Design + parsers + fixtures (draft)  
**Branch:** `feat/commercial-procurement-acquisition-pr5b`  
**Base:** `9a78aebd06a8065e249284ae54fabdd46ff8069c` (PR5A merge)

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
| C | `ocds_lista_agno_mes_range` | Official OCDS monthly range (≤1000/page) |

Bulk official downloads remain the PR4 historical/backfill lane.

## 2. Existing operational flow (unchanged)

```
ticket API
    ↓
summary keyword prefilter
    ↓
bounded detail lookups / detail cache
    ↓
equipment-first classifier
    ↓
CSV / manifest publication
    ↓
typed equipment opportunity publication
```

That flow is a **consumer** of source data. It is **not** the PR5B acquisition
snapshot contract. Auto-refresh is **not** routed through PR5B in this PR.

Compatibility adapter maps acquisition tender/line observations → existing
`CHILECOMPRA_NORMALIZED_FIELDS` without equipment classification.

## 3. Package

`src/origenlab_email_pipeline/commercial_procurement_acquisition/`

Contract versions:

- `commercial_procurement_acquisition_v1`
- `procurement_acquisition_parser_v1`
- fingerprint algorithms: `acquisition_source_fingerprint_v1`,
  `acquisition_normalized_semantic_digest_v1`
- raw digest: `sha256_canonical_json_v1`

Grains: Acquisition run → Response page → Source observation → Tender /
Line observation → Snapshot.

Cross-source coalescence is **deferred to PR5C**.

## 4. Fixture origin

All committed fixtures under
`tests/fixtures/commercial_procurement_acquisition/` are
`fixture_origin=synthetic_official_shape`.

They follow documented Mercado Público / OCDS field contracts and the observed
`Cantidad` / `Listado` / `Items.Listado` envelope shape. They are **not**
production-derived. No ticket is present.

Offline detail-cache JSON under `reports/out/` was inspected for shape only and
was not committed.

## 5. Data walkthrough (Cases A–E)

Generated (gitignored) report example:

`reports/out/active/current/commercial_procurement_acquisition_pr5b_<UTC>/`

### Case A — Ticket summary

| Stage | Source → result |
|-------|-----------------|
| Envelope | `Listado` → 2 source/tender observations, 0 lines |
| Query | sanitized `estado=activas` (no ticket) |
| Fingerprints | source + semantic (see table below) |

### Case B — Ticket detail

Summary vs detail provenance remains distinct (`ticket_licitacion_detail`).
Two stable line IDs after item shuffle. Compatibility with
`normalize_licitacion_detail_items` verified for codigo/title/close/line fields.

### Case C — OCDS package

Label: `synthetic_official_shape`. Retains `ocid` / release id / `tender.id` /
items. `tender.status` stored as `source_status_system=ocds` — **not** PR5
active eligibility.

### Case D — Partial detail failure

Summary ok + one detail ok + one detail failed →
`completeness_status=partial_detail_failure` (never false-complete).

### Case E — Cross-source keys

Synthetic pair sharing tender code `9999-1-LE26`. Ticket key
`ticket:…` vs OCDS key `ocds-tender:…`. **No coalescence** in PR5B.

### Fingerprint values (walkthrough fixtures)

| Case | source_fingerprint | normalized_semantic_digest | counts (src/tender/line) |
|------|--------------------|----------------------------|---------------------------|
| A | `3558bd644e84307457265317e6717af81cba3a088696395a16517953c4d7b3ed` | `681d6c47b933d5fd04434c9eb1a9377ac76abf436bd951ba38769b54cf9011ad` | 2/2/0 |
| B | `67c6f33a640b2163603b49e5974137064b6ad49a8519bb9da54b40aa171e9432` | `a320fb7aa019e65aff3bc734a5d811aa74f6d1ad0b32faff4451820bbd4d34ac` | 1/1/2 |
| C | `ea9fe239da18348a1a9500e701d8aa96b0ad7092192e1925391c3bbde51860c8` | `fd652d7b8b7baa3fa260815f947c7db8eb89758f398c73cf3048180e3fdbc8c3` | 1/1/1 |
| D | `1ca285fb9f607d5ac2e1d68d48c84c1314b7be138aa6eee5345d684067657097` | `10d73decc294023e08c2d7bf43a23c8e2f6a7b24f48bfe26b87ab447bc58707c` | 3/3/2 |

## 6. Safety

- No authenticated request (`authenticated_request_performed=false`)
- Ticket: boolean configured only; never read into parser path; never hashed
- CLI rejects `--network` / `--apply` / `--ticket`
- No production SQLite mutation; no PR2/PR3/PR4 mutation
- No Gmail / Postgres / dashboard / outreach / scheduler changes
- Reports remain gitignored

## 7. PR5C boundary

PR5C may consume these snapshots for deterministic candidate planning and
cross-source coalescence. PR5B does **not** emit relevance, account, contact,
or outreach outcomes.

## 8. CLI

```bash
uv run python scripts/commercial/build_commercial_procurement_acquisition_snapshot.py \
  --source-kind walkthrough \
  --out-dir reports/out/active/current/commercial_procurement_acquisition_pr5b_<UTC>/
```
