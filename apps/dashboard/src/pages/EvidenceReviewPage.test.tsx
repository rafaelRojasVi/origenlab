import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EvidenceReviewPage } from "./EvidenceReviewPage";

vi.mock("../api/v2Client", () => ({
  fetchV2Contacts: vi.fn(),
  fetchV2Organizations: vi.fn(),
  fetchV2Prospects: vi.fn(),
  fetchV2EvidenceRecords: vi.fn(),
}));

import {
  fetchV2Contacts,
  fetchV2EvidenceRecords,
  fetchV2Organizations,
  fetchV2Prospects,
} from "../api/v2Client";

const page = <T,>(items: T[], total = items.length) => ({
  items,
  total,
  limit: 50,
  offset: 0,
});

const KNOWN_ADDRESS = {
  source_record_id: "aaaaaaaa-1111-4111-8111-111111111111",
  source_kind: "gmail_message",
  dedupe_key: "gmail_message:m1",
  source_uri: "gmail://msg/m1",
  acquired_at: "2026-09-21T20:05:00Z",
  review_status: "pending",
  is_quarantined: false,
  subject: "Cotización insumos osmómetro",
  from_address: "diego.soto@farmadelta.example.cl",
  from_domain: "farmadelta.example.cl",
  message_date: "2026-09-21T12:08:33Z",
  thread_id: "t1",
  assertion_total: 1,
  assertions: [
    {
      assertion_id: "as1",
      kind: "contact_address",
      value_norm: "diego.soto@farmadelta.example.cl",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
  ],
  contact_matches: [
    {
      value_norm: "diego.soto@farmadelta.example.cl",
      contact_point_id: "c56dabff-fc17-5a6b-a2f4-87a3826510cb",
      usage: "unattributed" as const,
      confirmation: "machine_proposed" as const,
      person_id: null,
      person_display_name: null,
      organization_id: null,
      organization_name: null,
    },
  ],
  organization_matches: [],
  domain_organization: null,
};

const NAMED_ORGANIZATION = {
  ...KNOWN_ADDRESS,
  source_record_id: "bbbbbbbb-2222-4222-8222-222222222222",
  dedupe_key: "gmail_message:m2",
  source_uri: "gmail://msg/m2",
  subject: "Solicitud de cotización — Especial septiembre",
  from_address: "usuariaparticular9@gmail.com",
  from_domain: "gmail.com",
  assertion_total: 2,
  assertions: [
    {
      assertion_id: "as2",
      kind: "contact_address",
      value_norm: "usuariaparticular9@gmail.com",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
    {
      assertion_id: "as3",
      kind: "organization_name",
      value_norm: "labsalud del sur",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
  ],
  contact_matches: [],
  organization_matches: [],
};

/** A Google Workspace drip: our own tooling talking to us, not a laboratory. */
const VENDOR_NOTICE = {
  ...KNOWN_ADDRESS,
  source_record_id: "cccccccc-3333-4333-8333-333333333333",
  dedupe_key: "gmail_message:m3",
  source_uri: "gmail://msg/m3",
  subject: "Alerta de seguridad",
  from_address: "no-reply@accounts.google.com",
  from_domain: "accounts.google.com",
  assertion_total: 0,
  assertions: [],
  contact_matches: [],
  organization_matches: [],
};

/** A real counterparty, answered by their mail server. */
const AUTO_REPLY = {
  ...KNOWN_ADDRESS,
  source_record_id: "dddddddd-4444-4444-8444-444444444444",
  dedupe_key: "gmail_message:m4",
  source_uri: "gmail://msg/m4",
  subject: "Feriado Legal Re: Cotización insumos",
  from_address: "compras@institutoaustral.example.cl",
  from_domain: "institutoaustral.example.cl",
  assertion_total: 0,
  assertions: [],
  contact_matches: [],
  organization_matches: [],
};

beforeEach(() => {
  vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(
    page([KNOWN_ADDRESS, NAMED_ORGANIZATION, VENDOR_NOTICE, AUTO_REPLY], 20) as never,
  );
  vi.mocked(fetchV2Contacts).mockResolvedValue(page([], 9460) as never);
  vi.mocked(fetchV2Organizations).mockResolvedValue(page([], 1812) as never);
  vi.mocked(fetchV2Prospects).mockResolvedValue(page([], 0) as never);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("EvidenceReviewPage", () => {
  it("asks only for pending records", async () => {
    render(<EvidenceReviewPage />);
    await waitFor(() => expect(fetchV2EvidenceRecords).toHaveBeenCalled());
    // Scoped to Gmail as well as to pending: the four bulk migration manifests are also
    // `pending` records, and they are an import artefact, not a message anyone reviews.
    expect(vi.mocked(fetchV2EvidenceRecords).mock.calls[0][0]).toMatchObject({
      sourceKind: "gmail_message",
      reviewStatus: "pending",
    });
  });

  it("draws the whole flow, including the steps that have no work in them", async () => {
    render(<EvidenceReviewPage />);
    const flow = await screen.findByTestId("review-flow");
    expect(flow.querySelectorAll("li")).toHaveLength(5);
    expect(flow.textContent).toContain("Evidencia");
    expect(flow.textContent).toContain("Marketing");
    expect(flow.textContent).toContain("Cotización");
  });

  it("counts from the server total, never from the rows on screen", async () => {
    render(<EvidenceReviewPage />);
    // Two rows are rendered and twenty are pending. Reporting 2 would understate the queue.
    expect(await screen.findByText("20")).toBeTruthy();
    expect(screen.getByText("9.460")).toBeTruthy();
    expect(screen.getByText("1.812")).toBeTruthy();
  });

  it("lists one row per record with its sender, subject and date", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    expect(screen.getAllByTestId("review-queue-row")).toHaveLength(2);
    expect(screen.getByText("diego.soto@farmadelta.example.cl")).toBeTruthy();
    expect(screen.getByText("Cotización insumos osmómetro")).toBeTruthy();
  });

  it("says an existing address is a known address, and never a settled person", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    expect(screen.getByText("Dirección conocida, sin titular registrado")).toBeTruthy();
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText("Sin titular registrado")).toBeTruthy();
    expect(screen.getByText("Sin institución atribuida")).toBeTruthy();
  });

  it("separates a domain hint from a confirmed organization", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText("Dominio sin registrar — pista, no evidencia")).toBeTruthy();
  });

  it("spells out what is unresolved for the open record", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("usuariaparticular9@gmail.com"));
    const flags = screen.getAllByTestId("review-flag").map((node) => node.textContent ?? "");
    expect(flags.some((text) => text.includes("no existe todavía en el CRM durable"))).toBe(true);
    expect(flags.some((text) => text.includes("correo personal"))).toBe(true);
    expect(flags.some((text) => text.includes("labsalud del sur"))).toBe(true);
  });

  it("shows the provenance of every record it displays", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText(/gmail:\/\/msg\/m1/)).toBeTruthy();
  });

  it("offers every action as a disabled preview with its reason", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    const actions = screen.getAllByTestId("review-preview-action");
    expect(actions.length).toBeGreaterThan(0);
    // Not one of them may be clickable. A live button here would be a second writer into
    // durable truth, through a proxy that allows no POST under /v2.
    for (const action of actions) {
      expect((action as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("shows all four review commands for the open record", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    for (const id of [
      "keep_evidence_pending",
      "confirm_organization",
      "create_organization",
      "attach_contact_address",
    ]) {
      expect(screen.getByTestId(`command-preview-${id}`)).toBeTruthy();
    }
  });

  it("blocks every command until a reason is written", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    for (const id of ["keep_evidence_pending", "create_organization"]) {
      expect(screen.getByTestId(`command-preview-${id}`).dataset.availability).toBe("blocked");
    }
    fireEvent.change(screen.getByTestId("command-note"), {
      target: { value: "revisado a mano" },
    });
    // The simplest decision becomes possible; promoting an organization this message never
    // names does not, because no reason can supply a name the evidence lacks.
    expect(screen.getByTestId("command-preview-keep_evidence_pending").dataset.availability).toBe(
      "available",
    );
    expect(screen.getByTestId("command-preview-create_organization").dataset.availability).toBe(
      "blocked",
    );
  });

  it("will not create an organization from a domain hint even with a reason", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    fireEvent.change(screen.getByTestId("command-note"), { target: { value: "porque si" } });
    const panel = screen.getByTestId("command-preview-create_organization");
    expect(panel.dataset.availability).toBe("blocked");
    expect(panel.textContent).toContain("pista de dominio");
  });

  it("keeps every command button disabled even when the data would allow it", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    fireEvent.change(screen.getByTestId("command-note"), { target: { value: "revisado" } });
    // "Available" describes the data, never the wire. Nothing on this page may send.
    expect(screen.getByTestId("command-preview-keep_evidence_pending").dataset.availability).toBe(
      "available",
    );
    for (const action of screen.getAllByTestId("review-preview-action")) {
      expect((action as HTMLButtonElement).disabled).toBe(true);
    }
    expect(screen.getAllByTestId("preview-only-reason")[0].textContent).toContain("proxy");
  });

  it("marks marketing unavailable and says permission is what is missing", async () => {
    render(<EvidenceReviewPage />);
    const marketing = await screen.findByTestId("review-marketing-section");
    expect(marketing.textContent).toContain("No disponible");
    expect(marketing.textContent).toContain("permiso");
  });

  it("marks quotes unavailable until a reviewed commercial record exists", async () => {
    render(<EvidenceReviewPage />);
    const quotes = await screen.findByTestId("review-quotes-section");
    expect(quotes.textContent).toContain("No disponible");
    expect(quotes.textContent).toContain("revisados");
  });

  it("says so when a record carries more observations than it shows", async () => {
    vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(
      page([{ ...KNOWN_ADDRESS, assertion_total: 11448 }], 1) as never,
    );
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    expect(screen.getByText(/Mostrando 1 de 11\.448 observaciones/)).toBeTruthy();
  });

  it("explains an empty queue instead of rendering a blank page", async () => {
    vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(page([], 0) as never);
    render(<EvidenceReviewPage />);
    expect((await screen.findByTestId("v2-empty-state")).textContent).toContain(
      "Sin evidencia pendiente",
    );
  });

  it("surfaces a load failure rather than an empty queue", async () => {
    vi.mocked(fetchV2EvidenceRecords).mockRejectedValue(new Error("boom"));
    render(<EvidenceReviewPage />);
    expect(await screen.findByText(/Revisión de evidencia/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("review-queue-table")).toBeNull());
  });
});

describe("the first triage of the queue", () => {
  it("opens on the commercially useful batch, not on the whole queue", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    const rows = screen.getAllByTestId("review-queue-row");
    expect(rows).toHaveLength(2);
    const text = rows.map((row) => row.textContent ?? "").join(" ");
    expect(text).toContain("diego.soto@farmadelta.example.cl");
    expect(text).not.toContain("no-reply@accounts.google.com");
  });

  it("shows the size of every batch, including the ones it is not showing", async () => {
    render(<EvidenceReviewPage />);
    const strip = await screen.findByTestId("review-triage-filter");
    // Hiding the count of what is filtered out would make the filter look like a deletion.
    expect(strip.textContent).toContain("Correspondencia comercial");
    expect(strip.textContent).toContain("Respuesta automática de contraparte");
    expect(strip.textContent).toContain("Aviso de proveedor o seguridad");
    expect(strip.textContent).toContain("Sin clasificar");
  });

  it("reaches the filtered-out records rather than dropping them", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByTestId("review-triage-tab-vendor_notice"));
    const rows = screen.getAllByTestId("review-queue-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("no-reply@accounts.google.com");
  });

  it("can show the whole queue unfiltered", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByTestId("review-triage-tab-all"));
    expect(screen.getAllByTestId("review-queue-row")).toHaveLength(4);
  });

  it("puts the suggested category on the row", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    const chips = screen.getAllByTestId("review-triage-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent).toContain("Correspondencia comercial");
  });

  it("shows why the category was suggested, for the open record", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    const reasons = await screen.findAllByTestId("review-triage-reason");
    expect(reasons.length).toBeGreaterThan(0);
    expect(reasons.map((row) => row.textContent ?? "").join(" ")).toContain("cotiz");
  });

  it("says the suggestion is a reading aid and records nothing", async () => {
    render(<EvidenceReviewPage />);
    const note = await screen.findByTestId("review-triage-disclaimer");
    expect(note.textContent).toMatch(/no (se guarda|cambia|registra)/i);
  });

  it("keeps an empty batch explained rather than blank", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByTestId("review-triage-tab-unclassified"));
    expect(screen.queryAllByTestId("review-queue-row")).toHaveLength(0);
    expect(screen.getByTestId("review-triage-empty")).toBeTruthy();
  });

  it("still enables nothing: the triage does not unlock a command", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    await screen.findAllByTestId("review-triage-reason");
    const actions = screen.getAllByTestId("review-preview-action");
    expect(actions.length).toBeGreaterThan(0);
    for (const action of actions) {
      expect(action.hasAttribute("disabled")).toBe(true);
    }
  });
});

describe("what the workspace refuses to infer about an institution", () => {
  it("says in words that a domain never names an institution by itself", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    const rule = await screen.findByTestId("review-organization-rule");
    expect(rule.textContent).toMatch(/dominio/i);
    expect(rule.textContent).toMatch(/exacta|registrado/i);
  });
});

describe("what the workspace refuses to offer about a person", () => {
  /**
   * The failure this guards against is a wording one, and it is the expensive kind: a
   * reviewer who reads "confirmar persona" on a screen concludes the product can settle
   * who uses an address. It cannot -- no command writes `crm.person`, and the table is
   * empty -- so the words must not exist, anywhere, in any conjugation.
   */
  it("never names confirming a person as something that can be done", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/confirmar\s+(la\s+)?persona/i);
    expect(text).not.toMatch(/persona\s+confirmada/i);
    expect(text).not.toMatch(/identificar\s+(a\s+)?(la\s+)?persona/i);
  });

  it("keeps the address, the institution and the evidence as three separate readings", async () => {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("diego.soto@farmadelta.example.cl"));
    // The address is read as a channel, the institution as an attribution of its own, and
    // the message as provenance. None of the three stands in for another.
    expect(screen.getByText("Dirección conocida, sin titular registrado")).toBeTruthy();
    expect(screen.getByText("Sin institución atribuida")).toBeTruthy();
    expect(screen.getByTestId("review-organization-rule")).toBeTruthy();
  });
});

// ------------------------------------------- choosing which institution the sender belongs to
//
// The shape of the real pair in the queue, with reserved values: one message naming a
// manufacturer *and* the university the equipment is for, with one sender. The four single
// commands refuse such a record by rule; this is the surface for saying which name is the
// sender's without discarding the other.

const TWO_INSTITUTIONS = {
  ...KNOWN_ADDRESS,
  source_record_id: "eeeeeeee-5555-4555-8555-555555555555",
  dedupe_key: "gmail_message:m5",
  source_uri: "gmail://msg/m5",
  subject: "[Universidad Ejemplo] Ultrasonics Ejemplo: su solicitud de cotización",
  from_address: "ventas@proveedor.example",
  from_domain: "proveedor.example",
  assertion_total: 3,
  assertions: [
    {
      assertion_id: "as-addr",
      kind: "contact_address",
      value_norm: "ventas@proveedor.example",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
    {
      assertion_id: "as-new",
      kind: "organization_name",
      value_norm: "ultrasonics ejemplo",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
    {
      assertion_id: "as-existing",
      kind: "organization_name",
      value_norm: "universidad ejemplo",
      resolution: "unresolved",
      resolved_kind: null,
      resolved_id: null,
      ambiguity_note: null,
    },
  ],
  contact_matches: [],
  organization_matches: [
    {
      value_norm: "universidad ejemplo",
      organization_id: "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      name: "Universidad Ejemplo",
      confirmation: "machine_proposed" as const,
    },
  ],
};

describe("a message that names two institutions and has one sender", () => {
  beforeEach(() => {
    vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(page([TWO_INSTITUTIONS], 1) as never);
  });

  /** The attribution preview's own card. Other previews render requests of their own. */
  function attributionCard() {
    return screen.getByTestId("command-preview-attribute_sender_organization");
  }

  async function openTheRecord() {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("ventas@proveedor.example"));
    fireEvent.change(screen.getByTestId("command-note"), {
      target: { value: "el remitente es el fabricante" },
    });
  }

  /** Pick the nth asserted institution as the sender's — by its own control, not by index. */
  function chooseInstitution(nth: number) {
    const choice = screen.getAllByTestId("sender-institution-choice")[nth];
    fireEvent.click(within(choice).getByRole("radio"));
  }

  /** Say what the address is to that institution. There is no default, so it must be said. */
  function chooseRelationship(label: string | RegExp) {
    const choice = screen
      .getAllByTestId("address-relationship-choice")
      .find((candidate) =>
        typeof label === "string"
          ? (candidate.textContent ?? "").includes(label)
          : label.test(candidate.textContent ?? ""),
      );
    if (!choice) throw new Error(`no relationship choice matching ${label}`);
    fireEvent.click(within(choice).getByRole("radio"));
  }

  it("offers both names, and says what each one would do", async () => {
    await openTheRecord();
    const choices = screen.getAllByTestId("sender-institution-choice");
    expect(choices).toHaveLength(2);
    const text = choices.map((choice) => choice.textContent).join(" ");
    expect(text).toContain("«ultrasonics ejemplo»");
    expect(text).toContain("se crearía");
    expect(text).toContain("«universidad ejemplo»");
    expect(text).toContain("se confirmaría");
  });

  it("will not attribute anything until the operator picks one", async () => {
    await openTheRecord();
    const attribution = screen.getByTestId("command-preview-attribute_sender_organization");
    expect(attribution.dataset.availability).toBe("blocked");
    expect(attribution.textContent).toContain("2 instituciones");
    expect(within(attribution).queryByTestId("command-request")).toBeNull();
  });

  it("shows the exact request once the operator picks the sender's institution", async () => {
    await openTheRecord();
    chooseInstitution(0);
    chooseRelationship("Buzón compartido");
    const attribution = screen.getByTestId("command-preview-attribute_sender_organization");
    expect(attribution.dataset.availability).toBe("available");

    const request = JSON.parse(
      within(attributionCard()).getByTestId("command-request").querySelector("pre")!.textContent!,
    );
    expect(request).toEqual({
      source_record_id: TWO_INSTITUTIONS.source_record_id,
      note: "el remitente es el fabricante",
      organization_assertion_id: "as-new",
      address_assertion_id: "as-addr",
      usage: "shared_mailbox",
      target: "new",
      kind: "unknown",
    });
  });

  it("says out loud which institution it is leaving unresolved", async () => {
    await openTheRecord();
    chooseInstitution(0);
    chooseRelationship("Buzón compartido");
    expect(within(attributionCard()).getByTestId("command-leaves-unresolved").textContent).toContain(
      "«universidad ejemplo» queda sin resolver",
    );
  });

  it("switches to confirming when the chosen name already exists", async () => {
    await openTheRecord();
    chooseInstitution(1);
    chooseRelationship("Buzón compartido");
    const request = JSON.parse(
      within(attributionCard()).getByTestId("command-request").querySelector("pre")!.textContent!,
    );
    expect(request.target).toBe("existing");
    expect(request.organization_id).toBe("11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    expect(request.organization_assertion_id).toBe("as-existing");
    expect(within(attributionCard()).getByTestId("command-leaves-unresolved").textContent).toContain(
      "«ultrasonics ejemplo» queda sin resolver",
    );
  });

  it("still sends nothing: every button stays disabled with a complete request on screen", async () => {
    await openTheRecord();
    chooseInstitution(0);
    chooseRelationship("Buzón compartido");
    expect(within(attributionCard()).getByTestId("command-request")).toBeTruthy();
    for (const action of screen.getAllByTestId("review-preview-action")) {
      expect((action as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("never offers to settle who uses the address, whichever institution is chosen", async () => {
    await openTheRecord();
    chooseInstitution(0);
    chooseRelationship("Buzón compartido");
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/confirmar\s+(la\s+)?persona/i);
    expect(text).toContain("No crea persona ni afiliación");
  });

  // ----------------------------------------------------------------------------------------
  // The relationship control itself: it preselects nothing, and it says in the surface what
  // the neutral option does and does not claim.

  it("offers both relationships and marks neither", async () => {
    await openTheRecord();
    const choices = screen.getAllByTestId("address-relationship-choice");
    expect(choices).toHaveLength(2);
    for (const choice of choices) {
      expect((within(choice).getByRole("radio") as HTMLInputElement).checked).toBe(false);
    }
  });

  it("explains that the neutral option creates no person and names no owner", async () => {
    await openTheRecord();
    const neutral = screen
      .getAllByTestId("address-relationship-choice")
      .find((choice) => (choice.textContent ?? "").includes("sin identificar"))!;
    expect(neutral.textContent).toContain("No crea ninguna persona");
    expect(neutral.textContent).toContain("no dice de quién es la casilla");
    expect(neutral.textContent).toContain("no afirma que sea compartida");
  });

  it("blocks the attribution while no relationship is chosen, even with an institution", async () => {
    await openTheRecord();
    chooseInstitution(0);
    expect(attributionCard().dataset.availability).toBe("blocked");
    expect(attributionCard().textContent).toContain("no hay opción por omisión");
  });

  it("asks for no justification when the sender's address is a recognised desk", async () => {
    // This record's sender is `ventas@proveedor.example`.
    await openTheRecord();
    chooseInstitution(0);
    chooseRelationship("Buzón compartido");
    expect(screen.queryByTestId("shared-mailbox-override")).toBeNull();
    expect(attributionCard().dataset.availability).toBe("available");
  });

  it("keeps every button disabled whatever the operator chooses", async () => {
    await openTheRecord();
    chooseInstitution(0);
    chooseRelationship("sin identificar");
    expect(attributionCard().dataset.availability).toBe("available");
    for (const action of screen.getAllByTestId("review-preview-action")) {
      expect((action as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("sends the neutral relationship in the request when it is the one chosen", async () => {
    await openTheRecord();
    chooseInstitution(0);
    chooseRelationship("sin identificar");
    const request = JSON.parse(
      within(attributionCard()).getByTestId("command-request").querySelector("pre")!.textContent!,
    );
    expect(request.usage).toBe("individual_owner_unknown");
    expect(request).not.toHaveProperty("shared_mailbox_override_note");
  });
});

describe("a message whose sender address looks like a person's", () => {
  const NAMED_SENDER = {
    ...TWO_INSTITUTIONS,
    source_record_id: "33333333-cccc-4ccc-8ccc-cccccccccccc",
    from_address: "p.morales@proveedor.example",
    assertions: TWO_INSTITUTIONS.assertions.map((assertion) =>
      assertion.kind === "contact_address"
        ? { ...assertion, value_norm: "p.morales@proveedor.example" }
        : assertion,
    ),
  };

  beforeEach(() => {
    vi.mocked(fetchV2EvidenceRecords).mockResolvedValue(page([NAMED_SENDER], 1) as never);
  });

  async function openAndChoose(relationship: string) {
    render(<EvidenceReviewPage />);
    await screen.findByTestId("review-queue-table");
    fireEvent.click(screen.getByText("p.morales@proveedor.example"));
    fireEvent.change(screen.getByTestId("command-note"), {
      target: { value: "el remitente es el fabricante" },
    });
    fireEvent.click(within(screen.getAllByTestId("sender-institution-choice")[0]).getByRole("radio"));
    const choice = screen
      .getAllByTestId("address-relationship-choice")
      .find((candidate) => (candidate.textContent ?? "").includes(relationship))!;
    fireEvent.click(within(choice).getByRole("radio"));
  }

  function attributionCard() {
    return screen.getByTestId("command-preview-attribute_sender_organization");
  }

  it("refuses to call it a shared mailbox, and says what to choose instead", async () => {
    await openAndChoose("Buzón compartido");
    expect(attributionCard().dataset.availability).toBe("blocked");
    expect(attributionCard().textContent).toContain("varias personas la leen");
    expect(within(attributionCard()).queryByTestId("command-request")).toBeNull();
  });

  it("asks how the operator knows, and accepts the claim once they say", async () => {
    await openAndChoose("Buzón compartido");
    fireEvent.change(screen.getByTestId("shared-mailbox-override"), {
      target: { value: "la secretaria y el jefe de laboratorio la responden" },
    });
    expect(attributionCard().dataset.availability).toBe("available");
    const request = JSON.parse(
      within(attributionCard()).getByTestId("command-request").querySelector("pre")!.textContent!,
    );
    expect(request.usage).toBe("shared_mailbox");
    expect(request.shared_mailbox_override_note).toBe(
      "la secretaria y el jefe de laboratorio la responden",
    );
  });

  it("needs nothing extra for the neutral relationship", async () => {
    await openAndChoose("sin identificar");
    expect(screen.queryByTestId("shared-mailbox-override")).toBeNull();
    expect(attributionCard().dataset.availability).toBe("available");
  });

  it("still sends nothing, whichever way the operator resolves it", async () => {
    await openAndChoose("sin identificar");
    for (const action of screen.getAllByTestId("review-preview-action")) {
      expect((action as HTMLButtonElement).disabled).toBe(true);
    }
  });
});
