import { createContext, useContext } from "react";
import type { AuthSessionState } from "../api/authClient";

export interface AuthSessionContextValue {
  session: AuthSessionState;
  signOut: () => Promise<void>;
  /** Back to the profile screen of a shared sign-in; Google stays signed in. */
  switchProfile?: () => Promise<void>;
}

/**
 * Outside an `AuthGate` (component tests, the placeholder shell) there is no confirmed
 * session: no operator chip, no logout button.
 */
const DEFAULT_VALUE: AuthSessionContextValue = {
  session: { kind: "loading" },
  signOut: async () => undefined,
  switchProfile: async () => undefined,
};

export const AuthSessionContext = createContext<AuthSessionContextValue>(DEFAULT_VALUE);

export function useAuthSession(): AuthSessionContextValue {
  return useContext(AuthSessionContext);
}
