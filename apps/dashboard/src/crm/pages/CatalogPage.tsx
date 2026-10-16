/**
 * «Catálogo» (v2, spec 2026-10-13): every product, searched in the browser. The cost-free index is
 * loaded once (`catalogIndex.ts`) and typing filters it with no request (`catalogSearch.ts`): the
 * model code first, then words, accent-insensitive and typo-tolerant. A supplier rail and kind
 * chips scope the list, each with live counts; the supplier, kind and search live in the address
 * (`#/crm/catalogo?s=&k=&q=&ficha=1`) so a view survives a reload. A row opens the product panel
 * (`#/crm/catalogo/<uuid>`) with its photo, Drive documents, supplier prices (never to a viewer),
 * quoted-price history and the day's rates; sales and admin also see the costing parameters.
 *
 * Every read is `/v2/catalog/*`, mounted only behind `ORIGENLAB_V2_QUOTING_ENABLED`: a 404 means
 * «not enabled here» and is said calmly. There is no write here.
 */
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { OperatorApiError } from "../../api/operatorClient";
import { useAuthSession } from "../../context/AuthSessionContext";
import { KIND_TABS, fetchCostParameters } from "../catalog/catalogApi";
import { loadCatalogIndex, type CatalogIndex } from "../catalog/catalogIndex";
import { createCatalogSearch } from "../catalog/catalogSearch";
import { NotEnabled, ProductDrawer } from "../catalog/ProductPanel";
import { ResultList } from "../catalog/ResultList";
import { SupplierRail } from "../catalog/SupplierRail";
import type { CrmSection } from "../crmRoute";
import { Button, EmptyState, PageHeader, ResourceGate, Segmented, Skeleton, fmtDate, fmtInt } from "../ui";
import { useResource } from "../useResource";

const MAX_HITS = 2000;
/** Focus the search on arrival only with a mouse: on a phone it would pop the keyboard up. */
const FINE_POINTER = typeof window !== "undefined" && !!window.matchMedia?.("(pointer: fine)").matches;

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
        subtitle="Productos por proveedor, sus fichas y listas de precios, y lo que se cotizó antes."
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

type IndexState =
  | { kind: "loading" }
  | { kind: "ready"; index: CatalogIndex }
  | { kind: "unavailable" }
  | { kind: "error" };

function useCatalogIndex(): [IndexState, () => void] {
  const [state, setState] = useState<IndexState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    loadCatalogIndex().then(
      (index) => live && setState({ kind: "ready", index }),
      (error: unknown) =>
        live && setState(error instanceof OperatorApiError && error.status === 404 ? { kind: "unavailable" } : { kind: "error" }),
    );
    return () => {
      live = false;
    };
  }, [attempt]);
  return [state, useCallback(() => setAttempt((n) => n + 1), [])];
}

interface Filters {
  q: string;
  supplier: string;
  kind: string;
  /** «Con ficha»: only products with a data sheet. */
  ficha: boolean;
}

const NO_FILTERS: Filters = { q: "", supplier: "", kind: "", ficha: false };

/** The catalog's own part of the address: `#/crm/catalogo?s=<supplier>&k=<kind>&q=<text>&ficha=1`. */
function readFilters(): Filters {
  const query = window.location.hash.split("?")[1] ?? "";
  const p = new URLSearchParams(query);
  return { q: p.get("q") ?? "", supplier: p.get("s") ?? "", kind: p.get("k") ?? "", ficha: p.get("ficha") === "1" };
}

function writeFilters(f: Filters): void {
  const [path] = window.location.hash.split("?");
  if (path.replace(/\/$/, "") !== "#/crm/catalogo") return; // a product is open: its address is its own
  const p = new URLSearchParams();
  if (f.supplier) p.set("s", f.supplier);
  if (f.kind) p.set("k", f.kind);
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.ficha) p.set("ficha", "1");
  const next = p.toString() ? `${path}?${p}` : path;
  if (next !== window.location.hash) window.history.replaceState(window.history.state, "", next);
}

function ProductsView({ openId, navigate }: { openId: string | null; navigate: Navigate }) {
  const [state, retry] = useCatalogIndex();
  const [filters, setFilters] = useState<Filters>(readFilters);
  const [active, setActive] = useState(0);
  const searchBox = useRef<HTMLInputElement>(null);
  const deferredQ = useDeferredValue(filters.q);

  useEffect(() => writeFilters(filters), [filters, openId]);

  // «/» anywhere outside a field jumps to the search.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key !== "/" || e.metaKey || e.ctrlKey || (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      searchBox.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const engine = useMemo(() => (state.kind === "ready" ? createCatalogSearch(state.index.items) : null), [state]);
  const result = useMemo(
    () => engine?.search(deferredQ, { supplierId: filters.supplier || undefined, kind: filters.kind || undefined, hasDatasheet: filters.ficha }, MAX_HITS),
    [engine, deferredQ, filters.supplier, filters.kind, filters.ficha],
  );
  useEffect(() => setActive(0), [result]);

  const update = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const open = (pid: string) => navigate("catalogo", pid);

  function onSearchKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!result) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, result.hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && result.hits[active]) {
      e.preventDefault();
      open(result.hits[active].id);
    } else if (e.key === "Escape" && filters.q) {
      e.preventDefault();
      update({ q: "" });
    }
  }

  if (state.kind === "unavailable") return <NotEnabled />;
  if (state.kind === "error") {
    return (
      <div role="alert" className="rounded-lg border border-line bg-canvas-raised p-4">
        <p className="text-sm font-semibold text-ink">No se pudo cargar el catálogo</p>
        <p className="mt-1 text-xs text-ink-muted">Revisa la conexión e inténtalo de nuevo.</p>
        <Button className="mt-3" onClick={retry}>Reintentar</Button>
      </div>
    );
  }

  const byKind = result?.byKind ?? {};
  const kindAll = Object.values(byKind).reduce((a, b) => a + b, 0);
  const stale = deferredQ !== filters.q;

  return (
    <>
      <div className="grid gap-3 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <aside className="lg:sticky lg:top-3 lg:self-start">
          {state.kind === "ready" && result ? (
            <SupplierRail
              suppliers={state.index.suppliers}
              counts={result.bySupplier}
              allCount={result.allSuppliers}
              value={filters.supplier}
              onChange={(supplier) => update({ supplier })}
            />
          ) : (
            <Skeleton rows={6} />
          )}
        </aside>
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <input
              ref={searchBox}
              type="search"
              aria-label="Buscar en el catálogo"
              name="q"
              autoComplete="off"
              spellCheck={false}
              placeholder="Código o nombre, p. ej. UP200St o balanza…"
              value={filters.q}
              onChange={(e) => update({ q: e.target.value })}
              onKeyDown={onSearchKey}
              autoFocus={!openId && FINE_POINTER}
              className="h-9 w-full rounded-md border border-line bg-canvas-raised px-3 text-sm text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600 sm:w-96"
            />
            <Segmented
              label="Tipo de producto"
              value={filters.kind}
              onChange={(kind) => update({ kind })}
              options={[
                { value: "", label: "Todos", count: kindAll },
                ...KIND_TABS.map((t) => ({ value: t.value, label: t.label, count: byKind[t.value] ?? 0 })),
              ]}
            />
            <button
              type="button"
              aria-pressed={filters.ficha}
              onClick={() => update({ ficha: !filters.ficha })}
              className={`inline-flex min-h-[44px] items-center rounded-md border px-3 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${
                filters.ficha
                  ? "border-brand-600 bg-brand-50 text-brand-700"
                  : "border-line bg-canvas-raised text-ink-muted hover:text-ink"
              }`}
            >
              Con ficha
            </button>
          </div>
          {state.kind === "loading" || !result ? (
            <Skeleton rows={10} />
          ) : (
            <>
              <p data-testid="catalog-count" aria-live="polite" className={`text-xs text-ink-muted ${stale ? "opacity-60" : ""}`}>
                <span className="font-semibold tabular-nums text-ink">{fmtInt(result.total)}</span>{" "}
                {result.total === 1 ? "producto" : "productos"}
                {result.total > result.hits.length ? `, se muestran los primeros ${fmtInt(result.hits.length)}` : ""}
                <span className="ml-2 tabular-nums text-ink-faint">{result.ms < 1 ? "<1" : Math.round(result.ms)} ms</span>
                <span className="ml-3 hidden text-ink-faint sm:inline">↑ ↓ para moverse, Enter para abrir</span>
              </p>
              {result.total === 0 ? (
                <div className="space-y-2">
                  <EmptyState
                    title={
                      filters.q.trim()
                        ? `Sin resultados para «${filters.q.trim()}»`
                        : filters.ficha
                          ? "Ningún producto tiene ficha todavía en este filtro"
                          : "Sin productos en este filtro"
                    }
                  >
                    {filters.ficha
                      ? "Quita «Con ficha» o prueba con otro proveedor o tipo de producto."
                      : "Prueba con el código sin espacios, otra palabra u otro proveedor."}
                  </EmptyState>
                  <Button className="min-h-[44px] px-4" onClick={() => setFilters(NO_FILTERS)}>Limpiar filtros</Button>
                </div>
              ) : (
                <ResultList hits={result.hits} query={deferredQ} active={active} onOpen={open} onHover={setActive} />
              )}
            </>
          )}
        </div>
      </div>
      {openId ? <ProductDrawer id={openId} onClose={() => navigate("catalogo")} /> : null}
    </>
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
