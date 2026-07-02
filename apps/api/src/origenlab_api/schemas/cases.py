"""Warm commercial case queue (read-only previews)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

WarmCaseCategory = Literal[
    "client_opportunity",
    "client_response",
    "supplier_quote_received",
    "supplier_followup",
    "payment_admin",
    "logistics_admin",
    "internal_admin",
    "system_noise",
    "bounce_problem",
    "deal_evidence_candidate",
    "quote_sent",
    "waiting_supplier",
    "waiting_client",
    "campaign_outreach",
    "waiting_campaign_reply",
    "auto_acknowledgement",
    # Legacy aliases accepted as inputs and from old mirror/promotion rows.
    "client_reply",
    "supplier_reply",
    "bounce",
    "opportunity",
    "auto_reply",
    "vendor_logistics",
]

WarmCaseStatus = Literal["new", "open", "waiting", "quoted", "problem"]

CANONICAL_WARM_CASE_CATEGORIES: tuple[str, ...] = (
    "client_opportunity",
    "client_response",
    "supplier_quote_received",
    "supplier_followup",
    "payment_admin",
    "logistics_admin",
    "internal_admin",
    "system_noise",
    "bounce_problem",
    "deal_evidence_candidate",
    "quote_sent",
    "waiting_supplier",
    "waiting_client",
    "campaign_outreach",
    "waiting_campaign_reply",
    "auto_acknowledgement",
)

LEGACY_WARM_CASE_CATEGORY_ALIASES: dict[str, str] = {
    "vendor_logistics": "logistics_admin",
    "auto_reply": "system_noise",
    "bounce": "bounce_problem",
    "client_reply": "client_response",
    "supplier_reply": "supplier_followup",
    "opportunity": "client_opportunity",
}

WARM_CASE_CATEGORIES: frozenset[str] = frozenset(
    {*CANONICAL_WARM_CASE_CATEGORIES, *LEGACY_WARM_CASE_CATEGORY_ALIASES.keys()}
)


class WarmCasesMeta(BaseModel):
    data_source: Literal["sqlite", "postgres_mirror"] = "sqlite"
    read_only: bool = True
    reduced_mode: bool = False
    count: int = 0
    enrichment_available: bool = False
    note: str = ""
    canonical_categories: list[str] = Field(
        default_factory=lambda: list(CANONICAL_WARM_CASE_CATEGORIES)
    )
    legacy_category_aliases: dict[str, str] = Field(
        default_factory=lambda: dict(LEGACY_WARM_CASE_CATEGORY_ALIASES)
    )


class WarmCaseItem(BaseModel):
    case_id: str
    last_email_id: int
    last_seen_at: str | None = None
    account_name: str = ""
    contact_email: str = ""
    subject: str = ""
    category: WarmCaseCategory
    status: WarmCaseStatus
    next_action: str = ""
    equipment_signal: str = ""
    snippet: str = ""
    body_snippet: str = Field(default="", exclude=True)
    source_file: str = Field(default="", exclude=True)
    recipients_preview: str = Field(default="", exclude=True)
    sender_preview: str = Field(default="", exclude=True)
    gmail_url: str | None = None
    grouped_email_count: int = 1


class WarmCasesResponse(BaseModel):
    meta: WarmCasesMeta
    items: list[WarmCaseItem] = Field(default_factory=list)
