# Consolidated mail worker: approved one-time cutover

Built in PR #679; **not activated or deployed**. The legacy Gmail cron still runs in
production. Do not suspend it, change secrets or trigger a rollout without owner approval.
No new migration is needed: the existing Procrastinate schema accepts the new task/queue.

## What changes

One Render background service supervises two independent processes:

| Process | Queue | Concurrency | Work |
|---|---|---|---|
| Triage | `triage` | 2 | Existing per-minute sweep and message readings |
| Capture | `capture` | 1 | Gmail capture, then Drive filing, every ten minutes |

`mail-worker` is the canonical command; the existing `triage-worker` command aliases it,
so the initial cutover needs no provider start-command change. Internal `triage-child`
uses the existing triage runner; it receives no Gmail/Drive credentials. The capture
process is spawned only when `ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED=true`.
A missing/false flag preserves triage-only behavior and needs no capture secrets.
The existing Gmail and Drive business flags are retained exactly; this change does
not grant consent, initialise a mailbox, send email or activate CRM rules.

Procrastinate deduplicates periods across rolling instances. A queueing lock bounds
waiting capture jobs to one; a verified worker connection holds a **session advisory
lock** around the whole cycle. No queue execution lock is attached, so an orphaned
`doing` job cannot permanently block later periods. Closing/cancelling the connection
releases the lock. Existing Gmail/Drive locks and unique writes remain in force.

Each child command has a five-minute bound. The complete cycle has a nine-minute
bound; transient failures retry inside that bound with 30/90-second waits (at most
three attempts). Auth/configuration failures do not retry immediately. Retries stay
inside the current job/session lock: moving it back to `todo` could collide with a
new periodic waiting job. The next ten-minute cycle safely resumes committed capture
work. No historical job is replayed, deleted or repaired automatically.

The supervisor stops both process groups if either child exits unexpectedly. SIGTERM
allows bounded shutdown, then kills/reaps descendants. This isolates processing slots,
**not CPU or memory**: both processes still share the current 0.5 CPU / 512 MB service.
Measure actual peak RSS, capture duration and triage latency during the approved first
release. A resource upgrade or separately pinned capture worker remains possible if
real workload measurements require it; neither was changed here.

A restricted connector compatibility guard handles Procrastinate 3.10's assumption
that duplicate-key DETAIL is visible. PostgreSQL RLS masks it. Only the known queueing
constraint is converted to the library's duplicate exception; other failures propagate.
No RLS policy/grant is weakened. Actual worker-role PostgreSQL tests reproduce it.

## Cutover sequence

1. Obtain approval for GitHub protection/configuration and four Render auto-deploy
   triggers OFF. Confirm release and backup automation flags OFF. Keep all services
   running. Merge only after final-head CI, independent review and explicit approval.
2. With the old cron still running, use main's read-only `plan` and `backup_only`.
   Complete the actual production-data/offline-key restore drill and verify managed
   recovery inventory. Neither mode requires the scheduler cutover; neither deploys.
3. Obtain approval to copy the cron's existing Gmail/Drive configuration to the worker.
   Use Render's **Save only** operation; do not print values or change OAuth scopes,
   mailbox state, contact rules or the existing Gmail/Drive enabled values. Database
   and Storage keys already present must be verified, not blindly overwritten.
   Initially keep `ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED=false`.
4. Approve the first controlled release and short capture interruption. In that window,
   suspend the legacy cron and confirm it is suspended and its current run has finished.
   Keep the API, dashboard and triage service running. Stage the new scheduler flag true
   with Save only. This is a scheduling handoff, not permission to enable business flags.
   Confirm no unintended deployment was triggered. The old deployed triage code does
   not schedule capture; a previously consolidated deployment requires additional care.
5. Enable the reviewed release gate with approval and dispatch the controlled release.
   Preflight requires the old cron suspended and all four independent deploy triggers
   OFF. Database backup/migration precede **worker → API → dashboard**. Every deploy
   POST includes the exact commitId. No cron deploy POST exists.
6. Postflight waits up to twenty minutes for candidate-SHA triage sweep and successful
   Gmail→Drive-cycle aggregate logs, a fresh mailbox cursor, worker heartbeat/scheduling,
   and no stale eligible evidence gaps; it rechecks three deployment SHAs and suspended
   cron. `drive_mode=paused` is acceptable only if filing was already intentionally paused;
   it proves no filing. Per-document refusals still require normal operator attention.
7. Observe the first cycles and shared-resource usage; perform the authenticated CRM/API
   smoke. Only then approve unattended releases and daily backup automation. Retain the
   suspended legacy cron for rollback initially; deletion requires a separate decision.

Render environment guidance: [Save only and secret configuration](https://render.com/docs/configure-environment-variables).
Provider environment: [RENDER_GIT_COMMIT](https://render.com/docs/environment-variables).

## Failure and recovery

| Failure | Result | Recovery with approval |
|---|---|---|
| Active legacy cron | Controller refuses before DDL/deploy | Complete the approved handoff; never bypass |
| Worker build fails after cron suspension | Old triage still runs; capture pauses | Reconcile provider commit; fix and retry pinned release, or restore the old scheduling arrangement |
| Capture configuration invalid | Capture child exits; supervisor fails both; Render restarts | Correct approved configuration; redeploy/reconcile, do not leave a partial service accepted |
| Gmail transient failure | No Drive call; bounded retry; next period resumes | Inspect sanitised class/cursor age; no mass replay |
| Drive transient failure | Captured mail remains committed; retry is idempotent | Verify existing per-document refusal codes; do not duplicate Drive objects manually |
| Worker/command killed mid-cycle | OS closes DB sessions; next period can run | Inspect orphan counts; no historical-job mutation is required for new cycles |
| Timeout/RSS exhaustion | Service/postflight fails, release incomplete | Reconcile running commit and capture cursor; approve capacity change if measured necessary |
| Main advances during release | Pinned POSTs cannot deploy newer cron code; controller stops stale release | Reconcile committed migrations and deployed prefix; release newest reviewed compatible SHA |
| Rollback to legacy scheduling | Never resume cron while consolidated capture still runs | Set scheduler OFF and deploy/reconcile a reviewed triage-only build first; verify capture stopped, then resume the unchanged legacy cron build |

Do not reverse schema automatically. Keep running/previous code schema-compatible.
The synthetic restore tests do not replace a real production-data recovery drill.
