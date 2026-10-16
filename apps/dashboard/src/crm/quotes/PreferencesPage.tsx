/**
 * «Preferencias del Cotizador» (canvas «Nueva cotización sin caso · Preferencias», right half):
 * the company's defaults every new quote starts from, at `#/crm/cotizador/preferencias` — a fixed
 * pane of the Cotizador module, not a quote tab.
 *
 * Five groups (`PREFERENCE_GROUPS`): default markups, the margin target band and the
 * price-deviation warning, the import-expense profile, the printed default conditions, and the
 * numbering seed. Each field shows its current value (or what the engine uses while it is unset);
 * changing it and pressing «Guardar» asks for a «Motivo» (at least 3 characters) and sends
 * `set-cost-parameter` with the current row the operator saw. If someone changed it meanwhile the
 * API answers 409 `parameter_changed` and the dialog says «Otra persona lo cambió; recarga.», with
 * the reload. «Historial» lists the earlier values with who, when and why.
 *
 * Only administration sets the numbering seed: anyone else sees it read-only, with the reason.
 * Viewers never get here (the module is sales and admin only), and the read refuses them anyway.
 * A save that may have run (network failure, gateway error) keeps its `Idempotency-Key` for the
 * same retry (`definitive`), as «Cotizar» does — also across closing and reopening the reason
 * dialog, while the body is the same. The margin band's ends are checked against each other before
 * the reason is asked (`bandProblem`). Values are formatted on the string, no float.
 */
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { fetchCostParameters, type CostParameter, type CostParameters } from "../catalog/catalogApi";
import { Button, Modal, ResourceGate, Skeleton, fmtDateTime, toast } from "../ui";
import { useResource } from "../useResource";
import { definitive } from "./openOrCreateQuote";
import {
  PREFERENCE_GROUPS,
  TEXT_MAX,
  bandProblem,
  displayPreference,
  editTextOf,
  parsePreference,
  sameValue,
  type PreferenceField,
} from "./preferenceFields";
import { QuoteRefusal, newQuoteCommandKey, setCostParameter, type SetCostParameterBody } from "./quoteApi";
import { quoteRefusalText } from "./quoteRefusal";

export const REASON_MIN = 3;
const REASON_MAX = 500;

const FIELD_CLS =
  "w-full min-w-0 rounded-lg border border-line-strong bg-canvas-raised px-3 text-[14px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 aria-[invalid=true]:border-bad disabled:cursor-not-allowed disabled:bg-canvas-sunken disabled:text-ink-muted";

type Save = (body: SetCostParameterBody, key: string) => Promise<unknown>;
/** One key per attempt: the same body retried (the dialog closed and reopened included) keeps it. */
type KeyRef = { current: { sig: string; key: string } | null };

/** Local wording where the quote one would speak of «la cotización» or of quoting. */
const HERE: Record<string, string> = {
  validation_error: "El servidor no aceptó el valor; revísalo y vuelve a intentarlo.",
  role_may_not_view_costs: "Sólo ventas y administración pueden cambiar las preferencias.",
  forbidden: "Sólo ventas y administración pueden cambiar las preferencias.",
};

/** A refusal in Spanish (`quoteRefusalText`), in this page's words where they differ (`HERE`). */
function refusalText(e: unknown): string {
  if (e instanceof QuoteRefusal && e.code && HERE[e.code]) return HERE[e.code];
  return quoteRefusalText(e, "No se pudo guardar. Intenta de nuevo.");
}

export function PreferencesPage({ save = setCostParameter }: { save?: Save }) {
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";
  const [state, reload, refreshing] = useResource(fetchCostParameters);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-ink">Preferencias del Cotizador</h1>
        <span className="text-[13px] text-ink-muted">
          de la empresa · se usan en cada cotización nueva; cada una puede ajustarlas
          {refreshing && state.kind === "ready" ? " · actualizando…" : ""}
        </span>
      </div>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
        {(params) => (
          <>
            {PREFERENCE_GROUPS.map((g) => (
              <PreferenceGroupCard key={g.title} title={g.title} note={g.note}>
                {g.fields.map((f) => {
                  const current = params.current[f.key] ?? null;
                  return (
                    <PreferenceFieldEditor
                      // A new current row (saved here or elsewhere) starts the field again from it.
                      key={`${f.key}:${current?.id ?? "unset"}`}
                      field={f}
                      current={current}
                      history={historyOf(params, f.key)}
                      all={params.current}
                      readOnly={!!f.adminOnly && !isAdmin}
                      save={save}
                      onSaved={reload}
                    />
                  );
                })}
              </PreferenceGroupCard>
            ))}
            <p className="text-[12.5px] text-ink-muted">
              Cada cambio pide un motivo y queda en el historial (quién, cuándo, valor anterior). Sólo administración cambia el inicio de
              la numeración.
            </p>
          </>
        )}
      </ResourceGate>
    </div>
  );
}

function historyOf(params: CostParameters, key: string): CostParameter[] {
  return (params.history ?? []).filter((h) => h.key === key);
}

function PreferenceGroupCard({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 rounded-[14px] border border-line bg-canvas-raised px-[18px] py-4">
      <div>
        <h2 id={id} className="text-[15px] font-semibold text-ink">
          {title}
        </h2>
        {note ? <p className="mt-0.5 text-[12.5px] text-ink-muted">{note}</p> : null}
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">{children}</div>
    </section>
  );
}

function PreferenceFieldEditor({
  field,
  current,
  history,
  all,
  readOnly,
  save,
  onSaved,
}: {
  field: PreferenceField;
  current: CostParameter | null;
  history: CostParameter[];
  /** Every current parameter: the band's ends are checked against each other. */
  all: Record<string, CostParameter | undefined>;
  readOnly: boolean;
  save: Save;
  onSaved: () => void;
}) {
  const id = useId();
  const initial = editTextOf(field.kind, current?.value);
  const [text, setText] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState<{ value: string } | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const keyRef = useRef<{ sig: string; key: string } | null>(null);
  const parsed = parsePreference(field.kind, text);
  const changed = parsed.ok ? !sameValue(field.kind, parsed.value, current?.value) : text.trim() !== initial.trim();
  const shown = displayPreference(field.kind, current?.value);
  const hintId = `${id}-hint`;
  const problemId = `${id}-problem`;
  const historyId = `${id}-history`;

  const onSave = () => {
    if (!parsed.ok) {
      setProblem(parsed.problem);
      return;
    }
    const crossed = bandProblem(field.key, parsed.value, all);
    if (crossed) {
      setProblem(crossed);
      return;
    }
    setProblem(null);
    setAsking({ value: parsed.value });
  };

  const inputProps = {
    id,
    value: text,
    placeholder: field.placeholder,
    disabled: readOnly,
    "aria-invalid": problem ? true : undefined,
    "aria-describedby": [hintId, problem ? problemId : null].filter(Boolean).join(" "),
    onChange: (e: { target: { value: string } }) => {
      setText(e.target.value);
      setProblem(null);
    },
  };

  return (
    <div className="flex min-w-0 flex-col gap-[5px]" data-preference={field.key}>
      <label htmlFor={id} className="text-[12.5px] font-medium text-ink-muted">
        {field.label}
      </label>
      {field.kind === "text" ? (
        <textarea {...inputProps} rows={2} maxLength={TEXT_MAX} className={`${FIELD_CLS} min-h-[44px] resize-y py-2`} />
      ) : (
        <input {...inputProps} type="text" inputMode={field.kind === "percent" ? "decimal" : "numeric"} className={`${FIELD_CLS} h-11`} />
      )}
      <span id={hintId} className="text-[11.5px] text-ink-muted">
        {shown === null ? `Sin definir: ${field.unset}.` : `Vigente: ${shown}`}
        {field.hint ? ` · ${field.hint}` : ""}
        {readOnly ? " · Sólo administración la cambia." : ""}
      </span>
      {problem ? (
        <span id={problemId} role="alert" className="text-[12px] text-bad">
          {problem}
        </span>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {readOnly ? null : (
          <Button variant="primary" className="min-h-[44px] px-4 text-[14px]" disabled={!changed} onClick={onSave} aria-label={`Guardar ${field.label}`}>
            Guardar
          </Button>
        )}
        <Button
          variant="quiet"
          className="min-h-[44px] px-3 text-[13px]"
          aria-expanded={showHistory}
          aria-controls={showHistory ? historyId : undefined}
          aria-label={`Historial de ${field.label}${history.length ? ` (${history.length})` : ""}`}
          onClick={() => setShowHistory((v) => !v)}
        >
          Historial{history.length ? ` (${history.length})` : ""}
        </Button>
      </div>
      {showHistory ? <HistoryList id={historyId} field={field} current={current} history={history} /> : null}
      {asking ? (
        <ReasonDialog
          field={field}
          from={shown}
          to={displayPreference(field.kind, asking.value) ?? asking.value}
          body={{
            key: field.key,
            ...(field.kind === "text" ? { value_text: asking.value } : { value: asking.value }),
            expected_current_id: current?.id ?? null,
          }}
          save={save}
          keyRef={keyRef}
          onCancel={() => setAsking(null)}
          onSaved={() => {
            setAsking(null);
            toast(`Preferencia guardada · ${field.label}`);
            onSaved();
          }}
          onReload={() => {
            setAsking(null);
            onSaved();
          }}
        />
      ) : null}
    </div>
  );
}

function HistoryList({
  id,
  field,
  current,
  history,
}: {
  id: string;
  field: PreferenceField;
  current: CostParameter | null;
  history: CostParameter[];
}) {
  if (history.length === 0) {
    return (
      <p id={id} className="text-[12.5px] text-ink-muted">
        Sin cambios registrados.
      </p>
    );
  }
  return (
    <ol id={id} aria-label={`Historial de ${field.label}`} className="rounded-lg border border-line">
      {history.map((h) => (
        <li key={h.id} className="border-t border-line px-3 py-2 text-[12.5px] first:border-t-0">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-medium text-ink">{displayPreference(field.kind, h.value) ?? "—"}</span>
            {h.id === current?.id ? <span className="text-[11.5px] font-semibold text-good">vigente</span> : null}
          </div>
          <p className="text-ink-muted">
            {fmtDateTime(h.valid_from)} · {h.set_by ?? "carga inicial"}
          </p>
          {h.reason ? <p className="text-ink">«{h.reason}»</p> : null}
        </li>
      ))}
    </ol>
  );
}

function ReasonDialog({
  field,
  from,
  to,
  body,
  save,
  keyRef,
  onCancel,
  onSaved,
  onReload,
}: {
  field: PreferenceField;
  from: string | null;
  to: string;
  body: Omit<SetCostParameterBody, "reason">;
  save: Save;
  keyRef: KeyRef;
  onCancel: () => void;
  onSaved: () => void;
  onReload: () => void;
}) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [reasonProblem, setReasonProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || changed) return;
    const why = reason.trim();
    if (why.length < REASON_MIN) {
      setReasonProblem(`Escribe un motivo de al menos ${REASON_MIN} caracteres.`);
      return;
    }
    const full: SetCostParameterBody = { ...body, reason: why };
    const sig = JSON.stringify(full);
    if (keyRef.current?.sig !== sig) keyRef.current = { sig, key: newQuoteCommandKey() };
    setBusy(true);
    setError(null);
    try {
      await save(full, keyRef.current.key);
      keyRef.current = null;
      onSaved();
    } catch (err) {
      if (definitive(err)) keyRef.current = null;
      if (err instanceof QuoteRefusal && (err.code === "parameter_changed" || (err.status === 409 && err.code === "conflict"))) setChanged(true);
      else setError(refusalText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Cambiar «${field.label}»`} onClose={onCancel} busy={busy}>
      <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3">
        <p className="text-[13px] text-ink">
          {from ? `De ${from} a ${to}.` : `Queda en ${to}.`} Se usa en las cotizaciones nuevas; las guardadas no cambian.
        </p>
        <div className="flex flex-col gap-1">
          <label htmlFor={reasonId} className="text-xs font-medium text-ink">
            Motivo <span aria-hidden="true" className="text-bad">*</span>
          </label>
          <textarea
            id={reasonId}
            value={reason}
            aria-required="true"
            aria-invalid={reasonProblem ? true : undefined}
            aria-describedby={reasonProblem ? `${reasonId}-problem` : undefined}
            maxLength={REASON_MAX}
            rows={3}
            disabled={busy || changed}
            placeholder="Por qué cambia (queda en el historial)"
            onChange={(e) => {
              setReason(e.target.value);
              setReasonProblem(null);
            }}
            className={`${FIELD_CLS} resize-y py-2 text-[13.5px]`}
          />
          {reasonProblem ? (
            <span id={`${reasonId}-problem`} role="alert" className="text-[12px] text-bad">
              {reasonProblem}
            </span>
          ) : null}
        </div>
        {changed ? (
          <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warn/30 bg-warn-bg px-3 py-2">
            <span className="text-[13px] font-semibold text-warn">Otra persona lo cambió; recarga.</span>
            <Button variant="primary" className="min-h-[44px] px-4" onClick={onReload}>
              Recargar
            </Button>
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="text-[12.5px] text-bad">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button className="min-h-[44px] px-4 text-[14px]" onClick={onCancel} disabled={busy}>
            Cancelar
          </Button>
          {changed ? null : (
            <Button type="submit" variant="primary" className="min-h-[44px] px-4 text-[14px]" busy={busy} busyLabel="Guardando…">
              Guardar
            </Button>
          )}
        </div>
      </form>
    </Modal>
  );
}
