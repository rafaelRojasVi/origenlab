"""Campaign eligibility = manual hard-block sidecar + canonical candidate_export_gate.

Do not duplicate gate logic here. Manual inactive/hold is checked first and
blocks regardless of any other signal (hard exact-email block). An "active"
manual fact is informational only: on any other status the function falls
straight through to ``candidate_export_gate.evaluate_export_eligibility`` and
does not skip or relax any of its checks.
"""

from __future__ import annotations

from origenlab_email_pipeline.candidate_export_gate import (
    ExportGateResult,
    GateContext,
    evaluate_export_eligibility,
    normalize_export_email,
)

REASON_MANUAL_INACTIVE = "manual_inactive"
REASON_MANUAL_HOLD = "manual_hold"

_MANUAL_REASON = {"inactive": REASON_MANUAL_INACTIVE, "hold": REASON_MANUAL_HOLD}


def evaluate_campaign_eligibility(
    *,
    contact_email: str,
    institution_name: str | None,
    gate_ctx: GateContext,
    manual_status_by_email: dict[str, str],
) -> ExportGateResult:
    em = normalize_export_email(contact_email)
    if em and manual_status_by_email.get(em) in ("inactive", "hold"):
        reason = _MANUAL_REASON[manual_status_by_email[em]]
        return ExportGateResult(eligible=False, reasons=(reason,))
    return evaluate_export_eligibility(
        contact_email=contact_email, institution_name=institution_name, ctx=gate_ctx,
    )
