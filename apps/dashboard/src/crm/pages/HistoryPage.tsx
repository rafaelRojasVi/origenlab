import { useId, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { fetchHistory } from "../crmApi";
import type { HistoryItem } from "../crmTypes";
import type { CrmSection } from "../crmRoute";
import { refusalMessage, undoMailRuleAction } from "../mailRules";
import { Button, EmptyState, PageHeader, ResourceGate, Skeleton, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";

/**
 * «Historial»: what was decided in the CRM, newest first — who, what, on which case or
 * institution, when. An automatic mail action can be undone here (admin, with a reason); any
 * other decision is changed from its own case.
 */
export function HistoryPage({ navigate }: { navigate: (s: CrmSection, id?: string | null) => void }) {
  const [state, reload] = useResource(fetchHistory);
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";
  return (
    <div className="space-y-3">
      <PageHeader title="Historial" subtitle="Lo que se decidió en el CRM, lo más reciente primero." />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={8} />}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState title="Todavía no hay decisiones registradas" />
          ) : (
            <ol className="divide-y divide-line rounded-xl border border-line bg-canvas-raised">
              {data.items.map((item) => (
                <HistoryRow key={item.receipt_id} item={item} isAdmin={isAdmin} navigate={navigate} onUndone={reload} />
              ))}
            </ol>
          )
        }
      </ResourceGate>
    </div>
  );
}

function HistoryRow({ item, isAdmin, navigate, onUndone }: {
  item: HistoryItem;
  isAdmin: boolean;
  navigate: (s: CrmSection, id?: string | null) => void;
  onUndone: () => void;
}) {
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const undo = async () => {
    if (!item.undo) return;
    setBusy(true);
    setError(null);
    try {
      await undoMailRuleAction(item.undo.receipt_id, note.trim());
      toast("Acción deshecha.");
      setOpen(false);
      onUndone();
    } catch (err) {
      setError(refusalMessage(err, "No se pudo deshacer."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="space-y-1 px-4 py-3" data-testid="history-row">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
        <span className="font-semibold text-ink">{item.operator}</span>
        <span className="text-ink">{item.action}</span>
        {item.case ? (
          <>
            <span className="text-ink-faint" aria-hidden="true">·</span>
            <button
              type="button"
              onClick={() => navigate("oportunidades", item.case!.opportunity_id)}
              className="font-medium text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            >
              {item.case.title}
            </button>
          </>
        ) : item.organization ? (
          <>
            <span className="text-ink-faint" aria-hidden="true">·</span>
            <span className="font-medium text-ink">{item.organization.name}</span>
          </>
        ) : null}
        <span className="flex-1" />
        <span className="text-xs tabular-nums text-ink-muted">{fmtDate(item.at)}</span>
        {item.undone_at ? (
          <span className="text-xs text-ink-muted">Deshecha · {fmtDate(item.undone_at)}</span>
        ) : item.undo && isAdmin && !open ? (
          <Button onClick={() => setOpen(true)}>Deshacer</Button>
        ) : null}
      </div>
      {open && item.undo ? (
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={inputId} className="text-[11px] font-medium text-ink">
            Motivo
          </label>
          <input
            id={inputId}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="h-8 min-w-0 flex-1 rounded-md border border-line bg-canvas-raised px-2 text-[13px] text-ink focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20"
          />
          <Button variant="primary" disabled={!note.trim()} busy={busy} busyLabel="Deshaciendo…" onClick={() => void undo()}>
            Confirmar
          </Button>
          <Button onClick={() => setOpen(false)} disabled={busy}>
            Cancelar
          </Button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-xs text-bad">
          {error}
        </p>
      ) : null}
    </li>
  );
}
