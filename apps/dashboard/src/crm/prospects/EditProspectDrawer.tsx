/**
 * «Editar prospecto» (owner 2026-10-25: «let me edit everything — I found the person's email»).
 * Every field of the card, grouped as the card reads; only what changed is sent (`edit-prospect`),
 * a blank field clears it. A new correo asks where it was found — the trail «Contactar» keeps — and
 * is checked against the whole system while it is typed («¿Ya lo tenemos?»).
 */
import { useMemo, useRef, useState } from "react";
import { envelopeOfError } from "../../api/refusalEnvelope";
import { LookupResults } from "../LookupResults";
import { useLookup } from "../lookup";
import { Button, Drawer, FormField, TextInput, TextareaInput, toast } from "../ui";
import { editProspect, newProspectCommandKey, prospectRefusal, type ProspectChanges, type ProspectItem } from "./prospectApi";

type Form = Record<
  | "person_name" | "role" | "organization_name" | "unit" | "email" | "email_source_note" | "phone"
  | "why_now" | "equipment" | "fund_label" | "amount_label"
  | "website" | "linkedin_person" | "linkedin_org" | "orcid" | "scholar",
  string
>;

function formOf(item: ProspectItem): Form {
  return {
    person_name: item.person_name ?? "",
    role: item.role ?? "",
    organization_name: item.organization_name ?? "",
    unit: item.unit ?? "",
    email: item.email ?? "",
    email_source_note: item.email_source_note ?? "",
    phone: item.phone ?? "",
    why_now: item.why_now ?? "",
    equipment: item.equipment.join(", "),
    fund_label: item.fund_label ?? "",
    amount_label: item.amount_label ?? "",
    website: item.links.website ?? "",
    linkedin_person: item.links.linkedin_person ?? "",
    linkedin_org: item.links.linkedin_org ?? "",
    orcid: item.links.orcid ?? "",
    scholar: item.links.scholar ?? "",
  };
}

const norm = (v: string) => v.trim().replace(/\s+/g, " ");
const equipmentOf = (v: string) => v.split(/[,;\n]/).map(norm).filter(Boolean);

/** Only the fields that differ from the card, in the API's shape; a blank is a null. */
export function changesOf(before: Form, after: Form): ProspectChanges {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(after) as (keyof Form)[]) {
    if (key === "equipment") {
      const a = equipmentOf(after.equipment);
      if (a.join("\u0000") !== equipmentOf(before.equipment).join("\u0000")) out.equipment = a.length ? a : null;
      continue;
    }
    const a = norm(after[key]);
    const b = norm(before[key]);
    if (key === "email" ? a.toLowerCase() !== b.toLowerCase() : a !== b) out[key] = a || null;
  }
  return out as ProspectChanges;
}

const FIELD_LABEL: Record<string, string> = {
  person_name: "Nombre", role: "Cargo", organization_name: "Institución", unit: "Unidad", email: "Correo",
  email_source_note: "Dónde lo encontraste", phone: "Teléfono", why_now: "Por qué ahora", equipment: "Equipos",
  fund_label: "Fondo", amount_label: "Monto", website: "Sitio web", linkedin_person: "LinkedIn de la persona",
  linkedin_org: "LinkedIn de la institución", orcid: "ORCID", scholar: "Google Scholar",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="space-y-3">
      <legend className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{title}</legend>
      {children}
    </fieldset>
  );
}

export function EditProspectDrawer({
  item,
  onClose,
  onSaved,
}: {
  item: ProspectItem;
  onClose: () => void;
  onSaved: () => void;
}) {
  const before = useMemo(() => formOf(item), [item]);
  const [form, setForm] = useState<Form>(before);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ field: string | null; message: string } | null>(null);
  const key = useRef(newProspectCommandKey());
  const changes = changesOf(before, form);
  const emailChanged = "email" in changes && !!changes.email;
  const lookup = useLookup(emailChanged ? form.email : "");
  const needsSource = emailChanged && !norm(form.email_source_note);
  const set = (k: keyof Form) => (v: string) => {
    setForm((f) => ({ ...f, [k]: v }));
    setError(null);
    key.current = newProspectCommandKey();
  };
  const field = (k: keyof Form, opts: { hint?: string; placeholder?: string; required?: boolean; long?: boolean } = {}) => (
    <FormField label={FIELD_LABEL[k]} htmlFor={`edit-${k}`} hint={opts.hint} required={opts.required}
      error={error?.field === k ? error.message : null}>
      {opts.long ? (
        <TextareaInput id={`edit-${k}`} value={form[k]} onChange={set(k)} placeholder={opts.placeholder} disabled={busy} rows={3} />
      ) : (
        <TextInput id={`edit-${k}`} value={form[k]} onChange={set(k)} placeholder={opts.placeholder} disabled={busy} />
      )}
    </FormField>
  );

  async function save() {
    if (needsSource) {
      setError({ field: "email_source_note", message: "Di dónde encontraste el correo." });
      return;
    }
    const body = { ...changes };
    // The source travels with a new correo even when its text did not change.
    if (emailChanged && !("email_source_note" in body)) body.email_source_note = norm(form.email_source_note);
    setBusy(true);
    try {
      await editProspect({ finding_id: item.id, changes: body }, key.current);
      toast("Cambios guardados", "good");
      onSaved();
    } catch (err) {
      const refusal = prospectRefusal(err);
      const detailField = envelopeOfError(err).details?.field;
      const fieldName = typeof detailField === "string" ? detailField : null;
      setError({ field: fieldName, message: refusal?.message ?? "No se pudo guardar." });
    } finally {
      setBusy(false);
    }
  }

  const n = Object.keys(changes).length;
  return (
    <Drawer open onClose={onClose} title="Editar prospecto" subtitle={item.person_name ?? item.organization_name}>
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Section title="Persona">
          <div className="grid gap-3 sm:grid-cols-2">
            {field("person_name", { placeholder: "Sin nombre: es la institución" })}
            {field("role")}
          </div>
        </Section>
        <Section title="Institución">
          {field("organization_name", { required: true })}
          {field("unit", { placeholder: "Departamento, laboratorio o centro" })}
        </Section>
        <Section title="Contacto">
          {field("email", { placeholder: "nombre@institucion.cl" })}
          {emailChanged ? (
            <div className="-mt-1 rounded-md border border-line bg-canvas px-3 py-2" aria-live="polite">
              {lookup.kind === "ready" ? (
                <LookupResults result={lookup.data} compact />
              ) : lookup.kind === "loading" ? (
                <p className="text-[11px] text-ink-faint">Buscando si ya lo tenemos…</p>
              ) : lookup.kind === "error" ? (
                <p className="text-[11px] text-ink-faint">No se pudo revisar si ya lo tenemos.</p>
              ) : (
                <p className="text-[11px] text-ink-faint">Escribe el correo completo para revisar si ya lo tenemos.</p>
              )}
            </div>
          ) : null}
          {emailChanged || form.email_source_note
            ? field("email_source_note", { required: emailChanged, placeholder: "Ej.: sitio del laboratorio, firma de un correo, congreso" })
            : null}
          {field("phone", { placeholder: "+56 9 …" })}
        </Section>
        <Section title="Oportunidad">
          {field("why_now", { long: true })}
          {field("equipment", { hint: "Separados por coma." })}
          <div className="grid gap-3 sm:grid-cols-2">
            {field("fund_label")}
            {field("amount_label")}
          </div>
        </Section>
        <Section title="Enlaces">
          {field("website", { placeholder: "https://" })}
          <div className="grid gap-3 sm:grid-cols-2">
            {field("linkedin_person", { placeholder: "https://" })}
            {field("linkedin_org", { placeholder: "https://" })}
            {field("orcid", { placeholder: "https://orcid.org/…" })}
            {field("scholar", { placeholder: "https://scholar.google.com/…" })}
          </div>
        </Section>

        {error && !error.field ? <p className="text-[13px] text-bad" role="alert">{error.message}</p> : null}
        <div className="sticky -bottom-4 -mx-4 -mb-4 flex items-center gap-2 border-t border-line bg-canvas-raised px-4 py-3">
          <span className="text-[11px] text-ink-faint">
            {n === 0 ? "Sin cambios" : `${n} ${n === 1 ? "cambio" : "cambios"} · queda registrado quién editó`}
          </span>
          <Button onClick={onClose} disabled={busy} className="ml-auto">Cancelar</Button>
          <Button variant="primary" type="submit" disabled={n === 0} busy={busy} busyLabel="Guardando…">
            Guardar
          </Button>
        </div>
      </form>
    </Drawer>
  );
}
