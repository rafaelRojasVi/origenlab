"""Campaign eligibility = manual hard-block sidecar + canonical candidate_export_gate.

Do not duplicate gate logic here. Manual inactive/hold is checked first and
blocks regardless of any other signal (hard exact-email block). An "active"
manual fact is informational only: on any other status the function falls
straight through to ``candidate_export_gate.evaluate_export_eligibility`` and
does not skip or relax any of its checks.

The one thing a campaign adds is its **audience kind**. A ``cold`` campaign (the
default, and every campaign created before the column existed) keeps the gate
exactly as above. A ``warm`` campaign — addressed on purpose to people we have
already corresponded with — waives only the two "we already wrote to them once"
rules (``WARM_WAIVED_REASONS``); every refusal that encodes a decision or a risk
still applies. The kind is a property of the campaign row, set at ``init``, so
``select`` and the sender's pre-send recheck cannot disagree about it.
"""

from __future__ import annotations

from origenlab_email_pipeline.candidate_export_gate import (
    REASON_OUTREACH_CONTACTED,
    REASON_SENT_HISTORY,
    ExportGateResult,
    GateContext,
    evaluate_export_eligibility,
    normalize_export_email,
)

REASON_MANUAL_INACTIVE = "manual_inactive"
REASON_MANUAL_HOLD = "manual_hold"

_MANUAL_REASON = {"inactive": REASON_MANUAL_INACTIVE, "hold": REASON_MANUAL_HOLD}


AUDIENCE_COLD = "cold"
AUDIENCE_WARM = "warm"
AUDIENCE_KINDS = (AUDIENCE_COLD, AUDIENCE_WARM)

#: The two rules that only make sense for a cold list. ``sent_history`` ("we already
#: wrote to this mailbox once from contacto@") and ``outreach_contacted`` ("an earlier
#: batch reached this mailbox") exist so that cold outreach never insists on a stranger.
#: A warm campaign is addressed to people we have corresponded with on purpose — clients,
#: quote requesters, buyers — so for it those two facts are the selection criterion, not
#: a reason to refuse. Everything else stays: suppression (they asked out), domain
#: suppression, supplier domains, noise mailboxes, ``outreach_replied`` / ``outreach_snoozed``
#: (an explicit outcome, not mere contact), and the manual inactive/hold status.
WARM_WAIVED_REASONS: frozenset[str] = frozenset({REASON_SENT_HISTORY, REASON_OUTREACH_CONTACTED})


def evaluate_campaign_eligibility(
    *,
    contact_email: str,
    institution_name: str | None,
    gate_ctx: GateContext,
    manual_status_by_email: dict[str, str],
    audience_kind: str = AUDIENCE_COLD,
) -> ExportGateResult:
    if audience_kind not in AUDIENCE_KINDS:
        raise ValueError(f"audience_kind must be one of {AUDIENCE_KINDS}, got {audience_kind!r}")
    em = normalize_export_email(contact_email)
    if em and manual_status_by_email.get(em) in ("inactive", "hold"):
        reason = _MANUAL_REASON[manual_status_by_email[em]]
        return ExportGateResult(eligible=False, reasons=(reason,))
    result = evaluate_export_eligibility(
        contact_email=contact_email, institution_name=institution_name, ctx=gate_ctx,
    )
    if audience_kind == AUDIENCE_COLD or result.eligible:
        return result
    remaining = tuple(r for r in result.reasons if r not in WARM_WAIVED_REASONS)
    if remaining == result.reasons:
        return result
    return ExportGateResult(eligible=not remaining, reasons=remaining)
