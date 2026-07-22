# Local systemd user services for OrigenLab API

Status: canonical (local operator install)  
Owner: api-maintainers  
Last reviewed: 2026-07-13

Keep the local read-only operator API (`127.0.0.1:8001`) running via **systemd user units** (no root). Tracked templates live under [`deploy/systemd/user/`](../../../deploy/systemd/user/).

Related: mail/mirror cron [`OPERATOR_CRON.md`](../../email-pipeline/docs/pipeline/OPERATOR_CRON.md) · automation status source selection in [`DASHBOARD_AUTO_MIRROR.md`](../../email-pipeline/docs/pipeline/DASHBOARD_AUTO_MIRROR.md).

## Units

| Unit | Role |
|------|------|
| `origenlab-api.service` | `uv run --frozen uvicorn … --port 8001` (`Restart=always`; `[Unit]` StartLimitBurst=10 / 120s) |
| `origenlab-api-health.service` | `curl --fail` against `http://127.0.0.1:8001/health`; `[Unit]` `OnFailure=` → recover |
| `origenlab-api-health.timer` | Every **30 seconds** |
| `origenlab-api-recover.service` | `systemctl --user restart origenlab-api.service` on health failure (`[Unit]` StartLimitBurst=3 / 120s) |

`OnFailure=` and `StartLimitIntervalSec` / `StartLimitBurst` must live under **`[Unit]`**, not `[Service]`. Verify with:

```bash
systemd-analyze verify deploy/systemd/user/*.service deploy/systemd/user/*.timer
```

Paths use `%h` (user home) rather than committing a machine-specific home directory.

## Install

```bash
# 1) Confirm user systemd works
systemctl --user status

# 2) Inspect port 8001 — if a manual uvicorn already owns it, do NOT kill it.
ss -ltnp | grep 8001 || true

# 3) Install unit files
mkdir -p ~/.config/systemd/user
ln -sf "$HOME/dev/freelance/origenlab/deploy/systemd/user/origenlab-api.service" ~/.config/systemd/user/
ln -sf "$HOME/dev/freelance/origenlab/deploy/systemd/user/origenlab-api-health.service" ~/.config/systemd/user/
ln -sf "$HOME/dev/freelance/origenlab/deploy/systemd/user/origenlab-api-health.timer" ~/.config/systemd/user/
ln -sf "$HOME/dev/freelance/origenlab/deploy/systemd/user/origenlab-api-recover.service" ~/.config/systemd/user/

systemctl --user daemon-reload

# 4) Enable/start only when nothing else owns :8001
systemctl --user enable --now origenlab-api.service
systemctl --user enable --now origenlab-api-health.timer

# 5) Verify
systemctl --user status origenlab-api.service
systemctl --user status origenlab-api-health.timer
journalctl --user -u origenlab-api.service -n 40 --no-pager
curl -sS http://127.0.0.1:8001/health
```

If a **foreground / manual uvicorn** already listens on `:8001`, install the unit files and stop the manual process yourself before `enable --now`. Agents must not kill that process without confirmation.

## Optional lingering (user-approved only)

True background survival after logout typically needs:

```bash
loginctl enable-linger "$USER"
```

**Do not run this with sudo / without explicit operator approval.** Documented only.

## WSL / sleep caveat

WSL or Windows host sleep, and a stopped WSL VM, prevent true 24/7 local operation — cron and user systemd both pause until the environment wakes.

## SQLite cutover maintenance (boot auto-start)

During a SQLite production cutover, `STOP_READERS` **stops** `origenlab-api.service` and `origenlab-api-health.timer` and also **persistently disables** both (`systemctl --user disable`) so a WSL or user-manager restart cannot auto-start them and reopen production SQLite mid-maintenance.

- Runtime **masks** are not used (they do not survive WSL reboot the way operators need).
- `RESUME_SERVICES` may **start** the units for post-swap health while they remain **disabled** for boot.
- Exact pre-maintenance enablement is restored only at a terminal exit (`COMPLETED`, successful `abort_before_swap`, or `rollback_finalize` → `ABANDONED`).
- PR-D / the cutover orchestrator does **not** authorize a live cutover by itself; see [`SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md`](../../email-pipeline/docs/SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md).

Do not manually `systemctl --user enable` either unit during an in-progress cutover maintenance window.

## Safety

- API remains **GET-only** / read-only.
- Units must not send email, mutate Gmail, or write SQLite/Postgres.
- Optional `EnvironmentFile` for `apps/api/.env` (never commit secrets).
