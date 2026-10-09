"""Catalog routes, mounted only behind ORIGENLAB_V2_QUOTING_ENABLED.

Reads (`/v2/catalog/*`): every handler redacts cost fields for the viewer role before answering.

Commands (`/v2/commands/<catalog-command>`): every route requires an active `sales` or `admin`
operator (viewer → 403 `role_may_not_decide`), an `Idempotency-Key`, and a body Pydantic checks
with `extra="forbid"`; the handlers live in `commands.V2CatalogRepository`. The one multipart
command, `add-product-image`, checks the operator and the key before it reads a byte of the body.

Images (ARCHITECTURE §7): the bucket is private; the API stores the bytes and a browser gets only
a signed URL valid for at most ten minutes, minted after the operator is authorized for that
image (`GET /v2/catalog/images/{image_id}/url`). No image bytes are served from here.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from origenlab_api.v2.catalog.fx import FxUnavailable
from origenlab_api.v2.catalog.keys import PARAMETER_KEYS, model_key as normalize_model_key, PRODUCT_KINDS, LabdeliveryRefused, refuse_labdelivery
from origenlab_api.v2.catalog.reads import V2CatalogReads
from origenlab_api.v2.catalog.redaction import redact_costs
from origenlab_api.v2.catalog.storage import (
    MAX_IMAGE_BYTES,
    SIGNED_URL_SECONDS,
    CatalogStorage,
    ImageRefused,
    StorageUnavailable,
    check_image,
    image_path,
)
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, require_idempotency_key
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
    # Hidden images (each with its status) only for the roles that may un-hide them.
    found = _reads(request).product_detail(product_id, include_hidden=operator.role in ("sales", "admin"))
    if found is None:
        raise HTTPException(404, detail={"code": "product_not_found", "message": "product not found"})
    return redact_costs(found, operator.role)


@catalog_read_router.get("/price-history")
def price_history(request: Request, operator: Operator, model_key: str = Query(..., max_length=120),
                  limit: int = Query(20, ge=1, le=100)) -> dict:
    # Quoted sell prices are not costs: every authenticated role sees the history, unredacted.
    key = normalize_model_key(model_key)
    if key is None:
        raise HTTPException(422, detail={"code": "invalid_model_key", "message": "model_key has no letter or digit"})
    return _reads(request).price_history(key, limit)


@catalog_read_router.get("/suppliers")
def suppliers(request: Request, operator: Operator) -> dict:
    # Names and product counts only: no cost field, so nothing to redact for the viewer.
    return _reads(request).suppliers()


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


_IMAGE_NOT_FOUND = {"code": "image_not_found", "message": "image not found"}
_STORAGE_UNAVAILABLE = {"code": "storage_unavailable", "message": "image storage is unavailable"}


def _storage(request: Request) -> CatalogStorage:
    storage = getattr(request.app.state, "catalog_storage", None)
    if storage is None:
        raise HTTPException(503, detail=_STORAGE_UNAVAILABLE)
    return storage


@catalog_read_router.get("/images/{image_id}/url")
def image_url(image_id: UUID, request: Request, response: Response, operator: Operator) -> dict:
    """A signed URL for one image, valid `SIGNED_URL_SECONDS` (10 minutes, ARCHITECTURE §7).

    Every active role may see a product's visible images. A hidden image is signed only for
    sales and admin (who may un-hide it); a viewer gets the same 404 as for an image that does
    not exist, so the answer does not say that it does.
    """
    found = _reads(request).image(image_id)
    if found is None or (found["status"] == "hidden" and operator.role == "viewer"):
        raise HTTPException(404, detail=_IMAGE_NOT_FOUND)
    try:
        url = _storage(request).signed_url(found["storage_path"], SIGNED_URL_SECONDS)
    except StorageUnavailable:
        raise HTTPException(503, detail=_STORAGE_UNAVAILABLE) from None
    response.headers["Cache-Control"] = "no-store"  # the URL is a short-lived credential
    return {"url": url, "expires_in": SIGNED_URL_SECONDS}


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


ImageSource = Literal["upload", "datasheet", "manufacturer_site"]
_Caption = Annotated[str, StringConstraints(max_length=500)]
_SourceUrl = Annotated[str, StringConstraints(max_length=2000, pattern=r"^https?://[^\s]+$")]


class AddProductImageForm(BaseModel):
    """The text fields of the `add-product-image` multipart form. A blank optional field is absent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: UUID
    caption_es: _Caption | None = None
    source: ImageSource = "upload"
    source_url: _SourceUrl | None = None

    @field_validator("caption_es", "source_url", mode="before")
    @classmethod
    def _blank_is_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v


class AddProductImageFields(BaseModel):
    """What `add-product-image` binds and its idempotency digest covers: the image's hash, never its bytes.

    The same bytes and form under the same key digest the same, so a retried upload replays.
    """

    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    storage_path: str
    caption_es: _Caption | None = None
    source: ImageSource = "upload"
    source_url: _SourceUrl | None = None


class UpdateProductImageBody(_Base):
    """Confirm or hide an image, re-caption it (blank clears) or move it; a field left out is unchanged.

    `proposed` is not settable: it means a machine suggested the image and no one has decided.
    """

    image_id: UUID
    expected_version: int
    sort_order: int | None = Field(None, ge=0, le=10_000)
    caption_es: _Caption | None = None
    status: Literal["confirmed", "hidden"] | None = None


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


@catalog_command_router.post("/update-product-image")
def update_product_image(body: UpdateProductImageBody, request: Request, operator: Deciding,
                         idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("update-product-image", body, request, operator, idempotency_key)


#: One image of at most 8 MiB plus its multipart envelope: boundaries, part headers, short fields.
MAX_UPLOAD_BODY_BYTES = MAX_IMAGE_BYTES + 64 * 1024


def _image_too_large() -> HTTPException:
    return HTTPException(413, detail={"code": "image_too_large", "message": "the image is larger than 8 MiB"})


async def _read_capped_body(request: Request, max_bytes: int) -> bytes:
    """The request body, refused as 413 as soon as it is known to exceed `max_bytes`.

    A declared Content-Length over the cap is refused before anything is read; the stream is
    counted as it arrives too, so a chunked or understated body cannot be buffered unbounded.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_large = int(declared) > max_bytes
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_content_length",
                                             "message": "the Content-Length header is not a number"}) from None
        if too_large:
            raise _image_too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise _image_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _invalid_multipart() -> HTTPException:
    return HTTPException(400, detail={"code": "invalid_multipart",
                                      "message": "the body is not a valid multipart form with one image"})


async def _parse_upload_form(request: Request, body: bytes) -> FormData:
    """Parse the already-capped body as multipart: one file part at most, a handful of fields.

    A body the parser cannot read is 400 `invalid_multipart`. Starlette turns its own limits
    (too many files or fields, no boundary) into a bare 400, but lets the parser's errors
    (`python_multipart` `MultipartParseError`, a `ValueError`) escape as a 500; both end here.
    """
    async def replay() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        return await Request(request.scope, replay).form(max_files=1, max_fields=8)
    except (ValueError, StarletteHTTPException):
        raise _invalid_multipart() from None


def _form_invalid(errors: list[dict[str, Any]]) -> RequestValidationError:
    # Only what the production envelope reports: never the submitted value (it may be a file).
    return RequestValidationError([{k: e[k] for k in ("type", "loc", "msg") if k in e} for e in errors])


def _labdelivery_refused() -> HTTPException:
    return HTTPException(422, detail={"code": "labdelivery_refused", "message": "input of Labdelivery origin is refused"})


def _store_image(request: Request, parsed: AddProductImageForm, data: bytes,
                 declared: str | None) -> AddProductImageFields:
    """Check the bytes, find the product, hash, and put the object — in the threadpool, off the event loop.

    Hashing up to 8 MiB and the Storage request both block; neither may stall other requests.
    """
    try:
        content_type = check_image(data, declared)
    except ImageRefused as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from None
    if not _reads(request).product_exists(parsed.product_id):
        # Checked before the upload, so a mistyped product leaves no object behind; the command
        # checks again under its lock.
        raise HTTPException(404, detail={"code": "product_not_found", "message": "no such product"})
    sha256 = hashlib.sha256(data).hexdigest()
    fields = AddProductImageFields(
        product_id=parsed.product_id, sha256=sha256, content_type=content_type,
        storage_path=image_path(parsed.product_id, sha256, content_type), caption_es=parsed.caption_es,
        source=parsed.source, source_url=parsed.source_url,
    )
    try:
        _storage(request).put_if_absent(fields.storage_path, data, content_type)
    except StorageUnavailable:
        raise HTTPException(503, detail=_STORAGE_UNAVAILABLE) from None
    return fields


_ADD_IMAGE_OPENAPI = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object",
    "required": ["product_id", "file"],
    "properties": {
        "product_id": {"type": "string", "format": "uuid"},
        "caption_es": {"type": "string", "maxLength": 500},
        "source": {"type": "string", "enum": ["upload", "datasheet", "manufacturer_site"], "default": "upload"},
        "source_url": {"type": "string", "maxLength": 2000},
        "file": {"type": "string", "format": "binary", "description": "JPEG, PNG or WebP, at most 8 MiB"},
    },
}}}}}


@catalog_command_router.post("/add-product-image", openapi_extra=_ADD_IMAGE_OPENAPI)
async def add_product_image(request: Request, operator: Deciding,
                            idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Store one product image in the private bucket, then record it.

    In this order, and nothing later starts before everything earlier passed: the operator may
    decide (the dependency), the key is present, Storage is configured, the body fits, the form
    parses with one value per field and is valid, nothing in it names Labdelivery, the bytes are
    an accepted image, the product exists; then the object is put under its content-addressed
    path, and only then does the command transaction write the row and `product.image_added`. A
    failed transaction leaves an object that the next upload of the same bytes reuses. Checking
    and hashing the bytes and the put run in the threadpool, off the event loop.

    The form is parsed here rather than declared as `Form`/`File` parameters, because FastAPI
    reads a declared form body before it resolves any dependency: an unauthorized caller's
    upload would be spooled whole before the 401/403.
    """
    try:
        key = require_idempotency_key(idempotency_key)
    except CommandRefused as exc:
        raise HTTPException(exc.status_code, detail=_detail(exc)) from exc
    _storage(request)  # 503 before reading anything when there is nowhere to put the image
    form = await _parse_upload_form(request, await _read_capped_body(request, MAX_UPLOAD_BODY_BYTES))
    try:
        items = form.multi_items()
        repeated = sorted(k for k, n in Counter(k for k, _ in items).items() if n > 1)
        if repeated:  # one value per field: "the last one wins" would be a silent choice
            raise _form_invalid([{"type": "duplicate_field", "loc": ("body", name), "msg": "field sent more than once"}
                                 for name in repeated])
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise _form_invalid([{"type": "missing", "loc": ("body", "file"), "msg": "an image file is required"}])
        try:
            parsed = AddProductImageForm(**{k: v for k, v in items if k != "file"})
        except ValidationError as exc:
            raise _form_invalid(exc.errors()) from None
        # Spec S5, before anything is stored: the caption, the source URL and the file's own name.
        # The command refuses every submitted string again (V2CatalogRepository.execute).
        try:
            refuse_labdelivery(parsed.caption_es, parsed.source_url, upload.filename)
        except LabdeliveryRefused:
            raise _labdelivery_refused() from None
        data = await upload.read(MAX_IMAGE_BYTES + 1)
        declared = upload.content_type
    finally:
        await form.close()
    fields = await run_in_threadpool(_store_image, request, parsed, data, declared)
    return await run_in_threadpool(_command, "add-product-image", fields, request, operator, key)
