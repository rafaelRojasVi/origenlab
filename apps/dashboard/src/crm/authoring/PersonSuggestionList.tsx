/**
 * «Personas sugeridas»: people the quote emails already name (`/v2/workspace/person-suggestions`,
 * or the card's `person_suggestions`). «Crear persona» is one `create-person` with the name, the
 * address and the institution; the suggestion is gone on the next read because the address then
 * belongs to a person. «Ocultar» hides it in this browser only.
 */
import { useRef, useState } from "react";
import { Badge, fmtDate } from "../ui";
import { createPerson, newIdempotencyKey, refusalOf, type PersonSuggestion } from "./crmAuthoringApi";
import { useHiddenSuggestions } from "./hiddenSuggestions";

export function personRefusalText(code: string, message: string): string {
  switch (code) {
    case "contact_point_taken":
      return "Esa dirección ya es de otra persona del CRM.";
    case "shared_mailbox":
      return "Esa dirección es un buzón compartido, no una persona.";
    case "contact_point_inactive":
      return "Esa dirección fue desactivada en el CRM.";
    case "archived_subject":
      return "La institución está archivada.";
    default:
      return `${code}: ${message}`;
  }
}

export function personSuggestionNote(s: PersonSuggestion): string {
  const quotes = s.quotes === 1 ? "1 cotización enviada" : `${s.quotes} cotizaciones enviadas`;
  return `Sugerida desde ${quotes} a esta dirección (${s.name_source === "filename" ? "nombre del PDF" : "nombre del destinatario"}).`;
}

export function PersonSuggestionList({
  items,
  mayAuthor,
  showOrganization,
  onCreated,
}: {
  items: PersonSuggestion[];
  mayAuthor: boolean;
  showOrganization: boolean;
  onCreated: () => void;
}) {
  const [hidden, hide] = useHiddenSuggestions();
  const visible = items.filter((s) => !hidden.has(s.suggestion_ref));
  return (
    <div>
      <p className="mb-1.5 text-[11px] text-ink-faint">
        Nombres y direcciones de los correos de cotización. «Ocultar» sólo las oculta en este navegador.
      </p>
      {visible.length === 0 ? (
        <p className="text-xs text-ink-faint">Sin personas sugeridas.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line" aria-label="Personas sugeridas">
          {visible.map((s) => (
            <SuggestionItem
              key={s.suggestion_ref}
              s={s}
              mayAuthor={mayAuthor}
              showOrganization={showOrganization}
              onCreated={onCreated}
              onHide={() => hide(s.suggestion_ref)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function SuggestionItem({
  s,
  mayAuthor,
  showOrganization,
  onCreated,
  onHide,
}: {
  s: PersonSuggestion;
  mayAuthor: boolean;
  showOrganization: boolean;
  onCreated: () => void;
  onHide: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per suggestion: a lost answer retried is a replay, never a second person.
  const keyRef = useRef(newIdempotencyKey());

  async function create() {
    setBusy(true);
    setError(null);
    try {
      await createPerson(
        { display_name: s.display_name, email: s.email, organization_id: s.organization_id, note: personSuggestionNote(s) },
        keyRef.current,
      );
      onCreated();
    } catch (err) {
      const r = refusalOf(err);
      setError(personRefusalText(r?.code ?? "error", r?.message ?? String(err)));
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-xs">
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-ink">{s.display_name}</span>
        <span className="block truncate text-ink-muted">{s.email}</span>
      </span>
      {showOrganization ? <Badge glyph={false}>{s.organization_name ?? "Sin institución"}</Badge> : null}
      {s.name_source === "filename" ? (
        <Badge glyph={false} title="Nombre tomado del archivo PDF de la cotización">nombre del PDF</Badge>
      ) : null}
      <span className="text-[11px] text-ink-faint">
        {s.quotes} cotización{s.quotes === 1 ? "" : "es"} · {fmtDate(s.last_sent_at)}
      </span>
      {mayAuthor ? (
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy}
          className="h-6 rounded-md bg-ink px-2.5 text-[11px] font-medium text-white hover:bg-black disabled:opacity-50"
        >
          {busy ? "…" : "Crear persona"}
        </button>
      ) : null}
      <button
        type="button"
        onClick={onHide}
        className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink-muted hover:bg-canvas-sunken"
      >
        Ocultar
      </button>
      {error ? <p className="w-full text-[11px] text-bad">{error}</p> : null}
    </li>
  );
}
