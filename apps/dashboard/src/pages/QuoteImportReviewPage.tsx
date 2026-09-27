/**
 * Importación de cotizaciones — a quotation import plan read beside the database it landed in.
 *
 * The plan decides which opportunities are **ready** (imported), **waiting** for an organization
 * confirmation, or **held**; only the ready ones may exist in the database. This page lists the
 * three apart, shows each ready opportunity's quotations, revisions, canonical documents and
 * Gmail evidence as the database holds them, and puts the two invariants the import must keep —
 * nothing waiting or held in the database, nothing outside the ready scope attached to a ready
 * opportunity — at the top, computed by the API on every read.
 *
 * **Read-only.** One GET. No message body is ever shown — the API does not send one; the
 * operator opens the original message in Gmail.
 */

import { useEffect, useMemo, useState } from "react";

import {
  fetchImportReview,
  importRevisionPdfUrl,
  type ImportEvidence,
  type ImportOpportunity,
  type ImportReview,
} from "../api/quoteImportReview";
import { V2Chip } from "../components/v2/V2Chip";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { V2Panel } from "../components/v2/V2Panel";
import { V2TechnicalDetails } from "../components/v2/V2TechnicalDetails";
import { formatMirrorLoadError } from "../lib/humanizeApiError";
import {
  COMPLETENESS_LABELS,
  EMPTY_IMPORT_FILTER,
  PLAN_STATUS_LABELS,
  filterImportRows,
  shortSha,
  type ImportReviewFilter,
} from "../lib/quoteImportReview";

const INVARIANT_LABELS: Record<keyof ImportReview["invariants"], string> = {
  waiting_or_held_in_database: "Ninguna oportunidad en espera o retenida está en la base",
  unready_evidence_in_ready_opportunity: "Ninguna evidencia fuera del alcance «listo» en una oportunidad lista",
  ready_opportunity_incomplete: "Toda oportunidad lista tiene su evidencia completa",
};

const CHECK_LABELS: Record<string, string> = {
  opportunity_in_database: "Oportunidad en la base",
  confirmed_organization_linked: "Institución confirmada vinculada como solicitante",
  every_revision_in_database: "Cada revisión del plan está en la base",
  no_extra_revision_in_database: "Ninguna revisión ajena al plan",
  every_message_in_database: "Cada correo de origen está en la base",
  every_message_linked: "Cada correo está vinculado al caso",
  every_document_asserted: "Cada documento tiene su afirmación document_reference",
  revision_origin_is_canonical: "El origen de cada revisión es el correo canónico",
};

function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleString("es-CL", { dateStyle: "medium", timeStyle: "short" });
}

function n(value: number | undefined): string {
  return (value ?? 0).toLocaleString("es-CL");
}

function GmailLink({ url, label = "Abrir en Gmail" }: { url: string | null | undefined; label?: string }) {
  if (!url) return <span className="text-slate-400">sin enlace</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" className="text-sky-700 underline">
      {label}
    </a>
  );
}

function Counts({ review }: { review: ImportReview }) {
  const c = review.counts;
  const tiles: [string, number][] = [
    ["Oportunidades listas", c.ready],
    ["…en la base", c.ready_in_database],
    ["Cotizaciones", c.quotes_in_database_for_ready],
    ["Revisiones", c.revisions_in_database_for_ready],
    ["Correos Gmail", c.gmail_records_in_database],
    ["Afirmaciones", c.assertions_on_ready_records],
    ["Confirmaciones de institución", c.organization_confirmations],
    ["En espera", c.waiting],
    ["Retenidas", c.held],
  ];
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-9" data-testid="import-counts">
      {tiles.map(([label, value]) => (
        <div key={label} className="rounded border border-slate-200 bg-white p-2">
          <div className="text-xl font-semibold text-slate-900">{n(value)}</div>
          <div className="text-xs text-slate-600">{label}</div>
        </div>
      ))}
    </div>
  );
}

function Invariants({ review }: { review: ImportReview }) {
  return (
    <ul className="space-y-1" data-testid="import-invariants">
      {(Object.keys(INVARIANT_LABELS) as (keyof ImportReview["invariants"])[]).map((key) => {
        const inv = review.invariants[key];
        return (
          <li key={key} className="flex items-start gap-2 text-sm">
            <V2Chip tone={inv.ok ? "ok" : "danger"}>{inv.ok ? "Cumple" : `${inv.violations.length} fallos`}</V2Chip>
            <span>{INVARIANT_LABELS[key]}</span>
            {!inv.ok && (
              <pre className="ml-2 max-h-40 overflow-auto text-xs text-rose-800">
                {JSON.stringify(inv.violations, null, 1)}
              </pre>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Filters({ value, onChange }: { value: ImportReviewFilter; onChange: (f: ImportReviewFilter) => void }) {
  const input = "rounded border border-slate-300 px-2 py-1 text-sm";
  return (
    <div className="flex flex-wrap items-end gap-2" data-testid="import-filters">
      <label className="text-xs text-slate-600">
        Institución
        <input aria-label="Institución" className={`${input} block`} value={value.organization}
          onChange={(e) => onChange({ ...value, organization: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">
        N.º de cotización
        <input aria-label="N.º de cotización" className={`${input} block w-32`} value={value.quoteNumber}
          onChange={(e) => onChange({ ...value, quoteNumber: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">
        Oportunidad (id o título)
        <input aria-label="Oportunidad" className={`${input} block`} value={value.opportunity}
          onChange={(e) => onChange({ ...value, opportunity: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">
        Estado del plan
        <select aria-label="Estado del plan" className={`${input} block`} value={value.status}
          onChange={(e) => onChange({ ...value, status: e.target.value as ImportReviewFilter["status"] })}>
          <option value="all">Todos</option>
          <option value="ready">{PLAN_STATUS_LABELS.ready}</option>
          <option value="waiting">{PLAN_STATUS_LABELS.waiting}</option>
          <option value="held">{PLAN_STATUS_LABELS.held}</option>
        </select>
      </label>
      <label className="text-xs text-slate-600">
        Evidencia
        <select aria-label="Evidencia" className={`${input} block`} value={value.completeness}
          onChange={(e) => onChange({ ...value, completeness: e.target.value as ImportReviewFilter["completeness"] })}>
          <option value="all">Toda</option>
          {(Object.keys(COMPLETENESS_LABELS) as (keyof typeof COMPLETENESS_LABELS)[]).map((k) => (
            <option key={k} value={k}>{COMPLETENESS_LABELS[k]}</option>
          ))}
        </select>
      </label>
      <button type="button" className="rounded border border-slate-300 px-2 py-1 text-sm"
        onClick={() => onChange(EMPTY_IMPORT_FILTER)}>
        Limpiar
      </button>
    </div>
  );
}

function EvidenceRow({ ev }: { ev: ImportEvidence }) {
  return (
    <li className="rounded border border-slate-100 p-2 text-sm" data-testid="import-evidence">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{ev.subject || "(sin asunto)"}</span>
        <V2Chip tone={ev.in_ready_scope ? "ok" : "danger"}>
          {ev.in_ready_scope ? "en alcance «listo»" : "FUERA de alcance"}
        </V2Chip>
        {ev.is_quarantined && <V2Chip tone="danger">en cuarentena</V2Chip>}
        <GmailLink url={ev.gmail_url} />
        {ev.gmail_search_url && <GmailLink url={ev.gmail_search_url} label="buscar por Message-ID" />}
      </div>
      <div className="text-xs text-slate-600">
        {dateTime(ev.sent_at)} · De: {ev.sender || "—"} · Para: {ev.recipients || "—"}
      </div>
      <div className="text-xs italic text-slate-500">
        El cuerpo del correo no se muestra aquí; ábrelo en Gmail.
      </div>
      <ul className="mt-1 flex flex-wrap gap-1">
        {(ev.assertions ?? []).map((a, i) => (
          <li key={`${a.kind}-${i}`}>
            <V2Chip tone={a.resolution === "promoted" ? "ok" : "neutral"}>
              {a.kind}: {a.kind === "document_reference" ? shortSha(a.document_sha256) : a.value} ({a.resolution})
            </V2Chip>
          </li>
        ))}
      </ul>
    </li>
  );
}

function ReadyCard({ row }: { row: ImportOpportunity }) {
  const c = row.confirmation;
  return (
    <article className="rounded border border-slate-200 bg-white p-3" data-testid="import-ready-card">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold text-slate-900">{row.opportunity?.title ?? "(no está en la base)"}</h3>
        <V2Chip tone="ok">{PLAN_STATUS_LABELS[row.plan_status]}</V2Chip>
        <V2Chip tone={row.completeness === "complete" ? "ok" : "danger"}>
          {COMPLETENESS_LABELS[row.completeness]}
        </V2Chip>
        {row.opportunity && <V2Chip>etapa: {row.opportunity.stage}</V2Chip>}
      </header>
      <p className="mt-1 text-sm text-slate-700">
        <span className="font-medium">Institución confirmada:</span>{" "}
        {c.organization_name ?? c.organization_id ?? "—"} · {c.action} · por {c.confirmed_by} ({c.role}) el{" "}
        {dateTime(c.confirmed_at)}
      </p>
      <p className="text-xs text-slate-500">Impreso: {row.printed_addressee ?? row.printed_organization ?? "—"}</p>

      <table className="mt-2 w-full text-left text-sm [&_td]:px-2 [&_td]:py-1 [&_th]:px-2">
        <thead className="text-xs text-slate-500">
          <tr>
            <th>Cotización</th><th>Rev.</th><th>Estado</th><th>Enviada</th>
            <th>Documento canónico</th><th>Correo canónico</th><th>Duplicados</th>
          </tr>
        </thead>
        <tbody>
          {row.quotes.flatMap((q) =>
            q.revisions.map((rv) => {
              const pdf = rv.pdf_available ? importRevisionPdfUrl(rv.document_sha256) : null;
              return (
                <tr key={rv.document_sha256} className="border-t border-slate-100 align-top"
                  data-testid="import-revision">
                  <td className="font-mono">{q.quote_number}</td>
                  <td>{rv.revision_no ?? "—"}{rv.superseded_by_revision_no ? ` → ${rv.superseded_by_revision_no}` : ""}</td>
                  <td>{rv.status ?? "—"} <span className="text-xs text-slate-500">({rv.origin})</span></td>
                  <td>{dateTime(rv.sent_at)}</td>
                  <td>
                    <div>{rv.filename ?? "—"}</div>
                    <div className="font-mono text-xs text-slate-500">{shortSha(rv.document_sha256)}</div>
                    {pdf ? (
                      <a href={pdf} target="_blank" rel="noreferrer noopener" className="text-sky-700 underline">PDF</a>
                    ) : (
                      <span className="text-xs text-slate-400">PDF no disponible</span>
                    )}
                  </td>
                  <td className="font-mono text-xs">{rv.canonical_message?.replace("gmail_message:", "")}</td>
                  <td className="font-mono text-xs">
                    {rv.duplicate_messages.length
                      ? rv.duplicate_messages.map((m) => m.replace("gmail_message:", "")).join(", ")
                      : "—"}
                  </td>
                </tr>
              );
            }),
          )}
        </tbody>
      </table>

      <h4 className="mt-2 text-xs font-semibold uppercase text-slate-500">Evidencia Gmail</h4>
      <ul className="mt-1 space-y-1">
        {row.evidence.map((ev) => <EvidenceRow key={ev.dedupe_key} ev={ev} />)}
      </ul>

      <V2TechnicalDetails summary="Comprobaciones e identificadores">
        <ul className="text-xs">
          {row.checks.map((ch) => (
            <li key={ch.code}>{ch.ok ? "✓" : "✗"} {CHECK_LABELS[ch.code] ?? ch.code}</li>
          ))}
        </ul>
        <p className="font-mono text-xs">plan: {row.planned_opportunity_id} · base: {row.opportunity?.id} · recibos: {row.receipts_in_database}</p>
        {row.warnings.length > 0 && <pre className="text-xs">{JSON.stringify(row.warnings, null, 1)}</pre>}
      </V2TechnicalDetails>
    </article>
  );
}

function NotImportedTable({ rows, testId }: { rows: ImportOpportunity[]; testId: string }) {
  return (
    <table className="w-full text-left text-sm [&_td]:px-2 [&_td]:py-1 [&_th]:px-2" data-testid={testId}>
      <thead className="text-xs text-slate-500">
        <tr>
          <th>Institución impresa</th><th>Cotizaciones</th><th>Correos</th>
          <th>Confirmación</th><th>En la base</th><th>Motivos</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.planned_opportunity_id} className="border-t border-slate-100 align-top">
            <td>
              <div>{row.printed_organization ?? "—"}</div>
              <div className="text-xs text-slate-500">{row.printed_addressee}</div>
            </td>
            <td className="font-mono text-xs">{row.quotes.map((q) => q.quote_number).join(", ") || "—"}</td>
            <td className="text-xs">
              {row.evidence.map((ev) => (
                <div key={ev.dedupe_key}><GmailLink url={ev.gmail_url} label={ev.dedupe_key.replace("gmail_message:", "")} /></div>
              ))}
            </td>
            <td>
              <V2Chip tone={row.confirmation.state === "held" ? "danger" : "warn"}>
                {row.confirmation.state === "held" ? "retenida" : "pendiente"}
              </V2Chip>
            </td>
            <td>
              <V2Chip tone={row.completeness === "leaked" ? "danger" : "neutral"}>
                {COMPLETENESS_LABELS[row.completeness]}
              </V2Chip>
            </td>
            <td className="text-xs">{row.reasons.join(", ") || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function QuoteImportReviewPage() {
  const [review, setReview] = useState<ImportReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ImportReviewFilter>(EMPTY_IMPORT_FILTER);

  useEffect(() => {
    let live = true;
    fetchImportReview()
      .then((r) => live && setReview(r))
      .catch((e: unknown) => live && setError(formatMirrorLoadError("Importación de cotizaciones", e).message));
    return () => {
      live = false;
    };
  }, []);

  const rows = useMemo(() => (review ? filterImportRows(review.opportunities, filter) : []), [review, filter]);
  const ready = rows.filter((r) => r.plan_status === "ready");
  const waiting = rows.filter((r) => r.plan_status === "waiting");
  const held = rows.filter((r) => r.plan_status === "held");

  if (error) {
    return (
      <V2EmptyState title="No se pudo leer la revisión de importación"
        description={`${error} — la ruta existe sólo si la API se inició con ORIGENLAB_V2_IMPORT_REVIEW_PLAN_DIR.`} />
    );
  }
  if (!review) return <p className="p-4 text-sm text-slate-600">Cargando…</p>;

  const disposable = /^origenlab_test_[0-9a-f]{8}$/.test(review.database);
  return (
    <div className="space-y-4 p-4" data-testid="quote-import-review">
      <V2PageHeader
        title="Importación de cotizaciones"
        subtitle={`Plan ${shortSha(review.plan.sha256)} (${review.plan.version}, alcance «${review.plan.evidence_scope}») frente a la base ${review.database}`}
      />
      <p className={`rounded p-2 text-sm ${disposable ? "bg-amber-50 text-amber-900" : "bg-rose-50 text-rose-900"}`}>
        {disposable
          ? "Base desechable de ensayo. Sólo lectura: esta pantalla no escribe nada."
          : "Atención: esta base no es una base desechable de ensayo. Sólo lectura."}
      </p>
      <Counts review={review} />
      <V2Panel title="Invariantes de la importación">
        <Invariants review={review} />
        {review.unplanned.opportunities.length > 0 && (
          <p className="mt-2 text-xs text-slate-600">
            Fuera del plan (ya estaban en la base): {review.unplanned.opportunities.map((o) => o.title).join("; ")}.
            Cotizaciones fuera del plan: {review.unplanned.revisions.length}.
          </p>
        )}
      </V2Panel>
      <Filters value={filter} onChange={setFilter} />
      <V2Panel title="Listas — importadas" count={ready.length} testId="import-ready">
        {ready.length ? (
          <div className="space-y-3">{ready.map((r) => <ReadyCard key={r.planned_opportunity_id} row={r} />)}</div>
        ) : (
          <p className="text-sm text-slate-500">Ninguna con estos filtros.</p>
        )}
      </V2Panel>
      <V2Panel title="En espera — falta confirmar la institución" count={waiting.length} testId="import-waiting">
        <NotImportedTable rows={waiting} testId="import-waiting-table" />
      </V2Panel>
      <V2Panel title="Retenidas — bloqueadas por el plan" count={held.length} testId="import-held">
        <NotImportedTable rows={held} testId="import-held-table" />
      </V2Panel>
    </div>
  );
}
