# Outbound operator checklist (canonical lanes)

Status: canonical companion to [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md) and [`RUNBOOK.md`](../RUNBOOK.md#m-eprun-cold-export-gate).  
Use this for **repeatable** cold-outreach batch prep — not as a substitute for human judgment.

## Before you generate a batch

1. **Preflight when freshness is uncertain** — run [`check_outbound_readiness.py`](../../scripts/qa/check_outbound_readiness.py) (same Gmail/Sent defaults as [`outbound_core.py`](../../src/origenlab_email_pipeline/outbound_core.py)). Prefer `--json-out` to keep a record. Exit `1` = `not_ready` → fix DB/ingest/sidecars before exporting.
2. **Confirm the SQLite path** — same DB the mart/leads stack expects (`ORIGENLAB_SQLITE_PATH` or your explicit `--db`).
3. **Remember:** passing readiness + gate checks means “not auto-blocked by policy,” not “validated buyer” or “safe to bulk send.”

**After bulk NDR/contacted refreshes:** run read-only [`audit_prospectos_safety_drift.py`](../../scripts/qa/audit_prospectos_safety_drift.py) to measure raw Prospectos vs operational sidecar drift ([`SCHEMA_CLASSIFICATION_MODEL.md`](SCHEMA_CLASSIFICATION_MODEL.md)); report-only by default.

## Archive lane (warm revival)

| Step | Command (from `apps/email-pipeline/`) |
|------|----------------------------------------|
| Audit only (default) | `uv run python scripts/leads/build_archive_send_batch.py --out-dir <dir>` |
| Full batch | Same + `--build-batch` |

### What to open after the run

| Role | Artifact | Why |
|------|----------|-----|
| **Human review (send)** | `archive_outreach_send_ready.csv` | Rows intended as send candidates after pipeline steps. |
| **Human review (queue)** | `archive_outreach_review_required.csv` | Needs operator/commercial judgment before treating as send. |
| **Trust / debug** | `archive_outreach_build_summary.json` → nested **`outbound_run`** | Mailbox, Sent folders, sqlite path, counts, artifact paths, timestamp (`schema_version` **1**). |
| **Audit trail** | `archive_outreach_audit.csv`, `archive_outreach_shortlist_gate_audit.csv`, commercial precheck CSV | Why rows were included or blocked at each stage. |
| **Secondary** | `archive_outreach_shortlist.csv` | Intermediate pool; do not treat as final send list. |

**Quick trust view:** `uv run python scripts/qa/print_outbound_run_summary.py --json <dir>/archive_outreach_build_summary.json`

**Counts to sanity-check in `outbound_run.counts` / summary top-level:** `archive_audited_rows`, `archive_eligible_rows`, `shortlist_rows`, `send_ready_rows`, `review_required_rows`, `gate_blocked_rows`, `final_drop_rows` (align with your expectations for the week).

## Lead lane (curated prospects)

| Step | Command |
|------|---------|
| Export | `uv run python scripts/leads/export_next_marketing_recipients.py -o <path>.csv` |

Add **`--write-outbound-summary`** to emit `<stem>_outbound_summary.json` next to the CSV (recommended for auditability).

### What to open after the run

| Role | Artifact | Why |
|------|----------|-----|
| **Human review (send)** | The exported CSV (e.g. `next_marketing.csv`) | Operator working list from `lead_master` + gate. |
| **Trust / debug** | `<stem>_outbound_summary.json` → **`outbound_run`** (+ optional `lead_queue` stats) | Same envelope as archive: lane, gmail, sqlite, Sent folders, counts, paths. |

**Quick trust view:** `uv run python scripts/qa/print_outbound_run_summary.py --json <stem>_outbound_summary.json`

## Before drafting or sending

- Open **`send_ready`** (archive) or the **lead CSV** as the canonical working artifact; do not rely on an obsolete UI surface.
- Spot-check counts vs. `outbound_run` / summary.
- Resolve or defer **`review_required`** rows explicitly — do not assume they are sendable.

## After sending

- Update **blocker memory** so the next run does not re-offer the same contacts: Sent ingest for `contacto@origenlab.cl`, and/or [`mark_outreach_state.py`](../../scripts/leads/mark_outreach_state.py) (preview first, then **`--apply`** with operator, source, reason), plus suppression when appropriate.
- Keep the **CLI-produced CSV/JSON** (and readiness JSON if you ran it) as the record of what was selected for that batch.
- **After post-send refresh** (follow [`POST_SEND_SAFE_LOOP.md`](POST_SEND_SAFE_LOOP.md)): review the Prospectos drift report under `prospectos_safety_drift_<date>/`. **Drift is not a send-safety failure** — raw `lead_research_prospect` can lag suppressions/contacted state; export gates and sidecars remain authoritative ([`SCHEMA_CLASSIFICATION_MODEL.md`](SCHEMA_CLASSIFICATION_MODEL.md)).

## Campaign lane (durable SQLite ledger, e.g. `hielscher-sonicators-2026`)

Multi-batch campaign operation does not use CSV as its operational record — canonical state
(campaign row, per-recipient lifecycle, append-only send-attempt ledger, manual contact
sidecar) lives in SQLite. See [`OUTBOUND_SOURCE_OF_TRUTH.md` § Campaign ledger](../OUTBOUND_SOURCE_OF_TRUTH.md).

| Step | Command (from `apps/email-pipeline/`) |
|------|----------------------------------------|
| Create/init a campaign | `uv run python scripts/campaigns/outbound_campaign_cli.py init --campaign-id <id> --name <name> --sender-email <email> --sender-name <name> --subject <subject> --target <n> --baseline <n> [--db <path>]` |
| Register/update a manual contact fact | `... contact-status set --email <email> --status active\|inactive\|hold [--org-domain ...] [--org-name ...] [--role ...] [--reason ...] [--evidence ...] [--effective-at ...]` |
| Show a contact's manual status | `... contact-status show --email <email>` |
| Add candidates | `... candidates add --campaign-id <id> --email <email> [--email <email2> ...] [--institution ...]` |
| Select/reserve next N (canonical gate) | `... select --campaign-id <id> --n <n> [--gmail-user ...]` |
| Inspect the reserved batch | `... batch show --campaign-id <id>` |
| Dry-run send (default) | `... send --campaign-id <id> --html <path>` |
| Explicit live send | `... send --campaign-id <id> --html <path> --live` |
| Reconcile from Gmail Sent / suppression evidence | `... reconcile --campaign-id <id> [--gmail-user ...]` |
| Show campaign status/progress | `... status --campaign-id <id>` |

`status` reports: `target`, `baseline`, `ledger_attempts`, `total_accepted` (`baseline +
ledger_attempts`), `remaining`, `candidates`, `selected_reserved`, `sent`, `blocked`,
`bounced`. `select` and the sender's pre-send recheck both reuse the same shared gate as the
archive/lead lanes above, plus a hard `manual_contact_status` inactive/hold exact-email block
— an `active` manual fact is informational only and never bypasses suppression, domain
suppression, Sent-history, outreach state, supplier-domain, or noise checks. `send` is
dry-run unless `--live` is passed; a command retry never re-sends an already-accepted
attempt. Default paths never write to a Windows Downloads path; `--out` (where supported) is
refused if it points at one.

## What is **not** source of truth

- **Dashboard/API read surfaces** — useful for operational visibility, but they do **not** replace the canonical CLI outputs for “what we exported this run.”
- **Advanced / exploratory scripts** (e.g. `export_marketing_from_contact_master.py`) — not the default daily archive or lead path unless you intentionally choose them.
- **Gate “eligible”** alone — not proof of fit to contact; still require human review and small batches.

## Regression coverage

Blocker memory (Sent folders, `outreach_contact_state`, suppression) is covered by integration tests:  
`tests/test_archive_lane_outbound_integration.py`, `tests/test_next_marketing_queue_outbound_integration.py` (see [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md)).
