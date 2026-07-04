# Operator automation cron (two-loop architecture)

Status: canonical (operator contract)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-10

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

| Loop | Cadence | Wrapper script | Log file |
|------|---------|----------------|----------|
| Gmail → SQLite (`auto-refresh-mail`) | ~3 minutes | `scripts/operator/run_auto_refresh_mail.sh` | `reports/out/active/current/auto_refresh_cron.log` |
| SQLite → Postgres/dashboard (`auto-mirror-dashboard`) | ~15 minutes | `scripts/operator/run_auto_mirror_dashboard.sh` | `reports/out/active/current/auto_mirror_cron.log` |

Do **not** call `mirror-dashboard` from the mail watcher cron. Keep the loops separate.

---

## Recommended crontab lines

From `apps/email-pipeline/` (adjust home paths if your checkout differs):

```cron
*/3 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/scripts/operator/run_auto_refresh_mail.sh >> /home/rafael/dev/freelance/origenlab/apps/email-pipeline/reports/out/active/current/auto_refresh_cron.log 2>&1
*/15 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/scripts/operator/run_auto_mirror_dashboard.sh >> /home/rafael/dev/freelance/origenlab/apps/email-pipeline/reports/out/active/current/auto_mirror_cron.log 2>&1
```

**Do not store secrets in crontab.** Credentials belong in env files loaded by the operator CLI, not in cron lines.

### Wrapper env overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORIGENLAB_UV_BIN` | `/home/rafael/.local/bin/uv` | `uv` binary path |
| `ORIGENLAB_OPERATOR_NAME` | `rafael` | `--operator` for mirror publish audit |

---

## Verify

```bash
crontab -l
cd apps/email-pipeline
uv run origenlab operator-automation-status
tail -80 reports/out/active/current/auto_refresh_cron.log
tail -80 reports/out/active/current/auto_mirror_cron.log
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

Cron runs only while WSL (and the host system providing the cron daemon) is active. If the machine sleeps or WSL is stopped, automation pauses until the next scheduled tick after restart.

---

## Legacy runtime wrapper (migrate away)

Older setups may reference:

`reports/out/active/current/bin/run_auto_mirror_dashboard.sh`

That path is generated/runtime-ish. Prefer the tracked script:

`apps/email-pipeline/scripts/operator/run_auto_mirror_dashboard.sh`

`operator-automation-status` reports `migrate_cron_to_tracked_scripts` when the legacy wrapper is still present.
