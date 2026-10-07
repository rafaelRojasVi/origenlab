/**
 * «Catálogo» reads `/v2/catalog/*` only. A 404 means the API has not mounted the catalog
 * (`ORIGENLAB_V2_QUOTING_ENABLED` off) and is shown calmly; cost columns follow what the API sent,
 * so a viewer — whose answers carry no prices — never sees one; the drawer reads the product, its
 * quoted-price history and the day's rates, and an image the bucket cannot sign is a placeholder.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearResourceCache } from "../useResource";
import { CatalogPage } from "./CatalogPage";

// Every value below is invented: no real product, supplier, price or cost appears here.
const PRODUCT_ID = "6bafddfa-0000-4000-8000-0000000000c1";
const IMAGE_ID = "6bafddfa-0000-4000-8000-0000000000e1";
const SUPPLIER = { id: "6bafddfa-0000-4000-8000-0000000000b1", display_name: "Proveedor Ejemplo" };
const MAKER = { id: "6bafddfa-0000-4000-8000-0000000000a1", display_name: "Fabricante Ejemplo" };

function item(over: Record<string, unknown> = {}, withPrice = true) {
  return {
    id: PRODUCT_ID,
    model_number: "XB-100",
    model_key: "XB100",
    name: "Example stirrer",
    name_es: "Agitador de ejemplo",
    category_es: "Agitadores",
    product_kind: "equipment",
    manufacturer: MAKER,
    content_origin: "import",
    confirmed: false,
    primary_image_id: null,
    current_cost: {
      ...(withPrice ? { price: "1234.50" } : {}),
      currency: "USD",
      price_kind: "list",
      as_of: "2026-09-01T12:00:00+00:00",
      is_stale: false,
      supplier: SUPPLIER,
    },
    ...over,
  };
}

function detail(withCosts = true, images: unknown[] = []) {
  return {
    id: PRODUCT_ID,
    model_number: "XB-100",
    model_key: "XB100",
    name: "Example stirrer",
    name_es: "Agitador de ejemplo",
    description_es: "Descripción inventada.",
    category_es: "Agitadores",
    product_kind: "equipment",
    manufacturer: MAKER,
    content_origin: "import",
    content_confirmed_at: null,
    specs: [{ label_es: "Velocidad", value: "1500", unit: "rpm" }],
    weight_kg: "2.5",
    length_cm: null,
    width_cm: null,
    height_cm: null,
    origin_country: "DE",
    is_dangerous_goods: false,
    images,
    supplier_terms: [],
    ...(withCosts
      ? {
          cost_history: [
            {
              id: "c1",
              as_of: "2026-09-01T12:00:00+00:00",
              price: "1234.50",
              currency: "USD",
              price_kind: "list",
              list_price: null,
              discount_pct: null,
              incoterm: null,
              valid_until: null,
              min_qty: null,
              is_stale: false,
              source_document: null,
              supplier: SUPPLIER,
            },
          ],
        }
      : {}),
  };
}

const HISTORY = {
  model_key: "XB100",
  median_last_5: "999000.0000",
  items: [
    {
      date: "2026-08-14",
      line_total: "999000",
      qty: "1",
      unit_price: "999000",
      currency: "CLP",
      check_status: "verified",
      client_type: "universidad",
      document_number: "09999-26",
    },
  ],
};

interface Routes {
  products?: () => Response;
  product?: () => Response;
  image?: () => Response;
  parameters?: () => Response;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stub(routes: Routes = {}) {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, "http://localhost");
      urls.push(url);
      const path = url.pathname;
      if (path === "/v2/catalog/products") {
        return Promise.resolve(routes.products?.() ?? json({ items: [item()], total: 1, limit: 50, offset: 0 }));
      }
      if (path === `/v2/catalog/products/${PRODUCT_ID}`) return Promise.resolve(routes.product?.() ?? json(detail()));
      if (path === "/v2/catalog/price-history") return Promise.resolve(json(HISTORY));
      if (path === "/v2/catalog/fx") {
        const currency = url.searchParams.get("currency");
        return Promise.resolve(json({ currency, clp_per_unit: currency === "USD" ? "900.00" : "1000.00", as_of: "2026-10-06" }));
      }
      if (path === `/v2/catalog/images/${IMAGE_ID}/url`) {
        return Promise.resolve(routes.image?.() ?? json({ url: "https://storage.example/x.png", expires_in: 600 }));
      }
      if (path === "/v2/catalog/parameters") {
        return Promise.resolve(
          routes.parameters?.() ??
            json({
              current: { iva_rate: { id: "p1", value: "0.19", valid_from: "2026-10-01T00:00:00+00:00", set_by: null, reason: "Valor de ejemplo" } },
              history: [],
            }),
        );
      }
      return Promise.resolve(json({ detail: "Not Found" }, 404));
    }),
  );
  return urls;
}

function session(role: string): AuthSessionState {
  return {
    kind: "signed_in",
    method: "google_profile",
    operator: { operatorId: `op-${role}`, email: "ventas@example.cl", displayName: "Ventas", role },
    caseCommandsEnabled: true,
    crmAuthoringEnabled: true,
  };
}

function renderCatalog({ role = "sales", id = null as string | null } = {}) {
  const navigate = vi.fn();
  render(
    <AuthSessionContext.Provider value={{ session: session(role), signOut: async () => true }}>
      <CatalogPage id={id} navigate={navigate} />
    </AuthSessionContext.Provider>,
  );
  return navigate;
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("Catálogo list", () => {
  it("lists products with the total, the manufacturer and the latest price, and opens one by its id", async () => {
    stub();
    const navigate = renderCatalog();
    const row = await screen.findByTestId("catalog-row");
    expect(within(row).getByText("XB-100")).toBeInTheDocument();
    expect(within(row).getByText("Agitador de ejemplo")).toBeInTheDocument();
    expect(within(row).getByText("Fabricante Ejemplo")).toBeInTheDocument();
    expect(within(row).getByText("vía Proveedor Ejemplo")).toBeInTheDocument();
    expect(within(row).getByText("US$ 1.234,50")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Último precio" })).toBeInTheDocument();
    expect(screen.getByText("Productos", { selector: "dt" }).nextSibling).toHaveTextContent("1");
    fireEvent.click(within(row).getByRole("button", { name: "XB-100" }));
    expect(navigate).toHaveBeenCalledWith("catalogo", PRODUCT_ID);
  });

  it("searches by the typed text and filters by kind", async () => {
    const urls = stub();
    renderCatalog();
    await screen.findByTestId("catalog-row");
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar en el catálogo" }), { target: { value: "xb 100" } });
    await waitFor(() => expect(urls.some((u) => u.searchParams.get("q") === "xb 100")).toBe(true));
    const searched = urls.find((u) => u.searchParams.get("q") === "xb 100") as URL;
    expect(searched.searchParams.get("offset")).toBe("0");
    expect(searched.searchParams.has("supplier")).toBe(false);
    fireEvent.change(screen.getByRole("combobox", { name: /Tipo/ }), { target: { value: "consumable" } });
    await waitFor(() => expect(urls.some((u) => u.searchParams.get("kind") === "consumable")).toBe(true));
  });

  it("offers the suppliers the list has named as a filter", async () => {
    const urls = stub();
    renderCatalog();
    await screen.findByTestId("catalog-row");
    const select = screen.getByRole("combobox", { name: /Proveedor/ });
    await waitFor(() => expect(within(select).getByRole("option", { name: "Proveedor Ejemplo" })).toBeInTheDocument());
    fireEvent.change(select, { target: { value: SUPPLIER.id } });
    await waitFor(() => expect(urls.some((u) => u.searchParams.get("supplier") === SUPPLIER.id)).toBe(true));
  });

  it("shows a viewer no cost column and no costing parameters, because the API sends no price", async () => {
    stub({ products: () => json({ items: [item({}, false)], total: 1, limit: 50, offset: 0 }) });
    renderCatalog({ role: "viewer" });
    await screen.findByTestId("catalog-row");
    expect(screen.queryByRole("columnheader", { name: "Último precio" })).toBeNull();
    expect(screen.queryByText(/US\$/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Parámetros de costeo" })).toBeNull();
  });

  it("says calmly that the catalog is not enabled when the API answers 404", async () => {
    stub({ products: () => json({ detail: "Not Found" }, 404) });
    renderCatalog();
    expect(await screen.findByText("Catálogo no habilitado")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the costing parameters to sales, read-only", async () => {
    stub();
    renderCatalog();
    fireEvent.click(await screen.findByRole("button", { name: "Parámetros de costeo" }));
    const table = await screen.findByTestId("cost-parameters");
    expect(within(table).getByText("iva_rate")).toBeInTheDocument();
    expect(within(table).getByText("0.19")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Guardar|Editar|Nuevo/ })).toBeNull();
  });
});

describe("Catálogo product drawer", () => {
  it("loads the product, its supplier prices, the quoted-price history and the day's rates", async () => {
    const urls = stub();
    renderCatalog({ id: PRODUCT_ID });
    const dialog = await screen.findByRole("dialog", { name: "XB-100" });
    expect(await within(dialog).findByTestId("cost-history")).toHaveTextContent("US$ 1.234,50");
    const history = await within(dialog).findByTestId("quoted-history");
    expect(history).toHaveTextContent("$999.000");
    expect(history).toHaveTextContent("N° 09999-26");
    expect(urls.find((u) => u.pathname === "/v2/catalog/price-history")?.searchParams.get("model_key")).toBe("XB100");
    const fx = within(dialog).getByTestId("catalog-fx");
    await waitFor(() => expect(fx).toHaveTextContent("1 USD = $900"));
    expect(fx).toHaveTextContent("1 EUR = $1.000");
    expect(fx).toHaveTextContent("06 oct 2026");
    expect(within(dialog).getByTestId("image-placeholder")).toBeInTheDocument();
  });

  it("leaves supplier prices out of a viewer's drawer", async () => {
    stub({ product: () => json(detail(false)) });
    renderCatalog({ role: "viewer", id: PRODUCT_ID });
    const dialog = await screen.findByRole("dialog", { name: "XB-100" });
    await within(dialog).findByTestId("quoted-history");
    expect(within(dialog).queryByText("Precios observados")).toBeNull();
  });

  it("shows a placeholder when the image cannot be signed (no bucket yet: 503)", async () => {
    stub({
      product: () => json(detail(true, [{ id: IMAGE_ID, caption_es: null, status: "confirmed", sort_order: 0 }])),
      image: () => json({ detail: { code: "storage_unavailable" } }, 503),
    });
    renderCatalog({ id: PRODUCT_ID });
    const dialog = await screen.findByRole("dialog", { name: "XB-100" });
    expect(await within(dialog).findByTestId("image-placeholder")).toBeInTheDocument();
    expect(within(dialog).queryByRole("img")).toBeNull();
  });

  it("shows a signed image when the bucket answers", async () => {
    stub({ product: () => json(detail(true, [{ id: IMAGE_ID, caption_es: "Vista frontal", status: "confirmed", sort_order: 0 }])) });
    renderCatalog({ id: PRODUCT_ID });
    const img = await screen.findByRole("img", { name: "Vista frontal" });
    expect(img).toHaveAttribute("src", "https://storage.example/x.png");
  });

  it("says a product that does not exist is not found, and closes back to the list", async () => {
    stub({ product: () => json({ detail: { code: "product_not_found", message: "product not found" } }, 404) });
    const navigate = renderCatalog({ id: PRODUCT_ID });
    expect(await screen.findByText("Producto no encontrado")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(navigate).toHaveBeenCalledWith("catalogo");
  });
});
