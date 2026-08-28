# Post-send safe loop

Status: canonical (operator procedure)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-11

**Purpose:** Refresh operational truth after **new outbound mail**, **NDR/bounces**, or **suppression changes**. Use this loop so SQLite safety sidecars, exclusion CSVs, digest, and Postgres mirror match Gmail evidence.

**Not enough alone:** mirror-only refresh when Gmail changed — ingest + NDR review + safety exports must run first.

Related: [`SCHEMA_CLASSIFICATION_MODEL.md`](SCHEMA_CLASSIFICATION_MODEL.md) · [`SCRIPT_MAP.md`](../SCRIPT_MAP.md) · [`OPERATOR_COMMAND_SURFACE.md`](../OPERATOR_COMMAND_SURFACE.md)

**Preferred CLI (Phase 6C):** where a step maps to a unified subcommand, use `uv run python -m origenlab_email_pipeline.cli <subcommand>` first; script paths below are **advanced/manual** fallbacks (same behavior).

---

## Golden rule

When **Sent mail or INBOX NDRs changed**, run the **full loop** below (at least through safety memory + contacted audit + mirror). **`RUN_GMAIL_INGEST=0` mirror-only** is for presentation refresh only, not post-campaign safety.

**Send gates:** `contact_email_suppression`, `contact_domain_suppression`, `outreach_contact_state`, export gates — **not** `lead_research_prospect.classification`. **Dashboard LISTO / mirror_ok is not send approval.**

---

## Steps (from `apps/email-pipeline/`)

| # | Step | Command (preferred · advanced fallback) |
|---|------|----------------------------------------|
| 1 | **Gmail ingest (read-only IMAP)** | `uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py --folder INBOX --since-days N --skip-duplicate-message-id` then same for `[Gmail]/Enviados`. No `--replace-source`. |
| 2 | **NDR review queue (read-only)** | **`uv run origenlab ndr-review -- --since-days N`** · advanced: `scripts/qa/build_ndr_review_queue.py --since-days N` |
| 3 | **Review batches + dry-run plan** | Read `ndr_review_queue_<date>/NDR_REVIEW_SUMMARY.md`, batch CSVs, `apply_allowlist_batch_*.txt`; then **`uv run origenlab ndr-safe-auto-apply --batch A --dry-run --operator <name>`** |
| 4 | **Apply Batch A (guarded)** | After reviewing allowlist: **`uv run origenlab ndr-safe-auto-apply --batch A --apply --operator <name> --confirm-reviewed`** (runs targeted NDR apply + `refresh-safety` + rebuilds `ndr-review`). See [NDR apply rules](#ndr-apply-rules). |
| 5 | **Contacted universe** | `uv run python scripts/leads/audit_contacted_universe.py` |
| 6 | **Safety memory** | **`uv run python -m origenlab_email_pipeline.cli refresh-safety`** · advanced: `scripts/qa/refresh_outbound_safety_memory.py` |
| 7 | **Post-send digest** | **`uv run python -m origenlab_email_pipeline.cli post-send-digest -- --since-days N`** (**after** step 5) · advanced: `scripts/qa/build_post_send_digest.py` |
| 8 | **Mirror (Gmail ingest off)** | `GMAIL_SINCE_DAYS=0 RUN_GMAIL_INGEST=0 DASHBOARD_FAST=1 RUN_LEAD_RESEARCH_MIRROR=1 RUN_OUTBOUND_SIDECAR_MIRROR=1 bash scripts/ops/refresh_render_dashboard_once.sh` |
| 9 | **Prospectos drift (read-only)** | `uv run python scripts/qa/audit_prospectos_safety_drift.py` — drift ≠ send failure |
| 10 | **Operator status + verifiers** | **`uv run python -m origenlab_email_pipeline.cli status`** · advanced: `scripts/qa/operator_status.py`; check `/tmp/outbound_sidecar_mirror_verify.json`, `/tmp/lead_research_mirror_verify.json`, `/tmp/render_dashboard_mirror_verify.json` (`ok` / assertions passed) |

Adjust `N` (`--since-days`) to cover the campaign window (often `1`–`2`).

### Daily health (read-only, no mutations)

Run anytime for a single operator snapshot (does **not** replace the full loop after sends):

`uv run python -m origenlab_email_pipeline.cli daily-health -- --since-days 2`  
*Advanced:* `scripts/qa/run_daily_health_report.py --since-days 2`

Writes `reports/out/active/current/daily_health_report_<YYYY_MM_DD>/` with verdict **READY** | **REVIEW_NEEDED** | **BLOCKED**. Does not ingest Gmail, apply suppressions, or refresh mirrors.

---

## NDR apply rules

| Rule | Detail |
|------|--------|
| **Review queue first** | Run **`ndr-review`** to build batches A–E under `reports/out/active/current/ndr_review_queue_<date>/`. |
| **Dry-run first** | **`ndr-safe-auto-apply --batch A --dry-run`** previews Batch A allowlist and appends audit JSONL — **no** SQLite writes. |
| **Guarded Batch A apply (preferred)** | **`ndr-safe-auto-apply --batch A --apply --operator <name> --confirm-reviewed`** — requires operator review of `apply_allowlist_batch_a.txt`; exact email only; `bounce_no_such_user` only; max-apply cap; refuses high Batch E counts. Audit: `active/current/ndr_safe_auto_apply_audit.jsonl`. |
| **Batch B** | **Manual targeted fallback only** — `flag_ndr_bounces_from_contacto.py` with `--emails-file …/apply_allowlist_batch_b.txt` + `--only-code bounce_other` + `--apply` after separate operator approval. **Not** enabled in `ndr-safe-auto-apply --apply`. |
| **Batches C / D / E** | **Hold / review only** — never auto-apply. |
| **Targeted apply (advanced/manual)** | [`flag_ndr_bounces_from_contacto.py`](../../scripts/tools/flag_ndr_bounces_from_contacto.py) with `--emails-file` + `--only-code` + `--apply`. Allowlist emails **must** appear in current NDR scan evidence or apply is **refused**. |
| **Broad `--apply` (break-glass)** | `--apply` **without** `--emails-file` / `--only-code` upserts **all** scan matches — **high risk**; operator review required. Do not use in routine post-send loops. |
| **Exact-email only** | NDR apply writes `contact_email_suppression` per address — **not** domain suppression. |
| **Delay DSNs** | Subjects with `Notification (Delay)` / `(delay)` are **skipped** — wait for final failure NDR. |
| **No cron auto-apply** | `ndr-safe-auto-apply --apply` is **manual/operator-invoked only** — not scheduled. |

Preferred example (after reviewing `ndr_review_queue_*` allowlist):

```bash
uv run origenlab ndr-safe-auto-apply --batch A --dry-run --operator rafael
uv run origenlab ndr-safe-auto-apply --batch A --apply --operator rafael --confirm-reviewed
```

Advanced/manual fallback (Batch A or B, or custom allowlist):

```bash
uv run python scripts/tools/flag_ndr_bounces_from_contacto.py \
  --since-days 1 \
  --emails-file reports/out/active/current/ndr_review_queue_2026_06_11/apply_allowlist_batch_a.txt \
  --only-code bounce_no_such_user \
  --apply
```

Allowlist files under `reports/in/` are **gitignored** — do not commit. Queue allowlists under `ndr_review_queue_*` are operator artifacts in `active/current/`.

---

## Historical orchestrator (removed Phase 5A)

The **2026-06-01 one-off** shell drivers `run_post_send_2026_06_01_refresh.sh` and `run_manual_outreach_2026_06_01_post_send_refresh.sh` were **removed** in Phase 5A. The dated QA scripts `build_manual_outreach_2026_06_01_digest.py` and `apply_manual_outreach_2026_06_01_corrections.py` (and the frozen recipient registry module) were **removed** in Phase 5K — use **`cli post-send-digest`** / **`build_post_send_digest.py`** (step 7 below) instead. Those removed shells ran **broad NDR `--apply`** in some steps — do not recreate that pattern. For new waves, follow **this doc** and use targeted allowlists.

---

## What this loop does not do

- Send mail or create Gmail drafts  
- Import `reports/in/manual_reviews/` institution packs without explicit approval  
- Create `institution_alias` tables or domain-wide NDR blocks  
