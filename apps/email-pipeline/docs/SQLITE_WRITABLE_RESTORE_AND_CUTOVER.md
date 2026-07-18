# SQLite writable restore rehearsal and zero-data-loss cutover plan

**Status:** design + synthetic rehearsal tooling only.  
**Does not authorize:** pausing automation, opening production read-write for cutover, creating a real ~61 GiB writable copy, swapping production, deleting clones, or in-place `VACUUM`.

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

### Always-on / scheduled

| Path | SQLite R | SQLite W | External | Pause | Resume | Missed work |
|------|----------|----------|----------|-------|--------|-------------|
| Cron `auto-refresh-mail --once --apply` → `daily-core` | Y | **Y** (ingest, mart, commercial, …) | Gmail IMAP **R** | `touch …/auto_refresh_paused` | `rm` pause file | **Mostly replayable** (Message-ID skip + rebuildable marts) |
| Cron `auto-mirror-dashboard --once --apply` | Y | N | Postgres **W** | `touch …/dashboard_auto_mirror_paused` (also blocked by mail pause) | `rm` | **Replayable** (re-publish) |
| Cron ChileCompra equipment refresh | N | N | ChileCompra + Postgres | Comment cron / omit `--apply` | Restore cron | Partial (re-fetch) |
| `origenlab-api.service` (sqlite backend) | Y | **N** (`mode=ro` + `query_only`) | Optional PG | `systemctl --user stop origenlab-api` | `start` | N/A |
| API health timer | Indirect | N | curl | stop timer | start | N/A |

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

Schema apply, purges, dedupe, NDR auto-apply, outreach/suppression imports, commercial deal apply, lead research imports, attachment extracts, legacy mbox/IMAP ingest, etc. Pause method: **do not run**; for scheduled mail use pause file.

### What cannot reconstruct SQLite from Gmail/Postgres alone

- `email_mart_features`, mart tables, commercial_* tables  
- outreach/suppression/operator sidecar state  
- attachment extracts / research / catalog tables  
- Any in-flight uncommitted WAL frames  

Therefore **online backup while writers run cannot claim RPO=0** unless every post-backup writer delta is captured and replayed — the repo has **no** dual-write change log for that purpose.

---

## 2. Consistency options and recommendation

### Option A — Full maintenance window (recommended for RPO=0)

1. Pause **all** SQLite writers (mail auto-refresh pause file; skip manual `--apply`; optional stop API readers for clean `lsof`).  
2. Checkpoint/quiet WAL (writers stopped; confirm no `-wal` growth / no foreign PIDs holding the DB).  
3. Build a **current** compact candidate:
   - Preferred: Online Backup API → `/mnt/d` snapshot, then offline `VACUUM INTO`, **or**
   - Direct `VACUUM INTO` from paused production to a new `/mnt/d` destination (still not in-place VACUUM).  
4. Verify (quick_check, FK, schema fingerprint, critical counts, zero sidecars, manifest).  
5. Copy verified candidate to **same filesystem as production** staging path (no-clobber).  
6. Atomic rename swap; retain pre-cutover file.  
7. API recovery/read-only smoke, then gradual writer resume.

| Metric | Estimate |
|--------|----------|
| RPO | **0** (writers stopped before consistent copy/compact) |
| RTO / downtime | Dominated by compact (~2–3+ h historically for ~127→61 GiB) + verify + local copy (~0.5–1 h) + smoke; plan **4–6 h** window with margin |
| Writers stop | Before consistent snapshot/compact begins |
| Writers resume | After swap + RO smoke + explicit operator approval |
| Mail during window | Remains on Gmail server; next ingest after resume catches up by Message-ID |

### Option B — Online backup then offline compact (writers stay up)

Creates a point-in-time clone; **post-backup SQLite writes are a delta**. Gmail can replay *mail rows* but **not** all mart/commercial/sidecar state. **Cannot claim RPO=0** without a separate delta-capture mechanism (not available today).

### Option C — Direct `VACUUM INTO` from paused production to `/mnt/d`

Same RPO=0 as A if writers are paused. Locking: exclusive-ish rewrite traffic against a quiet DB; downtime ≈ compact duration on `/mnt/d` plus copy-back. Cross-FS staging required before atomic local swap (hard-link publish needs same FS).

### Option D — Incremental / dual-write shadow migration

High engineering cost (change log, dual-write, catch-up, cutover). Not worthwhile for a **one-time freelist compaction**.

### Recommendation

**Option A (maintenance window)**, using either Online Backup→offline compact or direct `VACUUM INTO` to `/mnt/d` **only while writers are paused**. Reject Option B for “zero data loss.” Defer Option D.

**Exact stop point:** after pause files + confirmed no SQLite writer PIDs + stable WAL.  
**Exact resume point:** after atomic swap, RO API smoke, operator sign-off; enable mail auto-refresh first, then mirror.

---

## 3. Disk capacity (cutover-shaped)

Assumptions: new compact ≈ 61 GiB; retain old production ≈ 127.5 GiB until approval.

| Filesystem | Free (approx.) | Need |
|------------|----------------|------|
| `/mnt/d` | ~242 GiB | New offline snapshot (~127 GiB) **or** direct compact (~61 GiB) + manifests; keep prior offline/compact until retention decision |
| `/` (prod volume) | ~207 GiB | Staging compact (~61 GiB) beside live prod **before** rename; after rename, old prod retained (~127 GiB) — peak extra ≈ **61 GiB** while both live+staged exist (fits in 207) |

**Fail closed** if free &lt; required + margin. Do not start compact/copy without documented free-space check.

---

## 4. Synthetic writable-restore rehearsal (tooling)

**Purpose:** practice writable restore mechanics on **tiny throwaway** DBs only.

```bash
cd apps/email-pipeline
# Preflight
uv run --frozen python scripts/maintenance/rehearse_sqlite_writable_restore.py \
  --build-synthetic-source /tmp/origenlab_rehearsal/synthetic_source.sqlite \
  --restore-target /tmp/origenlab_rehearsal/throwaway_restore.sqlite \
  --confirm-throwaway \
  --json

# Apply (copy + writable txn + rollback verification)
uv run --frozen python scripts/maintenance/rehearse_sqlite_writable_restore.py \
  --source /tmp/origenlab_rehearsal/synthetic_source.sqlite \
  --restore-target /tmp/origenlab_rehearsal/throwaway_restore.sqlite \
  --confirm-throwaway \
  --apply \
  --json
```

**Guarantees:**

- Refuses production path / canonical production / production-like basename  
- Basename must include `throwaway`, `rehearsal`, or `synthetic`  
- No-clobber targets; source fingerprint unchanged  
- Writable transaction + read-back + rollback to source bytes  
- Never opens the real ~61 GiB candidate  
- Sanitized JSON (no absolute secret paths required for operators)

Module: `origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal`  
Tests: `tests/test_sqlite_writable_restore_rehearsal.py`

---

## 5. Future cutover runbook (operator-gated; do not run from this PR)

Stop conditions (abort / rollback): verification failure, unexpected writer PID, sidecar appearance, fingerprint drift, free-space shortfall, API smoke failure, any Gmail/Postgres mutation outside the plan.

1. **Preflight:** confirm pause files work; record prod fingerprint; confirm free space on `/` and `/mnt/d`; confirm no competing maintenance units.  
2. **Pause writers:** `auto_refresh_paused`; `dashboard_auto_mirror_paused`; no manual `--apply`; optional stop API.  
3. **Confirm quiet:** no writer PIDs; WAL not growing; document `lsof`/fd evidence.  
4. **Build current compact** on `/mnt/d` (backup+compact or paused `VACUUM INTO`); unique stamp; completed compaction manifest.  
5. **Verify:** quick_check, FK=0, schema fingerprint, critical counts, zero sidecars, manifest `completed=true`.  
6. **Stage locally:** copy candidate to `emails.sqlite.staged.<STAMP>` on the **production filesystem** (no-clobber).  
7. **fsync** staging file + directory.  
8. **Atomic swap (same FS only):**  
   - `mv emails.sqlite emails.sqlite.pre_cutover.<STAMP>`  
   - `mv emails.sqlite.staged.<STAMP> emails.sqlite`  
   - Never `rm` the pre_cutover file in this step.  
9. **API RO / recovery-style smoke** against new file (ordinary `mode=ro` once live WAL/sidecars policy is satisfied — **not** immutable against live prod).  
10. Bounded GETs: `/health`, `/operator/status`.  
11. **Resume writers gradually:** remove mail pause → watch one successful ingest → remove mirror pause.  
12. Validate Gmail catch-up + dashboard mirror.  
13. **Retain** `emails.sqlite.pre_cutover.<STAMP>` until explicit retention approval.  
14. **Rollback trigger:** restore by renaming pre_cutover back to `emails.sqlite` after stopping writers again; re-verify.  
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
- Hard-link no-clobber unsupported on some `/mnt/d` layouts — compact/publish must refuse, not fall back to clobber.  
- Root free space ~207 GiB is adequate for staged 61 GiB but leaves less margin if another full 127 GiB clone is created on `/` by mistake.  
- Long maintenance window: WSL sleep/network; use system-level units for multi-hour compact.  
- Operator error during rename swap — mitigate with rehearsal + checklist + retained pre_cutover file.

---

## 8. Explicit non-goals of this document / PR

No production write open for cutover, no cron/systemd pause executed by agents, no real 61 GiB writable copy, no VACUUM on production, no clone deletion, no Gmail/Postgres mutation, no merge of cutover itself.
