import type { ReactNode } from "react";
import { Badge, Drawer, ExternalLink, ResourceGate, Skeleton, fmtDate, fmtDateTime } from "../ui";
import { useResource } from "../useResource";
import { displayName } from "./ProspectCard";
import { ProspectLinkRow } from "./ProspectLinks";
import { ProspectLogo } from "./ProspectLogo";
import { fetchProspect, type ProspectDetailData } from "./prospectApi";

const VERDICT_LABEL: Record<string, string> = {
  contactar: "Contactado", mas_tarde: "Más tarde", descartar: "Descartado", reabrir: "Reabierto",
};

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title}>
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{title}</h3>
      <div className="space-y-1 text-[13px] leading-5 text-ink">{children}</div>
    </section>
  );
}

function Line({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <p>
      <span className="text-ink-muted">{label}: </span>
      {value}
    </p>
  );
}

/** Everything known about one prospect, grouped, with the source of every fact (`#/crm/prospectos/<id>`). */
function DetailBody({ p }: { p: ProspectDetailData }) {
  return (
    <>
      <div className="flex items-center gap-3">
        <ProspectLogo id={p.id} name={p.organization_name} hasLogo={p.has_logo} size="lg" />
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-ink">{p.organization_name}</p>
          <ProspectLinkRow links={p.links} />
        </div>
      </div>
      <Block title="Persona">
        <Line label="Nombre" value={p.person_name ?? "Sin nombre (laboratorio)"} />
        <Line label="Cargo" value={p.role} />
      </Block>
      <Block title="Institución y laboratorio">
        <Line label="Institución" value={p.organization_name} />
        <Line label="Unidad" value={p.unit} />
      </Block>
      <Block title="Proyecto y fondos">
        <Line label="Fondo" value={p.fund_label} />
        <Line label="Proyecto" value={p.project_title} />
        <Line label="Monto" value={p.amount_label} />
        <Line label="Adjudicado" value={p.awarded_on ? fmtDate(p.awarded_on) : null} />
        <p className="pt-1">
          <span className="text-ink-muted">Por qué ahora: </span>
          {p.why_now}
        </p>
      </Block>
      <Block title="Cómo contactar">
        {p.email ? (
          <p>
            <a href={`mailto:${p.email}`} className="font-medium underline underline-offset-2">{p.email}</a>
            {p.email_source_url ? (
              <span className="ml-2 text-xs">
                <ExternalLink href={p.email_source_url}>{p.email_source_year ? `fuente: paper ${p.email_source_year}` : "fuente"}</ExternalLink>
              </span>
            ) : null}
          </p>
        ) : (
          <p><Badge tone="neutral">Sin correo — llamar</Badge></p>
        )}
        <Line label="Teléfono" value={p.phone} />
      </Block>
      <Block title="Equipos sugeridos">
        {p.equipment.length > 0 ? <p>{p.equipment.join(" · ")}</p> : <p className="text-ink-muted">Sin sugerencia</p>}
      </Block>
      <Block title="Por qué es nuevo">
        <ul className="list-disc space-y-0.5 pl-4">
          {p.reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
      </Block>
      <Block title="Fuentes">
        <ul className="space-y-1">
          {p.sources.map(([fact, url]) => (
            <li key={`${fact}-${url}`} className="break-all">
              <span className="text-ink-muted">{fact}: </span>
              <ExternalLink href={url}>{url.replace(/^https?:\/\//, "")}</ExternalLink>
            </li>
          ))}
        </ul>
      </Block>
      <Block title="Historial">
        {p.history.length === 0 ? (
          <p className="text-ink-muted">Sin decisiones todavía.</p>
        ) : (
          <ul className="space-y-1">
            {p.history.map((h) => (
              <li key={h.reviewed_at}>
                <span className="font-medium">{VERDICT_LABEL[h.verdict] ?? h.verdict}</span>
                <span className="text-ink-muted"> · {fmtDateTime(h.reviewed_at)}{h.reviewed_by ? ` · ${h.reviewed_by}` : ""}</span>
                {h.remind_on ? <span className="text-ink-muted"> · vuelve el {fmtDate(h.remind_on)}</span> : null}
                {h.reason ? <span className="block text-xs text-ink-muted">{h.reason}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </Block>
    </>
  );
}

export function ProspectDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [state, reload] = useResource(() => fetchProspect(id), [id]);
  const title = state.kind === "ready" ? displayName(state.data) : "Prospecto";
  return (
    <Drawer open onClose={onClose} title={title} subtitle={state.kind === "ready" ? state.data.fund_label : undefined}>
      <ResourceGate state={state} reload={reload} skeleton={<Skeleton rows={6} />}>
        {(p) => <DetailBody p={p} />}
      </ResourceGate>
    </Drawer>
  );
}
