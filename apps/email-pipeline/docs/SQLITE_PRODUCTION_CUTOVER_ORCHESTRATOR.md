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
  -- journal original_mode + intent
  -- open FD, fstat match device+inode, fchmod(~write)
  -- fsync; verify; rescan FDs
        |
        v
create_current_backup → compact → verify
        |
        v
approve_swap (staging owner + RO) → atomic_swap → readonly_smoke → resume_services
        |
        v
resume_writers_ponr          -- writer_resume_started (rollback forbidden)
resume_writers_restore_mode  -- fchmod restore writable on verified inode
resume_writers_mail          -- remove mail pause
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
| Chmod applied; `barrier_active` not yet journaled | `abort_before_swap` restores from `original_mode` / `permission_intent` / private plan |
| `barrier_active=true`, pre-swap | Continue stages or `abort_before_swap` |
| After `swap_intent` / exchange | Abort refused; use swap reconciliation / rollback rules |
| After `writer_resume_started` | Abort + automatic rollback refused |
| Mail observe failed | Mail re-paused; mirror blocked; fix then re-run observe |

`reconcile_permission_barrier` inspects mode vs journal and reports the recognized state — it never silently leaves writers unpaused with an unknown DB mode.

## Read-only smoke (loopback getter + fail-safe API cleanup)

`readonly_smoke` runs after `atomic_swap` and before `resume_services`.

**Default loopback getter.** `FilesystemAdapters.http_get` now has a real
production default: `make_loopback_json_getter(base_url)` is used automatically
when no getter is injected (dependency injection stays available for tests). The
getter is **GET-only**, restricted to the configured loopback origin
(`http://127.0.0.1:8001` by default), **rejects redirects and any final URL that
leaves the approved loopback origin**, applies bounded connect/read timeout and a
bounded response-byte limit, and requires **HTTP 200 with a JSON object**. It
attaches `ORIGENLAB_API_AUTH_TOKEN` via the `X-OriginLab-API-Key` header through
the existing settings mechanism and never logs the token, response bodies,
secrets, or absolute paths.

**Fail-safe API ownership.** The stage tracks whether it started the API
(`journal.smoke_started_api`). If the API start succeeds but any later HTTP smoke
request, validation, journal write, or stage completion fails, the stage:

- stops **only** the API it started (never an unrelated process);
- keeps the health timer stopped;
- leaves writers paused and `smoke_ok=false`;
- does **not** advance the journal from `atomic_swap`;
- preserves rollback-before-writers availability.

If the API is already active on entry and this stage did not start it, the stage
**fails closed without claiming ownership or stopping** the unrelated process. On
successful smoke the API remains running and the health timer remains stopped
until `resume_services` (unchanged designed behavior).

## Service activity / exit-143 bookkeeping

An intentional `systemctl stop` sends SIGTERM and can leave the unit in a
`failed` state with `ExecMainStatus=143`. `classify_api_activity(...)` encodes the
gate policy: any **non-active / no-PID / no-listener** state — including that
`failed`/143 result — is treated as **stopped**, while a process that is `active`
(or still has a live PID **and** a loopback listener) is treated as **running**
and rejected by "must be stopped" gates. Genuine failure sub-states
(auto-restart, OOM, restart-limit) are surfaced via `genuine_failure_signal` so a
real OOM, restart loop, bind failure, or traceback is never silently masked as a
clean stop. This PR intentionally does **not** change the deployed systemd unit;
the policy lives in the orchestrator + tests.

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
- `scripts/maintenance/orchestrate_sqlite_production_cutover.py`
- `tests/test_sqlite_production_cutover.py`
- `tests/test_sqlite_cutover_readonly_smoke.py` — loopback getter + exit-143 gates
