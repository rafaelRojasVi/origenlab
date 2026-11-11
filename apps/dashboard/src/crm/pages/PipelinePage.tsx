import { useEffect, useMemo, useState } from "react";
import { fetchPipeline } from "../crmApi";
import { crmHash } from "../crmRoute";
import { CASE_ORIGINS, NO_ORIGIN_LABEL, ORIGIN_LABEL, matchesOrigin, type OriginFilter } from "../caseOrigin";
import type { OpportunityCardData } from "../crmTypes";
import { CaseDrawer } from "../drawer/CaseDrawer";
import type { Mode } from "./CaseActions";
import { COLUMN_LABEL, CaseMoveForm, moveRefusal, type MoveTarget } from "./CaseMove";
import { BOARD_SORT_LABEL, Board, type BoardSort } from "./PipelineBoard";
import { DecideCases } from "./DecideCases";
import { PipelineSheet } from "./PipelineSheet";
import { applyCaseReceipt, onCaseReceipt, useMayRunCaseCommands } from "../caseCommands";
import { followUpsDue, isCotizarCase } from "../today";
import { boardViewFromHash, type BoardView } from "../goals";
import { caseAmountClp } from "../nextSteps";
import { formatMoney } from "../money";
import { HISTORICAL_STAGE_LABEL, byLatestActivity, isOnBoard, matchesQuery, stageBasis, type BoardColumnKey } from "../stage";
import { urgencyByCase, type CaseUrgency } from "../urgency";
import { EmptyState, Modal, PageHeader, ResourceGate, SearchInput, Segmented, Skeleton, toast } from "../ui";
import { useResource } from "../useResource";

type View = "board" | "list";
/** The urgency strip's filters: one at a time, a second click clears it. */
type Focus = "atrasado" | "responder" | "hoy" | "sin";

export function PipelinePage({ initialOpportunityId }: { initialOpportunityId?: string | null }) {
  const [state, reload, refreshing, patch] = useResource(fetchPipeline);
  // Move the card the moment a command is recorded; the reload that follows confirms the rest.
  useEffect(
    () => onCaseReceipt((receipt) => patch((data) => ({ ...data, items: applyCaseReceipt(data.items, receipt) }))),
    [patch],
  );
  return (
    <div className="space-y-3">
      <PageHeader title="Pipeline" subtitle="Cada caso en su estado, lo más urgente arriba." />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} cards />}>
        {(data) => (
          <Pipeline
            items={data.items}
            driveConfigured={data.drive_configured}
            initialId={initialOpportunityId ?? null}
            onChanged={reload}
            refreshing={refreshing}
          />
        )}
      </ResourceGate>
    </div>
  );
}

function Pipeline({
  items,
  driveConfigured,
  initialId,
  onChanged,
  refreshing = false,
}: {
  items: OpportunityCardData[];
  driveConfigured: boolean;
  initialId: string | null;
  /** Refetch the pipeline after a write from the drawer. */
  onChanged: () => void;
  /** A refetch is running behind the cards on screen. */
  refreshing?: boolean;
}) {
  const [view, setView] = useState<View>("board");
  const [q, setQ] = useState("");
  const [origin, setOrigin] = useState<OriginFilter>("all");
  const [focus, setFocus] = useState<Focus | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [openId, setOpenId] = useState<string | null>(initialId);
  // A drawer opened by a drop on «Ganada» arrives with «Marcar ganada» open.
  const [openWith, setOpenWith] = useState<{ mode: Exclude<Mode, null>; target?: MoveTarget } | null>(null);
  const [moving, setMoving] = useState<{ id: string; to: MoveTarget } | null>(null);
  const [sort, setSort] = useState<BoardSort>("urgent");
  const [deciding, setDeciding] = useState(false);
  const mayDecide = useMayRunCaseCommands();

  function open(id: string | null, withForm: typeof openWith = null) {
    setOpenWith(withForm);
    setOpenId(id);
    // Replace, not push: «back» after closing must not reopen the case just closed.
    if (id === null) history.replaceState(null, "", crmHash("oportunidades"));
  }

  /** A card dropped on another Tablero column: ask for what the move needs, or say why not. */
  function onMove(card: OpportunityCardData, to: BoardColumnKey) {
    if (to === "ganada") {
      open(card.opportunity_id, { mode: "won" });
      return;
    }
    const refusal = moveRefusal(card, to);
    if (refusal) {
      toast(refusal, "warn");
      return;
    }
    setMoving({ id: card.opportunity_id, to });
  }

  // `?vista=seguimiento|cotizar` (Hoy's goal tiles): only the cases that tile counts, until «Quitar filtro».
  const vista = useBoardView();
  const now = useMemo(() => new Date(), [items]);
  const urgency = useMemo(() => urgencyByCase(items, now), [items, now]);
  const viewItems = useMemo(() => {
    if (vista === null) return items;
    return items.filter((c) => (vista === "seguimiento" ? followUpsDue([c], now).length > 0 : isCotizarCase(c, now)));
  }, [items, vista, now]);

  const matchesFocus = (c: OpportunityCardData) => {
    if (!focus) return true;
    const u = urgency.get(c.opportunity_id);
    if (!u) return false;
    if (focus === "responder") return u.step?.kind === "responder" && u.step.dueDays <= 0;
    return u.level === focus;
  };
  const visible = useMemo(
    () => viewItems.filter((i) => matchesOrigin(i, origin) && matchesQuery(i, q)).sort(byLatestActivity),
    [viewItems, origin, q],
  );
  const focused = focus ? visible.filter(matchesFocus) : visible;
  const historicalCards = useMemo(() => items.filter((i) => stageBasis(i) === "historical_import"), [items]);
  const historical = historicalCards.length;
  const closedCount = visible.filter((c) => urgency.get(c.opportunity_id)?.level === "cerrado" && isOnBoard(c, now)).length;
  const openCard = items.find((i) => i.opportunity_id === openId) ?? null;
  const movingCard = moving ? (items.find((i) => i.opportunity_id === moving.id) ?? null) : null;

  if (items.length === 0) {
    return <EmptyState title="Sin oportunidades en el CRM">El CRM no tiene casos comerciales todavía.</EmptyState>;
  }

  return (
    <>
      <UrgencyStrip cards={visible} urgency={urgency} focus={focus} onFocus={(f) => setFocus(focus === f ? null : f)} />
      {historical > 0 && !deciding ? (
        <div
          className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/30 bg-warn-bg/50 px-3 py-2.5 text-xs leading-5 text-ink-muted"
          data-testid="historical-stage-notice"
        >
          <p className="min-w-0 flex-1">
            <strong className="font-semibold text-ink">
              {historical} de {items.length}
            </strong>{" "}
            casos muestran «{HISTORICAL_STAGE_LABEL}»: la importación encontró la cotización enviada, pero nadie ha decidido en qué
            están hoy.
          </p>
          {mayDecide ? (
            <button
              type="button"
              onClick={() => setDeciding(true)}
              className="inline-flex h-8 shrink-0 items-center rounded-md bg-brand-700 px-3 text-xs font-medium text-white hover:bg-brand-900"
            >
              Decidir {historical} casos
            </button>
          ) : null}
        </div>
      ) : null}
      {!driveConfigured ? (
        <p className="rounded-md border border-line bg-canvas-sunken/70 px-3 py-2 text-xs text-ink-muted">
          Los registros del archivo de Drive no están cargados en este API: las tarjetas muestran el CRM sin enlaces de Drive.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Segmented
          label="Vista"
          value={view}
          onChange={setView}
          options={[
            { value: "board", label: "Tablero" },
            { value: "list", label: "Lista" },
          ]}
        />
        <SearchInput value={q} onChange={setQ} label="Buscar oportunidades" placeholder="Institución, número, contacto…" />
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          Origen
          <select
            value={origin}
            onChange={(e) => setOrigin(e.target.value as OriginFilter)}
            className="h-8 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink focus:border-brand-600 focus:outline-none"
          >
            <option value="all">Todos</option>
            {CASE_ORIGINS.map((o) => (
              <option key={o} value={o}>
                {ORIGIN_LABEL[o]}
              </option>
            ))}
            <option value="none">{NO_ORIGIN_LABEL}</option>
          </select>
        </label>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {view === "board" ? (
            <label className="flex items-center gap-1.5 text-xs text-ink-muted">
              Ordenar
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as BoardSort)}
                className="h-8 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink focus:border-brand-600 focus:outline-none"
              >
                {(Object.keys(BOARD_SORT_LABEL) as BoardSort[]).map((k) => (
                  <option key={k} value={k}>
                    {BOARD_SORT_LABEL[k]}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink-muted hover:text-ink">
            <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} className="accent-[var(--color-accent)]" />
            Ver cerradas <span className="tabular-nums text-ink-faint">{closedCount}</span>
          </label>
        </div>
      </div>

      {vista !== null ? (
        <div className="flex flex-wrap items-center gap-2" data-testid="pipeline-vista">
          <span className="inline-flex items-center rounded-full bg-warn-bg px-2.5 py-1 text-xs font-semibold text-warn">
            {VISTA_LABEL[vista]} · <span className="ml-1 tabular-nums">{viewItems.length}</span>
          </span>
          <button
            type="button"
            onClick={() => { window.location.hash = crmHash("oportunidades"); }}
            className="inline-flex min-h-11 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-line-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
          >
            Quitar filtro
          </button>
        </div>
      ) : null}

      {deciding ? (
        <DecideCases cards={historicalCards} mode="historical" onApplied={onChanged} onClose={() => setDeciding(false)} />
      ) : focused.length === 0 ? (
        <EmptyState title="Ninguna oportunidad coincide">Quita el filtro de urgencia u origen, o cambia la búsqueda.</EmptyState>
      ) : view === "list" ? (
        <PipelineSheet cards={focused} urgency={urgency} showClosed={showClosed} now={now} onOpen={(id) => open(id)} />
      ) : (
        <Board
          cards={focused}
          onOpen={(id) => open(id)}
          onMove={mayDecide ? onMove : undefined}
          onLose={(card) => setMoving({ id: card.opportunity_id, to: "perdida" })}
          sort={sort}
          urgency={urgency}
          showClosed={showClosed}
          onShowClosed={() => setShowClosed(true)}
        />
      )}

      <CaseDrawer
        card={openCard}
        onClose={() => open(null)}
        onChanged={onChanged}
        refreshing={refreshing}
        initial={openWith}
      />
      {moving && movingCard ? (
        <Modal title={`Mover a «${COLUMN_LABEL[moving.to]}»`} onClose={() => setMoving(null)}>
          <p className="mb-3 truncate text-[13px] font-medium text-ink">{movingCard.organization?.name ?? movingCard.title}</p>
          <CaseMoveForm
            card={movingCard}
            target={moving.to}
            onCancel={() => setMoving(null)}
            onDone={(o, refetch) => {
              if (refetch) onChanged();
              if (o.tone === "bad") {
                toast(o.lines.join(" "), "bad");
                return;
              }
              setMoving(null);
              toast(o.tone === "good" ? `Movido a «${COLUMN_LABEL[moving.to]}».` : o.lines.join(" "), o.tone);
            }}
          />
        </Modal>
      ) : null}
    </>
  );
}

/**
 * What needs doing now, in one line: overdue, clients waiting on a reply, due today, no next step,
 * and the money in the overdue cases. Each figure filters the board and the list.
 */
function UrgencyStrip({
  cards, urgency, focus, onFocus,
}: { cards: OpportunityCardData[]; urgency: Map<string, CaseUrgency>; focus: Focus | null; onFocus: (f: Focus) => void }) {
  let atrasado = 0, responder = 0, hoy = 0, sin = 0, risk = 0;
  for (const c of cards) {
    const u = urgency.get(c.opportunity_id);
    if (!u) continue;
    if (u.level === "atrasado") { atrasado += 1; risk += caseAmountClp(c) ?? 0; }
    if (u.level === "hoy") hoy += 1;
    if (u.level === "sin") sin += 1;
    if (u.step?.kind === "responder" && u.step.dueDays <= 0) responder += 1;
  }
  const chip = (f: Focus, n: number, label: string, tone: string) => (
    <button
      type="button"
      onClick={() => onFocus(f)}
      aria-pressed={focus === f}
      disabled={n === 0 && focus !== f}
      className={`inline-flex h-9 items-baseline gap-1.5 rounded-lg border px-3 pt-1.5 text-[12.5px] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:opacity-50 ${
        focus === f ? "border-ink bg-ink text-canvas" : "border-line bg-canvas-raised text-ink-muted hover:border-line-strong"
      }`}
      data-testid={`urgency-${f}`}
    >
      <b className={`text-[15px] tabular-nums ${focus === f ? "" : tone}`}>{n}</b>
      {label}
    </button>
  );
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Urgencia" data-testid="urgency-strip">
      {chip("atrasado", atrasado, "atrasados", "text-bad")}
      {chip("responder", responder, "esperan respuesta", "text-warn")}
      {chip("hoy", hoy, "para hoy", "text-ink")}
      {chip("sin", sin, "sin próximo paso", "text-warn")}
      {risk > 0 ? (
        <button
          type="button"
          onClick={() => onFocus("atrasado")}
          className="inline-flex h-9 items-baseline gap-1.5 rounded-lg px-2 pt-1.5 text-[12.5px] text-ink-muted hover:text-ink"
          title="Neto de las cotizaciones de los casos atrasados"
          data-testid="urgency-risk"
        >
          <b className="text-[15px] tabular-nums text-bad">{formatMoney(String(risk), "CLP")}</b> en riesgo
        </button>
      ) : null}
    </div>
  );
}

const VISTA_LABEL: Record<BoardView, string> = { seguimiento: "Seguimiento vencido", cotizar: "Por cotizar" };

/** The `?vista=` on the address, kept in step with it. */
function useBoardView(): BoardView | null {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return boardViewFromHash(hash);
}
