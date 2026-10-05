import { useId, useMemo, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import {
  RULE_LABEL,
  applyMailRules,
  fetchMailRulesPreview,
  refusalMessage,
  undoMailRuleAction,
  type AppliedMailAction,
  type ApplyMailRulesResult,
  type MailRuleMode,
  type PlannedMailAction,
} from "../mailRules";
import { Badge, EmptyState, Panel, ResourceGate, Skeleton, fmtDate } from "../ui";
import { useResource } from "../useResource";

const MODE: Record<MailRuleMode, { label: string; tone: "good" | "warn" | "neutral" }> = {
  auto: { label: "Automática", tone: "good" },
  proposal: { label: "Propuesta", tone: "warn" },
  none: { label: "Sin acción", tone: "neutral" },
};

const RULE_ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"];

const BUTTON =
  "h-8 shrink-0 rounded-md border border-line px-3 text-xs font-medium text-ink hover:bg-canvas-sunken disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY =
  "h-8 shrink-0 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50";

/**
 * «Acciones automáticas del correo»: the email → cases rules' dry run, «Aplicar», and the applied
 * actions with «Deshacer». Admin only — for anyone else it renders nothing (the API refuses them
 * too).
 */
export function MailRulesPanel() {
  const { session } = useAuthSession();
  const isAdmin = session.kind === "signed_in" && session.operator.role === "admin";
  if (!isAdmin) return null;
  return <AdminPanel />;
}

function AdminPanel() {
  const [preview, reload] = useResource(fetchMailRulesPreview);
  const [showPlan, setShowPlan] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ApplyMailRulesResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const apply = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await applyMailRules());
      reload();
    } catch (err) {
      setError(refusalMessage(err, "No se pudieron aplicar las acciones."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResourceGate state={preview} reload={reload} skeleton={<Skeleton rows={4} />}>
      {(p) => {
        const autoCount = p.actions.filter((a) => a.mode === "auto").length;
        return (
          <div className="space-y-3">
            <Panel
              title="Acciones automáticas del correo"
              note={`${p.label} · ${p.emails_considered} correos leídos · ${p.already_applied} ya procesados`}
              bodyClassName="space-y-2 px-3 py-3"
            >
              <p className="text-xs text-ink-muted">
                Las reglas leen cada correo capturado y, cuando la coincidencia es segura, vinculan el correo, abren el caso o
                cambian su etapa. Cada acción queda como «{p.label}» con sus motivos y se puede deshacer.
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className={BUTTON}
                  onClick={() => {
                    setShowPlan(true);
                    reload();
                  }}
                >
                  Vista previa
                </button>
                <button type="button" className={PRIMARY} disabled={!p.commands_enabled || busy || autoCount === 0} onClick={() => void apply()}>
                  Aplicar{autoCount ? ` (${autoCount})` : ""}
                </button>
              </div>
              {!p.commands_enabled ? (
                <p className="text-[11px] text-ink-faint">Aplicar no está habilitado en este entorno; la vista previa no escribe nada.</p>
              ) : null}
              {error ? (
                <p role="alert" className="text-xs text-bad">
                  {error}
                </p>
              ) : null}
              {result ? <ApplyResult result={result} /> : null}
            </Panel>
            {showPlan ? <Plan actions={p.actions} /> : null}
            <Applied items={p.applied} onUndone={reload} />
          </div>
        );
      }}
    </ResourceGate>
  );
}

function ApplyResult({ result }: { result: ApplyMailRulesResult }) {
  const n = result.applied.length;
  return (
    <div role="status" className="space-y-1 text-xs">
      <p className="text-good">
        {n} aplicada{n === 1 ? "" : "s"} · {result.refused.length} rechazada{result.refused.length === 1 ? "" : "s"} ·{" "}
        {result.proposals_left_for_review} propuesta{result.proposals_left_for_review === 1 ? "" : "s"} para Revisión
      </p>
      {result.refused.map((r) => (
        <p key={`${r.evidence_id}-${r.rule_id}`} className="text-bad">
          {r.rule_id}: {r.message} <span className="text-ink-faint">({r.code})</span>
        </p>
      ))}
    </div>
  );
}

function Plan({ actions }: { actions: PlannedMailAction[] }) {
  const groups = useMemo(() => {
    const m = new Map<string, PlannedMailAction[]>();
    for (const a of actions) m.set(a.rule_id, [...(m.get(a.rule_id) ?? []), a]);
    return [...m.entries()].sort(([a], [b]) => RULE_ORDER.indexOf(a) - RULE_ORDER.indexOf(b));
  }, [actions]);
  if (actions.length === 0) {
    return <EmptyState title="Nada que hacer">Ningún correo nuevo coincide con una regla.</EmptyState>;
  }
  return (
    <Panel title="Vista previa" note="Lo que harían las reglas ahora; nada se escribe hasta «Aplicar»" bodyClassName="divide-y divide-line">
      {groups.map(([rule, rows]) => (
        <div key={rule} className="px-3 py-2">
          <p className="text-[13px] font-semibold text-ink">
            {rule} · {RULE_LABEL[rule] ?? rule} <span className="font-normal tabular-nums text-ink-muted">· {rows.length}</span>
          </p>
          <ul className="mt-1 space-y-1.5">
            {rows.map((a) => (
              <li key={a.evidence_id} className="text-xs">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <Badge tone={MODE[a.mode].tone}>{MODE[a.mode].label}</Badge>
                  {a.case_title ? <span className="font-medium text-ink">{a.case_title}</span> : null}
                  {a.organization_name && !a.case_title ? <span className="text-ink">{a.organization_name}</span> : null}
                  {a.quote_number ? <span className="tabular-nums text-ink-muted">{a.quote_number}</span> : null}
                  {a.subject ? <span className="min-w-0 truncate text-ink-faint">«{a.subject}»</span> : null}
                  {a.sent_at ? <span className="text-ink-faint">{fmtDate(a.sent_at)}</span> : null}
                </div>
                <ul className="ml-1 list-disc pl-4 text-[11px] text-ink-muted">
                  {a.reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Panel>
  );
}

function Applied({ items, onUndone }: { items: AppliedMailAction[]; onUndone: () => void }) {
  return (
    <Panel title="Acciones aplicadas" note="Las más recientes primero" bodyClassName="divide-y divide-line">
      {items.length === 0 ? (
        <p className="px-3 py-3 text-xs text-ink-muted">Ninguna todavía.</p>
      ) : (
        items.map((a) => <AppliedRow key={a.receipt_id} item={a} onUndone={onUndone} />)
      )}
    </Panel>
  );
}

function AppliedRow({ item, onUndone }: { item: AppliedMailAction; onUndone: () => void }) {
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const undo = async () => {
    setBusy(true);
    setError(null);
    try {
      await undoMailRuleAction(item.receipt_id, note.trim());
      setOpen(false);
      onUndone();
    } catch (err) {
      setError(refusalMessage(err, "No se pudo deshacer."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-1 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge tone={item.undone ? "neutral" : "info"}>{item.rule_id ?? "—"}</Badge>
        <span className="font-medium text-ink">{item.case_title ?? item.organization_name ?? "—"}</span>
        {item.quote_number ? <span className="tabular-nums text-ink-muted">{item.quote_number}</span> : null}
        <span className="text-ink-faint">
          {item.label} · {fmtDate(item.applied_at)} · aplicada por {item.applied_by ?? "—"}
        </span>
        <span className="flex-1" />
        {item.undone ? (
          <span className="text-ink-muted">
            deshecha por {item.undone_by ?? "—"} · {fmtDate(item.undone_at)}
          </span>
        ) : open ? null : (
          <button type="button" className={BUTTON} onClick={() => setOpen(true)}>
            Deshacer
          </button>
        )}
      </div>
      {item.reasons?.length ? <p className="text-[11px] text-ink-muted">{item.reasons.join(" · ")}</p> : null}
      {open && !item.undone ? (
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
          <button type="button" className={PRIMARY} disabled={busy || !note.trim()} onClick={() => void undo()}>
            Confirmar
          </button>
          <button type="button" className={BUTTON} onClick={() => setOpen(false)}>
            Cancelar
          </button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-bad">
          {error}
        </p>
      ) : null}
    </div>
  );
}
