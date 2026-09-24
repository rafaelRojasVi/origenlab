/**
 * Dashboard sign-in client: the current session, the Google login URL, and logout.
 *
 * The browser never sees a token. Sign-in is a full-page navigation to the API's
 * `/auth/google/login`, which sets an HttpOnly session cookie on the way back; this module
 * only asks the API who that cookie resolves to. What the dashboard does with the answer is a
 * convenience for the person using it -- the API refuses every `/v2` read without a resolved
 * operator whatever this screen shows.
 */

import { operatorApiUrl } from "./operatorClient";

export const AUTH_SESSION_PATH = "/auth/session";
export const AUTH_GOOGLE_LOGIN_PATH = "/auth/google/login";
export const AUTH_LOGOUT_PATH = "/auth/logout";

export interface AuthOperator {
  operatorId: string;
  email: string;
  displayName: string;
  role: string;
}

export type AuthSessionState =
  | { kind: "loading" }
  | { kind: "signed_in"; operator: AuthOperator; method: string }
  | {
      kind: "signed_out";
      googleLoginEnabled: boolean;
      workspaceDomain: string | null;
      detail: string | null;
    }
  /**
   * The API has no sign-in surface: it is the V1 deployment, or a V2 API behind a proxy
   * that does not list `/auth/*` yet. The dashboard behaves exactly as it did before
   * sign-in existed, behind Cloudflare Access.
   */
  | { kind: "not_configured" }
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
    return {
      kind: "signed_in",
      method: asString(data.auth_method) ?? "unknown",
      operator: {
        operatorId,
        email,
        displayName: asString(op.display_name) ?? email,
        role: asString(op.role) ?? "viewer",
      },
    };
  }
  if (status === 401) {
    return {
      kind: "signed_out",
      googleLoginEnabled: data.google_login_enabled === true,
      workspaceDomain: asString(data.workspace_domain),
      detail: asString(data.detail),
    };
  }
  if (status === 404) {
    return { kind: "not_configured" };
  }
  // The production Worker answers an unlisted path with 403 `path_not_allowed` before the
  // API is ever asked: a proxy deployed before this dashboard means "no sign-in here yet".
  const error = (data.error && typeof data.error === "object" ? data.error : {}) as Record<
    string,
    unknown
  >;
  if (status === 403 && error.code === "path_not_allowed") {
    return { kind: "not_configured" };
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

export async function logout(): Promise<void> {
  await fetch(operatorApiUrl(AUTH_LOGOUT_PATH), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
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
