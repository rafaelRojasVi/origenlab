import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AuthSessionState, ProfileCard } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData } from "../crmTypes";
import { seguimientoRows } from "../seguimiento";
import { SeguimientoSheet } from "./SeguimientoSheet";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-18T15:00:00Z");
const PROFILES: ProfileCard[] = [{ id: "op-rafa", displayName: "Rafa", roleLabel: "", key: "rafael" }];
const session: AuthSessionState = {
  kind: "signed_in",
  method: "google_profile",
  operator: { operatorId: "op-rafa", email: "ventas@example.cl", displayName: "Rafa", role: "sales" },
  caseCommandsEnabled: true,
  crmAuthoringEnabled: true,
};

function card(id: string, amount: string, sentAt: string, over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const rev = { revision_id: `r-${id}`, revision_no: 1, status: "sent", origin: null, sent_at: sentAt, superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: `CN0${id}`, candidates: [] };
  return {
    opportunity_id: id, title: `Cotización CN0${id}`, stage: "quoting", version: 1, created_at: sentAt, updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: `org-${id}`, name: `Cliente ${id}`, confirmation: "confirmed", version: 1 }, other_organizations: [],
    contact: null, quotes: [{ quote_id: `q-${id}`, quote_number: `CN0${id}`, revisions: [rev] }], quote_numbers: [`CN0${id}`], revision_count: 1,
    money: [{ currency: "CLP", net_total: amount }], latest_revision: rev, drive_folder: null, attention: [], status: "ok",
    next_action: { text: "", source: "suggested", due_at: null }, follow_up: { operator_id: "op-rafa", operator_name: "Rafa", channel: null },
    call_counts: { total: 0, today: 0 }, last_call_any: null, open_tasks: [], ...over,
  } as OpportunityCardData;
}

function renderSheet(cards: OpportunityCardData[]) {
  const rows = seguimientoRows(cards, PROFILES, NOW);
  render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <SeguimientoSheet rows={rows} profiles={PROFILES} now={NOW} selectedId={null} onSelect={vi.fn()} onGuion={vi.fn()} onCallSaved={vi.fn()} onReceipt={vi.fn()} onConflict={vi.fn()} />
    </AuthSessionContext.Provider>,
  );
}

const order = () => screen.getAllByTestId(/^seg-row-/).map((r) => r.getAttribute("data-testid"));

describe("SeguimientoSheet", () => {
  const cards = [
    card("1001", "500000", "2026-11-17T15:00:00Z"),
    card("1002", "9000000", "2026-11-13T15:00:00Z"),
    card("1003", "2000000", "2026-11-01T15:00:00Z"),
  ];

  it("sorts by Monto and by Sin respuesta, and totals the rows", () => {
    renderSheet(cards);
    fireEvent.click(screen.getByRole("button", { name: /Monto/ }));
    expect(order()).toEqual(["seg-row-1002", "seg-row-1003", "seg-row-1001"]);
    fireEvent.click(screen.getByRole("button", { name: /Monto/ }));
    expect(order()).toEqual(["seg-row-1001", "seg-row-1003", "seg-row-1002"]);
    fireEvent.click(screen.getByRole("button", { name: /Sin respuesta/ }));
    expect(order()).toEqual(["seg-row-1003", "seg-row-1002", "seg-row-1001"]);
    const foot = within(screen.getByTestId("seg-total"));
    expect(foot.getByText("3 casos")).toBeInTheDocument();
    expect(screen.getByTestId("seg-total-money")).toHaveTextContent("11.500.000");
  });

  it("tones the days pill, ok once touched today", () => {
    renderSheet([
      ...cards,
      card("1004", "1", "2026-11-01T15:00:00Z", { call_counts: { total: 1, today: 1 }, last_call_any: { at: "2026-11-18T14:00:00Z", outcome: "no_contesto", author_name: "Rafa" } }),
    ]);
    const tone = (id: string) => within(screen.getByTestId(`seg-row-${id}`)).getByTestId("seg-days").getAttribute("data-tone");
    expect(tone("1001")).toBe("ok");
    expect(tone("1002")).toBe("warn");
    expect(tone("1003")).toBe("bad");
    expect(tone("1004")).toBe("ok");
  });
});
