/**
 * CandidateReview — confirm or reject a supplier candidate assertion.
 */
import { useState } from "react";
import { CommandErrorNotice } from "../CommandErrorNotice";
import { useCommandKey } from "../commandKey";
import { refusalFromError, type Refusal } from "../commandRefusal";
import { FormField, SelectInput, TextInput } from "../ui";
import {
  PRODUCT_LINES,
  confirmSupplierCandidate,
  rejectSupplierCandidate,
  type ProductLineId,
} from "./crmAuthoringApi";

const PRODUCT_LINE_LABELS: Record<string, string> = {
  hielscher: "Hielscher",
  ortoalresa: "Ortoalresa",
  ika: "IKA",
  "adam-equipment": "Adam Equipment",
  loeser: "Loeser",
  serva: "SERVA",
};

interface Props {
  assertionId: string;
  domain: string;
  tradeName: string | null;
  onDone: () => void;
  onCancel: () => void;
}

export function ConfirmCandidateForm({ assertionId, domain, tradeName, onDone, onCancel }: Props) {
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [orgId, setOrgId] = useState("");
  const [newName, setNewName] = useState(tradeName ?? domain);
  const [newKind, setNewKind] = useState("company");
  const [classification, setClassification] = useState<"supplier" | "manufacturer">("supplier");
  const [selectedLines, setSelectedLines] = useState<Set<ProductLineId>>(new Set());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const key = useCommandKey();

  function toggleLine(line: ProductLineId) {
    setSelectedLines((prev) => {
      const next = new Set(prev);
      if (next.has(line)) next.delete(line);
      else next.add(line);
      return next;
    });
  }

  const canSubmit =
    confirmed &&
    note.trim().length > 0 &&
    (mode === "existing" ? orgId.trim().length > 0 : newName.trim().length > 0) &&
    !busy;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    const body = {
      assertion_id: assertionId,
      organization_id: mode === "existing" ? orgId.trim() : null,
      new_organization: mode === "new" ? { name: newName.trim(), kind: newKind } : null,
      classification,
      product_lines: selectedLines.size > 0 ? [...selectedLines] : undefined,
      note: note.trim(),
    };
    try {
      await confirmSupplierCandidate(body, key.keyFor(body));
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
      aria-labelledby="confirm-candidate-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="absolute inset-0 bg-ink/30" onClick={onCancel} aria-hidden="true" />
      <div className="relative w-full max-w-lg min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl">
        <h2 id="confirm-candidate-title" className="mb-1 text-base font-semibold text-ink">
          Confirmar candidato: {tradeName ?? domain}
        </h2>
        <p className="mb-4 text-xs text-ink-muted">Dominio: {domain}</p>

        <div className="grid grid-cols-1 gap-3">
          <div className="flex gap-3 text-xs">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" name="confirm-mode" checked={mode === "existing"} onChange={() => setMode("existing")} disabled={busy} />
              Vincular a organización existente
            </label>
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" name="confirm-mode" checked={mode === "new"} onChange={() => setMode("new")} disabled={busy} />
              Crear nueva organización
            </label>
          </div>

          {mode === "existing" ? (
            <FormField label="ID de la organización" required hint="UUID de la organización CRM">
              <TextInput value={orgId} onChange={setOrgId} placeholder="xxxxxxxx-xxxx-…" disabled={busy} />
            </FormField>
          ) : (
            <>
              <FormField label="Nombre" required>
                <TextInput value={newName} onChange={setNewName} disabled={busy} maxLength={300} />
              </FormField>
              <FormField label="Tipo">
                <SelectInput
                  value={newKind}
                  onChange={setNewKind}
                  options={[
                    { value: "company", label: "Empresa" },
                    { value: "university", label: "Universidad" },
                    { value: "laboratory", label: "Laboratorio" },
                    { value: "other", label: "Otro" },
                  ]}
                  disabled={busy}
                />
              </FormField>
            </>
          )}

          <FormField label="Clasificación" required>
            <SelectInput
              value={classification}
              onChange={(v) => setClassification(v as "supplier" | "manufacturer")}
              options={[
                { value: "supplier", label: "Proveedor" },
                { value: "manufacturer", label: "Fabricante" },
              ]}
              disabled={busy}
            />
          </FormField>

          <div className="space-y-1">
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

          <FormField label="Nota" required>
            <TextInput value={note} onChange={setNote} placeholder="Motivo de la confirmación" disabled={busy} maxLength={2000} />
          </FormField>

          <label className="flex cursor-pointer items-start gap-2 text-xs text-ink">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} disabled={busy} className="mt-0.5 shrink-0" />
            <span>Confirmo que este candidato es un proveedor o fabricante legítimo.</span>
          </label>

          <CommandErrorNotice refusal={error} />

          <div className="flex flex-wrap justify-end gap-2">
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
              disabled={!canSubmit}
              className="h-8 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "…" : "Confirmar proveedor"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

interface RejectProps {
  assertionId: string;
  domain: string;
  tradeName: string | null;
  onDone: () => void;
  onCancel: () => void;
}

export function RejectCandidateForm({ assertionId, domain, tradeName, onDone, onCancel }: RejectProps) {
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const key = useCommandKey();

  const canSubmit = confirmed && note.trim().length > 0 && !busy;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    const body = { assertion_id: assertionId, note: note.trim() };
    try {
      await rejectSupplierCandidate(body, key.keyFor(body));
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
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="reject-candidate-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="absolute inset-0 bg-ink/30" onClick={onCancel} aria-hidden="true" />
      <div className="relative w-full max-w-md min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl">
        <h2 id="reject-candidate-title" className="mb-1 text-base font-semibold text-ink">
          Rechazar candidato: {tradeName ?? domain}
        </h2>
        <p className="mb-4 text-xs text-ink-muted">Dominio: {domain}</p>
        <div className="space-y-3">
          <FormField label="Motivo del rechazo" htmlFor="reject-reason" required>
            <TextInput id="reject-reason" value={note} onChange={setNote} placeholder="Motivo" disabled={busy} maxLength={2000} />
          </FormField>
          <label className="flex cursor-pointer items-start gap-2 text-xs text-ink">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} disabled={busy} className="mt-0.5 shrink-0" />
            <span>Confirmo el rechazo de este candidato.</span>
          </label>
          <CommandErrorNotice refusal={error} />
          <div className="flex flex-wrap justify-end gap-2">
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
              disabled={!canSubmit}
              className="h-8 rounded-md bg-bad px-3 text-xs font-medium text-white hover:bg-bad/90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "…" : "Rechazar"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
