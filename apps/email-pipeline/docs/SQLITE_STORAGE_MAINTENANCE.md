# SQLite storage maintenance (observation first)

Status: canonical (operator safety)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-07-17

Related: [`SCRIPT_MAP.md`](SCRIPT_MAP.md) · [`SQLITE_BODY_STORAGE_ASSESSMENT.md`](SQLITE_BODY_STORAGE_ASSESSMENT.md) · [`pipeline/DAILY_CORE.md`](pipeline/DAILY_CORE.md) · [`RUNBOOK.md`](RUNBOOK.md) · [`CRUD_SAFETY.md`](CRUD_SAFETY.md)

**Observation tooling and offline candidate compaction tooling are implemented. Production cutover, clone deletion, and body-column drops remain prohibited without explicit approval.**

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
- Cheap destination verification only (`mode=ro&immutable=1` header + page/freelist/schema inventory; no sidecar creation; journal format from header bytes 18/19)
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

### Connection / immutable policy

Online Backup API snapshots can retain a **WAL-format database header** (bytes 18/19 = 2) while having **no** `-wal`/`-shm` companions. Opening such a file with ordinary `mode=ro` can create sidecars and fail the frozen fingerprint check.

| Case | Open URI |
| --- | --- |
| Confirmed offline copy (`--confirm-offline-copy`), not production, **no** `-wal`/`-shm`/`-journal` | `mode=ro&immutable=1` |
| Production DB / `--light-only` / unconfirmed path | ordinary `mode=ro` only — **never** immutable |
| Offline never inferred from filename alone | require `--confirm-offline-copy` + non-production gate |

Immutable is appropriate only for a **frozen, verified offline copy**. It must **never** be used against live production. A **non-empty WAL** blocks offline immutable auditing because immutable mode would ignore committed WAL content. This tool **never deletes** sidecars.

| Phase | Purpose | Production path |
| --- | --- | --- |
| `structural_light` | Constant-time storage PRAGMAs + `sqlite_master` inventory only | **`--light-only` on production** |
| `structural_quick` | `quick_check`, `foreign_key_check`, table COUNT/ID ranges | refused (offline copy + confirm) |
| `structural_full` | opt-in `integrity_check` via `--full-integrity-check` only (may take hours) | refused |
| `physical_dbstat` | page allocation by table/index/autoindex + reconciliation | refused |
| `column_bytes` | aggregate TEXT/BLOB bytes (`length(CAST(col AS BLOB))`) | refused |
| `duplicate_analysis` | SHA-256 body fingerprints for duplicate `message_id` groups; attachment external-payload dupes | refused |
| `usefulness_classification` | source tiers (rows + body **lengths**), discovered reference tables, review candidates — **bounded id-range streaming / scalar aggregates; never loads bodies into Python** | refused |

**Defaults:** heavy offline phases run when `--confirm-offline-copy` is set; `structural_full` is **not** in the default phase set. Use `--full-integrity-check` explicitly.

### Usefulness OOM incident and bounded-memory correction

A resume of `usefulness_classification` against a ~127.5 GiB offline snapshot was **OOM-killed** (~28 GiB Python RSS on a 30 GiB WSL VM) while earlier phases remained completed in the v2 checkpoint. Evidence showed ~60 GiB of body content and only ~0.058 GiB max single body — the failure was **cumulative query/temp growth**, not one giant value.

**Root cause (fixed):** the usefulness phase used a nested `SELECT … body_bytes FROM emails` subquery before `GROUP BY tier`, which can materialize large intermediate results, and it also re-ran body-fingerprint duplicate analysis (fetching body payloads) solely for a COUNT of duplicate extras.

**Correction:** usefulness now:

- streams source-tier rows as `tier` plus six scalar `length(CAST(col AS BLOB))` integers, with **no `GROUP BY`** and no `fetchall()` on body-length work
- uses **id-range batches** (default 5,000; override with `--usefulness-batch-size`) and checkpoints after each completed ID batch
- stores resumable batch state: current substep/cohort, next ID, integer accumulators, completed batch count, and batch configuration
- reuses source-tier byte totals for canonical/legacy cohorts, derives total body bytes from tier totals, scans referenced body bytes once, and derives historical-unreferenced as total-minus-referenced
- records privacy-safe EXPLAIN QUERY PLAN evidence that body-byte query shapes do not use a temporary GROUP BY sorter
- checkpoints privacy-safe **substeps** (`source_tiers` → `references` → `cohort_bytes` → `orphans_and_tables` → `finalize`) without marking the top-level phase `completed` until the end
- requests connection-local `temp_store=FILE` + bounded `cache_size`, then reports actual PRAGMA values and SQLite `TEMP_STORE` compile option

Those PRAGMAs are **not** a hard RSS bound. The remediation is the sorter-free query shape plus batch-granular resume; memory/swap increases only mask regressions.

**Increasing WSL memory, swap, or systemd MemoryMax is not remediation.** Resume the same v2 checkpoint with the fixed code; do not discard completed phases.

Privacy-safe progress lines (stderr) may include substep name, elapsed seconds, batch size, completed batch counts, rows processed in the last batch, and RSS — never sender/subject/body/paths/raw identifiers.

For long offline jobs, prefer a **system-level transient service** running as `rafael`, with a cgroup ceiling so any regression kills only the job, not WSL/Cursor. Do **not** use `systemd-run --user` as the primary command while linger is `no`. Nested `bash -lc` quoting previously produced a blank exit marker even when Python succeeded — always wrap through the checked-in exit-marker helper:

`scripts/maintenance/run_sqlite_maintenance_with_exit_marker.sh`

Example audit resume template (documentation only; do **not** launch without operator approval):

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/mnt/d/origenlab-sqlite-offline/deep_audit_20260716T023339Z
UNIT="origenlab-sqlite-deep-audit-resume-${STAMP}"
PROGRESS="${OUT}/resume_${STAMP}.progress.log"
MARKER="${OUT}/resume_${STAMP}.exit"
REPORT="${OUT}/resume_${STAMP}.report.json"

sudo systemd-run --unit="${UNIT}" \
  --uid=rafael \
  --property=WorkingDirectory=/home/rafael/dev/freelance/origenlab/apps/email-pipeline \
  --property=Environment=HOME=/home/rafael \
  --property=MemoryHigh=4G \
  --property=MemoryMax=6G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  /home/rafael/dev/freelance/origenlab/apps/email-pipeline/scripts/maintenance/run_sqlite_maintenance_with_exit_marker.sh \
    --progress-log "${PROGRESS}" \
    --exit-marker "${MARKER}" \
    -- \
    /bin/bash -c "set -o noclobber; /home/rafael/.local/bin/uv run --frozen python scripts/qa/audit_sqlite_deep.py \
      --db /mnt/d/origenlab-sqlite-offline/emails_offline_20260716T023339Z.sqlite \
      --confirm-offline-copy \
      --output-dir ${OUT} \
      --usefulness-batch-size 5000 \
      --resume --json > ${REPORT}"
```

The wrapper always appends both `SQLITE_MAINTENANCE_EXIT=<integer>` and the compatibility alias `AUDIT_RESUME_EXIT=<integer>` (including `0`) and writes the same integer to the exit-marker file. It intentionally does **not** use `set -e` around the wrapped command so ordinary failures still produce a marker. Disconnecting WSL/Cursor must not terminate a **system-level** transient unit; user-session units without linger are unsafe for multi-hour jobs.

### Offline compaction (`VACUUM INTO` candidate)

Tooling creates a **new compacted candidate** from a verified offline snapshot. It never runs in-place `VACUUM`, never swaps into production, and never deletes the source snapshot.

A compact candidate is **not** a production cutover. Any future swap requires a **separate recovery drill** and **explicit human approval**. Stale-clone deletion and body-column/schema redesign are **out of scope** for this operation.

**Same-filesystem note:** hard-link no-clobber publication requires the `.partial` and final destination names to share one filesystem. Destination vs source defaults to **refuse same filesystem**; `--allow-same-filesystem` is allowed only with `--confirm-offline-copy` on a verified non-production offline snapshot (synthetic tests / emergency). There is **no** silent fallback to clobber-capable rename/copy when hard links are unsupported (for example some 9p/`/mnt/d` layouts) — the job refuses before the expensive `VACUUM INTO`.

**Preflight (default — zero writes):**

```bash
cd /home/rafael/dev/freelance/origenlab/apps/email-pipeline
/home/rafael/.local/bin/uv run --frozen python scripts/maintenance/compact_sqlite_offline.py \
  --source /mnt/d/origenlab-sqlite-offline/emails_offline_20260716T023339Z.sqlite \
  --destination /mnt/d/origenlab-sqlite-offline/emails_compact_YYYYMMDDTHHMMSSZ.sqlite \
  --confirm-offline-copy \
  --json
```

**Apply (writes candidate only; still not production cutover):**

```bash
cd /home/rafael/dev/freelance/origenlab/apps/email-pipeline
/home/rafael/.local/bin/uv run --frozen python scripts/maintenance/compact_sqlite_offline.py \
  --source /mnt/d/origenlab-sqlite-offline/emails_offline_20260716T023339Z.sqlite \
  --destination /mnt/d/origenlab-sqlite-offline/emails_compact_YYYYMMDDTHHMMSSZ.sqlite \
  --confirm-offline-copy \
  --apply \
  --json
```

**System-level transient unit template (documentation only — do not launch without explicit approval):**

Runtime may be **several hours**; that is not a hard estimate. Do not compute a full ~127.5 GiB content hash. Use a **unique** unit name, progress log, exit marker, report, and destination basename every run. Keep `set -o noclobber` around report redirection.

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OFFLINE=/mnt/d/origenlab-sqlite-offline
SOURCE="${OFFLINE}/emails_offline_20260716T023339Z.sqlite"
DEST="${OFFLINE}/emails_compact_${STAMP}.sqlite"
UNIT="origenlab-sqlite-offline-compact-${STAMP}"
PROGRESS="${OFFLINE}/compact_${STAMP}.progress.log"
MARKER="${OFFLINE}/compact_${STAMP}.exit"
REPORT="${OFFLINE}/compact_${STAMP}.report.json"
EP=/home/rafael/dev/freelance/origenlab/apps/email-pipeline

# Refuse if destination or report already exist (operator safety).
test ! -e "${DEST}"
test ! -e "${REPORT}"

sudo systemd-run --unit="${UNIT}" \
  --uid=rafael \
  --property=WorkingDirectory="${EP}" \
  --property=Environment=HOME=/home/rafael \
  --property=MemoryHigh=4G \
  --property=MemoryMax=6G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  "${EP}/scripts/maintenance/run_sqlite_maintenance_with_exit_marker.sh" \
    --progress-log "${PROGRESS}" \
    --exit-marker "${MARKER}" \
    -- \
    /bin/bash -c "set -o noclobber; /home/rafael/.local/bin/uv run --frozen python scripts/maintenance/compact_sqlite_offline.py \
      --source ${SOURCE} \
      --destination ${DEST} \
      --confirm-offline-copy \
      --apply \
      --json > ${REPORT}"
```

Safety model:

- Requires `--confirm-offline-copy`; writes additionally require `--apply`
- Refuses configured production SQLite (path / samefile / device+inode)
- Source must have no `-wal`/`-shm`/`-journal`; opened `mode=ro&immutable=1`
- Destination / partial / final must never equal the source
- Destination must be new; unique `.partial.<pid>.<ns>` + hard-link no-clobber publish + completed `.compaction.manifest.json` as the completion marker
- If the DB is published but the manifest cannot be published, leave a detectable orphan and refuse subsequent runs until explicit cleanup
- Capacity check covers expected compacted output plus a conservative safety margin; malformed/unavailable FS stats fail closed
- Source fingerprint (size/mtime/device/inode + sidecar absence) frozen before work and verified after; drift refuses success
- Candidate verification: `quick_check`, bounded `foreign_key_check`, stable schema fingerprint (type/name/tbl_name/normalized SQL; ignores root-page layout noise), `user_version` / `application_id` / encoding / page_size, exact critical table counts (including `emails`), freelist reduced vs source (exact zero freelist not required), zero destination sidecars
- Estimated reclaim ≈ source allocated − candidate size (freelist-dominated on the audited snapshot: ~127.5 GiB → ~61 GiB active)
- Cleanup removes only this run’s script-owned partials/companions; never deletes a pre-existing DB, final manifest, source, or clone
- Unsupported directory fsync is recorded as a durability warning, not silent success

**Later operational procedure (do not run in this PR):**

1. Merge compaction tooling.
2. Launch against the `/mnt/d` offline snapshot under a unique system-level transient unit (template above).
3. Verify the candidate and compare ~127.5 GiB source vs ~61 GiB active output.
4. **Stop for explicit approval** before any recovery drill or production cutover.
5. Body-column redesign (~28.43 GiB within-row redundancy) and stale-clone deletion remain separate workstreams — see [`SQLITE_BODY_STORAGE_ASSESSMENT.md`](SQLITE_BODY_STORAGE_ASSESSMENT.md).

### Application recovery-readiness (immutable RO; not cutover)

Ordinary API opens use `mode=ro` only. That is **not** safe for fingerprint-frozen offline candidates with a WAL-format header and no live sidecars — SQLite may create `-wal`/`-shm`.

**Threat model:** `immutable=1` tells SQLite the file cannot change and may ignore locking/WAL updates. Enabling immutable against live production can return stale or inconsistent data. Therefore:

- Default API behavior remains ordinary `mode=ro`.
- `ORIGENLAB_SQLITE_IMMUTABLE_RO=1` alone is **never** enough.
- Recovery mode also requires:
  - `ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY=1`
  - `ORIGENLAB_SQLITE_COMPACTION_MANIFEST=<completed manifest>`
  - `ORIGENLAB_SQLITE_PATH=<candidate>` (non-production; aliases/samefile/device+inode refused)
  - `ORIGENLAB_API_BACKEND=sqlite` with `ORIGENLAB_POSTGRES_URL` unset
  - candidate regular file with **no** `-wal`/`-shm`/`-journal` (including zero-byte)
  - manifest `completed=true`, basename/size agreement, and successful verification fields
- Failed admission **fails API startup** (no silent fallback to ordinary/production mode).
- **Candidate vs production are separate concepts:** `ORIGENLAB_SQLITE_PATH` names the
  offline candidate; production exclusion uses a dedicated canonical resolver
  (`~/data/origenlab-email/sqlite/emails.sqlite` or `ORIGENLAB_DATA_ROOT/...`) that
  **never** treats the candidate override as production and **never** mutates
  `os.environ` (no temporary unset / restore).
- **Dotenv isolation:** when both recovery flags are present in the *process* environment
  (or `ORIGENLAB_DISABLE_DOTENV=1`), the API and email-pipeline settings loaders **do not**
  read project `.env` files. This prevents `WorkingDirectory=apps/api` from injecting
  secrets via pydantic-settings / python-dotenv.
  - Changing `WorkingDirectory` alone is **insufficient** (cwd `.env` and
    `apps/email-pipeline/.env` are still discoverable by default loaders).
  - Clearing only `ORIGENLAB_POSTGRES_URL` is **insufficient** (Gmail OAuth paths, API
    tokens, and other mutation-related keys can still enter via dotenv).
  - Process-global environment mutation is **prohibited** (concurrent settings callers
    must not observe a temporarily modified environment).
  - Unsafe values that are **explicitly** present in the process environment are still
    refused by admission (Postgres, Gmail/OAuth, tokens, CF Access secrets, etc.).
  - `ORIGENLAB_DISABLE_DOTENV=1` disables dotenv only; it does **not** grant recovery
    admission by itself.
  - Health exposes sanitized `dotenv_disabled=true` as an informational indicator only
    (never paths or secrets; not proof of admission).

In-process harness (synthetic or verified candidate; after merge):

```bash
cd /home/rafael/dev/freelance/origenlab/apps/api
/home/rafael/.local/bin/uv run --frozen python scripts/recovery_sqlite_readiness.py \
  --db /mnt/d/origenlab-sqlite-offline/emails_compact_YYYYMMDDTHHMMSSZ.sqlite \
  --manifest /mnt/d/origenlab-sqlite-offline/emails_compact_YYYYMMDDTHHMMSSZ.sqlite.compaction.manifest.json \
  --confirm-offline-copy \
  --json
```

Isolated HTTP drill on `127.0.0.1:8002` (documentation only — unique system-level transient unit; never replace production `:8001`):

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
UNIT="origenlab-api-recovery-readiness-${STAMP}"
CAND=/mnt/d/origenlab-sqlite-offline/emails_compact_YYYYMMDDTHHMMSSZ.sqlite
MANIFEST=${CAND}.compaction.manifest.json
EP=/home/rafael/dev/freelance/origenlab/apps/api
LOG=/mnt/d/origenlab-sqlite-offline/recovery_${STAMP}.log

# Explicit environment only — do not inherit production EnvironmentFile.
# Code-level dotenv isolation activates from the recovery flags below; also set
# ORIGENLAB_DISABLE_DOTENV=1 as defense in depth. Do not pass Postgres/Gmail/token vars.
# Do not mutate os.environ inside the app to “fix” production exclusion.
sudo systemd-run --unit="${UNIT}" \
  --uid=rafael \
  --property=WorkingDirectory="${EP}" \
  --property=Environment=HOME=/home/rafael \
  --property=Environment=ORIGENLAB_API_BACKEND=sqlite \
  --property=Environment=ORIGENLAB_SQLITE_PATH=${CAND} \
  --property=Environment=ORIGENLAB_SQLITE_IMMUTABLE_RO=1 \
  --property=Environment=ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY=1 \
  --property=Environment=ORIGENLAB_SQLITE_COMPACTION_MANIFEST=${MANIFEST} \
  --property=Environment=ORIGENLAB_DISABLE_DOTENV=1 \
  --property=MemoryMax=1G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=StandardOutput=append:${LOG} \
  --property=StandardError=append:${LOG} \
  /home/rafael/.local/bin/uv run --frozen uvicorn origenlab_api.main:app \
    --host 127.0.0.1 --port 8002
```

After the drill: stop only the recovery unit; confirm `:8001` was never restarted; confirm candidate fingerprint and zero sidecars before/after; treat the candidate as a **point-in-time** recovery experiment that **cannot** be swapped into production without a separate writable restore rehearsal and explicit approval.

**Writable restore rehearsal + cutover design (docs + synthetic tooling only):** see [`SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md`](SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md). Do not treat the July 2026 compact candidate as cutover-eligible while production continues to change.

**Still prohibited:** live production heavy audit, `VACUUM` / `VACUUM INTO` against production, deleting offline clones, mutating Gmail/Postgres/cron/systemd, or treating usefulness counts as deletion approval.

**Production-safe light mode:** `--light-only` runs `structural_light` only (no `quick_check`, FK checks, COUNT scans, dbstat, or body profiling). Safe on the configured production path without `--confirm-offline-copy` (ordinary `mode=ro`).

Heavy phases require `--confirm-offline-copy`. Outputs are sanitized JSON + Markdown with phase timings and **estimate-only** SQLite body-byte conclusions. Attachment `size_bytes` duplication is reported as **external payload only**, never as SQLite file savings. Conclusions use tri-state values (`yes` / `no` / `not_assessed`); SQLite integrity is `not_assessed` unless `structural_full` completed. Age or lack of references never classifies rows as deletable.

Example (synthetic or verified copy only):

```bash
cd apps/email-pipeline
uv run python scripts/qa/audit_sqlite_deep.py \
  --db /path/to/verified/emails_copy.sqlite \
  --confirm-offline-copy \
  --output-dir reports/out/active/current/sqlite_deep_audit
```

Use `--light-only` on production for constant-time storage metadata. Use `--full-integrity-check` only when a full `integrity_check` is explicitly required. Use `--resume` to continue from `audit_sqlite_deep_checkpoint.json` (refuses resume when schema version, file fingerprint, selected configuration, or in-progress usefulness batch size differs).
