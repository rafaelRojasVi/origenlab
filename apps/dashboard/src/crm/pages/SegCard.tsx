/**
 * One case as a card: the Agenda's rows, the board's cards and the Tabla under 1280 px (phone and
 * tablet). Everything the sheet row has: the name (opens the case), amount, contact and the latest
 * note, days without an answer, the last touch, the próximo contacto, the encargado and channel
 * (to see and to change), tap-to-call and WhatsApp, the guion, «Llamé» and «⋯».
 */
import { type DragEvent } from "react";
import type { ProfileCard } from "../../api/authClient";
import type { CaseCommandReceipt } from "../caseCommands";
import { contactLine, displayName } from "../caseDisplay";
import type { SegRow } from "../seguimiento";
import { useSegUi } from "../segStore";
import { cardMoney } from "./PipelineBoard";
import { FollowUpPicker } from "./FollowUpPicker";
import { CallForm } from "./SeguimientoCallRow";
import { Actions, CaseName } from "./SeguimientoSheet";
import { ContactButtons, DaysPill, LastNote, LastTouch, NextDate, nextReminder, type RowAction } from "./SegParts";

export interface CardHandlers {
  onOpen: (id: string) => void;
  onAction: (id: string, action: RowAction) => void;
  onCallSaved: (id: string, nextSaid: string) => void;
  onReceipt: (receipt: CaseCommandReceipt) => void;
  onConflict: () => void;
  onGuion?: (id: string) => void;
}

export interface CardSelection {
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
}

export function SegCard({
  row,
  profiles,
  now,
  clock,
  mayCall,
  mayCommand,
  callsPaused,
  leaving = false,
  flash = false,
  selection = null,
  onDragStart,
  on,
}: {
  row: SegRow;
  profiles: ProfileCard[];
  now: Date;
  clock?: () => Date;
  mayCall: boolean;
  mayCommand: boolean;
  callsPaused: boolean;
  leaving?: boolean;
  flash?: boolean;
  selection?: CardSelection | null;
  /** Set on the board: the card can be dragged to another column. */
  onDragStart?: (e: DragEvent<HTMLElement>) => void;
  on: CardHandlers;
}) {
  const [ui, act] = useSegUi();
  const id = row.card.opportunity_id;
  const calling = ui.callOpen === id && !leaving;
  const money = cardMoney(row.card).text;
  const checked = selection?.selected.has(id) ?? false;
  return (
    <article
      data-flip={leaving ? undefined : id}
      data-testid={`seg-card-${id}`}
      data-flash={flash || undefined}
      draggable={onDragStart ? true : undefined}
      onDragStart={onDragStart}
      className={`min-w-0 rounded-lg border bg-canvas-raised p-3 shadow-[0_1px_2px_rgb(24_24_27/0.04)] transition-[border-color,box-shadow] hover:border-line-strong ${checked ? "border-brand-600" : "border-line"} ${onDragStart ? "cursor-grab active:cursor-grabbing" : ""} ${leaving ? "crm-row-out" : ""} ${flash ? "seg-flash" : ""}`}
    >
      <div className="flex items-start gap-2">
        {selection ? (
          <input
            type="checkbox"
            aria-label={`Seleccionar ${displayName(row.card).name}`}
            checked={checked}
            onChange={() => selection.onToggle(id)}
            className="mt-0.5 h-5 w-5 shrink-0 accent-[var(--color-brand-700)]"
          />
        ) : null}
        <div className="min-w-0 flex-1">
          <CaseName row={row} onOpen={on.onOpen} wrap />
          <div className="truncate text-[12px] text-ink-muted">{contactLine(row.card) ?? "Sin contacto"}</div>
        </div>
        <span className="shrink-0 text-[13px] font-bold tabular-nums text-ink">{money || <span className="font-normal text-ink-faint">Sin monto</span>}</span>
      </div>
      <div className="mt-1">
        <LastNote row={row} now={now} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-muted">
        <DaysPill row={row} now={now} long />
        <span className="min-w-0 leading-snug">
          <LastTouch row={row} now={now} />
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-1.5 text-[12px] text-ink-muted">
        <span className="text-ink-faint">Próximo:</span>
        <NextDate due={nextReminder(row, now)} now={now} />
      </div>
      {mayCommand ? (
        <div className="mt-2" onPointerDown={(e) => e.stopPropagation()} data-testid="seg-card-follow-up">
          <FollowUpPicker card={row.card} profiles={profiles} onChanged={on.onReceipt} onConflict={on.onConflict} announce />
        </div>
      ) : (
        <div className="mt-2 text-[12px] text-ink-muted">Encargado: {profiles.find((p) => p.id === row.ownerId)?.displayName ?? "Sin confirmar"}</div>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <ContactButtons row={row} profiles={profiles} />
        <Actions
          row={row}
          open={calling}
          mayCall={mayCall}
          paused={callsPaused}
          guionButton
          onGuion={on.onGuion ? () => on.onGuion!(id) : undefined}
          onToggle={() => act.openCall(calling ? null : id)}
          onAction={mayCommand ? (a) => on.onAction(id, a) : undefined}
        />
      </div>
      {calling ? (
        <div className="mt-3 rounded-md border border-brand-100 bg-brand-50 p-3" data-testid={`seg-call-${id}`}>
          <CallForm
            row={row}
            clock={clock}
            onCancel={() => act.openCall(null)}
            onSaved={(said) => {
              act.openCall(null);
              on.onCallSaved(id, said);
            }}
          />
        </div>
      ) : null}
    </article>
  );
}
