import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import { PipelinePage } from "./PipelinePage";

// Every value below is invented; the repository is public.
const DAY = 86_400_000;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

function rev(sentAt: string): RevisionCard {
  return {
    revision_id: `r-${sentAt}`, revision_no: 1, status: "sent", origin: "authored", sent_at: sentAt,
    superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: "00041-26",
  };
}

function card(id: string, org: string, over: Partial<OpportunityCardData> = {}, sentAgo: number | null = null): OpportunityCardData {
  const r = sentAgo !== null ? rev(ago(sentAgo)) : null;
  return {
    opportunity_id: id, title: `Caso ${org}`, stage: "quoting", version: 1,
    created_at: ago(30 * DAY), updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: `org-${id}`, name: org, confirmation: "confirmed", version: 1 },
    other_organizations: [], contact: null,
    quotes: r ? [{ quote_id: `q-${id}`, quote_number: "00041-26", number_origin: "minted", revisions: [r] }] : [],
    quote_numbers: r ? ["00041-26"] : [], revision_count: r ? 1 : 0, latest_revision: r,
    drive_folder: null, attention: [], status: "ok", next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

const OVERDUE = card("11111111-1111-4111-8111-111111111111", "Laboratorio Vencido", {}, 8 * DAY);
const RECENT = card("22222222-2222-4222-8222-222222222222", "Planta Reciente", {}, 2 * DAY);
const REQUEST = card("33333333-3333-4333-8333-333333333333", "Instituto Pedido", { stage: "lead", created_at: ago(DAY) });
const ITEMS = [OVERDUE, RECENT, REQUEST];

function session(): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@ejemplo.invalid", displayName: "Ventas", role: "sales" },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function renderAt(hash: string) {
  window.location.hash = hash;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    if (path === "/v2/workspace/pipeline") {
      return Promise.resolve(new Response(JSON.stringify({ items: ITEMS, total: ITEMS.length, drive_configured: true }),
        { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(new Response("{}", { status: 404 }));
  }));
  render(
    <AuthSessionContext.Provider value={{ session: session(), signOut: async () => true }}>
      <PipelinePage />
    </AuthSessionContext.Provider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
  window.location.hash = "";
});

describe("Pipeline ?vista=", () => {
  it("?vista=seguimiento shows only the overdue case and «Quitar filtro» clears it", async () => {
    renderAt("#/ventas/pipeline?vista=seguimiento");
    expect(await screen.findAllByText("Laboratorio Vencido")).not.toHaveLength(0);
    expect(screen.queryByText("Planta Reciente")).toBeNull();
    expect(screen.queryByText("Instituto Pedido")).toBeNull();
    expect(screen.getByTestId("pipeline-vista")).toHaveTextContent("Seguimiento vencido · 1");
    const clear = screen.getByRole("button", { name: "Quitar filtro" });
    expect(clear.className).toContain("min-h-11");
    await act(async () => {
      fireEvent.click(clear);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(window.location.hash).toBe("#/ventas/pipeline");
    expect(screen.queryByTestId("pipeline-vista")).toBeNull();
    expect(screen.getAllByText("Planta Reciente")).not.toHaveLength(0);
    expect(screen.getAllByText("Instituto Pedido")).not.toHaveLength(0);
  });

  it("?vista=cotizar shows only cases waiting for a quote", async () => {
    renderAt("#/ventas/pipeline?vista=cotizar");
    expect(await screen.findAllByText("Instituto Pedido")).not.toHaveLength(0);
    expect(screen.queryByText("Laboratorio Vencido")).toBeNull();
    expect(screen.queryByText("Planta Reciente")).toBeNull();
    expect(screen.getByTestId("pipeline-vista")).toHaveTextContent("Por cotizar · 1");
  });

  it("an unknown vista shows the whole board", async () => {
    renderAt("#/ventas/pipeline?vista=otra");
    expect(await screen.findAllByText("Laboratorio Vencido")).not.toHaveLength(0);
    expect(screen.getAllByText("Planta Reciente")).not.toHaveLength(0);
    expect(screen.getAllByText("Instituto Pedido")).not.toHaveLength(0);
    expect(screen.queryByTestId("pipeline-vista")).toBeNull();
  });
});
