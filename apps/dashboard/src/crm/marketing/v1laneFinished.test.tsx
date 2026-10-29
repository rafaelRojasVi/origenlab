/** Finished V1-lane campaigns: placement, «Último envío», «Próxima campaña». All data invented. */
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CampaignSummary, V1LaneCampaign } from "../crmTypes";
import { isV1LaneFinished, mergeFinishedV1, overviewFigures } from "./calendar";
import { MarketingOverview } from "./MarketingOverview";
import { V1LaneCampaignCard } from "./V1LaneCampaignCard";

const lane = (key: string, name: string, send_days: string[], promo_until: string): V1LaneCampaign => ({
  key, name, channel: "v1", send_days, send_time: "09:30", promo_until,
});
const FINISHED = lane("a", "Campaña Alfa", ["2026-10-05", "2026-10-06", "2026-10-09"], "2026-10-25");
const RUNNING = lane("b", "Campaña Beta", ["2026-10-16", "2026-10-19", "2026-10-22"], "2026-10-30");
const UPCOMING = lane("c", "Campaña Gama", ["2026-10-26", "2026-10-27"], "2026-11-05");
const TODAY = "2026-10-19";

function crm(id: string, name: string, over: Partial<CampaignSummary> = {}): CampaignSummary {
  return {
    campaign_id: id, name, status: "sent", recipients_by_state: {}, send_attempts: [],
    created_at: "2026-09-01T12:00:00Z", first_sent_at: null, last_sent_at: null, approved_at: null,
    ...over,
  } as unknown as CampaignSummary;
}
const SEPT = crm("s", "Septiembre de prueba", {
  last_sent_at: "2026-09-17T20:00:00Z",
  send_batches: [{ day: "2026-09-17", accepted: 5, first_accepted_at: "2026-09-17T19:00:00Z", last_accepted_at: "2026-09-17T20:00:00Z" }],
});

beforeEach(() => vi.useFakeTimers({ toFake: ["Date"] }));
afterEach(() => vi.useRealTimers());

describe("finished V1-lane campaigns", () => {
  it("is finished only when the last send day is before today", () => {
    expect(isV1LaneFinished(FINISHED, TODAY)).toBe(true);
    expect(isV1LaneFinished(RUNNING, TODAY)).toBe(false); // sends on 22 Oct
    expect(isV1LaneFinished(UPCOMING, TODAY)).toBe(false);
    expect(isV1LaneFinished(lane("d", "Hoy", ["2026-10-19"], "2026-10-25"), TODAY)).toBe(false); // last day is today
  });

  it("sits among the sent campaigns by date, newest first, without reordering the CRM ones", () => {
    const newer = crm("n", "Octubre CRM", { last_sent_at: "2026-10-12T15:00:00Z" });
    const older = crm("o", "Agosto CRM", { last_sent_at: "2026-08-01T15:00:00Z" });
    const items = mergeFinishedV1([newer, SEPT, older], [FINISHED]);
    expect(items.map((i) => (i.kind === "v1" ? i.campaign.key : i.campaign.campaign_id))).toEqual(["n", "a", "s", "o"]);
  });

  it("renders «Envío terminado · resultados por importar» with a «canal V1» tag", () => {
    vi.setSystemTime(new Date("2026-10-19T15:00:00Z"));
    render(<V1LaneCampaignCard campaign={FINISHED} />);
    expect(screen.getByText("Envío terminado · resultados por importar")).toBeInTheDocument();
    expect(screen.getByTestId("v1-lane-tag")).toHaveTextContent("canal V1");
  });

  it("a running campaign keeps its running label and has no finished tag", () => {
    vi.setSystemTime(new Date("2026-10-19T15:00:00Z"));
    render(<V1LaneCampaignCard campaign={RUNNING} />);
    expect(screen.getByText("En curso · canal V1")).toBeInTheDocument();
    expect(screen.queryByTestId("v1-lane-tag")).toBeNull();
  });
});

describe("«Último envío» with the V1 lane", () => {
  it("takes the finished V1 campaign when it is more recent than the CRM send", () => {
    const f = overviewFigures([SEPT], TODAY, [FINISHED]);
    expect(f.lastSend).toEqual({ day: "2026-10-09", campaignName: "Campaña Alfa", daysAgo: 10 });
  });

  it("counts a running campaign by its latest send day up to today, never a future day", () => {
    const f = overviewFigures([SEPT], TODAY, [RUNNING]);
    expect(f.lastSend).toMatchObject({ day: "2026-10-19", campaignName: "Campaña Beta", daysAgo: 0 });
    expect(overviewFigures([SEPT], TODAY, [UPCOMING]).lastSend).toMatchObject({ campaignName: "Septiembre de prueba" });
  });

  it("keeps the CRM send when it is more recent", () => {
    const recent = crm("r", "Reciente", {
      send_batches: [{ day: "2026-10-14", accepted: 1, first_accepted_at: "2026-10-14T15:00:00Z", last_accepted_at: "2026-10-14T15:00:00Z" }],
    });
    expect(overviewFigures([recent], TODAY, [FINISHED]).lastSend?.campaignName).toBe("Reciente");
  });

  it("renders «hace 10 días» and the V1 campaign name", () => {
    render(<MarketingOverview campaigns={[SEPT]} today={TODAY} onOpen={vi.fn()} v1LaneCampaigns={[FINISHED]} />);
    expect(screen.getByTestId("last-send")).toHaveTextContent("Último envío: hace 10 días");
    expect(screen.getByTestId("marketing-overview")).toHaveTextContent("Campaña Alfa");
  });
});

describe("«Próxima campaña» with the V1 lane", () => {
  it("shows the nearest upcoming V1 campaign when no CRM campaign is planned", () => {
    const f = overviewFigures([SEPT], TODAY, [FINISHED, UPCOMING]);
    expect(f.nextPlanned).toMatchObject({ day: "2026-10-26", campaignName: "Campaña Gama", campaignId: null, inDays: 7 });
    render(<MarketingOverview campaigns={[SEPT]} today={TODAY} onOpen={vi.fn()} v1LaneCampaigns={[FINISHED, UPCOMING]} />);
    expect(screen.getByTestId("next-planned")).toHaveTextContent("Próxima campaña: en 7 días");
    expect(screen.getByText(/Campaña Gama · canal V1/)).toBeInTheDocument();
  });

  it("uses the next send day of a running campaign, and says none when nothing is ahead", () => {
    expect(overviewFigures([], TODAY, [RUNNING]).nextPlanned).toMatchObject({ day: "2026-10-22", inDays: 3 });
    expect(overviewFigures([], TODAY, [FINISHED]).nextPlanned).toBeNull();
  });

  it("prefers a CRM plan that is nearer than the V1 one", () => {
    const plan = crm("p", "Plan CRM", { status: "draft", planned_for_date: "2026-10-21" });
    expect(overviewFigures([plan], TODAY, [UPCOMING]).nextPlanned).toMatchObject({ campaignId: "p", day: "2026-10-21" });
  });
});
