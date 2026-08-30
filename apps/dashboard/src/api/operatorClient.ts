/**
 * Read-only client for apps/api operator endpoints.
 * Do not add mutation methods (POST/PUT/PATCH/DELETE).
 */

import { parseContactDetailResponse } from "./contactParse";
import type { ContactProfileUi } from "./contactTypes";
import { parseWarmCasesResponse } from "./commercialParse";
import {
  parseCommercialOpportunitiesResponse,
  parseCommercialOpportunityDetailResponse,
} from "./commercialOpportunitiesParse";
import type {
  CommercialOpportunitiesQuery,
  CommercialOpportunitiesResponse,
  CommercialOpportunityDetailResponse,
} from "./commercialOpportunitiesTypes";
import type { WarmCasesQuery, WarmCasesResponse } from "./commercialTypes";
import type {
  ChileCompraEquipmentAutoRefreshStatus,
  DailyCoreRunStatus,
  DashboardAutoMirrorStatus,
  DashboardMirrorSync,
  DailyCoreAutomationStatus,
  HealthResponse,
  MailAutoRefreshStatus,
  OperatorAutomationCronStatus,
  OperatorAutomationStatus,
  OperatorPathInfoMap,
  OperatorStatusResponse,
  RedactedPathInfo,
  TodayPanelData,
} from "./operatorTypes";

export const PRODUCTION_API_BASE_URL_REQUIRED =
  "VITE_ORIGENLAB_API_BASE_URL is required for production builds (npm run build). Set it to your public apps/api URL.";

export class OperatorApiConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OperatorApiConfigError";
  }
}

function isProductionBuild(): boolean {
  return import.meta.env.MODE === "production";
}

/** Resolve API base URL. Production builds must set VITE_ORIGENLAB_API_BASE_URL (no localhost fallback). */
export function getOperatorApiBaseUrl(): string {
  if (isProductionBuild()) {
    const raw = import.meta.env.VITE_ORIGENLAB_API_BASE_URL?.trim();
    if (!raw) {
      throw new OperatorApiConfigError(PRODUCTION_API_BASE_URL_REQUIRED);
    }
    return raw.replace(/\/$/, "");
  }
  const raw = import.meta.env.VITE_ORIGENLAB_API_BASE_URL?.trim();
  if (raw) {
    return raw.replace(/\/$/, "");
  }
  return "";
}

/** Join configured API base (may include path prefix like /api) with a route path. */
export function joinApiBaseUrl(base: string, path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const trimmedBase = base.trim().replace(/\/$/, "");
  if (!trimmedBase) {
    return normalizedPath;
  }
  try {
    const baseUrl = new URL(`${trimmedBase}/`);
    const relativePath = normalizedPath.startsWith("/") ? normalizedPath.slice(1) : normalizedPath;
    return new URL(relativePath, baseUrl).toString();
  } catch {
    throw new OperatorApiConfigError(`Invalid VITE_ORIGENLAB_API_BASE_URL: ${base}`);
  }
}

export function operatorApiUrl(
  path: string,
  params?: Record<
    string,
    string | number | boolean | readonly (string | number)[] | undefined
  >,
): string {
  const base = getOperatorApiBaseUrl();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const urlStr =
    base ||
    (typeof window !== "undefined"
      ? new URL(normalizedPath, `${window.location.origin}/`).toString()
      : (() => {
          throw new OperatorApiConfigError(
            "Cannot resolve API URL without window. Set VITE_ORIGENLAB_API_BASE_URL or run via npm run dev.",
          );
        })());

  const resolved = base ? joinApiBaseUrl(base, normalizedPath) : urlStr;
  const url = new URL(resolved);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined) continue;
      if (Array.isArray(value)) {
        for (const item of value) {
          url.searchParams.append(key, String(item));
        }
        continue;
      }
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export class OperatorApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "OperatorApiError";
  }
}

export async function fetchJsonGet<T>(url: string): Promise<T> {
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return res.json() as Promise<T>;
}

export function fetchHealth(): Promise<HealthResponse> {
  return fetchJsonGet<HealthResponse>(operatorApiUrl("/health"));
}

export function fetchOperatorStatus(
  maxStalenessDays = 14,
): Promise<OperatorStatusResponse> {
  return fetchJsonGet<unknown>(
    operatorApiUrl("/operator/status", { max_staleness_days: maxStalenessDays }),
  ).then(parseOperatorStatusResponse);
}

export async function fetchTodayPanel(): Promise<TodayPanelData> {
  const [health, operator] = await Promise.all([fetchHealth(), fetchOperatorStatus()]);
  return { health, operator };
}

export function fetchOperatorAutomationStatus(
  cooldownSeconds = 900,
): Promise<OperatorAutomationStatus> {
  return fetchJsonGet<unknown>(
    operatorApiUrl("/operator/automation-status", { "cooldown-seconds": cooldownSeconds }),
  ).then(parseOperatorAutomationStatus);
}

function optionalString(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return value as null | undefined;
  return typeof value === "string" ? value : String(value);
}

function optionalNumber(value: unknown): number | null | undefined {
  if (value === null || value === undefined) return value as null | undefined;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalBool(value: unknown, defaultValue = false): boolean {
  if (value === undefined || value === null) return defaultValue;
  return Boolean(value);
}

function optionalBoolOrNull(value: unknown): boolean | null {
  if (value === null || value === undefined) return null;
  return Boolean(value);
}

function defaultChilecompraEquipmentAutoRefreshStatus(): ChileCompraEquipmentAutoRefreshStatus {
  return {
    state_exists: false,
    lock_live: false,
    lock_age_seconds: null,
    freshness_age_seconds: null,
    next_run_due: null,
    consecutive_failures: 0,
  };
}

/** Parse a single redacted path companion object (basename + kind only). */
export function parseRedactedPathInfo(raw: unknown): RedactedPathInfo | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const row = raw as Record<string, unknown>;
  if (typeof row.basename !== "string") {
    return null;
  }
  if (typeof row.kind !== "string") {
    return null;
  }
  return {
    redacted: Boolean(row.redacted),
    basename: row.basename,
    kind: row.kind,
  };
}

/** Parse a section path_info map; skips invalid entries. */
export function parsePathInfoMap(raw: unknown): OperatorPathInfoMap | null {
  if (raw === null || raw === undefined) {
    return null;
  }
  if (typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const out: OperatorPathInfoMap = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const info = parseRedactedPathInfo(value);
    if (info) {
      out[key] = info;
    }
  }
  return out;
}

function parseChilecompraEquipmentAutoRefreshStatus(
  raw: unknown,
): ChileCompraEquipmentAutoRefreshStatus {
  if (!raw || typeof raw !== "object") {
    return defaultChilecompraEquipmentAutoRefreshStatus();
  }
  const row = raw as Record<string, unknown>;
  return {
    state_exists: optionalBool(row.state_exists),
    lock_live: optionalBool(row.lock_live),
    lock_age_seconds: optionalNumber(row.lock_age_seconds) ?? null,
    last_result: optionalString(row.last_result) ?? null,
    last_successful_refresh_at: optionalString(row.last_successful_refresh_at) ?? null,
    last_successful_publish_at: optionalString(row.last_successful_publish_at) ?? null,
    last_run_started_at: optionalString(row.last_run_started_at) ?? null,
    last_run_finished_at: optionalString(row.last_run_finished_at) ?? null,
    next_recommended_run_at: optionalString(row.next_recommended_run_at) ?? null,
    freshness_age_seconds: optionalNumber(row.freshness_age_seconds) ?? null,
    next_run_due: optionalBoolOrNull(row.next_run_due),
    consecutive_failures: optionalNumber(row.consecutive_failures) ?? 0,
    last_error: optionalString(row.last_error) ?? null,
    fetched_summaries: optionalNumber(row.fetched_summaries) ?? null,
    candidate_summaries: optionalNumber(row.candidate_summaries) ?? null,
    detail_requests: optionalNumber(row.detail_requests) ?? null,
    detail_cache_hits: optionalNumber(row.detail_cache_hits) ?? null,
    detail_error_count: optionalNumber(row.detail_error_count) ?? null,
    output_rows: optionalNumber(row.output_rows) ?? null,
    published_rows: optionalNumber(row.published_rows) ?? null,
    published_queue: optionalString(row.published_queue) ?? null,
    candidate_audit: optionalString(row.candidate_audit) ?? null,
    path_info: parsePathInfoMap(row.path_info),
    parse_error: optionalString(row.parse_error) ?? null,
  };
}

function parseOperatorAutomationCronStatus(raw: unknown): OperatorAutomationCronStatus {
  if (!raw || typeof raw !== "object") {
    return {};
  }
  const row = raw as Record<string, unknown>;
  const cron: OperatorAutomationCronStatus = {};
  const note = optionalString(row.note);
  if (note) {
    cron.note = note;
  }
  if (row.chilecompra_entry_present !== undefined) {
    cron.chilecompra_entry_present = optionalBool(row.chilecompra_entry_present);
  }
  if (row.chilecompra_uses_tracked_script !== undefined) {
    cron.chilecompra_uses_tracked_script = optionalBool(row.chilecompra_uses_tracked_script);
  }
  if (row.mail_entry_present !== undefined) {
    cron.mail_entry_present = optionalBool(row.mail_entry_present);
  }
  if (row.mirror_entry_present !== undefined) {
    cron.mirror_entry_present = optionalBool(row.mirror_entry_present);
  }
  return cron;
}

function parseDashboardMirrorSync(raw: unknown): DashboardMirrorSync | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const row = raw as Record<string, unknown>;
  return {
    table_available:
      row.table_available === undefined || row.table_available === null
        ? undefined
        : Boolean(row.table_available),
    status: optionalString(row.status) ?? undefined,
    latest_sync_id: optionalNumber(row.latest_sync_id) ?? null,
    started_at: optionalString(row.started_at) ?? null,
    finished_at: optionalString(row.finished_at) ?? null,
    elapsed_seconds: optionalNumber(row.elapsed_seconds) ?? null,
    canonical_contact_count: optionalNumber(row.canonical_contact_count) ?? null,
    canonical_organization_count: optionalNumber(row.canonical_organization_count) ?? null,
    canonical_opportunity_signal_count:
      optionalNumber(row.canonical_opportunity_signal_count) ?? null,
    archive_contact_count: optionalNumber(row.archive_contact_count) ?? null,
    archive_organization_count: optionalNumber(row.archive_organization_count) ?? null,
    archive_opportunity_signal_count:
      optionalNumber(row.archive_opportunity_signal_count) ?? null,
    email_suppression_count: optionalNumber(row.email_suppression_count) ?? null,
    domain_suppression_count: optionalNumber(row.domain_suppression_count) ?? null,
    outreach_state_count: optionalNumber(row.outreach_state_count) ?? null,
    error_message: optionalString(row.error_message) ?? null,
  };
}

/** Parse automation status JSON (for tests and defensive UI). */
export function parseOperatorAutomationStatus(data: unknown): OperatorAutomationStatus {
  const row = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const daily = (row.daily_core && typeof row.daily_core === "object"
    ? row.daily_core
    : {}) as Record<string, unknown>;
  const mail = (row.mail_auto_refresh && typeof row.mail_auto_refresh === "object"
    ? row.mail_auto_refresh
    : {}) as Record<string, unknown>;
  const mirror = (row.dashboard_auto_mirror && typeof row.dashboard_auto_mirror === "object"
    ? row.dashboard_auto_mirror
    : {}) as Record<string, unknown>;

  const dailyCore: DailyCoreAutomationStatus = {
    exists: optionalBool(daily.exists),
    status: optionalString(daily.status) ?? null,
    returncode: optionalNumber(daily.returncode) ?? null,
    generated_at_utc: optionalString(daily.generated_at_utc) ?? null,
    age_seconds: optionalNumber(daily.age_seconds) ?? null,
    steps: optionalNumber(daily.steps) ?? undefined,
    parse_error: optionalString(daily.parse_error) ?? null,
  };

  const mailRefresh: MailAutoRefreshStatus = {
    state_exists: optionalBool(mail.state_exists),
    paused: optionalBool(mail.paused),
    lock_live: optionalBool(mail.lock_live),
    dirty: optionalBool(mail.dirty),
    pending: optionalBool(mail.pending),
    last_result: optionalString(mail.last_result) ?? null,
    last_change_seen_at: optionalString(mail.last_change_seen_at) ?? null,
    last_successful_refresh_at: optionalString(mail.last_successful_refresh_at) ?? null,
    last_run_started_at: optionalString(mail.last_run_started_at) ?? null,
    last_run_finished_at: optionalString(mail.last_run_finished_at) ?? null,
    last_seen_inbox_total: optionalNumber(mail.last_seen_inbox_total) ?? null,
    last_seen_sent_total: optionalNumber(mail.last_seen_sent_total) ?? null,
    pending_inbox_total: optionalNumber(mail.pending_inbox_total) ?? null,
    pending_sent_total: optionalNumber(mail.pending_sent_total) ?? null,
    consecutive_failures: optionalNumber(mail.consecutive_failures) ?? 0,
  };

  const mirrorStatus: DashboardAutoMirrorStatus = {
    state_exists: optionalBool(mirror.state_exists),
    paused: optionalBool(mirror.paused),
    lock_live: optionalBool(mirror.lock_live),
    last_result: optionalString(mirror.last_result) ?? null,
    last_successful_mirror_at: optionalString(mirror.last_successful_mirror_at) ?? null,
    last_run_started_at: optionalString(mirror.last_run_started_at) ?? null,
    last_run_finished_at: optionalString(mirror.last_run_finished_at) ?? null,
    last_mirrored_daily_core_generated_at:
      optionalString(mirror.last_mirrored_daily_core_generated_at) ?? null,
    mirror_matches_daily_core:
      mirror.mirror_matches_daily_core === null || mirror.mirror_matches_daily_core === undefined
        ? null
        : Boolean(mirror.mirror_matches_daily_core),
    cooldown_seconds: optionalNumber(mirror.cooldown_seconds) ?? 900,
    cooldown_remaining_seconds: optionalNumber(mirror.cooldown_remaining_seconds) ?? 0,
    consecutive_failures: optionalNumber(mirror.consecutive_failures) ?? 0,
  };

  return {
    generated_at_utc: String(row.generated_at_utc ?? ""),
    active_current_dir: String(row.active_current_dir ?? ""),
    active_current_dir_info: parseRedactedPathInfo(row.active_current_dir_info),
    path_redaction_applied:
      row.path_redaction_applied === undefined || row.path_redaction_applied === null
        ? undefined
        : Boolean(row.path_redaction_applied),
    verdict: String(row.verdict ?? "unknown"),
    daily_core: dailyCore,
    mail_auto_refresh: mailRefresh,
    dashboard_auto_mirror: mirrorStatus,
    chilecompra_equipment_auto_refresh: parseChilecompraEquipmentAutoRefreshStatus(
      row.chilecompra_equipment_auto_refresh,
    ),
    cron: parseOperatorAutomationCronStatus(row.cron),
    recommended_action: String(row.recommended_action ?? "inspect_logs"),
    warnings: Array.isArray(row.warnings) ? row.warnings.map(String) : [],
    source:
      row.source === "postgres_snapshot" || row.source === "filesystem_active_current"
        ? row.source
        : null,
    snapshot_updated_at: optionalString(row.snapshot_updated_at) ?? null,
    snapshot_stale:
      row.snapshot_stale === null || row.snapshot_stale === undefined
        ? null
        : Boolean(row.snapshot_stale),
    dashboard_mirror_sync: parseDashboardMirrorSync(row.dashboard_mirror_sync),
  };
}

/** Dashboard warm queue: load full normalized set for client-side presets (Pagos/admin, Logística). */
export const DASHBOARD_WARM_CASES_QUERY: Required<
  Pick<WarmCasesQuery, "days" | "limit" | "positive_signal_only">
> = {
  days: 14,
  limit: 100,
  positive_signal_only: false,
};

export function fetchWarmCases(query: WarmCasesQuery = {}): Promise<WarmCasesResponse> {
  const params: Record<string, string | number | boolean> = {
    days: query.days ?? DASHBOARD_WARM_CASES_QUERY.days,
    limit: query.limit ?? DASHBOARD_WARM_CASES_QUERY.limit,
    positive_signal_only:
      query.positive_signal_only ?? DASHBOARD_WARM_CASES_QUERY.positive_signal_only,
  };
  if (query.category) {
    params.category = query.category;
  }
  if (query.include_noise) {
    params.include_noise = query.include_noise;
  }
  return fetchJsonGet<unknown>(operatorApiUrl("/cases/warm", params)).then(parseWarmCasesResponse);
}

/** Build GET /contacts/{email} path with encoded email segment. */
export function contactDetailPath(email: string): string {
  const trimmed = email.trim();
  if (!trimmed || !trimmed.includes("@")) {
    throw new OperatorApiError("Invalid contact email", 422);
  }
  return `/contacts/${encodeURIComponent(trimmed)}`;
}

export function fetchContactProfile(email: string): Promise<ContactProfileUi> {
  return fetchJsonGet<unknown>(operatorApiUrl(contactDetailPath(email))).then(
    parseContactDetailResponse,
  );
}

/** Parse health JSON (for tests and defensive UI). */
export function parseHealthResponse(data: unknown): HealthResponse {
  const row = data as HealthResponse;
  return {
    ok: Boolean(row.ok),
    service: String(row.service ?? "origenlab-api"),
    mode: String(row.mode ?? ""),
    backend: row.backend === "postgres" ? "postgres" : "sqlite",
    postgres_configured: Boolean(row.postgres_configured),
  };
}

/** Parse daily-core run summary (for tests and defensive UI). */
export function parseDailyCoreRunStatus(raw: unknown): DailyCoreRunStatus {
  if (!raw || typeof raw !== "object") {
    return { exists: false };
  }
  const row = raw as Record<string, unknown>;
  const out: DailyCoreRunStatus = {
    exists: Boolean(row.exists),
  };
  if (typeof row.path === "string") {
    out.path = row.path;
  }
  if (row.loaded !== undefined) {
    out.loaded = Boolean(row.loaded);
  }
  if (row.parse_error !== undefined) {
    out.parse_error = Boolean(row.parse_error);
  }
  if (typeof row.schema_version === "number" && Number.isFinite(row.schema_version)) {
    out.schema_version = row.schema_version;
  }
  if (typeof row.workflow === "string") {
    out.workflow = row.workflow;
  }
  if (typeof row.generated_at_utc === "string") {
    out.generated_at_utc = row.generated_at_utc;
  }
  if (typeof row.status === "string") {
    out.status = row.status;
  }
  if (typeof row.returncode === "number" && Number.isFinite(row.returncode)) {
    out.returncode = row.returncode;
  }
  if (typeof row.step_count === "number" && Number.isFinite(row.step_count)) {
    out.step_count = row.step_count;
  }
  if (typeof row.last_step === "string") {
    out.last_step = row.last_step;
  }
  if (row.send_approval !== undefined) {
    out.send_approval = Boolean(row.send_approval);
  }
  if (typeof row.postgres_mirror === "string") {
    out.postgres_mirror = row.postgres_mirror;
  }
  return out;
}

/** Parse operator status JSON (for tests and defensive UI). */
export function parseOperatorStatusResponse(data: unknown): OperatorStatusResponse {
  const row = data as OperatorStatusResponse;
  return {
    verdict: String(row.verdict ?? "UNKNOWN"),
    sqlite_path: String(row.sqlite_path ?? ""),
    campaign_mode: row.campaign_mode == null ? null : String(row.campaign_mode),
    operator_focus: row.operator_focus == null ? null : String(row.operator_focus),
    outbound_readiness: String(row.outbound_readiness ?? "n/a"),
    warnings: Array.isArray(row.warnings) ? row.warnings.map(String) : [],
    daily_core_run: parseDailyCoreRunStatus(row.daily_core_run),
  };
}


export function fetchCommercialOpportunities(
  query: CommercialOpportunitiesQuery = {},
): Promise<CommercialOpportunitiesResponse> {
  const params: Record<string, string | number | boolean> = {};

  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params[key] = value;
    }
  }

  return fetchJsonGet<unknown>(
    operatorApiUrl("/opportunities/commercial", params),
  ).then(parseCommercialOpportunitiesResponse);
}

export function commercialOpportunityDetailPath(
  opportunityId: string,
): string {
  return `/opportunities/commercial/${encodeURIComponent(opportunityId)}`;
}

export function fetchCommercialOpportunityDetail(
  opportunityId: string,
): Promise<CommercialOpportunityDetailResponse> {
  return fetchJsonGet<unknown>(
    operatorApiUrl(commercialOpportunityDetailPath(opportunityId)),
  ).then(parseCommercialOpportunityDetailResponse);
}
