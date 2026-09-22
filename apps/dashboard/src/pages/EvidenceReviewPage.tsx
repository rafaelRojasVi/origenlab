/**
 * The evidence review workspace — one page that shows the whole commercial flow and the
 * one step of it that currently has work in it.
 *
 * The flow is the point. Evidence becomes a contact and an institution only when a human
 * says so; a prospect only if there is commercial intent; marketing only with an explicit
 * recorded permission; a quote only against an already-reviewed contact and institution.
 * Each of those steps is drawn even when it is empty, because an operator who cannot see
 * the order cannot see why the queue matters.
 *
 * **Read-only, structurally.** No command client is imported here and the proxy allows no
 * POST under `/v2`. The action affordances are rendered disabled with the reason attached:
 * a button that looked live and did nothing would be worse than no button, and a button
 * that worked would be a second writer into durable truth. Deciding what staged evidence
 * establishes is a durable command, and the V2 command boundary does not exist yet.
 */

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchV2Contacts,
  fetchV2EvidenceRecords,
  fetchV2Organizations,
  fetchV2Prospects,
} from "../api/v2Client";
import type { V2EvidenceRecord, V2Page } from "../api/v2Types";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { sourceKindLabel } from "../lib/crmV2Browser";
import {
  addressesOf,
  domainCounts,
  identityHeadline,
  matchForAddress,
  organizationHeadline,
  organizationNamesOf,
  reviewDate,
  reviewFlags,
  reviewStatusLabel,
  REVIEW_FLOW,
} from "../lib/evidenceReview";
import { formatMirrorLoadError } from "../lib/humanizeApiError";

const PAGE_SIZE = 50;

interface Totals {
  contacts: number;
  organizations: number;
  prospects: number;
  pendingRecords: number;
}

function number(value: number): string {
  return value.toLocaleString("es-CL");
}

/**
 * A count with the sentence that keeps it from being over-read.
 *
 * "9.460 contactos" invites the reading "we have 9.460 customers". The caption is where
 * that reading is taken away.
 */
function TopCard({
  label,
  value,
  caption,
  emphasis,
}: {
  label: string;
  value: number;
  caption: string;
  emphasis?: boolean;
}) {
  return (
    <div
      data-testid="review-top-card"
      className={`rounded-xl border px-4 py-3 ${
        emphasis ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-[var(--color-card)]"
      }`}
    >
      <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{number(value)}</p>
      <p className="mt-1 text-xs text-[var(--color-muted)]">{caption}</p>
    </div>
  );
}

function FlowStrip({ pending }: { pending: number }) {
  const state: Record<string, { tone: string; note: string }> = {
    evidence: {
      tone: "border-amber-300 bg-amber-50",
      note: `${number(pending)} pendientes`,
    },
    review: { tone: "border-slate-300 bg-white", note: "Sin ruta de escritura todavía" },
    prospect: { tone: "border-slate-200 bg-slate-50", note: "No disponible" },
    marketing: { tone: "border-slate-200 bg-slate-50", note: "Bloqueado sin permiso" },
    quote: { tone: "border-slate-200 bg-slate-50", note: "No disponible" },
  };
  return (
    <ol
      className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5"
      data-testid="review-flow"
      aria-label="Flujo comercial"
    >
      {REVIEW_FLOW.map((step) => (
        <li
          key={step.id}
          className={`rounded-lg border px-3 py-2 text-xs ${state[step.id].tone}`}
        >
          <p className="font-semibold text-slate-900">{step.label}</p>
          <p className="mt-1 text-[var(--color-muted)]">{step.detail}</p>
          <p className="mt-1 font-medium text-slate-700">{state[step.id].note}</p>
        </li>
      ))}
    </ol>
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
        data-testid="review-preview-action"
        className="cursor-not-allowed rounded-md border border-slate-300 bg-slate-100 px-2 py-1 text-xs font-medium text-slate-500"
      >
        {label}
      </button>
      <span className="mt-1 text-[11px] text-[var(--color-muted)]">{reason}</span>
    </span>
  );
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

function RecordDetail({
  record,
  counts,
}: {
  record: V2EvidenceRecord;
  counts: Map<string, number>;
}) {
  const flags = reviewFlags(record, counts);
  const names = organizationNamesOf(record);
  return (
    <div className="space-y-4 border-t border-slate-200 bg-slate-50 px-3 py-3 text-sm">
      <div className="grid gap-3 md:grid-cols-2">
        <section className="space-y-1">
          <h4 className="text-xs font-semibold uppercase text-[var(--color-muted)]">
            Identidad observada
          </h4>
          {addressesOf(record).map((address) => {
            const hit = matchForAddress(record, address);
            return (
              <div key={address} data-testid="review-address" className="space-y-1">
                <p className="break-all font-medium text-slate-900">{address}</p>
                {hit ? (
                  <>
                    <p className="text-[var(--color-muted)]">
                      La dirección existe en el CRM durable (canal {hit.contact_point_id.slice(0, 8)}…).
                    </p>
                    <p>
                      {hit.person_display_name ? (
                        <Chip tone="ok">Persona confirmada: {hit.person_display_name}</Chip>
                      ) : (
                        <Chip tone="warn">Sin persona confirmada</Chip>
                      )}{" "}
                      {hit.organization_name ? (
                        <Chip tone="ok">Institución: {hit.organization_name}</Chip>
                      ) : (
                        <Chip tone="warn">Sin institución atribuida</Chip>
                      )}
                    </p>
                  </>
                ) : (
                  <p className="text-[var(--color-muted)]">
                    No existe todavía como canal en el CRM durable.
                  </p>
                )}
              </div>
            );
          })}
        </section>

        <section className="space-y-1">
          <h4 className="text-xs font-semibold uppercase text-[var(--color-muted)]">
            Institución
          </h4>
          <p className="text-[var(--color-muted)]">
            Pista de dominio:{" "}
            <span className="font-medium text-slate-800">{record.from_domain ?? "—"}</span>{" "}
            {record.domain_organization ? (
              <Chip tone="ok">Dominio registrado: {record.domain_organization.name}</Chip>
            ) : (
              <Chip tone="warn">Dominio sin registrar — pista, no evidencia</Chip>
            )}
          </p>
          {names.length === 0 ? (
            <p className="text-[var(--color-muted)]">
              El mensaje no nombra ninguna institución.
            </p>
          ) : (
            <ul className="space-y-1">
              {names.map((name) => {
                const hit = record.organization_matches.find((row) => row.value_norm === name);
                return (
                  <li key={name} data-testid="review-organization-name">
                    <span className="font-medium text-slate-900">«{name}»</span>{" "}
                    {hit ? (
                      <Chip tone="warn">Homónimo en el CRM: {hit.name}</Chip>
                    ) : (
                      <Chip tone="neutral">Sin homónimo registrado</Chip>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      <section className="space-y-1">
        <h4 className="text-xs font-semibold uppercase text-[var(--color-muted)]">
          Qué queda sin confirmar
        </h4>
        <ul className="list-disc space-y-1 pl-5 text-[var(--color-muted)]">
          {flags.map((flag) => (
            <li key={`${flag.kind}-${flag.text}`} data-testid="review-flag">
              {flag.text}
            </li>
          ))}
        </ul>
      </section>

      <section className="space-y-1">
        <h4 className="text-xs font-semibold uppercase text-[var(--color-muted)]">Procedencia</h4>
        <p className="break-all text-[var(--color-muted)]">
          {sourceKindLabel(record.source_kind)} · {record.source_uri ?? record.dedupe_key} ·
          registrado {reviewDate(record.acquired_at)} · {reviewStatusLabel(record.review_status)}
        </p>
        <p className="text-[var(--color-muted)]">
          Observaciones:{" "}
          {record.assertions
            .map((assertion) => `${assertion.kind} = ${assertion.value_norm}`)
            .join(" · ")}
        </p>
        {record.assertion_total > record.assertions.length ? (
          <p className="text-[var(--color-muted)]">
            Mostrando {record.assertions.length} de {number(record.assertion_total)}{" "}
            observaciones de este registro.
          </p>
        ) : null}
      </section>

      <section className="flex flex-wrap gap-4">
        <PreviewAction
          label="Confirmar persona"
          reason="Sin ruta de escritura: el límite de comandos V2 aún no existe."
        />
        <PreviewAction
          label="Atribuir institución"
          reason="Sin ruta de escritura: atribuir es una decisión durable."
        />
        <PreviewAction
          label="Descartar registro"
          reason="Sin ruta de escritura: rechazar evidencia también se audita."
        />
      </section>
    </div>
  );
}

export function EvidenceReviewPage() {
  const [page, setPage] = useState<V2Page<V2EvidenceRecord> | null>(null);
  const [totals, setTotals] = useState<Totals | null>(null);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // The three totals are read with `limit: 1` and taken from `total`, never from
      // `items.length`: the boundary pages, so counting the array would understate them.
      const [records, contacts, organizations, prospects] = await Promise.all([
        fetchV2EvidenceRecords({
          sourceKind: "gmail_message",
          reviewStatus: "pending",
          limit: PAGE_SIZE,
          offset,
        }),
        fetchV2Contacts({ limit: 1 }),
        fetchV2Organizations({ limit: 1 }),
        fetchV2Prospects({ limit: 1 }),
      ]);
      setPage(records);
      setTotals({
        contacts: contacts.total,
        organizations: organizations.total,
        prospects: prospects.total,
        pendingRecords: records.total,
      });
    } catch (caught) {
      setPage(null);
      setError(formatMirrorLoadError("Revisión de evidencia", caught).message);
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const records = useMemo(() => page?.items ?? [], [page]);
  const counts = useMemo(() => domainCounts(records), [records]);

  return (
    <div className="space-y-5">
      <V2PageHeader
        title="Revisión de evidencia"
        subtitle="Correos de Gmail pendientes de revisión: lo que cada uno afirma y qué falta decidir. Nada aquí crea personas, instituciones, prospectos, permisos ni cotizaciones — es una superficie de lectura."
      />

      <FlowStrip pending={totals?.pendingRecords ?? 0} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <TopCard
          label="Contactos"
          value={totals?.contacts ?? 0}
          caption="Canales de correo. Existir como dirección no es ser una persona confirmada."
        />
        <TopCard
          label="Organizaciones"
          value={totals?.organizations ?? 0}
          caption="Todas propuestas por máquina; ninguna confirmada por un operador todavía."
        />
        <TopCard
          label="Prospectos"
          value={totals?.prospects ?? 0}
          caption="Oportunidades en etapa lead o qualifying. Vacío porque aún no se migra el histórico durable."
        />
        <TopCard
          label="Evidencia pendiente"
          value={totals?.pendingRecords ?? 0}
          caption="Correos de Gmail registrados como evidencia, esperando una decisión humana."
          emphasis
        />
      </div>

      {error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </p>
      ) : null}
      {loading ? <p className="text-sm text-[var(--color-muted)]">Cargando…</p> : null}

      {page && records.length === 0 && !loading ? (
        <V2EmptyState
          title="Sin evidencia pendiente"
          description="Ningún registro de origen está esperando revisión. Nada que decidir en esta cola."
        />
      ) : null}

      {records.length > 0 ? (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-slate-900">
            Cola de revisión{" "}
            <span className="font-normal text-[var(--color-muted)]">
              {number(page?.total ?? 0)} correos pendientes de Gmail
            </span>
          </h3>
          <table className="w-full table-auto text-left text-sm" data-testid="review-queue-table">
            <thead className="text-xs uppercase text-[var(--color-muted)]">
              <tr>
                <th className="py-2">Remitente</th>
                <th>Asunto</th>
                <th>Fecha</th>
                <th>Identidad</th>
                <th>Institución</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => {
                const expanded = open === record.source_record_id;
                return (
                  <Fragment key={record.source_record_id}>
                    <tr
                      className="border-t border-slate-200 align-top"
                      data-testid="review-queue-row"
                    >
                      <td className="py-2">
                        <button
                          type="button"
                          aria-expanded={expanded}
                          onClick={() =>
                            setOpen(expanded ? null : record.source_record_id)
                          }
                          className="break-all text-left font-medium text-slate-900 underline decoration-dotted"
                        >
                          {record.from_address ?? record.dedupe_key}
                        </button>
                      </td>
                      <td className="text-slate-800">{record.subject ?? "—"}</td>
                      <td className="whitespace-nowrap text-[var(--color-muted)]">
                        {reviewDate(record.message_date ?? record.acquired_at)}
                      </td>
                      <td>
                        <Chip
                          tone={
                            identityHeadline(record) === "Persona confirmada" ? "ok" : "warn"
                          }
                        >
                          {identityHeadline(record)}
                        </Chip>
                      </td>
                      <td className="text-[var(--color-muted)]">
                        {organizationHeadline(record)}
                      </td>
                    </tr>
                    {expanded ? (
                      <tr>
                        <td colSpan={5} className="p-0">
                          <RecordDetail record={record} counts={counts} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>

          {page && page.total > PAGE_SIZE ? (
            <div className="flex items-center justify-between text-sm text-[var(--color-muted)]">
              <span>
                {number(page.offset + 1)}–{number(page.offset + records.length)} de{" "}
                {number(page.total)}
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
        </section>
      ) : null}

      <section
        className="rounded-xl border border-slate-200 bg-[var(--color-card)] px-4 py-3"
        data-testid="review-marketing-section"
      >
        <h3 className="text-sm font-semibold text-slate-900">Marketing</h3>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          No disponible. Un correo entrante no es permiso para enviar: el permiso es
          explícito, se registra por dirección y nada de esta cola lo otorga. Los dos
          interruptores de envío siguen en <code>false</code>.
        </p>
        <div className="mt-3 flex flex-wrap gap-4">
          <PreviewAction
            label="Inscribir en campaña"
            reason="Requiere permiso explícito registrado para esa dirección."
          />
          <PreviewAction
            label="Registrar permiso"
            reason="Sin ruta de escritura: el permiso es un hecho durable y auditado."
          />
        </div>
      </section>

      <section
        className="rounded-xl border border-slate-200 bg-[var(--color-card)] px-4 py-3"
        data-testid="review-quotes-section"
      >
        <h3 className="text-sm font-semibold text-slate-900">Cotizaciones</h3>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          No disponible. Una cotización se adjunta a un contacto y una institución ya
          revisados; mientras la cola de arriba esté sin resolver no hay a qué adjuntarla.
        </p>
        <div className="mt-3">
          <PreviewAction
            label="Crear cotización"
            reason="Requiere un contacto e institución revisados por una persona."
          />
        </div>
      </section>
    </div>
  );
}
