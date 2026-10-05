/**
 * NewOrganizationForm — modal form for register-organization command.
 * Used for both generic organizations and provider registration.
 */
import { useState } from "react";
import { CommandErrorNotice } from "../CommandErrorNotice";
import { useCommandKey } from "../commandKey";
import { refusalFromError, type Refusal } from "../commandRefusal";
import { FormField, SelectInput, TextInput } from "../ui";
import { PRODUCT_LINES, registerOrganization, type ProductLineId } from "./crmAuthoringApi";

const KIND_OPTIONS = [
  { value: "company", label: "Empresa" },
  { value: "university", label: "Universidad" },
  { value: "public_institution", label: "Institución pública" },
  { value: "hospital", label: "Hospital / clínica" },
  { value: "laboratory", label: "Laboratorio" },
  { value: "other", label: "Otro" },
];

const CLASSIFICATION_OPTIONS = [
  { value: "", label: "Sin clasificación" },
  { value: "customer", label: "Cliente" },
  { value: "supplier", label: "Proveedor" },
  { value: "manufacturer", label: "Fabricante" },
  { value: "prospect", label: "Prospecto" },
  { value: "partner", label: "Socio" },
  { value: "competitor", label: "Competidor" },
];

const PRODUCT_LINE_LABELS: Record<string, string> = {
  hielscher: "Hielscher",
  ortoalresa: "Ortoalresa",
  ika: "IKA",
  "adam-equipment": "Adam Equipment",
  loeser: "Loeser",
  serva: "SERVA",
};

interface Props {
  /**
   * When provided, forces the classification and hides the classification selector.
   * Used for ProvidersPage "Agregar proveedor" flow.
   */
  forcedClassification?: "supplier" | "manufacturer";
  onDone: () => void;
  onCancel: () => void;
}

export function NewOrganizationForm({ forcedClassification, onDone, onCancel }: Props) {
  const [name, setName] = useState("");
  const [legalName, setLegalName] = useState("");
  const [kind, setKind] = useState("company");
  const [classification, setClassification] = useState(forcedClassification ?? "");
  const [domain, setDomain] = useState("");
  const [selectedLines, setSelectedLines] = useState<Set<ProductLineId>>(new Set());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const key = useCommandKey();

  function toggleLine(line: ProductLineId) {
    setSelectedLines((prev) => {
      const next = new Set(prev);
      if (next.has(line)) next.delete(line);
      else next.add(line);
      return next;
    });
  }

  async function submit() {
    if (!name.trim() || !note.trim()) return;
    setBusy(true);
    setError(null);
    const body = {
      name: name.trim(),
      legal_name: legalName.trim() || null,
      kind,
      classification: (forcedClassification ?? classification) || null,
      domain: domain.trim() || null,
      product_lines: selectedLines.size > 0 ? [...selectedLines] : undefined,
      note: note.trim(),
    };
    try {
      await registerOrganization(body, key.keyFor(body));
      key.settle();
      onDone();
    } catch (err) {
      key.settle(err);
      setError(refusalFromError(err));
      setBusy(false);
    }
  }

  const title = forcedClassification ? "Agregar proveedor" : "Nueva organización";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-org-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="absolute inset-0 bg-ink/30" onClick={onCancel} aria-hidden="true" />
      <div className="relative w-full max-w-lg min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl">
        <h2 id="new-org-title" className="mb-4 text-base font-semibold text-ink">
          {title}
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormField label="Nombre" required>
            <TextInput
              id="new-org-name"
              value={name}
              onChange={setName}
              placeholder="Nombre de la organización"
              required
              disabled={busy}
              maxLength={300}
            />
          </FormField>
          <FormField label="Nombre legal">
            <TextInput value={legalName} onChange={setLegalName} disabled={busy} maxLength={400} />
          </FormField>
          <FormField label="Tipo" required>
            <SelectInput value={kind} onChange={setKind} options={KIND_OPTIONS} disabled={busy} />
          </FormField>
          {!forcedClassification ? (
            <FormField label="Clasificación">
              <SelectInput value={classification} onChange={setClassification} options={CLASSIFICATION_OPTIONS} disabled={busy} />
            </FormField>
          ) : (
            <FormField label="Clasificación">
              <p className="rounded-md border border-line bg-canvas-sunken/60 px-2.5 py-1.5 text-xs text-ink">
                {forcedClassification === "supplier" ? "Proveedor" : "Fabricante"} (forzado)
              </p>
            </FormField>
          )}
          <FormField label="Dominio web">
            <TextInput value={domain} onChange={setDomain} placeholder="ejemplo.com" disabled={busy} maxLength={253} />
          </FormField>
        </div>
        <div className="mt-3 space-y-1">
          <p className="text-xs font-medium text-ink">Líneas de productos</p>
          <div className="flex flex-wrap gap-2">
            {PRODUCT_LINES.map((line) => (
              <label key={line} className="flex cursor-pointer items-center gap-1.5 text-xs">
                <input
                  type="checkbox"
                  checked={selectedLines.has(line)}
                  onChange={() => toggleLine(line)}
                  disabled={busy}
                  className="shrink-0"
                />
                {PRODUCT_LINE_LABELS[line] ?? line}
              </label>
            ))}
          </div>
        </div>
        <div className="mt-3">
          <FormField label="Nota de registro" required hint="Describe la fuente o motivo del registro">
            <TextInput value={note} onChange={setNote} placeholder="Fuente o motivo" required disabled={busy} maxLength={2000} />
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
            disabled={busy || !name.trim() || !note.trim()}
            className="h-8 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "…" : title}
          </button>
        </div>
      </div>
    </div>
  );
}
