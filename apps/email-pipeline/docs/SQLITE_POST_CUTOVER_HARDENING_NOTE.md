# Post-cutover hardening note (not PR-G)

**Status:** repository hardening after a successful production cutover.  
**Roadmap:** canonical SQLite cutover roadmap remains **A–F**. This work is **post-closure hardening — not PR-G**.

## Context

Maintenance ID `cutover20260722T233536Z` completed successfully on production. During that cutover two defects required operational workarounds:

1. **API readiness race** — `READONLY_SMOKE` treated `systemctl start` (`Type=simple`) as HTTP readiness and raced `/health` before the listener was up. A temporary systemd `ExecStartPost` readiness gate unblocked the operator path; the repository must not require that drop-in again.
2. **Post-swap companion restore** — pre-barrier WAL/SHM were absent (`present=false`). After swap, the read-only API created WAL/SHM under the write barrier. Smoke recorded exact identities in `post_swap_companions`, but writer-mode restore refused to chmod those inodes because they were not barrier-captured pre-swap identities and were still read-only. A one-time exact-inode reconciliation unblocked restore.

Neither workaround is part of this PR. Production journals, recovery copies, and systemd drop-ins are untouched by this change set.

## Fixes

| Defect | Fix |
|--------|-----|
| Smoke readiness | Bounded loopback `/health` wait (`wait_for_api_health_ready` / `wait_api_ready`) between API start and full smoke; fail closed on timeout, process exit, HTTP/JSON/`ok` failures; preserve ownership fail-safe |
| Companion restore | Record durable companion `target_mode` at smoke recapture; restore exact post-swap device/inode via `chmod_verified_inode`; legacy records without `target_mode` remain fail-closed |

## Safety retained

- Durable smoke ownership-before-start; stop only owned API
- Health timer stays stopped through smoke
- Rollback forbidden after `writer_resume_started=true`
- Pathname-only `chmod` remains prohibited
- July 19 MID remains permanently non-resumable
- Completed/abandoned journals stay terminal under existing rules
- Cleanup of retained original / fresh backup remains separately gated by soak review

## Testing

All destructive, permission, crash, sidecar, and readiness tests use temporary synthetic environments only. No production SQLite open, no production service mutation, no Gmail/Postgres changes.
