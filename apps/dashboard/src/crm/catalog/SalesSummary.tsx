/**
 * «Ventas y cotizaciones» on the product panel: what OrigenLab charged for this model, what it cost
 * us, and whether it sold.
 *
 * The numbers first (how often quoted, how often sold, the last price, the range and — for sales
 * and admin — the gross margin), then price against cost over time: one stem per quote from our cost
 * in pesos (the supplier cost in force that day × that day's EUR/USD rate) up to the price quoted, so
 * the stem's length is the margin; the sold ones filled in the brand teal, the rest hollow; hover a
 * quote for its detail, click it to open its case. Then the quotes, the six newest unless asked for
 * all, each with its institution, outcome and margin. A quote read twice shows once. «Vendida» is
 * the quote the case was won WITH (`won_quote_id`), never another quote a won case passed over.
 *
 * Costs and margins arrive only for sales and admin (the API strips them for a viewer), so a viewer
 * sees prices alone. The margin is GROSS: before import freight, customs and transport.
 */
import { useMemo, useState } from "react";
import { crmHash } from "../crmRoute";
import { Badge } from "../ui";
import { fmtDay, fmtMoney, type PriceHistory, type QuotedPrice } from "./catalogApi";

const OUTCOME: Record<string, { label: string; tone: "good" | "bad" | "neutral" | "info" }> = {
  won: { label: "Vendida", tone: "good" },
  other_won: { label: "Ganó otra cotización", tone: "neutral" },
  lost: { label: "Perdida", tone: "bad" },
  abandoned: { label: "Cerrada", tone: "neutral" },
  open: { label: "Abierta", tone: "info" },
};

const WON_FILL = "#0f766e"; // brand-700: the one accent (highlight, not a category)
const CONTEXT = "#a1a1aa"; // zinc-400: hollow quoted dots and their stems
const COST = "#52525b"; // zinc-600: our cost, the foot of each stem
const ROWS_SHOWN = 6;

/** «0.4440» → «44,4 %». */
export function fmtPct(value: string | null | undefined): string {
  const n = value == null ? NaN : Number(value) * 100;
  return Number.isFinite(n) ? `${n.toLocaleString("es-CL", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %` : "—";
}

/** «€ 2.500,00 × $1.100 (16 sept 2026) = $2.750.000» — how a cost became pesos. */
function conversion(c: NonNullable<QuotedPrice["cost"]>): string {
  if (!c.fx) return fmtMoney(c.clp, "CLP");
  return `${fmtMoney(c.price, c.currency)} × ${fmtMoney(c.fx, "CLP")} (${fmtDay(c.fx_as_of)}) = ${fmtMoney(c.clp, "CLP")}`;
}

function costSource(c: NonNullable<QuotedPrice["cost"]>): string {
  const kind = c.price_kind === "dealer_net" ? "lista distribuidor" : c.price_kind === "costing_sheet" ? "hoja de costos" : "costo";
  return `${kind} del ${fmtDay(c.as_of)}${c.after_quote ? ", registrado después de la cotización" : ""}`;
}

/** One row per quote, newest first: the same document read twice (number, date, price) shows once. */
export function distinctQuotes(items: QuotedPrice[]): QuotedPrice[] {
  const seen = new Set<string>();
  const newest = [...items].sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));
  return newest.filter((r) => {
    const key = `${r.document_number ?? ""}|${r.date ?? ""}|${r.unit_price ?? r.line_total ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function SalesSummary({ history }: { history: PriceHistory }) {
  const s = history.summary;
  const items = useMemo(() => distinctQuotes(history.items), [history.items]);
  const [all, setAll] = useState(false);
  if (items.length === 0) {
    return <p className="text-xs text-ink-faint">Este modelo no aparece en cotizaciones anteriores.</p>;
  }
  // An API without the summary (an older deploy) still gets the list.
  const cur = s?.currency ?? items[0]?.currency ?? null;
  const shown = all ? items : items.slice(0, ROWS_SHOWN);
  return (
    <div className="space-y-3">
      {s ? (
        <dl data-testid="sales-stats" className="grid grid-cols-2 gap-x-4 gap-y-2">
          {s.margin ? (
            <div className="col-span-2 flex items-baseline gap-3 rounded-md bg-brand-50 px-3 py-2">
              <dt className="text-xs font-medium text-brand-900">Margen bruto</dt>
              <dd className="text-lg font-semibold tabular-nums text-brand-900">{fmtPct(s.margin.median)}</dd>
              <dd className="ml-auto text-right text-[11px] text-ink-muted">
                mediana de {s.margin.count} · último {fmtPct(s.margin.last)} · {fmtPct(s.margin.min)} – {fmtPct(s.margin.max)}
              </dd>
            </div>
          ) : null}
          <Stat label="Cotizado" value={`${s.times_quoted} ${s.times_quoted === 1 ? "vez" : "veces"}`}
                hint={s.institutions ? `${s.institutions} ${s.institutions === 1 ? "institución" : "instituciones"}` : undefined} />
          <Stat label="Vendido" value={`${s.times_won} ${s.times_won === 1 ? "vez" : "veces"}`}
                hint={s.won_prices.length ? s.won_prices.map((p) => fmtMoney(p, cur)).join(" · ") : "sin ventas registradas"} />
          <Stat label="Último precio" value={s.last ? fmtMoney(s.last.price, cur) : "—"} hint={s.last ? fmtDay(s.last.date) : undefined} />
          <Stat label="Rango" value={s.min && s.max ? `${fmtMoney(s.min, cur)} – ${fmtMoney(s.max, cur)}` : "—"}
                hint={s.median ? `mediana ${fmtMoney(s.median, cur)}` : undefined} />
        </dl>
      ) : null}
      <PriceChart items={items} currency={cur} />
      <ul data-testid="quoted-history" className="divide-y divide-line rounded-md border border-line text-xs">
        {shown.map((r, i) => (
          <QuoteRow key={`${r.document_number ?? "doc"}-${i}`} r={r} />
        ))}
      </ul>
      {items.length > ROWS_SHOWN ? (
        <button type="button" onClick={() => setAll(!all)} aria-expanded={all}
                className="text-xs font-medium text-brand-700 hover:underline">
          {all ? "Ver menos" : `Ver las ${items.length} cotizaciones`}
        </button>
      ) : null}
      {s?.margin ? (
        <p className="text-[11px] text-ink-faint">
          Margen bruto = (precio − costo) / precio. Costo del proveedor vigente ese día, al dólar o euro de ese día; antes
          de flete, aduana y transporte.
        </p>
      ) : null}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-ink-faint">{label}</dt>
      <dd className="truncate text-sm font-semibold tabular-nums text-ink">{value}</dd>
      {hint ? <dd className="truncate text-[11px] text-ink-muted">{hint}</dd> : null}
    </div>
  );
}

function QuoteRow({ r }: { r: QuotedPrice }) {
  const outcome = r.case ? OUTCOME[r.case.outcome] ?? OUTCOME.open : null;
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-2.5 py-1.5">
      <span className="font-medium tabular-nums text-ink">{fmtMoney(r.unit_price ?? r.line_total, r.currency)}</span>
      {r.margin && r.cost ? (
        <span title={`Costo ${conversion(r.cost)} · ${costSource(r.cost)}`}
              className="cursor-help rounded bg-brand-50 px-1 font-medium tabular-nums text-brand-900">
          {fmtPct(r.margin)}
        </span>
      ) : null}
      {r.qty && Number(r.qty) !== 1 ? <span className="text-ink-faint">× {r.qty}</span> : null}
      {r.check_status === "disputed" ? <Badge tone="warn">En duda</Badge> : null}
      {r.case?.institution ? (
        <a href={crmHash("oportunidades", r.case.id)} className="truncate text-brand-700 hover:underline">
          {r.case.institution}
        </a>
      ) : null}
      {outcome ? <Badge tone={outcome.tone}>{outcome.label}</Badge> : <span className="text-ink-faint">Sin caso</span>}
      <span className="ml-auto text-ink-faint">
        {r.document_number ? `N° ${r.document_number} · ` : ""}
        {fmtDay(r.date)}
      </span>
    </li>
  );
}

/* ───────────────────────────────────────────────────────────── chart ── */

const W = 560;
const H = 176;
const PAD = { top: 12, right: 14, bottom: 22, left: 78 };

interface Point { t: number; v: number; c: number | null; r: QuotedPrice }

function PriceChart({ items, currency }: { items: QuotedPrice[]; currency: string | null }) {
  const [hover, setHover] = useState<number | null>(null);
  const points: Point[] = items
    .filter((r) => r.date && (r.unit_price ?? r.line_total) && r.currency === currency && (!r.qty || Number(r.qty) === 1))
    .map((r) => ({ t: Date.parse(r.date as string), v: Number(r.unit_price ?? r.line_total),
                   c: r.cost && Number.isFinite(Number(r.cost.clp)) ? Number(r.cost.clp) : null, r }))
    .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v))
    .sort((a, b) => a.t - b.t);
  if (points.length === 0) return null;
  const ts = points.map((p) => p.t);
  const prices = points.map((p) => p.v);
  const costs = points.flatMap((p) => (p.c != null ? [p.c] : []));
  const all = [...prices, ...costs];
  const [t0, t1] = [Math.min(...ts), Math.max(...ts)];
  const span = Math.max(...all) - Math.min(...all);
  const pad = span * 0.08 || Math.max(...all) * 0.1 || 1;
  const [v0, v1] = [Math.max(0, Math.min(...all) - pad), Math.max(...all) + pad];
  // Points a fraction of a pixel apart in time (one quote resent) still get their own stem.
  const x = (t: number) => PAD.left + (t1 === t0 ? (W - PAD.left - PAD.right) / 2 : ((t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right));
  const y = (v: number) => PAD.top + (1 - (v - v0) / (v1 - v0)) * (H - PAD.top - PAD.bottom);
  const won = points.filter((p) => p.r.case?.outcome === "won").length;
  const label = `Precio cotizado${costs.length ? " y costo" : ""} en el tiempo: ${points.length} `
    + `${points.length === 1 ? "cotización" : "cotizaciones"}${won ? `, ${won} vendida${won === 1 ? "" : "s"}` : ""}`;
  // The lowest and highest price and the lowest cost as y ticks; one too close to a kept one is dropped.
  const ticks = [Math.max(...prices), Math.min(...prices), ...(costs.length ? [Math.min(...costs)] : [])]
    .reduce<number[]>((kept, v) => (kept.some((k) => Math.abs(y(k) - y(v)) < 18) ? kept : [...kept, v]), []);
  const costPath = points.filter((p) => p.c != null).map((p, i) => `${i ? "L" : "M"}${x(p.t)},${y(p.c as number)}`).join(" ");
  const hp = hover != null ? points[hover] : null;

  return (
    <figure className="space-y-1">
      <div className="relative" onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="h-auto w-full overflow-visible">
          {ticks.map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="currentColor" className="text-line" strokeWidth={1} />
              <text x={PAD.left - 12} y={y(v) + 3} textAnchor="end" className="fill-ink-faint text-[10px] tabular-nums">
                {fmtMoney(String(Math.round(v)), currency)}
              </text>
            </g>
          ))}
          <text x={PAD.left} y={H - 4} className="fill-ink-faint text-[10px]">{fmtDay(new Date(t0).toISOString().slice(0, 10))}</text>
          {t1 !== t0 ? (
            <text x={W - PAD.right} y={H - 4} textAnchor="end" className="fill-ink-faint text-[10px]">
              {fmtDay(new Date(t1).toISOString().slice(0, 10))}
            </text>
          ) : null}
          {/* our cost in pesos over time — the floor each margin stands on */}
          {costPath ? <path d={costPath} fill="none" stroke={COST} strokeWidth={1.5} strokeLinejoin="round" opacity={0.45} /> : null}
          {points.map((p, i) => {
            const isWon = p.r.case?.outcome === "won";
            const active = hover === i;
            const dim = hover != null && !active;
            const mark = (
              <g data-testid={isWon ? "price-dot-won" : undefined} opacity={dim ? 0.35 : 1}>
                {p.c != null ? (
                  <>
                    <line x1={x(p.t)} x2={x(p.t)} y1={y(p.c)} y2={y(p.v)} stroke={isWon ? WON_FILL : CONTEXT}
                          strokeWidth={active ? 3 : 2} strokeLinecap="round" />
                    <line x1={x(p.t) - 4} x2={x(p.t) + 4} y1={y(p.c)} y2={y(p.c)} stroke={COST} strokeWidth={2} strokeLinecap="round" />
                  </>
                ) : null}
                <circle cx={x(p.t)} cy={y(p.v)} r={active ? 6.5 : isWon ? 5.5 : 4.5}
                        fill={isWon ? WON_FILL : "white"} stroke={isWon ? "white" : CONTEXT} strokeWidth={2} pointerEvents="none" />
                {/* hit target larger than the mark */}
                <circle cx={x(p.t)} cy={y(p.v)} r={11} fill="transparent" data-testid="price-dot" className="cursor-pointer"
                        onMouseEnter={() => setHover(i)} />
              </g>
            );
            return p.r.case ? (
              <a key={i} href={crmHash("oportunidades", p.r.case.id)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                 aria-label={`${fmtMoney(String(p.v), currency)}, ${fmtDay(p.r.date)}${p.r.case.institution ? `, ${p.r.case.institution}` : ""}: abrir el caso`}>
                {mark}
              </a>
            ) : (
              <g key={i}>{mark}</g>
            );
          })}
        </svg>
        {hp ? <Tooltip p={hp} left={(x(hp.t) / W) * 100} top={(y(hp.v) / H) * 100} currency={currency} /> : null}
      </div>
      <figcaption className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-1">
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill={WON_FILL} /></svg>Vendida
        </span>
        <span className="inline-flex items-center gap-1">
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.5" fill="white" stroke={CONTEXT} strokeWidth="1.5" /></svg>Cotizada
        </span>
        {costs.length ? (
          <span className="inline-flex items-center gap-1">
            <svg width="10" height="12" aria-hidden="true">
              <line x1="5" x2="5" y1="1" y2="10" stroke={CONTEXT} strokeWidth="2" /><line x1="1" x2="9" y1="10" y2="10" stroke={COST} strokeWidth="2" />
            </svg>
            Costo → precio: el largo es el margen
          </span>
        ) : null}
        <span className="text-ink-faint">Pasa el cursor por una cotización; clic abre su caso.</span>
      </figcaption>
    </figure>
  );
}

function Tooltip({ p, left, top, currency }: { p: Point; left: number; top: number; currency: string | null }) {
  const r = p.r;
  const outcome = r.case ? OUTCOME[r.case.outcome]?.label ?? "Abierta" : "Sin caso";
  // Near the right edge the card opens to the left, so it never leaves the panel.
  const align = left > 70 ? "-translate-x-full" : left < 30 ? "" : "-translate-x-1/2";
  // A point in the upper half gets its card below it, so the card never covers the numbers above the chart.
  const below = top < 50;
  return (
    <div role="tooltip" style={{ left: `${left}%`, top: `${top}%` }}
         className={`pointer-events-none absolute z-10 w-72 ${align} ${below ? "translate-y-[14px]" : "-translate-y-[calc(100%+14px)]"} rounded-md border border-line bg-canvas-raised p-2.5 text-[11px] shadow-lg`}>
      <p className="truncate font-semibold text-ink">{r.case?.institution ?? "Sin institución"}</p>
      <p className="text-ink-faint">{outcome} · {r.document_number ? `N° ${r.document_number} · ` : ""}{fmtDay(r.date)}</p>
      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 tabular-nums">
        <dt className="text-ink-muted">Precio</dt>
        <dd className="text-right font-medium text-ink">{fmtMoney(String(p.v), currency)}</dd>
        {r.cost ? (
          <>
            <dt className="text-ink-muted">Costo</dt>
            <dd className="text-right text-ink">{fmtMoney(r.cost.clp, "CLP")}</dd>
            <dd className="col-span-2 text-right text-ink-faint">{conversion(r.cost)}</dd>
            <dd className="col-span-2 text-right text-ink-faint">{costSource(r.cost)}</dd>
            <dt className="text-ink-muted">Margen</dt>
            <dd className="text-right font-semibold text-brand-900">{fmtPct(r.margin)}</dd>
          </>
        ) : null}
      </dl>
    </div>
  );
}
