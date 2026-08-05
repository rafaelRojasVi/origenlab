"""Read-only safety gate wrappers — reuse existing suppression/outreach policy."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.candidate_export_gate import (
    GateContext,
    evaluate_export_eligibility,
)
from origenlab_email_pipeline.marketing_export_context import (
    load_outreach_state_map,
    load_suppressed_contact_domains,
    load_suppressed_norms,
)


@dataclass(frozen=True)
class SafetySnapshot:
    suppressed_norms: frozenset[str]
    suppressed_domains: frozenset[str]
    outreach_state_by_email: dict[str, str]


def load_safety_snapshot(conn: sqlite3.Connection) -> SafetySnapshot:
    """Load suppression + outreach maps read-only; never mutates state."""
    return SafetySnapshot(
        suppressed_norms=frozenset(load_suppressed_norms(conn)),
        suppressed_domains=frozenset(load_suppressed_contact_domains(conn)),
        outreach_state_by_email=dict(load_outreach_state_map(conn)),
    )


def evaluate_contact_safety(
    *,
    email_norm: str | None,
    institution_name: str | None,
    safety: SafetySnapshot,
) -> dict[str, Any]:
    """Return suppression/outreach results without inventing a second policy."""
    if not email_norm:
        return {
            "suppression_result": "email_missing",
            "outreach_state_result": "email_missing",
            "safety_blocked": False,
            "reasons": ("email_missing",),
        }
    ctx = GateContext(
        sent_recipient_norms=frozenset(),
        suppressed_norms=safety.suppressed_norms,
        outreach_state_by_email=dict(safety.outreach_state_by_email),
        supplier_domains=frozenset(),
        blocked_domains=frozenset(),
        suppressed_contact_domains=safety.suppressed_domains,
        skip_supplier_domain_filter=True,
        skip_noise_filter=True,
        strict_contact_graph_noise=False,
    )
    result = evaluate_export_eligibility(
        contact_email=email_norm,
        institution_name=institution_name,
        ctx=ctx,
    )
    outreach = safety.outreach_state_by_email.get(email_norm)
    if any("suppression" in r for r in result.reasons):
        suppression = "suppressed"
    elif result.eligible:
        suppression = "clear"
    else:
        suppression = "blocked"
    return {
        "suppression_result": suppression,
        "outreach_state_result": outreach or ("clear" if result.eligible else "blocked"),
        "safety_blocked": not result.eligible,
        "reasons": tuple(result.reasons),
    }
