import type { GmailInteractionAuditResponse } from "./gmailInteractionAuditTypes";
import { fetchJsonGet, operatorApiUrl } from "./operatorClient";

export const MIRROR_GMAIL_INTERACTIONS_PATH = "/mirror/audits/gmail-interactions";

export function mirrorGmailInteractionsUrl(): string {
  return operatorApiUrl(MIRROR_GMAIL_INTERACTIONS_PATH);
}

export async function fetchGmailInteractionAudit(): Promise<GmailInteractionAuditResponse> {
  return fetchJsonGet<GmailInteractionAuditResponse>(mirrorGmailInteractionsUrl());
}
