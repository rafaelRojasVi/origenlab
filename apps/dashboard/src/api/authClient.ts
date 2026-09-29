/**
 * Dashboard sign-in client: the current session, the Google login URL, logout, and the
 * operator-profile screen behind a shared Workspace sign-in.
 *
 * The browser never sees a token. Sign-in is a full-page navigation to the API's
 * `/auth/google/login`, which sets an HttpOnly session cookie on the way back; this module
 * only asks the API who that cookie resolves to. What the dashboard does with the answer is a
 * convenience for the person using it -- the API refuses every `/v2` read without a resolved
 * operator whatever this screen shows.
 *
 * A shared Workspace account signs in as a *principal*; the API then answers
 * `state: "profile_required"` until a person picks their profile and enters their PIN. The
 * PIN goes to the API in one POST body and nowhere else: it is not stored, logged or kept in
 * state after the request, and the API alone decides whether it is right.
 */

import { operatorApiUrl } from "./operatorClient";

export const AUTH_SESSION_PATH = "/auth/session";
export const AUTH_GOOGLE_LOGIN_PATH = "/auth/google/login";
export const AUTH_LOGOUT_PATH = "/auth/logout";
export const AUTH_PROFILES_PATH = "/auth/profiles";
export const AUTH_PROFILE_SELECT_PATH = "/auth/profile/select";
export const AUTH_PROFILE_CLEAR_PATH = "/auth/profile/clear";
/** Local development only; the API mounts it only on a loopback database outside production. */
export const AUTH_DEV_PRINCIPAL_PATH = "/auth/dev/principal-session";

/** The only paths this module ever POSTs to. */
const AUTH_POST_PATHS = [
  AUTH_LOGOUT_PATH,
  AUTH_PROFILE_SELECT_PATH,
  AUTH_PROFILE_CLEAR_PATH,
  AUTH_DEV_PRINCIPAL_PATH,
] as const;
type AuthPostPath = (typeof AUTH_POST_PATHS)[number];

export interface AuthOperator {
  operatorId: string;
  email: string;
  displayName: string;
  role: string;
}

/** The operator profile selected behind a shared sign-in, as the API labels it. */
export interface AuthProfile {
  id: string;
  displayName: string;
  roleLabel: string;
}

export type AuthSessionState =
  | { kind: "loading" }
  | {
      kind: "signed_in";
      operator: AuthOperator;
      method: string;
      /** Present only for a profile selected behind a shared sign-in. */
      profile?: AuthProfile | null;
      canSwitchProfile?: boolean;
    }
  | {
      kind: "signed_out";
      googleLoginEnabled: boolean;
      workspaceDomain: string | null;
      detail: string | null;
      devProfileLoginEnabled?: boolean;
    }
  /** Google has signed in a shared account; a person must now pick their profile. */
  | { kind: "profile_required"; principalEmail: string | null; profileExpired: boolean }
  /**
   * The session could not be confirmed. The gate fails closed on this: 403, 404, 5xx, a
   * malformed body and a network failure all land here, and none of them renders the
   * dashboard. There is deliberately no "sign-in not configured, render anyway" state.
   */
  | { kind: "error"; message: string };

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function parseAuthSessionResponse(status: number, body: unknown): AuthSessionState {
  const data = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  if (status === 200 && data.authenticated === true) {
    const op = (data.operator && typeof data.operator === "object" ? data.operator : {}) as Record<
      string,
      unknown
    >;
    const email = asString(op.email);
    const operatorId = asString(op.operator_id);
    if (!email || !operatorId) {
      return { kind: "error", message: "Respuesta de sesión incompleta" };
    }
    const rawProfile = (data.profile && typeof data.profile === "object" ? data.profile : null) as
      | Record<string, unknown>
      | null;
    const profileId = rawProfile ? asString(rawProfile.id) : null;
    return {
      kind: "signed_in",
      method: asString(data.auth_method) ?? "unknown",
      operator: {
        operatorId,
        email,
        displayName: asString(op.display_name) ?? email,
        role: asString(op.role) ?? "viewer",
      },
      profile:
        rawProfile && profileId
          ? {
              id: profileId,
              displayName: asString(rawProfile.display_name) ?? "",
              roleLabel: asString(rawProfile.role_label) ?? "",
            }
          : null,
      canSwitchProfile: data.can_switch_profile === true,
    };
  }
  if (status === 401 && data.state === "profile_required") {
    const principal = (data.principal && typeof data.principal === "object" ? data.principal : {}) as Record<
      string,
      unknown
    >;
    return {
      kind: "profile_required",
      principalEmail: asString(principal.email),
      profileExpired: data.profile_expired === true,
    };
  }
  if (status === 401) {
    return {
      kind: "signed_out",
      googleLoginEnabled: data.google_login_enabled === true,
      workspaceDomain: asString(data.workspace_domain),
      detail: asString(data.detail),
      devProfileLoginEnabled: data.dev_profile_login_enabled === true,
    };
  }
  if (status === 404) {
    return {
      kind: "error",
      message: "Este entorno no expone la verificación de sesión (HTTP 404). El panel no se abre sin una sesión verificada.",
    };
  }
  if (status === 403) {
    return {
      kind: "error",
      message: "El acceso a la verificación de sesión fue rechazado (HTTP 403). El panel no se abre sin una sesión verificada.",
    };
  }
  return { kind: "error", message: `No se pudo verificar la sesión (HTTP ${status})` };
}

export async function fetchAuthSession(): Promise<AuthSessionState> {
  try {
    const res = await fetch(operatorApiUrl(AUTH_SESSION_PATH), {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    const body: unknown = await res.json().catch(() => null);
    return parseAuthSessionResponse(res.status, body);
  } catch (err) {
    return {
      kind: "error",
      message: err instanceof Error ? err.message : "No se pudo verificar la sesión",
    };
  }
}

export function googleLoginUrl(): string {
  return operatorApiUrl(AUTH_GOOGLE_LOGIN_PATH);
}

/** The one POST site of this module, to one of `AUTH_POST_PATHS` only. */
async function postAuth(path: AuthPostPath, body: Record<string, string> | null): Promise<Response> {
  if (!(AUTH_POST_PATHS as readonly string[]).includes(path)) {
    throw new Error("not a sign-in path");
  }
  return fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers:
      body === null
        ? { Accept: "application/json" }
        : { Accept: "application/json", "Content-Type": "application/json" },
    body: body === null ? undefined : JSON.stringify(body),
  });
}

/**
 * End the session on the server. True only when the API confirmed it revoked the session (a
 * 2xx). A 503 `logout_not_recorded`, any other status or a network failure is false: the
 * session may still be live everywhere, the API left the cookie in place, and the caller must
 * keep showing the signed-in dashboard rather than pretend the person is signed out.
 */
export async function logout(): Promise<boolean> {
  try {
    const res = await postAuth(AUTH_LOGOUT_PATH, null);
    return res.ok;
  } catch {
    return false;
  }
}

/** Shown, and nothing else changes, when the server could not revoke the session. */
export const LOGOUT_FAILED_MESSAGE = "No se pudo cerrar la sesión de forma segura. Intenta nuevamente.";

export interface ProfileCard {
  id: string;
  displayName: string;
  roleLabel: string;
}

export type ProfileListResult =
  | { kind: "ok"; principalEmail: string | null; profiles: ProfileCard[] }
  | { kind: "signed_out" }
  | { kind: "error"; message: string };

export function parseProfilesResponse(status: number, body: unknown): ProfileListResult {
  if (status === 401) {
    return { kind: "signed_out" };
  }
  const data = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  if (status !== 200 || !Array.isArray(data.profiles)) {
    return { kind: "error", message: `No se pudieron cargar los perfiles (HTTP ${status})` };
  }
  const profiles: ProfileCard[] = [];
  for (const item of data.profiles) {
    const row = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
    const id = asString(row.id);
    const displayName = asString(row.display_name);
    if (id && displayName) {
      profiles.push({ id, displayName, roleLabel: asString(row.role_label) ?? "" });
    }
  }
  const principal = (data.principal && typeof data.principal === "object" ? data.principal : {}) as Record<
    string,
    unknown
  >;
  return { kind: "ok", principalEmail: asString(principal.email), profiles };
}

export async function fetchProfiles(): Promise<ProfileListResult> {
  try {
    const res = await fetch(operatorApiUrl(AUTH_PROFILES_PATH), {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    return parseProfilesResponse(res.status, await res.json().catch(() => null));
  } catch (err) {
    return { kind: "error", message: err instanceof Error ? err.message : "No se pudieron cargar los perfiles" };
  }
}

/**
 * `ok`: the API verified the PIN and the session now carries that profile. `refused`: the one
 * answer the API gives to a wrong PIN, an unknown or disabled profile, or a temporary lock --
 * deliberately indistinguishable. `signed_out`: the Google sign-in itself is gone.
 */
export type SelectProfileResult = "ok" | "refused" | "signed_out" | "error";

export async function selectProfile(profileId: string, pin: string): Promise<SelectProfileResult> {
  try {
    const res = await postAuth(AUTH_PROFILE_SELECT_PATH, { profile_id: profileId, pin });
    if (res.status === 200) return "ok";
    const data = (await res.json().catch(() => null)) as Record<string, unknown> | null;
    if (res.status === 401 && data?.detail === "profile_selection_failed") return "refused";
    if (res.status === 401) return "signed_out";
    return "error";
  } catch {
    return "error";
  }
}

/** Back to the profile screen. Keeps the Google sign-in; never signs out of Google. */
export async function clearProfile(): Promise<void> {
  await postAuth(AUTH_PROFILE_CLEAR_PATH, {});
}

/** Local development only: sign in as the API's configured invented principal. */
export async function devPrincipalSignIn(): Promise<void> {
  await postAuth(AUTH_DEV_PRINCIPAL_PATH, null);
}

const LOGIN_ERROR_MESSAGES: Record<string, string> = {
  access_denied: "Cancelaste el inicio de sesión con Google.",
  google_error: "Google no pudo completar el inicio de sesión. Intenta de nuevo.",
  invalid_request: "La solicitud de inicio de sesión no es válida. Intenta de nuevo.",
  invalid_state: "La solicitud de inicio de sesión expiró o no es válida. Intenta de nuevo.",
  token_exchange_failed: "No se pudo verificar la respuesta de Google. Intenta de nuevo.",
  invalid_token: "Google devolvió una identidad que no se pudo validar.",
  email_unverified: "El correo de esta cuenta de Google no está verificado.",
  wrong_domain: "Sólo pueden ingresar cuentas de Google Workspace de OrigenLab.",
  unknown_operator:
    "Tu cuenta no está registrada como operador. Pide a un administrador que te dé acceso.",
  operator_disabled: "Tu acceso de operador está deshabilitado.",
  operator_not_permitted: "Tu rol de operador no permite usar el panel.",
};

/** The `login_error` code the API's callback put in the URL, or null. */
export function readLoginError(search: string): string | null {
  const code = new URLSearchParams(search).get("login_error");
  return code && /^[a-z_]{1,40}$/.test(code) ? code : null;
}

export function loginErrorMessage(code: string): string {
  return LOGIN_ERROR_MESSAGES[code] ?? "No se pudo iniciar sesión.";
}
