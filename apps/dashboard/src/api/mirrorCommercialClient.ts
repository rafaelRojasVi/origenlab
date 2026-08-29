/**
 * Read-only client for apps/api Postgres mirror commercial deal endpoints.
 * Uses credentialed GET only for the deals list mirror.
 */

import { parseCommercialDealsListResponse } from "./commercialDealsParse";
import type { CommercialDealsListUi } from "./commercialDealsTypes";
import { fetchJsonGet, getOperatorApiBaseUrl, operatorApiUrl } from "./operatorClient";

export const MIRROR_COMMERCIAL_DEALS_PATH = "/mirror/commercial/deals";

const DEFAULT_DEALS_LIMIT = 20;

export function mirrorCommercialDealsUrl(limit = DEFAULT_DEALS_LIMIT): string {
  return operatorApiUrl(MIRROR_COMMERCIAL_DEALS_PATH, { limit });
}

export function fetchCommercialDealsMirror(
  limit = DEFAULT_DEALS_LIMIT,
): Promise<CommercialDealsListUi> {
  return fetchJsonGet<unknown>(mirrorCommercialDealsUrl(limit)).then(
    parseCommercialDealsListResponse,
  );
}

export { getOperatorApiBaseUrl };
