/**
 * Read-only client for the CRM workspace. GET only; nothing here can write.
 *
 * `/v2/workspace/*` and `/v2/cockpit/*` are **not** in the dashboard-proxy allowlist yet: they
 * work through the Vite dev proxy, and behind the production Worker they answer 403
 * `path_not_allowed`, which the pages show as "not enabled in this environment".
 */

import { fetchJsonGet, operatorApiUrl } from "../api/operatorClient";
import type {
  DriveArchiveResponse,
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
  workQueue: "/v2/cockpit/work-queue",
} as const;

export const fetchOverview = () => fetchJsonGet<WorkspaceOverview>(operatorApiUrl(WORKSPACE_PATHS.overview));
export const fetchPipeline = () => fetchJsonGet<PipelineResponse>(operatorApiUrl(WORKSPACE_PATHS.pipeline));
export const fetchProviders = () => fetchJsonGet<ProvidersResponse>(operatorApiUrl(WORKSPACE_PATHS.providers));
export const fetchEquipmentInterests = () =>
  fetchJsonGet<EquipmentInterestsResponse>(operatorApiUrl(WORKSPACE_PATHS.equipmentInterests));
export const fetchMarketing = () => fetchJsonGet<MarketingResponse>(operatorApiUrl(WORKSPACE_PATHS.marketing));
export const fetchDriveArchive = () => fetchJsonGet<DriveArchiveResponse>(operatorApiUrl(WORKSPACE_PATHS.drive));
export const fetchFx = () => fetchJsonGet<FxResponse>(operatorApiUrl(WORKSPACE_PATHS.fx));
export const fetchReview = () => fetchJsonGet<ReviewResponse>(operatorApiUrl(WORKSPACE_PATHS.review));
export const fetchMailSync = () => fetchJsonGet<MailSyncStatus>(operatorApiUrl(WORKSPACE_PATHS.mailSync));
export const fetchWorkQueue = () =>
  fetchJsonGet<WorkQueueResponse>(operatorApiUrl(WORKSPACE_PATHS.workQueue, { limit: 200 }));
