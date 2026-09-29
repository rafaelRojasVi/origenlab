import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseAuthSessionResponse, parseProfilesResponse } from "../../api/authClient";
import { useAuthSession } from "../../context/AuthSessionContext";
import { AuthGate } from "./AuthGate";

// Every name, address, id and PIN below is invented.
const PRINCIPAL = "compartida@example.test";
const PROFILES = {
  principal: { email: PRINCIPAL },
  profiles: [
    { id: "00000000-0000-4000-8000-000000000001", display_name: "Ana", role_label: "Administración" },
    { id: "00000000-0000-4000-8000-000000000002", display_name: "Bruno", role_label: "Administración" },
    { id: "00000000-0000-4000-8000-000000000003", display_name: "Carla", role_label: "Ventas" },
  ],
};
const PROFILE_REQUIRED = {
  authenticated: false,
  state: "profile_required",
  detail: "profile_required",
  principal: { email: PRINCIPAL },
  profile_expired: false,
  google_login_enabled: true,
  workspace_domain: "origenlab.cl",
};
const SIGNED_IN_AS_CARLA = {
  authenticated: true,
  state: "signed_in",
  auth_method: "google_profile",
  operator: { operator_id: PROFILES.profiles[2].id, email: PRINCIPAL, display_name: "Carla", role: "sales" },
  profile: { id: PROFILES.profiles[2].id, display_name: "Carla", role_label: "Ventas" },
  can_switch_profile: true,
  google_login_enabled: true,
  workspace_domain: "origenlab.cl",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function Probe() {
  const { session, switchProfile, signOut } = useAuthSession();
  return (
    <div data-testid="dashboard">
      {session.kind === "signed_in" ? `${session.operator.displayName}|${session.profile?.roleLabel}` : session.kind}
      <button type="button" onClick={() => void switchProfile?.()}>
        cambiar
      </button>
      <button type="button" onClick={() => void signOut()}>
        salir
      </button>
    </div>
  );
}

describe("parsing the shared sign-in answers", () => {
  it("reads profile_required as its own state, never as signed in", () => {
    expect(parseAuthSessionResponse(401, PROFILE_REQUIRED)).toEqual({
      kind: "profile_required",
      principalEmail: PRINCIPAL,
      profileExpired: false,
    });
    expect(parseAuthSessionResponse(401, { ...PROFILE_REQUIRED, profile_expired: true })).toMatchObject({
      profileExpired: true,
    });
  });

  it("reads the selected profile of a signed-in session", () => {
    expect(parseAuthSessionResponse(200, SIGNED_IN_AS_CARLA)).toMatchObject({
      kind: "signed_in",
      method: "google_profile",
      profile: { id: PROFILES.profiles[2].id, displayName: "Carla", roleLabel: "Ventas" },
      canSwitchProfile: true,
    });
  });

  it("keeps only well-formed profile cards", () => {
    expect(
      parseProfilesResponse(200, { profiles: [{ id: "a", display_name: "A", role_label: "Ventas" }, { id: "b" }, 7] }),
    ).toEqual({ kind: "ok", principalEmail: null, profiles: [{ id: "a", displayName: "A", roleLabel: "Ventas" }] });
    expect(parseProfilesResponse(401, {}).kind).toBe("signed_out");
    expect(parseProfilesResponse(500, {}).kind).toBe("error");
  });
});

describe("the profile screen", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function route(handlers: Record<string, (init?: RequestInit) => Response>) {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      const handler = handlers[`${(init?.method ?? "GET").toUpperCase()} ${path}`];
      if (!handler) throw new Error(`unexpected ${init?.method ?? "GET"} ${path}`);
      return handler(init);
    });
  }

  function calls(): string[] {
    return fetchMock.mock.calls.map(([input, init]) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      return `${(init?.method ?? "GET").toUpperCase()} ${new URL(url, "http://localhost").pathname}`;
    });
  }

  it("shows the three profiles and no CRM data before a profile is chosen", async () => {
    route({
      "GET /auth/session": () => json(401, PROFILE_REQUIRED),
      "GET /auth/profiles": () => json(200, PROFILES),
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByRole("heading", { name: "¿Quién está usando el CRM?" })).toBeInTheDocument();
    const names = (await screen.findAllByTestId("profile-card-name")).map((n) => n.textContent);
    expect(names).toEqual(["Ana", "Bruno", "Carla"]);
    expect(screen.getByText(PRINCIPAL)).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard")).not.toBeInTheDocument();
    expect(calls()).toEqual(["GET /auth/session", "GET /auth/profiles"]);
  });

  it("sends the PIN once, in the body, and opens the dashboard as the chosen profile", async () => {
    let session = PROFILE_REQUIRED as Record<string, unknown>;
    let selectBody: unknown = null;
    route({
      "GET /auth/session": () => json(session === PROFILE_REQUIRED ? 401 : 200, session),
      "GET /auth/profiles": () => json(200, PROFILES),
      "POST /auth/profile/select": (init) => {
        selectBody = JSON.parse(String(init?.body));
        session = SIGNED_IN_AS_CARLA;
        return json(200, { authenticated: true });
      },
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click((await screen.findAllByTestId("profile-card"))[2]);
    const dialog = await screen.findByRole("dialog", { name: "Carla" });
    const input = within(dialog).getByLabelText("PIN personal");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    fireEvent.change(input, { target: { value: "48a29-13" } });
    expect(input).toHaveValue("482913");
    fireEvent.click(within(dialog).getByTestId("pin-submit"));
    expect(await screen.findByTestId("dashboard")).toHaveTextContent("Carla|Ventas");
    expect(selectBody).toEqual({ profile_id: PROFILES.profiles[2].id, pin: "482913" });
    const select = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(select?.[1]?.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(select?.[1]?.credentials).toBe("include");
  });

  it("answers every refusal with the same message, clears the PIN, and never names the reason", async () => {
    route({
      "GET /auth/session": () => json(401, PROFILE_REQUIRED),
      "GET /auth/profiles": () => json(200, PROFILES),
      "POST /auth/profile/select": () => json(401, { detail: "profile_selection_failed" }),
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click((await screen.findAllByTestId("profile-card"))[0]);
    const input = await screen.findByTestId("pin-input");
    fireEvent.change(input, { target: { value: "111111" } });
    fireEvent.click(screen.getByTestId("pin-submit"));
    const error = await screen.findByTestId("pin-error");
    expect(error).toHaveTextContent("No se pudo abrir el perfil. Revisa el PIN");
    expect(error.textContent).not.toMatch(/no existe|deshabilitad|bloqueado ahora|incorrecto/i);
    expect(input).toHaveValue("");
    expect(document.body.textContent).not.toContain("111111");
    expect(screen.queryByTestId("dashboard")).not.toBeInTheDocument();
  });

  it("closes the dialog with Escape or Cancelar without sending anything", async () => {
    route({
      "GET /auth/session": () => json(401, PROFILE_REQUIRED),
      "GET /auth/profiles": () => json(200, PROFILES),
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click((await screen.findAllByTestId("profile-card"))[1]);
    fireEvent.keyDown(await screen.findByTestId("pin-dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("pin-dialog")).not.toBeInTheDocument());
    fireEvent.click((await screen.findAllByTestId("profile-card"))[1]);
    fireEvent.click(await screen.findByTestId("pin-cancel"));
    await waitFor(() => expect(screen.queryByTestId("pin-dialog")).not.toBeInTheDocument());
    expect(calls().filter((c) => c.startsWith("POST"))).toEqual([]);
  });

  it("Cambiar perfil drops the profile, keeps Google, and returns to the profile screen", async () => {
    let session = SIGNED_IN_AS_CARLA as Record<string, unknown>;
    route({
      "GET /auth/session": () => json(session === SIGNED_IN_AS_CARLA ? 200 : 401, session),
      "GET /auth/profiles": () => json(200, PROFILES),
      "POST /auth/profile/clear": () => {
        session = PROFILE_REQUIRED;
        return json(200, { state: "profile_required" });
      },
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("dashboard")).toHaveTextContent("Carla|Ventas");
    await act(async () => {
      fireEvent.click(screen.getByText("cambiar"));
    });
    expect(await screen.findByRole("heading", { name: "¿Quién está usando el CRM?" })).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard")).not.toBeInTheDocument();
    expect(calls()).toContain("POST /auth/profile/clear");
    expect(calls()).not.toContain("POST /auth/logout");
  });

  it("returns to the profile screen as soon as a CRM request is refused mid-session", async () => {
    let session = SIGNED_IN_AS_CARLA as Record<string, unknown>;
    route({
      "GET /auth/session": () => json(session === SIGNED_IN_AS_CARLA ? 200 : 401, session),
      "GET /auth/profiles": () => json(200, PROFILES),
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("dashboard")).toBeInTheDocument();
    session = { ...PROFILE_REQUIRED, profile_expired: true };
    const { notifyIfSessionRefused } = await import("../../api/operatorClient");
    await act(async () => {
      notifyIfSessionRefused(401);
    });
    expect(await screen.findByTestId("profile-expired")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard")).not.toBeInTheDocument();
  });

  it("Cerrar sesión on the profile screen ends the whole session", async () => {
    let signedOut = false;
    route({
      "GET /auth/session": () =>
        signedOut ? json(401, { authenticated: false, google_login_enabled: true }) : json(401, PROFILE_REQUIRED),
      "GET /auth/profiles": () => json(200, PROFILES),
      "POST /auth/logout": () => {
        signedOut = true;
        return json(200, { authenticated: false });
      },
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click(await screen.findByTestId("profile-sign-out"));
    expect(await screen.findByTestId("login-screen")).toBeInTheDocument();
  });

  it("a failed logout on the profile screen keeps it, says so, and a retry signs out", async () => {
    let logoutCalls = 0;
    let signedOut = false;
    route({
      "GET /auth/session": () =>
        signedOut ? json(401, { authenticated: false, google_login_enabled: true }) : json(401, PROFILE_REQUIRED),
      "GET /auth/profiles": () => json(200, PROFILES),
      "POST /auth/logout": () => {
        logoutCalls += 1;
        if (logoutCalls === 1) return json(503, { detail: "logout_not_recorded" });
        signedOut = true;
        return json(200, { authenticated: false });
      },
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click(await screen.findByTestId("profile-sign-out"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se pudo cerrar la sesión de forma segura. Intenta nuevamente.",
    );
    expect(screen.getAllByTestId("profile-card")).toHaveLength(3);
    expect(screen.queryByTestId("login-screen")).toBeNull();
    fireEvent.click(screen.getByTestId("profile-sign-out"));
    expect(await screen.findByTestId("login-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("logout-failed")).toBeNull();
    expect(logoutCalls).toBe(2);
  });

  it("a failed logout with a profile selected keeps the dashboard, and a retry signs out", async () => {
    let logoutCalls = 0;
    let signedOut = false;
    route({
      "GET /auth/session": () =>
        signedOut ? json(401, { authenticated: false, google_login_enabled: true }) : json(200, SIGNED_IN_AS_CARLA),
      "POST /auth/logout": () => {
        logoutCalls += 1;
        if (logoutCalls === 1) return json(503, { detail: "logout_not_recorded" });
        signedOut = true;
        return json(200, { authenticated: false });
      },
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    fireEvent.click(await screen.findByText("salir"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se pudo cerrar la sesión de forma segura. Intenta nuevamente.",
    );
    expect(screen.getByTestId("dashboard")).toHaveTextContent("Carla|Ventas");
    expect(calls().filter((c) => c === "GET /auth/session")).toHaveLength(1);
    fireEvent.click(screen.getByText("salir"));
    expect(await screen.findByTestId("login-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("dashboard")).toBeNull();
  });

  it("says so when the account has no profiles, and shows no cards", async () => {
    route({
      "GET /auth/session": () => json(401, PROFILE_REQUIRED),
      "GET /auth/profiles": () => json(200, { ...PROFILES, profiles: [] }),
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("profiles-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-card")).not.toBeInTheDocument();
  });

  it("offers the local test sign-in only when the API says it exists", async () => {
    route({
      "GET /auth/session": () =>
        json(401, { authenticated: false, state: "signed_out", google_login_enabled: false, dev_profile_login_enabled: true }),
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("dev-profile-login-button")).toBeInTheDocument();
  });

  it("never offers the local test sign-in otherwise", async () => {
    route({
      "GET /auth/session": () => json(401, { authenticated: false, google_login_enabled: true }),
    });
    render(
      <AuthGate>
        <Probe />
      </AuthGate>,
    );
    expect(await screen.findByTestId("login-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("dev-profile-login-button")).not.toBeInTheDocument();
  });
});

describe("the CRM header of a profile session", () => {
  async function renderTopBar(session: ReturnType<typeof parseAuthSessionResponse>, signOutResult = true) {
    const { TopBar } = await import("../../crm/CrmApp");
    const { AuthSessionContext } = await import("../../context/AuthSessionContext");
    const switchProfile = vi.fn(async () => undefined);
    const signOut = vi.fn(async () => signOutResult);
    render(
      <AuthSessionContext.Provider value={{ session, signOut, switchProfile }}>
        <TopBar onMenu={() => undefined} menuOpen={false} />
      </AuthSessionContext.Provider>,
    );
    return { switchProfile, signOut };
  }

  it("shows the selected profile and its role, with Cambiar perfil and Cerrar sesión", async () => {
    const { switchProfile, signOut } = await renderTopBar(parseAuthSessionResponse(200, SIGNED_IN_AS_CARLA));
    expect(screen.getByTestId("crm-operator-name")).toHaveTextContent("Carla");
    expect(screen.getByTestId("crm-operator-role")).toHaveTextContent("Ventas");
    fireEvent.click(screen.getByTestId("crm-switch-profile"));
    expect(switchProfile).toHaveBeenCalledTimes(1);
    expect(signOut).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("crm-sign-out"));
    expect(screen.getByTestId("crm-sign-out")).toHaveTextContent("Cerrar sesión");
    expect(signOut).toHaveBeenCalledTimes(1);
    // A shared account never offers "Cambiar cuenta": that would sign Google out.
    expect(screen.queryByTestId("crm-switch-account")).toBeNull();
  });

  it("Cambiar cuenta goes to Google only after a confirmed logout", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const individual = parseAuthSessionResponse(200, {
      authenticated: true,
      auth_method: "google_session",
      operator: { operator_id: "00000000-0000-4000-8000-0000000000aa", email: "persona@example.test", display_name: "Persona", role: "admin" },
    });
    const { signOut } = await renderTopBar(individual, false);
    fireEvent.click(screen.getByTestId("crm-switch-account"));
    await waitFor(() => expect(signOut).toHaveBeenCalledTimes(1));
    expect(assign).not.toHaveBeenCalled();
  });

  it("an individual Google operator keeps Cambiar cuenta and gets no profile switch", async () => {
    await renderTopBar(
      parseAuthSessionResponse(200, {
        authenticated: true,
        auth_method: "google_session",
        operator: { operator_id: "o1", email: "persona@example.test", display_name: "Persona", role: "admin" },
      }),
    );
    expect(screen.getByTestId("crm-switch-account")).toBeInTheDocument();
    expect(screen.queryByTestId("crm-switch-profile")).toBeNull();
    expect(screen.getByTestId("crm-operator-role")).toHaveTextContent("Administración");
  });
});
