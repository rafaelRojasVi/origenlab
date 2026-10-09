import { useId, useMemo, useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import {
  APPLY_BATCH,
  RULE_LABEL,
  applyMailRules,
  fetchMailRulesPreview,
  refusalMessage,
  setAutoMailRules,
  undoMailRuleAction,
  type AppliedMailAction,
  type AutoMailRulesState,
  type ApplyMailRulesResult,
  type MailRuleMode,
  type PlannedMailAction,
} from "../mailRules";
import { Badge, Button, EmptyState, Panel, ResourceGate, Skeleton, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";

const MODE: Record<MailRuleMode, { label: string; tone: "good" | "warn" | "neutral" }> = {
  auto: { label: "Automática", tone: "good" },
  proposal: { label: "Propuesta", tone: "warn" },
  none: { label: "Sin acción", tone: "neutral" },
};

const RULE_ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"];


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
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** The previewed automatic pairs, ten per request, in order; stops at the first failed request. */
  const apply = async (actions: PlannedMailAction[]) => {
    const pairs = actions.filter((a) => a.mode === "auto").map((a) => ({ evidence_id: a.evidence_id, rule_id: a.rule_id }));
    setBusy(true);
    setError(null);
    const total: ApplyMailRulesResult = { applied: [], refused: [] };
    setResult(total);
    setProgress({ done: 0, total: pairs.length });
    try {
      for (let i = 0; i < pairs.length; i += APPLY_BATCH) {
        const batch = await applyMailRules(pairs.slice(i, i + APPLY_BATCH));
        total.applied.push(...batch.applied);
        total.refused.push(...batch.refused);
        setResult({ applied: [...total.applied], refused: [...total.refused] });
        setProgress({ done: Math.min(i + APPLY_BATCH, pairs.length), total: pairs.length });
      }
    } catch (err) {
      setError(refusalMessage(err, "No se pudieron aplicar las acciones."));
    } finally {
      setBusy(false);
      reload();
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
                <Button
                  onClick={() => {
                    setShowPlan(true);
                    reload();
                  }}
                >
                  Vista previa
                </Button>
                <Button
                  variant="primary"
                  disabled={!p.commands_enabled || autoCount === 0}
                  busy={busy}
                  busyLabel="Aplicando…"
                  onClick={() => void apply(p.actions)}
                >
                  Aplicar{autoCount ? ` (${autoCount})` : ""}
                </Button>
              </div>
              {!p.commands_enabled ? (
                <p className="text-[11px] text-ink-faint">Aplicar no está habilitado en este entorno; la vista previa no escribe nada.</p>
              ) : null}
              {error ? (
                <p role="alert" className="text-xs text-bad">
                  {error}
                </p>
              ) : null}
              {progress ? (
                <p className="text-[11px] tabular-nums text-ink-muted">
                  {busy ? "Aplicando… " : ""}
                  {progress.done} de {progress.total} procesadas
                </p>
              ) : null}
              {result ? <ApplyResult result={result} /> : null}
            </Panel>
            {p.automatic ? <AutoSwitch state={p.automatic} onChanged={reload} /> : null}
            {showPlan ? <Plan actions={p.actions} /> : null}
            <Applied items={p.applied} onUndone={reload} />
          </div>
        );
      }}
    </ResourceGate>
  );
}

function lastRunLine(state: AutoMailRulesState): string {
  const run = state.last_run;
  if (!run) return "Todavía no ha pasado desde que se inició el servidor.";
  const when = fmtDate(run.at);
  if (run.skipped === "off") return `Última pasada ${when}: detenida, no hizo nada.`;
  if (run.skipped) return `Última pasada ${when}: no actuó.`;
  const parts = [`${run.applied} vinculado${run.applied === 1 ? "" : "s"}`];
  if (run.refused) parts.push(`${run.refused} rechazado${run.refused === 1 ? "" : "s"}`);
  if (run.pending) parts.push(`${run.pending} para la próxima pasada`);
  return `Última pasada ${when}: ${parts.join(" · ")}.`;
}

/**
 * The stop switch for the automatic run. On, every few minutes the server links each new email
 * that R1 (same Gmail thread) or R2 (same quote number) ties to exactly one open case, on behalf
 * of the admin who switched it on. Everything else still waits for «Aplicar». A note is required
 * both ways.
 */
function AutoSwitch({ state, onChanged }: { state: AutoMailRulesState; onChanged: () => void }) {
  const inputId = useId();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const acting = state.enabled && !state.blocked && state.timer_running;
  const target = !state.enabled || Boolean(state.blocked);
  const minutes = Math.max(1, Math.round(state.interval_seconds / 60));
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await setAutoMailRules(target, note.trim());
      toast(target ? "Vinculación automática activada." : "Vinculación automática detenida.");
      setOpen(false);
      setNote("");
      onChanged();
    } catch (err) {
      setError(refusalMessage(err, "No se pudo cambiar."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title="Vincular correos automáticamente"
      note={`${state.rules.join(" y ")} · cada ${minutes} min`}
      bodyClassName="space-y-2 px-3 py-3"
    >
      <div className="flex flex-wrap items-center gap-2" data-testid="auto-mail-rules">
        <Badge tone={acting ? "good" : state.enabled ? "warn" : "neutral"}>
          {acting ? "Activo" : state.enabled ? "Activo, sin actuar" : "Detenido"}
        </Badge>
        <span className="text-xs text-ink-muted">
          {state.changed_by
            ? `${state.enabled ? "Activado" : "Detenido"} por ${state.changed_by} · ${fmtDate(state.changed_at)}${state.note ? ` · «${state.note}»` : ""}`
            : "Nunca se ha activado."}
        </span>
        <span className="flex-1" />
        {open ? null : (
          <Button variant={target ? "primary" : "secondary"} onClick={() => setOpen(true)}>
            {target ? "Activar" : "Detener"}
          </Button>
        )}
      </div>
      <p className="text-xs text-ink-muted">
        Activo, el sistema vincula solo cada correo nuevo del mismo hilo de Gmail (R1) o con el número de una cotización
        (R2) de exactamente un caso abierto, a nombre de quien lo activó. Abrir casos, ganar o perder siguen esperando
        «Aplicar». Todo se puede deshacer abajo.
      </p>
      {state.blocked ? <p className="text-xs text-warn">{state.blocked}</p> : null}
      {!state.timer_running ? (
        <p className="text-[11px] text-ink-faint">El temporizador está apagado en el servidor; el interruptor no actuará.</p>
      ) : (
        <p className="text-[11px] text-ink-faint">{lastRunLine(state)}</p>
      )}
      {open ? (
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
          <Button
            variant={target ? "primary" : "danger"}
            disabled={!note.trim()}
            busy={busy}
            busyLabel={target ? "Activando…" : "Deteniendo…"}
            onClick={() => void submit()}
          >
            {target ? "Activar" : "Detener"}
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
    </Panel>
  );
}

function ApplyResult({ result }: { result: ApplyMailRulesResult }) {
  const n = result.applied.length;
  return (
    <div role="status" className="space-y-1 text-xs">
      <p className="text-good">
        {n} aplicada{n === 1 ? "" : "s"} · {result.refused.length} rechazada{result.refused.length === 1 ? "" : "s"}
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
    <div className="space-y-1 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge tone={item.undone ? "neutral" : "info"}>{item.rule_id ?? "—"}</Badge>
        <span className="font-medium text-ink">{item.case_title ?? item.organization_name ?? "—"}</span>
        {item.quote_number ? <span className="tabular-nums text-ink-muted">{item.quote_number}</span> : null}
        <span className="text-ink-faint">
          {item.label} · {fmtDate(item.applied_at)} ·{" "}
          {item.automatic ? `automática, a nombre de ${item.applied_by ?? "—"}` : `aplicada por ${item.applied_by ?? "—"}`}
        </span>
        <span className="flex-1" />
        {item.undone ? (
          <span className="text-ink-muted">
            deshecha por {item.undone_by ?? "—"} · {fmtDate(item.undone_at)}
          </span>
        ) : open ? null : (
          <Button onClick={() => setOpen(true)}>Deshacer</Button>
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
          <Button variant="primary" disabled={!note.trim()} busy={busy} busyLabel="Deshaciendo…" onClick={() => void undo()}>
            Confirmar
          </Button>
          <Button onClick={() => setOpen(false)} disabled={busy}>
            Cancelar
          </Button>
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
