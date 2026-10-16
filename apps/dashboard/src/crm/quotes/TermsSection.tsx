/**
 * The Cotizador's «Condiciones» card (canvas): the quote's fixed conditions (delivery time,
 * validity, payment, warranty, place of delivery, installation), the note for the client (it goes
 * on the PDF) and any extra condition («+ Agregar otra condición»: a name and its text).
 *
 * A template placeholder in brackets («[CONDICIÓN DE PAGO]») is marked to be completed before
 * approving. What the API would refuse (a text over its length, validity outside 1–365 days, an
 * extra condition without its name or text) is said next to its field.
 */
import { useId, useState } from "react";
import { addExtraTerm, removeExtraTerm, setTerm, updateExtraTerm, type DraftProblem, type TermKey } from "./draftEditor";
import type { QuoteDraft, QuoteDraftTerm } from "./quoteApi";

const TEXT_TERMS: {
  key: Exclude<TermKey, "validity_days" | "client_notes_es">;
  label: string;
}[] = [
  { key: "delivery_terms", label: "Plazo de entrega" },
  { key: "payment_terms", label: "Forma de pago" },
  { key: "warranty_terms_es", label: "Garantía" },
  { key: "delivery_place_es", label: "Lugar de entrega" },
  { key: "installation_terms_es", label: "Instalación y capacitación" },
];

const MAX_EXTRA = 12;
const PLACEHOLDER = /\[[^\]]*\]/;
const PLACEHOLDER_HINT = "Completa este texto antes de aprobar.";

const FIELD_CLS =
  "w-full min-w-0 rounded-lg border border-line-strong bg-canvas-raised px-3 text-[14px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-bad";
const INPUT_CLS = `h-[42px] ${FIELD_CLS}`;
const TEXTAREA_CLS = `resize-y py-2.5 ${FIELD_CLS}`;

/** A blank text is no condition (sent as none). */
const orNull = (v: string) => (v.trim() ? v : null);

/** The field's notes under it: the API's refusal first, then the placeholder hint. Ids for aria-describedby. */
function Notes({ id, problem, value }: { id: string; problem: string | null; value: string | null | undefined }) {
  const placeholder = !!value && PLACEHOLDER.test(value);
  if (!problem && !placeholder) return null;
  return (
    <span id={id} className="flex flex-col gap-0.5">
      {problem ? <span className="text-[12px] text-bad">{problem}</span> : null}
      {placeholder ? <span className="text-[12px] font-medium text-warn">{PLACEHOLDER_HINT}</span> : null}
    </span>
  );
}

const describedBy = (id: string, problem: string | null, value: string | null | undefined) =>
  problem || (value && PLACEHOLDER.test(value)) ? id : undefined;

const flagged = (value: string | null | undefined) => (value && PLACEHOLDER.test(value) ? " border-warn bg-warn-bg/40" : "");

/** «Validez (días)»: whole days; what cannot be read stays as typed and is named as the problem. */
function ValidityInput({
  id,
  value,
  problem,
  disabled,
  onChange,
}: {
  id: string;
  value: number | null | undefined;
  problem: string | null;
  disabled: boolean;
  onChange: (days: number | null) => void;
}) {
  const [typed, setTyped] = useState<string | null>(null);
  const shown = typed ?? (value === null || value === undefined || Number.isNaN(value) ? "" : String(value));
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="flex flex-col gap-[5px] text-[12.5px] text-ink-muted">
        Validez (días)
        <input
          id={id}
          type="text"
          inputMode="numeric"
          value={shown}
          disabled={disabled}
          aria-invalid={problem ? true : undefined}
          aria-describedby={describedBy(`${id}-notes`, problem, null)}
          onChange={(e) => {
            const t = e.target.value;
            setTyped(t);
            const trimmed = t.trim();
            onChange(!trimmed ? null : /^\d+$/.test(trimmed) ? Number(trimmed) : Number.NaN);
          }}
          onBlur={() => {
            if (!problem) setTyped(null);
          }}
          className={`${INPUT_CLS} font-mono tabular-nums`}
        />
      </label>
      <Notes id={`${id}-notes`} problem={problem} value={null} />
    </div>
  );
}

function TextTerm({
  id,
  label,
  value,
  problem,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  problem: string | null;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="flex flex-col gap-[5px] text-[12.5px] text-ink-muted">
        {label}
        <input
          id={id}
          type="text"
          value={value}
          disabled={disabled}
          aria-invalid={problem ? true : undefined}
          aria-describedby={describedBy(`${id}-notes`, problem, value)}
          onChange={(e) => onChange(e.target.value)}
          className={`${INPUT_CLS}${flagged(value)}`}
        />
      </label>
      <Notes id={`${id}-notes`} problem={problem} value={value} />
    </div>
  );
}

function ExtraTerm({
  index,
  term,
  problems,
  disabled,
  onPatch,
  onRemove,
}: {
  index: number;
  term: QuoteDraftTerm;
  problems: DraftProblem[];
  disabled: boolean;
  onPatch: (patch: Partial<QuoteDraftTerm>) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const n = index + 1;
  const labelProblem = problems.find((p) => p.field === "terms_extra.label_es")?.message_es ?? null;
  const textProblem = problems.find((p) => p.field === "terms_extra.text_es")?.message_es ?? null;
  return (
    <li className="grid grid-cols-1 gap-2 rounded-[10px] border border-line px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] sm:items-start">
      <span className="flex min-w-0 flex-col gap-1">
        <input
          type="text"
          aria-label={`Nombre de la condición ${n}`}
          aria-invalid={labelProblem ? true : undefined}
          aria-describedby={describedBy(`${id}-label`, labelProblem, term.label_es)}
          value={term.label_es}
          placeholder="Nombre (p. ej. Embalaje)"
          disabled={disabled}
          onChange={(e) => onPatch({ label_es: e.target.value })}
          className={`${INPUT_CLS}${flagged(term.label_es)}`}
        />
        <Notes id={`${id}-label`} problem={labelProblem} value={term.label_es} />
      </span>
      <span className="flex min-w-0 flex-col gap-1">
        <input
          type="text"
          aria-label={`Texto de la condición ${n}`}
          aria-invalid={textProblem ? true : undefined}
          aria-describedby={describedBy(`${id}-text`, textProblem, term.text_es)}
          value={term.text_es}
          placeholder="Texto que sale en la cotización"
          disabled={disabled}
          onChange={(e) => onPatch({ text_es: e.target.value })}
          className={`${INPUT_CLS}${flagged(term.text_es)}`}
        />
        <Notes id={`${id}-text`} problem={textProblem} value={term.text_es} />
      </span>
      {disabled ? null : (
        <button
          type="button"
          aria-label={`Quitar la condición ${n}`}
          onClick={onRemove}
          className="h-[42px] rounded-md px-2 text-[12.5px] font-medium text-ink-muted hover:bg-canvas-sunken hover:text-bad focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          Quitar
        </button>
      )}
    </li>
  );
}

export function TermsSection({
  draft,
  invalid,
  disabled,
  edit,
}: {
  draft: QuoteDraft;
  invalid: DraftProblem[];
  disabled: boolean;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
}) {
  const ids = useId();
  const t = draft.terms;
  const problemOf = (field: string) => invalid.find((p) => p.field === field && p.index === undefined)?.message_es ?? null;
  const extraProblem = problemOf("terms_extra");
  const notes = t.client_notes_es ?? "";
  const notesProblem = problemOf("terms.client_notes_es");

  const textTerm = ({ key, label }: (typeof TEXT_TERMS)[number]) => (
    <TextTerm
      key={key}
      id={`${ids}-${key}`}
      label={label}
      value={t[key] ?? ""}
      problem={problemOf(`terms.${key}`)}
      disabled={disabled}
      onChange={(v) => edit((d) => setTerm(d, key, orNull(v)))}
    />
  );

  return (
    <section
      aria-labelledby="cotizador-condiciones"
      className="flex flex-col gap-3 rounded-xl border border-line bg-canvas-raised px-5 py-4"
    >
      <h2 id="cotizador-condiciones" className="text-[17px] font-semibold text-ink">
        Condiciones
      </h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {textTerm(TEXT_TERMS[0])}
        <ValidityInput
          id={`${ids}-validity`}
          value={t.validity_days}
          problem={problemOf("terms.validity_days")}
          disabled={disabled}
          onChange={(days) => edit((d) => setTerm(d, "validity_days", days))}
        />
        {TEXT_TERMS.slice(1).map(textTerm)}
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={`${ids}-notes`} className="flex flex-col gap-[5px] text-[12.5px] text-ink-muted">
          Nota para el cliente (sale en el PDF)
          <textarea
            id={`${ids}-notes`}
            rows={3}
            value={notes}
            disabled={disabled}
            aria-invalid={notesProblem ? true : undefined}
            aria-describedby={describedBy(`${ids}-notes-notes`, notesProblem, notes)}
            onChange={(e) => {
              const v = e.target.value;
              edit((d) => setTerm(d, "client_notes_es", orNull(v)));
            }}
            className={`${TEXTAREA_CLS}${flagged(notes)}`}
          />
        </label>
        <Notes id={`${ids}-notes-notes`} problem={notesProblem} value={notes} />
      </div>
      {t.terms_extra.length ? (
        <ul aria-label="Otras condiciones" className="flex flex-col gap-2">
          {t.terms_extra.map((term, i) => (
            <ExtraTerm
              key={term.uid ?? `at-${i}`}
              index={i}
              term={term}
              problems={invalid.filter((p) => p.index === i && p.field.startsWith("terms_extra."))}
              disabled={disabled}
              onPatch={(patch) => edit((d) => updateExtraTerm(d, i, patch))}
              onRemove={() => edit((d) => removeExtraTerm(d, i))}
            />
          ))}
        </ul>
      ) : null}
      {extraProblem ? <p className="text-[12px] text-bad">{extraProblem}</p> : null}
      {disabled ? null : (
        <button
          type="button"
          disabled={t.terms_extra.length >= MAX_EXTRA}
          onClick={() => edit(addExtraTerm)}
          className="min-h-[44px] self-start rounded-md px-1 text-[13.5px] font-semibold text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
        >
          + Agregar otra condición
        </button>
      )}
    </section>
  );
}
