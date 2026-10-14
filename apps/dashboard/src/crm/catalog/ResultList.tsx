/**
 * The result list: fixed-height rows rendered as a window over the scroll position, so 10,000
 * matches cost as much to draw as 30. Each row: thumbnail, code (the part the search matched is
 * marked), Spanish name, maker and category, kind, and «Ficha» when Drive holds the product's own
 * data sheet. The keyboard selection lives in the page; this list keeps the active row in view.
 */
import { useEffect, useRef, useState } from "react";
import { crmHash } from "../crmRoute";
import { KIND_LABEL } from "./catalogApi";
import { codeKey, type IndexItem } from "./catalogSearch";
import { ProductImage } from "./ProductImage";

export const ROW_HEIGHT = 60;
const OVERSCAN = 6;
const FALLBACK_VIEWPORT = 600;

/** The original characters of `model` that the query's code key covers, or null. */
export function codeMatch(model: string, query: string): [number, number] | null {
  const key = codeKey(query);
  if (key.length < 2) return null;
  const positions: number[] = [];
  let normalized = "";
  for (let i = 0; i < model.length; i++) {
    const c = codeKey(model[i]);
    if (c) {
      normalized += c;
      positions.push(i);
    }
  }
  const at = normalized.indexOf(key);
  if (at < 0) return null;
  return [positions[at], positions[at + key.length - 1] + 1];
}

function Code({ model, query }: { model: string; query: string }) {
  const m = codeMatch(model, query);
  if (!m) return <>{model}</>;
  return (
    <>
      {model.slice(0, m[0])}
      <mark className="rounded-sm bg-brand-100 px-px text-brand-900">{model.slice(m[0], m[1])}</mark>
      {model.slice(m[1])}
    </>
  );
}

export function ResultList({ hits, query, active, onOpen, onHover }: {
  hits: IndexItem[];
  query: string;
  active: number;
  onOpen: (id: string) => void;
  onHover: (index: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(FALLBACK_VIEWPORT);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setViewport(el.clientHeight || FALLBACK_VIEWPORT);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // A new result set starts at the top.
  useEffect(() => {
    if (box.current) box.current.scrollTop = 0;
    setScrollTop(0);
  }, [hits]);

  // Keep the keyboard selection visible.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const top = active * ROW_HEIGHT;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + ROW_HEIGHT > el.scrollTop + viewport) el.scrollTop = top + ROW_HEIGHT - viewport;
  }, [active, viewport]);

  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const last = Math.min(hits.length, Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN);

  return (
    <div
      ref={box}
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      className="h-[calc(100dvh-15rem)] min-h-[22rem] overflow-y-auto rounded-lg border border-line bg-canvas-raised"
      role="list"
      aria-label="Productos"
    >
      <div style={{ height: hits.length * ROW_HEIGHT, position: "relative" }}>
        {hits.slice(first, last).map((item, offset) => {
          const index = first + offset;
          const selected = index === active;
          return (
            <div role="listitem" key={item.id} style={{ position: "absolute", top: index * ROW_HEIGHT, left: 0, right: 0 }}>
              <a
                href={crmHash("catalogo", item.id)}
                data-testid="catalog-row"
                aria-current={selected ? "true" : undefined}
                onClick={(e) => {
                  // A modified click opens a new tab as any link does; a plain one stays here.
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                  e.preventDefault();
                  onOpen(item.id);
                }}
                onMouseEnter={() => onHover(index)}
                style={{ height: ROW_HEIGHT }}
                className={`flex w-full items-center gap-3 border-b border-line px-3 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-600 ${
                  selected ? "bg-brand-50" : "hover:bg-canvas-sunken/60"
                }`}
              >
                <ProductImage imageId={item.image_id} kind={item.kind} alt="" size="thumb" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline gap-2">
                    <span className="font-mono text-xs font-medium text-brand-700">
                      <Code model={item.model_number} query={query} />
                    </span>
                    <span className="truncate text-xs text-ink">{item.name}</span>
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-ink-faint">
                    {item.manufacturer}
                    {item.category_es ? `, ${item.category_es}` : ""}
                  </span>
                </span>
                {item.has_datasheet ? (
                  <span
                    aria-label="Tiene ficha técnica"
                    className="shrink-0 rounded border border-brand-600/40 px-1.5 py-px text-[10px] font-medium text-brand-700"
                  >
                    Ficha
                  </span>
                ) : null}
                {item.kind ? (
                  <span className="hidden w-20 shrink-0 text-right text-[11px] text-ink-muted sm:block">
                    {KIND_LABEL[item.kind] ?? item.kind}
                  </span>
                ) : null}
              </a>
            </div>
          );
        })}
      </div>
    </div>
  );
}
