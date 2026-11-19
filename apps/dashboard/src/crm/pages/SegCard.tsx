/**
 * One case as a card: the Agenda's rows, the board's cards and the sheet on a phone. Name (opens
 * the case), amount, days without an answer, last touch, the next contact, and «Llamé».
 */
import { useState, type DragEvent } from "react";
import type { ProfileCard } from "../../api/authClient";
import type { CaseCommandReceipt } from "../caseCommands";
import { contactLine } from "../caseDisplay";
import type { SegRow } from "../seguimiento";
import { cardMoney } from "./PipelineBoard";
import { FollowUpPicker } from "./FollowUpPicker";
import { CallForm } from "./SeguimientoCallRow";
import { Actions, CaseName } from "./SeguimientoSheet";
import { DaysPill, LastTouch, NextDate, PhoneLink, nextReminder, type RowAction } from "./SegParts";

export interface CardHandlers {
  onOpen: (id: string) => void;
  onAction: (id: string, action: RowAction) => void;
  onCallSaved: (id: string, nextSaid: string) => void;
  onReceipt: (receipt: CaseCommandReceipt) => void;
  onConflict: () => void;
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
  ownerPicker = false,
  showPhone = false,
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
  /** The encargado picker on the card (the board's keyboard and touch way to move it). */
  ownerPicker?: boolean;
  showPhone?: boolean;
  /** Set on the board: the card can be dragged to another column. */
  onDragStart?: (e: DragEvent<HTMLElement>) => void;
  on: CardHandlers;
}) {
  const [calling, setCalling] = useState(false);
  const id = row.card.opportunity_id;
  const money = cardMoney(row.card).text;
  return (
    <article
      data-flip={leaving ? undefined : id}
      data-testid={`seg-card-${id}`}
      draggable={onDragStart ? true : undefined}
      onDragStart={onDragStart}
      className={`rounded-lg border border-line bg-canvas-raised p-3 shadow-[0_1px_2px_rgb(24_24_27/0.04)] transition-[border-color,box-shadow] hover:border-line-strong ${onDragStart ? "cursor-grab active:cursor-grabbing" : ""} ${leaving ? "crm-row-out" : ""}`}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <CaseName row={row} onOpen={on.onOpen} />
          <div className="truncate text-[12px] text-ink-muted">{contactLine(row.card) ?? "Sin contacto"}</div>
        </div>
        <span className="shrink-0 text-[13px] font-bold tabular-nums text-ink">{money || <span className="font-normal text-ink-faint">Sin monto</span>}</span>
      </div>
      {showPhone ? (
        <div className="mt-1 text-[12.5px]">
          <PhoneLink row={row} />
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-muted">
        <DaysPill row={row} now={now} />
        <span className="min-w-0 leading-snug">
          <LastTouch row={row} now={now} />
        </span>
      </div>
      <div className="mt-1 flex items-center gap-1.5 text-[12px] text-ink-muted">
        <span className="text-ink-faint">Próximo:</span>
        <NextDate due={nextReminder(row)} now={now} />
      </div>
      {ownerPicker && mayCommand ? (
        <div className="mt-2" onPointerDown={(e) => e.stopPropagation()}>
          <FollowUpPicker card={row.card} profiles={profiles} onChanged={on.onReceipt} onConflict={on.onConflict} compact only="owner" />
        </div>
      ) : null}
      <div className="mt-2">
        <Actions
          row={row}
          open={calling}
          mayCall={mayCall}
          paused={callsPaused}
          onToggle={() => setCalling((v) => !v)}
          onAction={mayCommand ? (a) => on.onAction(id, a) : undefined}
        />
      </div>
      {calling && !leaving ? (
        <div className="mt-3 rounded-md border border-brand-100 bg-brand-50 p-3" data-testid={`seg-call-${id}`}>
          <CallForm
            row={row}
            clock={clock}
            onCancel={() => setCalling(false)}
            onSaved={(said) => {
              setCalling(false);
              on.onCallSaved(id, said);
            }}
          />
        </div>
      ) : null}
    </article>
  );
}
