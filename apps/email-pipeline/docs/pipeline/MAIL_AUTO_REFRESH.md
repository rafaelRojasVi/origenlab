# Mailbox auto-refresh (debounced)

Status: canonical (operator contract)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-10

Related: [`DAILY_CORE.md`](DAILY_CORE.md) · [`DAILY_CORE_FAST_REFRESH_SPLIT.md`](DAILY_CORE_FAST_REFRESH_SPLIT.md) · [`OPERATOR_COMMAND_SURFACE.md`](../OPERATOR_COMMAND_SURFACE.md)

Debounced mailbox change detector that may run **`daily-core --apply`** after INBOX/Sent activity settles. Designed for a future cron/systemd schedule (every few minutes) without triggering one full refresh per email during large sent batches.

---

## Commands

From `apps/email-pipeline/`:

```bash
# Dry-run / status (default) — probes mailbox, updates state, prints counters
uv run --group gmail origenlab auto-refresh-mail --once

# Run daily-core when quiet + cooldown gates pass
uv run --group gmail origenlab auto-refresh-mail --once --apply
```

Gmail IMAP probe imports `google-auth` (optional **`gmail`** dependency group). Cron wrapper `scripts/operator/run_auto_refresh_mail.sh` uses `uv run --group gmail`; manual runs should too unless your venv already includes the group.

If cron logs `ModuleNotFoundError: No module named 'google'`:

```bash
cd apps/email-pipeline
uv sync --group dev --group data-tools --group postgres --group lab --group gmail --frozen
```

`--daemon` is **not implemented** yet. Use an external scheduler that invokes `--once` on an interval.

---

## Behavior

1. **Probe** — read-only Gmail IMAP UID counts for `INBOX` and `[Gmail]/Enviados` (reuses `ingest/gmail_imap.py` helpers).
2. **State** — compare to `reports/out/active/current/mail_auto_refresh_state.json` (`last_seen_inbox_total`, `last_seen_sent_total`, max UIDs, debounce timestamps).
3. **Debounce** — on change, mark `dirty` and wait **180s** quiet window (`--quiet-seconds`) before eligible to run.
4. **Cooldown** — after a successful refresh, block another run for **600s** (`--cooldown-seconds`).
5. **Large sent batch** — if `sent_delta > 50`, use **900s** quiet window (`--large-sent-delta`, `--large-sent-quiet-seconds`).
6. **Lock** — `reports/out/active/current/auto_refresh.lock` prevents concurrent runs; stale locks (>2h, dead PID) are cleared with a warning.
7. **Pause** — touch `reports/out/active/current/auto_refresh_paused` to disable auto-refresh cleanly.
8. **Apply** — when gates pass and `--apply` is set, runs `uv run origenlab daily-core --apply` (includes Gmail ingest — **not** `--skip-ingest`).

Stable stdout counters: `mail_auto_refresh`, `apply=`, `changed=`, `dirty=`, `reason=`, `inbox_total=`, `sent_total=`, deltas, `should_run=`, `ran_daily_core=`, `daily_core_returncode=`.

---

## Recommended schedule (not installed by repo)

Example **systemd timer** or **cron** every **2–5 minutes** on the operator host:

```bash
# cron example (every 3 minutes) — use tracked wrapper (includes --group gmail)
*/3 * * * * /path/to/apps/email-pipeline/scripts/operator/run_auto_refresh_mail.sh >> /var/log/origenlab-auto-refresh.log 2>&1
```

Pause during manual maintenance:

```bash
touch reports/out/active/current/auto_refresh_paused
# remove when done:
rm reports/out/active/current/auto_refresh_paused
```

---

## Safety boundaries

- **Dry-run default** — without `--apply`, never runs `daily-core`.
- **No send** — does not send mail, purge data, or apply NDR suppressions.
- **No Postgres mirror** — `daily-core` never includes mirror; this command does not add mirror.
- **No dashboard writes** — state/lock files under `reports/out/active/current/` only.
- **Lock** — concurrent invocations exit with `reason=already_running`.

---

## Timing context

After PR #166, `daily-core --apply --skip-ingest` is ~27s on production-scale data (feature mart path). Auto-refresh runs **full** daily-core (with Gmail ingest) when mail actually changed and debounce gates pass.

See [`DAILY_CORE_FAST_REFRESH_SPLIT.md`](DAILY_CORE_FAST_REFRESH_SPLIT.md) for the three-lane model (auto-refresh vs daily-core vs future fast path).
