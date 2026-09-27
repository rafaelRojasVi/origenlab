import { useEffect, useMemo, useState } from "react";
import { fetchV2Contacts } from "../../api/v2Client";
import { fetchOverview, fetchPipeline } from "../crmApi";
import type { OpportunityCardData } from "../crmTypes";
import type { CrmSection } from "../crmRoute";
import {
  Badge,
  EmptyState,
  NotImportedState,
  PageHeader,
  Panel,
  ResourceGate,
  SearchInput,
  Skeleton,
  StatLine,
  fmtDate,
  fmtInt,
} from "../ui";
import { useResource } from "../useResource";
import { splitAddress } from "../address";
import { useAuthSession } from "../../context/AuthSessionContext";
import { contactAddressesRedacted } from "../redaction";

interface Recipient {
  address: string;
  cases: OpportunityCardData[];
  lastSent: string | null;
}


export function PeoplePage({ navigate }: { navigate: (s: CrmSection, id?: string) => void }) {
  const [overview, reloadOverview] = useResource(fetchOverview);
  const [pipeline, reloadPipeline] = useResource(fetchPipeline);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);
  const [contacts, reloadContacts] = useResource(
    () => fetchV2Contacts({ q: debounced || undefined, limit: 30 }),
    [debounced],
  );

  const persons = overview.kind === "ready" ? overview.data.entities.find((e) => e.key === "persons")?.count ?? 0 : null;
  const { session } = useAuthSession();
  const redacted = contactAddressesRedacted(session);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Personas"
        subtitle="Quién está al otro lado de cada caso. Una dirección de correo no es una persona hasta que un operador la registra."
      />
      {redacted ? (
        <p
          role="note"
          className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-[12px] text-warn"
          data-testid="people-redaction-note"
        >
          Tu rol no puede ver direcciones de contacto: la API las entrega enmascaradas (<code>***@dominio</code>) y
          esta pantalla no las tiene. Un operador con rol <strong>sales</strong> o <strong>admin</strong> las ve completas.
        </p>
      ) : null}
      <ResourceGate state={overview} reload={reloadOverview} skeleton={<Skeleton rows={1} />}>
        {() =>
          persons === 0 ? (
            <NotImportedState title="El CRM no tiene personas registradas">
              Las personas de V1 (<code>commercial.contact</code>) no se han migrado porque su volcado no existe localmente, y
              ninguna evidencia se ha promovido a persona todavía. Abajo están los contactos reales que sí existen: los
              destinatarios de las cotizaciones (evidencia de Gmail) y las direcciones importadas.
            </NotImportedState>
          ) : (
            <StatLine items={[{ label: "Personas registradas", value: fmtInt(persons ?? 0) }]} />
          )
        }
      </ResourceGate>

      <ResourceGate state={pipeline} reload={reloadPipeline} skeleton={<Skeleton rows={4} cards />}>
        {(p) => <Recipients items={p.items} navigate={navigate} />}
      </ResourceGate>

      <Panel
        title="Direcciones de contacto importadas"
        note="crm.contact_point — sin vínculo a persona ni institución"
        aside={
          redacted ? (
            // The API compares only person and organization names for this role; say so
            // instead of offering an address search that would find nothing.
            <SearchInput value={q} onChange={setQ} label="Buscar por nombre" placeholder="Nombre de persona o institución…" />
          ) : (
            <SearchInput value={q} onChange={setQ} label="Buscar direcciones" placeholder="Dirección o dominio…" />
          )
        }
      >
        <ResourceGate state={contacts} reload={reloadContacts} skeleton={<div className="p-3"><Skeleton rows={5} /></div>}>
          {(page) =>
            page.items.length === 0 ? (
              <div className="p-3">
                <EmptyState title="Ninguna dirección coincide" />
              </div>
            ) : (
              <>
                <ul className="divide-y divide-line">
                  {page.items.map((c) => (
                    <li key={c.contact_point_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{c.address}</span>
                      {c.person_display_name ? <Badge tone="good">{c.person_display_name}</Badge> : null}
                      {c.organization_name ? <Badge glyph={false}>{c.organization_name}</Badge> : null}
                      <Badge tone={c.confirmation === "confirmed" ? "good" : "neutral"} glyph={false}>
                        {c.confirmation === "confirmed" ? "Confirmada" : "Importada"}
                      </Badge>
                      {c.address_control_count > 0 ? (
                        <Badge tone="warn" title="Tiene controles de envío (bloqueo o contacto previo)">
                          {c.address_control_count} control{c.address_control_count === 1 ? "" : "es"}
                        </Badge>
                      ) : null}
                    </li>
                  ))}
                </ul>
                <p className="border-t border-line px-3 py-2 text-[11px] text-ink-faint">
                  {page.items.length} de {fmtInt(page.total)} direcciones
                </p>
              </>
            )
          }
        </ResourceGate>
      </Panel>
    </div>
  );
}

function Recipients({ items, navigate }: { items: OpportunityCardData[]; navigate: (s: CrmSection, id?: string) => void }) {
  const recipients = useMemo(() => {
    const map = new Map<string, Recipient>();
    for (const c of items) {
      if (c.contact?.source !== "gmail_recipient" || !c.contact.address) continue;
      const key = splitAddress(c.contact.address).email.toLowerCase();
      const r = map.get(key) ?? { address: c.contact.address, cases: [], lastSent: null };
      r.cases.push(c);
      const sent = c.latest_revision?.sent_at ?? null;
      if (sent && (!r.lastSent || sent > r.lastSent)) r.lastSent = sent;
      map.set(key, r);
    }
    return [...map.values()].sort((a, b) => (b.lastSent ?? "").localeCompare(a.lastSent ?? ""));
  }, [items]);

  if (recipients.length === 0) {
    return <EmptyState title="Sin destinatarios de cotizaciones">Ninguna revisión tiene un correo de Gmail con destinatario.</EmptyState>;
  }
  return (
    <section>
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h2 className="text-[13px] font-semibold text-ink">Destinatarios de cotizaciones</h2>
        <span className="text-[11px] text-ink-faint">
          {recipients.length} direcciones · desde la evidencia de Gmail de cada revisión, no personas del CRM
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {recipients.map((r, i) => {
          const { display, email } = splitAddress(r.address);
          const orgs = [...new Set(r.cases.map((c) => c.organization?.name).filter(Boolean))];
          return (
            <article
              key={email}
              style={{ animationDelay: `${Math.min(i, 12) * 18}ms` }}
              className="crm-card-in rounded-md border border-line bg-canvas-raised p-3 shadow-[0_1px_1px_rgb(24_24_27/0.03)] hover:border-line-strong hover:shadow-sm"
            >
              <p className="truncate text-[13px] font-semibold text-ink">{display ?? email}</p>
              {display ? <p className="truncate text-xs text-ink-muted">{email}</p> : null}
              <p className="mt-1 truncate text-[11px] text-ink-muted">{orgs.join(" · ") || "Sin institución"}</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {r.cases.map((c) => (
                  <button
                    key={c.opportunity_id}
                    type="button"
                    onClick={() => navigate("oportunidades", c.opportunity_id)}
                    className="rounded-full border border-line bg-canvas-sunken px-1.5 py-px text-[11px] tabular-nums text-ink hover:border-brand-600/40 hover:text-brand-700"
                  >
                    {c.latest_revision?.quote_number ?? c.quote_numbers[0] ?? "caso"}
                  </button>
                ))}
              </div>
              <p className="mt-2 border-t border-line/70 pt-1.5 text-[11px] text-ink-faint">Última cotización {fmtDate(r.lastSent)}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}
