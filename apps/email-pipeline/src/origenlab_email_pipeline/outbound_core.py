"""Shared outbound defaults: Gmail user, Sent folders, GateContext builders, run-summary envelope.

Canonical CLIs and readiness checks should use this module so archive and lead lanes stay aligned
on blocker context (Sent scan, suppression, outreach state, suppliers, noise strictness).

Policy lives in ``candidate_export_gate`` and ``marketing_export_context``; this module only
centralizes **resolution** and **documentation** of what was used for a run.
"""

from __future__ import annotations

from typing import Any

import sqlite3

from origenlab_email_pipeline.config import Settings
from origenlab_email_pipeline.candidate_export_gate import GateContext
from origenlab_email_pipeline.marketing_export_context import (
    DEFAULT_EXCLUDE_DOMAINS,
    DEFAULT_SENT_FOLDERS,
    build_marketing_export_gate_context,
)

OUTBOUND_RUN_SUMMARY_SCHEMA_VERSION = "1"

DEFAULT_GMAIL_USER_FALLBACK = "contacto@origenlab.cl"

# Re-export for single import path in CLIs
__all__ = [
    "DEFAULT_EXCLUDE_DOMAINS",
    "DEFAULT_GMAIL_USER_FALLBACK",
    "DEFAULT_SENT_FOLDERS",
    "OUTBOUND_RUN_SUMMARY_SCHEMA_VERSION",
    "build_outbound_run_envelope",
    "gate_context_for_archive_batch",
    "gate_context_for_lead_master_export",
    "resolve_outbound_gmail_user",
    "resolve_outbound_sent_folders",
    "sent_folder_defaults_were_used",
]


def resolve_outbound_gmail_user(settings: Settings, *, explicit: str | None) -> str:
    """Mailbox for Sent-folder scans and gate context (CLI > settings > fallback)."""
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    gu = getattr(settings, "gmail_workspace_user", None)
    if gu is not None and str(gu).strip():
        return str(gu).strip()
    return DEFAULT_GMAIL_USER_FALLBACK


def resolve_outbound_sent_folders(cli_values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Use CLI list if non-empty after strip; otherwise ``DEFAULT_SENT_FOLDERS`` (both Gmail labels)."""
    if cli_values:
        t = tuple(str(x).strip() for x in cli_values if x is not None and str(x).strip())
        if t:
            return t
    return DEFAULT_SENT_FOLDERS


def sent_folder_defaults_were_used(cli_values: list[str] | tuple[str, ...] | None) -> bool:
    """True when ``resolve_outbound_sent_folders`` will take the shared default tuple."""
    if not cli_values:
        return True
    t = tuple(str(x).strip() for x in cli_values if x is not None and str(x).strip())
    return not bool(t)


def gate_context_for_archive_batch(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    extra_exclude_domains: tuple[str, ...] = (),
    strict_contact_graph_noise: bool = True,
    allow_prior_outreach_history: bool = False,
) -> GateContext:
    """GateContext for ``contact_master`` / archive audit (stricter graph noise)."""
    return build_marketing_export_gate_context(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        extra_exclude_domains=extra_exclude_domains,
        strict_contact_graph_noise=bool(strict_contact_graph_noise),
        allow_prior_outreach_history=bool(allow_prior_outreach_history),
    )


def gate_context_for_lead_master_export(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    extra_exclude_domains: tuple[str, ...] = (),
    allow_prior_outreach_history: bool = False,
) -> GateContext:
    """GateContext for ``lead_master`` exports (default noise strictness off)."""
    return build_marketing_export_gate_context(
        conn,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        extra_exclude_domains=extra_exclude_domains,
        strict_contact_graph_noise=False,
        allow_prior_outreach_history=bool(allow_prior_outreach_history),
    )


def build_outbound_run_envelope(
    *,
    lane: str,
    gmail_user: str,
    sqlite_path: str,
    sent_folders: tuple[str, ...],
    sent_folder_defaults_used: bool,
    strict_contact_graph_noise: bool,
    extra_exclude_domains: tuple[str, ...],
    created_at_utc: str,
    artifact_paths: dict[str, str] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Standard outbound run metadata (nested under ``outbound_run`` in summary JSON)."""
    return {
        "schema_version": OUTBOUND_RUN_SUMMARY_SCHEMA_VERSION,
        "lane": lane,
        "gmail_user": gmail_user,
        "sqlite_path": sqlite_path,
        "sent_folders_resolved": list(sent_folders),
        "sent_folder_defaults_used": bool(sent_folder_defaults_used),
        "strict_contact_graph_noise": bool(strict_contact_graph_noise),
        "extra_exclude_domains": [d.strip().lower() for d in extra_exclude_domains if str(d).strip()],
        "created_at_utc": created_at_utc,
        "artifact_paths": dict(artifact_paths or {}),
        "counts": {k: int(v) for k, v in (counts or {}).items()},
    }
