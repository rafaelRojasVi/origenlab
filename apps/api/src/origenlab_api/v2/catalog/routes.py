"""Catalog routes, mounted only behind ORIGENLAB_V2_QUOTING_ENABLED.

Reads (`/v2/catalog/*`): every handler redacts cost fields for the viewer role before answering.

Commands (`/v2/commands/<catalog-command>`): every route requires an active `sales` or `admin`
operator (viewer → 403 `role_may_not_decide`), an `Idempotency-Key`, and a body Pydantic checks
with `extra="forbid"`; the handlers live in `commands.V2CatalogRepository`.
"""
from __future__ import annotations

import datetime as dt
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from origenlab_api.v2.catalog.fx import FxUnavailable
from origenlab_api.v2.catalog.keys import PARAMETER_KEYS, PRODUCT_KINDS
from origenlab_api.v2.catalog.reads import V2CatalogReads
from origenlab_api.v2.catalog.redaction import redact_costs
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey
from origenlab_api.v2.crm_authoring_routes import _run

catalog_read_router = APIRouter(prefix="/v2/catalog")
catalog_command_router = APIRouter(prefix="/v2/commands", tags=["v2-catalog-commands"])


def _reads(request: Request) -> V2CatalogReads:
    return request.app.state.catalog_reads


@catalog_read_router.get("/products")
def search_products(request: Request, operator: Operator, q: str | None = Query(None, max_length=120),
                    supplier: UUID | None = None, kind: str | None = Query(None),
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
    if kind is not None and kind not in PRODUCT_KINDS:
        raise HTTPException(422, detail={"code": "unknown_product_kind", "message": "unknown product kind"})
    return redact_costs(_reads(request).search_products(q, supplier, kind, limit, offset), operator.role)


@catalog_read_router.get("/products/{product_id}")
def product_detail(product_id: UUID, request: Request, operator: Operator) -> dict:
    found = _reads(request).product_detail(product_id)
    if found is None:
        raise HTTPException(404, detail={"code": "product_not_found", "message": "product not found"})
    return redact_costs(found, operator.role)


@catalog_read_router.get("/suppliers/{organization_id}/terms")
def supplier_terms(organization_id: UUID, request: Request, operator: Operator) -> dict:
    found = _reads(request).supplier_terms(organization_id)
    if found is None:
        raise HTTPException(404, detail={"code": "supplier_terms_not_found", "message": "supplier terms not found"})
    return redact_costs(found, operator.role)


@catalog_read_router.get("/fx")
def fx_rate(request: Request, operator: Operator, currency: Literal["USD", "EUR"],
            date: dt.date | None = None) -> dict:
    # Public Banco Central data: every active role may read it.
    fx = request.app.state.catalog_fx
    try:
        quote = fx.rate(currency, date or fx.today())
    except FxUnavailable:
        raise HTTPException(503, detail={"code": "fx_unavailable",
                                         "message": "exchange rate unavailable"}) from None
    return {**quote, "clp_per_unit": str(quote["clp_per_unit"]), "as_of": quote["as_of"].isoformat()}


@catalog_read_router.get("/parameters")
def parameters(request: Request, operator: Operator) -> dict:
    # Costing parameters are commercial configuration as a whole; a viewer gets no redacted copy.
    if operator.role == "viewer":
        raise HTTPException(403, detail={"code": "role_may_not_view_costs",
                                         "message": "this role may not view cost parameters"})
    return redact_costs(_reads(request).parameters(), operator.role)


# ──────────────────────────────────────────────────────── command bodies ──

Currency = Literal["EUR", "USD", "CLP"]
ProductKind = Literal["equipment", "accessory", "consumable", "spare_part", "service"]
PriceKind = Literal["dealer_net", "list", "map", "supplier_offer", "order_confirmation", "purchase_order",
                    "costing_sheet", "negotiated"]
Route = Literal["import_courier", "import_freight", "domestic"]
Country = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
_Text500 = Annotated[str, StringConstraints(min_length=1, max_length=500)]
_Text200 = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_Incoterm = Annotated[str, StringConstraints(min_length=1, max_length=20)]


class _Base(BaseModel):
    """As `crm_authoring_routes._Base`, but the note is optional: the click is the decision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: Annotated[str | None, Field(default=None, min_length=1, max_length=2000)] = None


class SpecItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label_es: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    value: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    unit: Annotated[str, StringConstraints(max_length=20)] | None = None
    source: Literal["import", "machine", "operator"] = "operator"

    @field_validator("unit")
    @classmethod
    def _blank_unit_is_none(cls, v: str | None) -> str | None:
        return v or None


class CreateProductBody(_Base):
    manufacturer_organization_id: UUID
    model_number: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    name: _Text500 | None = None
    name_es: _Text500 | None = None
    description_es: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None
    product_kind: ProductKind | None = None
    category_es: _Text200 | None = None
    specs: list[SpecItem] = Field(default_factory=list, max_length=100)
    weight_kg: Decimal | None = Field(None, ge=0)
    length_cm: Decimal | None = Field(None, ge=0)
    width_cm: Decimal | None = Field(None, ge=0)
    height_cm: Decimal | None = Field(None, ge=0)
    origin_country: Country | None = None
    is_dangerous_goods: bool = False

    @field_validator("model_number", check_fields=False)
    @classmethod
    def _model_has_a_character(cls, v: str | None) -> str | None:
        # model_key drops spaces, dashes, underscores, dots and slashes; a model made only of
        # those would have an empty key and could not be told apart from another.
        if v is not None and not any(c.isalnum() for c in v):
            raise ValueError("model_number must contain a letter or a digit")
        return v


class UpdateProductBody(CreateProductBody):
    """Every field optional except the product and its version; a field left out is unchanged."""

    product_id: UUID
    expected_version: int
    manufacturer_organization_id: UUID | None = None  # type: ignore[assignment]
    model_number: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None  # type: ignore[assignment]
    specs: list[SpecItem] | None = Field(None, max_length=100)  # type: ignore[assignment]
    is_dangerous_goods: bool | None = None  # type: ignore[assignment]
    active: bool | None = None


class ConfirmProductContentBody(_Base):
    product_id: UUID
    expected_version: int


class RecordSupplierCostBody(_Base):
    product_id: UUID
    supplier_organization_id: UUID
    price: Decimal = Field(ge=0)
    currency: Currency
    price_kind: PriceKind
    as_of: AwareDatetime  # an instant: a timestamp without its offset is refused
    list_price: Decimal | None = Field(None, ge=0)
    discount_pct: Decimal | None = Field(None, ge=0, lt=1)
    incoterm: _Incoterm | None = None
    valid_until: date | None = None
    min_qty: Decimal | None = Field(None, gt=0)
    is_stale: bool = False
    source_document: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None


class SetSupplierTermsBody(_Base):
    supplier_organization_id: UUID
    expected_version: int | None = None  # None = create
    currency: Currency
    route: Route
    origin_country: Country | None = None
    incoterm: _Incoterm | None = None
    default_discount_pct: Decimal | None = Field(None, ge=0, le=Decimal("0.99"))
    packing_pct: Decimal | None = Field(None, ge=0, le=Decimal("0.5"))
    map_enforced: bool = False
    default_lead_time_es: _Text200 | None = None
    notes: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None


class SetCostParameterBody(_Base):
    key: str
    value: Decimal = Field(ge=0)
    reason: Annotated[str, StringConstraints(min_length=3, max_length=500)]
    #: The current row the operator saw; if another row has become current since, 409 `parameter_changed`.
    expected_current_id: UUID | None = None

    @field_validator("key")
    @classmethod
    def _known_key(cls, v: str) -> str:
        if v not in PARAMETER_KEYS:
            raise ValueError("unknown cost parameter")
        return v


class RecordFxRateBody(_Base):
    currency: Literal["USD", "EUR"]
    rate_date: date
    clp_per_unit: Decimal = Field(gt=0)
    reason: Annotated[str, StringConstraints(min_length=3, max_length=500)]


class ReviewDocumentLineBody(_Base):
    document_line_id: UUID
    qty: Decimal | None = Field(None, gt=0)
    unit_price: Decimal | None = Field(None, ge=0)
    line_total: Decimal | None = Field(None, ge=0)
    optional: bool | None = None
    review_note: Annotated[str, StringConstraints(min_length=3, max_length=1000)]


# ──────────────────────────────────────────────────────────── command routes ──

def _command(command_name: str, body: BaseModel, request: Request, operator: Any,
             idempotency_key: str | None) -> dict[str, Any]:
    return _run(command_name=command_name, body=body, fields=body.model_dump(mode="json"),
                repo=request.app.state.catalog_repository, operator=operator, idempotency_key=idempotency_key)


@catalog_command_router.post("/create-product")
def create_product(body: CreateProductBody, request: Request, operator: Deciding,
                   idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("create-product", body, request, operator, idempotency_key)


@catalog_command_router.post("/update-product")
def update_product(body: UpdateProductBody, request: Request, operator: Deciding,
                   idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("update-product", body, request, operator, idempotency_key)


@catalog_command_router.post("/confirm-product-content")
def confirm_product_content(body: ConfirmProductContentBody, request: Request, operator: Deciding,
                            idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("confirm-product-content", body, request, operator, idempotency_key)


@catalog_command_router.post("/record-supplier-cost")
def record_supplier_cost(body: RecordSupplierCostBody, request: Request, operator: Deciding,
                         idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("record-supplier-cost", body, request, operator, idempotency_key)


@catalog_command_router.post("/set-supplier-terms")
def set_supplier_terms(body: SetSupplierTermsBody, request: Request, operator: Deciding,
                       idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("set-supplier-terms", body, request, operator, idempotency_key)


@catalog_command_router.post("/set-cost-parameter")
def set_cost_parameter(body: SetCostParameterBody, request: Request, operator: Deciding,
                       idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("set-cost-parameter", body, request, operator, idempotency_key)


@catalog_command_router.post("/record-fx-rate")
def record_fx_rate(body: RecordFxRateBody, request: Request, operator: Deciding,
                   idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("record-fx-rate", body, request, operator, idempotency_key)


@catalog_command_router.post("/review-document-line")
def review_document_line(body: ReviewDocumentLineBody, request: Request, operator: Deciding,
                         idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("review-document-line", body, request, operator, idempotency_key)
