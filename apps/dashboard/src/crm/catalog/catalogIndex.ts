/**
 * The catalog index the search runs on (Catálogo v2): fetched once per session, kept in memory and
 * in sessionStorage by version, and revalidated with `?version=` — the API answers `unchanged`
 * when the stored copy is current, so a reload costs one tiny request instead of the whole list.
 */
import { fetchCatalogIndex, type SupplierSummary } from "./catalogApi";
import type { IndexItem } from "./catalogSearch";

export interface CatalogIndex {
  version: string;
  suppliers: SupplierSummary[];
  items: IndexItem[];
}

const STORAGE_KEY = "catalog-index:v1";
let memory: CatalogIndex | null = null;
let inflight: Promise<CatalogIndex> | null = null;

/** Forget the in-memory copy (sessionStorage stays): the next load revalidates by version. */
export function clearCatalogIndexMemory(): void {
  memory = null;
  inflight = null;
}

function decode(fields: string[], rows: unknown[][]): IndexItem[] {
  return rows.map((row) => {
    const item: Record<string, unknown> = {};
    fields.forEach((f, i) => (item[f] = row[i]));
    return item as unknown as IndexItem;
  });
}

function stored(): CatalogIndex | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as CatalogIndex) : null;
  } catch {
    return null;
  }
}

function store(index: CatalogIndex): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(index));
  } catch {
    // Quota or privacy mode: memory alone still serves this page.
  }
}

async function load(): Promise<CatalogIndex> {
  const previous = stored();
  const payload = await fetchCatalogIndex(previous?.version);
  if (payload.unchanged && previous) return previous;
  if (payload.unchanged) return loadWithoutVersion();
  const index = { version: payload.version, suppliers: payload.suppliers, items: decode(payload.fields, payload.items) };
  store(index);
  return index;
}

/** The stored copy vanished between the request and the answer: ask for the whole list. */
async function loadWithoutVersion(): Promise<CatalogIndex> {
  const payload = await fetchCatalogIndex();
  if (payload.unchanged) throw new Error("catalog index: unchanged without a stored copy");
  const index = { version: payload.version, suppliers: payload.suppliers, items: decode(payload.fields, payload.items) };
  store(index);
  return index;
}

export function loadCatalogIndex(): Promise<CatalogIndex> {
  if (memory) return Promise.resolve(memory);
  if (!inflight) {
    inflight = load().then(
      (index) => {
        memory = index;
        return index;
      },
      (error: unknown) => {
        inflight = null;
        throw error;
      },
    );
  }
  return inflight;
}
