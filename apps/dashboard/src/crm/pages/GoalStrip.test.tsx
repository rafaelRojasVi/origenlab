import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState, HomeFilter } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CallDay, OpportunityCardData, RevisionCard } from "../crmTypes";
import type { Invoice } from "../pagos/invoiceApi";
import { clearResourceCache } from "../useResource";
import { GoalStrip } from "./GoalStrip";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-08T15:00:00Z");
const DAY = 86_400_000;
const HOUR = 3_600_000;
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

function invoice(doc_type: number, issued_on: string, net_amount: number, exempt_amount = 0): Invoice {
  return {
    invoice_id: `i-${doc_type}-${issued_on}`, version: 1, doc_type, folio: 100, issued_on, days_open: 0, age_bucket: null,
    client_rut: "11.111.111-1", client_name: "Instituto Ficticio", organization: null, avg_days_client: 30, expected_pay_date: null,
    net_amount, vat_amount: 0, exempt_amount, total_amount: net_amount + exempt_amount,
    balance: null, reference: null, notes: [], status: "unpaid", paid_on: null, case_link: null, opportunity: null,
  };
}

const INVOICES = {
  items: [
    invoice(33, "2026-11-04", 1_050_000),
    invoice(34, "2026-11-05", 0, 200_000),
    invoice(61, "2026-11-06", 100_000),
    invoice(33, "2026-10-31", 500_000),
    invoice(56, "2026-11-07", 30_000),
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

/** A case asked `tookMs` before its first quote went out `sentAgo` ago (in «Conversación» — the
 *  client replied, so it is not a follow-up). */
const quoted = (sentAgo: number, tookMs: number) =>
  card({
    stage: "negotiating",
    requested_at: ago(sentAgo + tookMs),
    // A client reply after the quote puts the case in «Conversación»; `followUpsDue` skips it.
    last_contact: { inbound: { at: ago(sentAgo - 3_600_000), subject: "Re:", url: null }, outbound: null },
  }, [rev(ago(sentAgo))]);

const ITEMS: OpportunityCardData[] = [
  // Seguimiento: a quote that went out 8 days ago, nothing since.
  card({}, [rev(ago(8 * DAY))]),
  // Cotizar: a request two days old.
  card({ stage: "lead", created_at: ago(2 * DAY) }),
  // Solicitud → cotización: 5 h, 24 h, 36 h in the last 30 days → 1 día.
  quoted(1 * DAY, 5 * HOUR),
  quoted(3 * DAY, 24 * HOUR),
  quoted(10 * DAY, 36 * HOUR),
];

const WEEK: CallDay[] = [
  { day: "2026-11-02", outcome: "contesto", count: 4 },
  { day: "2026-11-06", outcome: "no_contesto", count: 10 },
  { day: "2026-11-06", outcome: "reunion", count: 1 },
  { day: "2026-10-01", outcome: "contesto", count: 9 },
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

function renderStrip(s: AuthSessionState, calls: CallDay[] = WEEK, items = ITEMS) {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: s, signOut: async () => true }}>
      <GoalStrip items={items} calls={calls} navigate={navigate} />
    </AuthSessionContext.Provider>,
  );
  return navigate;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("GoalStrip", () => {
  it("admin sees «Vendido en noviembre», the month's figure, the share of the target and the pace; «Ver facturas» opens Por cobrar", async () => {
    stub();
    const navigate = renderStrip(session("admin"));
    const tile = await screen.findByRole("link", { name: /Vendido en noviembre/ });
    // 33 + 34 + 56 − 61 issued in November (owner ruling 2026-11-08): 1.050.000 + 200.000 + 30.000 − 100.000.
    expect(tile).toHaveTextContent("$1,18 M");
    expect(tile).toHaveTextContent("19 % de la meta $6 M");
    expect(tile).toHaveTextContent("Faltan $4,82 M · $ 301.250 por día hábil (16 quedan, sin feriados)");
    expect(tile).toHaveTextContent("último folio del");
    expect(tile).toHaveTextContent("Ver facturas");
    expect(tile).toHaveAttribute("href", "#/finanzas/cobrar");
    fireEvent.click(tile);
    expect(navigate).toHaveBeenCalledWith("fin-cobrar");
  });

  it("viewer sees three tiles, no «$» and no invoices request", () => {
    const paths = stub();
    renderStrip(session("viewer"));
    const strip = screen.getByTestId("goal-strip");
    expect(strip.querySelectorAll("a")).toHaveLength(3);
    expect(strip).not.toHaveTextContent("$");
    expect(paths).not.toContain("/v2/workspace/invoices");
  });

  it("Seguimiento links to #/ventas/pipeline?vista=seguimiento with its count", () => {
    stub();
    renderStrip(session("sales"));
    const tile = screen.getByRole("link", { name: /Seguimiento/ });
    expect(tile).toHaveAttribute("href", "#/ventas/pipeline?vista=seguimiento");
    expect(tile).toHaveTextContent("1casos con seguimiento pendiente hoy");
    expect(tile).toHaveTextContent("Meta: 0");
    expect(tile).toHaveAttribute("data-tone", "warn");
  });

  it("Cotizar shows the count, «la más antigua: 2 días» and the median line", () => {
    stub();
    renderStrip(session("sales"));
    const tile = screen.getByRole("link", { name: /^Cotizar/ });
    expect(tile).toHaveAttribute("href", "#/ventas/pipeline?vista=cotizar");
    expect(tile).toHaveTextContent("1solicitudes sin cotizar · la más antigua: 2 días");
    expect(tile).toHaveTextContent("Solicitud → cotización: 1 día (30 días) · antes —");
    expect(tile).toHaveTextContent("Meta: mismo día hábil");
    // The median line is compared with no goal: never warn-coloured.
    const detail = screen.getByTestId("goal-cotizar-detail");
    expect(detail).toHaveClass("text-ink-muted");
    expect(detail).not.toHaveClass("text-warn");
  });

  it("Alcance reads «15 / 125 llamadas»", () => {
    vi.setSystemTime(new Date("2026-11-06T15:00:00Z"));
    stub();
    const navigate = renderStrip(session("sales"));
    const tile = screen.getByRole("link", { name: /Alcance · esta semana/ });
    expect(tile).toHaveTextContent("15 / 125 llamadas");
    expect(tile).toHaveTextContent("Hoy 11 / 25 · Conversaciones 30 días: 5 / 50");
    expect(tile).toHaveTextContent("Listas y llamadas");
    expect(tile).toHaveAttribute("href", "#/ventas/listas");
    fireEvent.click(tile);
    expect(navigate).toHaveBeenCalledWith("prospectos");
  });

  it("an invoices error keeps the other three tiles and offers Reintentar", async () => {
    stub(new Response("boom", { status: 500 }));
    renderStrip(session("admin"));
    expect(await screen.findByText("No se pudo leer lo facturado")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
    expect(screen.getByTestId("goal-strip").querySelectorAll("a")).toHaveLength(3);
  });

  it("an older API without calls shows 0", () => {
    stub();
    renderStrip(session("sales"), []);
    expect(screen.getByRole("link", { name: /Alcance/ })).toHaveTextContent("0 / 125 llamadas");
  });

  it("a llamadas profile still sees team-wide tiles", () => {
    stub();
    renderStrip(session("sales", "llamadas"));
    expect(screen.getByRole("link", { name: /^Cotizar/ })).toHaveTextContent("1solicitudes sin cotizar");
    expect(screen.getByRole("link", { name: /Seguimiento/ })).toHaveTextContent("1casos con seguimiento");
  });
});
