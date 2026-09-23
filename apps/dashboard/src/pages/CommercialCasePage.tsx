/**
 * Casos comerciales — a case read as a case, not as three tables.
 *
 * A case is where this system stops describing correspondence and starts describing
 * business: who is asking, what they are asking for, from whom, and what happened last.
 * The list is six-line summary cards; opening one adds the institutions, what it seeks and
 * the documents behind it, each linking out to Contacto 360 and Institución 360.
 *
 * **Everything that explains the machinery is in one closed drawer.** The six commands and
 * their blockers, the request bodies, "qué registraría", the stage machine and the raw ids
 * are all true and none of them is what an operator opened a case to read. They live under
 * «Detalles técnicos», collapsed, so the surface above can stay the four business questions.
 *
 * **Read-only, structurally.** No command client is imported and the dashboard-proxy allows
 * no POST under `/v2`. The six case commands exist upstream; their affordances render
 * disabled with the reason attached, inside the drawer, because a button that looked live
 * and did nothing would be worse than no button and one that worked would be a second
 * writer into durable truth.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchV2CaseCard, fetchV2Cases, fetchV2QuotesToFollowUp } from "../api/v2Client";
import type {
  V2CaseEvidence,
  V2CaseInterest,
  V2CaseOrganization,
  V2CommercialCase,
  V2CommercialCaseCard,
  V2Page,
  V2Quote,
} from "../api/v2Types";
import { V2Chip } from "../components/v2/V2Chip";
import { V2CaseSummaryCard } from "../components/v2/V2CaseSummaryCard";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2Panel } from "../components/v2/V2Panel";
import { V2TechnicalDetails } from "../components/v2/V2TechnicalDetails";
import {
  CASE_PREVIEW_ONLY_REASON,
  caseCommandPreviews,
  emptyCaseCommandContext,
  type CaseCommandPreview,
} from "../lib/caseCommands";
import {
  CASE_SUBJECT_LABELS,
  caseGaps,
  caseRelationLabel,
  caseRoleLabel,
  caseStageLabel,
  caseSummary,
  currentOrganizations,
  historicalOrganizations,
  interestHeadline,
  interestQuantity,
  linkedEvidence,
} from "../lib/commercialCase";
import { formatDate, quotesForCases, sourceKindLabel } from "../lib/crm360";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import { closeV2Detail, openV2Detail, useV2DetailId } from "../lib/v2DeepLink";

/**
 * Small on purpose.
 *
 * A summary card needs the case's *card*, not its list row — the interests and the supplier
 * parts are child rows — so one page of the list costs one request per row. Twenty is a
 * screenful; a fifty-row page would be fifty requests for rows nobody scrolled to.
 */
const PAGE_SIZE = 20;

function number(value: number): string {
  return value.toLocaleString("es-CL");
}

/** An action the workspace can describe but not perform. */
function PreviewAction({ label, reason }: { label: string; reason: string }) {
  return (
    <button
      type="button"
      disabled
      title={reason}
      data-testid="case-preview-action"
      className="cursor-not-allowed rounded-md border border-slate-300 bg-slate-100 px-2 py-1 text-xs font-medium text-slate-500"
    >
      {label}
    </button>
  );
}

function CommandPreviewCard({ preview }: { preview: CaseCommandPreview }) {
  return (
    <li
      data-testid={`case-command-preview-${preview.id}`}
      data-availability={preview.availability}
      className="rounded-md border border-slate-200 bg-white p-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <PreviewAction label={preview.label} reason={CASE_PREVIEW_ONLY_REASON} />
        <V2Chip tone={preview.availability === "available" ? "ok" : "neutral"}>
          {preview.availability === "available" ? "Datos suficientes" : "Bloqueado"}
        </V2Chip>
      </div>
      <p className="mt-1 text-xs text-[var(--color-muted)]">{preview.intent}</p>
      {preview.blockers.length > 0 ? (
        <ul className="mt-1 list-disc pl-4 text-xs text-amber-800">
          {preview.blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      ) : null}
      {preview.cautions.map((caution) => (
        <p key={caution} className="mt-1 text-xs text-amber-700">
          {caution}
        </p>
      ))}
      {preview.request ? (
        <details className="mt-1" data-testid="case-command-request">
          <summary className="cursor-pointer text-xs text-[var(--color-muted)]">
            La petición exacta que se enviaría
          </summary>
          {/*
            The request itself, not a description of it. A prose preview can agree with what
            the operator meant while the body disagrees with both, and the only place that
            shows up is the durable row afterwards.
          */}
          <pre className="mt-1 overflow-x-auto rounded bg-slate-900 p-2 text-[11px] text-slate-100">
            {JSON.stringify(preview.request, null, 2)}
          </pre>
        </details>
      ) : null}
      <details className="mt-1">
        <summary className="cursor-pointer text-xs text-[var(--color-muted)]">
          Qué registraría
        </summary>
        <ul className="mt-1 list-disc pl-4 text-xs text-[var(--color-muted)]">
          {preview.writes.map((write) => (
            <li key={write}>{write}</li>
          ))}
        </ul>
        <ul className="mt-1 list-disc pl-4 text-xs text-[var(--color-muted)]">
          {preview.doesNot.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </details>
    </li>
  );
}

function OrganizationRow({ row }: { row: V2CaseOrganization }) {
  return (
    <li className="rounded-md border border-slate-200 p-2 text-sm" data-testid="case-organization">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => openV2Detail("instituciones", row.organization_id)}
          className="text-left font-medium text-slate-900 underline decoration-dotted"
        >
          {row.name}
        </button>
        <V2Chip tone={row.role === "requesting_institution" ? "ok" : "neutral"}>
          {caseRoleLabel(row.role)}
        </V2Chip>
        {row.is_current ? null : <V2Chip tone="neutral">Cerrada</V2Chip>}
      </div>
      {row.supplier_exception_reason ? (
        <p className="mt-1 text-xs text-amber-800" data-testid="supplier-exception">
          Excepción de proveedor, {formatDate(row.supplier_exception_at)}
          {row.supplier_exception_by_display_name
            ? ` · ${row.supplier_exception_by_display_name}`
            : ""}
          : {row.supplier_exception_reason}
        </p>
      ) : null}
      {row.note ? <p className="mt-1 text-xs text-slate-700">{row.note}</p> : null}
    </li>
  );
}

function InterestRow({ row }: { row: V2CaseInterest }) {
  const quantity = interestQuantity(row);
  return (
    <li className="rounded-md border border-slate-200 p-2 text-sm" data-testid="case-interest">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-slate-900">{interestHeadline(row)}</span>
        {quantity ? <V2Chip tone="neutral">{quantity}</V2Chip> : null}
        {row.withdrawn_at ? <V2Chip tone="neutral">Retirado</V2Chip> : null}
      </div>
      {row.description ? <p className="mt-1 text-xs text-slate-700">{row.description}</p> : null}
      {row.withdraw_reason ? (
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          Retirado el {formatDate(row.withdrawn_at)}: {row.withdraw_reason}
        </p>
      ) : null}
    </li>
  );
}

function EvidenceRow({ row }: { row: V2CaseEvidence }) {
  return (
    <li className="rounded-md border border-slate-200 p-2 text-sm" data-testid="case-evidence">
      <div className="flex flex-wrap items-center gap-2">
        <V2Chip tone={row.relation === "contradicts" ? "warn" : "neutral"}>
          {caseRelationLabel(row.relation)}
        </V2Chip>
        <span className="text-xs text-[var(--color-muted)]">
          {CASE_SUBJECT_LABELS[row.subject_kind]}
          {row.source_kind ? ` · ${sourceKindLabel(row.source_kind)}` : ""}
        </span>
        {row.unlinked_at ? <V2Chip tone="neutral">Desvinculado</V2Chip> : null}
      </div>
      <p className="mt-1 break-all text-xs text-slate-700">
        {row.assertion_value ?? row.source_uri ?? row.subject_id}
      </p>
    </li>
  );
}

/** The whole case: its summary, its three sections, and one closed technical drawer. */
function CaseDetail({ opportunityId }: { opportunityId: string }) {
  const [card, setCard] = useState<V2CommercialCaseCard | null>(null);
  const [quotes, setQuotes] = useState<V2Quote[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [listRow, setListRow] = useState<V2CommercialCase | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCard(null);
    setError(null);
    fetchV2CaseCard(opportunityId)
      .then((loaded) => {
        if (!cancelled) {
          setCard(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(formatMirrorLoadError("Caso comercial", caught).message);
        }
      });
    // The quote follow-up queue has no per-case route, so it is crossed in the browser.
    fetchV2QuotesToFollowUp({ limit: 100 })
      .then((page) => {
        if (!cancelled) {
          setQuotes(page.items);
        }
      })
      .catch(() => {
        // A missing quote list must not blank the case. It renders as "sin cotización",
        // which is what it looked like before the request failed.
      });
    fetchV2Cases({ limit: 100 })
      .then((page) => {
        if (!cancelled) {
          setListRow(
            page.items.find((row) => row.opportunity_id === opportunityId) ?? null,
          );
        }
      })
      .catch(() => {
        /* The summary degrades to the card alone. */
      });
    return () => {
      cancelled = true;
    };
  }, [opportunityId]);

  const summary = useMemo(() => (card ? caseSummary(card) : null), [card]);
  const previews = useMemo(
    () => (card ? caseCommandPreviews(emptyCaseCommandContext(card)) : []),
    [card],
  );
  const gaps = useMemo(() => (card ? caseGaps(card) : []), [card]);

  const back = (
    <button
      type="button"
      onClick={() => closeV2Detail("casos")}
      className="rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700"
    >
      ← Casos
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

  if (!card || !summary) {
    return (
      <div className="space-y-4">
        {back}
        <p className="text-sm text-[var(--color-muted)]">Cargando el caso…</p>
      </div>
    );
  }

  const current = currentOrganizations(card.organizations);
  const history = historicalOrganizations(card.organizations);
  const linked = linkedEvidence(card.evidence);
  const caseQuotes = quotesForCases(quotes, [{ opportunity_id: card.opportunity_id } as V2CommercialCase]);

  const row: V2CommercialCase = listRow ?? {
    opportunity_id: card.opportunity_id,
    title: card.title,
    stage: card.stage,
    version: card.version,
    closed_at: card.closed_at,
    close_reason: card.close_reason,
    created_at: card.created_at,
    updated_at: card.updated_at,
    owner_operator_id: card.owner_operator_id,
    owner_display_name: card.owner_display_name,
    origin_source_record_id: card.origin_source_record_id,
    origin_source_kind: card.origin_source_kind,
    origin_source_uri: card.origin_source_uri,
    requesting_organization_id: summary.requesting?.organization_id ?? null,
    requesting_organization_name: summary.requesting?.name ?? null,
    requesting_confirmation: summary.requesting?.confirmation ?? null,
    organization_count: current.length,
    interest_count: card.interests.length,
    evidence_count: linked.length,
  };

  return (
    <div className="space-y-4" data-testid="case-360-detail">
      {back}

      <V2CaseSummaryCard
        row={row}
        summary={summary}
        quotes={caseQuotes}
        onOpen={() => undefined}
        onOpenOrganization={(id) => openV2Detail("instituciones", id)}
      />

      {card.close_reason ? (
        <p className="text-sm text-slate-700">Motivo del cierre: {card.close_reason}</p>
      ) : null}

      {gaps.length > 0 ? (
        <ul
          className="list-disc space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-5 py-2 text-sm text-amber-900"
          data-testid="case-gaps"
        >
          {gaps.map((gap) => (
            <li key={gap}>{gap}</li>
          ))}
        </ul>
      ) : null}

      <V2Panel
        title="Instituciones"
        count={current.length}
        caption="Qué es cada institución para este caso."
        testId="case-organizations"
      >
        {card.organizations.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            Ninguna institución en el caso. Nadie ha dicho quién pide.
          </p>
        ) : (
          <ul className="space-y-1">
            {[...current, ...history].map((organization) => (
              <OrganizationRow
                key={organization.opportunity_organization_id}
                row={organization}
              />
            ))}
          </ul>
        )}
      </V2Panel>

      <V2Panel title="Qué busca" count={card.interests.length} testId="case-interests">
        {card.interests.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            El caso no registra todavía qué busca.
          </p>
        ) : (
          <ul className="space-y-1">
            {card.interests.map((interest) => (
              <InterestRow key={interest.opportunity_interest_id} row={interest} />
            ))}
          </ul>
        )}
      </V2Panel>

      <V2Panel
        title="Documentos"
        count={linked.length}
        caption="Lo que el caso cita, y la lectura que un operador le dio."
        testId="case-evidence"
      >
        {card.evidence.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Sin evidencia vinculada.</p>
        ) : (
          <ul className="space-y-1">
            {card.evidence.map((evidence) => (
              <EvidenceRow key={evidence.opportunity_evidence_id} row={evidence} />
            ))}
          </ul>
        )}
      </V2Panel>

      <V2TechnicalDetails>
        <dl className="space-y-1">
          <div>
            <dt className="inline font-medium">opportunity_id: </dt>
            <dd className="inline break-all">{card.opportunity_id}</dd>
          </div>
          <div>
            <dt className="inline font-medium">stage / version / dueño: </dt>
            <dd className="inline">
              {card.stage} / {card.version} / {card.owner_display_name}
            </dd>
          </div>
          <div>
            <dt className="inline font-medium">origen: </dt>
            <dd className="inline break-all">
              {card.origin_source_kind ? sourceKindLabel(card.origin_source_kind) : "—"}
              {card.origin_source_uri ? ` · ${card.origin_source_uri}` : ""}
              {card.origin_review_status ? ` · ${card.origin_review_status}` : ""}
            </dd>
          </div>
          {card.reopened_from_title ? (
            <div>
              <dt className="inline font-medium">reabierto de: </dt>
              <dd className="inline">{card.reopened_from_title}</dd>
            </div>
          ) : null}
        </dl>

        <div>
          <p className="font-medium">Máquina de etapas (la sirve la API, no la copia el panel)</p>
          <p>
            Desde «{caseStageLabel(card.stage)}» puede pasar a:{" "}
            {card.stage_machine.allowed_next_stages.length === 0
              ? "ninguna — es terminal"
              : card.stage_machine.allowed_next_stages.map(caseStageLabel).join(" · ")}
          </p>
          <p>
            Exigen institución solicitante:{" "}
            {card.stage_machine.stages_requiring_a_requesting_institution
              .map(caseStageLabel)
              .join(" · ")}
          </p>
          <p>
            Exigen motivo de cierre:{" "}
            {card.stage_machine.stages_requiring_a_close_reason.map(caseStageLabel).join(" · ")}
          </p>
        </div>

        <div>
          <p className="font-medium">Decisiones disponibles</p>
          <p>{CASE_PREVIEW_ONLY_REASON}</p>
          <ul className="mt-2 space-y-2">
            {previews.map((preview) => (
              <CommandPreviewCard key={preview.id} preview={preview} />
            ))}
          </ul>
        </div>
      </V2TechnicalDetails>
    </div>
  );
}

function CaseList() {
  const [page, setPage] = useState<V2Page<V2CommercialCase> | null>(null);
  const [offset, setOffset] = useState(0);
  const [summaries, setSummaries] = useState<Record<string, V2CommercialCaseCard>>({});
  const [quotes, setQuotes] = useState<V2Quote[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const loaded = await fetchV2Cases({ limit: PAGE_SIZE, offset });
      setPage(loaded);
      // Each card is settled on its own: one failing case must not blank the other
      // nineteen, and a row without its card still renders from the list fields.
      const cards = await Promise.allSettled(
        loaded.items.map((row) => fetchV2CaseCard(row.opportunity_id)),
      );
      const next: Record<string, V2CommercialCaseCard> = {};
      cards.forEach((result) => {
        if (result.status === "fulfilled") {
          next[result.value.opportunity_id] = result.value;
        }
      });
      setSummaries(next);
    } catch (caught) {
      setPage(null);
      setError(formatMirrorLoadError("Casos comerciales", caught).message);
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    fetchV2QuotesToFollowUp({ limit: 100 })
      .then((loaded) => {
        if (!cancelled) {
          setQuotes(loaded.items);
        }
      })
      .catch(() => {
        /* Renders as "sin cotización", which is what it showed before the failure. */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The opening preview needs no case, which is why it is here and not on a card: it is
  // the one command that creates one. With nothing chosen it is blocked, and it says what
  // it is still missing — a title, a motive, and the document the case would exist because of.
  const openPreview = useMemo(() => caseCommandPreviews(emptyCaseCommandContext(null))[0], []);

  const cases = page?.items ?? [];

  return (
    <div className="space-y-4">
      <V2PageHeader
        title="Casos comerciales"
        subtitle="Quién pide, qué busca y por qué lo cree. Cada caso enlaza a su institución, sus contactos y sus cotizaciones."
      />

      {error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      ) : null}
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      {page && cases.length === 0 && !loading ? (
        <V2EmptyState
          title="Ningún caso comercial todavía"
          description="Un caso sólo existe cuando una persona lo abre desde un documento. Cero casos es el estado correcto, no una falla."
        />
      ) : null}

      {cases.length > 0 ? (
        <>
          <p className="text-sm text-[var(--color-muted)]">
            {number(page?.total ?? 0)} caso(s) en total
          </p>
          <ul className="space-y-3">
            {cases.map((row) => {
              const card = summaries[row.opportunity_id] ?? null;
              return (
                <li key={row.opportunity_id}>
                  <V2CaseSummaryCard
                    row={row}
                    summary={card ? caseSummary(card) : null}
                    quotes={quotes.filter(
                      (quote) => quote.opportunity_id === row.opportunity_id,
                    )}
                    onOpen={() => openV2Detail("casos", row.opportunity_id)}
                    onOpenOrganization={(id) => openV2Detail("instituciones", id)}
                  />
                </li>
              );
            })}
          </ul>
          <div className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
            <button
              type="button"
              disabled={offset <= 0}
              onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
              className="rounded-md border border-slate-300 px-2 py-1 disabled:opacity-40"
            >
              Anteriores
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= (page?.total ?? 0)}
              onClick={() => setOffset((value) => value + PAGE_SIZE)}
              className="rounded-md border border-slate-300 px-2 py-1 disabled:opacity-40"
            >
              Siguientes
            </button>
          </div>
        </>
      ) : null}

      <V2TechnicalDetails summary="Detalles técnicos · abrir un caso">
        <p>
          Un caso se abre <strong>desde un documento</strong>: el que lo causó queda vinculado
          como su origen en la misma transacción. Por eso un caso sin nada detrás no queda
          rechazado por una comprobación — queda impedido de pedirse.
        </p>
        <ul className="space-y-2">
          <CommandPreviewCard preview={openPreview} />
        </ul>
      </V2TechnicalDetails>
    </div>
  );
}

export function CommercialCasePage() {
  const selected = useV2DetailId("casos");
  return selected ? <CaseDetail opportunityId={selected} /> : <CaseList />;
}
