"""Catalog read routes (`/v2/catalog/*`), mounted only behind ORIGENLAB_V2_QUOTING_ENABLED.

Every handler redacts cost fields for the viewer role before answering.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from origenlab_api.v2.catalog.keys import PRODUCT_KINDS
from origenlab_api.v2.catalog.reads import V2CatalogReads
from origenlab_api.v2.catalog.redaction import redact_costs
from origenlab_api.v2.cockpit_routes import Operator

catalog_read_router = APIRouter(prefix="/v2/catalog")


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


@catalog_read_router.get("/parameters")
def parameters(request: Request, operator: Operator) -> dict:
    return redact_costs(_reads(request).parameters(), operator.role)
