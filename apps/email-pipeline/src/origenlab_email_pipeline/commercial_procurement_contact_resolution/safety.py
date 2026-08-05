"""Canonical outbound safety truth for PR5E — full GateContext, fail-closed gaps."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.candidate_export_gate import (
    GateContext,
    evaluate_export_eligibility,
    normalize_export_email,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.marketing_export_context import (
    DEFAULT_SENT_FOLDERS,
    build_marketing_export_gate_context,
)
from origenlab_email_pipeline.outbound_core import (
    DEFAULT_GMAIL_USER_FALLBACK,
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pii_safe_set_digest(values: frozenset[str] | set[str]) -> str:
    """Order-independent digest of membership without raw PII in the payload."""
    return canonical_json_digest(sorted(_hash_token(v) for v in values))


def _pii_safe_map_digest(mapping: dict[str, str]) -> str:
    rows = sorted(
        ({"k": _hash_token(k), "v": str(v)} for k, v in mapping.items()),
        key=lambda r: (r["k"], r["v"]),
    )
    return canonical_json_digest(rows)


@dataclass(frozen=True)
class SafetySnapshot:
    """Frozen canonical outbound gate inputs for one PR5E source load."""

    gate_context: GateContext | None
    gmail_user: str
    sent_folders: tuple[str, ...]
    truth_complete: bool
    missing_truth_sources: tuple[str, ...]
    load_error: str | None
    safety_fingerprint: str

    @property
    def loaded(self) -> bool:
        return self.gate_context is not None and self.load_error is None


def _required_truth_gaps(conn: sqlite3.Connection) -> list[str]:
    """Tables / inputs required for complete cold-export safety truth."""
    gaps: list[str] = []
    # Sent history is mandatory truth — missing emails table is not "empty Sent".
    if not _table_exists(conn, "emails"):
        gaps.append("emails")
    # Suppression / outreach / supplier / domain tables may be empty but must exist
    # so "no rows" is distinguishable from "not loaded".
    for table in (
        "contact_email_suppression",
        "outreach_contact_state",
        "contact_domain_suppression",
        "supplier_master",
    ):
        if not _table_exists(conn, table):
            gaps.append(table)
    return gaps


def safety_fingerprint_for_context(
    *,
    ctx: GateContext | None,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    truth_complete: bool,
    missing_truth_sources: tuple[str, ...],
    load_error: str | None,
) -> str:
    if ctx is None:
        return canonical_json_digest(
            {
                "kind": "safety_snapshot",
                "truth_complete": False,
                "missing_truth_sources": list(missing_truth_sources),
                "load_error": load_error or "gate_context_unavailable",
                "gmail_user_token": _hash_token(gmail_user),
                "sent_folders": list(sent_folders),
            }
        )
    return canonical_json_digest(
        {
            "kind": "safety_snapshot",
            "truth_complete": truth_complete,
            "missing_truth_sources": list(missing_truth_sources),
            "load_error": load_error,
            "gmail_user_token": _hash_token(gmail_user),
            "sent_folders": list(sent_folders),
            "sent_recipient_digest": _pii_safe_set_digest(ctx.sent_recipient_norms),
            "suppressed_email_digest": _pii_safe_set_digest(ctx.suppressed_norms),
            "suppressed_domain_digest": _pii_safe_set_digest(
                ctx.suppressed_contact_domains
            ),
            "blocked_domain_digest": _pii_safe_set_digest(ctx.blocked_domains),
            "supplier_domain_digest": _pii_safe_set_digest(ctx.supplier_domains),
            "outreach_state_digest": _pii_safe_map_digest(ctx.outreach_state_by_email),
            "sent_count": len(ctx.sent_recipient_norms),
            "suppressed_email_count": len(ctx.suppressed_norms),
            "suppressed_domain_count": len(ctx.suppressed_contact_domains),
            "blocked_domain_count": len(ctx.blocked_domains),
            "supplier_domain_count": len(ctx.supplier_domains),
            "outreach_state_count": len(ctx.outreach_state_by_email),
            "skip_noise_filter": ctx.skip_noise_filter,
            "skip_supplier_domain_filter": ctx.skip_supplier_domain_filter,
            "strict_contact_graph_noise": ctx.strict_contact_graph_noise,
        }
    )


def load_safety_snapshot(
    conn: sqlite3.Connection,
    *,
    gmail_user: str | None = None,
    sent_folders: tuple[str, ...] | list[str] | None = None,
) -> SafetySnapshot:
    """Load the full canonical outbound GateContext (read-only).

    Does not treat missing safety tables as empty blocker sets. Incomplete truth
    yields ``truth_complete=False`` so candidates become non-selectable
    ``safety_unknown`` rather than falsely clear.
    """
    from origenlab_email_pipeline.config import Settings

    resolved_user = resolve_outbound_gmail_user(
        Settings(), explicit=gmail_user
    ) or DEFAULT_GMAIL_USER_FALLBACK
    resolved_folders = resolve_outbound_sent_folders(
        list(sent_folders) if sent_folders is not None else None
    )
    if not resolved_folders:
        resolved_folders = DEFAULT_SENT_FOLDERS

    gaps = tuple(_required_truth_gaps(conn))
    if gaps:
        return SafetySnapshot(
            gate_context=None,
            gmail_user=resolved_user,
            sent_folders=tuple(resolved_folders),
            truth_complete=False,
            missing_truth_sources=gaps,
            load_error=f"missing_safety_truth_sources:{','.join(gaps)}",
            safety_fingerprint=safety_fingerprint_for_context(
                ctx=None,
                gmail_user=resolved_user,
                sent_folders=tuple(resolved_folders),
                truth_complete=False,
                missing_truth_sources=gaps,
                load_error=f"missing_safety_truth_sources:{','.join(gaps)}",
            ),
        )

    try:
        ctx = build_marketing_export_gate_context(
            conn,
            gmail_user=resolved_user,
            sent_folders=tuple(resolved_folders),
            skip_noise_filter=False,
            skip_supplier_domain_filter=False,
            strict_contact_graph_noise=False,
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on loader errors
        err = f"safety_gate_load_failed:{type(exc).__name__}"
        return SafetySnapshot(
            gate_context=None,
            gmail_user=resolved_user,
            sent_folders=tuple(resolved_folders),
            truth_complete=False,
            missing_truth_sources=("gate_context_load",),
            load_error=err,
            safety_fingerprint=safety_fingerprint_for_context(
                ctx=None,
                gmail_user=resolved_user,
                sent_folders=tuple(resolved_folders),
                truth_complete=False,
                missing_truth_sources=("gate_context_load",),
                load_error=err,
            ),
        )

    return SafetySnapshot(
        gate_context=ctx,
        gmail_user=resolved_user,
        sent_folders=tuple(resolved_folders),
        truth_complete=True,
        missing_truth_sources=(),
        load_error=None,
        safety_fingerprint=safety_fingerprint_for_context(
            ctx=ctx,
            gmail_user=resolved_user,
            sent_folders=tuple(resolved_folders),
            truth_complete=True,
            missing_truth_sources=(),
            load_error=None,
        ),
    )


def parse_usable_email(raw: str | None) -> str | None:
    """Canonical usable email — parsing must succeed; non-empty junk is not usable."""
    return normalize_export_email(raw or "")


def evaluate_contact_safety(
    *,
    email_norm: str | None,
    institution_name: str | None,
    safety: SafetySnapshot,
) -> dict[str, Any]:
    """Apply canonical ``candidate_export_gate`` policy.

    Incomplete safety truth → ``safety_unknown`` / non-selectable (fail closed).
    """
    if not email_norm:
        return {
            "suppression_result": "email_missing",
            "outreach_state_result": "email_missing",
            "safety_blocked": False,
            "safety_unknown": False,
            "reasons": ("email_missing",),
            "selectable_by_safety": False,
        }

    if not safety.loaded or safety.gate_context is None or not safety.truth_complete:
        return {
            "suppression_result": "safety_unknown",
            "outreach_state_result": "safety_unknown",
            "safety_blocked": True,
            "safety_unknown": True,
            "reasons": (
                "safety_truth_incomplete",
                *(safety.missing_truth_sources or ()),
                *((safety.load_error,) if safety.load_error else ()),
            ),
            "selectable_by_safety": False,
        }

    ctx = safety.gate_context
    result = evaluate_export_eligibility(
        contact_email=email_norm,
        institution_name=institution_name,
        ctx=ctx,
    )
    outreach = (ctx.outreach_state_by_email or {}).get(email_norm)
    reasons = tuple(result.reasons)
    if any(r == "suppression" or r == "domain_suppression" for r in reasons):
        suppression = "suppressed"
    elif result.eligible:
        suppression = "clear"
    else:
        suppression = "blocked"

    if outreach in {"contacted", "replied", "snoozed"}:
        outreach_result = outreach
    elif result.eligible:
        outreach_result = "clear"
    else:
        outreach_result = "blocked"

    return {
        "suppression_result": suppression,
        "outreach_state_result": outreach_result,
        "safety_blocked": not result.eligible,
        "safety_unknown": False,
        "reasons": reasons,
        "selectable_by_safety": bool(result.eligible),
    }


def safety_snapshot_from_gate_context(
    ctx: GateContext,
    *,
    gmail_user: str = DEFAULT_GMAIL_USER_FALLBACK,
    sent_folders: tuple[str, ...] = DEFAULT_SENT_FOLDERS,
    truth_complete: bool = True,
) -> SafetySnapshot:
    """Test helper: wrap an explicit GateContext as a complete SafetySnapshot."""
    return SafetySnapshot(
        gate_context=ctx,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        truth_complete=truth_complete,
        missing_truth_sources=() if truth_complete else ("synthetic_incomplete",),
        load_error=None if truth_complete else "synthetic_incomplete",
        safety_fingerprint=safety_fingerprint_for_context(
            ctx=ctx if truth_complete else None,
            gmail_user=gmail_user,
            sent_folders=sent_folders,
            truth_complete=truth_complete,
            missing_truth_sources=() if truth_complete else ("synthetic_incomplete",),
            load_error=None if truth_complete else "synthetic_incomplete",
        ),
    )
