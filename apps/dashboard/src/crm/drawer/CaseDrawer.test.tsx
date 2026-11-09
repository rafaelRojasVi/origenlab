import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { OpportunityCardData, QuoteLine, RevisionCard, TimelineEntry } from "../crmTypes";
import { clearResourceCache } from "../useResource";
import { CaseDrawer } from "./CaseDrawer";

// Every value below is invented; the repository is public.
const CASE = "11111111-1111-4111-8111-111111111111";
const ORG = "33333333-3333-4333-8333-333333333333";
const QUOTE = "44444444-4444-4444-8444-444444444444";
const PERSON = "77777777-7777-4777-8777-777777777777";

function line(over: Partial<QuoteLine>): QuoteLine {
  return {
    line_no: 1, item: null, parent_item: null, kind: null, brand: null, model: null, description: null,
    qty: "1", unit_price: "100000", line_total: "100000", optional: false, check_status: "ok", ...over,
  };
}

function rev(no: number, lines: QuoteLine[], net: string, over: Partial<RevisionCard> = {}): RevisionCard {
  return {
    revision_id: `rev-${no}`,
    revision_no: no,
    status: "sent",
    origin: "cotizador",
    sent_at: `2026-10-2${no + 6}T12:00:00Z`,
    superseded_by_revision_no: null,
    is_active: true,
    document: null,
    gmail: null,
    drive: null,
    quote_number: "01318-26",
    contents: { currency: "CLP", net_total: net, net_source: "document", tax_total: null, gross_total: null, lines },
    ...over,
  };
}

const equipo = line({ model: "UP200St", unit_price: "10980000", line_total: "10980000" });
const soporte = line({ line_no: 2, description: "Soporte ST1-16 con brida", unit_price: "600000", line_total: "600000" });

function card(over: Partial<OpportunityCardData> = {}): OpportunityCardData {
  const revisions = [
    rev(1, [equipo], "10980000", { superseded_by_revision_no: 2, is_active: false }),
    rev(2, [equipo, soporte], "11580000"),
  ];
  return {
    opportunity_id: CASE,
    title: "Cotización 01318-26 — Instituto Ficticio",
    stage: "quoting",
    version: 5,
    created_at: "2026-10-24T12:00:00Z",
    updated_at: "2026-10-29T12:00:00Z",
    closed_at: null,
    close_reason: null,
    origin: "campana",
    organization: { organization_id: ORG, name: "Instituto Ficticio", confirmation: "confirmed", version: 3 },
    other_organizations: [],
    contact: { source: "crm_participant", name: "Persona Ficticia", address: "persona@ficticio.invalid", others: 0, person_id: PERSON },
    quotes: [{ quote_id: QUOTE, quote_number: "01318-26", number_origin: "cotizador", revisions }],
    quote_numbers: ["01318-26"],
    revision_count: 2,
    money: [{ currency: "CLP", net_total: "11580000", quote_number: "01318-26", revision_no: 2 }],
    latest_revision: revisions[1],
    drive_folder: null,
    attention: [],
    status: "ok",
    next_action: { text: "Hacer seguimiento de 01318-26", source: "suggested", due_at: null },
    ...over,
  };
}

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@ficticio.invalid", displayName: "Vendedora Ficticia", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

const TIMELINE: TimelineEntry[] = [
  {
    entry_kind: "domain_event", occurred_at: "2026-10-24T12:00:00Z", event_type: "opportunity.created", aggregate_kind: "opportunity",
    aggregate_id: CASE, actor_operator_id: null, actor_display_name: null, payload: null, quote_id: null, quote_number: null,
    revision_no: null, pdf_sha256: null,
  },
  {
    entry_kind: "quotation_sent", occurred_at: "2026-10-29T12:00:00Z", event_type: null, aggregate_kind: null, aggregate_id: null,
    actor_operator_id: null, actor_display_name: null, payload: null, quote_id: QUOTE, quote_number: "01318-26", revision_no: 2,
    pdf_sha256: null,
  },
];

const PERSON_BODY = {
  person: { id: PERSON, display_name: "Persona Ficticia", status: "active", version: 1 },
  contact_points: [
    { id: "cp1", kind: "phone", value_display: "+56 9 1234 5678", value_norm: "+56912345678", usage: null, status: "active", version: 1, note: null, deactivated_at: null, created_at: "2026-10-01T00:00:00Z" },
    { id: "cp2", kind: "email", value_display: "persona@ficticio.invalid", value_norm: "persona@ficticio.invalid", usage: null, status: "active", version: 1, note: null, deactivated_at: null, created_at: "2026-10-01T00:00:00Z" },
  ],
  affiliations: [],
  notes: [],
  references: {},
  removal: { allowed: false, reasons: [] },
  authoring: { enabled: true, may_author: true, may_archive: false },
};

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

/** Serves the drawer's reads; `timeline` is a list of answers served in order (the last repeats). */
function stub({ timeline = [{ status: 200, body: { opportunity_id: CASE, entries: TIMELINE } }] }: { timeline?: { status: number; body: unknown }[] } = {}) {
  const paths: string[] = [];
  let timelineCalls = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    paths.push(path);
    if (path.endsWith("/timeline")) {
      const answer = timeline[Math.min(timelineCalls, timeline.length - 1)];
      timelineCalls += 1;
      return json(answer.body, answer.status);
    }
    if (path.endsWith("/notes")) return json({ opportunity_id: CASE, notes: [] });
    if (path.endsWith("/threads")) return json({ opportunity_id: CASE, threads: [] });
    if (path === `/v2/workspace/people/${PERSON}`) return json(PERSON_BODY);
    return Promise.resolve(new Response("{}", { status: 404 }));
  }));
  return paths;
}

function renderDrawer(role: string, c: OpportunityCardData = card()) {
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <CaseDrawer card={c} onClose={() => undefined} onChanged={() => undefined} />
    </AuthSessionContext.Provider>,
  );
  return screen.getByRole("dialog");
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("CaseDrawer", () => {
  it("shows five tabs and opens on Resumen", async () => {
    stub();
    const dialog = renderDrawer("sales");
    const tabs = within(within(dialog).getByRole("tablist")).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Resumen", "Cotización", "Contacto", "Notas y llamadas", "Correos"]);
    expect(within(dialog).getByRole("tab", { name: "Resumen" })).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).getByRole("tabpanel", { name: "Resumen" })).toBeVisible();
    // The header carries what the case is and where it stands.
    expect(within(dialog).getByRole("heading", { name: "Instituto Ficticio" })).toBeInTheDocument();
    expect(within(dialog).getByTestId("case-drawer-net")).toHaveTextContent("$ 11.580.000");
    expect(within(dialog).getByTestId("case-origin-badge")).toHaveTextContent("Origen: Campaña");
    // The timeline, newest first.
    const timeline = await within(dialog).findByTestId("case-timeline");
    expect(within(timeline).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringContaining("01318-26 rev. 2 enviada"),
      expect.stringContaining("Caso creado"),
    ]);
  });

  it("moves between tabs with ← and →", () => {
    stub();
    const dialog = renderDrawer("sales");
    const resumen = within(dialog).getByRole("tab", { name: "Resumen" });
    fireEvent.keyDown(resumen, { key: "ArrowRight" });
    const cotizacion = within(dialog).getByRole("tab", { name: "Cotización" });
    expect(cotizacion).toHaveAttribute("aria-selected", "true");
    expect(cotizacion).toHaveFocus();
    fireEvent.keyDown(cotizacion, { key: "ArrowLeft" });
    fireEvent.keyDown(within(dialog).getByRole("tab", { name: "Resumen" }), { key: "ArrowLeft" });
    expect(within(dialog).getByRole("tab", { name: "Correos" })).toHaveAttribute("aria-selected", "true");
  });

  it("Cotización lists revisions newest first with «qué cambió»", () => {
    stub();
    const dialog = renderDrawer("sales");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Cotización" }));
    const history = within(dialog).getByRole("region", { name: "Historial de esta cotización" });
    const items = within(history).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("rev 2");
    expect(items[0]).toHaveTextContent("Neto $ 10.980.000 → $ 11.580.000");
    expect(items[0]).toHaveTextContent("+ Soporte ST1-16 con brida");
    expect(items[1]).toHaveTextContent("rev 1");
    expect(items[1]).toHaveTextContent("Primera versión");
  });

  it("Registrar llamada opens the call form", async () => {
    stub();
    const dialog = renderDrawer("sales");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Correos" }));
    fireEvent.click(within(within(dialog).getByTestId("case-drawer-actions")).getByRole("button", { name: "Registrar llamada" }));
    expect(within(dialog).getByRole("tab", { name: "Resumen" })).toHaveAttribute("aria-selected", "true");
    expect(await within(dialog).findByTestId("log-call")).toBeVisible();
  });

  it("Marcar perdida opens the lost reasons", () => {
    stub();
    const dialog = renderDrawer("sales");
    const footer = within(dialog).getByTestId("case-drawer-footer");
    expect(within(footer).getByRole("button", { name: "Marcar ganada" })).toBeInTheDocument();
    fireEvent.click(within(footer).getByRole("button", { name: "Marcar perdida" }));
    const form = within(dialog).getByRole("form", { name: "Mover a «Perdida»" });
    expect(within(form).getByRole("button", { name: "Sin respuesta" })).toBeInTheDocument();
  });

  it("sales sees the contact's phones as call and WhatsApp links", async () => {
    stub();
    const dialog = renderDrawer("sales");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Contacto" }));
    const phones = await within(dialog).findByTestId("person-phones");
    expect(within(phones).getByRole("link", { name: /Llamar al \+56 9 1234 5678/ })).toHaveAttribute("href", "tel:+56912345678");
    expect(within(phones).getByRole("link", { name: /WhatsApp al \+56 9 1234 5678/ })).toHaveAttribute("href", "https://wa.me/56912345678");
  });

  it("viewer sees no footer buttons and no phones", async () => {
    const paths = stub();
    const dialog = renderDrawer("viewer");
    expect(within(dialog).queryByTestId("case-drawer-footer")).toBeNull();
    expect(within(within(dialog).getByTestId("case-drawer-actions")).queryByRole("button")).toBeNull();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Contacto" }));
    expect(within(dialog).queryByTestId("person-phones")).toBeNull();
    expect(within(dialog).queryByRole("link", { name: /WhatsApp/ })).toBeNull();
    await waitFor(() => expect(paths.some((p) => p.endsWith("/timeline"))).toBe(true));
    expect(paths).not.toContain(`/v2/workspace/people/${PERSON}`);
  });

  it("a closed case has no «Marcar ganada» or «Marcar perdida»", () => {
    stub();
    const dialog = renderDrawer("sales", card({ stage: "lost", closed_at: "2026-11-01T12:00:00Z", close_reason: "Sin respuesta" }));
    expect(within(dialog).queryByTestId("case-drawer-footer")).toBeNull();
  });

  it("a case with a future «Seguimiento» reminder shows the chip with that date", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-08T15:00:00Z"));
    try {
      stub();
      const task = { task_id: "t1", title: "Seguimiento 01318-26", due_at: "2026-11-11T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer("sales", card({ open_tasks: [task] }));
      // Rev 2 went out 28 Oct: on 11 Nov (14 days) the rhythm is on its third follow-up (3·7·10·15 → step 3).
      expect(within(dialog).getByTestId("case-drawer-followup")).toHaveTextContent("Seguimiento 3 de 4 · miércoles 11");
    } finally {
      vi.useRealTimers();
    }
  });

  describe("follow-up chip · Task 7 review fixes", () => {
    const at = (iso: string) => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date(iso));
    };
    afterEach(() => {
      vi.useRealTimers();
    });

    it("a reminder due today reads «hoy», not its weekday", () => {
      at("2026-11-08T15:00:00Z");
      stub();
      const task = { task_id: "t1", title: "Seguimiento 01318-26", due_at: "2026-11-08T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer("sales", card({ open_tasks: [task] }));
      expect(within(dialog).getByTestId("case-drawer-followup")).toHaveTextContent("Seguimiento 3 de 4 · hoy"); // 11 days since Oct 28 → step 3
    });

    it("an overdue reminder reads «hoy»", () => {
      at("2026-11-08T15:00:00Z");
      stub();
      const task = { task_id: "t1", title: "Seguimiento 01318-26", due_at: "2026-11-05T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer("sales", card({ open_tasks: [task] }));
      expect(within(dialog).getByTestId("case-drawer-followup")).toHaveTextContent(/· hoy$/);
    });

    it("a reminder our later email already met does not drive the chip; the next pending one does", () => {
      at("2026-11-08T15:00:00Z");
      stub();
      const met = { task_id: "t1", title: "Seguimiento 01318-26", due_at: "2026-11-03T12:00:00Z", version: 1, owner: null };
      const next = { task_id: "t2", title: "Seguimiento 01318-26", due_at: "2026-11-12T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer(
        "sales",
        card({ open_tasks: [met, next], last_contact: { inbound: null, outbound: { at: "2026-11-07T12:00:00Z", subject: "Seguimiento", url: null } } }),
      );
      expect(within(dialog).getByTestId("case-drawer-followup")).toHaveTextContent("Seguimiento 1 de 4 · jueves 12"); // 5 days since Nov 7 → step 1
    });
  });

  describe("footer «Siguiente paso» · Task 8 follow-ups", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("a reminder our later email already met is not the next step; the case's next action is", () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-11-08T15:00:00Z"));
      stub();
      const met = { task_id: "t1", title: "Seguimiento 01318-26", due_at: "2026-11-03T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer(
        "sales",
        card({ open_tasks: [met], last_contact: { inbound: null, outbound: { at: "2026-11-07T12:00:00Z", subject: "Seguimiento", url: null } } }),
      );
      const footer = within(dialog).getByTestId("case-drawer-footer");
      expect(footer).toHaveTextContent("Siguiente paso: Hacer seguimiento de 01318-26");
      expect(footer).not.toHaveTextContent("Seguimiento 01318-26 ·");
    });

    it("a pending reminder is the next step, with its day", () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-11-08T15:00:00Z"));
      stub();
      const met = { task_id: "t1", title: "Seguimiento antiguo", due_at: "2026-11-03T12:00:00Z", version: 1, owner: null };
      const next = { task_id: "t2", title: "Seguimiento 01318-26", due_at: "2026-11-12T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer(
        "sales",
        card({ open_tasks: [met, next], last_contact: { inbound: null, outbound: { at: "2026-11-07T12:00:00Z", subject: "Seguimiento", url: null } } }),
      );
      const footer = within(dialog).getByTestId("case-drawer-footer");
      expect(footer).toHaveTextContent(/Siguiente paso: Seguimiento 01318-26 · jue/);
      expect(footer).not.toHaveTextContent("Seguimiento antiguo");
    });

    it("Resumen's reminder line skips a reminder our later email already met, like the chip and footer", () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-11-08T15:00:00Z"));
      stub();
      const met = { task_id: "t1", title: "Seguimiento antiguo", due_at: "2026-11-03T12:00:00Z", version: 1, owner: null };
      const next = { task_id: "t2", title: "Seguimiento 01318-26", due_at: "2026-11-12T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer(
        "sales",
        card({ open_tasks: [met, next], last_contact: { inbound: null, outbound: { at: "2026-11-07T12:00:00Z", subject: "Seguimiento", url: null } } }),
      );
      const line = within(dialog).getByTestId("drawer-reminder");
      expect(line).toHaveTextContent("Te lo recuerdo el");
      expect(line).not.toHaveTextContent("Está hoy en");
    });

    it("Resumen shows no reminder line when the only reminder is already met", () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-11-08T15:00:00Z"));
      stub();
      const met = { task_id: "t1", title: "Seguimiento 01318-26", due_at: "2026-11-03T12:00:00Z", version: 1, owner: null };
      const dialog = renderDrawer(
        "sales",
        card({ open_tasks: [met], last_contact: { inbound: null, outbound: { at: "2026-11-07T12:00:00Z", subject: "Seguimiento", url: null } } }),
      );
      expect(within(dialog).queryByTestId("drawer-reminder")).toBeNull();
    });
  });

  it("reads the contact's phones only once Contacto is opened, and keeps them", async () => {
    const paths = stub();
    const dialog = renderDrawer("sales");
    await waitFor(() => expect(paths.some((p) => p.endsWith("/timeline"))).toBe(true));
    expect(paths).not.toContain(`/v2/workspace/people/${PERSON}`);
    fireEvent.click(within(dialog).getByRole("tab", { name: "Contacto" }));
    await within(dialog).findByTestId("person-phones");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Resumen" }));
    expect(within(dialog).getByTestId("person-phones")).toBeInTheDocument();
    expect(paths.filter((p) => p === `/v2/workspace/people/${PERSON}`)).toHaveLength(1);
  });

  it("each tab panel takes focus", () => {
    stub();
    const dialog = renderDrawer("sales");
    expect(within(dialog).getByRole("tabpanel", { name: "Resumen" })).toHaveAttribute("tabindex", "0");
  });

  it("timeline error shows «No se pudo cargar la línea de tiempo» with Reintentar", async () => {
    stub({ timeline: [{ status: 500, body: { detail: "boom" } }, { status: 200, body: { opportunity_id: CASE, entries: TIMELINE } }] });
    const dialog = renderDrawer("sales");
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("No se pudo cargar la línea de tiempo");
    fireEvent.click(within(alert).getByRole("button", { name: "Reintentar" }));
    const timeline = await within(dialog).findByTestId("case-timeline");
    expect(timeline).toHaveTextContent("Caso creado");
  });
});
