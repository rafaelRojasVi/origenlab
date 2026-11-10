import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { fetchDriveArchive, fetchFx, fetchMailQuoteNumbers, fetchPipeline } from "../crmApi";
import type { CrmSection, DatosTab } from "../crmRoute";
import { fmtClp, fmtRate, parseAmount, toClp, type FxRate, type FxResponse } from "../fx";
import { ResourceGate } from "../ui";
import { useResource, type ResourceState } from "../useResource";
import { PendientesPanel } from "../todos/PendientesPanel";
import { SANTIAGO, todayInSantiago } from "../marketing/calendar";
import { TodayBody, type QueueKindFilter } from "./TodayBody";
import { GoalStrip, type StripFilter } from "./GoalStrip";
import { HOME_FILTER_KINDS, useHomeFilter } from "../today";
import { planNextSteps, queueKindOf, type StepPlan } from "../nextSteps";
import { useAuthSession } from "../../context/AuthSessionContext";
import { WebStatsCard } from "../web/WebStatsCard";
import { fetchQuoteNumbering } from "../quotes/quoteApi";
import { findUses, knownQuoteNumbers, lastAndNext, parseQuoteNumber } from "../quoteNumbers";

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
      <HoyMiniHeader pipeline={pipeline} />

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
                <GoalStrip
                  items={p.items}
                  calls={p.calls ?? []}
                  navigate={navigate}
                  activeFilter={queueFilter === "followup" || queueFilter === "cotizar" ? queueFilter : null}
                  onFilterChange={handleStripFilter}
                  layout="stack"
                />
                <PendientesPanel cases={p.items} navigate={navigate} />
                <HoyQuoteCard pipeline={pipeline} />
                <LazyHoyFxCard />
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
function HoyMiniHeader({ pipeline }: { pipeline: PipelineState }) {
  const { session } = useAuthSession();
  const me = session.kind === "signed_in" ? (session.profile?.displayName ?? session.operator.displayName ?? null) : null;
  const [dateLabel] = useState(todayLabel);

  const homeFilter = useHomeFilter();
  // The same plan «Para hoy» renders, so the header and the week never disagree with the list.
  const plan = useMemo<StepPlan | null>(
    () => (pipeline.kind === "ready"
      ? planNextSteps(pipeline.data.items, new Date(), me, 7, (st) => HOME_FILTER_KINDS[homeFilter].has(queueKindOf(st)))
      : null),
    [pipeline, me, homeFilter],
  );
  const totalHoy = plan ? plan.steps.filter((st) => st.item !== null).length : null;

  const hintText = (() => {
    if (!plan) return null;
    const replies = plan.steps.filter((st) => st.kind === "responder").length;
    if (replies >= 3) return `${replies} clientes esperan respuesta · empieza por ahí`;
    if (replies > 0) return replies === 1 ? "1 cliente espera respuesta" : `${replies} clientes esperan respuesta`;
    return plan.overdue > 0 ? `${plan.overdue} ${plan.overdue === 1 ? "atrasada" : "atrasadas"} · retómalas primero` : null;
  })();

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2" data-testid="hoy-mini-header">
      <div>
        <h1 className="text-[17px] font-semibold tracking-tight text-ink">
          {dateLabel}
          {totalHoy !== null ? (
            <> · <span className="font-bold">{totalHoy} {totalHoy === 1 ? "cosa" : "cosas"} para hoy</span></>
          ) : null}
        </h1>
        {hintText ? <p className="text-[13px] text-ink-muted">{hintText}</p> : null}
      </div>
      {plan ? <WeekStrip plan={plan} /> : null}
    </header>
  );
}

const DAY_SHORT = new Intl.DateTimeFormat("es-CL", { weekday: "short" });

/** The week at a glance: overdue, today and the next days, each with how many steps fall on it. */
function WeekStrip({ plan }: { plan: StepPlan }) {
  const cell = "flex h-12 w-[3.4rem] flex-col items-center justify-center rounded-lg border text-[10.5px] leading-tight";
  return (
    <ol className="ml-auto flex gap-1" aria-label="Semana" data-testid="hoy-week">
      <li className={`${cell} ${plan.overdue > 0 ? "border-bad/40 text-bad" : "border-line text-ink-faint"}`}>
        <span className="text-[15px] font-bold tabular-nums leading-none">{plan.overdue}</span>
        <span>atrasado</span>
      </li>
      {plan.days.filter((d) => d.day.getDay() !== 0 && d.day.getDay() !== 6).slice(0, 5).map((d, i) => (
        <li key={d.day.toISOString()} className={`${cell} ${i === 0 ? "border-ink bg-ink text-canvas" : "border-line bg-canvas-raised text-ink-muted"}`}>
          <span className="text-[15px] font-bold tabular-nums leading-none">{d.count}</span>
          <span>{i === 0 ? "hoy" : i === 1 ? "mañana" : DAY_SHORT.format(d.day).replace(".", "")}</span>
        </li>
      ))}
    </ol>
  );
}

/** Compact quote-number card: last · next + copy + expandable ¿ya existe? check. */
function HoyQuoteCard({ pipeline }: { pipeline: PipelineState }) {
  const [drive] = useResource(fetchDriveArchive);
  const [mail] = useResource(fetchMailQuoteNumbers);
  const [numbering] = useResource(fetchQuoteNumbering);
  const [checking, setChecking] = useState(false);
  const [typed, setTyped] = useState("");
  const [copied, setCopied] = useState(false);
  const checkId = useId();
  const year = Number(todayInSantiago().slice(2, 4));

  const known = useMemo(() => knownQuoteNumbers(
    pipeline.kind === "ready" ? pipeline.data.items : [],
    drive.kind === "ready" ? drive.data.folders : [],
    mail.kind === "ready" ? mail.data.items : [],
  ), [pipeline, drive, mail]);

  const loading = pipeline.kind === "loading" || drive.kind === "loading" || mail.kind === "loading" || numbering.kind === "loading";
  const mailMissing = mail.kind !== "ready" && mail.kind !== "loading";
  const { last, next: clientNext } = lastAndNext(known, year);
  const server = numbering.kind === "ready" ? numbering.data : null;
  const serverNext = server?.configured && server.next_number ? parseQuoteNumber(server.next_number, year) : null;
  const exhausted = server !== null && server.configured && !server.next_number;
  const highest = server?.configured && server.highest_known ? server.highest_known : 0;
  const serverLast = highest > (last?.correlative ?? 0) ? `CN${String(highest).padStart(5, "0")}` : null;
  const next = serverNext ? server!.next_number! : clientNext;

  const lastDisplay = serverLast ?? last?.number ?? (loading ? null : "—");
  const lastLabel = serverLast ? "Numeración del sistema"
    : last ? [last.label, last.sources.join(" y ")].filter(Boolean).join(" · ") : null;
  const nextDisplay = serverNext ? `CN${String(serverNext.correlative).padStart(5, "0")}`
    : exhausted ? "—" : (loading ? "…" : next);

  const copy = () => {
    void navigator.clipboard?.writeText(next).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const parsed = typed.trim() ? parseQuoteNumber(typed, year) : null;
  const uses = parsed ? findUses(known, parsed) : [];

  return (
    <section data-testid="quote-number-box" aria-label="Número de cotización"
      className="rounded-xl border border-brand-600/30 bg-canvas-raised px-4 py-3 shadow-[0_1px_2px_rgb(28_25_23/0.04)]">
      <p className="mb-2 text-[10.5px] font-semibold uppercase tracking-widest text-ink-faint">Cotización</p>
      <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
        <div data-testid="quote-last" className="min-w-0">
          <p className="text-[11px] text-ink-muted">Última</p>
          {lastDisplay !== null ? (
            <p className="text-[18px] font-semibold tabular-nums leading-tight text-ink">{lastDisplay}</p>
          ) : (
            <div className="crm-skeleton mt-0.5 h-5 w-24 rounded" />
          )}
          {lastLabel ? <p className="max-w-[14rem] truncate text-[11px] text-ink-faint">{lastLabel}</p> : null}
        </div>
        <div data-testid="quote-next" className="min-w-0">
          <p className="text-[11px] text-ink-muted">Siguiente</p>
          <div className="flex items-center gap-2">
            <p className="text-[18px] font-semibold tabular-nums leading-tight text-accent">{nextDisplay}</p>
            <button type="button" onClick={copy} disabled={exhausted || (loading && !serverNext)}
              className="h-6 rounded-md border border-line px-2 text-[11px] font-medium text-ink-muted transition-colors hover:border-line-strong hover:text-ink disabled:opacity-50">
              {copied ? "Copiado ✓" : "Copiar"}
            </button>
          </div>
        </div>
        <button type="button" onClick={() => setChecking((v) => !v)} aria-expanded={checking}
          className="ml-auto self-end h-6 rounded-md border border-line px-2 text-[11px] font-medium text-ink-muted hover:border-line-strong hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600">
          {checking ? "Cerrar ✕" : "¿Ya existe?"}
        </button>
      </div>
      {/* Data-source notes — always visible so the operator knows the box's reach. */}
      {drive.kind !== "ready" && drive.kind !== "loading" ? (
        <p className="mt-1.5 text-[10.5px] text-ink-faint">Sin el archivo de Drive: no se pudo leer.</p>
      ) : null}
      {mailMissing ? (
        <p className="mt-0.5 text-[10.5px] text-ink-faint">Los números de Gmail no se pudieron leer.</p>
      ) : null}
      {checking ? (
        <div className="mt-3 border-t border-line pt-3">
          <label htmlFor={checkId} className="text-[11px] font-medium text-ink-muted">¿Ya existe este número?</label>
          <input id={checkId} value={typed} onChange={(e) => setTyped(e.target.value)}
            placeholder="Ej. 1247 o 01247-26" autoComplete="off"
            className="mt-1 h-8 w-full rounded-md border border-line bg-canvas px-2 text-[13px] tabular-nums text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20" />
          <p data-testid="quote-check" aria-live="polite" className="mt-1 min-h-[1rem] text-[11px]">
            {!typed.trim() ? null : !parsed ? (
              <span className="text-ink-muted">Escribe un número, por ejemplo 1247 o 01247-26.</span>
            ) : uses.length ? (
              <span className="font-medium text-bad">Ya usado: {uses.map((u) => `${u.number} · ${[u.label, u.sources.join(" y ")].filter(Boolean).join(" · ")}`).join(" — ")}</span>
            ) : (
              <span className={mailMissing ? "text-ink-muted" : "text-good"}>
                {mailMissing
                  ? "No está en el CRM ni en Drive; los números de Gmail no se pudieron leer."
                  : "No está en el CRM, en Drive ni en Gmail."}
              </span>
            )}
          </p>
        </div>
      ) : null}
    </section>
  );
}

/** FX card — lazy so it never delays the queue or the quote card. */
function LazyHoyFxCard() {
  const [started, setStarted] = useState(false);
  useEffect(() => {
    const id = window.requestAnimationFrame(() => setStarted(true));
    return () => window.cancelAnimationFrame(id);
  }, []);
  if (!started) return <HoyFxCardSkeleton />;
  return <HoyFxCardFetcher />;
}

function HoyFxCardFetcher() {
  const [fx, reloadFx] = useResource(fetchFx);
  return <HoyFxCard state={fx} reload={reloadFx} />;
}

function HoyFxCardSkeleton() {
  return (
    <div className="rounded-xl border border-line bg-canvas-raised px-4 py-3" aria-hidden="true">
      <div className="crm-skeleton mb-2 h-3 w-20 rounded" />
      <div className="crm-skeleton h-5 w-full rounded" />
    </div>
  );
}

function HoyFxCard({ state, reload }: { state: ResourceState<FxResponse>; reload: () => void }) {
  const [expanded, setExpanded] = useState(false);
  if (state.kind === "loading") return <HoyFxCardSkeleton />;
  if (state.kind !== "ready") {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-line bg-canvas-raised px-4 py-3">
        <p className="text-[13px] text-ink-muted">Tipo de cambio no disponible.</p>
        <button type="button" onClick={reload}
          className="h-7 rounded-md border border-line bg-canvas px-3 text-xs font-medium text-ink transition-colors hover:border-line-strong">
          Reintentar
        </button>
      </div>
    );
  }
  const fx = state.data;
  const byCode = Object.fromEntries(fx.rates.map((r) => [r.code, r])) as Partial<Record<FxRate["code"], FxRate>>;

  return (
    <section aria-label="Tipo de cambio" className="rounded-xl border border-line bg-canvas-raised px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-widest text-ink-faint">Moneda</p>
          <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-[14px] font-semibold tabular-nums text-ink">
            {byCode.USD ? (
              <span data-testid="fx-USD"><span className="text-[12px] font-normal text-ink-muted">Dólar </span><CountUp value={byCode.USD.clp} format={fmtRate} /></span>
            ) : null}
            {byCode.EUR ? (
              <span data-testid="fx-EUR"><span className="text-[12px] font-normal text-ink-muted">Euro </span><CountUp value={byCode.EUR.clp} format={fmtRate} /></span>
            ) : null}
            {byCode.UF ? (
              <span data-testid="fx-UF"><span className="text-[12px] font-normal text-ink-muted">UF </span><CountUp value={byCode.UF.clp} format={fmtRate} /></span>
            ) : null}
          </p>
          <p className="mt-1 text-[11px] text-ink-faint">
            {fx.source_label}.
            {fx.stale ? <span className="ml-1 font-medium text-warn">No se pudo actualizar.</span> : null}
          </p>
        </div>
        <button type="button" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}
          aria-label="Convertir moneda"
          className="mt-5 shrink-0 h-7 rounded-md border border-line px-2.5 text-[11.5px] font-medium text-ink-muted hover:border-line-strong hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600">
          Convertir {expanded ? "▴" : "▾"}
        </button>
      </div>
      {expanded ? (
        <div className="mt-3 border-t border-line pt-3">
          <Converter rates={fx.rates} uf={byCode.UF ?? null} />
        </div>
      ) : null}
    </section>
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
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem] 2xl:grid-cols-[minmax(0,1fr)_24rem]" aria-hidden="true">
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

