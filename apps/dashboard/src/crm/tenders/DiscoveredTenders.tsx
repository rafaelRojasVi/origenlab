import { useCallback, useState } from "react";
import { crmHash, type CrmSection } from "../crmRoute";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, ExternalLink, ResourceGate, SearchInput, Skeleton, fmtDateTime } from "../ui";
import { useResource } from "../useResource";
import { fetchDiscoveredTenders, type DiscoveredNotice } from "./tenderApi";
import { closingLabel, closingWhen } from "./tenderTime";

/**
 * «En Mercado Público»: every open Mercado Público tender that fits what OrigenLab sells, as the
 * worker's sweep keeps them (every 15 minutes). The catalog's families first («Balanzas y humedad»,
 * «Centrífugas», …), then other laboratory equipment; closing soonest first within each. Chips filter
 * by family, the search box by code, title or institution. A row shows the institution, when it
 * closes and how long is left (Santiago time), the estimate, the families and the items that matched;
 * «Ver en Mercado Público» opens the notice, «Preparar» (sales and admin) opens it as a tender case
 * through «Nueva licitación», and a notice already opened offers «Abrir» on its case.
 *
 * At phone width a row stacks its cells; nothing scrolls sideways.
 */
type Navigate = (section: CrmSection, id?: string | null) => void;

const OTHER = "laboratorio";
const NEW_MS = 24 * 3_600_000;

function matches(n: DiscoveredNotice, query: string): boolean {
  if (!query) return true;
  const hay = `${n.code} ${n.title} ${n.buyer ?? ""} ${n.region ?? ""}`.toLocaleLowerCase("es");
  return query
    .toLocaleLowerCase("es")
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => hay.includes(word));
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

  return (
    <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
      {(data) => {
        const familyCount = (key: string) =>
          key === OTHER ? data.counts.laboratorio : data.notices.filter((n) => n.families.some((f) => f.key === key)).length;
        const shown = data.notices.filter(
          (n) =>
            (family === null ||
              (family === OTHER ? n.tier === "laboratorio" : n.families.some((f) => f.key === family))) &&
            matches(n, query.trim()),
        );
        const chips = [
          ...data.families.filter((f) => familyCount(f.key) > 0),
          { key: OTHER, label: "Otros equipos de laboratorio" },
        ];
        return (
          <div className="space-y-3">
            <p className="text-[13px] text-ink-muted">
              Licitaciones abiertas en Mercado Público que calzan con lo que vendes: {data.counts.catalogo} de tu
              catálogo y {data.counts.laboratorio} de otros equipos de laboratorio.{" "}
              {data.last_checked_at ? (
                <span>Revisado {fmtDateTime(data.last_checked_at)}; se revisa cada 15 minutos.</span>
              ) : null}
            </p>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div role="group" aria-label="Filtrar por familia" className="flex flex-wrap gap-1.5">
                <FamilyChip label="Todas" count={data.notices.length} pressed={family === null} onClick={() => setFamily(null)} />
                {chips.map((f) => (
                  <FamilyChip
                    key={f.key}
                    label={f.label}
                    count={familyCount(f.key)}
                    pressed={family === f.key}
                    onClick={() => setFamily(f.key)}
                  />
                ))}
              </div>
              <SearchInput value={query} onChange={setQuery} placeholder="Buscar código, título o institución" label="Buscar licitación" />
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
                <ul className="divide-y divide-line">
                  {shown.map((n) => (
                    <NoticeItem key={n.id} notice={n} now={now} navigate={navigate} mayOpen={mayOpen} onPrepare={onPrepare} />
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

function itemLine(item: DiscoveredNotice["items"][number]): string {
  const name = item.name ?? item.description ?? "Ítem";
  const detail = item.description && item.description !== item.name ? ` — ${item.description}` : "";
  const qty = item.quantity !== null && item.quantity !== "" ? `${item.quantity}${item.unit ? ` ${item.unit.toLowerCase()}` : ""} × ` : "";
  return `${qty}${name}${detail}`;
}

function NoticeItem({
  notice: n,
  now,
  navigate,
  mayOpen,
  onPrepare,
}: {
  notice: DiscoveredNotice;
  now: Date;
  navigate: Navigate;
  mayOpen: boolean;
  onPrepare: (code: string) => void;
}) {
  const amount = n.estimated_amount ? formatMoney(String(n.estimated_amount.amount), n.estimated_amount.currency) : null;
  const isNew = n.first_seen_at !== null && now.getTime() - new Date(n.first_seen_at).getTime() < NEW_MS;
  const listed = Math.min(n.items.length, 3);
  return (
    <li data-testid={`notice-row-${n.code}`} className="flex flex-col gap-3 px-4 py-3 md:flex-row md:items-start md:justify-between">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted">
          <span className="tabular-nums">{n.code}</span>
          {isNew ? <Badge tone="info" glyph={false}>nueva</Badge> : null}
          {n.families.map((f) => (
            <Badge key={f.key} tone="brand" glyph={false}>{f.label}</Badge>
          ))}
          {n.tier === "laboratorio" ? <Badge tone="neutral" glyph={false}>equipo de laboratorio</Badge> : null}
        </div>
        <p className="break-words text-[13px] font-medium text-ink">{n.title}</p>
        <p className="break-words text-[12px] text-ink-muted">
          {n.buyer ?? (n.detail ? "—" : "Institución por leer")}
          {n.region ? ` · ${n.region.trim()}` : ""}
        </p>
        {n.items.length > 0 ? (
          <ul className="space-y-0.5 text-[12px] text-ink">
            {n.items.slice(0, 3).map((item, i) => (
              <li key={i} className="break-words">{itemLine(item)}</li>
            ))}
            {n.item_count > listed ? (
              <li className="text-ink-muted">
                y {n.item_count - listed} {n.item_count - listed === 1 ? "ítem más" : "ítems más"}
              </li>
            ) : null}
          </ul>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-col gap-2 md:w-56 md:items-end md:text-right">
        <div className="text-[13px] text-ink">
          <span className="block">{closingWhen(n.closes_at)}</span>
          <span className="block text-[11px] text-ink-muted">{closingLabel(n.closes_at, now)}</span>
          {amount ? <span className="block text-[12px] tabular-nums">{amount} <span className="text-[11px] text-ink-muted">estimado</span></span> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2 md:justify-end">
          <ExternalLink href={n.portal_url} label={`Ver ${n.code} en Mercado Público`}>
            Ver en Mercado Público
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
      </div>
    </li>
  );
}
