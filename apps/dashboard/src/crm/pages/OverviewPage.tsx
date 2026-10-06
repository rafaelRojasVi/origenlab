import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { fetchFx, fetchPipeline } from "../crmApi";
import type { CrmSection } from "../crmRoute";
import { fmtClp, fmtRate, parseAmount, toClp, type FxRate, type FxResponse } from "../fx";
import { PageHeader, ResourceGate } from "../ui";
import { useResource, type ResourceState } from "../useResource";
import { QuoteNumberBox } from "../QuoteNumberBox";
import { SANTIAGO, WEEKDAYS, todayInSantiago } from "../marketing/calendar";
import { TodayBody } from "./TodayBody";

type Navigate = (s: CrmSection, id?: string) => void;

/**
 * «Hoy», the first page: what the open cases ask of an operator today (`TodayBody`: tasks due,
 * clients who answered, the 3 · 14 · 30 follow-ups, what is left to add or decide), the next quote
 * number and the day's exchange rates. The data-health counts live on Revisión → Estado de los datos.
 */
export function OverviewPage({ navigate }: { navigate: Navigate }) {
  const [fx, reloadFx] = useResource(fetchFx);
  const [pipeline, reloadPipeline, refreshing] = useResource(fetchPipeline);
  return (
    <div className="space-y-5">
      <PageHeader
        title="Hoy"
        subtitle="Tareas, respuestas por contestar, seguimientos y lo que falta agregar."
        actions={<Clock />}
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <QuoteNumberBox pipeline={pipeline} />
        <FxSection state={fx} reload={reloadFx} />
      </div>
      <ResourceGate state={pipeline} reload={reloadPipeline} skeleton={<FollowUpsSkeleton />}>
        {(p) => <TodayBody items={p.items} navigate={navigate} onChanged={reloadPipeline} refreshing={refreshing} />}
      </ResourceGate>
    </div>
  );
}

/* ───────────────────────────────────────────────────────── exchange rates ── */

function FxSection({ state, reload }: { state: ResourceState<FxResponse>; reload: () => void }) {
  if (state.kind === "loading") {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1.2fr]" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="crm-skeleton h-[7.5rem] rounded-xl" />
        ))}
      </div>
    );
  }
  if (state.kind !== "ready") {
    return (
      <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-canvas-sunken/70 px-4 py-3">
        <p className="text-[13px] text-ink-muted">Tipo de cambio no disponible en este momento.</p>
        <button
          type="button"
          onClick={reload}
          className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink transition-colors hover:border-line-strong active:translate-y-px"
        >
          Reintentar
        </button>
      </div>
    );
  }
  const fx = state.data;
  const byCode = Object.fromEntries(fx.rates.map((r) => [r.code, r])) as Partial<Record<FxRate["code"], FxRate>>;
  return (
    <section aria-label="Tipo de cambio" className="space-y-2">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1.2fr]">
        {byCode.USD ? <RateCard rate={byCode.USD} unit="dólar" index={0} /> : null}
        {byCode.EUR ? <RateCard rate={byCode.EUR} unit="euro" index={1} /> : null}
        <Converter rates={fx.rates} uf={byCode.UF ?? null} />
      </div>
      {[byCode.USD, byCode.EUR].some((r) => r && r.as_of < todayInSantiago()) ? (
        <p data-testid="fx-weekend-note" className="text-[11px] text-ink-muted">
          El Banco Central no publica el dólar ni el euro los fines de semana ni feriados: rige el último valor publicado.
        </p>
      ) : null}
      <p className="text-[11px] text-ink-faint">
        Fuente: {fx.source_label}. Consultado a las {fmtTime(fx.fetched_at)}.
        {fx.stale ? <span className="ml-1 font-medium text-warn">No se pudo actualizar; se muestran las últimas cifras.</span> : null}
      </p>
    </section>
  );
}

function RateCard({ rate, unit, index }: { rate: FxRate; unit: string; index: number }) {
  return (
    <div
      data-testid={`fx-${rate.code}`}
      className="crm-rise group relative flex flex-col overflow-hidden rounded-xl border border-line bg-canvas-raised px-4 py-3.5 shadow-[0_1px_2px_rgb(28_25_23/0.04)] transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-px hover:border-line-strong hover:shadow-[0_6px_16px_-8px_rgb(13_148_136/0.25)]"
      style={{ "--i": index } as CSSProperties}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] font-medium text-ink-muted">{rate.label}</p>
        <span className="rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-semibold text-brand-700">{rate.code}</span>
      </div>
      <p className="mt-2 text-[28px] font-semibold leading-none tracking-tight text-ink tabular-nums">
        <CountUp value={rate.clp} format={fmtRate} />
      </p>
      <p className="mt-auto pt-2 text-[11px] text-ink-faint">
        pesos por {unit} · {publishedLabel(rate.as_of)}
      </p>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 h-0.5 origin-left scale-x-0 bg-brand-600 transition-transform duration-300 group-hover:scale-x-100"
      />
    </div>
  );
}

const CURRENCIES: FxRate["code"][] = ["USD", "EUR", "UF"];

function Converter({ rates, uf }: { rates: FxRate[]; uf: FxRate | null }) {
  const inputId = useId();
  const hintId = useId();
  const [text, setText] = useState("");
  const available = CURRENCIES.filter((c) => rates.some((r) => r.code === c));
  const [code, setCode] = useState<FxRate["code"]>(available[0] ?? "USD");
  const rate = rates.find((r) => r.code === code) ?? null;
  const amount = text.trim() ? parseAmount(text) : null;
  const invalid = text.trim() !== "" && amount === null;
  return (
    <div
      className="crm-rise rounded-xl border border-line bg-canvas-raised px-4 py-3.5 shadow-[0_1px_2px_rgb(28_25_23/0.04)] sm:col-span-2 lg:col-span-1"
      style={{ "--i": 2 } as CSSProperties}
    >
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={inputId} className="text-[12px] font-medium text-ink-muted">
          Monto
        </label>
        <div role="radiogroup" aria-label="Moneda" className="inline-flex rounded-md bg-canvas-sunken p-0.5">
          {available.map((c) => (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={c === code}
              onClick={() => setCode(c)}
              className={`h-6 rounded-[5px] px-2 text-[11px] font-semibold transition-colors ${
                c === code ? "bg-canvas-raised text-ink shadow-[0_1px_2px_rgb(24_24_27/0.08)]" : "text-ink-muted hover:text-ink"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>
      <input
        id={inputId}
        inputMode="decimal"
        autoComplete="off"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="1.250"
        aria-describedby={hintId}
        aria-invalid={invalid}
        className="mt-2 h-9 w-full rounded-md border border-line bg-canvas px-2.5 text-[15px] tabular-nums text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20"
      />
      <div className="mt-2 flex min-h-[2.25rem] items-baseline justify-between gap-3" aria-live="polite">
        {amount !== null && rate ? (
          <>
            <p data-testid="fx-result" className="text-[22px] font-semibold leading-none tracking-tight text-ink tabular-nums">
              {fmtClp(toClp(amount, rate.clp))}
            </p>
            <p className="text-right text-[11px] text-ink-faint">× {fmtRate(rate.clp)}</p>
          </>
        ) : (
          <p id={hintId} className={`text-[11px] ${invalid ? "text-bad" : "text-ink-faint"}`}>
            {invalid ? "Escribe un monto, por ejemplo 1.250 o 1.250,50." : "Escribe un monto para verlo en pesos."}
          </p>
        )}
      </div>
      {uf ? (
        <p className="mt-1 border-t border-line pt-2 text-[11px] text-ink-muted">
          UF <span className="font-semibold text-ink tabular-nums">{fmtRate(uf.clp)}</span> al {fmtDay(uf.as_of)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The figure rises to its value once, on arrival: a cue that it is today's. The text is set on
 * the element directly (no re-render per frame) and starts at the final value, so a reader, a
 * test or a reduced-motion setting always sees the real figure.
 */
function CountUp({ value, format }: { value: number; format: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof window.matchMedia !== "function" || typeof window.requestAnimationFrame !== "function") return undefined;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return undefined;
    const start = performance.now();
    const from = value * 0.94;
    let frame = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / 650);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = format(from + (value - from) * eased);
      if (p < 1) frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => {
      window.cancelAnimationFrame(frame);
      el.textContent = format(value);
    };
  }, [value, format]);
  return <span ref={ref}>{format(value)}</span>;
}

/* ──────────────────────────────────────────────────────────────── today ── */

function FollowUpsSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]" aria-hidden="true">
      <div className="space-y-3">
        {[0, 1].map((i) => (
          <div key={i} className="space-y-2 rounded-xl border border-line bg-canvas-raised p-4">
            <div className="crm-skeleton h-4 w-40 rounded" />
            {[0, 1, 2, 3].map((j) => (
              <div key={j} className="crm-skeleton h-8 rounded" />
            ))}
          </div>
        ))}
      </div>
      <div className="crm-skeleton h-32 rounded-lg" />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────── clock ── */

const CLOCK_DAY = new Intl.DateTimeFormat("es-CL", { timeZone: SANTIAGO, weekday: "long", day: "numeric", month: "long" });
const CLOCK_TIME = new Intl.DateTimeFormat("es-CL", { timeZone: SANTIAGO, hour: "2-digit", minute: "2-digit", hour12: false });

/** Today and the time in Santiago, so «hoy» on this page always means the same day for everyone. */
function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, []);
  // «Domingo, 4 de octubre»: only the first letter up, as Spanish writes it.
  const formatted = CLOCK_DAY.format(now);
  const day = formatted.charAt(0).toUpperCase() + formatted.slice(1);
  return (
    <p data-testid="resumen-clock" className="text-right text-xs text-ink-muted" title="Hora de Santiago">
      <span className="font-medium text-ink">{day}</span>
      <span className="ml-2 tabular-nums">{CLOCK_TIME.format(now)}</span>
    </p>
  );
}

/* ──────────────────────────────────────────────────────────────── format ── */

/** A `YYYY-MM-DD` day as "30 mar", read as that calendar day (no time-zone shift). */
function fmtDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return new Date(y, m - 1, d).toLocaleDateString("es-CL", { day: "numeric", month: "short" });
}

/** «publicado hoy», or the figure in force today and the day it was published («lun 30 mar»). */
function publishedLabel(asOf: string): string {
  if (asOf >= todayInSantiago()) return "publicado hoy";
  const d = new Date(`${asOf}T12:00:00Z`);
  return `vigente hoy · publicado el ${WEEKDAYS[(d.getUTCDay() + 6) % 7]} ${fmtDay(asOf)}`;
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", hour12: false });
}
