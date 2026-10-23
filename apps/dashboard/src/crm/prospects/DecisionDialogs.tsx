import { useState } from "react";
import { Button, ChoiceChips, FormField, Modal, TextInput } from "../ui";
import { addDays, addMonth } from "./santiagoDate";

export const DISCARD_REASONS = ["No es nuestro rubro", "Ya tiene proveedor", "Datos incorrectos", "Otro"] as const;

/** «Descartar»: one reason from the list (or «Otro» with a few words); the prospect never comes back. */
export function DescartarDialog({
  title,
  busy,
  onClose,
  onConfirm,
}: {
  title: string;
  busy: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [choice, setChoice] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const reason = choice === "Otro" ? other.trim() : choice;
  return (
    <Modal title={title} onClose={onClose} busy={busy}>
      <p className="mb-3 text-[13px] text-ink-muted">No volverá a aparecer. ¿Por qué?</p>
      <ChoiceChips label="Motivo" options={[...DISCARD_REASONS]} value={choice} onChange={setChoice} />
      {choice === "Otro" ? (
        <div className="mt-3">
          <FormField label="Motivo" htmlFor="discard-other">
            <TextInput id="discard-other" value={other} onChange={setOther} placeholder="En pocas palabras" disabled={busy} />
          </FormField>
        </div>
      ) : null}
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button variant="danger" disabled={!reason} busy={busy} onClick={() => reason && onConfirm(reason)}>
          Descartar
        </Button>
      </div>
    </Modal>
  );
}

/** «Más tarde»: mañana, en una semana, en un mes o una fecha; the prospect comes back that day. */
export function MasTardeDialog({
  title,
  today,
  busy,
  onClose,
  onConfirm,
}: {
  title: string;
  /** Today in Santiago, «YYYY-MM-DD». */
  today: string;
  busy: boolean;
  onClose: () => void;
  onConfirm: (remindOn: string) => void;
}) {
  const [day, setDay] = useState(addDays(today, 7));
  const quick: [string, string][] = [
    ["Mañana", addDays(today, 1)],
    ["En 1 semana", addDays(today, 7)],
    ["En 1 mes", addMonth(today)],
  ];
  const future = /^\d{4}-\d{2}-\d{2}$/.test(day) && day > today;
  return (
    <Modal title={title} onClose={onClose} busy={busy}>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Cuándo">
        {quick.map(([label, value]) => (
          <button
            key={label}
            type="button"
            aria-pressed={day === value}
            onClick={() => setDay(value)}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
              day === value ? "border-brand-700 bg-brand-700 text-white" : "border-line-strong bg-canvas-raised text-ink hover:border-brand-600"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <label className="mt-3 block text-xs font-medium text-ink">
        Fecha
        <input
          type="date"
          value={day}
          min={addDays(today, 1)}
          onChange={(e) => setDay(e.target.value)}
          disabled={busy}
          className="mt-1 block h-9 w-full rounded-md border border-line-strong bg-canvas-raised px-2 text-[13px] text-ink"
        />
      </label>
      {!future ? <p className="mt-1 text-xs text-bad">Elige una fecha a partir de mañana.</p> : null}
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button variant="primary" disabled={!future} busy={busy} onClick={() => onConfirm(day)}>
          Guardar
        </Button>
      </div>
    </Modal>
  );
}
