/**
 * V1-lane calendar events and marketing header — pure functions and rendering.
 *
 * Every campaign name, date and label is invented; the repository is public.
 */
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { V1LaneCampaign } from "../crmTypes";
import {
  buildV1LaneEvents,
  todayInSantiago,
  v1LaneStatus,
} from "./calendar";
import { CampaignCalendar } from "./CampaignCalendar";
import { MarketingOverview } from "./MarketingOverview";

// Every value below is invented; the repository is public.
const CYBER: V1LaneCampaign = {
  key: "cyber-2026-10",
  name: "Cyber OrigenLab",
  channel: "v1",
  send_days: ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"],
  send_time: "09:30",
  promo_until: "2026-10-11",
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// ─────────────────────────────────────── buildV1LaneEvents — pure function

describe("buildV1LaneEvents", () => {
  it("produces one event per send day", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z")); // 4 Oct — before first wave
    const events = buildV1LaneEvents([CYBER], todayInSantiago());
    expect(events).toHaveLength(5);
    const days = events.map((e) => e.day);
    expect(days).toEqual(["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]);
  });

  it("assigns kind v1_lane to every event", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z"));
    const events = buildV1LaneEvents([CYBER], todayInSantiago());
    expect(events.every((e) => e.kind === "v1_lane")).toBe(true);
  });

  it("labels each event with the wave number and total", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z"));
    const events = buildV1LaneEvents([CYBER], todayInSantiago());
    expect(events[0].campaignName).toBe("Cyber OrigenLab");
    // First wave detail includes "oleada 1 de 5"
    expect(events[0].detail).toMatch(/oleada 1 de 5/i);
    expect(events[4].detail).toMatch(/oleada 5 de 5/i);
  });

  it("names the V1 lane once: in the event kind, not again in the detail", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z"));
    const events = buildV1LaneEvents([CYBER], todayInSantiago());
    expect(events[0].kind).toBe("v1_lane");
    expect(events[0].detail).not.toMatch(/canal V1/i);
  });

  it("carries the send_time as the event time", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z"));
    const events = buildV1LaneEvents([CYBER], todayInSantiago());
    expect(events.every((e) => e.time === "09:30")).toBe(true);
  });

  it("marks past days with the V1 register note", () => {
    vi.setSystemTime(new Date("2026-10-07T15:00:00Z")); // 7 Oct in Santiago (UTC-3 → 12:00 Santiago)
    const today = todayInSantiago();
    const events = buildV1LaneEvents([CYBER], today);
    const past = events.filter((e) => e.day < today);
    const present = events.find((e) => e.day === today);
    const future = events.filter((e) => e.day > today);
    expect(past.length).toBeGreaterThan(0);
    past.forEach((e) => expect(e.detail).toMatch(/registro V1/i));
    expect(present?.detail).toMatch(/[Ee]n curso/);
    future.forEach((e) => expect(e.detail).not.toMatch(/registro V1/i));
  });

  it("never invents sent/received figures", () => {
    vi.setSystemTime(new Date("2026-10-10T15:00:00Z")); // after all waves
    const events = buildV1LaneEvents([CYBER], todayInSantiago());
    const text = events.map((e) => e.detail).join(" ");
    expect(text).not.toMatch(/\d{3,}/); // no 3-digit numbers (no counts)
  });
});

// ─────────────────────────────────────── v1LaneStatus — per-day status helper

describe("v1LaneStatus", () => {
  it("before first send day → Programada", () => {
    expect(v1LaneStatus("2026-10-05", "2026-10-04")).toBe("Programada");
  });
  it("on a send day → En curso", () => {
    expect(v1LaneStatus("2026-10-07", "2026-10-07")).toBe("En curso");
  });
  it("after a send day → Programada · resultado en el registro V1", () => {
    const status = v1LaneStatus("2026-10-05", "2026-10-06");
    expect(status).toMatch(/registro V1/i);
  });
});

// ─────────────────────────────────────── CampaignCalendar renders v1_lane events

describe("CampaignCalendar v1 lane", () => {
  it("shows v1_lane events in October when v1LaneCampaigns is passed", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z"));
    render(
      <CampaignCalendar
        campaigns={[]}
        taxonomy={null}
        today="2026-10-04"
        onOpen={vi.fn()}
        v1LaneCampaigns={[CYBER]}
      />,
    );
    // navigate to October (should start there since today is Oct 4)
    const events = screen.getAllByTestId("calendar-event");
    expect(events.some((e) => e.getAttribute("data-kind") === "v1_lane")).toBe(true);
  });

  it("v1_lane filter button appears in the filter bar", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z"));
    render(
      <CampaignCalendar
        campaigns={[]}
        taxonomy={null}
        today="2026-10-04"
        onOpen={vi.fn()}
        v1LaneCampaigns={[CYBER]}
      />,
    );
    expect(screen.getByRole("button", { name: /canal v1/i })).toBeInTheDocument();
  });

  it("shows events for all 5 send days in the desktop grid", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z"));
    render(
      <CampaignCalendar
        campaigns={[]}
        taxonomy={null}
        today="2026-10-04"
        onOpen={vi.fn()}
        v1LaneCampaigns={[CYBER]}
      />,
    );
    // Scope to the calendar grid only (the agenda duplicates the same events for mobile).
    const grid = screen.getByTestId("calendar-grid");
    const events = [...grid.querySelectorAll("[data-testid=calendar-event]")].filter(
      (e) => e.getAttribute("data-kind") === "v1_lane",
    );
    expect(events).toHaveLength(5);
  });
});

// ─────────────────────────────────────── MarketingOverview — V1 header banner

describe("MarketingOverview v1 lane banner", () => {
  it("shows «Programada por el canal V1» before the first send day", () => {
    vi.setSystemTime(new Date("2026-10-04T15:00:00Z")); // 4 Oct Santiago
    render(
      <MarketingOverview
        campaigns={[]}
        today="2026-10-04"
        onOpen={vi.fn()}
        v1LaneCampaigns={[CYBER]}
      />,
    );
    expect(screen.getByTestId("v1-lane-status")).toHaveTextContent(/Programada por el canal V1/i);
    expect(screen.getByTestId("v1-lane-status")).toHaveTextContent(/Cyber OrigenLab/i);
  });

  it("shows «En curso por el canal V1» during the campaign window", () => {
    vi.setSystemTime(new Date("2026-10-07T15:00:00Z")); // 7 Oct Santiago
    render(
      <MarketingOverview
        campaigns={[]}
        today="2026-10-07"
        onOpen={vi.fn()}
        v1LaneCampaigns={[CYBER]}
      />,
    );
    expect(screen.getByTestId("v1-lane-status")).toHaveTextContent(/En curso por el canal V1/i);
  });

  it("shows nothing after promo_until", () => {
    vi.setSystemTime(new Date("2026-10-12T15:00:00Z")); // 12 Oct — after promo_until (11 Oct)
    render(
      <MarketingOverview
        campaigns={[]}
        today="2026-10-12"
        onOpen={vi.fn()}
        v1LaneCampaigns={[CYBER]}
      />,
    );
    expect(screen.queryByTestId("v1-lane-status")).toBeNull();
  });

  it("shows nothing when v1LaneCampaigns is absent", () => {
    vi.setSystemTime(new Date("2026-10-07T15:00:00Z"));
    render(
      <MarketingOverview
        campaigns={[]}
        today="2026-10-07"
        onOpen={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("v1-lane-status")).toBeNull();
  });
});
