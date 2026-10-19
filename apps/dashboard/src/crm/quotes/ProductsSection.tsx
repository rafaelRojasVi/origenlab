/**
 * The Cotizador's «Productos» card (canvas): search the catalog, take a suggestion («Suele ir con
 * <principal>:» from the case's kits, «Mencionado en el correo:» from its emails) or add a free
 * line, then edit each line in the table. «Vista cliente» (ruling B7, switched in the page's
 * header) hides costs, recargo and margin, for sharing the screen with the client.
 *
 * Every number in the table is the API's `computed` (preview, save or workspace). A row finds its
 * priced line by its client-only `uid` in `computedKeys` (the draft that `computed` priced), never
 * by position, so removing or reordering lines never shows one row another's numbers; a line the
 * API has not priced yet shows none. The catalog index carries no cost, by design.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { loadCatalogIndex, type CatalogIndex } from "../catalog/catalogIndex";
import type { IndexItem } from "../catalog/catalogSearch";
import {
  addCatalogItem,
  addFreeLine,
  computedLineFor,
  principalItemOf,
  removeItem,
  setPrincipal,
  updateItem,
  type DraftProblem,
} from "./draftEditor";
import { GRID_CLIENT, GRID_FULL, GRID_GAP, LineRow, type LineProduct } from "./LineRow";
import { ProductSearch } from "./ProductSearch";
import type { Computed, ComputedLine, QuoteDraft, QuoteWorkspace } from "./quoteApi";

/** A suggestion whose product is not in the loaded index still adds a line the server can price. */
function suggestedItem(product_id: string, name: string, model: string | null): IndexItem {
  return {
    id: product_id,
    model_number: model ?? "",
    model_key: null,
    name,
    category_es: null,
    kind: null,
    manufacturer: "",
    supplier_ids: [],
    image_id: null,
    has_datasheet: false,
    name_en: null,
  };
}

function Chip({ label, onClick, disabled }: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex h-8 items-center rounded-full border border-dashed border-brand-700 px-3 text-[13px] font-semibold text-brand-700 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {label}
    </button>
  );
}

export function ProductsSection({
  workspace,
  draft,
  computed,
  computedKeys,
  stale,
  invalid,
  disabled,
  clientView,
  edit,
  loadIndex = loadCatalogIndex,
}: {
  workspace: QuoteWorkspace;
  draft: QuoteDraft;
  computed: Computed | null;
  /** The uids of the draft `computed` priced (line_no n is `computedKeys[n-1]`). */
  computedKeys: string[];
  /** `computed` prices an older draft than the one shown. */
  stale: boolean;
  invalid: DraftProblem[];
  disabled: boolean;
  /** «Vista cliente», switched in the page's header. */
  clientView: boolean;
  edit: (fn: (draft: QuoteDraft) => QuoteDraft) => void;
  loadIndex?: () => Promise<CatalogIndex>;
}) {
  const [index, setIndex] = useState<CatalogIndex | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [focusLine, setFocusLine] = useState<number | null>(null);
  const loader = useRef(loadIndex);
  loader.current = loadIndex;

  useEffect(() => {
    let live = true;
    setFailed(false);
    loader.current().then(
      (loaded) => live && setIndex(loaded),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [attempt]);

  const byId = useMemo(() => new Map((index?.items ?? []).map((i) => [i.id, i])), [index]);
  const supplierName = useMemo(() => new Map((index?.suppliers ?? []).map((s) => [s.id, s.display_name])), [index]);
  const currency = workspace.current?.quote_currency ?? "CLP";
  const onQuote = new Set(draft.items.map((i) => i.product_id).filter(Boolean));

  const lineOf = (i: number): ComputedLine | null => computedLineFor(computed, computedKeys, draft.items[i].uid);
  const economicsOf = (line: ComputedLine | null) =>
    (line && computed?.line_economics?.find((e) => e.line_no === line.line_no)) || null;

  function productOf(i: number, line: ComputedLine | null): LineProduct | null {
    const id = draft.items[i].product_id;
    const hit = id ? byId.get(id) : undefined;
    if (!hit) return null;
    const supplierId = line?.supplier_organization_id ?? hit.supplier_ids[0];
    return {
      code: hit.model_number || null,
      supplier: (supplierId && supplierName.get(supplierId)) || hit.manufacturer || null,
      imageId: hit.image_id,
      kind: hit.kind,
    };
  }

  const add = useCallback((hit: IndexItem) => edit((d) => addCatalogItem(d, hit)), [edit]);

  const principalItem = principalItemOf(draft, computed, computedKeys);
  const principalModel = principalItem
    ? ((principalItem.product_id && byId.get(principalItem.product_id)?.model_number) || principalItem.description)
    : null;

  const kits = workspace.suggestions.kits.filter((k) => k.product_id && !onQuote.has(k.product_id));
  const mentioned = workspace.suggestions.mentioned.filter((m) => m.product_id && !onQuote.has(m.product_id));
  const fromSuggestion = (product_id: string, name: string, model: string | null) =>
    add(byId.get(product_id) ?? suggestedItem(product_id, name, model));

  const grid = clientView ? GRID_CLIENT : GRID_FULL;
  // Unique per pane: the Cotizador module keeps several quotes in the page at once.
  const headingId = useId();

  return (
    <section aria-labelledby={headingId} className="overflow-hidden rounded-xl border border-line bg-canvas-raised">
      <header className="flex flex-wrap items-center justify-between gap-3 px-5 pb-3 pt-4">
        <h2 id={headingId} className="text-[17px] font-semibold text-ink">
          Productos
        </h2>
        <span className="text-[12.5px] text-ink-muted">
          {clientView ? "Vista cliente: sin costos ni márgenes" : "Costos y márgenes: sólo ventas y administración"}
        </span>
      </header>

      <div className="flex flex-col gap-2.5 px-5 pb-3.5">
        <ProductSearch index={index} failed={failed} onRetry={() => setAttempt((a) => a + 1)} onAdd={add} disabled={disabled} />
        {kits.length ? (
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="text-ink-muted">{principalModel ? `Suele ir con ${principalModel}:` : "Suele ir con el equipo principal:"}</span>
            {kits.map((k) => (
              <Chip
                key={k.model_key}
                label={`+ ${k.name}`}
                disabled={disabled}
                onClick={() => fromSuggestion(k.product_id as string, k.name, null)}
              />
            ))}
          </div>
        ) : null}
        {mentioned.length ? (
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="text-ink-muted">Mencionado en el correo:</span>
            {mentioned.map((m) => (
              <Chip
                key={m.model_key}
                label={`+ ${m.name}`}
                disabled={disabled}
                onClick={() => fromSuggestion(m.product_id as string, m.name, m.model)}
              />
            ))}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Chip
            label="+ Línea libre (sin catálogo)"
            disabled={disabled}
            onClick={() => {
              setFocusLine(draft.items.length);
              edit(addFreeLine);
            }}
          />
        </div>
      </div>

      {/* Two tiers per line (LineRow): it fits a 760 px column; narrower, it scrolls sideways. */}
      <div className="overflow-x-auto">
        <div role="table" aria-label="Líneas de la cotización" className={clientView ? "min-w-[480px]" : "min-w-[732px]"}>
          <div role="row" className="border-y border-line/80 bg-canvas-sunken px-5 py-2 text-xs font-semibold text-ink-muted">
            {/* Tier 1 is the product itself; its header is for screen readers. */}
            <span role="columnheader" className="sr-only">
              Producto
            </span>
            <div className={`grid ${grid} ${GRID_GAP}`}>
              <span role="columnheader" className="text-right">
                Cant.
              </span>
              {clientView ? null : (
                <span role="columnheader" className="text-right">
                  Costo unit.
                </span>
              )}
              {clientView ? null : (
                <span role="columnheader" className="text-right">
                  Recargo
                </span>
              )}
              <span role="columnheader" className="text-right">
                Precio unit.
              </span>
              <span role="columnheader" className="text-right">
                Total
              </span>
              {clientView ? null : (
                <span role="columnheader" className="text-right">
                  Margen
                </span>
              )}
              <span role="columnheader">
                <span className="sr-only">Acciones</span>
              </span>
            </div>
          </div>
          {draft.items.length === 0 ? (
            <p className="px-5 py-6 text-[13px] text-ink-muted">Busca un producto del catálogo o agrega una línea libre.</p>
          ) : (
            draft.items.map((item, i) => {
              const line = lineOf(i);
              return (
                <LineRow
                  key={item.uid ?? `at-${i}`}
                  index={i}
                  item={item}
                  line={line}
                  economics={economicsOf(line)}
                  product={productOf(i, line)}
                  currency={currency}
                  problems={invalid.filter((p) => p.index === i && p.field.startsWith("items."))}
                  clientView={clientView}
                  stale={stale}
                  disabled={disabled}
                  focusDescription={focusLine === i}
                  onPatch={(patch) => edit((d) => updateItem(d, i, patch))}
                  onPrincipal={() => edit((d) => setPrincipal(d, i))}
                  onRemove={() => {
                    setFocusLine(null);
                    edit((d) => removeItem(d, i));
                  }}
                />
              );
            })
          )}
        </div>
      </div>
    </section>
  );
}
