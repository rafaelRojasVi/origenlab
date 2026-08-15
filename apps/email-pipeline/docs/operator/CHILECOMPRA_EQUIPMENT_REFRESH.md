# ChileCompra equipment queue refresh (operator)

Automated operator step to refresh the equipment-first queue from the Mercado Público licitaciones API, publish the canonical dashboard CSV/audit artifacts, and direct-publish the typed equipment read model to Postgres when `--apply` runs with Postgres configured.

**PR5B note:** The acquisition snapshot parsers in
[`COMMERCIAL_PROCUREMENT_ACQUISITION_PR5B.md`](../audits/COMMERCIAL_PROCUREMENT_ACQUISITION_PR5B.md)
do **not** change this operational refresh path. Auto-refresh is not routed
through PR5B.

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
- `--force` — bypass cadence / cooldown gate
- `--cooldown-seconds 7200` — **nominal** scheduled-slot interval used when computing the next canonical slot after a successful apply (not a hard start-to-start floor)

## Cadence model (scheduled slots)

Cadence is **slot-based**; queue **freshness** remains **completion-based**.

| Concern | Field / rule |
|--------|----------------|
| Freshness | `last_successful_refresh_at` = finish time of the last successful apply |
| Cadence anchor | Successful starts near a canonical slot snap to that slot (`cadence_anchor_kind=scheduled_slot`) |
| Snap tolerance | Starts within **300 seconds** after a slot still snap to that slot; beyond that use wall-clock |
| Off-slot / manual | Anchor is the actual start (`cadence_anchor_kind=wall_clock`); no false snap |
| Next due | `next_recommended_run_at` = first canonical slot at or after `max(anchor + cooldown, finish)` |
| Spacing note | Because of the 300s snap, actual start-to-start spacing may be **up to five minutes shorter** than `cooldown_seconds` |

Canonical slots (`America/Santiago`):

- Minute **:12**
- Hours **08, 10, 12, 14, 16, 18, 20**

Examples (cooldown = 7200s):

- Start `08:12:03`, finish `08:14` → next **`10:12`** (same day)
- Start exactly `08:17:00` (five minutes late) → still snaps to `08:12` → next **`10:12`**
- Start `08:17:01` (just beyond tolerance) → `wall_clock` → next after `10:17` → **`12:12`**
- Start `20:12`, finish `20:14` → next day **`08:12`**
- Long run finishing `10:30` after an `08:12` slot → next **`12:12`**
- Off-slot manual success at `09:00` → next slot at or after `11:00` → **`12:12`**

`--force` still bypasses the gate. Cooldown skips and failures do **not** advance successful cadence anchors. `operator-automation-status` uses stored `next_recommended_run_at` for `next_run_due` / `chilecompra_refresh_due` — between valid slots and overnight this stays healthy; a missed scheduled slot still becomes attention.

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
4. **Mirror dashboard manually once** — confirm the non-equipment dashboard mirror sections after the equipment publish:
   ```bash
   cd apps/email-pipeline
   uv run origenlab auto-mirror-dashboard --once --apply --allow-non-scratch-postgres
   ```
5. **Only then install cron** — add the tracked wrapper entry below. `operator-automation-status` will report `install_chilecompra_cron` until the entry is present.

### Recommended crontab block

Every 2 hours during daytime (08:00–20:00 Santiago), offset from dashboard mirror jobs.
The tracked cron line is unchanged; the in-process gate now aligns to these slots so a finish a few minutes after `:12` does **not** skip the next `:12` tick.

```cron
12 8-20/2 * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/scripts/operator/run_auto_refresh_chilecompra_equipment.sh >> /home/rafael/dev/freelance/origenlab/apps/email-pipeline/reports/out/active/current/auto_chilecompra_cron.log 2>&1
```

`operator-automation-status` inspects crontab read-only and reports `chilecompra_entry_present` / `chilecompra_uses_tracked_script` under the `cron` section.

Suggested starting point for daytime operations:

- Hit each canonical slot at **:12** during business hours, **not** on the same minute as `auto-mirror-dashboard`.
- Example pattern: refresh ChileCompra at `:12`, mirror dashboard at `:35` every 2 hours.
- Prefer host timezone `America/Santiago` (or an equivalent cron TZ) so the tracked line matches the in-process schedule.

Keep `max-details` conservative (50 or lower) to respect API quotas.

## Quota caution

- Summary list + per-codigo detail lookups consume Mercado Público API quota.
- Detail cache under `reports/out/active/current/chilecompra_detail_cache/` reduces repeat lookups.
- Review `chilecompra_equipment_candidate_audit_YYYYMMDD.csv` when tuning `max-details`.

## Institution-prospect publication (opt-in, not yet activated)

`--publish-institution-prospects` / `--no-publish-institution-prospects` (default: **false**) publishes the institution-prospect read model (`commercial_procurement_institution_prospects`) from this exact run's detail cache and manifest into `reports/out/active/current/institution_prospects/`, which the W1 API (`apps/api`, `/operator/procurement/*`) reads by default.

```bash
cd apps/email-pipeline
uv run origenlab auto-refresh-chilecompra-equipment --once --apply --publish-institution-prospects
```

Key properties:

- **Reuses**, never re-derives: the same `detail_cache_dir` and this run's freshly-written equipment manifest are passed straight through. No second ChileCompra API call, no ANEXO acquisition (`enable_annex_opportunity_evidence` stays `false`).
- **Staged and validated before promotion**: the bundle is built into a sibling staging directory, validated through the same W1 `read_model.load_published_read_model()` loader the API uses, and only then atomically promoted into the canonical directory. A build or validation failure leaves the prior valid bundle completely untouched.
- **Decoupled from the equipment refresh's own success**: a failed institution-prospect publication never rolls back or blocks the equipment queue publication that already succeeded in the same run. It surfaces as `institution_prospect_result=failed` in state/output and the command returns exit code `3` (distinct from `0`=success, `1`=build failed, `2`=ticket missing) so it is attention-worthy without masquerading as a full refresh failure.
- **State fields** (`reports/out/active/current/chilecompra_equipment_auto_refresh_state.json`): `institution_prospect_result` (`disabled` | `applied` | `failed`), `last_successful_institution_prospect_publish_at`, `institution_prospect_contract_version`, `institution_prospect_as_of_utc`, `institution_prospect_profile_count`, `institution_prospect_current_opportunity_count`. Old state files without these fields still load (all default to `None`).
- **`operator-automation-status`** exposes a read-only `institution_prospect` section (bundle existence/validity, contract version, `as_of_utc`, age, `stale` past 48h — matching the API's own threshold, last publish result). A missing bundle before this flag is ever enabled is reported plainly and never degrades the rest of the automation-status verdict; a *requested* publication that failed does escalate to `attention`.

**Not yet activated**: the tracked wrapper (`scripts/operator/run_auto_refresh_chilecompra_equipment.sh`) does **not** pass this flag, so the next scheduled cron tick does not pick up this extra work merely because this code has merged. Activation is a separate, deliberate step once real production-cadence runtime/memory behavior has been observed with the flag on.

## Dashboard mirror relationship

With `--apply` and Postgres configured, this command direct-publishes the typed equipment read model to Postgres (`commercial.equipment_opportunity_source` / `commercial.equipment_opportunity`, exposed by `api.v_equipment_opportunity_current`).

It still publishes the dashboard CSV/manifest artifacts for audit and compatibility, but `auto-mirror-dashboard` / `mirror-dashboard --live` no longer reloads equipment from the CSV by default. Use `mirror-dashboard --live -- --include-equipment-opportunities` only for an explicit legacy/backfill CSV reload.

## Safety

- Review-only semantics: published rows use `review_required` / `mercado_publico_only`.
- **Never contact buyers outside Mercado Público** unless explicitly allowed and reviewed.
- No Gmail send, campaign send, purge, or contact approval in this workflow.
- State: `reports/out/active/current/chilecompra_equipment_auto_refresh_state.json`
- Lock: `reports/out/active/current/chilecompra_equipment_auto_refresh.lock`
