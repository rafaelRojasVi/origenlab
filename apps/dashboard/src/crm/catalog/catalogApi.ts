/**
 * Read-only client for the catalog (`/v2/catalog/*`, catalog 1a). GET only; nothing here can write.
 *
 * The API mounts these routes only behind `ORIGENLAB_V2_QUOTING_ENABLED`; with the switch off every
 * one is a 404, which `useResource` reports as `unavailable` and the page shows as «Catálogo no
 * habilitado». Costs are removed by the API for the viewer role (`redaction.py`): every cost field
 * below is optional and the screen renders only what comes back.
 */
import { fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";
import { fmtDate } from "../ui";

export const CATALOG_PATHS = {
  products: "/v2/catalog/products",
  suppliers: "/v2/catalog/suppliers",
  product: (id: string) => `/v2/catalog/products/${encodeURIComponent(id)}`,
  priceHistory: "/v2/catalog/price-history",
  fx: "/v2/catalog/fx",
  parameters: "/v2/catalog/parameters",
  imageUrl: (id: string) => `/v2/catalog/images/${encodeURIComponent(id)}/url`,
} as const;

/** `PRODUCT_KINDS` of `apps/api/.../v2/catalog/keys.py`, in its order. */
export const PRODUCT_KINDS = ["equipment", "accessory", "consumable", "spare_part", "service"] as const;
export type ProductKind = (typeof PRODUCT_KINDS)[number];

export const KIND_LABEL: Record<string, string> = {
  equipment: "Equipo",
  accessory: "Accesorio",
  consumable: "Insumo",
  spare_part: "Repuesto",
  service: "Servicio",
};

export const PRICE_KIND_LABEL: Record<string, string> = {
  dealer_net: "Neto distribuidor",
  list: "Lista",
  map: "Precio mínimo (MAP)",
  supplier_offer: "Oferta del proveedor",
  order_confirmation: "Confirmación de pedido",
  purchase_order: "Orden de compra",
  costing_sheet: "Hoja de costeo",
  negotiated: "Negociado",
};

/** The kind tabs of the product list, plural, in the order an operator scans them (no «Servicio»: none is loaded). */
export const KIND_TABS: { value: ProductKind; label: string }[] = [
  { value: "equipment", label: "Equipos" },
  { value: "accessory", label: "Accesorios" },
  { value: "spare_part", label: "Repuestos" },
  { value: "consumable", label: "Insumos" },
];

/** One supplier of `GET /v2/catalog/suppliers`: its active products, in total and by kind (`unclassified` when unset). */
export interface SupplierSummary {
  id: string;
  display_name: string;
  products: number;
  by_kind: Record<string, number>;
}

export interface NamedRef {
  id: string;
  display_name: string;
}

/** The product's newest supplier observation. `price` is absent for the viewer role. */
export interface CurrentCost {
  price?: string;
  currency: string;
  price_kind: string;
  as_of: string;
  is_stale: boolean;
  supplier: NamedRef;
}

export interface ProductListItem {
  id: string;
  model_number: string;
  model_key: string;
  name: string | null;
  name_es: string | null;
  category_es: string | null;
  product_kind: string | null;
  manufacturer: NamedRef;
  content_origin: string | null;
  confirmed: boolean;
  primary_image_id: string | null;
  current_cost: CurrentCost | null;
}

export interface ProductPage {
  items: ProductListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProductImage {
  id: string;
  caption_es: string | null;
  status: string;
  sort_order: number;
}

/** One supplier observation. Every money field is absent for the viewer role. */
export interface CostObservation {
  id: string;
  as_of: string;
  price?: string;
  currency: string;
  price_kind: string;
  list_price?: string | null;
  discount_pct?: string | null;
  incoterm: string | null;
  valid_until: string | null;
  min_qty: string | null;
  is_stale: boolean;
  source_document: string | null;
  supplier: NamedRef;
}

export interface SupplierTerms {
  supplier: NamedRef;
  currency: string;
  route: string;
  incoterm: string | null;
  origin_country: string | null;
  default_lead_time_es: string | null;
  map_enforced: boolean;
  default_discount_pct?: string | null;
  packing_pct?: string | null;
}

export interface SpecItem {
  label_es: string;
  value: string;
  unit: string | null;
}

export interface ProductDetail {
  id: string;
  model_number: string;
  model_key: string;
  name: string | null;
  name_es: string | null;
  description_es: string | null;
  category_es: string | null;
  product_kind: string | null;
  manufacturer: NamedRef;
  content_origin: string | null;
  content_confirmed_at: string | null;
  specs: SpecItem[] | null;
  weight_kg: string | null;
  length_cm: string | null;
  width_cm: string | null;
  height_cm: string | null;
  origin_country: string | null;
  is_dangerous_goods: boolean | null;
  images: ProductImage[];
  /** Absent for the viewer role. */
  cost_history?: CostObservation[];
  supplier_terms: SupplierTerms[];
}

/** A line of a past quote document: what OrigenLab charged, not what it paid. Every role sees it. */
export interface QuotedPrice {
  date: string | null;
  line_total: string | null;
  qty: string | null;
  unit_price: string | null;
  currency: string | null;
  check_status: string | null;
  client_type: string | null;
  document_number: string | null;
}

export interface PriceHistory {
  model_key: string | null;
  items: QuotedPrice[];
  median_last_5: string | null;
}

export interface CatalogFx {
  currency: "USD" | "EUR";
  clp_per_unit: string;
  as_of: string;
  source?: string;
  provider?: string;
}

export interface CostParameter {
  id: string;
  value: unknown;
  valid_from: string;
  set_by: string | null;
  reason: string | null;
}

export interface CostParameters {
  current: Record<string, CostParameter>;
  history: (CostParameter & { key: string })[];
}

export interface ProductQuery {
  q?: string;
  supplier?: string;
  kind?: string;
  limit: number;
  offset: number;
}

export const fetchProducts = (query: ProductQuery) =>
  fetchJsonGet<ProductPage>(
    operatorApiUrl(CATALOG_PATHS.products, {
      q: query.q || undefined,
      supplier: query.supplier || undefined,
      kind: query.kind || undefined,
      limit: query.limit,
      offset: query.offset,
    }),
  );
export const fetchSuppliers = () =>
  fetchJsonGet<{ items: SupplierSummary[] }>(operatorApiUrl(CATALOG_PATHS.suppliers));
export const fetchProduct = (id: string) => fetchJsonGet<ProductDetail>(operatorApiUrl(CATALOG_PATHS.product(id)));
export const fetchPriceHistory = (modelKey: string) =>
  fetchJsonGet<PriceHistory>(operatorApiUrl(CATALOG_PATHS.priceHistory, { model_key: modelKey }));
export const fetchCatalogFx = (currency: "USD" | "EUR") =>
  fetchJsonGet<CatalogFx>(operatorApiUrl(CATALOG_PATHS.fx, { currency }));
export const fetchCostParameters = () => fetchJsonGet<CostParameters>(operatorApiUrl(CATALOG_PATHS.parameters));
export const fetchImageUrl = (id: string) =>
  fetchJsonGet<{ url: string; expires_in: number }>(operatorApiUrl(CATALOG_PATHS.imageUrl(id)));

const DECIMALS: Record<string, number> = { CLP: 0, USD: 2, EUR: 2 };

/** «$12.345» for pesos, «US$ 1.234,50» and «€ 1.234,50» otherwise; null for an unreadable amount. */
export function fmtMoney(amount: string | null | undefined, currency: string | null | undefined): string {
  if (amount == null || amount === "") return "—";
  const value = Number(amount);
  if (!Number.isFinite(value)) return "—";
  const code = (currency ?? "").toUpperCase();
  const digits = DECIMALS[code] ?? 2;
  const text = value.toLocaleString("es-CL", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (code === "CLP") return `$${text}`;
  if (code === "USD") return `US$ ${text}`;
  if (code === "EUR") return `€ ${text}`;
  return code ? `${text} ${code}` : text;
}

/** «0.15» → «15 %». */
export function fmtPct(value: string | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toLocaleString("es-CL", { maximumFractionDigits: 2 })} %`;
}

/** True when the API sent a price at all: a viewer's answer never carries one. */
export function hasPrice(cost: { price?: string } | null | undefined): cost is { price: string } {
  return !!cost && typeof cost.price === "string";
}

/**
 * A calendar day («2026-10-06», an FX date or a quote date) as «06 oct 2026». Read as a local day:
 * `new Date("2026-10-06")` is UTC midnight, which Chile would show as the day before.
 */
export function fmtDay(value: string | null | undefined): string {
  const m = value?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return fmtDate(value);
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString("es-CL", { day: "2-digit", month: "short", year: "numeric" });
}
