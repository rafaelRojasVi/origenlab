/**
 * «Intereses observados» on CRM cards, and the equipment-line view behind a click.
 *
 * The lines are the Marketing taxonomy's six families (one brand each), served by the API; this
 * file defines no line of its own. Every interest shown carries its source and date. A card with
 * no evidence says «Sin información» — which is only said when the read succeeded: a read that
 * failed or is not enabled here says so instead, because "unknown" is not "none".
 */

import { useMemo } from "react";
import { fetchEquipmentInterests } from "../crmApi";
import type { EquipmentInterestsResponse, EquipmentLine, InterestInstitution, InterestPerson } from "../crmTypes";
import { fetchTaxonomy } from "../marketing/marketingApi";
import type { AudienceInterest, EquipmentTaxonomy } from "../marketing/marketingTypes";
import { EvidenceList, useInterestLabel } from "../marketing/interestEvidence";
import { Badge, Drawer, Section, fmtDate } from "../ui";
import { useResource, type ResourceState } from "../useResource";

export interface InterestData {
  index: EquipmentInterestsResponse;
  taxonomy: EquipmentTaxonomy;
  byOrganization: Map<string, AudienceInterest[]>;
  byAddressRef: Map<string, InterestPerson>;
  byContactPoint: Map<string, InterestPerson>;
}

async function loadInterests(): Promise<InterestData> {
  const [index, taxonomy] = await Promise.all([fetchEquipmentInterests(), fetchTaxonomy()]);
  return {
    index,
    taxonomy,
    byOrganization: new Map(index.institutions.map((i) => [i.organization_id, i.interests])),
    byAddressRef: new Map(index.persons.map((p) => [p.address_ref, p])),
    byContactPoint: new Map(
      index.persons.filter((p) => p.contact_point_id).map((p) => [p.contact_point_id as string, p]),
    ),
  };
}

export function useEquipmentInterests(): ResourceState<InterestData> {
  const [state] = useResource(loadInterests);
  return state;
}

function lineOf(data: InterestData, familyId: string): EquipmentLine | undefined {
  return data.index.lines.find((l) => l.family_id === familyId);
}

function Dot({ color }: { color: string | null | undefined }) {
  return <span aria-hidden="true" className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: color ?? "currentColor" }} />;
}

/** Interests grouped by line, in taxonomy order. */
function byLine(data: InterestData, interests: AudienceInterest[]): [EquipmentLine, AudienceInterest[]][] {
  return data.index.lines
    .map((l) => [l, interests.filter((i) => i.family_id === l.family_id)] as [EquipmentLine, AudienceInterest[]])
    .filter(([, items]) => items.length > 0);
}

/**
 * The «Intereses observados» block of a card. `interests` undefined means "no evidence found for
 * this card" once the read is ready.
 */
export function EquipmentInterestsBlock({
  state,
  interests,
  onSelectLine,
  compact = false,
  heading = true,
}: {
  state: ResourceState<InterestData>;
  interests: AudienceInterest[] | undefined;
  onSelectLine: (familyId: string) => void;
  compact?: boolean;
  /** Off where the surrounding section already says «Intereses observados». */
  heading?: boolean;
}) {
  return (
    <div data-testid="equipment-interests">
      {heading ? <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">Intereses observados</p> : null}
      {state.kind === "loading" ? (
        <p className="text-[11px] text-ink-faint">Cargando…</p>
      ) : state.kind !== "ready" ? (
        <p className="text-[11px] text-ink-faint" title={state.message}>
          No disponible en este entorno
        </p>
      ) : !interests || interests.length === 0 ? (
        <p className="text-[11px] text-ink-muted">Sin información</p>
      ) : (
        <Lines data={state.data} interests={interests} onSelectLine={onSelectLine} compact={compact} />
      )}
    </div>
  );
}

function Lines({
  data,
  interests,
  onSelectLine,
  compact,
}: {
  data: InterestData;
  interests: AudienceInterest[];
  onSelectLine: (familyId: string) => void;
  compact: boolean;
}) {
  const label = useInterestLabel(data.taxonomy);
  return (
    <ul className="mt-0.5 space-y-1">
      {byLine(data, interests).map(([line, items]) => {
        const shown = compact ? items.slice(0, 1) : items;
        return (
          <li key={line.family_id} className="text-[11px]">
            <button
              type="button"
              onClick={() => onSelectLine(line.family_id)}
              className="inline-flex max-w-full items-center gap-1.5 font-semibold text-ink hover:text-brand-700 hover:underline"
              title="Ver contactos e instituciones de esta línea"
            >
              <Dot color={line.color} />
              <span className="truncate">{line.name}</span>
            </button>
            {shown.map((i, n) => (
              <p key={n} className="truncate pl-3.5 text-ink-muted">
                {label(i)} · {i.basis_label} · {i.source.label} · {i.date ? fmtDate(i.date) : "sin fecha"}
              </p>
            ))}
            {compact && items.length > 1 ? <p className="pl-3.5 text-ink-faint">+{items.length - 1} evidencia(s) más</p> : null}
          </li>
        );
      })}
    </ul>
  );
}

/** Line dots for a list row: shown only where there is evidence (rows are not cards). */
export function LineDots({
  state,
  interests,
  onSelectLine,
}: {
  state: ResourceState<InterestData>;
  interests: AudienceInterest[] | undefined;
  onSelectLine: (familyId: string) => void;
}) {
  if (state.kind !== "ready" || !interests?.length) return null;
  return (
    <>
      {byLine(state.data, interests).map(([line, items]) => (
        <button
          key={line.family_id}
          type="button"
          onClick={() => onSelectLine(line.family_id)}
          title={`${items.length} evidencia(s) · ver la línea`}
          className="inline-flex items-center gap-1 rounded-full border border-line px-1.5 py-px text-[11px] text-ink hover:border-brand-600/40"
        >
          <Dot color={line.color} />
          {line.name}
        </button>
      ))}
    </>
  );
}

/** The six lines with their counts; a click opens the line. */
export function EquipmentLineStrip({
  state,
  onSelectLine,
}: {
  state: ResourceState<InterestData>;
  onSelectLine: (familyId: string) => void;
}) {
  if (state.kind !== "ready") return null;
  return (
    <nav aria-label="Líneas de equipos" className="flex flex-wrap gap-1.5" data-testid="equipment-line-strip">
      {state.data.index.lines.map((l) => {
        const n = l.crm_people + l.address_only + l.institutions;
        return (
          <button
            key={l.family_id}
            type="button"
            onClick={() => onSelectLine(l.family_id)}
            className="inline-flex items-center gap-1.5 rounded-full border border-line bg-canvas-raised px-2 py-0.5 text-[11px] text-ink hover:border-brand-600/40"
          >
            <Dot color={l.color} />
            {l.name}
            <span className="tabular-nums text-ink-faint">{n ? n : "Sin información"}</span>
          </button>
        );
      })}
    </nav>
  );
}

/** Who has evidenced interest in one line: CRM people, address-only evidence, institutions. */
export function EquipmentLineDrawer({
  state,
  familyId,
  onClose,
}: {
  state: ResourceState<InterestData>;
  familyId: string | null;
  onClose: () => void;
}) {
  if (!familyId || state.kind !== "ready") return null;
  const data = state.data;
  const line = lineOf(data, familyId);
  if (!line) return null;
  return <LineDrawerBody data={data} line={line} onClose={onClose} />;
}

function LineDrawerBody({ data, line, onClose }: { data: InterestData; line: EquipmentLine; onClose: () => void }) {
  const label = useInterestLabel(data.taxonomy);
  const { people, addressOnly, institutions } = useMemo(() => {
    const inLine = (xs: AudienceInterest[]) => xs.filter((i) => i.family_id === line.family_id);
    const persons = data.index.persons
      .map((p) => ({ ...p, interests: inLine(p.interests) }))
      .filter((p) => p.interests.length > 0);
    return {
      people: persons.filter((p) => p.link === "crm_person"),
      addressOnly: persons.filter((p) => p.link === "address_only"),
      institutions: data.index.institutions
        .map((i) => ({ ...i, interests: inLine(i.interests) }))
        .filter((i) => i.interests.length > 0),
    };
  }, [data, line.family_id]);
  const brands = data.taxonomy.brands.filter((b) => line.brand_ids.includes(b.id)).map((b) => b.name).join(", ");
  return (
    <Drawer open onClose={onClose} title={line.name} subtitle={`Línea de equipos · ${brands}`}>
      <Section title={`Personas del CRM (${people.length})`}>
        {people.length === 0 ? (
          <p className="text-xs text-ink-faint">Ninguna persona registrada en el CRM tiene evidencia en esta línea.</p>
        ) : (
          <PersonList persons={people} label={label} />
        )}
      </Section>
      <Section title={`Solo dirección — evidencia histórica (${addressOnly.length})`}>
        <p className="mb-2 text-[11px] text-ink-muted">
          Direcciones que aparecen en la evidencia (destinatarios de cotizaciones enviadas). No son personas del CRM hasta
          que un operador las registre.
        </p>
        {addressOnly.length === 0 ? (
          <p className="text-xs text-ink-faint">Sin información</p>
        ) : (
          <PersonList persons={addressOnly} label={label} />
        )}
      </Section>
      <Section title={`Instituciones (${institutions.length})`}>
        {institutions.length === 0 ? (
          <p className="text-xs text-ink-faint">Sin información</p>
        ) : (
          <InstitutionList institutions={institutions} label={label} />
        )}
      </Section>
    </Drawer>
  );
}

function PersonList({ persons, label }: { persons: InterestPerson[]; label: (i: AudienceInterest) => string }) {
  return (
    <ul className="space-y-3" data-testid="line-person-list">
      {persons.map((p) => (
        <li key={p.key}>
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span className="text-[13px] font-semibold text-ink">{p.display_name ?? p.address}</span>
            {p.display_name ? <span className="text-[11px] text-ink-muted">{p.address}</span> : null}
            {p.link === "crm_person" ? (
              <Badge tone="good">Persona del CRM</Badge>
            ) : p.contact_point_id ? (
              <Badge tone="neutral" title="crm.contact_point sin persona vinculada">Dirección en el CRM, sin persona</Badge>
            ) : (
              <Badge tone="warn" title="Solo aparece en la evidencia de Gmail">Solo evidencia</Badge>
            )}
          </div>
          <EvidenceList interests={p.interests} label={label} />
        </li>
      ))}
    </ul>
  );
}

function InstitutionList({ institutions, label }: { institutions: InterestInstitution[]; label: (i: AudienceInterest) => string }) {
  return (
    <ul className="space-y-3">
      {institutions.map((i) => (
        <li key={i.organization_id}>
          <p className="mb-1 text-[13px] font-semibold text-ink">{i.name ?? "Institución sin nombre"}</p>
          <EvidenceList interests={i.interests} label={label} />
        </li>
      ))}
    </ul>
  );
}
