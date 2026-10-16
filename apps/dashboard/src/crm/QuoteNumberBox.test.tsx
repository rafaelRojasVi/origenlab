import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuoteNumberBox } from "./QuoteNumberBox";
import type { PipelineResponse } from "./crmTypes";
import type { ResourceState } from "./useResource";

// Every value is invented; the repository is public.
const NOW = new Date("2026-03-31T15:00:00Z");
const EMPTY: ResourceState<PipelineResponse> = {
  kind: "ready",
  data: { items: [], total: 0, drive_configured: true } as unknown as PipelineResponse,
};

function respond(numbering: unknown) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      calls.push(path);
      if (path === "/v2/quotes/numbering" && numbering !== undefined) {
        if (numbering instanceof Response) return Promise.resolve(numbering);
        return Promise.resolve(new Response(JSON.stringify(numbering), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return calls;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("QuoteNumberBox · numeración del servidor", () => {
  it("takes «Siguiente» from the server when numbering is configured", async () => {
    const calls = respond({ configured: true, seed: 42, next_number: "00042-26", highest_known: 41, ignored_mail_numbers: [] });
    render(<QuoteNumberBox pipeline={EMPTY} compact />);
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("CN00042"));
    const next = screen.getByTestId("quote-next");
    expect(next).toHaveTextContent("00042-26");
    expect(next).toHaveTextContent("La asigna el sistema al crear la cotización");
    expect(next).not.toHaveTextContent(/sin configurar/i);
    expect(calls.filter((p) => p === "/v2/quotes/numbering")).toHaveLength(1);
  });

  it("shows the server's highest known number as «Último» when it is larger", async () => {
    respond({ configured: true, seed: 42, next_number: "00043-26", highest_known: 42, ignored_mail_numbers: [] });
    render(<QuoteNumberBox pipeline={EMPTY} />);
    await waitFor(() => expect(screen.getByTestId("quote-last")).toHaveTextContent("CN00042"));
    expect(screen.getByTestId("quote-last")).not.toHaveTextContent("-26");
  });

  it("ignores a highest_known that is smaller than the client-side «Último»", async () => {
    const pipeline = {
      kind: "ready",
      data: {
        items: [{ organization: { name: "Laboratorio Ejemplo" }, quotes: [{ quote_number: "01020-26" }] }],
        total: 1,
        drive_configured: true,
      },
    } as unknown as ResourceState<PipelineResponse>;
    respond({ configured: true, seed: 5, next_number: "00006-26", highest_known: 5, ignored_mail_numbers: [] });
    render(<QuoteNumberBox pipeline={pipeline} />);
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("CN00006"));
    expect(screen.getByTestId("quote-last")).toHaveTextContent("01020-26");
    expect(screen.getByTestId("quote-last")).not.toHaveTextContent("CN00005");
  });

  it("says so, with nothing to copy, when the series is exhausted", async () => {
    respond({ configured: true, seed: 1, next_number: null, highest_known: 99999, ignored_mail_numbers: [] });
    render(<QuoteNumberBox pipeline={EMPTY} />);
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("La numeración llegó a 99999; un administrador debe fijar un nuevo inicio."));
    expect(screen.getByTestId("quote-next")).not.toHaveTextContent("-26");
    expect(screen.getByRole("button", { name: "Copiar" })).toBeDisabled();
  });

  it("waits for the server's answer: «…» and Copy disabled until it arrives", async () => {
    let release: (r: Response) => void = () => undefined;
    const held = new Promise<Response>((resolve) => { release = resolve; });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        return new URL(url, "http://localhost").pathname === "/v2/quotes/numbering"
          ? held
          : Promise.resolve(new Response("{}", { status: 404 }));
      }),
    );
    render(<QuoteNumberBox pipeline={EMPTY} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByTestId("quote-next")).toHaveTextContent("…");
    expect(screen.getByTestId("quote-next")).not.toHaveTextContent("00001-26");
    expect(screen.getByRole("button", { name: "Copiar" })).toBeDisabled();
    release(new Response(JSON.stringify({ configured: true, seed: 42, next_number: "00042-26", highest_known: null, ignored_mail_numbers: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("CN00042"));
    expect(screen.getByRole("button", { name: "Copiar" })).toBeEnabled();
  });

  it("keeps the client-side «Siguiente» and hints at the setup when numbering is not configured", async () => {
    respond({ configured: false, seed: null, next_number: null, highest_known: null, ignored_mail_numbers: [] });
    render(<QuoteNumberBox pipeline={EMPTY} />);
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("00001-26"));
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("La numeración del sistema aún no está configurada; un administrador fija el inicio en Parámetros."));
    expect(screen.getByTestId("quote-next")).not.toHaveTextContent("La asigna el sistema");
  });

  it("behaves as before, with no error text, when the viewer is refused (403)", async () => {
    respond(new Response(JSON.stringify({ detail: { code: "role_may_not_view_costs", message: "no" } }), { status: 403, headers: { "Content-Type": "application/json" } }));
    render(<QuoteNumberBox pipeline={EMPTY} />);
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("00001-26"));
    const box = screen.getByTestId("quote-number-box");
    expect(box).not.toHaveTextContent(/sin configurar/i);
    expect(box).not.toHaveTextContent("La asigna el sistema");
    expect(box).not.toHaveTextContent(/role_may_not_view_costs/);
  });

  it("behaves as before when the numbering read fails", async () => {
    respond(new Response("{}", { status: 500 }));
    render(<QuoteNumberBox pipeline={EMPTY} />);
    await waitFor(() => expect(screen.getByTestId("quote-next")).toHaveTextContent("00001-26"));
    expect(screen.getByTestId("quote-number-box")).not.toHaveTextContent(/sin configurar/i);
  });
});
