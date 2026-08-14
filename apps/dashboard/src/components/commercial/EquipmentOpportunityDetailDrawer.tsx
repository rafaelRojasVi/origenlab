import type { ReactNode } from "react";
import type { EquipmentOpportunityItem } from "../../api/commercialTypes";
import {
  formatDashboardDateTime,
  formatEquipmentCloseDate,
  formatEquipmentPublicationDate,
} from "../../lib/dashboardDateFormat";
import { TokenLabel } from "../operator/TokenLabel";
import { EquipmentTriageBadges } from "./EquipmentTriageBadges";
import { EquipmentWatchlistButton } from "./EquipmentWatchlistButton";

export function isSafeMercadoPublicoUrl(url: string): boolean {
  const trimmed = url.trim();
  if (!trimmed) return false;
  return !/ticket|api\.chilecompra/i.test(trimmed);
}

export function MercadoPublicoLink({
  url,
  className = "inline-flex items-center rounded-md border border-sky-200 bg-sky-50 px-3 py-1.5 text-sm font-medium text-sky-800 hover:bg-sky-100",
}: {
  url: string;
  className?: string;
}) {
  if (!isSafeMercadoPublicoUrl(url)) return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className={className}>
      Buscar en Mercado Público
    </a>
  );
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  if (children == null || children === "" || children === "—") return null;
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)] sm:w-36">
        {label}
      </dt>
      <dd className="min-w-0 text-sm text-slate-800">{children}</dd>
    </div>
  );
}

function DrawerSection({
  title,
  children,
  muted = false,
}: {
  title: string;
  children: ReactNode;
  muted?: boolean;
}) {
  return (
    <section
      className={
        muted
          ? "rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-3 space-y-2"
          : "space-y-2"
      }
    >
      <h3 className={`text-sm font-semibold ${muted ? "text-slate-600" : "text-slate-800"}`}>
        {title}
      </h3>
      <dl className="space-y-2">{children}</dl>
    </section>
  );
}

function AnexosSection({ item }: { item: EquipmentOpportunityItem }) {
  const anexos = item.anexos ?? [];
  const hasAnexos = anexos.length > 0;

  return (
    <DrawerSection title="Adjuntos / bases">
      {hasAnexos ? (
        <ul className="space-y-3">
          {anexos.map((anexo, index) => {
            const label = anexo.nombre?.trim() || `Adjunto ${index + 1}`;
            const safeUrl =
              anexo.url && isSafeMercadoPublicoUrl(anexo.url) ? anexo.url.trim() : "";
            return (
              <li
                key={`${label}-${index}`}
                className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800"
              >
                <p className="font-medium text-slate-900">{label}</p>
                {anexo.tipo ? (
                  <p className="text-xs text-[var(--color-muted)]">Tipo: {anexo.tipo}</p>
                ) : null}
                {anexo.descripcion ? <p className="mt-1">{anexo.descripcion}</p> : null}
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--color-muted)]">
                  {anexo.tamano ? <span>Tamaño: {anexo.tamano}</span> : null}
                  {anexo.fecha_adjunto ? <span>Fecha: {anexo.fecha_adjunto}</span> : null}
                </div>
                {safeUrl ? (
                  <a
                    href={safeUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-flex text-sm font-medium text-sky-800 hover:underline"
                  >
                    Abrir adjunto
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-slate-700">
          Adjuntos disponibles en Mercado Público; abrir la licitación para ver anexos.
        </p>
      )}
    </DrawerSection>
  );
}

function DrawerBody({ item }: { item: EquipmentOpportunityItem }) {
  const publication = item.fecha_publicacion
    ? formatEquipmentPublicationDate(item.fecha_publicacion)
    : "";
  const closeFormatted = formatEquipmentCloseDate(item.close_date, item.close_at);
  const apiChecked = item.api_checked_at_utc
    ? formatDashboardDateTime(item.api_checked_at_utc)
    : "";

  return (
    <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
      <DrawerSection title="Resumen">
        <DetailRow label="Categoría">
          <TokenLabel token={item.equipment_category} kind="equipment_category" />
        </DetailRow>
        <DetailRow label="Región">{item.region || "—"}</DetailRow>
        <DetailRow label="Estado ChileCompra">
          {item.chilecompra_status ? (
            <TokenLabel token={item.chilecompra_status} kind="equipment_contact_status" />
          ) : (
            "—"
          )}
        </DetailRow>
        <DetailRow label="Código estado">{item.chilecompra_status_code || "—"}</DetailRow>
        <DetailRow label="Validez">{item.validity_status || "—"}</DetailRow>
        <DetailRow label="Canal seguro">
          <TokenLabel token={item.safe_channel} kind="equipment_safe_channel" />
        </DetailRow>
        <DetailRow label="Acción recomendada">
          <TokenLabel token={item.next_action} kind="equipment_next_action" />
        </DetailRow>
        <DetailRow label="Requiere proveedor">{item.supplier_needed || "—"}</DetailRow>
        <DetailRow label="Fuente">{item.source || "—"}</DetailRow>
        {apiChecked ? (
          <DetailRow label="Revisado API en">{apiChecked}</DetailRow>
        ) : null}
      </DrawerSection>

      <DrawerSection title="Fechas">
        <DetailRow label="Fecha cierre">{closeFormatted}</DetailRow>
        {publication ? <DetailRow label="Fecha publicación">{publication}</DetailRow> : null}
        {item.close_date && item.close_date !== closeFormatted ? (
          <p className="text-xs text-[var(--color-muted)]">Raw: {item.close_date}</p>
        ) : null}
      </DrawerSection>

      <DrawerSection title="Ítem / evidencia">
        <DetailRow label="Descripción">{item.item_description || "—"}</DetailRow>
        <DetailRow label="Nota operador">{item.operator_note || "—"}</DetailRow>
        <DetailRow label="Proveedor">{item.supplier_needed || "—"}</DetailRow>
      </DrawerSection>

      <DrawerSection title="Metadata ChileCompra / UNSPSC">
        <DetailRow label="Cantidad">{item.cantidad || "—"}</DetailRow>
        <DetailRow label="Unidad">{item.unidad || "—"}</DetailRow>
        <DetailRow label="Producto">{item.producto || "—"}</DetailRow>
        <DetailRow label="UNSPSC">{item.unspsc_code || "—"}</DetailRow>
        <DetailRow label="Nivel 1">{item.nivel_1 || "—"}</DetailRow>
        <DetailRow label="Nivel 2">{item.nivel_2 || "—"}</DetailRow>
        <DetailRow label="Nivel 3">{item.nivel_3 || "—"}</DetailRow>
      </DrawerSection>

      <AnexosSection item={item} />

      <DrawerSection title="Procedencia (solo lectura)" muted>
        <DetailRow label="Fuente">{item.source || "—"}</DetailRow>
        <DetailRow label="Revisado API">{apiChecked || item.api_checked_at_utc || "—"}</DetailRow>
        <DetailRow label="Código estado">{item.chilecompra_status_code || "—"}</DetailRow>
        <DetailRow label="Validez">{item.validity_status || "—"}</DetailRow>
      </DrawerSection>
    </div>
  );
}

export function EquipmentOpportunityDetailDrawer({
  item,
  open,
  layout = "responsive",
  onClose,
  watchlistSaved,
  onToggleWatchlist,
}: {
  item: EquipmentOpportunityItem | null;
  open: boolean;
  layout?: "overlay" | "inline" | "responsive";
  onClose: () => void;
  watchlistSaved?: boolean;
  onToggleWatchlist?: () => void;
}) {
  if (!open || !item) return null;

  const panel = (
    <aside
      id="equipment-opportunity-detail-panel"
      data-testid="equipment-opportunity-detail-drawer"
      className={
        layout === "responsive"
          ? "mt-4 flex w-full flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm md:fixed md:inset-y-0 md:right-0 md:z-50 md:mt-0 md:h-full md:max-w-lg md:rounded-none md:border-l md:border-t-0 md:shadow-xl"
          : layout === "inline"
            ? "flex w-full flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm"
            : "relative z-10 flex h-full w-full max-w-lg flex-col border-l border-[var(--color-border)] bg-[var(--color-card)] shadow-xl"
      }
      role="dialog"
      aria-labelledby="equipment-opportunity-detail-heading"
      aria-modal={layout === "overlay"}
    >
      <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-4">
        <div className="min-w-0 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">
            Licitación · solo lectura
          </p>
          <h2 id="equipment-opportunity-detail-heading" className="text-lg font-semibold text-slate-900">
            {item.buyer?.trim() || "Sin comprador"}
          </h2>
          <p className="font-mono text-sm text-slate-700">{item.codigo_licitacion || "—"}</p>
          {item.title ? <p className="text-sm text-slate-800">{item.title}</p> : null}
          {onToggleWatchlist ? (
            <EquipmentWatchlistButton
              item={item}
              saved={Boolean(watchlistSaved)}
              onToggle={onToggleWatchlist}
            />
          ) : null}
          <EquipmentTriageBadges item={item} />
          <div className="flex flex-wrap gap-2">
            {item.chilecompra_status ? (
              <TokenLabel token={item.chilecompra_status} kind="equipment_contact_status" />
            ) : null}
            <TokenLabel token={item.contact_status} kind="equipment_contact_status" />
          </div>
          {item.mercado_publico_url ? (
            <MercadoPublicoLink url={item.mercado_publico_url} />
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar detalle de licitación"
          className="shrink-0 rounded-md border border-[var(--color-border)] px-2 py-1 text-sm text-slate-700 hover:bg-slate-50"
        >
          Cerrar
        </button>
      </header>
      <DrawerBody item={item} />
      <footer className="border-t border-[var(--color-border)] px-4 py-3 text-xs text-[var(--color-muted)]">
        Detalle de solo lectura · no envía correos · sin acciones en Gmail
      </footer>
    </aside>
  );

  if (layout === "inline") {
    return panel;
  }

  if (layout === "responsive") {
    return (
      <>
        <button
          type="button"
          className="fixed inset-0 z-40 hidden bg-slate-900/30 md:block"
          aria-label="Cerrar detalle de licitación"
          onClick={onClose}
        />
        {panel}
      </>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/30"
        aria-label="Cerrar detalle de licitación"
        onClick={onClose}
      />
      {panel}
    </div>
  );
}

export function equipmentOpportunityRowKey(row: EquipmentOpportunityItem): string {
  const code = row.codigo_licitacion?.trim() || row.buyer?.trim() || "row";
  return `eq-${row.priority_rank}-${code}`;
}
