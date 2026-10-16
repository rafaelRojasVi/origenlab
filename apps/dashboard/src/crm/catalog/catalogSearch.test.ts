import { describe, expect, it } from "vitest";
import { codeKey, createCatalogSearch, foldText, type IndexItem } from "./catalogSearch";

const item = (id: string, model: string, name: string, extra: Partial<IndexItem> = {}): IndexItem => ({
  id,
  model_number: model,
  model_key: codeKey(model),
  name,
  category_es: null,
  kind: "equipment",
  manufacturer: "Fabricante Ficticio",
  supplier_ids: ["s1"],
  image_id: null,
  has_datasheet: false,
  name_en: null,
  ...extra,
});

const items = [
  item("a", "UP200St", "Procesador ultrasónico 200 W"),
  item("b", "UP400St", "Procesador ultrasónico 400 W"),
  item("c", "BAL-120", "Balanza analítica", { supplier_ids: ["s2"] }),
  item("d", "S26d7", "Sonotrodo de titanio", { kind: "accessory" }),
];

describe("catalogSearch", () => {
  const engine = createCatalogSearch(items);

  it("normalises codes exactly like the database's model_key", () => {
    expect(codeKey("up-200 st")).toBe("UP200ST");
    expect(codeKey("a_b.c/d")).toBe("ABCD");
  });

  it("folds accents and case", () => expect(foldText("Balánza ÚLTRA")).toBe("balanza ultra"));

  it("ranks the exact code first and never treats a neighbour code as exact", () => {
    const r = engine.search("up 200-st");
    expect(r.hits[0].id).toBe("a");
    expect(r.exact.has("a")).toBe(true);
    expect(r.exact.has("b")).toBe(false);
    expect(r.hits.map((h) => h.id)).not.toContain("b");
  });

  it("matches a code prefix while typing", () => expect(engine.search("UP2").hits.map((h) => h.id)).toEqual(["a"]));

  it("matches words without accents, by prefix, and with a typo", () => {
    expect(engine.search("balanza").hits[0].id).toBe("c");
    expect(engine.search("ultrasonico").hits.map((h) => h.id).sort()).toEqual(["a", "b"]);
    expect(engine.search("sonotrdo").hits[0].id).toBe("d");
    expect(engine.search("titan").hits[0].id).toBe("d");
  });

  it("finds a product by its English name too", () => {
    const e = createCatalogSearch([item("en", "AG-1", "Agitador magnético", { name_en: "Magnetic stirrer" })]);
    expect(e.search("stirrer").hits.map((h) => h.id)).toEqual(["en"]);
  });

  it("needs every word: «procesador 400» is only the 400", () =>
    expect(engine.search("procesador 400").hits.map((h) => h.id)).toEqual(["b"]));

  it("scopes by supplier and kind; each facet counts what clicking it would give", () => {
    const r = engine.search("", { supplierId: "s1", kind: "accessory" });
    expect(r.hits.map((h) => h.id)).toEqual(["d"]);
    expect(r.total).toBe(1);
    expect(r.bySupplier).toEqual({ s1: 1 }); // ignores the supplier scope, keeps the kind
    expect(r.byKind).toEqual({ equipment: 2, accessory: 1 }); // ignores the kind scope, keeps the supplier
    expect(r.allSuppliers).toBe(1); // «Todos» in the rail: the kind scope, every supplier
  });

  it("«Con ficha» keeps only products with a data sheet and narrows every count", () => {
    const withSheets = createCatalogSearch([
      item("a", "UP200St", "Procesador ultrasónico 200 W", { has_datasheet: true }),
      item("b", "UP400St", "Procesador ultrasónico 400 W"),
      item("c", "BAL-120", "Balanza analítica", { supplier_ids: ["s2"], has_datasheet: true }),
      item("d", "S26d7", "Sonotrodo de titanio", { kind: "accessory" }),
    ]);
    const r = withSheets.search("", { hasDatasheet: true });
    expect(r.hits.map((h) => h.id)).toEqual(["a", "c"]);
    expect(r.total).toBe(2);
    expect(r.bySupplier).toEqual({ s1: 1, s2: 1 });
    expect(r.byKind).toEqual({ equipment: 2 });
    expect(withSheets.search("procesador", { hasDatasheet: true }).hits.map((h) => h.id)).toEqual(["a"]);
    expect(withSheets.search("", { hasDatasheet: false }).total).toBe(4);
    expect(engine.search("", { hasDatasheet: true }).total).toBe(0); // none of `items` has one
  });

  it("an empty query lists everything in index order", () =>
    expect(engine.search("  ").hits.map((h) => h.id)).toEqual(["a", "b", "c", "d"]));

  it("caps hits but counts all", () => {
    const r = engine.search("", {}, 2);
    expect(r.hits).toHaveLength(2);
    expect(r.total).toBe(4);
  });

  // Spec S1 targets < 50 ms per keystroke on an operator's machine (measured 1–4 ms in a browser).
  // A shared CI runner under a parallel suite is ~2× slower and noisy: best of five, 100 ms ceiling.
  it("searches 10,500 items fast enough to type through", () => {
    const big = Array.from({ length: 10_500 }, (_, i) =>
      item(`x${i}`, `MOD-${i}`, `Equipo de laboratorio ${i % 97}`, { supplier_ids: [`s${i % 8}`] }),
    );
    const e = createCatalogSearch(big);
    e.search("equipo");
    for (const q of ["laboratorio 4", "MOD-12", "equpo", ""]) {
      let best = Infinity;
      for (let run = 0; run < 5; run++) {
        const t = performance.now();
        e.search(q, { supplierId: "s3" });
        best = Math.min(best, performance.now() - t);
      }
      expect(best, q).toBeLessThan(100);
    }
  });
});
