/**
 * NoteList + NoteForm — reusable for person, organization and opportunity subjects.
 */
import { useState } from "react";
import { useAuthSession } from "../../context/AuthSessionContext";
import { CommandErrorNotice } from "../CommandErrorNotice";
import { useCommandKey } from "../commandKey";
import { isStaleRefusal, refusalFromError, refusalText, type Refusal } from "../commandRefusal";
import {
  Badge,
  ConfirmDialog,
  FormField,
  Section,
  TextareaInput,
  fmtDate,
} from "../ui";
import {
  addNote,
  archiveNote,
  reviseNote,
  type NoteRow,
} from "./crmAuthoringApi";
import { isAdmin } from "./authoring";

interface NoteListProps {
  notes: NoteRow[];
  subjectKind: "person" | "organization" | "opportunity";
  subjectId: string;
  mayAuthor: boolean;
  onRefresh: () => void;
}

export function NoteList({ notes, subjectKind, subjectId, mayAuthor, onRefresh }: NoteListProps) {
  const { session } = useAuthSession();
  const myId = session.kind === "signed_in" ? session.operator.operatorId : null;
  const admin = isAdmin(session);
  const [adding, setAdding] = useState(false);
  // The chain being revised, not the revision: if another operator revises it meanwhile,
  // «Cargar versión actual» brings the new latest revision under the same open form.
  const [editRoot, setEditRoot] = useState<string | null>(null);
  const [archiveId, setArchiveId] = useState<string | null>(null);
  const [error, setError] = useState<Refusal | null>(null);
  const [historyOpen, setHistoryOpen] = useState<Set<string>>(new Set());

  // Group notes by root_note_id to get revision chains.
  const byRoot = new Map<string, NoteRow[]>();
  for (const n of notes) {
    const key = n.root_note_id;
    const arr = byRoot.get(key) ?? [];
    arr.push(n);
    byRoot.set(key, arr);
  }
  // Latest revision of each chain (is_latest === true).
  const latestNotes = notes.filter((n) => n.is_latest);

  function toggleHistory(rootId: string) {
    setHistoryOpen((prev) => {
      const next = new Set(prev);
      if (next.has(rootId)) next.delete(rootId);
      else next.add(rootId);
      return next;
    });
  }

  const archiveTarget = archiveId ? notes.find((n) => n.id === archiveId) ?? null : null;

  return (
    <Section
      title={`Notas (${latestNotes.length})`}
      aside={
        mayAuthor ? (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken"
          >
            Agregar nota
          </button>
        ) : null
      }
    >
      {adding && (
        <NoteForm
          mode="add"
          subjectKind={subjectKind}
          subjectId={subjectId}
          onDone={() => { setAdding(false); onRefresh(); }}
          onCancel={() => setAdding(false)}
        />
      )}
      {latestNotes.length === 0 && !adding ? (
        <p className="text-xs text-ink-faint">Sin notas.</p>
      ) : (
        <ul className="space-y-2">
          {latestNotes.map((n) => {
            const chain = byRoot.get(n.root_note_id) ?? [n];
            const showHistory = historyOpen.has(n.root_note_id);
            const canEdit = mayAuthor && n.status === "active";
            const canArchive = n.status === "active" && (admin || myId === n.author_operator_id);
            return (
              <li
                key={n.root_note_id}
                className={`rounded-md border p-3 text-xs ${n.status === "archived" ? "border-line bg-canvas-sunken/60 opacity-60" : "border-line bg-canvas-raised"}`}
              >
                <div className="flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-ink">{n.author_name}</p>
                    <p className="text-[11px] text-ink-faint">{fmtDate(n.created_at)}</p>
                  </div>
                  {n.status === "archived" ? <Badge tone="neutral">Archivada</Badge> : null}
                  {n.revision_no > 1 ? (
                    <Badge tone="info" glyph={false}>
                      Rev {n.revision_no}
                    </Badge>
                  ) : null}
                </div>
                {editRoot === n.root_note_id ? (
                  <NoteForm
                    mode="revise"
                    noteId={n.id}
                    expectedVersion={n.version}
                    initialBody={n.body}
                    onDone={() => { setEditRoot(null); onRefresh(); }}
                    onCancel={() => setEditRoot(null)}
                    onReload={onRefresh}
                  />
                ) : (
                  <p className="mt-2 whitespace-pre-wrap break-words text-ink">{n.body}</p>
                )}
                {error && archiveId === n.id ? <p className="mt-1 text-[11px] text-bad">{refusalText(error)}</p> : null}
                <div className="mt-2 flex flex-wrap gap-2">
                  {canEdit && editRoot !== n.root_note_id ? (
                    <button
                      type="button"
                      onClick={() => setEditRoot(n.root_note_id)}
                      className="text-[11px] font-medium text-brand-700 hover:underline"
                    >
                      Editar
                    </button>
                  ) : null}
                  {canArchive ? (
                    <button
                      type="button"
                      onClick={() => { setArchiveId(n.id); setError(null); }}
                      className="text-[11px] font-medium text-bad hover:underline"
                    >
                      Archivar
                    </button>
                  ) : null}
                  {chain.length > 1 ? (
                    <button
                      type="button"
                      onClick={() => toggleHistory(n.root_note_id)}
                      className="text-[11px] text-ink-faint hover:text-ink"
                    >
                      {showHistory ? "Ocultar historial" : `Historial (${chain.length} versiones)`}
                    </button>
                  ) : null}
                </div>
                {showHistory && chain.length > 1 ? (
                  <ul className="mt-2 border-t border-line/70 pt-2 space-y-2">
                    {chain
                      .filter((r) => r.id !== n.id)
                      .sort((a, b) => b.revision_no - a.revision_no)
                      .map((r) => (
                        <li key={r.id} className="text-[11px] text-ink-muted">
                          <span className="font-medium">Rev {r.revision_no}</span> — {fmtDate(r.created_at)}
                          <p className="mt-0.5 whitespace-pre-wrap break-words opacity-70">{r.body}</p>
                        </li>
                      ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {archiveTarget ? (
        <ConfirmDialog
          title="Archivar nota"
          lines={[
            `Se archivará la nota escrita por ${archiveTarget.author_name}.`,
            "La nota original se conserva; sólo se marca como archivada.",
          ]}
          requireReason
          reasonLabel="Motivo del archivo"
          confirmLabel="Archivar nota"
          error={error ? refusalText(error) : null}
          onReload={isStaleRefusal(error) ? () => { setError(null); onRefresh(); } : undefined}
          onCancel={() => { setArchiveId(null); setError(null); }}
          onConfirm={async (reason) => {
            try {
              await archiveNote({ note_id: archiveTarget.id, expected_version: archiveTarget.version, note: reason });
              setArchiveId(null);
              setError(null);
              onRefresh();
            } catch (err) {
              setError(refusalFromError(err));
            }
          }}
        />
      ) : null}
    </Section>
  );
}

interface NoteFormAddProps {
  mode: "add";
  subjectKind: "person" | "organization" | "opportunity";
  subjectId: string;
  onDone: () => void;
  onCancel: () => void;
}

interface NoteFormReviseProps {
  mode: "revise";
  noteId: string;
  expectedVersion: number;
  initialBody?: string;
  onDone: () => void;
  onCancel: () => void;
  /** Re-read the note; the typed text stays and the next save revises the latest revision. */
  onReload?: () => void;
}

type NoteFormProps = NoteFormAddProps | NoteFormReviseProps;

export function NoteForm(props: NoteFormProps) {
  const [body, setBody] = useState(props.mode === "revise" ? (props.initialBody ?? "") : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const key = useCommandKey();
  const onReload = props.mode === "revise" ? props.onReload : undefined;

  async function submit() {
    if (!body.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (props.mode === "add") {
        const request = { subject_kind: props.subjectKind, subject_id: props.subjectId, body: body.trim() };
        await addNote(request, key.keyFor(request));
      } else {
        const request = { note_id: props.noteId, expected_version: props.expectedVersion, body: body.trim() };
        await reviseNote(request, key.keyFor(request));
      }
      key.settle();
      props.onDone();
    } catch (err) {
      // An unanswered attempt keeps its key, so resending the same note replays it.
      key.settle(err);
      setError(refusalFromError(err));
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 space-y-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3">
      <FormField
        label={props.mode === "add" ? "Nueva nota" : "Editar nota (crea una revisión; el original se conserva)"}
        required
      >
        <TextareaInput
          value={body}
          onChange={setBody}
          placeholder="Escribe la nota…"
          maxLength={8000}
          rows={4}
          disabled={busy}
        />
      </FormField>
      {body.length > 7500 ? (
        <p className="text-[11px] text-warn">{body.length}/8000 caracteres</p>
      ) : null}
      <CommandErrorNotice refusal={error} onReload={onReload ? () => { setError(null); onReload(); } : undefined} />
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || !body.trim()}
          className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "…" : props.mode === "add" ? "Agregar" : "Guardar revisión"}
        </button>
        <button
          type="button"
          onClick={props.onCancel}
          disabled={busy}
          className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}
