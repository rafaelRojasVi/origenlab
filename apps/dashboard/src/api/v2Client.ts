/**
 * Read-only client for the V2 durable read boundary (`/v2/*`).
 *
 * Credentialed GET only. Every path here is listed by name in the dashboard-proxy
 * allowlist; the Worker refuses anything else under `/v2`, so adding a call here without
 * adding the path there produces a clean 403 rather than a surprise.
 */

import {
  parseV2ContactCard,
  parseV2ContactsPage,
  parseV2EvidencePage,
  parseV2EvidenceRecordsPage,
  parseV2CasesPage,
  parseV2CommercialCaseCard,
  parseV2OrganizationCard,
  parseV2OrganizationCasesPage,
  parseV2OpportunitiesPage,
  parseV2OrganizationsPage,
  parseV2QuotesPage,
  parseV2ReviewSummary,
  parseV2TasksPage,
} from "./v2Parse";
import type {
  V2CaseQuoteState,
  V2Contact,
  V2ContactCard,
  V2ContactIdentity,
  V2CommercialCase,
  V2CommercialCaseCard,
  V2EvidenceItem,
  V2EvidenceRecord,
  V2OrganizationCard,
  V2OrganizationCase,
  V2Opportunity,
  V2OrganizationFilter,
  V2OrganizationSegment,
  V2OrganizationsPage,
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
export const V2_EVIDENCE_PATH = "/v2/evidence";
export const V2_EVIDENCE_RECORDS_PATH = "/v2/evidence/records";
export const V2_CASES_PATH = "/v2/cases";

/**
 * The card paths are built from an identifier, so they are built in one place.
 *
 * The proxy allows a UUID-shaped segment and nothing else. Building the path anywhere but
 * here risks a caller interpolating a search string into a URL the Worker then refuses
 * with a 403 that reads like an outage.
 */
export function v2ContactCardPath(contactPointId: string): string {
  return `${V2_CONTACTS_PATH}/${encodeURIComponent(contactPointId)}`;
}

export function v2OrganizationCardPath(organizationId: string): string {
  return `${V2_ORGANIZATIONS_PATH}/${encodeURIComponent(organizationId)}`;
}

export function v2OrganizationCasesPath(organizationId: string): string {
  return `${v2OrganizationCardPath(organizationId)}/cases`;
}

export function v2CaseCardPath(opportunityId: string): string {
  return `${V2_CASES_PATH}/${encodeURIComponent(opportunityId)}`;
}

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


export function fetchV2ContactCard(contactPointId: string): Promise<V2ContactCard> {
  return fetchJsonGet<unknown>(operatorApiUrl(v2ContactCardPath(contactPointId))).then(
    parseV2ContactCard,
  );
}

export function fetchV2OrganizationCard(
  organizationId: string,
): Promise<V2OrganizationCard> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(v2OrganizationCardPath(organizationId)),
  ).then(parseV2OrganizationCard);
}

/**
 * The evidence trail.
 *
 * `resolution` and `source_kind` are the database's own closed vocabularies; the API
 * answers 422 for anything else rather than an empty page, so a typo here surfaces as an
 * error instead of as "there is nothing to review".
 */
export function fetchV2Evidence(
  params: {
    q?: string;
    resolution?: string;
    sourceKind?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<V2Page<V2EvidenceItem>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_EVIDENCE_PATH, {
      q: params.q,
      resolution: params.resolution,
      source_kind: params.sourceKind,
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2EvidencePage);
}

/**
 * The review queue, one row per source record.
 *
 * `review_status` and `source_kind` are the database's closed vocabularies; the API answers
 * 422 for anything else, so a typo surfaces as an error rather than as "nothing to review".
 */
export function fetchV2EvidenceRecords(
  params: {
    sourceKind?: string;
    reviewStatus?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<V2Page<V2EvidenceRecord>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_EVIDENCE_RECORDS_PATH, {
      source_kind: params.sourceKind,
      review_status: params.reviewStatus,
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2EvidenceRecordsPage);
}

/**
 * Commercial cases.
 *
 * `stage` is the schema's own vocabulary and the API answers 422 for anything else, so a
 * typo surfaces as an error rather than as "this operator has no cases".
 *
 * There is no companion writer in this module and there is not going to be one by
 * accident: the six case commands exist as `POST /v2/commands/*` upstream, and the proxy
 * allows no POST under `/v2` at all. `caseCommands.ts` describes what they would record;
 * nothing here sends it.
 */
export function fetchV2Cases(
  params: {
    stage?: string | readonly string[];
    openOnly?: boolean;
    organizationId?: string;
    organizationQ?: string;
    interestQ?: string;
    quoteState?: V2CaseQuoteState;
    activeWithinDays?: number;
    limit?: number;
    offset?: number;
  } = {},
): Promise<V2Page<V2CommercialCase>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(V2_CASES_PATH, {
      stage: params.stage,
      open_only: params.openOnly ? "true" : undefined,
      organization_id: params.organizationId,
      organization_q: params.organizationQ || undefined,
      interest_q: params.interestQ || undefined,
      quote_state: params.quoteState,
      active_within_days: params.activeWithinDays,
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2CasesPage);
}

/**
 * Every case this institution is part of, whatever part it holds.
 *
 * Not a filter over `fetchV2Cases`: that list carries only the requesting institution, so
 * crossing it in the browser would find an institution's cases only where it is the one
 * asking, and silently drop the ones where it supplies or manufactures. The server reads
 * `crm.opportunity_organization`, which is where participation is actually recorded.
 */
export function fetchV2OrganizationCases(
  organizationId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<V2Page<V2OrganizationCase>> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(v2OrganizationCasesPath(organizationId), {
      limit: params.limit ?? DEFAULT_LIMIT,
      offset: params.offset ?? 0,
    }),
  ).then(parseV2OrganizationCasesPage);
}

export function fetchV2CaseCard(opportunityId: string): Promise<V2CommercialCaseCard> {
  return fetchJsonGet<unknown>(operatorApiUrl(v2CaseCardPath(opportunityId))).then(
    parseV2CommercialCaseCard,
  );
}
