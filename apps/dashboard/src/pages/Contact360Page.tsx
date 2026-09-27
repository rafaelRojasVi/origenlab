/**
 * Contacto 360 — the operator's entry point into the V2 durable core.
 *
 * One person (or one pending channel) and everything OrigenLab knows about them in one
 * screen: how to reach them, which institution they belong to, which commercial cases and
 * quotes they are involved in, what may be sent to them, and what happened last.
 *
 * **A known address with no person is a pending channel, not an invented person.** That is
 * the first thing the screen says, because it is the state most of this data is actually
 * in and the easiest to misread as a name.
 *
 * **Read-only, structurally.** No command client is imported here and the dashboard-proxy
 * allows no POST under `/v2`. Nothing on this page decides anything; the ids, vocabularies
 * and provenance that would let someone reason about a future decision live in the closed
 * technical drawer at the bottom.
 */

import { useEffect, useMemo, useState } from "react";

import { fetchV2ContactCard, fetchV2Contacts } from "../api/v2Client";
import type {
  V2Contact,
  V2ContactCard,
  V2ContactIdentity,
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
  confirmationText,
  controlKindLabel,
  controlSourceLabel,
  contactChannels,
  contactIdentity,
  formatDate,
  marketingStance,
  missingChannelKinds,
  quotesForCases,
  relationCoverageNote,
  sourceKindLabel,
} from "../lib/crm360";
import { NO_ORGANIZATION_EXPLANATION, cappedListNote, cardCount, pageFooter } from "../lib/crmV2Browser";
import {
  CONTACT_IDENTITY_HINTS,
  CONTACT_IDENTITY_LABELS,
  activityKindLabel,
  connectedInterestHeadline,
  connectedQuoteLine,
  contactIdentityOf,
  organizationKindLabel,
  participantRoleLabel,
  withCardFacts,
} from "../lib/crmConnections";
import { caseRelationLabel, caseStageLabel } from "../lib/commercialCase";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import { closeV2Detail, openV2Detail, useV2DetailId } from "../lib/v2DeepLink";
import { useV2FollowUpQuotes } from "../lib/useV2FollowUpQuotes";
import { useV2OrganizationCases } from "../lib/useV2OrganizationCases";

const PAGE_SIZE = 50;
const ACTIVITY_SHOWN = 6;

const IDENTITY_TABS: readonly { id: V2ContactIdentity | "all"; label: string }[] = [
  { id: "all", label: "Todos" },
  { id: "person", label: CONTACT_IDENTITY_LABELS.person },
  { id: "organization_mailbox", label: CONTACT_IDENTITY_LABELS.organization_mailbox },
  { id: "unattributed", label: CONTACT_IDENTITY_LABELS.unattributed },
];

function IdentityChip({ identity }: { identity: V2ContactIdentity }) {
  const tone = identity === "person" ? "ok" : identity === "organization_mailbox" ? "neutral" : "warn";
  return <V2Chip tone={tone}>{CONTACT_IDENTITY_LABELS[identity]}</V2Chip>;
}

function ContactList() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [identity, setIdentity] = useState<V2ContactIdentity | "all">("all");
  const [withCases, setWithCases] = useState(false);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<V2Page<V2Contact> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchV2Contacts({
      q: submitted || undefined,
      identity: identity === "all" ? undefined : identity,
      withCases,
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
          setError(formatMirrorLoadError("Contactos", caught).message);
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
  }, [submitted, identity, withCases, offset]);

  const rows = page?.items ?? [];
  const narrowed = submitted !== "" || identity !== "all" || withCases;

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Contactos"
        subtitle="Direcciones del núcleo durable V2. Primero las que tienen una persona registrada, luego los buzones de una institución, al final las direcciones sin atribuir. Nada se atribuye por el dominio."
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
            placeholder="correo, persona o institución"
            className="mt-1 w-80 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
        >
          Buscar
        </button>
        <label className="flex items-center gap-2 pb-1 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={withCases}
            onChange={(event) => {
              setOffset(0);
              setWithCases(event.target.checked);
            }}
          />
          Sólo con participación en casos
        </label>
      </form>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Identidad registrada">
        {IDENTITY_TABS.map((tab) => {
          const on = identity === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              aria-pressed={on}
              onClick={() => {
                setOffset(0);
                setIdentity(tab.id);
              }}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                on
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"
              }`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      {identity !== "all" ? (
        <p className="text-xs text-[var(--color-muted)]">{CONTACT_IDENTITY_HINTS[identity]}</p>
      ) : null}

      {error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      ) : null}
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      {page && rows.length === 0 && !loading ? (
        <V2EmptyState
          title="Ningún contacto con estos filtros"
          description={
            narrowed
              ? "Nada registrado todavía cumple la búsqueda. Buscar por institución sólo encuentra direcciones registradas a su nombre, no las de su dominio."
              : "Todavía no hay contactos registrados en el núcleo durable."
          }
        />
      ) : null}

      {rows.length > 0 ? (
        <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 bg-[var(--color-card)]" data-testid="contact-360-table">
          {rows.map((row) => {
            const rowIdentity = contactIdentityOf(row);
            return (
              <li key={row.contact_point_id}>
                <button
                  type="button"
                  onClick={() => openV2Detail("contactos", row.contact_point_id)}
                  className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-left hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-500"
                >
                  <span className="min-w-0 flex-1 basis-64">
                    <span className="block break-all font-medium text-slate-900">
                      {row.person_display_name ?? row.address}
                    </span>
                    {row.person_display_name ? (
                      <span className="block break-all text-xs text-[var(--color-muted)]">
                        {row.address}
                      </span>
                    ) : null}
                  </span>
                  <span className="basis-44">
                    <IdentityChip identity={rowIdentity} />
                  </span>
                  <span className="min-w-0 basis-56 text-sm text-[var(--color-muted)]">
                    {row.organization_name ?? "Sin institución registrada"}
                  </span>
                  <span className="flex basis-48 flex-wrap gap-1 text-xs">
                    {row.case_count > 0 ? (
                      <V2Chip tone="ok">
                        {row.case_count === 1 ? "1 caso" : `${row.case_count} casos`}
                      </V2Chip>
                    ) : null}
                    {row.address_control_count > 0 ? (
                      <V2Chip>
                        {row.address_control_count === 1
                          ? "1 control"
                          : `${row.address_control_count} controles`}
                      </V2Chip>
                    ) : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      {page && page.total > 0 ? (
        <div className="flex items-center justify-between text-sm text-[var(--color-muted)]">
          <span data-testid="contact-360-footer">
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

/**
 * What this contact's institution is on this case — every part it holds.
 *
 * Read from the institution, and the line says so: this contact is not recorded as a
 * participant of anything. A person appears on a case through the institution they belong
 * to, and pretending otherwise would attach a commercial part to someone nobody named.
 */
function ContactCaseRole({ row }: { row: V2OrganizationCase }) {
  const { current, ended } = caseRoleLabels(row);
  if (current.length === 0 && ended.length === 0) {
    return <span className="text-[var(--color-muted)]">Sin papel anotado</span>;
  }
  return (
    <span className="flex flex-wrap gap-1" data-testid="contact-360-case-role">
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

function ContactDetail({ contactPointId }: { contactPointId: string }) {
  const [card, setCard] = useState<V2ContactCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const followUpQuotes = useV2FollowUpQuotes();
  const [showAllActivity, setShowAllActivity] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setCard(null);
    setError(null);
    fetchV2ContactCard(contactPointId)
      .then((loaded) => {
        if (!cancelled) {
          setCard(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(formatMirrorLoadError("Ficha de contacto", caught).message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [contactPointId]);

  const identity = useMemo(() => (card ? contactIdentity(card) : null), [card]);
  const channels = useMemo(() => (card ? contactChannels(card) : []), [card]);
  const stance = useMemo(() => (card ? marketingStance(card.address_controls) : null), [card]);
  const activity = useMemo(() => (card ? activityFromEvidence(card.evidence) : []), [card]);
  /*
    The institution's cases, asked for by institution on the server — every case it is
    part of, not only the ones it is asking in. A contact of a supplier institution was
    previously shown no cases at all, which read as "nothing is happening" when in fact
    that institution was named on every one of them.
  */
  const orgCases = useV2OrganizationCases(card?.organization_id ?? null);
  const cases = orgCases.cases;
  const quotes = useMemo(
    () => quotesForCases(followUpQuotes.quotes, cases),
    [followUpQuotes.quotes, cases],
  );
  const caseCoverage = relationCoverageNote(cases.length, orgCases.total);

  const back = (
    <button
      type="button"
      onClick={() => closeV2Detail("contactos")}
      className="rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700"
    >
      ← Contactos
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

  if (!card || !identity || !stance) {
    return (
      <div className="space-y-4">
        {back}
        <p className="text-sm text-[var(--color-muted)]">Cargando el contacto…</p>
      </div>
    );
  }

  const missing = missingChannelKinds(channels);
  const recordedIdentity = contactIdentityOf(card);
  const summary = card.connection_summary;
  const shownActivity = showAllActivity ? activity : activity.slice(0, ACTIVITY_SHOWN);
  const activityTotal = cardCount(card.counts, "evidence", activity.length);

  return (
    <div className="space-y-4" data-testid="contact-360-detail">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {back}
        {card.organization_id ? (
          <button
            type="button"
            onClick={() => openV2Detail("instituciones", card.organization_id!)}
            className="rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700"
          >
            Ver institución →
          </button>
        ) : null}
      </div>

      <header
        className={`rounded-xl border p-4 ${
          identity.kind === "pending_channel"
            ? "border-amber-200 bg-amber-50"
            : "border-slate-200 bg-[var(--color-card)]"
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <IdentityChip identity={recordedIdentity} />
          {identity.kind === "pending_channel" ? (
            <p className="text-xs font-semibold uppercase tracking-wide text-amber-800">
              Nadie identificado todavía
            </p>
          ) : null}
        </div>
        <h2 className="mt-1 break-all text-lg font-semibold text-slate-900">{identity.title}</h2>
        <p className="mt-1 text-sm text-[var(--color-muted)]">{identity.qualifier}</p>
        {identity.note ? (
          <p className="mt-2 text-sm text-amber-900" data-testid="contact-360-pending-note">
            {identity.note}
          </p>
        ) : null}
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <V2Panel title="Cómo contactarle" testId="contact-360-channels">
          <ul className="space-y-1 text-sm">
            {channels.map((channel) => (
              <li key={channel.contactPointId} className="flex flex-wrap items-baseline gap-2">
                <span className="w-20 shrink-0 text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  {channel.kindLabel}
                </span>
                <span className="min-w-0 break-all text-slate-800">{channel.address}</span>
                {channel.isPrimary ? <V2Chip>abierto</V2Chip> : null}
              </li>
            ))}
          </ul>
          {missing.length > 0 ? (
            <p className="text-xs text-[var(--color-muted)]">
              Sin {missing.join(" ni ").toLowerCase()} registrado.
            </p>
          ) : null}
        </V2Panel>

        <V2Panel title="Institución" testId="contact-360-organization">
          {card.organization_name ? (
            <div className="space-y-1">
              <button
                type="button"
                onClick={() => openV2Detail("instituciones", card.organization_id!)}
                className="text-left text-sm font-medium text-slate-900 underline decoration-dotted"
              >
                {card.organization_name}
              </button>
              <p className="text-xs text-[var(--color-muted)]">
                {organizationKindLabel(card.organization_kind ?? "unknown")}
              </p>
              {card.affiliations.length > 0 ? (
                <ul className="mt-1 space-y-0.5 text-sm text-slate-800">
                  {card.affiliations.map((affiliation) => (
                    <li key={affiliation.affiliation_id}>
                      {affiliation.organization_name}
                      {affiliation.role_title ? ` — ${affiliation.role_title}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-[var(--color-muted)]">{NO_ORGANIZATION_EXPLANATION}</p>
          )}
        </V2Panel>
      </div>

      <V2Panel
        title="Casos en los que participa"
        count={summary.cases}
        caption="Por una participación registrada de esta dirección o de su persona. Es el papel de la persona, no el de su institución."
        testId="contact-360-cases"
      >
        {card.cases.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            No hay participación registrada todavía para este contacto.
            {card.organization_id
              ? " Los casos de su institución se listan abajo por separado."
              : ""}
          </p>
        ) : (
          <ul className="space-y-2">
            {card.cases.map((row) => (
              <li
                key={row.opportunity_id}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm"
              >
                <button
                  type="button"
                  onClick={() => openV2Detail("casos", row.opportunity_id)}
                  className="min-w-0 text-left font-medium text-slate-900 underline decoration-dotted"
                >
                  {row.title}
                </button>
                <V2Chip tone={row.closed_at ? "neutral" : "ok"}>{caseStageLabel(row.stage)}</V2Chip>
                {row.roles.map((role) => (
                  <V2Chip key={role}>{participantRoleLabel(role)}</V2Chip>
                ))}
                <span className="text-xs text-[var(--color-muted)]">
                  Pide:{" "}
                  {row.requesting_organization_name ?? "no registrado todavía"}
                </span>
              </li>
            ))}
          </ul>
        )}
        {cappedListNote(card.cases.length, summary.cases) ? (
          <p className="text-xs text-[var(--color-muted)]">
            {cappedListNote(card.cases.length, summary.cases)}
          </p>
        ) : null}
      </V2Panel>

      {card.organization_id ? (
        <V2Panel
          title="Casos de su institución"
          count={orgCases.total}
          caption="Todos los casos en los que su institución tiene un papel, con el papel exacto. Que la institución participe no significa que esta dirección lo haga."
          testId="contact-360-organization-cases"
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
              Su institución no tiene todavía un papel registrado en ningún caso.
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
                    role={<ContactCaseRole row={row} />}
                  />
                </li>
              ))}
            </ul>
          ) : null}
          {caseCoverage ? <p className="text-xs text-amber-800">{caseCoverage}</p> : null}
        </V2Panel>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <V2Panel
          title="Equipos e intereses"
          count={summary.interests}
          caption="Lo que buscan los casos en los que participa."
          testId="contact-360-interests"
        >
          {card.interests.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">
              {summary.cases > 0
                ? "Sus casos todavía no registran qué equipo buscan."
                : "Sin participación registrada todavía, no hay intereses que mostrar."}
            </p>
          ) : (
            <ul className="space-y-1 text-sm">
              {card.interests.map((interest) => (
                <li key={interest.opportunity_interest_id} className="flex flex-wrap items-baseline gap-2">
                  <span className="font-medium text-slate-900">
                    {connectedInterestHeadline(interest)}
                  </span>
                  <span className="text-xs text-[var(--color-muted)]">{interest.opportunity_title}</span>
                </li>
              ))}
            </ul>
          )}
        </V2Panel>

        <V2Panel
          title="Cotizaciones"
          count={summary.quotes}
          caption="De los casos en los que participa. Última revisión de cada una."
          testId="contact-360-quotes"
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
                  <span className="text-xs text-[var(--color-muted)]">{quote.opportunity_title}</span>
                </li>
              ))}
            </ul>
          )}
        </V2Panel>
      </div>

      <V2Panel title="Marketing" testId="contact-360-marketing">
        <div className="flex flex-wrap items-center gap-2">
          <V2Chip tone={stance.tone}>{stance.headline}</V2Chip>
        </div>
        <p className="text-xs text-[var(--color-muted)]">{stance.detail}</p>
        <p className="text-xs text-[var(--color-muted)]">
          El estado es de la <strong>dirección</strong>, no de la persona: sigue vigente
          aunque el titular cambie o nunca se registre.
        </p>
        {card.address_controls.length > 0 ? (
          <ul className="space-y-0.5 text-sm text-slate-800" data-testid="contact-360-controls">
            {card.address_controls.map((control, index) => (
              <li key={`${control.source}-${index}`}>
                {controlKindLabel(control.control_kind)} · {control.purpose} ·{" "}
                <span className="text-[var(--color-muted)]">
                  {controlSourceLabel(control.source)}
                  {control.until_at ? ` · hasta ${formatDate(control.until_at)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">Ningún control registrado sobre esta dirección.</p>
        )}
        {card.marketing.length > 0 ? (
          <ul className="mt-1 space-y-0.5 text-sm text-slate-800">
            {card.marketing.map((entry, index) => (
              <li key={`${entry.campaign_name}-${index}`}>
                {entry.campaign_name} · {entry.recipient_state} · {entry.attempt_count} intento(s)
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            No figura en ninguna campaña registrada en V2.
          </p>
        )}
      </V2Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <V2Panel
          title="Actividad comercial"
          count={summary.activities}
          caption="Registrada en los casos en los que participa."
          testId="contact-360-activity"
        >
          {card.activities.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">Ninguna registrada todavía.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {card.activities.map((item) => (
                <li key={item.activity_id} className="flex flex-wrap items-baseline gap-2">
                  <span className="w-24 shrink-0 text-xs text-[var(--color-muted)]">
                    {formatDate(item.occurred_at)}
                  </span>
                  <V2Chip>{activityKindLabel(item.kind)}</V2Chip>
                  <span className="text-slate-800">{item.summary ?? "Sin resumen"}</span>
                  <span className="text-xs text-[var(--color-muted)]">{item.opportunity_title}</span>
                </li>
              ))}
            </ul>
          )}
        </V2Panel>

        <V2Panel
          title="Evidencia"
          count={activityTotal + summary.case_evidence}
          caption="Dónde se observó esta dirección, y los documentos de sus casos."
          testId="contact-360-evidence"
        >
          {activity.length === 0 && card.case_evidence.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">Ninguna registrada todavía.</p>
          ) : null}
          {activity.length > 0 ? (
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
              {cappedListNote(activity.length, activityTotal) ? (
                <p className="text-xs text-[var(--color-muted)]">
                  {cappedListNote(activity.length, activityTotal)}
                </p>
              ) : null}
            </>
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
                </li>
              ))}
            </ul>
          ) : null}
        </V2Panel>
      </div>

      <V2TechnicalDetails>
        <dl className="space-y-1">
          <div>
            <dt className="inline font-medium">contact_point_id: </dt>
            <dd className="inline break-all">{card.contact_point_id}</dd>
          </div>
          <div>
            <dt className="inline font-medium">usage / confirmation: </dt>
            <dd className="inline">
              {card.usage} / {card.confirmation} ({confirmationText(card.confirmation)})
            </dd>
          </div>
          <div>
            <dt className="inline font-medium">organization_id: </dt>
            <dd className="inline break-all">{card.organization_id ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">person_id: </dt>
            <dd className="inline break-all">{card.person_id ?? "—"}</dd>
          </div>
          <div>
            <dt className="inline font-medium">procedencia: </dt>
            <dd className="inline break-all">
              {card.origin_source_kind ? sourceKindLabel(card.origin_source_kind) : "—"}
              {card.origin_source_uri ? ` · ${card.origin_source_uri}` : ""}
              {card.origin_review_status ? ` · ${card.origin_review_status}` : ""}
            </dd>
          </div>
        </dl>
        <div>
          <p className="font-medium">Controles sobre la dirección</p>
          {card.address_controls.length === 0 ? (
            <p>Ninguno.</p>
          ) : (
            <ul className="list-disc pl-4">
              {card.address_controls.map((control, index) => (
                <li key={`${control.source}-${index}`}>
                  {control.control_kind} · {control.purpose} · {control.scope} · {control.source}
                  {control.until_at ? ` · hasta ${formatDate(control.until_at)}` : ""}
                  {control.reason ? ` · ${control.reason}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <p className="font-medium">Afirmaciones y procedencia</p>
          {card.evidence.length === 0 ? (
            <p>Ninguna.</p>
          ) : (
            <ul className="list-disc pl-4">
              {card.evidence.map((row) => (
                <li key={row.assertion_id} className="break-all">
                  {row.kind} = {row.value_norm} · {row.resolution} ·{" "}
                  {sourceKindLabel(row.source_kind)}
                  {row.source_uri ? ` · ${row.source_uri}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
        <p>
          Esta pantalla sólo lee. Los comandos del núcleo durable V2 existen en{" "}
          <code>apps/api</code> y el proxy no admite ningún POST bajo <code>/v2</code>.
        </p>
      </V2TechnicalDetails>
    </div>
  );
}

export function Contact360Page() {
  const selected = useV2DetailId("contactos");
  return selected ? <ContactDetail contactPointId={selected} /> : <ContactList />;
}
