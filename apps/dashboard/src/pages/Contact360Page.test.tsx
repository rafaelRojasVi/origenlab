import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Contact360Page } from "./Contact360Page";

vi.mock("../api/v2Client", () => ({
  fetchV2Contacts: vi.fn(),
  fetchV2ContactCard: vi.fn(),
  fetchV2OrganizationCases: vi.fn(),
  fetchV2QuotesToFollowUp: vi.fn(),
}));

import {
  fetchV2OrganizationCases,
  fetchV2ContactCard,
  fetchV2Contacts,
  fetchV2QuotesToFollowUp,
} from "../api/v2Client";
import {
  addressControl,
  cardEvidence,
  connectedCase,
  connectedQuote,
  contactCard,
  emptyConnectionSummary,
  listedContact,
  listedQuote,
  organizationCaseWithRoles,
} from "../lib/__fixtures__/crm360";

const CONTACT_ID = "96301691-af05-51ea-82e3-05f5fae40837";
const ORG_ID = "11111111-2222-4333-8444-555555555555";

const page = <T,>(items: T[], total = items.length) => ({
  items,
  total,
  limit: 50,
  offset: 0,
});

beforeEach(() => {
  window.location.hash = "";
  vi.mocked(fetchV2Contacts).mockResolvedValue(
    page(
      [
        listedContact({
          usage: "shared_mailbox",
          organization_id: ORG_ID,
          organization_name: "Instituto Ficticio de Metrología",
        }),
      ],
      9460,
    ) as never,
  );
  vi.mocked(fetchV2ContactCard).mockResolvedValue(contactCard());
  vi.mocked(fetchV2OrganizationCases).mockResolvedValue(page([]) as never);
  vi.mocked(fetchV2QuotesToFollowUp).mockResolvedValue(page([]) as never);
});

afterEach(() => {
  window.location.hash = "";
  vi.resetAllMocks();
});

describe("the contact list", () => {
  it("shows a channel with no owner as its address and its recorded identity, never a name", async () => {
    render(<Contact360Page />);
    const table = await screen.findByTestId("contact-360-table");
    expect(within(table).getByText("compras@instituto.invalid")).toBeTruthy();
    expect(within(table).getByText("Buzón de institución")).toBeTruthy();
    expect(within(table).getByText("Instituto Ficticio de Metrología")).toBeTruthy();
  });

  it("tells a confirmed person, an institution's mailbox and an unattributed address apart", async () => {
    vi.mocked(fetchV2Contacts).mockResolvedValue(
      page([
        listedContact({
          contact_point_id: "c-1",
          address: "maria@instituto.invalid",
          person_id: "p-1",
          person_display_name: "María Ficticia",
          organization_id: ORG_ID,
          organization_name: "Instituto Ficticio de Metrología",
          case_count: 2,
        }),
        listedContact({
          contact_point_id: "c-2",
          address: "otro@instituto.invalid",
          address_control_count: 1,
        }),
      ]) as never,
    );
    render(<Contact360Page />);
    const table = await screen.findByTestId("contact-360-table");
    const rows = within(table).getAllByRole("listitem");
    expect(rows[0].textContent).toContain("María Ficticia");
    expect(rows[0].textContent).toContain("Persona confirmada");
    expect(rows[0].textContent).toContain("2 casos");
    // Same domain as the institution, and still nobody's: nothing is read from the domain.
    expect(rows[1].textContent).toContain("Dirección sin atribuir");
    expect(rows[1].textContent).toContain("Sin institución registrada");
    expect(rows[1].textContent).toContain("1 control");
  });

  it("sends the identity filter, the case toggle and the search to the server", async () => {
    render(<Contact360Page />);
    await screen.findByTestId("contact-360-table");
    fireEvent.click(screen.getByRole("button", { name: "Persona confirmada" }));
    await waitFor(() => {
      const calls = vi.mocked(fetchV2Contacts).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({ identity: "person" });
    });
    fireEvent.click(screen.getByLabelText("Sólo con participación en casos"));
    fireEvent.change(screen.getByPlaceholderText("correo, persona o institución"), {
      target: { value: "Instituto" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => {
      const calls = vi.mocked(fetchV2Contacts).mock.calls;
      expect(calls[calls.length - 1][0]).toMatchObject({
        identity: "person",
        withCases: true,
        q: "Instituto",
      });
    });
  });

  it("counts from the server total, not from the rows on screen", async () => {
    render(<Contact360Page />);
    expect((await screen.findByTestId("contact-360-footer")).textContent).toContain("9.460");
  });

  it("opens a contact as a deep link, so the screen can be shared and reopened", async () => {
    render(<Contact360Page />);
    fireEvent.click(await screen.findByText("compras@instituto.invalid"));
    expect(window.location.hash).toBe(`#/contactos?id=${CONTACT_ID}`);
    // Let the detail the hash opened mount inside this test, so its fetches never run
    // after the mocks are reset.
    await screen.findByTestId("contact-360-detail");
    // The institution's cases are fetched only once the card has loaded; wait for that
    // too, or under load it starts after `vi.resetAllMocks()` and reads `undefined`.
    await waitFor(() => expect(fetchV2OrganizationCases).toHaveBeenCalled());
  });
});

describe("Contacto 360", () => {
  function open() {
    window.location.hash = `#/contactos?id=${CONTACT_ID}`;
    render(<Contact360Page />);
    return screen.findByTestId("contact-360-detail");
  }

  it("leads with the pending-channel state and says why nobody is named", async () => {
    await open();
    const note = screen.getByTestId("contact-360-pending-note");
    expect(note.textContent).toContain("buzón de rol");
    expect(screen.getByText("Nadie identificado todavía")).toBeTruthy();
    expect(screen.getByText("Buzón de institución")).toBeTruthy();
  });

  it("never claims marketing permission from having received mail", async () => {
    vi.mocked(fetchV2ContactCard).mockResolvedValue(
      contactCard({ address_controls: [addressControl({ control_kind: "prior_contact" })] }),
    );
    await open();
    const panel = screen.getByTestId("contact-360-marketing");
    expect(panel.textContent).toContain("Sin permiso registrado");
    expect(panel.textContent).toContain("no un consentimiento");
  });

  it("shows a suppression as the loudest thing on the marketing panel", async () => {
    vi.mocked(fetchV2ContactCard).mockResolvedValue(
      contactCard({
        address_controls: [
          addressControl({
            control_kind: "block",
            purpose: "all",
            source: "unsubscribe_handler",
            reason: "Pidió la baja",
          }),
        ],
      }),
    );
    await open();
    expect(
      within(screen.getByTestId("contact-360-marketing")).getByText(
        "No contactar por ningún motivo",
      ),
    ).toBeTruthy();
  });

  it("lists its institution's cases apart, with the part the institution holds", async () => {
    vi.mocked(fetchV2OrganizationCases).mockResolvedValue(
      page([organizationCaseWithRoles([{ role: "supplier" }])]) as never,
    );
    vi.mocked(fetchV2QuotesToFollowUp).mockResolvedValue(page([listedQuote()]) as never);
    await open();
    await waitFor(() => {
      expect(screen.getByTestId("case-summary-card")).toBeTruthy();
    });
    const orgCases = screen.getByTestId("contact-360-organization-cases");
    expect(within(orgCases).getByText("Caso ficticio 1")).toBeTruthy();
    // The institution supplies here and asks in nothing. The old browser-side join over
    // `/v2/cases` showed this contact no cases at all.
    expect(screen.getByTestId("contact-360-case-role").textContent).toContain("Proveedor");
    expect(vi.mocked(fetchV2OrganizationCases).mock.calls[0][0]).toBe(ORG_ID);
    // The contact's own participation is a different fact, said as not recorded yet.
    expect(screen.getByTestId("contact-360-cases").textContent).toContain(
      "No hay participación registrada todavía",
    );
  });

  it("shows the cases it participates in with the person's part, even with no requesting institution", async () => {
    vi.mocked(fetchV2ContactCard).mockResolvedValue(
      contactCard({
        organization_id: null,
        organization_name: null,
        cases: [connectedCase({ roles: ["technical", "quote_recipient"] })],
        quotes: [connectedQuote()],
        connection_summary: emptyConnectionSummary({ cases: 1, open_cases: 1, quotes: 1 }),
      }),
    );
    await open();
    const cases = screen.getByTestId("contact-360-cases");
    expect(cases.textContent).toContain("Caso ficticio 1");
    expect(cases.textContent).toContain("Contacto técnico");
    expect(cases.textContent).toContain("Recibe la cotización");
    expect(cases.textContent).toContain("Pide: no registrado todavía");
    expect(cases.textContent).not.toContain("No hay participación");
    expect(screen.queryByTestId("contact-360-organization-cases")).toBeNull();
    expect(screen.getByTestId("contact-360-quotes").textContent).toContain("1235 · rev. 2 · Enviada");
  });

  it("says a zero quote count is unmigrated history, not an absence of business", async () => {
    await open();
    expect(screen.getByTestId("contact-360-quotes").textContent).toContain("aún no se migra");
  });

  it("keeps ids, vocabulary and provenance in a drawer that starts closed", async () => {
    await open();
    const drawer = screen.getByTestId("v2-technical-details") as HTMLDetailsElement;
    expect(drawer.open).toBe(false);
    expect(drawer.textContent).toContain(CONTACT_ID);
    expect(drawer.textContent).toContain("shared_mailbox");
  });

  it("reads activity newest first", async () => {
    vi.mocked(fetchV2ContactCard).mockResolvedValue(
      contactCard({
        evidence: [
          cardEvidence({
            assertion_id: "a-old",
            value_norm: "la observación antigua",
            observed_at: "2026-09-01T12:00:00Z",
          }),
          cardEvidence({
            assertion_id: "a-new",
            value_norm: "la observación reciente",
            observed_at: "2026-09-20T12:00:00Z",
          }),
        ],
        counts: { evidence: 2 },
      }),
    );
    await open();
    const rows = within(screen.getByTestId("contact-360-evidence")).getAllByRole("listitem");
    expect(rows[0].textContent).toContain("la observación reciente");
    expect(rows[1].textContent).toContain("la observación antigua");
  });

  it("reports a failed load instead of drawing an empty contact", async () => {
    vi.mocked(fetchV2ContactCard).mockRejectedValue(new Error("boom"));
    window.location.hash = `#/contactos?id=${CONTACT_ID}`;
    render(<Contact360Page />);
    await waitFor(() => {
      expect(screen.getAllByText(/Ficha de contacto/).length).toBeGreaterThan(0);
    });
    expect(screen.queryByTestId("contact-360-detail")).toBeNull();
  });
});
