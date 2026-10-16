import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { formatMoney } from "../money";
import { Badge, Button, EmptyState, Skeleton } from "../ui";
import { statusOf } from "./IssuedView";
import { QuoteRefusal, fetchQuoteList, type QuoteListPage, type QuoteListRow, type QuoteListStatus } from "./quoteApi";
import { quoteLabel } from "./quoteLabel";
import { quoteRefusalText } from "./quoteRefusal";
import { quoteWhen } from "./quoteWhen";
import { MAX_TABS, type TabMeta } from "./tabStore";

/**
 * «Todas»: the Cotizador module's list of quotes, one row per quote at its current revision,
 * newest change first. A filter (Borradores by default), a search that waits 300 ms after the last
 * key, a counts line, and «Cargar más» by the API's cursor. A row opens the quote as a tab
 * (`onOpen`, with the label, client and state the tab shows); an imported quote says «importada»
 * and opens read-only (the quote page edits authored drafts only). Every number is the API's.
 *
 * Only the newest request counts: changing the filter or the search abandons what is in flight
 * (aborted, and a token keeps a late answer out anyway) and starts again from the first page.
 * Nothing is read while the pane is hidden; shown again, it refreshes in place — the rows stay
 * until the new answer replaces them.
 */
export const SEARCH_DEBOUNCE_MS = 300;

type Counts = QuoteListPage["counts"];

const FILTERS: { value: QuoteListStatus; label: string }[] = [
  { value: "draft", label: "Borradores" },
  { value: "approved", label: "Aprobadas" },
  { value: "sent", label: "Enviadas" },
  { value: "all", label: "Todas" },
];

const EMPTY: Record<QuoteListStatus, string> = {
  draft: "No hay borradores.",
  approved: "No hay cotizaciones aprobadas.",
  sent: "Ninguna cotización enviada todavía.",
  all: "Aún no hay cotizaciones.",
};

/** «3 borradores · 5 aprobadas sin enviar · 41 enviadas»: what is zero is left out. */
export function countsLine(c: Counts): string {
  const parts: string[] = [];
  if (c.draft) parts.push(`${c.draft} ${c.draft === 1 ? "borrador" : "borradores"}`);
  if (c.approved) parts.push(`${c.approved} ${c.approved === 1 ? "aprobada" : "aprobadas"} sin enviar`);
  if (c.sent) parts.push(`${c.sent} ${c.sent === 1 ? "enviada" : "enviadas"}`);
  return parts.length ? parts.join(" · ") : "Sin cotizaciones";
}

const isAbort = (e: unknown) => !!e && typeof e === "object" && (e as { name?: unknown }).name === "AbortError";

export function metaOfRow(r: QuoteListRow): TabMeta {
  return {
    label: quoteLabel({ label: r.label, quote_number: r.quote_number }),
    client: r.client?.name || r.opportunity.title || null,
    status: r.status,
  };
}

function productsText(r: QuoteListRow): string {
  const shown = r.items.first.filter(Boolean).join(", ");
  const rest = r.items.count - r.items.first.length;
  if (!shown) return r.items.count ? `${r.items.count} productos` : "—";
  return rest > 0 ? `${shown} +${rest} más` : shown;
}

export interface QuoteListPaneProps {
  /** False while the pane is hidden behind an open quote: nothing is read. */
  active?: boolean;
  onOpen: (quoteId: string, meta: TabMeta) => void;
  onPreferences?: () => void;
  onNew?: () => void;
}

export function QuoteListPane({ active = true, onOpen, onPreferences, onNew }: QuoteListPaneProps) {
  const [status, setStatus] = useState<QuoteListStatus>("draft");
  const [text, setText] = useState("");
  const [term, setTerm] = useState("");
  const [rows, setRows] = useState<QuoteListRow[]>([]);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [errorText, setErrorText] = useState("");
  const [more, setMore] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [tick, setTick] = useState(0);

  // The newest request's number: an answer from an older one is dropped on arrival.
  const generation = useRef(0);
  // The filter and search the rows on screen answer; the same again is a refresh in place.
  const loadedKey = useRef<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setTerm(text.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [text]);

  useEffect(() => {
    if (!active) return;
    const key = `${status}\u0000${term}`;
    const mine = ++generation.current;
    const controller = new AbortController();
    const inPlace = loadedKey.current === key;
    if (!inPlace) {
      loadedKey.current = null;
      setRows([]);
      setNext(null);
      setPhase("loading");
    }
    setMore({ busy: false, error: null });
    fetchQuoteList({ status, q: term || undefined }, controller.signal)
      .then((p) => {
        if (mine !== generation.current || controller.signal.aborted) return;
        loadedKey.current = key;
        setRows(p.rows);
        setCounts(p.counts);
        setNext(p.next_cursor);
        setPhase("ready");
      })
      .catch((e: unknown) => {
        if (mine !== generation.current || controller.signal.aborted || isAbort(e)) return;
        if (inPlace) return; // a refresh that failed leaves the rows as they were
        setErrorText(quoteRefusalText(e, "No se pudo cargar la lista de cotizaciones."));
        setPhase("error");
      });
    return () => controller.abort();
  }, [active, status, term, tick]);

  const loadMore = useCallback(() => {
    if (!next || more.busy) return;
    const mine = generation.current;
    const controller = new AbortController();
    setMore({ busy: true, error: null });
    fetchQuoteList({ status, q: term || undefined, cursor: next }, controller.signal)
      .then((p) => {
        if (mine !== generation.current) return;
        setRows((old) => {
          const seen = new Set(old.map((r) => r.quote_id));
          return [...old, ...p.rows.filter((r) => !seen.has(r.quote_id))];
        });
        setCounts(p.counts);
        setNext(p.next_cursor);
        setMore({ busy: false, error: null });
      })
      .catch((e: unknown) => {
        if (mine !== generation.current || isAbort(e)) return;
        // A cursor the API does not recognise: the list starts over rather than ending half way.
        if (e instanceof QuoteRefusal && e.code === "bad_cursor") {
          loadedKey.current = null;
          setTick((n) => n + 1);
          return;
        }
        setMore({ busy: false, error: "No se pudieron cargar más cotizaciones. Intenta de nuevo." });
      });
  }, [next, more.busy, status, term]);

  const open = (r: QuoteListRow) => onOpen(r.quote_id, metaOfRow(r));
  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, r: QuoteListRow) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open(r);
    }
  };

  const empty = phase === "ready" && rows.length === 0;
  return (
    <div className="space-y-[18px]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-ink">Cotizaciones</h1>
          {counts ? <span className="text-[13px] text-ink-muted">{countsLine(counts)}</span> : null}
        </div>
        <div className="flex gap-2">
          <Button
            onClick={onPreferences}
            disabled={!onPreferences}
            title={onPreferences ? undefined : "Llega en la siguiente entrega"}
            className="min-h-[44px] px-4 text-[14px] font-semibold"
          >
            Preferencias
          </Button>
          <Button
            variant="primary"
            onClick={onNew}
            disabled={!onNew}
            title={onNew ? undefined : "Llega en la siguiente entrega"}
            className="min-h-[44px] px-[18px] text-[14px] font-semibold"
          >
            Nueva cotización
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <div role="group" aria-label="Estado" className="inline-flex overflow-hidden rounded-[10px] border border-line-strong bg-canvas-raised">
          {FILTERS.map((f, i) => {
            const on = f.value === status;
            return (
              <button
                key={f.value}
                type="button"
                aria-pressed={on}
                onClick={() => setStatus(f.value)}
                className={`inline-flex h-11 items-center px-3.5 text-[13.5px] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${
                  i > 0 ? "border-l border-line-strong" : ""
                } ${on ? "bg-brand-700 font-semibold text-white" : "text-ink hover:bg-canvas-sunken"}`}
              >
                {f.label}
              </button>
            );
          })}
        </div>
        <input
          type="search"
          aria-label="Buscar"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Buscar por número, cliente o producto…"
          maxLength={200}
          className="h-11 min-w-[260px] flex-1 rounded-[10px] border border-line-strong bg-canvas-raised px-3 text-[14px] text-ink placeholder:text-ink-faint focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600"
        />
      </div>

      {phase === "loading" ? <Skeleton rows={6} /> : null}
      {phase === "error" ? (
        <div role="alert" className="rounded-lg border border-bad/30 bg-bad-bg px-4 py-3">
          <p className="text-[13px] font-semibold text-bad">{errorText}</p>
          <Button
            className="mt-2 min-h-[44px] px-4 text-[14px]"
            onClick={() => {
              loadedKey.current = null;
              setTick((n) => n + 1);
            }}
          >
            Reintentar
          </Button>
        </div>
      ) : null}
      {empty ? (
        <EmptyState title={term ? `Ninguna cotización coincide con «${term}».` : EMPTY[status]}>
          {term ? "Prueba con otro número, cliente o producto, o cambia el estado." : undefined}
        </EmptyState>
      ) : null}

      {phase === "ready" && rows.length > 0 ? (
        <div className="overflow-x-auto rounded-[14px] border border-line bg-canvas-raised">
          <table className="w-full min-w-[860px] border-collapse text-left text-[13.5px] text-ink">
            <thead>
              <tr className="bg-canvas-sunken text-[12px] font-semibold text-ink-muted">
                <th scope="col" className="w-[120px] px-[18px] py-2.5 font-semibold">Número</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Cliente · caso</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Productos</th>
                <th scope="col" className="w-[120px] px-3 py-2.5 font-semibold">Estado</th>
                <th scope="col" className="w-[140px] px-3 py-2.5 text-right font-semibold">Total</th>
                <th scope="col" className="w-[110px] px-[18px] py-2.5 text-right font-semibold">Actualizada</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const st = statusOf(r.status);
                const total = formatMoney(r.grand_total, r.currency);
                const imported = r.origin === "historical_import";
                return (
                  <tr
                    key={r.quote_id}
                    tabIndex={0}
                    onClick={() => open(r)}
                    onKeyDown={(e) => onRowKey(e, r)}
                    className="min-h-[44px] cursor-pointer border-t border-line hover:bg-canvas-sunken focus:outline-none focus-visible:bg-canvas-sunken focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600"
                  >
                    <td className="px-[18px] py-3 align-middle">
                      <span className="font-mono font-medium tabular-nums">{quoteLabel({ label: r.label, quote_number: r.quote_number })}</span>
                      {imported ? (
                        <span className="mt-1 block text-[11.5px] font-normal text-ink-muted" title="Cotización de antes de esta aplicación: se ve, no se edita">
                          importada
                        </span>
                      ) : null}
                    </td>
                    <td className="max-w-[260px] px-3 py-3 align-middle">
                      <span className="block truncate font-semibold">{r.client?.name ?? "Sin institución"}</span>
                      <span className="block truncate text-[12.5px] text-ink-muted">
                        {r.opportunity.title ?? "Sin título"}
                        {r.opportunity.closed ? " · caso cerrado" : ""}
                      </span>
                    </td>
                    <td className="max-w-[260px] px-3 py-3 align-middle text-[13px] text-ink-muted" title={r.items.first.join(", ")}>
                      <span className="block truncate">{productsText(r)}</span>
                    </td>
                    <td className="px-3 py-3 align-middle">
                      <Badge tone={st.tone}>{st.label}</Badge>
                    </td>
                    <td className="px-3 py-3 text-right align-middle font-mono tabular-nums">{total ?? "—"}</td>
                    <td className="whitespace-nowrap px-[18px] py-3 text-right align-middle text-[13px] text-ink-muted">
                      <time dateTime={r.updated_at}>{quoteWhen(r.updated_at)}</time>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {more.error ? (
        <p role="alert" className="text-[13px] font-semibold text-bad">
          {more.error}
        </p>
      ) : null}
      {phase === "ready" && next ? (
        <div className="flex justify-center">
          <Button busy={more.busy} busyLabel="Cargando…" onClick={loadMore} className="min-h-[44px] px-5 text-[14px]">
            Cargar más
          </Button>
        </div>
      ) : null}

      <p className="text-[12.5px] text-ink-muted">
        Abrir una fila la agrega como pestaña. Las pestañas quedan abiertas en este computador hasta que las cierres (hasta {MAX_TABS}); cada una guarda sola.
      </p>
    </div>
  );
}
