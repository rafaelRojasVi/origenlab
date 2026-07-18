# SQLite writable restore rehearsal and zero-data-loss cutover plan

**Status:** design + synthetic rehearsal tooling only.  
**Does not authorize:** pausing automation, opening production read-write for cutover, creating a real ~61 GiB writable copy, swapping production, deleting clones, or in-place `VACUUM` / `VACUUM INTO` against production.

**Validated context (as of 2026-07-18):**

| Item | Value |
|------|--------|
| Main SHA (post recovery dotenv merge) | `80bcde522478a7b24c5b2d6093a928708efd1f67` |
| Compact candidate | `/mnt/d/.../emails_compact_20260717T183537Z.sqlite` (~60.890 GiB) |
| Production allocated | ~127.494 GiB |
| Experiment reclaim | ~66.604 GiB / 52.24% |
| Read-only recovery drill | PASS — `recovery_drill_20260718T174826Z.evidence.json` |
| Candidate cutover-eligible? | **No** — production continued changing after the source snapshot |
| Root free (`/`) | ~207 GiB |
| `/mnt/d` free | ~242–243 GiB |

Related: [`SQLITE_STORAGE_MAINTENANCE.md`](SQLITE_STORAGE_MAINTENANCE.md), recovery-readiness section, offline compaction tooling.

---

## 1. Writer / reader inventory (production SQLite)

Canonical DB: `ORIGENLAB_SQLITE_PATH` or `~/data/origenlab-email/sqlite/emails.sqlite` (WAL).

Exact pause-file paths (from code; relative to `Settings.resolved_reports_dir()`, default `apps/email-pipeline/reports/out`):

| Pause file | Absolute-relative path |
|------------|------------------------|
| Mail auto-refresh | `{reports_dir}/active/current/auto_refresh_paused` |
| Dashboard auto-mirror | `{reports_dir}/active/current/dashboard_auto_mirror_paused` |

Constants: `mail_auto_refresh.PAUSE_FILENAME`, `dashboard_auto_mirror.PAUSE_FILENAME`.

### Always-on / scheduled

| Path | SQLite R | SQLite W | External | Pause | Resume | Missed work |
|------|----------|----------|----------|-------|--------|-------------|
| Cron `auto-refresh-mail --once --apply` → `daily-core` | Y | **Y** (ingest, mart, commercial, …) | Gmail IMAP **R** | `touch {reports_dir}/active/current/auto_refresh_paused` | `rm` that pause file | **Mostly replayable** (Message-ID skip + rebuildable marts) |
| Cron `auto-mirror-dashboard --once --apply` | Y | N | Postgres **W** | `touch {reports_dir}/active/current/dashboard_auto_mirror_paused` (also blocked by mail pause) | `rm` | **Replayable** (re-publish) |
| Cron ChileCompra equipment refresh | N | N | ChileCompra + Postgres | Comment cron / omit `--apply` | Restore cron | Partial (re-fetch) |
| `origenlab-api.service` (sqlite backend) | Y | **N** (`mode=ro` + `query_only`) | Optional PG | **Mandatory stop before filesystem swap** (see §2/§5) | `systemctl --user start` after smoke | N/A |
| API health timer | Indirect | N | curl | **Stop with API** during swap window | start with API | N/A |

### Primary write chain (`daily-core` / `refresh-dashboard --apply`)

| Step | SQLite W | Notes |
|------|----------|-------|
| `gmail-ingest` (`05_workspace_gmail_imap_to_sqlite`) | **Y** | Incremental Message-ID |
| `build-email-mart-features --missing-only --apply` | **Y** | Rebuildable |
| `build-mart --rebuild` | **Y** | Rebuildable |
| `build-commercial-intel` | **Y** | Mostly incremental |
| `refresh-safety` | Reports (sidecar-ish) | Re-exportable |
| Status / NDR review / digests | R | — |

### Manual / break-glass writers (must be idle during cutover)

Schema apply, purges, dedupe, NDR auto-apply, outreach/suppression imports, commercial deal apply, lead research imports, attachment extracts, legacy mbox/IMAP ingest, etc. Pause method: **do not run**; for scheduled mail use pause file. Cutover preflight must prove no writer PIDs / locks remain (pause files alone are insufficient).

### What cannot reconstruct SQLite from Gmail/Postgres alone

- `email_mart_features`, mart tables, commercial_* tables  
- outreach/suppression/operator sidecar state  
- attachment extracts / research / catalog tables  
- Any in-flight uncommitted WAL frames  

Therefore **online backup while writers run cannot claim RPO=0** unless every post-backup writer delta is captured and replayed — the repo has **no** dual-write change log for that purpose.

---

## 2. Consistency options and recommendation

### Option A — Full maintenance window (recommended for RPO=0)

Writers stay stopped through backup, compaction, verification, staging, swap, and smoke.

1. Pause **all** SQLite writers (`auto_refresh_paused` + `dashboard_auto_mirror_paused`; no manual `--apply`).  
2. Confirm quiet: no writer PIDs, locks gone, WAL not growing.  
3. **Mandatory:** stop `origenlab-api.service` and its health timer before any filesystem rename/swap.  
4. Checkpoint/quiet WAL so committed frames are not left only in `-wal` while the main file moves (see WAL notes below).  
5. Build a **current** compact candidate using the **proven Online Backup API → offline compact** path (not `VACUUM INTO` against production):
   - Online Backup API snapshot onto `/mnt/d` (writers already paused → RPO=0 snapshot).  
   - Compact that verified snapshot **directly into a new no-clobber staging file on production ext4 (`/`)**.  
6. Verify staged candidate on `/` (quick_check, FK, schema, counts, zero sidecars, manifest).  
7. Perform cutover only via a **tested cutover tool** (see swap semantics) — not ad-hoc dual `mv`.  
8. API recovery/read-only smoke, then gradual writer resume.

| Metric | Estimate |
|--------|----------|
| RPO | **0** (all writers stopped before Online Backup snapshot through smoke) |
| RTO / downtime | Dominated by backup+compact (~2–3+ h historically) + verify + smoke; plan **4–6 h** window with margin |
| Writers stop | Before backup begins; remain stopped through smoke |
| API/health stop | Before filesystem swap (mandatory) |
| Writers resume | After swap + RO smoke + explicit operator approval |
| Mail during window | Remains on Gmail server; next ingest after resume catches up by Message-ID |

#### WAL / Online Backup notes

- SQLite Online Backup API copies **committed** database pages as of the backup run. With writers paused and WAL quiet/checkpointed, committed state is included; uncommitted transactions are not.  
- **Never** rename only `emails.sqlite` while committed frames remain only in `emails.sqlite-wal`. Checkpoint or otherwise ensure the main DB file + companions are handled as a consistent set by the cutover tool.  
- Pause files are necessary but **not sufficient** until in-flight writer PIDs and locks are gone.

### Option B — Online backup then offline compact (writers stay up)

Creates a point-in-time clone; **post-backup SQLite writes are a delta**. Gmail can replay *mail rows* but **not** all mart/commercial/sidecar state. **Cannot claim RPO=0**.

### Option C — Direct `VACUUM INTO` from production

**Not recommended.** Conflicts with the standing prohibition on production `VACUUM` / `VACUUM INTO`, and is unnecessary while the Online Backup API path is proven.

### Option D — Incremental / dual-write shadow migration

High engineering cost. Not worthwhile for a **one-time freelist compaction**.

### Recommendation

**Option A**, Online Backup (paused writers) → compact into staging on `/` → verified swap tool. Reject Option B for zero-loss. Do not use Option C. Defer Option D.

---

## 3. Disk capacity (corrected topology)

Conservative compaction destination requirement ≈ source + margin ≈ **133.9 GiB** for a ~127.5 GiB snapshot (tooling `required_capacity_bytes`).

| Layout | Free | Fits? |
|--------|------|-------|
| Keep **both** new ~127 GiB snapshot **and** compact on `/mnt/d` | ~242 GiB free | **No** (~261 GiB need) |
| Snapshot on `/mnt/d` (~127 GiB), compact staging on `/` (~61 GiB candidate; capacity check ≤ ~133.9 GiB) | `/mnt/d` ~242, `/` ~207 | **Yes** — `/` leaves ~146 GiB after a ~61 GiB staged candidate |
| Require more `/mnt/d` space or separately approved retention cleanup before dual artifacts on `/mnt/d` | — | Alternative |

**Recommended topology:**

1. Pause all writers (and later stop API/health before swap).  
2. Online Backup API → current snapshot on `/mnt/d`.  
3. Compact verified snapshot → **new no-clobber staging file on production ext4 `/`**.  
4. Verify local staged candidate before any swap.  

**Fail closed** if free &lt; required + margin. Do not start compact/copy without documented free-space check. Do not create another full 127 GiB clone on `/` by mistake.

---

## 4. Synthetic writable-restore rehearsal (tooling)

**Purpose:** practice writable restore mechanics on **tiny throwaway** DBs only, under an explicit scratch root.

Default scratch root: `/tmp/origenlab_sqlite_rehearsal`.

```bash
cd apps/email-pipeline

# 1) Build marked fixture (WRITE — requires --apply)
uv run --frozen python scripts/maintenance/rehearse_sqlite_writable_restore.py \
  --operation build-fixture \
  --source /tmp/origenlab_sqlite_rehearsal/synthetic_source.sqlite \
  --scratch-root /tmp/origenlab_sqlite_rehearsal \
  --confirm-throwaway \
  --apply \
  --json

# 2) Zero-write preflight (no mkdir / locks / files)
uv run --frozen python scripts/maintenance/rehearse_sqlite_writable_restore.py \
  --operation preflight \
  --source /tmp/origenlab_sqlite_rehearsal/synthetic_source.sqlite \
  --restore-target /tmp/origenlab_sqlite_rehearsal/throwaway_restore.sqlite \
  --scratch-root /tmp/origenlab_sqlite_rehearsal \
  --confirm-throwaway \
  --json

# 3) Rehearse (WRITE — separate apply operation)
uv run --frozen python scripts/maintenance/rehearse_sqlite_writable_restore.py \
  --operation rehearse \
  --source /tmp/origenlab_sqlite_rehearsal/synthetic_source.sqlite \
  --restore-target /tmp/origenlab_sqlite_rehearsal/throwaway_restore.sqlite \
  --scratch-root /tmp/origenlab_sqlite_rehearsal \
  --confirm-throwaway \
  --apply \
  --json
```

**Fail-closed guarantees:**

- Source and target must resolve under the scratch root  
- Approved basename tokens: `throwaway` / `rehearsal` / `synthetic`  
- Dedicated marker table `origenlab_sqlite_rehearsal_meta` required on source  
- Hard max source size **64 MiB**; streaming copy chunk **1 MiB** (no `Path.read_bytes`)  
- Refuses production path/dir/aliases, known offline/compact path shapes, sidecars, collisions  
- Preflight is byte-for-byte zero-write on the tree  
- Evidence claims `sqlite_readonly_reopen_verified` (not a full API process reopen)  
- Typed `RehearsalFailureCategory` / exit codes  

Module: `origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal`  
Tests: `tests/test_sqlite_writable_restore_rehearsal.py`

---

## 5. Future cutover runbook (operator-gated; do not run from this PR)

Stop conditions (abort / rollback): verification failure, unexpected writer PID, sidecar appearance, fingerprint drift, free-space shortfall, API smoke failure, any Gmail/Postgres mutation outside the plan.

**Swap semantics:** the pair `mv production → pre_cutover` then `mv staged → production` is **not** one atomic operation. Do **not** put raw live `mv` commands in the operator checklist until a tested cutover tool exists that either:

- performs a same-filesystem `renameat2(RENAME_EXCHANGE)`-style exchange with fsync and **refuses** when unsupported, or  
- implements an explicitly crash-recoverable **two-phase** rename protocol with a durable state manifest and exact recovery actions.

Until that tool ships, this section is a design checklist only.

1. **Preflight:** pause-file paths as in §1; record prod fingerprint; confirm free space on `/` and `/mnt/d`; confirm no competing maintenance units.  
2. **Pause writers:** create `{reports_dir}/active/current/auto_refresh_paused` and `dashboard_auto_mirror_paused`; no manual `--apply`.  
3. **Confirm quiet:** no writer PIDs; locks gone; WAL not growing; document `lsof`/fd evidence; prove no break-glass writer is active.  
4. **Build current compact:** Online Backup → `/mnt/d` snapshot; compact into staging on `/`; unique stamp; completed compaction manifest.  
5. **Verify** staged candidate on `/`.  
6. **Stop API + health timer** (mandatory before swap).  
7. **WAL/companion handling** per cutover tool (never move main DB while committed frames remain only in WAL).  
8. **Cutover tool** swap (exchange or two-phase with durable manifest) — retain pre-cutover file.  
9. **API RO smoke** against new file.  
10. Bounded GETs: `/health`, `/operator/status`.  
11. **Resume writers gradually** after operator sign-off.  
12. Validate Gmail catch-up + dashboard mirror.  
13. **Retain** pre-cutover file until explicit retention approval.  
14. **Rollback** only via the cutover tool’s documented recovery actions.  
15. Clone deletion only under a **later** explicit approval (Section 6).

**Destructive commands must use fully expanded paths — no unresolved variables or globs in live operator commands.**

---

## 6. Stale-copy retention (assessment only — do not delete)

| Artifact | ~Size | Unique value | Superseded by `/mnt/d` offline+compact? | Recommendation |
|----------|-------|--------------|----------------------------------------|----------------|
| `emails.before_email_mart_features_20260609_215126.sqlite` | ~127.5 GiB | Pre–mart-features June clone | Partially (schema era differs); keep until post-cutover confidence | **Keep** until cutover+soak; then reconsider |
| `backups/emails-20260526T163109Z.sqlite` | ~127.5 GiB | Commercial deal phase3 rollback | Historical | **Keep** until cutover+soak |
| `backups/emails_before_gmail_refresh_20260522_170343.sqlite` | **0 B** | Empty placeholder | Yes | Safe to remove **after** separate approval (no recovery value) |
| `backups/emails.commercial_deal_phase1_test.sqlite` | ~127.5 GiB | Test/scratch | Likely | Prefer delete **after** cutover approval; confirm unused |
| `/mnt/d/.../emails_offline_20260716T023339Z.sqlite` | ~127.5 GiB | Proven offline source for compact | Keep as lineage | **Keep** |
| `/mnt/d/.../emails_compact_20260717T183537Z.sqlite` | ~60.9 GiB | Verified compact + RO recovery evidence | Point-in-time only (stale vs live) | **Keep** as experiment evidence; not cutover source |

**Estimated reclaim if same-volume clones removed after successful cutover+soak:** on the order of **~250–380 GiB** depending which ~127.5 GiB files are approved for deletion (never delete in this PR).

---

## 7. Remaining risks

- Production drift vs July-16 compact candidate (already proven).  
- `/mnt/d` cannot hold snapshot+compact simultaneously at current free space (~242 vs ~261 GiB).  
- Hard-link no-clobber unsupported on some `/mnt/d` layouts — compact/publish must refuse, not fall back to clobber.  
- Long maintenance window: WSL sleep/network; use system-level units for multi-hour compact.  
- Cutover rename tool not yet implemented — do not improvise dual `mv`.  
- Operator error during swap — mitigate with synthetic rehearsal + checklist + retained pre_cutover file.

---

## 8. Explicit non-goals of this document / PR

No production write open for cutover, no cron/systemd pause executed by agents, no real 61 GiB writable copy, no VACUUM on production, no clone deletion, no Gmail/Postgres mutation, no merge of cutover itself.
