# Gmail / Google Workspace and this pipeline

## Operational default vs legacy IMAP

**Primary (operational mailbox path):** **Google Workspace Gmail** via **`05_workspace_gmail_imap_to_sqlite.py`** and **`ORIGENLAB_GMAIL_*`**. Ingested rows use **`source_file`** values prefixed with **`gmail:`**. Those **`gmail:contacto@...`** rows remain the canonical Gmail-source rows for contacto review/drafting helpers; the former Streamlit surfaces were removed on 2026-06-04 (see [`RUNBOOK.md`](../RUNBOOK.md#m-eprun-mailbox-primary)).

**Legacy / optional:** **Titan (or any password IMAP)** via **`04_imap_to_sqlite.py`** and **`ORIGENLAB_IMAP_*`**. Rows use **`imap:`** prefixes; they do **not** match the Gmail-specific **`gmail:contacto@...`** source pattern used by contacto review/drafting helpers.

| Role | Mailbox host | Script | Auth |
|------|--------------|--------|------|
| **Primary** | **Google Workspace / Gmail** | `05_workspace_gmail_imap_to_sqlite.py` | OAuth2 desktop + `ORIGENLAB_GMAIL_*` |
| **Legacy / optional** | **Titan / generic IMAP** (password) | `04_imap_to_sqlite.py` | `ORIGENLAB_IMAP_*` |

Workspace ingest is documented end-to-end in **[WORKSPACE_GMAIL_IMAP.md](WORKSPACE_GMAIL_IMAP.md)** (`uv sync --group workspace`).

## Gmail REST API (not implemented)

You could alternatively use:

- `users.messages.list` / `users.messages.get` with `format=raw`
- Scope e.g. `https://www.googleapis.com/auth/gmail.readonly`
- Base64url-decode the RFC 822 payload → same `BytesParser` path as IMAP

That REST path is **not** in the repo yet; **IMAP + OAuth2** covers the same mailbox with less code for full-folder fetch.

## IMAP scope note

Gmail **IMAP** with OAuth uses scope **`https://mail.google.com/`** (full mail). The REST **readonly** scope does **not** replace that for IMAP.

## Doc drift (Titan vs Workspace)

[`apps/web/docs/email-setup.md`](../../../web/docs/email-setup.md) may still describe **Titan** for contacto@. If the team has moved that address to **Workspace**, treat **Gmail** as operational truth and update the web runbook when convenient.
