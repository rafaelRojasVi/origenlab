/**
 * OrgAuthoringSection — lazy-loaded authoring section inside OrgDrawer.
 * Fetches fetchOrganizationAuthoring and renders edit, identifiers, domains,
 * classifications, product lines, contact points, people, notes, references.
 */
import { useCallback, useRef, useState } from "react";
import {
  Badge,
  ConfirmDialog,
  FormField,
  ResourceGate,
  Section,
  SelectInput,
  Skeleton,
  TextInput,
  fmtDate,
} from "../ui";
import { useResource } from "../useResource";
import {
  addOrganizationClassification,
  addOrganizationDomain,
  addOrganizationIdentifier,
  archiveOrganization,
  confirmOrganizationRecord,
  fetchOrganizationAuthoring,
  linkOrganizationProductLine,
  newIdempotencyKey,
  PRODUCT_LINES,
  refusalOf,
  removeOrganizationClassification,
  removeOrganizationDomain,
  restoreOrganizationDomain,
  removeOrganizationIdentifier,
  restoreOrganization,
  unlinkOrganizationProductLine,
  updateOrganization,
  type OrganizationAuthoringResponse,
} from "./crmAuthoringApi";
import { ContactPointList } from "./ContactPointList";
import { NoteList } from "./NoteList";
import { PersonSuggestionList } from "./PersonSuggestionList";
import { WebSuggestionsSection } from "./WebSuggestionsSection";
import { refusalText } from "./webSuggestions";

const PRODUCT_LINE_LABELS: Record<string, string> = {
  hielscher: "Hielscher",
  ortoalresa: "Ortoalresa",
  ika: "IKA",
  "adam-equipment": "Adam Equipment",
  loeser: "Loeser",
  serva: "SERVA",
};

interface Props {
  organizationId: string;
  mayAuthor: boolean;
  admin: boolean;
  /** Called after a change the list behind the card shows too (confirmation, name, people). */
  onChanged?: () => void;
}

export function OrgAuthoringSection({ organizationId, mayAuthor, admin, onChanged }: Props) {
  const load = useCallback(() => fetchOrganizationAuthoring(organizationId), [organizationId]);
  const [state, reload] = useResource(load, [organizationId]);
  const refresh = useCallback(() => {
    reload();
    onChanged?.();
  }, [reload, onChanged]);
  return (
    <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={4} />}>
      {(data) => (
        <OrgAuthoringBody
          data={data}
          organizationId={organizationId}
          mayAuthor={mayAuthor}
          admin={admin}
          onRefresh={refresh}
        />
      )}
    </ResourceGate>
  );
}

function OrgAuthoringBody({
  data,
  organizationId,
  mayAuthor,
  admin,
  onRefresh,
}: {
  data: OrganizationAuthoringResponse;
  organizationId: string;
  mayAuthor: boolean;
  admin: boolean;
  onRefresh: () => void;
}) {
  const { organization, identifiers, domains, classifications, product_lines, contact_points, people, notes, references, removal } = data;
  const [editing, setEditing] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  return (
    <>
      <ConfirmationBar
        organization={organization}
        mayAuthor={mayAuthor && organization.status === "active"}
        onDone={onRefresh}
      />

      {data.web_suggestions ? (
        <WebSuggestionsSection
          data={data}
          suggestion={data.web_suggestions}
          mayAuthor={mayAuthor && organization.status === "active"}
          onRefresh={onRefresh}
        />
      ) : null}

      {data.person_suggestions && data.person_suggestions.length > 0 ? (
        <Section title={`Personas sugeridas (${data.person_suggestions.length})`}>
          <PersonSuggestionList
            items={data.person_suggestions}
            mayAuthor={mayAuthor && organization.status === "active"}
            showOrganization={false}
            onCreated={onRefresh}
          />
        </Section>
      ) : null}

      {/* Status */}
      {organization.status === "archived" ? (
        <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
          Archivada el {fmtDate(organization.archived_at)}
          {organization.archive_reason ? ` — ${organization.archive_reason}` : ""}
        </div>
      ) : null}

      {/* Edit */}
      <Section
        title="Edición"
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
          <EditOrgForm
            org={organization}
            onDone={() => { setEditing(false); onRefresh(); }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <dt className="text-ink-faint">Nombre</dt>
            <dd className="font-medium text-ink">{organization.name}</dd>
            <dt className="text-ink-faint">Nombre legal</dt>
            <dd className="text-ink">{organization.legal_name ?? "—"}</dd>
            <dt className="text-ink-faint">Tipo</dt>
            <dd className="text-ink">{organization.kind}</dd>
            <dt className="text-ink-faint">Versión</dt>
            <dd className="text-ink">{organization.version}</dd>
          </dl>
        )}
      </Section>

      {/* Identifiers */}
      <IdentifierSection
        identifiers={identifiers}
        organizationId={organizationId}
        orgVersion={organization.version}
        mayAuthor={mayAuthor && organization.status === "active"}
        onRefresh={onRefresh}
      />

      {/* Domains */}
      <DomainSection
        domains={domains}
        organizationId={organizationId}
        orgVersion={organization.version}
        mayAuthor={mayAuthor && organization.status === "active"}
        onRefresh={onRefresh}
      />

      {/* Classifications */}
      <ClassificationSection
        classifications={classifications}
        organizationId={organizationId}
        orgVersion={organization.version}
        mayAuthor={mayAuthor && organization.status === "active"}
        onRefresh={onRefresh}
      />

      {/* Product lines */}
      <ProductLineSection
        productLines={product_lines}
        organizationId={organizationId}
        orgVersion={organization.version}
        mayAuthor={mayAuthor && organization.status === "active"}
        onRefresh={onRefresh}
      />

      {/* Contact points */}
      <ContactPointList
        contactPoints={contact_points}
        ownerId={organizationId}
        ownerVersion={organization.version}
        subjectKind="organization"
        mayAuthor={mayAuthor && organization.status === "active"}
        onRefresh={onRefresh}
      />

      {/* People (affiliations) */}
      <Section title={`Personas (${people.filter((p) => !p.valid_to).length})`}>
        {people.filter((p) => !p.valid_to).length === 0 ? (
          <p className="text-xs text-ink-faint">Sin personas vinculadas.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {people.filter((p) => !p.valid_to).map((p) => (
              <li key={p.affiliation_id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
                <span className="min-w-0 flex-1 truncate font-medium text-ink">{p.display_name}</span>
                {p.role_title ? <Badge glyph={false}>{p.role_title}</Badge> : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Notes */}
      <NoteList
        notes={notes}
        subjectKind="organization"
        subjectId={organizationId}
        mayAuthor={mayAuthor && organization.status === "active"}
        onRefresh={onRefresh}
      />

      {/* References */}
      <Section title="Referencias">
        <ul className="space-y-1 text-xs text-ink-muted">
          <li>Destinatarios de campañas: <strong className="text-ink">{references.campaign_recipients}</strong></li>
          <li>Oportunidades: <strong className="text-ink">{references.opportunities}</strong></li>
          <li>Cotizaciones: <strong className="text-ink">{references.quotes}</strong></li>
          <li>Vinculaciones a personas: <strong className="text-ink">{references.affiliations}</strong></li>
          <li>Aserciones de evidencia: <strong className="text-ink">{references.evidence_assertions}</strong></li>
          <li>Productos en catálogo: <strong className="text-ink">{references.catalog_products}</strong></li>
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
            {organization.status === "active" ? (
              <button
                type="button"
                onClick={() => { setShowArchive(true); setArchiveError(null); }}
                className="h-7 rounded-md border border-bad/40 bg-bad-bg px-3 text-xs font-medium text-bad hover:bg-bad/10"
              >
                Archivar organización…
              </button>
            ) : (
              <RestoreOrgButton orgId={organizationId} version={organization.version} onDone={onRefresh} />
            )}
          </div>
        </Section>
      ) : null}

      {showArchive ? (
        <ConfirmDialog
          title={`Archivar «${organization.name}»`}
          lines={[
            `Se archivará la organización "${organization.name}".`,
            "Sus identificadores, dominios y vinculaciones se conservan.",
          ]}
          requireReason
          reasonLabel="Motivo del archivo"
          confirmLabel="Archivar organización"
          error={archiveError}
          onCancel={() => { setShowArchive(false); setArchiveError(null); }}
          onConfirm={async (reason) => {
            try {
              await archiveOrganization({ organization_id: organizationId, expected_version: organization.version, note: reason });
              setShowArchive(false);
              setArchiveError(null);
              onRefresh();
            } catch (err) {
              const r = refusalOf(err);
              setArchiveError(r ? `${r.code}: ${r.message}` : String(err));
            }
          }}
        />
      ) : null}
    </>
  );
}

/**
 * Whether an operator has looked at this institution. «Confirmar institución» while it is only
 * machine-proposed; afterwards who confirmed it and when (`organization.confirmed` event).
 */
function ConfirmationBar({
  organization,
  mayAuthor,
  onDone,
}: {
  organization: OrganizationAuthoringResponse["organization"];
  mayAuthor: boolean;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());

  if (organization.confirmation === "confirmed") {
    return (
      <p className="flex flex-wrap items-center gap-1.5 text-xs text-ink-muted" data-testid="org-confirmation">
        <Badge tone="good">Confirmada</Badge>
        <span>
          {organization.confirmed_by_name ? `por ${organization.confirmed_by_name}` : "por un operador"}
          {organization.confirmed_at ? ` el ${fmtDate(organization.confirmed_at)}` : ""}
        </span>
      </p>
    );
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await confirmOrganizationRecord(
        { organization_id: organization.id, expected_version: organization.version },
        keyRef.current,
      );
      onDone();
    } catch (err) {
      const r = refusalOf(err);
      setError(refusalText(r?.code ?? "error", r?.message ?? String(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="org-confirmation">
      <Badge tone="warn" title="Nombre propuesto por máquina desde la migración">Propuesta</Badge>
      <span className="text-ink-muted">Ningún operador ha revisado esta institución.</span>
      {mayAuthor ? (
        <button
          type="button"
          onClick={() => void confirm()}
          disabled={busy}
          className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50"
        >
          {busy ? "…" : "Confirmar institución"}
        </button>
      ) : null}
      {error ? <p className="w-full text-[11px] text-bad">{error}</p> : null}
    </div>
  );
}

function EditOrgForm({
  org,
  onDone,
  onCancel,
}: {
  org: OrganizationAuthoringResponse["organization"];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(org.name);
  const [legalName, setLegalName] = useState(org.legal_name ?? "");
  const [kind, setKind] = useState(org.kind);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());

  async function submit() {
    if (!name.trim() || !note.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await updateOrganization({
        organization_id: org.id,
        expected_version: org.version,
        name: name.trim(),
        legal_name: legalName.trim() || null,
        kind: kind,
        note: note.trim(),
      }, keyRef.current);
      onDone();
    } catch (err) {
      const r = refusalOf(err);
      if (r?.code === "stale_version") setError("Otro operador modificó este registro; recarga y vuelve a intentar.");
      else setError(r ? `${r.code}: ${r.message}` : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <FormField label="Nombre" required>
        <TextInput value={name} onChange={setName} disabled={busy} maxLength={300} />
      </FormField>
      <FormField label="Nombre legal">
        <TextInput value={legalName} onChange={setLegalName} disabled={busy} maxLength={400} />
      </FormField>
      <FormField label="Tipo">
        <SelectInput
          value={kind}
          onChange={setKind}
          options={[
            { value: "company", label: "Empresa" },
            { value: "university", label: "Universidad" },
            { value: "public_institution", label: "Institución pública" },
            { value: "hospital", label: "Hospital" },
            { value: "laboratory", label: "Laboratorio" },
            { value: "other", label: "Otro" },
          ]}
          disabled={busy}
        />
      </FormField>
      <FormField label="Nota del cambio" required>
        <TextInput value={note} onChange={setNote} placeholder="Motivo" disabled={busy} maxLength={2000} />
      </FormField>
      {error ? <p className="col-span-full text-[11px] text-bad">{error}</p> : null}
      <div className="col-span-full flex flex-wrap gap-2">
        <button type="button" onClick={submit} disabled={busy || !name.trim() || !note.trim()} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50">
          {busy ? "…" : "Guardar"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">
          Cancelar
        </button>
      </div>
    </div>
  );
}

function RestoreOrgButton({ orgId, version, onDone }: { orgId: string; version: number; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="h-7 rounded-md border border-good/40 bg-good-bg px-3 text-xs font-medium text-good hover:bg-good/10">
        Restaurar organización…
      </button>
      {open ? (
        <div className="mt-2 space-y-2 rounded-md border border-line p-3 text-xs">
          <FormField label="Nota de restauración" required>
            <TextInput value={note} onChange={setNote} placeholder="Motivo" disabled={busy} maxLength={2000} />
          </FormField>
          {error ? <p className="text-[11px] text-bad">{error}</p> : null}
          <div className="flex gap-2">
            <button type="button" onClick={async () => {
              if (!note.trim()) return;
              setBusy(true);
              try {
                await restoreOrganization({ organization_id: orgId, expected_version: version, note: note.trim() }, keyRef.current);
                setOpen(false);
                onDone();
              } catch (err) {
                const r = refusalOf(err);
                setError(r ? `${r.code}: ${r.message}` : String(err));
                setBusy(false);
              }
            }} disabled={busy || !note.trim()} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50">
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

function IdentifierSection({
  identifiers,
  organizationId,
  orgVersion,
  mayAuthor,
  onRefresh,
}: {
  identifiers: OrganizationAuthoringResponse["identifiers"];
  organizationId: string;
  orgVersion: number;
  mayAuthor: boolean;
  onRefresh: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [removeId, setRemoveId] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const active = identifiers.filter((i) => !i.removed_at);
  const removeTarget = removeId ? identifiers.find((i) => i.id === removeId) ?? null : null;
  return (
    <Section
      title={`Identificadores (${active.length})`}
      aside={
        mayAuthor ? (
          <button type="button" onClick={() => setAdding(true)} className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken">
            Agregar
          </button>
        ) : null
      }
    >
      {adding && (
        <AddIdentifierForm
          organizationId={organizationId}
          orgVersion={orgVersion}
          onDone={() => { setAdding(false); onRefresh(); }}
          onCancel={() => setAdding(false)}
        />
      )}
      {active.length === 0 && !adding ? (
        <p className="text-xs text-ink-faint">Sin identificadores.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line">
          {active.map((id) => (
            <li key={id.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
              <span className="font-mono font-medium text-ink">{id.value_norm}</span>
              <Badge glyph={false}>{id.scheme}</Badge>
              {mayAuthor ? (
                <button type="button" onClick={() => { setRemoveId(id.id); setRemoveError(null); }} className="ml-auto text-[11px] font-medium text-bad hover:underline">
                  Eliminar
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {removeTarget ? (
        <ConfirmDialog
          title="Eliminar identificador"
          lines={[
            `Se eliminará el identificador "${removeTarget.value_norm}" (${removeTarget.scheme}).`,
            "La eliminación es lógica; el registro se conserva.",
          ]}
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Eliminar"
          error={removeError}
          onCancel={() => { setRemoveId(null); setRemoveError(null); }}
          onConfirm={async (reason) => {
            try {
              await removeOrganizationIdentifier({ organization_id: organizationId, expected_version: orgVersion, identifier_id: removeTarget.id, note: reason });
              setRemoveId(null);
              setRemoveError(null);
              onRefresh();
            } catch (err) {
              const r = refusalOf(err);
              setRemoveError(r ? `${r.code}: ${r.message}` : String(err));
            }
          }}
        />
      ) : null}
    </Section>
  );
}

function AddIdentifierForm({ organizationId, orgVersion, onDone, onCancel }: {
  organizationId: string;
  orgVersion: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [scheme, setScheme] = useState("rut");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());
  async function submit() {
    if (!value.trim() || !note.trim()) return;
    setBusy(true);
    try {
      await addOrganizationIdentifier({ organization_id: organizationId, expected_version: orgVersion, scheme, value: value.trim(), note: note.trim() }, keyRef.current);
      onDone();
    } catch (err) {
      const r = refusalOf(err);
      setError(r ? `${r.code}: ${r.message}` : String(err));
      setBusy(false);
    }
  }
  return (
    <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3 sm:grid-cols-2">
      <FormField label="Esquema">
        <SelectInput value={scheme} onChange={setScheme} options={[{ value: "rut", label: "RUT" }, { value: "chilecompra", label: "ChileCompra" }, { value: "other", label: "Otro" }]} disabled={busy} />
      </FormField>
      <FormField label="Valor" required>
        <TextInput value={value} onChange={setValue} disabled={busy} maxLength={100} />
      </FormField>
      <FormField label="Nota" required>
        <TextInput value={note} onChange={setNote} placeholder="Fuente" disabled={busy} maxLength={2000} />
      </FormField>
      {error ? <p className="col-span-full text-[11px] text-bad">{error}</p> : null}
      <div className="col-span-full flex gap-2">
        <button type="button" onClick={submit} disabled={busy || !value.trim() || !note.trim()} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50">{busy ? "…" : "Agregar"}</button>
        <button type="button" onClick={onCancel} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">Cancelar</button>
      </div>
    </div>
  );
}

function DomainSection({ domains, organizationId, orgVersion, mayAuthor, onRefresh }: {
  domains: OrganizationAuthoringResponse["domains"];
  organizationId: string;
  orgVersion: number;
  mayAuthor: boolean;
  onRefresh: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [removeId, setRemoveId] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [restoreId, setRestoreId] = useState<string | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const active = domains.filter((d) => !d.removed_at);
  // Soft-removed rows stay on the organization; an operator may bring one back on the same
  // row (never a duplicate), and only here — a domain is never restored onto another organization.
  const removed = domains.filter((d) => !!d.removed_at);
  const removeTarget = removeId ? active.find((d) => d.id === removeId) ?? null : null;
  const restoreTarget = restoreId ? removed.find((d) => d.id === restoreId) ?? null : null;
  return (
    <Section title={`Dominios (${active.length})`} aside={
      mayAuthor ? <button type="button" onClick={() => setAdding(true)} className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken">Agregar</button> : null
    }>
      {adding && (
        <AddDomainForm organizationId={organizationId} orgVersion={orgVersion} onDone={() => { setAdding(false); onRefresh(); }} onCancel={() => setAdding(false)} />
      )}
      {active.length === 0 && !adding ? <p className="text-xs text-ink-faint">Sin dominios.</p> : (
        <ul className="divide-y divide-line rounded-md border border-line">
          {active.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
              <span className="font-mono text-ink">{d.domain_norm}</span>
              {d.scope ? <Badge glyph={false}>{d.scope}</Badge> : null}
              {mayAuthor ? <button type="button" onClick={() => { setRemoveId(d.id); setRemoveError(null); }} className="ml-auto text-[11px] font-medium text-bad hover:underline">Eliminar</button> : null}
            </li>
          ))}
        </ul>
      )}
      {removed.length > 0 ? (
        <details className="mt-2 rounded-md border border-dashed border-line" data-testid="removed-domains">
          <summary className="cursor-pointer list-none px-3 py-1.5 text-[11px] text-ink-faint">
            Eliminados ({removed.length})
          </summary>
          <ul className="divide-y divide-line border-t border-line" aria-label="Dominios eliminados">
            {removed.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
                <span className="font-mono text-ink-muted line-through">{d.domain_norm}</span>
                {d.scope ? <Badge glyph={false}>{d.scope}</Badge> : null}
                <span className="text-[11px] text-ink-faint">
                  Eliminado el {fmtDate(d.removed_at)}
                  {d.remove_reason ? ` — ${d.remove_reason}` : ""}
                </span>
                {mayAuthor ? (
                  <button
                    type="button"
                    onClick={() => { setRestoreId(d.id); setRestoreError(null); }}
                    className="ml-auto text-[11px] font-medium text-ink hover:underline"
                  >
                    Restaurar
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {restoreTarget ? (
        <ConfirmDialog
          title="Restaurar dominio"
          lines={[
            `Se restaurará el dominio "${restoreTarget.domain_norm}" en esta organización.`,
            "Vuelve a estar vigente sobre la misma fila; ningún dominio se traslada a otra organización.",
          ]}
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Restaurar"
          error={restoreError}
          onCancel={() => { setRestoreId(null); setRestoreError(null); }}
          onConfirm={async (reason) => {
            try {
              await restoreOrganizationDomain({ organization_id: organizationId, expected_version: orgVersion, domain_id: restoreTarget.id, note: reason });
              setRestoreId(null);
              setRestoreError(null);
              onRefresh();
            } catch (err) {
              const r = refusalOf(err);
              setRestoreError(r ? `${r.code}: ${r.message}` : String(err));
            }
          }}
        />
      ) : null}
      {removeTarget ? (
        <ConfirmDialog
          title="Eliminar dominio"
          lines={[`Se eliminará el dominio "${removeTarget.domain_norm}".`, "La eliminación es lógica."]}
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Eliminar"
          error={removeError}
          onCancel={() => { setRemoveId(null); setRemoveError(null); }}
          onConfirm={async (reason) => {
            try {
              await removeOrganizationDomain({ organization_id: organizationId, expected_version: orgVersion, domain_id: removeTarget.id, note: reason });
              setRemoveId(null);
              setRemoveError(null);
              onRefresh();
            } catch (err) {
              const r = refusalOf(err);
              setRemoveError(r ? `${r.code}: ${r.message}` : String(err));
            }
          }}
        />
      ) : null}
    </Section>
  );
}

function AddDomainForm({ organizationId, orgVersion, onDone, onCancel }: { organizationId: string; orgVersion: number; onDone: () => void; onCancel: () => void; }) {
  const [domain, setDomain] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());
  async function submit() {
    if (!domain.trim() || !note.trim()) return;
    setBusy(true);
    try {
      await addOrganizationDomain({ organization_id: organizationId, expected_version: orgVersion, domain: domain.trim(), note: note.trim() }, keyRef.current);
      onDone();
    } catch (err) {
      const r = refusalOf(err);
      setError(r ? `${r.code}: ${r.message}` : String(err));
      setBusy(false);
    }
  }
  return (
    <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3 sm:grid-cols-2">
      <FormField label="Dominio" required>
        <TextInput value={domain} onChange={setDomain} placeholder="ejemplo.com" disabled={busy} maxLength={253} />
      </FormField>
      <FormField label="Nota" required>
        <TextInput value={note} onChange={setNote} placeholder="Fuente" disabled={busy} maxLength={2000} />
      </FormField>
      {error ? <p className="col-span-full text-[11px] text-bad">{error}</p> : null}
      <div className="col-span-full flex gap-2">
        <button type="button" onClick={submit} disabled={busy || !domain.trim() || !note.trim()} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50">{busy ? "…" : "Agregar"}</button>
        <button type="button" onClick={onCancel} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">Cancelar</button>
      </div>
    </div>
  );
}

function ClassificationSection({ classifications, organizationId, orgVersion, mayAuthor, onRefresh }: {
  classifications: OrganizationAuthoringResponse["classifications"];
  organizationId: string;
  orgVersion: number;
  mayAuthor: boolean;
  onRefresh: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [removeId, setRemoveId] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const active = classifications.filter((c) => !c.valid_to);
  const removeTarget = removeId ? classifications.find((c) => c.id === removeId) ?? null : null;
  return (
    <Section title={`Clasificaciones (${active.length})`} aside={
      mayAuthor ? <button type="button" onClick={() => setAdding(true)} className="h-6 rounded-md border border-line bg-canvas-raised px-2.5 text-[11px] font-medium text-ink hover:bg-canvas-sunken">Agregar</button> : null
    }>
      {adding && (
        <AddClassificationForm organizationId={organizationId} orgVersion={orgVersion} onDone={() => { setAdding(false); onRefresh(); }} onCancel={() => setAdding(false)} />
      )}
      {active.length === 0 && !adding ? <p className="text-xs text-ink-faint">Sin clasificaciones activas.</p> : (
        <ul className="divide-y divide-line rounded-md border border-line">
          {active.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
              <Badge glyph={false}>{c.role}</Badge>
              {c.valid_from ? <span className="text-ink-faint">desde {fmtDate(c.valid_from)}</span> : null}
              {mayAuthor ? <button type="button" onClick={() => { setRemoveId(c.id); setRemoveError(null); }} className="ml-auto text-[11px] font-medium text-bad hover:underline">Cerrar</button> : null}
            </li>
          ))}
        </ul>
      )}
      {removeTarget ? (
        <ConfirmDialog
          title={`Cerrar clasificación "${removeTarget.role}"`}
          lines={[`Se cerrará la clasificación "${removeTarget.role}".`]}
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Cerrar"
          error={removeError}
          onCancel={() => { setRemoveId(null); setRemoveError(null); }}
          onConfirm={async (reason) => {
            try {
              await removeOrganizationClassification({ organization_id: organizationId, expected_version: orgVersion, relationship_id: removeTarget.id, note: reason });
              setRemoveId(null);
              setRemoveError(null);
              onRefresh();
            } catch (err) {
              const r = refusalOf(err);
              setRemoveError(r ? `${r.code}: ${r.message}` : String(err));
            }
          }}
        />
      ) : null}
    </Section>
  );
}

function AddClassificationForm({ organizationId, orgVersion, onDone, onCancel }: { organizationId: string; orgVersion: number; onDone: () => void; onCancel: () => void; }) {
  const [role, setRole] = useState("customer");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());
  async function submit() {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await addOrganizationClassification({ organization_id: organizationId, expected_version: orgVersion, role, note: note.trim() }, keyRef.current);
      onDone();
    } catch (err) {
      const r = refusalOf(err);
      setError(r ? `${r.code}: ${r.message}` : String(err));
      setBusy(false);
    }
  }
  return (
    <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3 sm:grid-cols-2">
      <FormField label="Rol">
        <SelectInput value={role} onChange={setRole} options={[
          { value: "customer", label: "Cliente" },
          { value: "supplier", label: "Proveedor" },
          { value: "manufacturer", label: "Fabricante" },
          { value: "prospect", label: "Prospecto" },
          { value: "partner", label: "Socio" },
          { value: "competitor", label: "Competidor" },
        ]} disabled={busy} />
      </FormField>
      <FormField label="Nota" required>
        <TextInput value={note} onChange={setNote} placeholder="Motivo" disabled={busy} maxLength={2000} />
      </FormField>
      {error ? <p className="col-span-full text-[11px] text-bad">{error}</p> : null}
      <div className="col-span-full flex gap-2">
        <button type="button" onClick={submit} disabled={busy || !note.trim()} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50">{busy ? "…" : "Agregar"}</button>
        <button type="button" onClick={onCancel} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">Cancelar</button>
      </div>
    </div>
  );
}

function ProductLineSection({ productLines, organizationId, orgVersion, mayAuthor, onRefresh }: {
  productLines: OrganizationAuthoringResponse["product_lines"];
  organizationId: string;
  orgVersion: number;
  mayAuthor: boolean;
  onRefresh: () => void;
}) {
  const [addingLine, setAddingLine] = useState<string | null>(null);
  const [unlinkId, setUnlinkId] = useState<string | null>(null);
  const [unlinkError, setUnlinkError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const keyRef = useRef(newIdempotencyKey());
  const active = productLines.filter((p) => !p.valid_to);
  const activeIds = new Set(active.map((p) => p.line_id));
  const unlinkTarget = unlinkId ? productLines.find((p) => p.id === unlinkId) ?? null : null;

  async function doLink() {
    if (!addingLine || !note.trim()) return;
    setBusy(true);
    try {
      await linkOrganizationProductLine({ organization_id: organizationId, expected_version: orgVersion, line_id: addingLine, note: note.trim() }, keyRef.current);
      setAddingLine(null);
      setNote("");
      onRefresh();
    } catch (err) {
      const r = refusalOf(err);
      setAddError(r ? `${r.code}: ${r.message}` : String(err));
      setBusy(false);
    }
  }

  return (
    <Section title={`Líneas de productos (${active.length})`}>
      <div className="flex flex-wrap gap-2">
        {PRODUCT_LINES.map((line) => {
          const linked = active.find((p) => p.line_id === line);
          return (
            <div key={line} className="flex items-center gap-1">
              <Badge tone={linked ? "good" : "neutral"} glyph={linked ? true : false}>
                {PRODUCT_LINE_LABELS[line] ?? line}
              </Badge>
              {mayAuthor && !activeIds.has(line) ? (
                <button type="button" onClick={() => { setAddingLine(line); setAddError(null); }} className="text-[10px] text-brand-700 hover:underline">+</button>
              ) : null}
              {mayAuthor && linked ? (
                <button type="button" onClick={() => { setUnlinkId(linked.id); setUnlinkError(null); }} className="text-[10px] text-bad hover:underline">−</button>
              ) : null}
            </div>
          );
        })}
      </div>
      {addingLine ? (
        <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-brand-600/20 bg-canvas-sunken/60 p-3">
          <p className="text-xs font-medium text-ink">Vincular {PRODUCT_LINE_LABELS[addingLine] ?? addingLine}</p>
          <FormField label="Nota" required>
            <TextInput value={note} onChange={setNote} placeholder="Motivo" disabled={busy} maxLength={2000} />
          </FormField>
          {addError ? <p className="text-[11px] text-bad">{addError}</p> : null}
          <div className="flex gap-2">
            <button type="button" onClick={doLink} disabled={busy || !note.trim()} className="h-7 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black disabled:opacity-50">{busy ? "…" : "Vincular"}</button>
            <button type="button" onClick={() => setAddingLine(null)} className="h-7 rounded-md border border-line bg-canvas-raised px-3 text-xs font-medium text-ink hover:bg-canvas-sunken">Cancelar</button>
          </div>
        </div>
      ) : null}
      {unlinkTarget ? (
        <ConfirmDialog
          title={`Desvincular ${PRODUCT_LINE_LABELS[unlinkTarget.line_id] ?? unlinkTarget.line_id}`}
          lines={[`Se desvinculará la línea "${unlinkTarget.line_name ?? unlinkTarget.line_id}".`]}
          requireReason
          reasonLabel="Motivo"
          confirmLabel="Desvincular"
          error={unlinkError}
          onCancel={() => { setUnlinkId(null); setUnlinkError(null); }}
          onConfirm={async (reason) => {
            try {
              await unlinkOrganizationProductLine({ organization_id: organizationId, expected_version: orgVersion, link_id: unlinkTarget.id, note: reason });
              setUnlinkId(null);
              setUnlinkError(null);
              onRefresh();
            } catch (err) {
              const r = refusalOf(err);
              setUnlinkError(r ? `${r.code}: ${r.message}` : String(err));
            }
          }}
        />
      ) : null}
    </Section>
  );
}
