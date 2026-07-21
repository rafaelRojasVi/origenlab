# SQLite production cutover orchestrator

**Status:** hardened staged orchestrator + synthetic tests (draft).  
**Does not authorize** running a real production cutover from this PR alone.

Three distinct modes (do not conflate):

| Mode | What it does |
|------|----------------|
| Writable **RO rehearsal** | Separate tool (`sqlite_writable_restore_rehearsal`) — scratch fixtures only |
| **Zero-write production preflight** | `plan_preflight` — reports only; no locks, journals, pauses, chmod |
| **Real staged apply** | Separate `--apply` per stage with high-friction auth |

The July 2026 compact candidate is **not eligible** for production cutover (evidence only).

## Corrected access classification

| Class | Entry | Barrier |
|-------|-------|---------|
| **SQLite writers** | `mail_auto_refresh` / daily-core | `auto_refresh_paused` |
| **SQLite writers** | ad-hoc operator scripts | OS chmod write barrier (accidental RW opens) |
| **SQLite readers** | `dashboard_auto_mirror` | `dashboard_auto_mirror_paused` |
| **SQLite readers** | `origenlab-api` + health timer | `systemctl stop` |
| **Unrelated external** | `chilecompra_equipment_auto_refresh` | optional quiet — **does not open/mutate SQLite**; **must not block RPO=0** |

Threat model: chmod write barrier blocks ordinary accidental ad-hoc SQLite writes. **Malicious/root bypass is explicitly out of scope.**

## State diagram

```text
plan_preflight (zero-write, no lock)
        |
        v
pause_writers → stop_readers → quiesce_wal
        |
        v
apply_os_write_barrier
  -- capture main + exact -wal/-shm via open/fstat
  -- journal artifact_permissions before first chmod
  -- fchmod(~write) per captured identity; partial → barrier_partial
  -- fsync; verify; rescan FDs
        |
        v
create_current_backup → compact → verify
        |
        v
approve_swap (staging owner + RO) → atomic_swap
  -- post_swap_main binds new production inode
  -- companions re-captured (never copy old sidecar inode onto a new one)
        → readonly_smoke → resume_services
        |
        v
resume_writers_ponr          -- writer_resume_started (rollback forbidden)
resume_writers_restore_mode  -- restore main + barrier-changed companions; refuse RO SHM/WAL
resume_writers_mail          -- require writable artifact; then remove mail pause
resume_writers_observe_mail  -- accept size/mtime drift; on failure RE-PAUSE mail
resume_writers_mirror        -- only if mail_observe_ok
resume_writers_observe_mirror
resume_writers_commit → completed

abort_before_swap: before swap_intent AND before PoNR only
```

## Write-barrier / abort recovery matrix

| Condition | Safe action |
|-----------|-------------|
| Intent written; chmod not yet applied | Re-run `apply_os_write_barrier` or `abort_before_swap` |
| Chmod applied to some members; `barrier_partial` | `abort_before_swap` restores **every** member in `members_changed` (main/WAL/SHM) |
| Chmod applied; `barrier_active` not yet journaled | `abort_before_swap` restores from `artifact_permissions` / `original_mode` / private plan |
| `barrier_active=true`, pre-swap | Continue stages or `abort_before_swap` |
| After `swap_intent` / exchange | Abort refused; use swap reconciliation / rollback rules |
| After `writer_resume_started` | Abort + automatic rollback refused |
| Mail observe failed | Mail re-paused; mirror blocked; fix then re-run observe |
| SHM/WAL still `0444` before mail resume | Fail closed; do not remove mail pause; mirror stays paused |

`reconcile_permission_barrier` inspects main (+ journaled companions) vs journal and reports the recognized state — it never silently leaves writers unpaused with an unknown DB mode, and never invents companion modes for a legacy journal that lacks `artifact_permissions`.

## Artifact permissions (main / `-wal` / `-shm`) — PR-B

The production SQLite file is one **artifact** with three exact members (no globs):

| Member | Path rule |
|--------|-----------|
| main | production basename |
| WAL | `production` + `-wal` |
| SHM | `production` + `-shm` |

**Capture** uses open + `fstat` (symlink / non-regular refused). Journal field `artifact_permissions` records presence, device, inode, mode, uid/gid, nlink, size, and which members were barrier-changed. Legacy `original_mode` remains mirrored from main for compatibility. Older journals without `artifact_permissions` still load; companion ownership is **never** silently invented for an in-progress legacy barrier.

**Write barrier** journals the full capture **before** the first `fchmod`, then applies the RO barrier only to captured identities. Partial chmod sets `barrier_partial` and remains recoverable via `abort_before_swap`.

**Swap-aware restore:** `RENAME_EXCHANGE` moves main inodes only; `-wal`/`-shm` stay path-bound. After swap, `post_swap_main` binds writable restore to the **new** production inode. Pre-swap companion metadata is never copied onto a different inode. `readonly_smoke` re-captures companions present after API open. Writer resume restores barrier-changed companions that still match identity, requires any other present companion to already be writable, and refuses mail resume if SHM/WAL remain read-only.

## Post-incident PR roadmap A–F (canonical)

| PR | Scope | Status |
|----|--------|--------|
| **A — Smoke + fail-safe** | Bounded loopback JSON getter; fail-safe API cleanup; SIGTERM/143 bookkeeping | **Complete** (PR [#387](https://github.com/rafaelRojasVi/origenlab/pull/387)) |
| **B — Permissions + sidecars** | Capture/apply/reconcile/restore main + WAL + SHM modes via FD `fstat`/`fchmod` | **Implemented by this change** |
| **C — Rollback finalize** | Supported `rollback_finalize` (abandoned ≠ completed) | **Not started** |
| **D — Maintenance boot policy** | Prevent API/timer auto-start after WSL reboot during maintenance | **Not started** |
| **E — Observability + backup FD taxonomy** | Trusted backup WAL/SHM locking FD classification; sanitized OperationalError detail | **Not started** |
| **F — Incident regression pack** | Broader end-to-end incident-chain regressions | **Not started** |

**Merging PR-B does not authorize a cutover.** The abandoned maintenance ID `cutover20260719T163633Z` remains abandoned and **must never be resumed**. Any future cutover requires a fresh MID and the full human approval boundary below.

## Read-only smoke (loopback getter + fail-safe API cleanup)

`readonly_smoke` runs after `atomic_swap` and before `resume_services`.

**Default loopback getter.** `FilesystemAdapters.http_get` now has a real
production default: `make_loopback_json_getter(base_url)` is used automatically
when no getter is injected (dependency injection stays available for tests). The
getter is **GET-only**, restricted to the configured loopback origin
(`http://127.0.0.1:8001` by default), **rejects redirects, userinfo, fragments,
non-loopback hosts, and any origin/port drift** on both the request URL and the
final URL, and requires **HTTP 200 with a JSON object**. The response byte cap is
enforced by reading at most `limit + 1` bytes. Timeouts are **per endpoint**:
`/operator/status` gets a longer bounded allowance (180s — it has been observed
to take ~57s under real load) while `/health` and `/operator/automation-status`
keep a short bound (15s).

**Auth contract.** The getter matches the real `apps/api` auth contract
(`origenlab_api.http_security`): the API accepts **`Authorization: Bearer <token>`
(checked first) and `X-OriginLab-API-Key: <token>` (fallback)**, comparing with
`secrets.compare_digest`; `/health` is public. The getter sends **both** headers
from `ORIGENLAB_API_AUTH_TOKEN` and never logs the token, response bodies,
secrets, or absolute paths. A contract test in `apps/api`
(`tests/test_smoke_getter_auth_contract.py`) feeds the getter's emitted headers
through the real `extract_api_token` to keep the two sides in lock-step.

**Fail-safe API ownership.** The stage records durable ownership
(`journal.smoke_started_api = true`) **before** calling `start_api()`, so a crash
can never orphan an *unowned* running API — a resumed run sees the flag and either
re-drives or safely stops it. If the API start succeeds but any later HTTP smoke
request, validation, journal write, or stage completion fails, the stage:

- stops **only** the API it started (never an unrelated process);
- **verifies via `api_activity()` that the API has no PID/listener before clearing
  ownership**; if the stop failed or the API is still running/ambiguous it keeps
  `smoke_started_api=true`, surfaces a sanitized `manual_stop_required` on the
  original error, and never claims cleanup succeeded;
- keeps the health timer stopped;
- leaves writers paused and `smoke_ok=false`;
- does **not** advance the journal from `atomic_swap`;
- preserves rollback-before-writers availability;
- **preserves the original smoke error** (cleanup state is *attached* as sanitized
  evidence, never substituted).

If the API is already active/ambiguous on entry and this stage did not start it,
the stage **fails closed without claiming ownership or stopping** the unrelated
process. On successful smoke the API remains running and the health timer remains
stopped until `resume_services` (unchanged designed behavior).

## Service activity / exit-143 bookkeeping

An intentional `systemctl stop` sends SIGTERM and can leave the unit in a
`failed` state with `ExecMainStatus=143`. `classify_api_activity(...)` — used by
the **real** `FilesystemAdapters.api_activity()` path (systemd `is-active` +
`MainPID`/`SubState` + a loopback listener probe), not only by tests — encodes the
gate policy:

- a **known** stopped state (`inactive`/`failed`/`dead`) with **no PID and no
  listener** — including the `failed`/143 result of an intentional stop — is
  **stopped**;
- `active`, any live **PID or listener**, or an **unknown/unreachable** activity
  text is treated as **running/ambiguous** and **fails closed** so "must be
  stopped" gates never proceed on uncertainty;
- genuine failure sub-states (auto-restart, OOM, restart-limit) are surfaced via
  `genuine_failure_signal` so a real OOM, restart loop, bind failure, or traceback
  is never silently masked as a clean stop.

This PR intentionally does **not** change the deployed systemd unit; the policy
lives in the orchestrator + tests.

## Final human approval boundary

Real apply requires **all** of:

1. `--confirm-production-cutover`
2. Unique `--maintenance-id` matching `^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$`
3. Exact 40-char `--expected-main-sha` matching `git rev-parse HEAD` (and `main` / clean / `origin/main` for non-synthetic)
4. Exact `--expected-production-path` + fingerprint matching canonical production
5. Separate `--approve-swap` for `approve_swap` / `atomic_swap`
6. Operator maintenance window + capacity confirmation on target topology

No interactive fingerprint echo is implemented; the operator must supply the exact tokens.

## Remaining environment-only blockers

1. Live maintenance window + disk capacity on `/mnt/d` and `/`.
2. Prove `renameat2(RENAME_EXCHANGE|NOREPLACE)` on the production filesystem.
3. Explicit human approval of the tokens above at swap time.
4. Keep **draft** until an approved window; this PR does not authorize a real cutover.

## Code-readiness scope of this change

This change fixes **code readiness only** (real loopback smoke getter, fail-safe
API cleanup, exit-143 classification) and **does not authorize another cutover
maintenance ID**. The abandoned attempt **`cutover20260719T163633Z`** remains
abandoned and **must never be resumed**; any future cutover requires a fresh MID
and the full human approval boundary above.

## Module / tests

- `origenlab_email_pipeline.qa.sqlite_production_cutover`
- `origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions` — PR-B artifact mode capture/barrier/restore
- `scripts/maintenance/orchestrate_sqlite_production_cutover.py`
- `tests/test_sqlite_production_cutover.py`
- `tests/test_sqlite_cutover_readonly_smoke.py` — loopback getter + exit-143 gates
- `tests/test_sqlite_cutover_artifact_permissions.py` — main/WAL/SHM permissions + July 19 SHM regression
