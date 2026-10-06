import { useState } from "react";
import { WonFlowError, moveCase, stagePath } from "../caseCommands";
import { refusalMessage } from "../mailRules";
import {
  CLASS_LABEL,
  INTENT_LABEL,
  approvalMove,
  fetchTriageReadings,
  reviewTriage,
  stageLabel,
  type TriageCorrection,
  type TriageReading,
  type TriageStatus,
  type TriageVerdict,
} from "../triage";
import { Badge, Button, EmptyState, Panel, ResourceGate, Segmented, Skeleton, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";

const VERDICT: Record<TriageVerdict, { label: string; tone: "good" | "warn" | "bad" }> = {
  approved: { label: "Aprobada", tone: "good" },
  corrected: { label: "Corregida", tone: "warn" },
  rejected: { label: "Rechazada", tone: "bad" },
};

const DEFAULT_STAGES = ["lead", "qualifying", "qualified", "quoting", "negotiating", "won", "lost", "abandoned",
  "not_a_case", "unclear"];

/**
 * «Correos (sugerencias)»: what the mail triage suggested for each email a person wrote — class,
 * products, the case stage — and a verdict on each. «Aprobar» also moves the case to the suggested
 * stage (as «Cambiar estado» would); «Corregir» records the right answer (and moves the case when
 * a stage is chosen); «Rechazar» records that the suggestion was wrong. Every verdict is kept, with
 * its note, to improve the rules.
 */
export function TriagePanel() {
  const [status, setStatus] = useState<TriageStatus>("pending");
  const loader = () => fetchTriageReadings(status);
  const [state, reload] = useResource(loader, [status]);
  return (
    <div className="space-y-3">
      <Segmented
        label="Sugerencias"
        value={status}
        onChange={setStatus}
        options={[
          { value: "pending", label: "Por revisar", count: status === "pending" && state.kind === "ready" ? state.data.items.length : undefined },
          { value: "reviewed", label: "Revisadas" },
        ]}
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState title={status === "pending" ? "Nada por revisar" : "Aún no hay sugerencias revisadas"}>
              Aquí aparecen los correos de personas que el sistema leyó: qué pidieron, qué productos y en qué estado
              queda el caso.
            </EmptyState>
          ) : (
            <div className="space-y-2">
              {data.items.map((r) => (
                <ReadingCard key={r.assertion_id} reading={r} stages={data.vocabulary?.stages ?? DEFAULT_STAGES}
                  classes={data.vocabulary?.classes ?? Object.keys(CLASS_LABEL)}
                  intents={data.vocabulary?.intents ?? Object.keys(INTENT_LABEL)} onDone={reload} />
              ))}
            </div>
          )
        }
      </ResourceGate>
    </div>
  );
}

function ReadingCard({ reading: r, stages, classes, intents, onDone }: {
  reading: TriageReading;
  stages: string[];
  classes: string[];
  intents: string[];
  onDone: () => void;
}) {
  const [mode, setMode] = useState<"view" | "correct" | "reject">("view");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [fix, setFix] = useState<TriageCorrection>({});
  const [productsText, setProductsText] = useState("");
  const move = approvalMove(r);
  const onCase = r.cases.length === 1 ? r.cases[0] : null;
  const allowed = r.transitions.find((t) => t.opportunity_id === onCase?.opportunity_id)?.transition_allowed;

  async function submit(verdict: TriageVerdict) {
    setBusy(true);
    const corrected: TriageCorrection = { ...fix };
    if (verdict === "corrected" && productsText.trim()) corrected.products = parseProducts(productsText);
    try {
      await reviewTriage({ assertion_id: r.assertion_id, verdict, corrected, note });
      const target = verdict === "approved" ? move?.to : verdict === "corrected" ? corrected.stage : undefined;
      if (target && onCase && stagePath(onCase.stage, target)) {
        const reason = note.trim() || "Sugerencia del correo revisada";
        await moveCase(onCase, target, reason, target === "lost" || target === "abandoned" ? reason : null);
        toast(`${VERDICT[verdict].label}; el caso quedó en «${stageLabel(target)}».`);
      } else {
        toast(`${VERDICT[verdict].label}.`);
      }
      onDone();
    } catch (err) {
      if (err instanceof WonFlowError) {
        toast(`La sugerencia quedó registrada, pero el caso no se movió: ${refusalMessage(err.cause, "rechazado")}`, "bad");
        onDone();
      } else {
        toast(refusalMessage(err, "No se pudo registrar la revisión."), "bad");
      }
    } finally {
      setBusy(false);
    }
  }

  const correctionEmpty = !fix.class && !fix.stage && !fix.intent && !productsText.trim();
  return (
    <Panel
      title={r.subject || "(sin asunto)"}
      note={`${r.sender ?? "—"} · ${fmtDate(r.sent_at)}`}
      aside={r.review ? <Badge tone={VERDICT[r.review.verdict].tone}>{VERDICT[r.review.verdict].label}</Badge> : null}
      bodyClassName="space-y-2 px-3 py-2 text-xs"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="info">{CLASS_LABEL[r.class ?? ""] ?? r.class ?? "—"}</Badge>
        {r.intent ? <Badge>{INTENT_LABEL[r.intent] ?? r.intent}</Badge> : null}
        {r.urgency === "high" ? <Badge tone="bad">Urgente</Badge> : null}
        {r.needs_reply ? <Badge tone="warn">Necesita respuesta</Badge> : null}
        {r.model_state && r.model_state !== "ran" ? <Badge>Sólo reglas ({r.model_state})</Badge> : null}
      </div>
      {r.summary_es ? <p className="text-ink">{r.summary_es}</p> : null}
      <p className="text-ink-muted">
        Estado sugerido: <strong className="text-ink">{stageLabel(r.stage)}</strong>
        {onCase ? (
          <>
            {" "}· caso «{onCase.title ?? "sin título"}» hoy en <strong>{stageLabel(onCase.stage)}</strong>
            {allowed === false ? <span className="text-bad"> (ese cambio no es un paso permitido)</span> : null}
          </>
        ) : r.cases.length > 1 ? (
          <> · el hilo está en {r.cases.length} casos: no se mueve ninguno</>
        ) : (
          <> · el hilo no está en ningún caso</>
        )}
      </p>
      {r.products.length > 0 ? (
        <ul className="list-disc pl-4 text-ink-muted">
          {r.products.map((p, i) => (
            <li key={i}>
              {p.quantity ? `${p.quantity} × ` : ""}
              {p.description}
              {p.model ? ` (${p.model})` : ""}
              {p.catalog_product_id ? <Badge tone="good">en catálogo</Badge> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {r.review?.note ? <p className="text-ink-faint">Nota: {r.review.note} — {r.review.reviewed_by ?? ""}</p> : null}

      {mode === "correct" ? (
        <div className="grid gap-2 sm:grid-cols-3">
          <Select label="Clase" value={fix.class} options={classes} labels={CLASS_LABEL}
            onChange={(v) => setFix({ ...fix, class: v })} />
          <Select label="Estado" value={fix.stage} options={stages} labelOf={stageLabel}
            onChange={(v) => setFix({ ...fix, stage: v })} />
          <Select label="Qué quiere" value={fix.intent} options={intents} labels={INTENT_LABEL}
            onChange={(v) => setFix({ ...fix, intent: v })} />
          <label className="sm:col-span-3">
            <span className="text-ink-muted">Productos correctos (uno por línea, «2 × balanza analítica»; vacío = sin cambio)</span>
            <textarea className="mt-1 w-full rounded border border-line bg-canvas p-1" rows={2} value={productsText}
              onChange={(e) => setProductsText(e.target.value)} />
          </label>
        </div>
      ) : null}
      {mode !== "view" || r.review === null ? (
        <label className="block">
          <span className="text-ink-muted">Nota (opcional): por qué, para mejorar las reglas</span>
          <input className="mt-1 w-full rounded border border-line bg-canvas p-1" value={note} maxLength={2000}
            onChange={(e) => setNote(e.target.value)} />
        </label>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {mode === "view" ? (
          <>
            <Button variant="primary" busy={busy} onClick={() => submit("approved")}>
              {move ? `Aprobar y pasar a «${stageLabel(move.to)}»` : "Aprobar"}
            </Button>
            <Button busy={busy} onClick={() => setMode("correct")}>Corregir</Button>
            <Button variant="danger" busy={busy} onClick={() => setMode("reject")}>Rechazar</Button>
          </>
        ) : mode === "correct" ? (
          <>
            <Button variant="primary" busy={busy} disabled={correctionEmpty} onClick={() => submit("corrected")}>
              Guardar corrección{fix.stage && onCase && stagePath(onCase.stage, fix.stage) ? ` y pasar a «${stageLabel(fix.stage)}»` : ""}
            </Button>
            <Button variant="quiet" onClick={() => setMode("view")}>Cancelar</Button>
          </>
        ) : (
          <>
            <Button variant="danger" busy={busy} onClick={() => submit("rejected")}>Confirmar rechazo</Button>
            <Button variant="quiet" onClick={() => setMode("view")}>Cancelar</Button>
          </>
        )}
      </div>
    </Panel>
  );
}

function Select({ label, value, options, labels, labelOf, onChange }: {
  label: string;
  value: string | undefined;
  options: string[];
  labels?: Record<string, string>;
  labelOf?: (v: string) => string;
  onChange: (v: string | undefined) => void;
}) {
  return (
    <label>
      <span className="text-ink-muted">{label}</span>
      <select className="mt-1 w-full rounded border border-line bg-canvas p-1" value={value ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">Sin cambio</option>
        {options.map((o) => (
          <option key={o} value={o}>{labelOf ? labelOf(o) : labels?.[o] ?? o}</option>
        ))}
      </select>
    </label>
  );
}

/** «2 × balanza analítica» / «balanza» → products; blank lines skipped. */
export function parseProducts(text: string): NonNullable<TriageCorrection["products"]> {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 20)
    .map((line) => {
      const m = line.match(/^(\d{1,6})\s*[x×*]\s*(.+)$/i);
      return {
        description: (m ? m[2] : line).slice(0, 300),
        model: null,
        quantity: m ? Number(m[1]) : null,
        catalog_product_id: null,
      };
    });
}
