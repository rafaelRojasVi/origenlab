import { useState } from "react";
import { Button, Modal } from "../ui";
import type { OrganizationCandidate } from "./prospectApi";

const NEW = "__nueva__";

/**
 * «Contactar» when two or more institutions in the CRM match: the person picks the right one or
 * «Crear nueva institución». Nothing is written until they choose (DOMAIN.md §5: ambiguity fails closed).
 */
export function ContactarDialog({
  candidates,
  busy,
  onClose,
  onChoose,
}: {
  candidates: OrganizationCandidate[];
  busy: boolean;
  onClose: () => void;
  onChoose: (choice: { organization_id: string } | { create_new_organization: true }) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <Modal title="¿Cuál de estas instituciones es?" onClose={onClose} busy={busy}>
      <p className="mb-3 text-[13px] text-ink-muted">En el CRM hay más de una institución que calza. Elige la correcta.</p>
      <fieldset className="space-y-1.5">
        <legend className="sr-only">Institución</legend>
        {[...candidates.map((c) => ({ id: c.id, label: c.name })), { id: NEW, label: "Crear nueva institución" }].map((c) => (
          <label key={c.id} className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-line px-3 py-2 text-[13px] hover:border-brand-600">
            <input type="radio" name="org" value={c.id} checked={picked === c.id} onChange={() => setPicked(c.id)} className="accent-brand-700" />
            <span>{c.label}</span>
          </label>
        ))}
      </fieldset>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button
          variant="primary"
          disabled={!picked}
          busy={busy}
          onClick={() => picked && onChoose(picked === NEW ? { create_new_organization: true } : { organization_id: picked })}
        >
          Agregar al CRM
        </Button>
      </div>
    </Modal>
  );
}
