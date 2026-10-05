/**
 * PersonDrawer — full authoring view of a CRM person.
 * Loads fetchPersonAuthoring and renders edit, contact points, affiliations, notes,
 * references, archive/restore, and merge.
 */
import { useCallback, useState } from "react";
import { CommandErrorNotice } from "../CommandErrorNotice";
import { useCommandKey } from "../commandKey";
import { isStaleRefusal, refusalFromError, refusalText, type Refusal } from "../commandRefusal";
import {
  Badge,
  ConfirmDialog,
  Drawer,
  FormField,
  ResourceGate,
  Section,
  Skeleton,
  TextInput,
  fmtDate,
} from "../ui";
import { useResource } from "../useResource";
import {
  archivePerson,
  fetchPersonAuthoring,
  linkPersonOrganization,
  restorePerson,
  unlinkPersonOrganization,
  updatePerson,
  type PersonAuthoringResponse,
} from "./crmAuthoringApi";
import { ContactPointList } from "./ContactPointList";
import { NoteList } from "./NoteList";
import { MergePeopleDialog } from "./MergePeopleDialog";
import { isAdmin } from "./authoring";
import { useAuthSession } from "../../context/AuthSessionContext";

interface Props {
  personId: string;
  onClose: () => void;
  mayAuthor: boolean;
}

export function PersonDrawer({ personId, onClose, mayAuthor }: Props) {
  const { session } = useAuthSession();
  const admin = isAdmin(session);
  const load = useCallback(() => fetchPersonAuthoring(personId), [personId]);
  const [state, reload] = useResource(load, [personId]);
  const [showArchive, setShowArchive] = useState(false);
  const [showMerge, setShowMerge] = useState(false);
  const [archiveError, setArchiveError] = useState<Refusal | null>(null);

  return (
    <Drawer
      open
      onClose={onClose}
      title={
        state.kind === "ready"
          ? state.data.person.display_name
          : "Cargando…"
      }
      subtitle={
        state.kind === "ready" && state.data.person.status === "archived" ? (
          <Badge tone="neutral">Archivada</Badge>
        ) : undefined
      }
    >
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={5} />}>
        {(data) => (
          <PersonDrawerBody
            data={data}
            personId={personId}
            mayAuthor={mayAuthor}
            admin={admin}
            onRefresh={reload}
            showArchive={showArchive}
            setShowArchive={setShowArchive}
            showMerge={showMerge}
            setShowMerge={setShowMerge}
            archiveError={archiveError}
            setArchiveError={setArchiveError}
            onClose={onClose}
          />
        )}
      </ResourceGate>
    </Drawer>
  );
}

function PersonDrawerBody({
  data,
  personId,
  mayAuthor,
  admin,
  onRefresh,
  showArchive,
  setShowArchive,
  showMerge,
  setShowMerge,
  archiveError,
  setArchiveError,
  onClose,
}: {
  data: PersonAuthoringResponse;
  personId: string;
  mayAuthor: boolean;
  admin: boolean;
  onRefresh: () => void;
  showArchive: boolean;
  setShowArchive: (v: boolean) => void;
  showMerge: boolean;
  setShowMerge: (v: boolean) => void;
  archiveError: Refusal | null;
  setArchiveError: (v: Refusal | null) => void;
  onClose: () => void;
}) {
  const { person, contact_points, affiliations, notes, references, removal } = data;
  const [editing, setEditing] = useState(false);

  return (
    <>
      {/* Status */}
      {person.status === "archived" ? (
        <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
          Archivada el {fmtDate(person.archived_at)}
          {person.archive_reason ? ` — ${person.archive_reason}` : ""}
        </div>
      ) : null}

      {/* Edit form */}
      <Section
        title="Datos"
        aside={
          mayAuthor && !editing ? (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken"
            >
              Editar
            </button>
          ) : null
        }
      >
        {editing ? (
          <EditPersonForm
            person={person}
            onDone={() => { setEditing(false); onRefresh(); }}
            onCancel={() => setEditing(false)}
            onReload={onRefresh}
          />
        ) : (
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
            <FieldRow label="Nombre completo" value={person.display_name} />
            <FieldRow label="Nombre" value={person.given_name} />
            <FieldRow label="Apellido" value={person.family_name} />
            <FieldRow label="Título" value={person.title} />
            <FieldRow label="Versión" value={String(person.version)} />
            <FieldRow label="Confirmación" value={person.confirmation} />
          </dl>
        )}
      </Section>

      {/* Contact points */}
      <ContactPointList
        contactPoints={contact_points}
        ownerId={personId}
        ownerVersion={person.version}
        subjectKind="person"
        mayAuthor={mayAuthor && person.status === "active"}
        onRefresh={onRefresh}
      />

      {/* Affiliations */}
      <AffiliationSection
        affiliations={affiliations}
        personId={personId}
        personVersion={person.version}
        mayAuthor={mayAuthor && person.status === "active"}
        onRefresh={onRefresh}
      />

      {/* Notes */}
      <NoteList
        notes={notes}
        subjectKind="person"
        subjectId={personId}
        mayAuthor={mayAuthor && person.status === "active"}
        onRefresh={onRefresh}
      />

      {/* References */}
      <Section title="Referencias">
        <ul className="space-y-1 text-xs text-ink-muted">
          <li>Campañas como destinatario: <strong className="text-ink">{references.campaign_recipients}</strong></li>
          <li>Participaciones en oportunidades: <strong className="text-ink">{references.opportunity_participants}</strong></li>
          <li>Cotizaciones: <strong className="text-ink">{references.quotes}</strong></li>
          <li>Aserciones de evidencia: <strong className="text-ink">{references.evidence_assertions}</strong></li>
          <li>Notas: <strong className="text-ink">{references.notes}</strong></li>
        </ul>
        {removal.reasons.length > 0 ? (
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] font-medium text-bad">Por qué no se puede eliminar</summary>
            <ul className="mt-1 space-y-0.5">
              {removal.reasons.map((r, i) => (
                <li key={i} className="text-[11px] text-ink-muted">· {r}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </Section>

      {/* Admin actions */}
      {admin ? (
        <Section title="Acciones de administración">
          <div className="flex flex-wrap gap-2">
            {person.status === "active" ? (
              <button
                type="button"
                onClick={() => { setShowArchive(true); setArchiveError(null); }}
                className="h-7 rounded-md border border-bad/40 bg-bad-bg px-3 text-xs font-medium text-bad hover:bg-bad/10"
              >
                Archivar persona…
              </button>
            ) : (
              <RestoreButton personId={personId} version={person.version} onDone={onRefresh} onReload={onRefresh} />
            )}
            {person.status === "active" && !person.merged_into_person_id ? (
              <button
                type="button"
                onClick={() => setShowMerge(true)}
                className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken"
              >
                Fusionar con otra persona…
              </button>
            ) : null}
          </div>
        </Section>
      ) : null}

      {showArchive ? (
        <ConfirmDialog
          title={`Archivar persona «${person.display_name}»`}
          lines={[
            `Se archivará la persona "${person.display_name}".`,
            `Sus ${contact_points.length} puntos de contacto y ${affiliations.length} vinculaciones se conservan.`,
            "La persona no se puede eliminar; el archivo es el único cierre.",
          ]}
          requireReason
          reasonLabel="Motivo del archivo"
          confirmLabel="Archivar persona"
          error={archiveError ? refusalText(archiveError) : null}
          onReload={isStaleRefusal(archiveError) ? () => { setArchiveError(null); onRefresh(); } : undefined}
          onCancel={() => { setShowArchive(false); setArchiveError(null); }}
          onConfirm={async (reason) => {
            try {
              await archivePerson({ person_id: personId, expected_version: person.version, note: reason });
              setShowArchive(false);
              setArchiveError(null);
              onRefresh();
            } catch (err) {
              setArchiveError(refusalFromError(err));
            }
          }}
        />
      ) : null}

      {showMerge ? (
        <MergePeopleDialog
          loserId={personId}
          loserName={person.display_name}
          loserVersion={person.version}
          onDone={() => { setShowMerge(false); onClose(); }}
          onCancel={() => setShowMerge(false)}
          onReload={onRefresh}
        />
      ) : null}

      <p className="font-mono text-[10px] text-ink-faint">person {personId}</p>
    </>
  );
}

function FieldRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <>
      <dt className="text-ink-faint">{label}</dt>
      <dd className="font-medium text-ink">{value ?? "—"}</dd>
    </>
  );
}

function EditPersonForm({
  person,
  onDone,
  onCancel,
  onReload,
}: {
  person: PersonAuthoringResponse["person"];
  onDone: () => void;
  onCancel: () => void;
  /** Re-read the person; the typed values stay, the next save carries the new version. */
  onReload: () => void;
}) {
  const [displayName, setDisplayName] = useState(person.display_name);
  const [givenName, setGivenName] = useState(person.given_name ?? "");
  const [familyName, setFamilyName] = useState(person.family_name ?? "");
  const [title, setTitle] = useState(person.title ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const key = useCommandKey();

  async function submit() {
    if (!displayName.trim() || !note.trim()) return;
    setBusy(true);
    setError(null);
    const body = {
      person_id: person.id,
      expected_version: person.version,
      display_name: displayName.trim(),
      given_name: givenName.trim() || null,
      family_name: familyName.trim() || null,
      title: title.trim() || null,
      note: note.trim(),
    };
    try {
      await updatePerson(body, key.keyFor(body));
      key.settle();
      onDone();
    } catch (err) {
      key.settle(err);
      setError(refusalFromError(err));
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <FormField label="Nombre para mostrar" required>
        <TextInput id="edit-display-name" value={displayName} onChange={setDisplayName} disabled={busy} maxLength={200} />
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
      <FormField label="Nota del cambio" required>
        <TextInput value={note} onChange={setNote} placeholder="Motivo" disabled={busy} maxLength={2000} />
      </FormField>
      <CommandErrorNotice
        refusal={error}
        onReload={() => { setError(null); onReload(); }}
        className="col-span-full text-[11px] text-bad"
      />
      <div className="col-span-full flex flex-wrap gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || !displayName.trim() || !note.trim()}
          className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "…" : "Guardar"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">
          Cancelar
        </button>
      </div>
    </div>
  );
}

function RestoreButton({
  personId,
  version,
  onDone,
  onReload,
}: {
  personId: string;
  version: number;
  onDone: () => void;
  onReload: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  const key = useCommandKey();
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="h-7 rounded-md border border-good/40 bg-good-bg px-3 text-xs font-medium text-good hover:bg-good/10"
      >
        Restaurar persona…
      </button>
      {open ? (
        <div className="mt-2 space-y-2 rounded-md border border-line p-3 text-xs">
          <FormField label="Nota de restauración" required>
            <TextInput value={note} onChange={setNote} placeholder="Motivo" disabled={busy} maxLength={2000} />
          </FormField>
          <CommandErrorNotice refusal={error} onReload={() => { setError(null); onReload(); }} />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={async () => {
                if (!note.trim()) return;
                setBusy(true);
                setError(null);
                const body = { person_id: personId, expected_version: version, note: note.trim() };
                try {
                  await restorePerson(body, key.keyFor(body));
                  key.settle();
                  setOpen(false);
                  onDone();
                } catch (err) {
                  key.settle(err);
                  setError(refusalFromError(err));
                  setBusy(false);
                }
              }}
              disabled={busy || !note.trim()}
              className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50"
            >
              {busy ? "…" : "Restaurar"}
            </button>
            <button type="button" onClick={() => setOpen(false)} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">
              Cancelar
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}

function AffiliationSection({
  affiliations,
  personId,
  personVersion,
  mayAuthor,
  onRefresh,
}: {
  affiliations: PersonAuthoringResponse["affiliations"];
  personId: string;
  personVersion: number;
  mayAuthor: boolean;
  onRefresh: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [unlinkId, setUnlinkId] = useState<string | null>(null);
  const [unlinkError, setUnlinkError] = useState<Refusal | null>(null);
  const active = affiliations.filter((a) => !a.valid_to);
  const unlinkTarget = unlinkId ? affiliations.find((a) => a.id === unlinkId) ?? null : null;
  return (
    <Section
      title={`Vinculaciones (${active.length})`}
      aside={
        mayAuthor ? (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken"
          >
            Vincular
          </button>
        ) : null
      }
    >
      {adding && (
        <AddAffiliationForm
          personId={personId}
          personVersion={personVersion}
          onDone={() => { setAdding(false); onRefresh(); }}
          onCancel={() => setAdding(false)}
          onReload={onRefresh}
        />
      )}
      {active.length === 0 && !adding ? (
        <p className="text-xs text-ink-faint">Sin vinculaciones activas.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line">
          {active.map((a) => (
            <li key={a.id} className="px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-medium text-ink">{a.organization_name}</span>
                {a.role_title ? <Badge glyph={false}>{a.role_title}</Badge> : null}
              </div>
              {mayAuthor ? (
                <button
                  type="button"
                  onClick={() => { setUnlinkId(a.id); setUnlinkError(null); }}
                  className="mt-1 text-[11px] font-medium text-bad hover:underline"
                >
                  Desvincular
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {affiliations.filter((a) => a.valid_to).length > 0 ? (
        <p className="mt-1 text-[11px] text-ink-faint">
          {affiliations.filter((a) => a.valid_to).length} vinculaciones cerradas
        </p>
      ) : null}
      {unlinkTarget ? (
        <ConfirmDialog
          title={`Desvincular de ${unlinkTarget.organization_name}`}
          lines={[
            `Se cerrará la vinculación con "${unlinkTarget.organization_name}".`,
            "La afiliación se conserva como historial cerrado.",
          ]}
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Desvincular"
          error={unlinkError ? refusalText(unlinkError) : null}
          onReload={isStaleRefusal(unlinkError) ? () => { setUnlinkError(null); onRefresh(); } : undefined}
          onCancel={() => { setUnlinkId(null); setUnlinkError(null); }}
          onConfirm={async (reason) => {
            try {
              await unlinkPersonOrganization({
                person_id: personId,
                expected_version: personVersion,
                affiliation_id: unlinkTarget.id,
                note: reason,
              });
              setUnlinkId(null);
              setUnlinkError(null);
              onRefresh();
            } catch (err) {
              setUnlinkError(refusalFromError(err));
            }
          }}
        />
      ) : null}
    </Section>
  );
}

function AddAffiliationForm({
  personId,
  personVersion,
  onDone,
  onCancel,
  onReload,
}: {
  personId: string;
  personVersion: number;
  onDone: () => void;
  onCancel: () => void;
  onReload: () => void;
}) {
  const [orgId, setOrgId] = useState("");
  const [roleTitle, setRoleTitle] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Refusal | null>(null);
  const key = useCommandKey();

  async function submit() {
    if (!orgId.trim() || !note.trim()) return;
    setBusy(true);
    setError(null);
    const body = {
      person_id: personId,
      expected_version: personVersion,
      organization_id: orgId.trim(),
      role_title: roleTitle.trim() || null,
      note: note.trim(),
    };
    try {
      await linkPersonOrganization(body, key.keyFor(body));
      key.settle();
      onDone();
    } catch (err) {
      key.settle(err);
      setError(refusalFromError(err));
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3 sm:grid-cols-2">
      <FormField label="ID de organización" required hint="UUID de la organización">
        <TextInput value={orgId} onChange={setOrgId} placeholder="xxxxxxxx-xxxx-…" disabled={busy} />
      </FormField>
      <FormField label="Cargo o rol">
        <TextInput value={roleTitle} onChange={setRoleTitle} placeholder="Investigador, Jefe, etc." disabled={busy} maxLength={200} />
      </FormField>
      <FormField label="Nota de registro" required>
        <TextInput value={note} onChange={setNote} placeholder="Fuente o motivo" disabled={busy} maxLength={2000} />
      </FormField>
      <CommandErrorNotice
        refusal={error}
        onReload={() => { setError(null); onReload(); }}
        className="col-span-full text-[11px] text-bad"
      />
      <div className="col-span-full flex flex-wrap gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || !orgId.trim() || !note.trim()}
          className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50"
        >
          {busy ? "…" : "Vincular"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">
          Cancelar
        </button>
      </div>
    </div>
  );
}
