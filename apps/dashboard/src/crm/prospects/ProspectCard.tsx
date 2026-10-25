import { Badge, Button, ExternalLink, fmtDate } from "../ui";
import { ProspectLinkRow } from "./ProspectLinks";
import { ProspectLogo } from "./ProspectLogo";
import type { ProspectItem } from "./prospectApi";

/** «adjudicado hace 3 semanas»: how fresh the money is, in words. */
export function awardedAgo(awardedOn: string | null, now: Date): string | null {
  if (!awardedOn) return null;
  const days = Math.floor((now.getTime() - new Date(`${awardedOn}T12:00:00Z`).getTime()) / 86_400_000);
  if (Number.isNaN(days) || days < 0) return null;
  if (days < 14) return days <= 1 ? "adjudicado hace 1 día" : `adjudicado hace ${days} días`;
  if (days < 60) return `adjudicado hace ${Math.round(days / 7)} semanas`;
  const months = Math.round(days / 30);
  return months < 12 ? `adjudicado hace ${months} meses` : `adjudicado hace ${Math.floor(months / 12)} año${months >= 24 ? "s" : ""}`;
}

export function displayName(p: Pick<ProspectItem, "person_name" | "organization_name">): string {
  return p.person_name ?? p.organization_name;
}

/**
 * One prospect, best-first in the list: who and where, the money and how fresh it is, «Por qué
 * ahora», the equipment that fits, how to reach them (with where the email was published), the links
 * found, and the three decisions. Status is said in words and a glyph, never by colour alone.
 */
export function ProspectCard({
  item,
  now,
  canDecide,
  selected,
  busy,
  onToggle,
  onOpen,
  onContact,
  onLater,
  onDiscard,
  onEdit,
}: {
  item: ProspectItem;
  now: Date;
  canDecide: boolean;
  selected: boolean;
  busy: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onContact: () => void;
  onLater: () => void;
  onDiscard: () => void;
  /** «Editar»: correct any field of the card (owner 2026-10-25). */
  onEdit?: () => void;
}) {
  const name = displayName(item);
  const ago = awardedAgo(item.awarded_on, now);
  const knownOrg = item.organization_candidates.length === 1;
  const maybeDuplicate = item.organization_candidates.length > 1;
  return (
    <article
      data-testid={`prospect-card-${item.id}`}
      aria-label={name}
      className={`crm-rise rounded-xl border bg-canvas-raised p-4 shadow-sm transition-colors ${
        selected ? "border-brand-600 ring-1 ring-brand-600/40" : "border-line hover:border-line-strong"
      }`}
    >
      <div className="flex items-start gap-3">
        {canDecide ? (
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            aria-label={`Seleccionar a ${name}`}
            className="mt-1 h-4 w-4 shrink-0 accent-brand-700"
          />
        ) : null}
        <ProspectLogo id={item.id} name={item.organization_name} hasLogo={item.has_logo} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {item.returns_today ? <Badge tone="info">Vuelve hoy</Badge> : null}
            {item.state === "known" ? <Badge tone="warn">Ya conocido</Badge> : null}
            {knownOrg ? <Badge tone="good" title={item.organization_candidates[0].name}>Institución conocida</Badge> : null}
            {maybeDuplicate ? <Badge tone="warn">Posible duplicado</Badge> : null}
          </div>
          <h3 className="mt-0.5 text-[15px] font-semibold leading-5 text-ink">
            <button type="button" onClick={onOpen} className="text-left hover:text-accent hover:underline focus-visible:underline">
              {name}
            </button>
          </h3>
          <p className="text-[13px] text-ink-muted">
            {[item.person_name ? item.role : null, item.organization_name !== name ? item.organization_name : null, item.unit]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {item.fund_label ? (
          <div className="hidden shrink-0 text-right sm:block">
            <p className="text-[13px] font-semibold text-ink">{item.fund_label}</p>
            <p className="text-xs text-ink-muted">{[item.amount_label, ago].filter(Boolean).join(" · ")}</p>
          </div>
        ) : null}
      </div>

      <div className="mt-3 space-y-2.5 sm:pl-14">
        {item.fund_label ? (
          <p className="text-xs text-ink-muted sm:hidden">{[item.fund_label, item.amount_label, ago].filter(Boolean).join(" · ")}</p>
        ) : null}
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">Por qué ahora</p>
          <p className="text-[13px] leading-5 text-ink">{item.why_now}</p>
        </div>
        {item.equipment.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5" aria-label="Equipos que calzan">
            {item.equipment.map((e) => (
              <li key={e} className="rounded-full border border-brand-600/25 bg-brand-50 px-2 py-0.5 text-xs font-medium text-accent">
                {e}
              </li>
            ))}
          </ul>
        ) : null}
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
          {item.email ? (
            <>
              <a href={`mailto:${item.email}`} className="font-medium text-ink underline decoration-line-strong underline-offset-2 hover:decoration-ink">
                {item.email}
              </a>
              {item.email_source_url ? (
                <span className="text-xs text-ink-muted">
                  <ExternalLink href={item.email_source_url}>
                    {item.email_source_year ? `fuente: paper ${item.email_source_year}` : "fuente"}
                  </ExternalLink>
                </span>
              ) : item.email_source_note ? (
                <span className="text-xs text-ink-muted">fuente: {item.email_source_note}</span>
              ) : null}
            </>
          ) : (
            <>
              <Badge tone="neutral">Sin correo — llamar</Badge>
              {item.phone ? <a href={`tel:${item.phone.replace(/\s+/g, "")}`} className="font-medium text-ink">{item.phone}</a> : null}
            </>
          )}
          {item.email && item.phone ? <span className="text-ink-muted">· {item.phone}</span> : null}
        </p>
        <ProspectLinkRow links={item.links} />
        {item.edited ? (
          <p className="text-[11px] text-ink-faint">
            Editado{item.edited.by ? ` por ${item.edited.by}` : ""} · {fmtDate(item.edited.at)}
          </p>
        ) : null}
      </div>

      {canDecide ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3 sm:pl-14">
          <Button variant="primary" onClick={onContact} busy={busy} aria-label={`Contactar a ${name}`} className="min-h-9">
            Contactar
          </Button>
          <Button onClick={onLater} disabled={busy} aria-label={`Más tarde: ${name}`} className="min-h-9">
            Más tarde
          </Button>
          <Button variant="quiet" onClick={onDiscard} disabled={busy} aria-label={`Descartar a ${name}`} className="min-h-9">
            Descartar
          </Button>
          {onEdit ? (
            <button type="button" onClick={onEdit} disabled={busy} aria-label={`Editar a ${name}`}
              className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs font-medium text-ink-muted hover:bg-canvas-sunken hover:text-ink disabled:opacity-50">
              <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M10.5 2.5l3 3L6 13H3v-3l7.5-7.5z" />
              </svg>
              Editar
            </button>
          ) : null}
          <button type="button" onClick={onOpen} className={`${onEdit ? "" : "ml-auto "}text-xs font-medium text-accent hover:underline`}>
            Ver todo
          </button>
        </div>
      ) : null}
    </article>
  );
}
