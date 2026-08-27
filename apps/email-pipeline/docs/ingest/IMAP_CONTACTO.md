# IMAP ingest for `contacto@origenlab.cl` (Titan / generic password IMAP)

**Legacy or optional path.** If **contacto@origenlab.cl** is on **Google Workspace**, use **[`WORKSPACE_GMAIL_IMAP.md`](WORKSPACE_GMAIL_IMAP.md)** and **`05_workspace_gmail_imap_to_sqlite.py`** instead — that is the **operational default** in this repo. Workspace ingest produces `gmail:...` rows; contacto review/drafting helpers use that Gmail-specific source pattern.

This document describes **password IMAP** (e.g. **Titan**) via **`04_imap_to_sqlite.py`** (`imap:...` rows). Older web runbooks may still mention Titan; align [`apps/web/docs/email-setup.md`](../../../web/docs/email-setup.md) with your actual host when convenient.

## What this does

`scripts/ingest/04_imap_to_sqlite.py` connects with **SSL IMAP**, searches messages (optionally **SINCE** N days), fetches each message with **BODY.PEEK[]** (avoids marking read on many servers), parses MIME the same way as mbox ingest, and **INSERT**s into `emails` with:

- `source_file` = `imap:contacto@origenlab.cl/INBOX` (or your user + folder)
- Same body / Phase-2 columns as `02_mbox_to_sqlite.py`

## Credentials (`.env`)

Set in `apps/email-pipeline/.env` (never commit):

```bash
ORIGENLAB_IMAP_HOST=imap.titan.email
ORIGENLAB_IMAP_PORT=993
ORIGENLAB_IMAP_USER=contacto@origenlab.cl
ORIGENLAB_IMAP_PASSWORD=...   # app password if Titan offers it
```

Enable **external / third-party app access** in Titan if IMAP is blocked.

## Commands

From `apps/email-pipeline`:

```bash
# Last 90 days, INBOX only
uv run python scripts/ingest/04_imap_to_sqlite.py --folder INBOX --since-days 90

# Full INBOX (can be large)
uv run python scripts/ingest/04_imap_to_sqlite.py --folder INBOX

# Re-download this source only (deletes prior rows with same source_file, then inserts)
uv run python scripts/ingest/04_imap_to_sqlite.py --folder INBOX --since-days 365 --replace-source

# Append but skip duplicates by Message-ID (useful against a DB that already has PST mail)
uv run python scripts/ingest/04_imap_to_sqlite.py --folder INBOX --since-days 30 --skip-duplicate-message-id
```

**Sent mail:** Titan folder name may be `Sent`, `Sent Items`, or localized — list folders with your mail client or a small IMAP probe, then pass `--folder`.

## SQLite target

Uses **`ORIGENLAB_SQLITE_PATH`** (see `.env.example`). For experiments, use a **copy** of `emails.sqlite` so you do not mix live mailbox rows with a frozen PST archive without intent.

## After ingest

Re-run any Phase-2 / mart steps you rely on (if the pipeline adds columns or derived tables after raw insert). Then use dataset scripts as today:

```bash
uv run python scripts/dataset/export_tatiana_candidate_cohort.py --exclude-noise --include-tatiana-text-signals
```
