import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CaseThreadsResponse } from "../crmTypes";

// Every value below is invented; the repository is public.
const RESPONSE: CaseThreadsResponse = {
  opportunity_id: "case-1",
  threads: [
    {
      thread_id: "t1", subject: "Cotización balanza", kind: "cliente", count: 5, last_at: "2026-09-05T12:00:00Z",
      url: "https://mail.google.com/mail/u/0/#all/m5",
      messages: [5, 4, 3, 2, 1].map((n) => ({
        message_id: `m${n}`, sent_at: `2026-09-0${n}T12:00:00Z`, direction: n % 2 ? "outbound" : "inbound",
        from: n % 2 ? "Equipo OrigenLab" : "Ana Ficticia", subject: "Cotización balanza", has_pdf: n === 1,
        url: `https://mail.google.com/mail/u/0/#all/m${n}`,
      })),
    },
    {
      thread_id: "t2", subject: "Precio equipo", kind: "proveedor", count: 1, last_at: "2026-09-01T12:00:00Z",
      url: "https://mail.google.com/mail/u/0/#all/s1",
      messages: [{ message_id: "s1", sent_at: "2026-09-01T12:00:00Z", direction: "inbound", from: "fabricante-ficticio.example",
        subject: "Precio equipo", has_pdf: false, url: "https://mail.google.com/mail/u/0/#all/s1" }],
    },
  ],
};

vi.mock("../crmApi", () => ({ fetchCaseThreads: vi.fn(async () => RESPONSE) }));

describe("Correos del caso as cadenas", () => {
  it("shows each cadena with its party, count and newest three emails, and opens the rest on request", async () => {
    const { CaseThreads } = await import("./CaseThreads");
    render(<CaseThreads opportunityId="case-1" />);
    const threads = await screen.findAllByTestId("case-thread");
    expect(threads).toHaveLength(2);
    const client = threads[0];
    expect(client).toHaveTextContent("Cotización balanza");
    expect(client).toHaveTextContent("Cliente");
    expect(client).toHaveTextContent("5 correos");
    expect(within(client).getAllByRole("listitem")).toHaveLength(3);
    expect(within(client).getByRole("link", { name: /Abrir la cadena «Cotización balanza» en Gmail/ }).getAttribute("href"))
      .toContain("authuser=contacto");
    fireEvent.click(within(client).getByRole("button", { name: "Ver todo (5)" }));
    expect(within(client).getAllByRole("listitem")).toHaveLength(5);
    expect(within(client).getByText("PDF")).toBeInTheDocument();
    expect(threads[1]).toHaveTextContent("Proveedor");
    expect(threads[1]).toHaveTextContent("fabricante-ficticio.example");
    // When matters (owner 2026-10-12): each email shows its hour, not only the day.
    expect(threads[1]).toHaveTextContent(/01 sept 2026, \d{2}:\d{2}/);
  });
});
