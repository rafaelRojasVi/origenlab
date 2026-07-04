import { afterEach, describe, expect, it, vi } from "vitest";
import {
  OperatorApiConfigError,
  OperatorApiError,
  contactDetailPath,
  fetchContactProfile,
  fetchHealth,
  fetchOperatorStatus,
  getOperatorApiBaseUrl,
  joinApiBaseUrl,
  operatorApiUrl,
  parseHealthResponse,
  parseOperatorAutomationStatus,
  parseOperatorStatusResponse,
  parseDailyCoreRunStatus,
  parsePathInfoMap,
  parseRedactedPathInfo,
} from "./operatorClient";

describe("operator API client", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("getOperatorApiBaseUrl throws in production when env is missing", () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "");
    expect(() => getOperatorApiBaseUrl()).toThrow(OperatorApiConfigError);
    expect(() => getOperatorApiBaseUrl()).toThrow(/VITE_ORIGENLAB_API_BASE_URL/);
  });

  it("getOperatorApiBaseUrl uses env in production (no localhost fallback)", () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "https://api.example.com/");
    expect(getOperatorApiBaseUrl()).toBe("https://api.example.com");
  });

  it("getOperatorApiBaseUrl strips trailing slash from env", () => {
    vi.stubEnv("MODE", "development");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "http://api.example.com/");
    expect(getOperatorApiBaseUrl()).toBe("http://api.example.com");
  });

  it("joinApiBaseUrl preserves /api path prefix in production proxy base", () => {
    expect(joinApiBaseUrl("https://dashboard.origenlab.cl/api", "/operator/status")).toBe(
      "https://dashboard.origenlab.cl/api/operator/status",
    );
    expect(joinApiBaseUrl("https://dashboard.origenlab.cl/api/", "/health")).toBe(
      "https://dashboard.origenlab.cl/api/health",
    );
    expect(joinApiBaseUrl("https://api.example.com", "/operator/status")).toBe(
      "https://api.example.com/operator/status",
    );
  });

  it("operatorApiUrl preserves /api path prefix when env includes proxy segment", () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "https://dashboard.origenlab.cl/api");
    expect(operatorApiUrl("/operator/status")).toBe("https://dashboard.origenlab.cl/api/operator/status");
    expect(operatorApiUrl("/mirror/catalog/products")).toBe(
      "https://dashboard.origenlab.cl/api/mirror/catalog/products",
    );
  });

  it("getOperatorApiBaseUrl is empty in dev when env unset (vite proxy)", () => {
    vi.stubEnv("MODE", "development");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "");
    expect(getOperatorApiBaseUrl()).toBe("");
  });

  it("operatorApiUrl builds operator status with staleness param", () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "http://127.0.0.1:8001");
    const url = operatorApiUrl("/operator/status", { max_staleness_days: 14 });
    expect(url).toContain("/operator/status");
    expect(url).toContain("max_staleness_days=14");
  });

  it("parseHealthResponse normalizes backend", () => {
    const parsed = parseHealthResponse({
      ok: true,
      service: "origenlab-api",
      mode: "operator-postgres-readonly",
      backend: "postgres",
      postgres_configured: true,
    });
    expect(parsed.backend).toBe("postgres");
    expect(parsed.postgres_configured).toBe(true);
  });

  it("parseOperatorStatusResponse normalizes warnings", () => {
    const parsed = parseOperatorStatusResponse({
      verdict: "CAUTION",
      sqlite_path: "/data/emails.sqlite",
      campaign_mode: "warm",
      operator_focus: "follow-up",
      outbound_readiness: "mirror_stale",
      warnings: ["sync older than 7d"],
    });
    expect(parsed.verdict).toBe("CAUTION");
    expect(parsed.warnings).toEqual(["sync older than 7d"]);
    expect(parsed.daily_core_run).toEqual({ exists: false });
  });

  it("parseOperatorStatusResponse returns exists false when daily_core_run is missing", () => {
    const parsed = parseOperatorStatusResponse({
      verdict: "READY",
      sqlite_path: "",
      campaign_mode: null,
      operator_focus: null,
      outbound_readiness: "ready",
      warnings: [],
    });
    expect(parsed.daily_core_run).toEqual({ exists: false });
  });

  it("parseOperatorAutomationStatus parses ChileCompra equipment auto-refresh fields", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-11T14:38:18+00:00",
      active_current_dir: "<local-active-current>",
      verdict: "healthy",
      daily_core: { exists: true, status: "success", returncode: 0 },
      mail_auto_refresh: { state_exists: true, dirty: false, pending: false },
      dashboard_auto_mirror: { state_exists: true, mirror_matches_daily_core: true },
      chilecompra_equipment_auto_refresh: {
        state_exists: true,
        lock_live: false,
        lock_age_seconds: 12,
        last_result: "refreshed",
        last_successful_refresh_at: "2026-06-11T14:20:00+00:00",
        last_successful_publish_at: "2026-06-11T14:38:00+00:00",
        next_recommended_run_at: "2026-06-11T17:41:00+00:00",
        freshness_age_seconds: 1080,
        next_run_due: false,
        consecutive_failures: 0,
        detail_requests: 4,
        detail_cache_hits: 2,
        detail_error_count: 0,
        published_rows: 7,
      },
      cron: {
        chilecompra_entry_present: true,
        chilecompra_uses_tracked_script: true,
        mail_entry_present: true,
        mirror_entry_present: true,
      },
      recommended_action: "none",
      warnings: [],
    });
    expect(parsed.chilecompra_equipment_auto_refresh.state_exists).toBe(true);
    expect(parsed.chilecompra_equipment_auto_refresh.published_rows).toBe(7);
    expect(parsed.chilecompra_equipment_auto_refresh.detail_requests).toBe(4);
    expect(parsed.cron.chilecompra_entry_present).toBe(true);
    expect(parsed.cron.chilecompra_uses_tracked_script).toBe(true);
  });

  it("parseOperatorAutomationStatus defaults missing ChileCompra section", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-11T14:38:18+00:00",
      active_current_dir: "/tmp",
      verdict: "healthy",
      recommended_action: "none",
    });
    expect(parsed.chilecompra_equipment_auto_refresh).toEqual({
      state_exists: false,
      lock_live: false,
      lock_age_seconds: null,
      freshness_age_seconds: null,
      next_run_due: null,
      consecutive_failures: 0,
    });
  });

  it("parseOperatorAutomationStatus preserves postgres snapshot source fields", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-11T14:38:18+00:00",
      active_current_dir: "<local-active-current>",
      verdict: "healthy",
      daily_core: { exists: true, status: "success", returncode: 0 },
      mail_auto_refresh: { state_exists: true, dirty: false, pending: false },
      dashboard_auto_mirror: { state_exists: true, mirror_matches_daily_core: true },
      cron: { note: "not inspected by API" },
      recommended_action: "none",
      warnings: [],
      source: "postgres_snapshot",
      snapshot_updated_at: "2026-06-11T14:38:21+00:00",
      snapshot_stale: false,
    });
    expect(parsed.source).toBe("postgres_snapshot");
    expect(parsed.snapshot_updated_at).toBe("2026-06-11T14:38:21+00:00");
    expect(parsed.snapshot_stale).toBe(false);
  });

  it("parseOperatorAutomationStatus parses dashboard_mirror_sync metadata", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-19T18:59:56+00:00",
      active_current_dir: "current",
      verdict: "attention",
      daily_core: { exists: true, status: "success", returncode: 0 },
      mail_auto_refresh: { state_exists: true, dirty: false, pending: false },
      dashboard_auto_mirror: {
        state_exists: true,
        last_result: "mail_dirty",
        mirror_matches_daily_core: false,
      },
      cron: { note: "not inspected by API" },
      recommended_action: "wait_for_mail_quiet_window",
      warnings: [],
      dashboard_mirror_sync: {
        table_available: true,
        status: "success",
        latest_sync_id: 135,
        finished_at: "2026-06-19T18:59:56+00:00",
        canonical_contact_count: 2318,
      },
    });
    expect(parsed.dashboard_mirror_sync?.status).toBe("success");
    expect(parsed.dashboard_mirror_sync?.latest_sync_id).toBe(135);
    expect(parsed.dashboard_mirror_sync?.canonical_contact_count).toBe(2318);
  });

  it("parseOperatorAutomationStatus parses dashboard_auto_mirror run timestamps", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-19T18:59:56+00:00",
      active_current_dir: "current",
      verdict: "healthy",
      daily_core: { exists: true, status: "success", returncode: 0 },
      mail_auto_refresh: { state_exists: true, dirty: false, pending: false },
      dashboard_auto_mirror: {
        state_exists: true,
        last_result: "success",
        mirror_matches_daily_core: true,
        last_run_started_at: "2026-06-19T18:55:00+00:00",
        last_run_finished_at: "2026-06-19T18:59:56+00:00",
      },
      cron: { note: "not inspected by API" },
      recommended_action: "none",
      warnings: [],
    });
    expect(parsed.dashboard_auto_mirror.last_run_started_at).toBe("2026-06-19T18:55:00+00:00");
    expect(parsed.dashboard_auto_mirror.last_run_finished_at).toBe("2026-06-19T18:59:56+00:00");
  });

  it("parseOperatorAutomationStatus nulls unknown source values", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-11T14:38:18+00:00",
      active_current_dir: "/tmp",
      verdict: "healthy",
      recommended_action: "none",
      source: "unexpected",
      snapshot_stale: undefined,
    });
    expect(parsed.source).toBeNull();
    expect(parsed.snapshot_updated_at).toBeNull();
    expect(parsed.snapshot_stale).toBeNull();
  });

  it("parseOperatorAutomationStatus parses redacted path companion fields", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-16T12:00:00+00:00",
      active_current_dir: "/home/ops/reports/out/active/current",
      active_current_dir_info: {
        redacted: true,
        basename: "current",
        kind: "directory",
      },
      path_redaction_applied: true,
      verdict: "healthy",
      daily_core: { exists: true, status: "success", returncode: 0 },
      mail_auto_refresh: { state_exists: true, dirty: false, pending: false },
      dashboard_auto_mirror: { state_exists: true, mirror_matches_daily_core: true },
      chilecompra_equipment_auto_refresh: {
        state_exists: true,
        published_queue:
          "/home/ops/reports/out/active/current/equipment_first_operator_queue_20260616.csv",
        candidate_audit:
          "/home/ops/reports/out/active/current/chilecompra_equipment_candidate_audit_20260616.csv",
        path_info: {
          published_queue: {
            redacted: true,
            basename: "equipment_first_operator_queue_20260616.csv",
            kind: "file",
          },
          candidate_audit: {
            redacted: true,
            basename: "chilecompra_equipment_candidate_audit_20260616.csv",
            kind: "file",
          },
        },
      },
      cron: { note: "not inspected by API" },
      recommended_action: "none",
      warnings: [],
    });

    expect(parsed.active_current_dir).toBe("/home/ops/reports/out/active/current");
    expect(parsed.active_current_dir_info).toEqual({
      redacted: true,
      basename: "current",
      kind: "directory",
    });
    expect(parsed.path_redaction_applied).toBe(true);
    expect(parsed.chilecompra_equipment_auto_refresh.published_queue).toBe(
      "/home/ops/reports/out/active/current/equipment_first_operator_queue_20260616.csv",
    );
    expect(parsed.chilecompra_equipment_auto_refresh.path_info?.published_queue.basename).toBe(
      "equipment_first_operator_queue_20260616.csv",
    );
    expect(parsed.chilecompra_equipment_auto_refresh.path_info?.candidate_audit.kind).toBe(
      "file",
    );
  });

  it("parseOperatorAutomationStatus tolerates malformed path_info", () => {
    const parsed = parseOperatorAutomationStatus({
      generated_at_utc: "2026-06-16T12:00:00+00:00",
      active_current_dir: "/tmp/active/current",
      active_current_dir_info: "not-an-object",
      verdict: "healthy",
      chilecompra_equipment_auto_refresh: {
        state_exists: true,
        path_info: {
          published_queue: { basename: 123, kind: "file" },
          candidate_audit: null,
        },
      },
      recommended_action: "none",
    });

    expect(parsed.active_current_dir).toBe("/tmp/active/current");
    expect(parsed.active_current_dir_info).toBeNull();
    expect(parsed.chilecompra_equipment_auto_refresh.path_info).toEqual({});
  });

  it("parseRedactedPathInfo accepts redacted companion objects", () => {
    expect(
      parseRedactedPathInfo({ redacted: true, basename: "current", kind: "directory" }),
    ).toEqual({
      redacted: true,
      basename: "current",
      kind: "directory",
    });
  });

  it("parseRedactedPathInfo rejects raw strings and invalid objects", () => {
    expect(parseRedactedPathInfo("/home/ops/reports/out/active/current")).toBeNull();
    expect(parseRedactedPathInfo({ basename: 123, kind: "file" })).toBeNull();
    expect(parseRedactedPathInfo({ basename: "queue.csv", kind: null })).toBeNull();
    expect(parseRedactedPathInfo(null)).toBeNull();
  });

  it("parsePathInfoMap skips invalid entries and keeps basename-only companions", () => {
    const parsed = parsePathInfoMap({
      published_queue: {
        redacted: true,
        basename: "equipment_first_operator_queue_20260616.csv",
        kind: "file",
      },
      candidate_audit: "/home/ops/reports/out/active/current/audit.csv",
      broken: { basename: 1, kind: "file" },
    });
    expect(parsed).toEqual({
      published_queue: {
        redacted: true,
        basename: "equipment_first_operator_queue_20260616.csv",
        kind: "file",
      },
    });
    expect(JSON.stringify(parsed)).not.toContain("/home/ops");
  });

  it("parsePathInfoMap returns null for non-object input", () => {
    expect(parsePathInfoMap("not-a-map")).toBeNull();
    expect(parsePathInfoMap(undefined)).toBeNull();
  });

  it("parseDailyCoreRunStatus parses valid summary fields", () => {
    const parsed = parseDailyCoreRunStatus({
      path: "/reports/active/current/daily_core_run_manifest.json",
      exists: true,
      loaded: true,
      workflow: "daily-core",
      status: "success",
      returncode: 0,
      step_count: 7,
      send_approval: false,
      postgres_mirror: "not included",
    });
    expect(parsed.exists).toBe(true);
    expect(parsed.loaded).toBe(true);
    expect(parsed.workflow).toBe("daily-core");
    expect(parsed.step_count).toBe(7);
    expect(parsed.send_approval).toBe(false);
  });

  it("fetchHealth uses GET only", async () => {
    vi.stubEnv("MODE", "development");
    vi.stubGlobal("window", { location: { origin: "http://127.0.0.1:5173" } });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        service: "origenlab-api",
        mode: "operator-sqlite-readonly",
        backend: "sqlite",
        postgres_configured: false,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const body = await fetchHealth();
    expect(body.backend).toBe("sqlite");
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });

  it("all operator API fetches send credentials include for Cloudflare Access", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "https://api.origenlab.cl");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        verdict: "OK",
        sqlite_path: "",
        campaign_mode: null,
        operator_focus: null,
        outbound_readiness: "n/a",
        warnings: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchOperatorStatus();
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("fetchOperatorStatus throws OperatorApiError on failure", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "http://127.0.0.1:8001");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: "down", text: async () => "" }));
    await expect(fetchOperatorStatus()).rejects.toBeInstanceOf(OperatorApiError);
  });

  it("operatorApiUrl builds warm cases GET path", () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "https://api.example.com");
    const url = operatorApiUrl("/cases/warm", { limit: 100, positive_signal_only: false });
    expect(url).toContain("/cases/warm");
    expect(url).toContain("positive_signal_only=false");
    expect(url).toContain("limit=100");
  });

  it("contactDetailPath URL-encodes email for GET /contacts/{email}", () => {
    expect(contactDetailPath("buyer+tag@acme.cl")).toBe(
      "/contacts/buyer%2Btag%40acme.cl",
    );
    expect(contactDetailPath("  buyer@acme.cl  ")).toBe("/contacts/buyer%40acme.cl");
  });

  it("contactDetailPath rejects invalid email before fetch", () => {
    expect(() => contactDetailPath("not-an-email")).toThrow(OperatorApiError);
    expect(() => contactDetailPath("")).toThrow(OperatorApiError);
  });

  it("fetchContactProfile uses GET only and handles API errors", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "http://127.0.0.1:8001");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: "Unprocessable",
      text: async () => "invalid email",
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchContactProfile("x@y.z")).rejects.toBeInstanceOf(OperatorApiError);
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
    expect(fetchMock.mock.calls[0][0]).toContain("/contacts/");
  });

  it("fetchContactProfile parses successful response", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "http://127.0.0.1:8001");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          meta: { data_source: "sqlite", read_only: true, reduced_mode: false, note: "" },
          contact: {
            email: "a@b.cl",
            normalized_email: "a@b.cl",
            name: "",
            domain: "",
            organization_name: "",
            organization_domain: "",
            last_seen_at: null,
            first_seen_at: null,
            message_count: 0,
          },
          outreach: {
            state: null,
            last_contacted_at: null,
            source: null,
            notes: null,
            do_not_repeat: false,
            suppressed_email: false,
            suppressed_domain: false,
          },
          sent_history: { sent_count: 0, latest_sent_at: null, latest_subject: null },
          warnings: [],
        }),
      }),
    );

    const profile = await fetchContactProfile("a@b.cl");
    expect(profile.contact.normalized_email).toBe("a@b.cl");
  });

  it("operatorApiUrl builds equipment opportunities GET path", () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_ORIGENLAB_API_BASE_URL", "https://api.example.com");
    const url = operatorApiUrl("/opportunities/equipment", { limit: 30 });
    expect(url).toContain("/opportunities/equipment");
    expect(url).toContain("limit=30");
  });
});
