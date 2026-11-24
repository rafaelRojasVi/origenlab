import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AuthSessionState, ProfileCard } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData } from "../crmTypes";
import { seguimientoRows } from "../seguimiento";
import { sortRows, type SegOrder } from "../seguimientoView";
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

function renderSheet(cards: OpportunityCardData[], order: SegOrder = "prioridad", onOrder = vi.fn(), onOpen = vi.fn()) {
  const rows = sortRows(seguimientoRows(cards, PROFILES, NOW), order);
  render(
    <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
      <SeguimientoSheet rows={rows} profiles={PROFILES} now={NOW} selectedId={null} order={order} onOrder={onOrder} onOpen={onOpen} onSelect={vi.fn()} onGuion={vi.fn()} onCallSaved={vi.fn()} onReceipt={vi.fn()} onConflict={vi.fn()} />
    </AuthSessionContext.Provider>,
  );
  return { onOrder, onOpen };
}

const order = () => screen.getAllByTestId(/^seg-row-/).map((r) => r.getAttribute("data-testid"));

describe("SeguimientoSheet", () => {
  const cards = [
    card("1001", "500000", "2026-11-17T15:00:00Z"),
    card("1002", "9000000", "2026-11-13T15:00:00Z"),
    card("1003", "2000000", "2026-11-01T15:00:00Z"),
  ];

  it("asks for the Monto and Sin respuesta orders, shows the rows as ordered, and totals them", () => {
    const { onOrder } = renderSheet(cards, "monto");
    expect(order()).toEqual(["seg-row-1002", "seg-row-1003", "seg-row-1001"]);
    expect(screen.getByRole("columnheader", { name: /Monto/ })).toHaveAttribute("aria-sort", "descending");
    // A second click on the active order goes back to «Prioridad».
    fireEvent.click(screen.getByRole("button", { name: /Monto/ }));
    expect(onOrder).toHaveBeenLastCalledWith("prioridad");
    fireEvent.click(screen.getByRole("button", { name: /Sin respuesta/ }));
    expect(onOrder).toHaveBeenLastCalledWith("dias");
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

  // Owner 2026-11-24: the pill said «56 d sin respuesta» counting from the quote while the
  // client had answered last week. It reads the conversation since the quote, as the Pipeline does.
  it("the days pill counts from the last touch, and says so when the client answered last", () => {
    const quoteSent = "2026-10-01T15:00:00Z";
    renderSheet([
      card("1006", "1", quoteSent, { last_contact: {
        outbound: { at: "2026-11-16T14:58:00Z", subject: "Re: Cotización", url: null, sender_name: "OrigenLab" },
        inbound: { at: "2026-11-17T12:06:00Z", subject: "Re: Cotización", url: null, sender_name: "Persona Ficticia" },
      } } as Partial<OpportunityCardData>),
      card("1007", "1", quoteSent, { last_contact: {
        outbound: { at: "2026-11-13T15:00:00Z", subject: "Re: Cotización", url: null, sender_name: "OrigenLab" },
        inbound: { at: "2026-10-02T15:00:00Z", subject: "Re: Cotización", url: null, sender_name: "Persona Ficticia" },
      } } as Partial<OpportunityCardData>),
    ]);
    const pill = (id: string) => within(screen.getByTestId(`seg-row-${id}`)).getByTestId("seg-days");
    expect(pill("1006")).toHaveTextContent("Respondió 17 nov");
    expect(pill("1006")).not.toHaveTextContent(/\d+ d/);
    expect(pill("1006").getAttribute("data-tone")).toBe("ok");
    // Our follow-up of 13 Nov is the clock, not the quote of 1 Oct: 5 days, a warning.
    expect(pill("1007")).toHaveTextContent("5 d");
    expect(pill("1007").getAttribute("data-tone")).toBe("warn");
  });

  // Production 2026-11-24: «estoy de viaje, retomo la próxima semana» needs no reply (`no_reply`), so the
  // conversation is the client's, not silent — the pill says when they wrote and still counts, warning after a week.
  it("a client message that needs no reply reads «Escribió …», toned by its age", () => {
    renderSheet([card("1012", "1", "2026-10-01T15:00:00Z", { last_contact: {
      outbound: { at: "2026-11-09T14:58:00Z", subject: "Re: Cotización", url: null, sender_name: "OrigenLab" },
      inbound: { at: "2026-11-10T12:06:00Z", subject: "Re: Cotización", url: null, sender_name: "Persona Ficticia", no_reply: true },
    } } as Partial<OpportunityCardData>)]);
    const pill = within(screen.getByTestId("seg-row-1012")).getByTestId("seg-days");
    expect(pill).toHaveTextContent("Escribió 10 nov");
    expect(pill.getAttribute("data-tone")).toBe("bad");
  });

  it("an email touch is not called «automático»: a person sent it", () => {
    renderSheet([card("1008", "1", "2026-11-10T15:00:00Z", { last_contact: {
      outbound: { at: "2026-11-16T14:58:00Z", subject: "Re: Cotización", url: null, sender_name: "OrigenLab" }, inbound: null,
    } } as Partial<OpportunityCardData>)]);
    expect(within(screen.getByTestId("seg-row-1008")).queryByText(/automático/)).toBeNull();
  });

  it("orders «Sin respuesta» by days since the last touch, the cases the client answered last at the end", () => {
    const quoteSent = "2026-10-01T15:00:00Z";
    const rows = sortRows(seguimientoRows([
      card("1009", "1", quoteSent, { last_contact: { outbound: null, inbound: { at: "2026-11-17T12:06:00Z", subject: "", url: null, sender_name: "" } } } as Partial<OpportunityCardData>),
      card("1010", "1", "2026-11-14T15:00:00Z"),
      card("1011", "1", quoteSent, { last_contact: { outbound: { at: "2026-11-16T15:00:00Z", subject: "", url: null, sender_name: "" }, inbound: null } } as Partial<OpportunityCardData>),
    ], PROFILES, NOW), "dias");
    expect(rows.map((r) => r.card.opportunity_id)).toEqual(["1010", "1011", "1009"]);
  });

  it("shows a masked phone as plain text, not a tel: link", () => {
    renderSheet([card("1005", "1", "2026-11-01T15:00:00Z", { contact: { source: "crm_participant", name: "Persona", address: null, others: 0, phone: "***", phone_confirmed: null } as OpportunityCardData["contact"] })]);
    const r = within(screen.getByTestId("seg-row-1005"));
    expect(r.getByTestId("phone-masked")).toHaveTextContent("***");
    expect(r.queryByRole("link", { name: /\*\*\*/ })).toBeNull();
  });

  it("the case name opens the case", () => {
    const { onOpen } = renderSheet(cards);
    fireEvent.click(screen.getByRole("button", { name: "Cliente 1002" }));
    expect(onOpen).toHaveBeenCalledWith("1002");
  });
});
