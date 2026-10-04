import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  loginErrorMessage,
  parseAuthSessionResponse,
  readLoginError,
} from "../../api/authClient";
import { AuthSessionContext, useAuthSession } from "../../context/AuthSessionContext";
import { useResource } from "../../crm/useResource";
import { AuthGate } from "./AuthGate";

const SIGNED_IN = {
  authenticated: true,
  auth_method: "google_session",
  operator: {
    operator_id: "00000000-0000-4000-8000-0000000000aa",
    email: "contacto@origenlab.cl",
    display_name: "Contacto",
    role: "admin",
  },
  google_login_enabled: true,
  workspace_domain: "origenlab.cl",
};

const SIGNED_OUT = {
  authenticated: false,
  detail: "no dashboard session: sign in with Google Workspace",
  google_login_enabled: true,
  workspace_domain: "origenlab.cl",
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function Probe() {
  const { session, signOut } = useAuthSession();
  return (
    <div data-testid="dashboard">
      {session.kind}
      <button type="button" onClick={() => void signOut()}>
        salir
      </button>
    </div>
  );
}

let rememberedAnswer = "primera";
const rememberedLoader = () => Promise.resolve(rememberedAnswer);

function RememberingProbe() {
  const [state] = useResource(rememberedLoader);
  return <p data-testid="remembered">{state.kind === "ready" ? state.data : state.kind}</p>;
}

describe("parseAuthSessionResponse", () => {
  it("reads a signed-in operator", () => {
    expect(parseAuthSessionResponse(200, SIGNED_IN)).toEqual({
      kind: "signed_in",
      method: "google_session",
      operator: {
        operatorId: SIGNED_IN.operator.operator_id,
        email: "contacto@origenlab.cl",
        displayName: "Contacto",
        role: "admin",
      },
      profile: null,
      canSwitchProfile: false,
    });
  });

  it("reads 401 as signed out with the sign-in options", () => {
    expect(parseAuthSessionResponse(401, SIGNED_OUT)).toMatchObject({
      kind: "signed_out",
      googleLoginEnabled: true,
      workspaceDomain: "origenlab.cl",
    });
  });

  it("fails closed on 404 and on the proxy's path_not_allowed 403", () => {
    expect(parseAuthSessionResponse(404, { detail: "Not Found" }).kind).toBe("error");
    expect(parseAuthSessionResponse(403, { error: { code: "path_not_allowed" } }).kind).toBe(
      "error",
    );
    expect(parseAuthSessionResponse(403, {}).kind).toBe("error");
  });

  it("treats any other answer as an error, never as signed in", () => {
    expect(parseAuthSessionResponse(500, {}).kind).toBe("error");
    expect(parseAuthSessionResponse(403, { error: { code: "forbidden" } }).kind).toBe("error");
    expect(parseAuthSessionResponse(200, { authenticated: true }).kind).toBe("error");
    expect(parseAuthSessionResponse(200, { authenticated: false }).kind).toBe("error");
  });
});

describe("login error codes", () => {
  it("accepts only a short lowercase code", () => {
    expect(readLoginError("?login_error=wrong_domain")).toBe("wrong_domain");
    expect(readLoginError("?login_error=<script>")).toBeNull();
    expect(readLoginError("")).toBeNull();
  });

  it("maps every code the API emits to a message", () => {
    for (const code of [
      "access_denied",
      "google_error",
      "invalid_request",
      "invalid_state",
      "token_exchange_failed",
      "invalid_token",
      "email_unverified",
      "wrong_domain",
      "unknown_operator",
      "operator_disabled",
      "operator_not_permitted",
    ]) {
      expect(loginErrorMessage(code)).not.toBe("No se pudo iniciar sesión.");
    }
    expect(loginErrorMessage("something_new")).toBe("No se pudo iniciar sesión.");
  });
});

describe("AuthGate", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the Google login button and not the dashboard when signed out", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, SIGNED_OUT));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    const button = await screen.findByTestId("google-login-button");
    expect(button).toHaveTextContent("Iniciar sesión con Google");
    expect(button.getAttribute("href")).toMatch(/\/auth\/google\/login$/);
    expect(screen.getByTestId("login-screen")).toHaveTextContent("@origenlab.cl");
    expect(screen.queryByTestId("dashboard")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/auth\/session$/);
  });

  it("says so when Google sign-in is off in this environment", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { ...SIGNED_OUT, google_login_enabled: false }));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("login-screen")).toHaveTextContent("no está habilitado");
    expect(screen.queryByTestId("google-login-button")).toBeNull();
  });

  it("shows the callback's error and removes it from the address bar", async () => {
    window.history.replaceState(null, "", "/?login_error=unknown_operator");
    fetchMock.mockResolvedValue(jsonResponse(401, SIGNED_OUT));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("login-error")).toHaveTextContent(
      "no está registrada como operador",
    );
    expect(window.location.search).toBe("");
  });

  it("renders the dashboard for a signed-in operator", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, SIGNED_IN));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("dashboard")).toHaveTextContent("signed_in");
  });

  it.each([
    [404, { detail: "Not Found" }],
    [403, { error: { code: "path_not_allowed" } }],
    [403, { error: { code: "forbidden" } }],
    [500, {}],
    [502, null],
    [200, { authenticated: true }],
  ])("does not render the dashboard when /auth/session answers %i", async (status, body) => {
    fetchMock.mockResolvedValue(jsonResponse(status, body));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("auth-error")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard")).toBeNull();
  });

  it("does not render the dashboard when the session cannot be checked", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByText("No se pudo verificar la sesión")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard")).toBeNull();
  });

  it.each([
    ["a 503 logout_not_recorded", () => Promise.resolve(jsonResponse(503, { detail: "logout_not_recorded" }))],
    ["a network failure", () => Promise.reject(new TypeError("Failed to fetch"))],
  ])("stays signed in and says so after %s, and a retry signs out", async (_name, failure) => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, SIGNED_IN))
      .mockImplementationOnce(failure)
      .mockResolvedValueOnce(jsonResponse(200, { authenticated: false }))
      .mockResolvedValueOnce(jsonResponse(401, SIGNED_OUT));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click(await screen.findByText("salir"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se pudo cerrar la sesión de forma segura. Intenta nuevamente.",
    );
    expect(screen.getByTestId("dashboard")).toHaveTextContent("signed_in");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByText("salir"));
    await waitFor(() => expect(screen.getByTestId("google-login-button")).toBeInTheDocument());
    expect(screen.queryByTestId("logout-failed")).toBeNull();
    expect(String(fetchMock.mock.calls[2][0])).toMatch(/\/auth\/logout$/);
  });

  it("forgets every page's remembered data once the session ends", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, SIGNED_IN))
      .mockResolvedValueOnce(jsonResponse(200, { authenticated: false }))
      .mockResolvedValueOnce(jsonResponse(401, SIGNED_OUT));
    const gate = render(
      <AuthGate>
        <Probe />
        <RememberingProbe />
      </AuthGate>,
    );
    await waitFor(() => expect(screen.getByTestId("remembered")).toHaveTextContent("primera"));
    fireEvent.click(screen.getByText("salir"));
    await waitFor(() => expect(screen.getByTestId("google-login-button")).toBeInTheDocument());
    gate.unmount();

    // Same operator signs in again: nothing from before the sign-out is shown while loading.
    rememberedAnswer = "segunda";
    const session = parseAuthSessionResponse(200, SIGNED_IN);
    render(
      <AuthSessionContext.Provider value={{ session, signOut: async () => true }}>
        <RememberingProbe />
      </AuthSessionContext.Provider>,
    );
    expect(screen.getByTestId("remembered")).toHaveTextContent("loading");
  });

  it("signs out with a POST to /auth/logout and returns to the login screen", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, SIGNED_IN))
      .mockResolvedValueOnce(jsonResponse(200, { authenticated: false }))
      .mockResolvedValueOnce(jsonResponse(401, SIGNED_OUT));
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click(await screen.findByText("salir"));
    await waitFor(() => expect(screen.getByTestId("google-login-button")).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toMatch(/\/auth\/logout$/);
    expect(init).toMatchObject({ method: "POST", credentials: "include" });
  });
});
