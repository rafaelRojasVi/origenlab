import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { TestSendPanel } from "./TestSendPanel";

const CONFIG = { enabled: true, per_hour: 10, per_day: 30, sender: "contacto@origenlab.cl" };
const TARGET = { v1_lane_key: "cyber-2026-10" };

function asRole(role: string, ui: ReactNode) {
  return (
    <AuthSessionContext.Provider value={{ session: { kind: "signed_in", method: "google_session",
      operator: { operatorId: "op", email: "op@example.invalid", displayName: "Op", role } }, signOut: async () => true }}>
      {ui}
    </AuthSessionContext.Provider>
  );
}

function stub(post: { status: number; body: unknown }, tests: unknown[] = []) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    calls.push({ url, init });
    if (url.includes("test-send-history")) {
      return Promise.resolve(new Response(JSON.stringify({ tests, remaining: { hour: 10, day: 30 } }), { status: 200 }));
    }
    return Promise.resolve(new Response(JSON.stringify(post.body), { status: post.status }));
  }));
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("«Enviar prueba»", () => {
  it("is not shown to sales or when the API says it is off", () => {
    stub({ status: 200, body: {} });
    const { rerender } = render(asRole("sales", <TestSendPanel target={TARGET} config={CONFIG} />));
    expect(screen.queryByRole("button", { name: "Enviar prueba" })).toBeNull();
    rerender(asRole("admin", <TestSendPanel target={TARGET} config={{ ...CONFIG, enabled: false }} />));
    expect(screen.queryByRole("button", { name: "Enviar prueba" })).toBeNull();
  });

  it("sends the campaign to the typed address and says where to look", async () => {
    const calls = stub({ status: 200, body: { status: "sent", to: "ana@example.invalid", subject: "[PRUEBA] X",
                                              gmail_message_id: "m1", sent_at: "2026-10-05T12:00:00Z" } });
    render(asRole("admin", <TestSendPanel target={TARGET} config={CONFIG} />));
    fireEvent.change(screen.getByLabelText("Enviar una prueba a"), { target: { value: "ana@example.invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar prueba" }));
    expect(await screen.findByText(/Enviada a ana@example\.invalid/)).toBeInTheDocument();
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect(post.url).toMatch(/\/v2\/commands\/send-campaign-test$/);
    expect(JSON.parse(String(post.init!.body))).toEqual({ v1_lane_key: "cyber-2026-10", to: "ana@example.invalid" });
    expect((post.init!.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
  });

  it("says plainly when the limit is reached", async () => {
    stub({ status: 429, body: { detail: { code: "test_send_limit", message: "límite", next_allowed_at: "2026-10-05T13:00:00Z" } } });
    render(asRole("admin", <TestSendPanel target={TARGET} config={CONFIG} />));
    fireEvent.change(screen.getByLabelText("Enviar una prueba a"), { target: { value: "ana@example.invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar prueba" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Límite de pruebas alcanzado\. La próxima se puede enviar a las 10:00\./);
  });

  it("falls back when the 429 carries no time", async () => {
    stub({ status: 429, body: { detail: { code: "test_send_limit", message: "límite" } } });
    render(asRole("admin", <TestSendPanel target={TARGET} config={CONFIG} />));
    fireEvent.change(screen.getByLabelText("Enviar una prueba a"), { target: { value: "ana@example.invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar prueba" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Límite de pruebas alcanzado. Intenta más tarde.");
  });

  it("accepts only one plain mailbox — the API's rule, the reviewer's probes included", () => {
    stub({ status: 200, body: {} });
    render(asRole("admin", <TestSendPanel target={TARGET} config={CONFIG} />));
    const field = screen.getByLabelText("Enviar una prueba a");
    const button = () => screen.getByRole("button", { name: "Enviar prueba" });
    for (const bad of [
      "a@b.example(origenlab.cl",
      "=?utf-8?q?boss?=@=?utf-8?q?origenlab?=.cl",
      "ops:ana@x.example",
      "ñandú@example.invalid",
      "a@b.c",
      "a@b.123",
      ".a@example.invalid",
      "a.@example.invalid",
      "a..b@example.invalid",
      "a@-b.example",
      "a@b-.example",
      "a@b..example",
      "a@example.invalid.",
      `a@${"d".repeat(64)}.example`,
      `${"x".repeat(250)}@example.invalid`,
      '"Ana" <ana@example.invalid>',
      "a@example.invalid;b@example.invalid",
    ]) {
      fireEvent.change(field, { target: { value: bad } });
      expect(button(), bad).toBeDisabled();
    }
    for (const ok of ["ana@example.invalid", " Ana.Perez+test@sub.example.invalid ", "a_b%c-d@x-y.example.invalid"]) {
      fireEvent.change(field, { target: { value: ok } });
      expect(button(), ok).toBeEnabled();
    }
  });

  it("shows why a past test failed: Google's status and error code", async () => {
    stub({ status: 200, body: {} }, [
      { at: "2026-10-05T12:00:00Z", by: "Op", to: "ana@example.invalid", status: "failed", error: "gmail_rejected",
        error_detail: "HTTP 403 PERMISSION_DENIED/dailyLimitExceeded" },
      { at: "2026-10-05T11:00:00Z", by: "Op", to: "bea@example.invalid", status: "failed", error: "network" },
    ]);
    render(asRole("admin", <TestSendPanel target={TARGET} config={CONFIG} />));
    expect(await screen.findByText(/falló \(HTTP 403 PERMISSION_DENIED\/dailyLimitExceeded\)/)).toBeInTheDocument();
    expect(screen.getByText(/falló \(network\)/)).toBeInTheDocument();
  });

  it("does not send an address with a name or a comma", async () => {
    const calls = stub({ status: 200, body: {} });
    render(asRole("admin", <TestSendPanel target={TARGET} config={CONFIG} />));
    fireEvent.change(screen.getByLabelText("Enviar una prueba a"), { target: { value: "a@example.invalid, b@example.invalid" } });
    expect(screen.getByRole("button", { name: "Enviar prueba" })).toBeDisabled();
    await waitFor(() => expect(calls.filter((c) => c.init?.method === "POST")).toHaveLength(0));
  });
});
