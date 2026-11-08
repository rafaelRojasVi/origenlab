import { useEffect, useMemo, useState } from "react";
import { fetchPipeline } from "../crmApi";
import { crmHash } from "../crmRoute";
import { CASE_ORIGINS, NO_ORIGIN_LABEL, ORIGIN_LABEL, matchesOrigin, originLabel, type OriginFilter } from "../caseOrigin";
import type { OpportunityCardData } from "../crmTypes";
import { CaseDrawer } from "../drawer/CaseDrawer";
import type { Mode } from "./CaseActions";
import { COLUMN_LABEL, CaseMoveForm, moveRefusal, type MoveTarget } from "./CaseMove";
import { BOARD_SORT_LABEL, Board, type BoardSort } from "./PipelineBoard";
import { DecideCases, reviewableOpenCards } from "./DecideCases";
import { applyCaseReceipt, onCaseReceipt, useMayRunCaseCommands } from "../caseCommands";
import { quoteToOpen, useOpenOrCreateQuote } from "../quotes/openOrCreateQuote";
import { awaitsQuote, isCotizarCase } from "../today";
import { boardViewFromHash, needsFollowUp, type BoardView } from "../goals";
import { contactLine, isDelivered } from "../caseDisplay";
import { inSharedMailbox } from "../gmailLinks";
import {
  HISTORICAL_STAGE_LABEL,
  STATUS_LABEL,
  byLatestActivity,
  matchesQuery,
  pausedUntil,
  stageBasis,
  stageDisplay,
  type BoardColumnKey,
} from "../stage";
import {
  Badge,
  Button,
  EmptyState,
  Modal,
  PageHeader,
  ResourceGate,
  SearchInput,
  Segmented,
  Skeleton,
  StatLine,
  fmtDate,
  initials,
  toast,
} from "../ui";
import { GrowingListEnd } from "../GrowingListEnd";
import { useGrowingList } from "../useGrowingList";
import { useResource } from "../useResource";
import { splitAddress } from "../address";

type StatusFilter = "all" | "blocked" | "pending" | "ok";
type View = "cards" | "board";

export function PipelinePage({ initialOpportunityId }: { initialOpportunityId?: string | null }) {
  const [state, reload, refreshing, patch] = useResource(fetchPipeline);
  // Move the card the moment a command is recorded; the reload that follows confirms the rest.
  useEffect(
    () => onCaseReceipt((receipt) => patch((data) => ({ ...data, items: applyCaseReceipt(data.items, receipt) }))),
    [patch],
  );
  return (
    <div className="space-y-3">
      <PageHeader
        title="Oportunidades"
        subtitle="Casos comerciales del CRM con sus cotizaciones, revisiones, documentos y evidencia de Gmail y Drive."
      />
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
  const [status, setStatus] = useState<StatusFilter>("all");
  const [view, setView] = useState<View>("board");
  const [q, setQ] = useState("");
  const [origin, setOrigin] = useState<OriginFilter>("all");
  const [openId, setOpenId] = useState<string | null>(initialId);
  // A drawer opened by a drop on «Ganada» arrives with «Marcar ganada» open.
  const [openWith, setOpenWith] = useState<{ mode: Exclude<Mode, null>; target?: MoveTarget } | null>(null);
  const [moving, setMoving] = useState<{ id: string; to: MoveTarget } | null>(null);
  const [sort, setSort] = useState<BoardSort>("recent");
  const [deciding, setDeciding] = useState<false | "historical" | "open">(false);
  const mayDecide = useMayRunCaseCommands();

  function open(id: string | null, withForm: typeof openWith = null) {
    setOpenWith(withForm);
    setOpenId(id);
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

  // `?vista=seguimiento|cotizar` (Hoy's goal tiles): only the cases that tile counts, in Tablero
  // and Tarjetas alike, until «Quitar filtro».
  const vista = useBoardView();
  const viewItems = useMemo(() => {
    if (vista === null) return items;
    const now = new Date();
    return items.filter((c) => (vista === "seguimiento" ? needsFollowUp(c, now) : isCotizarCase(c, now)));
  }, [items, vista]);

  const counts = useMemo(() => {
    const c = { all: viewItems.length, blocked: 0, pending: 0, ok: 0 };
    for (const i of viewItems) c[i.status] += 1;
    return c;
  }, [viewItems]);

  const visible = useMemo(
    () =>
      viewItems
        .filter((i) => (status === "all" || i.status === status) && matchesOrigin(i, origin) && matchesQuery(i, q))
        .sort(byLatestActivity),
    [viewItems, status, origin, q],
  );
  const quotes = items.reduce((n, i) => n + i.quotes.length, 0);
  const revisions = items.reduce((n, i) => n + i.revision_count, 0);
  const withDrive = items.filter((i) => i.drive_folder).length;
  const historicalCards = useMemo(() => items.filter((i) => stageBasis(i) === "historical_import"), [items]);
  const historical = historicalCards.length;
  const openCards = useMemo(() => reviewableOpenCards(items), [items]);
  const paused = items.filter((i) => pausedUntil(i)).length;
  const openCard = items.find((i) => i.opportunity_id === openId) ?? null;
  const movingCard = moving ? (items.find((i) => i.opportunity_id === moving.id) ?? null) : null;

  if (items.length === 0) {
    return <EmptyState title="Sin oportunidades en el CRM">El CRM no tiene casos comerciales todavía.</EmptyState>;
  }

  return (
    <>
      <StatLine
        items={[
          { label: "Oportunidades", value: items.length },
          { label: "Cotizaciones", value: quotes },
          { label: "Revisiones", value: revisions },
          { label: "Con carpeta Drive", value: `${withDrive}/${items.length}` },
          { label: "En pausa", value: paused },
          { label: "Bloqueadas", value: counts.blocked, tone: counts.blocked ? "bad" : undefined },
        ]}
      />
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
              onClick={() => setDeciding("historical")}
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
          label="Estado"
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: "Todas", count: counts.all },
            { value: "blocked", label: "Bloqueadas", count: counts.blocked },
            { value: "pending", label: "Pendientes", count: counts.pending },
            { value: "ok", label: "Al día", count: counts.ok },
          ]}
        />
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
        <SearchInput value={q} onChange={setQ} label="Buscar oportunidades" placeholder="Institución, número, contacto…" />
        <span className="text-xs text-ink-faint tabular-nums">{visible.length} resultados</span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {mayDecide && !deciding && openCards.length > 0 ? (
            <button
              type="button"
              onClick={() => setDeciding("open")}
              className="inline-flex h-8 items-center rounded-md border border-line-strong bg-canvas-raised px-3 text-xs font-medium text-ink hover:border-ink-faint"
            >
              Revisar casos abiertos ({openCards.length})
            </button>
          ) : null}
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
          <Segmented
            label="Vista"
            value={view}
            onChange={setView}
            options={[
              { value: "cards", label: "Tarjetas" },
              { value: "board", label: "Tablero" },
            ]}
          />
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
        <DecideCases
          cards={deciding === "open" ? openCards : historicalCards}
          mode={deciding}
          onApplied={onChanged}
          onClose={() => setDeciding(false)}
        />
      ) : visible.length === 0 ? (
        <EmptyState title="Ninguna oportunidad coincide">Cambia el filtro de estado u origen, o la búsqueda.</EmptyState>
      ) : view === "cards" ? (
        <CardGrid cards={visible} onOpen={(id) => open(id)} />
      ) : (
        <Board cards={visible} onOpen={(id) => open(id)} onMove={mayDecide ? onMove : undefined} sort={sort} />
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

/** Every case as a card, drawn in slices: the list holds closed cases too. */
function CardGrid({ cards, onOpen }: { cards: OpportunityCardData[]; onOpen: (id: string) => void }) {
  const { shown, hidden, showMore, endRef } = useGrowingList(cards);
  return (
    <>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {shown.map((card, i) => (
          <OpportunityCard key={card.opportunity_id} card={card} index={i} onOpen={onOpen} />
        ))}
      </div>
      <div className="mt-3">
        <GrowingListEnd hidden={hidden} onMore={showMore} endRef={endRef} />
      </div>
    </>
  );
}

function Dot({ ok }: { ok: boolean }) {
  return <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? "bg-good" : "bg-line-strong"}`} />;
}

const PRE_QUOTE_STAGES_CARD = new Set(["lead", "qualifying", "qualified"]);

/** True for an open case at a pre-quote stage with no sent quote — shows the «Por cotizar» card variant. */
function isSolicitudSinCotizar(card: OpportunityCardData): boolean {
  return (
    !card.closed_at &&
    PRE_QUOTE_STAGES_CARD.has(card.stage) &&
    awaitsQuote(card)
  );
}

/** «Por cotizar» card: who, what they asked for, days waiting and two action buttons. */
function SolicitudSinCotizarCard({
  card,
  index,
  onOpen,
}: {
  card: OpportunityCardData;
  index: number;
  onOpen: (id: string) => void;
}) {
  const contact = contactLine(card);
  const startAt = card.last_contact?.inbound?.at ?? card.created_at ?? null;
  const waitMs = startAt ? Math.max(0, Date.now() - Date.parse(startAt)) : 0;
  const waitDays = Math.floor(waitMs / 86_400_000);
  const waitHours = Math.floor(waitMs / 3_600_000);
  const waitLabel = waitHours < 24 ? `${waitHours} h` : `${waitDays} d`;
  const waitToneClass = waitDays >= 14 ? "bg-bad-bg text-bad" : waitDays >= 3 ? "bg-warn-bg text-warn" : "bg-good-bg text-good";
  const waitFullLabel = waitDays > 0 ? `${waitDays} ${waitDays === 1 ? "día" : "días"} esperando` : `${waitHours} ${waitHours === 1 ? "hora" : "horas"} esperando`;

  const solicitudItem = card.title.match(/^[Ss]olicitud\s*[—–-]+\s*(.+)$/)?.[1]?.trim()
    ?? card.last_contact?.inbound?.subject?.trim()
    ?? card.title;
  const originUrl = card.last_contact?.inbound?.url
    ? (inSharedMailbox(card.last_contact.inbound.url) ?? card.last_contact.inbound.url)
    : null;
  // Sales and admin go straight to the Cotizador; anyone else opens the case, as before.
  const mayQuote = useMayRunCaseCommands();
  const quote = useOpenOrCreateQuote();

  return (
    <article
      data-testid={`opportunity-card-${card.opportunity_id}`}
      style={{ animationDelay: `${Math.min(index, 12) * 18}ms` }}
      className="crm-card-in group flex flex-col gap-2 rounded-md border border-brand-600/30 bg-canvas-raised px-3 py-2.5 shadow-[0_1px_1px_rgb(24_24_27/0.03)] transition-[box-shadow,border-color] duration-150 focus-within:border-brand-600/50 hover:border-brand-600/50 hover:shadow-sm"
    >
      {/* Header: «Por cotizar» chip + days pill */}
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center rounded-full bg-brand-100 px-2 py-0.5 text-[11px] font-semibold text-accent" data-testid="por-cotizar-chip">
          Por cotizar
        </span>
        <span
          className={`ml-auto inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-bold tabular-nums ${waitToneClass}`}
          aria-label={waitFullLabel}
          data-testid="por-cotizar-wait"
        >
          {waitLabel} esperando
        </span>
      </div>
      {card.origin ? (
        <p className="text-[11px] leading-4 text-ink-faint" data-testid="card-origin">
          Origen: {originLabel(card.origin)}
        </p>
      ) : null}

      {/* Institution + contact */}
      <button
        type="button"
        onClick={() => onOpen(card.opportunity_id)}
        aria-haspopup="dialog"
        className="block w-full min-w-0 rounded-sm text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
      >
        <span className="block truncate text-[13px] font-semibold leading-5 text-ink">
          {card.organization?.name ?? <span className="text-bad">Sin institución</span>}
        </span>
        {contact ? <span className="block truncate text-xs leading-4 text-ink-muted">{contact}</span> : null}
      </button>

      {/* «Pide» box */}
      <div className="rounded-md bg-canvas-sunken px-2.5 py-1.5 text-[12px]" data-testid="pide-box">
        <span className="text-[11px] text-ink-faint">Pide</span>
        <p className="mt-0.5 line-clamp-2 text-ink">{solicitudItem}</p>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2">
        <Button
          variant="primary"
          busy={quote.busy}
          busyLabel="Creando…"
          className="min-h-[44px]"
          onClick={() => (mayQuote ? void quote.run(card) : onOpen(card.opportunity_id))}
        >
          {mayQuote && quoteToOpen(card) ? "Continuar cotización" : "Cotizar"}
        </Button>
        {originUrl ? (
          <a
            href={originUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-7 items-center rounded-md border border-line bg-canvas-raised px-3 text-xs text-ink hover:border-line-strong"
          >
            Ver pedido ↗
          </a>
        ) : null}
      </div>
    </article>
  );
}

export function OpportunityCard({
  card,
  index,
  onOpen,
}: {
  card: OpportunityCardData;
  index: number;
  onOpen: (id: string) => void;
}) {
  // A case at a pre-quote stage with no sent quote uses the «Por cotizar» simplified card.
  if (isSolicitudSinCotizar(card)) {
    return <SolicitudSinCotizarCard card={card} index={index} onOpen={onOpen} />;
  }

  const latest = card.latest_revision;
  const blocking = card.attention.find((a) => a.blocking);
  const status = STATUS_LABEL[card.status];
  const stage = stageDisplay(card);
  // The quote of the latest revision leads; the others follow as "+N".
  const numbers = latest
    ? [latest.quote_number, ...card.quote_numbers.filter((n) => n !== latest.quote_number)]
    : card.quote_numbers;
  const contact = card.contact?.address ? splitAddress(card.contact.address) : null;
  return (
    <article
      data-testid={`opportunity-card-${card.opportunity_id}`}
      style={{ animationDelay: `${Math.min(index, 12) * 18}ms` }}
      className="crm-card-in group flex flex-col rounded-md border border-line bg-canvas-raised px-3 py-2.5 shadow-[0_1px_1px_rgb(24_24_27/0.03)] transition-[box-shadow,border-color] duration-150 focus-within:border-brand-600/50 hover:border-line-strong hover:shadow-sm"
    >
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 truncate text-xs font-semibold tracking-wide text-ink tabular-nums" title={numbers.join(", ")}>
          {numbers.length ? numbers[0] : "Sin cotización"}
          {numbers.length > 1 ? <span className="font-normal text-ink-faint"> +{numbers.length - 1}</span> : null}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <Badge tone={stage.tone} glyph={false} title={stage.title}>
            {stage.label}
          </Badge>
          {card.status !== "ok" ? <Badge tone={status.tone}>{status.label}</Badge> : null}
        </span>
      </div>

      <button
        type="button"
        onClick={() => onOpen(card.opportunity_id)}
        aria-haspopup="dialog"
        className="mt-1 block w-full min-w-0 rounded-sm text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
      >
        <span className="block truncate text-[13px] font-semibold leading-5 text-ink">
          {card.organization?.name ?? <span className="text-bad">Sin institución</span>}
        </span>
        <span className="block truncate text-xs leading-4 text-ink-muted">{card.title}</span>
      </button>

      <dl className="mt-2 grid grid-cols-[auto_1fr] items-center gap-x-2.5 gap-y-1 text-[11px] leading-4">
        <dt className="text-ink-faint">Contacto</dt>
        <dd className="min-w-0 truncate text-ink-muted" title={contact?.email ?? undefined}>
          {card.contact ? (
            <>
              {card.contact.name ?? contact?.display ?? contact?.email}
              {card.contact.others > 0 ? <span className="text-ink-faint"> +{card.contact.others}</span> : null}
              {card.contact.source === "gmail_recipient" ? <span className="text-ink-faint"> · destinatario</span> : null}
            </>
          ) : (
            <span className="text-ink-faint">Sin contacto</span>
          )}
        </dd>
        <dt className="text-ink-faint">Origen</dt>
        <dd className={`min-w-0 truncate ${card.origin ? "text-ink-muted" : "text-ink-faint"}`} data-testid="card-origin">
          {originLabel(card.origin)}
        </dd>
        <dt className="text-ink-faint">Revisión</dt>
        <dd className="min-w-0 truncate">
          {latest ? (
            <span className="text-ink">
              <span className="font-semibold tabular-nums">
                {card.quotes.length > 1 ? `${latest.quote_number} ` : ""}r{latest.revision_no}
              </span>
              <span className="text-ink-muted"> · {fmtDate(latest.sent_at)}</span>
              {card.revision_count > 1 ? <span className="text-ink-faint"> · {card.revision_count} revisiones</span> : null}
            </span>
          ) : (
            <span className="inline-flex rounded border border-dashed border-line-strong px-1 text-ink-muted">Sin revisión</span>
          )}
        </dd>
        <dt className="text-ink-faint">Documento</dt>
        <dd className="min-w-0 truncate text-ink-muted" title={latest?.document?.filename ?? undefined}>
          {latest?.document?.filename ?? (latest?.document ? "PDF sin nombre" : "—")}
        </dd>
        <dt className="text-ink-faint">Evidencia</dt>
        <dd className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          {latest && !isDelivered(latest) ? (
            <span className="text-ink-faint">En el Cotizador, sin enviar</span>
          ) : (
            <>
          {card.drive_folder ? (
            <a
              href={card.drive_folder.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-good hover:underline"
              aria-label={`Abrir carpeta de Drive de ${card.organization?.name ?? card.title}`}
              title="Carpeta del caso en Drive"
            >
              <Dot ok /> Drive <span className="text-[9px] font-semibold uppercase text-ink-faint">local</span>
            </a>
          ) : (
            <span className="inline-flex items-center gap-1 text-ink-faint">
              <Dot ok={false} /> sin Drive
            </span>
          )}
          {latest?.gmail ? (
            <a
              href={latest.gmail.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-good hover:underline"
              aria-label={`Abrir correo de Gmail de ${latest.quote_number}`}
            >
              <Dot ok /> Gmail
            </a>
          ) : (
            <span className="inline-flex items-center gap-1 text-ink-faint">
              <Dot ok={false} /> sin Gmail
            </span>
          )}
            </>
          )}
        </dd>
      </dl>

      {blocking ? (
        <p className="mt-2 rounded bg-bad-bg px-1.5 py-1 text-[11px] font-medium leading-4 text-bad">
          <span aria-hidden="true">✕ </span>
          {blocking.label}
        </p>
      ) : null}

      <div className="mt-auto flex items-center gap-2 border-t border-line/70 pt-1.5 text-[11px] leading-4 [margin-top:0.5rem]">
        <p className="min-w-0 flex-1 truncate text-ink-muted" title={`${card.next_action.text} — sugerencia, no es una tarea del CRM`}>
          <span className="text-ink-faint">Sugerencia · </span>
          {card.next_action.text}
        </p>
        <span
          className="inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border border-line bg-canvas-sunken text-[9px] font-semibold text-ink-muted"
          title={card.organization?.name ?? ""}
          aria-hidden="true"
        >
          {initials(card.organization?.name)}
        </span>
      </div>
    </article>
  );
}
