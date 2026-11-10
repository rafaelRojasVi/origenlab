import { useAuthSession } from "../../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import type { CrmSection } from "../crmRoute";
import { crmHash } from "../crmRoute";
import type { CallDay, OpportunityCardData } from "../crmTypes";
import {
  MONTHLY_TARGET_CLP,
  PILOT_CONVERSATIONS_30D,
  dateSantiago,
  formatMillions,
  formatWait,
  lastIssuedOn,
  reach,
  requiredPace,
  soldThisMonth,
} from "../goals";
import { formatMoney } from "../money";
import { fetchFinanceSettings } from "../finanzas/financeApi";
import { fetchInvoices } from "../pagos/invoiceApi";
import { buildParaTiQueue } from "../today";
import { useResource } from "../useResource";

type Navigate = (section: CrmSection, id?: string | null) => void;

/** Which strip tile is currently filtering the queue (null = no filter / show all). */
export type StripFilter = "followup" | "cotizar" | null;

const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

/**
 * «Franja de metas» v3 — compact horizontal strip of 4 numbers.
 * Meta del mes · Seguimientos pendientes · Por cotizar · Alcance de la semana.
 *
 * Seguimientos and Por cotizar are buttons that filter the queue; counts are derived
 * from `buildParaTiQueue` so the strip tile number always equals the rows it filters.
 */
export function GoalStrip({
  items,
  calls,
  navigate,
  activeFilter,
  onFilterChange,
  layout = "strip",
}: {
  items: OpportunityCardData[];
  calls: CallDay[];
  navigate: Navigate;
  activeFilter: StripFilter;
  onFilterChange: (f: StripFilter) => void;
  /** `strip`: four tiles in a row (default). `stack`: one column, for Hoy's side column («Tu semana»). */
  layout?: "strip" | "stack";
}) {
  const { session } = useAuthSession();
  const seesMoney = session.kind === "signed_in" && ROLES_THAT_DECIDE_CASES.has(session.operator.role);
  const now = new Date();
  const today = dateSantiago(now);

  // Queue-derived counts (whole team — me=null).
  const fullQueue = buildParaTiQueue(items, now, null);
  const seguimientosCount = fullQueue.filter((q) => q.kind === "followup").length;
  const cotizarItems = fullQueue.filter((q) => q.kind === "cotizar");
  const cotizarCount = cotizarItems.length;
  const cotizarOldestMs = cotizarCount > 0 ? Math.max(...cotizarItems.map((q) => q.waitMs)) : null;

  const r = reach(calls, today);

  const tileBase =
    "flex flex-col gap-0.5 bg-canvas-raised px-4 py-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600";
  const tileMeta = `${tileBase} transition-colors hover:bg-canvas-sunken`;

  return (
    <section
      aria-label="Números del día"
      data-testid="goal-strip"
      className={`grid gap-px overflow-hidden rounded-xl border border-line bg-line ${layout === "stack" ? "grid-cols-1" : "grid-cols-2 lg:grid-cols-4"}`}
    >
      {/* Meta del mes */}
      {seesMoney ? (
        <MetaTile today={today} navigate={navigate} tileBase={tileMeta} />
      ) : (
        <div className={tileBase} data-testid="goal-vendido-hidden">
          <span className={LABEL}>Meta del mes</span>
          <span className="text-[20px] font-semibold leading-tight text-ink-muted">—</span>
        </div>
      )}

      {/* Seguimientos pendientes */}
      <button
        type="button"
        data-testid="goal-seguimiento"
        aria-pressed={activeFilter === "followup"}
        onClick={() => onFilterChange(activeFilter === "followup" ? null : "followup")}
        className={`${tileBase} text-left transition-colors hover:bg-canvas-sunken ${
          activeFilter === "followup" ? "shadow-[inset_0_-2px_0_theme(colors.brand.600)]" : ""
        }`}
      >
        <span className={LABEL}>Seguimientos pendientes</span>
        <span className={`text-[20px] font-semibold leading-tight tabular-nums ${seguimientosCount === 0 ? "text-good" : "text-warn"}`}>
          {seguimientosCount}
        </span>
        <span className="text-[11px] text-ink-faint">cotización enviada, sin respuesta</span>
      </button>

      {/* Por cotizar */}
      <button
        type="button"
        data-testid="goal-cotizar"
        aria-pressed={activeFilter === "cotizar"}
        onClick={() => onFilterChange(activeFilter === "cotizar" ? null : "cotizar")}
        className={`${tileBase} text-left transition-colors hover:bg-canvas-sunken ${
          activeFilter === "cotizar" ? "shadow-[inset_0_-2px_0_theme(colors.brand.600)]" : ""
        }`}
      >
        <span className={LABEL}>Por cotizar</span>
        <span className="flex flex-wrap items-baseline gap-x-1.5 text-[20px] font-semibold leading-tight tabular-nums text-ink">
          {cotizarCount}
          {cotizarOldestMs !== null && cotizarCount > 0 ? (
            <small className="text-[12px] font-medium text-ink-muted">
              · {formatWait(cotizarOldestMs)} la más antigua
            </small>
          ) : null}
        </span>
        <span className="text-[11px] text-ink-faint">cotizar mismo día gana 29 %</span>
      </button>

      {/* Alcance de la semana */}
      <a
        href={crmHash("prospectos")}
        data-testid="goal-alcance"
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
          e.preventDefault();
          navigate("prospectos");
        }}
        className={`${tileMeta} block`}
      >
        <span className={LABEL}>Alcance · esta semana</span>
        <span className={`text-[20px] font-semibold leading-tight tabular-nums ${r.week >= r.weekTarget ? "text-good" : "text-ink"}`}>
          {r.week}
          <small className="ml-1.5 text-[13px] font-medium text-ink-muted">/ {r.weekTarget} llamadas</small>
        </span>
        <span className="text-[11px] text-ink-faint">
          equipo · lunes a hoy · conv 30 d: {r.conversations30d}/{PILOT_CONVERSATIONS_30D}
        </span>
      </a>
    </section>
  );
}

const LABEL = "text-[11px] font-semibold uppercase tracking-wide text-ink-faint";

/* ──────────────────────────────────────────── Meta del mes tile ── */

function MetaTile({ today, navigate, tileBase }: { today: string; navigate: Navigate; tileBase: string }) {
  const [state, reload] = useResource(fetchInvoices);
  const [settingsState] = useResource(fetchFinanceSettings);
  const monthlyTarget =
    settingsState.kind === "ready" ? settingsState.data.monthly_target.net : MONTHLY_TARGET_CLP;
  const month = MONTHS[Number(today.slice(5, 7)) - 1] ?? "";

  if (state.kind === "loading") {
    return (
      <div className={tileBase} data-testid="goal-vendido" aria-busy="true">
        <span className={LABEL}>Meta de {month}</span>
        <div className="crm-skeleton mt-1 h-5 w-20 rounded" aria-hidden="true" />
      </div>
    );
  }

  if (state.kind !== "ready") {
    return (
      <div className={tileBase} data-testid="goal-vendido">
        <span className={LABEL}>Meta de {month}</span>
        <button type="button" onClick={reload} className="mt-1 text-left text-[11px] text-warn hover:underline">
          Sin datos · reintentar
        </button>
      </div>
    );
  }

  const sold = soldThisMonth(state.data.items ?? [], today);
  const pace = requiredPace(sold.net, monthlyTarget, today);
  const pct = Math.min(100, Math.floor((sold.net / monthlyTarget) * 100));
  const last = lastIssuedOn(state.data.items ?? []);
  const met = pace.remaining === 0;

  return (
    <a
      href={crmHash("pagos")}
      data-testid="goal-vendido"
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate("pagos");
      }}
      className={`${tileBase} block`}
    >
      <span className={LABEL}>Meta de {month}</span>
      <span className={`text-[20px] font-semibold leading-tight tabular-nums ${met ? "text-good" : "text-ink"}`}>
        {formatMillions(sold.net)}
        <small className="ml-1.5 text-[13px] font-medium text-ink-muted">de {formatMillions(monthlyTarget)}</small>
      </span>
      <span aria-hidden="true" className="mt-1 block h-1.5 overflow-hidden rounded-full bg-canvas-sunken">
        <span
          className={`block h-full rounded-full transition-[width] duration-700 ${met ? "bg-good" : "bg-brand-600"}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-[11px] text-ink-faint">
        {met
          ? "Meta cumplida"
          : pace.perDay !== null
            ? `faltan ${formatMillions(pace.remaining)} · ${formatMoney(String(pace.perDay), "CLP")}/día`
            : `faltan ${formatMillions(pace.remaining)}`}
        {last ? ` · último folio ${fmtDay(last)}` : ""}
      </span>
    </a>
  );
}

function fmtDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return new Date(y, m - 1, d).toLocaleDateString("es-CL", { day: "numeric", month: "short" });
}
