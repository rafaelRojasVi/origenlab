import { useCallback, useEffect, useState } from "react";
import type { ContactProfileUi } from "../../api/contactTypes";
import {
  OperatorApiError,
  fetchContactProfile,
} from "../../api/operatorClient";
import type { ApiBackend } from "../../api/operatorTypes";
import { contactDataSourceLabel } from "../../lib/dataSourceLabel";

function formatContactError(e: unknown): string {
  if (e instanceof OperatorApiError) {
    if (e.status === 404) {
      return "Contacto no encontrado.";
    }
    if (e.status === 422) {
      return "Correo inválido.";
    }
    return `API ${e.status}: ${e.message}`;
  }
  if (e instanceof Error) {
    return e.message;
  }
  return "No se pudo cargar el perfil del contacto.";
}

function OutreachTruthGuide({ profile }: { profile: ContactProfileUi }) {
  const { outreach, sent_history } = profile;
  const hasState = Boolean(outreach.state?.trim());
  const hasSent = sent_history.sent_count > 0;
  const showGuide =
    outreach.do_not_repeat ||
    outreach.suppressed_email ||
    outreach.suppressed_domain ||
    (hasSent && !hasState);

  if (!showGuide) {
    return null;
  }

  return (
    <div
      className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-800 space-y-2"
      role="note"
    >
      <p className="font-semibold text-slate-900">Cómo leer los campos de contacto (solo lectura)</p>
      <ul className="list-disc pl-4 space-y-1">
        <li>
          <strong>No repetir / supresión</strong> — bandera de seguridad en SQLite; no autoriza
          envíos desde este panel.
        </li>
        <li>
          <strong>Historial de envíos</strong> — resumen desde Gmail Enviados; solo evidencia, no
          escribe el estado lateral.
        </li>
        <li>
          <strong>Estado de contacto</strong> — registro manual cuando existe; vacío significa sin
          fila o no definido en el pipeline (no es lo mismo que “nunca contactado”).
        </li>
      </ul>
      {outreach.do_not_repeat && !hasState ? (
        <p className="text-slate-700">
          {hasSent
            ? "«No repetir» activo pero el estado está vacío — el historial muestra envíos previos; actualice la verdad operativa solo vía scripts del pipeline."
            : "«No repetir» activo pero el estado está vacío — trátelo como memoria de seguridad; confirme en el pipeline antes de contactar."}
        </p>
      ) : null}
    </div>
  );
}

function SuppressionBanner({ profile }: { profile: ContactProfileUi }) {
  const { outreach } = profile;
  const flags: string[] = [];
  if (outreach.do_not_repeat) {
    flags.push("No repetir");
  }
  if (outreach.suppressed_email) {
    flags.push("Correo suprimido");
  }
  if (outreach.suppressed_domain) {
    flags.push("Dominio suprimido");
  }
  if (flags.length === 0) {
    return null;
  }
  return (
    <div
      className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-950"
      role="alert"
    >
      <p className="font-semibold">Restricción de contacto — solo lectura</p>
      <ul className="mt-1 list-disc pl-5">
        {flags.map((f) => (
          <li key={f}>{f}</li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-red-900">
        Sin acciones de escritura desde este panel. Actualice la verdad de envío en el pipeline
        SQLite y scripts del operador.
      </p>
    </div>
  );
}

export function ContactProfilePanel({
  email,
  open,
  onClose,
  backend,
  mirrorBackend,
}: {
  email: string | null;
  open: boolean;
  onClose: () => void;
  backend: ApiBackend;
  mirrorBackend: boolean;
}) {
  const [profile, setProfile] = useState<ContactProfileUi | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (targetEmail: string) => {
    setLoading(true);
    setError(null);
    setProfile(null);
    try {
      setProfile(await fetchContactProfile(targetEmail));
    } catch (e) {
      setError(formatContactError(e));
      setProfile(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open || !email) {
      return;
    }
    void load(email);
  }, [open, email, load]);

  if (!open || !email) {
    return null;
  }

  const showMirrorNote =
    mirrorBackend || profile?.meta.data_source === "postgres_mirror";

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="presentation">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/30"
        aria-label="Cerrar perfil de contacto"
        onClick={onClose}
      />
      <aside
        className="relative z-10 flex h-full w-full max-w-md flex-col border-l border-[var(--color-border)] bg-[var(--color-card)] shadow-xl"
        role="dialog"
        aria-labelledby="contact-profile-heading"
        aria-modal="true"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
          <div>
            <h2 id="contact-profile-heading" className="text-lg font-semibold text-slate-900">
              Perfil de contacto · solo lectura
            </h2>
            <p className="mt-1 text-xs text-[var(--color-muted)] break-all">{email}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
          >
            Cerrar
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {showMirrorNote ? (
            <p className="rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900">
              El espejo Postgres no define envíos ni el estado de contacto.
            </p>
          ) : null}

          {loading ? (
            <div className="space-y-3" role="status" aria-live="polite">
              <div className="h-20 animate-pulse rounded-lg bg-slate-200/80" />
              <div className="h-32 animate-pulse rounded-lg bg-slate-100" />
            </div>
          ) : null}

          {error ? (
            <div
              className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
              role="alert"
            >
              <p className="font-medium">No se pudo cargar el perfil</p>
              <p className="mt-1">{error}</p>
              <button
                type="button"
                onClick={() => void load(email)}
                className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-800 hover:bg-red-50"
              >
                Reintentar
              </button>
            </div>
          ) : null}

          {!loading && !error && profile ? (
            <>
              <p className="text-xs text-[var(--color-muted)]">
                Fuente: {contactDataSourceLabel(backend, profile.meta.data_source)}
                {profile.meta.reduced_mode ? " · modo reducido" : ""}
              </p>

              <SuppressionBanner profile={profile} />

              <OutreachTruthGuide profile={profile} />

              {profile.meta.reduced_mode && profile.meta.note ? (
                <p className="text-sm text-amber-900 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  {profile.meta.note}
                </p>
              ) : null}

              <section className="space-y-2">
                <h3 className="text-sm font-semibold text-slate-800">Identidad</h3>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-[var(--color-muted)]">Correo</dt>
                  <dd className="break-all">{profile.contact.normalized_email || profile.contact.email}</dd>
                  {profile.contact.name ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Nombre</dt>
                      <dd>{profile.contact.name}</dd>
                    </>
                  ) : null}
                  {profile.contact.organization_name ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Organización</dt>
                      <dd>{profile.contact.organization_name}</dd>
                    </>
                  ) : null}
                  {profile.contact.domain || profile.contact.organization_domain ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Dominio</dt>
                      <dd>
                        {profile.contact.organization_domain || profile.contact.domain}
                      </dd>
                    </>
                  ) : null}
                  <dt className="text-[var(--color-muted)]">Mensajes</dt>
                  <dd>{profile.contact.message_count}</dd>
                  {profile.contact.first_seen_at ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Primera vez visto</dt>
                      <dd>{profile.contact.first_seen_at}</dd>
                    </>
                  ) : null}
                  {profile.contact.last_seen_at ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Última actividad</dt>
                      <dd>{profile.contact.last_seen_at}</dd>
                    </>
                  ) : null}
                </dl>
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold text-slate-800">Estado de contacto</h3>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-[var(--color-muted)]">Estado</dt>
                  <dd>
                    {profile.outreach.state?.trim() ? profile.outreach.state : "— (sin registro lateral)"}
                  </dd>
                  {profile.outreach.last_contacted_at ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Último contacto</dt>
                      <dd>{profile.outreach.last_contacted_at}</dd>
                    </>
                  ) : null}
                  {profile.outreach.source ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Fuente</dt>
                      <dd>{profile.outreach.source}</dd>
                    </>
                  ) : null}
                  {profile.outreach.notes ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Notas</dt>
                      <dd className="text-slate-800">{profile.outreach.notes}</dd>
                    </>
                  ) : null}
                </dl>
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold text-slate-800">Historial de envíos (resumen)</h3>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-[var(--color-muted)]">Cantidad enviados</dt>
                  <dd>{profile.sent_history.sent_count}</dd>
                  {profile.sent_history.latest_sent_at ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Último envío</dt>
                      <dd>{profile.sent_history.latest_sent_at}</dd>
                    </>
                  ) : null}
                  {profile.sent_history.latest_subject ? (
                    <>
                      <dt className="text-[var(--color-muted)]">Último asunto</dt>
                      <dd>{profile.sent_history.latest_subject}</dd>
                    </>
                  ) : null}
                </dl>
              </section>

              {profile.warnings.length > 0 ? (
                <section className="rounded-lg border border-amber-200 bg-amber-50/80 px-3 py-3">
                  <h3 className="text-sm font-semibold text-amber-950">Advertencias</h3>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-950">
                    {profile.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {profile.contact.message_count === 0 &&
              !profile.outreach.state &&
              profile.sent_history.sent_count === 0 ? (
                <p className="text-sm text-[var(--color-muted)]" role="status">
                  Datos limitados para esta dirección (correo válido, poca información).
                </p>
              ) : null}
            </>
          ) : null}
        </div>

        <footer className="border-t border-[var(--color-border)] px-4 py-3 text-xs text-[var(--color-muted)]">
          Solo GET /contacts/… · sin enviar, redactar ni archivar
        </footer>
      </aside>
    </div>
  );
}
