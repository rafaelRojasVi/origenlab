import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OperatorAutomationStatus } from "../../api/operatorTypes";
import { AutomationHealthCard } from "./AutomationHealthCard";

const BASE_STATUS: OperatorAutomationStatus = {
  generated_at_utc: "2026-07-02T16:14:00+00:00",
  active_current_dir: "current",
  active_current_dir_info: {
    redacted: true,
    basename: "current",
    kind: "directory",
  },
  path_redaction_applied: true,
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
    lock_age_seconds: null,
    last_result: "refreshed",
    last_successful_refresh_at: "2026-07-02T15:25:29+00:00",
    last_successful_publish_at: "2026-07-02T15:25:29+00:00",
    freshness_age_seconds: 1200,
    next_run_due: false,
    consecutive_failures: 0,
    published_rows: 4,
  },
  cron: { note: "not inspected during snapshot publish" },
  recommended_action: "none",
  warnings: [],
  source: "postgres_snapshot",
  snapshot_updated_at: "2026-07-02T16:12:01+00:00",
  snapshot_stale: false,
};

vi.mock("../../api/operatorClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/operatorClient")>();
  return {
    ...actual,
    fetchOperatorAutomationStatus: vi.fn(),
  };
});

import { fetchOperatorAutomationStatus } from "../../api/operatorClient";

const mockFetchOperatorAutomationStatus = vi.mocked(fetchOperatorAutomationStatus);

afterEach(() => {
  vi.clearAllMocks();
});

describe("AutomationHealthCard automatic read-only refresh", () => {
  it("fetches operator automation status once on first mount without a click", async () => {
    mockFetchOperatorAutomationStatus.mockResolvedValue(BASE_STATUS);

    render(<AutomationHealthCard />);

    await screen.findByText("Automatización al día");

    expect(mockFetchOperatorAutomationStatus).toHaveBeenCalledTimes(1);
    expect(mockFetchOperatorAutomationStatus).toHaveBeenCalledWith();
  });

  it("keeps the manual refresh button as the same read-only status fetch", async () => {
    mockFetchOperatorAutomationStatus.mockResolvedValue(BASE_STATUS);
    const onRefresh = vi.fn();

    render(<AutomationHealthCard variant="detailed" onRefresh={onRefresh} />);

    await screen.findByText("Estado de automatización");
    expect(mockFetchOperatorAutomationStatus).toHaveBeenCalledTimes(1);

    mockFetchOperatorAutomationStatus.mockResolvedValueOnce({
      ...BASE_STATUS,
      verdict: "attention",
      recommended_action: "run_auto_mirror_dashboard",
      dashboard_auto_mirror: {
        ...BASE_STATUS.dashboard_auto_mirror,
        mirror_matches_daily_core: false,
      },
    });

    fireEvent.click(screen.getByRole("button", { name: /Actualizar estado/i }));

    await waitFor(() => {
      expect(mockFetchOperatorAutomationStatus).toHaveBeenCalledTimes(2);
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Enviar|Archivar|Publicar|Ejecutar/i })).toBeNull();
  });
});
