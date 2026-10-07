/**
 * «Catálogo»: the products loaded into the catalog (catalog 1a), read-only. A searchable, paged
 * list filtered by supplier and kind; a drawer per product (`#/crm/catalogo/<uuid>`) with its
 * details, the supplier observations, what past quotes charged («Historial de cotizaciones») and
 * the day's USD/EUR rate; and, for sales and admin, the costing parameters.
 *
 * Every read is `/v2/catalog/*`. The API mounts it only behind `ORIGENLAB_V2_QUOTING_ENABLED`, so
 * a 404 means «not enabled here» and the page says so calmly. Costs are removed by the API for the
 * viewer role: a column or block appears only when its figures came back, never on the role alone.
 * There is no write here.
 */
import { useCallback, useEffect, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import {
  KIND_LABEL,
  KIND_TABS,
  PRICE_KIND_LABEL,
  fetchCatalogFx,
  fetchCostParameters,
  fetchImageUrl,
  fetchPriceHistory,
  fetchProduct,
  fetchProducts,
  fetchSuppliers,
  fmtDay,
  fmtMoney,
  fmtPct,
  hasPrice,
  type CostObservation,
  type SupplierSummary,
  type ProductDetail,
  type ProductListItem,
  type ProductPage,
} from "../catalog/catalogApi";
import type { CrmSection } from "../crmRoute";
import {
  Badge,
  Button,
  Drawer,
  EmptyState,
  PageHeader,
  ResourceGate,
  SearchInput,
  Section,
  Segmented,
  SelectInput,
  Skeleton,
  StatLine,
  fmtDate,
  fmtInt,
} from "../ui";
import { useResource, type ResourceState } from "../useResource";

const PAGE_SIZE = 50;

type Tab = "productos" | "parametros";
type Navigate = (s: CrmSection, id?: string | null) => void;

export function CatalogPage({ id, navigate }: { id: string | null; navigate: Navigate }) {
  const { session } = useAuthSession();
  const seesParameters = session.kind === "signed_in" && session.operator.role !== "viewer";
  const [tab, setTab] = useState<Tab>("productos");

  return (
    <div className="space-y-3">
      <PageHeader
        title="Catálogo"
        subtitle="Productos, precios observados de proveedores y lo que se cotizó antes. Sólo lectura."
      />
      {seesParameters ? (
        <Segmented
          label="Vista del catálogo"
          value={tab}
          onChange={setTab}
          options={[
            { value: "productos", label: "Productos" },
            { value: "parametros", label: "Parámetros de costeo" },
          ]}
        />
      ) : null}
      {tab === "parametros" && seesParameters ? <ParametersView /> : <ProductsView openId={id} navigate={navigate} />}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────── products ── */

function ProductsView({ openId, navigate }: { openId: string | null; navigate: Navigate }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [supplier, setSupplier] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  // A new search or filter starts from the first page.
  useEffect(() => {
    const next = q.trim();
    if (next === debounced) return;
    const t = setTimeout(() => {
      setDebounced(next);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [q, debounced]);
  const filterBy = (set: (v: string) => void) => (v: string) => {
    set(v);
    setOffset(0);
  };

  const load = useCallback(
    () => fetchProducts({ q: debounced, supplier, kind, limit: PAGE_SIZE, offset }),
    [debounced, supplier, kind, offset],
  );
  const [state, reload] = useResource(load, [load]);
  const [suppliersState] = useResource(fetchSuppliers, []);
  const suppliers = suppliersState.kind === "ready" ? suppliersState.data.items : [];
  const kindCounts = countsByKind(suppliers, supplier);

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <SearchInput value={q} onChange={setQ} label="Buscar en el catálogo" placeholder="Modelo o nombre…" />
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          Proveedor
          <span className="w-48">
            <SelectInput
              value={supplier}
              onChange={filterBy(setSupplier)}
              placeholder="Todos"
              options={suppliers.map((s) => ({ value: s.id, label: `${s.display_name} (${fmtInt(s.products)})` }))}
            />
          </span>
        </label>
<Segmented
          label="Tipo de producto"
          value={kind}
          onChange={filterBy(setKind)}
          options={[
            { value: "", label: "Todos", count: kindCounts.all },
            ...KIND_TABS.map((t) => ({ value: t.value, label: t.label, count: kindCounts[t.value] ?? 0 })),
          ]}
        />
      </div>
      {state.kind === "unavailable" ? (
        <NotEnabled />
      ) : (
        <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={8} />}>
          {(page) => <ProductTable page={page} onOpen={(pid) => navigate("catalogo", pid)} onPage={setOffset} />}
        </ResourceGate>
      )}
      {openId ? <ProductDrawer id={openId} onClose={() => navigate("catalogo")} /> : null}
    </>
  );
}

/**
 * Products per kind for the tabs: of the chosen supplier, or of every supplier when none is. A
 * product two suppliers list counts under each, so «Todos» across suppliers is an upper bound.
 */
function countsByKind(suppliers: SupplierSummary[], supplierId: string): Record<string, number> {
  const counts: Record<string, number> = { all: 0 };
  for (const s of suppliers) {
    if (supplierId && s.id !== supplierId) continue;
    counts.all += s.products;
    for (const [k, n] of Object.entries(s.by_kind)) counts[k] = (counts[k] ?? 0) + n;
  }
  return counts;
}

function NotEnabled() {
  return (
    <div role="status">
      <EmptyState title="Catálogo no habilitado">
        El catálogo todavía no está activo en este entorno. Cuando se active, aquí aparecerán los productos y sus
        precios.
      </EmptyState>
    </div>
  );
}

function ProductTable({
  page,
  onOpen,
  onPage,
}: {
  page: ProductPage;
  onOpen: (id: string) => void;
  onPage: (offset: number) => void;
}) {
  // Only when the API sent figures: a viewer's answer carries none, and the column is not shown.
  const showPrice = page.items.some((i) => hasPrice(i.current_cost));
  const first = page.total === 0 ? 0 : page.offset + 1;
  const last = page.offset + page.items.length;
  return (
    <>
      <StatLine
        items={[
          { label: "Productos", value: fmtInt(page.total) },
          ...(page.total > 0 ? [{ label: "Mostrando", value: `${fmtInt(first)}–${fmtInt(last)}` }] : []),
        ]}
      />
      {page.items.length === 0 ? (
        <EmptyState title="Ningún producto coincide">Prueba con otro modelo, nombre o filtro.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line bg-canvas-raised">
          <table className="w-full min-w-[720px] border-collapse text-xs">
            <thead>
              <tr className="border-b border-line text-left text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
                <th scope="col" className="px-3 py-2">Modelo</th>
                <th scope="col" className="px-3 py-2">Nombre</th>
                <th scope="col" className="px-3 py-2">Fabricante · proveedor</th>
                {showPrice ? (
                  <th scope="col" className="px-3 py-2 text-right">Último precio</th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {page.items.map((p) => (
                <ProductRow key={p.id} product={p} showPrice={showPrice} onOpen={onOpen} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {page.total > page.limit ? (
        <div className="flex items-center justify-end gap-2">
          <Button disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))}>
            Anterior
          </Button>
          <Button disabled={last >= page.total} onClick={() => onPage(page.offset + page.limit)}>
            Siguiente
          </Button>
        </div>
      ) : null}
    </>
  );
}

function ProductRow({
  product: p,
  showPrice,
  onOpen,
}: {
  product: ProductListItem;
  showPrice: boolean;
  onOpen: (id: string) => void;
}) {
  const cost = p.current_cost;
  return (
    <tr className="border-b border-line last:border-b-0 hover:bg-canvas-sunken/60" data-testid="catalog-row">
      <td className="px-3 py-2 align-top">
        <button
          type="button"
          onClick={() => onOpen(p.id)}
          className="text-left font-semibold text-brand-700 hover:underline"
        >
          {p.model_number}
        </button>
        {p.product_kind ? <div className="mt-0.5 text-[11px] text-ink-faint">{KIND_LABEL[p.product_kind] ?? p.product_kind}</div> : null}
      </td>
      <td className="px-3 py-2 align-top text-ink">
        {p.name_es || p.name || <span className="text-ink-faint">Sin nombre</span>}
        {p.category_es ? <div className="mt-0.5 text-[11px] text-ink-faint">{p.category_es}</div> : null}
      </td>
      <td className="px-3 py-2 align-top text-ink-muted">
        <div className="text-ink">{p.manufacturer.display_name}</div>
        {cost?.supplier && cost.supplier.display_name !== p.manufacturer.display_name ? (
          <div className="mt-0.5 text-[11px]">vía {cost.supplier.display_name}</div>
        ) : null}
      </td>
      {showPrice ? (
        <td className="px-3 py-2 text-right align-top tabular-nums">
          {cost && hasPrice(cost) ? (
            <>
              <div className="font-medium text-ink">{fmtMoney(cost.price, cost.currency)}</div>
              <div className="mt-0.5 text-[11px] text-ink-faint">
                {PRICE_KIND_LABEL[cost.price_kind] ?? cost.price_kind} · {fmtDate(cost.as_of)}
                {cost.is_stale ? " · antiguo" : ""}
              </div>
            </>
          ) : (
            <span className="text-ink-faint">—</span>
          )}
        </td>
      ) : null}
    </tr>
  );
}

/* ───────────────────────────────────────────────────────────── drawer ── */

function isProductNotFound(state: ResourceState<unknown>): boolean {
  return state.kind === "unavailable" && state.message.includes("product_not_found");
}

function ProductDrawer({ id, onClose }: { id: string; onClose: () => void }) {
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
      <ProductImageView imageId={image?.id ?? null} caption={image?.caption_es ?? null} />
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
      <QuotedHistory modelKey={p.model_key} />
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

/** What past quote documents charged for this model: sell prices, not costs; every role sees them. */
function QuotedHistory({ modelKey }: { modelKey: string }) {
  const load = useCallback(() => fetchPriceHistory(modelKey), [modelKey]);
  const [state, reload] = useResource(load, [load]);
  return (
    <Section title="Historial de cotizaciones">
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={2} />}>
        {(h) =>
          h.items.length === 0 ? (
            <p className="text-xs text-ink-faint">Este modelo no aparece en cotizaciones anteriores.</p>
          ) : (
            <div data-testid="quoted-history">
              {h.median_last_5 ? (
                <p className="mb-1.5 text-xs text-ink-muted">
                  Mediana de las últimas 5:{" "}
                  <span className="font-semibold tabular-nums text-ink">{fmtMoney(h.median_last_5, h.items[0]?.currency)}</span>
                </p>
              ) : null}
              <ul className="divide-y divide-line rounded-md border border-line text-xs">
                {h.items.map((r, i) => (
                  <li key={`${r.document_number ?? "doc"}-${i}`} className="flex flex-wrap items-baseline gap-x-2 px-2.5 py-1.5">
                    <span className="font-medium tabular-nums text-ink">
                      {fmtMoney(r.unit_price ?? r.line_total, r.currency)}
                    </span>
                    {r.qty && Number(r.qty) !== 1 ? <span className="text-ink-faint">× {r.qty}</span> : null}
                    {r.check_status === "disputed" ? <Badge tone="warn">En duda</Badge> : null}
                    <span className="ml-auto text-ink-faint">
                      {r.document_number ? `N° ${r.document_number} · ` : ""}
                      {fmtDay(r.date)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )
        }
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

/** The product's first image, by a short-lived signed URL; any refusal (no bucket yet, 404) is a placeholder. */
function ProductImageView({ imageId, caption }: { imageId: string | null; caption: string | null }) {
  if (!imageId) return <ImagePlaceholder />;
  return <SignedImage imageId={imageId} caption={caption} />;
}

function SignedImage({ imageId, caption }: { imageId: string; caption: string | null }) {
  const load = useCallback(() => fetchImageUrl(imageId), [imageId]);
  const [state] = useResource(load, [load]);
  const [broken, setBroken] = useState(false);
  if (state.kind === "loading") return <div className="crm-skeleton h-40 rounded-md" aria-label="Cargando imagen" />;
  if (state.kind !== "ready" || broken) return <ImagePlaceholder />;
  return (
    <figure>
      <img
        src={state.data.url}
        alt={caption ?? "Imagen del producto"}
        onError={() => setBroken(true)}
        className="max-h-56 w-full rounded-md border border-line bg-canvas-sunken object-contain"
      />
      {caption ? <figcaption className="mt-1 text-[11px] text-ink-faint">{caption}</figcaption> : null}
    </figure>
  );
}

function ImagePlaceholder() {
  return (
    <div
      data-testid="image-placeholder"
      className="flex h-28 items-center justify-center rounded-md border border-dashed border-line-strong bg-canvas-sunken/60 text-xs text-ink-faint"
    >
      Sin imagen
    </div>
  );
}

/* ───────────────────────────────────────────────────────── parameters ── */

function ParametersView() {
  const [state, reload] = useResource(fetchCostParameters);
  if (state.kind === "unavailable") return <NotEnabled />;
  return (
    <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={5} />}>
      {(params) => {
        const rows = Object.entries(params.current).sort(([a], [b]) => a.localeCompare(b));
        return rows.length === 0 ? (
          <EmptyState title="Sin parámetros de costeo">Ningún parámetro tiene un valor vigente.</EmptyState>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-line bg-canvas-raised" data-testid="cost-parameters">
            <table className="w-full min-w-[560px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-line text-left text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
                  <th scope="col" className="px-3 py-2">Parámetro</th>
                  <th scope="col" className="px-3 py-2 text-right">Valor</th>
                  <th scope="col" className="px-3 py-2">Vigente desde</th>
                  <th scope="col" className="px-3 py-2">Motivo</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([key, p]) => (
                  <tr key={key} className="border-b border-line last:border-b-0">
                    <td className="px-3 py-2 font-mono text-[11px] text-ink">{key}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink">
                      {typeof p.value === "string" || typeof p.value === "number" ? String(p.value) : JSON.stringify(p.value)}
                    </td>
                    <td className="px-3 py-2 text-ink-muted">{fmtDate(p.valid_from)}</td>
                    <td className="px-3 py-2 text-ink-muted">{p.reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }}
    </ResourceGate>
  );
}
