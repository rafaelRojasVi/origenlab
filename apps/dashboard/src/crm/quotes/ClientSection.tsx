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
import { setClientDetail, setRecipient } from "./draftEditor";
import type { QuoteClientDetails, QuoteDraft, QuoteRecipientPick, QuoteWorkspace, QuoteWorkspaceContact } from "./quoteApi";
import { AssignInstitution } from "../pages/AssignInstitution";
import type { OpportunityCardData } from "../crmTypes";

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

/** «Datos del cliente»: what the PDF prints; the placeholder is what it prints when left blank. */
const DETAILS: { key: keyof QuoteClientDetails; label: string; max: number; type?: string; autoComplete?: string }[] = [
  { key: "contact_name", label: "Nombre del contacto", max: 200, autoComplete: "off" },
  { key: "contact_role", label: "Cargo", max: 200 },
  { key: "department", label: "Unidad / depto.", max: 200 },
  { key: "phone", label: "Teléfono", max: 60, type: "tel" },
  { key: "institution_name", label: "Institución (razón social)", max: 500 },
  { key: "rut", label: "RUT", max: 20 },
  { key: "address", label: "Dirección", max: 200 },
  { key: "city", label: "Ciudad / región", max: 200 },
];

const FIELD_CLS =
  "h-10 w-full min-w-0 rounded-md border border-line bg-canvas-raised px-2.5 text-[13px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50";

export function ClientSection({
  workspace,
  draft,
  invalid,
  disabled,
  refusal = null,
  edit,
  onInstitutionAdded,
}: {
  workspace: QuoteWorkspace;
  draft: QuoteDraft;
  invalid: DraftProblem[];
  disabled: boolean;
  /** Why the server refused the last save's recipient, in Spanish (`quoteRefusalText`); null when it did not. */
  refusal?: string | null;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
  /** After «Agregar institución» names the case's institution: save pending edits and reload. */
  onInstitutionAdded?: () => void | Promise<void>;
}) {
  const ids = useId();
  const { institution, contacts, lo_que_pidio } = workspace.case;
  const [typing, setTyping] = useState(false);
  const [adding, setAdding] = useState(false);
  const [addProblem, setAddProblem] = useState<string | null>(null);
  // The case's institution, named here without the trip to the case (a persona natural needs none).
  const mayAdd = !institution && !disabled && !workspace.case.closed && workspace.case.version != null && !!onInstitutionAdded;
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
  const pickedContact = (() => {
    const pid = recipient?.participant_id ?? (recipient ? null : (draft as { recipient_participant_id?: string | null }).recipient_participant_id);
    const c = pid ? contacts.find((x) => x.participant_id === pid) : recipient ? contacts.find((x) => samePick(pickOf(x), recipient)) : undefined;
    // A contact known only by address has its address as its name: that is no name to print.
    return c?.name && c.name !== c.email ? c.name : (recipient?.name ?? null);
  })();
  // A persona natural (owner, 2026-10-22): no institution and not a tender — the person is the
  // client and the quote is approved as is (#816). What «Datos del cliente» types prints over it.
  const persona = !institution && !workspace.tender;
  const typedInstitution = draft.client?.institution_name?.trim() || null;
  const typedRut = draft.client?.rut?.trim() || null;
  const personName = draft.client?.contact_name?.trim() || pickedContact || recipient?.email || null;
  const fromCrm: Partial<Record<keyof QuoteClientDetails, string | null>> = {
    contact_name: pickedContact,
    institution_name: institution?.name ?? null,
    rut: institution?.rut ?? null,
  };

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
  const typedName = (recipient?.name ?? "").trim();
  // Said once a field is left with something in it: a bad address, or a name with no address
  // (the draft would not be saved, and nothing else would say why).
  const emailProblem =
    emailLeft && (typedEmail || typedName) ? (invalid.find((p) => p.field === "recipient.email")?.message_es ?? null) : null;

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
        {persona ? (
          <>
            <span className="truncate text-[15px] font-semibold text-ink">{typedInstitution ?? "Persona natural (particular)"}</span>
            {personName ? <span className="truncate text-[13px] text-ink">{personName}</span> : null}
            {typedRut ? <span className="text-[13px] text-ink-muted">RUT {typedRut}</span> : null}
            <span className="text-[12.5px] text-ink-muted">Sin institución: se emite a nombre de la persona de «Para».</span>
          </>
        ) : (
          <>
            <span className="truncate text-[15px] font-semibold text-ink">{institution?.name ?? "Sin institución"}</span>
            <span className="text-[13px] text-ink-muted">{institution?.rut ? `RUT ${institution.rut}` : "RUT pendiente"}</span>
          </>
        )}
        {mayAdd && !adding ? (
          <button
            type="button"
            onClick={() => {
              setAddProblem(null);
              setAdding(true);
            }}
            className={
              persona
                ? "mt-1 inline-flex min-h-[36px] items-center self-start text-[12.5px] font-medium text-brand-700 underline-offset-2 hover:underline"
                : "mt-1 inline-flex min-h-[44px] items-center self-start rounded-md border border-brand-600 px-3 text-[13px] font-semibold text-brand-700 hover:bg-brand-50"
            }
          >
            {persona ? "¿Es de una institución? Agregarla" : "Agregar institución"}
          </button>
        ) : null}
        {addProblem ? <p role="alert" className="text-[12.5px] text-danger-700">{addProblem}</p> : null}
      </div>
      {mayAdd && adding ? (
        <div className="md:col-span-3">
          <AssignInstitution
            card={{ opportunity_id: workspace.case.id, version: workspace.case.version, pending_institution_mentions: [] } as unknown as OpportunityCardData}
            onCancel={() => setAdding(false)}
            onDone={(outcome) => {
              setAdding(false);
              if (outcome.tone === "good") void onInstitutionAdded?.();
              else setAddProblem(outcome.lines.join(" "));
            }}
          />
        </div>
      ) : null}

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
                onBlur={() => setEmailLeft(true)}
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

      <fieldset className="grid min-w-0 grid-cols-1 gap-3 border-t border-line pt-3 sm:grid-cols-2 md:col-span-3 lg:grid-cols-4">
        <legend className="mb-1 text-xs font-semibold text-ink-muted">
          Datos del cliente <span className="font-normal">(salen en el PDF; vacío = lo que dice el CRM)</span>
        </legend>
        {DETAILS.map((f) => (
          <div key={f.key} className="flex min-w-0 flex-col gap-1">
            <label htmlFor={`${ids}-${f.key}`} className="text-xs text-ink-muted">
              {f.label}
            </label>
            <input
              id={`${ids}-${f.key}`}
              type={f.type ?? "text"}
              value={draft.client?.[f.key] ?? ""}
              maxLength={f.max}
              disabled={disabled}
              autoComplete={f.autoComplete}
              placeholder={fromCrm[f.key] ?? ""}
              onChange={(e) => edit((d) => setClientDetail(d, f.key, e.target.value))}
              className={FIELD_CLS}
            />
          </div>
        ))}
      </fieldset>
    </section>
  );
}
