import { vi } from "vitest";
import { AUTH_SESSION_PATH } from "../api/authClient";

/** What the API answers on `GET /auth/session` for a signed-in Google Workspace operator. */
export const SIGNED_IN_SESSION_BODY = {
  authenticated: true,
  auth_method: "google_session",
  operator: {
    operator_id: "00000000-0000-4000-8000-0000000000aa",
    email: "operador@origenlab.cl",
    display_name: "Operador de prueba",
    role: "admin",
  },
  google_login_enabled: true,
  workspace_domain: "origenlab.cl",
};

/**
 * Answer `GET /auth/session` as a signed-in operator so a full `DashboardApp` render gets
 * past `AuthGate`. Every other request goes to whatever `fetch` was installed before, so the
 * suite's own module mocks keep deciding what the pages see. Undo with `vi.unstubAllGlobals()`.
 */
export function stubSignedInAuthSession(): void {
  const previousFetch = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (new URL(url, "http://localhost").pathname.endsWith(AUTH_SESSION_PATH)) {
        return Promise.resolve(
          new Response(JSON.stringify(SIGNED_IN_SESSION_BODY), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return previousFetch(input, init);
    }),
  );
}
