"""Campaign eligibility = manual hard-block sidecar + canonical candidate export gate.

Campaigns deliberately differ from one-shot cold-export lanes in one respect:
prior marketing delivery is historical evidence, not a permanent unsubscribe.
Therefore campaign evaluation enables ``allow_prior_outreach_history`` before
delegating to the canonical gate. Gmail Sent plus historical ``contacted`` or
``replied`` state do not block a later campaign by themselves.

Hard blockers remain hard: manual ``inactive``/``hold`` is checked first;
canonical email/domain suppression, ``snoozed`` outreach state, internal
addresses, supplier domains and marketing-noise checks still apply.
"""

from __future__ import annotations

from dataclasses import replace

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

    # Durable campaigns may re-contact recipients of older campaigns. This is
    # intentionally applied here (not in the shared builder) so archive/lead
    # one-shot exports retain their legacy no-repeat semantics unless an
    # operator explicitly opts into repeat-campaign mode.
    campaign_ctx = replace(gate_ctx, allow_prior_outreach_history=True)
    return evaluate_export_eligibility(
        contact_email=contact_email,
        institution_name=institution_name,
        ctx=campaign_ctx,
    )
