/**
 * «Ventas» on the product panel: what OrigenLab charged for this model and whether it sold.
 *
 * Four numbers first (how often quoted, how often sold and at what price, the last price, the
 * range), then the prices over time — one dot per quote, the ones that sold filled in the brand
 * teal and named in the legend, the rest hollow gray context, the median as a dashed rule — and
 * then every quote with its institution, a link to its case and its outcome in words. Sell prices
 * only, never costs: every role sees them. «Vendida» is the quote the case was won WITH
 * (`won_quote_id`), never another quote a won case passed over.
 */
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
const CONTEXT = "#a1a1aa"; // zinc-400: hollow context dots and the median rule

export function SalesSummary({ history }: { history: PriceHistory }) {
  const s = history.summary;
  if (history.items.length === 0) {
    return <p className="text-xs text-ink-faint">Este modelo no aparece en cotizaciones anteriores.</p>;
  }
  // An API without the summary (an older deploy) still gets the list.
  const cur = s?.currency ?? history.items[0]?.currency ?? null;
  return (
    <div className="space-y-3">
      {s ? (
      <dl data-testid="sales-stats" className="grid grid-cols-2 gap-x-4 gap-y-2">
        <Stat label="Cotizado" value={`${s.times_quoted} ${s.times_quoted === 1 ? "vez" : "veces"}`}
              hint={s.institutions ? `${s.institutions} ${s.institutions === 1 ? "institución" : "instituciones"}` : undefined} />
        <Stat label="Vendido" value={`${s.times_won} ${s.times_won === 1 ? "vez" : "veces"}`}
              hint={s.won_prices.length ? s.won_prices.map((p) => fmtMoney(p, cur)).join(" · ") : "sin ventas registradas"} />
        <Stat label="Último precio" value={s.last ? fmtMoney(s.last.price, cur) : "—"} hint={s.last ? fmtDay(s.last.date) : undefined} />
        <Stat label="Rango" value={s.min && s.max ? `${fmtMoney(s.min, cur)} – ${fmtMoney(s.max, cur)}` : "—"}
              hint={s.median ? `mediana ${fmtMoney(s.median, cur)}` : undefined} />
      </dl>
      ) : null}
      <PriceChart items={history.items} currency={cur} median={s?.median ?? history.median_last_5} />
      <ul data-testid="quoted-history" className="divide-y divide-line rounded-md border border-line text-xs">
        {history.items.map((r, i) => (
          <QuoteRow key={`${r.document_number ?? "doc"}-${i}`} r={r} />
        ))}
      </ul>
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
const H = 132;
const PAD = { top: 10, right: 12, bottom: 22, left: 72 };

function PriceChart({ items, currency, median }: { items: QuotedPrice[]; currency: string | null; median: string | null }) {
  const points = items
    .filter((r) => r.date && r.line_total && r.currency === currency && (!r.qty || Number(r.qty) === 1))
    .map((r) => ({ t: Date.parse(r.date as string), v: Number(r.line_total), r }))
    .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v));
  if (points.length === 0) return null;
  const ts = points.map((p) => p.t);
  const vs = points.map((p) => p.v);
  const [t0, t1] = [Math.min(...ts), Math.max(...ts)];
  const pad = (Math.max(...vs) - Math.min(...vs)) * 0.15 || Math.max(...vs) * 0.1 || 1;
  const [v0, v1] = [Math.max(0, Math.min(...vs) - pad), Math.max(...vs) + pad];
  const x = (t: number) => PAD.left + (t1 === t0 ? (W - PAD.left - PAD.right) / 2 : ((t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right));
  const y = (v: number) => PAD.top + (1 - (v - v0) / (v1 - v0)) * (H - PAD.top - PAD.bottom);
  const med = median != null ? Number(median) : null;
  const won = points.filter((p) => p.r.case?.outcome === "won").length;
  const label = `Precio cotizado en el tiempo: ${points.length} ${points.length === 1 ? "cotización" : "cotizaciones"}`
    + (won ? `, ${won} vendida${won === 1 ? "" : "s"}` : "");

  return (
    <figure className="space-y-1">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="h-auto w-full overflow-visible">
        {/* recessive frame: the lowest and the highest price quoted, as the two y ticks */}
        {[...new Set([Math.min(...vs), Math.max(...vs)])].map((v) => (
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
        {med != null && Number.isFinite(med) ? (
          <line x1={PAD.left} x2={W - PAD.right} y1={y(med)} y2={y(med)} stroke={CONTEXT} strokeWidth={1.5} strokeDasharray="4 4">
            <title>Mediana {fmtMoney(String(med), currency)}</title>
          </line>
        ) : null}
        {points.map((p, i) => {
          const isWon = p.r.case?.outcome === "won";
          return (
            <g key={i} data-testid={isWon ? "price-dot-won" : undefined}>
              {/* hit target larger than the mark */}
              <circle cx={x(p.t)} cy={y(p.v)} r={10} fill="transparent" data-testid="price-dot">
                <title>
                  {`${fmtMoney(String(p.v), currency)} · ${fmtDay(p.r.date)}${p.r.document_number ? ` · N° ${p.r.document_number}` : ""}`
                    + `${p.r.case?.institution ? ` · ${p.r.case.institution}` : ""}${isWon ? " · vendida" : ""}`}
                </title>
              </circle>
              <circle
                cx={x(p.t)}
                cy={y(p.v)}
                r={isWon ? 5.5 : 4.5}
                fill={isWon ? WON_FILL : "white"}
                stroke={isWon ? "white" : CONTEXT}
                strokeWidth={2}
                pointerEvents="none"
              />
            </g>
          );
        })}
      </svg>
      <figcaption className="flex flex-wrap items-center gap-x-3 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-1">
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill={WON_FILL} /></svg>Vendida
        </span>
        <span className="inline-flex items-center gap-1">
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.5" fill="white" stroke={CONTEXT} strokeWidth="1.5" /></svg>Cotizada
        </span>
        {med != null ? (
          <span className="inline-flex items-center gap-1">
            <svg width="14" height="10" aria-hidden="true"><line x1="0" x2="14" y1="5" y2="5" stroke={CONTEXT} strokeWidth="1.5" strokeDasharray="3 3" /></svg>
            Mediana
          </span>
        ) : null}
        <span className="text-ink-faint">Precio por unidad; pasa el cursor por un punto para ver la cotización.</span>
      </figcaption>
    </figure>
  );
}
