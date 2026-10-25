/**
 * «¿Ya lo tenemos?» answered: Bajas first (it decides whether we may write at all), then the CRM
 * people and institutions, the mail we exchanged and the prospects. Quiet when nothing matched.
 * `compact` is the one-line answer under the correo field of «Editar prospecto».
 */
import { crmHash } from "./crmRoute";
import type { LookupResult } from "./lookup";
import { fmtDate } from "./ui";

function Row({ children }: { children: React.ReactNode }) {
  return <li className="flex min-w-0 items-baseline gap-2 py-1 text-[13px]">{children}</li>;
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{title}</p>
      <ul className="mt-0.5">{children}</ul>
    </div>
  );
}

const LINK = "min-w-0 truncate font-medium text-ink hover:text-accent hover:underline";

function mailLine(r: LookupResult): string | null {
  if (!r.mail) return null;
  const n = r.mail.messages;
  const who = r.mail.addresses ? ` con ${r.mail.addresses} ${r.mail.addresses === 1 ? "dirección" : "direcciones"}` : "";
  return `${n} ${n === 1 ? "correo" : "correos"}${who}${r.mail.last_at ? ` · último ${fmtDate(r.mail.last_at)}` : ""}`;
}

export function LookupResults({
  result,
  compact = false,
  onOpenProspect,
}: {
  result: LookupResult;
  compact?: boolean;
  onOpenProspect?: (id: string) => void;
}) {
  const blocks = result.controls.filter((c) => c.kind === "block");
  const reached = result.controls.some((c) => c.kind === "prior_contact");
  const mail = mailLine(result);

  if (compact) {
    const bits = [
      ...result.people.map((p) => `En el CRM: ${p.display_name}`),
      ...(result.people.length === 0 ? result.addresses.map((a) => `En el CRM: ${a.organization_name ?? a.display_name ?? a.email}`) : []),
      ...(mail ? [`Nos escribimos: ${mail}`] : []),
      ...(reached ? ["Ya recibió una campaña"] : []),
      ...result.prospects.map((p) => `Otro prospecto: ${p.person_name ?? p.organization_name ?? "—"}`),
    ];
    return (
      <div className="space-y-0.5 text-[11px]" data-testid="lookup-compact" role="status">
        {blocks.length ? (
          <p className="font-medium text-bad">En Bajas{blocks[0].reason ? `: ${blocks[0].reason}` : ""} — no se le puede escribir.</p>
        ) : null}
        {bits.length ? bits.map((b) => <p key={b} className="text-warn">{b}</p>) : null}
        {!blocks.length && !bits.length ? <p className="text-good">✓ No aparece en ningún lado: es nuevo.</p> : null}
        {result.organizations.length === 1 ? (
          <p className="text-ink-muted">Institución conocida: {result.organizations[0].name}</p>
        ) : null}
      </div>
    );
  }

  if (!result.found) {
    return (
      <p className="text-[13px] text-ink-muted" role="status" data-testid="lookup-empty">
        <span aria-hidden="true" className="text-good">✓ </span>
        «{result.query}» no aparece en el CRM, en los correos, en Bajas ni en Prospectos.
      </p>
    );
  }
  return (
    <div className="space-y-3" data-testid="lookup-results" role="status">
      {blocks.length ? (
        <p className="rounded-md bg-bad-bg px-3 py-2 text-[13px] font-medium text-bad">
          En Bajas ({blocks[0].scope === "domain" ? "todo el dominio" : "esta dirección"})
          {blocks[0].reason ? `: ${blocks[0].reason}` : ""}. No se le puede escribir.
        </p>
      ) : null}
      {result.people.length ? (
        <Group title="Personas en el CRM">
          {result.people.map((p) => (
            <Row key={p.person_id}>
              <a href={crmHash("personas", p.person_id)} className={LINK}>{p.display_name}</a>
              <span className="min-w-0 truncate text-xs text-ink-muted">{[p.organization_name, p.email].filter(Boolean).join(" · ")}</span>
            </Row>
          ))}
        </Group>
      ) : null}
      {result.organizations.length ? (
        <Group title="Instituciones">
          {result.organizations.map((o) => (
            <Row key={o.organization_id}>
              <a href={crmHash("organizaciones", o.organization_id)} className={LINK}>{o.name}</a>
              {o.domain ? <span className="text-xs text-ink-faint">{o.domain}</span> : null}
            </Row>
          ))}
        </Group>
      ) : null}
      {result.addresses.length ? (
        <Group title="Correos guardados en el CRM">
          {result.addresses.map((a) => (
            <Row key={a.email}>
              <span className="min-w-0 truncate text-ink">{a.email}</span>
              <span className="min-w-0 truncate text-xs text-ink-muted">{a.display_name ?? a.organization_name ?? "sin dueño"}</span>
            </Row>
          ))}
        </Group>
      ) : null}
      {mail || reached ? (
        <Group title="Correos con nosotros">
          {mail ? <Row><span className="text-ink">{mail}</span></Row> : null}
          {reached ? <Row><span className="text-ink">Ya recibió una campaña</span></Row> : null}
        </Group>
      ) : null}
      {result.prospects.length ? (
        <Group title="Prospectos">
          {result.prospects.map((p) => (
            <Row key={p.finding_id}>
              {onOpenProspect ? (
                <button type="button" onClick={() => onOpenProspect(p.finding_id)} className={`${LINK} text-left`}>
                  {p.person_name ?? p.organization_name ?? "Prospecto"}
                </button>
              ) : (
                <span className="min-w-0 truncate font-medium text-ink">{p.person_name ?? p.organization_name ?? "Prospecto"}</span>
              )}
              {p.person_name && p.organization_name ? <span className="min-w-0 truncate text-xs text-ink-muted">{p.organization_name}</span> : null}
            </Row>
          ))}
        </Group>
      ) : null}
    </div>
  );
}
