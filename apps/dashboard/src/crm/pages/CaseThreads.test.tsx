import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CaseMessage, CaseThreadsResponse } from "../crmTypes";

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
        subject: "Precio equipo", has_pdf: false, url: "https://mail.google.com/mail/u/0/#all/s1",
        person_guesses: [{ guess_id: "g1:p1", address: "x@fabricante-ficticio.example", person_id: "p1",
          person_name: "Pedro Soto", organization_name: "Universidad Ficticia", reasons: ["mismo apellido"],
          question_es: "¿Es Pedro Soto (Universidad Ficticia)?" }] }],
    },
  ],
};

const MESSAGE: CaseMessage = {
  message_id: "m4", subject: "Cotización balanza", from: "Ana Ficticia", to: ["Ventas"], cc: [],
  sent_at: "2026-09-04T12:00:00Z", text: "Gracias, ¿incluye calibración?", truncated: false,
  quoted: "El jue, 3 sept 2026, Ventas escribió:\n> Adjunto cotización", attachments: [{ filename: "Plano.pdf", size: 10 }],
};
const fetchCaseMessage = vi.fn(async (_case: string, _id: string) => MESSAGE);

vi.mock("../crmApi", () => ({
  fetchCaseThreads: vi.fn(async () => RESPONSE),
  fetchCaseMessage: (c: string, id: string) => fetchCaseMessage(c, id),
}));

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op-1", email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true,
  };
}

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

  it("opens an email in place, quoted conversation folded, one at a time", async () => {
    const { CaseThreads } = await import("./CaseThreads");
    render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <CaseThreads opportunityId="case-1" />
      </AuthSessionContext.Provider>,
    );
    const [client] = await screen.findAllByTestId("case-thread");
    const line = within(client).getAllByRole("button", { name: /Ana Ficticia/ })[0];
    expect(line).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(line);
    const mail = await within(client).findByTestId("mail-message");
    expect(fetchCaseMessage).toHaveBeenCalledWith("case-1", "m4");
    expect(mail).toHaveTextContent("Gracias, ¿incluye calibración?");
    expect(mail).toHaveTextContent("Plano.pdf");
    expect(mail).not.toHaveTextContent("Adjunto cotización");
    fireEvent.click(within(mail).getByRole("button", { name: /Ver conversación anterior/ }));
    expect(mail).toHaveTextContent("Adjunto cotización");
    expect(within(client).getByRole("link", { name: "Responder en Gmail ↗" }).getAttribute("href")).toContain("#all/m4");
    fireEvent.click(line);
    expect(within(client).queryByTestId("mail-message")).toBeNull();
  });

  it("a role that does not read addresses keeps the Gmail link", async () => {
    const { CaseThreads } = await import("./CaseThreads");
    render(
      <AuthSessionContext.Provider value={{ session: session("viewer"), signOut: async () => true }}>
        <CaseThreads opportunityId="case-1" />
      </AuthSessionContext.Provider>,
    );
    const [client] = await screen.findAllByTestId("case-thread");
    expect(within(client).queryAllByRole("button", { name: /Ana Ficticia/ })).toHaveLength(0);
    expect(within(client).getAllByRole("link").length).toBeGreaterThan(1);
  });

  it("shows the «¿Es …?» guess under the sender, and «Sí» posts the answer and clears it", async () => {
    const posts: { path: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      posts.push({ path: new URL(url, "http://localhost").pathname, body: JSON.parse(String(init?.body ?? "{}")) });
      return Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
    const { CaseThreads } = await import("./CaseThreads");
    render(
      <AuthSessionContext.Provider value={{ session: session("sales"), signOut: async () => true }}>
        <CaseThreads opportunityId="case-1" />
      </AuthSessionContext.Provider>,
    );
    const threads = await screen.findAllByTestId("case-thread");
    const guess = within(threads[1]).getByTestId("person-guess");
    expect(guess).toHaveTextContent("¿Es Pedro Soto (Universidad Ficticia)?");
    expect(within(threads[0]).queryByTestId("person-guess")).toBeNull();
    fireEvent.click(within(guess).getByRole("button", { name: "Sí" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/v2/commands/answer-person-guess");
    expect(posts[0].body).toMatchObject({ guess_id: "g1:p1", answer: "si" });
    await waitFor(() => expect(within(threads[1]).queryByTestId("person-guess")).toBeNull());
    vi.unstubAllGlobals();
  });
});
