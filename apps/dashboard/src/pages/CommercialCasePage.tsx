/**
 * The commercial-case workspace — what a case says, and what deciding anything about it
 * would record.
 *
 * A case is where this system stops describing correspondence and starts describing
 * business: who is asking, what they are asking for, and why we believe either. The three
 * tables behind it ship empty and stay empty until a command writes one, so the first thing
 * this page has to do well is render *nothing* honestly — an empty list here is the correct
 * state, not an outage.
 *
 * **Read-only, structurally.** No command client is imported and the proxy allows no POST
 * under `/v2`. The six case commands exist upstream; their affordances are rendered
 * disabled with the reason attached, because a button that looked live and did nothing
 * would be worse than no button, and a button that worked would be a second writer into
 * durable truth.
 *
 * **Nothing on this screen picks anything.** The previews compute from what an operator has
 * chosen, and here they have chosen nothing: no institution is pre-selected, no role is
 * defaulted, no stage is proposed. What the page shows is therefore every command blocked,
 * each one saying exactly what it is still missing — which is the honest account of a
 * surface that cannot yet decide.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchV2CaseCard, fetchV2Cases } from "../api/v2Client";
import type {
  V2CaseEvidence,
  V2CaseInterest,
  V2CaseOrganization,
  V2CommercialCase,
  V2CommercialCaseCard,
  V2Page,
} from "../api/v2Types";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
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
  currentOrganizations,
  historicalOrganizations,
  interestHeadline,
  interestQuantity,
  linkedEvidence,
} from "../lib/commercialCase";
import { formatMirrorLoadError } from "../lib/humanizeApiError";

const PAGE_SIZE = 50;

function number(value: number): string {
  return value.toLocaleString("es-CL");
}

function date(value: string | null): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("es-CL");
}

function Chip({ tone, children }: { tone: "neutral" | "warn" | "ok"; children: React.ReactNode }) {
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

/**
 * An action the workspace can describe but not perform.
 *
 * `disabled` plus the reason, rather than a hidden button: the operator should be able to
 * see what the next slice will make possible, and exactly why it is not possible today.
 */
function PreviewAction({ label, reason }: { label: string; reason: string }) {
  return (
    <span className="inline-flex flex-col">
      <button
        type="button"
        disabled
        title={reason}
        data-testid="case-preview-action"
        className="cursor-not-allowed rounded-md border border-slate-300 bg-slate-100 px-2 py-1 text-xs font-medium text-slate-500"
      >
        {label}
      </button>
    </span>
  );
}

function CommandPreviewCard({ preview }: { preview: CaseCommandPreview }) {
  return (
    <li
      data-testid={`case-command-preview-${preview.id}`}
      data-availability={preview.availability}
      className="rounded-md border border-slate-200 p-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <PreviewAction label={preview.label} reason={CASE_PREVIEW_ONLY_REASON} />
        <Chip tone={preview.availability === "available" ? "ok" : "neutral"}>
          {preview.availability === "available" ? "Datos suficientes" : "Bloqueado"}
        </Chip>
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
    <li className="rounded-md border border-slate-200 p-2 text-xs" data-testid="case-organization">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-slate-900">{row.name}</span>
        <Chip tone="neutral">{caseRoleLabel(row.role)}</Chip>
        <Chip tone={row.confirmation === "confirmed" ? "ok" : "warn"}>
          {row.confirmation === "confirmed" ? "Confirmada por una persona" : "Propuesta por la máquina"}
        </Chip>
        {row.is_current ? null : <Chip tone="neutral">Cerrada</Chip>}
      </div>
      <p className="mt-1 text-[11px] text-[var(--color-muted)]">
        Vigente desde {date(row.valid_from)}
        {row.valid_to ? ` hasta ${date(row.valid_to)}` : ""}
        {row.confirmed_by_display_name ? ` · ${row.confirmed_by_display_name}` : ""}
      </p>
      {row.supplier_exception_reason ? (
        <p className="mt-1 text-[11px] text-amber-800" data-testid="supplier-exception">
          Excepción de proveedor, {date(row.supplier_exception_at)}
          {row.supplier_exception_by_display_name
            ? ` · ${row.supplier_exception_by_display_name}`
            : ""}
          : {row.supplier_exception_reason}
        </p>
      ) : null}
      {row.note ? <p className="mt-1 text-[11px] text-slate-700">{row.note}</p> : null}
    </li>
  );
}

function InterestRow({ row }: { row: V2CaseInterest }) {
  const quantity = interestQuantity(row);
  return (
    <li className="rounded-md border border-slate-200 p-2 text-xs" data-testid="case-interest">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-slate-900">{interestHeadline(row)}</span>
        {quantity ? <Chip tone="neutral">{quantity}</Chip> : null}
        {row.withdrawn_at ? <Chip tone="neutral">Retirado</Chip> : null}
      </div>
      {row.description ? (
        <p className="mt-1 text-[11px] text-slate-700">{row.description}</p>
      ) : null}
      {row.withdraw_reason ? (
        <p className="mt-1 text-[11px] text-[var(--color-muted)]">
          Retirado el {date(row.withdrawn_at)}: {row.withdraw_reason}
        </p>
      ) : null}
    </li>
  );
}

function EvidenceRow({ row }: { row: V2CaseEvidence }) {
  return (
    <li className="rounded-md border border-slate-200 p-2 text-xs" data-testid="case-evidence">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={row.relation === "contradicts" ? "warn" : "neutral"}>
          {caseRelationLabel(row.relation)}
        </Chip>
        <span className="text-[var(--color-muted)]">
          {CASE_SUBJECT_LABELS[row.subject_kind]}
        </span>
        {row.unlinked_at ? <Chip tone="neutral">Desvinculado</Chip> : null}
      </div>
      <p className="mt-1 text-[11px] text-slate-700">
        {row.assertion_value ?? row.source_uri ?? row.subject_id}
      </p>
      <p className="mt-1 text-[11px] text-[var(--color-muted)]">
        Vinculado por {row.linked_by_display_name} el {date(row.linked_at)}
        {row.unlink_reason ? ` · desvinculado: ${row.unlink_reason}` : ""}
      </p>
    </li>
  );
}

function Section({ title, caption, children }: {
  title: string;
  caption: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-1">
      <h4 className="text-xs font-semibold text-slate-900">{title}</h4>
      <p className="text-[11px] text-[var(--color-muted)]">{caption}</p>
      {children}
    </section>
  );
}

/**
 * One open case, with its parts, what it seeks, why it believes it, and the six previews.
 *
 * Closed part rows and withdrawn interests are shown rather than filtered: a part is never
 * rewritten in this schema — changing one closes a row and opens another — so a card that
 * showed only what is current would make the audit trail invisible exactly where it matters.
 */
function CaseCard({ card }: { card: V2CommercialCaseCard }) {
  const previews = useMemo(
    () => caseCommandPreviews(emptyCaseCommandContext(card)),
    [card],
  );
  const gaps = useMemo(() => caseGaps(card), [card]);
  const current = currentOrganizations(card.organizations);
  const history = historicalOrganizations(card.organizations);
  const linked = linkedEvidence(card.evidence);

  return (
    <div className="space-y-4 rounded-lg border border-slate-200 bg-[var(--color-card)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone="neutral">{caseStageLabel(card.stage)}</Chip>
        <Chip tone="neutral">versión {card.version}</Chip>
        {card.closed_at ? <Chip tone="neutral">Cerrado el {date(card.closed_at)}</Chip> : null}
        <span className="text-xs text-[var(--color-muted)]">
          Dueño: {card.owner_display_name}
        </span>
      </div>
      {card.close_reason ? (
        <p className="text-xs text-slate-700">Motivo del cierre: {card.close_reason}</p>
      ) : null}

      {gaps.length > 0 ? (
        <ul
          className="list-disc space-y-1 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900"
          data-testid="case-gaps"
        >
          {gaps.map((gap) => (
            <li key={gap}>{gap}</li>
          ))}
        </ul>
      ) : null}

      <Section
        title={`Instituciones (${number(current.length)} vigentes de ${number(card.organizations.length)})`}
        caption="Qué es cada institución para este caso. Un caso puede no saber todavía quién pide: es un estado legítimo hasta «Calificado», no un vacío que haya que rellenar."
      >
        {card.organizations.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">
            Ninguna institución en el caso. Nadie ha dicho quién pide.
          </p>
        ) : (
          <ul className="space-y-1">
            {[...current, ...history].map((row) => (
              <OrganizationRow key={row.opportunity_organization_id} row={row} />
            ))}
          </ul>
        )}
      </Section>

      <Section
        title={`Qué busca (${number(card.interests.length)})`}
        caption="El asunto del caso. No hay precio ni monto aquí y no hay sitio para uno: el dinero vive sólo en las revisiones de cotización."
      >
        {card.interests.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">
            El caso no registra todavía qué busca.
          </p>
        ) : (
          <ul className="space-y-1">
            {card.interests.map((row) => (
              <InterestRow key={row.opportunity_interest_id} row={row} />
            ))}
          </ul>
        )}
      </Section>

      <Section
        title={`Por qué lo cree (${number(linked.length)} vínculos vigentes)`}
        caption="Los documentos que el caso cita, con la lectura que un operador les dio. «Lo contradice» es tan legítimo como «Origen del caso»."
      >
        {card.evidence.length === 0 ? (
          <p className="text-xs text-[var(--color-muted)]">Sin evidencia vinculada.</p>
        ) : (
          <ul className="space-y-1">
            {card.evidence.map((row) => (
              <EvidenceRow key={row.opportunity_evidence_id} row={row} />
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Decisiones disponibles"
        caption={CASE_PREVIEW_ONLY_REASON}
      >
        <ul className="space-y-2">
          {previews.map((preview) => (
            <CommandPreviewCard key={preview.id} preview={preview} />
          ))}
        </ul>
      </Section>
    </div>
  );
}

function CaseRow({
  row,
  expanded,
  onToggle,
  card,
  cardError,
}: {
  row: V2CommercialCase;
  expanded: boolean;
  onToggle: () => void;
  card: V2CommercialCaseCard | null;
  cardError: string | null;
}) {
  return (
    <li className="rounded-lg border border-slate-200 bg-[var(--color-card)]" data-testid="case-row">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="w-full px-3 py-2 text-left"
      >
        <span className="block text-sm font-medium text-slate-900">{row.title}</span>
        <span className="mt-1 flex flex-wrap items-center gap-2">
          <Chip tone="neutral">{caseStageLabel(row.stage)}</Chip>
          {row.requesting_organization_name ? (
            <Chip tone="neutral">Pide: {row.requesting_organization_name}</Chip>
          ) : (
            <Chip tone="warn">Nadie ha dicho quién pide</Chip>
          )}
          <span className="text-xs text-[var(--color-muted)]">
            {number(row.organization_count)} institución(es) · {number(row.interest_count)}{" "}
            interés(es) · {number(row.evidence_count)} documento(s)
          </span>
        </span>
      </button>
      {expanded ? (
        <div className="border-t border-slate-200 p-3">
          {cardError ? (
            <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
              {cardError}
            </p>
          ) : card ? (
            <CaseCard card={card} />
          ) : (
            <p className="text-sm text-[var(--color-muted)]">Cargando el caso…</p>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function CommercialCasePage() {
  const [page, setPage] = useState<V2Page<V2CommercialCase> | null>(null);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [card, setCard] = useState<V2CommercialCaseCard | null>(null);
  const [cardError, setCardError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPage(await fetchV2Cases({ limit: PAGE_SIZE, offset }));
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
    if (!open) {
      setCard(null);
      setCardError(null);
      return;
    }
    let cancelled = false;
    setCard(null);
    setCardError(null);
    fetchV2CaseCard(open)
      .then((loaded) => {
        if (!cancelled) {
          setCard(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setCardError(formatMirrorLoadError("Caso comercial", caught).message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const cases = page?.items ?? [];
  // The opening preview needs no case, which is why it is here and not on a card: it is
  // the one command that creates one. With nothing chosen it is blocked, and it says what
  // it is still missing — a title, a motive, and the document the case would exist because of.
  const openPreview = useMemo(
    () => caseCommandPreviews(emptyCaseCommandContext(null))[0],
    [],
  );

  return (
    <div className="space-y-5">
      <V2PageHeader
        title="Casos comerciales"
        subtitle="Un caso dice quién pide, qué busca y por qué lo cree. Esta superficie sólo lee: los seis comandos existen en la API, y ninguno es alcanzable desde el navegador."
      />

      {error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      ) : null}
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      <section className="space-y-2 rounded-lg border border-slate-200 bg-[var(--color-card)] p-3">
        <h3 className="text-sm font-semibold text-slate-900">Abrir un caso</h3>
        <p className="text-xs text-[var(--color-muted)]">
          Un caso se abre <strong>desde un documento</strong>: el que lo causó queda vinculado
          como su origen en la misma transacción. Por eso un caso sin nada detrás no queda
          rechazado por una comprobación — queda impedido de pedirse.
        </p>
        <ul className="space-y-2">
          <CommandPreviewCard preview={openPreview} />
        </ul>
      </section>

      {page && cases.length === 0 && !loading ? (
        <V2EmptyState
          title="Ningún caso comercial todavía"
          description="Las tres tablas del caso están vacías porque ningún comando ha escrito una. Eso es el estado correcto, no una falla: un caso sólo existe cuando una persona lo abre."
        />
      ) : null}

      {cases.length > 0 ? (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-slate-900">
            Casos{" "}
            <span className="font-normal text-[var(--color-muted)]">
              {number(page?.total ?? 0)} en total
            </span>
          </h3>
          <ul className="space-y-2">
            {cases.map((row) => (
              <CaseRow
                key={row.opportunity_id}
                row={row}
                expanded={open === row.opportunity_id}
                onToggle={() =>
                  setOpen((current) =>
                    current === row.opportunity_id ? null : row.opportunity_id,
                  )
                }
                card={open === row.opportunity_id ? card : null}
                cardError={open === row.opportunity_id ? cardError : null}
              />
            ))}
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
        </section>
      ) : null}
    </div>
  );
}
