import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchOperatorAutomationStatus } from "./operatorClient";

const AUTOMATION_STATUS_RESPONSE = {
  generated_at_utc: "2026-07-02T16:14:00+00:00",
  active_current_dir: "current",
  verdict: "healthy",
  daily_core: {
    exists: true,
    status: "success",
    returncode: 0,
    generated_at_utc: "2026-07-02T16:07:17+00:00",
    age_seconds: 400,
    steps: 8,
  },
  mail_auto_refresh: {
    state_exists: true,
    paused: false,
    lock_live: false,
    dirty: false,
    pending: false,
    last_result: "no_change",
    last_successful_refresh_at: "2026-07-02T16:07:17+00:00",
    last_seen_inbox_total: 759,
    last_seen_sent_total: 1048,
    consecutive_failures: 0,
  },
  dashboard_auto_mirror: {
    state_exists: true,
    paused: false,
    lock_live: false,
    last_result: "success",
    last_successful_mirror_at: "2026-07-02T16:12:00+00:00",
    last_mirrored_daily_core_generated_at: "2026-07-02T16:07:17+00:00",
    mirror_matches_daily_core: true,
    cooldown_seconds: 900,
    cooldown_remaining_seconds: 0,
    consecutive_failures: 0,
  },
  chilecompra_equipment_auto_refresh: {
    state_exists: true,
    lock_live: false,
    consecutive_failures: 0,
  },
  cron: { note: "not inspected during snapshot publish" },
  recommended_action: "none",
  warnings: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("operator automation status client", () => {
  it("reads /operator/automation-status with GET only", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => AUTOMATION_STATUS_RESPONSE,
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchOperatorAutomationStatus();

    expect(status.verdict).toBe("healthy");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/operator/automation-status");
    expect(String(url)).toContain("cooldown-seconds=900");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
    expect(init.headers).toEqual({ Accept: "application/json" });
    expect(String(url)).not.toMatch(/send|draft|archive|trash|apply|ingest|mirror-dashboard/i);
  });
});
