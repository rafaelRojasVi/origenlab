import { createContext, useContext } from "react";
import type { AuthSessionState } from "../api/authClient";

export interface AuthSessionContextValue {
  session: AuthSessionState;
  /**
   * End the session. Resolves true once the server confirmed the revocation; false when it
   * could not, in which case the session stays signed in and a message says so.
   */
  signOut: () => Promise<boolean>;
  /** Back to the profile screen of a shared sign-in; Google stays signed in. */
  switchProfile?: () => Promise<void>;
}

/**
 * Outside an `AuthGate` (component tests, the placeholder shell) there is no confirmed
 * session: no operator chip, no logout button.
 */
const DEFAULT_VALUE: AuthSessionContextValue = {
  session: { kind: "loading" },
  signOut: async () => false,
  switchProfile: async () => undefined,
};

export const AuthSessionContext = createContext<AuthSessionContextValue>(DEFAULT_VALUE);

export function useAuthSession(): AuthSessionContextValue {
  return useContext(AuthSessionContext);
}
