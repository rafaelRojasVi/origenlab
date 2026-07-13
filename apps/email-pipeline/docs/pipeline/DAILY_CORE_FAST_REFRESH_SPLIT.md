# Daily core vs fast refresh (future workflow split)

Status: design / runbook (not implemented)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-09

Related: [`DAILY_CORE.md`](DAILY_CORE.md) · [`POSTGRES_MIRROR_REFRESH.md`](POSTGRES_MIRROR_REFRESH.md) · [`RUNBOOK.md`](../RUNBOOK.md) · [`OPERATOR_COMMAND_SURFACE.md`](../OPERATOR_COMMAND_SURFACE.md) · mart features: [`EMAIL_MART_FEATURES_DESIGN.md`](EMAIL_MART_FEATURES_DESIGN.md)

This document records **timing evidence** from the Gmail ingest optimization series and defines how operator workflows should split once we add a lighter **fast refresh** path for per-email / near-real-time automation.

**Scope of this doc:** design and operator expectations only. It does **not** implement a new CLI, change `daily-core` semantics, or alter mart/mirror/send safety defaults.

---

## Timing evidence (daily-core `--apply`)

Measured on the production SQLite mailbox (~217k `emails` rows) after PRs #148–#152 (step timings, header preflight, batched Message-ID preflight, gmail deps in validation).

| Milestone | Total `daily-core --apply` | `gmail-ingest` | `build-mart -- --rebuild` | Notes |
|-----------|---------------------------|----------------|---------------------------|-------|
| Before Gmail optimization (PR #148 baseline) | **~1262s** (~21m) | **~812s** | ~421s | Per-UID full RFC822 for duplicates |
| After batched Message-ID preflight (#151) | **~444s** (~7m24s) | ~371s → improving | ~413s | Gmail still dominant |
| Latest post-optimization run | **~444s** (~7m24s) | **17.80s** | **395.66s** | Gmail no longer bottleneck |

**Mart rebuild detail (latest run):**

| Metric | Value |
|--------|-------|
| `build-mart -- --rebuild` wall time | 395.66s |
| `[timing] email_scan_seconds` (mart email scan stage) | 373.38s |
| Emails scanned | 217,100 |

**Conclusion:** daily-core was **mart-bound** on the legacy body scan (~368s). After feature-backed mart rebuild (~3s scan), ingest + missing-only feature refresh dominate typical runs. A future per-email automation path must still **not** call full `daily-core --apply` on every new message.

---

## Three workflow lanes (target model)

```
                    ┌─────────────────────────────────────┐
                    │  Gmail (new mail / Sent evidence)   │
                    └─────────────────┬───────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
          v                           v                           v
   daily-core --apply          operator-fast-refresh*      mirror-dashboard --apply
   (full SQLite truth)         (future: incremental)       (Postgres publish)
          │                           │                           │
          v                           v                           v
   SQLite + reports/out        SQLite recent deltas*        Postgres mirror
   full mart rebuild           no full mart rebuild*        dashboard/API reads
   safety + NDR review         recent operator views*       read-only UI
```

\* **Not implemented yet.** Names are provisional (`operator-fast-refresh`, `email-event-refresh`).

### 1. `daily-core` (canonical)

**Command today:** `uv run origenlab daily-core --apply`

**Purpose:** full **SQLite operational truth** reconciliation for scheduled operator runs (daily / post-send batch review).

**Includes (eight steps):**

1. `gmail-ingest` — INBOX + Sent
2. `build-email-mart-features --missing-only --apply` — insert feature rows for new emails only
3. `build-mart -- --rebuild --use-email-mart-features` — break-glass full mart rebuild from precomputed features
4. `build-commercial-intel` — incremental SQLite commercial tables
5. `refresh-safety` — anti-repeat / DNR export chain
6. `ndr-review` — read-only NDR batches (no apply)
7. `post-send-digest` — contacted-universe audit artifacts
8. `status` — READY / CAUTION / BLOCKED

`refresh-dashboard --apply --no-mirror` still uses the legacy email-body mart scan (step 2 = `build-mart -- --rebuild` only).

**Semantics (must remain):**

- Full SQLite truth refresh, including **full mart rebuild**.
- Safety exports and NDR review visibility.
- **Not** per-email automation.
- **Not** Postgres mirror (separate step).
- **Does not send**, purge, apply NDR suppressions, or run Alembic.

See [`DAILY_CORE.md`](DAILY_CORE.md) for the operator contract.

### 2. `operator-fast-refresh` / `email-event-refresh` (future)

**Status:** design target only — **no CLI exists yet.**

**Intended trigger:** new email arrives or is sent (webhook, poll, or manual “refresh since last run”), when full daily-core is too heavy and semantically too broad.

**Goals:**

| Goal | Fast refresh (future) | daily-core (today) |
|------|----------------------|-------------------|
| Gmail ingest scope | Changed / recent messages only | Full INBOX + Sent scan (with duplicate skip) |
| Mart | **Avoid** `build-mart -- --rebuild` | Always full rebuild |
| Commercial intel | TBD incremental path | `build-commercial-intel` |
| Safety / NDR / post-send digest | Out of scope or reduced | Full chain |
| Postgres mirror | **Never** inline | Never inline (same) |
| Send / purge / NDR apply | **Never** | Never |

**Operator surfaces to refresh (conceptual):**

- Recent warm cases / equipment opportunities visible in dashboard Today views
- Recent commercial deal edges tied to newly ingested threads
- Operator status hints that depend on freshness of **recent** mail, not full historical mart

**Explicit non-goals for fast refresh:**

- Replacing `daily-core` as the canonical truth reconciliation job
- Outbound send approval or campaign export gates
- Postgres schema migrations (Alembic)
- Full historical mart parity on every event

**Implemented (debounced auto-refresh):** `uv run origenlab auto-refresh-mail --once [--apply]` — see [`MAIL_AUTO_REFRESH.md`](MAIL_AUTO_REFRESH.md). Coalesces INBOX/Sent UID-count changes before running full `daily-core --apply`. Not a per-email fast path; use external cron/systemd with `--once` every few minutes.

**Implemented (debounced dashboard publish):** `uv run origenlab auto-mirror-dashboard --once [--apply --allow-non-scratch-postgres]` — see [`DASHBOARD_AUTO_MIRROR.md`](DASHBOARD_AUTO_MIRROR.md). Separate one-minute publishing evaluation (default 60s cooldown); never part of the 3-minute mail watcher.

**Open engineering questions (for a future PR series):**

- Precomputed per-email features (`email_mart_features`) — see [`EMAIL_MART_FEATURES_DESIGN.md`](EMAIL_MART_FEATURES_DESIGN.md) (implemented; daily-core uses feature-backed mart since PR #166)
- Incremental mart updates vs partial table refresh vs “recent window” materialized views in SQLite
- How to detect “changed” Gmail UIDs (since cursor, `SINCE`, or Message-ID delta)
- When to **escalate** from fast refresh back to `daily-core --apply` (drift, missed UIDs, mart checksum failure)
- Whether fast refresh writes a separate run manifest for operator visibility

### 3. `mirror-dashboard` (separate publish step — unchanged)

**Command:** `uv run origenlab mirror-dashboard --apply` (or `--live --apply` for dashboard-facing views)

**Purpose:** publish refreshed SQLite-side state to the **Postgres read-only mirror** for reporting and the React dashboard.

**Contract:**

- Runs **after** SQLite truth is updated (`daily-core` and/or future fast refresh).
- Dashboard and API mirror routes remain **read-only** — not send approval.
- Alembic remains a separate, explicit operator action (`mirror-dashboard --alembic --apply`).

See [`POSTGRES_MIRROR_REFRESH.md`](POSTGRES_MIRROR_REFRESH.md).

---

## Current limitation: `--since-days` does not shorten mart

`daily-core --apply --since-days N` (and `refresh-dashboard --apply --since-days N`) passes `--since-days` to **`gmail-ingest` only** via the refresh wrapper.

It does **not** change step 2: mart rebuild is still hardcoded as:

```text
build-mart -- --rebuild
```

So even with a narrow Gmail window, **full mart rebuild and full email scan (~217k rows)** still run. Operators should not expect `--since-days` to materially reduce daily-core duration until a future incremental mart path exists.

---

## Recommended operator sequencing (today)

```bash
cd apps/email-pipeline

# 1. Full SQLite truth (scheduled; ~7m+ with current mart scan)
uv run origenlab daily-core --apply

# 2. Optional Postgres publish (separate; requires Postgres URL)
uv run origenlab mirror-dashboard --live --apply
```

Do **not** substitute mirror success or dashboard READY/LISTO for send approval. See [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md).

---

## What this doc does not do

- Does **not** add `operator-fast-refresh` or `email-event-refresh` CLI commands
- Does **not** change `daily-core`, `refresh-dashboard`, `build-mart`, or `mirror-dashboard` behavior
- Does **not** define send, purge, NDR apply, or Alembic procedures

Implementation tracking should start with a small PR that adds incremental mart design notes or a spike behind a feature flag — not by narrowing `daily-core` semantics.
