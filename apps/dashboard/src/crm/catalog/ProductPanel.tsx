/**
 * The product panel of «Catálogo»: the product, its photo, its Drive documents, the supplier
 * observations (only when the API sent them — never to a viewer), what past quotes charged, the
 * day's rates and the supplier terms. Read-only.
 */
import { useCallback } from "react";
import {
  KIND_LABEL,
  PRICE_KIND_LABEL,
  fetchCatalogFx,
  fetchPriceHistory,
  fetchProduct,
  fmtDay,
  fmtMoney,
  fmtPct,
  hasPrice,
  type CostObservation,
  type ProductDetail,
} from "./catalogApi";
import { ProductDownloads } from "./ProductDownloads";
import { ProductImage } from "./ProductImage";
import { SalesSummary } from "./SalesSummary";
import { Badge, Drawer, EmptyState, ResourceGate, Section, Skeleton, fmtDate } from "../ui";
import { useResource, type ResourceState } from "../useResource";

export function NotEnabled() {
  return (
    <div role="status">
      <EmptyState title="Catálogo no habilitado">
        El catálogo todavía no está activo en este entorno. Cuando se active, aquí aparecerán los productos y sus
        precios.
      </EmptyState>
    </div>
  );
}


function isProductNotFound(state: ResourceState<unknown>): boolean {
  return state.kind === "unavailable" && state.message.includes("product_not_found");
}

export function ProductDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const load = useCallback(() => fetchProduct(id), [id]);
  const [state, reload] = useResource(load, [load]);
  const title = state.kind === "ready" ? state.data.model_number : "Producto";
  const subtitle =
    state.kind === "ready"
      ? [state.data.manufacturer.display_name, state.data.product_kind ? KIND_LABEL[state.data.product_kind] : null]
          .filter(Boolean)
          .join(" · ")
      : undefined;
  return (
    <Drawer open onClose={onClose} title={title} subtitle={subtitle}>
      {isProductNotFound(state) ? (
        <EmptyState title="Producto no encontrado">Puede que el enlace sea antiguo o que el producto ya no exista.</EmptyState>
      ) : state.kind === "unavailable" ? (
        <NotEnabled />
      ) : (
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
          {(product) => <ProductBody product={product} />}
        </ResourceGate>
      )}
    </Drawer>
  );
}

function ProductBody({ product: p }: { product: ProductDetail }) {
  const image = p.images[0] ?? null;
  const dims = [p.length_cm, p.width_cm, p.height_cm].every((v) => v != null)
    ? `${p.length_cm} × ${p.width_cm} × ${p.height_cm} cm`
    : null;
  const facts: [string, string][] = [
    ["Nombre", p.name_es || p.name || "—"],
    ["Categoría", p.category_es ?? "—"],
    ["Tipo", p.product_kind ? (KIND_LABEL[p.product_kind] ?? p.product_kind) : "—"],
    ["Fabricante", p.manufacturer.display_name],
    ["Origen", p.origin_country ?? "—"],
    ["Peso", p.weight_kg != null ? `${p.weight_kg} kg` : "—"],
    ["Medidas", dims ?? "—"],
    ["Mercancía peligrosa", p.is_dangerous_goods ? "Sí" : "No"],
  ];
  return (
    <>
      <figure>
        <ProductImage
          imageId={image?.id ?? null}
          kind={p.product_kind}
          alt={image?.caption_es ?? `Imagen de ${p.model_number}`}
          size="large"
        />
        {image?.caption_es ? <figcaption className="mt-1 text-[11px] text-ink-faint">{image.caption_es}</figcaption> : null}
      </figure>
      {p.documents ? <ProductDownloads documents={p.documents} /> : null}
      <Section
        title="Detalle"
        aside={
          p.content_confirmed_at ? (
            <Badge tone="good">Contenido confirmado</Badge>
          ) : (
            <Badge tone="neutral">Sin confirmar</Badge>
          )
        }
      >
        <dl className="grid grid-cols-[8.5rem_1fr] gap-x-3 gap-y-1 text-xs">
          {facts.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink-faint">{k}</dt>
              <dd className="text-ink">{v}</dd>
            </div>
          ))}
        </dl>
        {p.description_es ? <p className="mt-2 whitespace-pre-line text-xs leading-5 text-ink-muted">{p.description_es}</p> : null}
      </Section>
      {p.specs && p.specs.length > 0 ? (
        <Section title="Especificaciones">
          <ul className="space-y-0.5 text-xs">
            {p.specs.map((s, i) => (
              <li key={`${s.label_es}-${i}`}>
                <span className="text-ink-faint">{s.label_es}:</span>{" "}
                <span className="text-ink">
                  {s.value}
                  {s.unit ? ` ${s.unit}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
      {p.cost_history ? <CostHistory rows={p.cost_history} /> : null}
      {p.price_history ? (
        <Section title="Ventas y cotizaciones"><SalesSummary history={p.price_history} /></Section>
      ) : (
        <QuotedHistory modelKey={p.model_key} />
      )}
      <FxBlock />
      {p.supplier_terms.length > 0 ? (
        <Section title="Condiciones del proveedor">
          <ul className="space-y-1 text-xs text-ink-muted">
            {p.supplier_terms.map((t) => (
              <li key={t.supplier.id}>
                <span className="font-medium text-ink">{t.supplier.display_name}</span> · {t.currency} ·{" "}
                {ROUTE_LABEL[t.route] ?? t.route}
                {t.incoterm ? ` · ${t.incoterm}` : ""}
                {t.default_lead_time_es ? ` · ${t.default_lead_time_es}` : ""}
                {t.default_discount_pct != null ? ` · descuento ${fmtPct(t.default_discount_pct)}` : ""}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </>
  );
}

const ROUTE_LABEL: Record<string, string> = {
  import_courier: "importación courier",
  import_freight: "importación carga",
  domestic: "nacional",
};

/** The supplier observations. Only rendered when the API sent them (never to a viewer). */
function CostHistory({ rows }: { rows: CostObservation[] }) {
  return (
    <Section title="Precios observados">
      {rows.length === 0 ? (
        <p className="text-xs text-ink-faint">Sin precios de proveedor registrados.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line text-xs" data-testid="cost-history">
          {rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-2.5 py-1.5">
              <span className="font-medium tabular-nums text-ink">{hasPrice(r) ? fmtMoney(r.price, r.currency) : "—"}</span>
              <span className="text-ink-muted">{PRICE_KIND_LABEL[r.price_kind] ?? r.price_kind}</span>
              {r.list_price != null ? (
                <span className="text-ink-faint">lista {fmtMoney(r.list_price, r.currency)}</span>
              ) : null}
              {r.discount_pct != null ? <span className="text-ink-faint">dto. {fmtPct(r.discount_pct)}</span> : null}
              {r.incoterm ? <span className="text-ink-faint">{r.incoterm}</span> : null}
              {r.is_stale ? <Badge tone="warn">Antiguo</Badge> : null}
              <span className="ml-auto text-ink-faint">
                {r.supplier.display_name} · {fmtDate(r.as_of)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/** «Ventas y cotizaciones» by its own request — only for an older API whose product does not carry it. */
function QuotedHistory({ modelKey }: { modelKey: string }) {
  const load = useCallback(() => fetchPriceHistory(modelKey), [modelKey]);
  const [state, reload] = useResource(load, [load]);
  return (
    <Section title="Ventas y cotizaciones">
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} />}>
        {(h) => <SalesSummary history={h} />}
      </ResourceGate>
    </Section>
  );
}

const fxUsd = () => fetchCatalogFx("USD");
const fxEur = () => fetchCatalogFx("EUR");

/** The day's USD and EUR rate in pesos, with the date each is for. A failed rate says so; nothing else breaks. */
function FxBlock() {
  const [usd] = useResource(fxUsd);
  const [eur] = useResource(fxEur);
  return (
    <Section title="Tipo de cambio">
      <ul className="space-y-0.5 text-xs" data-testid="catalog-fx">
        <FxLine code="USD" state={usd} />
        <FxLine code="EUR" state={eur} />
      </ul>
    </Section>
  );
}

function FxLine({ code, state }: { code: string; state: ResourceState<{ clp_per_unit: string; as_of: string }> }) {
  if (state.kind === "loading") return <li className="text-ink-faint">1 {code} = …</li>;
  if (state.kind !== "ready") return <li className="text-ink-faint">1 {code}: no disponible</li>;
  return (
    <li className="text-ink-muted">
      1 {code} = <span className="font-semibold tabular-nums text-ink">{fmtMoney(state.data.clp_per_unit, "CLP")}</span>{" "}
      <span className="text-ink-faint">· al {fmtDay(state.data.as_of)}</span>
    </li>
  );
}

