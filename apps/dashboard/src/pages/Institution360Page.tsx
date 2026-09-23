/**
 * Institución 360 — one institution and everything it is involved in.
 *
 * The screen keeps two facts apart that are constantly confused, and it never merges them
 * into one badge: what an institution **is** (its identity and its channels) and what it is
 * **to OrigenLab commercially** (a recorded supplier or manufacturer relationship, versus
 * the institutions that ask for things in commercial cases). An organization can legitimately
 * be both at once, and a single "tipo" label would have to pick one and be wrong.
 *
 * **Read-only, structurally.** No command client is imported and the dashboard-proxy allows
 * no POST under `/v2`. Ids, raw vocabulary and provenance live in the closed drawer.
 */

import { useEffect, useMemo, useState } from "react";

import { fetchV2OrganizationCard, fetchV2Organizations } from "../api/v2Client";
import type {
  V2Organization,
  V2OrganizationCard,
  V2OrganizationCardChannel,
  V2OrganizationCase,
  V2Page,
} from "../api/v2Types";
import { V2Chip } from "../components/v2/V2Chip";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2Panel } from "../components/v2/V2Panel";
import { V2TechnicalDetails } from "../components/v2/V2TechnicalDetails";
import { V2CaseSummaryCard } from "../components/v2/V2CaseSummaryCard";
import {
  activityFromEvidence,
  caseRoleLabels,
  channelKindLabel,
  confirmationText,
  formatDate,
  institutionRoles,
  quotesForCases,
  relationCoverageNote,
  sourceKindLabel,
  splitInstitutionChannels,
  usageLabel,
} from "../lib/crm360";
import { cappedListNote, cardCount, pageFooter } from "../lib/crmV2Browser";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import { closeV2Detail, openV2Detail, useV2DetailId } from "../lib/v2DeepLink";
import { useV2FollowUpQuotes } from "../lib/useV2FollowUpQuotes";
import { useV2OrganizationCases } from "../lib/useV2OrganizationCases";

const PAGE_SIZE = 50;
const ACTIVITY_SHOWN = 6;

function InstitutionList() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<V2Page<V2Organization> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchV2Organizations({ q: submitted || undefined, limit: PAGE_SIZE, offset })
      .then((loaded) => {
        if (!cancelled) {
          setPage(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setPage(null);
          setError(formatMirrorLoadError("Instituciones", caught).message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [submitted, offset]);

  const rows = page?.items ?? [];

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Instituciones"
        subtitle="Instituciones del núcleo durable V2, con sus canales, sus casos y sus cotizaciones."
      />

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setOffset(0);
          setSubmitted(query.trim());
        }}
      >
        <label className="flex flex-col text-xs text-[var(--color-muted)]">
          Buscar
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="nombre"
            className="mt-1 w-72 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
          />
        </label>
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
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      {page && rows.length === 0 && !loading ? (
        <V2EmptyState title="Sin instituciones" description="Ninguna fila coincide con la búsqueda." />
      ) : null}

      {rows.length > 0 ? (
        <table className="w-full table-auto text-left text-sm" data-testid="institution-360-table">
          <thead className="text-xs uppercase text-[var(--color-muted)]">
            <tr>
              <th className="py-2">Institución</th>
              <th>Tipo</th>
              <th>Canales</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.organization_id} className="border-t border-slate-200">
                <td className="py-2">
                  <button
                    type="button"
                    onClick={() => openV2Detail("instituciones", row.organization_id)}
                    className="text-left font-medium text-slate-900 underline decoration-dotted"
                  >
                    {row.name}
                  </button>
                </td>
                <td className="text-[var(--color-muted)]">
                  {row.kind === "unknown" ? "Sin clasificar" : row.kind}
                </td>
                <td className="text-[var(--color-muted)]">
                  {row.contact_point_count.toLocaleString("es-CL")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {page && page.total > 0 ? (
        <div className="flex items-center justify-between text-sm text-[var(--color-muted)]">
          <span data-testid="institution-360-footer">
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
    </div>
  );
}

function ChannelRow({ channel }: { channel: V2OrganizationCardChannel }) {
  return (
    <li className="flex flex-wrap items-baseline gap-2 text-sm">
      <button
        type="button"
        onClick={() => openV2Detail("contactos", channel.contact_point_id)}
        className="min-w-0 break-all text-left font-medium text-slate-900 underline decoration-dotted"
      >
        {channel.person_display_name ?? channel.address}
      </button>
      {channel.person_display_name ? (
        <span className="min-w-0 break-all text-[var(--color-muted)]">{channel.address}</span>
      ) : (
        <V2Chip tone="warn">canal pendiente</V2Chip>
      )}
      <span className="text-xs text-[var(--color-muted)]">
        {channelKindLabel(channel.channel_kind)} · {usageLabel(channel.usage)}
      </span>
    </li>
  );
}

/**
 * What this institution is on this case — every part it holds, never just the first.
 *
 * Supplier *and* manufacturer on the same case is the ordinary shape of an instrument
 * deal, so this is a row of chips rather than one label. A part whose row has closed is
 * shown greyed and marked, because "was the supplier" is a different fact from "is".
 */
function CaseRoleChips({ row }: { row: V2OrganizationCase }) {
  const { current, ended } = caseRoleLabels(row);
  if (current.length === 0 && ended.length === 0) {
    return <span className="text-[var(--color-muted)]">Sin papel anotado</span>;
  }
  return (
    <span className="flex flex-wrap gap-1" data-testid="institution-360-case-role">
      {current.map((label) => (
        <V2Chip key={`current-${label}`} tone="ok">
          {label}
        </V2Chip>
      ))}
      {ended.map((label) => (
        <V2Chip key={`ended-${label}`} tone="neutral">
          {label} · terminado
        </V2Chip>
      ))}
    </span>
  );
}

function InstitutionDetail({ organizationId }: { organizationId: string }) {
  const [card, setCard] = useState<V2OrganizationCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const followUpQuotes = useV2FollowUpQuotes();
  /*
    The cases are asked for by institution, on the server. They are deliberately not a
    browser-side filter over a page of `/v2/cases`: that list names only the institution
    that is asking, so filtering it would hide every case where this one supplies,
    manufactures, pays or is named — which is most of them for a manufacturer.
  */
  const orgCases = useV2OrganizationCases(organizationId);
  const cases = orgCases.cases;
  const [showAllActivity, setShowAllActivity] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setCard(null);
    setError(null);
    fetchV2OrganizationCard(organizationId)
      .then((loaded) => {
        if (!cancelled) {
          setCard(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(formatMirrorLoadError("Ficha de institución", caught).message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [organizationId]);

  const roles = useMemo(
    () => (card ? institutionRoles(card, orgCases.cases) : null),
    [card, orgCases.cases],
  );
  const channels = useMemo(
    () => (card ? splitInstitutionChannels(card) : { named: [], pending: [] }),
    [card],
  );
  const quotes = useMemo(
    () => quotesForCases(followUpQuotes.quotes, cases),
    [followUpQuotes.quotes, cases],
  );
  const activity = useMemo(() => (card ? activityFromEvidence(card.evidence) : []), [card]);
  const caseCoverage = relationCoverageNote(cases.length, orgCases.total);
  const quoteCoverage = relationCoverageNote(followUpQuotes.quotes.length, followUpQuotes.total);

  const back = (
    <button
      type="button"
      onClick={() => closeV2Detail("instituciones")}
      className="rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700"
    >
      ← Instituciones
    </button>
  );

  if (error) {
    return (
      <div className="space-y-4">
        {back}
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      </div>
    );
  }

  if (!card || !roles) {
    return (
      <div className="space-y-4">
        {back}
        <p className="text-sm text-[var(--color-muted)]">Cargando la institución…</p>
      </div>
    );
  }

  const channelTotal = cardCount(card.counts, "contact_points", card.contact_points.length);
  const shownActivity = showAllActivity ? activity : activity.slice(0, ACTIVITY_SHOWN);
  const activityTotal = cardCount(card.counts, "evidence", activity.length);

  return (
    <div className="space-y-4" data-testid="institution-360-detail">
      {back}

      <header className="rounded-xl border border-slate-200 bg-[var(--color-card)] p-4">
        <h2 className="text-lg font-semibold text-slate-900">{card.name}</h2>
        {card.legal_name && card.legal_name !== card.name ? (
          <p className="text-sm text-[var(--color-muted)]">{card.legal_name}</p>
        ) : null}
        <div className="mt-2 flex flex-wrap gap-2">
          <V2Chip tone={card.kind === "unknown" ? "warn" : "neutral"}>
            {card.kind === "unknown" ? "Sin clasificar" : card.kind}
          </V2Chip>
          <V2Chip tone={card.confirmation === "confirmed" ? "ok" : "warn"}>
            {confirmationText(card.confirmation)}
          </V2Chip>
        </div>
        {card.merged_into_organization_name ? (
          <p className="mt-2 text-sm text-amber-800">
            Fusionada en {card.merged_into_organization_name}.
          </p>
        ) : null}
      </header>

      <V2Panel
        title="Papel comercial"
        caption="Lo que la institución es para OrigenLab, que no es lo mismo que lo que la institución es."
        testId="institution-360-roles"
      >
        {roles.empty ? (
          <p className="text-sm text-[var(--color-muted)]">
            Sin papel comercial registrado. Nadie ha anotado una relación y no participa en
            ningún caso cargado.
          </p>
        ) : (
          <div className="space-y-2 text-sm">
            {roles.recorded.length > 0 ? (
              <p>
                <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  Relación anotada:{" "}
                </span>
                {roles.recorded.join(" · ")}
              </p>
            ) : null}
            {roles.onCases.length > 0 ? (
              <div>
                <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  En los casos
                </p>
                <ul className="mt-1 space-y-0.5" data-testid="institution-360-case-roles">
                  {roles.onCases.map((tally) => (
                    <li key={tally.role}>
                      {tally.label}:{" "}
                      <span className="text-[var(--color-muted)]">
                        en {tally.caseCount.toLocaleString("es-CL")} caso(s)
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </V2Panel>

      <V2Panel
        title="Contactos y canales"
        count={channelTotal}
        caption="Primero las personas con nombre; abajo los canales sin titular establecido."
        testId="institution-360-channels"
      >
        {card.contact_points.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            Ninguno vinculado. Compartir un dominio de correo no vincula un canal.
          </p>
        ) : (
          <>
            {channels.named.length > 0 ? (
              <ul className="space-y-1">
                {channels.named.map((channel) => (
                  <ChannelRow key={channel.contact_point_id} channel={channel} />
                ))}
              </ul>
            ) : null}
            {channels.pending.length > 0 ? (
              <>
                <p className="pt-2 text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  Canales pendientes
                </p>
                <ul className="space-y-1">
                  {channels.pending.map((channel) => (
                    <ChannelRow key={channel.contact_point_id} channel={channel} />
                  ))}
                </ul>
              </>
            ) : null}
            {cappedListNote(card.contact_points.length, channelTotal) ? (
              <p className="text-xs text-[var(--color-muted)]">
                {cappedListNote(card.contact_points.length, channelTotal)}
              </p>
            ) : null}
          </>
        )}
      </V2Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <V2Panel
          title="Casos"
          count={orgCases.total}
          caption="Todos los casos en los que participa, con el papel exacto que tiene en cada uno."
          testId="institution-360-cases"
        >
          {orgCases.error ? (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {orgCases.error}
            </p>
          ) : null}
          {orgCases.loading ? (
            <p className="text-sm text-[var(--color-muted)]">Cargando los casos…</p>
          ) : null}
          {!orgCases.loading && !orgCases.error && cases.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">
              No participa en ningún caso cargado — ni pidiendo, ni proveyendo, ni fabricando.
            </p>
          ) : null}
          {cases.length > 0 ? (
            <ul className="space-y-2">
              {cases.map((row) => (
                <li key={row.opportunity_id}>
                  <V2CaseSummaryCard
                    row={row}
                    summary={null}
                    quotes={quotes.filter((quote) => quote.opportunity_id === row.opportunity_id)}
                    onOpen={() => openV2Detail("casos", row.opportunity_id)}
                    onOpenOrganization={(id) => openV2Detail("instituciones", id)}
                    role={<CaseRoleChips row={row} />}
                  />
                </li>
              ))}
            </ul>
          ) : null}
          {caseCoverage ? <p className="text-xs text-amber-800">{caseCoverage}</p> : null}
        </V2Panel>

        <V2Panel
          title="Cotizaciones en seguimiento"
          count={quotes.length}
          caption="Cruzadas por el identificador del caso, nunca por el nombre de la institución."
          testId="institution-360-quotes"
        >
          {followUpQuotes.error ? (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {followUpQuotes.error}
            </p>
          ) : null}
          {quotes.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">
              Ninguna. El histórico de cotizaciones de V1 todavía no se migra.
            </p>
          ) : (
            <ul className="space-y-1 text-sm text-slate-800">
              {quotes.map((quote) => (
                <li key={quote.quote_id}>
                  {quote.quote_number ?? "Sin número"} rev. {quote.revision_no} · {quote.status} ·
                  enviada {formatDate(quote.sent_at)}
                </li>
              ))}
            </ul>
          )}
          {quoteCoverage ? <p className="text-xs text-amber-800">{quoteCoverage}</p> : null}
        </V2Panel>
      </div>

      <V2Panel title="Unidades y dominios" testId="institution-360-structure">
        <div className="space-y-1 text-sm text-slate-800">
          <p>
            <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Depende de:{" "}
            </span>
            {card.parent_organization_name ? (
              <button
                type="button"
                onClick={() => openV2Detail("instituciones", card.parent_organization_id!)}
                className="underline decoration-dotted"
              >
                {card.parent_organization_name}
              </button>
            ) : (
              <span className="text-[var(--color-muted)]">ninguna</span>
            )}
          </p>
          <p>
            <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Unidades:{" "}
            </span>
            {card.child_organizations.length === 0 ? (
              <span className="text-[var(--color-muted)]">ninguna</span>
            ) : (
              card.child_organizations.map((child, index) => (
                <span key={child.organization_id}>
                  {index > 0 ? " · " : ""}
                  <button
                    type="button"
                    onClick={() => openV2Detail("instituciones", child.organization_id)}
                    className="underline decoration-dotted"
                  >
                    {child.name}
                  </button>
                </span>
              ))
            )}
          </p>
          <p>
            <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Dominios:{" "}
            </span>
            {card.domains.length === 0 ? (
              <span className="text-[var(--color-muted)]">ninguno registrado</span>
            ) : (
              card.domains.map((domain) => `${domain.domain} (${domain.scope})`).join(" · ")
            )}
          </p>
        </div>
      </V2Panel>

      <V2Panel title="Actividad" count={activityTotal} testId="institution-360-activity">
        {activity.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Sin actividad registrada.</p>
        ) : (
          <>
            <ul className="space-y-1 text-sm">
              {shownActivity.map((item) => (
                <li key={item.key} className="flex flex-wrap items-baseline gap-2">
                  <span className="w-24 shrink-0 text-xs text-[var(--color-muted)]">
                    {formatDate(item.when)}
                  </span>
                  <span className="text-slate-800">{item.headline}</span>
                  {item.pending ? <V2Chip tone="warn">sin revisar</V2Chip> : null}
                  {item.detail ? (
                    <span className="min-w-0 break-all text-[var(--color-muted)]">{item.detail}</span>
                  ) : null}
                </li>
              ))}
            </ul>
            {activity.length > ACTIVITY_SHOWN ? (
              <button
                type="button"
                onClick={() => setShowAllActivity((value) => !value)}
                className="text-xs text-slate-700 underline"
              >
                {showAllActivity ? "Ver menos" : `Ver las ${activity.length} cargadas`}
              </button>
            ) : null}
          </>
        )}
      </V2Panel>


      <V2TechnicalDetails>
        <dl className="space-y-1">
          <div>
            <dt className="inline font-medium">organization_id: </dt>
            <dd className="inline break-all">{card.organization_id}</dd>
          </div>
          <div>
            <dt className="inline font-medium">kind / confirmation / version: </dt>
            <dd className="inline">
              {card.kind} / {card.confirmation} / {card.version}
            </dd>
          </div>
          <div>
            <dt className="inline font-medium">procedencia: </dt>
            <dd className="inline break-all">
              {card.origin_source_kind ? sourceKindLabel(card.origin_source_kind) : "—"}
              {card.origin_source_uri ? ` · ${card.origin_source_uri}` : ""}
              {card.origin_review_status ? ` · ${card.origin_review_status}` : ""}
            </dd>
          </div>
          {card.note ? (
            <div>
              <dt className="inline font-medium">nota: </dt>
              <dd className="inline">{card.note}</dd>
            </div>
          ) : null}
        </dl>
        <div>
          <p className="font-medium">Relaciones (crm.organization_relationship)</p>
          {card.relationships.length === 0 ? (
            <p>Ninguna.</p>
          ) : (
            <ul className="list-disc pl-4">
              {card.relationships.map((relationship) => (
                <li key={relationship.organization_relationship_id}>
                  {relationship.role} · desde {formatDate(relationship.valid_from)}
                  {relationship.valid_to ? ` hasta ${formatDate(relationship.valid_to)}` : ""}
                  {relationship.note ? ` · ${relationship.note}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <p className="font-medium">Personas registradas</p>
          {card.people.length === 0 ? (
            <p>Ninguna. La evidencia migrada no trae nombres de personas.</p>
          ) : (
            <ul className="list-disc pl-4">
              {card.people.map((person) => (
                <li key={person.person_id}>
                  {person.display_name}
                  {person.role_title ? ` — ${person.role_title}` : ""} · {person.confirmation}
                </li>
              ))}
            </ul>
          )}
        </div>
        <p>
          Esta pantalla sólo lee. El proxy no admite ningún POST bajo <code>/v2</code>.
        </p>
      </V2TechnicalDetails>
    </div>
  );
}

export function Institution360Page() {
  const selected = useV2DetailId("instituciones");
  return selected ? <InstitutionDetail organizationId={selected} /> : <InstitutionList />;
}
