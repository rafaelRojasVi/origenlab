import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import type { CampaignSummary } from "../crmTypes";
import { MarketingPage } from "../pages/MarketingPage";
import type { MarketingAnalytics } from "./analytics";
import { sendRange } from "./CampaignRows";

// Every campaign, address and figure below is invented; the repository is public.
const ID = "d0000000-0000-4000-8000-000000000001";
const SENT: CampaignSummary = {
  campaign_id: ID, name: "Balanzas (ficticia)", status: "archived", subject: "Asunto ficticio", approved_at: null,
  created_at: "2026-10-19T12:00:00Z", first_sent_at: "2026-10-20T13:00:00Z", last_sent_at: "2026-10-27T14:00:00Z",
  recipients_by_state: { sent: 400 }, send_attempts: [{ submission_state: "accepted", delivery_state: "pending", count: 400 }],
  send_batches: [], origin: "imported_v1",
  totals: { audience: 400, included: 400, sent: 400, excluded: 0, blocked: 0, unsent: 0, rejected: 0, bounced: 0, responses: 0 },
};

function analytics(overrides: Partial<MarketingAnalytics> = {}): MarketingAnalytics {
  return {
    reply_window_days: 14,
    overview: { reached: 700, writable: 590, bounced: 98, baja: 2, other_blocks: 10, with_open_case: 31 },
    campaigns: { [ID]: { sent: 400, new_people: 0, bounced: 11, baja: 1, responded: 12, quote_request: 3, cases_after: 2, ndr_unblocked: 0 } },
    coverage: [
      { segment: "sin_caso", people: 900, contacted: 500, not_contacted: 350, blocked: 50 },
      { segment: "solicitada", people: 10, contacted: 4, not_contacted: 6, blocked: 0 },
      { segment: "enviada", people: 0, contacted: 0, not_contacted: 0, blocked: 0 },
      { segment: "conversacion", people: 0, contacted: 0, not_contacted: 0, blocked: 0 },
      { segment: "pausa", people: 2, contacted: 1, not_contacted: 1, blocked: 0 },
      { segment: "ganada", people: 40, contacted: 30, not_contacted: 8, blocked: 2 },
      { segment: "perdida", people: 5, contacted: 5, not_contacted: 0, blocked: 0 },
    ],
    alerts: { ndr_unblocked: 0, soft_bounces: 0 },
    blocks: {
      by_kind: [{ kind: "not_in_directory", n: 120 }, { kind: "rebote_revisado", n: 300 }, { kind: "baja", n: 2 }],
      by_week: [{ week: "2026-10-19", rebote: 300, baja: 2, otro: 0 }, { week: "2026-10-26", rebote: 120, baja: 0, otro: 0 }],
    },
    mail_captured_from: "2026-09-01T00:00:00+00:00",
    ...overrides,
  };
}

const LEGACY_HOLDS = {
  all_campaigns: { blocked: false, block: null, block_version: 0 },
  legacy: [{
    block_id: "b0000000-0000-4000-8000-000000000001", scope: "legacy_campaign", scope_label: "Campaña del registro V1",
    campaign_id: null, legacy_campaign_key: "ola-ficticia", reason: "Retención ficticia", reference: "ficticia",
    placed_at: "2026-09-27T23:45:00Z", placed_by_kind: "migrator", placed_by: "Migración", lifted_at: null, lifted_by: null,
    lift_reason: null, version: 1, active: true,
  }],
  active: [], recently_lifted: [], by_campaign: {}, effect: "x", expires: false, commands_enabled: false, may_decide: false,
};

const PLAN = {
  key: "balanzas-ficticia", name: "Balanzas (ficticia)", channel: "v1", send_days: ["2026-10-20", "2026-11-03"],
  send_time: "09:30", promo_until: "2026-11-10", clients_per_day: [200, 200], total_clients: 400, audience_rule: "x",
  html: null, subject: "Asunto ficticio", imported: true,
};

function stub({ data = analytics(), campaign = SENT, plans = [] as unknown[] }: { data?: MarketingAnalytics; campaign?: CampaignSummary; plans?: unknown[] } = {}) {
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      calls.push(url);
      const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
      const p = url.pathname;
      if (p.endsWith("/v2/workspace/marketing")) {
        return json({ campaigns: [campaign], contact_controls: [], authoring: {}, storage: { table: "outbound.campaign", database: "origenlab_clean" }, v1_lane_campaigns: plans });
      }
      if (p.endsWith("/marketing/taxonomy")) return json(taxonomyJson);
      if (p.endsWith("/marketing/analytics")) return json(data);
      if (p.endsWith("/marketing/analytics/people") && url.searchParams.get("metric") === "responded") {
        return json({ total: null, limit: 50, offset: 0, rows: [] });
      }
      if (p.endsWith("/marketing/analytics/people")) {
        return json({ total: 1, limit: 50, offset: 0, rows: [{ address_norm: "ana@instituto.invalid", opportunity_id: null, person_id: null,
          name: "Ana Ficticia", organization: "Instituto Ficticio", case_stage: null, case_title: null }] });
      }
      if (p.endsWith("/marketing/campaign-blocks")) return json(LEGACY_HOLDS);
      return Promise.resolve(new Response(JSON.stringify({ detail: "not found" }), { status: 404 }));
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Marketing analytics", () => {
  it("renders KPIs from analytics, the writable one first", async () => {
    stub();
    render(<MarketingPage />);
    const kpis = await screen.findByTestId("analytics-kpis");
    const buttons = within(kpis).getAllByRole("button");
    expect(buttons[0]).toHaveTextContent("Aún se les puede escribir");
    expect(buttons[0]).toHaveTextContent("590");
    expect(screen.getByTestId("kpi-reached")).toHaveTextContent("700");
    expect(screen.getByTestId("kpi-bounced")).toHaveTextContent("98");
    expect(screen.getByTestId("kpi-with_open_case")).toHaveTextContent("31");
  });

  it("a KPI opens the people it counts", async () => {
    const calls = stub();
    render(<MarketingPage />);
    fireEvent.click(await screen.findByTestId("kpi-with_open_case"));
    expect(await screen.findByTestId("people-list")).toHaveTextContent("Ana Ficticia");
    const people = calls.filter((u) => u.pathname.endsWith("/analytics/people")).at(-1)!;
    expect(people.searchParams.get("metric")).toBe("with_open_case");
    expect(people.searchParams.get("campaign_id")).toBeNull();
  });

  it("null renders «—» not 0", async () => {
    stub({ data: analytics({ campaigns: { [ID]: { sent: 400, new_people: 0, bounced: 11, baja: 1, responded: null, quote_request: null, cases_after: 0, ndr_unblocked: null } } }) });
    render(<MarketingPage />);
    const row = await screen.findByTestId("campaign-row");
    await waitFor(() => expect(within(row).getAllByTitle("Aún no medido")).toHaveLength(2));
  });

  it("coverage groups open cases and a cell opens its people", async () => {
    const calls = stub();
    render(<MarketingPage />);
    const open = await screen.findByTestId("coverage-abierto");
    expect(open).toHaveTextContent("Con caso abierto");
    expect(open).toHaveTextContent("12"); // solicitada 10 + pausa 2
    fireEvent.click(within(open).getByRole("button", { name: "Nunca les escribimos: 7" }));
    await screen.findByTestId("people-list");
    const q = calls.filter((u) => u.pathname.endsWith("/analytics/people")).at(-1)!;
    expect(q.searchParams.get("segment")).toBe("abierto");
    expect(q.searchParams.get("column")).toBe("not_contacted");
    expect(screen.getByTestId("coverage-total")).toHaveTextContent("957");
    fireEvent.click(within(open).getByRole("button", { name: "ver detalle" }));
    expect(screen.getByTestId("coverage-open-detail")).toHaveTextContent("En pausa");
  });

  it("campaign row shows measured results and its click opens the detail", async () => {
    stub();
    render(<MarketingPage />);
    const row = await screen.findByTestId("campaign-row");
    await waitFor(() => expect(row).toHaveTextContent("2,8 %"));
    expect(row).toHaveTextContent("20–27 oct");
    fireEvent.click(row);
    expect(await screen.findByTestId("campaign-detail")).toBeInTheDocument();
    expect(await screen.findByTestId("result-quote_request")).toHaveTextContent("3");
  });

  it("a figure in a row opens its people without opening the detail", async () => {
    const calls = stub();
    render(<MarketingPage />);
    const row = await screen.findByTestId("campaign-row");
    await waitFor(() => within(row).getByRole("button", { name: "3" }));
    fireEvent.click(within(row).getByRole("button", { name: "3" }));
    await screen.findByTestId("people-list");
    expect(screen.queryByTestId("campaign-detail")).toBeNull();
    const q = calls.filter((u) => u.pathname.endsWith("/analytics/people")).at(-1)!;
    expect(q.searchParams.get("metric")).toBe("quote_request");
    expect(q.searchParams.get("campaign_id")).toBe(ID);
  });

  it("an old V1 hold is not a box on top; it lives in «Bajas y rebotes»", async () => {
    stub();
    render(<MarketingPage />);
    await screen.findByTestId("campaign-row");
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("holds-banner")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Bajas y rebotes" }));
    expect(await screen.findByTestId("holds-banner")).toHaveTextContent("retención");
  });

  it("no «no sincronizadas» copy anywhere when responded is measured", async () => {
    stub();
    render(<MarketingPage />);
    await screen.findByTestId("campaign-row");
    await screen.findByTestId("analytics-kpis");
    expect(screen.queryByText(/no sincronizadas/i)).toBeNull();
    expect(screen.queryByText(/lo planificado, no lo enviado/)).toBeNull();
  });

  it("alert hidden when ndr_unblocked is 0, shown with the count otherwise", async () => {
    stub();
    const view = render(<MarketingPage />);
    await screen.findByTestId("analytics-kpis");
    expect(screen.queryByTestId("analytics-alert")).toBeNull();
    view.unmount();
    vi.unstubAllGlobals();
    stub({ data: analytics({ alerts: { ndr_unblocked: 7, soft_bounces: 40 } }) });
    render(<MarketingPage />);
    expect(await screen.findByTestId("analytics-alert")).toHaveTextContent("7 rebotes permanentes");
    expect(screen.getByTestId("soft-bounces")).toHaveTextContent("40 rebotes temporales");
  });
});

describe("sendRange", () => {
  it("reads like the owner writes dates", () => {
    expect(sendRange("2026-10-20T13:00:00Z", "2026-10-27T14:00:00Z")).toBe("20–27 oct");
    expect(sendRange("2026-09-17T13:00:00Z", "2026-09-17T15:00:00Z")).toBe("17 sep");
    expect(sendRange("2026-09-28T13:00:00Z", "2026-10-03T15:00:00Z")).toBe("28 sep – 3 oct");
    expect(sendRange(null, null)).toBe("—");
  });
});

describe("review fixes", () => {
  it("the bounce share is over the people reached, not over every send", async () => {
    stub();
    render(<MarketingPage />);
    expect(await screen.findByTestId("kpi-bounced")).toHaveTextContent("14 % de las alcanzadas");
  });

  it("an imported V1 plan shows as its row, not a second card, and keeps its days in the calendar", async () => {
    stub({ plans: [PLAN] });
    render(<MarketingPage />);
    await screen.findByTestId("campaign-row");
    expect(screen.queryByTestId("v1-lane-card")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Calendario" }));
    await waitFor(() => expect(document.body.textContent).toContain("Balanzas (ficticia)"));
  });

  it("a figure the CRM cannot measure opens «Aún no medido», not an empty list", async () => {
    stub();
    render(<MarketingPage />);
    const row = await screen.findByTestId("campaign-row");
    fireEvent.click(row);
    fireEvent.click(await screen.findByRole("button", { name: "Respuestas" }));
    expect(await screen.findByTestId("people-unmeasured")).toHaveTextContent("Aún no medido");
  });
});

describe("Bajas y rebotes", () => {
  it("shows the blocks at a glance: totals, origin donut, automatic kinds", async () => {
    stub();
    render(<MarketingPage />);
    await screen.findByTestId("analytics-kpis");
    fireEvent.click(screen.getByRole("button", { name: "Bajas y rebotes" }));
    const o = await screen.findByTestId("blocks-overview");
    await waitFor(() => expect(within(o).getByTestId("blocks-kpi-total")).toHaveTextContent("422"));
    expect(within(o).getByTestId("blocks-kpi-auto")).toHaveTextContent("120");
    expect(within(o).getByTestId("auto-kinds")).toHaveTextContent("Ya no está en la organización");
    expect(within(o).getByRole("img", { name: /Direcciones bloqueadas por origen/ })).toHaveAccessibleName(
      expect.stringContaining("Rebote revisado a mano 300"),
    );
  });
});
