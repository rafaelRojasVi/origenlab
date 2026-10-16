/**
 * One line of the Cotizador's product table (canvas «Productos»). What the operator types is the
 * draft (quantity, recargo, a typed price, a free line's description, the client note); every
 * number shown is the API's `computed` line, only formatted. Typing a price prices the line by
 * hand (`margin_mode: "none"`, «por precio»); «volver a recargo» goes back to the recargo it had.
 *
 * A cost is typed (amount + currency) on a free line, on a catalog line the API found no cost for,
 * or after «cambiar costo»; a typed cost replaces the catalog's (`cost_observation_id` cleared) and
 * «usar costo del catálogo» drops it. While the prices are for an older draft (`stale`), the
 * numbers are dimmed and marked busy.
 */
import { useEffect, useRef, useState } from "react";
import { formatMoney } from "../money";
import type { DraftProblem } from "./draftEditor";
import type { ComputedLine, QuoteDraftItem } from "./quoteApi";
import { amountTextToDecimal, decimalToPercentText, formatPct, percentTextToDecimal, priceTextToDecimal, readQuantity } from "./quoteFormat";
import { ProductImage } from "../catalog/ProductImage";

/** Product · Cant. · Costo unit. · Recargo · Precio unit. · Total · Margen · menu. */
export const GRID_FULL = "grid-cols-[minmax(0,1fr)_72px_176px_104px_140px_140px_96px_40px]";
/** «Vista cliente»: no cost, recargo or margin. */
export const GRID_CLIENT = "grid-cols-[minmax(0,1fr)_72px_140px_140px_40px]";

const MARGIN_STATE: Record<string, { label: string; cls: string }> = {
  no_cost: { label: "Sin costo", cls: "bg-canvas-sunken text-ink-muted" },
  loss: { label: "Pérdida", cls: "bg-bad-bg text-bad" },
  below_target: { label: "Bajo", cls: "bg-warn-bg text-warn" },
  on_target: { label: "En rango", cls: "bg-good-bg text-good" },
  above_target: { label: "Sobre", cls: "bg-info-bg text-info" },
};

export interface LineProduct {
  code: string | null;
  supplier: string | null;
  imageId: string | null;
  kind: string | null;
}

const CELL_INPUT =
  "h-8 w-full min-w-0 rounded-md border border-line-strong bg-canvas-raised px-2 text-right font-mono text-[13.5px] tabular-nums text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50";

/** An input that shows a formatted value at rest and the raw one while it is edited. */
export function NumberInput({
  label,
  display,
  editValue,
  placeholder,
  disabled,
  onChange,
  onFocus,
  suffix,
  className = CELL_INPUT,
  invalid = false,
}: {
  label: string;
  display: string;
  editValue: string;
  placeholder?: string;
  disabled: boolean;
  onChange: (text: string) => void;
  onFocus?: () => void;
  suffix?: string;
  className?: string;
  invalid?: boolean;
}) {
  const [text, setText] = useState<string | null>(null);
  return (
    <span className="relative flex items-center">
      <input
        type="text"
        inputMode="decimal"
        aria-label={label}
        value={text ?? display}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        onFocus={(e) => {
          onFocus?.();
          setText(editValue);
          const el = e.currentTarget;
          if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => el.select());
        }}
        onBlur={() => setText(null)}
        onChange={(e) => {
          setText(e.target.value);
          onChange(e.target.value);
        }}
        className={`${className}${suffix ? " pr-6" : ""}`}
      />
      {suffix ? (
        <span aria-hidden="true" className="pointer-events-none absolute right-2 font-mono text-[12px] text-ink-muted">
          {suffix}
        </span>
      ) : null}
    </span>
  );
}

const COST_CURRENCIES = ["CLP", "EUR", "USD"] as const;
type CostCurrency = (typeof COST_CURRENCIES)[number];
const asCurrency = (c: string | null | undefined): CostCurrency | null =>
  (COST_CURRENCIES as readonly string[]).includes(c ?? "") ? (c as CostCurrency) : null;

/** A typed cost: the amount and its currency go together (the API refuses one without the other). */
function CostInputs({
  n,
  item,
  line,
  disabled,
  onPatch,
  onEditing,
}: {
  n: number;
  item: QuoteDraftItem;
  line: ComputedLine | null;
  disabled: boolean;
  onPatch: (patch: Partial<QuoteDraftItem>) => void;
  /** The operator is working on the cost: keep the inputs even if it is cleared. */
  onEditing: () => void;
}) {
  const [picked, setPicked] = useState<CostCurrency>(asCurrency(item.cost_currency) ?? asCurrency(line?.cost_currency) ?? "CLP");
  const currency = asCurrency(item.cost_currency) ?? picked;
  const amount = item.unit_cost ?? "";
  return (
    <span className="flex items-center gap-1">
      <span className="min-w-0 flex-1">
        <NumberInput
          label={`Costo unitario de la línea ${n}`}
          display={amount ? (formatMoney(amount, currency) ?? amount) : ""}
          editValue={amount}
          placeholder="costo"
          disabled={disabled}
          onFocus={onEditing}
          onChange={(t) => {
            onEditing();
            const value = amountTextToDecimal(t);
            onPatch(value === null ? { unit_cost: null, cost_currency: null } : { unit_cost: value, cost_currency: currency, cost_observation_id: null });
          }}
        />
      </span>
      <select
        aria-label={`Moneda del costo de la línea ${n}`}
        value={currency}
        disabled={disabled}
        onFocus={onEditing}
        onChange={(e) => {
          onEditing();
          const next = e.target.value as CostCurrency;
          setPicked(next);
          if (item.unit_cost) onPatch({ cost_currency: next, cost_observation_id: null });
        }}
        className="h-8 w-[58px] shrink-0 rounded-md border border-line-strong bg-canvas-raised px-1 text-[12.5px] text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:opacity-50"
      >
        {COST_CURRENCIES.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </span>
  );
}

const qtyText = (q: string) => {
  const n = Number(q);
  return q.trim() && Number.isFinite(n) ? n.toLocaleString("es-CL", { maximumFractionDigits: 6, useGrouping: false }) : q;
};

export function LineRow({
  index,
  item,
  line,
  product,
  currency,
  problems,
  clientView,
  stale,
  disabled,
  focusDescription,
  onPatch,
  onPrincipal,
  onRemove,
}: {
  index: number;
  item: QuoteDraftItem;
  /** The API's priced line for this item, or null until it answers. */
  line: ComputedLine | null;
  product: LineProduct | null;
  currency: string;
  problems: DraftProblem[];
  clientView: boolean;
  /** The prices shown are for an older draft: dimmed and marked busy until the API answers. */
  stale: boolean;
  disabled: boolean;
  /** A new free line: put the cursor in its description. */
  focusDescription: boolean;
  onPatch: (patch: Partial<QuoteDraftItem>) => void;
  onPrincipal: () => void;
  onRemove: () => void;
}) {
  const n = index + 1;
  const free = !item.product_id;
  const byPrice = item.margin_mode === "none";
  const principal = line ? line.is_principal : !!item.is_principal;
  const [menu, setMenu] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [costOpen, setCostOpen] = useState(false);
  const description = useRef<HTMLInputElement>(null);
  const note = useRef<HTMLTextAreaElement>(null);
  const menuBox = useRef<HTMLDivElement>(null);
  const focusNote = useRef(false);

  useEffect(() => {
    if (focusDescription) description.current?.focus();
  }, [focusDescription]);

  useEffect(() => {
    if (noteOpen && focusNote.current) {
      focusNote.current = false;
      note.current?.focus();
    }
  }, [noteOpen]);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menuBox.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [menu]);

  const money = (amount: string | null | undefined, cur: string | null | undefined = currency) => formatMoney(amount, cur);
  const costClp =
    line && line.unit_base && Number(line.expense_to_line) > 0
      ? `${money(line.unit_base, "CLP")} con gastos`
      : line && line.cost_currency && line.cost_currency !== "CLP"
        ? money(line.cost_clp_unit, "CLP")
        : null;
  const state = line ? (MARGIN_STATE[line.margin_state] ?? { label: line.margin_state, cls: "bg-canvas-sunken text-ink-muted" }) : null;
  const rowProblems = problems.filter((p) => p.field.startsWith("items."));
  // A value with a problem is shown as typed, never re-formatted.
  const priceProblem = rowProblems.some((p) => p.field === "items.typed_unit_price");
  const showNote = noteOpen || !!item.line_note_es;
  const recargoDefault = item.margin_pct ? undefined : decimalToPercentText(line?.margin_pct) || undefined;
  const typedCost = item.unit_cost != null || item.cost_currency != null;
  const costInputs = free || typedCost || costOpen || (line !== null && !line.cost_currency);
  const busy = stale ? { "aria-busy": true as const } : {};
  const dim = stale ? " opacity-50" : "";

  return (
    <div role="row" aria-label={`Línea ${n}: ${item.description || "sin descripción"}`} className="border-b border-line/70">
      <div className={`grid ${clientView ? GRID_CLIENT : GRID_FULL} items-center gap-3 px-5 py-3`}>
        <div role="cell" className="flex min-w-0 items-center gap-3">
          <ProductImage imageId={product?.imageId ?? null} kind={product?.kind ?? item.item_type ?? null} alt="" size="thumb" />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            {free ? (
              <input
                ref={description}
                type="text"
                aria-label={`Descripción de la línea ${n}`}
                aria-invalid={rowProblems.some((p) => p.field === "items.description") || undefined}
                value={item.description}
                disabled={disabled}
                placeholder="Qué es: servicio, flete, repuesto…"
                onChange={(e) => onPatch({ description: e.target.value })}
                className="h-8 w-full min-w-0 rounded-md border border-line-strong bg-canvas-raised px-2 text-[14px] font-semibold text-ink placeholder:font-normal placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:opacity-50"
              />
            ) : (
              <span className="truncate text-[14px] font-semibold text-ink" title={item.description}>
                {item.description}
              </span>
            )}
            {product?.code || (product?.supplier && !clientView) || free ? (
              <span className="truncate text-[12.5px] text-ink-muted">
                {product?.code ? <span className="font-mono">{product.code}</span> : null}
                {product?.code && product?.supplier && !clientView ? " · " : null}
                {clientView ? null : (product?.supplier ?? null)}
                {free && !product?.code ? "Línea libre (sin catálogo)" : null}
              </span>
            ) : null}
            {principal && !clientView ? (
              <span className="self-start rounded-md bg-brand-50 px-1.5 py-0.5 text-[11.5px] font-semibold text-brand-700">
                Equipo principal · absorbe los gastos
              </span>
            ) : null}
          </div>
        </div>

        <div role="cell">
          <NumberInput
            label={`Cantidad de la línea ${n}`}
            display={item.input_problems?.quantity ? item.quantity : qtyText(item.quantity)}
            editValue={item.input_problems?.quantity ? item.quantity : qtyText(item.quantity)}
            disabled={disabled}
            onChange={(t) => {
              const { value, problem } = readQuantity(t);
              onPatch({ quantity: value, input_problems: problem ? { quantity: problem } : undefined });
            }}
          />
        </div>

        {clientView ? null : (
          <div role="cell" {...busy} className="flex flex-col items-end gap-0.5 text-right">
            {costInputs ? (
              <>
                <CostInputs n={n} item={item} line={line} disabled={disabled} onPatch={onPatch} onEditing={() => setCostOpen(true)} />
                {costClp && line?.unit_cost ? <span className={`text-xs tabular-nums text-ink-muted${dim}`}>{costClp}</span> : null}
                {!free && (typedCost || costOpen) ? (
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => {
                      setCostOpen(false);
                      onPatch({ unit_cost: null, cost_currency: null });
                    }}
                    className="text-[12px] font-semibold text-brand-700 hover:underline disabled:opacity-50"
                  >
                    usar costo del catálogo
                  </button>
                ) : null}
              </>
            ) : line?.unit_cost ? (
              <>
                <span className={`font-mono text-[13.5px] tabular-nums text-ink${dim}`}>{money(line.unit_cost, line.cost_currency)}</span>
                {costClp ? <span className={`text-xs tabular-nums text-ink-muted${dim}`}>{costClp}</span> : null}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => setCostOpen(true)}
                  className="text-[12px] font-semibold text-brand-700 hover:underline disabled:opacity-50"
                >
                  cambiar costo
                </button>
              </>
            ) : (
              <span className="text-[13px] text-ink-faint">—</span>
            )}
          </div>
        )}

        {clientView ? null : (
          <div role="cell" className="flex flex-col items-end gap-0.5">
            {byPrice ? (
              <>
                <span className="text-[11.5px] font-semibold uppercase tracking-wide text-ink-muted">por precio</span>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onPatch({ margin_mode: "markup", typed_unit_price: null })}
                  className="text-[12px] font-semibold text-brand-700 hover:underline disabled:opacity-50"
                >
                  volver a recargo
                </button>
              </>
            ) : (
              <NumberInput
                label={item.margin_mode === "margin" ? `Margen de la línea ${n}` : `Recargo de la línea ${n}`}
                display={decimalToPercentText(item.margin_pct)}
                editValue={decimalToPercentText(item.margin_pct)}
                placeholder={recargoDefault}
                suffix="%"
                disabled={disabled}
                onChange={(t) => onPatch({ margin_pct: percentTextToDecimal(t) })}
              />
            )}
          </div>
        )}

        <div role="cell" {...busy} className={dim.trim()}>
          <NumberInput
            label={`Precio unitario de la línea ${n}`}
            display={
              priceProblem
                ? (item.typed_unit_price ?? "")
                : (money(line?.unit_price) ?? (byPrice ? (money(item.typed_unit_price) ?? "") : ""))
            }
            editValue={(byPrice ? item.typed_unit_price : line?.unit_price) ?? ""}
            disabled={disabled}
            onChange={(t) => onPatch({ margin_mode: "none", typed_unit_price: priceTextToDecimal(t) })}
          />
        </div>

        <div role="cell" {...busy} className={`text-right font-mono text-[14px] font-medium tabular-nums text-ink${dim}`}>
          {money(line?.line_total) ?? <span className="text-ink-faint">—</span>}
        </div>

        {clientView ? null : (
          <div role="cell" {...busy} className={`flex flex-col items-end gap-1${dim}`}>
            {line?.gross_margin ? <span className="font-mono text-[12.5px] tabular-nums text-ink">{formatPct(line.gross_margin)}</span> : null}
            {state ? (
              <span className={`inline-flex h-[22px] items-center rounded-full px-2 text-[12px] font-semibold ${state.cls}`}>{state.label}</span>
            ) : null}
          </div>
        )}

        <div role="cell" ref={menuBox} className="relative flex justify-end">
          <button
            type="button"
            aria-label={`Más acciones de la línea ${n}`}
            aria-haspopup="menu"
            aria-expanded={menu}
            disabled={disabled}
            onClick={() => setMenu((m) => !m)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-md text-lg leading-none text-ink-muted hover:bg-canvas-sunken hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:opacity-50"
          >
            ⋯
          </button>
          {menu ? (
            <div role="menu" aria-label={`Línea ${n}`} className="absolute right-0 top-full z-20 mt-1 w-52 rounded-lg border border-line bg-canvas-raised py-1 text-left shadow-lg">
              {principal ? null : (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenu(false);
                    onPrincipal();
                  }}
                  className="block w-full px-3 py-2 text-left text-[13px] text-ink hover:bg-canvas-sunken"
                >
                  Hacer principal
                </button>
              )}
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  focusNote.current = true;
                  if (noteOpen || item.line_note_es) note.current?.focus();
                  else setNoteOpen(true);
                }}
                className="block w-full px-3 py-2 text-left text-[13px] text-ink hover:bg-canvas-sunken"
              >
                Nota para el cliente
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  onRemove();
                }}
                className="block w-full px-3 py-2 text-left text-[13px] text-bad hover:bg-bad-bg"
              >
                Quitar
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {showNote || rowProblems.length ? (
        <div role="cell" className="space-y-1.5 px-5 pb-3 pl-[76px]">
          {showNote ? (
            <label className="flex flex-col gap-1 text-[12.5px] text-ink-muted">
              <span>Nota para el cliente, línea {n} (sale en el PDF)</span>
              <textarea
                ref={note}
                rows={2}
                value={item.line_note_es ?? ""}
                disabled={disabled}
                onChange={(e) => onPatch({ line_note_es: e.target.value })}
                className="resize-y rounded-md border border-line-strong bg-canvas-raised px-2.5 py-1.5 text-[13px] text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 disabled:opacity-50"
              />
            </label>
          ) : null}
          {rowProblems.map((p) => (
            <p key={`${p.field}-${p.message_es}`} className="text-[12px] text-bad">
              {p.message_es}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
