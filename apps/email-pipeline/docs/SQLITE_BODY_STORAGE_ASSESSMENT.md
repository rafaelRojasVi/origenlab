# SQLite body-column storage assessment

**Status:** documentation / inventory only. No schema change, column deletion, or compaction cutover is authorized by this document.

**Related:** [`SQLITE_STORAGE_MAINTENANCE.md`](SQLITE_STORAGE_MAINTENANCE.md) · deep audit within-row redundancy (~28.4264 GiB on the 2026-07-16 offline snapshot).

## Scope

Inventory of consumers for these `emails` columns:

- `body`
- `body_html`
- `body_text_raw`
- `body_text_clean`
- `full_body_clean`
- `top_reply_clean`

## Derivation chain

Live ingest converges on `insert_email()` (`src/origenlab_email_pipeline/db.py`):

| Layer | Source | Columns |
|-------|--------|---------|
| Legacy MIME pair | `parse_mbox.body_content()` | `body`, `body_html` |
| Phase 2.1 | `extract_body_structured()` | `body_text_raw`, `body_text_clean` |
| Phase 2.2 | `normalize_full_body()` / `extract_top_reply()` | `full_body_clean`, `top_reply_clean` |

Writers: mbox/IMAP ingest scripts and `gmail_imap.py`. Phase 2.2 backfill may UPDATE `full_body_clean` / `top_reply_clean` where empty. Postgres archive migrate copies all six.

## Per-column roles

| Column | Role | Authoritative / derived | External need | Redundancy note |
|--------|------|-------------------------|---------------|-----------------|
| `body` | Legacy plain text | Authoritative for older scanners | JSONL export, business filters, ML, some NDR fallbacks | Often overlaps Phase 2 text |
| `body_html` | Raw HTML MIME | Unique fidelity; not derived from text columns | Archive / JSONL / Postgres parity | Analytics-cold but **not** drop-safe without MIME retention |
| `body_text_raw` | Phase 2.1 raw text | Derived from MIME | Low runtime fan-out; backfill input | Highest redundancy vs `body` |
| `body_text_clean` | Phase 2.1 preferred readable text | Derived; preferred for search | Equipment LIKE, NDR primary, UI coalesce | Often equals raw/full on plain mail |
| `full_body_clean` | Normalized full text | Derived from Phase 2.1 | Mart fallback, Tatiana hybrid, NDR preference | Often equals `body_text_clean` |
| `top_reply_clean` | Reply/signature-trimmed | Derived from `full_body_clean` (lossy) | Primary mart / commercial / UI preview | Equal to full when no cut; unique when shorter |

## Consumer summary

- **Mart / commercial:** prefer `top_reply_clean`, fall back to `full_body_clean`.
- **Search / NDR / post-send:** prefer cleaned text; still coalesce through `body`.
- **Export / migrate:** all six for fidelity.
- **Deep audit:** sizes all six; reports within-row exact-duplicate bytes.

## Engineering opportunity (not approval)

The offline deep audit reported **~28.4264 GiB** exact within-row redundant body bytes. That figure is an **upper-bound engineering opportunity**, not:

- guaranteed reclaimable disk after NULLing columns,
- approval to delete columns or historical rows,
- or a substitute for offline freelist compaction (`VACUUM INTO` candidate tooling).

Freelist reclaim (~66.5 GiB on the audited snapshot) is a separate, safer first step via offline compaction tooling and does not require body redesign.

## Suggested later sequence (observation only)

1. Merge offline compaction tooling and run against the verified `/mnt/d` snapshot.
2. Verify the compact candidate (~61 GiB active expected) without production swap.
3. Only after explicit approval: plan body-column consumer migration, dual-write, and offline measurement.
4. Never gate outbound safety or treat age/lack of references as deletion approval.
