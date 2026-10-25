/**
 * The one client for «Prospectos» (spec 2026-10-23): the list and its views, one prospect's full
 * profile, its logo (bytes the worker stored — the dashboard never loads the organization's site),
 * «Más tarde» / «Descartar» / «Deshacer» (`review-prospect`), «Contactar» (`contact-prospect`) and
 * «Editar» (`edit-prospect`, a person's correction to the card; owner 2026-10-25).
 *
 * Not `/v2/prospects` (the lead-stage opportunities). The commands need a `sales` or `admin`
 * operator and an `Idempotency-Key`; this module owns their paths (pinned by
 * `src/test/noWritePolicy.test.ts`) through one internal `post()`.
 */
import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import { envelopeOfError, refusalOfError } from "../../api/refusalEnvelope";

export type ProspectView = "nuevos" | "conocidos" | "mas_tarde" | "contactados" | "descartados";
export type ProspectVerdict = "contactar" | "mas_tarde" | "descartar" | "reabrir";

export interface ProspectLinks {
  website: string | null;
  linkedin_person: string | null;
  linkedin_org: string | null;
  orcid: string | null;
  scholar: string | null;
}

export interface OrganizationCandidate {
  id: string;
  name: string;
}

export interface ProspectItem {
  id: string;
  person_name: string | null;
  role: string | null;
  organization_name: string;
  unit: string | null;
  source: string;
  fund_label: string | null;
  amount_label: string | null;
  awarded_on: string | null;
  why_now: string;
  equipment: string[];
  email: string | null;
  email_source_url: string | null;
  email_source_year: number | null;
  /** Where a person found the email they added («Editar»); a text, not a link. */
  email_source_note?: string | null;
  phone: string | null;
  links: ProspectLinks;
  has_logo: boolean;
  confidence: "alta" | "media" | "baja" | null;
  score: number;
  state: "new" | "known";
  reasons: string[];
  organization_candidates: OrganizationCandidate[];
  latest_verdict: { verdict: ProspectVerdict; reason: string | null; remind_on: string | null; reviewed_at: string } | null;
  returns_today: boolean;
  acquired_at: string;
  /** Who last corrected the card, and when; null when nobody has. */
  edited?: { at: string; by: string | null } | null;
  /** The last Gmail draft prepared for this prospect, if any. */
  email_draft?: { draft_id: string; drafted_at: string; gmail_url: string | null } | null;
}

export interface ProspectCounts {
  nuevos: number;
  por_revisar: number;
  contactados_mes: number;
  vuelven_hoy: number;
  omitidos_bajas: number;
}

export interface ProspectList {
  counts: ProspectCounts;
  items: ProspectItem[];
  has_more: boolean;
}

export interface ProspectDetailData extends ProspectItem {
  project_title: string | null;
  sources: [string, string][];
  history: { verdict: ProspectVerdict; reason: string | null; remind_on: string | null; reviewed_at: string; reviewed_by: string | null }[];
}

export const PAGE_SIZE = 25;

export interface ProspectSearchRun {
  id: string;
  trigger: "daily" | "manual";
  status: "done" | "failed";
  started_at: string | null;
  finished_at: string | null;
  counts: { added?: number; skipped?: number; no_email?: number; low_fit?: number; pool_left?: number; error?: string };
  error: string | null;
}

export interface ProspectSearchStatus {
  last_run: ProspectSearchRun | null;
  running: boolean;
  unreviewed: number;
}

/** A fresh key for one command attempt; the same attempt retried keeps it. */
export function newProspectCommandKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `prospect-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** The API's refusal (code, message, status) behind an error; null for an error that is not the API's. */
export const prospectRefusal = refusalOfError;

/** The institutions to choose from when «Contactar» was refused as `organization_ambiguous`. */
export function ambiguousCandidates(err: unknown): OrganizationCandidate[] | null {
  const envelope = envelopeOfError(err);
  if (envelope.code !== "organization_ambiguous") return null;
  const raw = envelope.details?.candidates;
  return Array.isArray(raw) ? (raw as OrganizationCandidate[]) : [];
}

export const fetchProspects = (view: ProspectView, offset = 0) =>
  fetchJsonGet<ProspectList>(
    operatorApiUrl("/v2/prospect-findings", { view, limit: String(PAGE_SIZE), offset: String(offset) }),
  );

export const fetchProspect = (id: string) =>
  fetchJsonGet<ProspectDetailData>(operatorApiUrl(`/v2/prospect-findings/${encodeURIComponent(id)}`));

export const prospectLogoUrl = (id: string) => operatorApiUrl(`/v2/prospect-findings/${encodeURIComponent(id)}/logo`);

async function post(path: string, body: Record<string, unknown>, idempotencyKey: string): Promise<Record<string, unknown>> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return (await res.json()) as Record<string, unknown>;
}

/** «Más tarde» (a future date), «Descartar» (a reason) or «Deshacer» after descartar (`reabrir`). */
export const reviewProspect = (
  body: { finding_id: string; verdict: "mas_tarde" | "descartar" | "reabrir"; reason?: string; remind_on?: string },
  idempotencyKey: string = newProspectCommandKey(),
) => post("/v2/commands/review-prospect", body, idempotencyKey);

/** «Contactar»: the person, their email and the institution into the CRM, as a prospect. */
export const contactProspect = (
  body: { finding_id: string; organization_id?: string; create_new_organization?: boolean },
  idempotencyKey: string = newProspectCommandKey(),
) => post("/v2/commands/contact-prospect", body, idempotencyKey);

/** The fields «Editar» may change; a null clears one. */
export type ProspectChanges = Partial<{
  person_name: string | null;
  role: string | null;
  organization_name: string;
  unit: string | null;
  email: string | null;
  email_source_note: string | null;
  email_source_url: string | null;
  phone: string | null;
  website: string | null;
  linkedin_person: string | null;
  linkedin_org: string | null;
  orcid: string | null;
  scholar: string | null;
  why_now: string | null;
  equipment: string[] | null;
  fund_label: string | null;
  amount_label: string | null;
}>;

/** «Editar»: a person's correction to the card; only the changed fields are sent. */
export const editProspect = (
  body: { finding_id: string; changes: ProspectChanges },
  idempotencyKey: string = newProspectCommandKey(),
) => post("/v2/commands/edit-prospect", body, idempotencyKey);

/** «Buscar ahora» status header: last run, running flag, unreviewed count. */
export const fetchSearchStatus = () =>
  fetchJsonGet<ProspectSearchStatus>(operatorApiUrl("/v2/prospect-findings/search-status"));

/** «Buscar ahora»: enqueue a manual search run. Returns `{queued, run_id?}`. */
export const requestProspectSearch = (
  idempotencyKey: string = newProspectCommandKey(),
) => post("/v2/commands/request-prospect-search", {}, idempotencyKey) as Promise<{ queued: boolean; run_id?: string; reason?: string }>;

export interface ProspectEmailPreview {
  finding_id: string;
  to: string | null;
  subject: string;
  text: string;
  html: string;
  person_name: string | null;
  fund_label: string | null;
}

export interface PrepareProspectEmailResult {
  command: string;
  finding_id: string;
  draft_id: string;
  message_id: string;
  gmail_url: string;
  to: string;
  subject: string;
  signature: boolean;
  labels_applied: boolean;
  contacted: boolean;
  event_ids: string[];
  idempotency_key: string;
  command_receipt_id: string;
  replayed: boolean;
}

/** «Preparar correo»: preview the intro email (no Gmail fetch, signature placeholder shown). */
export const fetchEmailPreview = (id: string) =>
  fetchJsonGet<ProspectEmailPreview>(operatorApiUrl(`/v2/prospect-findings/${encodeURIComponent(id)}/email-preview`));

/** «Preparar correo»: create a Gmail draft, never send it. */
export const prepareProspectEmail = (
  body: { finding_id: string; again?: boolean },
  idempotencyKey: string = newProspectCommandKey(),
) => post("/v2/commands/prepare-prospect-email", body, idempotencyKey) as unknown as Promise<PrepareProspectEmailResult>;
