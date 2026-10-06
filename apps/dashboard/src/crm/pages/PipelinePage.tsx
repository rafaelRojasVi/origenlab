import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCaseNotes, fetchPipeline } from "../crmApi";
import type { OpportunityCardData, RevisionCard } from "../crmTypes";
import { useMayAuthorCrm } from "../authoring/authoring";
import { NoteList } from "../authoring/NoteList";
import { CaseActions, type Mode } from "./CaseActions";
import { COLUMN_LABEL, CaseMoveForm, moveRefusal, type MoveTarget } from "./CaseMove";
import { BOARD_SORT_LABEL, Board, type BoardSort } from "./PipelineBoard";
import { DecideCases } from "./DecideCases";
import { useMayRunCaseCommands } from "../caseCommands";
import {
  HISTORICAL_STAGE_LABEL,
  ORIGIN_LABEL,
  STATUS_LABEL,
  byLatestSent,
  matchesQuery,
  pausedUntil,
  stageBasis,
  stageDisplay,
  type BoardColumnKey,
} from "../stage";
import {
  Badge,
  DisabledAction,
  Drawer,
  EmptyState,
  ExternalLink,
  Modal,
  PageHeader,
  ResourceGate,
  SearchInput,
  Section,
  Segmented,
  Skeleton,
  StatLine,
  WRITE_DISABLED_REASON,
  fmtDate,
  initials,
  LocalDriveLink,
  SuggestedTag,
  toast,
} from "../ui";
import { useResource } from "../useResource";
import { splitAddress } from "../address";

type StatusFilter = "all" | "blocked" | "pending" | "ok";
type View = "cards" | "board";

export function PipelinePage({ initialOpportunityId }: { initialOpportunityId?: string | null }) {
  const [state, reload, refreshing] = useResource(fetchPipeline);
  return (
    <div className="space-y-3">
      <PageHeader
        title="Oportunidades"
        subtitle="Casos comerciales del CRM con sus cotizaciones, revisiones, documentos y evidencia de Gmail y Drive."
        actions={<DisabledAction id="pipeline-new-disabled" reason={WRITE_DISABLED_REASON}>Nueva oportunidad</DisabledAction>}
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
  const [openId, setOpenId] = useState<string | null>(initialId);
  // A drawer opened by a drop on «Ganada» arrives with «Marcar ganada» open.
  const [openWith, setOpenWith] = useState<{ mode: Exclude<Mode, null>; target?: MoveTarget } | null>(null);
  const [moving, setMoving] = useState<{ id: string; to: MoveTarget } | null>(null);
  const [sort, setSort] = useState<BoardSort>("recent");
  const [deciding, setDeciding] = useState(false);
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

  const counts = useMemo(() => {
    const c = { all: items.length, blocked: 0, pending: 0, ok: 0 };
    for (const i of items) c[i.status] += 1;
    return c;
  }, [items]);

  const visible = useMemo(
    () => items.filter((i) => (status === "all" || i.status === status) && matchesQuery(i, q)).sort(byLatestSent),
    [items, status, q],
  );
  const quotes = items.reduce((n, i) => n + i.quotes.length, 0);
  const revisions = items.reduce((n, i) => n + i.revision_count, 0);
  const withDrive = items.filter((i) => i.drive_folder).length;
  const historicalCards = useMemo(() => items.filter((i) => stageBasis(i) === "historical_import"), [items]);
  const historical = historicalCards.length;
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
        <SearchInput value={q} onChange={setQ} label="Buscar oportunidades" placeholder="Institución, número, contacto…" />
        <span className="text-xs text-ink-faint tabular-nums">{visible.length} resultados</span>
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

      {deciding ? (
        <DecideCases cards={historicalCards} onApplied={onChanged} onClose={() => setDeciding(false)} />
      ) : visible.length === 0 ? (
        <EmptyState title="Ninguna oportunidad coincide">Cambia el filtro de estado o la búsqueda.</EmptyState>
      ) : view === "cards" ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
          {visible.map((card, i) => (
            <OpportunityCard key={card.opportunity_id} card={card} index={i} onOpen={(id) => open(id)} />
          ))}
        </div>
      ) : (
        <Board cards={visible} onOpen={(id) => open(id)} onMove={mayDecide ? onMove : undefined} sort={sort} />
      )}

      <OpportunityDrawer
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

function Dot({ ok }: { ok: boolean }) {
  return <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? "bg-good" : "bg-line-strong"}`} />;
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
          {card.drive_folder ? (
            <a
              href={card.drive_folder.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-good hover:underline"
              aria-label={`Abrir carpeta de Drive de ${card.organization?.name ?? card.title} (enlace de registro local)`}
              title="Enlace tomado del registro local del archivo de Drive; no se consultó Drive en vivo."
            >
              <Dot ok /> Drive <span className="text-[9px] font-semibold uppercase text-ink-faint">local</span>
            </a>
          ) : (
            <span className="inline-flex items-center gap-1 font-medium text-warn">
              <Dot ok={false} /> Drive: falta
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
            <span className="inline-flex items-center gap-1 font-medium text-warn">
              <Dot ok={false} /> Gmail: falta
            </span>
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

function RevisionRow({ rev }: { rev: RevisionCard }) {
  return (
    <li className="relative pl-5">
      <span
        aria-hidden="true"
        className={`absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full border-2 ${rev.is_active ? "border-brand-600 bg-brand-50" : "border-line-strong bg-canvas-raised"}`}
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-semibold tabular-nums text-ink">r{rev.revision_no}</span>
        <span className="text-xs text-ink-muted">{fmtDate(rev.sent_at)}</span>
        <Badge tone={rev.status === "sent" ? "good" : rev.status === "void" ? "bad" : "neutral"}>
          {rev.status === "sent" ? "Enviada" : rev.status === "void" ? "Anulada" : rev.status}
        </Badge>
        {rev.superseded_by_revision_no ? (
          <Badge tone="neutral" glyph={false}>
            Reemplazada por r{rev.superseded_by_revision_no}
          </Badge>
        ) : rev.is_active ? (
          <Badge tone="brand" glyph={false}>
            Vigente
          </Badge>
        ) : null}
      </div>
      <p className="mt-0.5 truncate text-[11px] text-ink-muted" title={rev.document?.filename ?? undefined}>
        {rev.document?.filename ?? "Documento sin nombre registrado"}
        {rev.origin ? <span className="text-ink-faint"> · {ORIGIN_LABEL[rev.origin] ?? rev.origin}</span> : null}
      </p>
      <p className="mt-0.5 flex flex-wrap gap-x-3 text-[11px]">
        {rev.drive ? (
          <LocalDriveLink href={rev.drive.file_url} label={`Abrir PDF r${rev.revision_no} en Drive`}>
            PDF en Drive
          </LocalDriveLink>
        ) : (
          <span className="text-warn">PDF no archivado en Drive</span>
        )}
        {rev.gmail ? (
          <ExternalLink href={rev.gmail.url} label={`Abrir correo de r${rev.revision_no} en Gmail`}>
            Correo en Gmail
          </ExternalLink>
        ) : (
          <span className="text-warn">Sin correo vinculado</span>
        )}
      </p>
      {rev.document?.sha256 ? (
        <p className="mt-0.5 font-mono text-[10px] text-ink-faint" title={rev.document.sha256}>
          sha256 {rev.document.sha256.slice(0, 16)}…
        </p>
      ) : null}
    </li>
  );
}

function OpportunityDrawer({
  card,
  onClose,
  onChanged,
  refreshing = false,
  initial = null,
}: {
  card: OpportunityCardData | null;
  onClose: () => void;
  onChanged: () => void;
  refreshing?: boolean;
  initial?: { mode: Exclude<Mode, null>; target?: MoveTarget } | null;
}) {
  const mayAuthor = useMayAuthorCrm();
  // «Registrar seguimiento» opens the note form of *this* case once. The signal belongs to the
  // case it was pressed on and is dropped when the drawer closes or another case opens, so the
  // form never pops open by itself on the next case.
  const [followUp, setFollowUp] = useState<{ id: string; n: number } | null>(null);
  const caseId = card?.opportunity_id ?? null;
  useEffect(() => {
    setFollowUp((f) => (f && f.id === caseId ? f : null));
  }, [caseId]);
  if (!card) return null;
  const openSignal = followUp && followUp.id === card.opportunity_id ? followUp.n : 0;
  const status = STATUS_LABEL[card.status];
  const stage = stageDisplay(card);
  const historical = stageBasis(card) === "historical_import";
  return (
    <Drawer
      open
      onClose={onClose}
      busy={refreshing}
      title={card.organization?.name ?? "Sin institución"}
      subtitle={
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="truncate">{card.title}</span>
        </span>
      }
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={stage.tone} glyph={false} title={stage.title}>
          {stage.label}
        </Badge>
        {historical ? (
          <Badge tone="warn" title="Ningún operador ha confirmado en el CRM el estado actual de este caso.">
            Estado actual sin verificar
          </Badge>
        ) : null}
        <Badge tone={status.tone}>{status.label}</Badge>
        {card.organization?.confirmation === "machine_proposed" ? (
          <Badge tone="warn" title="La institución fue propuesta por máquina y no la ha confirmado un operador">
            Institución sin confirmar
          </Badge>
        ) : null}
        <span className="text-[11px] text-ink-faint">Actualizada {fmtDate(card.updated_at)}</span>
      </div>

      {card.next_action.source === "task" ? (
        <Section title="Próxima tarea">
          <p className="text-[13px] text-ink">{card.next_action.text}</p>
          <p className="mt-0.5 text-[11px] text-ink-faint">
            Para el {fmtDate(card.next_action.due_at)}
            {card.open_tasks?.[0]?.owner ? ` · ${card.open_tasks[0].owner}` : ""}
            {(card.open_tasks?.length ?? 0) > 1 ? ` · ${(card.open_tasks?.length ?? 0) - 1} más` : ""}
          </p>
        </Section>
      ) : (
        <Section title="Siguiente paso sugerido" aside={<SuggestedTag />}>
          <p className="text-[13px] text-ink">{card.next_action.text}</p>
          <p className="mt-0.5 text-[11px] text-ink-faint">Sugerido a partir del estado del caso — el caso no tiene tareas abiertas.</p>
        </Section>
      )}

      {card.attention.length > 0 ? (
        <Section title="Bloqueos y pendientes">
          <ul className="space-y-1">
            {card.attention.map((a) => (
              <li key={a.code} className="flex items-start gap-1.5 text-xs">
                <Badge tone={a.blocking ? "bad" : "warn"}>{a.blocking ? "Bloquea" : "Pendiente"}</Badge>
                <span className="pt-px text-ink-muted">{a.label}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Institución y contacto">
        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 text-xs">
          <dt className="text-ink-faint">Solicitante</dt>
          <dd className="text-ink">{card.organization?.name ?? <span className="text-bad">Sin institución</span>}</dd>
          {card.other_organizations.map((o) => (
            <div key={`${o.organization_id}-${o.role}`} className="contents">
              <dt className="text-ink-faint">{o.role === "supplier" ? "Proveedor" : o.role === "manufacturer" ? "Fabricante" : o.role}</dt>
              <dd className="text-ink">{o.name}</dd>
            </div>
          ))}
          <dt className="text-ink-faint">Contacto</dt>
          <dd className="min-w-0 break-words text-ink">
            {card.contact ? (
              <>
                {card.contact.name ?? card.contact.address}
                {card.contact.others > 0 ? <span className="text-ink-faint"> y {card.contact.others} más</span> : null}
                <span className="block text-[11px] text-ink-faint">
                  {card.contact.source === "gmail_recipient"
                    ? "Destinatario del correo de la cotización (evidencia de Gmail) — no es una persona registrada en el CRM"
                    : "Participante registrado en el CRM"}
                </span>
              </>
            ) : (
              <span className="text-ink-faint">Sin contacto</span>
            )}
          </dd>
        </dl>
      </Section>

      <Section
        title={`Cotizaciones (${card.quotes.length})`}
        aside={
          card.drive_folder ? (
            <span className="text-xs">
              <LocalDriveLink href={card.drive_folder.url} label="Abrir carpeta del caso en Drive">
                Carpeta del caso
              </LocalDriveLink>
            </span>
          ) : null
        }
      >
        {card.quotes.length === 0 ? (
          <p className="rounded-md border border-dashed border-line-strong px-3 py-3 text-xs text-ink-muted">
            Este caso no tiene cotizaciones registradas.
          </p>
        ) : (
          <div className="space-y-3">
            {card.quotes.map((q) => (
              <div key={q.quote_id} className="rounded-md border border-line p-2.5">
                <div className="mb-2 flex items-center gap-2">
                  <span className="text-[13px] font-semibold tabular-nums text-ink">{q.quote_number}</span>
                  {q.number_origin === "printed_historical" ? (
                    <span className="text-[11px] text-ink-faint">número impreso en el PDF</span>
                  ) : null}
                  <span className="ml-auto text-[11px] text-ink-faint">
                    {q.revisions.length} {q.revisions.length === 1 ? "revisión" : "revisiones"}
                  </span>
                </div>
                <ol className="space-y-2.5 border-l border-line pl-0 [&>li]:-ml-[5px]">
                  {[...q.revisions].reverse().map((r) => (
                    <RevisionRow key={r.revision_id} rev={r} />
                  ))}
                </ol>
              </div>
            ))}
          </div>
        )}
        {card.drive_folder ? (
          <p className="mt-1.5 text-[10px] text-ink-faint">
            Enlaces de Drive desde los registros del archivo de casos (aún no están en el CRM).
          </p>
        ) : null}
      </Section>

      <Section title="Acciones">
        <CaseActions
          card={card}
          initial={initial}
          onChanged={onChanged}
          onFollowUp={() =>
            setFollowUp((f) => ({ id: card.opportunity_id, n: (f && f.id === card.opportunity_id ? f.n : 0) + 1 }))
          }
        />
      </Section>

      <CaseNotes key={card.opportunity_id} opportunityId={card.opportunity_id} mayAuthor={mayAuthor} openSignal={openSignal} />

      <p className="font-mono text-[10px] text-ink-faint">opportunity {card.opportunity_id}</p>
    </Drawer>
  );
}

/** The case's notes, read from the API and re-read after «Registrar seguimiento». */
function CaseNotes({ opportunityId, mayAuthor, openSignal }: { opportunityId: string; mayAuthor: boolean; openSignal: number }) {
  const load = useCallback(() => fetchCaseNotes(opportunityId), [opportunityId]);
  const [state, reload] = useResource(load, [opportunityId]);
  return (
    <>
      <NoteList
        notes={state.kind === "ready" ? state.data.notes : []}
        subjectKind="opportunity"
        subjectId={opportunityId}
        mayAuthor={mayAuthor}
        onRefresh={reload}
        openSignal={openSignal}
      />
      {state.kind === "loading" ? <p className="-mt-2 text-[11px] text-ink-faint">Cargando notas…</p> : null}
      {state.kind !== "ready" && state.kind !== "loading" ? (
        <p className="-mt-2 text-[11px] text-warn" data-testid="case-notes-unavailable">
          No se pudieron leer las notas de este caso{state.kind === "unavailable" ? " (no habilitado en este entorno)" : ""}.
        </p>
      ) : null}
    </>
  );
}
