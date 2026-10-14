import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearCatalogIndexMemory, loadCatalogIndex } from "./catalogIndex";

const FIELDS = ["id", "model_number", "model_key", "name", "category_es", "kind", "manufacturer", "supplier_ids",
  "image_id", "has_datasheet"];
const full = (version: string, name = "Sonda") => ({
  version,
  fields: FIELDS,
  suppliers: [{ id: "s1", display_name: "Proveedor", products: 1, by_kind: { equipment: 1 } }],
  items: [["p1", "SYN-1", "SYN1", name, null, "equipment", "Fabricante", ["s1"], null, true]],
});

function stubFetch(...bodies: unknown[]) {
  const fn = vi.fn();
  for (const b of bodies) fn.mockResolvedValueOnce(new Response(JSON.stringify(b), { status: 200 }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("loadCatalogIndex", () => {
  beforeEach(() => {
    clearCatalogIndexMemory();
    sessionStorage.clear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("decodes the positional rows by the declared fields", async () => {
    const fetchMock = stubFetch(full("v1"));
    const idx = await loadCatalogIndex();
    expect(idx.items[0]).toEqual({ id: "p1", model_number: "SYN-1", model_key: "SYN1", name: "Sonda",
      category_es: null, kind: "equipment", manufacturer: "Fabricante", supplier_ids: ["s1"], image_id: null,
      has_datasheet: true });
    expect(idx.suppliers[0].display_name).toBe("Proveedor");
    expect(String(fetchMock.mock.calls[0][0])).not.toContain("version=");
  });

  it("reuses memory without fetching again", async () => {
    const fetchMock = stubFetch(full("v1"));
    await loadCatalogIndex();
    await loadCatalogIndex();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("revalidates a stored copy by version and keeps it when unchanged", async () => {
    stubFetch(full("v1"));
    await loadCatalogIndex();
    clearCatalogIndexMemory();
    const fetchMock = stubFetch({ version: "v1", unchanged: true });
    const idx = await loadCatalogIndex();
    expect(String(fetchMock.mock.calls[0][0])).toContain("version=v1");
    expect(idx.items[0].name).toBe("Sonda");
  });

  it("replaces a stored copy when the version moved", async () => {
    stubFetch(full("v1"));
    await loadCatalogIndex();
    clearCatalogIndexMemory();
    stubFetch(full("v2", "Sonda nueva"));
    expect((await loadCatalogIndex()).items[0].name).toBe("Sonda nueva");
  });

  it("works when sessionStorage refuses to store", async () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    stubFetch(full("v1"));
    expect((await loadCatalogIndex()).items).toHaveLength(1);
    spy.mockRestore();
  });

  it("a failed fetch rejects and is not cached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response("boom", { status: 500 })));
    await expect(loadCatalogIndex()).rejects.toThrow();
    stubFetch(full("v1"));
    expect((await loadCatalogIndex()).items).toHaveLength(1);
  });
});
