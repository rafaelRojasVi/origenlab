/**
 * Read-only client for the CRM workspace. GET only; nothing here can write.
 *
 * `/v2/workspace/*` and `/v2/cockpit/*` are **not** in the dashboard-proxy allowlist yet: they
 * work through the Vite dev proxy, and behind the production Worker they answer 403
 * `path_not_allowed`, which the pages show as "not enabled in this environment".
 */

import { fetchJsonGet, operatorApiUrl } from "../api/operatorClient";
import type {
  CaseMailDocumentsResponse,
  DriveArchiveResponse,
  MailQuoteNumbersResponse,
  MailSyncStatus,
  MarketingResponse,
  PipelineResponse,
  EquipmentInterestsResponse,
  ProvidersResponse,
  ReviewResponse,
  WorkQueueResponse,
  WorkspaceOverview,
} from "./crmTypes";
import type { FxResponse } from "./fx";
import type { NoteRow } from "./authoring/crmAuthoringApi";

export const WORKSPACE_PATHS = {
  overview: "/v2/workspace/overview",
  pipeline: "/v2/workspace/pipeline",
  providers: "/v2/workspace/providers",
  equipmentInterests: "/v2/workspace/equipment-interests",
  marketing: "/v2/workspace/marketing",
  drive: "/v2/workspace/drive",
  review: "/v2/workspace/review",
  fx: "/v2/workspace/fx",
  mailSync: "/v2/workspace/mail-sync",
  mailQuoteNumbers: "/v2/workspace/mail-quote-numbers",
  workQueue: "/v2/cockpit/work-queue",
  caseNotes: (opportunityId: string) => `/v2/workspace/opportunities/${encodeURIComponent(opportunityId)}/notes`,
  caseMailDocuments: (opportunityId: string) =>
    `/v2/workspace/opportunities/${encodeURIComponent(opportunityId)}/mail-documents`,
} as const;

export const fetchOverview = () => fetchJsonGet<WorkspaceOverview>(operatorApiUrl(WORKSPACE_PATHS.overview));
export const fetchPipeline = () => fetchJsonGet<PipelineResponse>(operatorApiUrl(WORKSPACE_PATHS.pipeline));
/** The notes on one case, for the drawer's «Registrar seguimiento». Writing one is `add-note`. */
export const fetchCaseNotes = (opportunityId: string) =>
  fetchJsonGet<{ opportunity_id: string; notes: NoteRow[] }>(operatorApiUrl(WORKSPACE_PATHS.caseNotes(opportunityId)));
/** The Gmail messages linked to one case and their documents, for «Registrar cotización». A read only. */
export const fetchCaseMailDocuments = (opportunityId: string) =>
  fetchJsonGet<CaseMailDocumentsResponse>(operatorApiUrl(WORKSPACE_PATHS.caseMailDocuments(opportunityId)));
export const fetchProviders = () => fetchJsonGet<ProvidersResponse>(operatorApiUrl(WORKSPACE_PATHS.providers));
export const fetchEquipmentInterests = () =>
  fetchJsonGet<EquipmentInterestsResponse>(operatorApiUrl(WORKSPACE_PATHS.equipmentInterests));
export const fetchMarketing = () => fetchJsonGet<MarketingResponse>(operatorApiUrl(WORKSPACE_PATHS.marketing));
export const fetchDriveArchive = () => fetchJsonGet<DriveArchiveResponse>(operatorApiUrl(WORKSPACE_PATHS.drive));
export const fetchFx = () => fetchJsonGet<FxResponse>(operatorApiUrl(WORKSPACE_PATHS.fx));
export const fetchReview = () => fetchJsonGet<ReviewResponse>(operatorApiUrl(WORKSPACE_PATHS.review));
export const fetchMailQuoteNumbers = () =>
  fetchJsonGet<MailQuoteNumbersResponse>(operatorApiUrl(WORKSPACE_PATHS.mailQuoteNumbers));
export const fetchMailSync = () => fetchJsonGet<MailSyncStatus>(operatorApiUrl(WORKSPACE_PATHS.mailSync));
export const fetchWorkQueue = () =>
  fetchJsonGet<WorkQueueResponse>(operatorApiUrl(WORKSPACE_PATHS.workQueue, { limit: 200 }));
