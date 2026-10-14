/**
 * Hoy · «Hecho automáticamente» (owner, 2026-10-14: the system acts by itself, a person removes what
 * is wrong). What the automatic email rules did in the last two days: the requests they opened (a
 * client asking for a price, a forwarded request) and the emails they filed on a case (by thread or
 * by the quote number they name — a payment notice on its won case). Each row says why, opens the
 * case, and «Quitar» undoes it (sales or admin): a wrong request is closed as a correction, a wrong
 * filing is unlinked. Nothing renders when the system did nothing.
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

const fetchAutomatic = () =>
  fetchJsonGet<{ items: AutomaticAction[] }>(operatorApiUrl("/v2/workspace/automatic-actions"));

type Navigate = (s: CrmSection, id?: string | null) => void;

export function AutomaticActionsPanel({ navigate, onChanged }: { navigate: Navigate; onChanged: () => void }) {
  const load = useCallback(fetchAutomatic, []);
  const [state, reload] = useResource(load, [load]);
  const [gone, setGone] = useState<Set<string>>(new Set());
  const mayDecide = useMayRunCaseCommands();
  if (state.kind !== "ready" || !Array.isArray(state.data?.items)) return null;
  const items = state.data.items.filter((i) => !gone.has(i.receipt_id));
  if (items.length === 0) return null;
  const opened = items.filter((i) => i.kind === "opened").length;
  return (
    <Panel
      title="Hecho automáticamente"
      aside={<Badge tone="info" glyph={false}>{items.length}</Badge>}
      bodyClassName="divide-y divide-line"
    >
      <p className="px-4 pt-2 text-[11px] text-ink-muted">
        {opened ? `${opened} ${opened === 1 ? "solicitud abierta" : "solicitudes abiertas"} y ` : ""}
        correos archivados en su caso en los últimos dos días. Si algo no corresponde, «Quitar» lo deshace.
      </p>
      {items.map((it) => (
        <AutomaticRow key={it.receipt_id} item={it} mayDecide={mayDecide} navigate={navigate}
          onRemoved={() => { setGone((g) => new Set(g).add(it.receipt_id)); onChanged(); reload(); }} />
      ))}
    </Panel>
  );
}

function AutomaticRow({ item, mayDecide, navigate, onRemoved }: {
  item: AutomaticAction;
  mayDecide: boolean;
  navigate: Navigate;
  onRemoved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [key] = useState(newCaseCommandKey);
  const opened = item.kind === "opened";

  async function remove() {
    setBusy(true);
    try {
      await removeAutomaticAction({ receipt_id: item.receipt_id }, key);
      toast(opened ? "Solicitud quitada." : "Correo quitado del caso.");
      onRemoved();
    } catch (err) {
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="automatic-row" className="flex flex-wrap items-start gap-x-3 gap-y-1.5 px-4 py-2.5">
      <div className="min-w-[12rem] flex-1">
        <p className="flex flex-wrap items-center gap-x-2 text-[13px] font-medium text-ink">
          <Badge tone={opened ? "good" : "neutral"} glyph={false}>{opened ? "Solicitud abierta" : "Archivado en el caso"}</Badge>
          <span className="truncate">{item.what ?? item.case_title ?? "Correo"}</span>
        </p>
        <p className="mt-0.5 text-[11px] text-ink-muted">
          {item.case_title ? <>{opened ? "Caso" : "En"} «{item.case_title}» · </> : null}
          {item.applied_at ? `${fmtDate(item.applied_at)} · ` : ""}
          <span className="text-ink-faint">{item.reasons.join("; ")}</span>
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        {item.case_id ? (
          <Button variant="secondary" onClick={() => navigate("oportunidades", item.case_id)}>Abrir caso</Button>
        ) : null}
        {mayDecide ? (
          <Button variant="quiet" onClick={() => void remove()} busy={busy} busyLabel="Quitando…" disabled={busy}>
            Quitar
          </Button>
        ) : null}
      </div>
    </div>
  );
}
