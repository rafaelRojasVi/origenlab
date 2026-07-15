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

SQLite documents that a **standard** `VACUUM` may require temporary free disk
space **up to twice the original database-file size** while rewriting the file,
and it takes exclusive access that interrupts operator workloads.

For the current production baseline of **127.4941 GiB**, that conservative
temporary-space figure is about **254.99 GiB** *before* any additional safety
margin. Observed free space around **207.25 GiB** is **below** that bound, so a
live VACUUM is unsafe even before considering runtime disruption. Freelist
space remains reusable inside the file without compaction.

**Never** run `VACUUM`, `VACUUM INTO`, `incremental_vacuum`, `REINDEX`, `ANALYZE`,
`dbstat` scans against production, `wal_checkpoint`, or schema/page-size changes
without an explicit offline plan. Live VACUUM is hard-prohibited under current
capacity.

## 4. Standard VACUUM headroom vs `VACUUM INTO` destination capacity

Classic (standard) `VACUUM` rewrites in place and may need **up to ~2× the live
database file size** as temporary free space on the same volume (see §3). That
temporary headroom requirement is distinct from `VACUUM INTO`, which writes a
compacted result to an **approved destination** on (usually) separate storage
and therefore needs destination capacity sized for the compacted output plus
safety margin—not the same “twice the live file as scratch space on the live
volume” rule.

Insufficient standard-VACUUM temporary headroom is a hard stop. Do not confuse
that with destination sizing for a future `VACUUM INTO` of a verified copy.

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
