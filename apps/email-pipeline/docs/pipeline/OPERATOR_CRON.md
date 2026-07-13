# Operator automation cron (two-loop architecture)

Status: canonical (operator contract)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-07-13

Related: [`MAIL_AUTO_REFRESH.md`](MAIL_AUTO_REFRESH.md) · [`DASHBOARD_AUTO_MIRROR.md`](DASHBOARD_AUTO_MIRROR.md) · [`OPERATOR_COMMAND_SURFACE.md`](../OPERATOR_COMMAND_SURFACE.md)

Tracked wrapper scripts live under `scripts/operator/`. They are thin schedulers only — safety gates remain inside `origenlab auto-refresh-mail` and `origenlab auto-mirror-dashboard`.

**Dependency groups:** cron wrappers pin optional `uv` groups so a minimal venv sync does not miss imports:

| Wrapper | `uv run` group | Why |
|---------|----------------|-----|
| `run_auto_refresh_mail.sh` | `--group gmail` | IMAP probe imports `google-auth` |
| `run_auto_mirror_dashboard.sh` | `--group postgres` | mirror sync uses Alembic/psycopg |
| `run_auto_refresh_chilecompra_equipment.sh` | *(default only)* | ChileCompra refresh uses core deps |

If mail auto-refresh cron logs `ModuleNotFoundError: No module named 'google'`, resync with the gmail group:

```bash
cd apps/email-pipeline
uv sync --group dev --group data-tools --group postgres --group lab --group gmail --frozen
uv run --group gmail python -c "from google.auth.transport.requests import Request; print('gmail deps ok')"
```

---

## Two loops

| Loop | Cadence | Wrapper / command | Log file |
|------|---------|-------------------|----------|
| Gmail → SQLite (`auto-refresh-mail`) | ~3 minutes | `scripts/operator/run_auto_refresh_mail.sh` | `reports/out/active/current/auto_refresh_cron.log` |
| SQLite → Postgres/dashboard (`auto-mirror-dashboard`) | every minute | tracked wrapper **or** direct `uv run origenlab auto-mirror-dashboard … --cooldown-seconds 60` | `auto_mirror_cron.log` / `auto_mirror_cron.err.log` |

Ordered flow (not simultaneous):

1. Gmail / `daily-core` completes successfully
2. Mail state becomes clean (`dirty=false`, no pending)
3. Dashboard mirror evaluation can start within about one minute (default cooldown **60s**)

Locks and fingerprints make repeated one-minute evaluations cheap no-ops. Do **not** call `mirror-dashboard` from the mail watcher cron. Keep the loops separate.

---

## Recommended crontab lines

From `apps/email-pipeline/` (adjust home paths if your checkout differs):

```cron
*/3 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/scripts/operator/run_auto_refresh_mail.sh >> /home/rafael/dev/freelance/origenlab/apps/email-pipeline/reports/out/active/current/auto_refresh_cron.log 2>&1
* * * * * cd /home/rafael/dev/freelance/origenlab/apps/email-pipeline && /home/rafael/.local/bin/uv run origenlab auto-mirror-dashboard --once --apply --allow-non-scratch-postgres --cooldown-seconds 60 >/dev/null 2>> reports/out/active/current/auto_mirror_cron.err.log
```

**Do not store secrets in crontab.** Credentials belong in env files loaded by the operator CLI, not in cron lines.

### Wrapper env overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORIGENLAB_UV_BIN` | `/home/rafael/.local/bin/uv` | `uv` binary path |
| `ORIGENLAB_OPERATOR_NAME` | `rafael` | `--operator` for mirror publish audit |

The tracked mirror wrapper also defaults to `--cooldown-seconds 60`.

---

## Verify

```bash
crontab -l
cd apps/email-pipeline
uv run origenlab operator-automation-status
tail -80 reports/out/active/current/auto_refresh_cron.log
tail -80 reports/out/active/current/auto_mirror_cron.err.log
```

`operator-automation-status` inspects the user crontab read-only and flags missing entries, legacy runtime wrappers, or broken joined flags (e.g. `--apply--operator`).

Use `--skip-cron-inspection` when you only want manifest/mail/mirror state.

---

## Pause files

Create under `reports/out/active/current/` to stop a loop without editing crontab:

| File | Effect |
|------|--------|
| `auto_refresh_paused` | Skips `auto-refresh-mail --apply` |
| `dashboard_auto_mirror_paused` | Skips `auto-mirror-dashboard --apply` |

Remove the file to resume.

---

## WSL caveat

Cron runs only while WSL (and the host system providing the cron daemon) is active. If the machine sleeps or WSL is stopped, automation pauses until the next scheduled tick after restart. The same caveat applies to local systemd user services for `apps/api` — see [`apps/api/docs/LOCAL_SYSTEMD.md`](../../../api/docs/LOCAL_SYSTEMD.md).

---

## Legacy runtime wrapper (migrate away)

Older setups may reference:

`reports/out/active/current/bin/run_auto_mirror_dashboard.sh`

That path is generated/runtime-ish. Prefer the tracked script:

`apps/email-pipeline/scripts/operator/run_auto_mirror_dashboard.sh`

or the direct one-minute `uv run origenlab auto-mirror-dashboard …` line above.

`operator-automation-status` reports `migrate_cron_to_tracked_scripts` when the legacy wrapper is still present.
