import { useMemo, useState } from "react";
import { crmHash } from "../crmRoute";
import { Badge, Drawer, EmptyState, Panel, ResourceGate, SearchInput, Section, Segmented, Skeleton, StatLine, fmtDate, fmtInt, type Tone } from "../ui";
import { useResource } from "../useResource";
import { destinationsOf, recipientList, selectAllEligible, toggle, type SelectableDestination } from "./audienceSelection";
import { fetchAudience } from "./marketingApi";
import type {
  AudienceInstitution,
  AudienceInterest,
  AudiencePerson,
  AudienceQuery,
  AudienceResponse,
  Eligibility,
  EquipmentTaxonomy,
  InterestBasis,
} from "./marketingTypes";

const BASIS_OPTIONS: { value: InterestBasis; label: string; tone: Tone }[] = [
  { value: "purchased", label: "Compró", tone: "good" },
  { value: "requested_quotation", label: "Pidió cotización", tone: "brand" },
  { value: "requested_information", label: "Pidió información", tone: "info" },
  { value: "inferred_relevance", label: "Relevancia inferida", tone: "neutral" },
];
const BASIS_TONE = Object.fromEntries(BASIS_OPTIONS.map((b) => [b.value, b.tone])) as Record<InterestBasis, Tone>;

type Recorded = "all" | "crm" | "evidence";
type Open = { kind: "person"; row: AudiencePerson } | { kind: "institution"; row: AudienceInstitution } | null;

export function AudienceBuilder({ taxonomy }: { taxonomy: EquipmentTaxonomy }) {
  const [familyId, setFamilyId] = useState("");
  const [brandId, setBrandId] = useState("");
  const [modelId, setModelId] = useState("");
  const [bases, setBases] = useState<InterestBasis[]>([]);
  const [recorded, setRecorded] = useState<Recorded>("all");
  const [q, setQ] = useState("");
  const [organizationId, setOrganizationId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<Open>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const query: AudienceQuery = {
    family_id: familyId, brand_id: brandId, model_id: modelId, basis: bases,
    recorded: recorded === "all" ? undefined : recorded, q, organization_id: organizationId ?? undefined,
  };
  const [state, reload] = useResource(() => fetchAudience(query), [familyId, brandId, modelId, bases.join(), recorded, q, organizationId]);

  const brands = taxonomy.brands.filter((b) => !familyId || b.family_id === familyId);
  const models = taxonomy.models.filter((m) => (!brandId || m.brand_id === brandId) && (!familyId || m.family_id === familyId));
  const label = useLabels(taxonomy);

  const selectCls = "h-7 rounded-md border border-line bg-canvas-raised px-2 text-xs text-ink focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600";

  return (
    <div className="space-y-4" data-testid="audience-builder">
      <div className="flex flex-wrap items-end gap-2">
        <select aria-label="Familia de equipos" className={selectCls} value={familyId} onChange={(e) => { setFamilyId(e.target.value); setBrandId(""); setModelId(""); }}>
          <option value="">Todas las familias</option>
          {taxonomy.families.map((f) => (
            <option key={f.id} value={f.id}>{f.name}</option>
          ))}
        </select>
        <select aria-label="Marca" className={selectCls} value={brandId} onChange={(e) => { setBrandId(e.target.value); setModelId(""); }}>
          <option value="">Todas las marcas</option>
          {brands.map((b) => (
            <option key={b.id} value={b.id}>{b.name}</option>
          ))}
        </select>
        <select aria-label="Modelo" className={selectCls} value={modelId} onChange={(e) => setModelId(e.target.value)}>
          <option value="">Todos los modelos</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.name}{m.kind === "serie" ? " (serie)" : ""}</option>
          ))}
        </select>
        <SearchInput value={q} onChange={setQ} placeholder="Institución o persona…" label="Buscar institución o persona" />
        <Segmented
          label="Origen del interés"
          value={recorded}
          onChange={setRecorded}
          options={[
            { value: "all", label: "Todo" },
            { value: "crm", label: "Registrado en CRM" },
            { value: "evidence", label: "Sólo en evidencia" },
          ]}
        />
      </div>
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Tipo de evidencia">
        {BASIS_OPTIONS.map((b) => {
          const on = bases.includes(b.value);
          return (
            <button
              key={b.value}
              type="button"
              aria-pressed={on}
              onClick={() => setBases((xs) => (on ? xs.filter((x) => x !== b.value) : [...xs, b.value]))}
              className={`h-6 rounded-full border px-2.5 text-[11px] font-medium ${on ? "border-brand-600 bg-brand-50 text-brand-700" : "border-line bg-canvas-raised text-ink-muted hover:text-ink"}`}
            >
              {b.label}
            </button>
          );
        })}
        {organizationId ? (
          <button type="button" onClick={() => setOrganizationId(null)} className="h-6 rounded-full border border-brand-600 bg-brand-50 px-2.5 text-[11px] font-medium text-brand-700">
            Institución filtrada ✕
          </button>
        ) : null}
      </div>

      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
        {(data) => {
          const all = destinationsOf(data.persons, data.institutions);
          const list = recipientList(selected, all);
          return (
            <>
              <Coverage data={data} taxonomy={taxonomy} />
              <SelectionBar
                data={data}
                selectedCount={list.length}
                onSelectAll={() => {
                  const r = selectAllEligible(selected, data.persons.map((p) => p.key), all);
                  setSelected(r.selected);
                  setNotice(r.skipped ? `${r.skipped} persona(s) no seleccionable(s): no cumplen las condiciones de envío.` : null);
                }}
                onClear={() => { setSelected(new Set()); setNotice(null); }}
                notice={notice}
              />
              <div className="grid gap-4 2xl:grid-cols-2">
                <Panel title="Instituciones" note="interés a nivel de institución (caso solicitante)" bodyClassName="divide-y divide-line">
                  {data.institutions.length === 0 ? (
                    <p className="px-3 py-4 text-xs text-ink-muted">Sin información para este filtro.</p>
                  ) : (
                    data.institutions.map((inst) => (
                      <CandidateRow
                        key={inst.organization_id}
                        title={inst.name ?? "Institución sin nombre"}
                        subtitle={`${inst.destinations.length} destino(s) conocido(s)`}
                        interests={inst.interests}
                        others={inst.other_interest_count}
                        label={label}
                        onOpen={() => setOpen({ kind: "institution", row: inst })}
                      />
                    ))
                  )}
                </Panel>
                <Panel title="Personas" note="interés a nivel de contacto (destinatario o participante)" bodyClassName="divide-y divide-line">
                  {data.persons.length === 0 ? (
                    <p className="px-3 py-4 text-xs text-ink-muted">Sin información para este filtro.</p>
                  ) : (
                    data.persons.map((p) => (
                      <CandidateRow
                        key={p.key}
                        title={p.display_name ?? p.address}
                        subtitle={p.display_name ? p.address : p.contact_point_id ? "punto de contacto sin persona" : "sin punto de contacto en el CRM"}
                        interests={p.interests}
                        others={p.other_interest_count}
                        label={label}
                        onOpen={() => setOpen({ kind: "person", row: p })}
                        select={{
                          eligibility: p.eligibility,
                          checked: selected.has(p.key),
                          onToggle: () => setSelected((s) => toggle(s, all.get(p.key))),
                        }}
                      />
                    ))
                  )}
                </Panel>
              </div>
              <CandidateDrawer
                open={open}
                onClose={() => setOpen(null)}
                label={label}
                selected={selected}
                onToggle={(d) => setSelected((s) => toggle(s, d))}
                onFilterInstitution={(id) => { setOrganizationId(id); setOpen(null); }}
              />
            </>
          );
        }}
      </ResourceGate>
    </div>
  );
}

function useLabels(t: EquipmentTaxonomy) {
  return useMemo(() => {
    const brands = new Map(t.brands.map((b) => [b.id, b.name]));
    const models = new Map(t.models.map((m) => [m.id, m.name]));
    return (i: AudienceInterest) => (i.model_id ? `${brands.get(i.brand_id)} ${models.get(i.model_id)}` : `${brands.get(i.brand_id)} (marca)`);
  }, [t]);
}

function Coverage({ data, taxonomy }: { data: AudienceResponse; taxonomy: EquipmentTaxonomy }) {
  const c = data.coverage;
  const reviewTotal = Object.values(c.review_queue).reduce((n, x) => n + x, 0);
  return (
    <Panel title="Cobertura del interés por equipo" note="qué está vinculado en el CRM y qué existe sólo en evidencia" bodyClassName="p-3 space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[28rem] text-xs" data-testid="coverage-table">
          <thead>
            <tr className="text-left text-[11px] text-ink-faint">
              <th className="py-1 pr-3 font-medium">Marca</th>
              <th className="py-1 pr-3 font-medium">Registrado en CRM</th>
              <th className="py-1 pr-3 font-medium">Sólo en correos o cotizaciones</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {taxonomy.brands.map((b) => {
              const l = c.brand_linkage[b.id] ?? { crm: 0, evidence_only: 0 };
              const none = l.crm === 0 && l.evidence_only === 0;
              return (
                <tr key={b.id}>
                  <td className="py-1.5 pr-3 font-medium text-ink">{b.name}</td>
                  <td className="py-1.5 pr-3 tabular-nums">{none ? <span className="text-ink-faint">Sin información</span> : fmtInt(l.crm)}</td>
                  <td className="py-1.5 pr-3 tabular-nums">{none ? <span className="text-ink-faint">Sin información</span> : fmtInt(l.evidence_only)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ul className="space-y-1 text-[11px] leading-4 text-ink-muted">
        <li>
          Intereses registrados en casos (<code>crm.opportunity_interest</code>): <b className="text-ink">{fmtInt(c.crm_interest_rows)}</b>
          {c.crm_interest_rows === 0 ? " — el CRM aún no tiene intereses de equipo registrados; lo que se ve abajo proviene de evidencia." : ` (${c.crm_interests_unmatched} sin coincidencia con el catálogo).`}
        </li>
        <li>
          Correos de cotización enviados que nombran un equipo del catálogo: <b className="text-ink">{fmtInt(c.quotation_evidence_with_mentions)}</b> de{" "}
          {fmtInt(c.quotation_evidence_records)}; títulos de caso que nombran uno: <b className="text-ink">{fmtInt(c.case_titles_with_mentions)}</b>.
        </li>
        <li>
          Nuevas consultas suman intereses al registrarlos en su caso («Registrar interés») o al importar su cotización; esta vista los lee
          sin pasos adicionales. Las identidades ambiguas quedan en{" "}
          <a className="font-medium text-brand-700 underline" href={crmHash("revision")}>Revisión</a>: {fmtInt(reviewTotal)} pendiente(s), y{" "}
          {fmtInt(c.recipients_without_contact_point)} destinatario(s) sin punto de contacto.
        </li>
        <li>Sin evidencia significa «Sin información», no bajo interés. No se calcula ningún puntaje.</li>
      </ul>
    </Panel>
  );
}

function SelectionBar({
  data,
  selectedCount,
  onSelectAll,
  onClear,
  notice,
}: {
  data: AudienceResponse;
  selectedCount: number;
  onSelectAll: () => void;
  onClear: () => void;
  notice: string | null;
}) {
  const s = data.sending;
  return (
    <div className="space-y-1.5 rounded-lg border border-line bg-canvas-raised px-3 py-2" data-testid="selection-bar">
      <div className="flex flex-wrap items-center gap-3">
        <StatLine
          items={[
            { label: "Instituciones", value: fmtInt(data.institutions.length) },
            { label: "Personas", value: fmtInt(data.persons.length) },
            { label: "Destinos únicos", value: fmtInt(s.unique_destinations) },
            { label: "Enviables", value: fmtInt(s.eligible_unique_destinations), tone: "good" },
            { label: "Seleccionados", value: fmtInt(selectedCount) },
          ]}
        />
        <div className="ml-auto flex gap-2">
          <button type="button" onClick={onSelectAll} className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken">
            Seleccionar personas enviables
          </button>
          <button type="button" onClick={onClear} className="h-7 rounded-md px-2.5 text-xs font-medium text-ink-muted hover:bg-canvas-sunken">
            Limpiar
          </button>
        </div>
      </div>
      {s.excluded_by_reason.length ? (
        <p className="text-[11px] text-ink-muted">
          Excluidos: {s.excluded_by_reason.map((r) => `${r.label} (${r.count})`).join(" · ")}. Relevancia no habilita el envío.
        </p>
      ) : null}
      {notice ? <p className="text-[11px] text-warn">{notice}</p> : null}
      <p className="text-[10px] text-ink-faint">
        La selección existe sólo en esta pestaña: el CRM registra una audiencia al congelarla, paso que aún no existe. Nada se envía desde aquí.
      </p>
    </div>
  );
}

function EligibilityBadge({ e }: { e: Eligibility }) {
  return e.eligible ? (
    <Badge tone="good">Enviable</Badge>
  ) : (
    <Badge tone="bad" title={e.reasons.map((r) => r.label).join(", ")}>
      {e.reasons[0]?.label ?? "Excluido"}
      {e.reasons.length > 1 ? ` +${e.reasons.length - 1}` : ""}
    </Badge>
  );
}

function InterestChip({ i, label }: { i: AudienceInterest; label: (i: AudienceInterest) => string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded border border-line bg-canvas-sunken px-1.5 py-px text-[11px] text-ink">
      <span className="truncate">{label(i)}</span>
      <Badge tone={BASIS_TONE[i.basis]} glyph={false}>{i.basis_label}</Badge>
      {!i.recorded_in_crm ? <span className="text-[9px] font-semibold uppercase tracking-wide text-ink-faint">evidencia</span> : null}
    </span>
  );
}

function CandidateRow({
  title,
  subtitle,
  interests,
  others,
  label,
  onOpen,
  select,
}: {
  title: string;
  subtitle: string;
  interests: AudienceInterest[];
  others: number;
  label: (i: AudienceInterest) => string;
  onOpen: () => void;
  select?: { eligibility: Eligibility; checked: boolean; onToggle: () => void };
}) {
  return (
    <div className="flex items-start gap-2.5 px-3 py-2" data-testid="audience-row">
      {select ? (
        <input
          type="checkbox"
          aria-label={`Seleccionar ${title}`}
          className="mt-1"
          checked={select.checked}
          disabled={!select.eligibility.eligible}
          title={select.eligibility.eligible ? undefined : select.eligibility.reasons.map((r) => r.label).join(", ")}
          onChange={select.onToggle}
        />
      ) : null}
      <div className="min-w-0 flex-1">
        <button type="button" onClick={onOpen} className="max-w-full truncate text-left text-[13px] font-semibold text-ink hover:underline">
          {title}
        </button>
        <p className="truncate text-[11px] text-ink-faint">{subtitle}</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {interests.slice(0, 3).map((i, n) => (
            <InterestChip key={n} i={i} label={label} />
          ))}
          {interests.length > 3 ? <span className="text-[11px] text-ink-faint">+{interests.length - 3}</span> : null}
          {others ? <span className="text-[11px] text-ink-faint">· {others} fuera del filtro</span> : null}
        </div>
      </div>
      {select ? <EligibilityBadge e={select.eligibility} /> : null}
    </div>
  );
}

function EvidenceList({ interests, label }: { interests: AudienceInterest[]; label: (i: AudienceInterest) => string }) {
  return (
    <ul className="divide-y divide-line rounded-md border border-line">
      {interests.map((i, n) => (
        <li key={n} className="space-y-0.5 px-3 py-2 text-xs">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold text-ink">{label(i)}</span>
            <Badge tone={BASIS_TONE[i.basis]} glyph={false}>{i.basis_label}</Badge>
            <span className="ml-auto text-[11px] tabular-nums text-ink-muted">{i.date ? fmtDate(i.date) : "sin fecha"}</span>
          </div>
          <p className="text-[11px] text-ink-muted">
            {i.source.label}
            {i.recorded_in_crm ? (i.confirmation === "confirmed" ? " · confirmado por operador" : i.confirmation === "machine_proposed" ? " · propuesto por la máquina" : "") : " · no registrado como interés en el CRM"}
          </p>
          <p className="text-[11px] text-ink-faint">
            Coincidencia «{i.matched_term}» en: {i.source.detail ?? i.source.case_title ?? "—"}
          </p>
          {i.source.opportunity_id ? (
            <a className="text-[11px] font-medium text-brand-700 underline" href={crmHash("oportunidades", i.source.opportunity_id)}>
              Ver caso{i.source.quote_numbers.length ? ` · ${i.source.quote_numbers.join(", ")}` : ""}
            </a>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function EligibilityDetail({ e }: { e: Eligibility }) {
  return (
    <div className="space-y-1 text-xs">
      <EligibilityBadge e={e} />
      {e.reasons.length > 1 ? <p className="text-ink-muted">{e.reasons.map((r) => r.label).join(" · ")}</p> : null}
      {e.notes.map((n) => (
        <p key={n.code} className="text-[11px] text-ink-faint">{n.label}</p>
      ))}
    </div>
  );
}

function CandidateDrawer({
  open,
  onClose,
  label,
  selected,
  onToggle,
  onFilterInstitution,
}: {
  open: Open;
  onClose: () => void;
  label: (i: AudienceInterest) => string;
  selected: ReadonlySet<string>;
  onToggle: (d: SelectableDestination) => void;
  onFilterInstitution: (organizationId: string) => void;
}) {
  if (!open) return <Drawer open={false} onClose={onClose} title="">{null}</Drawer>;
  if (open.kind === "person") {
    const p = open.row;
    return (
      <Drawer open onClose={onClose} title={p.display_name ?? p.address} subtitle="Contacto · interés a nivel de persona">
        <Section title="Contacto">
          <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-xs">
            <dt className="text-ink-faint">Dirección</dt>
            <dd className="break-all text-ink">{p.address}</dd>
            <dt className="text-ink-faint">Punto de contacto</dt>
            <dd className="text-ink">{p.contact_point_id ? "registrado en el CRM" : "no registrado (identidad sin resolver)"}</dd>
            <dt className="text-ink-faint">Persona</dt>
            <dd className="text-ink">{p.person_id ? p.display_name : "sin persona vinculada"}</dd>
          </dl>
        </Section>
        <Section title="Condiciones de envío">
          <EligibilityDetail e={p.eligibility} />
        </Section>
        <Section title={`Evidencia de interés (${p.interests.length})`}>
          <EvidenceList interests={p.interests} label={label} />
        </Section>
      </Drawer>
    );
  }
  const inst = open.row;
  return (
    <Drawer open onClose={onClose} title={inst.name ?? "Institución"} subtitle="Institución · interés a nivel de institución">
      <button type="button" onClick={() => onFilterInstitution(inst.organization_id)} className="h-7 rounded-md border border-line bg-canvas-raised px-2.5 text-xs font-medium text-ink hover:bg-canvas-sunken">
        Filtrar audiencia por esta institución
      </button>
      <Section title={`Evidencia de interés (${inst.interests.length})`}>
        <EvidenceList interests={inst.interests} label={label} />
      </Section>
      <Section title={`Destinos (${inst.destinations.length})`}>
        {inst.destinations.length === 0 ? (
          <EmptyState title="Sin destinos conocidos">Ningún punto de contacto de esta institución ni destinatario de sus cotizaciones.</EmptyState>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {inst.destinations.map((d) => (
              <li key={d.key} className="flex items-start gap-2 px-3 py-2 text-xs">
                <input
                  type="checkbox"
                  aria-label={`Seleccionar ${d.address}`}
                  className="mt-0.5"
                  checked={selected.has(d.key)}
                  disabled={!d.eligibility.eligible}
                  onChange={() => onToggle(d)}
                />
                <div className="min-w-0 flex-1">
                  <p className="break-all text-ink">{d.address}</p>
                  <p className="text-[11px] text-ink-faint">{d.via === "recipient_of_case_quotation" ? "recibió una cotización del caso" : "punto de contacto de la institución"}</p>
                </div>
                <EligibilityDetail e={d.eligibility} />
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Drawer>
  );
}
