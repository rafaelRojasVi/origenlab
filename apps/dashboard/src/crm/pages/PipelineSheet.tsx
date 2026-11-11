/**
 * Pipeline «Lista» as a sheet (owner 2026-11-11: «más campos, como Excel»): one row per case, one fact
 * per column, a fixed header, every column sortable (most urgent first by default), a totals row.
 * A click on a row opens the case.
 */
import { useMemo, useState, type ReactNode } from "react";
import { originLabel } from "../caseOrigin";
import { contactLine, displayName, quoteProduct } from "../caseDisplay";
import type { OpportunityCardData } from "../crmTypes";
import { formatMoney } from "../money";
import { caseAmountClp } from "../nextSteps";
import { BOARD_COLUMNS, boardColumnOf, type BoardColumnKey } from "../stage";
import { URGENCY_BADGE, URGENCY_EDGE, urgencyText, type CaseUrgency } from "../urgency";
import { useGrowingList } from "../useGrowingList";
import { GrowingListEnd } from "../GrowingListEnd";
import { QueueIcon, stepAction, stepIcon } from "./HoyQueueParts";
import { cardMoney, shortDate } from "./PipelineBoard";

const COLUMN_NAME = Object.fromEntries(BOARD_COLUMNS.map((c) => [c.key, c.label])) as Record<BoardColumnKey, string>;

interface Row {
  card: OpportunityCardData;
  u: CaseUrgency | undefined;
  name: string;
  contact: string | null;
  product: string | null;
  quote: string | null;
  sent: string | null;
  stage: BoardColumnKey;
  due: Date | null;
  last: { at: string; who: "cliente" | "nosotros" } | null;
  origin: string | null;
  net: number | null;
  netText: string;
}

type Key = "urg" | "name" | "contact" | "product" | "quote" | "sent" | "stage" | "step" | "due" | "last" | "origin" | "net";

const time = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN);

function lastTouch(c: OpportunityCardData): Row["last"] {
  const inb = c.last_contact?.inbound?.at ?? null;
  const out = c.last_contact?.outbound?.at ?? null;
  if (!inb && !out) return null;
  return time(inb) >= time(out) || !out ? { at: inb!, who: "cliente" } : { at: out, who: "nosotros" };
}

const COLS: { key: Key; label: string; className?: string; sort: (r: Row) => number | string }[] = [
  { key: "urg", label: "Urgencia", sort: (r) => r.u?.rank ?? Number.MAX_SAFE_INTEGER },
  { key: "name", label: "Institución", sort: (r) => r.name.toLocaleLowerCase("es") },
  { key: "step", label: "Próximo paso", sort: (r) => (r.u?.step ? stepAction(r.u.step) : r.card.next_action.text).toLocaleLowerCase("es") },
  { key: "due", label: "Vence", sort: (r) => r.due?.getTime() ?? Number.MAX_SAFE_INTEGER },
  { key: "net", label: "Neto", className: "text-right", sort: (r) => r.net ?? -1 },
  { key: "stage", label: "Estado", sort: (r) => BOARD_COLUMNS.findIndex((c) => c.key === r.stage) },
  { key: "quote", label: "Cotización", sort: (r) => r.quote ?? "~" },
  { key: "sent", label: "Enviada", sort: (r) => time(r.sent) || 0 },
  { key: "last", label: "Último contacto", sort: (r) => time(r.last?.at) || 0 },
  { key: "product", label: "Producto", sort: (r) => (r.product ?? "~").toLocaleLowerCase("es") },
  { key: "contact", label: "Contacto", sort: (r) => (r.contact ?? "~").toLocaleLowerCase("es") },
  { key: "origin", label: "Origen", sort: (r) => r.origin ?? "~" },
];

/** The two frozen columns (Urgencia, Institución), as in a spreadsheet's frozen pane. */
const FROZEN: Partial<Record<Key, string>> = {
  urg: "sticky left-0 z-[1] w-[7.5rem] min-w-[7.5rem]",
  name: "sticky left-[7.5rem] z-[1] shadow-[1px_0_0] shadow-line-strong",
};

export function PipelineSheet({
  cards, urgency, showClosed, now, onOpen,
}: {
  cards: OpportunityCardData[];
  urgency: Map<string, CaseUrgency>;
  showClosed: boolean;
  now: Date;
  onOpen: (id: string) => void;
}) {
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: "urg", dir: 1 });
  const rows = useMemo(() => {
    const out: Row[] = cards
      .filter((c) => showClosed || urgency.get(c.opportunity_id)?.level !== "cerrado")
      .map((c) => {
        const u = urgency.get(c.opportunity_id);
        const m = cardMoney(c);
        return {
          card: c, u,
          name: displayName(c).name,
          contact: contactLine(c),
          product: quoteProduct(c),
          quote: m.cn ? `${m.cn}${m.rev && m.rev > 1 ? ` r${m.rev}` : ""}${c.quote_numbers.length > 1 ? ` +${c.quote_numbers.length - 1}` : ""}` : null,
          sent: c.latest_revision?.sent_at ?? null,
          stage: boardColumnOf(c, now),
          due: u?.step?.due ?? null,
          last: lastTouch(c),
          origin: c.origin ? originLabel(c.origin) : null,
          net: caseAmountClp(c),
          netText: m.text,
        };
      });
    const col = COLS.find((c) => c.key === sort.key)!;
    return out.sort((a, b) => {
      const x = col.sort(a), y = col.sort(b);
      const d = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "es");
      return d * sort.dir || (a.u?.rank ?? 0) - (b.u?.rank ?? 0);
    });
  }, [cards, urgency, showClosed, now, sort]);
  const total = rows.reduce((s, r) => s + (r.net ?? 0), 0);
  const { shown, hidden, showMore, endRef } = useGrowingList(rows);

  const th = (c: (typeof COLS)[number]) => {
    const on = sort.key === c.key;
    return (
      <th key={c.key} scope="col" aria-sort={on ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
        className={`sticky top-0 border-b border-r border-line-strong bg-canvas-sunken p-0 text-left ${FROZEN[c.key] ? `${FROZEN[c.key]} !z-20` : "z-10"} ${c.className ?? ""}`}>
        <button type="button" onClick={() => setSort(on ? { key: c.key, dir: sort.dir === 1 ? -1 : 1 } : { key: c.key, dir: c.key === "net" || c.key === "last" || c.key === "sent" ? -1 : 1 })}
          className={`flex w-full items-center gap-1 whitespace-nowrap px-2.5 py-2 text-[11px] font-bold uppercase tracking-wider hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${on ? "text-ink" : "text-ink-faint"} ${c.className?.includes("text-right") ? "justify-end" : ""}`}>
          {c.label}<span aria-hidden="true" className="w-2 text-[10px]">{on ? (sort.dir === 1 ? "▲" : "▼") : ""}</span>
        </button>
      </th>
    );
  };
  const cell = (key: Key, content: ReactNode, cls = "", title?: string) => (
    <td key={key} className={`border-b border-r border-line px-2.5 py-1.5 ${FROZEN[key] ? `${FROZEN[key]} bg-inherit` : ""} ${cls}`} title={title}>{content}</td>
  );
  const none = <span className="text-ink-faint">—</span>;
  const render = (r: Row): Record<Key, ReactNode> => {
    const overdue = r.due && r.u?.level === "atrasado";
    const step = r.u?.step ? stepAction(r.u.step) : r.card.next_action.text;
    return {
      urg: (
        <td key="urg" className={`${FROZEN.urg} whitespace-nowrap border-b border-l-4 border-r border-b-line border-r-line bg-inherit px-2.5 py-1.5 ${r.u ? URGENCY_EDGE[r.u.level] : "border-l-transparent"}`}>
          {r.u && r.u.level !== "cerrado" ? (
            <span className={`rounded px-1.5 py-px text-[11px] font-bold tabular-nums ${URGENCY_BADGE[r.u.level]}`}>{urgencyText(r.u)}</span>
          ) : none}
        </td>
      ),
      name: cell("name", <span className="block max-w-[15rem] truncate font-semibold text-ink">{r.name}</span>, "", r.name),
      step: cell("step", (
        <span className="flex max-w-[15rem] items-center gap-1.5">
          {r.u?.step ? <QueueIcon name={stepIcon(r.u.step)} /> : null}
          <span className="truncate">{step}</span>
        </span>
      ), "", step),
      due: cell("due", r.due ? shortDate(r.due.toISOString(), now) : none, `whitespace-nowrap tabular-nums ${overdue ? "font-semibold text-bad" : "text-ink-muted"}`),
      net: cell("net", r.netText || none, "whitespace-nowrap text-right font-semibold tabular-nums text-ink"),
      stage: cell("stage", COLUMN_NAME[r.stage], "whitespace-nowrap"),
      quote: cell("quote", r.quote ?? <span className="text-ink-faint">Sin cotización</span>, "whitespace-nowrap font-mono text-[12px] tabular-nums"),
      sent: cell("sent", r.sent ? shortDate(r.sent, now) : none, "whitespace-nowrap tabular-nums text-ink-muted"),
      last: cell("last", r.last ? <>{shortDate(r.last.at, now)} <span className="text-ink-faint">· {r.last.who}</span></> : none, "whitespace-nowrap tabular-nums text-ink-muted"),
      product: cell("product", <span className="block max-w-[13rem] truncate">{r.product ?? none}</span>, "text-ink-muted", r.product ?? undefined),
      contact: cell("contact", <span className="block max-w-[13rem] truncate">{r.contact ?? none}</span>, "text-ink-muted", r.contact ?? undefined),
      origin: cell("origin", r.origin ?? none, "whitespace-nowrap text-ink-muted"),
    };
  };

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-canvas-raised" data-testid="pipeline-list">
      <div className="max-h-[calc(100vh-15rem)] min-h-64 overflow-auto">
        <table className="w-max min-w-full border-separate border-spacing-0 text-[12.5px]">
          <thead><tr>{COLS.map(th)}</tr></thead>
          <tbody>
            {shown.map((r) => {
              const cells = render(r);
              return (
                <tr key={r.card.opportunity_id} onClick={() => onOpen(r.card.opportunity_id)}
                  className="cursor-pointer bg-canvas-raised transition-colors hover:bg-canvas-sunken"
                  data-testid={`pipeline-row-${r.card.opportunity_id}`}>
                  {COLS.map((c) => cells[c.key])}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="text-[12px] font-semibold">
              {COLS.map((c) => (
                <td key={c.key} className={`sticky bottom-0 whitespace-nowrap border-t border-line-strong bg-canvas-sunken px-2.5 py-2 ${FROZEN[c.key] ? `${FROZEN[c.key]} !z-20` : "z-10"} ${c.key === "net" ? "text-right tabular-nums text-ink" : "text-ink-muted"}`}
                  data-testid={c.key === "net" ? "pipeline-list-total" : undefined}>
                  {c.key === "urg" ? `${rows.length} casos` : c.key === "name" && hidden > 0 ? `mostrando ${shown.length}` : c.key === "net" ? formatMoney(String(total), "CLP") : null}
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
        <div className="p-2">
          <GrowingListEnd hidden={hidden} onMore={showMore} endRef={endRef} />
        </div>
      </div>
    </div>
  );
}
