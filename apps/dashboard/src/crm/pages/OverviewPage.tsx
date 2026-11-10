import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { fetchFx, fetchPipeline } from "../crmApi";
import type { CrmSection, DatosTab } from "../crmRoute";
import { fmtClp, fmtRate, parseAmount, toClp, type FxRate, type FxResponse } from "../fx";
import { ResourceGate } from "../ui";
import { useResource, type ResourceState } from "../useResource";
import { QuoteNumberBox } from "../QuoteNumberBox";
import { PendientesPanel } from "../todos/PendientesPanel";
import { SANTIAGO } from "../marketing/calendar";
import { TodayBody, type QueueKindFilter } from "./TodayBody";
import { GoalStrip, type StripFilter } from "./GoalStrip";
import { buildParaTiQueue, filterQueue, useHomeFilter } from "../today";
import { useAuthSession } from "../../context/AuthSessionContext";
import { WebStatsCard } from "../web/WebStatsCard";

type Navigate = (s: CrmSection, id?: string | null, tab?: DatosTab) => void;

const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

/** «Martes 10 de noviembre» in Santiago, without the time. */
function todayLabel(): string {
  // Use "numeric" for month so formatToParts returns a number string ("3"), not a name ("marzo").
  const f = new Intl.DateTimeFormat("es-CL", {
    timeZone: SANTIAGO, weekday: "long", day: "numeric", month: "numeric",
  });
  const p = Object.fromEntries(f.formatToParts(new Date()).map((x) => [x.type, x.value]));
  const weekday = p.weekday.charAt(0).toUpperCase() + p.weekday.slice(1);
  return `${weekday} ${Number(p.day)} de ${MONTHS[Number(p.month) - 1]}`;
}

/**
 * «Hoy» v3: a quiet one-line header, the goal strip, the unified queue, and the side tools.
 * FX rates load lazily (after first paint) so they never block the queue.
 */
export function OverviewPage({ navigate }: { navigate: Navigate }) {
  const [pipeline, reloadPipeline, refreshing] = useResource(fetchPipeline);

  // Queue filter shared between GoalStrip and TodayBody — null = show all.
  const [queueFilter, setQueueFilter] = useState<QueueKindFilter>(null);

  // The strip can only set "followup" | "cotizar" | null — cast is safe.
  const handleStripFilter = useCallback((f: StripFilter) => setQueueFilter(f), []);

  // Dismissible «Mientras no estabas» note is handled inside TodayBody (SystemSummaryLine).

  return (
    <div className="space-y-5">
      {/* Quiet header: date + N cosas para hoy */}
      <HoyMiniHeader pipeline={pipeline} queueFilter={queueFilter} />

      {/* Goal strip: Meta · Seguimientos · Cotizar · Alcance */}
      {pipeline.kind === "ready" ? (
        <GoalStrip
          items={pipeline.data.items}
          calls={pipeline.data.calls ?? []}
          navigate={navigate}
          activeFilter={queueFilter === "followup" || queueFilter === "cotizar" ? queueFilter : null}
          onFilterChange={handleStripFilter}
        />
      ) : null}

      <ResourceGate state={pipeline} reload={reloadPipeline} skeleton={<FollowUpsSkeleton />}>
        {(p) => (
          <TodayBody
            items={p.items}
            tendersClosing={p.tenders_closing}
            navigate={navigate}
            onChanged={reloadPipeline}
            refreshing={refreshing}
            queueFilter={queueFilter}
            onQueueFilterChange={setQueueFilter}
            aside={
              <>
                <PendientesPanel cases={p.items} navigate={navigate} />
                <QuoteNumberBox pipeline={pipeline} compact />
                <LazyFxSection />
                <WebStatsCard />
              </>
            }
          />
        )}
      </ResourceGate>
    </div>
  );
}

/* ──────────────────────────────────────────────────── quiet header ── */

type PipelineState = ReturnType<typeof useResource<Awaited<ReturnType<typeof fetchPipeline>>>>[0];

/**
 * One quiet line: date · «N cosas para hoy» + a contextual hint when items are waiting.
 * Replaces the dark HeroHeader for Hoy (HeroHeader is kept for other pages that use it).
 */
function HoyMiniHeader({ pipeline, queueFilter }: { pipeline: PipelineState; queueFilter: QueueKindFilter }) {
  const { session } = useAuthSession();
  const me = session.kind === "signed_in" ? (session.profile?.displayName ?? session.operator.displayName ?? null) : null;
  const [dateLabel] = useState(todayLabel);

  const homeFilter = useHomeFilter();
  // Same queue as «Para hoy» below, so the header never disagrees with the list.
  const queue = useMemo(
    () => (pipeline.kind === "ready" ? filterQueue(buildParaTiQueue(pipeline.data.items, new Date(), me), homeFilter) : null),
    [pipeline, me, homeFilter],
  );
  const totalHoy = queue ? queue.length : null;

  const hintText = (() => {
    if (!queue) return null;
    const replies = queue.filter((q) => q.kind === "reply").length;
    if (replies >= 3) return `${replies} clientes esperan respuesta · empieza por ahí`;
    if (replies > 0) return replies === 1 ? "1 cliente espera respuesta" : `${replies} clientes esperan respuesta`;
    return null;
  })();

  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1" data-testid="hoy-mini-header">
      <div>
        <h1 className="text-[17px] font-semibold tracking-tight text-ink">
          {dateLabel}
          {totalHoy !== null ? (
            <> · <span className="font-bold">{totalHoy} {totalHoy === 1 ? "cosa" : "cosas"} para hoy</span></>
          ) : null}
        </h1>
        {hintText ? (
          <p className="mt-0.5 text-[13px] text-ink-muted">{hintText}</p>
        ) : null}
      </div>
      {me ? (
        <span className="text-[12px] text-ink-faint">
          {queueFilter ? `Filtrando por ${queueFilter}` : "Actualizado · el mismo Hoy para todo el equipo"}
        </span>
      ) : null}
    </header>
  );
}

/* ───────────────────────────────────────────────────── lazy FX ── */

/**
 * FX rates load after first paint so they never block the queue render.
 * Shows a skeleton until the fetch starts, then hands off to FxSection.
 */
function LazyFxSection() {
  const [started, setStarted] = useState(false);
  useEffect(() => {
    const id = window.requestAnimationFrame(() => setStarted(true));
    return () => window.cancelAnimationFrame(id);
  }, []);

  if (!started) return <FxSectionSkeleton />;
  return <FxSectionFetcher />;
}

function FxSectionFetcher() {
  const [fx, reloadFx] = useResource(fetchFx);
  return <FxSection state={fx} reload={reloadFx} />;
}

function FxSectionSkeleton() {
  return (
    <div className="space-y-3 rounded-xl border border-line bg-canvas-raised px-4 py-3.5" aria-hidden="true">
      <div className="flex items-center justify-between">
        <div className="crm-skeleton h-4 w-24 rounded" />
      </div>
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => <div key={i} className="crm-skeleton h-5 rounded" />)}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────── exchange rates ── */

function FxSection({ state, reload }: { state: ResourceState<FxResponse>; reload: () => void }) {
  if (state.kind === "loading") {
    return <FxSectionSkeleton />;
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
      {/* Compact 3-column rates + UF */}
      <div className="rounded-xl border border-line bg-canvas-raised px-4 py-3">
        <div className="grid grid-cols-3 gap-x-3 gap-y-2">
          {byCode.USD ? <CompactRate rate={byCode.USD} unit="dólar" /> : null}
          {byCode.EUR ? <CompactRate rate={byCode.EUR} unit="euro" /> : null}
          {byCode.UF  ? <CompactRate rate={byCode.UF}  unit="UF"   /> : null}
        </div>
        <div className="mt-3 border-t border-line pt-2">
          <Converter rates={fx.rates} uf={byCode.UF ?? null} />
        </div>
      </div>
      <p className="text-[11px] text-ink-faint">
        Fuente: {fx.source_label}. Consultado a las {fmtTime(fx.fetched_at)}.
        {fx.stale ? <span className="ml-1 font-medium text-warn">No se pudo actualizar.</span> : null}
      </p>
    </section>
  );
}

function CompactRate({ rate, unit }: { rate: FxRate; unit: string }) {
  return (
    <div data-testid={`fx-${rate.code}`}>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{unit}</p>
      <p className="text-[16px] font-semibold tabular-nums text-ink">
        <CountUp value={rate.clp} format={fmtRate} />
      </p>
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
    <div>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={inputId} className="text-[12px] font-medium text-ink-muted">Monto</label>
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
            <p data-testid="fx-result" className="text-[18px] font-semibold leading-none tracking-tight text-ink tabular-nums">
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
 * The figure rises to its value once, on arrival.
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

/* ──────────────────────────────────────────────────────────────── format ── */

function fmtDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return new Date(y, m - 1, d).toLocaleDateString("es-CL", { day: "numeric", month: "short" });
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit", hour12: false });
}
