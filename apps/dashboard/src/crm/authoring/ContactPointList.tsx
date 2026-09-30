/**
 * ContactPointList — list, add, edit and deactivate contact points on a person or organization.
 */
import { useRef, useState } from "react";
import {
  Badge,
  ConfirmDialog,
  FormField,
  Section,
  SelectInput,
  TextInput,
  fmtDate,
} from "../ui";
import {
  addContactPoint,
  deactivateContactPoint,
  newIdempotencyKey,
  refusalOf,
  updateContactPoint,
  type ContactPointRow,
} from "./crmAuthoringApi";
import { isMaskedAddress } from "../redaction";

const USAGE_OPTIONS = [
  { value: "", label: "Sin etiqueta" },
  { value: "work", label: "Trabajo" },
  { value: "personal", label: "Personal" },
  { value: "main", label: "Principal" },
  { value: "other", label: "Otro" },
];

interface Props {
  contactPoints: ContactPointRow[];
  ownerId: string;
  ownerVersion: number;
  subjectKind: "person" | "organization";
  mayAuthor: boolean;
  onRefresh: () => void;
}

export function ContactPointList({ contactPoints, ownerId, ownerVersion, subjectKind, mayAuthor, onRefresh }: Props) {
  const [adding, setAdding] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [deactivateId, setDeactivateId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const active = contactPoints.filter((c) => c.status === "active");
  const inactive = contactPoints.filter((c) => c.status === "inactive");
  const deactivateTarget = deactivateId ? contactPoints.find((c) => c.id === deactivateId) ?? null : null;

  return (
    <Section
      title={`Puntos de contacto (${active.length})`}
      aside={
        mayAuthor ? (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken"
          >
            Agregar
          </button>
        ) : null
      }
    >
      {adding && (
        <AddContactPointForm
          ownerId={ownerId}
          ownerVersion={ownerVersion}
          subjectKind={subjectKind}
          onDone={() => { setAdding(false); onRefresh(); }}
          onCancel={() => setAdding(false)}
        />
      )}
      {active.length === 0 && !adding ? (
        <p className="text-xs text-ink-faint">Sin puntos de contacto activos.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line">
          {active.map((cp) => (
            <ContactPointItem
              key={cp.id}
              cp={cp}
              mayAuthor={mayAuthor}
              editing={editId === cp.id}
              onEdit={() => setEditId(cp.id)}
              onDeactivate={() => { setDeactivateId(cp.id); setError(null); }}
              onEditDone={() => { setEditId(null); onRefresh(); }}
              onEditCancel={() => setEditId(null)}
            />
          ))}
        </ul>
      )}
      {inactive.length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-ink-faint">
            {inactive.length} desactivado{inactive.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-1 divide-y divide-line rounded-md border border-line opacity-60">
            {inactive.map((cp) => (
              <ContactPointItem
                key={cp.id}
                cp={cp}
                mayAuthor={false}
                editing={false}
                onEdit={() => undefined}
                onDeactivate={() => undefined}
                onEditDone={() => undefined}
                onEditCancel={() => undefined}
              />
            ))}
          </ul>
        </details>
      ) : null}
      {deactivateTarget ? (
        <ConfirmDialog
          title="Desactivar punto de contacto"
          lines={[
            `Se desactivará: ${deactivateTarget.value_display}`,
            "El registro se conserva; sólo se marca como inactivo.",
          ]}
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Desactivar"
          error={error}
          onCancel={() => { setDeactivateId(null); setError(null); }}
          onConfirm={async (reason) => {
            try {
              await deactivateContactPoint({
                contact_point_id: deactivateTarget.id,
                expected_version: deactivateTarget.version,
                note: reason,
              });
              setDeactivateId(null);
              setError(null);
              onRefresh();
            } catch (err) {
              const r = refusalOf(err);
              setError(r ? `${r.code}: ${r.message}` : String(err));
            }
          }}
        />
      ) : null}
    </Section>
  );
}

function ContactPointItem({
  cp,
  mayAuthor,
  editing,
  onEdit,
  onDeactivate,
  onEditDone,
  onEditCancel,
}: {
  cp: ContactPointRow;
  mayAuthor: boolean;
  editing: boolean;
  onEdit: () => void;
  onDeactivate: () => void;
  onEditDone: () => void;
  onEditCancel: () => void;
}) {
  const masked = isMaskedAddress(cp.value_display) || isMaskedAddress(cp.value_norm);
  return (
    <li className="px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-medium text-ink">{cp.value_display || cp.value_norm}</span>
        <Badge tone={cp.kind === "email" ? "info" : "neutral"} glyph={false}>
          {cp.kind === "email" ? "Email" : "Teléfono"}
        </Badge>
        {cp.usage ? <Badge glyph={false}>{cp.usage}</Badge> : null}
        {cp.status === "inactive" ? (
          <Badge tone="neutral">Inactivo · {fmtDate(cp.deactivated_at)}</Badge>
        ) : null}
      </div>
      {editing ? (
        <EditContactPointForm
          cp={cp}
          onDone={onEditDone}
          onCancel={onEditCancel}
        />
      ) : (
        mayAuthor && cp.status === "active" && !masked ? (
          <div className="mt-1 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onEdit}
              className="text-[11px] font-medium text-brand-700 hover:underline"
            >
              Editar
            </button>
            <button
              type="button"
              onClick={onDeactivate}
              className="text-[11px] font-medium text-bad hover:underline"
            >
              Desactivar
            </button>
          </div>
        ) : null
      )}
    </li>
  );
}

function AddContactPointForm({
  ownerId,
  ownerVersion,
  subjectKind: _subjectKind,
  onDone,
  onCancel,
}: {
  ownerId: string;
  ownerVersion: number;
  subjectKind: "person" | "organization";
  onDone: () => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<"email" | "phone">("email");
  const [value, setValue] = useState("");
  const [usage, setUsage] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());

  async function submit() {
    if (!value.trim() || !note.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await addContactPoint(
        {
          person_id: ownerId,
          expected_version: ownerVersion,
          kind,
          value: value.trim(),
          usage: usage || null,
          note: note.trim(),
        },
        keyRef.current,
      );
      onDone();
    } catch (err) {
      const r = refusalOf(err);
      if (r?.code === "stale_version") {
        setError("Otro operador modificó este registro; recarga y vuelve a intentar.");
      } else {
        setError(r ? `${r.code}: ${r.message}` : String(err));
      }
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3 sm:grid-cols-2">
      <FormField label="Tipo" required>
        <SelectInput
          value={kind}
          onChange={(v) => setKind(v as "email" | "phone")}
          options={[
            { value: "email", label: "Email" },
            { value: "phone", label: "Teléfono" },
          ]}
          disabled={busy}
        />
      </FormField>
      <FormField label="Valor" required>
        <TextInput value={value} onChange={setValue} placeholder="correo@dominio.cl" disabled={busy} maxLength={300} />
      </FormField>
      <FormField label="Uso">
        <SelectInput value={usage} onChange={setUsage} options={USAGE_OPTIONS} disabled={busy} />
      </FormField>
      <FormField label="Nota de registro" required>
        <TextInput value={note} onChange={setNote} placeholder="Motivo o fuente" disabled={busy} maxLength={2000} />
      </FormField>
      {error ? <p className="col-span-full text-[11px] text-bad">{error}</p> : null}
      <div className="col-span-full flex flex-wrap gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || !value.trim() || !note.trim()}
          className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "…" : "Agregar"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}

function EditContactPointForm({
  cp,
  onDone,
  onCancel,
}: {
  cp: ContactPointRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [usage, setUsage] = useState(cp.usage ?? "");
  const [display, setDisplay] = useState(cp.value_display ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());

  async function submit() {
    if (!note.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await updateContactPoint(
        {
          contact_point_id: cp.id,
          expected_version: cp.version,
          usage: usage || null,
          value_display: display || null,
          note: note.trim(),
        },
        keyRef.current,
      );
      onDone();
    } catch (err) {
      const r = refusalOf(err);
      if (r?.code === "stale_version") {
        setError("Otro operador modificó este registro; recarga y vuelve a intentar.");
      } else {
        setError(r ? `${r.code}: ${r.message}` : String(err));
      }
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3 sm:grid-cols-2">
      <FormField label="Nombre para mostrar">
        <TextInput value={display} onChange={setDisplay} placeholder={cp.value_norm} disabled={busy} maxLength={300} />
      </FormField>
      <FormField label="Uso">
        <SelectInput value={usage} onChange={setUsage} options={USAGE_OPTIONS} disabled={busy} />
      </FormField>
      <FormField label="Nota de cambio" required>
        <TextInput value={note} onChange={setNote} placeholder="Motivo del cambio" disabled={busy} maxLength={2000} />
      </FormField>
      {error ? <p className="col-span-full text-[11px] text-bad">{error}</p> : null}
      <div className="col-span-full flex flex-wrap gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || !note.trim()}
          className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "…" : "Guardar"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}
