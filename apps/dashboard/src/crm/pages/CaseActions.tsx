/**
 * The case drawer's «Acciones»: change stage, mark won, register a follow-up, confirm the
 * institution. Every write goes through its owning client (`caseCommands.ts` for the two case
 * commands, `crmAuthoringApi.ts` for the institution and the note) and is followed by a refetch
 * of the pipeline, so stage, version and notes on screen are what the API recorded.
 *
 * Enabled only when the session says the commands are mounted and the role may decide
 * (`sales`/`admin`); otherwise each action stays a `DisabledAction` with its reason. The API
 * remains the authority: a refusal is shown in Spanish, never swallowed.
 */
import { useRef, useState, type FormEvent } from "react";
import { confirmOrganizationRecord } from "../authoring/crmAuthoringApi";
import { useMayAuthorCrm } from "../authoring/authoring";
import {
  CLOSING_STAGES,
  TERMINAL_STAGES,
  advanceCaseStage,
  caseRefusalText,
  isStaleRefusal,
  markCaseWon,
  mayBeWonFrom,
  newCaseCommandKey,
  nextStages,
  useMayRunCaseCommands,
  winnableRevisions,
  WonFlowError,
  type WonStep,
} from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { crmHash } from "../crmRoute";
import { STAGE_LABEL } from "../stage";
import { DisabledAction, FormField, SelectInput, TextareaInput, WRITE_DISABLED_REASON, fmtDate } from "../ui";

const PRIMARY =
  "h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50";
const SECONDARY =
  "h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken disabled:cursor-not-allowed disabled:opacity-50";

type Mode = null | "stage" | "won";

interface Outcome {
  tone: "good" | "bad" | "warn";
  lines: string[];
}

export function CaseActions({
  card,
  onChanged,
  onFollowUp,
}: {
  card: OpportunityCardData;
  /** Refetch the pipeline after any recorded write, or a refusal that says the view is stale. */
  onChanged: () => void;
  /** Open the note form of the drawer's note list. */
  onFollowUp: () => void;
}) {
  const mayDecide = useMayRunCaseCommands();
  const mayAuthor = useMayAuthorCrm();
  const [mode, setMode] = useState<Mode>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const closed = TERMINAL_STAGES.has(card.stage) || card.closed_at != null;
  const hasVersion = typeof card.version === "number";
  const stages = nextStages(card.stage);
  const revisions = winnableRevisions(card);

  const decideReason = !mayDecide
    ? WRITE_DISABLED_REASON
    : !hasVersion
      ? "El API no informó la versión del caso"
      : closed
        ? "El caso está cerrado"
        : null;
  const wonReason =
    decideReason ??
    (!mayBeWonFrom(card.stage)
      ? "Se marca ganada desde «Cotizando» o «Negociando»"
      : revisions.length === 0
        ? "No hay una revisión enviada y vigente"
        : null);

  /** Show what happened; close the form unless it was refused; refetch when anything moved. */
  function finished(next: Outcome, refetch: boolean) {
    setOutcome(next);
    if (next.tone !== "bad") setMode(null);
    if (refetch) onChanged();
  }

  return (
    <div className="space-y-3" data-testid="case-actions">
      <div className="flex flex-wrap gap-2">
        {decideReason ? (
          <DisabledAction id="drawer-advance" reason={decideReason}>
            Cambiar etapa
          </DisabledAction>
        ) : (
          <button type="button" className={SECONDARY} aria-expanded={mode === "stage"} onClick={() => { setOutcome(null); setMode(mode === "stage" ? null : "stage"); }}>
            Cambiar etapa
          </button>
        )}
        {wonReason ? (
          <DisabledAction id="drawer-won" reason={wonReason}>
            Marcar ganada
          </DisabledAction>
        ) : (
          <button type="button" className={SECONDARY} aria-expanded={mode === "won"} onClick={() => { setOutcome(null); setMode(mode === "won" ? null : "won"); }}>
            Marcar ganada
          </button>
        )}
        {mayAuthor ? (
          <button type="button" className={SECONDARY} onClick={onFollowUp}>
            Registrar seguimiento
          </button>
        ) : (
          <DisabledAction id="drawer-followup" reason={WRITE_DISABLED_REASON}>
            Registrar seguimiento
          </DisabledAction>
        )}
        <DisabledAction id="drawer-revision" reason={WRITE_DISABLED_REASON}>
          Nueva revisión
        </DisabledAction>
      </div>

      {card.organization?.confirmation === "machine_proposed" ? (
        <ConfirmInstitution card={card} mayAuthor={mayAuthor} onDone={finished} />
      ) : null}

      {mode === "stage" && hasVersion ? (
        <StageForm card={card} stages={stages} onCancel={() => setMode(null)} onDone={finished} />
      ) : null}
      {mode === "won" && hasVersion ? (
        <WonForm card={card} revisions={revisions} onCancel={() => setMode(null)} onDone={finished} />
      ) : null}

      {outcome ? (
        <div
          role={outcome.tone === "bad" ? "alert" : "status"}
          className={`rounded-md border px-3 py-2 text-xs ${
            outcome.tone === "good"
              ? "border-good/40 bg-good-bg text-good"
              : outcome.tone === "warn"
                ? "border-warn/40 bg-warn-bg text-warn"
                : "border-bad/40 bg-bad-bg text-bad"
          }`}
          data-testid="case-action-outcome"
        >
          {outcome.lines.map((l, i) => (
            <p key={i}>{l}</p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function receiptLine(step: WonStep, n: number, total: number): string {
  const replay = step.receipt.replayed ? " (ya estaba registrado)" : "";
  return `${total > 1 ? `${n}/${total} · ` : ""}${step.label}: registrado${replay} · recibo ${step.receipt.command_receipt_id.slice(0, 8)}`;
}

function StageForm({
  card,
  stages,
  onCancel,
  onDone,
}: {
  card: OpportunityCardData;
  stages: string[];
  onCancel: () => void;
  onDone: (o: Outcome, refetch: boolean) => void;
}) {
  const [stage, setStage] = useState(stages[0] ?? "");
  const [closeReason, setCloseReason] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const keyRef = useRef(newCaseCommandKey());
  const closing = CLOSING_STAGES.has(stage);
  const ready = stage !== "" && note.trim() !== "" && (!closing || closeReason.trim() !== "") && !busy;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    try {
      const receipt = await advanceCaseStage(
        {
          opportunity_id: card.opportunity_id,
          opportunity_version: card.version as number,
          stage,
          close_reason: closing ? closeReason.trim() : null,
          note: note.trim(),
        },
        keyRef.current,
      );
      onDone(
        {
          tone: "good",
          lines: [receiptLine({ label: `Etapa → ${STAGE_LABEL[receipt.stage] ?? receipt.stage}`, receipt }, 1, 1)],
        },
        true,
      );
    } catch (err) {
      keyRef.current = newCaseCommandKey();
      onDone({ tone: "bad", lines: [caseRefusalText(err)] }, isStaleRefusal(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-2 rounded-md border border-line p-3" aria-label="Cambiar etapa">
      <p className="text-[11px] text-ink-muted">
        Etapa actual: <strong className="text-ink">{STAGE_LABEL[card.stage] ?? card.stage}</strong>. Sólo se ofrecen los cambios
        permitidos desde ella.
      </p>
      <FormField label="Nueva etapa" htmlFor="case-stage" required>
        <SelectInput
          id="case-stage"
          value={stage}
          onChange={setStage}
          options={stages.map((s) => ({ value: s, label: STAGE_LABEL[s] ?? s }))}
        />
      </FormField>
      {closing ? (
        <FormField label="Motivo de cierre" htmlFor="case-close-reason" required hint="Cerrar un caso necesita un motivo.">
          <TextareaInput id="case-close-reason" value={closeReason} onChange={setCloseReason} maxLength={2000} rows={2} />
        </FormField>
      ) : null}
      <FormField label="Nota" htmlFor="case-stage-note" required hint="Por qué cambia la etapa; queda en la auditoría del caso.">
        <TextareaInput id="case-stage-note" value={note} onChange={setNote} maxLength={2000} rows={2} />
      </FormField>
      <div className="flex justify-end gap-2">
        <button type="button" className={SECONDARY} onClick={onCancel} disabled={busy}>
          Cancelar
        </button>
        <button type="submit" className={PRIMARY} disabled={!ready}>
          {busy ? "Registrando…" : "Cambiar etapa"}
        </button>
      </div>
    </form>
  );
}

function WonForm({
  card,
  revisions,
  onCancel,
  onDone,
}: {
  card: OpportunityCardData;
  revisions: ReturnType<typeof winnableRevisions>;
  onCancel: () => void;
  onDone: (o: Outcome, refetch: boolean) => void;
}) {
  const [revisionId, setRevisionId] = useState(revisions[0]?.revision_id ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const twoSteps = card.stage === "quoting";
  const chosen = revisions.find((r) => r.revision_id === revisionId) ?? null;
  const ready = chosen !== null && note.trim() !== "" && !busy;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!chosen || !ready) return;
    setBusy(true);
    try {
      const steps = await markCaseWon(
        { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number },
        { quote_id: chosen.quote_id, revision_no: chosen.revision_no },
        note.trim(),
      );
      onDone({ tone: "good", lines: steps.map((s, i) => receiptLine(s, i + 1, steps.length)) }, true);
    } catch (err) {
      const done = err instanceof WonFlowError ? err.done : [];
      const total = twoSteps ? 2 : 1;
      if (done.length > 0) {
        // The first step stays recorded: say exactly that, and refetch so the drawer shows it.
        onDone(
          {
            tone: "warn",
            lines: [
              ...done.map((s, i) => receiptLine(s, i + 1, total)),
              `${done.length + 1}/${total} · ${STAGE_LABEL.won}: no se registró — ${caseRefusalText(err)}`,
              `El caso quedó en «${STAGE_LABEL.negotiating}».`,
            ],
          },
          true,
        );
      } else {
        onDone({ tone: "bad", lines: [caseRefusalText(err)] }, isStaleRefusal(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-2 rounded-md border border-line p-3" aria-label="Marcar ganada">
      {twoSteps ? (
        <p className="text-[11px] text-ink-muted" data-testid="won-two-steps">
          El caso está en «{STAGE_LABEL.quoting}». Se registrarán dos pasos, cada uno con su recibo: primero la etapa pasa a «
          {STAGE_LABEL.negotiating}» y luego el caso se marca ganado.
        </p>
      ) : null}
      <FormField label="Revisión aceptada" htmlFor="case-won-revision" required hint="Sólo revisiones enviadas y vigentes.">
        <SelectInput
          id="case-won-revision"
          value={revisionId}
          onChange={setRevisionId}
          options={revisions.map((r) => ({
            value: r.revision_id,
            label: `${r.quote_number} r${r.revision_no}${r.sent_at ? ` · enviada ${fmtDate(r.sent_at)}` : ""}`,
          }))}
        />
      </FormField>
      <FormField label="Nota" htmlFor="case-won-note" required hint="Por ejemplo, la orden de compra recibida.">
        <TextareaInput id="case-won-note" value={note} onChange={setNote} maxLength={2000} rows={2} />
      </FormField>
      <div className="flex justify-end gap-2">
        <button type="button" className={SECONDARY} onClick={onCancel} disabled={busy}>
          Cancelar
        </button>
        <button type="submit" className={PRIMARY} disabled={!ready}>
          {busy ? "Registrando…" : "Marcar ganada"}
        </button>
      </div>
    </form>
  );
}

function ConfirmInstitution({
  card,
  mayAuthor,
  onDone,
}: {
  card: OpportunityCardData;
  mayAuthor: boolean;
  onDone: (o: Outcome, refetch: boolean) => void;
}) {
  const org = card.organization;
  const [busy, setBusy] = useState(false);
  const keyRef = useRef(newCaseCommandKey());
  if (!org) return null;
  const version = typeof org.version === "number" ? org.version : null;

  async function confirm() {
    if (version === null || !org) return;
    setBusy(true);
    try {
      await confirmOrganizationRecord({ organization_id: org.organization_id, expected_version: version }, keyRef.current);
      onDone({ tone: "good", lines: [`Institución «${org.name ?? ""}» confirmada.`] }, true);
    } catch (err) {
      keyRef.current = newCaseCommandKey();
      onDone({ tone: "bad", lines: [caseRefusalText(err)] }, isStaleRefusal(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-warn/40 bg-warn-bg/40 px-3 py-2 text-xs" data-testid="case-confirm-institution">
      <span className="text-ink-muted">
        «{org.name ?? "Sin nombre"}» está por confirmar: la propuso una regla de correo y ninguna persona la revisó.
      </span>
      {mayAuthor && version !== null ? (
        <button type="button" className={PRIMARY} onClick={() => void confirm()} disabled={busy}>
          {busy ? "Confirmando…" : "Confirmar institución"}
        </button>
      ) : (
        <DisabledAction id="drawer-confirm-org" reason={WRITE_DISABLED_REASON}>
          Confirmar institución
        </DisabledAction>
      )}
      <a href={crmHash("organizaciones", org.organization_id)} className="text-brand-700 hover:underline">
        Abrir institución para renombrarla
      </a>
    </div>
  );
}
