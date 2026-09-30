import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchV2Organizations } from "../../api/v2Client";
import type { V2Organization, V2OrganizationFilter, V2OrganizationSegment } from "../../api/v2Types";
import { fetchPipeline } from "../crmApi";
import type { AudienceInterest } from "../marketing/marketingTypes";
import {
  EquipmentInterestsBlock,
  EquipmentLineDrawer,
  EquipmentLineStrip,
  useEquipmentInterests,
  type InterestData,
} from "../interests/EquipmentInterests";
import type { ResourceState } from "../useResource";
import type { OpportunityCardData } from "../crmTypes";
import type { CrmSection } from "../crmRoute";
import { stageDisplay } from "../stage";
import {
  Badge,
  Drawer,
  EmptyState,
  LocalDriveLink,
  PageHeader,
  ResourceGate,
  SearchInput,
  Section,
  Segmented,
  Skeleton,
  StatLine,
  fmtDate,
  fmtInt,
  initials,
} from "../ui";
import { useResource } from "../useResource";
import { useMayAuthorCrm, useIsAdmin } from "../authoring/authoring";
import { NewOrganizationForm } from "../authoring/NewOrganizationForm";
import { OrgAuthoringSection } from "../authoring/OrgAuthoringSection";

type Scope = "cases" | "all";

export function OrganizationsPage({ navigate }: { navigate: (s: CrmSection, id?: string) => void }) {
  const [segment, setSegment] = useState<V2OrganizationSegment>("customers");
  const [scope, setScope] = useState<Scope>("cases");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(
    () =>
      fetchV2Organizations({
        q: debounced || undefined,
        segment,
        has: scope === "cases" ? (["cases"] as V2OrganizationFilter[]) : undefined,
        limit: 60,
      }),
    [debounced, segment, scope],
  );
  const [state, reload] = useResource(load, [load]);
  const [pipeline] = useResource(fetchPipeline);
  const cardsByOrg = useMemo(() => {
    const m = new Map<string, OpportunityCardData[]>();
    if (pipeline.kind === "ready") {
      for (const c of pipeline.data.items) {
        const id = c.organization?.organization_id;
        if (!id) continue;
        m.set(id, [...(m.get(id) ?? []), c]);
      }
    }
    return m;
  }, [pipeline]);
  const [openId, setOpenId] = useState<string | null>(null);
  const interests = useEquipmentInterests();
  const mayAuthor = useMayAuthorCrm();
  const admin = useIsAdmin();
  const [showNewOrg, setShowNewOrg] = useState(false);
  const [line, setLine] = useState<string | null>(null);
  const interestsOf = (id: string) => (interests.kind === "ready" ? interests.data.byOrganization.get(id) : undefined);
  const openLine = (familyId: string) => {
    setOpenId(null);
    setLine(familyId);
  };

  return (
    <div className="space-y-3">
      <PageHeader
        title="Organizaciones"
        subtitle="Instituciones del CRM, ordenadas por conexiones durables. Las propuestas por máquina se marcan como tales."
        actions={
          mayAuthor ? (
            <button
              type="button"
              onClick={() => setShowNewOrg(true)}
              className="h-8 rounded-md bg-ink px-3 text-xs font-medium text-white hover:bg-black"
            >
              Nueva organización
            </button>
          ) : null
        }
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Segmented
          label="Segmento"
          value={segment}
          onChange={setSegment}
          options={[
            { value: "customers", label: "Clientes" },
            { value: "suppliers", label: "Proveedores" },
            { value: "others", label: "Otras" },
          ]}
        />
        <Segmented
          label="Alcance"
          value={scope}
          onChange={setScope}
          options={[
            { value: "cases", label: "Con casos" },
            { value: "all", label: "Todas" },
          ]}
        />
        <SearchInput value={q} onChange={setQ} label="Buscar organizaciones" placeholder="Nombre de la institución…" />
      </div>
      <EquipmentLineStrip state={interests} onSelectLine={openLine} />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} cards />}>
        {(page) => {
          const open = page.items.find((o) => o.organization_id === openId) ?? null;
          return (
            <>
              <StatLine
                items={[
                  { label: "Resultados", value: fmtInt(page.total) },
                  ...(page.facets
                    ? [
                        { label: "Clientes", value: fmtInt(page.facets.customers) },
                        { label: "Proveedores", value: fmtInt(page.facets.suppliers) },
                        { label: "Todas", value: fmtInt(page.facets.all) },
                      ]
                    : []),
                ]}
              />
              {page.items.length === 0 ? (
                <EmptyState title="Ninguna organización coincide">
                  {scope === "cases" ? "Prueba con «Todas» para incluir las que aún no tienen casos." : "Cambia la búsqueda o el segmento."}
                </EmptyState>
              ) : (
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
                  {page.items.map((o, i) => (
                    <OrgCard
                      key={o.organization_id}
                      org={o}
                      index={i}
                      cases={cardsByOrg.get(o.organization_id) ?? []}
                      onOpen={setOpenId}
                      interestState={interests}
                      interests={interestsOf(o.organization_id)}
                      onSelectLine={openLine}
                    />
                  ))}
                </div>
              )}
              {page.total > page.items.length ? (
                <p className="text-center text-[11px] text-ink-faint">
                  Mostrando {page.items.length} de {fmtInt(page.total)} — afina la búsqueda para ver otras.
                </p>
              ) : null}
              <OrgDrawer
                org={open}
                cases={open ? cardsByOrg.get(open.organization_id) ?? [] : []}
                onClose={() => setOpenId(null)}
                navigate={navigate}
                interestState={interests}
                interests={open ? interestsOf(open.organization_id) : undefined}
                onSelectLine={openLine}
                mayAuthor={mayAuthor}
                admin={admin}
              />
            </>
          );
        }}
      </ResourceGate>
      <EquipmentLineDrawer state={interests} familyId={line} onClose={() => setLine(null)} />
      {showNewOrg ? (
        <NewOrganizationForm
          onDone={() => { setShowNewOrg(false); reload(); }}
          onCancel={() => setShowNewOrg(false)}
        />
      ) : null}
    </div>
  );
}

interface InterestProps {
  interestState: ResourceState<InterestData>;
  interests: AudienceInterest[] | undefined;
  onSelectLine: (familyId: string) => void;
}

function OrgCard({
  org,
  index,
  cases,
  onOpen,
  interestState,
  interests,
  onSelectLine,
}: {
  org: V2Organization;
  index: number;
  cases: OpportunityCardData[];
  onOpen: (id: string) => void;
} & InterestProps) {
  const quotes = cases.reduce((n, c) => n + c.quotes.length, 0);
  const lastSent = cases.map((c) => c.latest_revision?.sent_at ?? "").sort().at(-1) || null;
  return (
    <article
      style={{ animationDelay: `${Math.min(index, 12) * 18}ms` }}
      className="crm-card-in flex gap-3 rounded-md border border-line bg-canvas-raised p-3 shadow-[0_1px_1px_rgb(24_24_27/0.03)] transition-[box-shadow,border-color] hover:border-line-strong hover:shadow-sm"
    >
      <span
        aria-hidden="true"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-brand-50 text-xs font-semibold text-brand-700 ring-1 ring-brand-600/15"
      >
        {initials(org.name)}
      </span>
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => onOpen(org.organization_id)}
          aria-haspopup="dialog"
          className="block w-full truncate text-left text-[13px] font-semibold leading-5 text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
        >
          {org.name}
        </button>
        <div className="mt-0.5 flex flex-wrap items-center gap-1">
          {org.confirmation === "confirmed" ? (
            <Badge tone="good">Confirmada</Badge>
          ) : (
            <Badge tone="warn" title="Nombre propuesto por máquina desde la migración">
              Propuesta
            </Badge>
          )}
          {org.kind !== "unknown" ? <Badge glyph={false}>{org.kind === "institution" ? "Institución" : org.kind}</Badge> : null}
        </div>
        <dl className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
          <div>
            <dt className="text-ink-faint">Casos</dt>
            <dd className="font-semibold tabular-nums text-ink">{org.case_count}</dd>
          </div>
          <div>
            <dt className="text-ink-faint">Cotizaciones</dt>
            <dd className="font-semibold tabular-nums text-ink">{Math.max(org.quote_count, quotes)}</dd>
          </div>
          <div>
            <dt className="text-ink-faint">Última enviada</dt>
            <dd className="truncate font-medium text-ink">{fmtDate(lastSent)}</dd>
          </div>
        </dl>
        <p className="mt-1.5 truncate text-[11px] text-ink-faint">
          {org.contact_point_count > 0 ? `${org.contact_point_count} direcciones` : "Sin direcciones vinculadas"} ·{" "}
          {org.confirmed_people_count > 0 ? `${org.confirmed_people_count} personas` : "sin personas registradas"}
        </p>
        <div className="mt-2 border-t border-line/70 pt-1.5">
          <EquipmentInterestsBlock state={interestState} interests={interests} onSelectLine={onSelectLine} compact />
        </div>
      </div>
    </article>
  );
}

function OrgDrawer({
  org,
  cases,
  onClose,
  navigate,
  interestState,
  interests,
  onSelectLine,
  mayAuthor = false,
  admin = false,
}: {
  org: V2Organization | null;
  cases: OpportunityCardData[];
  onClose: () => void;
  navigate: (s: CrmSection, id?: string) => void;
  mayAuthor?: boolean;
  admin?: boolean;
} & InterestProps) {
  if (!org) return null;
  const roles: [string, number][] = [
    ["Solicitante", org.cases_as_requesting_institution],
    ["Usuario final", org.cases_as_end_user_institution],
    ["Comprador", org.cases_as_purchasing_agent],
    ["Financista", org.cases_as_funder],
    ["Proveedor", org.cases_as_supplier],
    ["Fabricante", org.cases_as_manufacturer],
    ["Mencionada", org.cases_as_mentioned],
  ];
  return (
    <Drawer open onClose={onClose} title={org.name} subtitle={org.confirmation === "confirmed" ? "Confirmada por un operador" : "Propuesta por máquina — sin confirmar"}>
      <Section title="Rol en los casos">
        <div className="flex flex-wrap gap-1.5">
          {roles.filter(([, n]) => n > 0).length === 0 ? (
            <span className="text-xs text-ink-faint">Sin casos</span>
          ) : (
            roles
              .filter(([, n]) => n > 0)
              .map(([label, n]) => (
                <Badge key={label} glyph={false}>
                  {label} · {n}
                </Badge>
              ))
          )}
        </div>
      </Section>
      <Section title={`Oportunidades (${cases.length})`}>
        {cases.length === 0 ? (
          <p className="text-xs text-ink-faint">Ninguna oportunidad del CRM tiene esta institución como solicitante.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {cases.map((c) => (
              <li key={c.opportunity_id}>
                <button
                  type="button"
                  onClick={() => navigate("oportunidades", c.opportunity_id)}
                  className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-canvas-sunken/50"
                >
                  <span className="w-28 shrink-0 truncate text-xs font-semibold tabular-nums">{c.quote_numbers.join(", ") || "—"}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-muted">{stageDisplay(c).label}</span>
                  <span className="text-[11px] text-ink-faint">{fmtDate(c.latest_revision?.sent_at)}</span>
                </button>
                {c.drive_folder ? (
                  <p className="px-2.5 pb-2 text-[11px]">
                    <LocalDriveLink href={c.drive_folder.url}>Carpeta en Drive</LocalDriveLink>
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Intereses observados">
        <EquipmentInterestsBlock state={interestState} interests={interests} onSelectLine={onSelectLine} heading={false} />
      </Section>
      <Section title="Contacto">
        <p className="text-xs text-ink-muted">
          {org.confirmed_people_count > 0
            ? `${org.confirmed_people_count} personas registradas.`
            : "Sin personas registradas: el CRM aún no vincula direcciones ni personas a instituciones."}
        </p>
      </Section>
      {mayAuthor || admin ? (
        <OrgAuthoringSection organizationId={org.organization_id} mayAuthor={mayAuthor} admin={admin} />
      ) : null}
      <p className="font-mono text-[10px] text-ink-faint">organization {org.organization_id}</p>
    </Drawer>
  );
}
