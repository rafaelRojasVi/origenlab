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
  contactCard,
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
        {
          contact_point_id: CONTACT_ID,
          address: "compras@instituto.invalid",
          usage: "shared_mailbox" as const,
          confirmation: "machine_proposed" as const,
          person_id: null,
          person_display_name: null,
          organization_id: ORG_ID,
          organization_name: "Instituto Ficticio de Metrología",
          created_at: null,
        },
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
  it("shows a channel with no owner as a pending channel rather than as a name", async () => {
    render(<Contact360Page />);
    const row = await screen.findByText("Canal pendiente");
    expect(row).toBeTruthy();
    expect(screen.getByText("sin persona")).toBeTruthy();
  });

  it("counts from the server total, not from the rows on screen", async () => {
    render(<Contact360Page />);
    expect((await screen.findByTestId("contact-360-footer")).textContent).toContain("9.460");
  });

  it("opens a contact as a deep link, so the screen can be shared and reopened", async () => {
    render(<Contact360Page />);
    fireEvent.click(await screen.findByText("Canal pendiente"));
    expect(window.location.hash).toBe(`#/contactos?id=${CONTACT_ID}`);
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
    expect(screen.getByText(/Canal pendiente/)).toBeTruthy();
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

  it("attaches every case its institution is part of, with the part it holds", async () => {
    vi.mocked(fetchV2OrganizationCases).mockResolvedValue(
      page([organizationCaseWithRoles([{ role: "supplier" }])]) as never,
    );
    vi.mocked(fetchV2QuotesToFollowUp).mockResolvedValue(page([listedQuote()]) as never);
    await open();
    await waitFor(() => {
      expect(screen.getByTestId("case-summary-card")).toBeTruthy();
    });
    expect(within(screen.getByTestId("contact-360-cases")).getByText("Caso ficticio 1")).toBeTruthy();
    // The institution supplies here and asks in nothing. The old browser-side join over
    // `/v2/cases` showed this contact no cases at all.
    expect(screen.getByTestId("contact-360-case-role").textContent).toContain("Proveedor");
    expect(vi.mocked(fetchV2OrganizationCases).mock.calls[0][0]).toBe(ORG_ID);
    expect(within(screen.getByTestId("contact-360-quotes")).getByText(/1235/)).toBeTruthy();
  });

  it("says a zero quote count is unmigrated history, not an absence of business", async () => {
    await open();
    expect(screen.getByTestId("contact-360-quotes").textContent).toContain(
      "todavía no se migra",
    );
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
    const rows = within(screen.getByTestId("contact-360-activity")).getAllByRole("listitem");
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
