import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import taxonomyJson from "../../../../api/src/origenlab_api/v2/equipment_taxonomy.json";
import type { EquipmentInterestsResponse, ProvidersResponse, SupplierDirectoryEntry } from "../crmTypes";
import type { AudienceInterest, EquipmentTaxonomy } from "../marketing/marketingTypes";
import { OrganizationsPage } from "../pages/OrganizationsPage";
import { PeoplePage } from "../pages/PeoplePage";
import { ProvidersPage } from "../pages/ProvidersPage";

// Every value below is invented; the repository is public.
const taxonomy = taxonomyJson as EquipmentTaxonomy;
const ORG = "11111111-2222-4333-8444-555555555555";
const ORG_EMPTY = "11111111-2222-4333-8444-666666666666";

function respond(routes: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      const hit = Object.entries(routes).find(([p]) => path === p);
      if (!hit) return Promise.resolve(new Response("{}", { status: 404 }));
      return Promise.resolve(new Response(JSON.stringify(hit[1]), { status: 200, headers: { "Content-Type": "application/json" } }));
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

function interest(over: Partial<AudienceInterest> = {}): AudienceInterest {
  return {
    brand_id: "hielscher",
    family_id: "sonicacion",
    model_id: null,
    matched_term: "Hielscher",
    matched_text: "hielscher",
    basis: "requested_quotation",
    basis_label: "Pidió cotización",
    source: {
      kind: "quotation_evidence",
      label: "Evidencia de cotización (correo enviado)",
      opportunity_id: null,
      case_title: null,
      quote_numbers: [],
      source_record_id: "sr1",
      interest_id: null,
      detail: "Cotización Hielscher",
    },
    date: "2026-03-12T10:00:00+00:00",
    recorded_in_crm: false,
    confirmation: null,
    ...over,
  };
}

const LINES = taxonomy.families.map((f) => ({
  family_id: f.id,
  name: f.name,
  color: f.color,
  brand_ids: taxonomy.brands.filter((b) => b.family_id === f.id).map((b) => b.id),
  crm_people: 0,
  address_only: 0,
  institutions: 0,
}));

const INDEX: EquipmentInterestsResponse = {
  lines: LINES.map((l) => (l.family_id === "sonicacion" ? { ...l, crm_people: 1, address_only: 1, institutions: 1 } : l)),
  persons: [
    {
      key: "cp:cp-ana", address: "ana@uni.invalid", address_ref: "ref-ana", contact_point_id: "cp-ana",
      person_id: "per-ana", display_name: "Ana Ficticia", organization_ids: [ORG], link: "crm_person",
      interests: [interest()],
    },
    {
      key: "addr:x", address: "***@uni.invalid", address_ref: "ref-lab", contact_point_id: null,
      person_id: null, display_name: null, organization_ids: [ORG], link: "address_only",
      interests: [interest({ brand_id: "ortoalresa", family_id: "centrifugacion" }), interest()],
    },
  ],
  institutions: [{ organization_id: ORG, name: "Universidad Ficticia", interests: [interest()] }],
};

function org(id: string, name: string) {
  return { organization_id: id, name, kind: "institution", confirmation: "confirmed", case_count: 1, cases_as_requesting_institution: 1 };
}

const ORGS = { items: [org(ORG, "Universidad Ficticia"), org(ORG_EMPTY, "Instituto Sin Evidencia")], total: 2, limit: 60, offset: 0 };

describe("ProvidersPage", () => {
  const directory: SupplierDirectoryEntry[] = taxonomy.brands.map((b) => ({
    brand_id: b.id,
    name: b.name,
    page_url: b.page_url,
    family: taxonomy.families.find((f) => f.id === b.family_id)!,
    model_count: taxonomy.models.filter((m) => m.brand_id === b.id).length,
    crm_organizations: [],
    candidate_hints: b.id === "hielscher" ? [{ domain: "hielscher.invalid", trade_name: null, resolution: "unresolved" }] : [],
  }));
  const body: ProvidersResponse = {
    directory,
    on_cases: [],
    candidates: [
      { domain: "hielscher.invalid", trade_name: null, resolution: "unresolved", mentions: 3 },
      { domain: "otro.invalid", trade_name: "Otro Ltda", resolution: "unresolved", mentions: 1 },
    ],
  };

  it("shows the six canonical suppliers first and keeps candidates collapsed", async () => {
    respond({ "/v2/workspace/providers": body });
    render(<ProvidersPage />);
    const dir = await screen.findByTestId("supplier-directory");
    const names = within(dir).getAllByTestId("directory-card").map((c) => c.querySelector("p")?.textContent);
    expect(names).toEqual([
      "Hielscher Ultrasonics", "Ortoalresa", "IKA", "Adam Equipment", "Löser Messtechnik", "SERVA Electrophoresis",
    ]);
    const candidates = screen.getByTestId("supplier-candidates") as HTMLDetailsElement;
    expect(candidates.open).toBe(false);
    expect(within(candidates).getByText("Candidatos por revisar")).toBeInTheDocument();
    // The directory comes before the candidates in the page.
    expect(dir.compareDocumentPosition(candidates) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // A matching candidate is a hint on the brand, still unreviewed — not a directory entry.
    expect(within(dir).getByText(/Candidatos que la citan: hielscher\.invalid · sin revisar/)).toBeInTheDocument();
    expect(within(dir).queryByText("otro.invalid")).toBeNull();
    // No session → no write affordance for candidates at all.
    expect(screen.queryByText(/Confirmar/)).toBeNull();
    expect(screen.queryByText(/Rechazar/)).toBeNull();
  });
});

describe("Intereses observados on organization cards", () => {
  it("shows line, source and date, «Sin información» without evidence, and opens the line", async () => {
    respond({
      "/v2/organizations": ORGS,
      "/v2/workspace/equipment-interests": INDEX,
      "/v2/workspace/marketing/taxonomy": taxonomy,
    });
    render(<OrganizationsPage navigate={() => undefined} />);
    const withEvidence = (await screen.findByText("Universidad Ficticia")).closest("article")!;
    const block = within(withEvidence).getByTestId("equipment-interests");
    // Historical evidence is an observed interest, never a stated preference.
    expect(within(withEvidence).getByText("Intereses observados")).toBeInTheDocument();
    expect(screen.queryByText(/preferencia/i)).toBeNull();
    expect(await within(block).findByText(/Hielscher Ultrasonics \(marca\) · Pidió cotización · Evidencia de cotización \(correo enviado\) · /)).toBeInTheDocument();
    const without = screen.getByText("Instituto Sin Evidencia").closest("article")!;
    expect(within(without).getByText("Sin información")).toBeInTheDocument();

    fireEvent.click(within(block).getByRole("button", { name: /Sonicación/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Personas del CRM (1)")).toBeInTheDocument();
    expect(within(dialog).getByText("Solo dirección — evidencia histórica (1)")).toBeInTheDocument();
    expect(within(dialog).getByText("Instituciones (1)")).toBeInTheDocument();
    expect(within(dialog).getByText("Persona del CRM")).toBeInTheDocument();
    expect(within(dialog).getByText("Solo evidencia")).toBeInTheDocument();
    // Only this line's evidence: the address-only row's centrifuge interest is not listed here.
    expect(within(dialog).queryByText(/Ortoalresa/)).toBeNull();
  });

  it("never says «Sin información» when the interests could not be read", async () => {
    respond({ "/v2/organizations": ORGS });
    render(<OrganizationsPage navigate={() => undefined} />);
    await screen.findByText("Instituto Sin Evidencia");
    expect((await screen.findAllByText("No disponible en este entorno")).length).toBe(2);
    expect(screen.queryByText("Sin información")).toBeNull();
  });
});

describe("Intereses observados on people cards", () => {
  it("joins a masked recipient to its evidence by its opaque address ref and lists several lines", async () => {
    respond({
      "/v2/workspace/overview": { entities: [{ key: "persons", count: 0, provenance: "not_imported", note: "…" }] },
      "/v2/workspace/pipeline": {
        items: [{
          opportunity_id: "c1", title: "Caso", stage: "quoting", created_at: null, updated_at: null, closed_at: null,
          close_reason: null, organization: null, other_organizations: [],
          contact: { source: "gmail_recipient", name: null, address: "***@uni.invalid", others: 0, address_ref: "ref-lab" },
          quotes: [], quote_numbers: [], revision_count: 0, latest_revision: null, drive_folder: null, attention: [],
          status: "ok", next_action: { text: "", source: "suggested", due_at: null },
        }],
        total: 1,
        drive_configured: false,
      },
      "/v2/contacts": { items: [], total: 0, limit: 30, offset: 0 },
      "/v2/workspace/equipment-interests": INDEX,
      "/v2/workspace/marketing/taxonomy": taxonomy,
    });
    render(<PeoplePage navigate={() => undefined} />);
    const card = (await screen.findByText("***@uni.invalid")).closest("article")!;
    const block = within(card).getByTestId("equipment-interests");
    expect(await within(block).findByRole("button", { name: /Sonicación/ })).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: /Centrifugación/ })).toBeInTheDocument();
  });
});
