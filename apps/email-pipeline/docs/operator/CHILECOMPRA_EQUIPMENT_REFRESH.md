# ChileCompra equipment queue refresh (operator)

Automated operator step to refresh the equipment-first queue from the Mercado Público licitaciones API, publish the canonical dashboard CSV, and let the existing dashboard auto-mirror job push changes to Postgres.

## Required environment

- `CHILECOMPRA_API_TICKET` — Mercado Público API ticket. Read from the environment only; never commit or print in logs.

## Manual command

Dry-run (no API calls, no writes):

```bash
cd apps/email-pipeline
uv run origenlab auto-refresh-chilecompra-equipment --once
```

Apply (fetch, write API queue + audit, publish canonical dashboard CSV, update manifest):

```bash
cd apps/email-pipeline
uv run origenlab auto-refresh-chilecompra-equipment --once --apply
```

Operator shell wrapper:

```bash
apps/email-pipeline/scripts/operator/run_auto_refresh_chilecompra_equipment.sh
```

Useful flags:

- `--max-details 50` — conservative detail lookup cap (default)
- `--detail-sleep-seconds 3` — pause between detail lookups
- `--detail-cache-dir reports/out/active/current/chilecompra_detail_cache`
- `--no-publish` — build API queue only; skip canonical dashboard publish
- `--force` — bypass cooldown
- `--cooldown-seconds 7200` — default daytime cooldown between successful applies

## Recommended cron timing

Do **not** add cron entries in code — schedule externally when ready.

### Rollout checklist

Complete these steps **in order** before installing cron:

1. **Dry-run** — confirm command wiring without API calls or writes:
   ```bash
   cd apps/email-pipeline
   uv run origenlab auto-refresh-chilecompra-equipment --once
   ```
2. **Apply once with `--force`** — prove fetch, publish, and state update:
   ```bash
   cd apps/email-pipeline
   uv run origenlab auto-refresh-chilecompra-equipment --once --apply --force
   ```
3. **Check automation status** — verify ChileCompra section and mail/mirror health:
   ```bash
   cd apps/email-pipeline
   uv run origenlab operator-automation-status
   ```
4. **Mirror dashboard manually once** — confirm Postgres mirror after publish:
   ```bash
   cd apps/email-pipeline
   uv run origenlab auto-mirror-dashboard --once --apply --allow-non-scratch-postgres
   ```
5. **Only then install cron** — add the tracked wrapper entry below. `operator-automation-status` will report `install_chilecompra_cron` until the entry is present.

### Recommended crontab block

Every 2 hours during daytime (08:00–20:00), offset from dashboard mirror jobs:

```cron
12 8-20/2 * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/scripts/operator/run_auto_refresh_chilecompra_equipment.sh >> /home/rafael/dev/freelance/origenlab/apps/email-pipeline/reports/out/active/current/auto_chilecompra_cron.log 2>&1
```

`operator-automation-status` inspects crontab read-only and reports `chilecompra_entry_present` / `chilecompra_uses_tracked_script` under the `cron` section.

Suggested starting point for daytime operations:

- Every **2–4 hours** during business hours, **not** on the same minute as `auto-mirror-dashboard`.
- Example pattern: refresh ChileCompra at `:12`, mirror dashboard at `:35` every 2 hours.

Keep `max-details` conservative (50 or lower) to respect API quotas.

## Quota caution

- Summary list + per-codigo detail lookups consume Mercado Público API quota.
- Detail cache under `reports/out/active/current/chilecompra_detail_cache/` reduces repeat lookups.
- Review `chilecompra_equipment_candidate_audit_YYYYMMDD.csv` when tuning `max-details`.

## Dashboard mirror relationship

With `--apply` and Postgres configured, this command direct-publishes the typed equipment read model to Postgres (`commercial.equipment_opportunity_source` / `commercial.equipment_opportunity`, exposed by `api.v_equipment_opportunity_current`).

It still publishes the dashboard CSV/manifest artifacts for audit and compatibility, but `auto-mirror-dashboard` / `mirror-dashboard --live` no longer reloads equipment from the CSV by default. Use `mirror-dashboard --live -- --include-equipment-opportunities` only for an explicit legacy/backfill CSV reload.

## Safety

- Review-only semantics: published rows use `review_required` / `mercado_publico_only`.
- **Never contact buyers outside Mercado Público** unless explicitly allowed and reviewed.
- No Gmail send, campaign send, purge, or contact approval in this workflow.
- State: `reports/out/active/current/chilecompra_equipment_auto_refresh_state.json`
- Lock: `reports/out/active/current/chilecompra_equipment_auto_refresh.lock`
