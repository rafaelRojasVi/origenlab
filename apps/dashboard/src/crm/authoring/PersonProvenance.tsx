/**
 * «También conocido como» and «Lo que sabemos» on the contact drawer: the other names this person
 * writes under, and for each learned name, address and institution where it came from. What the
 * machine proposed can be taken back («Quitar», when a rule made it) or confirmed («Confirmar»).
 */
import { useRef, useState } from "react";
import { caseRefusalText, removeAutomaticAction } from "../caseCommands";
import { Badge, Button, Section } from "../ui";
import { confirmPersonFact, newIdempotencyKey, type OtherName, type ProvenanceItem } from "./crmAuthoringApi";

const KIND_ES: Record<ProvenanceItem["kind"], string> = {
  name: "Nombre",
  address: "Correo",
  affiliation: "Institución",
};

export function OtherNames({ names }: { names: OtherName[] }) {
  if (names.length === 0) return null;
  return (
    <div data-testid="other-names">
      <Section title="También conocido como">
        <ul className="flex flex-wrap gap-1.5">
          {names.map((n) => (
            <li key={n.id}>
              <Badge tone="neutral" glyph={false}>{n.name}</Badge>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

export function ProvenanceList({ items, mayAuthor, onChanged }: {
  items: ProvenanceItem[];
  mayAuthor: boolean;
  onChanged: () => void;
}) {
  if (items.length === 0) return null;
  return (
    <Section title="Lo que sabemos">
      <ul className="divide-y divide-line rounded-md border border-line">
        {items.map((it) => (
          <ProvenanceRow key={`${it.kind}:${it.id}`} item={it} mayAuthor={mayAuthor} onChanged={onChanged} />
        ))}
      </ul>
    </Section>
  );
}

function ProvenanceRow({ item, mayAuthor, onChanged }: { item: ProvenanceItem; mayAuthor: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState<"remove" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const keys = useRef({ remove: newIdempotencyKey(), confirm: newIdempotencyKey() });

  async function run(which: "remove" | "confirm") {
    setBusy(which);
    setError(null);
    try {
      if (which === "remove" && item.receipt_id) {
        await removeAutomaticAction({ receipt_id: item.receipt_id }, keys.current.remove);
      } else {
        await confirmPersonFact({ kind: item.kind, id: item.id }, keys.current.confirm);
      }
      onChanged();
    } catch (err) {
      setError(caseRefusalText(err));
    } finally {
      setBusy(null);
    }
  }

  const canRemove = mayAuthor && item.machine && !!item.receipt_id;
  const canConfirm = mayAuthor && item.machine;
  return (
    <li data-testid="provenance-row" className="px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="min-w-[10rem] flex-1">
          <p className="font-medium text-ink">
            <span className="mr-1.5 font-normal text-ink-faint">{KIND_ES[item.kind] ?? ""}</span>
            {item.label}
          </p>
          <p className="text-[11px] text-ink-muted">{item.source_es}</p>
        </div>
        {canRemove || canConfirm ? (
          <div className="flex shrink-0 gap-2">
            {canConfirm ? (
              <Button variant="secondary" onClick={() => void run("confirm")} busy={busy === "confirm"} disabled={busy !== null}>
                Confirmar
              </Button>
            ) : null}
            {canRemove ? (
              <Button variant="quiet" onClick={() => void run("remove")} busy={busy === "remove"} busyLabel="Quitando…" disabled={busy !== null}>
                Quitar
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {error ? <p role="alert" className="mt-1 text-[11px] text-bad">{error}</p> : null}
    </li>
  );
}
