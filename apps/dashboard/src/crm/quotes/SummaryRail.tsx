/**
 * The Cotizador's right rail (canvas): the totals with the gross margin on its target band,
 * «Economía de la cotización» under them (`EconomicsCard`: sale, product cost, import costs by
 * kind, margin — internal, never printed), the
 * exchange rates (one per currency, «Cambiar» for a manual one with its reason), «Avisos» and what
 * the principal model was quoted at before («<model> antes»).
 *
 * Every number is the API's: `computed` of the last preview, save or workspace for an authored
 * draft, the stored totals and line rates for an issued revision. Nothing is priced here; the
 * margin bar only places the API's margin on a 0–100 % scale.
 *
 * «Vista cliente» (B7) keeps the totals and the rates and hides what is internal: the margin, the
 * warnings (they speak of costs), the past prices (other clients' names and prices) and why a rate
 * was set by hand. Refusals are said in Spanish (`quoteRefusalText`).
 * B4: when the saved totals are older than today's rates or parameters, a banner asks to save, and
 * «Guardar ahora» saves at once (the autosave only runs after an edit).
 */
import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { fetchPriceHistory, fmtDay, type PriceHistory } from "../catalog/catalogApi";
import { loadCatalogIndex, type CatalogIndex } from "../catalog/catalogIndex";
import { distinctQuotes } from "../catalog/SalesSummary";
import { formatMoney } from "../money";
import { EconomicsCard } from "./EconomicsCard";
import { Button } from "../ui";
import { draftProblems, isEditable, principalItemOf, rowOfLine, setFxOverride, type DraftEditorState } from "./draftEditor";
import type { QuoteDraft, QuotePreferences, QuoteWorkspace } from "./quoteApi";
import { quoteRefusalText } from "./quoteRefusal";
import { MARGIN_TARGET_FALLBACK, compareDecimal } from "./preferenceFields";
import { amountTextToDecimal, decimalEditText, decimalToPercentText, formatPct } from "./quoteFormat";
import type { FlushResult } from "./useQuoteDraft";

/**
 * The margin's target band: the workspace's `params` (the cost parameters `margin_target_min` /
 * `margin_target_max` as the engine applies them, «Preferencias»), else the engine's documented
 * fallback 0.25 / 0.45 (an older API, or a null bound). `margin_state` (the words by the number) is
 * always the API's. Placed on the string: "0.275" → 27.5 %, no float. A band whose minimum is above
 * its maximum (set before «Preferencias» checked it) is not drawn: the rail says it is ill-defined.
 */
const TARGET_MIN_FALLBACK = MARGIN_TARGET_FALLBACK.margin_target_min;
const TARGET_MAX_FALLBACK = MARGIN_TARGET_FALLBACK.margin_target_max;
export const BAND_INVERTED = "Banda de margen mal definida en Preferencias";

type Band = { inverted: false; minText: string; maxText: string; minCss: string; maxCss: string } | { inverted: true };

/** A ratio as a CSS percentage ("0.275" → "27.5"), at most 100; null when it is not a plain decimal. */
function bandPoint(ratio: string | null | undefined): { text: string; css: string } | null {
  if (ratio == null) return null;
  const text = decimalToPercentText(String(ratio).trim());
  const css = text.replace(",", ".");
  if (!/^\d+(\.\d+)?$/.test(css)) return null;
  const int = css.split(".")[0];
  return int.length > 3 || Number(int) >= 100 ? { text, css: "100" } : { text, css };
}

export function targetBand(params: QuotePreferences | undefined): Band {
  const minRatio = bandPoint(params?.margin_target_min) ? String(params!.margin_target_min) : TARGET_MIN_FALLBACK;
  const maxRatio = bandPoint(params?.margin_target_max) ? String(params!.margin_target_max) : TARGET_MAX_FALLBACK;
  if (compareDecimal(minRatio, maxRatio) === 1) return { inverted: true };
  const min = bandPoint(minRatio)!;
  const max = bandPoint(maxRatio)!;
  return { inverted: false, minText: min.text, maxText: max.text, minCss: min.css, maxCss: max.css };
}

const MARGIN_STATE: Record<string, { label: string; cls: string }> = {
  no_cost: { label: "Sin costo", cls: "text-[#a9bdb7]" },
  loss: { label: "Pérdida", cls: "text-red-300" },
  below_target: { label: "Bajo el objetivo", cls: "text-amber-300" },
  on_target: { label: "En rango", cls: "text-teal-300" },
  above_target: { label: "Sobre el objetivo", cls: "text-sky-300" },
};

const FX_SOURCE: Record<string, string> = {
  bcentral: "Banco Central",
  manual: "manual",
  supplier_quote: "cotización del proveedor",
};

/**
 * The price history's `client_type` (the catalog's `CLIENT_TYPES`, plus the CRM's own words for an
 * institution), in Spanish; anything else reads «Otro».
 */
const CLIENT_TYPE: Record<string, string> = {
  university: "Universidad",
  public_health: "Salud pública",
  public_other: "Institución pública",
  public_institution: "Institución pública",
  private_company: "Empresa privada",
  research_center: "Centro de investigación",
  hospital_clinic: "Clínica u hospital",
  individual: "Persona natural",
  unknown: "Otro",
  other: "Otro",
};

export function clientTypeLabel(type: string | null | undefined): string | null {
  if (!type) return null;
  return CLIENT_TYPE[type] ?? "Otro";
}

const HISTORY_ROWS = 4;
const REASON_MIN = 3;
const REASON_MAX = 500;

const CARD = "flex flex-col rounded-[14px] border border-line bg-canvas-raised px-4 py-3.5";
const FIELD_CLS =
  "h-10 w-full min-w-0 rounded-md border border-line-strong bg-canvas-raised px-2.5 text-[13.5px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 aria-[invalid=true]:border-bad";

/** «IVA 19 %» from "0.19": display only. */
function ivaLabel(rate: string | null | undefined): string {
  const n = rate == null ? NaN : Number(rate) * 100;
  return Number.isFinite(n) ? `IVA ${n.toLocaleString("es-CL", { maximumFractionDigits: 1 })} %` : "IVA";
}

/** «$ 1.099,02»: an exchange rate keeps its two decimals (a peso amount has none). */
function fmtRate(value: string | null | undefined): string {
  const n = value == null || value === "" ? NaN : Number(value);
  return Number.isFinite(n) ? `$ ${n.toLocaleString("es-CL", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
}

/** Where the margin marker sits on the 0–100 % bar; null without a margin. */
function markerPct(margin: string | null | undefined): number | null {
  const n = margin == null ? NaN : Number(margin) * 100;
  if (!Number.isFinite(n)) return null;
  return Math.round(Math.min(100, Math.max(0, n)) * 100) / 100;
}

type FxRow = { currency: string; clp_per_unit: string; as_of: string | null; source: string | null };

/* ── the rail ─────────────────────────────────────────────────────────────── */

export function SummaryRail({
  state,
  stale,
  disabled,
  clientView,
  edit,
  onSaveNow,
  loadIndex = loadCatalogIndex,
  loadPriceHistory = (modelKey: string) => fetchPriceHistory(modelKey),
}: {
  state: DraftEditorState;
  /** `computed` prices an older draft than the one shown. */
  stale: boolean;
  /** The editor is frozen (someone else saved). */
  disabled: boolean;
  /** «Vista cliente» (B7): no margin, no warnings, no past prices. */
  clientView: boolean;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
  onSaveNow: () => Promise<FlushResult>;
  loadIndex?: () => Promise<CatalogIndex>;
  loadPriceHistory?: (modelKey: string) => Promise<PriceHistory>;
}) {
  const ws = state.workspace;
  const editable = isEditable(ws);
  // The index only names the principal's model (and its key): no principal product, no load.
  const index = useCatalogIndex(loadIndex, !!currentPrincipal(state, editable)?.product_id);
  return (
    <aside aria-label="Resumen de la cotización" className="flex min-w-0 max-w-[400px] flex-[1_1_320px] flex-col gap-3.5">
      {editable ? <PricesChanged state={state} disabled={disabled} onSaveNow={onSaveNow} /> : null}
      <TotalsCard state={state} editable={editable} stale={stale} clientView={clientView} />
      {clientView ? null : (
        <EconomicsCard
          economics={editable ? state.computed?.economics : ws.current?.economics}
          stale={editable && stale}
        />
      )}
      <FxCard state={state} editable={editable} disabled={disabled} clientView={clientView} edit={edit} />
      {clientView ? null : <NoticesCard state={state} editable={editable} />}
      {clientView ? null : <HistoryCard state={state} editable={editable} index={index} loadPriceHistory={loadPriceHistory} />}
    </aside>
  );
}

/** The catalog index (cached by `loadCatalogIndex`, so no new request): the principal's model and key. */
function useCatalogIndex(
  loadIndex: () => Promise<CatalogIndex>,
  wanted: boolean,
): { loaded: boolean; byId: Map<string, CatalogIndex["items"][number]> } {
  const [index, setIndex] = useState<CatalogIndex | null | "failed">(null);
  const loader = useRef(loadIndex);
  loader.current = loadIndex;
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    loader.current().then(
      (loaded) => live && setIndex(loaded),
      () => live && setIndex("failed"),
    );
    return () => {
      live = false;
    };
  }, [wanted]);
  return useMemo(
    () => ({
      loaded: index !== null,
      byId: new Map((index && index !== "failed" ? index.items : []).map((i) => [i.id, i])),
    }),
    [index],
  );
}

/* ── B4: prices changed since the last save ───────────────────────────────── */

function PricesChanged({
  state,
  disabled,
  onSaveNow,
}: {
  state: DraftEditorState;
  disabled: boolean;
  onSaveNow: () => Promise<FlushResult>;
}) {
  const cur = state.workspace.current;
  // Only a draft that was priced and saved before (B4); a save in this page stores today's prices.
  if (!cur?.needs_saving || cur.totals_computed_at == null || state.lastSavedAt !== null) return null;
  // An incomplete draft is not sent (the editor names what is missing next to each field).
  const incomplete = draftProblems(state.draft).length > 0;
  return (
    <div role="status" className="flex flex-col gap-2 rounded-[14px] border border-warn/30 bg-warn-bg px-4 py-3">
      <p className="text-[13px] leading-snug text-ink">
        Los precios cambiaron desde el último guardado (tipo de cambio o parámetros). Guarda para usarlos.
      </p>
      <Button
        variant="primary"
        className="min-h-[44px] self-start px-4"
        busy={state.saveState === "saving"}
        busyLabel="Guardando…"
        disabled={disabled || incomplete}
        onClick={() => void onSaveNow()}
      >
        Guardar ahora
      </Button>
      {incomplete ? <p className="text-[12.5px] text-ink-muted">Completa el borrador para guardarlo.</p> : null}
    </div>
  );
}

/* ── totals and margin ────────────────────────────────────────────────────── */

function TotalsCard({
  state,
  editable,
  stale,
  clientView,
}: {
  state: DraftEditorState;
  editable: boolean;
  stale: boolean;
  clientView: boolean;
}) {
  const cur = state.workspace.current;
  const currency = cur?.quote_currency ?? "CLP";
  const totals = editable ? state.computed?.totals ?? null : null;
  const net = editable ? totals?.net : cur?.subtotal;
  const iva = editable ? totals?.iva : cur?.tax_total;
  const total = editable ? totals?.total : cur?.grand_total;
  const money = (v: string | null | undefined) => formatMoney(v, currency) ?? "—";
  return (
    <section
      aria-label="Totales"
      aria-busy={stale || undefined}
      className={`flex flex-col gap-3 rounded-[14px] bg-[#141617] px-5 py-[18px] text-[#f4f6f5]${stale ? " opacity-60" : ""}`}
    >
      <div className="flex justify-between text-sm">
        <span className="text-[#a9bdb7]">Neto</span>
        <span className="font-mono tabular-nums">{money(net)}</span>
      </div>
      <div className="flex justify-between text-sm">
        <span className="text-[#a9bdb7]">{ivaLabel(cur?.tax_rate)}</span>
        <span className="font-mono tabular-nums">{money(iva)}</span>
      </div>
      <div className="h-px bg-[#2c3433]" />
      <div className="flex items-baseline justify-between">
        <span className="text-sm text-[#a9bdb7]">Total</span>
        <span className="font-mono text-[26px] font-medium tabular-nums">{money(total)}</span>
      </div>
      {editable && !totals ? <p className="text-[12.5px] text-[#a9bdb7]">Todavía no hay precios calculados.</p> : null}
      {totals && !clientView ? (
        <MarginBlock totals={totals} currency={currency} band={targetBand(state.workspace.params)} />
      ) : null}
    </section>
  );
}

function MarginBlock({
  totals,
  currency,
  band,
}: {
  totals: NonNullable<DraftEditorState["computed"]>["totals"];
  currency: string;
  band: Band;
}) {
  const pct = formatPct(totals.margin);
  const at = markerPct(totals.margin);
  const st = MARGIN_STATE[totals.margin_state] ?? { label: totals.margin_state, cls: "text-[#f4f6f5]" };
  const target = band.inverted ? BAND_INVERTED : `objetivo ${band.minText}–${band.maxText} %`;
  const bandLabel = pct ? `Margen bruto ${pct} en una escala de 0 a 100 %; ${target}` : `Sin margen: falta el costo de alguna línea; ${target}`;
  return (
    <div className="flex flex-col gap-2 rounded-[10px] bg-[#1f2425] p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] text-[#a9bdb7]">Margen bruto</span>
        <span className="flex items-baseline gap-2">
          <span className={`text-xs font-semibold ${st.cls}`}>{st.label}</span>
          {pct ? <span className={`text-[22px] font-bold tabular-nums ${st.cls}`}>{pct}</span> : null}
        </span>
      </div>
      <div role="img" aria-label={bandLabel} className="relative h-2.5 rounded-full bg-[#2c3433]">
        {band.inverted ? null : (
          <div
            data-band
            className="absolute inset-y-0 rounded-full bg-[#134e4a]"
            style={{ left: `${band.minCss}%`, width: `calc(${band.maxCss}% - ${band.minCss}%)` }}
          />
        )}
        {at === null ? null : (
          <div
            data-marker
            className="absolute -top-[3px] h-4 w-3 rounded bg-teal-300"
            style={{ left: `calc(${at}% - 6px)` }}
          />
        )}
      </div>
      <div className="flex justify-between text-[11.5px] text-[#a9bdb7]">
        <span>0 %</span>
        <span className={band.inverted ? "text-amber-300" : undefined}>{target}</span>
        <span>100 %</span>
      </div>
      <span className="text-[12.5px] text-[#a9bdb7]">
        Recargo sobre costo {formatPct(totals.markup) ?? "—"} · costo total {formatMoney(totals.cost_base, currency) ?? "—"}
      </span>
    </div>
  );
}

/* ── exchange rates ───────────────────────────────────────────────────────── */

function fxRows(state: DraftEditorState, editable: boolean): FxRow[] {
  if (!editable) {
    // An issued revision: the rate each foreign-currency line was frozen with.
    const seen = new Map<string, FxRow>();
    for (const l of state.workspace.current?.lines ?? []) {
      if (!l.cost_currency || l.cost_currency === "CLP" || !l.fx_rate || seen.has(l.cost_currency)) continue;
      seen.set(l.cost_currency, { currency: l.cost_currency, clp_per_unit: l.fx_rate, as_of: l.fx_as_of, source: l.fx_source });
    }
    return [...seen.values()];
  }
  const rows: FxRow[] = (state.computed?.fx ?? []).map((f) => ({ ...f }));
  // An override not priced yet (or for a currency no line uses) still shows, with what was typed.
  for (const o of state.draft.fx_overrides) {
    if (!rows.some((r) => r.currency === o.currency)) rows.push({ currency: o.currency, clp_per_unit: o.clp_per_unit, as_of: null, source: "manual" });
  }
  return rows;
}

function FxCard({
  state,
  editable,
  disabled,
  clientView,
  edit,
}: {
  state: DraftEditorState;
  editable: boolean;
  disabled: boolean;
  /** «Vista cliente»: a manual rate says «manual», not why (the reason is internal). */
  clientView: boolean;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
}) {
  const headingId = useId();
  const rows = fxRows(state, editable);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section aria-labelledby={headingId} className={`${CARD} gap-2`}>
      <h2 id={headingId} className="text-[15px] font-semibold text-ink">
        Tipo de cambio
      </h2>
      {rows.length === 0 ? <p className="text-[13px] text-ink-muted">Todas las líneas están en pesos.</p> : null}
      {rows.map((r) => {
        const override = editable ? state.draft.fx_overrides.find((o) => o.currency === r.currency) : undefined;
        const changeable = editable && (r.currency === "EUR" || r.currency === "USD");
        const source = override
          ? clientView
            ? "manual"
            : `manual: ${override.reason}`
          : [FX_SOURCE[r.source ?? ""] ?? r.source, r.as_of ? fmtDay(r.as_of) : null].filter(Boolean).join(", ");
        return (
          <div key={r.currency} className="flex flex-col gap-1">
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className="flex items-baseline gap-1.5">
                <span>{r.currency}</span>
                <span className="font-mono tabular-nums">{fmtRate(r.clp_per_unit)}</span>
              </span>
              {changeable && open !== r.currency ? (
                <button
                  type="button"
                  aria-label={`Cambiar ${r.currency}`}
                  disabled={disabled}
                  onClick={() => setOpen(r.currency)}
                  className="min-h-[32px] rounded-md px-2 text-[13px] font-semibold text-brand-700 hover:bg-canvas-sunken focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Cambiar
                </button>
              ) : null}
            </div>
            {source ? <span className="text-xs text-ink-muted">{source}</span> : null}
            {override && open !== r.currency ? (
              <button
                type="button"
                disabled={disabled}
                onClick={() => edit((d) => setFxOverride(d, r.currency as "EUR" | "USD", null, ""))}
                className="min-h-[32px] self-start rounded-md text-[13px] font-semibold text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Volver al Banco Central
              </button>
            ) : null}
            {open === r.currency ? (
              <FxOverrideForm
                currency={r.currency as "EUR" | "USD"}
                initialRate={override?.clp_per_unit ?? r.clp_per_unit}
                initialReason={override?.reason ?? ""}
                onCancel={() => setOpen(null)}
                onApply={(rate, reason) => {
                  edit((d) => setFxOverride(d, r.currency as "EUR" | "USD", rate, reason));
                  setOpen(null);
                }}
              />
            ) : null}
          </div>
        );
      })}
      {rows.length > 0 ? <p className="text-xs text-ink-faint">Uno por moneda en toda la cotización.</p> : null}
    </section>
  );
}

function FxOverrideForm({
  currency,
  initialRate,
  initialReason,
  onCancel,
  onApply,
}: {
  currency: "EUR" | "USD";
  initialRate: string;
  initialReason: string;
  onCancel: () => void;
  onApply: (rate: string, reason: string) => void;
}) {
  const id = useId();
  const [rate, setRate] = useState(() => decimalEditText(initialRate));
  const [reason, setReason] = useState(initialReason);
  const [problem, setProblem] = useState<{ field: "rate" | "reason"; message: string } | null>(null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const value = amountTextToDecimal(rate);
    if (!value || !/^\d+(\.\d+)?$/.test(value) || Number(value) <= 0) {
      setProblem({ field: "rate", message: "Escribe el tipo de cambio en pesos, por ejemplo 1.099,02." });
      return;
    }
    const why = reason.trim();
    if (why.length < REASON_MIN) {
      setProblem({ field: "reason", message: "Indica por qué cambias el tipo de cambio (mínimo 3 letras)." });
      return;
    }
    onApply(value, why);
  };
  return (
    <form onSubmit={submit} className="mt-1 flex flex-col gap-2 rounded-lg border border-line bg-canvas-sunken/60 p-3">
      <label htmlFor={`${id}-rate`} className="text-xs font-medium text-ink">
        Tipo de cambio {currency} (pesos)
      </label>
      <input
        id={`${id}-rate`}
        inputMode="decimal"
        value={rate}
        onChange={(e) => setRate(e.target.value)}
        aria-invalid={problem?.field === "rate" || undefined}
        className={`${FIELD_CLS} text-right font-mono tabular-nums`}
      />
      <label htmlFor={`${id}-reason`} className="text-xs font-medium text-ink">
        Motivo
      </label>
      <input
        id={`${id}-reason`}
        value={reason}
        maxLength={REASON_MAX}
        placeholder="Por ejemplo: tasa acordada con el proveedor"
        onChange={(e) => setReason(e.target.value)}
        aria-invalid={problem?.field === "reason" || undefined}
        className={FIELD_CLS}
      />
      {problem ? (
        <p role="alert" className="text-xs text-bad">
          {problem.message}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" className="min-h-[44px] px-4">
          Usar este tipo de cambio
        </Button>
        <Button className="min-h-[44px] px-4" onClick={onCancel}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

/* ── avisos ───────────────────────────────────────────────────────────────── */

/** The warnings in Spanish. The expense ones (`dhl_fallback:*`, `profile_parameter_missing:*`) are said in «Gastos». */
function notices(state: DraftEditorState, editable: boolean): string[] {
  if (!editable) return [];
  const out: string[] = [];
  const cur = state.workspace.current;
  // The workspace's reason holds until something newer was priced here.
  if (cur?.computed_error && state.computedSeq === 0) out.push(quoteRefusalText(cur.computed_error, "Este borrador no se pudo calcular."));
  const pe = state.previewError;
  // In Spanish (`quoteRefusalText`); a body that is not the API's envelope is not shown.
  if (pe) out.push(quoteRefusalText(pe, "No se pudo actualizar el cálculo; se muestran los últimos precios."));
  for (const w of state.computed?.warnings ?? []) {
    if (w.startsWith("dhl_fallback:") || w.startsWith("profile_parameter_missing:")) continue;
    if (w === "principal_missing") {
      out.push("Hay gastos de importación pero ningún equipo principal.");
      continue;
    }
    const noCost = /^no_cost:(\d+)$/.exec(w);
    if (noCost) {
      const row = rowOfLine(state.draft, state.computedKeys, Number(noCost[1]));
      if (row !== null) out.push(`La línea ${row} no tiene costo.`);
      continue;
    }
    out.push(w);
  }
  return [...new Set(out)];
}

function NoticesCard({ state, editable }: { state: DraftEditorState; editable: boolean }) {
  const headingId = useId();
  const list = notices(state, editable);
  if (list.length === 0) return null;
  return (
    <section aria-labelledby={headingId} className={`${CARD} gap-2.5`}>
      <h2 id={headingId} className="text-[15px] font-semibold text-ink">
        Avisos
      </h2>
      <ul className="flex flex-col gap-2.5">
        {list.map((text) => (
          <li key={text} className="flex items-start gap-2.5">
            <span
              aria-hidden="true"
              className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-100 text-xs font-bold text-amber-900"
            >
              !
            </span>
            <span className="text-[13px] leading-snug text-ink">{text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── the principal model before ───────────────────────────────────────────── */

type Principal = { product_id: string | null; description: string | null };

/** The principal line now: the priced/marked one of the draft, or the stored one of an issued revision. */
function currentPrincipal(state: DraftEditorState, editable: boolean): Principal | null {
  if (editable) {
    const item = principalItemOf(state.draft, state.computed, state.computedKeys);
    return item ? { product_id: item.product_id ?? null, description: item.description } : null;
  }
  const line = state.workspace.current?.lines?.find((l) => l.is_principal);
  return line ? { product_id: line.product_id, description: line.description } : null;
}

/** The principal the workspace's `price_history` was read for (its draft as loaded). */
function savedPrincipal(ws: QuoteWorkspace): Principal | null {
  const cur = ws.current;
  const items = cur?.draft?.items ?? [];
  const n = cur?.computed?.principal_line_no;
  const item = (n ? items[n - 1] : undefined) ?? items.find((i) => i.is_principal);
  return item ? { product_id: item.product_id ?? null, description: item.description } : null;
}

type Loaded = { key: string; history: PriceHistory | null; failed: boolean };

function HistoryCard({
  state,
  editable,
  index,
  loadPriceHistory,
}: {
  state: DraftEditorState;
  editable: boolean;
  index: ReturnType<typeof useCatalogIndex>;
  loadPriceHistory: (modelKey: string) => Promise<PriceHistory>;
}) {
  const headingId = useId();
  const ws = state.workspace;
  const principal = currentPrincipal(state, editable);
  const product = principal?.product_id ? index.byId.get(principal.product_id) : undefined;
  const saved = editable ? savedPrincipal(ws) : principal;
  const same = !!principal && (principal.product_id ?? null) === (saved?.product_id ?? null);
  const key = product?.model_key ?? null;
  // Another principal than the one the workspace read: its own history, by model key.
  const fetchKey = !principal || same || !key || key === ws.price_history?.model_key ? null : key;

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const loader = useRef(loadPriceHistory);
  loader.current = loadPriceHistory;
  useEffect(() => {
    if (!fetchKey || loaded?.key === fetchKey) return;
    let live = true;
    loader.current(fetchKey).then(
      (history) => live && setLoaded({ key: fetchKey, history: Array.isArray(history?.items) ? history : null, failed: false }),
      () => live && setLoaded({ key: fetchKey, history: null, failed: true }),
    );
    return () => {
      live = false;
    };
  }, [fetchKey, loaded?.key]);

  const model = product?.model_number || principal?.description || null;
  const title = principal ? `${model ?? "Equipo principal"} antes` : "Precios anteriores";

  let mode: "none" | "loading" | "failed" | "ready" = "ready";
  let history: PriceHistory | null = null;
  if (!principal) mode = "none";
  else if (fetchKey) {
    if (loaded?.key !== fetchKey) mode = "loading";
    else if (loaded.failed) mode = "failed";
    else history = loaded.history;
  } else if (same || (key && key === ws.price_history?.model_key)) history = ws.price_history;
  else if (!index.loaded) mode = "loading";

  const currency = history?.summary?.currency ?? history?.items[0]?.currency ?? "CLP";
  const median = history?.items.length ? formatMoney(history.median_last_5, currency) : null;
  const line = (text: string) => <p className="text-[13px] text-ink-muted">{text}</p>;
  return (
    <section aria-labelledby={headingId} className={`${CARD} gap-2`}>
      <div className="flex items-baseline justify-between gap-2">
        <h2 id={headingId} className="min-w-0 truncate text-[15px] font-semibold text-ink">
          {title}
        </h2>
        {median ? (
          <span className="shrink-0 text-xs text-ink-muted" title="Mediana de las últimas cinco cotizaciones">
            mediana {median}
          </span>
        ) : null}
      </div>
      {mode === "none"
        ? line("Marca un equipo principal para ver sus precios anteriores.")
        : mode === "loading"
          ? line("Buscando cotizaciones anteriores…")
          : mode === "failed"
            ? line("No se pudieron leer las cotizaciones anteriores.")
            : <HistoryRows history={history} />}
    </section>
  );
}

function HistoryRows({ history }: { history: PriceHistory | null }) {
  const rows = useMemo(() => distinctQuotes(history?.items ?? []).slice(0, HISTORY_ROWS), [history]);
  if (rows.length === 0) return <p className="text-[13px] text-ink-muted">Este equipo no aparece en cotizaciones anteriores.</p>;
  return (
    <ul>
      {rows.map((r, i) => (
        <li
          key={`${r.document_number ?? "doc"}-${r.date ?? ""}-${i}`}
          className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-2 border-t border-line py-1.5 text-[13px]"
        >
          <span className="tabular-nums text-ink-muted">{fmtDay(r.date)}</span>
          <span className="truncate">{r.case?.institution ?? clientTypeLabel(r.client_type) ?? "—"}</span>
          <span className="font-mono tabular-nums">{formatMoney(r.unit_price ?? r.line_total, r.currency) ?? "—"}</span>
        </li>
      ))}
    </ul>
  );
}
