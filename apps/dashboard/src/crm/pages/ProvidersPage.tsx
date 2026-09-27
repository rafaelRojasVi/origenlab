import { useMemo, useState } from "react";
import { fetchProviders } from "../crmApi";
import type { SupplierDirectoryEntry } from "../crmTypes";
import {
  Badge,
  DisabledAction,
  EmptyState,
  ExternalLink,
  PageHeader,
  Panel,
  ResourceGate,
  SearchInput,
  Skeleton,
  StatLine,
  WRITE_DISABLED_REASON,
  initials,
} from "../ui";
import { useResource } from "../useResource";

const ROLE_LABEL: Record<string, string> = { supplier: "Proveedor", manufacturer: "Fabricante" };

export function ProvidersPage() {
  const [state, reload] = useResource(fetchProviders);
  const [q, setQ] = useState("");
  return (
    <div className="space-y-4">
      <PageHeader
        title="Proveedores"
        subtitle="Las seis marcas que OrigenLab representa, los proveedores registrados en casos y, aparte, los candidatos detectados por máquina que nadie ha revisado."
      />
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} cards />}>
        {(data) => <Body data={data} q={q} setQ={setQ} />}
      </ResourceGate>
    </div>
  );
}

function Body({
  data,
  q,
  setQ,
}: {
  data: Awaited<ReturnType<typeof fetchProviders>>;
  q: string;
  setQ: (v: string) => void;
}) {
  const directory = useMemo(() => data.directory ?? [], [data.directory]);
  const onCases = useMemo(() => {
    // An organization already shown on a directory brand is not listed twice.
    const onDirectory = new Set(directory.flatMap((d) => d.crm_organizations.map((o) => o.organization_id)));
    const m = new Map<string, { id: string; name: string; confirmation: string; roles: string[]; cases: number }>();
    for (const r of data.on_cases) {
      if (onDirectory.has(r.organization_id)) continue;
      const e = m.get(r.organization_id) ?? { id: r.organization_id, name: r.name, confirmation: r.confirmation, roles: [], cases: 0 };
      e.roles.push(r.role);
      e.cases = Math.max(e.cases, r.cases);
      m.set(r.organization_id, e);
    }
    return [...m.values()];
  }, [data.on_cases, directory]);
  const needle = q.trim().toLowerCase();
  const candidates = data.candidates.filter(
    (c) => !needle || c.domain.includes(needle) || (c.trade_name ?? "").toLowerCase().includes(needle),
  );
  const unresolved = data.candidates.filter((c) => c.resolution === "unresolved").length;
  return (
    <>
      <StatLine
        items={[
          { label: "Directorio", value: directory.length },
          { label: "Otros en casos", value: onCases.length },
          { label: "Candidatos", value: data.candidates.length },
          { label: "Sin revisar", value: unresolved, tone: unresolved ? "warn" : undefined },
        ]}
      />
      <section data-testid="supplier-directory">
        <div className="mb-2 flex flex-wrap items-baseline gap-2">
          <h2 className="text-[13px] font-semibold text-ink">Directorio de proveedores</h2>
          <span className="text-[11px] text-ink-faint">
            Curado: las marcas del catálogo publicado en origenlab.cl, una por línea de equipos
          </span>
        </div>
        {directory.length === 0 ? (
          <EmptyState title="Directorio no disponible">Esta API no entrega el directorio de marcas.</EmptyState>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {directory.map((d) => (
              <DirectoryCard key={d.brand_id} entry={d} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-[13px] font-semibold text-ink">Otros registrados en casos</h2>
        {onCases.length === 0 ? (
          <EmptyState title="Ningún otro proveedor registrado en un caso" />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {onCases.map((p) => (
              <article key={p.id} className="flex gap-3 rounded-md border border-line bg-canvas-raised p-3">
                <span
                  aria-hidden="true"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-canvas-sunken text-xs font-semibold text-ink-muted ring-1 ring-line"
                >
                  {initials(p.name)}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-semibold text-ink">{p.name}</p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {p.roles.map((r) => (
                      <Badge key={r} glyph={false}>
                        {ROLE_LABEL[r] ?? r}
                      </Badge>
                    ))}
                    <Badge tone={p.confirmation === "confirmed" ? "good" : "warn"}>
                      {p.confirmation === "confirmed" ? "Confirmado" : "Propuesto"}
                    </Badge>
                  </div>
                  <p className="mt-1.5 text-[11px] text-ink-muted">
                    {p.cases} caso{p.cases === 1 ? "" : "s"}
                  </p>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <details className="group rounded-md border border-line bg-canvas-raised" data-testid="supplier-candidates">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-[13px] font-semibold text-ink">
          <span aria-hidden="true" className="text-ink-faint transition-transform group-open:rotate-90">›</span>
          Candidatos por revisar
          <Badge tone={unresolved ? "warn" : "neutral"} glyph={false}>
            {data.candidates.length}
          </Badge>
          <span className="ml-auto text-[11px] font-normal text-ink-faint">Detectados por máquina · nunca se promueven solos</span>
        </summary>
        <div className="border-t border-line p-2">
          <Panel
            title="Candidatos"
            note="Detectados por dominio en el manifiesto de migración · sin revisar no significa proveedor"
            aside={<SearchInput value={q} onChange={setQ} label="Buscar candidatos" placeholder="Marca o dominio…" />}
          >
            {candidates.length === 0 ? (
              <div className="p-3">
                <EmptyState title="Ningún candidato coincide" />
              </div>
            ) : (
              <ul className="grid grid-cols-1 divide-y divide-line sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-3">
                {candidates.map((c) => (
                  <li key={c.domain} className="flex items-center gap-2 border-line px-3 py-2 sm:border-b">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-ink">{c.trade_name ?? c.domain}</p>
                      <p className="truncate text-[11px] text-ink-faint">{c.domain}</p>
                    </div>
                    <Badge tone={c.resolution === "unresolved" ? "warn" : "good"}>
                      {c.resolution === "unresolved" ? "Sin revisar" : c.resolution}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
            <div className="border-t border-line px-3 py-2.5">
              <DisabledAction id="providers-review-disabled" reason={WRITE_DISABLED_REASON}>
                Confirmar como proveedor
              </DisabledAction>
            </div>
          </Panel>
        </div>
      </details>
    </>
  );
}

function DirectoryCard({ entry }: { entry: SupplierDirectoryEntry }) {
  const reviewed = entry.candidate_hints.filter((h) => h.resolution !== "unresolved").length;
  return (
    <article className="flex gap-3 rounded-md border border-line bg-canvas-raised p-3" data-testid="directory-card">
      <span
        aria-hidden="true"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-xs font-semibold text-white"
        style={{ background: entry.family.color ?? undefined }}
      >
        {initials(entry.name)}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-semibold text-ink">{entry.name}</p>
        <p className="truncate text-[11px] text-ink-muted">{entry.family.name}</p>
        <div className="mt-1 flex flex-wrap gap-1">
          <Badge tone="good" glyph={false}>
            Directorio
          </Badge>
          <Badge glyph={false}>
            {entry.model_count} modelo{entry.model_count === 1 ? "" : "s"} en catálogo
          </Badge>
        </div>
        <p className="mt-1.5 text-[11px] text-ink-muted">
          {entry.crm_organizations.length === 0
            ? "Sin organización del CRM registrada en casos"
            : entry.crm_organizations
                .map((o) => `${o.name} · ${o.cases} caso${o.cases === 1 ? "" : "s"}${o.confirmation === "confirmed" ? "" : " (propuesta)"}`)
                .join(" · ")}
        </p>
        {entry.candidate_hints.length > 0 ? (
          <p className="mt-0.5 truncate text-[11px] text-ink-faint" title="Candidatos detectados cuyo dominio o nombre cita la marca. No se promueven.">
            Candidatos que la citan: {entry.candidate_hints.map((h) => h.domain).join(", ")}
            {reviewed < entry.candidate_hints.length ? " · sin revisar" : ""}
          </p>
        ) : null}
        {entry.page_url ? (
          <p className="mt-1 text-[11px]">
            <ExternalLink href={entry.page_url}>Ficha en origenlab.cl</ExternalLink>
          </p>
        ) : null}
      </div>
    </article>
  );
}
