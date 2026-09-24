import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  fetchAuthSession,
  googleLoginUrl,
  loginErrorMessage,
  logout,
  readLoginError,
  type AuthSessionState,
} from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";

function loginErrorFromUrl(): string | null {
  return typeof window === "undefined" ? null : readLoginError(window.location.search);
}

/**
 * Decide, before any dashboard data is requested, whether this browser may see the
 * dashboard: signed in, or on a deployment with no sign-in surface at all.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSessionState>({ kind: "loading" });
  const [loginError] = useState<string | null>(loginErrorFromUrl);

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

  const signOut = useCallback(async () => {
    try {
      await logout();
    } finally {
      await refresh();
    }
  }, [refresh]);

  if (session.kind === "loading") {
    return (
      <main
        className="flex min-h-screen items-center justify-center bg-[var(--color-surface)] text-sm text-slate-500"
        data-testid="auth-loading"
      >
        Verificando sesión…
      </main>
    );
  }
  if (session.kind === "signed_out") {
    return <LoginScreen session={session} loginError={loginError} />;
  }
  if (session.kind === "error") {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[var(--color-surface)] p-8 text-center">
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
    <AuthSessionContext.Provider value={{ session, signOut }}>{children}</AuthSessionContext.Provider>
  );
}

function LoginScreen({
  session,
  loginError,
}: {
  session: Extract<AuthSessionState, { kind: "signed_out" }>;
  loginError: string | null;
}) {
  useEffect(() => {
    document.title = "Iniciar sesión · OrigenLab";
  }, []);

  const domain = session.workspaceDomain ?? "origenlab.cl";

  return (
    <main
      className="flex min-h-screen items-center justify-center bg-[var(--color-surface)] p-6"
      data-testid="login-screen"
    >
      <div className="w-full max-w-sm rounded-2xl bg-[var(--color-card)] p-8 shadow-sm ring-1 ring-[var(--color-border)]">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">OrigenLab</p>
        <h1 className="mt-1 text-xl font-semibold text-slate-900">Centro operador</h1>
        <p className="mt-2 text-sm text-slate-600">
          Ingresa con tu cuenta de Google Workspace <strong>@{domain}</strong>.
        </p>

        {loginError ? (
          <p
            role="alert"
            className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800 ring-1 ring-inset ring-red-200"
            data-testid="login-error"
          >
            {loginErrorMessage(loginError)}
          </p>
        ) : null}

        {session.googleLoginEnabled ? (
          <a
            href={googleLoginUrl()}
            className="mt-6 flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-700"
            data-testid="google-login-button"
          >
            Iniciar sesión con Google
          </a>
        ) : (
          <p className="mt-6 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700 ring-1 ring-inset ring-slate-200">
            El inicio de sesión con Google no está habilitado en este entorno.
          </p>
        )}

        <p className="mt-4 text-xs text-slate-500">
          Sólo se solicita tu nombre y correo. El panel no accede a Gmail, Drive ni Calendar.
        </p>
      </div>
    </main>
  );
}
