# Mart freshness and plausible dates

## Raw archive

- Table `emails` keeps `date_iso` exactly as produced by ingest. Bad headers (e.g. spam with year 2033) are **not** modified in SQLite by this policy.

## Derived mart timeline fields

When `scripts/mart/build_business_mart.py` aggregates **first_seen_at** / **last_seen_at** on `contact_master` and `organization_master`, and **sent_at** on `document_master`, it uses `freshness_dates.email_date_iso_for_mart_timeline`:

- If `date_iso` parses to a calendar date **after** `today + slack_days` (default **2**), that message **does not** move the min/max timeline for that contact/org/document row.
- Message **counts** and intent/doc tallies still include the email.
- Unparseable `date_iso` strings are still used for bounds (same conservative behaviour as SQLite `date(date_iso) IS NULL` in **Salud de datos** plausible max).

Override slack: `--mart-date-slack-days` on the build script.

## Operator UI (**Salud de datos**)

- **max(date_iso) absoluto** vs **plausible** for raw mail.
- For the mart, **absolute** vs **plausible** MAX on `last_seen_at` / `sent_at` (SQL filter aligned with the same calendar rule).
- **Vigencia** compares raw **plausible** mail to mart peaks from **contact / organization / document only**, not `opportunity_signals.created_at`.

## `opportunity_signals.created_at`

Set to `now_iso()` at mart rebuild time when rows are inserted. It is a **rebuild stamp**, not “when the business event happened”. The retired Streamlit UI labeled this as **Mart (regenerado)**; the underlying rebuild-stamp semantics remain unchanged.

## Related code

- `src/origenlab_email_pipeline/freshness_dates.py` — policy implementation.
