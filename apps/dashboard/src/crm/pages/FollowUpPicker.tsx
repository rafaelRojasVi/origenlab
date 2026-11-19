/**
 * «Encargado» and «Próximo contacto» for one case: who follows it and by which channel. Used in Hoy's
 * case panel and, `compact`, in the Seguimiento sheet. Every change is one command carrying the full
 * pair, so the API never holds a half-set state, and the next command uses the version the last
 * receipt returned (two quick changes never collide).
 */
import { useEffect, useRef, useState } from "react";
import type { ProfileCard } from "../../api/authClient";
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

export function FollowUpPicker({
  card,
  profiles,
  onChanged,
  compact = false,
}: {
  card: OpportunityCardData;
  profiles: ProfileCard[];
  onChanged: (receipt: CaseCommandReceipt) => void;
  compact?: boolean;
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
      setOwnerId(nextOwner);
      setChannel(sentChannel);
      onChanged(receipt);
    } catch (err) {
      toast(caseRefusalText(err), "bad");
    } finally {
      setBusy(false);
    }
  }

  const ownerOptions = [
    { value: "", label: "Sin asignar" },
    ...profiles.map((p) => ({ value: p.id, label: p.id === suggested ? `${p.displayName} (sugerido)` : p.displayName })),
  ];
  const channelOptions = channelsFor(shownOwner, profiles).map((c) => ({ value: c, label: CHANNEL_LABEL[c] }));
  const label = "mb-1 block text-[12px] font-semibold text-ink-muted";
  const ids = `follow-up-${card.opportunity_id}`;

  return (
    <div className={compact ? "flex flex-wrap items-end gap-2" : "grid gap-3 sm:grid-cols-2"} data-testid="follow-up-picker">
      <div className="min-w-0">
        <label htmlFor={`${ids}-owner`} className={label}>Encargado</label>
        <SelectInput
          id={`${ids}-owner`}
          value={shownOwner ?? ""}
          disabled={busy}
          options={ownerOptions}
          onChange={(v) => void save(v === "" ? null : v, shownChannel)}
        />
        {suggested ? (
          <div className="mt-1 flex items-center gap-2 text-[12px] text-ink-muted">
            <span>{suggestedName} es el sugerido.</span>
            <Button variant="secondary" busy={busy} onClick={() => void save(suggested, shownChannel)}>
              Confirmar
            </Button>
          </div>
        ) : null}
      </div>
      <div className="min-w-0">
        <label htmlFor={`${ids}-channel`} className={label}>Próximo contacto</label>
        <SelectInput
          id={`${ids}-channel`}
          value={shownChannel}
          disabled={busy}
          options={channelOptions}
          onChange={(v) => void save(shownOwner, v as FollowUpChannel)}
        />
      </div>
    </div>
  );
}
