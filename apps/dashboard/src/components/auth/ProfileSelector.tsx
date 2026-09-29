import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { fetchProfiles, selectProfile, type ProfileCard } from "../../api/authClient";

/**
 * "¿Quién está usando el CRM?" — the profile screen behind a shared Google Workspace sign-in.
 *
 * Google has proven the shared account; this screen asks which person is at the keyboard. The
 * cards come from the API (only the profiles linked to this account) and the PIN is checked by
 * the API alone: this component sends it once, in the request body, and forgets it whatever
 * the answer. Every refusal reads the same, so the screen never says whether a PIN was wrong,
 * a profile disabled or temporarily locked.
 *
 * No CRM data is requested until a profile is selected: this screen renders instead of the
 * dashboard, and the API refuses every CRM route with `profile_required` in the meantime.
 */

const REFUSED_MESSAGE =
  "No se pudo abrir el perfil. Revisa el PIN e inténtalo de nuevo. Después de varios intentos fallidos, el acceso queda bloqueado por unos minutos.";
const PIN_MAX_LENGTH = 12;

const AVATAR_TONES = [
  "bg-brand-600 text-white",
  "bg-indigo-600 text-white",
  "bg-amber-500 text-stone-950",
  "bg-rose-600 text-white",
  "bg-sky-600 text-white",
  "bg-stone-700 text-white",
];

function initialOf(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; principalEmail: string | null; profiles: ProfileCard[] }
  | { kind: "error"; message: string };

export function ProfileSelector({
  principalEmail,
  profileExpired,
  onSelected,
  onSignedOut,
  onSignOut,
}: {
  principalEmail: string | null;
  profileExpired: boolean;
  /** The API accepted the PIN: the session now carries the profile. */
  onSelected: () => void | Promise<void>;
  /** The Google sign-in itself is no longer valid. */
  onSignedOut: () => void | Promise<void>;
  /** "Cerrar sesión": end the whole application session. */
  onSignOut: () => unknown;
}) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [chosen, setChosen] = useState<{ card: ProfileCard; tone: string } | null>(null);

  useEffect(() => {
    document.title = "Elegir perfil · OrigenLab";
  }, []);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    const result = await fetchProfiles();
    if (result.kind === "signed_out") {
      await onSignedOut();
      return;
    }
    setState(result.kind === "ok" ? { kind: "ready", principalEmail: result.principalEmail, profiles: result.profiles } : result);
  }, [onSignedOut]);

  useEffect(() => {
    void load();
  }, [load]);

  const account = state.kind === "ready" ? state.principalEmail ?? principalEmail : principalEmail;

  return (
    <main className="flex min-h-screen flex-col overflow-x-hidden bg-canvas text-ink" data-testid="profile-selector">
      <header className="flex items-center gap-2 px-4 pt-6 sm:px-8">
        <span
          aria-hidden="true"
          className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-600 text-xs font-bold text-white"
        >
          O
        </span>
        <span className="text-[15px] font-semibold tracking-tight">OrigenLab</span>
        <span aria-hidden="true" className="h-4 w-px bg-line-strong" />
        <span className="text-[13px] text-ink-muted">Panel comercial</span>
      </header>

      <div className="flex flex-1 items-start justify-center px-4 py-10 sm:items-center sm:px-8">
        <div className="w-full max-w-2xl">
          <h1 className="text-center text-2xl font-semibold tracking-tight sm:text-[28px]">¿Quién está usando el CRM?</h1>
          <p className="mt-2 text-center text-[13px] leading-5 text-ink-muted">
            Elige tu perfil e ingresa tu PIN personal.
            {account ? (
              <>
                {" "}
                Cuenta de Google: <strong className="break-all font-medium text-ink">{account}</strong>
              </>
            ) : null}
          </p>

          {profileExpired ? (
            <p
              role="status"
              className="mx-auto mt-5 max-w-md rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-center text-[13px] text-warn"
              data-testid="profile-expired"
            >
              Tu perfil cambió o su sesión terminó. Vuelve a elegirlo para continuar.
            </p>
          ) : null}

          <section aria-label="Perfiles" className="mt-8">
            {state.kind === "loading" ? (
              <p className="text-center text-[13px] text-ink-faint" data-testid="profiles-loading">
                Cargando perfiles…
              </p>
            ) : state.kind === "error" ? (
              <div className="flex flex-col items-center gap-3 text-center" data-testid="profiles-error">
                <p className="text-[13px] text-bad">{state.message}</p>
                <button
                  type="button"
                  onClick={() => void load()}
                  className="h-9 rounded-md border border-line-strong bg-canvas-raised px-4 text-[13px] font-medium hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
                >
                  Reintentar
                </button>
              </div>
            ) : state.profiles.length === 0 ? (
              <p
                className="mx-auto max-w-md rounded-md border border-line bg-canvas-raised px-4 py-3 text-center text-[13px] text-ink-muted"
                data-testid="profiles-empty"
              >
                Esta cuenta no tiene perfiles disponibles. Pide a un administrador que te asigne uno.
              </p>
            ) : (
              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4" data-testid="profile-cards">
                {state.profiles.map((card, i) => {
                  const tone = AVATAR_TONES[i % AVATAR_TONES.length];
                  return (
                    <li key={card.id} className="min-w-0">
                      <button
                        type="button"
                        onClick={() => setChosen({ card, tone })}
                        className="group flex w-full min-w-0 flex-col items-center gap-3 rounded-xl border border-line bg-canvas-raised px-3 py-5 shadow-[0_1px_2px_rgb(24_24_27/0.05)] transition hover:-translate-y-0.5 hover:border-brand-600/40 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 sm:py-6"
                        data-testid="profile-card"
                      >
                        <span
                          aria-hidden="true"
                          className={`flex h-16 w-16 items-center justify-center rounded-2xl text-2xl font-semibold shadow-sm sm:h-20 sm:w-20 sm:text-3xl ${tone}`}
                        >
                          {initialOf(card.displayName)}
                        </span>
                        <span className="w-full min-w-0">
                          <span className="block truncate text-[15px] font-semibold text-ink" data-testid="profile-card-name">
                            {card.displayName}
                          </span>
                          {card.roleLabel ? (
                            <span className="mt-0.5 block truncate text-[12px] text-ink-muted">{card.roleLabel}</span>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <div className="mt-10 flex justify-center">
            <button
              type="button"
              onClick={() => void onSignOut()}
              className="rounded-md px-3 py-1.5 text-[13px] font-medium text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
              data-testid="profile-sign-out"
            >
              Cerrar sesión
            </button>
          </div>
        </div>
      </div>

      {chosen ? (
        <PinDialog
          card={chosen.card}
          tone={chosen.tone}
          onCancel={() => setChosen(null)}
          onSelected={onSelected}
          onSignedOut={onSignedOut}
        />
      ) : null}
    </main>
  );
}

function PinDialog({
  card,
  tone,
  onCancel,
  onSelected,
  onSignedOut,
}: {
  card: ProfileCard;
  tone: string;
  onCancel: () => void;
  onSelected: () => void | Promise<void>;
  onSignedOut: () => void | Promise<void>;
}) {
  const titleId = useId();
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || pin.length === 0) return;
    setBusy(true);
    setError(null);
    const submitted = pin;
    // The PIN leaves component state before the request resolves; nothing keeps a copy.
    setPin("");
    const result = await selectProfile(card.id, submitted);
    if (result === "ok") {
      await onSelected();
      return;
    }
    if (result === "signed_out") {
      await onSignedOut();
      return;
    }
    setBusy(false);
    setError(result === "refused" ? REFUSED_MESSAGE : "No se pudo verificar el PIN. Intenta de nuevo.");
    inputRef.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && !busy) {
      event.stopPropagation();
      onCancel();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-stone-950/40 px-4 pb-4 backdrop-blur-[2px] sm:items-center sm:pb-0"
      onKeyDown={onKeyDown}
      data-testid="pin-dialog-backdrop"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-sm rounded-2xl border border-line bg-canvas-raised p-6 shadow-xl"
        data-testid="pin-dialog"
      >
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-xl font-semibold ${tone}`}
          >
            {initialOf(card.displayName)}
          </span>
          <div className="min-w-0">
            <h2 id={titleId} className="truncate text-base font-semibold">
              {card.displayName}
            </h2>
            {card.roleLabel ? <p className="truncate text-[12px] text-ink-muted">{card.roleLabel}</p> : null}
          </div>
        </div>

        <form className="mt-5" onSubmit={(e) => void submit(e)} noValidate>
          <label htmlFor={`${titleId}-pin`} className="block text-[13px] font-medium text-ink">
            PIN personal
          </label>
          <input
            ref={inputRef}
            id={`${titleId}-pin`}
            type="password"
            inputMode="numeric"
            autoComplete="off"
            enterKeyHint="go"
            maxLength={PIN_MAX_LENGTH}
            value={pin}
            disabled={busy}
            onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, "").slice(0, PIN_MAX_LENGTH))}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            className="mt-1.5 h-11 w-full rounded-md border border-line-strong bg-canvas-raised px-3 text-center text-lg tracking-[0.4em] text-ink shadow-sm focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30 disabled:opacity-60"
            data-testid="pin-input"
          />
          {error ? (
            <p id={errorId} role="alert" className="mt-3 rounded-md border border-bad/30 bg-bad-bg px-3 py-2 text-[13px] leading-5 text-bad" data-testid="pin-error">
              {error}
            </p>
          ) : null}
          <div className="mt-5 flex gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={busy}
              className="h-10 flex-1 rounded-md border border-line-strong bg-canvas-raised text-[13px] font-medium text-ink hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:opacity-60"
              data-testid="pin-cancel"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={busy || pin.length === 0}
              className="h-10 flex-1 rounded-md bg-brand-600 text-[13px] font-semibold text-white shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-2 disabled:opacity-60"
              data-testid="pin-submit"
            >
              {busy ? "Verificando…" : "Entrar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
