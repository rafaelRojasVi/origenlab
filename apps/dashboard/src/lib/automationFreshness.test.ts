import { describe, expect, it } from "vitest";
import type { OperatorAutomationStatus } from "../api/operatorTypes";
import {
  buildAutomationFreshnessSummary,
  formatAutomationFreshnessAgeLabel,
} from "./automationFreshness";

const NOW = new Date("2026-06-10T18:20:00+00:00");

function minutesAgoIso(minutes: number, now: Date = NOW): string {
  return new Date(now.getTime() - minutes * 60_000).toISOString();
}

function baseStatus(overrides: Partial<OperatorAutomationStatus> = {}): OperatorAutomationStatus {
  return {
    generated_at_utc: "2026-06-10T18:12:48+00:00",
    active_current_dir: "/hidden/active/current",
    verdict: "healthy",
    daily_core: {
      exists: true,
      status: "success",
      returncode: 0,
      generated_at_utc: "2026-06-10T18:12:48+00:00",
      age_seconds: 432,
      steps: 8,
    },
    mail_auto_refresh: {
      state_exists: true,
      paused: false,
      lock_live: false,
      dirty: false,
      pending: false,
      last_result: "no_change",
      last_successful_refresh_at: "2026-06-10T18:12:48+00:00",
      last_seen_inbox_total: 403,
      last_seen_sent_total: 971,
      consecutive_failures: 0,
    },
    dashboard_auto_mirror: {
      state_exists: true,
      paused: false,
      lock_live: false,
      last_result: "success",
      last_successful_mirror_at: "2026-06-10T18:18:33+00:00",
      last_mirrored_daily_core_generated_at: "2026-06-10T18:12:48+00:00",
      mirror_matches_daily_core: true,
      cooldown_seconds: 900,
      cooldown_remaining_seconds: 0,
      consecutive_failures: 0,
    },
    chilecompra_equipment_auto_refresh: {
      state_exists: false,
      lock_live: false,
      lock_age_seconds: null,
      freshness_age_seconds: null,
      next_run_due: null,
      consecutive_failures: 0,
    },
    cron: { note: "not inspected by API" },
    recommended_action: "none",
    warnings: [],
    snapshot_updated_at: "2026-06-10T18:15:00+00:00",
    snapshot_stale: false,
    ...overrides,
  };
}

describe("buildAutomationFreshnessSummary", () => {
  it("returns fresh when recent Postgres success is under threshold", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_successful_mirror_at: minutesAgoIso(180),
          last_run_finished_at: null,
          last_result: "success",
        },
        dashboard_mirror_sync: {
          status: "success",
          finished_at: minutesAgoIso(5),
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("fresh");
    expect(summary.title).toBe("Automatización al día");
    expect(summary.mirrorSourceLabel).toBe("Espejo Postgres");
    expect(summary.mirrorAgeLabel).toBe("hace 5 min");
  });

  it("returns fresh when old Postgres success plus recent already_mirrored and parity true", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(2),
          last_successful_mirror_at: minutesAgoIso(180),
          mirror_matches_daily_core: true,
        },
        dashboard_mirror_sync: {
          status: "success",
          finished_at: minutesAgoIso(150),
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("fresh");
    expect(summary.title).toBe("Automatización al día");
    expect(summary.mirrorAgeLabel).toBe("hace 2 min");
    expect(summary.mirrorMaterialAgeLabel).toBe("hace 3 h");
    expect(summary.detail).toMatch(/comprobaciones recientes/i);
  });

  it("does not stale Gmail when useful refresh is old but recent no_change check exists", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_result: "no_change",
          last_run_finished_at: minutesAgoIso(3),
          last_successful_refresh_at: minutesAgoIso(600),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(3),
          last_successful_mirror_at: minutesAgoIso(3),
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("fresh");
    expect(summary.gmailAgeLabel).toBe("hace 3 min");
    expect(summary.gmailMaterialAgeLabel).toBe("hace 10 h");
  });

  it("does not stale mirror when material publication is old but recent parity check exists", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_run_finished_at: minutesAgoIso(4),
          last_successful_refresh_at: minutesAgoIso(4),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(4),
          last_successful_mirror_at: minutesAgoIso(240),
          mirror_matches_daily_core: true,
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("fresh");
    expect(summary.mirrorAgeLabel).toBe("hace 4 min");
    expect(summary.mirrorMaterialAgeLabel).toBe("hace 4 h");
  });

  it("warns when recent check exists but mail.dirty is true", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          dirty: true,
          last_result: "no_change",
          last_run_finished_at: minutesAgoIso(2),
          last_successful_refresh_at: minutesAgoIso(2),
        },
        dashboard_mirror_sync: {
          status: "success",
          finished_at: minutesAgoIso(5),
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("warning");
    expect(summary.title).toBe("Correo pendiente de procesar");
  });

  it("stales when recent already_mirrored exists but parity is false", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(1),
          last_successful_mirror_at: minutesAgoIso(1),
          mirror_matches_daily_core: false,
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("stale");
    expect(summary.title).toBe("Espejo desalineado con daily-core");
  });

  it("does not treat consecutive failures as fresh", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_run_finished_at: minutesAgoIso(1),
          consecutive_failures: 2,
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_run_finished_at: minutesAgoIso(1),
          consecutive_failures: 0,
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("stale");
    expect(summary.title).toBe("Automatización con fallos");
  });

  it("does not count failed Postgres lifecycle as freshness proof", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_successful_mirror_at: minutesAgoIso(60),
          last_run_finished_at: null,
          last_result: "error",
        },
        dashboard_mirror_sync: {
          status: "failed",
          finished_at: minutesAgoIso(2),
        },
      }),
      { now: NOW },
    );
    expect(summary.mirrorSourceLabel).toBe("Loop auto-mirror");
    expect(summary.tone).toBe("stale");
  });

  it("does not count running Postgres lifecycle as terminal freshness proof", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_successful_mirror_at: minutesAgoIso(60),
          last_run_finished_at: null,
        },
        dashboard_mirror_sync: {
          status: "running",
          finished_at: null,
          started_at: minutesAgoIso(1),
        },
      }),
      { now: NOW },
    );
    expect(summary.mirrorSourceLabel).toBe("Loop auto-mirror");
    expect(summary.tone).toBe("stale");
  });

  it("returns stale when successful checks are older than configured thresholds", () => {
    const summary = buildAutomationFreshnessSummary(baseStatus(), {
      now: new Date("2026-06-10T18:45:00+00:00"),
    });
    expect(summary.tone).toBe("stale");
    expect(summary.title).toBe("Loop auto-mirror desactualizado");
  });

  it("returns fresh when recent snapshot and verified loops are healthy", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_run_finished_at: minutesAgoIso(2),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(2),
        },
        snapshot_updated_at: minutesAgoIso(5),
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("fresh");
    expect(summary.snapshotAgeLabel).toBe("hace 5 min");
  });

  it("keeps snapshot stale even when loops are healthy", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_run_finished_at: minutesAgoIso(1),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(1),
        },
        snapshot_stale: true,
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("stale");
    expect(summary.warning).toBe("Dashboard puede estar desactualizado.");
  });

  it("returns unknown for invalid timestamps", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_successful_refresh_at: "not-a-date",
          last_run_finished_at: "also-bad",
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_successful_mirror_at: "also-bad",
          last_run_finished_at: "bad",
        },
        snapshot_updated_at: "invalid",
        generated_at_utc: "invalid",
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("unknown");
    expect(summary.gmailAgeLabel).toBe("sin dato");
    expect(summary.mirrorAgeLabel).toBe("sin dato");
    expect(summary.snapshotAgeLabel).toBe("sin dato");
  });

  it("keeps Spanish labels coherent for healthy verified automation", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_run_finished_at: minutesAgoIso(1),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(1),
        },
      }),
      { now: NOW },
    );
    expect(summary.title).toBe("Automatización al día");
    expect(summary.detail).toMatch(/No hay cambios pendientes/i);
    expect(summary.gmailMaterialAgeLabel).toMatch(/^hace /);
    expect(summary.mirrorMaterialAgeLabel).toMatch(/^hace /);
  });

  it("renders production-like contradictory fixture as non-red healthy state", () => {
    // Mirrors observed production: healthy verdict, recent no_change / already_mirrored,
    // old material ages, old Postgres success, recent snapshot.
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        generated_at_utc: minutesAgoIso(1),
        snapshot_updated_at: minutesAgoIso(1),
        snapshot_stale: false,
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_result: "no_change",
          dirty: false,
          pending: false,
          consecutive_failures: 0,
          last_run_finished_at: minutesAgoIso(1),
          last_successful_refresh_at: minutesAgoIso(648),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          mirror_matches_daily_core: true,
          consecutive_failures: 0,
          paused: false,
          lock_live: false,
          last_run_finished_at: minutesAgoIso(1),
          last_successful_mirror_at: minutesAgoIso(273),
        },
        dashboard_mirror_sync: {
          status: "success",
          finished_at: minutesAgoIso(146),
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("fresh");
    expect(summary.title).toBe("Automatización al día");
    expect(summary.gmailAgeLabel).toBe("hace 1 min");
    expect(summary.mirrorAgeLabel).toBe("hace 1 min");
    expect(summary.gmailMaterialAgeLabel).toBe("hace 10 h");
    expect(summary.mirrorMaterialAgeLabel).toBe("hace 4 h");
  });

  it("keeps material publication timestamps visible as informational age", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_run_finished_at: minutesAgoIso(2),
          last_successful_refresh_at: minutesAgoIso(90),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_result: "already_mirrored",
          last_run_finished_at: minutesAgoIso(2),
          last_successful_mirror_at: minutesAgoIso(120),
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("fresh");
    expect(summary.gmailMaterialAgeLabel).toBe("hace 1 h");
    expect(summary.mirrorMaterialAgeLabel).toBe("hace 2 h");
  });

  it("returns warning when Gmail lacks a recent check/refresh but mirror is fresh", () => {
    const summary = buildAutomationFreshnessSummary(baseStatus(), {
      now: new Date("2026-06-10T18:25:00+00:00"),
    });
    expect(summary.tone).toBe("warning");
    expect(summary.title).toBe("Gmail/SQLite con retraso");
    expect(summary.detail).toMatch(/Gmail → SQLite/i);
  });

  it("prefers dashboard_mirror_sync finished_at over stale loop timestamp", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          dirty: false,
          last_run_finished_at: minutesAgoIso(5),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_successful_mirror_at: "2026-06-10T12:00:00+00:00",
          last_result: "mail_dirty",
          mirror_matches_daily_core: false,
        },
        dashboard_mirror_sync: {
          status: "success",
          finished_at: "2026-06-10T18:15:00+00:00",
          latest_sync_id: 135,
        },
      }),
      { now: NOW },
    );
    expect(summary.mirrorSourceLabel).toBe("Espejo Postgres");
    expect(summary.mirrorAgeLabel).toBe("hace 5 min");
    // Parity false remains an attention/stale signal.
    expect(summary.tone).toBe("stale");
    expect(summary.title).toBe("Espejo desalineado con daily-core");
  });

  it("warns when postgres sync is fresh but mail is dirty", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          dirty: true,
          last_run_finished_at: minutesAgoIso(5),
          last_successful_refresh_at: minutesAgoIso(5),
        },
        dashboard_auto_mirror: {
          ...baseStatus().dashboard_auto_mirror,
          last_successful_mirror_at: "2026-06-09T12:00:00+00:00",
          last_result: "mail_dirty",
          mirror_matches_daily_core: false,
        },
        dashboard_mirror_sync: {
          status: "success",
          finished_at: "2026-06-10T18:15:00+00:00",
        },
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("warning");
    expect(summary.title).toBe("Correo pendiente de procesar");
  });

  it("falls back to loop auto-mirror when dashboard_mirror_sync is missing", () => {
    const summary = buildAutomationFreshnessSummary(baseStatus(), { now: NOW });
    expect(summary.mirrorSourceLabel).toBe("Loop auto-mirror");
    expect(summary.mirrorAgeLabel).toBe("hace 1 min");
  });

  it("falls back to loop auto-mirror when dashboard_mirror_sync failed", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        dashboard_mirror_sync: {
          status: "failed",
          finished_at: "2026-06-10T18:15:00+00:00",
        },
      }),
      { now: NOW },
    );
    expect(summary.mirrorSourceLabel).toBe("Loop auto-mirror");
    expect(summary.mirrorAgeLabel).toBe("hace 1 min");
  });

  it("returns stale with dashboard warning when snapshot_stale is true", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({ snapshot_stale: true }),
      { now: NOW },
    );
    expect(summary.tone).toBe("stale");
    expect(summary.warning).toBe("Dashboard puede estar desactualizado.");
  });

  it("returns unknown when core timestamps are missing", () => {
    const summary = buildAutomationFreshnessSummary(
      baseStatus({
        mail_auto_refresh: {
          ...baseStatus().mail_auto_refresh,
          last_successful_refresh_at: null,
          last_run_finished_at: null,
        },
        snapshot_updated_at: null,
        generated_at_utc: "",
      }),
      { now: NOW },
    );
    expect(summary.tone).toBe("unknown");
    expect(summary.warning).toBe("No se pudo confirmar frescura completa.");
    expect(summary.gmailAgeLabel).toBe("sin dato");
  });

  it("formats ages older than one hour in hours", () => {
    expect(
      formatAutomationFreshnessAgeLabel(
        NOW.getTime() - new Date("2026-06-10T16:50:00+00:00").getTime(),
      ),
    ).toBe("hace 1 h");
  });
});
