# Lead account rollup layer (v1.5)

Additive CRM-style **accounts** on top of `lead_master` **without** changing raw ingest or per-tender rows.

## Tables (same SQLite as emails + mart)

| Table | Purpose |
|--------|---------|
| `lead_account_master` | One row per deduped public buyer / institution cluster |
| `lead_account_aliases` | Alternate raw names seen for that account |
| `lead_account_membership` | Links `lead_master.id` → `lead_account_master.id` |
| `lead_account_matches_existing_orgs` | Links accounts → `organization_master.domain` (mart PK is **domain**, not an integer id) |
| `lead_account_overrides` | Manual remaps (raw / normalized → target canonical name) |

Schema source of truth: `src/origenlab_email_pipeline/lead_accounts_schema.py` (`ensure_lead_account_tables`).

## Design notes

- **Idempotent full rebuild:** [`build_lead_account_rollup.py`](../../scripts/leads/advanced/build_lead_account_rollup.py) **deletes** rollup rows and rebuilds from `lead_master`. It does **not** delete `external_leads_raw`, `lead_master`, or mart tables.
- **Dedupe key:** `account_dedupe_key(normalized_name, primary_domain)` → stored as `account_dedupe_key` (unique).
- **Junk names:** Rows with junk `org_name` and **no** usable domain are **skipped** (no membership). Junk + domain → `domain_only_fallback` + `quality_status=needs_review`.
- **Overrides:** Insert rows into `lead_account_overrides` with `override_type` in (`remap_raw_name`, `merge`, `manual`), `is_active=1`, and `target_account_name` (and optional `normalized_source_value` / `source_value`).

## CLI (from repo root)

```bash
# 0) Optional: audit raw org names before rollup
uv run python scripts/leads/advanced/audit_lead_org_quality.py

# 1) Build / refresh rollup (after lead_master is up to date)
uv run python scripts/leads/advanced/build_lead_account_rollup.py

# 2) Match accounts to email mart (requires organization_master populated)
uv run python scripts/leads/advanced/match_lead_accounts_to_existing_orgs.py
# Optional fuzzy (always needs_review on fuzzy path):
uv run python scripts/leads/advanced/match_lead_accounts_to_existing_orgs.py --allow-fuzzy

# 3) Validate
uv run python scripts/leads/advanced/validate_lead_account_rollup.py
```

Override DB path: `--db /path/to/emails.sqlite`

## Sample SQL for dashboards / analysis

**Top buyer accounts by tender count:**

```sql
SELECT id, canonical_name, primary_domain, lead_count, source_count, quality_status
FROM lead_account_master
ORDER BY lead_count DESC
LIMIT 50;
```

**Account with sample tenders (titles):**

```sql
SELECT a.canonical_name, l.id, l.org_name, l.evidence_summary, l.source_url
FROM lead_account_master a
JOIN lead_account_membership m ON m.lead_account_id = a.id
JOIN lead_master l ON l.id = m.lead_id
WHERE a.id = ?
ORDER BY COALESCE(l.priority_score, -1) DESC
LIMIT 20;
```

**Accounts already linked to the email archive (mart):**

```sql
SELECT a.canonical_name, a.lead_count, m.organization_domain, m.match_method, m.confidence, m.review_status
FROM lead_account_master a
JOIN lead_account_matches_existing_orgs m ON m.lead_account_id = a.id
ORDER BY a.lead_count DESC;
```

**Leads not rolled up (junk / missing key):**

```sql
SELECT COUNT(*) FROM lead_master lm
WHERE NOT EXISTS (SELECT 1 FROM lead_account_membership x WHERE x.lead_id = lm.id);
```

## Normalization helpers

Python module: `origenlab_email_pipeline.org_normalize` — `normalize_org_name`, `is_junk_org_name`, `normalize_domain`.

## Deviation from original spec

`organization_master` has **no** surrogate integer primary key; matches use **`organization_domain`** (TEXT) aligned with `organization_master.domain`.
