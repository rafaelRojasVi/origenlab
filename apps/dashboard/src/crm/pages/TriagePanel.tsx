import { useMemo, useRef, useState } from "react";
import {
  WonFlowError, markCaseWon, moveCase, newCaseCommandKey, openCommercialCase, stagePath,
  useMayRunCaseCommands,
} from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { inSharedMailbox } from "../gmailLinks";
import { useLeave } from "../useLeave";
import { refusalMessage } from "../mailRules";
import {
  CLASS_LABEL,
  INTENT_LABEL,
  approvalMove,
  cardsById,
  fetchTriageReadings,
  isOpenableRequest,
  openCaseTitle,
  reopenProposal,
  reviewTriage,
  sortInbox,
  stageLabel,
  wonProposal,
  type HiddenReason,
  type ReopenProposal,
  type TriageCorrection,
  type TriageReading,
  type TriageReadings,
  type TriageStatus,
  type TriageVerdict,
  type WonProposal,
} from "../triage";
import { Badge, Button, Panel, ResourceGate, Skeleton, fmtDate, toast } from "../ui";
import { useResource } from "../useResource";

const VERDICT: Record<TriageVerdict, { label: string; tone: "good" | "warn" | "bad" }> = {
  approved: { label: "Aprobada", tone: "good" },
  corrected: { label: "Corregida", tone: "warn" },
  rejected: { label: "Rechazada", tone: "bad" },
};

const DEFAULT_STAGES = ["lead", "qualifying", "qualified", "quoting", "negotiating", "won", "lost", "abandoned",
  "not_a_case", "unclear"];

/** Rows shown before «Ver N más». */
const PREVIEW = 5;

/**
 * «Correos sin caso» on «Hoy»: the emails a person wrote that no case holds yet (`sortInbox`) —
 * one compact row each: subject, who, when, the thread in Gmail and «Descartar». «Revisar» opens
 * what the triage read and the verdict buttons; a quote request also offers «Abrir caso», which
 * opens the case at «Solicitada» from that email; a reply after the closing of a case lost in the
 * last 90 days offers «Reabrir» (`reopenProposal`: that same case moves back). A purchase order the triage read on a case the
 * board can still win (`wonProposal`, from `cards`) is a «¿Marcar ganada?» row instead, with the
 * revision to win against. Other emails already on a case, from a supplier, automatic notices and
 * older messages of the same thread are not asked about, only counted. «Revisadas» lists the
 * verdicts already given. `onCaseChanged` is called after a case was won or opened, so the board
 * behind the panel reloads.
 */
export function TriagePanel({ cards = [], onCaseChanged }: {
  cards?: readonly OpportunityCardData[];
  onCaseChanged?: () => void;
} = {}) {
  const [status, setStatus] = useState<TriageStatus>("pending");
  const [all, setAll] = useState(false);
  const loader = () => fetchTriageReadings(status);
  const [state, reload] = useResource(loader, [status]);
  const byId = useMemo(() => cardsById(cards), [cards]);
  const sorted = state.kind === "ready" && status === "pending" ? sortInbox(state.data.items, byId) : null;
  const changed = () => { reload(); onCaseChanged?.(); };
  return (
    <Panel
      title={status === "pending" ? "Correos sin caso" : "Correos revisados"}
      aside={sorted ? <Badge tone={sorted.ask.length ? "warn" : "good"} glyph={false}>{sorted.ask.length}</Badge> : null}
      bodyClassName="divide-y divide-line"
    >
      <ResourceGate state={state} reload={reload} skeleton={<div className="p-3"><Skeleton rows={3} /></div>}>
        {(data) => {
          if (status === "reviewed") {
            return data.items.length === 0 ? (
              <p className="px-4 py-3 text-xs text-ink-muted">Aún no hay correos revisados.</p>
            ) : (
              <div className="space-y-2 p-3">
                {data.items.map((r) => (
                  <ReadingCard key={r.assertion_id} reading={r} stages={data.vocabulary?.stages ?? DEFAULT_STAGES}
                    classes={data.vocabulary?.classes ?? Object.keys(CLASS_LABEL)}
                    intents={data.vocabulary?.intents ?? Object.keys(INTENT_LABEL)} onDone={reload} />
                ))}
              </div>
            );
          }
          const { ask, hidden } = sortInbox(data.items, byId);
          const shown = all ? ask : ask.slice(0, PREVIEW);
          return (
            <>
              {ask.length === 0 ? (
                <p className="px-4 py-3 text-xs text-ink-muted">Ningún correo nuevo fuera de un caso.</p>
              ) : (
                shown.map((r) => {
                  const won = wonProposal(r, byId);
                  if (won) return <WonRow key={r.assertion_id} reading={r} proposal={won} onDone={reload} onCaseChanged={changed} />;
                  const reopen = reopenProposal(r, byId);
                  if (reopen) return <ReopenRow key={r.assertion_id} reading={r} proposal={reopen} onDone={reload} onCaseChanged={changed} />;
                  return <InboxRow key={r.assertion_id} reading={r} openable={isOpenableRequest(r, byId)} vocabulary={data.vocabulary} onDone={reload} onCaseChanged={changed} />;
                })
              )}
              {ask.length > PREVIEW ? (
                <button type="button" onClick={() => setAll(!all)}
                  className="w-full px-4 py-2 text-left text-[12px] font-medium text-brand-700 hover:underline">
                  {all ? "Ver menos" : `Ver ${ask.length - PREVIEW} más`}
                </button>
              ) : null}
              <p className="px-4 py-2 text-[11px] text-ink-faint">
                {hiddenLine(hidden)}
                <button type="button" onClick={() => setStatus("reviewed")} className="ml-1 text-brand-700 hover:underline">
                  Ver revisados
                </button>
              </p>
            </>
          );
        }}
      </ResourceGate>
      {status === "reviewed" ? (
        <button type="button" onClick={() => setStatus("pending")}
          className="w-full px-4 py-2 text-left text-[12px] font-medium text-brand-700 hover:underline">
          ← Volver a los correos sin caso
        </button>
      ) : null}
    </Panel>
  );
}

function hiddenLine(hidden: Partial<Record<HiddenReason, number>>): string {
  const parts = (Object.entries(hidden) as [HiddenReason, number][]).map(([k, n]) => `${n} ${k}`);
  return parts.length ? `No se muestran: ${parts.join(" · ")}.` : "";
}

function gmailUrl(r: TriageReading): string | null {
  return r.thread_id ? inSharedMailbox(`https://mail.google.com/mail/u/0/#all/${r.thread_id}`) : null;
}

function GmailLink({ url }: { url: string | null }) {
  if (!url) return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer"
      className="inline-flex h-8 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong">
      Abrir ↗
    </a>
  );
}

/**
 * «OC recibida · ¿marcar ganada?»: a purchase order the triage read on a case that can be won.
 * Aceptar records the «approved» verdict, then wins the case exactly as the drawer's «Marcar
 * ganada» does (`markCaseWon`: → «Conversación» if needed, then `record-case-won` against the
 * chosen revision), each step with its own key so a retry never records a step twice. Descartar
 * records a «rejected» verdict. The row leaves at once and comes back only if the server refuses.
 */
function WonRow({ reading: r, proposal, onDone, onCaseChanged }: {
  reading: TriageReading;
  proposal: WonProposal;
  onDone: () => void;
  onCaseChanged: () => void;
}) {
  const mayDecide = useMayRunCaseCommands();
  const { leaving, gone, leave } = useLeave(r);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<string | null>(proposal.revisions.length === 1 ? proposal.revisions[0].revision_id : null);
  const keys = useRef({ advance: newCaseCommandKey(), won: newCaseCommandKey(), reviewed: false });
  const revision = proposal.revisions.find((x) => x.revision_id === picked) ?? null;
  const url = gmailUrl(r);
  const caseName = proposal.card.organization?.name ?? proposal.case.title ?? "caso sin título";

  async function accept() {
    if (!revision) return;
    setBusy(true);
    const note = `OC recibida por correo (${fmtDate(r.sent_at)}): ganada contra ${revision.quote_number} r${revision.revision_no}, desde Hoy`;
    try {
      if (!keys.current.reviewed) {
        await reviewTriage({ assertion_id: r.assertion_id, verdict: "approved", corrected: {}, note: "OC recibida: caso ganado desde Hoy" });
        keys.current.reviewed = true;
      }
      await markCaseWon(proposal.case, { quote_id: revision.quote_id, revision_no: revision.revision_no }, note,
        { advance: keys.current.advance, won: keys.current.won });
      toast(`«${caseName}» ganada contra ${revision.quote_number} r${revision.revision_no}.`);
      leave();
      onDone();
      onCaseChanged();
    } catch (err) {
      if (err instanceof WonFlowError) {
        const done = err.done.map((s) => s.label).join(", ");
        toast(`${done ? `${done}; ` : ""}no se pudo marcar ganada: ${refusalMessage(err.cause, "rechazado")}`, "bad");
        onCaseChanged();
      } else {
        toast(refusalMessage(err, "No se pudo registrar la revisión."), "bad");
      }
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    setBusy(true);
    try {
      await reviewTriage({ assertion_id: r.assertion_id, verdict: "rejected", corrected: {}, note: "OC descartada desde Hoy" });
      leave();
      onDone();
    } catch (err) {
      toast(refusalMessage(err, "No se pudo descartar."), "bad");
    } finally {
      setBusy(false);
    }
  }

  if (gone) return null;
  return (
    <div className={leaving ? "crm-row-out" : ""} data-testid="won-proposal">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
        <div className="min-w-[12rem] flex-1">
          <p className="truncate text-[13px] font-medium text-ink">
            <Badge tone="good">OC recibida</Badge>{" "}
            {caseName} · ¿marcar ganada {proposal.revisions.length === 1 ? `${revision?.quote_number} r${revision?.revision_no}` : ""}?
          </p>
          <p className="truncate text-[11px] text-ink-muted">
            {r.subject || "(sin asunto)"} · {r.sender ?? "—"} · {fmtDate(r.sent_at)}
          </p>
          {proposal.revisions.length > 1 ? (
            <fieldset className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink">
              <legend className="sr-only">Revisión que ganó</legend>
              {proposal.revisions.map((x) => (
                <label key={x.revision_id} className="inline-flex items-center gap-1.5">
                  <input type="radio" name={`won-${r.assertion_id}`} checked={picked === x.revision_id}
                    onChange={() => setPicked(x.revision_id)} />
                  {x.quote_number} r{x.revision_no} · {fmtDate(x.sent_at)}
                </label>
              ))}
            </fieldset>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2">
          <GmailLink url={url} />
          {mayDecide ? (
            <>
              <Button variant="primary" onClick={() => void accept()} busy={busy} busyLabel="…" disabled={!revision}>Marcar ganada</Button>
              <Button onClick={() => void discard()} busy={busy} busyLabel="…">Descartar</Button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * A person wrote, after the closing, on the thread of a case lost or abandoned in the last 90
 * days: «¿Reabrir?». «Reabrir» moves that same case back to «Conversación» (or «Solicitada» when
 * it never named who was asking) — its quotes and history stay — then records the verdict.
 * «Descartar» records a rejected verdict and the case stays closed.
 */
function ReopenRow({ reading: r, proposal, onDone, onCaseChanged }: {
  reading: TriageReading;
  proposal: ReopenProposal;
  onDone: () => void;
  onCaseChanged: () => void;
}) {
  const mayDecide = useMayRunCaseCommands();
  const { leaving, gone, leave } = useLeave(r);
  const [busy, setBusy] = useState(false);
  const reopenKey = useRef(newCaseCommandKey());
  const url = gmailUrl(r);
  const caseName = proposal.card?.organization?.name ?? proposal.case.title ?? "caso sin título";
  const closedLabel = proposal.case.stage === "abandoned" ? "cerrado sin respuesta" : "perdido";
  const target = stageLabel(proposal.stage);

  async function reopen() {
    setBusy(true);
    try {
      await moveCase(
        { opportunity_id: proposal.case.opportunity_id, stage: proposal.case.stage, version: proposal.case.version },
        proposal.stage,
        `Reabierto desde Hoy: el cliente volvió a escribir (${fmtDate(r.sent_at)})`,
        null,
        [reopenKey.current],
      );
    } catch (err) {
      toast(refusalMessage(err instanceof WonFlowError ? err.cause : err, "No se pudo reabrir el caso."), "bad");
      setBusy(false);
      return;
    }
    try {
      await reviewTriage({ assertion_id: r.assertion_id, verdict: "approved", corrected: {}, note: "Caso reabierto desde Hoy" });
      toast(`«${caseName}» reabierto en «${target}».`);
      leave();
      onDone();
    } catch (err) {
      toast(`El caso se reabrió, pero la lectura no quedó revisada: ${refusalMessage(err, "rechazado")}`, "bad");
    } finally {
      setBusy(false);
      onCaseChanged();
    }
  }

  async function discard() {
    setBusy(true);
    try {
      await reviewTriage({ assertion_id: r.assertion_id, verdict: "rejected", corrected: {}, note: "No se reabre; descartado desde Hoy" });
      leave();
      onDone();
    } catch (err) {
      toast(refusalMessage(err, "No se pudo descartar."), "bad");
    } finally {
      setBusy(false);
    }
  }

  if (gone) return null;
  return (
    <div className={leaving ? "crm-row-out" : ""} data-testid="reopen-proposal">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
        <div className="min-w-[12rem] flex-1">
          <p className="truncate text-[13px] font-medium text-ink">
            <Badge tone="warn">Volvió a escribir</Badge>{" "}
            {caseName} · caso {closedLabel} hace {proposal.daysSinceClose} d · ¿reabrir en «{target}»?
          </p>
          <p className="truncate text-[11px] text-ink-muted">
            {r.subject || "(sin asunto)"} · {r.sender ?? "—"} · {fmtDate(r.sent_at)}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <GmailLink url={url} />
          {mayDecide ? (
            <>
              <Button variant="primary" onClick={() => void reopen()} busy={busy} busyLabel="…">Reabrir</Button>
              <Button onClick={() => void discard()} busy={busy} busyLabel="…">Descartar</Button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function InboxRow({ reading: r, openable, vocabulary, onDone, onCaseChanged }: {
  /** A quote request no case holds: «Abrir caso» is offered. */
  openable: boolean;
  reading: TriageReading;
  vocabulary: TriageReadings["vocabulary"];
  onDone: () => void;
  onCaseChanged: () => void;
}) {
  const mayDecide = useMayRunCaseCommands();
  const { leaving, gone, leave } = useLeave(r);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const openKey = useRef(newCaseCommandKey());
  const url = gmailUrl(r);

  async function discard() {
    setBusy(true);
    try {
      await reviewTriage({ assertion_id: r.assertion_id, verdict: "rejected", corrected: {}, note: "Descartado desde Hoy" });
      leave();
      onDone();
    } catch (err) {
      toast(refusalMessage(err, "No se pudo descartar."), "bad");
    } finally {
      setBusy(false);
    }
  }

  /**
   * «Abrir caso»: the case first (it is the decision), the verdict after. If the verdict is refused
   * the case still exists, and the row says so rather than hiding it.
   */
  async function openCase() {
    setBusy(true);
    const title = openCaseTitle(r);
    try {
      await openCommercialCase(
        { title, origin_source_record_id: r.source_record_id, note: "Abierto desde Hoy: solicitud de cotización recibida por correo" },
        openKey.current,
      );
    } catch (err) {
      toast(refusalMessage(err, "No se pudo abrir el caso."), "bad");
      setBusy(false);
      return;
    }
    try {
      await reviewTriage({ assertion_id: r.assertion_id, verdict: "approved", corrected: {}, note: "Caso abierto desde Hoy" });
      toast(`Caso «${title}» abierto en «${stageLabel("lead")}»: confirma la institución en Hoy.`);
      leave();
      onDone();
    } catch (err) {
      toast(`El caso «${title}» se abrió, pero la lectura no quedó revisada: ${refusalMessage(err, "rechazado")}`, "bad");
    } finally {
      setBusy(false);
      onCaseChanged();
    }
  }

  if (gone) return null;
  return (
    <div className={leaving ? "crm-row-out" : ""}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
        <div className="min-w-[12rem] flex-1">
          <p className="truncate text-[13px] font-medium text-ink">{r.subject || "(sin asunto)"}</p>
          <p className="truncate text-[11px] text-ink-muted">
            {r.sender ?? "—"} · {fmtDate(r.sent_at)}
            {r.class && CLASS_LABEL[r.class] ? ` · ${CLASS_LABEL[r.class]}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <GmailLink url={url} />
          {mayDecide ? (
            <>
              {openable ? (
                <Button variant="primary" onClick={() => void openCase()} busy={busy} busyLabel="…">Abrir caso</Button>
              ) : null}
              <Button variant="quiet" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Cerrar" : "Revisar"}</Button>
              <Button onClick={() => void discard()} busy={busy} busyLabel="…">Descartar</Button>
            </>
          ) : null}
        </div>
      </div>
      {open ? (
        <div className="px-4 pb-3">
          <ReadingCard reading={r} stages={vocabulary?.stages ?? DEFAULT_STAGES}
            classes={vocabulary?.classes ?? Object.keys(CLASS_LABEL)}
            intents={vocabulary?.intents ?? Object.keys(INTENT_LABEL)} onDone={onDone} embedded />
        </div>
      ) : null}
    </div>
  );
}

function ReadingCard({ reading: r, stages, classes, intents, onDone, embedded = false }: {
  reading: TriageReading;
  /** Inside its «Correos sin caso» row, which already shows the subject and sender. */
  embedded?: boolean;
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
      title={embedded ? undefined : r.subject || "(sin asunto)"}
      note={embedded ? undefined : `${r.sender ?? "—"} · ${fmtDate(r.sent_at)}`}
      aside={r.review ? <Badge tone={VERDICT[r.review.verdict].tone}>{VERDICT[r.review.verdict].label}</Badge> : null}
      bodyClassName="space-y-2 px-3 py-2 text-xs"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="info">{CLASS_LABEL[r.class ?? ""] ?? r.class ?? "—"}</Badge>
        {r.intent ? <Badge>{INTENT_LABEL[r.intent] ?? r.intent}</Badge> : null}
        {r.urgency === "high" ? <Badge tone="bad">Urgente</Badge> : null}
        {r.needs_reply ? <Badge tone="warn">Necesita respuesta</Badge> : null}
      </div>
      {r.summary_es ? <p className="text-ink">{r.summary_es}</p> : null}
      {r.stage || onCase ? <p className="text-ink-muted">
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
      </p> : null}
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
