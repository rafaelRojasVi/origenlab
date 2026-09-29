import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CampaignSummary } from "../crmTypes";
import {
  buildEvents,
  daysBetween,
  monthGrid,
  overviewFigures,
  relativeDay,
  santiagoDay,
  santiagoTime,
} from "./calendar";
import { CampaignCalendar } from "./CampaignCalendar";
import { CampaignDetail } from "./CampaignDetail";
import { buildPreviewDocument } from "./emailPreview";
import { CAMPAIGN_COMMAND_PATHS } from "./marketingApi";
import type { CampaignArchive, EquipmentTaxonomy } from "./marketingTypes";
import { MarketingOverview } from "./MarketingOverview";

// Every campaign, address and figure below is invented; the repository is public.
const taxonomy = taxonomyJson as EquipmentTaxonomy;
const TODAY = "2026-09-27";

const base: CampaignSummary = {
  campaign_id: "",
  name: "",
  status: "archived",
  subject: null,
  approved_at: null,
  created_at: "2026-09-01T12:00:00Z",
  updated_at: "2026-09-01T12:00:00Z",
  first_sent_at: null,
  last_sent_at: null,
  recipients_by_state: {},
  send_attempts: [],
  replies_recorded: 0,
  send_batches: [],
  origin: "native_v2",
  equipment_lines: [],
};

const IMPORTED: CampaignSummary = {
  ...base,
  campaign_id: "a0000000-0000-4000-8000-000000000001",
  name: "Sonicadores (histórica)",
  origin: "imported_v1",
  equipment_lines: [{ family_id: "sonicacion", source: "name_or_subject", matched_term: "hielscher" }],
  // Two real batches: the evening of the 15th (one already the 16th in UTC) and the 16th.
  send_batches: [
    { day: "2026-09-15", accepted: 20, first_accepted_at: "2026-09-15T22:03:00Z", last_accepted_at: "2026-09-16T01:30:00Z" },
    { day: "2026-09-16", accepted: 70, first_accepted_at: "2026-09-16T12:13:00Z", last_accepted_at: "2026-09-16T16:22:00Z" },
  ],
  planned_for_date: "2026-10-01", // stale planning on a sent campaign is never shown
};
const DRAFT: CampaignSummary = {
  ...base,
  campaign_id: "a0000000-0000-4000-8000-000000000002",
  name: "Borrador centrífugas",
  status: "draft",
  version: 2,
  updated_at: "2026-09-26T14:00:00Z",
  planned_for_date: "2026-10-02",
  planned_for_at: "2026-10-02T12:30:00Z",
  planning_version: 1,
};
const FROZEN: CampaignSummary = {
  ...base,
  campaign_id: "a0000000-0000-4000-8000-000000000003",
  name: "Congelada dispersores",
  status: "audience_frozen",
  audience_frozen_at: "2026-09-20T15:00:00Z",
  equipment_lines: [{ family_id: "dispersion-homogeneizacion", source: "audience_criteria", matched_term: null }],
};
const CAMPAIGNS = [IMPORTED, DRAFT, FROZEN];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-27T15:00:00Z"));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ─────────────────────────────────────────────────────────────── pure calendar logic

describe("calendar logic", () => {
  it("places instants on their America/Santiago day and time", () => {
    expect(santiagoDay("2026-09-16T01:30:00Z")).toBe("2026-09-15");
    expect(santiagoTime("2026-09-16T01:30:00Z")).toBe("22:30");
    expect(santiagoDay("2026-07-01T03:30:00Z")).toBe("2026-06-30"); // winter: UTC-4
  });

  it("says relative days in Spanish", () => {
    expect(relativeDay("2026-09-15", TODAY)).toBe("hace 12 días");
    expect(relativeDay("2026-10-02", TODAY)).toBe("en 5 días");
    expect(relativeDay(TODAY, TODAY)).toBe("hoy");
    expect(relativeDay("2026-09-26", TODAY)).toBe("ayer");
    expect(relativeDay("2026-09-28", TODAY)).toBe("mañana");
    expect(daysBetween("2026-02-28", "2026-03-01")).toBe(1);
  });

  it("builds a six-week Monday-first grid", () => {
    const weeks = monthGrid("2026-09");
    expect(weeks).toHaveLength(6);
    expect(weeks[0][0]).toEqual({ day: "2026-08-31", inMonth: false }); // Monday
    expect(weeks[0][1]).toEqual({ day: "2026-09-01", inMonth: true });
  });

  it("shows each real batch, the freeze, the draft save and the plan — never an invented date", () => {
    const events = buildEvents(CAMPAIGNS);
    const sent = events.filter((e) => e.kind === "sent");
    expect(sent.map((e) => [e.day, e.detail])).toEqual([
      ["2026-09-15", "Lote 1 de 2 · 20 aceptados"],
      ["2026-09-16", "Lote 2 de 2 · 70 aceptados"],
    ]);
    expect(sent[0].time).toBe("19:03–22:30");
    expect(events.find((e) => e.kind === "frozen")?.day).toBe("2026-09-20");
    expect(events.find((e) => e.kind === "draft")?.day).toBe("2026-09-26");
    const planned = events.filter((e) => e.kind === "planned");
    expect(planned.map((e) => [e.campaignId, e.day, e.time])).toEqual([[DRAFT.campaign_id, "2026-10-02", "09:30"]]);
  });

  it("computes the overview from recorded sends and planning only", () => {
    const f = overviewFigures(CAMPAIGNS, TODAY);
    expect(f.lastSend).toMatchObject({ day: "2026-09-16", daysAgo: 11 });
    expect(f.nextPlanned).toMatchObject({ day: "2026-10-02", inDays: 5, time: "09:30" });
    expect([f.sent, f.drafts, f.frozen, f.planned]).toEqual([1, 1, 1, 1]);
    expect(overviewFigures([DRAFT], "2026-10-03").planned).toBe(0); // a past plan is not "planned"
  });
});

// ─────────────────────────────────────────────────────────────── overview

describe("MarketingOverview", () => {
  it("shows last send and next planned campaign prominently, and no engagement metrics", () => {
    const onOpen = vi.fn();
    render(<MarketingOverview campaigns={CAMPAIGNS} today={TODAY} onOpen={onOpen} />);
    expect(screen.getByTestId("last-send")).toHaveTextContent("Último envío: hace 11 días");
    expect(screen.getByTestId("next-planned")).toHaveTextContent("Próxima campaña: en 5 días");
    expect(screen.getByTestId("overview-counts")).toHaveTextContent("Enviadas1Borradores1Audiencia congelada1Planificadas1");
    expect(screen.getByText("Planificación interna · no programa el envío")).toBeInTheDocument();
    const text = screen.getByTestId("marketing-overview").textContent ?? "";
    expect(text).not.toMatch(/apertura|clic|respuesta/i);
    fireEvent.click(screen.getByRole("button", { name: DRAFT.name }));
    expect(onOpen).toHaveBeenCalledWith(DRAFT.campaign_id);
  });

  it("says so when nothing was sent or planned", () => {
    render(<MarketingOverview campaigns={[]} today={TODAY} onOpen={() => undefined} />);
    expect(screen.getByTestId("last-send")).toHaveTextContent("Sin envíos registrados");
    expect(screen.getByTestId("next-planned")).toHaveTextContent("Ninguna campaña planificada");
  });
});

// ─────────────────────────────────────────────────────────────── calendar

describe("CampaignCalendar", () => {
  const renderCalendar = (onOpen = vi.fn()) => {
    render(<CampaignCalendar campaigns={CAMPAIGNS} taxonomy={taxonomy} today={TODAY} onOpen={onOpen} />);
    const grid = screen.getByTestId("calendar-grid");
    return { grid, onOpen };
  };
  const cell = (grid: HTMLElement, day: string) => grid.querySelector(`[data-day="${day}"]`) as HTMLElement;

  it("opens on the current month with each kind distinguished and imported campaigns marked", () => {
    const { grid } = renderCalendar();
    expect(screen.getByTestId("calendar-month")).toHaveTextContent(/septiembre de 2026/i);
    const sent = within(cell(grid, "2026-09-15")).getByTestId("calendar-event");
    expect(sent).toHaveAttribute("data-kind", "sent");
    expect(sent).toHaveAttribute("data-origin", "imported_v1");
    expect(sent).toHaveTextContent("V1");
    expect(sent).toHaveTextContent("hace 12 días");
    expect(within(cell(grid, "2026-09-20")).getByTestId("calendar-event")).toHaveAttribute("data-kind", "frozen");
    expect(within(cell(grid, "2026-09-26")).getByTestId("calendar-event")).toHaveAttribute("data-kind", "draft");
    // The stale plan on the sent campaign (Oct 1) is never drawn.
    expect(within(cell(grid, "2026-10-01")).queryAllByTestId("calendar-event")).toHaveLength(0);
    const planned = within(cell(grid, "2026-10-02")).getByTestId("calendar-event");
    expect(planned).toHaveAttribute("data-kind", "planned");
    expect(planned).toHaveTextContent("en 5 días");
  });

  it("moves between months and «Hoy» comes back", () => {
    renderCalendar();
    fireEvent.click(screen.getByRole("button", { name: "Mes siguiente" }));
    expect(screen.getByTestId("calendar-month")).toHaveTextContent(/octubre de 2026/i);
    fireEvent.click(screen.getByRole("button", { name: "Mes anterior" }));
    fireEvent.click(screen.getByRole("button", { name: "Mes anterior" }));
    expect(screen.getByTestId("calendar-month")).toHaveTextContent(/agosto de 2026/i);
    fireEvent.click(screen.getByRole("button", { name: "Hoy" }));
    expect(screen.getByTestId("calendar-month")).toHaveTextContent(/septiembre de 2026/i);
  });

  it("filters by status and by equipment line", () => {
    const { grid } = renderCalendar();
    const kinds = () => [...grid.querySelectorAll("[data-testid=calendar-event]")].map((e) => e.getAttribute("data-kind"));
    fireEvent.click(screen.getByRole("button", { name: /^Enviada$/ }));
    expect(kinds()).not.toContain("sent");
    fireEvent.click(screen.getByRole("button", { name: /^Enviada$/ }));
    fireEvent.change(screen.getByLabelText("Línea de equipo"), { target: { value: "sonicacion" } });
    expect(new Set(kinds())).toEqual(new Set(["sent"]));
    fireEvent.change(screen.getByLabelText("Línea de equipo"), { target: { value: "__none__" } });
    expect(new Set(kinds())).toEqual(new Set(["draft", "planned"]));
  });

  it("an event opens its campaign, on the grid and on the phone agenda", () => {
    const { grid, onOpen } = renderCalendar();
    fireEvent.click(within(cell(grid, "2026-10-02")).getByTestId("calendar-event"));
    expect(onOpen).toHaveBeenCalledWith(DRAFT.campaign_id);
    const agenda = screen.getByTestId("calendar-agenda");
    expect(agenda).toHaveClass("md:hidden");
    expect(grid).toHaveClass("hidden", "md:block");
    expect(within(agenda).getAllByTestId("calendar-event").length).toBeGreaterThan(0);
  });
});

// ─────────────────────────────────────────────────────────────── sent HTML archive

const HOSTILE_HTML =
  '<p onclick="steal()">Hola</p><script>alert(1)</script><form action="https://evil.test"><input name="x"></form>' +
  '<img src="https://tracker.test/open.gif?u=1" width="1" height="1"><img src="https://cdn.other.test/p.png">' +
  '<img src="https://origenlab.cl/products/ika/t-25-digital.png">' +
  '<a href="https://origenlab.cl/marcas/ika/" target="_top" ping="https://tracker.test/ping">Ver</a>' +
  '<a href="javascript:parent.location=\'https://evil.test\'" target="_parent">x</a>' +
  '<iframe src="https://evil.test"></iframe><meta http-equiv="refresh" content="0;url=https://evil.test">';

function archive(over: Partial<CampaignArchive> = {}): CampaignArchive {
  return {
    campaign_id: FROZEN.campaign_id, name: FROZEN.name, status: "audience_frozen", subject: "Dispersores", preheader: "T 25 digital",
    version: 3, content_sha256: "ab".repeat(32), content_frozen_at: "2026-09-20T15:00:00Z", audience_frozen_at: "2026-09-20T15:00:00Z",
    audience_sha256: "cd".repeat(32), audience_policy_version: "marketing-audience/2026-09-27.v1", created_at: "2026-09-19T10:00:00Z",
    origin: "native_v2", sender_address: "ventas@example.invalid", sender_name: "Ventas", html: HOSTILE_HTML, html_state: "archived_verified",
    recipients_by_state: { snapshotted: 12, excluded: 3 }, send_attempts: [], send_batches: [],
    metrics: { opens: null, clicks: null, note: "" }, storage: { table: "outbound.campaign", database: "origenlab_test_abcd1234" },
    ...over,
  };
}

let posts: { path: string; body: unknown; headers: Record<string, string> }[] = [];

function stubArchive(a: CampaignArchive) {
  posts = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { planned_for_date: string | null };
        posts.push({ path: url.pathname, body, headers: init.headers as Record<string, string> });
        return Promise.resolve(
          new Response(
            JSON.stringify({
              campaign_id: a.campaign_id, status: a.status, planning_version: 2, planned_for_date: body.planned_for_date,
              planned_for_at: body.planned_for_date ? `${body.planned_for_date}T13:00:00Z` : null, time_zone: "America/Santiago",
              changed: true, schedules_send: false, label: "Planificación interna · no programa el envío",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url.pathname.endsWith(`/campaigns/${a.campaign_id}/archive`)) {
        return Promise.resolve(new Response(JSON.stringify(a), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
}

function withRole(role: string | null, node: ReactNode) {
  const session =
    role === null
      ? ({ kind: "loading" } as const)
      : ({ kind: "signed_in", method: "google", operator: { email: "op@example.test", displayName: "Op", role, operatorId: "o1" } } as never);
  return <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>{node}</AuthSessionContext.Provider>;
}

function renderDetail(
  a: CampaignArchive,
  summary: CampaignSummary,
  role: string | null = "sales",
  planningEnabled = true,
  tab: "resumen" | "html" = "resumen",
) {
  stubArchive(a);
  const onPlanned = vi.fn();
  render(
    withRole(
      role,
      <CampaignDetail
        summary={summary}
        planningEnabled={planningEnabled}
        initialTab={tab}
        onBack={() => undefined}
        onEdit={() => undefined}
        onPlanned={onPlanned}
      />,
    ),
  );
  return { onPlanned };
}

describe("sent HTML archive", () => {
  it("renders the frozen HTML sandboxed, sanitized, without trackers, remote images or live links", async () => {
    renderDetail(archive(), FROZEN, "sales", true, "html");
    const frame = await screen.findByTestId("email-frame");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    const doc = new DOMParser().parseFromString(srcdoc, "text/html");
    expect(doc.head.querySelector("meta[http-equiv]")?.getAttribute("content")).toContain("default-src 'none'");
    for (const sel of ["script", "form", "input", "iframe", 'meta[http-equiv="refresh"]']) expect(doc.querySelector(sel), sel).toBeNull();
    expect(doc.querySelector("p")?.hasAttribute("onclick")).toBe(false);
    const imgs = [...doc.querySelectorAll("img")].map((i) => i.getAttribute("src"));
    expect(imgs[0]).toMatch(/^data:image\/svg\+xml/); // tracking pixel → placeholder
    expect(imgs[1]).toMatch(/^data:image\/svg\+xml/); // non-allowlisted host → placeholder
    expect(imgs[2]).toBe("https://origenlab.cl/products/ika/t-25-digital.png");
    for (const a of doc.querySelectorAll("a")) {
      expect(a.getAttribute("href")).toBe("#");
      expect(a.hasAttribute("target")).toBe(false);
      expect(a.hasAttribute("ping")).toBe(false);
    }
    expect(srcdoc).not.toContain("tracker.test");
    expect(srcdoc).not.toContain("evil.test");
    await waitFor(() => expect(screen.getByTestId("preview-safety")).toHaveTextContent("2 imagen(es) remota(s) no cargada(s)"));
  });

  it("offers desktop, mobile and a read-only raw view that never becomes markup", async () => {
    renderDetail(archive(), FROZEN, "sales", true, "html");
    await screen.findByTestId("preview-desktop");
    expect(screen.getByTestId("email-frame")).toHaveStyle({ width: "640px" });
    fireEvent.click(screen.getByRole("button", { name: "Móvil" }));
    expect(screen.getByTestId("preview-mobile")).toBeInTheDocument();
    expect(screen.getByTestId("email-frame")).toHaveStyle({ width: "375px" });
    fireEvent.click(within(screen.getByRole("group", { name: "Vista del correo" })).getByRole("button", { name: "HTML" }));
    const raw = screen.getByTestId("raw-html");
    expect(raw.tagName).toBe("PRE");
    expect(raw.textContent).toContain("<script>alert(1)</script>");
    expect(raw.querySelector("script, form, img, iframe")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });

  it("shows subject, preheader, version, fingerprint, sender and counts, and no engagement numbers", async () => {
    renderDetail(archive(), FROZEN);
    const record = await screen.findByTestId("send-record");
    expect(record).toHaveTextContent("Dispersores");
    expect(record).toHaveTextContent("T 25 digital");
    expect(record).toHaveTextContent("v3");
    expect(screen.getByTestId("content-hash")).toHaveTextContent("sha256:abababababababab");
    expect(record).toHaveTextContent("Ventas · ventas@example.invalid");
    expect(record).toHaveTextContent("En la audiencia, no enviado: 12 · Excluido: 3");
    expect(record).toHaveTextContent("Aperturas y clicsNo registrados en el CRM");
    expect(screen.getByTestId("send-dates")).toHaveTextContent("Sin envíos registrados");
  });

  it("an imported campaign without archived HTML says «HTML enviado no archivado» and shows its real batches", async () => {
    renderDetail(
      archive({
        campaign_id: IMPORTED.campaign_id, name: IMPORTED.name, status: "archived", origin: "imported_v1", html: null,
        html_state: "not_archived", subject: null, preheader: null, content_sha256: null, content_frozen_at: null, version: 1,
        send_batches: IMPORTED.send_batches!, send_attempts: [{ submission_state: "accepted", delivery_state: "pending", count: 90 },
          { submission_state: "rejected", delivery_state: "n/a", count: 2 }],
      }),
      IMPORTED,
    );
    const dates = await screen.findByTestId("send-dates");
    expect(screen.getByText("Histórica importada (V1)")).toBeInTheDocument();
    expect(screen.getByText("Cerrada · solo lectura")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Editar|Reabrir|Reenviar|Planificar/ })).toBeNull();
    expect(dates).toHaveTextContent("Lote 1:");
    expect(dates).toHaveTextContent("19:03–22:30 · 20 aceptados");
    expect(dates).toHaveTextContent("Lote 2:");
    const record = screen.getByTestId("send-record");
    expect(record).toHaveTextContent("AsuntoNo disponible");
    expect(record).toHaveTextContent("Huella del contenidoNo disponible");
    expect(record).toHaveTextContent("92 · 90 aceptados · 2 rechazados");
    expect(screen.queryByTestId("planning-label")).toBeNull(); // a sent campaign is not planned
    fireEvent.click(screen.getByRole("button", { name: "HTML" }));
    const note = await screen.findByTestId("html-unavailable");
    expect(note).toHaveTextContent("HTML enviado no archivado");
    expect(screen.queryByTestId("email-frame")).toBeNull();
  });

  it("a draft never shows its editable HTML as sent", async () => {
    renderDetail(
      archive({ campaign_id: DRAFT.campaign_id, status: "draft", html: null, html_state: "not_frozen", content_sha256: null }),
      DRAFT,
      "sales",
      true,
      "html",
    );
    expect(await screen.findByTestId("html-unavailable")).toHaveAttribute("data-state", "not_frozen");
    expect(screen.queryByTestId("email-frame")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────── planning

describe("planning", () => {
  const draftArchive = () => archive({ campaign_id: DRAFT.campaign_id, status: "draft", html: null, html_state: "not_frozen" });

  it("a viewer sees the plan read-only and cannot write", async () => {
    renderDetail(draftArchive(), DRAFT, "viewer");
    expect(await screen.findByTestId("planning-current")).toHaveTextContent("Planificada para viernes, 2 de octubre de 2026 a las 09:30");
    expect(screen.getByTestId("planning-read-only")).toHaveTextContent("Ventas o Administración");
    expect(screen.queryByTestId("save-planning")).toBeNull();
    expect(posts).toEqual([]);
  });

  it("planning switched off is read-only for everyone", async () => {
    renderDetail(draftArchive(), DRAFT, "admin", false);
    expect(await screen.findByTestId("planning-read-only")).toHaveTextContent("no está habilitada");
  });

  it("sales saves a date through the planning command only, with a key, and is told nothing was scheduled", async () => {
    const { onPlanned } = renderDetail(draftArchive(), DRAFT, "sales");
    expect(await screen.findByTestId("planning-label")).toHaveTextContent("Planificación interna · no programa el envío");
    fireEvent.change(screen.getByLabelText("Fecha (Santiago)"), { target: { value: "2026-10-09" } });
    fireEvent.change(screen.getByLabelText("Hora (opcional)"), { target: { value: "10:00" } });
    fireEvent.click(screen.getByTestId("save-planning"));
    await waitFor(() => expect(onPlanned).toHaveBeenCalled());
    expect(posts).toHaveLength(1);
    expect(posts[0].path).toBe(CAMPAIGN_COMMAND_PATHS.plan);
    expect(posts[0].headers["Idempotency-Key"]).toBeTruthy();
    expect(posts[0].body).toEqual({
      campaign_id: DRAFT.campaign_id, expected_planning_version: 1, planned_for_date: "2026-10-09", planned_for_time: "10:00",
    });
    expect(screen.getByRole("status")).toHaveTextContent("No se programó ningún envío");
  });

  it("removing the date is its own audited command with a null day", async () => {
    renderDetail(draftArchive(), DRAFT, "admin");
    fireEvent.click(await screen.findByTestId("clear-planning"));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ planned_for_date: null, planned_for_time: null });
    expect(await screen.findByRole("status")).toHaveTextContent("Planificación eliminada");
  });
});

describe("preview sanitizer: link targets", () => {
  it("drops target, ping and download so a link cannot aim at the dashboard", () => {
    const out = buildPreviewDocument('<a href="https://origenlab.cl/" target="_top" ping="https://t.test" download>x</a>');
    const a = new DOMParser().parseFromString(out.html, "text/html").querySelector("a")!;
    expect([a.getAttribute("href"), a.hasAttribute("target"), a.hasAttribute("ping"), a.hasAttribute("download")]).toEqual([
      "#", false, false, false,
    ]);
    expect(out.removed).toEqual(expect.arrayContaining(["target=", "ping=", "download="]));
  });
});

describe("MarketingPage calendar flow", () => {
  it("shows the overview, opens the calendar tab, and a sent event opens its archive", async () => {
    const { MarketingPage } = await import("../pages/MarketingPage");
    const importedArchive = archive({
      campaign_id: IMPORTED.campaign_id, name: IMPORTED.name, status: "archived", origin: "imported_v1", html: null,
      html_state: "not_archived", send_batches: IMPORTED.send_batches!,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
        const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
        if (url.pathname.endsWith("/v2/workspace/marketing")) {
          return json({ campaigns: CAMPAIGNS, contact_controls: [], replies_note: "", authoring: { drafts_enabled: false, planning_enabled: true } });
        }
        if (url.pathname.endsWith("/v2/workspace/marketing/taxonomy")) return json(taxonomy);
        if (url.pathname.endsWith(`/campaigns/${IMPORTED.campaign_id}/archive`)) return json(importedArchive);
        return Promise.resolve(new Response("{}", { status: 404 }));
      }),
    );
    render(<MarketingPage />);
    expect(await screen.findByTestId("last-send")).toHaveTextContent("Último envío: hace 11 días");
    expect(screen.getByTestId("next-planned")).toHaveTextContent("Próxima campaña: en 5 días");
    fireEvent.click(screen.getByRole("button", { name: "Calendario" }));
    const grid = await screen.findByTestId("calendar-grid");
    fireEvent.click(within(grid.querySelector('[data-day="2026-09-16"]') as HTMLElement).getByTestId("calendar-event"));
    fireEvent.click(await screen.findByRole("button", { name: "HTML" }));
    expect(await screen.findByTestId("html-unavailable")).toHaveTextContent("HTML enviado no archivado");
    expect(screen.getByTestId("campaign-detail")).toHaveTextContent("Histórica importada (V1)");
  });
});
