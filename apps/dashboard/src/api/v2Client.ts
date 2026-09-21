/**
 * Read-only client for the V2 durable read boundary (`/v2/*`).
 *
 * Credentialed GET only. Every path here is listed by name in the dashboard-proxy
 * allowlist; the Worker refuses anything else under `/v2`, so adding a call here without
 * adding the path there produces a clean 403 rather than a surprise.
 */

import {
  parseV2ContactsPage,
  parseV2OpportunitiesPage,
  parseV2OrganizationsPage,
  parseV2QuotesPage,
  parseV2ReviewSummary,
  parseV2TasksPage,
} from "./v2Parse";
import type {
  V2Contact,
  V2Opportunity,
  V2Organization,
  V2Page,
  V2Quote,
  V2ReviewSummary,
  V2Task,
} from "./v2Types";
import { fetchJsonGet, operatorApiUrl } from "./operatorClient";

export const V2_CONTACTS_PATH = "/v2/contacts";
export const V2_ORGANIZATIONS_PATH = "/v2/organizations";
export const V2_PROSPECTS_PATH = "/v2/prospects";
export const V2_ACTIVE_OPPORTUNITIES_PATH = "/v2/opportunities/active";
export const V2_TASKS_DUE_PATH = "/v2/tasks/due";
export const V2_REVIEW_SUMMARY_PATH = "/v2/review/summary";
export const V2_QUOTES_FOLLOWUP_PATH = "/v2/quotes/followup";

const DEFAULT_LIMIT = 50;

export function fetchV2Contacts(
  params: { q?: string; limit?: number; offset?: number } = {},
): Promise<V2Page<V2Contact>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_CONTACTS_PATH, {
      q: params.q,
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2ContactsPage);
}

export function fetchV2Organizations(
  params: { q?: string; limit?: number; offset?: number } = {},
): Promise<V2Page<V2Organization>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_ORGANIZATIONS_PATH, {
      q: params.q,
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2OrganizationsPage);
}

export function fetchV2Prospects(
  params: { limit?: number; offset?: number } = {},
): Promise<V2Page<V2Opportunity>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_PROSPECTS_PATH, {
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2OpportunitiesPage);
}

export function fetchV2ActiveOpportunities(
  params: { limit?: number; offset?: number } = {},
): Promise<V2Page<V2Opportunity>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_ACTIVE_OPPORTUNITIES_PATH, {
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2OpportunitiesPage);
}

/**
 * Follow-ups due within `horizonDays`.
 *
 * The default of 30 is wide on purpose: the four cards bucket the same response into
 * overdue / today / upcoming client-side, exactly as the V1 work queue does, so one request
 * serves both of the task cards instead of two requests disagreeing about "now".
 */
export function fetchV2TasksDue(
  params: { horizonDays?: number; limit?: number; offset?: number } = {},
): Promise<V2Page<V2Task>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_TASKS_DUE_PATH, {
      horizon_days: params.horizonDays ?? 30,
      limit: params.limit ?? 200,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2TasksPage);
}

export function fetchV2ReviewSummary(): Promise<V2ReviewSummary> {
  return fetchJsonGet<unknown>(operatorApiUrl(V2_REVIEW_SUMMARY_PATH)).then(
    parseV2ReviewSummary,
  );
}

export function fetchV2QuotesToFollowUp(
  params: { limit?: number; offset?: number } = {},
): Promise<V2Page<V2Quote>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_QUOTES_FOLLOWUP_PATH, {
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2QuotesPage);
}
