/**
 * MergePeopleDialog — admin-only merge of two CRM persons.
 * Fetches the merge preview, shows moves + conflicts, requires confirmation.
 */
import { useCallback, useRef, useState } from "react";
import { FormField, ResourceGate, Skeleton, TextInput } from "../ui";
import { useResource } from "../useResource";
import {
  fetchMergePreview,
  mergePeople,
  newIdempotencyKey,
  refusalOf,
} from "./crmAuthoringApi";

interface Props {
  loserId: string;
  loserName: string;
  loserVersion: number;
  onDone: () => void;
  onCancel: () => void;
}

export function MergePeopleDialog({ loserId, loserName, loserVersion, onDone, onCancel }: Props) {
  const [winnerId, setWinnerId] = useState("");

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="merge-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="absolute inset-0 bg-ink/30" onClick={onCancel} aria-hidden="true" />
      <div className="relative w-full max-w-lg min-w-0 rounded-xl border border-line bg-canvas-raised p-5 shadow-xl">
        <h2 id="merge-dialog-title" className="text-base font-semibold text-ink">
          Fusionar personas
        </h2>
        <p className="mt-1 text-xs text-ink-muted">
          La persona <strong>{loserName}</strong> se archivará y todos sus datos se reapuntarán al ganador.
        </p>
        <div className="mt-4 space-y-3">
          <FormField label="ID de la persona ganadora" required hint="UUID de la persona que conservará los datos">
            <TextInput
              value={winnerId}
              onChange={setWinnerId}
              placeholder="xxxxxxxx-xxxx-…"
            />
          </FormField>
        </div>
        {winnerId.length >= 36 ? (
          <MergePreviewPanel
            loserId={loserId}
            loserName={loserName}
            loserVersion={loserVersion}
            winnerId={winnerId}
            onDone={onDone}
            onCancel={onCancel}
          />
        ) : (
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="h-8 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
            >
              Cancelar
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function MergePreviewPanel({
  loserId,
  loserName,
  loserVersion,
  winnerId,
  onDone,
  onCancel,
}: {
  loserId: string;
  loserName: string;
  loserVersion: number;
  winnerId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const load = useCallback(() => fetchMergePreview(loserId, winnerId), [loserId, winnerId]);
  const [state, reload] = useResource(load, [loserId, winnerId]);
  const [note, setNote] = useState("");
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());

  return (
    <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={3} />}>
      {(preview) => {
        const canSubmit = checked && note.trim().length > 0 && !busy;
        return (
          <div className="mt-4 space-y-3">
            <div className="rounded-md border border-line p-3 text-xs">
              <p className="font-medium text-ink">
                Ganador: {preview.winner.display_name}
              </p>
              <p className="text-ink-muted">Perdedor: {loserName} → archivado después de la fusión</p>
            </div>
            <div className="rounded-md border border-line p-3 text-xs">
              <p className="font-medium text-ink mb-1">Datos que se moverán:</p>
              <ul className="space-y-0.5 text-ink-muted">
                <li>· {preview.moves.contact_points} puntos de contacto</li>
                <li>· {preview.moves.affiliations} vinculaciones</li>
                <li>· {preview.moves.opportunity_participants} participaciones en oportunidades</li>
                <li>· {preview.moves.campaign_recipients} participaciones en campañas</li>
                <li>· {preview.moves.notes} notas</li>
              </ul>
            </div>
            {preview.conflicts.length > 0 ? (
              <div className="rounded-md border border-warn/30 bg-warn-bg p-3 text-xs">
                <p className="font-medium text-warn mb-1">Conflictos (el ganador conserva los suyos):</p>
                <ul className="space-y-0.5 text-warn">
                  {preview.conflicts.map((c, i) => <li key={i}>· {c}</li>)}
                </ul>
              </div>
            ) : null}
            <FormField label="Nota de la fusión" required>
              <TextInput value={note} onChange={setNote} placeholder="Motivo de la fusión" maxLength={2000} disabled={busy} />
            </FormField>
            <label className="flex cursor-pointer items-start gap-2 text-xs text-ink">
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => setChecked(e.target.checked)}
                className="mt-0.5 shrink-0"
              />
              <span>
                Entiendo que <strong>{loserName}</strong> quedará archivada y sus datos se reapuntarán a{" "}
                <strong>{preview.winner.display_name}</strong>.
              </span>
            </label>
            {error ? <p className="text-[11px] text-bad">{error}</p> : null}
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={onCancel}
                disabled={busy}
                className="h-8 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={!canSubmit}
                onClick={async () => {
                  if (!canSubmit) return;
                  setBusy(true);
                  setError(null);
                  try {
                    await mergePeople(
                      {
                        loser_person_id: loserId,
                        winner_person_id: winnerId,
                        expected_loser_version: loserVersion,
                        expected_winner_version: preview.winner.version,
                        expected_preview_sha256: preview.preview_sha256,
                        confirmed: true,
                        note: note.trim(),
                      },
                      keyRef.current,
                    );
                    onDone();
                  } catch (err) {
                    const r = refusalOf(err);
                    if (r?.code === "stale_version") {
                      setError("Otro operador modificó este registro; recarga y vuelve a intentar.");
                    } else if (r?.code === "preview_changed") {
                      setError("La vista previa cambió antes de confirmar; recarga y revisa.");
                    } else {
                      setError(r ? `${r.code}: ${r.message}` : String(err));
                    }
                    setBusy(false);
                  }
                }}
                className="h-8 rounded-md bg-bad px-3 text-xs font-medium text-white hover:bg-bad/90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? "…" : "Fusionar personas"}
              </button>
            </div>
          </div>
        );
      }}
    </ResourceGate>
  );
}
