/**
 * The catalog's in-browser search (Catálogo v2, spec 2026-10-13 D2–D4).
 *
 * Two paths, in this order:
 * 1. **Codes.** The query's code key (the database's `model_key` rule: no spaces, `-`, `_`, `.`,
 *    `/`; upper case) is looked up in a sorted array — exact matches first, then codes that start
 *    with it. Operators search by model code most of the time, and fuzziness must never push the
 *    exact code down or turn «UP200» into «UP400».
 * 2. **Words.** MiniSearch over name, category, maker and model: accent- and case-insensitive,
 *    every word required, prefix on the word being typed, typo tolerance only for words of four
 *    letters or more that carry no digit.
 *
 * «Con ficha» keeps only products whose data sheet is in Drive and narrows every count below.
 * Facets: the supplier counts ignore the supplier scope (but keep the kind) and the kind counts
 * ignore the kind scope (but keep the supplier), so each control shows what clicking it gives.
 */
import MiniSearch from "minisearch";

/** One product of `GET /v2/catalog/index`, decoded. No cost field exists here, by design. */
export interface IndexItem {
  id: string;
  model_number: string;
  model_key: string | null;
  name: string;
  category_es: string | null;
  kind: string | null;
  manufacturer: string;
  supplier_ids: string[];
  image_id: string | null;
  has_datasheet: boolean;
  /** The supplier's (usually English) name when it differs from the Spanish one. */
  name_en: string | null;
}

export interface SearchScope {
  supplierId?: string;
  kind?: string;
  /** Only products with an active data sheet («Con ficha»); it narrows every count, like the rest. */
  hasDatasheet?: boolean;
}

export interface SearchResult {
  hits: IndexItem[];
  total: number;
  bySupplier: Record<string, number>;
  byKind: Record<string, number>;
  /** Matches across every supplier within the kind scope: the rail's «Todos». */
  allSuppliers: number;
  /** Ids whose code equals the query's code key. */
  exact: Set<string>;
  ms: number;
}

/** Same normalisation as `catalog.product.model_key` (and `catalog/keys.py`). */
export function codeKey(text: string): string {
  return text.replace(/[\s\-_./]/g, "").toUpperCase();
}

export function foldText(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export function createCatalogSearch(items: IndexItem[]) {
  const byId = new Map(items.map((i) => [i.id, i]));
  const codes = items
    .map((item) => ({ key: item.model_key ?? codeKey(item.model_number), item }))
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));

  const mini = new MiniSearch<IndexItem>({
    fields: ["name", "name_en", "category_es", "manufacturer", "model_number"],
    storeFields: [],
    processTerm: (term) => foldText(term),
    searchOptions: {
      boost: { model_number: 3, name: 2 },
      prefix: (_term, i, terms) => i === terms.length - 1,
      fuzzy: (term) => (/\d/.test(term) || term.length < 4 ? false : term.length >= 8 ? 2 : 1),
      combineWith: "AND",
    },
  });
  mini.addAll(items);

  function codeHits(query: string): { exact: IndexItem[]; prefix: IndexItem[] } {
    const key = codeKey(query);
    const exact: IndexItem[] = [];
    const prefix: IndexItem[] = [];
    if (key.length < 2) return { exact, prefix };
    let lo = 0;
    let hi = codes.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (codes[mid].key < key) lo = mid + 1;
      else hi = mid;
    }
    for (let i = lo; i < codes.length && codes[i].key.startsWith(key); i++) {
      (codes[i].key === key ? exact : prefix).push(codes[i].item);
    }
    return { exact, prefix };
  }

  function search(query: string, scope: SearchScope = {}, limit = 500): SearchResult {
    const started = performance.now();
    const q = query.trim();
    let ordered: IndexItem[];
    let exact = new Set<string>();
    if (!q) {
      ordered = items;
    } else {
      const codeMatches = codeHits(q);
      exact = new Set(codeMatches.exact.map((i) => i.id));
      const first = [...codeMatches.exact, ...codeMatches.prefix];
      const seen = new Set(first.map((i) => i.id));
      const words = mini
        .search(q)
        .map((r) => byId.get(r.id as string))
        .filter((i): i is IndexItem => i !== undefined && !seen.has(i.id));
      ordered = [...first, ...words];
    }

    const bySupplier: Record<string, number> = {};
    const byKind: Record<string, number> = {};
    const hits: IndexItem[] = [];
    let total = 0;
    let allSuppliers = 0;
    for (const item of ordered) {
      if (scope.hasDatasheet && !item.has_datasheet) continue;
      const kindOk = !scope.kind || item.kind === scope.kind;
      const supplierOk = !scope.supplierId || item.supplier_ids.includes(scope.supplierId);
      if (kindOk) {
        allSuppliers++;
        for (const s of item.supplier_ids) bySupplier[s] = (bySupplier[s] ?? 0) + 1;
      }
      if (supplierOk) {
        const k = item.kind ?? "unclassified";
        byKind[k] = (byKind[k] ?? 0) + 1;
      }
      if (kindOk && supplierOk) {
        total++;
        if (hits.length < limit) hits.push(item);
      }
    }
    return { hits, total, bySupplier, byKind, allSuppliers, exact, ms: performance.now() - started };
  }

  return { search };
}

export type CatalogSearch = ReturnType<typeof createCatalogSearch>;
