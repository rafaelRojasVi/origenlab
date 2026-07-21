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
| **B — Permissions + sidecars** | Capture/apply/reconcile/restore main + WAL + SHM modes via FD `fstat`/`fchmod` | **Complete** (PR [#393](https://github.com/rafaelRojasVi/origenlab/pull/393)) |
| **C — Rollback finalize** | Supported `rollback_finalize` (abandoned ≠ completed) | **Implemented by this change** |
| **D — Maintenance boot policy** | Prevent API/timer auto-start after WSL reboot during maintenance | **Not started** |
| **E — Observability + backup FD taxonomy** | Trusted backup WAL/SHM locking FD classification; sanitized OperationalError detail | **Not started** |
| **F — Incident regression pack** | Broader end-to-end incident-chain regressions | **Not started** |

**Merging PR-C does not authorize a cutover.** It only adds an explicit terminal path for an already-verified rollback. The abandoned maintenance ID `cutover20260719T163633Z` remains permanently non-resumable — it is rejected by `_require_auth` for **every** operation, including `--rollback-finalize` — and any future cutover requires a fresh MID and the full human approval boundary below. PR-D–F remain unstarted.

### Deferred (known) swap-reconciliation hardening

Out of scope for PR-C and intentionally **not** changed: `reconcile_atomic_swap_state` still classifies an "exchange succeeded but the journal write was stale" reality as `ambiguous_pre_exchange`. Teaching the classifier to positively distinguish that case is a separate future swap-reconciliation hardening item; it is not folded into rollback finalize.

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

## Rollback finalize — terminal `ABANDONED` (PR-C)

After a verified **pre-point-of-no-return** rollback (`attempt_rollback_before_writers`) has physically restored the original database to the production path, the cutover is *not* complete and must never become complete. PR-C adds an explicit, crash-safe operation to close it out.

**Distinct terminal state.** `CutoverStage.ABANDONED` is a terminal state that is **mutually exclusive** with `COMPLETED`. ABANDONED is deliberately kept **out of the linear `STAGE_ORDER`**, so ordinary `next_stage`/`previous_stage` progression can never advance a rolled-back MID toward `COMPLETED`. ABANDONED is **never** a successful cutover, is **never** soak-eligible, and **never** unblocks Waves 3B/3C. The `COMPLETED` stage handler additionally refuses if `abandoned`/`rollback_verified` is set.

**Structured rollback proof.** A successful rollback is no longer inferred from `stage=atomic_swap`, a note string, `swap_direction`, or pathnames. `attempt_rollback_before_writers` now durably records `rollback_verified=true` plus a structured `rollback_proof` block: the restored original fingerprint, the live restored device/inode (which it also writes back to `production_device`/`production_inode`), the approved-plan original identity for cross-check, that the rollback occurred **before** `writer_resume_started`, that the compacted candidate is **not** production (and its retained basename), and that `rollback_finalize` is the only supported forward operation. It reports `next_required: "rollback_finalize"`. Legacy journals lacking this structured proof **refuse** `rollback_finalize` and require manual reconciliation.

**Normal-path lockout (every mutating entrypoint).** Once any rollback-finalize marker is recorded — `rollback_verified`, `rollback_finalize_intent`, `rollback_finalized`, `abandoned`, or `stage=ABANDONED` — the shared predicate `_rollback_finalize_locked` refuses **all** ordinary execute/resume/apply stages (including `readonly_smoke → completed` and `resume_writers_ponr`), a **second** `attempt_rollback_before_writers` (so the original and candidate can never be exchanged again), and `abort_before_swap`. Only the dedicated `rollback_finalize` operation, status, and manual reconciliation may proceed. A crash therefore can never leave a rolled-back journal able to continue toward `COMPLETED` or to re-swap.

**Separate writer semantics.** Normal-cutover fields `writer_resume_started` / `writers_resumed` keep their meaning (writers resumed on the **new** production DB) and are never set by finalize. Rollback finalize uses its own state — `rollback_original_writers_resumed`, `rollback_finalize_mail_resumed`, `rollback_finalize_mirror_resumed` — and status reports `writers_resumed_against: "restored_original"`.

**Eligibility (fail closed on any ambiguity).** All of: supported journal version; explicit `rollback_verified` proof; `writer_resume_started == false`; normal writers never resumed; journal neither `COMPLETED` nor already terminal; production fingerprint **and** device/inode match the captured original (and the approved-plan original); the compacted candidate is not at production; the retained candidate is present and matches, or absent only per the explicit rule; no unexplained main/WAL/SHM identity drift; exact maintenance-ID match. Finalize never attempts another exchange to force the state to fit.

**Crash-safe finalization order.** (1) `--apply` + full approval + **exact repository HEAD** match, plus the **same non-synthetic canonical production path / type / symlink / samefile binding and confined journal-location rules as `apply_stage`** (evaluated before any mutation; the mutable production fingerprint is *not* compared here — operator approval is bound to the rollback proof's original/restored fingerprint, and live content is checked separately via authoritative identity + `quick_check` after writer control is regained); (2) strict terminal-consistency check — idempotent success is returned **only** for a record that is both journal-*coherent* **and** passes the full typed `rollback_proof` validation **and** the live authoritative identity check (restored device/inode == approved plan, candidate not at production, retained candidate present/matched); any mixed / malformed / incomplete / identity-contradictory terminal record → sanitized `MANUAL_RECOVERY_REQUIRED`, never `already_finalized=true`; (3) typed `rollback_proof` schema validation, requiring approved-plan device/inode; (4) durably write `rollback_finalize_intent` before any mutation; (5) **reassert both writer pauses**, then **durably stop + verify the health timer FIRST** (so it can never reopen/retrigger API work in the window before quiescence), then **classify the API activity once (ambiguous preserved) and reconcile it (owned → durable-intent stop + activity-classifier verification; ambiguous or unowned → manual recovery, never stopped) BEFORE demanding writer/FD quiescence**, then prove quiescence (pauses + locks + FDs) — so a real API-held read-only production/sidecar FD is released by the owned stop instead of forcing manual recovery, and an interrupted finalize whose writers legitimately resumed is re-paused and recovered instead of rejected on content drift; (6) immutable identity bindings via **authoritative fstat capture** (device/inode + approved plan) and candidate-not-production; the pristine size/mtime fingerprint is required only while no writer-pause removal was ever *intended* or recorded (once mail resume was durably intended, identity + `quick_check` carry safety); (7) verify WAL/SHM lifecycle against a **typed** `rollback_sidecar_proof` **before** touching services — a missing/malformed proof, or an appeared / disappeared / drifted / candidate / ambiguous / not-barrier-applied sidecar, fails closed (never delete/chmod); (8) restore the original main/WAL/SHM using PR-B's exact device/inode-bound logic rebound to the **original** (now-live) inode; (9) reconcile the remaining service policy against the captured **pre-maintenance policy** with **durable stop/start intent + verified success** for both API and health timer (retain ownership + `MANUAL_RECOVERY_REQUIRED` on any unverified stop; clear current service intent only after verified completion); (10) verify health against the restored original via the bounded loopback getter, using an **owned temporary API** (started/verified/stopped) when the API was inactive pre-maintenance; (11) recapture API-open sidecars through hardened `lstat`/open/`fstat`; (12) converge to and verify the pre-maintenance service state; (13) verify main + every present sidecar writable; (14) resume mail then mirror with genuine intent-before / verified-pause-absence / success-after and observation gates; (15) only then durably set `abandoned=true`, `rollback_finalized=true` (+ timestamp), clear `rollback_finalize_intent`, `stage=ABANDONED`, fsync the journal dir, leaving `completed=false` and reporting `cutover_succeeded=false`, `soak_eligible=false`, `waves_unblocked=false`.

**Immutable vs mutable identity.** Device/inode and the approved-plan original identity are **immutable bindings** checked on every entry/retry via **`capture_member_identity`** — a non-following, non-blocking `open` + `fstat` (the same PR-B primitive that closes the FIFO/symlink/TOCTOU hole), never a pathname-following `stat`. `attempt_rollback_before_writers` itself captures the **restored main** identity through this authoritative fstat (exactly like WAL/SHM) when it records the proof; if that capture fails *after* the physical exchange it records a durable conservative lock (`rollback_identity_capture_failed`) that refuses normal progression and routes `rollback_finalize` to manual recovery — it never fabricates a proof identity, reports verified rollback, or allows the normal cutover path to continue. The deferred, general `reconcile_atomic_swap_state` classifier is intentionally unchanged. The original size/mtime **fingerprint** is mutable once writers legitimately resumed: finalize records `rollback_finalize_post_mail_fingerprint` at the instant mail may write, and a retry after real mail activity (or after a durable resume *intent*) relies on immutable identity + `quick_check` rather than demanding the pristine fingerprint — so recovery never rejects a legitimately-changed original while leaving writers live.

**Sidecar lifecycle.** `RENAME_EXCHANGE` moves only the main file, so `attempt_rollback_before_writers` records a **typed** `rollback_sidecar_proof` (exact-integer schema, non-empty string capture timestamp, and per-role WAL/SHM records whose `present` is an exact boolean and whose device/inode are exact integers, captured via authoritative fstat) immediately after the physical rollback. Both the `rollback_proof` and `rollback_sidecar_proof` are parsed as fully untrusted input: non-dict values, `bool`/`str`/`float` schema coercion, empty timestamps, non-exact booleans, unsafe basenames (separators / traversal / absolute / NUL), proof plan device/inode that do not equal the actual `approved_plan` values, `old != restored` / `old == new` fingerprints, and a mismatched expected approval fingerprint all fail closed to sanitized `MANUAL_RECOVERY_REQUIRED`. A **missing** proof fails closed even when both pathname sidecars are currently absent; sidecar identity is always read via `capture_member_identity` (fstat), never pathname `stat`.

| Boundary | WAL/SHM expectation |
|----------|----------------------|
| after rollback (proof captured) | typed identity snapshot (fstat) or explicit absence |
| before API start | typed proof present **and** live identity equals proof **and** an original member that is present, identity-matched, `barrier_applied=true`, and in PR-B `members_changed`; else fail closed |
| missing / malformed proof, candidate-created / appeared / disappeared / drifted / ambiguous / not-barrier-applied | `MANUAL_RECOVERY_REQUIRED` (never delete/chmod) |
| after restored-original API open | new sidecars legitimate; hardened-recaptured, then writable-verified |

**Service ownership / pre-maintenance policy.** `STOP_READERS` durably captures `pre_maintenance_api_active` / `pre_maintenance_health_timer_active` before stopping anything. On retry the ordering is fixed: reassert pauses → **durably stop + verify the health timer** (before it can reopen/retrigger API work) → **classify API activity once** (ambiguous never discarded) → ambiguous/unowned activity is `MANUAL_RECOVERY_REQUIRED` without stopping it → demonstrably owned activity is a durable stop intent + verified stop → only then FD/writer quiescence and identity/permission work. Ownership is **re-checked immediately before using/starting the health-check API**, so an API that appeared after the initial reconcile is never silently accepted (ambiguous or unowned → manual recovery). Every late/final timer stop carries durable intent + verified-success persistence; no externally visible service transition occurs without a journal intent; the orchestrator starts **only** services active pre-maintenance, clears the *current* service intent only after verified completion, never clears ownership on an unverified stop, and final policy convergence verifies **both** services against the captured pre-maintenance truth. Manual recovery attempts a **verified stop of a demonstrably owned temporary API** (clearing that ownership only when the stop verifies). A legacy journal missing the capture fails closed (backward-compatible refusal).

**Failure / retry behavior.** Each writer resume is genuinely **intent-before / verified-success-after**: finalize persists only `*_resume_intent`, unlinks the pause, **authoritatively observes the pause is absent**, and only then persists historical `*_resumed=true` and current `*_pause_absent=true` (+ post-mail fingerprint). A crash after unlink but before success leaves durable intent, and the retry reconciles from intent + live pause observation; an unlink that fails before mutation never claims resume; an unexpectedly still-present pause fails closed. A crash before the terminal write leaves `rollback_finalize_intent` set, so the normal cutover path stays locked and re-running `--rollback-finalize` reconciles actual pause/service/identity state and safely finishes `ABANDONED` — it never restarts the normal path. **Historical** resume truth (`*_resumed`) is set once and **never reset**; only **current** pause state (`*_pause_absent`, `rollback_finalize_writers_repaused`) changes on re-pause. A partial writer resume (observation fails) fails safe by reasserting **each** pause **independently** — recording mail and mirror **current** presence/absence separately (so a partial state where one re-pause succeeds and the other fails is persisted exactly), preserving historical resume truth, stopping a demonstrably **owned temporary API before** the final quiescence assessment (then rerunning quiescence so evidence describes the post-cleanup state), and preserving triggering, cleanup, and journal-write failures **separately and sanitized** — returning `MANUAL_RECOVERY_REQUIRED` (never `ABANDONED`/`COMPLETED`/success). The journal itself is written through an atomic replace that **never moves the live canonical journal away before the replacement**: the previous version is preserved via a hardlink (`.prev`) so the canonical path is *always* the old or the new complete journal — there is no path-missing window merely to retain `.prev`. Real-tmpdir fault tests exercise every write phase (temporary open/write, temporary fsync, previous-version preservation, final replace, new-file fsync, parent-directory fsync) and verify the actual canonical and `.prev` contents after each fault: a pre-replace fault leaves the OLD complete journal in place, and a replacement visible before a failed directory fsync is treated as *installed/visible but durability indeterminate* (never "proven durable", never a silently missing/ambiguous canonical journal). The synthetic suite additionally injects faults at each modeled atomic-write phase plus every finalize boundary and service-policy scenario, asserting the normal path stays locked, the original device/inode stays at production, the candidate stays retained, terminal flags stay coherent, and each retry either reaches a proven-reconcilable `ABANDONED` or a specifically-justified sanitized manual recovery. Sanitized errors contain no token, secret, response body, or absolute production path.

**Command / approval.** `orchestrate_sqlite_production_cutover.py --rollback-finalize` **requires `--apply`**, the full production approval contract (`--confirm-production-cutover`, exact MID / path / `--expected-main-sha`, `--expected-production-fingerprint` **bound to the rollback proof's original/restored fingerprint** — an arbitrary non-empty value can never pass — actual repository HEAD match, `--approve-swap`), and is mutually exclusive with `--abort-before-swap`, `--rollback-before-writers`, and any non-default `--stage`. On a valid rolled-back journal it executes/reconciles; on a **coherent** already-`ABANDONED` journal it returns a safe idempotent "already finalized" status; on `COMPLETED`, an in-progress cutover, a post-PoNR journal, or a **mixed** terminal record it refuses / requires manual reconciliation. Status output distinguishes `ABANDONED` from success and states that operations run against the restored original.

**Evidence preserved.** Finalize deletes nothing: the compacted candidate, retained database, backup, journal, and incident evidence all remain. No cleanup policy lives in PR-C.

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
- `tests/test_sqlite_cutover_rollback_finalize.py` — PR-C terminal ABANDONED, structured rollback proof, lockout, crash-safe finalize + failure injection
