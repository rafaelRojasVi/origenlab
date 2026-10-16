/**
 * «Agregar del catálogo»: the Cotizador's product search, over the same in-browser index as the
 * Catálogo page (`catalogSearch.ts`: an exact code first, then codes that start with it, then
 * words). «/» focuses it from anywhere on the page; ↑/↓ move through the results, Enter or a click
 * adds the one marked. The index carries no cost: the server prices the line once it is added.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { CatalogIndex } from "../catalog/catalogIndex";
import { createCatalogSearch, type IndexItem } from "../catalog/catalogSearch";
import { ProductImage } from "../catalog/ProductImage";

const MAX_RESULTS = 8;

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

export function ProductSearch({
  index,
  failed,
  onRetry,
  onAdd,
  disabled,
}: {
  /** Null while the catalog index loads. */
  index: CatalogIndex | null;
  failed: boolean;
  onRetry: () => void;
  onAdd: (hit: IndexItem) => void;
  disabled: boolean;
}) {
  const ids = useId();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const engine = useMemo(() => (index ? createCatalogSearch(index.items) : null), [index]);
  const hits = useMemo(
    () => (engine && query.trim() ? engine.search(query, {}, MAX_RESULTS).hits : []),
    [engine, query],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      e.preventDefault();
      input.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function add(hit: IndexItem) {
    onAdd(hit);
    setQuery("");
    setOpen(false);
    setActive(0);
  }

  const showing = open && query.trim().length > 0;
  const listId = `${ids}-results`;

  return (
    <div className="relative">
      <label className="flex h-[46px] items-center gap-2.5 rounded-lg border-[1.5px] border-brand-700 bg-canvas-raised px-3.5 focus-within:ring-2 focus-within:ring-brand-600/30">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" className="shrink-0 text-brand-700">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
        <input
          ref={input}
          type="text"
          role="combobox"
          aria-label="Agregar del catálogo"
          aria-expanded={showing}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showing && hits[active] ? `${listId}-${active}` : undefined}
          value={query}
          disabled={disabled}
          placeholder={index || failed ? "Agregar del catálogo: código, modelo o nombre…" : "Cargando catálogo…"}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && hits.length) {
              e.preventDefault();
              setOpen(true);
              setActive((a) => Math.min(a + 1, hits.length - 1));
            } else if (e.key === "ArrowUp" && hits.length) {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter" && showing && hits[active]) {
              e.preventDefault();
              add(hits[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          className="min-w-0 flex-1 border-0 bg-transparent text-[14.5px] text-ink outline-none placeholder:text-ink-faint disabled:cursor-not-allowed"
        />
        <kbd aria-hidden="true" className="rounded border border-line px-1.5 py-px font-mono text-xs text-ink-muted">
          /
        </kbd>
      </label>

      {failed ? (
        <p role="alert" className="mt-1.5 text-[12.5px] text-bad">
          No se pudo cargar el catálogo.{" "}
          <button type="button" onClick={onRetry} className="font-semibold text-brand-700 underline">
            Reintentar
          </button>
        </p>
      ) : null}

      {showing ? (
        <div className="absolute inset-x-0 top-full z-20 mt-1 overflow-hidden rounded-lg border border-line bg-canvas-raised shadow-lg">
          {hits.length ? (
            <ul id={listId} role="listbox" aria-label="Productos del catálogo" className="max-h-96 overflow-y-auto py-1">
              {hits.map((hit, i) => (
                <li
                  key={hit.id}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === active}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    add(hit);
                  }}
                  onMouseEnter={() => setActive(i)}
                  className={`flex cursor-pointer items-center gap-3 px-3 py-2 ${i === active ? "bg-brand-50" : ""}`}
                >
                  <ProductImage imageId={hit.image_id} kind={hit.kind} alt="" size="thumb" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] text-ink">
                      <span className="font-mono font-medium">{hit.model_number}</span>
                      <span className="text-ink-muted"> · </span>
                      {hit.name}
                    </span>
                    <span className="block truncate text-xs text-ink-muted">{hit.manufacturer}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p id={listId} className="px-3 py-2.5 text-[13px] text-ink-muted">
              {engine ? `Nada en el catálogo con «${query.trim()}».` : "Cargando catálogo…"}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
