/**
 * «Decidir casos»: every case whose stage is only the historical import's trace («Enviada ·
 * histórico»), each with a proposed decision the operator reviews and applies in one go.
 * «Revisar casos abiertos» (owner, 2026-10-31) opens the same table over every open case with a
 * sent quote: which ones are simply lost, which go on (`proposeOpenDecision`).
 *
 * The proposal is a rule over what the card already carries, never stored and never sent:
 * the client wrote after the quote → «Conversación»; 45 days or more with no answer →
 * «Perdida · Sin respuesta»; otherwise → «Seguimiento» (a follow-up task due now, so it shows
 * in «Hoy»). The operator can change any row — to «En pausa» with a date, or «Ganada» against the
 * newest sent revision — or leave it out. Applying runs the same commands the drawer and the
 * Tablero use, one case at a time, each with its own receipt; a refused row says why and the
 * rest go on.
 */
import { useMemo, useRef, useState } from "react";
import { inSharedMailbox } from "../gmailLinks";
import {
  caseRefusalText,
  createTask,
  markCaseWon,
  moveCase,
  newCaseCommandKey,
  winnableRevisions,
} from "../caseCommands";
import { ageTone, contactLine, conversation, daysSince, displayName } from "../caseDisplay";
import type { OpportunityCardData } from "../crmTypes";
import { LOST_REASONS, PAUSE_REASONS, pausedUntil } from "../stage";
import { Button, fmtDate, toast, type Tone } from "../ui";
import { isoDay, morningOf } from "./CaseMove";

export type Decision = "seguir" | "conversacion" | "pausa" | "perdida" | "ganada";

export const DECISION_LABEL: Record<Decision, string> = {
  seguir: "Seguimiento",
  conversacion: "Conversación",
  pausa: "En pausa",
  perdida: "Perdida",
  ganada: "Ganada",
};

/** Days without an answer after which «Perdida · Sin respuesta» is proposed. */
export const SILENT_AFTER_DAYS = 45;

export function proposeDecision(card: OpportunityCardData, now: Date): { decision: Decision; why: string } {
  const conv = conversation(card, now);
  // The silence counts from OrigenLab's last touch (the quote, or a later email or logged
  // follow-up), the same clock the board shows — not from the quote alone.
  const days = conv.days ?? daysSince(card.latest_revision?.sent_at, now) ?? 0;
  if (conv.kind === "replied") return { decision: "conversacion", why: conv.text.replace(" · te toca", "") };
  if (days >= SILENT_AFTER_DAYS) {
    return { decision: "perdida", why: `${days} días sin respuesta${conv.kind === "followed_up" ? " desde el seguimiento" : ""}` };
  }
  return { decision: "seguir", why: conv.kind === "followed_up" ? `Seguimiento hace ${days} días` : `Enviada hace ${days} días` };
}

/** Open cases with a sent quote: the ones «Revisar casos abiertos» lists. */
export function reviewableOpenCards(cards: OpportunityCardData[]): OpportunityCardData[] {
  return cards.filter(
    (c) => !c.closed_at && (c.stage === "quoting" || c.stage === "negotiating") && c.quotes.some((q) => q.revisions.some((r) => r.sent_at)),
  );
}

/**
 * For any open case (owner, 2026-10-31): lost is counted from the quote when the client never
 * wrote back — our own follow-ups do not make an unanswered quote fresh. Only what changes the
 * case comes ticked: a lost one, and a reply on a case not yet in «Conversación». A pause still
 * running, a reply on a case already in «Conversación» and an ordinary follow-up (Hoy already
 * schedules it on its 3 · 14 · 30 rhythm) come unticked.
 */
export function proposeOpenDecision(card: OpportunityCardData, now: Date): { decision: Decision; why: string; include: boolean } {
  const until = pausedUntil(card, now);
  if (until) return { decision: "pausa", why: `En pausa hasta ${fmtDate(until)}`, include: false };
  const sent = card.latest_revision?.sent_at ?? null;
  const inbound = card.last_contact?.inbound?.at ?? null;
  const clientWrote = !!inbound && (!sent || Date.parse(inbound) > Date.parse(sent) + 60_000);
  const quoteDays = daysSince(sent, now);
  if (!clientWrote && quoteDays != null && quoteDays >= SILENT_AFTER_DAYS) {
    return { decision: "perdida", why: `Cotización hace ${quoteDays} días y nunca respondió`, include: true };
  }
  const p = proposeDecision(card, now);
  if (p.decision === "perdida") return { ...p, include: true };
  if (p.decision === "conversacion") {
    return card.stage === "negotiating" ? { ...p, why: `${p.why} · ya en Conversación`, include: false } : { ...p, include: true };
  }
  return { ...p, why: `${p.why} · Hoy lo sigue solo`, include: false };
}

interface Row {
  decision: Decision;
  include: boolean;
  lostReason: string;
  pauseReason: string;
  pauseDay: string;
}

type Result = { ok: true; text: string } | { ok: false; text: string };

const BADGE: Record<Tone, string> = {
  brand: "bg-brand-50 text-accent",
  info: "bg-info-bg text-info",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
  good: "bg-good-bg text-good",
  neutral: "bg-canvas-sunken text-ink-muted",
};

const CHOICE_ON: Record<Decision, string> = {
  seguir: "border-brand-700 bg-brand-700 text-white",
  conversacion: "border-warn bg-warn text-on-solid",
  pausa: "border-violet-600 bg-violet-600 text-white",
  perdida: "border-bad bg-bad text-on-solid",
  ganada: "border-good bg-good text-on-solid",
};

export function DecideCases({
  cards,
  onApplied,
  onClose,
  now,
  mode = "historical",
}: {
  /** The cases to decide: the historical ones, or every open one (`mode="open"`). */
  cards: OpportunityCardData[];
  mode?: "historical" | "open";
  /** Refetch the pipeline after anything was recorded. */
  onApplied: () => void;
  onClose: () => void;
  now?: Date;
}) {
  const at = useMemo(() => now ?? new Date(), [now]);
  const propose = (c: OpportunityCardData) => (mode === "open" ? proposeOpenDecision(c, at) : { ...proposeDecision(c, at), include: true });
  const proposals = useMemo(
    () => Object.fromEntries(cards.map((c) => [c.opportunity_id, mode === "open" ? proposeOpenDecision(c, at) : proposeDecision(c, at)])),
    [cards, at, mode],
  );
  const [rows, setRows] = useState<Record<string, Row>>(() =>
    Object.fromEntries(
      cards.map((c) => [
        c.opportunity_id,
        {
          decision: propose(c).decision,
          include: propose(c).include,
          lostReason: "Sin respuesta",
          pauseReason: PAUSE_REASONS[2],
          pauseDay: (mode === "open" && pausedUntil(c, at)?.slice(0, 10)) || isoDay(at, 30),
        },
      ]),
    ),
  );
  const [filter, setFilter] = useState<Decision | "all">("all");
  const [running, setRunning] = useState<{ done: number; total: number } | null>(null);
  const [results, setResults] = useState<Record<string, Result>>({});
  const keysRef = useRef<Record<string, string[]>>({});

  const row = (id: string): Row =>
    rows[id] ?? { decision: "seguir", include: false, lostReason: "Sin respuesta", pauseReason: PAUSE_REASONS[2], pauseDay: isoDay(at, 30) };
  const set = (id: string, patch: Partial<Row>) => setRows((r) => ({ ...r, [id]: { ...r[id], ...patch } }));
  const pending = cards.filter((c) => !results[c.opportunity_id]?.ok);
  const counts = pending.reduce<Record<string, number>>((n, c) => {
    const d = row(c.opportunity_id)?.decision;
    n[d] = (n[d] ?? 0) + 1;
    return n;
  }, {});
  const visible = pending.filter((c) => filter === "all" || row(c.opportunity_id)?.decision === filter);
  const chosen = pending.filter((c) => row(c.opportunity_id)?.include);

  function keys(id: string): string[] {
    keysRef.current[id] ??= [newCaseCommandKey(), newCaseCommandKey(), newCaseCommandKey()];
    return keysRef.current[id];
  }

  async function applyOne(card: OpportunityCardData, r: Row): Promise<Result> {
    const why = proposals[card.opportunity_id]?.why ?? "";
    const base = { opportunity_id: card.opportunity_id, stage: card.stage, version: card.version as number };
    const k = keys(card.opportunity_id);
    const quote = card.latest_revision?.quote_number ?? "la cotización";
    if (r.decision === "seguir") {
      await createTask(
        { opportunity_id: card.opportunity_id, title: `Seguimiento de ${quote}`, due_at: at.toISOString(), note: `Decidido en bloque: ${why}.` },
        k[0],
      );
      return { ok: true, text: "Seguimiento para hoy" };
    }
    if (r.decision === "pausa") {
      await createTask(
        { opportunity_id: card.opportunity_id, title: `Retomar: ${r.pauseReason}`, due_at: morningOf(r.pauseDay), note: r.pauseReason },
        k[0],
      );
      return { ok: true, text: `En pausa hasta ${fmtDate(morningOf(r.pauseDay))}` };
    }
    if (r.decision === "conversacion") {
      if (card.stage === "negotiating") return { ok: true, text: "Ya estaba en Conversación" };
      await moveCase(base, "negotiating", `Decidido en bloque: ${why}.`, null, k);
      return { ok: true, text: "En conversación" };
    }
    if (r.decision === "perdida") {
      const stage = LOST_REASONS.find((x) => x.label === r.lostReason)?.stage ?? "lost";
      await moveCase(base, stage, `${r.lostReason} (decidido en bloque: ${why}).`, r.lostReason, k);
      return { ok: true, text: `Perdida · ${r.lostReason}` };
    }
    const revision = winnableRevisions(card)[0];
    if (!revision) return { ok: false, text: "No hay una revisión enviada y vigente para ganar." };
    await markCaseWon(base, revision, "Ganada (decidido en bloque).", { advance: k[0], won: k[1] });
    return { ok: true, text: `Ganada · ${revision.quote_number} r${revision.revision_no}` };
  }

  async function apply() {
    const todo = chosen;
    if (todo.length === 0) return;
    setRunning({ done: 0, total: todo.length });
    let ok = 0;
    for (const [i, card] of todo.entries()) {
      let result: Result;
      try {
        result = await applyOne(card, row(card.opportunity_id));
      } catch (err) {
        delete keysRef.current[card.opportunity_id];
        result = { ok: false, text: caseRefusalText(err) };
      }
      if (result.ok) ok += 1;
      setResults((r) => ({ ...r, [card.opportunity_id]: result }));
      setRunning({ done: i + 1, total: todo.length });
    }
    setRunning(null);
    onApplied();
    const failed = todo.length - ok;
    toast(failed ? `${ok} decididos, ${failed} con error: revisa la lista.` : `${ok} casos decididos.`, failed ? "warn" : "good");
    // Done: back to the cards (owner, 2026-10-19: the emptied table looked like a dead end). A
    // failure keeps the list open so its reason can be read.
    if (!failed) onClose();
  }

  const filters: (Decision | "all")[] = ["all", "conversacion", "seguir", "pausa", "perdida", "ganada"];
  return (
    <section className="space-y-3 rounded-xl border border-line bg-canvas-raised p-4" aria-labelledby="decide-title" data-testid="decide-cases">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 id="decide-title" className="text-base font-semibold text-ink">
            {mode === "open" ? `Revisar ${pending.length} casos abiertos` : `Decidir ${pending.length} casos históricos`}
          </h2>
          <p className="mt-0.5 max-w-3xl text-xs leading-5 text-ink-muted">
            {mode === "open" ? "Los casos con cotización enviada que siguen abiertos. «Perdida» si la cotización tiene 45 días o más y el cliente nunca respondió (aunque le hayamos escrito). Vienen marcados solo los que cambian algo; los seguimientos normales, las pausas y los que ya están en «Conversación» vienen desmarcados. " : null}
            Cada caso trae una propuesta a partir del correo: si el cliente escribió después de la cotización pasa a «Conversación»; con{" "}
            {SILENT_AFTER_DAYS} días o más sin respuesta, a «Perdida · Sin respuesta»; si no, queda en «Enviada» con un seguimiento para hoy.
            Cambia lo que no corresponda, desmarca lo que prefieras ver después y aplica.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button onClick={onClose} disabled={running != null}>
            Volver a las tarjetas
          </Button>
          <Button
            variant="primary"
            onClick={() => void apply()}
            disabled={chosen.length === 0}
            busy={running != null}
            busyLabel={running ? `Aplicando ${running.done}/${running.total}…` : undefined}
          >
            Aplicar {chosen.length} decisiones
          </Button>
        </div>
      </header>

      <div role="group" aria-label="Filtrar por decisión" className="flex flex-wrap gap-1.5">
        {filters.map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
              filter === f ? "border-ink bg-ink text-on-solid" : "border-line-strong bg-canvas-raised text-ink hover:border-ink-faint"
            }`}
          >
            {f === "all" ? "Todas" : DECISION_LABEL[f]} <span className="tabular-nums opacity-70">{f === "all" ? pending.length : (counts[f] ?? 0)}</span>
          </button>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[920px] border-collapse text-xs">
          <thead>
            <tr className="text-left text-[11px] text-ink-faint">
              <th className="w-8 px-2 py-1.5 font-medium">
                <span className="sr-only">Incluir</span>
              </th>
              <th className="px-2 py-1.5 font-medium">Caso</th>
              <th className="px-2 py-1.5 font-medium">Correo</th>
              <th className="px-2 py-1.5 font-medium">Decisión</th>
              <th className="px-2 py-1.5 font-medium">Detalle</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((card) => {
              const id = card.opportunity_id;
              const r = row(id);
              const { name } = displayName(card);
              const days = daysSince(card.latest_revision?.sent_at, at);
              const conv = conversation(card, at);
              const result = results[id];
              const canWin = winnableRevisions(card).length > 0;
              return (
                <tr key={id} className={`border-t border-line align-top ${r.include ? "" : "opacity-50"}`} data-testid={`decide-row-${id}`}>
                  <td className="px-2 py-2">
                    <input
                      type="checkbox"
                      checked={r.include}
                      onChange={(e) => set(id, { include: e.target.checked })}
                      aria-label={`Incluir ${name}`}
                      className="h-4 w-4 accent-brand-700"
                    />
                  </td>
                  <td className="px-2 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className="font-semibold text-ink">{name}</span>
                      {days != null ? (
                        <span className={`rounded px-1 text-[10px] font-semibold tabular-nums ${BADGE[ageTone(days)]}`}>{days} d</span>
                      ) : null}
                    </div>
                    <div className="text-[11px] text-ink-muted tabular-nums">
                      {card.latest_revision?.quote_number ?? "sin cotización"} · {fmtDate(card.latest_revision?.sent_at)}
                      {contactLine(card) ? ` · ${contactLine(card)}` : ""}
                    </div>
                  </td>
                  <td className="px-2 py-2">
                    {conv.url ? (
                      <a href={inSharedMailbox(conv.url) ?? undefined} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                        {conv.text}
                      </a>
                    ) : (
                      <span className="text-ink-muted">{conv.text}</span>
                    )}
                    <div className="text-[11px] text-ink-faint">Propuesta: {proposals[id]?.why}</div>
                  </td>
                  <td className="px-2 py-2">
                    <div role="group" aria-label={`Decisión para ${name}`} className="flex flex-wrap gap-1">
                      {(Object.keys(DECISION_LABEL) as Decision[])
                        .filter((d) => d !== "ganada" || canWin)
                        .map((d) => (
                          <button
                            key={d}
                            type="button"
                            aria-pressed={r.decision === d}
                            onClick={() => set(id, { decision: d, include: true })}
                            className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                              r.decision === d ? CHOICE_ON[d] : "border-line-strong bg-canvas-raised text-ink-muted hover:text-ink"
                            }`}
                          >
                            {DECISION_LABEL[d]}
                          </button>
                        ))}
                    </div>
                  </td>
                  <td className="px-2 py-2">
                    {result ? (
                      <span className={result.ok ? "font-medium text-good" : "text-bad"} role={result.ok ? undefined : "alert"}>
                        {result.ok ? `✓ ${result.text}` : result.text}
                      </span>
                    ) : r.decision === "perdida" ? (
                      <select
                        value={r.lostReason}
                        onChange={(e) => set(id, { lostReason: e.target.value })}
                        aria-label={`Motivo de pérdida de ${name}`}
                        className="rounded-md border border-line bg-canvas-raised px-1.5 py-1 text-[11px]"
                      >
                        {LOST_REASONS.map((x) => (
                          <option key={x.label}>{x.label}</option>
                        ))}
                      </select>
                    ) : r.decision === "pausa" ? (
                      <div className="flex flex-wrap gap-1">
                        <input
                          type="date"
                          value={r.pauseDay}
                          min={isoDay(at, 1)}
                          onChange={(e) => set(id, { pauseDay: e.target.value })}
                          aria-label={`Retomar ${name} el`}
                          className="rounded-md border border-line bg-canvas-raised px-1.5 py-0.5 text-[11px]"
                        />
                        <select
                          value={r.pauseReason}
                          onChange={(e) => set(id, { pauseReason: e.target.value })}
                          aria-label={`Motivo de la pausa de ${name}`}
                          className="rounded-md border border-line bg-canvas-raised px-1.5 py-1 text-[11px]"
                        >
                          {PAUSE_REASONS.map((x) => (
                            <option key={x}>{x}</option>
                          ))}
                        </select>
                      </div>
                    ) : r.decision === "seguir" ? (
                      <span className="text-ink-faint">Tarea para hoy</span>
                    ) : r.decision === "ganada" ? (
                      <span className="text-ink-faint">Contra la revisión enviada más reciente</span>
                    ) : (
                      <span className="text-ink-faint">Pasa a Conversación</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {visible.length === 0 ? <p className="py-4 text-center text-xs text-ink-faint">No hay casos con esa decisión.</p> : null}
    </section>
  );
}
