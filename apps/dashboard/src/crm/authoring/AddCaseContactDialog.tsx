/**
 * AddCaseContactDialog — turns a case's quote recipient (Gmail evidence) into a CRM person on the case.
 *
 * One button, two commands, in this order:
 *   1. `add-case-participant` by email — when a CRM person already holds the address, that person is
 *      linked and nothing is created;
 *   2. only on `no_crm_person_for_address`: `create-person` with the address (and the case's
 *      institution as affiliation), then `add-case-participant` by the new person's id.
 * Each command keeps its own idempotency key across retries, so a retry after a failed second step
 * never creates the person twice.
 */
import { useRef, useState } from "react";
import { splitAddress } from "../address";
import { FormField, Modal, SelectInput, TextInput } from "../ui";
import {
  addCaseParticipant,
  createPerson,
  newIdempotencyKey,
  refusalOf,
  type ParticipantRole,
} from "./crmAuthoringApi";

export const PARTICIPANT_ROLE_LABEL: Record<ParticipantRole, string> = {
  quote_recipient: "Destinatario de la cotización",
  end_user: "Usuario final",
  technical: "Contacto técnico",
  purchasing: "Compras / adquisiciones",
  finance: "Finanzas",
  approver: "Aprueba la compra",
  signatory: "Firma",
  other: "Otro",
};

const ROLE_OPTIONS = (Object.keys(PARTICIPANT_ROLE_LABEL) as ParticipantRole[]).map((value) => ({
  value,
  label: PARTICIPANT_ROLE_LABEL[value],
}));

export function AddCaseContactDialog({
  opportunityId,
  opportunityVersion,
  address,
  organization,
  quoteNumber,
  onDone,
  onCancel,
}: {
  opportunityId: string;
  opportunityVersion: number;
  /** The recipient as the mail carried it: `Nombre <correo>` or a bare address. */
  address: string;
  /** The case's institution: the new person's affiliation when one is created. */
  organization: { organization_id: string; name: string | null } | null;
  quoteNumber: string | null;
  onDone: (outcome: { created: boolean; name: string }) => void;
  onCancel: () => void;
}) {
  const parts = splitAddress(address);
  const [displayName, setDisplayName] = useState(parts.display ?? "");
  const [role, setRole] = useState<ParticipantRole>("quote_recipient");
  const [affiliate, setAffiliate] = useState(Boolean(organization));
  const [note, setNote] = useState(
    `Destinatario de la cotización${quoteNumber ? ` ${quoteNumber}` : ""} (evidencia de Gmail)`,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keys = useRef({ byEmail: newIdempotencyKey(), create: newIdempotencyKey(), byId: newIdempotencyKey() });
  const createdId = useRef<string | null>(null);

  async function submit() {
    if (!note.trim()) return;
    setBusy(true);
    setError(null);
    const base = { opportunity_id: opportunityId, opportunity_version: opportunityVersion, role, note: note.trim() };
    try {
      if (createdId.current === null) {
        try {
          await addCaseParticipant({ ...base, email: parts.email }, keys.current.byEmail);
          onDone({ created: false, name: displayName.trim() || parts.email });
          return;
        } catch (err) {
          if (refusalOf(err)?.code !== "no_crm_person_for_address") throw err;
        }
        if (!displayName.trim()) {
          setError("Ninguna persona del CRM tiene este correo: escribe su nombre para crearla.");
          setBusy(false);
          return;
        }
        const receipt = await createPerson(
          {
            display_name: displayName.trim(),
            email: parts.email,
            organization_id: affiliate && organization ? organization.organization_id : null,
            note: note.trim(),
          },
          keys.current.create,
        );
        createdId.current = String(receipt.person_id);
      }
      await addCaseParticipant({ ...base, person_id: createdId.current }, keys.current.byId);
      onDone({ created: true, name: displayName.trim() });
    } catch (err) {
      const r = refusalOf(err);
      setError(r ? `${r.code}: ${r.message}` : String(err));
      setBusy(false);
    }
  }

  return (
    <Modal title="Agregar contacto al caso" onClose={onCancel} busy={busy}>
      <p className="mb-3 text-xs text-ink-muted">
        Si una persona del CRM ya tiene este correo, se vincula esa persona al caso. Si no, se crea la persona
        {organization?.name ? ` (con afiliación a ${organization.name})` : ""} y se vincula.
      </p>
      <div className="grid grid-cols-1 gap-3">
        <FormField label="Correo">
          <p className="break-all text-[13px] text-ink">{parts.email}</p>
        </FormField>
        <FormField label="Nombre" htmlFor="case-contact-name" hint="Se usa sólo si hay que crear la persona">
          <TextInput
            id="case-contact-name"
            value={displayName}
            onChange={setDisplayName}
            placeholder="Nombre completo"
            disabled={busy || createdId.current !== null}
            maxLength={200}
          />
        </FormField>
        <FormField label="Rol en el caso" htmlFor="case-contact-role">
          <SelectInput
            id="case-contact-role"
            value={role}
            onChange={(v) => setRole(v as ParticipantRole)}
            options={ROLE_OPTIONS}
            disabled={busy}
          />
        </FormField>
        {organization ? (
          <label className="flex items-center gap-2 text-xs text-ink">
            <input
              type="checkbox"
              checked={affiliate}
              onChange={(e) => setAffiliate(e.target.checked)}
              disabled={busy || createdId.current !== null}
            />
            Si se crea, afiliarla a {organization.name ?? "la institución del caso"}
          </label>
        ) : null}
        <FormField label="Nota" htmlFor="case-contact-note" required>
          <TextInput id="case-contact-note" value={note} onChange={setNote} required disabled={busy} maxLength={2000} />
        </FormField>
      </div>
      {error ? <p className="mt-2 text-[11px] text-bad">{error}</p> : null}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="h-8 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken disabled:opacity-50"
        >
          Cancelar
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={busy || !note.trim()}
          className="h-8 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "…" : "Agregar al caso"}
        </button>
      </div>
    </Modal>
  );
}
