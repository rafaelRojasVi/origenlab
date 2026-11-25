/**
 * «Encargado» and «Próximo contacto» for one case: who follows it and by which channel. Used in Hoy's
 * case panel and, `compact`, in the Seguimiento sheet, where `only` puts each select in its own
 * column (the other value is still sent, as the card holds it). Every change is one command carrying the full
 * pair, so the API never holds a half-set state, and the next command uses the version the last
 * receipt returned (two quick changes never collide).
 */
import { useEffect, useRef, useState } from "react";
import type { ProfileCard } from "../../api/authClient";
import { OperatorApiError } from "../../api/operatorClient";
import {
  caseRefusalText,
  setCaseFollowUp,
  useMayRunCaseCommands,
  type CaseCommandReceipt,
} from "../caseCommands";
import type { OpportunityCardData } from "../crmTypes";
import { channelsFor, normalizeChannel, suggestOwner, type FollowUpChannel } from "../seguimiento";
import { Button, SelectInput, toast } from "../ui";

const CHANNEL_LABEL: Record<FollowUpChannel, string> = { llamada: "Llamada", correo: "Correo", whatsapp: "WhatsApp" };

/** The toast after a new encargado: «Asignado a Tati», or that nobody is confirmed any more. */
export function assignedText(ownerId: string | null, profiles: ProfileCard[]): string {
  const name = ownerId ? profiles.find((p) => p.id === ownerId)?.displayName : null;
  return name ? `Asignado a ${name}` : "Sin encargado confirmado";
}

/** 44 px on a phone (a finger), the usual size from `sm`. */
const SELECT_TAP = "max-sm:min-h-11 max-sm:text-[13px]";

export function FollowUpPicker({
  card,
  profiles,
  onChanged,
  compact = false,
  only,
  onConflict,
  announce = false,
}: {
  card: OpportunityCardData;
  profiles: ProfileCard[];
  onChanged: (receipt: CaseCommandReceipt) => void;
  compact?: boolean;
  /** Render just one of the two selects (a sheet column each). */
  only?: "owner" | "channel";
  /** The case changed under us (409): the page refetches it. */
  onConflict?: () => void;
  /** Say «Asignado a X» once a new encargado is recorded (Seguimiento). */
  announce?: boolean;
}) {
  const may = useMayRunCaseCommands();
  const [ownerId, setOwnerId] = useState<string | null>(card.follow_up?.operator_id ?? null);
  const [channel, setChannel] = useState<FollowUpChannel | null>(card.follow_up?.channel ?? null);
  const [busy, setBusy] = useState(false);
  // The newest version we know: the card's, or the one our last receipt returned if that is newer.
  const version = useRef(card.version ?? 0);
  const fingerprint = `${card.opportunity_id}|${card.version}|${card.follow_up?.operator_id}|${card.follow_up?.channel}`;
  useEffect(() => {
    if ((card.version ?? 0) >= version.current) {
      version.current = card.version ?? 0;
      setOwnerId(card.follow_up?.operator_id ?? null);
      setChannel(card.follow_up?.channel ?? null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fingerprint]);

  if (!may) return null;

  const hasPhone = !!card.contact?.phone;
  const suggested = ownerId ? null : suggestOwner(card, profiles);
  const shownOwner = ownerId ?? suggested;
  const shownChannel = normalizeChannel(channel, shownOwner, profiles, hasPhone);
  const suggestedName = suggested ? profiles.find((p) => p.id === suggested)?.displayName : null;

  async function save(nextOwner: string | null, nextChannel: FollowUpChannel | null) {
    if (busy) return;
    // The channel always fits the owner actually sent: no WhatsApp for anyone but its people.
    const sentChannel = normalizeChannel(nextChannel, nextOwner, profiles, hasPhone);
    setBusy(true);
    try {
      const receipt = await setCaseFollowUp({
        opportunity_id: card.opportunity_id,
        opportunity_version: version.current,
        follow_up_operator_id: nextOwner,
        channel: sentChannel,
      });
      version.current = receipt.opportunity_version;
      if (announce && nextOwner !== ownerId) toast(assignedText(nextOwner, profiles));
      setOwnerId(nextOwner);
      setChannel(sentChannel);
      onChanged(receipt);
    } catch (err) {
      toast(caseRefusalText(err), "bad");
      if (err instanceof OperatorApiError && err.status === 409) onConflict?.();
    } finally {
      setBusy(false);
    }
  }

  const ownerOptions = [
    { value: "", label: "Sin confirmar" },
    ...profiles.map((p) => ({ value: p.id, label: p.id === suggested ? `${p.displayName} (sugerido)` : p.displayName })),
  ];
  const channelOptions = channelsFor(shownOwner, profiles).map((c) => ({ value: c, label: CHANNEL_LABEL[c] }));
  // In the sheet the column header names the select; the label stays for screen readers.
  const label = compact ? "sr-only" : "mb-1 block text-[12px] font-semibold text-ink-muted";
  const ids = `follow-up-${card.opportunity_id}`;

  return (
    <div className={compact ? "flex flex-wrap items-center gap-x-3 gap-y-1" : "grid gap-3 sm:grid-cols-2"} data-testid="follow-up-picker">
      {only !== "channel" ? <div className={compact ? "flex min-w-0 items-center gap-1.5" : "min-w-0"}>
        <label htmlFor={`${ids}-owner`} className={label}>Encargado</label>
        <SelectInput
          id={`${ids}-owner`}
          value={shownOwner ?? ""}
          disabled={busy}
          className={SELECT_TAP}
          options={ownerOptions}
          onChange={(v) => void save(v === "" ? null : v, shownChannel)}
        />
        {suggested ? (
          <div className={`flex items-center gap-2 text-[12px] text-ink-muted ${compact ? "" : "mt-1"}`}>
            <span>{compact ? "Sugerido" : `${suggestedName} es el sugerido.`}</span>
            <Button
              className="max-sm:h-11"
              variant={compact ? "quiet" : "secondary"}
              busy={busy}
              aria-label={`Confirmar a ${suggestedName}`}
              onClick={() => void save(suggested, shownChannel)}
            >
              Confirmar
            </Button>
          </div>
        ) : null}
      </div> : null}
      {only !== "owner" ? <div className={compact ? "flex min-w-0 items-center gap-1.5" : "min-w-0"}>
        <label htmlFor={`${ids}-channel`} className={label}>Próximo contacto</label>
        {compact ? <span aria-hidden="true" className="text-[12px] text-ink-faint">por</span> : null}
        <SelectInput
          id={`${ids}-channel`}
          value={shownChannel}
          disabled={busy}
          className={SELECT_TAP}
          options={channelOptions}
          onChange={(v) => void save(shownOwner, v as FollowUpChannel)}
        />
      </div> : null}
    </div>
  );
}
