/**
 * The V1-lane campaign card (plan per day, audience rule, email preview), the counts on its
 * calendar chips, and hiding historical campaigns that were never sent.
 *
 * Every campaign name, date, count and label is invented; the repository is public.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import type { CampaignSummary, MarketingResponse, V1LaneCampaign } from "../crmTypes";
import { MarketingPage } from "../pages/MarketingPage";
import { buildV1LaneEvents } from "./calendar";
import { isNeverSentHistorical } from "./campaignTotals";
import { V1LaneCampaignCard } from "./V1LaneCampaignCard";

const PLAN: V1LaneCampaign = {
  key: "promo-2026-03",
  name: "Promo Ejemplo",
  channel: "v1",
  send_days: ["2026-03-02", "2026-03-03", "2026-03-04"],
  send_time: "09:30",
  promo_until: "2026-03-06",
  clients_per_day: [1000, 1067, 528],
  total_clients: 2595,
  audience_rule: "Clientes que escribieron o compraron; sin proveedores.",
  html: '<p>Hola</p><img src="https://origenlab.cl/products/ika/t-25-digital.png">',
};

beforeEach(() => vi.useFakeTimers({ toFake: ["Date"] }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("V1-lane campaign card", () => {
  it("shows how many clients go out each day, the total, and each day's honest status", () => {
    vi.setSystemTime(new Date("2026-03-03T15:00:00Z")); // Tuesday in Santiago
    render(<V1LaneCampaignCard campaign={PLAN} />);
    const card = screen.getByTestId("v1-lane-card");
    expect(card).toHaveTextContent("Promo Ejemplo");
    expect(card).toHaveTextContent("En curso · canal V1");
    expect(screen.getByTestId("v1-lane-day-2026-03-02")).toHaveTextContent(/1\.000 clientes.*resultado en el registro V1/);
    expect(screen.getByTestId("v1-lane-day-2026-03-03")).toHaveTextContent(/1\.067 clientes.*En curso/);
    expect(screen.getByTestId("v1-lane-day-2026-03-04")).toHaveTextContent(/528 clientes.*Programada/);
    expect(screen.getByTestId("v1-lane-total")).toHaveTextContent("2.595 clientes");
    expect(card).toHaveTextContent("Clientes que escribieron o compraron; sin proveedores.");
  });

  it("says when the send is over and the results still have to be imported", () => {
    vi.setSystemTime(new Date("2026-03-09T15:00:00Z"));
    render(<V1LaneCampaignCard campaign={PLAN} />);
    expect(screen.getByTestId("v1-lane-card")).toHaveTextContent("Envío terminado · resultados por importar");
  });

  it("previews the real email in the sandboxed frame", () => {
    vi.setSystemTime(new Date("2026-03-01T15:00:00Z"));
    render(<V1LaneCampaignCard campaign={PLAN} />);
    expect(screen.getByTestId("v1-lane-card")).toHaveTextContent("Programada · canal V1");
    const frame = within(screen.getByTestId("v1-lane-preview")).getByTestId("email-frame");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("srcdoc")).toContain("Content-Security-Policy");
  });

  it("says the preview is not loaded when the email is not available here", () => {
    vi.setSystemTime(new Date("2026-03-01T15:00:00Z"));
    render(<V1LaneCampaignCard campaign={{ ...PLAN, html: null }} />);
    expect(screen.getByTestId("v1-lane-no-preview")).toBeInTheDocument();
  });

  it("puts each day's count on its calendar chip", () => {
    const events = buildV1LaneEvents([PLAN], "2026-03-01");
    expect(events.map((e) => e.detail)).toEqual([
      "Oleada 1 de 3 · 1.000 clientes · Programada",
      "Oleada 2 de 3 · 1.067 clientes · Programada",
      "Oleada 3 de 3 · 528 clientes · Programada",
    ]);
  });
});

// ──────────────────────────────────────────────────── never-sent historical campaigns

const base = {
  status: "archived", subject: null, preheader: null, has_html: false, version: 1, approved_at: null,
  created_at: "2026-03-01T00:00:00Z", updated_at: "2026-03-01T00:00:00Z", replies_recorded: 0,
};
const SENT_V1 = {
  ...base, campaign_id: "c0000000-0000-4000-8000-000000000011", name: "Ola enviada", origin: "imported_v1",
  first_sent_at: "2026-03-02T12:00:00Z", last_sent_at: "2026-03-02T12:00:00Z", recipients_by_state: { sent: 12 },
  send_attempts: [{ submission_state: "accepted", delivery_state: "pending", count: 12 }],
} as unknown as CampaignSummary;
const NEVER_SENT_V1 = {
  ...base, campaign_id: "c0000000-0000-4000-8000-000000000012", name: "Ola descartada", origin: "imported_v1",
  first_sent_at: null, last_sent_at: null, recipients_by_state: { excluded: 30 }, send_attempts: [],
} as unknown as CampaignSummary;

describe("never-sent historical campaigns", () => {
  it("are recognised only when imported from V1 with no attempt and no send", () => {
    expect(isNeverSentHistorical(NEVER_SENT_V1)).toBe(true);
    expect(isNeverSentHistorical(SENT_V1)).toBe(false);
    expect(isNeverSentHistorical({ ...NEVER_SENT_V1, origin: "native_v2" } as CampaignSummary)).toBe(false);
    expect(isNeverSentHistorical({ ...NEVER_SENT_V1, first_sent_at: "2026-03-02T12:00:00Z" } as CampaignSummary)).toBe(false);
  });

  it("are hidden from the list and its count, behind a link that shows them", async () => {
    const data: MarketingResponse = {
      campaigns: [SENT_V1, NEVER_SENT_V1], contact_controls: [], replies_note: "sin respuestas",
      storage: { table: "outbound.campaign", database: "origenlab_clean" }, authoring: { drafts_enabled: false },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = new URL(String(input instanceof Request ? input.url : input), "http://localhost").pathname;
        const body = path.endsWith("/v2/workspace/marketing") ? data : path.endsWith("/marketing/taxonomy") ? taxonomyJson : null;
        return Promise.resolve(new Response(JSON.stringify(body ?? { detail: "not found" }), { status: body ? 200 : 404 }));
      }),
    );
    render(<MarketingPage />);
    const cards = await screen.findAllByTestId("campaign-card");
    expect(cards.map((c) => c.textContent)).toEqual([expect.stringContaining("Ola enviada")]);
    fireEvent.click(screen.getByRole("button", { name: /Mostrar 1 campaña nunca enviada/ }));
    expect(screen.getAllByTestId("campaign-card")).toHaveLength(2);
  });
});
