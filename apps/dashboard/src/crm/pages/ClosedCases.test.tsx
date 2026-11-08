import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CaseMoney, OpportunityCardData } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import { ClosedCases, closedByMonth } from "./ClosedCases";
import { PipelinePage } from "./PipelinePage";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-10T15:00:00Z");

let n = 0;
function closed(
  stage: OpportunityCardData["stage"],
  closedAt: string | null,
  reason: string | null,
  money: CaseMoney[] = [],
  over: Partial<OpportunityCardData> = {},
): OpportunityCardData {
  n += 1;
  const id = `9${String(n).padStart(7, "0")}-0000-4000-8000-000000000000`;
  return {
    opportunity_id: id,
    title: `Caso ficticio ${n}`,
    stage,
    version: 1,
    created_at: "2026-08-01T12:00:00Z",
    updated_at: closedAt,
    closed_at: closedAt,
    close_reason: reason,
    organization: { organization_id: `org-${n}`, name: `Instituto Ficticio ${n}`, confirmation: "confirmed", version: 1 },
    other_organizations: [],
    contact: null,
    quotes: money.length
      ? [{ quote_id: `q-${n}`, quote_number: money[0].quote_number ?? "00000-26", number_origin: "minted", revisions: [] }]
      : [],
    quote_numbers: [],
    revision_count: 0,
    money,
    latest_revision: null,
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "", source: "suggested", due_at: null },
    ...over,
  };
}

const clp = (net: string, cn: string): CaseMoney => ({ currency: "CLP", net_total: net, quote_number: cn, revision_no: 1 });
const unread = (cn: string): CaseMoney => ({ currency: "unread", net_total: "", quote_number: cn, revision_no: 1 });

function fixtures() {
  // 23:30 on 31 October in Santiago (UTC-3) is 02:30 UTC on 1 November: an October loss.
  const lateOct = closed("lost", "2026-11-01T02:30:00Z", "Precio", [clp("1200000", "00101-26")]);
  const midOct = closed("abandoned", "2026-10-14T15:00:00Z", null, [unread("00102-26")]);
  const earlyOct = closed("lost", "2026-10-02T15:00:00Z", "Precio", [clp("800000", "00103-26")]);
  const sep = closed("lost", "2026-09-15T15:00:00Z", "Plazo de entrega", [clp("500000", "00104-26")]);
  const recent = closed("lost", "2026-11-07T15:00:00Z", "Precio", [clp("999000", "00105-26")]); // 3 days: still on the board
  const won = closed("won", "2026-09-01T15:00:00Z", null, [clp("4000000", "00106-26")]);
  return { lateOct, midOct, earlyOct, sep, recent, won };
}

describe("closedByMonth", () => {
  it("groups losses by Santiago month, newest first", () => {
    const f = fixtures();
    const months = closedByMonth([f.sep, f.earlyOct, f.lateOct, f.midOct], NOW);
    expect(months.map((m) => [m.month, m.label])).toEqual([
      ["2026-10", "octubre 2026"],
      ["2026-09", "septiembre 2026"],
    ]);
    // Newest closing first inside the month.
    expect(months[0].cards.map((c) => c.opportunity_id)).toEqual([f.lateOct, f.midOct, f.earlyOct].map((c) => c.opportunity_id));
  });

  it("totals the neto in CLP and counts unread", () => {
    const f = fixtures();
    const [oct, sep] = closedByMonth([f.sep, f.earlyOct, f.lateOct, f.midOct], NOW);
    expect(oct).toMatchObject({ count: 3, clp: 2_000_000, unread: 1 });
    expect(sep).toMatchObject({ count: 1, clp: 500_000, unread: 0 });
  });

  it("counts reasons, «Sin motivo» for null", () => {
    const f = fixtures();
    const [oct] = closedByMonth([f.earlyOct, f.lateOct, f.midOct], NOW);
    expect(oct.reasons).toEqual([
      { reason: "Precio", count: 2 },
      { reason: "Sin motivo", count: 1 },
    ]);
  });

  it("a loss from 3 days ago is not here", () => {
    const f = fixtures();
    expect(closedByMonth([f.recent], NOW)).toEqual([]);
  });

  it("won cases never appear here", () => {
    const f = fixtures();
    expect(closedByMonth([f.won], NOW)).toEqual([]);
  });
});

describe("ClosedCases", () => {
  it("shows each month's count, lost neto and reason chips, and a row per case", () => {
    const f = fixtures();
    render(<ClosedCases cards={[f.sep, f.earlyOct, f.lateOct, f.midOct, f.won, f.recent]} onOpen={() => undefined} now={NOW} />);
    const oct = screen.getByTestId("closed-month-2026-10");
    expect(within(oct).getByRole("heading", { name: "octubre 2026" })).toBeInTheDocument();
    expect(within(oct).getByTestId("closed-month-summary")).toHaveTextContent("3 casos");
    expect(within(oct).getByTestId("closed-month-summary")).toHaveTextContent("$ 2.000.000");
    expect(within(oct).getByTestId("closed-month-summary")).toHaveTextContent("+ 1 sin leer");
    expect(within(oct).getAllByTestId("closed-reason").map((e) => e.textContent)).toEqual(["Precio · 2", "Sin motivo · 1"]);
    const rows = within(oct).getAllByTestId(/^closed-row-/);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Instituto Ficticio");
    expect(rows[0]).toHaveTextContent("00101-26");
    expect(rows[0]).toHaveTextContent("$ 1.200.000");
    expect(rows[0]).toHaveTextContent("Precio");
    expect(rows[1]).toHaveTextContent("sin leer");
    expect(rows[1]).toHaveTextContent("Sin motivo");
    expect(screen.getByTestId("closed-month-2026-09")).toBeInTheDocument();
    expect(screen.queryByTestId(`closed-row-${f.won.opportunity_id}`)).toBeNull();
    expect(screen.queryByTestId(`closed-row-${f.recent.opportunity_id}`)).toBeNull();
  });

  it("row click opens the drawer", () => {
    const f = fixtures();
    const onOpen = vi.fn();
    render(<ClosedCases cards={[f.earlyOct]} onOpen={onOpen} now={NOW} />);
    fireEvent.click(screen.getByTestId(`closed-row-${f.earlyOct.opportunity_id}`));
    expect(onOpen).toHaveBeenCalledWith(f.earlyOct.opportunity_id);
  });

  it("empty → «Sin casos cerrados todavía»", () => {
    const f = fixtures();
    render(<ClosedCases cards={[f.won, f.recent]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByText("Sin casos cerrados todavía")).toBeInTheDocument();
    expect(screen.queryByTestId(/^closed-month-/)).toBeNull();
  });

  it("each month's table is named by its month", () => {
    const f = fixtures();
    render(<ClosedCases cards={[f.sep, f.earlyOct]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByRole("table", { name: "octubre 2026" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "septiembre 2026" })).toBeInTheDocument();
  });

  it("notes «Las ganadas siguen en el tablero.»", () => {
    render(<ClosedCases cards={[]} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId("closed-note")).toHaveTextContent("Las ganadas siguen en el tablero.");
  });
});

describe("Pipeline «Cerradas»", () => {
  function session(): AuthSessionState {
    return {
      kind: "signed_in",
      method: "google_profile",
      operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Ventas", role: "sales" },
      caseCommandsEnabled: true,
      crmAuthoringEnabled: true,
    };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    clearResourceCache();
  });

  it("the tab lists old losses by month, notes the ganadas stay on the board, and a row opens the drawer", async () => {
    const old = closed("lost", "2026-09-15T15:00:00Z", "Precio", [clp("500000", "00104-26")]);
    const items = [old, closed("quoting", null, null)];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const path = new URL(url, "http://localhost").pathname;
        if (path === "/v2/workspace/pipeline") {
          return Promise.resolve(
            new Response(JSON.stringify({ items, total: items.length, drive_configured: true }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        return Promise.resolve(new Response("{}", { status: 404 }));
      }),
    );
    render(
      <AuthSessionContext.Provider value={{ session: session(), signOut: async () => true }}>
        <PipelinePage />
      </AuthSessionContext.Provider>,
    );
    const vista = await screen.findByRole("group", { name: "Vista" });
    fireEvent.click(within(vista).getByRole("button", { name: "Cerradas" }));
    expect(screen.getByTestId("closed-note")).toHaveTextContent("Las ganadas siguen en el tablero.");
    expect(screen.getByTestId("closed-month-2026-09")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`closed-row-${old.opportunity_id}`));
    expect(await screen.findByRole("dialog")).toHaveTextContent(old.organization?.name ?? "");
  });

  it("when the search leaves nothing, the tab still notes what Cerradas holds", async () => {
    const old = closed("lost", "2026-09-15T15:00:00Z", "Precio", [clp("500000", "00104-26")]);
    const items = [old];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ items, total: items.length, drive_configured: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    render(
      <AuthSessionContext.Provider value={{ session: session(), signOut: async () => true }}>
        <PipelinePage />
      </AuthSessionContext.Provider>,
    );
    const vista = await screen.findByRole("group", { name: "Vista" });
    fireEvent.click(within(vista).getByRole("button", { name: "Cerradas" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar oportunidades" }), { target: { value: "zzz-ninguna" } });
    expect(await screen.findByText("Ninguna oportunidad coincide")).toBeInTheDocument();
    expect(screen.getByTestId("closed-note")).toHaveTextContent("Las ganadas siguen en el tablero.");
  });

  it("on the board, an empty search shows no Cerradas note", async () => {
    const items = [closed("quoting", null, null)];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ items, total: items.length, drive_configured: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    render(
      <AuthSessionContext.Provider value={{ session: session(), signOut: async () => true }}>
        <PipelinePage />
      </AuthSessionContext.Provider>,
    );
    await screen.findByRole("group", { name: "Vista" });
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar oportunidades" }), { target: { value: "zzz-ninguna" } });
    expect(await screen.findByText("Ninguna oportunidad coincide")).toBeInTheDocument();
    expect(screen.queryByTestId("closed-note")).toBeNull();
  });
});
