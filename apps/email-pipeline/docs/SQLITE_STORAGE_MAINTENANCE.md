# SQLite storage maintenance (observation first)

Status: canonical (operator safety)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-07-15

Related: [`SCRIPT_MAP.md`](SCRIPT_MAP.md) · [`pipeline/DAILY_CORE.md`](pipeline/DAILY_CORE.md) · [`RUNBOOK.md`](RUNBOOK.md) · [`CRUD_SAFETY.md`](CRUD_SAFETY.md)

**This document describes observation and a future controlled maintenance path. The current OrigenLab change set implements observation only.**

---

## 1. What freelist pages mean

SQLite keeps deleted pages on an internal **freelist**. Those pages remain inside the database file so later writes can reuse them without growing the file. Freelist space is therefore **allocated file bytes that are not currently holding live row data**.

## 2. Why ~66.5 GiB freelist is reusable space, not corruption

A freelist on the order of tens of GiB can accumulate after large rebuilds, feature backfills, or historical churn. It is **not** evidence of corruption. It is reusable capacity **inside** the existing file. Do not confuse freelist size with “lost data” or a requirement to compact.

## 3. Why the live database should not be VACUUMed now

`VACUUM` rewrites the database and can require approximately as much temporary free disk as the live file size (standard VACUUM), while also taking exclusive access and interrupting operator workloads. With a ~127.5 GiB file and roughly 208 GiB free disk, a live VACUUM is an unnecessary production risk relative to the benefit of reclaiming freelist space that SQLite can already reuse.

**Never** run `VACUUM`, `VACUUM INTO`, `incremental_vacuum`, `REINDEX`, `ANALYZE`, `dbstat` scans against production, `wal_checkpoint`, or schema/page-size changes without an explicit offline plan.

## 4. Standard VACUUM headroom risk

Classic `VACUUM` builds a rewritten copy. Operators should assume they need **free headroom on the order of the database size** (plus safety margin) before attempting compaction on any copy or cutover candidate. Insufficient headroom is a hard stop.

## 5. `auto_vacuum` limits on an existing database

`PRAGMA auto_vacuum` mainly affects how freelist/pages are managed for databases created with that mode. Changing `auto_vacuum` on a large existing OrigenLab database does **not** magically reclaim tens of GiB and is not an approved hot-path fix.

## 6. Prefer Online Backup API for future snapshots

For future maintenance snapshots, prefer SQLite’s **Online Backup API** (or an approved filesystem-consistent cold copy under downtime) over ad-hoc copies while writers are active. Observation tooling in this repo never creates such backups by itself.

## 7. `VACUUM INTO` only against approved destinations

If compaction is ever approved, prefer `VACUUM INTO` (or equivalent rewrite) **only** into an approved destination on separate storage with verified capacity. Never rewrite the live path in place as the first experiment.

## 8. Proposed future controlled procedure (not implemented here)

1. Observe storage trends for **14–30 days** via daily aggregate telemetry.
2. Obtain **separate** storage with ample headroom.
3. Create a **verified** backup/snapshot (Online Backup API or controlled downtime copy).
4. Run heavy diagnostics (`dbstat`, count audits) **only against a copy**.
5. Compact the **copy**, not production.
6. Validate schema, row counts, and Sent/history audits on the compacted copy.
7. Schedule controlled downtime and an **atomic swap** only after validation.
8. Keep an explicit **rollback** plan (retain prior file until post-cutover confidence).

## 9. Observation-only statement

The telemetry module, `audit_sqlite_storage.py --storage-only`, daily-core evidence files, and `/operator/automation-status` `sqlite_storage` section are **read-only observation**. They:

- open SQLite with URI `mode=ro` and `PRAGMA query_only=ON`
- never mutate SQLite/Postgres/Gmail
- never recommend automatic VACUUM
- treat a high freelist ratio as **informational**, not a global fault by itself

Safe read-only smoke:

```bash
cd apps/email-pipeline
uv run python scripts/qa/audit_sqlite_storage.py --storage-only --json
```

Do **not** pass `--include-dbstat` against production without explicit approval.
