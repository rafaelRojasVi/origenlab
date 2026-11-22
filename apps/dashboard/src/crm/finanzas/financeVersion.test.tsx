import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import type { Expense, SupplierOrder } from "./financeApi";
import { FinanzasGastosPage } from "./FinanzasGastosPage";
import { FinanzasPorPagarPage } from "./FinanzasPorPagarPage";

// Every value below is invented; the repository is public.
const NOW = new Date("2026-11-18T15:00:00Z");

const EXPENSE: Expense = {
  expense_id: "e-1", version: 4, month: "2026-11-01", category: "software", amount_clp: 45000, paid_by: "empresa",
  notes: "Licencia ficticia", source: "manual", external_ref: null, created_at: "2026-11-02T12:00:00Z",
  updated_at: "2026-11-02T12:00:00Z", created_by: null,
};

const ORDER: SupplierOrder = {
  order_id: "o-1", version: 7, supplier_name: "Proveedor Ficticio GmbH", ordered_on: "2026-11-03", paid_on: "2026-11-04",
  amount_original: 3200, currency: "USD", amount_clp: 3_000_000, expected_arrival_on: "2026-12-01", received_on: null,
  notes: "Orden ficticia", created_at: "2026-11-03T12:00:00Z", opportunity_id: "case-1", quote_id: "q-1", opportunity_title: "Caso ficticio",
};

interface Post { path: string; body: Record<string, unknown> }

/** The finance reads, and the commands answered by `answer` (a saved version by default). */
function stub(answer: (p: Post) => Response | null = () => null) {
  const posts: Post[] = [];
  const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost").pathname;
    const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
    if (init?.method === "POST") {
      const post = { path, body: JSON.parse(String(init.body)) as Record<string, unknown> };
      posts.push(post);
      const custom = answer(post);
      if (custom) return Promise.resolve(custom);
      return json(path.endsWith("upsert-expense") ? { expense_id: "e-1", version: 5 } : { order_id: "o-1", version: 8 });
    }
    reads.push(path);
    if (path === "/v2/workspace/finance/expenses") return json({ month: "2026-11-01", items: [EXPENSE], total_clp: 45000 });
    if (path === "/v2/workspace/finance/supplier-orders") return json({ items: [ORDER], in_flight_total: 3_000_000, in_flight_count: 1 });
    return json({}, 404);
  }));
  return { posts, reads };
}

const stale = () =>
  new Response(JSON.stringify({ detail: { code: "stale_version", message: "the row changed since it was read" } }), {
    status: 409, headers: { "Content-Type": "application/json" },
  });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearResourceCache();
});

const page = (el: React.ReactNode) => render(<>{el}<Toaster /></>);

describe("Gastos: an edit names the version it read", () => {
  it("an edit sends expected_version; a create sends none", async () => {
    const { posts } = stub();
    page(<FinanzasGastosPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Editar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Actualizar" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ expense_id: "e-1", expected_version: 4 });

    fireEvent.click(screen.getByRole("button", { name: "+ Registrar gasto" }));
    fireEvent.change(await screen.findByLabelText(/Monto \(CLP\)/), { target: { value: "12000" } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar gasto" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1].body).not.toHaveProperty("expense_id");
    expect(posts[1].body).not.toHaveProperty("expected_version");
  });

  it("a 409 stale_version says so and reloads", async () => {
    const { posts, reads } = stub(() => stale());
    page(<FinanzasGastosPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Editar" }));
    const before = reads.length;
    fireEvent.click(await screen.findByRole("button", { name: "Actualizar" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Alguien cambió este registro; se recargó");
    await waitFor(() => expect(reads.filter((r) => r === "/v2/workspace/finance/expenses").length).toBeGreaterThan(before));
  });
});

describe("Por pagar: an edit names the version it read", () => {
  it("editing an order sends its version and keeps the fields the form does not show", async () => {
    const { posts } = stub();
    page(<FinanzasPorPagarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Editar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Actualizar pedido" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ order_id: "o-1", expected_version: 7, opportunity_id: "case-1", quote_id: "q-1" });
  });

  it("«Recibido» sends the version and the order as read, with only the reception day new", async () => {
    const { posts } = stub();
    page(<FinanzasPorPagarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Recibido" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({
      order_id: "o-1", expected_version: 7, paid_on: "2026-11-04", amount_clp: 3_000_000,
      expected_arrival_on: "2026-12-01", order_notes: "Orden ficticia", opportunity_id: "case-1", received_on: "2026-11-18",
    });
  });

  it("a new order sends no version", async () => {
    const { posts } = stub();
    page(<FinanzasPorPagarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "+ Registrar pedido" }));
    const drawer = await screen.findByRole("dialog");
    fireEvent.change(within(drawer).getByLabelText(/Proveedor/), { target: { value: "Otro Proveedor Ficticio" } });
    fireEvent.change(within(drawer).getByLabelText(/^Monto/), { target: { value: "100" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "Registrar pedido" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).not.toHaveProperty("order_id");
    expect(posts[0].body).not.toHaveProperty("expected_version");
  });

  it("a 409 stale_version on «Recibido» says so and reloads", async () => {
    const { reads } = stub(() => stale());
    page(<FinanzasPorPagarPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Recibido" }));
    expect(await screen.findByTestId("toaster")).toHaveTextContent("Alguien cambió este registro; se recargó");
    await waitFor(() => expect(reads.filter((r) => r === "/v2/workspace/finance/supplier-orders")).toHaveLength(2));
  });
});
