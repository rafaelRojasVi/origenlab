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
  PREVIEW_ONLY_REASON,
  commandPreviews,
} from "../lib/evidenceCommands";

import {
  fetchV2Contacts,
  fetchV2EvidenceRecords,
  fetchV2Organizations,
  fetchV2Prospects,
} from "../api/v2Client";
import type { V2AttachableUsage, V2EvidenceRecord, V2Page } from "../api/v2Types";
import { V2EmptyState } from "../components/v2/V2EmptyState";
import { V2PageHeader } from "../components/v2/V2PageHeader";
import { sourceKindLabel } from "../lib/crmV2Browser";
import {
  addressesOf,
  domainCounts,
  identityHeadline,
  isRoleMailbox,
  matchForAddress,
  organizationHeadline,
  organizationNamesOf,
  reviewDate,
  reviewFlags,
  reviewStatusLabel,
  hasRegisteredPerson,
  REVIEW_FLOW,
} from "../lib/evidenceReview";
import type { TriageCategory } from "../lib/evidenceTriage";
import {
  recordsInCategory,
  triageCategoryCaption,
  triageCategoryLabel,
  triageCounts,
  triageOf,
  TRIAGE_CATEGORIES,
} from "../lib/evidenceTriage";
import { formatMirrorLoadError } from "../lib/humanizeApiError";

const PAGE_SIZE = 50;

/** The batch the workspace opens on, plus the escape hatch that shows everything. */
type TriageFilter = TriageCategory | "all";

/**
 * Said once, at the top, and never softened further down.
 *
 * The triage is the only thing on this page that offers an opinion, so it is also the only
 * thing that has to disclaim one. The wording matters: not "provisional", not "draft" --
 * *nothing is written*. A reviewer who believes the category was saved will stop checking it.
 */
const TRIAGE_DISCLAIMER =
  "La categoría es una sugerencia de lectura calculada en el momento a partir del remitente y el asunto. No se guarda en ninguna parte, no cambia el estado del registro y no decide nada: si te parece mal, ignórala.";

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

/**
 * The batch selector, with every batch's size on it — including the batches it is hiding.
 *
 * This is the one place where the workspace could quietly become a filter that loses
 * evidence, so it is built not to be: the counts of all four categories are always visible,
 * `Todo` is always one click away, and nothing is ever removed from the underlying page.
 */
function TriageFilterStrip({
  counts,
  total,
  selected,
  onSelect,
}: {
  counts: Record<TriageCategory, number>;
  total: number;
  selected: TriageFilter;
  onSelect: (filter: TriageFilter) => void;
}) {
  const tabs: { id: TriageFilter; label: string; count: number; caption: string }[] = [
    ...TRIAGE_CATEGORIES.map((category) => ({
      id: category as TriageFilter,
      label: triageCategoryLabel(category),
      count: counts[category],
      caption: triageCategoryCaption(category),
    })),
    {
      id: "all" as TriageFilter,
      label: "Todo",
      count: total,
      caption: "La cola completa, sin ordenar por categoría. Nada se oculta nunca.",
    },
  ];
  return (
    <div className="space-y-2" data-testid="review-triage-filter">
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {tabs.map((tab) => {
          const active = tab.id === selected;
          return (
            <li key={tab.id}>
              <button
                type="button"
                aria-pressed={active}
                data-testid={`review-triage-tab-${tab.id}`}
                onClick={() => onSelect(tab.id)}
                className={`h-full w-full rounded-lg border px-3 py-2 text-left text-xs transition ${
                  active
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-slate-200 bg-[var(--color-card)] text-slate-800 hover:border-slate-400"
                }`}
              >
                <span className="block text-lg font-semibold">{number(tab.count)}</span>
                <span className="block font-medium">{tab.label}</span>
                <span
                  className={`mt-1 block ${active ? "text-slate-200" : "text-[var(--color-muted)]"}`}
                >
                  {tab.caption}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      <p className="text-xs text-[var(--color-muted)]" data-testid="review-triage-disclaimer">
        {TRIAGE_DISCLAIMER}
      </p>
    </div>
  );
}

function TriageChip({ record }: { record: V2EvidenceRecord }) {
  const verdict = triageOf(record);
  const tone: Record<TriageCategory, string> = {
    commercial: "bg-emerald-100 text-emerald-800",
    counterparty_auto_reply: "bg-sky-100 text-sky-800",
    vendor_notice: "bg-slate-200 text-slate-700",
    unclassified: "bg-amber-100 text-amber-800",
  };
  return (
    <span
      data-testid="review-triage-chip"
      className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${tone[verdict.category]}`}
    >
      {triageCategoryLabel(verdict.category)}
    </span>
  );
}

/** The reasons behind the chip, so the reviewer can disagree with something specific. */
function TriageReasons({ record }: { record: V2EvidenceRecord }) {
  const verdict = triageOf(record);
  return (
    <section className="space-y-1">
      <h4 className="text-xs font-semibold uppercase text-[var(--color-muted)]">
        Por qué se sugirió «{triageCategoryLabel(verdict.category)}»
      </h4>
      <ul className="list-disc space-y-1 pl-5 text-[var(--color-muted)]">
        {verdict.reasons.map((reason) => (
          <li key={`${reason.kind}-${reason.text}`} data-testid="review-triage-reason">
            {reason.text}
          </li>
        ))}
      </ul>
    </section>
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
      <TriageReasons record={record} />
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
                        <Chip tone="ok">Persona registrada: {hit.person_display_name}</Chip>
                      ) : (
                        <Chip tone="warn">Sin titular registrado</Chip>
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
          <p
            className="rounded-md bg-white px-2 py-1 text-[11px] text-[var(--color-muted)]"
            data-testid="review-organization-rule"
          >
            Una institución aparece aquí de dos maneras y de ninguna otra: porque el mensaje
            nombra <strong>exactamente</strong> el nombre de una institución ya registrada, o
            porque el dominio del remitente está <strong>registrado</strong> como dominio de
            una institución. Un dominio que se parece al nombre de una institución no la
            nombra: ese parecido es interpretación, y la interpretación la haces tú.
          </p>
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

      <CommandPanel record={record} domainShareCount={counts.get(record.from_domain ?? "") ?? 1} />
    </div>
  );
}

/**
 * The two relationships an operator may assert, with the claim each one makes spelled out.
 *
 * The second exists because it had to be added: until this change the schema had no shape
 * for "this institution operates this address and we do not know whose it is", so every
 * attribution wrote `shared_mailbox` — which says a named person's mailbox is a desk several
 * people read. The wording here is the point of the control, not decoration around it.
 */
const RELATIONSHIP_CHOICES: ReadonlyArray<{
  value: V2AttachableUsage;
  label: string;
  explanation: string;
}> = [
  {
    value: "shared_mailbox",
    label: "Buzón compartido de la institución",
    explanation:
      "Afirma que varias personas leen esta casilla: ventas@, secretaria@, produccion@. Es una afirmación sobre el mundo, no una etiqueta.",
  },
  {
    value: "individual_owner_unknown",
    label: "De una persona, sin identificar",
    explanation:
      "Registra que la institución opera la dirección y nada más. No crea ninguna persona, no dice de quién es la casilla y no afirma que sea compartida. Es el estado honesto mientras no haya un nombre.",
  },
];

/**
 * The four review commands, each showing what it would record and why it can or cannot run.
 *
 * The note and the organization are local state and go nowhere. That is the point of a
 * preview: the operator can compose a real decision, read back exactly what it would write,
 * and discover a mismatch before anything durable happens.
 */
function CommandPanel({
  record,
  domainShareCount,
}: {
  record: V2EvidenceRecord;
  domainShareCount: number;
}) {
  const [note, setNote] = useState("");
  const [selectedOrganizationId, setSelectedOrganizationId] = useState<string | null>(null);
  // Which *asserted name* the operator says is the sender's. Null until they say, and it
  // stays null for a single-name message too: "there is only one" is a reason to pick it
  // quickly, not a reason for the surface to pick it for them.
  const [senderAssertionId, setSenderAssertionId] = useState<string | null>(null);
  // What the address *is* to the institution. Null until the operator says, and it stays
  // null: the two values claim opposite things about a person, so neither may be preselected.
  const [relationship, setRelationship] = useState<V2AttachableUsage | null>(null);
  const [overrideNote, setOverrideNote] = useState("");
  const unresolvedAddresses = record.assertions.filter(
    (assertion) =>
      assertion.kind === "contact_address" && assertion.resolution === "unresolved",
  );
  const namedInstitutions = record.assertions.filter(
    (assertion) =>
      assertion.kind === "organization_name" && assertion.resolution === "unresolved",
  );
  const previews = useMemo(
    () =>
      commandPreviews({
        record,
        domainShareCount,
        selectedOrganizationId,
        selectedOrganizationAssertionId: senderAssertionId,
        addressRelationship: relationship,
        sharedMailboxOverrideNote: overrideNote,
        note,
      }),
    [
      record,
      domainShareCount,
      selectedOrganizationId,
      senderAssertionId,
      relationship,
      overrideNote,
      note,
    ],
  );

  return (
    <section className="space-y-3 border-t border-slate-200 pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase text-[var(--color-muted)]">
          Decisiones posibles
        </h4>
        <span className="text-[11px] text-[var(--color-muted)]" data-testid="preview-only-reason">
          {PREVIEW_ONLY_REASON}
        </span>
      </div>

      <label className="block text-xs">
        <span className="text-[var(--color-muted)]">Motivo (obligatorio, queda auditado)</span>
        <input
          type="text"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          data-testid="command-note"
          placeholder="Por qué decides esto"
          className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-xs"
        />
      </label>

      {record.organization_matches.length > 0 || record.domain_organization ? (
        <label className="block text-xs">
          <span className="text-[var(--color-muted)]">
            Institución seleccionada (ninguna por omisión)
          </span>
          <select
            value={selectedOrganizationId ?? ""}
            onChange={(event) => setSelectedOrganizationId(event.target.value || null)}
            data-testid="command-organization"
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-xs"
          >
            <option value="">— sin seleccionar —</option>
            {record.organization_matches.map((match) => (
              <option key={match.organization_id} value={match.organization_id}>
                {match.name} (nombre idéntico)
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {unresolvedAddresses.length > 0 ? (
        <fieldset
          className="space-y-1 rounded-md border border-slate-200 p-2 text-xs"
          data-testid="address-relationship"
        >
          <legend className="px-1 text-[var(--color-muted)]">
            ¿Qué es esta dirección para la institución? (sin opción por omisión)
          </legend>
          <p className="text-[11px] text-[var(--color-muted)]">
            Las dos opciones afirman cosas opuestas sobre una persona real. Ninguna viene
            marcada: elegir es parte de la decisión, no un trámite previo.
          </p>
          {RELATIONSHIP_CHOICES.map((choice) => (
            <label
              key={choice.value}
              className="flex items-start gap-2"
              data-testid="address-relationship-choice"
            >
              <input
                type="radio"
                name={`address-relationship-${record.source_record_id}`}
                checked={relationship === choice.value}
                onChange={() => setRelationship(choice.value)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium text-slate-900">{choice.label}</span>
                <span className="block text-[11px] text-[var(--color-muted)]">
                  {choice.explanation}
                </span>
              </span>
            </label>
          ))}
          {relationship ? (
            <button
              type="button"
              onClick={() => {
                setRelationship(null);
                setOverrideNote("");
              }}
              className="text-[11px] underline decoration-dotted"
            >
              Deshacer la elección
            </button>
          ) : null}
          {relationship === "shared_mailbox" &&
          unresolvedAddresses.some((assertion) => !isRoleMailbox(assertion.value_norm)) ? (
            <label className="mt-1 block">
              <span className="text-[var(--color-muted)]">
                Esta dirección no tiene un nombre de función reconocido. Escribe cómo sabes
                que varias personas la leen: queda en el evento.
              </span>
              <input
                type="text"
                value={overrideNote}
                onChange={(event) => setOverrideNote(event.target.value)}
                data-testid="shared-mailbox-override"
                placeholder="Cómo sabes que es una mesa y no una persona"
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-xs"
              />
            </label>
          ) : null}
        </fieldset>
      ) : null}

      {namedInstitutions.length > 0 ? (
        <fieldset className="space-y-1 rounded-md border border-slate-200 p-2 text-xs">
          <legend className="px-1 text-[var(--color-muted)]">
            ¿Cuál de estas instituciones es la del remitente?
          </legend>
          <p className="text-[11px] text-[var(--color-muted)]">
            Un correo puede nombrar al proveedor y al cliente final. Sólo una es la del
            remitente; las demás quedan sin resolver y el registro sigue pendiente.
          </p>
          {namedInstitutions.map((assertion) => {
            const match = record.organization_matches.find(
              (row) => row.value_norm === assertion.value_norm,
            );
            return (
              <label
                key={assertion.assertion_id}
                className="flex items-start gap-2"
                data-testid="sender-institution-choice"
              >
                <input
                  type="radio"
                  name={`sender-institution-${record.source_record_id}`}
                  checked={senderAssertionId === assertion.assertion_id}
                  onChange={() => setSenderAssertionId(assertion.assertion_id)}
                  className="mt-0.5"
                />
                <span>
                  <span className="font-medium text-slate-900">«{assertion.value_norm}»</span>{" "}
                  {match ? (
                    <Chip tone="neutral">Ya existe: {match.name} — se confirmaría</Chip>
                  ) : (
                    <Chip tone="neutral">No existe — se crearía con ese texto exacto</Chip>
                  )}
                </span>
              </label>
            );
          })}
          {senderAssertionId ? (
            <button
              type="button"
              onClick={() => setSenderAssertionId(null)}
              className="text-[11px] underline decoration-dotted"
            >
              Deshacer la elección
            </button>
          ) : null}
        </fieldset>
      ) : null}

      <ul className="space-y-3">
        {previews.map((preview) => (
          <li
            key={preview.id}
            data-testid={`command-preview-${preview.id}`}
            data-availability={preview.availability}
            className="rounded-md border border-slate-200 p-2"
          >
            <div className="flex flex-wrap items-center gap-2">
              <PreviewAction label={preview.label} reason={PREVIEW_ONLY_REASON} />
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
            {preview.leavesUnresolved.length > 0 ? (
              <ul
                className="mt-1 list-disc pl-4 text-xs text-sky-800"
                data-testid="command-leaves-unresolved"
              >
                {preview.leavesUnresolved.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : null}
            {preview.request ? (
              <details className="mt-1" data-testid="command-request">
                <summary className="cursor-pointer text-xs text-[var(--color-muted)]">
                  La petición exacta que se enviaría
                </summary>
                {/*
                  The request itself, not a description of it. A prose preview can agree with
                  what the operator meant while the body disagrees with both, and the only
                  place that shows up is the durable row afterwards.
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
        ))}
      </ul>
    </section>
  );
}

export function EvidenceReviewPage() {
  const [page, setPage] = useState<V2Page<V2EvidenceRecord> | null>(null);
  const [totals, setTotals] = useState<Totals | null>(null);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  // The workspace opens on the work. Twenty commercial messages sitting between a Google
  // drip campaign and a Tidio signup is the reason nobody reads this queue.
  const [filter, setFilter] = useState<TriageFilter>("commercial");
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
  // Domain sharing is counted across the **whole** page, not the filtered view: two
  // messages from one institution matter even when the triage put them in different batches.
  const counts = useMemo(() => domainCounts(records), [records]);
  const triage = useMemo(() => triageCounts(records), [records]);
  const visible = useMemo(
    () => (filter === "all" ? records : recordsInCategory(records, filter)),
    [records, filter],
  );

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
          caption="Son direcciones, no personas. Que una dirección exista no dice quién la usa, y esta superficie no lo establece."
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

          <TriageFilterStrip
            counts={triage}
            total={records.length}
            selected={filter}
            onSelect={setFilter}
          />

          {visible.length === 0 ? (
            <p
              className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-[var(--color-muted)]"
              data-testid="review-triage-empty"
            >
              Ningún correo de esta página cayó en «{filter === "all" ? "Todo" : triageCategoryLabel(filter)}».
              Los {number(records.length)} registros de la página siguen ahí — elige otra
              categoría o «Todo» para verlos.
            </p>
          ) : null}

          <table className="w-full table-auto text-left text-sm" data-testid="review-queue-table">
            <thead className="text-xs uppercase text-[var(--color-muted)]">
              <tr>
                <th className="py-2">Remitente</th>
                <th>Categoría sugerida</th>
                <th>Asunto</th>
                <th>Fecha</th>
                <th>Identidad</th>
                <th>Institución</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((record) => {
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
                      <td>
                        <TriageChip record={record} />
                      </td>
                      <td className="text-slate-800">{record.subject ?? "—"}</td>
                      <td className="whitespace-nowrap text-[var(--color-muted)]">
                        {reviewDate(record.message_date ?? record.acquired_at)}
                      </td>
                      <td>
                        <Chip
                          tone={hasRegisteredPerson(record) ? "ok" : "warn"}
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
                        <td colSpan={6} className="p-0">
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
                {filter === "all" ? null : ` · mostrando ${number(visible.length)} de esta página`}
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
            reason="Requiere una dirección y una institución ya revisadas por un operador."
          />
        </div>
      </section>
    </div>
  );
}
