/**
 * Read-only client for apps/api catalog mirror endpoints.
 * GET only · credentials include.
 */

import { parseCatalogProductDetailResponse, parseCatalogProductsListResponse } from "./catalogParse";
import type { CatalogListQuery, CatalogProductDetailResponseUi, CatalogProductsListUi } from "./catalogTypes";
import { fetchJsonGet, getOperatorApiBaseUrl, operatorApiUrl } from "./operatorClient";

export const MIRROR_CATALOG_PRODUCTS_PATH = "/mirror/catalog/products";

const DEFAULT_CATALOG_LIMIT = 100;

export function mirrorCatalogProductsUrl(query: CatalogListQuery = {}): string {
  const params: Record<string, string | number> = {
    limit: query.limit ?? DEFAULT_CATALOG_LIMIT,
  };
  if (query.q?.trim()) {
    params.q = query.q.trim();
  }
  if (query.brand?.trim()) {
    params.brand = query.brand.trim();
  }
  if (query.equipment_class?.trim()) {
    params.equipment_class = query.equipment_class.trim();
  }
  if (query.category_key?.trim()) {
    params.category_key = query.category_key.trim();
  }
  return operatorApiUrl(MIRROR_CATALOG_PRODUCTS_PATH, params);
}

export function mirrorCatalogProductDetailUrl(productKey: string): string {
  const key = encodeURIComponent(productKey.trim());
  return operatorApiUrl(`${MIRROR_CATALOG_PRODUCTS_PATH}/${key}`);
}

export function fetchCatalogProductsMirror(
  query: CatalogListQuery = {},
): Promise<CatalogProductsListUi> {
  return fetchJsonGet<unknown>(mirrorCatalogProductsUrl(query)).then(
    parseCatalogProductsListResponse,
  );
}

export function fetchCatalogProductDetailMirror(
  productKey: string,
): Promise<CatalogProductDetailResponseUi> {
  return fetchJsonGet<unknown>(mirrorCatalogProductDetailUrl(productKey)).then(
    parseCatalogProductDetailResponse,
  );
}

export { getOperatorApiBaseUrl };
