"""Pydantic models for Postgres dashboard mirror endpoints (API-3 shared)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from origenlab_email_pipeline.catalog.catalog_mirror_safety import (
    validate_catalog_prose_field,
)

# ---------------------------------------------------------------------------
# Shared mirror disclaimers, constants, and prose helpers
# ---------------------------------------------------------------------------

POSTGRES_MIRROR_NOTE: str = (
    "Espejo Postgres de solo lectura; SQLite/Gmail sigue siendo la fuente operativa. "
    "Los correos nuevos no aparecen aquí hasta ingest Gmail, rebuild del mart y sync del espejo."
)

COMMERCIAL_PURCHASE_DISCLAIMER: str = (
    "Eventos de compra confirmados promovidos desde Gmail/SQLite. "
    "No sustituyen revisión operativa de OC, factura o despacho."
)

COMMERCIAL_DEAL_DISCLAIMER: str = (
    "Espejo Postgres redactado del ledger comercial SQLite. "
    "No incluye cuerpos de correo, IDs de transferencia completos ni rutas locales. "
    "SQLite sigue siendo la fuente operativa."
)

CATALOG_DISCLAIMER: str = (
    "Catálogo operador (espejo Postgres redactado). "
    "Precios de proveedor son datos internos (is_public_safe=false). "
    "SQLite sigue siendo la fuente operativa; no incluye cuerpos de correo ni datos bancarios."
)

LEAD_RESEARCH_DISCLAIMER: str = (
    "Prospectos generados desde investigación y deduplicados contra historial OrigenLab. "
    "Revisión humana requerida antes de cualquier contacto."
)
# Canonical operator-facing text (single sentence break; avoid split-literal join bugs).
assert "OrigenLab. Revisión" in LEAD_RESEARCH_DISCLAIMER
assert "OrigenLab.Revisión" not in LEAD_RESEARCH_DISCLAIMER

# Regression guard: implicit multi-line concat must not yield "OrigenLab.Revisión".
_LEAD_DISCLAIMER_JOINED_ORIGENLAB_RE = re.compile(r"OrigenLab\.Revisión", re.IGNORECASE)


def prepare_lead_research_disclaimer(value: object) -> object:
    """Ensure sentence break after OrigenLab before API/dashboard render."""
    if not isinstance(value, str):
        return value
    return _LEAD_DISCLAIMER_JOINED_ORIGENLAB_RE.sub("OrigenLab. Revisión", value)


def _validate_catalog_product_prose(value: object, info: Any) -> object:
    return validate_catalog_prose_field(value, field=f"product.{info.field_name}")


def _validate_catalog_response_disclaimer(value: object) -> object:
    return validate_catalog_prose_field(value, field="response.disclaimer")


def _validate_lead_research_disclaimer_field(value: object) -> object:
    return prepare_lead_research_disclaimer(value)


def _validate_catalog_history_prose(value: object, info: Any) -> object:
    return validate_catalog_prose_field(value, field=f"commercial_history.{info.field_name}")


# ---------------------------------------------------------------------------
# Dashboard summary, sync meta, health
# ---------------------------------------------------------------------------


class DashboardSummaryResponse(BaseModel):
    contact_count: int = 0
    organization_count: int = 0
    opportunity_signal_count: int = 0
    email_suppression_count: int = 0
    domain_suppression_count: int = 0
    outreach_state_count: int = 0
    commercial_purchase_event_count: int = 0
    commercial_purchase_event_item_count: int = 0
    latest_confirmed_purchase_gross_clp: int | None = None
    tables: dict[str, bool] = Field(default_factory=dict)
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    eventually_consistent: bool = True
    scope: Literal["canonical", "archive"] = "canonical"
    scope_available: bool = True
    scope_note: str = ""
    archive_mirror_counts: dict[str, int] = Field(default_factory=dict)


class DependencyStatus(BaseModel):
    name: str
    status: Literal["ok", "error", "skipped"]
    detail: str = ""


class HealthDependenciesResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    dependencies: list[DependencyStatus]
    postgres_url_redacted: str = ""
    note: str = (
        "API reads Postgres mirrors only. SQLite ingest remains authoritative until cutover."
    )


class DashboardSyncMetaResponse(BaseModel):
    """Latest dashboard Postgres mirror sync (reporting.dashboard_sync_run)."""

    table_available: bool = False
    status: Literal[
        "missing_table", "no_rows", "success", "failed", "dry_run", "unknown"
    ] = "no_rows"
    latest_sync_id: int | None = None
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None
    elapsed_seconds: float | None = None
    postgres_mirror_note: str = POSTGRES_MIRROR_NOTE
    canonical_contact_count: int = 0
    canonical_organization_count: int = 0
    canonical_opportunity_signal_count: int = 0
    archive_contact_count: int = 0
    archive_organization_count: int = 0
    archive_opportunity_signal_count: int = 0
    email_suppression_count: int = 0
    domain_suppression_count: int = 0
    outreach_state_count: int = 0
    error_message: str | None = None


class ContactRow(BaseModel):
    email: str
    contact_name_best: str | None = None
    domain: str | None = None
    organization_name_guess: str | None = None
    organization_type_guess: str | None = None
    first_seen_at: datetime | str | None = None
    last_seen_at: datetime | str | None = None
    total_emails: int | None = None
    confidence_score: float | None = None
    top_equipment_tags: str | None = None


class OrganizationRow(BaseModel):
    domain: str
    organization_name_guess: str | None = None
    organization_type_guess: str | None = None
    first_seen_at: datetime | str | None = None
    last_seen_at: datetime | str | None = None
    total_emails: int | None = None
    total_contacts: int | None = None
    top_equipment_tags: str | None = None
    key_contacts: str | None = None


class PaginatedContactsResponse(BaseModel):
    items: list[ContactRow]
    total: int
    limit: int
    offset: int
    table_available: bool = True
    scope: Literal["canonical", "archive"] = "canonical"
    scope_available: bool = True
    scope_note: str = ""


class PaginatedOrganizationsResponse(BaseModel):
    items: list[OrganizationRow]
    total: int
    limit: int
    offset: int
    table_available: bool = True
    scope: Literal["canonical", "archive"] = "canonical"
    scope_available: bool = True
    scope_note: str = ""


# ---------------------------------------------------------------------------
# Classification (recent / actions)
# ---------------------------------------------------------------------------


class ClassificationSummaryResponse(BaseModel):
    scope: Literal["canonical"] = "canonical"
    table_available: bool = False
    status: Literal["missing_table", "no_rows", "ok"] = "no_rows"
    total_rows: int = 0
    counts_by_label: dict[str, int] = Field(default_factory=dict)
    kpi: dict[str, int] = Field(default_factory=dict)
    disclaimer: str = (
        "Clasificación heurística de QA sobre Gmail canónico (contacto@origenlab.cl). "
        "No es verdad CRM ni decisión automática de envío."
    )


class ClassificationEmailRow(BaseModel):
    email_id: int
    date_iso: datetime | str | None = None
    folder: str | None = None
    from_addr: str | None = None
    to_addrs: str | None = None
    subject: str | None = None
    predicted_label: str
    confidence: str
    ambiguous: bool = False
    recommended_action: str
    etiqueta_ui: str
    evidence: str | None = None
    contact_email: str | None = None
    contact_domain: str | None = None


class ClassificationRecentResponse(BaseModel):
    scope: Literal["canonical"] = "canonical"
    table_available: bool = False
    items: list[ClassificationEmailRow] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    label_filter: str | None = None


class ClassificationActionGroup(BaseModel):
    recommended_action: str
    action_label_es: str
    count: int
    sample_subjects: list[str] = Field(default_factory=list)


class ClassificationActionsResponse(BaseModel):
    scope: Literal["canonical"] = "canonical"
    table_available: bool = False
    groups: list[ClassificationActionGroup] = Field(default_factory=list)
    disclaimer: str = (
        "Acciones sugeridas por heurística; el operador decide en dashboard/CLI."
    )


# ---------------------------------------------------------------------------
# Outbound suppressions, outreach state, readiness
# ---------------------------------------------------------------------------


class EmailSuppressionRow(BaseModel):
    email: str
    suppression_reason_code: str
    suppression_reason_text: str | None = None
    suppression_source: str | None = None
    last_bounced_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    updated_by: str | None = None


class PaginatedEmailSuppressionsResponse(BaseModel):
    items: list[EmailSuppressionRow]
    total: int
    limit: int
    offset: int
    table_available: bool = True


class OutreachContactStateRow(BaseModel):
    contact_email_norm: str
    state: str
    first_contacted_at: datetime | str | None = None
    last_contacted_at: datetime | str | None = None
    source: str | None = None
    notes: str | None = None
    updated_at: datetime | str | None = None
    updated_by: str | None = None
    lead_id: int | None = None


class PaginatedOutreachStateResponse(BaseModel):
    items: list[OutreachContactStateRow]
    total: int
    limit: int
    offset: int
    table_available: bool = True


class OutboundReadinessResponse(BaseModel):
    verdict: Literal["ready", "ready_with_warnings", "not_ready", "unknown"]
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    eventually_consistent: bool = True
    postgres_url_redacted: str = ""
    gmail_user: str = ""
    tables: dict[str, bool] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    mart: dict[str, Any] = Field(default_factory=dict)
    sidecars: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Based on Postgres mirror tables only. Sent-folder ingest and live gates still use "
        "SQLite; sync lag may make this differ from CLI/SQLite operational truth."
    )


# ---------------------------------------------------------------------------
# Commercial purchase events
# ---------------------------------------------------------------------------


class CommercialPurchaseEventItemRow(BaseModel):
    line_number: int
    ref_code: str | None = None
    product_name: str
    brand: str | None = None
    quantity: str | None = None
    net_amount_clp: int | None = None
    evidence_source: str | None = None


class CommercialPurchaseEventRow(BaseModel):
    id: int
    source_email_id: int | None = None
    buyer_org_name: str
    buyer_contact_name: str | None = None
    buyer_contact_email: str | None = None
    buyer_domain: str | None = None
    purchase_status: str
    purchase_status_label_es: str = ""
    oc_number: str
    quote_number: str | None = None
    project_name: str | None = None
    project_code: str | None = None
    net_amount_clp: int | None = None
    iva_amount_clp: int | None = None
    gross_amount_clp: int | None = None
    currency: str = "CLP"
    email_date_iso: str | None = None
    email_subject: str | None = None
    commercial_summary: str | None = None
    suggested_action_es: str | None = None
    line_items: list[CommercialPurchaseEventItemRow] = Field(default_factory=list)
    product_summary: str | None = None


class CommercialPurchaseEventsListResponse(BaseModel):
    table_available: bool = False
    items: list[CommercialPurchaseEventRow] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    disclaimer: str = COMMERCIAL_PURCHASE_DISCLAIMER


class CommercialPurchaseEventDetailResponse(BaseModel):
    table_available: bool = False
    event: CommercialPurchaseEventRow | None = None
    disclaimer: str = COMMERCIAL_PURCHASE_DISCLAIMER


# ---------------------------------------------------------------------------
# Commercial deals
# ---------------------------------------------------------------------------


class CommercialDealProductLineSummary(BaseModel):
    side: str | None = None
    line_kind: str | None = None
    product_name: str | None = None
    category: str | None = None
    quantity: str | None = None
    unit: str | None = None
    currency: str | None = None
    line_net_amount: int | None = None


class CommercialDealCostSummaryByType(BaseModel):
    cost_kind: str
    currency: str
    total_amount_integer: int = 0
    row_count: int = 0


class CommercialDealPaymentSummaryMasked(BaseModel):
    direction: str | None = None
    payment_method: str | None = None
    paid_at: str | None = None
    currency: str | None = None
    amount_gross_integer: int | None = None
    amount_net_integer: int | None = None
    iva_amount_integer: int | None = None
    amount_decimal: str | None = None
    amount_minor: int | None = None
    secondary_currency: str | None = None
    secondary_amount_decimal: str | None = None
    secondary_amount_minor: int | None = None


class CommercialDealRow(BaseModel):
    deal_key: str
    client_org_name: str
    supplier_org_name: str
    deal_status: str
    margin_status: str
    reconciliation_status: str | None = None
    freight_status: str | None = None
    client_sale_net_clp: int | None = None
    client_iva_amount_clp: int | None = None
    client_sale_gross_clp: int | None = None
    client_payment_received_clp: int | None = None
    supplier_invoice_total_decimal: str | None = None
    supplier_invoice_total_minor: int | None = None
    supplier_amount_paid_decimal: str | None = None
    supplier_amount_paid_minor: int | None = None
    margin_net_clp: int | None = None
    margin_pct: float | None = None
    updated_at: str | None = None
    product_line_summaries: list[CommercialDealProductLineSummary] = Field(default_factory=list)
    cost_summaries_by_type: list[CommercialDealCostSummaryByType] = Field(default_factory=list)
    payment_summaries_masked: list[CommercialDealPaymentSummaryMasked] = Field(default_factory=list)
    margin_blockers: list[str] = Field(default_factory=list)


class CommercialDealsListResponse(BaseModel):
    table_available: bool = False
    items: list[CommercialDealRow] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    read_only: bool = True
    disclaimer: str = COMMERCIAL_DEAL_DISCLAIMER


class CommercialDealDetailResponse(BaseModel):
    table_available: bool = False
    deal: CommercialDealRow | None = None
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    read_only: bool = True
    disclaimer: str = COMMERCIAL_DEAL_DISCLAIMER


# ---------------------------------------------------------------------------
# Catalog mirror (products, specs, supplier offers)
# ---------------------------------------------------------------------------


class CatalogProductListItem(BaseModel):
    product_key: str
    display_name: str
    brand: str | None = None
    product_kind: str
    equipment_class: str | None = None
    model_number: str | None = None
    public_summary: str | None = None
    confidence: str

    @field_validator("display_name", "public_summary", mode="before")
    @classmethod
    def _prepare_product_prose(cls, value: object, info) -> object:
        return _validate_catalog_product_prose(value, info)


class CatalogProductAliasRow(BaseModel):
    alias_source: str
    alias_code: str
    alias_kind: str | None = None


class CatalogProductCategoryRow(BaseModel):
    category_key: str
    display_name: str
    equipment_class: str | None = None
    is_primary: bool = False

    @field_validator("display_name", mode="before")
    @classmethod
    def _prepare_display_name(cls, value: object) -> object:
        return validate_catalog_prose_field(value, field="category.display_name")


class CatalogProductSpecRow(BaseModel):
    spec_group: str | None = None
    spec_key: str
    spec_value: str
    spec_value_numeric: float | None = None
    spec_unit: str | None = None
    source: str
    confidence: str

    @field_validator("spec_value", mode="before")
    @classmethod
    def _prepare_spec_value(cls, value: object) -> object:
        return validate_catalog_prose_field(value, field="spec.spec_value")


class CatalogSupplierOfferRow(BaseModel):
    offer_key: str
    supplier_org_name: str | None = None
    supplier_domain: str | None = None
    offer_status: str
    quoted_at: str | None = None
    valid_until: str | None = None
    incoterm: str | None = None
    payment_terms: str | None = None
    delivery_terms: str | None = None
    currency: str | None = None
    quantity_offered: str | None = None
    availability_note: str | None = None
    confidence: str

    @field_validator("payment_terms", "delivery_terms", "availability_note", mode="before")
    @classmethod
    def _prepare_supplier_offer_prose(cls, value: object) -> object:
        return validate_catalog_prose_field(value, field="supplier_offer.prose")


class CatalogPriceSnapshotRow(BaseModel):
    snapshot_key: str
    snapshot_kind: str
    offer_key: str | None = None
    currency: str | None = None
    amount_decimal: str | None = None
    amount_minor: int | None = None
    amount_clp_integer: int | None = None
    quantity: str | None = None
    unit: str | None = None
    incoterm: str | None = None
    price_notes: str | None = None
    is_public_safe: bool = False
    confidence: str
    observed_at: str | None = None

    @field_validator("price_notes", mode="before")
    @classmethod
    def _prepare_price_notes(cls, value: object) -> object:
        return validate_catalog_prose_field(value, field="price_snapshot.price_notes")


class CatalogCommercialLinkRow(BaseModel):
    link_kind: str
    link_ref: str
    confidence: str


class CatalogProductCommercialHistoryRow(BaseModel):
    history_key: str
    deal_key: str
    deal_label: str
    client_org_name: str | None = None
    supplier_org_name: str | None = None
    line_side: str
    line_kind: str
    quantity: str | None = None
    unit: str | None = None
    currency: str | None = None
    amount_net_clp: int | None = None
    amount_decimal: str | None = None
    amount_minor: int | None = None
    unit_price_decimal: str | None = None
    total_price_decimal: str | None = None
    margin_status: str | None = None
    deal_status: str | None = None
    is_public_safe: bool = False
    source_summary: str | None = None
    confidence: str

    @field_validator(
        "deal_label",
        "source_summary",
        "client_org_name",
        "supplier_org_name",
        mode="before",
    )
    @classmethod
    def _prepare_history_prose(cls, value: object, info) -> object:
        return _validate_catalog_history_prose(value, info)


class CatalogProductDetail(BaseModel):
    product_key: str
    display_name: str
    brand: str | None = None
    manufacturer_name: str | None = None
    product_kind: str
    equipment_class: str | None = None
    model_number: str | None = None
    default_unit: str | None = None
    website_slug: str | None = None
    website_product_id: str | None = None
    public_summary: str | None = None
    is_active: bool = True
    confidence: str
    aliases: list[CatalogProductAliasRow] = Field(default_factory=list)
    categories: list[CatalogProductCategoryRow] = Field(default_factory=list)
    specs: list[CatalogProductSpecRow] = Field(default_factory=list)
    supplier_offers: list[CatalogSupplierOfferRow] = Field(default_factory=list)
    price_snapshots: list[CatalogPriceSnapshotRow] = Field(default_factory=list)
    commercial_links: list[CatalogCommercialLinkRow] = Field(default_factory=list)
    commercial_history: list[CatalogProductCommercialHistoryRow] = Field(default_factory=list)

    @field_validator("display_name", "public_summary", "manufacturer_name", mode="before")
    @classmethod
    def _prepare_product_prose(cls, value: object, info) -> object:
        return _validate_catalog_product_prose(value, info)


class CatalogProductsListResponse(BaseModel):
    table_available: bool = False
    items: list[CatalogProductListItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    read_only: bool = True
    disclaimer: str = CATALOG_DISCLAIMER

    @field_validator("disclaimer", mode="before")
    @classmethod
    def _prepare_disclaimer(cls, value: object) -> object:
        return _validate_catalog_response_disclaimer(value)


class CatalogProductDetailResponse(BaseModel):
    table_available: bool = False
    product: CatalogProductDetail | None = None
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    read_only: bool = True
    disclaimer: str = CATALOG_DISCLAIMER

    @field_validator("disclaimer", mode="before")
    @classmethod
    def _prepare_disclaimer(cls, value: object) -> object:
        return _validate_catalog_response_disclaimer(value)


# ---------------------------------------------------------------------------
# Lead research / prospect detail
# ---------------------------------------------------------------------------


class LeadProspectListItem(BaseModel):
    prospect_key: str
    organization_name: str
    contact_name: str | None = None
    email: str | None = None
    domain: str | None = None
    sector: str | None = None
    region: str | None = None
    buyer_type: str | None = None
    product_angle: str | None = None
    final_score: int = 0
    classification: str
    status: str
    spanish_message_angle: str | None = None
    recommended_next_action: str | None = None
    risk_flags: str | None = None
    evidence_url: str | None = None
    is_blocked: bool = False
    campaign_bucket: str | None = None
    source_type: str | None = None
    dataset_label: str | None = None
    gmail_first_contacted_at: str | None = None
    gmail_last_contacted_at: str | None = None
    gmail_sent_count: int | None = None
    gmail_received_count: int | None = None
    gmail_latest_subject_safe: str | None = None
    commercial_action_bucket: str | None = None
    operational_next_action: str | None = None


class LeadProspectEvidenceRow(BaseModel):
    evidence_kind: str = "public_url"
    evidence_url: str | None = None
    evidence_note: str | None = None
    source: str | None = None
    confidence: str | None = None


class LeadProspectRecommendationRow(BaseModel):
    campaign_bucket: str | None = None
    recommended_message_angle: str | None = None
    recommended_next_action: str | None = None
    why_this_lead: str | None = None
    suggested_subject: str | None = None
    suggested_body_preview: str | None = None
    safety_note: str | None = None


class LeadProspectBlockReasonRow(BaseModel):
    reason_code: str
    reason_label: str | None = None


class LeadProspectDetail(BaseModel):
    prospect_key: str
    organization_name: str
    contact_name: str | None = None
    email: str | None = None
    domain: str | None = None
    sector: str | None = None
    region: str | None = None
    buyer_type: str | None = None
    likely_need: str | None = None
    product_angle: str | None = None
    evidence_url: str | None = None
    evidence_note: str | None = None
    source: str | None = None
    final_score: int = 0
    confidence: str | None = None
    classification: str
    spanish_message_angle: str | None = None
    risk_flags: str | None = None
    block_or_review_reason: str | None = None
    recommended_next_action: str | None = None
    status: str
    campaign_bucket: str | None = None
    is_blocked: bool = False
    source_type: str | None = None
    dataset_label: str | None = None
    gmail_first_contacted_at: str | None = None
    gmail_last_contacted_at: str | None = None
    gmail_sent_count: int | None = None
    gmail_received_count: int | None = None
    gmail_latest_subject_safe: str | None = None
    commercial_action_bucket: str | None = None
    operational_next_action: str | None = None


class LeadProspectsListResponse(BaseModel):
    table_available: bool = False
    items: list[LeadProspectListItem] = Field(default_factory=list)
    total: int = 0
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    read_only: bool = True
    disclaimer: str = LEAD_RESEARCH_DISCLAIMER

    @field_validator("disclaimer", mode="before")
    @classmethod
    def _prepare_disclaimer(cls, value: object) -> object:
        return _validate_lead_research_disclaimer_field(value)


class LeadProspectDetailResponse(BaseModel):
    table_available: bool = False
    prospect: LeadProspectDetail | None = None
    evidence: list[LeadProspectEvidenceRow] = Field(default_factory=list)
    recommendation: LeadProspectRecommendationRow | None = None
    block_reasons: list[LeadProspectBlockReasonRow] = Field(default_factory=list)
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    read_only: bool = True
    disclaimer: str = LEAD_RESEARCH_DISCLAIMER

    @field_validator("disclaimer", mode="before")
    @classmethod
    def _prepare_disclaimer(cls, value: object) -> object:
        return _validate_lead_research_disclaimer_field(value)


class LeadResearchSummaryResponse(BaseModel):
    table_available: bool = False
    total: int = 0
    review_count: int = 0
    blocked_count: int = 0
    net_new_safe: int = 0
    gmail_historico: int = 0
    followup_antiguo: int = 0
    caso_activo: int = 0
    public_tender_review: int = 0
    same_domain_review: int = 0
    research_needed: int = 0
    ready_to_contact: int = 0
    already_contacted: int = 0
    needs_email_enrichment: int = 0
    tender_opportunity: int = 0
    review_history: int = 0
    followup_eligible: int = 0
    last_batch_row_count: int | None = None
    data_source: Literal["postgres_mirror"] = "postgres_mirror"
    read_only: bool = True
    disclaimer: str = LEAD_RESEARCH_DISCLAIMER

    @field_validator("disclaimer", mode="before")
    @classmethod
    def _prepare_disclaimer(cls, value: object) -> object:
        return _validate_lead_research_disclaimer_field(value)
