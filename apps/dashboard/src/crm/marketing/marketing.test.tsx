import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import { MarketingPage } from "../pages/MarketingPage";
import type { MarketingResponse } from "../crmTypes";
import { destinationsOf, recipientList, selectAllEligible, toggle } from "./audienceSelection";
import { PREVIEW_CSP, buildPreviewDocument } from "./emailPreview";
import { TEMPLATES, imageUrlsIn, renderTemplate } from "./emailTemplates";
import { CAMPAIGN_COMMAND_PATHS } from "./marketingApi";
import type { AudiencePerson, AudienceResponse, CampaignContent, EquipmentTaxonomy, FreezePreview, FreezeRow } from "./marketingTypes";

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

  it("treats every data: link as a script URL, not only data:text/html", () => {
    const doc = parse(
      '<a href="data:image/svg+xml,&lt;svg onload=alert(1)&gt;">a</a><a href=" DATA:text/plain,x">b</a>',
    );
    for (const a of doc.querySelectorAll("a")) {
      expect(a.getAttribute("href")).toBe("#");
      expect(a.getAttribute("title")).toBeNull();
    }
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
    expect(posts[0].path).toBe(CAMPAIGN_COMMAND_PATHS.create);
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

// ─────────────────────────────────────────────────────────────── audience freeze

const LINES = [
  ["hielscher", "Hielscher", "Sonicación"],
  ["ortoalresa", "Ortoalresa", "Centrifugación"],
  ["ika", "IKA", "Dispersión y homogeneización"],
  ["adam-equipment", "Adam Equipment", "Pesaje y humedad"],
  ["loeser", "Löser", "Osmometría"],
  ["serva", "SERVA", "Electroforesis, reactivos y consumibles"],
] as const;

function freezeRow(key: string, address: string, extra: Partial<FreezeRow> = {}): FreezeRow {
  return {
    key, address, display_name: null, organizations: [{ organization_id: "o1", name: "Universidad Ficticia" }], via: ["person_interest"],
    evidence: [], relevance: "evidenced", evidence_observed_at: "2026-03-12T10:00:00Z", review_codes: [], inclusion: "included",
    reasons: [], note_labels: [], recontact_review_required: false, prior_contact: null,
    lines: LINES.map(([brand_id, brand, line]) => ({
      brand_id, brand, line, status: brand_id === "hielscher" ? "evidenced" : "sin_informacion",
      label: brand_id === "hielscher" ? "1 evidencia(s)" : "Sin información", bases: [], latest_observed_at: null,
    })),
    ...extra,
  };
}

const BLOCKERS = [
  { code: "unsubscribe_sync_not_automatic", label: "BAJA sin sincronización automática", detail: "Las respuestas de Gmail no se sincronizan automáticamente todavía." },
  { code: "no_send_path", label: "Sin ruta de envío", detail: "Sin cliente de Gmail." },
];

const PREVIEW: FreezePreview = {
  campaign_id: DRAFT.campaign_id, policy_version: "marketing-audience/2026-09-27.v1", criteria: { version: 1, brand_id: "hielscher" },
  preview_sha256: "f".repeat(64),
  content: { subject: "Dispersores IKA", preheader: null, has_html: true, body_text_chars: 4, content_sha256: "c".repeat(64), campaign_version: 3, promises_baja: true },
  rows: [
    freezeRow("cp:1", "ana@uni.test"),
    freezeRow("addr:2", "nuevo@uni.test", { review_codes: ["no_contact_point"] }),
    freezeRow("cp:3", "baja@uni.test", { inclusion: "excluded", reasons: [{ code: "block", label: "Dirección bloqueada" }] }),
  ],
  review_required: ["addr:2"], review_pending: ["addr:2"], malformed_count: 0,
  recontact_review: { enabled: false, policy_version: null, required: [] },
  counts: {
    candidates: 3, rows: 3, included: 2, excluded: 1, malformed_not_stored: 0, review_required: 1, recontact_review_required: 0,
    excluded_by_reason: [{ code: "block", label: "Dirección bloqueada", count: 1 }],
    included_by_line: LINES.map(([brand_id, brand, line]) => ({ brand_id, brand, line, evidenced: brand_id === "hielscher" ? 2 : 0, sin_informacion: brand_id === "hielscher" ? 0 : 2 })),
  },
  problems: [{ code: "review_pending", message: "1 destino(s) con identidad ambigua esperan una decisión" }],
  send_blockers: BLOCKERS, freeze_enabled: true,
};

function stubFreezeApi({ freezeEnabled, frozen = false, preview = PREVIEW }: { freezeEnabled: boolean; frozen?: boolean; preview?: FreezePreview }) {
  posts = [];
  const content = frozen ? { ...DRAFT_CONTENT, status: "audience_frozen" } : DRAFT_CONTENT;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      const path = url.pathname;
      if (init?.method === "POST") {
        posts.push({ path, body: JSON.parse(String(init.body)), headers: init.headers as Record<string, string> });
        return jsonResponse({
          campaign_id: DRAFT.campaign_id, status: "audience_frozen", version: 4, frozen_at: "2026-09-27T13:00:00Z",
          policy_version: PREVIEW.policy_version, content_sha256: "c".repeat(64), audience_sha256: "a".repeat(64),
          counts: { rows: 3, included: 2, excluded: 1, malformed_not_stored: 0 },
          storage: { tables: ["outbound.campaign", "outbound.campaign_recipient"], database: "origenlab_test_abcd1234" },
          send_blockers: BLOCKERS, replayed: false,
        });
      }
      if (path.endsWith("/v2/workspace/marketing")) {
        const m = marketing(true);
        return jsonResponse({ ...m, campaigns: [{ ...DRAFT, status: content.status }, ARCHIVED], authoring: { drafts_enabled: true, freeze_enabled: freezeEnabled } });
      }
      if (path.endsWith("/v2/workspace/marketing/taxonomy")) return jsonResponse(taxonomy);
      if (path.endsWith("/freeze-preview")) return jsonResponse({ ...preview, freeze_enabled: freezeEnabled });
      if (path.endsWith("/recipients"))
        return jsonResponse({
          campaign_id: DRAFT.campaign_id, name: DRAFT.name, status: "audience_frozen", version: 4, audience_frozen_at: "2026-09-27T13:00:00Z",
          audience_policy_version: PREVIEW.policy_version, content_sha256: "c".repeat(64), audience_sha256: "a".repeat(64),
          storage: { table: "outbound.campaign_recipient", database: "origenlab_test_abcd1234" }, send_blockers: BLOCKERS,
          recipients: frozen || posts.length
            ? [{ recipient_id: "r1", address: "ana@uni.test", organization_name: "Universidad Ficticia", display_name: null, frozen_at: "2026-09-27T13:00:00Z",
                 inclusion: "included", frozen_reasons: [], frozen_notes: [], relevance: "evidenced", interest_evidence: [{}],
                 evidence_observed_at: "2026-03-12T10:00:00Z", identity_review: null, recontact_review: null, recontact_override_at: null,
                 campaign_version: 4, content_sha256: "c".repeat(64),
                 policy_version: PREVIEW.policy_version, lifecycle_state: "snapshotted",
                 send_time_refusals: [{ code: "unsubscribe", label: "Solicitó la BAJA" }], suppressed_since_freeze: true }]
            : [],
          suppressed_since_freeze: 1, unsubscribed_since_freeze: 1,
        });
      if (path.endsWith(`/campaigns/${DRAFT.campaign_id}`)) return jsonResponse(content);
      return jsonResponse({ detail: "not found" }, 404);
    }),
  );
}

async function openFreeze() {
  render(<MarketingPage />);
  const cards = await screen.findAllByTestId("campaign-card");
  fireEvent.click(within(cards[0]).getByRole("button", { name: "Editar" }));
  await screen.findByTestId("persistence");
  fireEvent.click(await screen.findByTestId("open-freeze"));
  return screen.findByTestId("audience-freeze");
}

describe("audience freeze", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the BAJA blocker, requires a reviewed identity, and freezes only from the final confirmation", async () => {
    stubFreezeApi({ freezeEnabled: true });
    await openFreeze();
    expect(screen.getByTestId("baja-blocker")).toHaveTextContent("BAJA sin sincronización automática");
    fireEvent.change(screen.getByLabelText("Línea"), { target: { value: "hielscher" } });
    fireEvent.click(screen.getByTestId("freeze-review"));
    await screen.findByTestId("freeze-preview");

    const lines = screen.getAllByTestId("line-coverage");
    expect(lines).toHaveLength(6);
    expect(lines[5]).toHaveTextContent("SERVA — Electroforesis, reactivos y consumibles");
    expect(lines[5]).toHaveTextContent("2 Sin información");
    expect(screen.queryByText(/interés bajo|baja relevancia/i)).toBeNull();
    expect(screen.getAllByTestId("excluded-row")[0]).toHaveTextContent("Dirección bloqueada");

    const cont = screen.getByTestId("freeze-continue");
    expect(cont).toBeDisabled(); // the ambiguous identity has no decision yet
    const review = screen.getByTestId("review-row");
    fireEvent.click(within(review).getByLabelText("Incluir"));
    expect(cont).toBeDisabled(); // a decision needs a note
    fireEvent.change(within(review).getByLabelText("Nota de revisión para nuevo@uni.test"), { target: { value: "Es la jefa de laboratorio" } });
    expect(cont).toBeEnabled();
    fireEvent.click(cont);

    const confirm = await screen.findByTestId("freeze-confirmation");
    expect(confirm).toHaveTextContent("marketing-audience/2026-09-27.v1");
    expect(screen.getByTestId("baja-in-content")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enviar/i })).toBeNull();
    const button = screen.getByTestId("freeze-confirm");
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByTestId("freeze-ack"));
    expect(posts).toEqual([]);
    fireEvent.click(button);

    await screen.findByTestId("freeze-done");
    expect(posts).toHaveLength(1);
    expect(posts[0].path).toBe(CAMPAIGN_COMMAND_PATHS.freeze);
    expect(posts[0].headers["Idempotency-Key"]).toBeTruthy();
    expect(posts[0].body).toMatchObject({
      campaign_id: DRAFT.campaign_id, expected_version: 3, expected_preview_sha256: "f".repeat(64), confirmed: true,
      criteria: { brand_id: "hielscher", model_id: null, recorded: null, scope: "both" },
      review_decisions: [{ key: "addr:2", decision: "include", note: "Es la jefa de laboratorio" }], excluded_keys: [],
    });
    expect(screen.getByTestId("freeze-done")).toHaveTextContent("Nada fue enviado");
    expect(await screen.findAllByTestId("frozen-row")).toHaveLength(1);
  });

  it("an unchecked destination is excluded and the freeze cannot run where it is not enabled", async () => {
    stubFreezeApi({ freezeEnabled: false });
    await openFreeze();
    fireEvent.click(screen.getByTestId("freeze-review"));
    await screen.findByTestId("freeze-preview");
    const review = screen.getByTestId("review-row");
    fireEvent.click(within(review).getByLabelText("Excluir"));
    fireEvent.change(within(review).getByLabelText("Nota de revisión para nuevo@uni.test"), { target: { value: "No es cliente" } });
    fireEvent.click(screen.getByLabelText("Incluir ana@uni.test"));
    expect(screen.getByTestId("freeze-continue")).toBeDisabled(); // nobody left to include
    fireEvent.click(screen.getByLabelText("Incluir ana@uni.test"));
    fireEvent.click(screen.getByTestId("freeze-continue"));
    fireEvent.click(await screen.findByTestId("freeze-ack"));
    expect(screen.getByTestId("freeze-confirm")).toBeDisabled();
    expect(screen.getByTestId("freeze-disabled")).toHaveTextContent("no está habilitado");
    expect(posts).toEqual([]);
  });

  it("a frozen campaign shows its snapshot read-only and a new version is a new unsaved draft", async () => {
    stubFreezeApi({ freezeEnabled: true, frozen: true });
    render(<MarketingPage />);
    const cards = await screen.findAllByTestId("campaign-card");
    fireEvent.click(within(cards[0]).getByRole("button", { name: "Abrir" }));
    expect(await screen.findByTestId("persistence")).toHaveAttribute("data-state", "read_only");
    fireEvent.click(await screen.findByTestId("open-snapshot"));
    await screen.findByTestId("frozen-snapshot");
    expect(screen.getByTestId("baja-blocker")).toBeInTheDocument();
    expect(await screen.findAllByTestId("frozen-row")).toHaveLength(1);
    // Frozen as included, but a «BAJA» arrived afterwards: the live contract refuses it.
    expect(screen.getByTestId("refused-since-freeze")).toHaveTextContent("BAJA posterior al congelamiento");
    expect(screen.getByTestId("frozen-snapshot")).toHaveTextContent("BAJA posterior al congelamiento1");
    expect(screen.queryByTestId("freeze-review")).toBeNull();
    fireEvent.click(screen.getByTestId("new-version"));
    await waitFor(() => expect(screen.getByTestId("persistence")).toHaveAttribute("data-state", "memory"));
    expect(screen.getByLabelText("Nombre interno")).toHaveValue("Copia de Borrador IKA");
    expect(posts).toEqual([]);
  });
});

const PRIOR = [{ code: "prior_contact", label: "Contacto previo registrado" }];

const PREVIEW_W12: FreezePreview = {
  ...PREVIEW,
  policy_version: "marketing-audience/2026-09-27.v2-w12",
  rows: [
    ...PREVIEW.rows,
    freezeRow("cp:4", "prev@uni.test", {
      inclusion: "excluded", reasons: PRIOR, recontact_review_required: true,
      prior_contact: { destination: "prev@uni.test", last_contact_at: "2025-11-03T14:00:00Z", campaign_id: "v1", campaign_name: "Campaña V1 noviembre",
                       sources: [{ source: "send_accepted", reason: "envío aceptado", recorded_at: "2025-11-03" }] },
    }),
    freezeRow("cp:5", "viejo@uni.test", {
      inclusion: "excluded", reasons: PRIOR, recontact_review_required: true,
      prior_contact: { destination: "viejo@uni.test", last_contact_at: null, campaign_id: null, campaign_name: null,
                       sources: [{ source: "wave1a_union", reason: "v1 prior contact", recorded_at: "2026-09-20" }] },
    }),
    freezeRow("addr:6", "sin-cp@uni.test", {
      inclusion: "excluded", reasons: PRIOR, recontact_review_required: true, review_codes: ["no_contact_point"],
      prior_contact: { destination: "sin-cp@uni.test", last_contact_at: null, campaign_id: null, campaign_name: null,
                       sources: [{ source: "wave1b_prior_contact", reason: "v1 prior contact", recorded_at: "2026-09-20" }] },
    }),
  ],
  review_required: ["addr:2", "addr:6"],
  recontact_review: { enabled: true, policy_version: "recontact-review/2026-09-27.v1", required: ["cp:4", "cp:5", "addr:6"] },
  counts: { ...PREVIEW.counts, rows: 6, excluded: 4, recontact_review_required: 3 },
};

describe("W12 recontact review", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows each prior contact, needs a note, applies a reviewed bulk decision per recipient, and freezes it", async () => {
    stubFreezeApi({ freezeEnabled: true, preview: PREVIEW_W12 });
    await openFreeze();
    fireEvent.click(screen.getByTestId("freeze-review"));
    await screen.findByTestId("freeze-preview");

    const rows = screen.getAllByTestId("recontact-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Destino: prev@uni.test");
    expect(within(rows[0]).getByTestId("recontact-last-contact")).toHaveTextContent("Campaña V1 noviembre");
    expect(within(rows[0]).getByTestId("recontact-last-contact")).not.toHaveTextContent("fecha desconocida");
    expect(within(rows[1]).getByTestId("recontact-last-contact")).toHaveTextContent("fecha desconocida · Historial V1 (ola 1A)");
    for (const r of rows) expect(r).toHaveTextContent("Sin decisión: excluido");
    // Prior contacts are not in the plain exclusion list: they have their own panel.
    expect(screen.getAllByTestId("excluded-row")).toHaveLength(1);
    // Only one identity needs a decision until sin-cp is approved.
    expect(screen.getAllByTestId("review-row")).toHaveLength(1);
    const nuevo = screen.getByTestId("review-row");
    fireEvent.click(within(nuevo).getByLabelText("Incluir"));
    fireEvent.change(within(nuevo).getByLabelText("Nota de revisión para nuevo@uni.test"), { target: { value: "Es la jefa de laboratorio" } });
    const cont = screen.getByTestId("freeze-continue");
    expect(cont).toBeEnabled(); // no W12 decision: prior contacts simply stay excluded

    fireEvent.click(within(rows[0]).getByLabelText("Aprobar recontacto"));
    expect(cont).toBeDisabled(); // a recontact decision needs a note
    fireEvent.change(within(rows[0]).getByLabelText("Nota de recontacto para prev@uni.test"), { target: { value: "Pidió la ficha del UP200St" } });
    expect(cont).toBeEnabled();

    // Bulk: select two, write one note, look at the exact list, acknowledge, apply.
    fireEvent.click(screen.getByLabelText("Seleccionar viejo@uni.test"));
    fireEvent.click(screen.getByLabelText("Seleccionar sin-cp@uni.test"));
    const reviewBulk = screen.getByTestId("recontact-bulk-review");
    expect(reviewBulk).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Nota para la selección"), { target: { value: "Lote revisado: clientes 2024" } });
    fireEvent.click(reviewBulk);
    const list = screen.getByTestId("recontact-bulk-confirm");
    expect(list).toHaveTextContent("viejo@uni.test");
    expect(list).toHaveTextContent("sin-cp@uni.test");
    expect(screen.getByTestId("recontact-bulk-apply")).toBeDisabled();
    fireEvent.click(screen.getByTestId("recontact-bulk-ack"));
    fireEvent.click(screen.getByTestId("recontact-bulk-apply"));
    expect(screen.getAllByTestId("recontact-row")[2]).toHaveTextContent("Recontacto aprobado · selección");

    // Approving sin-cp makes its ambiguous identity matter.
    expect(cont).toBeDisabled();
    const reviews = screen.getAllByTestId("review-row");
    expect(reviews).toHaveLength(2);
    fireEvent.click(within(reviews[1]).getByLabelText("Incluir"));
    fireEvent.change(within(reviews[1]).getByLabelText("Nota de revisión para sin-cp@uni.test"), { target: { value: "Es el mismo investigador" } });
    expect(cont).toBeEnabled();
    fireEvent.click(cont);

    const confirm = await screen.findByTestId("freeze-confirmation");
    expect(confirm).toHaveTextContent("marketing-audience/2026-09-27.v2-w12");
    expect(confirm).toHaveTextContent("recontact-review/2026-09-27.v1");
    expect(screen.getByTestId("recontact-summary")).toHaveTextContent("3 destino(s) con contacto previo");
    fireEvent.click(screen.getByTestId("freeze-ack"));
    fireEvent.click(screen.getByTestId("freeze-confirm"));
    await screen.findByTestId("freeze-done");

    expect(posts).toHaveLength(1);
    expect(posts[0].body).toMatchObject({
      recontact_decisions: [
        { key: "cp:4", decision: "approve", note: "Pidió la ficha del UP200St", mode: "individual" },
        { key: "cp:5", decision: "approve", note: "Lote revisado: clientes 2024", mode: "bulk" },
        { key: "addr:6", decision: "approve", note: "Lote revisado: clientes 2024", mode: "bulk" },
      ],
      review_decisions: [
        { key: "addr:2", decision: "include", note: "Es la jefa de laboratorio" },
        { key: "addr:6", decision: "include", note: "Es el mismo investigador" },
      ],
    });
  });

  it("without W12 a prior contact is excluded with no way to approve it", async () => {
    const preview: FreezePreview = {
      ...PREVIEW,
      rows: [...PREVIEW.rows, freezeRow("cp:4", "prev@uni.test", { inclusion: "excluded", reasons: PRIOR })],
    };
    stubFreezeApi({ freezeEnabled: true, preview });
    await openFreeze();
    fireEvent.click(screen.getByTestId("freeze-review"));
    await screen.findByTestId("freeze-preview");
    expect(screen.queryByTestId("recontact-panel")).toBeNull();
    expect(screen.getByTestId("recontact-disabled")).toHaveTextContent("no está habilitada");
    expect(screen.queryByText("Aprobar recontacto")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────── W10 unsubscribe status

const SUPPRESSIONS = {
  summary: { unsubscribed_addresses: 2, baja_messages: 3, last_recorded_at: "2026-09-27T12:00:00Z" },
  entries: [
    { contact_control_id: "cc1", address: "***@uni.test", purpose: "marketing", reason: "unsubscribe", source: "unsubscribe_handler",
      recorded_at: "2026-09-27T12:00:00Z", baja_messages: 2, last_observed_at: "2026-09-26T18:04:05Z" },
    { contact_control_id: "cc2", address: "***@lab.test", purpose: "marketing", reason: "suppression list", source: "wave1a_suppression",
      recorded_at: "2026-09-05T12:00:00Z", baja_messages: 1, last_observed_at: "2026-09-26T18:10:00Z" },
  ],
  truncated: false,
  frozen_campaigns: [{ campaign_id: DRAFT.campaign_id, name: "Borrador IKA", status: "audience_frozen", unsubscribed_since_freeze: 1,
    refused_since_freeze: 1, included_at_freeze: 12 }],
  blocks_by_purpose: [{ kind: "block", purpose: "marketing", count: 2 }],
  gmail_sync: { automatic: false, label: "Las respuestas de Gmail no se sincronizan automáticamente todavía. Una BAJA queda registrada sólo cuando un operador aplica un lote de respuestas ya descargadas." },
  grammar: { version: "baja-reply/2026-09-27.v1", accepted: ["BAJA", "BAJA."], rule: "Sólo una respuesta cuyo texto propio es exactamente «BAJA»." },
  apply_enabled: false, permanent: true, resubscribe_supported: false,
  storage: { table: "outbound.contact_control", database: "origenlab_test_abcd1234" },
};

describe("Bajas (W10)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the suppression state read-only, masked as served, and says Gmail is not synchronized", async () => {
    const calls: { path: string; method: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
        calls.push({ path: url.pathname, method: init?.method ?? "GET" });
        if (url.pathname.endsWith("/v2/workspace/marketing")) return jsonResponse(marketing(true));
        if (url.pathname.endsWith("/v2/workspace/marketing/taxonomy")) return jsonResponse(taxonomy);
        if (url.pathname.endsWith("/v2/workspace/marketing/suppressions")) return jsonResponse(SUPPRESSIONS);
        return jsonResponse({ detail: "not found" }, 404);
      }),
    );
    render(<MarketingPage />);
    await screen.findAllByTestId("campaign-card");
    fireEvent.click(screen.getByRole("button", { name: "Bajas" }));
    const panel = await screen.findByTestId("suppression-status");
    expect(screen.getByTestId("gmail-sync-notice")).toHaveTextContent("no se sincronizan automáticamente");
    expect(screen.getByTestId("gmail-sync-notice")).toHaveTextContent("no existe la re-suscripción");
    const rows = screen.getAllByTestId("suppression-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("***@uni.test");
    expect(rows[1]).toHaveTextContent("bloqueo previo: suppression list");
    expect(screen.getByTestId("frozen-vs-baja")).toHaveTextContent("1 con BAJA posterior");
    expect(screen.getByTestId("baja-grammar")).toHaveTextContent("«BAJA», «BAJA.»");
    // No action of any kind: no button inside the panel, no Send anywhere, no write.
    expect(within(panel).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /enviar|aplicar|sincronizar|suscrib/i })).toBeNull();
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    expect(panel.textContent).not.toMatch(/[a-z0-9]+@(uni|lab)\.test/);
  });
});
