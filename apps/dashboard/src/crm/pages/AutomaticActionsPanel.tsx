/**
 * Hoy · «Hecho automáticamente» (owner, 2026-10-14: the system acts by itself, a person removes what
 * is wrong). Only what is news: the requests the automatic email rules opened and the emails they
 * filed on a case by a quote number, a forward or the context matcher — not a reply filed on a thread
 * already in a case (R1), which is the conversation going on and never needed a person (owner,
 * 2026-10-15: «that's so much»). Emails filed on one case by one rule are one row («3 correos
 * archivados»); five rows show, the rest behind «Ver N más». Each row says why, opens the case, and
 * «Quitar» undoes it — every email of a grouped row (sales or admin), except a Cotizador quote marked
 * sent (R16), which is final: a wrong one is voided in the Cotizador. Nothing renders when there is
 * no news.
 */
import { useCallback, useState } from "react";
import { fetchJsonGet, operatorApiUrl } from "../../api/operatorClient";
import { caseRefusalText, newCaseCommandKey, removeAutomaticAction, useMayRunCaseCommands } from "../caseCommands";
import type { CrmSection } from "../crmRoute";
import { Badge, Button, Panel, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";

export interface AutomaticAction {
  receipt_id: string;
  applied_at: string | null;
  rule_id: string | null;
  kind: "opened" | "filed";
  source: "email" | "forward";
  reasons: string[];
  case_id: string | null;
  case_title: string | null;
  quote_number: string | null;
  what: string | null;
}

/** Rules whose filings are routine — a reply on a thread already in a case — and stay off Hoy. */
const ROUTINE_RULES = new Set(["R1"]);
const ROWS_SHOWN = 5;

interface Row {
  key: string;
  items: AutomaticAction[];
}

/** One row per opened request; one row per (case, rule) for the emails filed there. Newest first. */
export function newsRows(items: AutomaticAction[]): Row[] {
  const rows: Row[] = [];
  const filed = new Map<string, Row>();
  for (const it of items) {
    if (ROUTINE_RULES.has(it.rule_id ?? "")) continue;
    if (it.kind === "filed" && it.case_id) {
      const key = `${it.case_id}|${it.rule_id}`;
      const row = filed.get(key);
      if (row) {
        row.items.push(it);
        continue;
      }
      const fresh = { key, items: [it] };
      filed.set(key, fresh);
      rows.push(fresh);
    } else {
      rows.push({ key: it.receipt_id, items: [it] });
    }
  }
  return rows;
}

const fetchAutomatic = () =>
  fetchJsonGet<{ items: AutomaticAction[] }>(operatorApiUrl("/v2/workspace/automatic-actions"));

type Navigate = (s: CrmSection, id?: string | null) => void;

export function AutomaticActionsPanel({ navigate, onChanged }: { navigate: Navigate; onChanged: () => void }) {
  const load = useCallback(fetchAutomatic, []);
  const [state, reload] = useResource(load, [load]);
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [all, setAll] = useState(false);
  const mayDecide = useMayRunCaseCommands();
  if (state.kind !== "ready" || !Array.isArray(state.data?.items)) return null;
  const rows = newsRows(state.data.items.filter((i) => !gone.has(i.receipt_id)));
  if (rows.length === 0) return null;
  const opened = rows.filter((r) => r.items[0].kind === "opened").length;
  const filed = rows.reduce((n, r) => n + (r.items[0].kind === "filed" ? r.items.length : 0), 0);
  const summary = [
    opened ? `${opened} ${opened === 1 ? "solicitud abierta" : "solicitudes abiertas"}` : null,
    filed ? `${filed} ${filed === 1 ? "correo archivado en su caso" : "correos archivados en su caso"}` : null,
  ].filter(Boolean).join(" y ");
  const shown = all ? rows : rows.slice(0, ROWS_SHOWN);
  return (
    <Panel
      title="Hecho automáticamente"
      aside={<Badge tone="info" glyph={false}>{rows.length}</Badge>}
      bodyClassName="divide-y divide-line"
    >
      <p className="px-4 pt-2 text-[11px] text-ink-muted">
        {summary} en los últimos dos días. Si algo no corresponde, «Quitar» lo deshace.
      </p>
      {shown.map((row) => (
        <AutomaticRow key={row.key} items={row.items} mayDecide={mayDecide} navigate={navigate}
          onRemoved={() => {
            setGone((g) => new Set([...g, ...row.items.map((i) => i.receipt_id)]));
            onChanged();
            reload();
          }} />
      ))}
      {rows.length > ROWS_SHOWN ? (
        <div className="px-4 py-2">
          <button type="button" onClick={() => setAll(!all)} aria-expanded={all}
                  className="text-xs font-medium text-brand-700 hover:underline">
            {all ? "Ver menos" : `Ver ${rows.length - ROWS_SHOWN} más`}
          </button>
        </div>
      ) : null}
    </Panel>
  );
}

function AutomaticRow({ items, mayDecide, navigate, onRemoved }: {
  items: AutomaticAction[];
  mayDecide: boolean;
  navigate: Navigate;
  onRemoved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [keys] = useState(() => new Map(items.map((i) => [i.receipt_id, newCaseCommandKey()])));
  const item = items[0];
  const opened = item.kind === "opened";
  const many = items.length > 1;
  const sent = item.rule_id === "R16";

  async function remove() {
    setBusy(true);
    try {
      for (const it of items) {
        await removeAutomaticAction({ receipt_id: it.receipt_id }, keys.get(it.receipt_id) ?? newCaseCommandKey());
      }
      toast(opened ? "Solicitud quitada." : many ? "Correos quitados del caso." : "Correo quitado del caso.");
      onRemoved();
    } catch (err) {
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(false);
    }
  }

  const label = sent ? "Cotización enviada" : opened ? "Solicitud abierta"
    : many ? `${items.length} correos archivados` : "Archivado en el caso";
  return (
    <div data-testid="automatic-row" className="flex flex-wrap items-start gap-x-3 gap-y-1.5 px-4 py-2.5">
      <div className="min-w-[12rem] flex-1">
        <p className="flex flex-wrap items-center gap-x-2 text-[13px] font-medium text-ink">
          <Badge tone={opened ? "good" : "neutral"} glyph={false}>{label}</Badge>
          <span className="truncate" title={many ? items.map((i) => i.what).join(" · ") : undefined}>
            {item.what ?? item.case_title ?? "Correo"}
            {many ? <span className="font-normal text-ink-muted"> y {items.length - 1} más</span> : null}
          </span>
        </p>
        <p className="mt-0.5 text-[11px] text-ink-muted">
          {item.case_title ? <>{opened ? "Caso" : "En"} «{item.case_title}» · </> : null}
          {item.applied_at ? fmtDate(item.applied_at) : ""}
        </p>
        <p className="line-clamp-2 text-[11px] text-ink-faint" title={item.reasons.join("; ")}>
          Por qué: {item.reasons.join("; ")}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        {item.case_id ? (
          <Button variant="secondary" onClick={() => navigate("oportunidades", item.case_id)}>Abrir caso</Button>
        ) : null}
        {mayDecide && !sent ? (
          <Button variant="quiet" onClick={() => void remove()} busy={busy} busyLabel="Quitando…" disabled={busy}>
            Quitar
          </Button>
        ) : null}
      </div>
    </div>
  );
}
