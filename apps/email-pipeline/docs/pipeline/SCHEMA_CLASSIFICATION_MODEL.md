# Schema & classification layer model

Status: canonical (documentation checkpoint)  
Owner: email-pipeline-maintainers  
Last reviewed: 2026-06-01

Read-only audit artifacts (gitignored, not committed):  
`reports/out/active/current/schema_audit_2026_06_01/`

Related: [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md) · [`SCHEMA_OWNERSHIP.md`](SCHEMA_OWNERSHIP.md) · [`RUNBOOK.md`](../RUNBOOK.md) · [`SCRIPT_MAP.md`](../SCRIPT_MAP.md)

---

## 1. One-page mental model

Think in **six layers**. Lower layers are evidence; upper layers are derived for humans. **Send decisions must not skip the safety layer.**

| Layer | Question it answers | Primary stores / code |
| --- | --- | --- |
| **Evidence** | What happened in mail and imports? | `emails` (Gmail ingest), attachments, `external_leads_raw`, `lead_research_evidence`, commercial signal facts |
| **Safety** | Must we block this exact email or domain? | `contact_email_suppression`, `contact_domain_suppression`, export gates (`candidate_export_gate`) |
| **Outreach lifecycle** | Did we already outreach this exact email? | `outreach_contact_state` (`contacted`, `snoozed`, `not_contacted`, …) |
| **Business classification** | What kind of org/contact is this? | `buyer_type`, `sector`, `campaign_bucket`, warm-case role rules — **not** send approval alone |
| **Workflow / review** | What should an operator do next? | `lead_research_prospect.status`, `is_blocked`, block-reason rows, campaign queues |
| **UI / read model** | What does the dashboard show? | Postgres `lead_intel.*`, `outbound.*`, API mirror routes, Spanish labels in dashboard TS |

**Rule of thumb:** Evidence and safety are **facts**. Workflow and UI are **opinions and presentation** — rebuildable if inputs are refreshed.

---

## 2. Source-of-truth table

| Store | Role | Safe to rebuild? | Dashboard? |
| --- | --- | --- | --- |
| **`emails`** | Immutable Gmail (and related) message evidence | **No** — re-ingest only with care | Indirect (classification mirror) |
| **`contact_email_suppression`** | Exact-email safety (bounce, DNC, etc.) | **No** — operator/NDR writes are authoritative | Yes (mirror) |
| **`contact_domain_suppression`** | Domain-level cold-outreach block | **No** | Yes (mirror) |
| **`outreach_contact_state`** | Per-email contacted / snoozed lifecycle | **No** — backfill/sync updates | Yes (mirror) |
| **`lead_research_prospect`** (+ batch, block_reason, evidence) | Derived prospect **queue** from CSV/DeepSearch + overlays | **Yes** — rebuild from batch scripts | Yes (mirror) |
| **`contact_master` / `organization_master`** | Derived business mart | **Yes** — `build_business_mart.py` | Yes |
| **Postgres + dashboard API** | Read-only mirror / read model | N/A (reload from SQLite) | Yes |
| **`reports/out/active/current/*.csv` / `*.json`** | Generated artifacts for operators | **Yes** — always label generator + run time | Sometimes copied into UI context |

**Not source of truth for send gates:** `lead_research_prospect.classification`, dashboard KPIs, post-send digest JSON alone, or mirror segment counts without verifying SQLite sidecars.

---

## 3. Golden send-gate rule

> **NEVER approve sending from `lead_research_prospect.classification` alone.**

Sending and cold-export safety must use the **shared export gate** backed by:

1. **`contact_email_suppression`** — exact email (bounce `bounce_*`, `manual_do_not_contact`, `reported_non_delivery`, …)
2. **`contact_domain_suppression`** — registrable domain block (all addresses on domain)
3. **`outreach_contact_state`** — already contacted / snoozed memory
4. **Refreshed exclusion CSVs** — e.g. `bounced_emails_for_exclusion.csv`, `contacted_exact_emails_for_exclusion.csv` from [`audit_contacted_universe.py`](../../scripts/leads/audit_contacted_universe.py)
5. **Sent-history preflight** — Gmail Sent rows in `emails` for the canonical mailbox/folders ([`outbound_sent_preflight.py`](../../src/origenlab_email_pipeline/outbound_sent_preflight.py))

Canonical export CLIs: [`OUTBOUND_SOURCE_OF_TRUTH.md`](../OUTBOUND_SOURCE_OF_TRUTH.md).

### Email vs domain suppression precedence

- **Email row wins for that address** — if `user@example.com` is listed in `contact_email_suppression`, that address is blocked regardless of domain rules.
- **Domain row blocks the whole domain** — `contact_domain_suppression` applies to cold outreach exports that evaluate domain gates; use when any contact `@domain` must not receive marketing.
- **Both can apply** — export gate evaluates the union; do not infer domain blocks from prospect `classification`.

---

## 4. Confusing mismatches (expected, not bugs)

| Symptom | Explanation |
| --- | --- |
| **`bounced_block` prospects (few) vs bounce suppressions (many)** | Prospect table is rebuilt from research CSVs; NDR apply updates **suppression** immediately. Rebuild/overlay lead research after bulk bounces. |
| **`contacted_exact` CSV row count > `outreach_contact_state`** | CSV includes mart/history breadth; sidecar is per-email outreach memory. Different definitions. |
| **`lead_blocked` raw vs built counts differ** | Mirror verify compares **built** counts (operational overlay). Raw SQLite `is_blocked=1` can differ — see `outbound_sidecar_mirror_verify.py` / tests. |
| **`net_new_safe` KPI = 0** | Can be **correct** when the active batch has no `classification=net_new_safe_review` rows (all blocked/review/tender). |
| **`classification` mixes safety + workflow** | e.g. `already_contacted_block` vs `research_only_contact_needed`. Treat as **builder vocabulary** until split (P1); use safety tables for gates. |
| **`post_send_safety_summary` bounce count stale** | Digest reads CSV; run **`audit_contacted_universe.py` before `build_post_send_digest.py`**. |
| **`bounce_other` for domain-not-found NDRs** | Safety is still correct; analytics taxonomy may refine later (P1). |

---

## 5. Refresh order checklist

Use after campaigns, NDR waves, or suppression changes:

1. **Ingest Gmail** (if needed) — read-only IMAP: `scripts/ingest/05_workspace_gmail_imap_to_sqlite.py` (Sent + INBOX per RUNBOOK).
2. **Flag / apply exact NDR suppressions** (operator-approved only) — `scripts/tools/flag_ndr_bounces_from_contacto.py` (dry-run default; targeted `--emails-file` + `--only-code` + `--apply`; broad `--apply` is break-glass — [`POST_SEND_SAFE_LOOP.md`](POST_SEND_SAFE_LOOP.md)).
3. **`scripts/leads/audit_contacted_universe.py`** — refreshes exclusion CSVs including `bounced_emails_for_exclusion.csv`.
4. **`scripts/qa/refresh_outbound_safety_memory.py`** — exports + readiness checks.
5. **Rebuild marts / prospect read models** (if needed) — `build_business_mart.py`, lead research builder, presentation/cyber merge scripts per campaign.
6. **`scripts/qa/build_post_send_digest.py`** — **after** step 3 so bounce counts match SQLite.
7. **Mirror refresh** — `scripts/ops/refresh_render_dashboard_once.sh` (see [`REFRESH_RENDER_DASHBOARD_ONCE.md`](../REFRESH_RENDER_DASHBOARD_ONCE.md)); keep `RUN_OUTBOUND_SIDECAR_MIRROR=1` after campaigns.
8. **Verify JSONs** — `/tmp/outbound_sidecar_mirror_verify.json`, `/tmp/lead_research_mirror_verify.json`, `/tmp/render_dashboard_mirror_verify.json` (`ok` / assertions passed).

Operator status (read-only): `scripts/qa/operator_status.py`.

**Prospectos drift (read-only):** after bulk NDR/contacted refreshes, run  
`uv run python scripts/qa/audit_prospectos_safety_drift.py` — writes  
`reports/out/active/current/prospectos_safety_drift_<YYYY_MM_DD>/` (gitignored). Included as step 12 in  
[`POST_SEND_SAFE_LOOP.md`](POST_SEND_SAFE_LOOP.md) (report-only; `ORIGENLAB_STRICT_PROSPECTOS_DRIFT=1` for `--strict`).  
Drift is not a send-safety failure; export gates remain authoritative.

**Institution grouping (read-model audit):** `uv run python scripts/qa/audit_institution_grouping.py` — domain-level cards are for **read-only exploration** only (not send gates). The audit tags ESP/platform/noise domains with `do_not_promote_to_institution` and emits conservative `alias_seed_candidates.csv` (`proposed_manual_review`). No production alias table exists yet; ~26% of proposed seeds passed conservative review.

---

## 6. Do not change yet

Without explicit approval and a migration plan:

- **`emails` schema** — ingest contract for all downstream jobs
- **Suppression / outreach sidecar schemas** — `contact_email_suppression`, `contact_domain_suppression`, `outreach_contact_state`
- **Mirror verify contracts** — tests and dashboard PRECAUCIÓN logic depend on them
- **Warm case / Today path** — separate from Prospectos lead research (`warm_case_*`, equipment queue)
- **Legacy 2016–2019 pipeline** — isolated `source_type`; do not merge into net-new without operator sign-off
- **Institution / account grouping** — `lead_account_*` unification and a production alias table remain deferred (read-only domain-card grouping only)

---

## 7. P0 / P1 roadmap

### P0 — safety correctness (documentation + ops)

| Item | Status |
| --- | --- |
| Document send-gate rule (this file) | Done |
| Document refresh order (§5) | Done |
| Document email vs domain suppression precedence (§3) | Done |
| RUNBOOK / AGENTS link for discoverability | Linked from parent docs |

### Prospectos display overlay (empty email + same-domain evidence)

Raw `lead_research_prospect` rows may have **no email** while `outreach_contact_state` or Gmail sent fields show **same-domain** contact. The operational overlay (`lead_research_operational_overlay.py`) upgrades those rows to **`same_domain_contacted_review`** for mirror/dashboard only — **not** send approval and **not** a raw-table backfill. Suppression (exact or domain) still wins.

### P1 — consistency / readability (future code)

| Item | Notes |
| --- | --- |
| Split prospect `classification` into `workflow_bucket` + safety overlay | Additive columns; backfill on next research rebuild |
| Normalize `lead_research_block_reason.reason_code` (reduce generic `review_reason`) | Import-time mapping |
| Align NDR suppression codes with failure taxonomy | Analytics only; optional `bounce_domain_not_found` |
| Document raw vs built `lead_blocked` in dashboard tooltip | Dev/operator UX |

Do **not** start P1 until P0 send-gate discipline is routine in ops.

---

## Quick reference: suppression reason codes (email)

Defined in [`contact_email_suppression.py`](../../src/origenlab_email_pipeline/contact_email_suppression.py):

- `bounce_no_such_user`, `bounce_access_denied`, `bounce_other`
- `manual_do_not_contact`, `reported_non_delivery`

Bounce detection from NDR bodies: [`ndr_bounce_extraction.py`](../../src/origenlab_email_pipeline/ndr_bounce_extraction.py).
