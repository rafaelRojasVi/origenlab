import { useCallback, useState, type CSSProperties } from "react";
import { crmHash, noticeRouteId, type CrmSection } from "../crmRoute";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, ExternalLink, ResourceGate, SearchInput, Skeleton, fmtDateTime } from "../ui";
import { useResource } from "../useResource";
import { fetchDiscoveredTenders, type DiscoveredList, type DiscoveredNotice } from "./tenderApi";
import { closingLabel, closingWhen } from "./tenderTime";

/**
 * «En Mercado Público»: every open Mercado Público tender that fits what OrigenLab sells, as the
 * worker's sweep keeps them (every 15 minutes) — a table read at a glance, the same shape as «Mis
 * licitaciones». A stat line says how many are the catalog's, close this week, are new today and what
 * they add up to; chips filter by family, the box by code, title or institution, the select sorts.
 *
 * A row: code, «nueva», the families, the title (it opens the notice's page, `DiscoveredTenderPage`),
 * the items that matched and what of the catalog they named («calza con»); the institution; when it
 * closes and how long is left (Santiago time; a pulsing dot when it closes within two days); the
 * estimate; and the actions — «Mercado Público ↗» always in sight, «Preparar» (sales and admin) or
 * «Abrir» when a case already exists.
 *
 * Rows rise in one after another (`crm-rise`); it stops under reduced motion. At phone width a row
 * stacks its cells; nothing scrolls sideways.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;
type Sort = "cierre" | "nuevas" | "monto";

const OTHER = "laboratorio";
const NEW_MS = 24 * 3_600_000;
const WEEK_MS = 7 * 24 * 3_600_000;
const URGENT_MS = 2 * 24 * 3_600_000;
const GRID = "md:grid md:grid-cols-[minmax(0,2.6fr)_minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,.8fr)_10.5rem] md:items-start md:gap-4";

function matches(n: DiscoveredNotice, query: string): boolean {
  if (!query) return true;
  const hay = `${n.code} ${n.title} ${n.buyer ?? ""} ${n.region ?? ""} ${(n.terms ?? []).join(" ")}`.toLocaleLowerCase("es");
  return query
    .toLocaleLowerCase("es")
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => hay.includes(word));
}

const time = (iso: string | null) => (iso ? new Date(iso).getTime() : Number.NaN);
const isNew = (n: DiscoveredNotice, now: Date) => n.first_seen_at !== null && now.getTime() - time(n.first_seen_at) < NEW_MS;

function sorted(rows: DiscoveredNotice[], sort: Sort): DiscoveredNotice[] {
  if (sort === "cierre") return rows; // the API's order: the catalog first, then by closing
  const copy = [...rows];
  if (sort === "nuevas") copy.sort((a, b) => (time(b.first_seen_at) || 0) - (time(a.first_seen_at) || 0));
  if (sort === "monto") copy.sort((a, b) => (b.estimated_amount?.amount ?? -1) - (a.estimated_amount?.amount ?? -1));
  return copy;
}

function summary(data: DiscoveredList, now: Date) {
  const left = (n: DiscoveredNotice) => time(n.closes_at) - now.getTime();
  const clp = data.notices.filter((n) => n.estimated_amount && n.estimated_amount.currency === "CLP");
  return {
    week: data.notices.filter((n) => left(n) > 0 && left(n) <= WEEK_MS).length,
    fresh: data.notices.filter((n) => isNew(n, now)).length,
    amount: clp.reduce((sum, n) => sum + (n.estimated_amount?.amount ?? 0), 0),
  };
}

export function DiscoveredTenders({
  navigate,
  now,
  mayOpen,
  onPrepare,
}: {
  navigate: Navigate;
  now: Date;
  mayOpen: boolean;
  onPrepare: (code: string) => void;
}) {
  const load = useCallback(() => fetchDiscoveredTenders(), []);
  const [state, reload] = useResource(load, []);
  const [family, setFamily] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("cierre");

  return (
    <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
      {(data) => {
        const familyCount = (key: string) =>
          key === OTHER ? data.counts.laboratorio : data.notices.filter((n) => n.families.some((f) => f.key === key)).length;
        const shown = sorted(
          data.notices.filter(
            (n) =>
              (family === null || (family === OTHER ? n.tier === "laboratorio" : n.families.some((f) => f.key === family))) &&
              matches(n, query.trim()),
          ),
          sort,
        );
        const chips = [
          ...data.families.filter((f) => familyCount(f.key) > 0),
          { key: OTHER, label: "Otros equipos de laboratorio" },
        ];
        const s = summary(data, now);
        return (
          <div className="space-y-3">
            <dl className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
              <Stat label="Tu catálogo" value={data.counts.catalogo} />
              <Divider />
              <Stat label="Otros de laboratorio" value={data.counts.laboratorio} />
              <Divider />
              <Stat label="Cierran esta semana" value={s.week} tone={s.week > 0 ? "bad" : undefined} />
              <Divider />
              <Stat label="Nuevas hoy" value={s.fresh} />
              {s.amount > 0 ? (
                <>
                  <Divider />
                  <Stat label="Monto estimado abierto" value={formatMoney(String(s.amount), "CLP") ?? "—"} />
                </>
              ) : null}
              {data.last_checked_at ? (
                <span className="flex items-center gap-1.5 text-[11px] text-ink-faint sm:ml-auto">
                  <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-good" />
                  Revisado {fmtDateTime(data.last_checked_at)} · se revisa cada 15 minutos
                </span>
              ) : null}
            </dl>
            <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
              <div role="group" aria-label="Filtrar por familia" className="flex flex-wrap gap-1.5">
                <FamilyChip label="Todas" count={data.notices.length} pressed={family === null} onClick={() => setFamily(null)} />
                {chips.map((f) => (
                  <FamilyChip key={f.key} label={f.label} count={familyCount(f.key)} pressed={family === f.key} onClick={() => setFamily(f.key)} />
                ))}
              </div>
              <div className="flex gap-2">
                <SearchInput value={query} onChange={setQuery} placeholder="Buscar código, equipo o institución" label="Buscar licitación" />
                <label className="sr-only" htmlFor="discovered-sort">
                  Ordenar
                </label>
                <select
                  id="discovered-sort"
                  value={sort}
                  onChange={(e) => setSort(e.target.value as Sort)}
                  className="h-7 rounded-md border border-line bg-canvas-raised px-1.5 text-xs text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
                >
                  <option value="cierre">Cierra antes</option>
                  <option value="nuevas">Más nuevas</option>
                  <option value="monto">Mayor monto</option>
                </select>
              </div>
            </div>
            {data.notices.length === 0 ? (
              <EmptyState
                title={
                  data.last_checked_at
                    ? "No hay licitaciones abiertas que calcen con tu catálogo en este momento."
                    : "La búsqueda en Mercado Público aún no ha corrido. La primera revisión llega en unos minutos."
                }
              />
            ) : shown.length === 0 ? (
              <EmptyState title="Ninguna licitación calza con ese filtro." />
            ) : (
              <section aria-label="Licitaciones en Mercado Público" className="overflow-hidden rounded-xl border border-line bg-canvas-raised">
                <div
                  aria-hidden="true"
                  className={`hidden border-b border-line bg-canvas-sunken px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-ink-faint ${GRID}`}
                >
                  <span>Licitación</span>
                  <span>Institución</span>
                  <span>Cierre</span>
                  <span>Estimado</span>
                  <span className="text-right">Acciones</span>
                </div>
                <ul className="divide-y divide-line">
                  {shown.map((n, i) => (
                    <NoticeItem
                      key={n.id}
                      notice={n}
                      index={Math.min(i, 12)}
                      now={now}
                      navigate={navigate}
                      mayOpen={mayOpen}
                      onPrepare={onPrepare}
                    />
                  ))}
                </ul>
              </section>
            )}
          </div>
        );
      }}
    </ResourceGate>
  );
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: "bad" }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt>{label}</dt>
      <dd className={`font-semibold tabular-nums ${tone === "bad" ? "text-bad" : "text-ink"}`}>{value}</dd>
    </div>
  );
}

function Divider() {
  return <span aria-hidden="true" className="h-3 w-px bg-line-strong" />;
}

function FamilyChip({ label, count, pressed, onClick }: { label: string; count: number; pressed: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`inline-flex min-h-[32px] items-center gap-1.5 rounded-full border px-3 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 ${
        pressed
          ? "border-brand-700 bg-brand-700 text-white"
          : "border-line-strong bg-canvas-raised text-ink hover:border-brand-600 hover:text-brand-700"
      }`}
    >
      <span>{label}</span> <span className="tabular-nums opacity-80">{count}</span>
    </button>
  );
}

export function itemLine(item: DiscoveredNotice["items"][number]): string {
  const name = item.name ?? item.description ?? "Ítem";
  const detail = item.description && item.description !== item.name ? ` — ${item.description}` : "";
  const qty = item.quantity !== null && item.quantity !== "" ? `${item.quantity}${item.unit ? ` ${item.unit.toLowerCase()}` : ""} × ` : "";
  return `${qty}${name}${detail}`;
}

function NoticeItem({
  notice: n,
  index,
  now,
  navigate,
  mayOpen,
  onPrepare,
}: {
  notice: DiscoveredNotice;
  index: number;
  now: Date;
  navigate: Navigate;
  mayOpen: boolean;
  onPrepare: (code: string) => void;
}) {
  const amount = n.estimated_amount ? formatMoney(String(n.estimated_amount.amount), n.estimated_amount.currency) : null;
  const leftMs = time(n.closes_at) - now.getTime();
  const urgent = leftMs > 0 && leftMs <= URGENT_MS;
  const listed = Math.min(n.items.length, 2);
  const more = n.item_count - listed;
  const terms = n.terms ?? [];
  const page = noticeRouteId(n.id);
  return (
    <li
      data-testid={`notice-row-${n.code}`}
      className={`crm-rise flex flex-col gap-2 px-4 py-3 transition-colors hover:bg-canvas-sunken/40 ${GRID}`}
      style={{ "--i": index } as CSSProperties}
    >
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted">
          <span className="tabular-nums">{n.code}</span>
          {isNew(n, now) ? (
            <Badge tone="info" glyph={false}>
              nueva
            </Badge>
          ) : null}
          {n.families.map((f) => (
            <Badge key={f.key} tone="brand" glyph={false}>
              {f.label}
            </Badge>
          ))}
          {n.tier === "laboratorio" ? (
            <Badge tone="neutral" glyph={false}>
              equipo de laboratorio
            </Badge>
          ) : null}
        </div>
        <a
          href={crmHash("licitaciones", page)}
          onClick={(e) => {
            e.preventDefault();
            navigate("licitaciones", page);
          }}
          className="block break-words text-[13px] font-medium text-ink hover:text-brand-700 focus:outline-none focus-visible:underline"
        >
          {n.title}
        </a>
        {listed > 0 ? (
          <p className="break-words text-xs text-ink-muted">
            {n.items.slice(0, listed).map(itemLine).join(" · ")}
            {more > 0 ? ` · y ${more} ${more === 1 ? "ítem más" : "ítems más"}` : ""}
          </p>
        ) : null}
        {terms.length > 0 ? (
          <p className="text-[11px] text-ink-faint">
            Calza con: <span className="font-medium text-brand-700">{terms.join(" · ")}</span>
          </p>
        ) : null}
      </div>
      <div className="min-w-0 text-[13px] text-ink">
        <span className="block break-words">{n.buyer ?? (n.detail ? "—" : "Institución por leer")}</span>
        {n.region ? <span className="block text-[11px] text-ink-muted">{n.region.trim()}</span> : null}
      </div>
      <div className="text-[13px]">
        <span className="flex items-center gap-1.5">
          {urgent ? (
            <span aria-hidden="true" className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-bad opacity-60 motion-reduce:hidden" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-bad" />
            </span>
          ) : null}
          <span className={urgent ? "font-semibold text-bad" : leftMs <= WEEK_MS ? "font-medium text-warn" : "text-ink"}>
            {closingLabel(n.closes_at, now)}
          </span>
        </span>
        <span className="block text-[11px] text-ink-muted">{closingWhen(n.closes_at)}</span>
      </div>
      <div className="text-[13px] tabular-nums text-ink">{amount ?? "—"}</div>
      <div className="flex flex-wrap items-center gap-2 md:justify-end">
        <ExternalLink href={n.portal_url} label={`Ver ${n.code} en Mercado Público`}>
          Mercado Público
        </ExternalLink>
        {n.opportunity_id ? (
          <a
            href={crmHash("licitaciones", n.opportunity_id)}
            onClick={(e) => {
              e.preventDefault();
              navigate("licitaciones", n.opportunity_id);
            }}
            className="text-xs font-medium text-brand-700 underline underline-offset-2"
          >
            Abrir
          </a>
        ) : mayOpen ? (
          <Button variant="primary" onClick={() => onPrepare(n.code)}>
            Preparar
          </Button>
        ) : null}
      </div>
    </li>
  );
}
