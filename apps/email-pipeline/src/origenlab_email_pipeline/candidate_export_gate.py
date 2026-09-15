"""Shared pre-export eligibility for cold outreach (lead path + contact_master path).

Single policy implementation: operator CLIs and read modules must not duplicate these rules.
Reuses email/domain suppression, active-commercial holds, optional prior-outreach history,
supplier_master, and noise heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.marketing_contact_noise import (
    marketing_outreach_noise_email,
    marketing_outreach_noise_organization_guess,
)
from origenlab_email_pipeline.marketing_supplier_domains import is_supplier_email_domain

# Stable reason codes (CSV, logs, tests).
REASON_INVALID_EMAIL = "invalid_email"
REASON_INTERNAL_DOMAIN = "internal_domain"
REASON_SUPPRESSION = "suppression"
REASON_DOMAIN_SUPPRESSION = "domain_suppression"
REASON_ACTIVE_COMMERCIAL_ENGAGEMENT = "active_commercial_engagement"
REASON_SENT_HISTORY = "sent_history"
REASON_OUTREACH_CONTACTED = "outreach_contacted"
REASON_OUTREACH_REPLIED = "outreach_replied"
REASON_OUTREACH_SNOOZED = "outreach_snoozed"
REASON_SUPPLIER_DOMAIN = "supplier_domain"
REASON_NOISE_EMAIL = "noise_email"
REASON_NOISE_ORGANIZATION = "noise_organization"

_OUTREACH_REASON = {
    "contacted": REASON_OUTREACH_CONTACTED,
    "replied": REASON_OUTREACH_REPLIED,
    "snoozed": REASON_OUTREACH_SNOOZED,
}


@dataclass(frozen=True)
class GateContext:
    """Inputs for eligibility. Build once per export run.

    ``allow_prior_outreach_history`` is deliberately narrow: when true, a
    previous marketing send (Gmail Sent) and historical ``contacted``/``replied``
    outreach state are informational rather than permanent blockers. Explicit
    suppression, domain suppression, active-commercial holds, ``snoozed`` state,
    internal/supplier/noise checks, and campaign-layer manual hold/inactive still block.
    """

    sent_recipient_norms: frozenset[str]
    suppressed_norms: frozenset[str]
    outreach_state_by_email: dict[str, str]
    supplier_domains: frozenset[str]
    blocked_domains: frozenset[str]
    #: Registrable domains (and subdomains) blocked via ``contact_domain_suppression``.
    suppressed_contact_domains: frozenset[str] = frozenset()
    #: Derived from durable CRM active opportunities / active quotations.
    commercial_hold_norms: frozenset[str] = frozenset()
    skip_noise_filter: bool = False
    skip_supplier_domain_filter: bool = False
    #: Tighter ``marketing_contact_noise`` rules for ``contact_master`` mail-graph exports.
    strict_contact_graph_noise: bool = False
    #: Allow repeat marketing to previously-sent/contacted/replied recipients.
    allow_prior_outreach_history: bool = False


@dataclass(frozen=True)
class ExportGateResult:
    eligible: bool
    """If not eligible, a single-element tuple with the first triggered rule (evaluation order fixed)."""

    reasons: tuple[str, ...]


def email_domain_under_operator_domain_suppression(email_domain: str, suppressed: frozenset[str]) -> bool:
    """True when ``email_domain`` matches or is a subdomain of a suppressed registrable domain."""
    d = (email_domain or "").strip().lower()
    if not d or not suppressed:
        return False
    if d in suppressed:
        return True
    return any(d.endswith("." + s) for s in suppressed if s)


def normalize_export_email(contact_email: str) -> str | None:
    """Match ``marketing_export_context.norm_lead_email`` / contact_master export: first mailbox."""
    raw = (contact_email or "").strip()
    if not raw:
        return None
    found = emails_in(raw)
    if not found:
        return None
    return found[0]


def evaluate_export_eligibility(
    *,
    contact_email: str,
    institution_name: str | None,
    ctx: GateContext,
) -> ExportGateResult:
    em = normalize_export_email(contact_email)
    if not em:
        return ExportGateResult(eligible=False, reasons=(REASON_INVALID_EMAIL,))

    if "@" not in em:
        return ExportGateResult(eligible=False, reasons=(REASON_INVALID_EMAIL,))

    dom = em.rsplit("@", 1)[-1].lower()
    if dom in ctx.blocked_domains:
        return ExportGateResult(eligible=False, reasons=(REASON_INTERNAL_DOMAIN,))

    if em in ctx.suppressed_norms:
        return ExportGateResult(eligible=False, reasons=(REASON_SUPPRESSION,))

    if email_domain_under_operator_domain_suppression(dom, ctx.suppressed_contact_domains):
        return ExportGateResult(eligible=False, reasons=(REASON_DOMAIN_SUPPRESSION,))

    # A current consultation / opportunity / quotation is a temporary marketing
    # hold. This is distinct from historical Sent memory and remains blocking in
    # repeat-campaign mode.
    if em in ctx.commercial_hold_norms:
        return ExportGateResult(
            eligible=False,
            reasons=(REASON_ACTIVE_COMMERCIAL_ENGAGEMENT,),
        )

    # Historical campaign delivery is not an unsubscribe. Keep the legacy
    # fail-closed behaviour by default, but let an explicitly-declared repeat
    # campaign treat prior Sent history as informational.
    if not ctx.allow_prior_outreach_history and em in ctx.sent_recipient_norms:
        return ExportGateResult(eligible=False, reasons=(REASON_SENT_HISTORY,))

    st = (ctx.outreach_state_by_email or {}).get(em)
    if st:
        # ``snoozed`` remains a deliberate temporary operator block even for a
        # repeat campaign. ``contacted``/``replied`` only describe historical
        # outreach when repeat-campaign mode is explicitly enabled.
        if st == "snoozed":
            return ExportGateResult(eligible=False, reasons=(REASON_OUTREACH_SNOOZED,))
        if not ctx.allow_prior_outreach_history:
            reason = _OUTREACH_REASON.get(st)
            if reason:
                return ExportGateResult(eligible=False, reasons=(reason,))

    if not ctx.skip_supplier_domain_filter and ctx.supplier_domains:
        if is_supplier_email_domain(em, ctx.supplier_domains):
            return ExportGateResult(eligible=False, reasons=(REASON_SUPPLIER_DOMAIN,))

    if not ctx.skip_noise_filter:
        if marketing_outreach_noise_email(
            em, strict_contact_graph=ctx.strict_contact_graph_noise
        ):
            return ExportGateResult(eligible=False, reasons=(REASON_NOISE_EMAIL,))
        if marketing_outreach_noise_organization_guess(institution_name or ""):
            return ExportGateResult(eligible=False, reasons=(REASON_NOISE_ORGANIZATION,))

    return ExportGateResult(eligible=True, reasons=())
