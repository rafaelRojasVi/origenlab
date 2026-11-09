import type { ReactNode } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { ROLES_THAT_DECIDE_CASES } from "../caseCommands";
import type { CrmSection } from "../crmRoute";
import { crmHash } from "../crmRoute";
import type { CallDay, OpportunityCardData } from "../crmTypes";
import {
  MONTHLY_TARGET_CLP,
  PILOT_CALLS_PER_DAY,
  PILOT_CONVERSATIONS_30D,
  cotizarBacklog,
  dateSantiago,
  formatMillions,
  formatWait,
  lastIssuedOn,
  needsFollowUp,
  quoteSpeed,
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

const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

/**
 * «Franja de metas»: the four goals first on «Hoy» — Vendido vs meta, Seguimiento, Cotizar and
 * Alcance — each a link into the view that acts on it. The figures are the whole team's, whatever
 * the profile's queue. A viewer gets no money tile (and «Vendido» never asks for the invoices).
 */
export function GoalStrip({ items, calls, navigate }: { items: OpportunityCardData[]; calls: CallDay[]; navigate: Navigate }) {
  const { session } = useAuthSession();
  const seesMoney = session.kind === "signed_in" && ROLES_THAT_DECIDE_CASES.has(session.operator.role);
  const now = new Date();
  const today = dateSantiago(now);
  const followUps = items.filter((c) => needsFollowUp(c, now)).length;
  // Hoy's whole queue (no profile filter), so the tile agrees with the Cotizar section.
  const backlog = cotizarBacklog(buildParaTiQueue(items, now, null));
  const speed = quoteSpeed(items, now);
  const r = reach(calls, today);

  return (
    <section aria-label="Metas" data-testid="goal-strip" className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {seesMoney ? <SoldTile today={today} navigate={navigate} /> : null}
      <Tile
        href={`${crmHash("oportunidades")}?vista=seguimiento`}
        testId="goal-seguimiento"
        label="Seguimiento"
        met={followUps === 0}
        value={String(followUps)}
        mean="casos enviados sin toque hace más de 7 días"
        foot="Meta: 0"
        action="Ver en el pipeline"
      />
      <Tile
        href={`${crmHash("oportunidades")}?vista=cotizar`}
        testId="goal-cotizar"
        label="Cotizar"
        met={backlog.count === 0}
        value={String(backlog.count)}
        mean={
          <>
            solicitudes sin cotizar
            {backlog.oldestMs !== null ? <> · la más antigua: {formatWait(backlog.oldestMs)}</> : null}
          </>
        }
        detail={`Solicitud → cotización: ${speed.current.n && speed.current.medianMs !== null ? formatWait(speed.current.medianMs) : "—"} (30 días) · antes ${
          speed.previous.n && speed.previous.medianMs !== null ? formatWait(speed.previous.medianMs) : "—"}`}
        detailTone={false}
        foot="Meta: mismo día hábil"
        action="Ver en el pipeline"
      />
      <Tile
        href={crmHash("prospectos")}
        onClick={() => navigate("prospectos")}
        testId="goal-alcance"
        label="Alcance · esta semana"
        met={r.week >= r.weekTarget}
        value={<>{r.week} <span className="text-[15px] font-medium text-ink-muted">/ {r.weekTarget} llamadas</span></>}
        mean={`Hoy ${r.today} / ${PILOT_CALLS_PER_DAY} · Conversaciones 30 días: ${r.conversations30d} / ${PILOT_CONVERSATIONS_30D}`}
        foot="del equipo, lunes a hoy"
        action="Listas y llamadas"
      />
    </section>
  );
}

/** «Vendido en {mes}»: its own read of the SII register, so only a role that sees money asks for it. */
function SoldTile({ today, navigate }: { today: string; navigate: Navigate }) {
  const [state, reload] = useResource(fetchInvoices);
  // Fetch the monthly target from finance settings; fall back to the hardcoded $6 M default.
  const [settingsState] = useResource(fetchFinanceSettings);
  const monthlyTarget =
    settingsState.kind === "ready" ? settingsState.data.monthly_target.net : MONTHLY_TARGET_CLP;
  const month = MONTHS[Number(today.slice(5, 7)) - 1] ?? "";
  const label = `Vendido en ${month}`;
  if (state.kind === "loading") {
    return (
      <div className={`${TILE} justify-between`} data-testid="goal-vendido" aria-busy="true">
        <p className={LABEL}>{label}</p>
        <div className="crm-skeleton mt-2 h-7 w-24 rounded" aria-hidden="true" />
        <p className="mt-2 text-[12px] text-ink-faint">Leyendo lo facturado…</p>
      </div>
    );
  }
  if (state.kind !== "ready") {
    return (
      <div className={TILE} data-testid="goal-vendido" role="status">
        <p className={LABEL}>{label}</p>
        <p className="mt-2 text-[13px] text-ink-muted">No se pudo leer lo facturado</p>
        <button
          type="button"
          onClick={reload}
          className="mt-3 inline-flex min-h-11 items-center self-start rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          Reintentar
        </button>
      </div>
    );
  }
  const items = state.data.items ?? [];
  const sold = soldThisMonth(items, today);
  const pace = requiredPace(sold.net, monthlyTarget, today);
  const pct = Math.max(0, Math.floor((sold.net / monthlyTarget) * 100));
  const last = lastIssuedOn(items);
  const met = pace.remaining === 0;
  const paceLine = met
    ? "Meta cumplida"
    : pace.perDay === null
      ? "Sin días hábiles este mes"
      : `Faltan ${formatMillions(pace.remaining)} · ${formatMoney(String(pace.perDay), "CLP")} por día hábil (${pace.days} quedan, sin feriados)`;
  return (
    <Tile
      href={crmHash("pagos")}
      onClick={() => navigate("pagos")}
      testId="goal-vendido"
      label={label}
      met={met}
      value={formatMillions(sold.net)}
      mean={`${pct} % de la meta ${formatMillions(monthlyTarget)}`}
      bar={Math.min(100, pct)}
      detail={paceLine}
      foot={`neto, facturado en el SII${last ? ` · último folio del ${fmtDay(last)}` : ""}`}
      action="Ver facturas"
    />
  );
}

const TILE =
  "crm-rise flex min-h-11 flex-col rounded-xl border border-line bg-canvas-raised px-4 py-3.5 shadow-(--tile-shadow)";
const LABEL = "text-[12px] font-medium text-ink-muted";

function Tile({ href, onClick, testId, label, met, value, mean, bar, detail, detailTone = true, foot, action }: {
  href: string;
  /** Navigate in-app (a plain click); the `href` still serves a new tab. */
  onClick?: () => void;
  testId: string;
  label: string;
  met: boolean;
  value: ReactNode;
  mean: ReactNode;
  /** 0–100: how far toward the goal. */
  bar?: number;
  detail?: string;
  /** Colour `detail` good/warn by `met`. Off for a line that is compared with no goal. */
  detailTone?: boolean;
  foot: string;
  action: string;
}) {
  return (
    <a
      href={href}
      data-testid={testId}
      data-tone={met ? "good" : "warn"}
      onClick={(e) => {
        if (!onClick || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        onClick();
      }}
      className={`${TILE} group transition-[border-color,box-shadow] hover:border-line-strong hover:shadow-(--tile-shadow-hover) focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 focus-visible:ring-offset-1`}
    >
      <span className="flex items-center justify-between gap-2">
        <span className={LABEL}>{label}</span>
        <span
          aria-hidden="true"
          className={`h-2 w-2 shrink-0 rounded-full ${met ? "bg-good" : "bg-warn"}`}
        />
      </span>
      <span className={`mt-2 text-[26px] font-semibold leading-none tracking-tight tabular-nums ${met ? "text-good" : "text-ink"}`}>
        {value}
      </span>
      <span className="mt-1.5 text-[12px] leading-4 text-ink-muted">{mean}</span>
      {bar !== undefined ? (
        <span aria-hidden="true" className="mt-2.5 block h-1.5 overflow-hidden rounded-full bg-canvas-sunken">
          <span className={`block h-full rounded-full ${met ? "bg-good" : "bg-warn"}`} style={{ width: `${bar}%` }} />
        </span>
      ) : null}
      {detail ? (
        <span
          data-testid={`${testId}-detail`}
          className={`mt-2 text-[12px] leading-4 ${!detailTone ? "text-ink-muted" : met ? "text-good" : "text-warn"}`}
        >
          {detail}
        </span>
      ) : null}
      <span className="mt-auto flex items-end justify-between gap-2 pt-2.5 text-[11px] leading-4">
        <span className="text-ink-faint">{foot}</span>
        <span className="shrink-0 font-medium text-accent group-hover:underline">{action} →</span>
      </span>
    </a>
  );
}

/** A `YYYY-MM-DD` day as «6 nov», read as that calendar day. */
function fmtDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return new Date(y, m - 1, d).toLocaleDateString("es-CL", { day: "numeric", month: "short" });
}
