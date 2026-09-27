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
  V2OrganizationFilter,
  V2OrganizationCardChannel,
  V2OrganizationCase,
  V2OrganizationSegment,
  V2OrganizationsPage,
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
  controlKindLabel,
  controlSourceLabel,
  formatDate,
  institutionRoles,
  organizationRelationshipLabel,
  quotesForCases,
  relationCoverageNote,
  sourceKindLabel,
  splitInstitutionChannels,
  usageLabel,
} from "../lib/crm360";
import { cappedListNote, cardCount, pageFooter } from "../lib/crmV2Browser";
import { caseRelationLabel } from "../lib/commercialCase";
import {
  activityKindLabel,
  connectedInterestHeadline,
  connectedQuoteLine,
  DEFAULT_ORGANIZATION_SEGMENT,
  ORGANIZATION_FILTER_LABELS,
  ORGANIZATION_FILTER_ORDER,
  ORGANIZATION_SEGMENT_ORDER,
  organizationRoleCounts,
  organizationSegmentHint,
  organizationSegmentLabel,
  RECENT_ACTIVITY_WINDOWS,
  organizationInitials,
  organizationKindLabel,
  withCardFacts,
} from "../lib/crmConnections";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import { closeV2Detail, openV2Detail, useV2DetailId } from "../lib/v2DeepLink";
import { useV2FollowUpQuotes } from "../lib/useV2FollowUpQuotes";
import { useV2OrganizationCases } from "../lib/useV2OrganizationCases";

const PAGE_SIZE = 50;
const ACTIVITY_SHOWN = 6;

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-[var(--color-muted)]">{label}</dt>
      <dd
        className={`text-sm font-semibold ${value > 0 ? "text-slate-900" : "text-slate-400"}`}
      >
        {value.toLocaleString("es-CL")}
      </dd>
    </div>
  );
}

/**
 * The avatar is the recorded name's initials. There is no logo column in the durable core
 * and a logo fetched by domain would be an identity inference, so there is no image.
 */
function OrganizationAvatar({ name, confirmed }: { name: string; confirmed: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-semibold ${
        confirmed ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"
      }`}
    >
      {organizationInitials(name)}
    </span>
  );
}

/**
 * What the institution is on cases, one count per part, beside the total. Kept as its own
 * line so a supplier's cases never read as cases it asked for.
 */
function OrganizationRoleCounts({ row }: { row: V2Organization }) {
  return (
    <div data-testid="institution-card-roles">
      <p className="text-[11px] uppercase tracking-wide text-[var(--color-muted)]">
        Papel en casos
      </p>
      <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {organizationRoleCounts(row).map((item) => (
          <div key={item.key} className="flex items-baseline gap-1">
            <dt className="text-xs text-[var(--color-muted)]">{item.label}</dt>
            <dd
              className={`font-semibold ${item.count > 0 ? "text-slate-900" : "text-slate-400"}`}
            >
              {item.count.toLocaleString("es-CL")}
            </dd>
          </div>
        ))}
        <div className="flex items-baseline gap-1 border-l border-slate-200 pl-4">
          <dt className="text-xs text-[var(--color-muted)]">Total casos</dt>
          <dd className="font-semibold text-slate-900">
            {row.case_count.toLocaleString("es-CL")}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function OrganizationListCard({ row }: { row: V2Organization }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => openV2Detail("instituciones", row.organization_id)}
        className="flex h-full w-full flex-col gap-3 rounded-xl border border-slate-200 bg-[var(--color-card)] p-4 text-left transition hover:border-slate-400 hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-500"
        data-testid="institution-card"
      >
        <span className="flex items-start gap-3">
          <OrganizationAvatar name={row.name} confirmed={row.confirmation === "confirmed"} />
          <span className="min-w-0 flex-1">
            <span className="block break-words font-semibold text-slate-900">{row.name}</span>
            <span className="mt-1 flex flex-wrap gap-1">
              <V2Chip tone={row.kind === "unknown" ? "warn" : "neutral"}>
                {organizationKindLabel(row.kind)}
              </V2Chip>
              <V2Chip tone={row.confirmation === "confirmed" ? "ok" : "neutral"}>
                {row.confirmation === "confirmed" ? "Confirmada" : "Propuesta de máquina"}
              </V2Chip>
              {row.open_case_count > 0 ? (
                <V2Chip tone="ok">
                  {row.open_case_count === 1
                    ? "1 caso abierto"
                    : `${row.open_case_count} casos abiertos`}
                </V2Chip>
              ) : null}
              {row.relationship_roles.map((role) => (
                <V2Chip key={role} tone="neutral">
                  Registrada como {organizationRelationshipLabel(role).toLowerCase()}
                </V2Chip>
              ))}
            </span>
          </span>
        </span>
        <OrganizationRoleCounts row={row} />
        <dl className="grid grid-cols-4 gap-x-3 gap-y-2 sm:grid-cols-7">
          <Metric label="Contactos" value={row.contact_point_count} />
          <Metric label="Personas" value={row.confirmed_people_count} />
          <Metric label="Casos" value={row.case_count} />
          <Metric label="Abiertos" value={row.open_case_count} />
          <Metric label="Intereses" value={row.interest_count} />
          <Metric label="Cotiz." value={row.quote_count} />
          <Metric label="Activ." value={row.activity_count} />
        </dl>
        <span className="text-xs text-[var(--color-muted)]">
          Última actividad:{" "}
          {row.last_activity_at ? (
            <span className="text-slate-800">{formatDate(row.last_activity_at)}</span>
          ) : (
            "no registrada todavía"
          )}
        </span>
      </button>
    </li>
  );
}

function InstitutionList() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [filters, setFilters] = useState<V2OrganizationFilter[]>([]);
  const [activeWithinDays, setActiveWithinDays] = useState<number | null>(null);
  const [segment, setSegment] = useState<V2OrganizationSegment | null>(
    DEFAULT_ORGANIZATION_SEGMENT,
  );
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<V2OrganizationsPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchV2Organizations({
      q: submitted || undefined,
      has: filters,
      activeWithinDays: activeWithinDays ?? undefined,
      segment: segment ?? undefined,
      limit: PAGE_SIZE,
      offset,
    })
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
  }, [submitted, filters, activeWithinDays, segment, offset]);

  const rows = page?.items ?? [];
  const narrowed = submitted !== "" || filters.length > 0 || activeWithinDays !== null;

  const toggle = (filter: V2OrganizationFilter) => {
    setOffset(0);
    setFilters((current) =>
      current.includes(filter) ? current.filter((f) => f !== filter) : [...current, filter],
    );
  };

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Instituciones"
        subtitle="Primero las que piden equipos a OrigenLab. Proveedores y fabricantes van aparte: aparecer en un caso no los vuelve clientes. Nada se cruza por nombre ni por dominio."
      />

      <div
        className="flex flex-wrap gap-1 border-b border-slate-200"
        role="tablist"
        aria-label="Papel comercial"
        data-testid="institution-segments"
      >
        {ORGANIZATION_SEGMENT_ORDER.map((option) => {
          const selected = option === segment;
          const count = page?.facets ? page.facets[option ?? "all"] : null;
          return (
            <button
              key={option ?? "all"}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => {
                setOffset(0);
                setSegment(option);
              }}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
                selected
                  ? "border-slate-900 text-slate-900"
                  : "border-transparent text-slate-600 hover:text-slate-900"
              }`}
            >
              {organizationSegmentLabel(option)}
              {count !== null ? (
                <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 text-xs text-slate-700">
                  {count.toLocaleString("es-CL")}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-[var(--color-muted)]" data-testid="institution-segment-hint">
        {organizationSegmentHint(segment)}
      </p>

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setOffset(0);
          setSubmitted(query.trim());
        }}
      >
        <label className="flex flex-col text-xs text-[var(--color-muted)]">
          Buscar por nombre
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="p. ej. universidad"
            className="mt-1 w-72 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
        >
          Buscar
        </button>
        <label className="flex flex-col text-xs text-[var(--color-muted)]">
          Actividad reciente
          <select
            value={activeWithinDays ?? ""}
            onChange={(event) => {
              setOffset(0);
              setActiveWithinDays(event.target.value ? Number(event.target.value) : null);
            }}
            className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
            data-testid="institution-activity-filter"
          >
            <option value="">Cualquiera</option>
            {RECENT_ACTIVITY_WINDOWS.map((days) => (
              <option key={days} value={days}>
                Últimos {days} días
              </option>
            ))}
          </select>
        </label>
      </form>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Filtros">
        {ORGANIZATION_FILTER_ORDER.map((filter) => {
          const on = filters.includes(filter);
          return (
            <button
              key={filter}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(filter)}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                on
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"
              }`}
            >
              {ORGANIZATION_FILTER_LABELS[filter]}
            </button>
          );
        })}
        {narrowed ? (
          <button
            type="button"
            onClick={() => {
              setOffset(0);
              setFilters([]);
              setActiveWithinDays(null);
              setQuery("");
              setSubmitted("");
            }}
            className="px-2 py-1 text-xs text-slate-600 underline"
          >
            Limpiar filtros
          </button>
        ) : null}
      </div>

      {error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      ) : null}
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      {page && rows.length === 0 && !loading ? (
        <V2EmptyState
          title={
            segment && !narrowed
              ? `Sin ${organizationSegmentLabel(segment).toLowerCase()} registradas todavía`
              : "Ninguna institución con estos filtros"
          }
          description={
            narrowed
              ? "Nada registrado todavía cumple la combinación elegida. Que no esté registrado no significa que no exista."
              : segment
                ? "Ninguna institución tiene todavía ese papel en un caso ni esa relación registrada. Las demás siguen en «Todas»."
                : "Todavía no hay instituciones registradas en el núcleo durable."
          }
        />
      ) : null}

      {rows.length > 0 ? (
        <ul className="grid gap-3 xl:grid-cols-2" data-testid="institution-360-table">
          {rows.map((row) => (
            <OrganizationListCard key={row.organization_id} row={row} />
          ))}
        </ul>
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
  const summary = card.connection_summary;
  const peopleTotal = cardCount(card.counts, "people", card.people.length);
  const caseTitles = new Map(card.cases.map((row) => [row.opportunity_id, row.title]));
  const shownActivity = showAllActivity ? activity : activity.slice(0, ACTIVITY_SHOWN);
  const activityTotal = cardCount(card.counts, "evidence", activity.length);

  return (
    <div className="space-y-4" data-testid="institution-360-detail">
      {back}

      <header className="rounded-xl border border-slate-200 bg-[var(--color-card)] p-4">
        <div className="flex items-start gap-3">
          <OrganizationAvatar name={card.name} confirmed={card.confirmation === "confirmed"} />
          <div className="min-w-0">
            <h2 className="break-words text-lg font-semibold text-slate-900">{card.name}</h2>
            {card.legal_name && card.legal_name !== card.name ? (
              <p className="text-sm text-[var(--color-muted)]">{card.legal_name}</p>
            ) : null}
            <div className="mt-2 flex flex-wrap gap-2">
              <V2Chip tone={card.kind === "unknown" ? "warn" : "neutral"}>
                {organizationKindLabel(card.kind)}
              </V2Chip>
              <V2Chip tone={card.confirmation === "confirmed" ? "ok" : "warn"}>
                {confirmationText(card.confirmation)}
              </V2Chip>
            </div>
          </div>
        </div>
        <dl className="mt-3 grid grid-cols-4 gap-x-3 gap-y-2 sm:grid-cols-8" data-testid="institution-360-summary">
          <Metric label="Contactos" value={channelTotal} />
          <Metric label="Personas" value={summary.confirmed_people ?? card.people.length} />
          <Metric label="Casos" value={summary.cases} />
          <Metric label="Abiertos" value={summary.open_cases} />
          <Metric label="Intereses" value={summary.interests} />
          <Metric label="Cotiz." value={summary.quotes} />
          <Metric label="Activ." value={summary.activities} />
          {/* The same total the Evidencia section counts: its cases' documents plus what
              was observed about the institution itself. */}
          <Metric label="Evidencia" value={activityTotal + summary.case_evidence} />
        </dl>
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          Última actividad:{" "}
          {summary.last_activity_at ? (
            <span className="text-slate-800">{formatDate(summary.last_activity_at)}</span>
          ) : (
            "no registrada todavía"
          )}
        </p>
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

      <V2Panel
        title="Personas y afiliaciones"
        count={peopleTotal}
        caption="Personas registradas en esta institución por una afiliación. Una dirección de su dominio no es una afiliación."
        testId="institution-360-people"
      >
        {card.people.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            Ninguna persona registrada todavía. La evidencia migrada no trae nombres, y no se
            deducen de las direcciones.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.people.map((person) => (
              <li
                key={`${person.person_id}-${person.valid_from ?? ""}`}
                className="flex flex-wrap items-baseline gap-2"
              >
                <span className="font-medium text-slate-900">{person.display_name}</span>
                {person.role_title ? (
                  <span className="text-[var(--color-muted)]">{person.role_title}</span>
                ) : null}
                {person.unit_label ? (
                  <span className="text-[var(--color-muted)]">· {person.unit_label}</span>
                ) : null}
                <V2Chip tone={person.confirmation === "confirmed" ? "ok" : "warn"}>
                  {person.confirmation === "confirmed" ? "afiliación confirmada" : "afiliación propuesta"}
                </V2Chip>
                {person.valid_to ? (
                  <V2Chip tone="neutral">terminó {formatDate(person.valid_to)}</V2Chip>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {cappedListNote(card.people.length, peopleTotal) ? (
          <p className="text-xs text-[var(--color-muted)]">
            {cappedListNote(card.people.length, peopleTotal)}
          </p>
        ) : null}
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
                    row={withCardFacts(row, card)}
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
          title="Cotizaciones"
          count={summary.quotes}
          caption="De sus casos, por el identificador del caso. Se muestra la última revisión de cada una."
          testId="institution-360-quotes"
        >
          {card.quotes.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">
              Ninguna registrada todavía en V2. El histórico de cotizaciones de V1 aún no se
              migra, así que esto no significa que nunca se le haya cotizado.
            </p>
          ) : (
            <ul className="space-y-1 text-sm text-slate-800">
              {card.quotes.map((quote) => (
                <li key={quote.quote_id} className="flex flex-wrap items-baseline gap-2">
                  <span>{connectedQuoteLine(quote)}</span>
                  <button
                    type="button"
                    onClick={() => openV2Detail("casos", quote.opportunity_id)}
                    className="text-xs text-[var(--color-muted)] underline decoration-dotted"
                  >
                    {quote.opportunity_title}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {cappedListNote(card.quotes.length, summary.quotes) ? (
            <p className="text-xs text-[var(--color-muted)]">
              {cappedListNote(card.quotes.length, summary.quotes)}
            </p>
          ) : null}
        </V2Panel>
      </div>

      <V2Panel
        title="Equipos e intereses solicitados"
        count={summary.interests}
        caption="Lo que buscan sus casos, sin los intereses retirados."
        testId="institution-360-interests"
      >
        {card.interests.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            {summary.cases > 0
              ? "Sus casos todavía no registran qué equipo buscan."
              : "Sin casos registrados todavía, no hay intereses que mostrar."}
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {card.interests.map((interest) => (
              <li key={interest.opportunity_interest_id} className="flex flex-wrap items-baseline gap-2">
                <span className="font-medium text-slate-900">
                  {connectedInterestHeadline(interest)}
                </span>
                {interest.quantity !== null ? (
                  <span className="text-[var(--color-muted)]">
                    × {interest.quantity}
                    {interest.quantity_unit ? ` ${interest.quantity_unit}` : ""}
                  </span>
                ) : null}
                {interest.confirmation !== "confirmed" ? (
                  <V2Chip tone="warn">propuesta</V2Chip>
                ) : null}
                <button
                  type="button"
                  onClick={() => openV2Detail("casos", interest.opportunity_id)}
                  className="text-xs text-[var(--color-muted)] underline decoration-dotted"
                >
                  {interest.opportunity_title}
                </button>
              </li>
            ))}
          </ul>
        )}
        {cappedListNote(card.interests.length, summary.interests) ? (
          <p className="text-xs text-[var(--color-muted)]">
            {cappedListNote(card.interests.length, summary.interests)}
          </p>
        ) : null}
      </V2Panel>

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

      <div className="grid gap-4 lg:grid-cols-2">
        <V2Panel
          title="Actividad comercial"
          count={summary.activities}
          caption="Llamadas, reuniones, notas y correos registrados en sus casos."
          testId="institution-360-activity"
        >
          {card.activities.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">
              Ninguna actividad registrada todavía en sus casos.
            </p>
          ) : (
            <ul className="space-y-1 text-sm">
              {card.activities.map((item) => (
                <li key={item.activity_id} className="flex flex-wrap items-baseline gap-2">
                  <span className="w-24 shrink-0 text-xs text-[var(--color-muted)]">
                    {formatDate(item.occurred_at)}
                  </span>
                  <V2Chip>{activityKindLabel(item.kind)}</V2Chip>
                  <span className="text-slate-800">{item.summary ?? "Sin resumen"}</span>
                  <span className="text-xs text-[var(--color-muted)]">
                    {item.opportunity_title}
                    {item.recorded_by_display_name ? ` · ${item.recorded_by_display_name}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {cappedListNote(card.activities.length, summary.activities) ? (
            <p className="text-xs text-[var(--color-muted)]">
              {cappedListNote(card.activities.length, summary.activities)}
            </p>
          ) : null}
        </V2Panel>

        <V2Panel
          title="Evidencia"
          count={activityTotal + summary.case_evidence}
          caption="Documentos vinculados a sus casos, y lo que se observó sobre la institución misma."
          testId="institution-360-evidence"
        >
          {card.case_evidence.length === 0 && activity.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">Ninguna registrada todavía.</p>
          ) : null}
          {card.case_evidence.length > 0 ? (
            <ul className="space-y-1 text-sm">
              {card.case_evidence.map((link) => (
                <li key={link.opportunity_evidence_id} className="flex flex-wrap items-baseline gap-2">
                  <span className="w-24 shrink-0 text-xs text-[var(--color-muted)]">
                    {formatDate(link.linked_at)}
                  </span>
                  <V2Chip tone={link.relation === "contradicts" ? "danger" : "neutral"}>
                    {caseRelationLabel(link.relation)}
                  </V2Chip>
                  <span className="text-slate-800">
                    {link.source_kind ? sourceKindLabel(link.source_kind) : "Documento"}
                  </span>
                  <button
                    type="button"
                    onClick={() => openV2Detail("casos", link.opportunity_id)}
                    className="text-xs text-[var(--color-muted)] underline decoration-dotted"
                  >
                    {caseTitles.get(link.opportunity_id) ?? "abrir caso"}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {activity.length > 0 ? (
            <>
              <p className="pt-1 text-xs uppercase tracking-wide text-[var(--color-muted)]">
                Observaciones sobre la institución
              </p>
              <ul className="space-y-1 text-sm">
                {shownActivity.map((item) => (
                  <li key={item.key} className="flex flex-wrap items-baseline gap-2">
                    <span className="w-24 shrink-0 text-xs text-[var(--color-muted)]">
                      {formatDate(item.when)}
                    </span>
                    <span className="text-slate-800">{item.headline}</span>
                    {item.pending ? <V2Chip tone="warn">sin revisar</V2Chip> : null}
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
          ) : null}
        </V2Panel>
      </div>

      <V2Panel
        title="Marketing"
        caption="Controles sobre sus direcciones y sus dominios registrados. Un control es de la dirección, no de la persona; y no existe un «permitido»: sólo bloqueos, esperas y contacto previo."
        testId="institution-360-marketing"
      >
        <div className="grid gap-3 text-sm md:grid-cols-3">
          <div>
            <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Direcciones con control ({cardCount(card.counts, "address_controls", card.address_controls.length).toLocaleString("es-CL")})
            </p>
            {card.address_controls.length === 0 ? (
              <p className="text-[var(--color-muted)]">Ninguno registrado.</p>
            ) : (
              <ul className="space-y-0.5">
                {card.address_controls.map((control, index) => (
                  <li key={`${control.value_norm}-${index}`} className="break-all">
                    <span className="text-slate-800">{control.value_norm}</span>{" "}
                    <span className="text-[var(--color-muted)]">
                      · {controlKindLabel(control.control_kind)} · {controlSourceLabel(control.source)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Dominios con control ({cardCount(card.counts, "domain_controls", card.domain_controls.length).toLocaleString("es-CL")})
            </p>
            {card.domain_controls.length === 0 ? (
              <p className="text-[var(--color-muted)]">
                {card.domains.length === 0
                  ? "Sin dominios registrados, así que ningún control de dominio le corresponde."
                  : "Ninguno registrado."}
              </p>
            ) : (
              <ul className="space-y-0.5">
                {card.domain_controls.map((control, index) => (
                  <li key={`${control.value_norm}-${index}`}>
                    <span className="text-slate-800">{control.value_norm}</span>{" "}
                    <span className="text-[var(--color-muted)]">
                      · {controlKindLabel(control.control_kind)} · {controlSourceLabel(control.source)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Campañas ({cardCount(card.counts, "marketing", card.marketing.length).toLocaleString("es-CL")})
            </p>
            {card.marketing.length === 0 ? (
              <p className="text-[var(--color-muted)]">
                Ninguno de sus contactos registrados está en una campaña.
              </p>
            ) : (
              <ul className="space-y-0.5">
                {card.marketing.map((entry, index) => (
                  <li key={`${entry.contact_point_id}-${index}`} className="break-all">
                    <span className="text-slate-800">{entry.campaign_name}</span>{" "}
                    <span className="text-[var(--color-muted)]">
                      · {entry.address ?? "—"} · {entry.recipient_state} · {entry.attempt_count} intento(s)
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
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
