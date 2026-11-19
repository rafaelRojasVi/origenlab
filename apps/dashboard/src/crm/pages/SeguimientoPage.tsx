/**
 * «Ventas › Seguimiento» (owner 2026-11-18): every open case with a sent quote, as a sheet. Who
 * follows it, how to reach them, how long it has waited, and «Llamé» to record a call. Our emails
 * count by themselves. On top, today's KPIs; above the sheet, one filter per encargado (kept in the
 * hash, `?encargado=`), «Solo atrasados» and a search. `?caso=<id>` (Hoy's «Ver en Seguimiento»)
 * selects that row and scrolls to it.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchProfiles, type ProfileCard } from "../../api/authClient";
import { useAuthSession } from "../../context/AuthSessionContext";
import { applyCaseReceipt, onCaseReceipt, type CaseCommandReceipt } from "../caseCommands";
import { contactLine, displayName } from "../caseDisplay";
import { fetchPipeline } from "../crmApi";
import type { PipelineResponse } from "../crmTypes";
import { formatMoney } from "../money";
import { seguimientoKpis, seguimientoRows, type SegRow } from "../seguimiento";
import { EmptyState, PageHeader, ResourceGate, SearchInput, Skeleton, initials, toast } from "../ui";
import { useResource } from "../useResource";
import { CallScript } from "./CallScript";
import { SeguimientoSheet } from "./SeguimientoSheet";

/** «Todos», «Mis casos», «Sin asignar», or a profile (by its key, else its id). */
type Filter = "todos" | "mios" | "sin" | string;

const HASH_PATH = "#/ventas/seguimiento";

function hashParams(): URLSearchParams {
  const hash = window.location.hash;
  const at = hash.indexOf("?");
  return new URLSearchParams(at < 0 ? "" : hash.slice(at + 1));
}

/** The page's own query (`?encargado=`, `?caso=`), read on every hash change. */
function useHashParams(): URLSearchParams {
  const [params, setParams] = useState(hashParams);
  useEffect(() => {
    const sync = () => setParams(hashParams());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  return params;
}

/** Keep the filter in the address without a new history entry (and without a hashchange). */
function writeFilter(filter: Filter) {
  const params = hashParams();
  if (filter === "todos") params.delete("encargado");
  else params.set("encargado", filter);
  const query = params.toString();
  history.replaceState(null, "", `${HASH_PATH}${query ? `?${query}` : ""}`);
}

const filterKey = (p: ProfileCard) => p.key || p.id;

/** The person a row shows: the encargado, else the suggestion the picker shows selected. */
const shownOwner = (r: SegRow) => r.ownerId ?? r.suggestedOwnerId;

function matchesFilter(r: SegRow, filter: Filter, me: string | null, profiles: ProfileCard[]): boolean {
  if (filter === "todos") return true;
  // «Sin asignar»: nobody confirmed yet (a suggestion is only a suggestion).
  if (filter === "sin") return r.ownerId === null;
  if (filter === "mios") return !!me && shownOwner(r) === me;
  const profile = profiles.find((p) => filterKey(p) === filter);
  return !!profile && shownOwner(r) === profile.id;
}

function matchesSearch(r: SegRow, q: string): boolean {
  const needle = q.trim().toLocaleLowerCase("es");
  if (!needle) return true;
  const hay = [displayName(r.card).name, r.card.organization?.name, contactLine(r.card), ...r.card.quote_numbers, r.card.latest_revision?.quote_number]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("es");
  return hay.includes(needle);
}

export function SeguimientoPage({ initialOpportunityId = null, now: fixedNow }: { initialOpportunityId?: string | null; now?: Date }) {
  const [state, reload, refreshing, patch] = useResource(fetchPipeline);
  const [profileState] = useResource<ProfileCard[]>(async () => {
    const r = await fetchProfiles();
    return r.kind === "ok" ? r.profiles : [];
  });
  // A recorded command (encargado, canal) shows at once, with the version the next command needs.
  useEffect(
    () => onCaseReceipt((receipt) => patch((data) => ({ ...data, items: applyCaseReceipt(data.items, receipt) }))),
    [patch],
  );
  const profiles = profileState.kind === "ready" ? profileState.data : [];
  return (
    <div className="space-y-4">
      <ResourceGate state={state} reload={reload} skeleton={<PageSkeleton />}>
        {(data) => (
          <Seguimiento
            data={data}
            profiles={profiles}
            fixedNow={fixedNow}
            initialId={initialOpportunityId}
            reload={reload}
            refreshing={refreshing}
            onReceipt={(receipt) => patch((d) => ({ ...d, items: applyCaseReceipt(d.items, receipt) }))}
          />
        )}
      </ResourceGate>
    </div>
  );
}

function PageSkeleton() {
  return (
    <>
      <PageHeader title="Seguimiento" subtitle="Cotizaciones enviadas que esperan respuesta." />
      <Skeleton rows={8} />
    </>
  );
}

function Seguimiento({
  data,
  profiles,
  fixedNow,
  initialId,
  reload,
  refreshing,
  onReceipt,
}: {
  data: PipelineResponse;
  profiles: ProfileCard[];
  fixedNow?: Date;
  initialId: string | null;
  reload: () => void;
  refreshing: boolean;
  onReceipt: (receipt: CaseCommandReceipt) => void;
}) {
  const { session } = useAuthSession();
  const signedIn = session.kind === "signed_in" ? session : null;
  const me = signedIn ? signedIn.profile?.id ?? signedIn.operator.operatorId : null;
  const myName = signedIn ? signedIn.profile?.displayName || signedIn.operator.displayName : "OrigenLab";
  const params = useHashParams();
  const caso = params.get("caso")?.toLowerCase() ?? initialId;

  // «Now» moves with each read, so day counts and «hoy» are as of the data on screen.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => fixedNow ?? new Date(), [fixedNow, data]);
  const rows = useMemo(() => seguimientoRows(data.items, profiles, now), [data.items, profiles, now]);
  const kpis = useMemo(() => seguimientoKpis(rows, data, now), [rows, data, now]);

  const [filter, setFilterState] = useState<Filter>(() => params.get("encargado") ?? "todos");
  const [onlyLate, setOnlyLate] = useState(false);
  const [q, setQ] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(caso);
  const [guionOpen, setGuionOpen] = useState(false);
  useEffect(() => {
    if (caso) setSelectedId(caso);
  }, [caso]);

  const setFilter = useCallback((f: Filter) => {
    setFilterState(f);
    writeFilter(f);
  }, []);

  const visible = rows.filter((r) => matchesFilter(r, filter, me, profiles) && (!onlyLate || r.late) && matchesSearch(r, q));
  const count = (f: Filter) => rows.filter((r) => matchesFilter(r, f, me, profiles)).length;
  const filterOptions: { value: Filter; label: string }[] = [
    { value: "todos", label: "Todos" },
    ...(me ? [{ value: "mios", label: "Mis casos" }] : []),
    ...profiles.map((p) => ({ value: filterKey(p), label: p.displayName })),
    { value: "sin", label: "Sin asignar" },
  ];
  const guionRow = rows.find((r) => r.card.opportunity_id === selectedId) ?? visible[0] ?? null;

  function onCallSaved(id: string, said: string) {
    setSelectedId(id);
    toast(`Llamada guardada · próximo: ${said}`);
    reload();
  }

  return (
    <>
      <PageHeader
        title="Seguimiento"
        subtitle="Cotizaciones enviadas que esperan respuesta. Llama, escribe y marca: los correos que mandamos se marcan solos."
        actions={
          <button
            type="button"
            aria-pressed={guionOpen}
            disabled={!guionRow}
            onClick={() => setGuionOpen((v) => !v)}
            className={`inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:opacity-50 ${guionOpen ? "border-brand-100 bg-brand-50 text-accent" : "border-line-strong bg-canvas-raised text-ink hover:bg-canvas-sunken"}`}
          >
            Guion de llamada
          </button>
        }
      />
      <KpiStrip kpis={kpis} profiles={profiles} rowsCount={rows.length} />
      {rows.length === 0 ? (
        <EmptyState title="No hay cotizaciones enviadas esperando respuesta." />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div role="group" aria-label="Encargado" className="inline-flex flex-wrap gap-0.5 rounded-lg border border-line bg-canvas-sunken p-[3px]">
              {filterOptions.map((o) => {
                const on = o.value === filter;
                return (
                  <button
                    key={o.value}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setFilter(o.value)}
                    className={`inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${on ? "bg-canvas-raised text-ink shadow-[0_0_0_1px_var(--color-line)]" : "text-ink-muted hover:text-ink"}`}
                  >
                    {o.label}
                    <span className="text-[11px] font-bold tabular-nums text-ink-faint">{count(o.value)}</span>
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              aria-pressed={onlyLate}
              onClick={() => setOnlyLate((v) => !v)}
              className={`inline-flex h-9 items-center rounded-md border px-3 text-[13px] font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${onlyLate ? "border-brand-100 bg-brand-50 text-accent" : "border-line-strong bg-canvas-raised text-ink hover:bg-canvas-sunken"}`}
            >
              Solo atrasados
            </button>
            <div className="ml-auto w-full sm:w-auto">
              <SearchInput value={q} onChange={setQ} placeholder="Buscar cliente, contacto o N°…" label="Buscar" />
            </div>
          </div>
          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_auto]">
            {visible.length === 0 ? (
              <EmptyState title="Ningún caso con este filtro." />
            ) : (
              <SeguimientoSheet
                rows={visible}
                profiles={profiles}
                now={now}
                selectedId={selectedId}
                scrollToId={caso}
                onSelect={setSelectedId}
                onGuion={(id) => {
                  setSelectedId(id);
                  setGuionOpen(true);
                }}
                onCallSaved={onCallSaved}
                onReceipt={onReceipt}
                onConflict={reload}
                callsPaused={refreshing}
              />
            )}
            {guionOpen && guionRow ? <CallScript row={guionRow} me={myName} now={now} onClose={() => setGuionOpen(false)} /> : null}
          </div>
        </>
      )}
    </>
  );
}

type Kpis = ReturnType<typeof seguimientoKpis>;

/** «Llamadas hoy» per person (every profile, zero included) plus anyone else who called today. */
function callsPerPerson(kpis: Kpis, profiles: ProfileCard[]): { id: string; name: string; today: number }[] {
  const byId = new Map(kpis.callsByOperator.map((o) => [o.operatorId, o]));
  const people = profiles.map((p) => ({ id: p.id, name: p.displayName, today: byId.get(p.id)?.today ?? 0 }));
  const others = kpis.callsByOperator.filter((o) => !profiles.some((p) => p.id === o.operatorId));
  return [...people, ...others.map((o) => ({ id: o.operatorId, name: o.name, today: o.today }))];
}

function KpiStrip({ kpis, profiles, rowsCount }: { kpis: Kpis; profiles: ProfileCard[]; rowsCount: number }) {
  const pct = kpis.goal > 0 ? Math.min(100, Math.round((kpis.callsToday / kpis.goal) * 100)) : 0;
  const tile = "flex min-w-0 flex-col gap-1 rounded-xl border border-line bg-canvas-raised px-4 py-3.5";
  const label = "text-[12px] font-semibold text-ink-muted";
  const value = "text-[26px] font-extrabold leading-tight tracking-tight tabular-nums text-ink";
  const sub = "text-[12px] text-ink-faint";
  return (
    <section aria-label="Indicadores de hoy" className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-[1.5fr_repeat(4,minmax(0,1fr))]">
      <div className={`${tile} col-span-2 min-[900px]:col-span-1`} data-testid="kpi-calls">
        <span className={label}>Llamadas hoy</span>
        <span className={value}>
          {kpis.callsToday} <span className="text-[14px] font-semibold text-ink-faint">de {kpis.goal}</span>
        </span>
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1">
          {callsPerPerson(kpis, profiles).map((p) => (
            <span key={p.id} className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted" data-testid={`kpi-calls-${p.id}`}>
              <span aria-hidden="true" className="grid h-[22px] w-[22px] place-items-center rounded-full bg-canvas-sunken text-[10px] font-bold text-ink-muted">
                {initials(p.name)}
              </span>
              {p.name} <b className="text-[15px] text-ink">{p.today}</b>
            </span>
          ))}
        </div>
        {/* The bar alone, no track: how far today's calls are toward the goal. */}
        <div aria-hidden="true" className="mt-1.5 h-1 rounded-sm bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <div className={tile} data-testid="kpi-touched">
        <span className={label}>Casos tocados hoy</span>
        <span className={value}>{kpis.touchedToday}</span>
        <span className={sub}>llamada o correo</span>
      </div>
      <div className={tile} data-testid="kpi-late">
        <span className={label}>Sin toque 3+ días</span>
        <span className={`${value} ${kpis.late > 0 ? "!text-warn" : ""}`}>{kpis.late}</span>
        <span className={sub}>ritmo de 3 días</span>
      </div>
      <div className={tile} data-testid="kpi-emails">
        <span className={label}>Correos enviados hoy</span>
        <span className={value}>{kpis.emailsToday}</span>
        <span className={sub}>se cuentan solos</span>
      </div>
      <div className={tile} data-testid="kpi-money">
        <span className={label}>En seguimiento</span>
        <span className={`${value} truncate`}>{formatMoney(String(kpis.moneyClp), "CLP")}</span>
        <span className={sub}>{rowsCount} {rowsCount === 1 ? "cotización" : "cotizaciones"}</span>
      </div>
    </section>
  );
}
