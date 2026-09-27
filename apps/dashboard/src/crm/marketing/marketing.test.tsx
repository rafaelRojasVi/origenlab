import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import { MarketingPage } from "../pages/MarketingPage";
import type { MarketingResponse } from "../crmTypes";
import { destinationsOf, recipientList, selectAllEligible, toggle } from "./audienceSelection";
import { PREVIEW_CSP, buildPreviewDocument } from "./emailPreview";
import { TEMPLATES, imageUrlsIn, renderTemplate } from "./emailTemplates";
import { CAMPAIGN_DRAFT_COMMAND_PATHS } from "./marketingApi";
import type { AudiencePerson, AudienceResponse, CampaignContent, EquipmentTaxonomy } from "./marketingTypes";

// Every institution, address and campaign below is invented; the repository is public.
const taxonomy = taxonomyJson as EquipmentTaxonomy;

// ─────────────────────────────────────────────────────────────── preview isolation

describe("buildPreviewDocument", () => {
  const parse = (html: string) => new DOMParser().parseFromString(buildPreviewDocument(html).html, "text/html");

  it("puts the CSP first in <head> and removes every script, frame, form and handler", () => {
    const out = buildPreviewDocument(
      '<p onclick="x()">hola</p><script>alert(1)</script><iframe src="https://x.test"></iframe>' +
        '<form action="https://x.test"><input></form><meta http-equiv="refresh" content="0;url=https://x.test">' +
        '<base href="https://x.test/"><object data="x"></object><svg><image href="https://x.test/p.png"/></svg>',
    );
    const doc = new DOMParser().parseFromString(out.html, "text/html");
    const first = doc.head.querySelector("meta[http-equiv]");
    expect(first?.getAttribute("content")).toBe(PREVIEW_CSP);
    expect(PREVIEW_CSP).toContain("default-src 'none'");
    for (const sel of ["script", "iframe", "form", "input", "base", "object", "svg image", 'meta[http-equiv="refresh"]']) {
      expect(doc.querySelector(sel), sel).toBeNull();
    }
    expect(doc.querySelector("p")?.hasAttribute("onclick")).toBe(false);
    expect(out.removed).toEqual(expect.arrayContaining(["<script>", "<iframe>", "<form>", "on…="]));
  });

  it("blocks remote images and CSS urls (tracking pixels) but keeps origenlab.cl images", () => {
    const out = buildPreviewDocument(
      '<img src="https://tracker.test/open.gif?id=1" width="1"><img src="https://origenlab.cl/products/ika/t-25-digital.png">' +
        '<div style="background:url(https://tracker.test/bg.png)">x</div><style>@import url(https://x.test/a.css); td{background:url("https://tracker.test/c.png")}</style>' +
        '<img srcset="https://tracker.test/a.png 1x, https://origenlab.cl/products/ika/t-10-basic.png 2x">',
    );
    const doc = new DOMParser().parseFromString(out.html, "text/html");
    const imgs = [...doc.querySelectorAll("img")];
    expect(imgs[0].getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
    expect(imgs[1].getAttribute("src")).toBe("https://origenlab.cl/products/ika/t-25-digital.png");
    expect(imgs[2].getAttribute("srcset")).toBe("https://origenlab.cl/products/ika/t-10-basic.png 2x");
    expect(doc.querySelector("div")?.getAttribute("style")).not.toContain("tracker.test");
    expect(doc.querySelector("style")?.textContent).not.toContain("tracker.test");
    expect(doc.querySelector("style")?.textContent).not.toContain("@import");
    expect(out.blockedImages).toEqual(
      expect.arrayContaining(["https://tracker.test/open.gif?id=1", "https://tracker.test/bg.png", "https://tracker.test/c.png", "https://tracker.test/a.png"]),
    );
  });

  it("keeps links visible but inert, and drops script URLs", () => {
    const doc = parse('<a href="https://origenlab.cl/marcas/ika/">Ver</a><a href=" javascript:alert(1)">x</a>');
    const [a, b] = [...doc.querySelectorAll("a")];
    expect(a.getAttribute("href")).toBe("#");
    expect(a.getAttribute("title")).toBe("https://origenlab.cl/marcas/ika/");
    expect(b.getAttribute("href")).toBe("#");
    expect(b.getAttribute("title")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────── templates

describe("OrigenLab templates", () => {
  const verified = new Set(taxonomy.models.map((m) => m.image?.url));

  it.each(TEMPLATES.map((t) => t.id))("'%s' uses only verified catalogue images and no unsafe markup", (id) => {
    const html = renderTemplate(taxonomy, { template: id, brandId: "hielscher" });
    expect(html).toContain("<!doctype html>");
    expect(html).not.toMatch(/<script|\son[a-z]+=|javascript:/i);
    for (const url of imageUrlsIn(html)) {
      expect(url.startsWith("https://origenlab.cl/"), url).toBe(true);
      if (url.includes("/products/")) expect(verified.has(url), url).toBe(true);
    }
    expect(buildPreviewDocument(html).blockedImages).toEqual([]);
  });

  it("a product template names the chosen model and links to its page", () => {
    const model = taxonomy.models.find((m) => m.id === "loeser-i-osmometer")!;
    const html = renderTemplate(taxonomy, { template: "producto", modelId: model.id });
    expect(html).toContain(model.name);
    expect(html).toContain(model.image!.url);
    expect(html).toContain(model.page_url);
  });
});

// ─────────────────────────────────────────────────────────────── selection rules

function person(key: string, address: string, eligible: boolean): AudiencePerson {
  return {
    key, address, contact_point_id: null, person_id: null, display_name: null, organization_ids: [], interests: [], other_interest_count: 0,
    eligibility: { eligible, reasons: eligible ? [] : [{ code: "blocked_address", label: "Dirección bloqueada" }], notes: [] },
  };
}

describe("audience selection", () => {
  const persons = [person("a", "ana@lab.test", true), person("b", "baja@lab.test", false), person("c", "ANA@lab.test", true)];
  const all = destinationsOf(persons, []);

  it("never selects an ineligible destination, one at a time or all at once", () => {
    expect(toggle(new Set(), all.get("b")).has("b")).toBe(false);
    const r = selectAllEligible(new Set(), ["a", "b", "c"], all);
    expect([...r.selected].sort()).toEqual(["a", "c"]);
    expect(r.skipped).toBe(1);
  });

  it("the recipient list is eligible-only and one per address, even for a stale selection", () => {
    expect(recipientList(new Set(["a", "b", "c"]), all).map((d) => d.address)).toEqual(["ana@lab.test"]);
  });
});

// ─────────────────────────────────────────────────────────────── page

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const ARCHIVED = {
  campaign_id: "c0000000-0000-4000-8000-000000000001", name: "Campaña archivada", status: "archived", subject: null, preheader: null,
  has_html: false, version: 1, approved_at: null, created_at: "2026-03-01T00:00:00Z", updated_at: "2026-03-01T00:00:00Z",
  first_sent_at: "2026-03-02T00:00:00Z", last_sent_at: "2026-03-02T00:00:00Z", recipients_by_state: { sent: 10 }, send_attempts: [], replies_recorded: 0,
};
const DRAFT = {
  ...ARCHIVED, campaign_id: "c0000000-0000-4000-8000-000000000002", name: "Borrador IKA", status: "draft", subject: "Dispersores IKA",
  has_html: true, recipients_by_state: {}, first_sent_at: null, last_sent_at: null,
};
const DRAFT_CONTENT: CampaignContent = {
  campaign_id: DRAFT.campaign_id, name: DRAFT.name, status: "draft", subject: DRAFT.subject, preheader: null,
  body_html: '<p>Hola</p><img src="https://tracker.test/p.gif">', has_text: false, version: 3, max_sends: 50,
  recontact_interval_days: 90, created_at: null, updated_at: "2026-09-27T10:00:00Z", created_by: "Op", database: "origenlab_test_abcd1234",
};

function marketing(draftsEnabled: boolean): MarketingResponse {
  return {
    campaigns: [DRAFT, ARCHIVED], contact_controls: [], replies_note: "sin respuestas",
    storage: { table: "outbound.campaign", database: "origenlab_test_abcd1234" }, authoring: { drafts_enabled: draftsEnabled },
  };
}

const AUDIENCE: AudienceResponse = {
  persons: [
    { ...person("cp:1", "ana@uni.test", true), interests: [] },
    { ...person("cp:2", "baja@uni.test", false), interests: [] },
  ],
  institutions: [],
  sending: { unique_destinations: 2, eligible_unique_destinations: 1, excluded_by_reason: [{ code: "blocked_address", label: "Dirección bloqueada", count: 1 }] },
  coverage: {
    crm_interest_rows: 0, crm_interests_matched: 0, crm_interests_unmatched: 0, quotation_evidence_records: 5,
    quotation_evidence_with_mentions: 2, quotation_evidence_already_recorded: 0, case_titles_with_mentions: 1,
    recipients_without_contact_point: 1, brand_linkage: { hielscher: { crm: 0, evidence_only: 2 } }, review_queue: { "organization_name:unresolved": 3 },
  },
};
AUDIENCE.persons.forEach((p) => {
  p.interests = [{
    brand_id: "hielscher", family_id: "sonicacion", model_id: "hielscher-up200st", matched_term: "UP200St", matched_text: "up200st",
    basis: "requested_quotation", basis_label: "Pidió cotización",
    source: { kind: "quotation_evidence", label: "Evidencia de cotización (correo enviado)", opportunity_id: "11111111-1111-4111-8111-111111111111",
      case_title: "Caso", quote_numbers: ["00001-26"], source_record_id: "s1", interest_id: null, detail: "Cotización UP200St" },
    date: "2026-03-12T10:00:00Z", recorded_in_crm: false, confirmation: null,
  }];
});

let posts: { path: string; body: unknown; headers: Record<string, string> }[] = [];

function stubApi(draftsEnabled: boolean) {
  posts = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const path = url.pathname;
      if (init?.method === "POST") {
        posts.push({ path, body: JSON.parse(String(init.body)), headers: init.headers as Record<string, string> });
        return jsonResponse({
          campaign_id: "c0000000-0000-4000-8000-0000000000ff", status: "draft", version: 1, saved_at: "2026-09-27T12:34:00+00:00",
          changed_fields: ["name"], storage: { table: "outbound.campaign", database: "origenlab_test_abcd1234" }, replayed: false,
        });
      }
      if (path.endsWith("/v2/workspace/marketing")) return jsonResponse(marketing(draftsEnabled));
      if (path.endsWith("/v2/workspace/marketing/taxonomy")) return jsonResponse(taxonomy);
      if (path.endsWith("/v2/workspace/marketing/audience")) return jsonResponse(AUDIENCE);
      if (path.endsWith(`/campaigns/${DRAFT.campaign_id}`)) return jsonResponse(DRAFT_CONTENT);
      return jsonResponse({ detail: "not found" }, 404);
    }),
  );
}

describe("MarketingPage", () => {
  beforeEach(() => vi.spyOn(window, "confirm").mockReturnValue(true));
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows a sandboxed thumbnail for stored HTML and «Contenido no importado» when there is none", async () => {
    stubApi(false);
    render(<MarketingPage />);
    const cards = await screen.findAllByTestId("campaign-card");
    expect(within(cards[1]).getByTestId("thumb-not-imported")).toHaveTextContent("Contenido no importado");
    const thumb = await within(cards[0]).findByTestId("thumb");
    const frame = within(thumb).getByTestId("email-frame");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame.getAttribute("srcdoc")).toContain("Content-Security-Policy");
    expect(frame.getAttribute("srcdoc")).not.toContain("tracker.test");
    expect(screen.getByTestId("campaign-storage-note")).toHaveTextContent("origenlab_test_abcd1234");
  });

  it("a new draft says it exists only in this tab and cannot be saved where drafts are disabled", async () => {
    stubApi(false);
    render(<MarketingPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Nueva campaña" }));
    const banner = await screen.findByTestId("persistence");
    expect(banner).toHaveAttribute("data-state", "memory");
    expect(banner).toHaveTextContent("existe sólo en esta pestaña");
    expect(banner).toHaveTextContent("no está habilitado");
    fireEvent.change(screen.getByLabelText("Nombre interno"), { target: { value: "Prueba" } });
    expect(screen.getByTestId("save-draft")).toBeDisabled();
    expect(posts).toEqual([]);
  });

  it("saving creates the draft through the draft command and then says where it is stored", async () => {
    stubApi(true);
    render(<MarketingPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Nueva campaña" }));
    await screen.findByTestId("persistence");
    fireEvent.change(screen.getByLabelText("Nombre interno"), { target: { value: "Sonicadores" } });
    expect(screen.getByTestId("save-draft")).toBeDisabled(); // limits are policy: no defaults
    fireEvent.change(screen.getByLabelText("Máximo de envíos"), { target: { value: "40" } });
    fireEvent.change(screen.getByLabelText("Días para recontactar"), { target: { value: "90" } });
    await waitFor(() => expect(screen.getByLabelText("Plantilla")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Usar plantilla" }));
    fireEvent.click(screen.getByTestId("save-draft"));
    await waitFor(() => expect(screen.getByTestId("persistence")).toHaveAttribute("data-state", "saved"));
    expect(screen.getByTestId("persistence")).toHaveTextContent("outbound.campaign");
    expect(screen.getByTestId("persistence")).toHaveTextContent("origenlab_test_abcd1234");
    expect(posts).toHaveLength(1);
    expect(posts[0].path).toBe(CAMPAIGN_DRAFT_COMMAND_PATHS.create);
    expect(posts[0].headers["Idempotency-Key"]).toBeTruthy();
    expect(posts[0].body).toMatchObject({ name: "Sonicadores", max_sends: 40, recontact_interval_days: 90 });
    expect(String((posts[0].body as { body_html: string }).body_html)).toContain("https://origenlab.cl/products/");

    fireEvent.change(screen.getByLabelText("Asunto"), { target: { value: "Nuevo asunto" } });
    expect(screen.getByTestId("persistence")).toHaveAttribute("data-state", "dirty");
  });

  it("an existing draft opens with its stored version, and a duplicate is unsaved", async () => {
    stubApi(true);
    render(<MarketingPage />);
    const cards = await screen.findAllByTestId("campaign-card");
    fireEvent.click(within(cards[0]).getByRole("button", { name: "Editar" }));
    const banner = await screen.findByTestId("persistence");
    expect(banner).toHaveAttribute("data-state", "saved");
    expect(banner).toHaveTextContent("versión 3");
    expect(await screen.findByTestId("preview-blocked")).toHaveTextContent("1 recurso(s) remoto(s)");
    fireEvent.click(screen.getByRole("button", { name: "Duplicar" }));
    await waitFor(() => expect(screen.getByTestId("persistence")).toHaveAttribute("data-state", "memory"));
    expect(screen.getByLabelText("Nombre interno")).toHaveValue("Copia de Borrador IKA");
  });

  it("audience: coverage says «Sin información», ineligible people cannot be selected, a row opens its evidence", async () => {
    stubApi(false);
    render(<MarketingPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Audiencias por equipo" }));
    const builder = await screen.findByTestId("audience-builder");
    const coverage = await within(builder).findByTestId("coverage-table");
    const serva = within(coverage).getByText("SERVA Electrophoresis").closest("tr")!;
    expect(serva).toHaveTextContent("Sin información");
    expect(within(coverage).getByText("Hielscher Ultrasonics").closest("tr")).toHaveTextContent("2");

    expect(screen.getByLabelText("Seleccionar baja@uni.test")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Seleccionar personas enviables" }));
    expect(screen.getByLabelText("Seleccionar ana@uni.test")).toBeChecked();
    expect(screen.getByLabelText("Seleccionar baja@uni.test")).not.toBeChecked();
    expect(screen.getByTestId("selection-bar")).toHaveTextContent("1 persona(s) no seleccionable(s)");

    fireEvent.click(screen.getByRole("button", { name: "baja@uni.test" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Dirección bloqueada");
    expect(dialog).toHaveTextContent("Pidió cotización");
    expect(dialog).toHaveTextContent("no registrado como interés en el CRM");
    expect(within(dialog).getByRole("link", { name: /Ver caso/ })).toHaveAttribute("href", "#/crm/oportunidades/11111111-1111-4111-8111-111111111111");
  });
});
