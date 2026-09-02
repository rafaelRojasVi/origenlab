/**
 * Narrow client for durable customer-quote commands and reads (CRM-Q1).
 *
 * Create sends an intentionally empty body: every quote field (number,
 * status, Drive references) is server-controlled. The browser
 * never supplies X-OriginLab-Operator-Email; the production Worker
 * injects identity.
 */

import { OperatorApiError, operatorApiUrl } from "./operatorClient";

import {
  parseCustomerQuote,
  parseCustomerQuoteGlobalListResponse,
  parseCustomerQuoteListResponse,
  parseCustomerQuoteReadResponse,
} from "./customerQuoteParse";

import type {
  CustomerQuote,
  CustomerQuoteGlobalListResponse,
  CustomerQuoteListResponse,
  CustomerQuoteReadResponse,
  RetryCustomerQuoteDriveWorkspaceCommand,
} from "./customerQuoteTypes";


const CUSTOMER_QUOTE_ID_RE = /^quote_[0-9a-f]{32}$/;

const SALES_OPPORTUNITY_ID_RE = /^sales_[0-9a-f]{32}$/;

const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9._:-]{1,200}$/;


function requireCustomerQuoteId(quoteId: string): string {
  const normalized = quoteId.trim();

  if (!CUSTOMER_QUOTE_ID_RE.test(normalized)) {
    throw new OperatorApiError("Invalid customer quote ID", 422);
  }

  return normalized;
}


function requireSalesOpportunityId(salesOpportunityId: string): string {
  const normalized = salesOpportunityId.trim();

  if (!SALES_OPPORTUNITY_ID_RE.test(normalized)) {
    throw new OperatorApiError("Invalid sales opportunity ID", 422);
  }

  return normalized;
}


function requireIdempotencyKey(idempotencyKey: string): string {
  const normalized = idempotencyKey.trim();

  if (!IDEMPOTENCY_KEY_RE.test(normalized)) {
    throw new OperatorApiError("Invalid idempotency key", 422);
  }

  return normalized;
}


async function fetchJson<T>(
  url: string,
  init: RequestInit,
): Promise<T> {
  const response = await fetch(url, init);

  if (!response.ok) {
    const text = await response.text().catch(() => "");

    throw new OperatorApiError(
      text || response.statusText || `HTTP ${response.status}`,
      response.status,
    );
  }

  return response.json() as Promise<T>;
}


export function salesOpportunityQuotesPath(
  salesOpportunityId: string,
): string {
  return `/operations/sales-opportunities/${requireSalesOpportunityId(
    salesOpportunityId,
  )}/quotes`;
}


export function customerQuotePath(quoteId: string): string {
  return `/operations/customer-quotes/${requireCustomerQuoteId(quoteId)}`;
}


export function customerQuoteDriveWorkspacePath(quoteId: string): string {
  return `${customerQuotePath(quoteId)}/drive-workspace`;
}


export function fetchCustomerQuotes(
  salesOpportunityId: string,
): Promise<CustomerQuoteListResponse> {
  return fetchJson<unknown>(
    operatorApiUrl(salesOpportunityQuotesPath(salesOpportunityId)),
    {
      method: "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
      },
    },
  ).then(parseCustomerQuoteListResponse);
}


export function fetchCustomerQuote(
  quoteId: string,
): Promise<CustomerQuoteReadResponse> {
  return fetchJson<unknown>(
    operatorApiUrl(customerQuotePath(quoteId)),
    {
      method: "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
      },
    },
  ).then(parseCustomerQuoteReadResponse);
}


export function createCustomerQuote(
  salesOpportunityId: string,
  idempotencyKey: string,
): Promise<CustomerQuote> {
  return fetchJson<unknown>(
    operatorApiUrl(salesOpportunityQuotesPath(salesOpportunityId)),
    {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": requireIdempotencyKey(idempotencyKey),
      },
      body: JSON.stringify({}),
    },
  ).then(parseCustomerQuote);
}


export function fetchCustomerQuotesGlobal(params?: {
  stage?: readonly string[];
  driveStatus?: readonly string[];
  limit?: number;
  offset?: number;
}): Promise<CustomerQuoteGlobalListResponse> {
  return fetchJson<unknown>(
    operatorApiUrl("/operations/customer-quotes", {
      stage: params?.stage,
      drive_status: params?.driveStatus,
      limit: params?.limit,
      offset: params?.offset,
    }),
    {
      method: "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
      },
    },
  ).then(parseCustomerQuoteGlobalListResponse);
}


export function retryCustomerQuoteDriveWorkspace(
  quoteId: string,
  command: RetryCustomerQuoteDriveWorkspaceCommand,
): Promise<CustomerQuote> {
  return fetchJson<unknown>(
    operatorApiUrl(customerQuoteDriveWorkspacePath(quoteId)),
    {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(command),
    },
  ).then(parseCustomerQuote);
}
