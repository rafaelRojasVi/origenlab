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

For maintenance snapshots, prefer SQLite’s **Online Backup API** over ad-hoc copies while writers are active.

Operator tool (does **not** write unless `--apply` is passed):

**Preflight (default — zero writes):**

```bash
cd apps/email-pipeline
uv run python scripts/maintenance/backup_sqlite_online.py \
  --source /path/to/emails.sqlite \
  --destination /mnt/d/origenlab-sqlite-offline/emails_offline_YYYYMMDDTHHMMSSZ.sqlite \
  --json
```

**Execute backup (requires `--apply`):**

```bash
cd apps/email-pipeline
uv run python scripts/maintenance/backup_sqlite_online.py \
  --source /path/to/emails.sqlite \
  --destination /mnt/d/origenlab-sqlite-offline/emails_offline_YYYYMMDDTHHMMSSZ.sqlite \
  --apply
```

Planned operator destination directory (create only when ready to back up; this doc does not create it):

`/mnt/d/origenlab-sqlite-offline`

Behavior of `backup_sqlite_online.py`:

- Default is **preflight only**: `stat` + read-only SQLite **header parse** (no `sqlite3.connect()` on the live source). Reports page size/count from the header; freelist/schema/table metadata are `not_assessed_until_apply`. No lock directory, no destination artifacts, and no source WAL/SHM creation from preflight.
- `--apply` required to create lock, `.partial`, backup, or completed manifest
- Uses `sqlite3.Connection.backup()` only — **never** plain `cp` / `rsync` of a live WAL database
- Opens source with URI `mode=ro` only under `--apply`
- Requires explicit `--source` and `--destination`; destination must not already exist
- Writes via script-owned `.partial` (+ companions cleaned on failure); file fsync mandatory
- **Publication** is same-filesystem hard-link no-clobber (`os.link(partial, final)` → `unlink(partial)`); fails safely on `EEXIST` so neither final DB nor final manifest can overwrite a path created after preflight. Equivalent for the manifest. If the destination FS cannot hard-link, `--apply` fails **before** the long copy rather than weakening overwrite protection.
- Completion = final DB **and** completed final `.manifest.json` (manifest hard-link publish is the completion marker)
- Crash window: final DB without final completed manifest is an **orphan** — next run refuses overwrite; cleanup must be explicit (never silently treated as completed). Partial artifacts from a failed run are cleaned; a pre-existing final left by a race is left for operator review (orphan policy).
- Directory open/fsync may be unsupported on some mounts (e.g. `/mnt/d` 9p / DrvFS); recorded as a durability warning (including post-publish warnings surfaced on the return value / stderr). File fsync remains mandatory.
- Refuses source/destination aliases (`samefile` / resolve)
- By default refuses same-filesystem destinations (`--allow-same-filesystem` for tests/emergencies only)
- Checks destination free space (source size + conservative margin) before starting
- Default `--pages-per-batch 4096` (positive/configurable); time-based progress + guaranteed final 100%
- Concurrent backup lock (lock FD closed on every flock `OSError`); SIGINT/SIGTERM abort never publishes an incomplete completed pair
- Writes a sanitized JSON `.manifest.json` (basenames only; no mailbox content / absolute paths)
- Cheap destination verification only (header, `query_only`, page/freelist/schema inventory)
- Does **not** run `integrity_check`, `dbstat`, duplicate analysis, `VACUUM`, or the deep audit

**Old same-volume clones** under `~/data/origenlab-email/sqlite/` (including `backups/`) must **not** be deleted until a **current** Online Backup API copy on separate storage has completed and passed the deep forensic audit.

**Proposed post-merge synthetic smoke (not this change):** after merge, run a tiny temporary SQLite through `--apply` onto a throwaway path under `/mnt/d` only if the operator creates the parent directory — never against production.

## 7. `VACUUM INTO` only against approved destinations

If compaction is ever approved, prefer `VACUUM INTO` (or equivalent rewrite) **only** into an approved destination on separate storage with verified capacity. Never rewrite the live path in place as the first experiment.

**Explicit prohibitions:** live plain-file copying of the active WAL database (`cp`/`rsync`) and live `VACUUM` / `VACUUM INTO` against production.

## 8. Proposed future controlled procedure (not implemented as automatic)

1. Observe storage trends for **14–30 days** via daily aggregate telemetry.
2. Obtain **separate** storage with ample headroom (planned: `/mnt/d/origenlab-sqlite-offline`).
3. Create a **verified** backup/snapshot with `backup_sqlite_online.py --apply` (Online Backup API; preflight first without `--apply`).
4. Run heavy diagnostics (`dbstat`, deep audit) **only against that copy** with `--confirm-offline-copy`.
5. Compact the **copy**, not production.
6. Validate schema, row counts, and Sent/history audits on the compacted copy.
7. Schedule controlled downtime and an **atomic swap** only after validation.
8. Keep an explicit **rollback** plan (retain prior file until post-cutover confidence). Only then consider reclaiming stale same-volume clones.

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

## 10. Deep forensic audit (offline copy only)

`scripts/qa/audit_sqlite_deep.py` performs a privacy-safe, resumable deep audit for **verified offline/backup copies** on separate storage. It never runs against the configured production SQLite path for heavy phases, even with `--confirm-offline-copy`.

| Phase | Purpose | Production path |
|-------|---------|-----------------|
| `structural_light` | Constant-time storage PRAGMAs + `sqlite_master` inventory only | **`--light-only` on production** |
| `structural_quick` | `quick_check`, `foreign_key_check`, table COUNT/ID ranges | refused (offline copy + confirm) |
| `structural_full` | opt-in `integrity_check` via `--full-integrity-check` only (may take hours) | refused |
| `physical_dbstat` | page allocation by table/index/autoindex + reconciliation | refused |
| `column_bytes` | aggregate TEXT/BLOB bytes (`length(CAST(col AS BLOB))`) | refused |
| `duplicate_analysis` | SHA-256 body fingerprints for duplicate `message_id` groups; attachment external-payload dupes | refused |
| `usefulness_classification` | source tiers (rows + body bytes), discovered reference tables, review candidates | refused |

**Defaults:** heavy offline phases run when `--confirm-offline-copy` is set; `structural_full` is **not** in the default phase set. Use `--full-integrity-check` explicitly.

**Production-safe light mode:** `--light-only` runs `structural_light` only (no `quick_check`, FK checks, COUNT scans, dbstat, or body profiling). Safe on the configured production path without `--confirm-offline-copy`.

Heavy phases require `--confirm-offline-copy`. Outputs are sanitized JSON + Markdown with phase timings and **estimate-only** SQLite body-byte conclusions. Attachment `size_bytes` duplication is reported as **external payload only**, never as SQLite file savings. Conclusions use tri-state values (`yes` / `no` / `not_assessed`); SQLite integrity is `not_assessed` unless `structural_full` completed. Age or lack of references never classifies rows as deletable.

Example (synthetic or verified copy only):

```bash
cd apps/email-pipeline
uv run python scripts/qa/audit_sqlite_deep.py \
  --db /path/to/verified/emails_copy.sqlite \
  --confirm-offline-copy \
  --output-dir reports/out/active/current/sqlite_deep_audit
```

Use `--light-only` on production for constant-time storage metadata. Use `--full-integrity-check` only when a full `integrity_check` is explicitly required. Use `--resume` to continue from `audit_sqlite_deep_checkpoint.json` (refuses resume when schema version, file fingerprint, or selected configuration differs).
