/**
 * NewPersonForm — modal form for create-person command.
 */
import { useState } from "react";
import { CommandErrorNotice } from "../CommandErrorNotice";
import { useCommandKey } from "../commandKey";
import { refusalFromError, type Refusal } from "../commandRefusal";
import { FormField, SelectInput, TextInput } from "../ui";
import { createPerson } from "./crmAuthoringApi";

interface Props {
  onDone: () => void;
  onCancel: () => void;
  organizationOptions?: { value: string; label: string }[];
}

export function NewPersonForm({ onDone, onCancel, organizationOptions = [] }: Props) {
  const [displayName, setDisplayName] = useState("");
  const [givenName, setGivenName] = useState("");
  const [familyName, setFamilyName] = useState("");
  const [title, setTitle] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [orgId, setOrgId] = useState("");
  const [roleTitle, setRoleTitle] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const key = useCommandKey();

  async function submit() {
    if (!displayName.trim() || !note.trim()) return;
    setBusy(true);
    setError(null);
    const body = {
      display_name: displayName.trim(),
      given_name: givenName.trim() || null,
      family_name: familyName.trim() || null,
      title: title.trim() || null,
      email: email.trim() || null,
      phone: phone.trim() || null,
      organization_id: orgId || null,
      role_title: roleTitle.trim() || null,
      note: note.trim(),
    };
    try {
      await createPerson(body, key.keyFor(body));
      key.settle();
      onDone();
    } catch (err) {
      key.settle(err);
      setError(refusalFromError(err));
      setBusy(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-person-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="absolute inset-0 bg-ink/30" onClick={onCancel} aria-hidden="true" />
      <div className="relative w-full max-w-lg min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl">
        <h2 id="new-person-title" className="mb-4 text-base font-semibold text-ink">
          Nuevo contacto
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormField label="Nombre para mostrar" htmlFor="new-person-display-name" required>
            <TextInput
              id="new-person-display-name"
              value={displayName}
              onChange={setDisplayName}
              placeholder="Nombre completo"
              required
              disabled={busy}
              maxLength={200}
            />
          </FormField>
          <FormField label="Nombre de pila">
            <TextInput value={givenName} onChange={setGivenName} disabled={busy} maxLength={100} />
          </FormField>
          <FormField label="Apellido">
            <TextInput value={familyName} onChange={setFamilyName} disabled={busy} maxLength={100} />
          </FormField>
          <FormField label="Título">
            <TextInput value={title} onChange={setTitle} placeholder="Dr., Ing., etc." disabled={busy} maxLength={50} />
          </FormField>
          <FormField label="Email">
            <TextInput value={email} onChange={setEmail} placeholder="correo@dominio.cl" disabled={busy} maxLength={300} />
          </FormField>
          <FormField label="Teléfono">
            <TextInput value={phone} onChange={setPhone} placeholder="+56 9 …" disabled={busy} maxLength={50} />
          </FormField>
          {organizationOptions.length > 0 ? (
            <FormField label="Institución">
              <SelectInput
                value={orgId}
                onChange={setOrgId}
                options={organizationOptions}
                placeholder="Sin institución"
                disabled={busy}
              />
            </FormField>
          ) : null}
          <FormField label="Cargo en la institución">
            <TextInput value={roleTitle} onChange={setRoleTitle} placeholder="Investigador, Jefe…" disabled={busy} maxLength={200} />
          </FormField>
          <FormField label="Nota de registro" htmlFor="new-person-note" required hint="Describe la fuente o motivo del registro">
            <TextInput id="new-person-note" value={note} onChange={setNote} placeholder="Fuente o motivo" required disabled={busy} maxLength={2000} />
          </FormField>
        </div>
        <CommandErrorNotice refusal={error} className="mt-2 text-[11px] text-bad" />
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
            disabled={busy || !displayName.trim() || !note.trim()}
            className="h-8 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "…" : "Crear contacto"}
          </button>
        </div>
      </div>
    </div>
  );
}
