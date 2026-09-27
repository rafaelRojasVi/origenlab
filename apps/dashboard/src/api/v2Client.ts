/**
 * Read-only client for the V2 durable read boundary (`/v2/*`).
 *
 * Credentialed GET only. Every path here is listed by name in the dashboard-proxy
 * allowlist; the Worker refuses anything else under `/v2`, so adding a call here without
 * adding the path there produces a clean 403 rather than a surprise.
 */

import { parseV2ContactsPage, parseV2OrganizationsPage } from "./v2Parse";
import type {
  V2Contact,
  V2ContactIdentity,
  V2OrganizationFilter,
  V2OrganizationSegment,
  V2OrganizationsPage,
  V2Page,
} from "./v2Types";
import { fetchJsonGet, operatorApiUrl } from "./operatorClient";

export const V2_CONTACTS_PATH = "/v2/contacts";
export const V2_ORGANIZATIONS_PATH = "/v2/organizations";

const DEFAULT_LIMIT = 50;

/**
 * Contact points, recorded identities first.
 *
 * `q` matches the address, the recorded person's name or the recorded institution's name;
 * `identity` filters on the channel's own foreign keys, never on its address.
 */
export function fetchV2Contacts(
  params: {
    q?: string;
    identity?: V2ContactIdentity;
    withCases?: boolean;
    limit?: number;
    offset?: number;
  } = {},
): Promise<V2Page<V2Contact>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_CONTACTS_PATH, {
      q: params.q,
      identity: params.identity,
      with_cases: params.withCases ? "true" : undefined,
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2ContactsPage);
}

/**
 * Organizations, most connected first. Every `has` filter must hold; `activeWithinDays`
 * keeps organizations whose latest case activity is inside the window, and `segment` one
 * commercial side. The page carries `facets`, one count per segment.
 */
export function fetchV2Organizations(
  params: {
    q?: string;
    has?: readonly V2OrganizationFilter[];
    activeWithinDays?: number;
    /** One commercial side; omitted means every organization. */
    segment?: V2OrganizationSegment;
    limit?: number;
    offset?: number;
  } = {},
): Promise<V2OrganizationsPage> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_ORGANIZATIONS_PATH, {
      q: params.q,
      has: params.has && params.has.length > 0 ? params.has : undefined,
      active_within_days: params.activeWithinDays,
      segment: params.segment,
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2OrganizationsPage);
}
