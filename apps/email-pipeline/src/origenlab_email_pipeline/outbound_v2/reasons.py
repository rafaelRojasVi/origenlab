"""The closed exclusion vocabulary shared by the application and Slice 0.

Every code here appears in ``campaign_recipient_exclusion_reasons_vocabulary``
(``supabase/migrations/20260908120000_slice0_outbound_campaign_content_criteria_exclusions.sql``).
The two lists are kept identical by ``tests/outbound_v2/test_reasons.py``, which reads the
migration — a code the database would reject must never reach a freeze plan.

Seven codes are the original Slice 0 vocabulary; the rest are the reconciliation of
``candidate_export_gate`` and ``outbound_campaign_gate`` onto it.
"""

from __future__ import annotations

# Contact controls (outbound.contact_control).
REASON_BLOCK = "block"
REASON_BLOCK_DOMAIN = "block_domain"
REASON_PRIOR_CONTACT = "prior_contact"
REASON_PRIOR_REPLY = "prior_reply"
REASON_COOLDOWN = "cooldown"

# Campaign policy.
REASON_POLICY_SUPPLIER = "policy_supplier"
REASON_POLICY_NO_CHANNEL = "policy_no_channel"
REASON_POLICY_INTERNAL_DOMAIN = "policy_internal_domain"
REASON_POLICY_NOISE_ADDRESS = "policy_noise_address"
REASON_POLICY_NOISE_ORGANIZATION = "policy_noise_organization"

# Commercial precheck (archive lane).
REASON_PRECHECK_BLOCK = "precheck_block"
REASON_PRECHECK_SWITCH = "precheck_switch"

# Operator sidecar.
REASON_MANUAL_INACTIVE = "manual_inactive"
REASON_MANUAL_HOLD = "manual_hold"

# Data and structure.
REASON_INVALID_ADDRESS = "invalid_address"
REASON_ALREADY_IN_AUDIENCE = "already_in_audience"

#: The database's closed list, in no particular order.
EXCLUSION_VOCABULARY: frozenset[str] = frozenset(
    {
        REASON_BLOCK,
        REASON_BLOCK_DOMAIN,
        REASON_PRIOR_CONTACT,
        REASON_PRIOR_REPLY,
        REASON_COOLDOWN,
        REASON_POLICY_SUPPLIER,
        REASON_POLICY_NO_CHANNEL,
        REASON_POLICY_INTERNAL_DOMAIN,
        REASON_POLICY_NOISE_ADDRESS,
        REASON_POLICY_NOISE_ORGANIZATION,
        REASON_PRECHECK_BLOCK,
        REASON_PRECHECK_SWITCH,
        REASON_MANUAL_INACTIVE,
        REASON_MANUAL_HOLD,
        REASON_INVALID_ADDRESS,
        REASON_ALREADY_IN_AUDIENCE,
    }
)

#: Reasons a recontact override may clear (WORKFLOWS.md §W12). A block, a policy rule or a
#: malformed address is never overridable — an override buys another contact, not a bypass.
OVERRIDABLE_REASONS: frozenset[str] = frozenset(
    {REASON_PRIOR_CONTACT, REASON_PRIOR_REPLY, REASON_COOLDOWN}
)
