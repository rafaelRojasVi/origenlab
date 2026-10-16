/**
 * «Catálogo» (v2) reads `/v2/catalog/*` only. The list is the cost-free index, searched in the
 * browser: typing makes no request. A 404 means the API has not mounted the catalog and is shown
 * calmly. The drawer reads the product, its quoted-price history, the day's rates and its Drive
 * documents (price lists only when the API sent them, never to a viewer); an image the bucket
 * cannot sign is a placeholder.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthSessionState } from "../../api/authClient";
import { AuthSessionContext } from "../../context/AuthSessionContext";
import { clearCatalogIndexMemory } from "../catalog/catalogIndex";
import { resetImageUrls } from "../catalog/imageUrls";
import { clearResourceCache } from "../useResource";
import { CatalogPage } from "./CatalogPage";

// Every value below is invented: no real product, supplier, price or cost appears here.
const PRODUCT_ID = "6bafddfa-0000-4000-8000-0000000000c1";
const IMAGE_ID = "6bafddfa-0000-4000-8000-0000000000e1";
const SUPPLIER = { id: "6bafddfa-0000-4000-8000-0000000000b1", display_name: "Proveedor Ejemplo" };
const MAKER = { id: "6bafddfa-0000-4000-8000-0000000000a1", display_name: "Fabricante Ejemplo" };

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

const OTHER = { id: "00000000-0000-4000-8000-0000000000b2", display_name: "Otro Proveedor" };
const FIELDS = ["id", "model_number", "model_key", "name", "category_es", "kind", "manufacturer", "supplier_ids",
  "image_id", "has_datasheet"];
const SECOND_ID = "6bafddfa-0000-4000-8000-0000000000c2";
const INDEX = {
  version: "v1",
  fields: FIELDS,
  suppliers: [
    { ...SUPPLIER, products: 2, by_kind: { equipment: 1, consumable: 1 } },
    { ...OTHER, products: 1, by_kind: { consumable: 1 } },
  ],
  items: [
    [PRODUCT_ID, "XB-100", "XB100", "Agitador de ejemplo", "Agitadores", "equipment", "Fabricante Ejemplo",
      [SUPPLIER.id], null, true],
    [SECOND_ID, "BAL-200", "BAL200", "Balanza analítica", "Balanzas", "consumable", "Fabricante Ejemplo",
      [SUPPLIER.id, OTHER.id], IMAGE_ID, false],
  ],
};

interface Routes {
  index?: () => Response;
  product?: () => Response;
  images?: () => Response;
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
      if (path === "/v2/catalog/index") return Promise.resolve(routes.index?.() ?? json(INDEX));
      if (path === `/v2/catalog/products/${PRODUCT_ID}`) return Promise.resolve(routes.product?.() ?? json(detail()));
      if (path === "/v2/catalog/price-history") return Promise.resolve(json(HISTORY));
      if (path === "/v2/catalog/fx") {
        const currency = url.searchParams.get("currency");
        return Promise.resolve(json({ currency, clp_per_unit: currency === "USD" ? "900.00" : "1000.00", as_of: "2026-10-06" }));
      }
      if (path === "/v2/catalog/images/urls") {
        const ids = (url.searchParams.get("ids") ?? "").split(",");
        return Promise.resolve(
          routes.images?.() ??
            json({ urls: Object.fromEntries(ids.map((i) => [i, "https://storage.example/x.png"])), expires_in: 600 }),
        );
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

const rows = () => screen.queryAllByTestId("catalog-row");
const search = () => screen.getByRole("searchbox", { name: "Buscar en el catálogo" });

beforeEach(() => {
  clearCatalogIndexMemory();
  resetImageUrls();
  sessionStorage.clear();
  window.history.replaceState(null, "", "#/crm/catalogo");
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearResourceCache();
});

describe("Catálogo list", () => {
  it("lists the index and opens a product by its id", async () => {
    stub();
    const navigate = renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const row = rows()[0];
    expect(within(row).getByText("Agitador de ejemplo")).toBeInTheDocument();
    expect(within(row).getByText(/Fabricante Ejemplo/)).toBeInTheDocument();
    expect(within(row).getByLabelText("Tiene ficha técnica")).toBeInTheDocument();
    expect(screen.getByTestId("catalog-count")).toHaveTextContent("2 productos");
    fireEvent.click(row);
    expect(navigate).toHaveBeenCalledWith("catalogo", PRODUCT_ID);
  });

  it("each row is a real link, so a modified click opens a new tab instead of the panel", async () => {
    stub();
    const navigate = renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(rows()[0]).toHaveAttribute("href", `#/crm/catalogo/${PRODUCT_ID}`);
    fireEvent.click(rows()[0], { ctrlKey: true });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("searches as you type without asking the API again, and highlights the code", async () => {
    const urls = stub();
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    fireEvent.change(search(), { target: { value: "bal 2" } });
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(within(rows()[0]).getByText("Balanza analítica")).toBeInTheDocument();
    fireEvent.change(search(), { target: { value: "xb-1" } });
    await waitFor(() => expect(within(rows()[0]).getByText("XB-1", { selector: "mark" })).toBeInTheDocument());
    expect(urls.filter((u) => u.pathname !== "/v2/catalog/images/urls").map((u) => u.pathname)).toEqual(["/v2/catalog/index"]);
  });

  it("scopes by supplier from the rail, with counts, and remembers it in the address", async () => {
    stub();
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const rail = screen.getByRole("navigation", { name: "Proveedores" });
    expect(within(rail).getByRole("button", { name: /Todos\s*2/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(rail).getByRole("button", { name: /Otro Proveedor\s*1/ }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(window.location.hash).toContain(`s=${OTHER.id}`);
  });

  it("restores the supplier and the search from the address", async () => {
    window.history.replaceState(null, "", `#/crm/catalogo?s=${OTHER.id}&q=balanza`);
    stub();
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(search()).toHaveValue("balanza");
  });

  it("filters by kind with live counts", async () => {
    stub();
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: /Insumos\s*1/ }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(within(rows()[0]).getByText("Balanza analítica")).toBeInTheDocument();
  });

  it("«Con ficha» keeps only products with a data sheet, remembers it in the address and clears", async () => {
    stub();
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const toggle = screen.getByRole("button", { name: "Con ficha" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(within(rows()[0]).getByText("Agitador de ejemplo")).toBeInTheDocument();
    expect(within(rows()[0]).getByLabelText("Tiene ficha técnica")).toBeInTheDocument();
    expect(window.location.hash).toContain("ficha=1");
    // The supplier rail counts what clicking it would give with the filter on.
    const rail = screen.getByRole("navigation", { name: "Proveedores" });
    expect(within(rail).getByRole("button", { name: /Otro Proveedor\s*0/ })).toBeInTheDocument();
    fireEvent.click(within(rail).getByRole("button", { name: /Otro Proveedor/ }));
    expect(await screen.findByText("Ningún producto tiene ficha todavía en este filtro")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Con ficha" })).toHaveAttribute("aria-pressed", "false");
    expect(window.location.hash).not.toContain("ficha");
  });

  it("restores «Con ficha» from the address", async () => {
    window.history.replaceState(null, "", "#/crm/catalogo?ficha=1");
    stub();
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Con ficha" })).toHaveAttribute("aria-pressed", "true");
  });

  it("moves with the arrow keys and opens with Enter; «/» focuses the search; Escape clears it", async () => {
    stub();
    const navigate = renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    fireEvent.keyDown(document.body, { key: "/" });
    expect(search()).toHaveFocus();
    fireEvent.keyDown(search(), { key: "ArrowDown" });
    fireEvent.keyDown(search(), { key: "Enter" });
    expect(navigate).toHaveBeenCalledWith("catalogo", SECOND_ID);
    fireEvent.change(search(), { target: { value: "zzz" } });
    fireEvent.keyDown(search(), { key: "Escape" });
    expect(search()).toHaveValue("");
  });

  it("says when nothing matches and offers to clear the filters", async () => {
    stub();
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    fireEvent.change(search(), { target: { value: "nada-coincide" } });
    expect(await screen.findByText(/Sin resultados/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(rows()).toHaveLength(2));
  });

  it("shows an error with a retry when the index cannot load", async () => {
    let fail = true;
    stub({ index: () => (fail ? json({ detail: "boom" }, 500) : json(INDEX)) });
    renderCatalog();
    expect(await screen.findByText("No se pudo cargar el catálogo")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(rows()).toHaveLength(2));
  });

  it("says calmly that the catalog is not enabled when the API answers 404", async () => {
    stub({ index: () => json({ detail: "Not Found" }, 404) });
    renderCatalog();
    expect(await screen.findByText("Catálogo no habilitado")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("thumbnails become placeholders after one refused signing request", async () => {
    const urls = stub({ images: () => json({ detail: { code: "storage_unavailable" } }, 503) });
    renderCatalog();
    await waitFor(() => expect(rows()).toHaveLength(2));
    await waitFor(() => expect(within(rows()[1]).getByTestId("image-placeholder")).toBeInTheDocument());
    expect(urls.filter((u) => u.pathname === "/v2/catalog/images/urls")).toHaveLength(1);
  });

  it("hides the costing parameters from a viewer", async () => {
    stub();
    renderCatalog({ role: "viewer" });
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(screen.queryByRole("button", { name: "Parámetros de costeo" })).toBeNull();
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
  it("reads the quoted-price history from the product itself: one request, not two", async () => {
    const urls = stub({ product: () => json({ ...detail(), price_history: HISTORY }) });
    renderCatalog({ id: PRODUCT_ID });
    const dialog = await screen.findByRole("dialog", { name: "XB-100" });
    expect(await within(dialog).findByTestId("quoted-history")).toHaveTextContent("N° 09999-26");
    expect(urls.some((u) => u.pathname === "/v2/catalog/price-history")).toBe(false);
  });

  it("an older API without the embedded history still gets it, by its own request", async () => {
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
      images: () => json({ detail: { code: "storage_unavailable" } }, 503),
    });
    renderCatalog({ id: PRODUCT_ID });
    const dialog = await screen.findByRole("dialog", { name: "XB-100" });
    expect(await within(dialog).findByTestId("image-placeholder")).toBeInTheDocument();
    expect(within(dialog).queryByRole("img", { name: /Imagen de/ })).toBeNull();
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

  it("offers the product's own data sheet, and the supplier's price list, as Drive links", async () => {
    stub({
      product: () =>
        json({
          ...detail(),
          documents: {
            datasheets: [{ id: "d1", title: "XB-100.pdf", url: "https://drive.google.com/file/d/SheetX/view", as_of: null }],
            price_lists: [{ id: "l1", title: "Lista 2026.xlsx", url: "https://drive.google.com/file/d/ListX/view",
              as_of: "2026-01-01", supplier: SUPPLIER }],
          },
        }),
    });
    renderCatalog({ id: PRODUCT_ID });
    const dialog = await screen.findByRole("dialog", { name: "XB-100" });
    const sheet = await within(dialog).findByRole("link", { name: /Ficha técnica/ });
    expect(sheet).toHaveAttribute("href", "https://drive.google.com/file/d/SheetX/view");
    expect(sheet).toHaveAttribute("target", "_blank");
    expect(sheet).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(dialog).getByRole("link", { name: /Lista de precios.*Proveedor Ejemplo/ })).toHaveAttribute(
      "href", "https://drive.google.com/file/d/ListX/view");
  });

  it("a viewer with no data sheet sees no download section", async () => {
    stub({ product: () => json({ ...detail(false), documents: { datasheets: [] } }) });
    renderCatalog({ role: "viewer", id: PRODUCT_ID });
    const dialog = await screen.findByRole("dialog", { name: "XB-100" });
    await within(dialog).findByTestId("quoted-history");
    expect(within(dialog).queryByText("Descargas")).toBeNull();
  });
});
