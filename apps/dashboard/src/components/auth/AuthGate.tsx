import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  clearProfile,
  devPrincipalSignIn,
  fetchAuthSession,
  googleLoginUrl,
  loginErrorMessage,
  LOGOUT_FAILED_MESSAGE,
  logout,
  readLoginError,
  type AuthSessionState,
} from "../../api/authClient";
import { SESSION_REFUSED_EVENT } from "../../api/operatorClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../../crm/useResource";
import { ProfileSelector } from "./ProfileSelector";

function loginErrorFromUrl(): string | null {
  return typeof window === "undefined" ? null : readLoginError(window.location.search);
}

/**
 * Decide, before any dashboard data is requested, whether this browser may see the
 * dashboard. Only a confirmed signed-in session renders it; every other answer — 401, 403,
 * 404, 5xx, a malformed body, a network failure — fails closed.
 *
 * A shared Workspace sign-in without a selected profile (`profile_required`) renders the
 * profile screen and nothing else: no dashboard component mounts, so no CRM data is asked for.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSessionState>({ kind: "loading" });
  const [loginError] = useState<string | null>(loginErrorFromUrl);
  const [logoutFailed, setLogoutFailed] = useState(false);

  useEffect(() => {
    // Drop `?login_error=` from the address bar so a reload does not repeat the message.
    // An effect, not the state initializer: StrictMode may run an initializer twice.
    if (loginError && typeof window !== "undefined") {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
    }
  }, [loginError]);

  const refresh = useCallback(async () => {
    setSession({ kind: "loading" });
    setSession(await fetchAuthSession());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A 401 from any CRM request means the session or the selected profile no longer holds
  // (signed out elsewhere, role/PIN/status changed). Re-ask the API rather than show stale data.
  const signedIn = session.kind === "signed_in";
  useEffect(() => {
    if (!signedIn || typeof window === "undefined") return undefined;
    const onRefused = () => void refresh();
    window.addEventListener(SESSION_REFUSED_EVENT, onRefused);
    return () => window.removeEventListener(SESSION_REFUSED_EVENT, onRefused);
  }, [signedIn, refresh]);

  // Pages remember their last answer per operator and profile (`useResource`). Whenever no
  // session is confirmed — loading, signed out, profile screen, error — that memory goes, so
  // nothing from before a sign-out or a profile change can be shown after it.
  useEffect(() => {
    if (!signedIn) clearResourceCache();
  }, [signedIn]);

  // "Cerrar sesión". Nothing on this side changes until the server confirms it revoked the
  // session: on a failure the dashboard (or the profile screen) stays exactly as it was, the
  // cookie is still in place, and a message asks to try again. Only after a confirmed
  // revocation is the in-memory session dropped (the dashboard keeps no other browser state).
  const signOut = useCallback(async () => {
    const revoked = await logout();
    if (!revoked) {
      setLogoutFailed(true);
      return false;
    }
    setLogoutFailed(false);
    await refresh();
    return true;
  }, [refresh]);

  const logoutAlert = logoutFailed ? (
    <div className="fixed inset-x-0 top-2 z-50 flex justify-center px-4" data-testid="logout-failed">
      <p
        role="alert"
        className="flex max-w-lg items-center gap-3 rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-[13px] text-bad shadow-sm"
      >
        <span>{LOGOUT_FAILED_MESSAGE}</span>
        <button
          type="button"
          onClick={() => setLogoutFailed(false)}
          className="shrink-0 rounded px-1.5 text-[12px] font-medium hover:bg-bad/10"
          aria-label="Cerrar aviso"
        >
          ✕
        </button>
      </p>
    </div>
  ) : null;

  // "Cambiar perfil": back to the profile screen. The Google sign-in stays; only the selected
  // profile is dropped, and the dashboard unmounts before anything else is requested.
  const switchProfile = useCallback(async () => {
    setSession({ kind: "loading" });
    try {
      await clearProfile();
    } finally {
      await refresh();
    }
  }, [refresh]);

  if (session.kind === "loading") {
    return (
      <main
        className="flex min-h-screen items-center justify-center bg-canvas text-[13px] text-ink-faint"
        data-testid="auth-loading"
      >
        Verificando sesión…
      </main>
    );
  }
  if (session.kind === "signed_out") {
    return <LoginScreen session={session} loginError={loginError} onDevSignIn={refresh} />;
  }
  if (session.kind === "profile_required") {
    return (
      <>
      {logoutAlert}
      <ProfileSelector
        principalEmail={session.principalEmail}
        profileExpired={session.profileExpired}
        onSelected={refresh}
        onSignedOut={refresh}
        onSignOut={signOut}
      />
      </>
    );
  }
  if (session.kind !== "signed_in") {
    return (
      <main
        className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[var(--color-surface)] p-8 text-center"
        data-testid="auth-error"
      >
        <h1 className="text-lg font-semibold text-slate-900">No se pudo verificar la sesión</h1>
        <p className="max-w-md text-sm text-slate-600">{session.message}</p>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-700"
        >
          Reintentar
        </button>
      </main>
    );
  }
  return (
    <AuthSessionContext.Provider value={{ session, signOut, switchProfile }}>
      {logoutAlert}
      {children}
    </AuthSessionContext.Provider>
  );
}

function LoginScreen({
  session,
  loginError,
  onDevSignIn,
}: {
  session: Extract<AuthSessionState, { kind: "signed_out" }>;
  loginError: string | null;
  onDevSignIn: () => Promise<void>;
}) {
  useEffect(() => {
    document.title = "Iniciar sesión · OrigenLab";
  }, []);

  const domain = session.workspaceDomain ?? "origenlab.cl";

  return (
    <main className="flex min-h-screen flex-col bg-canvas text-ink" data-testid="login-screen">
      <div className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-[22rem]">
          <div className="mb-6 flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-600 text-xs font-bold text-white"
            >
              O
            </span>
            <span className="text-[15px] font-semibold tracking-tight">OrigenLab</span>
            <span aria-hidden="true" className="h-4 w-px bg-line-strong" />
            <span className="text-[13px] text-ink-muted">Centro operador</span>
          </div>
          <div className="rounded-lg border border-line bg-canvas-raised p-6 shadow-[0_1px_2px_rgb(24_24_27/0.05)]">
            <h1 className="text-base font-semibold">Iniciar sesión</h1>
            <p className="mt-1 text-[13px] leading-5 text-ink-muted">
              Ingresa con tu cuenta de Google Workspace <strong className="font-semibold text-ink">@{domain}</strong>.
            </p>

            {loginError ? (
              <p
                role="alert"
                className="mt-4 rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-[13px] text-bad"
                data-testid="login-error"
              >
                {loginErrorMessage(loginError)}
              </p>
            ) : null}

            {session.googleLoginEnabled ? (
              <a
                href={googleLoginUrl()}
                className="mt-5 flex h-9 w-full items-center justify-center gap-2 rounded-md border border-line-strong bg-canvas-raised px-4 text-[13px] font-medium text-ink shadow-sm transition-colors hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
                data-testid="google-login-button"
              >
                <svg aria-hidden="true" viewBox="0 0 18 18" className="h-4 w-4">
                  <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
                  <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
                  <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
                  <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
                </svg>
                Iniciar sesión con Google
              </a>
            ) : (
              <p className="mt-5 rounded-md border border-line bg-canvas-sunken px-3 py-2 text-[13px] text-ink-muted">
                El inicio de sesión con Google no está habilitado en este entorno.
              </p>
            )}
          </div>
          {session.devProfileLoginEnabled ? (
            <button
              type="button"
              onClick={() => void devPrincipalSignIn().finally(() => void onDevSignIn())}
              className="mt-3 flex h-9 w-full items-center justify-center rounded-md border border-dashed border-warn/50 bg-warn-bg px-4 text-[13px] font-medium text-warn hover:bg-warn-bg/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
              data-testid="dev-profile-login-button"
            >
              Entrar con la cuenta de prueba local
            </button>
          ) : null}
          <p className="mt-4 text-[11px] leading-4 text-ink-faint">
            Sólo se solicita tu nombre y correo. El panel no accede a Gmail, Drive ni Calendar.
          </p>
        </div>
      </div>
    </main>
  );
}
