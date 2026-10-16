/**
 * «Preferencias del Cotizador»: every field shows its current value; «Guardar» asks for a reason
 * and sends `set-cost-parameter` with the row seen; a change by someone else is said; the seed is
 * administration's. Every value is invented: this repository is public. Refusals use the API's
 * real envelope (`{error: {code, message, details: {code, message}}}`).
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import type { CostParameters } from "../catalog/catalogApi";
import { Toaster } from "../ui";
import { clearResourceCache } from "../useResource";
import { PreferencesPage } from "./PreferencesPage";

const PARAMS: CostParameters = {
  current: {
    default_markup_equipment: { id: "p-eq-2", value: "0.40", valid_from: "2026-10-01T15:00:00Z", set_by: "Admin Ejemplo", reason: "Política de prueba" },
    margin_target_min: { id: "p-min-1", value: "0.125", valid_from: "2026-09-01T15:00:00Z", set_by: "Admin Ejemplo", reason: "Inicial" },
    profile_freight_clp: { id: "p-fr-1", value: "150000", valid_from: "2026-09-01T15:00:00Z", set_by: "Admin Ejemplo", reason: "Inicial" },
    quote_default_payment_terms: { id: "p-pay-1", value: "Contado [CONDICIÓN]", valid_from: "2026-09-01T15:00:00Z", set_by: null, reason: "Inicial" },
    quote_default_validity_days: { id: "p-val-1", value: "30", valid_from: "2026-09-01T15:00:00Z", set_by: null, reason: "Inicial" },
    quote_number_seed: { id: "p-seed-1", value: "42", valid_from: "2026-09-01T15:00:00Z", set_by: "Admin Ejemplo", reason: "Inicio de prueba" },
  },
  history: [
    { key: "quote_default_payment_terms", id: "p-pay-1", value: "Contado [CONDICIÓN]", valid_from: "2026-09-01T15:00:00Z", set_by: null, reason: "Inicial" },
    { key: "default_markup_equipment", id: "p-eq-2", value: "0.40", valid_from: "2026-10-01T15:00:00Z", set_by: "Admin Ejemplo", reason: "Política de prueba" },
    { key: "default_markup_equipment", id: "p-eq-1", value: "0.35", valid_from: "2026-09-01T15:00:00Z", set_by: "Ventas Ejemplo", reason: "Valor inicial" },
    { key: "margin_target_min", id: "p-min-1", value: "0.125", valid_from: "2026-09-01T15:00:00Z", set_by: "Admin Ejemplo", reason: "Inicial" },
  ],
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
/** The API's envelope (errors.py): the HTTP code outside, the command's own in `details`. */
const refusal = (status: number, outer: string, code: string, message: string) =>
  json({ error: { code: outer, message, details: { code, message }, request_id: "req-1" } }, status);

type Post = { body: Record<string, unknown>; key: string | null };

function stubApi(onPost: (call: number) => Response | Promise<Response> = () => json({ ok: true, cost_parameter_id: "p-new", key: "k" })) {
  const posts: Post[] = [];
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(raw, "http://localhost").pathname;
      if (path === "/v2/catalog/parameters") {
        reads++;
        return Promise.resolve(json(PARAMS));
      }
      if (path === "/v2/commands/set-cost-parameter" && init?.method === "POST") {
        const headers = new Headers(init.headers);
        posts.push({ body: JSON.parse(String(init.body)), key: headers.get("Idempotency-Key") });
        return Promise.resolve(onPost(posts.length));
      }
      return Promise.resolve(json({}, 404));
    }),
  );
  return { posts, reads: () => reads };
}

function sessionAs(role: "sales" | "admin"): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: `op-${role}`, email: `${role}@example.cl`, displayName: role, role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function renderPage(role: "sales" | "admin" = "sales") {
  return render(
    <AuthSessionContext.Provider value={{ session: sessionAs(role), signOut: async () => true }}>
      <PreferencesPage />
      <Toaster />
    </AuthSessionContext.Provider>,
  );
}

const field = (label: string) => screen.findByLabelText(label);
const dialog = () => screen.getByRole("dialog", { name: /^Cambiar/ });

async function change(label: string, text: string) {
  fireEvent.change(await field(label), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: `Guardar ${label}` }));
}

function giveReason(text: string) {
  fireEvent.change(within(dialog()).getByLabelText(/Motivo/), { target: { value: text } });
  fireEvent.click(within(dialog()).getByRole("button", { name: "Guardar" }));
}

beforeEach(() => {
  clearResourceCache();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PreferencesPage — the current values", () => {
  it("shows each field's current value, and what applies while one is unset", async () => {
    stubApi();
    renderPage();
    expect(await field("Equipos")).toHaveValue("40");
    expect(screen.getByLabelText("Mínimo")).toHaveValue("12,5");
    expect(screen.getByLabelText("Flete")).toHaveValue("150.000");
    expect(screen.getByLabelText("Validez (días)")).toHaveValue("30");
    expect(screen.getByLabelText("Forma de pago")).toHaveValue("Contado [CONDICIÓN]");
    expect(screen.getByText(/Vigente: 40 %/)).toBeInTheDocument();
    expect(screen.getByText(/Vigente: \$ 150\.000/)).toBeInTheDocument();
    // Unset: empty, and what the engine uses instead — as the placeholder too (e4).
    expect(screen.getByLabelText("Accesorios")).toHaveValue("");
    expect(screen.getByLabelText("Accesorios")).toHaveAttribute("placeholder", "45 %");
    expect(screen.getByLabelText("Equipos")).toHaveAttribute("placeholder", "40 %");
    expect(screen.getByLabelText("Máximo")).toHaveAttribute("placeholder", "45 %");
    expect(screen.getByLabelText("Validez (días)")).toHaveAttribute("placeholder", "30");
    // Nothing falls back for these: no placeholder.
    expect(screen.getByLabelText("Desviación de precio")).not.toHaveAttribute("placeholder");
    expect(screen.getByLabelText("Inicio de la numeración")).not.toHaveAttribute("placeholder");
    // The deviation warning is stored, not built yet (M3): the hint does not promise it.
    expect(screen.getByText(/Sin definir: no hay aviso/)).toHaveTextContent("se guarda; el aviso todavía no está construido");
    expect(screen.queryByText(/aviso si se aleja del histórico/)).toBeNull();
    expect(screen.getAllByText(/Sin definir: se usa 45 %/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Sin definir: no hay aviso/)).toBeInTheDocument();
    for (const title of ["Recargos por defecto", "Margen objetivo", "Gastos de importación (perfil)", "Condiciones por defecto", "Numeración"]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
  });

  it("«Guardar» is off until the value changes", async () => {
    stubApi();
    renderPage();
    await field("Equipos");
    expect(screen.getByRole("button", { name: "Guardar Equipos" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Equipos"), { target: { value: "40 %" } });
    expect(screen.getByRole("button", { name: "Guardar Equipos" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Equipos"), { target: { value: "42" } });
    expect(screen.getByRole("button", { name: "Guardar Equipos" })).toBeEnabled();
  });

  it("names what cannot be read instead of asking for a reason", async () => {
    const api = stubApi();
    renderPage();
    await change("Equipos", "cuarenta");
    expect(screen.getByRole("alert")).toHaveTextContent("Escribe un porcentaje");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.posts).toHaveLength(0);
  });
});

describe("PreferencesPage — saving", () => {
  it("requires a reason of at least 3 characters, then sends the row seen as expected_current_id", async () => {
    const api = stubApi();
    renderPage();
    await change("Equipos", "42,5");
    expect(within(dialog()).getByText(/De 40 % a 42,5 %/)).toBeInTheDocument();
    giveReason("ab");
    expect(within(dialog()).getByRole("alert")).toHaveTextContent("al menos 3 caracteres");
    expect(api.posts).toHaveLength(0);
    const readsBefore = api.reads();
    giveReason("  Ajuste de prueba  ");
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toEqual({
      key: "default_markup_equipment",
      value: "0.425",
      expected_current_id: "p-eq-2",
      reason: "Ajuste de prueba",
    });
    expect(api.posts[0].key).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("Preferencia guardada · Equipos")).toBeInTheDocument();
    await waitFor(() => expect(api.reads()).toBeGreaterThan(readsBefore));
  });

  it("an unset parameter is sent with expected_current_id null; amounts and texts in their own form", async () => {
    const api = stubApi();
    renderPage();
    await change("Accesorios", "45");
    giveReason("Primera vez");
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toMatchObject({ key: "default_markup_accessory", value: "0.45", expected_current_id: null });

    await change("Flete", "1.234.567");
    giveReason("Nuevo flete");
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts[1].body).toMatchObject({ key: "profile_freight_clp", value: "1234567", expected_current_id: "p-fr-1" });

    await change("Forma de pago", "30 días [CONDICIÓN]");
    giveReason("Nuevo texto");
    await waitFor(() => expect(api.posts).toHaveLength(3));
    expect(api.posts[2].body).toEqual({
      key: "quote_default_payment_terms",
      value_text: "30 días [CONDICIÓN]",
      expected_current_id: "p-pay-1",
      reason: "Nuevo texto",
    });
  });

  it("someone else changed it (409 parameter_changed): «Otra persona lo cambió; recarga.», and «Recargar» reads again", async () => {
    const api = stubApi(() => refusal(409, "conflict", "parameter_changed", "this parameter was changed since you loaded it; reload and decide again"));
    renderPage();
    await change("Equipos", "50");
    giveReason("Ajuste de prueba");
    expect(await within(dialog()).findByText("Otra persona lo cambió; recarga.")).toBeInTheDocument();
    expect(within(dialog()).queryByRole("button", { name: "Guardar" })).toBeNull();
    const readsBefore = api.reads();
    fireEvent.click(within(dialog()).getByRole("button", { name: "Recargar" }));
    await waitFor(() => expect(api.reads()).toBeGreaterThan(readsBefore));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("the same change, after closing and reopening the dialog, keeps the key of an uncertain failure", async () => {
    const api = stubApi((n) => (n === 1 ? new Response("Bad gateway", { status: 502 }) : json({ ok: true })));
    renderPage();
    await change("Equipos", "50");
    giveReason("Ajuste de prueba");
    await within(dialog()).findByRole("alert");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Guardar Equipos" }));
    giveReason("Ajuste de prueba");
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts[1].key).toBe(api.posts[0].key);
  });

  it("an uncertain failure keeps the Idempotency-Key for the same retry; a refusal does not", async () => {
    const api = stubApi((n) => (n === 1 ? new Response("Bad gateway", { status: 502 }) : n === 2 ? json({ ok: true }) : json({ ok: true })));
    renderPage();
    await change("Equipos", "50");
    giveReason("Ajuste de prueba");
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent("No se pudo guardar. Intenta de nuevo.");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts[1].key).toBe(api.posts[0].key);
  });

  it("a refusal is said in Spanish, and the next attempt gets a new key", async () => {
    const api = stubApi((n) => (n === 1 ? refusal(422, "validation_error", "validation_error", "Request validation failed") : json({ ok: true })));
    renderPage();
    await change("Equipos", "50");
    giveReason("Ajuste de prueba");
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent("El servidor no aceptó el valor");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts[1].key).not.toBe(api.posts[0].key);
  });
});

describe("PreferencesPage — the margin band's ends", () => {
  it("refuses a minimum above the maximum before asking for a reason", async () => {
    const api = stubApi();
    renderPage();
    // The maximum is unset: the engine's 45 % applies.
    await change("Mínimo", "50");
    expect(screen.getByRole("alert")).toHaveTextContent("El mínimo no puede ser mayor que el máximo (45 %).");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.posts).toHaveLength(0);
  });

  it("refuses a maximum below the current minimum", async () => {
    const api = stubApi();
    renderPage();
    await change("Máximo", "10");
    expect(screen.getByRole("alert")).toHaveTextContent("El máximo no puede ser menor que el mínimo (12,5 %).");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.posts).toHaveLength(0);
  });

  it("names a percentage with too many decimals before asking for a reason", async () => {
    stubApi();
    renderPage();
    await change("Equipos", "12,34567");
    expect(screen.getByRole("alert")).toHaveTextContent("Usa como máximo 4 decimales en un porcentaje.");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("PreferencesPage — numbering is administration's", () => {
  it("sales sees the seed read-only, with no «Guardar»", async () => {
    stubApi();
    renderPage("sales");
    const seed = await field("Inicio de la numeración");
    expect(seed).toHaveValue("42");
    expect(seed).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Guardar Inicio de la numeración" })).toBeNull();
    expect(screen.getByText(/Sólo administración la cambia/)).toBeInTheDocument();
  });

  it("an admin changes it", async () => {
    const api = stubApi();
    renderPage("admin");
    const seed = await field("Inicio de la numeración");
    expect(seed).toBeEnabled();
    await change("Inicio de la numeración", "120");
    giveReason("Nuevo inicio");
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toMatchObject({ key: "quote_number_seed", value: "120", expected_current_id: "p-seed-1" });
  });

  it("the API's 403 role_may_not_set_numbering is said in Spanish", async () => {
    stubApi(() => refusal(403, "forbidden", "role_may_not_set_numbering", "Sólo administración fija el inicio de la numeración."));
    renderPage("admin");
    await change("Inicio de la numeración", "120");
    giveReason("Nuevo inicio");
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent("Sólo administración fija el inicio de la numeración.");
  });
});

describe("PreferencesPage — who may change them", () => {
  it.each([
    ["role_may_not_view_costs", "the command's own code"],
    ["forbidden", "a bare 403"],
  ])("a 403 %s (%s) says who may change the preferences, not who may quote", async (code) => {
    stubApi(() =>
      code === "forbidden"
        ? json({ error: { code: "forbidden", message: "Forbidden", details: {}, request_id: "req-1" } }, 403)
        : refusal(403, "forbidden", code, "sales or admin only"),
    );
    renderPage();
    await change("Equipos", "41");
    giveReason("Ajuste de prueba");
    const alert = await within(dialog()).findByRole("alert");
    expect(alert).toHaveTextContent("Sólo ventas y administración pueden cambiar las preferencias.");
    expect(alert).not.toHaveTextContent("cotizar");
  });
});

describe("PreferencesPage — «Historial»", () => {
  it("lists the earlier values with who, when and why", async () => {
    stubApi();
    renderPage();
    await field("Equipos");
    // The accessible name carries the visible count; aria-controls only while the list exists.
    const toggle = screen.getByRole("button", { name: "Historial de Equipos (2)" });
    expect(toggle).toHaveTextContent("Historial (2)");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).not.toHaveAttribute("aria-controls");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-controls", screen.getByRole("list", { name: "Historial de Equipos" }).id);
    const list = screen.getByRole("list", { name: "Historial de Equipos" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("40 %");
    expect(rows[0]).toHaveTextContent("vigente");
    expect(rows[1]).toHaveTextContent("35 %");
    expect(rows[1]).toHaveTextContent("Ventas Ejemplo");
    expect(rows[1]).toHaveTextContent("«Valor inicial»");
    expect(rows[1]).toHaveTextContent("2026");
  });

  it("a row without an operator says «carga inicial»", async () => {
    stubApi();
    renderPage();
    await field("Forma de pago");
    fireEvent.click(screen.getByRole("button", { name: "Historial de Forma de pago (1)" }));
    const list = screen.getByRole("list", { name: "Historial de Forma de pago" });
    expect(within(list).getByRole("listitem")).toHaveTextContent("carga inicial");
  });

  it("says when a field has no history", async () => {
    stubApi();
    renderPage();
    await field("Accesorios");
    fireEvent.click(screen.getByRole("button", { name: "Historial de Accesorios" }));
    expect(screen.getByText("Sin cambios registrados.")).toBeInTheDocument();
  });
});

describe("PreferencesPage — a viewer", () => {
  it("is refused by the read and sees no field", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(json({ error: { code: "forbidden", message: "no", details: { code: "role_may_not_view_costs" } } }, 403))),
    );
    renderPage();
    await act(async () => {
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });
    expect(await screen.findByText("Sin permiso para ver esta sección")).toBeInTheDocument();
    expect(screen.queryByLabelText("Equipos")).toBeNull();
  });
});
