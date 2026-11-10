import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState, HomeFilter } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CallDay, OpportunityCardData, RevisionCard } from "../crmTypes";
import type { Invoice } from "../pagos/invoiceApi";
import { clearResourceCache } from "../useResource";
import { GoalStrip, type StripFilter } from "./GoalStrip";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-08T15:00:00Z");
const DAY = 86_400_000;
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

function invoice(doc_type: number, issued_on: string, net_amount: number, exempt_amount = 0): Invoice {
  return {
    invoice_id: `i-${doc_type}-${issued_on}`, version: 1, doc_type, folio: 100, issued_on, days_open: 0, age_bucket: null,
    client_rut: "11.111.111-1", client_name: "Instituto Ficticio", organization: null,
    net_amount, vat_amount: 0, exempt_amount, total_amount: net_amount + exempt_amount,
    balance: null, reference: null, notes: [], status: "unpaid", paid_on: null, case_link: null, opportunity: null,
  };
}

const INVOICES = {
  items: [
    invoice(33, "2026-11-04", 1_050_000),
    invoice(33, "2026-10-31", 500_000),
  ],
  summary: { por_cobrar: 0, unpaid_count: 0, by_age: { "0-30": 0, "31-60": 0, "61-90": 0, "90+": 0 }, without_case: 0 },
};

function rev(sentAt: string | null): RevisionCard {
  return {
    revision_id: `r-${sentAt}`, revision_no: 1, status: sentAt ? "sent" : "draft", origin: "authored", sent_at: sentAt,
    superseded_by_revision_no: null, is_active: true, document: null, gmail: null, drive: null, quote_number: "01239-26",
  };
}

let n = 0;
function card(over: Partial<OpportunityCardData> = {}, revisions: RevisionCard[] = []): OpportunityCardData {
  n += 1;
  return {
    opportunity_id: `00000000-0000-4000-8000-0000000002${String(n).padStart(2, "0")}`,
    title: `Caso ${n}`, stage: "quoting", version: 1, created_at: null, updated_at: null, closed_at: null, close_reason: null,
    organization: { organization_id: `org-${n}`, name: `Instituto Ficticio ${n}`, confirmation: "confirmed", version: 1 },
    other_organizations: [], contact: null,
    quotes: revisions.length ? [{ quote_id: `q-${n}`, quote_number: "01239-26", number_origin: "allocated", revisions }] : [],
    quote_numbers: [], revision_count: revisions.length, latest_revision: revisions[revisions.length - 1] ?? null,
    drive_folder: null, attention: [], status: "ok", next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

// ITEMS: 1 followup (quote exactly 7 days old = «segundo» threshold), 1 cotizar (request 2 days old)
const ITEMS: OpportunityCardData[] = [
  card({}, [rev(ago(7 * DAY))]),           // followup at exactly day 7 (segundo threshold)
  card({ stage: "lead", created_at: ago(2 * DAY) }), // cotizar
];

const WEEK: CallDay[] = [
  { day: "2026-11-02", outcome: "contesto", count: 4 },
  { day: "2026-11-06", outcome: "no_contesto", count: 10 },
  { day: "2026-11-06", outcome: "reunion", count: 1 },
];

function session(role: string, homeFilter: HomeFilter | null = null): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@ejemplo.invalid", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
    ...(homeFilter ? { profile: { id: "p-ana", displayName: "Ana", roleLabel: "Ventas", homeFilter } } : {}),
  };
}

function stub(invoices: unknown = INVOICES): string[] {
  const paths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      paths.push(path);
      if (path === "/v2/workspace/invoices") {
        if (invoices instanceof Response) return Promise.resolve(invoices);
        return Promise.resolve(new Response(JSON.stringify(invoices), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return paths;
}

function renderStrip(
  s: AuthSessionState,
  calls: CallDay[] = WEEK,
  items = ITEMS,
  activeFilter: StripFilter = null,
  onFilterChange = vi.fn(),
) {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: s, signOut: async () => true }}>
      <GoalStrip items={items} calls={calls} navigate={navigate} activeFilter={activeFilter} onFilterChange={onFilterChange} />
    </AuthSessionContext.Provider>,
  );
  return { navigate, onFilterChange };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  n = 0;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearResourceCache();
  n = 0;
});

describe("GoalStrip v3", () => {
  it("renders the goal strip with 4 tiles", () => {
    stub();
    renderStrip(session("sales"));
    expect(screen.getByTestId("goal-strip")).toBeInTheDocument();
    expect(screen.getByTestId("goal-seguimiento")).toBeInTheDocument();
    expect(screen.getByTestId("goal-cotizar")).toBeInTheDocument();
    expect(screen.getByTestId("goal-alcance")).toBeInTheDocument();
  });

  it("Seguimientos count equals the queue followup rows (MUST match)", () => {
    stub();
    renderStrip(session("sales"));
    // ITEMS has 1 followup (8-day quote) — same as the queue would show.
    const tile = screen.getByTestId("goal-seguimiento");
    expect(tile).toHaveTextContent("1");
  });

  it("Cotizar shows count with oldest age", () => {
    stub();
    renderStrip(session("sales"));
    const tile = screen.getByTestId("goal-cotizar");
    expect(tile).toHaveTextContent("1");
    expect(tile).toHaveTextContent("2 días"); // 2-day-old request
  });

  it("Alcance link navigates to prospectos", () => {
    stub();
    const { navigate } = renderStrip(session("sales"));
    const tile = screen.getByTestId("goal-alcance");
    fireEvent.click(tile);
    expect(navigate).toHaveBeenCalledWith("prospectos");
  });

  it("admin sees Meta tile (loading then ready with invoice data)", async () => {
    stub();
    renderStrip(session("admin"));
    // After fetch resolves, the tile shows invoice data as a link.
    const tile = await screen.findByRole("link", { name: /Meta de/ });
    expect(tile).toBeInTheDocument();
    expect(tile).toHaveAttribute("data-testid", "goal-vendido");
  });

  it("viewer does not see Meta money tile (goal-vendido-hidden or similar)", () => {
    const paths = stub();
    renderStrip(session("viewer"));
    // Viewer gets a placeholder tile, not the full money tile
    expect(screen.queryByTestId("goal-vendido")).not.toBeInTheDocument();
    expect(paths).not.toContain("/v2/workspace/invoices");
  });

  it("Seguimientos button calls onFilterChange with 'followup'", () => {
    stub();
    const { onFilterChange } = renderStrip(session("sales"));
    fireEvent.click(screen.getByTestId("goal-seguimiento"));
    expect(onFilterChange).toHaveBeenCalledWith("followup");
  });

  it("Cotizar button calls onFilterChange with 'cotizar'", () => {
    stub();
    const { onFilterChange } = renderStrip(session("sales"));
    fireEvent.click(screen.getByTestId("goal-cotizar"));
    expect(onFilterChange).toHaveBeenCalledWith("cotizar");
  });

  it("active filter button calls onFilterChange with null (toggle off)", () => {
    stub();
    const { onFilterChange } = renderStrip(session("sales"), WEEK, ITEMS, "followup");
    // Already active → clicking should clear
    fireEvent.click(screen.getByTestId("goal-seguimiento"));
    expect(onFilterChange).toHaveBeenCalledWith(null);
  });

  it("active filter button shows bottom border indicator", () => {
    stub();
    renderStrip(session("sales"), WEEK, ITEMS, "followup");
    const tile = screen.getByTestId("goal-seguimiento");
    // The active tile has the inset shadow class
    expect(tile.className).toContain("shadow-");
  });

  it("empty items → 0 for Seguimientos and Cotizar", () => {
    stub();
    renderStrip(session("sales"), WEEK, []);
    expect(screen.getByTestId("goal-seguimiento")).toHaveTextContent("0");
    expect(screen.getByTestId("goal-cotizar")).toHaveTextContent("0");
  });

  it("an invoices error shows retry option for admin", async () => {
    stub(new Response("boom", { status: 500 }));
    renderStrip(session("admin"));
    expect(await screen.findByText(/Sin datos · reintentar/)).toBeInTheDocument();
  });
});
