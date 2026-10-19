/**
 * The Cotizador's «Gastos de importación» card (canvas): how the import expenses are worked out —
 * «Perfil» (the Plantilla's flat amounts) or «Manual» — and one card per expense. Internal: the
 * page hides it in «Vista cliente».
 *
 * In Perfil the amounts are the API's and read-only; «Editar montos» (ruling B5) switches to Manual
 * with a copy of what was computed, so the operator edits from the numbers they saw. Going back to
 * Perfil drops the manual expenses: the API adds manual expenses on top of the derived ones in every
 * method, so keeping them would count them twice.
 *
 * A manual expense is a «Tipo» chosen from a list (Flete, Agente de aduana, …), an optional detail
 * and an amount, stored in its label as «Tipo — Detalle» (`expenseKind`): the economics split reads
 * the type, never a guess (owner, 2026-10-19). An older free label shows the type the API reads in
 * it and is kept as typed until the row is changed.
 *
 * «DHL calculado» is no longer offered (2026-10-19): the catalog has no weights or origins and no
 * DHL rates are loaded, so it always fell back to the profile. A draft saved with it opens as it was
 * — the API still prices it, its lines are shown read-only — with a note; nothing is changed until
 * the operator picks Perfil or «Editar montos» (which copies its lines into Manual).
 *
 * Every amount shown is the API's `computed` (or, in Manual, what the operator typed), only formatted.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { loadCatalogIndex, type CatalogIndex } from "../catalog/catalogIndex";
import { formatMoney } from "../money";
import {
  addManualExpense,
  principalItemOf,
  removeManualExpense,
  setExpenseMethod,
  updateManualExpense,
  type DraftProblem,
} from "./draftEditor";
import { EXPENSE_KINDS, encodeExpenseLabel, parseExpenseLabel } from "./expenseKind";
import { NumberInput } from "./LineRow";
import type { Computed, ImportCostKind, QuoteDraft, QuoteDraftExpense } from "./quoteApi";
import { ConfirmDialog } from "../ui";
import { amountTextToDecimal, decimalEditText, formatPct } from "./quoteFormat";

type Method = QuoteDraft["expense_method"];

// «dhl_calc» stays a value the API reads (older drafts), not one the editor offers.
const METHODS: { value: Method; label: string }[] = [
  { value: "profile", label: "Perfil" },
  { value: "manual", label: "Manual" },
];
const MAX_LABEL = 120;

const CURRENCIES = ["CLP", "EUR", "USD"] as const;
const MAX_MANUAL = 20;

const FIELD_CLS =
  "h-10 w-full min-w-0 rounded-md border border-line-strong bg-canvas-raised px-2.5 text-[13.5px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-bad";
const AMOUNT_CLS = `${FIELD_CLS} text-right font-mono tabular-nums`;

type ComputedExpense = Computed["expenses"][number];

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : typeof v === "number" ? String(v) : null);

/** «DHL · 12,5 kg · zona 3»: what the DHL calculation used, from its `calc_inputs`. */
function dhlSummary(inputs: unknown): string {
  const parts = ["DHL"];
  const i = (inputs && typeof inputs === "object" ? inputs : {}) as Record<string, unknown>;
  const kg = text(i.chargeable_kg);
  if (kg && Number.isFinite(Number(kg))) parts.push(`${Number(kg).toLocaleString("es-CL", { maximumFractionDigits: 2 })} kg`);
  const zone = text(i.zone);
  if (zone) parts.push(`zona ${zone}`);
  const duty = formatPct(text(i.duty_rate));
  if (duty) parts.push(duty);
  const origin = text(i.origin_country);
  if (origin && duty) parts.push(`origen ${origin}`);
  return parts.join(" · ");
}

function originOf(e: ComputedExpense): string {
  if (e.method === "profile") return "perfil Plantilla";
  if (e.method === "dhl_calc") return dhlSummary(e.calc_inputs);
  return "manual";
}

/** The Spanish for the expense warnings the API gave; the rest are the «Avisos» card's. */
function expenseNotices(warnings: string[]): string[] {
  const out: string[] = [];
  if (warnings.some((w) => w.startsWith("dhl_fallback:"))) out.push("Faltan pesos o datos: se usó el perfil de la Plantilla.");
  for (const w of warnings) {
    if (w.startsWith("profile_parameter_missing:"))
      out.push(`Falta el parámetro ${w.slice("profile_parameter_missing:".length)} del perfil.`);
  }
  return out;
}

function MethodToggle({ value, disabled, onChange }: { value: Method; disabled: boolean; onChange: (m: Method) => void }) {
  return (
    <div
      role="group"
      aria-label="Cómo calcular los gastos"
      className="inline-flex overflow-hidden rounded-[10px] border border-line-strong"
    >
      {METHODS.map((m, i) => {
        const active = m.value === value;
        return (
          <button
            key={m.value}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(m.value)}
            className={`h-11 px-3.5 text-[13.5px] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-60 ${
              i > 0 ? "border-l border-line-strong" : ""
            } ${active ? "bg-brand-700 font-semibold text-white" : "bg-canvas-raised text-ink hover:bg-canvas-sunken"}`}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}

function ManualExpense({
  index,
  expense,
  problems,
  disabled,
  onPatch,
  onRemove,
}: {
  index: number;
  expense: QuoteDraftExpense;
  problems: DraftProblem[];
  disabled: boolean;
  onPatch: (patch: Partial<QuoteDraftExpense>) => void;
  onRemove: () => void;
}) {
  const n = index + 1;
  const { kind, detail } = parseExpenseLabel(expense.label);
  // What is typed in «Detalle» while it has the focus (a trailing space stays in the box, not in the label).
  const [typing, setTyping] = useState<string | null>(null);
  // A field's problem shows once it has content or was left: a row just added is not red on arrival
  // (the draft still is not sent while it is incomplete).
  const [left, setLeft] = useState({ label: false, amount: false });
  const shown = (field: "label" | "amount") => left[field] || !!expense[field].trim();
  const labelProblem = shown("label") ? (problems.find((p) => p.field === "manual_expenses.label")?.message_es ?? null) : null;
  const amountProblem = shown("amount") ? (problems.find((p) => p.field === "manual_expenses.amount")?.message_es ?? null) : null;
  // A value with a problem is shown as typed, never re-formatted.
  const display = amountProblem ? expense.amount : (formatMoney(expense.amount, expense.currency) ?? expense.amount);
  const room = kind ? MAX_LABEL - encodeExpenseLabel(kind, "x").length + 1 : MAX_LABEL;
  return (
    <li className="flex flex-col gap-1.5 rounded-[10px] border border-line px-3 py-2.5 md:border-0 md:px-0 md:py-0">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-[minmax(160px,200px)_minmax(0,1fr)_minmax(180px,220px)_auto] md:items-center">
        <select
          aria-label={`Tipo del gasto ${n}`}
          aria-invalid={labelProblem ? true : undefined}
          value={kind ?? ""}
          disabled={disabled}
          onChange={(e) => {
            if (e.target.value) onPatch({ label: encodeExpenseLabel(e.target.value as ImportCostKind, typing ?? detail) });
          }}
          onBlur={() => setLeft((l) => ({ ...l, label: true }))}
          className={FIELD_CLS}
        >
          <option value="" disabled>
            Elige el tipo
          </option>
          {EXPENSE_KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          aria-label={`Detalle del gasto ${n}`}
          value={typing ?? detail}
          placeholder="Detalle (opcional)"
          maxLength={room}
          disabled={disabled || !kind}
          onFocus={() => setTyping(detail)}
          onChange={(e) => {
            setTyping(e.target.value);
            if (kind) onPatch({ label: encodeExpenseLabel(kind, e.target.value) });
          }}
          onBlur={() => setTyping(null)}
          className={FIELD_CLS}
        />
        <div className="col-span-2 flex items-center gap-1.5 md:col-span-1">
          <span className="min-w-0 flex-1">
            <NumberInput
              label={`Monto del gasto ${n}`}
              display={display}
              editValue={amountProblem ? expense.amount : decimalEditText(expense.amount)}
              placeholder="monto"
              disabled={disabled}
              invalid={!!amountProblem}
              className={AMOUNT_CLS}
              onBlur={() => setLeft((l) => ({ ...l, amount: true }))}
              onChange={(t) => onPatch({ amount: amountTextToDecimal(t) ?? "" })}
            />
          </span>
          <select
            aria-label={`Moneda del gasto ${n}`}
            value={expense.currency}
            disabled={disabled}
            onChange={(e) =>
              onPatch({
                currency: e.target.value as QuoteDraftExpense["currency"],
              })
            }
            className="h-10 w-[64px] shrink-0 rounded-md border border-line-strong bg-canvas-raised px-1 text-[13px] text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:opacity-50"
          >
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        {disabled ? (
          <span className="hidden md:block" />
        ) : (
          <button
            type="button"
            aria-label={`Quitar el gasto ${n}`}
            onClick={onRemove}
            className="col-span-2 min-h-[36px] justify-self-end rounded-md px-2 text-[12.5px] font-medium text-ink-muted hover:bg-canvas-sunken hover:text-bad focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 md:col-span-1"
          >
            Quitar
          </button>
        )}
      </div>
      {labelProblem || amountProblem ? (
        <div className="flex flex-col gap-0.5">
          {labelProblem ? <p className="text-[12px] text-bad">{labelProblem}</p> : null}
          {amountProblem ? <p className="text-[12px] text-bad">{amountProblem}</p> : null}
        </div>
      ) : null}
    </li>
  );
}

export function ExpensesSection({
  draft,
  computed,
  computedKeys,
  stale,
  invalid,
  disabled,
  edit,
  loadIndex = loadCatalogIndex,
}: {
  draft: QuoteDraft;
  computed: Computed | null;
  /** The uids of the draft `computed` priced (line_no n is `computedKeys[n-1]`). */
  computedKeys: string[];
  /** `computed` prices an older draft than the one shown. */
  stale: boolean;
  invalid: DraftProblem[];
  disabled: boolean;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
  loadIndex?: () => Promise<CatalogIndex>;
}) {
  // Only for the principal's model in the sentence; the index is cached, so this costs no request.
  const [index, setIndex] = useState<CatalogIndex | null>(null);
  const loader = useRef(loadIndex);
  loader.current = loadIndex;
  useEffect(() => {
    let live = true;
    loader.current().then(
      (loaded) => live && setIndex(loaded),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, []);
  const byId = useMemo(() => new Map((index?.items ?? []).map((i) => [i.id, i])), [index]);

  const manual = draft.expense_method === "manual";
  const principal = principalItemOf(draft, computed, computedKeys);
  const principalModel = principal
    ? (principal.product_id && byId.get(principal.product_id)?.model_number) || principal.description || null
    : null;
  const where =
    draft.allocation_mode === "proportional_cost"
      ? "Se reparten según el costo de cada línea."
      : `Se suman al equipo principal${principalModel ? ` (${principalModel})` : ""}. Al cliente no se muestran como línea.`;
  const notices = computed ? expenseNotices(computed.warnings) : [];
  const listProblem = invalid.find((p) => p.field === "manual_expenses" && p.index === undefined)?.message_es ?? null;
  const dim = stale ? " opacity-50" : "";

  // Leaving Manual with amounts typed asks first: a derived method drops them (they would count twice).
  const [confirmLeave, setConfirmLeave] = useState<Method | null>(null);
  // Unique per pane: the Cotizador module keeps several quotes in the page at once.
  const ids = useId();
  const apply = (m: Method) =>
    // Only priced numbers are copied: while `computed` prices an older draft (another method, or a
    // preview that failed) Manual starts from what the draft holds, never from stale amounts.
    edit((d) => setExpenseMethod(d, m, stale ? null : computed));
  const choose = (m: Method) => {
    if (m === draft.expense_method) return;
    if (manual && draft.manual_expenses.length > 0) setConfirmLeave(m);
    else apply(m);
  };

  return (
    <section aria-labelledby={`${ids}-gastos`} className="flex flex-col gap-3 rounded-xl border border-line bg-canvas-raised px-5 py-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 id={`${ids}-gastos`} className="text-[17px] font-semibold text-ink">
            Gastos de importación
          </h2>
          <span className="text-[12.5px] text-ink-muted">{where}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!manual && !disabled && computed && computed.expenses.length > 0 ? (
            <span className="flex items-center gap-1.5">
              {stale ? (
                <span id={`${ids}-espera`} className="text-[12px] text-ink-muted">
                  Espera el cálculo
                </span>
              ) : null}
              <button
                type="button"
                disabled={stale}
                aria-describedby={stale ? `${ids}-espera` : undefined}
                onClick={() => choose("manual")}
                className="h-11 rounded-md px-3 text-[13.5px] font-semibold text-brand-700 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Editar montos
              </button>
            </span>
          ) : null}
          <MethodToggle value={draft.expense_method} disabled={disabled} onChange={choose} />
        </div>
      </header>

      {draft.expense_method === "dhl_calc" ? (
        <p role="note" className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-[12.5px] text-ink">
          Este borrador usa «DHL calculado», que ya no se ofrece. Sus gastos siguen como estaban; para cambiarlos, elige Perfil o
          «Editar montos».
        </p>
      ) : null}

      {manual ? (
        <>
          {draft.manual_expenses.length ? (
            <>
            <div aria-hidden="true" className="hidden grid-cols-[minmax(160px,200px)_minmax(0,1fr)_minmax(180px,220px)_auto] gap-2 text-[12px] text-ink-muted md:grid">
              <span>Tipo</span>
              <span>Detalle (opcional)</span>
              <span className="text-right">Monto</span>
              <span className="w-[52px]" />
            </div>
            <ul aria-label="Gastos manuales" className="flex flex-col gap-2.5 md:gap-2">
              {draft.manual_expenses.map((e, i) => (
                <ManualExpense
                  key={e.uid ?? `at-${i}`}
                  index={i}
                  expense={e}
                  problems={invalid.filter((p) => p.index === i && p.field.startsWith("manual_expenses."))}
                  disabled={disabled}
                  onPatch={(patch) => edit((d) => updateManualExpense(d, i, patch))}
                  onRemove={() => edit((d) => removeManualExpense(d, i))}
                />
              ))}
            </ul>
            </>
          ) : (
            <p className="text-[13px] text-ink-muted">Sin gastos de importación.</p>
          )}
          {listProblem ? <p className="text-[12px] text-bad">{listProblem}</p> : null}
          <div className="flex flex-wrap items-center justify-between gap-2">
            {disabled ? (
              <span />
            ) : (
              <button
                type="button"
                disabled={draft.manual_expenses.length >= MAX_MANUAL}
                onClick={() => edit(addManualExpense)}
                className="min-h-[44px] self-start rounded-md px-1 text-[13.5px] font-semibold text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                + Agregar gasto
              </button>
            )}
            <span className="text-[12px] text-ink-muted">
              Si vuelves a Perfil, estos montos se descartan y se calculan de nuevo.
            </span>
          </div>
        </>
      ) : !computed ? (
        <p className="text-[13px] text-ink-muted">Los gastos aparecen cuando el borrador se calcula.</p>
      ) : computed.expenses.length === 0 ? (
        <p className="text-[13px] text-ink-muted">Sin gastos de importación.</p>
      ) : (
        <ul
          aria-label="Gastos calculados"
          aria-busy={stale || undefined}
          className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4"
        >
          {computed.expenses.map((e) => (
            <li key={e.line_no} className={`flex flex-col gap-1 rounded-[10px] border border-line px-3 py-2.5${dim}`}>
              <span className="text-[12.5px] text-ink-muted">{e.label}</span>
              <span className="font-mono text-[15px] tabular-nums text-ink">{formatMoney(e.amount, e.currency) ?? e.amount}</span>
              <span className="text-[11.5px] text-ink-muted">{originOf(e)}</span>
            </li>
          ))}
        </ul>
      )}

      {notices.map((n) => (
        <p key={n} className="text-[12.5px] text-warn">
          {n}
        </p>
      ))}

      {confirmLeave ? (
        <ConfirmDialog
          title={`¿Volver a ${METHODS.find((m) => m.value === confirmLeave)?.label ?? confirmLeave}?`}
          lines={["Se borrarán los gastos escritos a mano.", "El sistema vuelve a calcular los gastos con el método elegido."]}
          confirmLabel="Borrar y cambiar"
          onConfirm={() => {
            apply(confirmLeave);
            setConfirmLeave(null);
          }}
          onCancel={() => setConfirmLeave(null)}
        />
      ) : null}
    </section>
  );
}
