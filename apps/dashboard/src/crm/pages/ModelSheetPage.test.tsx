import "@testing-library/jest-dom";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { OperatorApiError } from "../../api/operatorClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../useResource";
import type { ModelSheetRead, PublishStatus } from "../web/webPublishApi";

vi.mock("../web/webPublishApi", async (importActual) => {
  const actual = await importActual<typeof import("../web/webPublishApi")>();
  return { ...actual, fetchModelSheet: vi.fn(), publishModelSheet: vi.fn(), fetchPublishStatus: vi.fn() };
});

import * as api from "../web/webPublishApi";
import { ModelSheetPage } from "./ModelSheetPage";

// Every value is invented; the repository is public.
const SHEET = {
  slug: "modelo-ficticio",
  family: "sonicacion",
  brand: "hielscher",
  name: "Modelo Ficticio 100",
  headline: "Un equipo ficticio para pruebas",
  lead: "Texto de apertura ficticio.",
  applications: ["Dispersión de muestras", "Extracción"],
  specs: [{ label: "Potencia", value: "100 W", source: "https://fabricante.example/ficha" }],
  accessories: ["Sonda ficticia"],
  related: ["otro-modelo"],
  faqs: [{ q: "¿Sirve para pruebas?", a: "Sí." }],
  official_url: "https://fabricante.example/modelo",
  verified_on: "2026-11-01",
};

const READ: ModelSheetRead = {
  model_id: "modelo-ficticio",
  sheet: SHEET,
  sha: "abc123",
  html_url: "https://github.com/ejemplo/repo/blob/main/modelo-ficticio.json",
  valid: true,
  errors: [],
};

const fetchModelSheet = vi.mocked(api.fetchModelSheet);
const publishModelSheet = vi.mocked(api.publishModelSheet);
const fetchPublishStatus = vi.mocked(api.fetchPublishStatus);

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in", method: "google_profile",
    operator: { operatorId: "op", email: "a@example.invalid", displayName: "Admin", role },
    caseCommandsEnabled: true, crmAuthoringEnabled: true,
  } as AuthSessionState;
}

function renderPage(id: string | null = "modelo-ficticio", role = "admin") {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <ModelSheetPage id={id} navigate={navigate} />
    </AuthSessionContext.Provider>,
  );
  return { navigate };
}

function status(s: PublishStatus["status"]): PublishStatus {
  return { status: s, run_url: "https://github.com/ejemplo/repo/actions/runs/1", updated_at: new Date().toISOString(), head_sha: "def456" };
}

beforeEach(() => {
  fetchModelSheet.mockResolvedValue(READ);
  publishModelSheet.mockResolvedValue({ outcome: "dispatched", run_url: "https://github.com/ejemplo/repo/actions/runs/1", commit_sha: "def456" });
  fetchPublishStatus.mockResolvedValue(status("running"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  clearResourceCache();
});

/** A required field's label ends in « *»: match by its start. */
const re = (name: string) => new RegExp(`^${name.replace(/[()]/g, "\\$&")}`);
const f = (name: string) => screen.getByLabelText(re(name));
const findF = (name: string) => screen.findByLabelText(re(name));

function refusal(status: number, body: unknown) {
  return new OperatorApiError(JSON.stringify(body), status);
}

describe("«Catálogo web»", () => {
  it("renders the fields of the loaded sheet", async () => {
    renderPage();
    expect(await findF("Nombre del fabricante")).toHaveValue("Modelo Ficticio 100");
    expect(fetchModelSheet).toHaveBeenCalledWith("modelo-ficticio");
    expect(f("Identificador (slug)")).toHaveValue("modelo-ficticio");
    expect(f("Identificador (slug)")).toHaveAttribute("readonly");
    expect(f("Marca")).toHaveValue("hielscher");
    expect(f("Familia")).toHaveValue("sonicacion");
    expect(f("Titular")).toHaveValue("Un equipo ficticio para pruebas");
    expect(f("Texto de apertura")).toHaveValue("Texto de apertura ficticio.");
    expect(screen.getByLabelText("Aplicación 1")).toHaveValue("Dispersión de muestras");
    expect(screen.getByLabelText("Aplicación 2")).toHaveValue("Extracción");
    expect(screen.getByLabelText("Especificación 1: etiqueta")).toHaveValue("Potencia");
    expect(screen.getByLabelText("Especificación 1: valor")).toHaveValue("100 W");
    expect(screen.getByLabelText("Especificación 1: fuente")).toHaveValue("https://fabricante.example/ficha");
    expect(screen.getByLabelText("Accesorio 1")).toHaveValue("Sonda ficticia");
    expect(screen.getByLabelText("Modelo relacionado 1")).toHaveValue("otro-modelo");
    expect(screen.getByLabelText("Pregunta 1")).toHaveValue("¿Sirve para pruebas?");
    expect(screen.getByLabelText("Respuesta 1")).toHaveValue("Sí.");
    expect(f("Página oficial")).toHaveValue("https://fabricante.example/modelo");
    expect(f("Verificado el")).toHaveValue("2026-11-01");
  });

  it("never offers price, stock, warranty or lead time", async () => {
    renderPage();
    await findF("Nombre del fabricante");
    expect(screen.queryByLabelText(/precio|stock|garant|plazo/i)).not.toBeInTheDocument();
  });

  it("shows the errors of a sheet that does not pass validation, on load", async () => {
    fetchModelSheet.mockResolvedValue({
      ...READ,
      valid: false,
      errors: [{ path: "headline", message: "'abc' is too long" }, { path: "specs/0/source", message: "'x' does not match '^https?://'" }],
    });
    renderPage();
    expect(await screen.findByText(/La ficha publicada no cumple el esquema/)).toBeInTheDocument();
    const headline = f("Titular").closest("div") as HTMLElement;
    expect(within(headline).getByText(/Demasiado largo/)).toBeInTheDocument();
    const source = screen.getByLabelText("Especificación 1: fuente").parentElement as HTMLElement;
    expect(within(source).getByText(/Debe empezar por http/)).toBeInTheDocument();
  });

  it("starts a new sheet when the model has none yet", async () => {
    fetchModelSheet.mockResolvedValue(null);
    renderPage("modelo-nuevo");
    expect(await screen.findByText(/Ficha nueva/)).toBeInTheDocument();
    expect(f("Identificador (slug)")).toHaveValue("modelo-nuevo");
    expect(f("Nombre del fabricante")).toHaveValue("");
    expect(f("Marca")).toHaveValue("");
    expect(screen.queryByLabelText("Aplicación 1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Añadir aplicación" }));
    expect(screen.getByLabelText("Aplicación 1")).toHaveValue("");
  });

  it("says publishing is off when the read is not mounted", async () => {
    fetchModelSheet.mockRejectedValue(refusal(404, { detail: "Not Found" }));
    renderPage();
    expect(await screen.findByText("Publicar web no está activado")).toBeInTheDocument();
  });

  it("is for admins only", async () => {
    renderPage("modelo-ficticio", "sales");
    expect(await screen.findByText("Sólo administración")).toBeInTheDocument();
    expect(fetchModelSheet).not.toHaveBeenCalled();
  });

  it("asks for a model id and opens it from «Cargar»", () => {
    const { navigate } = renderPage(null);
    expect(fetchModelSheet).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Identificador del modelo"), { target: { value: "up400st" } });
    fireEvent.click(screen.getByRole("button", { name: "Cargar" }));
    expect(navigate).toHaveBeenCalledWith("catalogo-web", "up400st");
  });

  it("limits applications to six", async () => {
    renderPage();
    await screen.findByLabelText("Aplicación 1");
    const add = screen.getByRole("button", { name: "Añadir aplicación" });
    for (let i = 0; i < 4; i++) fireEvent.click(add);
    expect(screen.getByLabelText("Aplicación 6")).toBeInTheDocument();
    expect(add).toBeDisabled();
  });

  it("publishes {model_id, sheet} with the edits, then reports the deployment", async () => {
    renderPage();
    const name = await findF("Nombre del fabricante");
    fireEvent.change(name, { target: { value: "Modelo Ficticio 200" } });
    fireEvent.change(screen.getByLabelText("Página PDF oficial"), { target: { value: "https://fabricante.example/ficha.pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "Añadir pregunta" }));
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    await waitFor(() => expect(publishModelSheet).toHaveBeenCalledTimes(1));
    const body = publishModelSheet.mock.calls[0][0];
    expect(body.model_id).toBe("modelo-ficticio");
    expect(body.base_sha).toBe("abc123");
    expect(body.sheet).toEqual({
      ...SHEET,
      name: "Modelo Ficticio 200",
      official_pdf_url: "https://fabricante.example/ficha.pdf",
    });
    expect(await screen.findByText("Publicado — despliegue en curso")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Ver el despliegue/ })).toHaveAttribute("href", "https://github.com/ejemplo/repo/actions/runs/1");
  });

  it("shows the server's schema errors next to the field they name after a 422", async () => {
    publishModelSheet.mockRejectedValue(
      refusal(422, {
        detail: {
          code: "sheet_invalid",
          message: "the sheet does not match the site schema",
          errors: [{ path: "lead", message: "'x' is too long" }, { path: "faqs/0/a", message: "'' is not of type 'string'" }, { path: "", message: "'verified_on' is a required property" }],
        },
      }),
    );
    renderPage();
    await findF("Titular");
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    const lead = (await findF("Texto de apertura")).closest("div") as HTMLElement;
    expect(await within(lead).findByText(/Demasiado largo/)).toBeInTheDocument();
    const answer = screen.getByLabelText("Respuesta 1").parentElement as HTMLElement;
    expect(within(answer).getByText(/Tipo de dato no válido/)).toBeInTheDocument();
    const verified = f("Verificado el").closest("div") as HTMLElement;
    expect(within(verified).getByText(/Falta este campo/)).toBeInTheDocument();
    expect(screen.getByText(/No se publicó: la ficha no cumple el esquema/)).toBeInTheDocument();
    expect(screen.queryByText("Publicado — despliegue en curso")).not.toBeInTheDocument();
  });

  it("explains a rate limit and a failed commit", async () => {
    publishModelSheet.mockRejectedValueOnce(refusal(429, { detail: { code: "publish_rate_limited", message: "slow down" } }));
    renderPage();
    await findF("Titular");
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    expect(await screen.findByText(/Demasiadas publicaciones seguidas/)).toBeInTheDocument();
    publishModelSheet.mockRejectedValueOnce(refusal(502, { code: "commit_failed" }));
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    expect(await screen.findByText(/GitHub no guardó la ficha/)).toBeInTheDocument();
  });

  it("sends base_sha null for a new sheet", async () => {
    fetchModelSheet.mockResolvedValue(null);
    renderPage();
    await findF("Titular");
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    await waitFor(() => expect(publishModelSheet).toHaveBeenCalledTimes(1));
    expect(publishModelSheet.mock.calls[0][0].base_sha).toBeNull();
  });

  it("asks to reload when the sheet changed in GitHub since it was loaded", async () => {
    publishModelSheet.mockRejectedValueOnce(refusal(502, { detail: { code: "commit_conflict", message: "m" } }));
    renderPage();
    await findF("Titular");
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    expect(await screen.findByText("La ficha cambió en GitHub; vuelve a cargar y publica de nuevo.")).toBeInTheDocument();
    expect(screen.queryByText("Publicado — despliegue en curso")).not.toBeInTheDocument();
  });

  it("bases the next publish on the sha the last publish wrote", async () => {
    publishModelSheet.mockResolvedValueOnce({ outcome: "dispatched", run_url: "https://github.com/x/runs/1", commit_sha: "def456", sha: "fff999" });
    renderPage();
    await findF("Titular");
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    await screen.findByText("Publicado — despliegue en curso");
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    await waitFor(() => expect(publishModelSheet).toHaveBeenCalledTimes(2));
    expect(publishModelSheet.mock.calls[0][0].base_sha).toBe("abc123");
    expect(publishModelSheet.mock.calls[1][0].base_sha).toBe("fff999");
  });

  it("sends one request when the form is submitted twice before the first answer", async () => {
    let resolve: (v: api.PublishReceipt) => void = () => undefined;
    publishModelSheet.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderPage();
    await findF("Titular");
    const form = screen.getByRole("form", { name: /Ficha del modelo/ });
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(publishModelSheet).toHaveBeenCalledTimes(1);
    await act(async () => { resolve({ outcome: "dispatched", run_url: "https://github.com/x/runs/1", commit_sha: "def456" }); });
    await screen.findByText("Publicado — despliegue en curso");
  });

  it("links each field's error to it and moves focus to the explanation after a failed publish", async () => {
    publishModelSheet.mockRejectedValue(refusal(422, { detail: { code: "sheet_invalid", message: "m", errors: [{ path: "lead", message: "'x' is too long" }, { path: "specs/0/source", message: "'x' does not match '^https?://'" }] } }));
    renderPage();
    await findF("Titular");
    expect(f("Texto de apertura")).not.toHaveAttribute("aria-invalid");
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    const alert = await screen.findByText(/No se publicó: la ficha no cumple el esquema/);
    await waitFor(() => expect(alert.closest("[role=alert]")).toHaveFocus());
    const lead = f("Texto de apertura");
    expect(lead).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(lead.getAttribute("aria-describedby") ?? "")).toHaveTextContent(/Demasiado largo/);
    const source = screen.getByLabelText("Especificación 1: fuente");
    expect(source).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(source.getAttribute("aria-describedby") ?? "")).toHaveTextContent(/http/);
  });

  it("counts the published sheet as the new baseline", async () => {
    renderPage();
    const name = await findF("Nombre del fabricante");
    fireEvent.change(name, { target: { value: "Modelo Ficticio 300" } });
    expect(screen.getByText("Cambios sin publicar")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
    await screen.findByText("Publicado — despliegue en curso");
    expect(screen.queryByText("Cambios sin publicar")).not.toBeInTheDocument();
    fireEvent.change(name, { target: { value: "Otro" } });
    fireEvent.click(screen.getByRole("button", { name: "Descartar cambios" }));
    expect(f("Nombre del fabricante")).toHaveValue("Modelo Ficticio 300");
  });

  // 50 rows render ~150 labelled inputs: label and role queries scan all of them and were slow under
  // the parallel suite, so this test finds the buttons by their text and waits with its own budget.
  it("stops adding rows at 50", async () => {
    fetchModelSheet.mockResolvedValue({ ...READ, sheet: { ...SHEET, specs: Array.from({ length: 50 }, (_, i) => ({ label: `L${i}`, value: "v", source: "https://fabricante.example/x" })) } });
    renderPage();
    const addSpec = (await screen.findByText("Añadir especificación", {}, { timeout: 10_000 })).closest("button");
    await waitFor(() => expect(addSpec).toBeDisabled(), { timeout: 10_000 });
    // Disabled by the 50 rows, not by a load still in flight.
    expect(screen.getByDisplayValue("L49")).toBeInTheDocument();
    expect(screen.getByText("Añadir pregunta").closest("button")).toBeEnabled();
  }, 30_000);

  it("discards edits back to the loaded sheet", async () => {
    renderPage();
    const name = await findF("Nombre del fabricante");
    const discard = screen.getByRole("button", { name: "Descartar cambios" });
    expect(discard).toBeDisabled();
    fireEvent.change(name, { target: { value: "Otro nombre" } });
    expect(discard).toBeEnabled();
    fireEvent.click(discard);
    expect(f("Nombre del fabricante")).toHaveValue("Modelo Ficticio 100");
  });

  describe("status after publishing", () => {
    async function publish() {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      renderPage();
      await findF("Titular");
      fireEvent.click(screen.getByRole("button", { name: "Publicar en el sitio" }));
      await screen.findByText("Publicado — despliegue en curso");
    }

    it("polls every 15 seconds and stops on success", async () => {
      fetchPublishStatus.mockResolvedValueOnce(status("running")).mockResolvedValue(status("success"));
      await publish();
      await waitFor(() => expect(fetchPublishStatus).toHaveBeenCalledTimes(1));
      expect(fetchPublishStatus).toHaveBeenCalledWith("modelo-ficticio");
      expect(screen.getByText(/Construyendo el sitio/)).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
      await waitFor(() => expect(fetchPublishStatus).toHaveBeenCalledTimes(2));
      expect(await screen.findByText(/El sitio ya está actualizado/)).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(fetchPublishStatus).toHaveBeenCalledTimes(2);
    });

    it("does not take the previous deployment's success for this one", async () => {
      fetchPublishStatus.mockResolvedValue({ ...status("success"), updated_at: "2026-01-01T00:00:00Z", head_sha: "old000" });
      await publish();
      await waitFor(() => expect(fetchPublishStatus).toHaveBeenCalledTimes(1));
      expect(screen.queryByText(/El sitio ya está actualizado/)).not.toBeInTheDocument();
      expect(screen.getByText(/Buscando el despliegue/)).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
      await waitFor(() => expect(fetchPublishStatus).toHaveBeenCalledTimes(2));
    });

    it("trusts a run whose commit is the published one, whatever its clock says", async () => {
      fetchPublishStatus.mockResolvedValue({ ...status("success"), updated_at: "2026-01-01T00:00:00Z", head_sha: "def456" });
      await publish();
      expect(await screen.findByText(/El sitio ya está actualizado/)).toBeInTheDocument();
    });

    it("stops asking when the status route is refused or missing", async () => {
      fetchPublishStatus.mockRejectedValue(refusal(404, { detail: "Not Found" }));
      await publish();
      await waitFor(() => expect(fetchPublishStatus).toHaveBeenCalledTimes(1));
      expect(await screen.findByText(/No se pudo consultar el estado/)).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(fetchPublishStatus).toHaveBeenCalledTimes(1);
    });

    it("gives up after an hour without news and says so", async () => {
      fetchPublishStatus.mockResolvedValue(status("waiting"));
      await publish();
      await act(async () => { await vi.advanceTimersByTimeAsync(15_000 * 250); });
      expect(await screen.findByText("Sin novedades del despliegue; revise el enlace")).toBeInTheDocument();
      const calls = fetchPublishStatus.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(fetchPublishStatus).toHaveBeenCalledTimes(calls);
    });

    it("says when the run waits for the production approval in GitHub", async () => {
      fetchPublishStatus.mockResolvedValue(status("waiting"));
      await publish();
      expect(await screen.findByText("Esperando aprobación en GitHub")).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
      await waitFor(() => expect(fetchPublishStatus.mock.calls.length).toBeGreaterThanOrEqual(2));
    });

    it("stops polling when the page is closed", async () => {
      fetchPublishStatus.mockResolvedValue(status("running"));
      await publish();
      await waitFor(() => expect(fetchPublishStatus).toHaveBeenCalledTimes(1));
      cleanup();
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(fetchPublishStatus).toHaveBeenCalledTimes(1);
    });
  });
});
