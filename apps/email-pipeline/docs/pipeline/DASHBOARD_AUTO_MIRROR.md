# Dashboard auto-mirror (debounced publishing)

Status: canonical (operator contract)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-07-13

Related: [`MAIL_AUTO_REFRESH.md`](MAIL_AUTO_REFRESH.md) · [`DAILY_CORE.md`](DAILY_CORE.md) · [`POSTGRES_MIRROR_REFRESH.md`](POSTGRES_MIRROR_REFRESH.md) · [`DAILY_CORE_FAST_REFRESH_SPLIT.md`](DAILY_CORE_FAST_REFRESH_SPLIT.md) · [`OPERATOR_CRON.md`](OPERATOR_CRON.md)

**See also:** `uv run origenlab operator-automation-status` — read-only health for both automation loops (mail + mirror + user crontab). Also visible on dashboard Today via `GET /operator/automation-status` (apps/api; cron not inspected by API). Cron wrappers: [`OPERATOR_CRON.md`](OPERATOR_CRON.md).

Mail refresh and dashboard mirror are **separate ordered loops**. After Gmail/`daily-core` completes successfully and mail state is clean, the one-minute mirror evaluation can start within about one minute (subject to the 60-second cooldown). Do **not** embed the mirror inside `daily-core`.

---

## Commands

From `apps/email-pipeline/`:

```bash
# Dry-run / status (default)
uv run origenlab auto-mirror-dashboard --once

# Publish when all gates pass
uv run origenlab auto-mirror-dashboard --once --apply \
  --allow-non-scratch-postgres
```

Optional flags: `--cooldown-seconds 60` (default), `--operator rafael`, `--reason "…"`. Custom cooldown values remain supported.

`--daemon` is **not implemented** — use an external scheduler with `--once`.

---

## Architecture (two loops)

| Loop | Cadence | Purpose |
|------|---------|---------|
| `auto-refresh-mail` | ~3 minutes | Gmail → SQLite via `daily-core` when mail changes |
| `auto-mirror-dashboard` | every minute | SQLite → Postgres/dashboard via `mirror-dashboard --live --apply` |

Repeated one-minute evaluations are cheap when locks/fingerprints/cooldown gates no-op (`already_mirrored`, `cooldown`, lock held). Overlap prevention (mirror lock) still applies when a mirror takes longer than one minute.

Do **not** put `mirror-dashboard` inside the mail watcher cron.

---

## Gates (all must pass before mirror)

1. `daily_core_run_manifest.json` exists with `status=success`, `returncode=0`, `generated_at_utc` set
2. `mail_auto_refresh_state.json` exists with `dirty=false` and all `pending_*` null
3. `auto_refresh.lock` not held by a live process (mail refresh not running)
4. `dashboard_auto_mirror.lock` not held by a live process
5. Latest daily-core timestamp / dashboard-input fingerprint not already mirrored
6. Cooldown elapsed since last successful mirror (default **60s**)
7. `--apply` present to actually run mirror
8. `--allow-non-scratch-postgres` present with `--apply` (explicit non-scratch consent)

Dry-run: when gates 1–6 pass but `--apply` is omitted → `should_run=true`, `ran_mirror=false`, `reason=dry_run`.

---

## What apply runs

Equivalent to:

```bash
uv run origenlab mirror-dashboard --live --apply \
  --operator rafael \
  --reason "Automated dashboard mirror after successful daily-core" \
  -- --allow-non-scratch-postgres
```

- **No Alembic** in this workflow
- **No Gmail** ingest
- **No send / purge / NDR apply**
- **No dashboard UI writes** (Postgres mirror sync only)
- **`--include-operator-snapshots`** (part of `--live`): publishes read-only dashboard evidence to `ops.pipeline_kv`:
  - `dashboard_gmail_interaction_audit_v1` — compact per-domain Gmail/SQLite interaction counts (subjects only; no bodies)
  - `operator_automation_status_snapshot_v1` — redacted automation health from local `active/current` (no filesystem paths)

These snapshots are **read-only dashboard evidence**. They do **not** authorize sending, export, or outreach.

**Source selection for `GET /operator/automation-status`:**

- Local **SQLite** API (`ORIGENLAB_API_BACKEND=sqlite`) reads live `filesystem_active_current` even when Postgres has a published automation snapshot (mirror sync metadata may still come from Postgres).
- Production **Postgres** API prefers the published Postgres automation snapshot, with filesystem fallback when unavailable.

Postgres mirror and dashboard LISTO are **not send approval**.

---

## State / lock / pause files

Under `reports/out/active/current/`:

| File | Purpose |
|------|---------|
| `dashboard_auto_mirror_state.json` | Last mirror time, mirrored daily-core timestamp |
| `dashboard_auto_mirror.lock` | Prevents concurrent mirror runs |
| `dashboard_auto_mirror_paused` | Pause mirror automation |
| `auto_refresh_paused` | Also pauses mirror (shared mail pause) |

Pause mirror:

```bash
touch reports/out/active/current/dashboard_auto_mirror_paused
rm reports/out/active/current/dashboard_auto_mirror_paused   # resume
```

---

## Recommended cron (not installed by repo)

```bash
# every minute — gates make most ticks cheap no-ops
* * * * * cd /path/to/apps/email-pipeline && uv run origenlab auto-mirror-dashboard --once --apply --allow-non-scratch-postgres --cooldown-seconds 60 >/dev/null 2>> reports/out/active/current/auto_mirror_cron.err.log
```

---

## Troubleshooting

- **API smoke / bad Postgres env** — mirror sync and API health are separate; fix `ORIGENLAB_POSTGRES_URL` / `ORIGENLAB_CLOUD_POSTGRES_URL` before expecting `/mirror/*` routes to work.
- **`allow_non_scratch_required`** — pass `--allow-non-scratch-postgres` explicitly with `--apply`.
- **`mail_dirty` / `mail_pending`** — wait for mail auto-refresh to finish debouncing and complete `daily-core`.
- **`already_mirrored`** — current daily-core manifest timestamp / dashboard-input fingerprint was already published.
- **Stale automation on local SQLite API** — ensure `ORIGENLAB_API_BACKEND=sqlite` so status comes from live `active/current`, not a prior Postgres snapshot.

Stable stdout counters: `dashboard_auto_mirror`, `apply=`, `reason=`, `daily_core_status=`, `mail_dirty=`, `should_run=`, `ran_mirror=`, `allow_non_scratch_postgres=`, etc.
