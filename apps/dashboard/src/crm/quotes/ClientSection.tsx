/**
 * The Cotizador's first card (canvas «Cliente / Para / Lo que pidió»): the institution the quote is
 * for, whom it goes to, and what the client asked for.
 *
 * «Para» picks one of the case's contacts (`case.contacts`: participants first, then people on the
 * case's emails) or «Otro correo…», a typed address with an optional name. The API takes exactly
 * one of participant / person / contact point / email, and a name only with an email; the pick
 * sent follows that rule. Whether an address may receive quotes (OrigenLab's own, Labdelivery) is
 * the server's to say: its refusal (`refusal`) shows under «Para». While an address is typed the
 * pick stays «Otro correo…» even when it matches a contact, and «no válido» waits until the field
 * has content and loses the focus.
 */
import { useEffect, useId, useState } from "react";
import type { DraftProblem } from "./draftEditor";
import { setRecipient } from "./draftEditor";
import type { QuoteDraft, QuoteRecipientPick, QuoteWorkspace, QuoteWorkspaceContact } from "./quoteApi";

const OTHER = "other";
const SAVED = "saved";

/** The one identifier the API takes for this contact, the others left out. */
function pickOf(c: QuoteWorkspaceContact): QuoteRecipientPick {
  if (c.participant_id) return { participant_id: c.participant_id };
  if (c.person_id) return { person_id: c.person_id };
  if (c.contact_point_id) return { contact_point_id: c.contact_point_id };
  return { email: c.email, name: c.name };
}

function samePick(a: QuoteRecipientPick, b: QuoteRecipientPick): boolean {
  if (a.participant_id || b.participant_id) return a.participant_id === b.participant_id;
  if (a.person_id || b.person_id) return a.person_id === b.person_id;
  if (a.contact_point_id || b.contact_point_id) return a.contact_point_id === b.contact_point_id;
  return !!a.email && (a.email ?? "").trim().toLowerCase() === (b.email ?? "").trim().toLowerCase();
}

function contactLabel(c: QuoteWorkspaceContact): string {
  if (c.name && c.email) return `${c.name} <${c.email}>`;
  return c.email ?? c.name ?? "Contacto sin correo";
}

const FIELD_CLS =
  "h-10 w-full min-w-0 rounded-md border border-line bg-canvas-raised px-2.5 text-[13px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50";

export function ClientSection({
  workspace,
  draft,
  invalid,
  disabled,
  refusal = null,
  edit,
}: {
  workspace: QuoteWorkspace;
  draft: QuoteDraft;
  invalid: DraftProblem[];
  disabled: boolean;
  /** Why the server refused the last save's recipient, verbatim; null when it did not. */
  refusal?: string | null;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
}) {
  const ids = useId();
  const { institution, contacts, lo_que_pidio } = workspace.case;
  const [typing, setTyping] = useState(false);
  const [emailLeft, setEmailLeft] = useState(false);
  // A reload (new workspace) drops what was half-typed here.
  useEffect(() => {
    setTyping(false);
    setEmailLeft(false);
  }, [workspace]);
  // The server's refusal stands until the operator changes the pick (or a new one arrives).
  const [refusalSeen, setRefusalSeen] = useState<string | null>(null);
  useEffect(() => setRefusalSeen(null), [refusal]);
  const shownRefusal = refusal && refusalSeen !== refusal ? refusal : null;
  const changed = () => {
    if (refusal) setRefusalSeen(refusal);
  };
  const recipient = draft.recipient ?? null;

  const matched = recipient ? contacts.findIndex((c) => samePick(pickOf(c), recipient)) : -1;
  const value = typing
    ? OTHER
    : matched >= 0
      ? String(matched)
      : recipient?.email !== undefined && recipient?.email !== null
        ? OTHER
        : recipient
          ? SAVED
          : "";
  const typedEmail = (recipient?.email ?? "").trim();
  const emailProblem = emailLeft && typedEmail ? (invalid.find((p) => p.field === "recipient.email")?.message_es ?? null) : null;

  function choose(v: string) {
    setEmailLeft(false);
    changed();
    if (v === OTHER) {
      setTyping(true);
      edit((d) => setRecipient(d, null));
      return;
    }
    setTyping(false);
    if (v === "" || v === SAVED) {
      if (v === "") edit((d) => setRecipient(d, null));
      return;
    }
    const c = contacts[Number(v)];
    if (c) edit((d) => setRecipient(d, pickOf(c)));
  }

  function typeAddress(email: string, name: string) {
    changed();
    const e = email.trim() ? email : "";
    edit((d) => setRecipient(d, e || name.trim() ? { email: e, name: name.trim() ? name : null } : null));
  }

  return (
    <section
      aria-label="Cliente"
      className="grid grid-cols-1 gap-4 rounded-xl border border-line bg-canvas-raised px-5 py-4 md:grid-cols-3"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-xs text-ink-muted">Cliente</span>
        <span className="truncate text-[15px] font-semibold text-ink">{institution?.name ?? "Sin institución"}</span>
        <span className="text-[13px] text-ink-muted">{institution?.rut ? `RUT ${institution.rut}` : "RUT pendiente"}</span>
      </div>

      <div className="flex min-w-0 flex-col gap-1">
        <label htmlFor={`${ids}-para`} className="text-xs text-ink-muted">
          Para
        </label>
        <select
          id={`${ids}-para`}
          value={value}
          disabled={disabled}
          onChange={(e) => choose(e.target.value)}
          className={`${FIELD_CLS} font-semibold`}
        >
          <option value="">Elige a quién va…</option>
          {contacts.map((c, i) => (
            <option key={`${c.participant_id ?? c.person_id ?? c.contact_point_id ?? c.email}-${i}`} value={String(i)}>
              {contactLabel(c)}
            </option>
          ))}
          {value === SAVED ? <option value={SAVED}>Destinatario guardado</option> : null}
          <option value={OTHER}>Otro correo…</option>
        </select>
        {value === OTHER ? (
          <div className="mt-1 grid gap-2">
            <div>
              <label htmlFor={`${ids}-email`} className="text-xs text-ink-muted">
                Correo
              </label>
              <input
                id={`${ids}-email`}
                type="email"
                value={recipient?.email ?? ""}
                disabled={disabled}
                aria-invalid={emailProblem ? true : undefined}
                onChange={(e) => typeAddress(e.target.value, recipient?.name ?? "")}
                onBlur={() => setEmailLeft(true)}
                placeholder="nombre@institucion.cl"
                className={FIELD_CLS}
              />
            </div>
            <div>
              <label htmlFor={`${ids}-name`} className="text-xs text-ink-muted">
                Nombre (opcional)
              </label>
              <input
                id={`${ids}-name`}
                type="text"
                value={recipient?.name ?? ""}
                disabled={disabled}
                onChange={(e) => typeAddress(recipient?.email ?? "", e.target.value)}
                className={FIELD_CLS}
              />
            </div>
            {emailProblem ? <p className="text-[12px] text-bad">{emailProblem}</p> : null}
          </div>
        ) : null}
        {shownRefusal ? (
          <p role="alert" className="mt-1 text-[12.5px] text-bad">
            {shownRefusal}
          </p>
        ) : null}
      </div>

      {lo_que_pidio ? (
        <div className="flex min-w-0 flex-col gap-1 rounded-lg bg-brand-50 px-3 py-2.5">
          <span className="text-xs font-semibold text-brand-700">Lo que pidió</span>
          <p className="whitespace-pre-line text-[13px] leading-snug text-ink">{lo_que_pidio}</p>
        </div>
      ) : null}
    </section>
  );
}
