/**
 * The V2 CRM browser — the first searchable operator surface over the durable core.
 *
 * Four cards, one page: contacts, organizations, prospects and evidence. Everything here
 * reads `/v2/*`, which is the **durable** V2 core — not the `mirror*` and `leadIntel*`
 * projections the older pages read, which may be dropped and rebuilt at any time.
 *
 * It is read-only, and structurally so: there is no command client imported here, and the
 * proxy allows no POST under `/v2`. Confirming an organization, naming a person or merging
 * two identities are durable commands that belong to the V2 command boundary, which does
 * not exist yet. Until it does, this page is where an operator *looks*, and the review
 * queue it shows is worked through the migration tools.
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchV2ContactCard,
  fetchV2Contacts,
  fetchV2Evidence,
  fetchV2OrganizationCard,
  fetchV2Organizations,
  fetchV2Prospects,
} from "../api/v2Client";
import type {
  V2CardEvidence,
  V2Contact,
  V2ContactCard,
  V2EvidenceItem,
  V2Opportunity,
  V2Organization,
  V2OrganizationCard,
  V2Page,
} from "../api/v2Types";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import {
  CRM_V2_TABS,
  EVIDENCE_RESOLUTIONS,
  EVIDENCE_SOURCE_KINDS,
  NO_ORGANIZATION_EXPLANATION,
  cappedListNote,
  cardCount,
  confirmationLabel,
  noPersonExplanation,
  pageFooter,
  resolutionLabel,
  sourceKindLabel,
  usageLabel,
  type CrmV2Tab,
} from "../lib/crmV2Browser";
import { formatMirrorLoadError } from "../lib/humanizeApiError";

const PAGE_SIZE = 50;

type AnyPage =
  | { kind: "contacts"; page: V2Page<V2Contact> }
  | { kind: "organizations"; page: V2Page<V2Organization> }
  | { kind: "prospects"; page: V2Page<V2Opportunity> }
  | { kind: "evidence"; page: V2Page<V2EvidenceItem> };

type OpenCard =
  | { kind: "contact"; data: V2ContactCard }
  | { kind: "organization"; data: V2OrganizationCard };

function Badge({ tone, children }: { tone: "neutral" | "warn" | "ok"; children: React.ReactNode }) {
  const palette = {
    neutral: "bg-slate-100 text-slate-700",
    warn: "bg-amber-100 text-amber-800",
    ok: "bg-emerald-100 text-emerald-800",
  }[tone];
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${palette}`}>
      {children}
    </span>
  );
}

function ConfirmationBadge({ confirmation }: { confirmation: string }) {
  return (
    <Badge tone={confirmation === "confirmed" ? "ok" : "warn"}>
      {confirmationLabel(confirmation)}
    </Badge>
  );
}

function EvidenceList({ rows, total }: { rows: V2CardEvidence[]; total: number }) {
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--color-muted)]">Sin evidencia registrada.</p>;
  }
  const note = cappedListNote(rows.length, total);
  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div
          key={row.assertion_id}
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          data-testid="v2-card-evidence-row"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-800">{row.kind}</span>
            <Badge tone={row.resolution === "promoted" ? "ok" : "warn"}>
              {resolutionLabel(row.resolution)}
            </Badge>
            <Badge tone="neutral">{sourceKindLabel(row.source_kind)}</Badge>
            {row.source_is_quarantined ? <Badge tone="warn">En cuarentena</Badge> : null}
          </div>
          <p className="mt-1 break-all text-[var(--color-muted)]">{row.value_norm}</p>
          {row.ambiguity_note ? (
            <p className="mt-1 text-amber-800">{row.ambiguity_note}</p>
          ) : null}
          {row.source_uri ? (
            <p className="mt-1 break-all text-xs text-[var(--color-muted)]">{row.source_uri}</p>
          ) : null}
        </div>
      ))}
      {note ? <p className="text-xs text-[var(--color-muted)]">{note}</p> : null}
    </div>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold text-slate-900">
        {title}
        {typeof count === "number" ? (
          <span className="ml-2 font-normal text-[var(--color-muted)]">{count.toLocaleString("es-CL")}</span>
        ) : null}
      </h4>
      {children}
    </section>
  );
}

function ContactCardBody({ card }: { card: V2ContactCard }) {
  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <p className="break-all text-base font-semibold text-slate-900">{card.address}</p>
        <div className="flex flex-wrap gap-2">
          <Badge tone="neutral">{usageLabel(card.usage)}</Badge>
          <ConfirmationBadge confirmation={card.confirmation} />
          {card.origin_source_kind ? (
            <Badge tone="neutral">{sourceKindLabel(card.origin_source_kind)}</Badge>
          ) : null}
        </div>
      </div>

      <Section title="Persona">
        {card.person_display_name ? (
          <p className="text-sm text-slate-800">{card.person_display_name}</p>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">{noPersonExplanation(card.usage)}</p>
        )}
      </Section>

      <Section title="Institución">
        {card.organization_name ? (
          <p className="text-sm text-slate-800">{card.organization_name}</p>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">{NO_ORGANIZATION_EXPLANATION}</p>
        )}
      </Section>

      <Section
        title="Otros canales de la misma persona"
        count={cardCount(card.counts, "sibling_contact_points", card.sibling_contact_points.length)}
      >
        {card.sibling_contact_points.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Ninguno.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.sibling_contact_points.map((sibling) => (
              <li key={sibling.contact_point_id} className="break-all text-slate-800">
                {sibling.address}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Afiliaciones" count={cardCount(card.counts, "affiliations", card.affiliations.length)}>
        {card.affiliations.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            Ninguna. Una afiliación es una relación que nadie escribió en un encabezado de correo.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.affiliations.map((affiliation) => (
              <li key={affiliation.affiliation_id} className="text-slate-800">
                {affiliation.organization_name}
                {affiliation.role_title ? ` — ${affiliation.role_title}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Historial de campañas" count={cardCount(card.counts, "marketing", card.marketing.length)}>
        {card.marketing.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Nunca incluido en una campaña.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.marketing.map((entry, index) => (
              <li key={`${entry.campaign_name}-${index}`} className="text-slate-800">
                {entry.campaign_name} — {entry.recipient_state} ({entry.attempt_count} intentos)
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Controles sobre la dirección"
        count={cardCount(card.counts, "address_controls", card.address_controls.length)}
      >
        <p className="mb-2 text-xs text-[var(--color-muted)]">
          Un control es un hecho sobre la dirección, no sobre una identidad: sigue vigente
          aunque el titular sea desconocido o cambie.
        </p>
        {card.address_controls.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Ninguno.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.address_controls.map((control, index) => (
              <li key={`${control.source}-${index}`} className="text-slate-800">
                {control.control_kind} · {control.purpose} · {control.source}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Evidencia" count={cardCount(card.counts, "evidence", card.evidence.length)}>
        <EvidenceList
          rows={card.evidence}
          total={cardCount(card.counts, "evidence", card.evidence.length)}
        />
      </Section>
    </div>
  );
}

function OrganizationCardBody({ card }: { card: V2OrganizationCard }) {
  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <p className="text-base font-semibold text-slate-900">{card.name}</p>
        <div className="flex flex-wrap gap-2">
          <Badge tone={card.kind === "unknown" ? "warn" : "neutral"}>
            {card.kind === "unknown" ? "Tipo sin clasificar" : card.kind}
          </Badge>
          <ConfirmationBadge confirmation={card.confirmation} />
          {card.origin_source_kind ? (
            <Badge tone="neutral">{sourceKindLabel(card.origin_source_kind)}</Badge>
          ) : null}
        </div>
        {card.merged_into_organization_name ? (
          <p className="text-sm text-amber-800">
            Fusionada en {card.merged_into_organization_name}.
          </p>
        ) : null}
      </div>

      <Section
        title="Canales"
        count={cardCount(card.counts, "contact_points", card.contact_points.length)}
      >
        {card.contact_points.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            Ninguno vinculado. Compartir un dominio de correo no vincula un canal.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.contact_points.map((channel) => (
              <li key={channel.contact_point_id} className="break-all text-slate-800">
                {channel.address}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Personas" count={cardCount(card.counts, "people", card.people.length)}>
        {card.people.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            Ninguna. La evidencia migrada no trae nombres de personas.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.people.map((person) => (
              <li key={person.person_id} className="text-slate-800">
                {person.display_name}
                {person.role_title ? ` — ${person.role_title}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Dominios" count={cardCount(card.counts, "domains", card.domains.length)}>
        {card.domains.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Ninguno registrado.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.domains.map((domain) => (
              <li key={domain.organization_domain_id} className="text-slate-800">
                {domain.domain} ({domain.scope})
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Relaciones"
        count={cardCount(card.counts, "relationships", card.relationships.length)}
      >
        {card.relationships.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Ninguna registrada.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.relationships.map((relationship) => (
              <li key={relationship.organization_relationship_id} className="text-slate-800">
                {relationship.role}
                {relationship.note ? ` — ${relationship.note}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Unidades dependientes"
        count={cardCount(card.counts, "child_organizations", card.child_organizations.length)}
      >
        {card.child_organizations.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Ninguna.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.child_organizations.map((child) => (
              <li key={child.organization_id} className="text-slate-800">
                {child.name}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Evidencia" count={cardCount(card.counts, "evidence", card.evidence.length)}>
        <EvidenceList
          rows={card.evidence}
          total={cardCount(card.counts, "evidence", card.evidence.length)}
        />
      </Section>
    </div>
  );
}

function CardDrawer({ card, onClose }: { card: OpenCard; onClose: () => void }) {
  return (
    <aside
      className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-slate-200 bg-[var(--color-card)] p-5 shadow-xl"
      role="dialog"
      aria-label={card.kind === "contact" ? "Ficha de contacto" : "Ficha de institución"}
      data-testid="v2-card-drawer"
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--color-muted)]">
          {card.kind === "contact" ? "Ficha de contacto" : "Ficha de institución"}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700"
        >
          Cerrar
        </button>
      </div>
      {card.kind === "contact" ? (
        <ContactCardBody card={card.data} />
      ) : (
        <OrganizationCardBody card={card.data} />
      )}
    </aside>
  );
}

export function CrmV2Page() {
  const [tab, setTab] = useState<CrmV2Tab>("contacts");
  const [query, setQuery] = useState("");
  const [resolution, setResolution] = useState("");
  const [sourceKind, setSourceKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<AnyPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [card, setCard] = useState<OpenCard | null>(null);
  const [cardError, setCardError] = useState<string | null>(null);

  const definition = CRM_V2_TABS.find((entry) => entry.id === tab)!;
  const definitionLabel = definition.label;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const trimmed = query.trim();
    try {
      if (tab === "contacts") {
        const page = await fetchV2Contacts({ q: trimmed || undefined, limit: PAGE_SIZE, offset });
        setResult({ kind: "contacts", page });
      } else if (tab === "organizations") {
        const page = await fetchV2Organizations({ q: trimmed || undefined, limit: PAGE_SIZE, offset });
        setResult({ kind: "organizations", page });
      } else if (tab === "prospects") {
        const page = await fetchV2Prospects({ limit: PAGE_SIZE, offset });
        setResult({ kind: "prospects", page });
      } else {
        const page = await fetchV2Evidence({
          q: trimmed || undefined,
          resolution: resolution || undefined,
          sourceKind: sourceKind || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        setResult({ kind: "evidence", page });
      }
    } catch (caught) {
      setResult(null);
      setError(formatMirrorLoadError(definitionLabel, caught).message);
    } finally {
      setLoading(false);
    }
  }, [tab, query, resolution, sourceKind, offset, definitionLabel]);

  useEffect(() => {
    void load();
  }, [load]);

  function switchTab(next: CrmV2Tab) {
    setTab(next);
    setOffset(0);
    setQuery("");
    setResolution("");
    setSourceKind("");
    setResult(null);
  }

  async function openContact(contactPointId: string) {
    setCardError(null);
    try {
      setCard({ kind: "contact", data: await fetchV2ContactCard(contactPointId) });
    } catch (caught) {
      setCardError(formatMirrorLoadError("Ficha de contacto", caught).message);
    }
  }

  async function openOrganization(organizationId: string) {
    setCardError(null);
    try {
      setCard({ kind: "organization", data: await fetchV2OrganizationCard(organizationId) });
    } catch (caught) {
      setCardError(formatMirrorLoadError("Ficha de institución", caught).message);
    }
  }

  const page = result?.page;

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="CRM V2 (núcleo durable)"
        subtitle="Lectura directa del núcleo durable V2. No es un espejo reconstruible: cada fila trae su procedencia."
      />

      <nav className="flex flex-wrap gap-2" aria-label="Tarjetas del CRM V2">
        {CRM_V2_TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => switchTab(entry.id)}
            aria-current={entry.id === tab ? "page" : undefined}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
              entry.id === tab
                ? "bg-slate-900 text-white"
                : "border border-slate-300 text-slate-700"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      <p className="text-sm text-[var(--color-muted)]">{definition.description}</p>

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setOffset(0);
          void load();
        }}
      >
        {definition.searchable ? (
          <label className="flex flex-col text-xs text-[var(--color-muted)]">
            Buscar
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={tab === "organizations" ? "nombre" : "dirección o valor"}
              className="mt-1 w-64 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
            />
          </label>
        ) : null}

        {tab === "evidence" ? (
          <>
            <label className="flex flex-col text-xs text-[var(--color-muted)]">
              Resolución
              <select
                value={resolution}
                onChange={(event) => {
                  setResolution(event.target.value);
                  setOffset(0);
                }}
                className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
              >
                <option value="">Todas</option>
                {EVIDENCE_RESOLUTIONS.map((value) => (
                  <option key={value} value={value}>
                    {resolutionLabel(value)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col text-xs text-[var(--color-muted)]">
              Procedencia
              <select
                value={sourceKind}
                onChange={(event) => {
                  setSourceKind(event.target.value);
                  setOffset(0);
                }}
                className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
              >
                <option value="">Todas</option>
                {EVIDENCE_SOURCE_KINDS.map((value) => (
                  <option key={value} value={value}>
                    {sourceKindLabel(value)}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : null}

        <button
          type="submit"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
        >
          Buscar
        </button>
      </form>

      {error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      ) : null}
      {cardError ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {cardError}
        </p>
      ) : null}
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      {page && page.items.length === 0 && !loading ? (
        <V2EmptyState
          title="Sin filas"
          description={
            tab === "prospects"
              ? "Sin datos: falta migrar el histórico V1. crm.opportunity está vacía porque las oportunidades durables de V1 aún no se migran — no porque no haya trabajo."
              : "Ninguna fila coincide con la búsqueda."
          }
        />
      ) : null}

      {result?.kind === "contacts" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-contacts-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Dirección</th>
              <th>Uso</th>
              <th>Estado</th>
              <th>Institución</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.contact_point_id} className="border-t border-slate-200">
                <td className="py-2">
                  <button
                    type="button"
                    className="break-all text-left font-medium text-slate-900 underline decoration-dotted"
                    onClick={() => void openContact(row.contact_point_id)}
                  >
                    {row.address}
                  </button>
                </td>
                <td>{usageLabel(row.usage)}</td>
                <td>
                  <ConfirmationBadge confirmation={row.confirmation} />
                </td>
                <td className="text-[var(--color-muted)]">{row.organization_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {result?.kind === "organizations" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-organizations-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Nombre</th>
              <th>Tipo</th>
              <th>Estado</th>
              <th>Canales</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.organization_id} className="border-t border-slate-200">
                <td className="py-2">
                  <button
                    type="button"
                    className="text-left font-medium text-slate-900 underline decoration-dotted"
                    onClick={() => void openOrganization(row.organization_id)}
                  >
                    {row.name}
                  </button>
                </td>
                <td>{row.kind === "unknown" ? "Sin clasificar" : row.kind}</td>
                <td>
                  <ConfirmationBadge confirmation={row.confirmation} />
                </td>
                <td>{row.contact_point_count.toLocaleString("es-CL")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {result?.kind === "prospects" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-prospects-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Oportunidad</th>
              <th>Etapa</th>
              <th>Institución</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.opportunity_id} className="border-t border-slate-200">
                <td className="py-2 font-medium text-slate-900">{row.title}</td>
                <td>{row.stage}</td>
                <td className="text-[var(--color-muted)]">{row.organization_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {result?.kind === "evidence" && result.page.items.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="v2-evidence-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Observación</th>
              <th>Tipo</th>
              <th>Resolución</th>
              <th>Procedencia</th>
            </tr>
          </thead>
          <tbody>
            {result.page.items.map((row) => (
              <tr key={row.assertion_id} className="border-t border-slate-200">
                <td className="break-all py-2 text-slate-900">{row.value_norm}</td>
                <td>{row.kind}</td>
                <td>
                  <Badge tone={row.resolution === "promoted" ? "ok" : "warn"}>
                    {resolutionLabel(row.resolution)}
                  </Badge>
                </td>
                <td className="text-[var(--color-muted)]">{sourceKindLabel(row.source_kind)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {page && page.total > 0 ? (
        <div className="flex items-center justify-between text-sm text-[var(--color-muted)]">
          <span data-testid="v2-page-footer">
            {pageFooter(page.total, page.limit || PAGE_SIZE, page.offset)}
          </span>
          <span className="flex gap-2">
            <button
              type="button"
              disabled={page.offset <= 0}
              onClick={() => setOffset(Math.max(0, page.offset - PAGE_SIZE))}
              className="rounded-md border border-slate-300 px-2 py-1 disabled:opacity-40"
            >
              Anterior
            </button>
            <button
              type="button"
              disabled={page.offset + PAGE_SIZE >= page.total}
              onClick={() => setOffset(page.offset + PAGE_SIZE)}
              className="rounded-md border border-slate-300 px-2 py-1 disabled:opacity-40"
            >
              Siguiente
            </button>
          </span>
        </div>
      ) : null}

      {card ? <CardDrawer card={card} onClose={() => setCard(null)} /> : null}
    </div>
  );
}
