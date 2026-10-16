/**
 * The one client for the quote API: the workspace and numbering reads, the live preview, the
 * five quote commands (create, save a draft, approve, void, new revision) and «Preferencias»
 * (`set-cost-parameter`).
 *
 * The browser never prices a quote. Every number it shows is a string the API computed (the
 * `computed` block of a preview, a save or the workspace, or a stored total); `Decimal` below is
 * only a name for that string. Do not parse it into a float to calculate with.
 *
 * This module owns the seven POST paths: the six `/v2/commands/<…>` strings and
 * `/v2/quotes/preview`, pinned by `src/test/noWritePolicy.test.ts`, through one internal
 * `post()`. The preview writes nothing (it prices a draft and answers), so it carries no
 * `Idempotency-Key`; every command carries one. Upstream everything here is sales/admin only
 * (a viewer gets 403 `role_may_not_view_costs`) and the commands need
 * `ORIGENLAB_V2_COMMANDS_ENABLED`.
 */
import { OperatorApiError, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import type { PriceHistory } from "../catalog/catalogApi";
import { parseRefusalEnvelope } from "../../api/refusalEnvelope";

/** A number as the API prints it: a decimal string, never a JS number. */
export type Decimal = string;

/* ── the draft the editor sends ───────────────────────────────────────────── */

/** The API's `ItemType`; absent means the product's kind, else "other". */
export type QuoteItemType = "equipment" | "accessory" | "consumable" | "spare_part" | "service" | "other";

export interface QuoteDraftItem {
  product_id?: string | null;
  description: string;
  quantity: Decimal;
  item_type?: QuoteItemType | null;
  unit_cost?: Decimal | null;
  cost_currency?: "CLP" | "EUR" | "USD" | null;
  supplier_organization_id?: string | null;
  cost_observation_id?: string | null;
  margin_mode: "markup" | "margin" | "none";
  margin_pct?: Decimal | null;
  typed_unit_price?: Decimal | null;
  is_principal?: boolean;
  line_note_es?: string | null;
  /**
   * Client-only, never sent (`sendableDraft` strips them; the API forbids extra fields): the line's
   * identity in the editor, which ties it to its priced line, and what the operator typed that
   * cannot be read (shown as the field's problem; nothing is sent while it stands).
   */
  uid?: string;
  input_problems?: { quantity?: string };
}

export interface QuoteDraftExpense {
  label: string;
  amount: Decimal;
  currency: "CLP" | "EUR" | "USD";
  /** Client-only, never sent: the row's identity in the editor (see `QuoteDraftItem.uid`). */
  uid?: string;
}

/** A condition beyond the fixed ones («+ Agregar otra condición»). */
export interface QuoteDraftTerm {
  label_es: string;
  text_es: string;
  /** Client-only, never sent: the row's identity in the editor (see `QuoteDraftItem.uid`). */
  uid?: string;
}

export interface QuoteDraftTerms {
  delivery_terms?: string | null;
  validity_days?: number | null;
  payment_terms?: string | null;
  warranty_terms_es?: string | null;
  delivery_place_es?: string | null;
  installation_terms_es?: string | null;
  client_notes_es?: string | null;
  terms_extra: QuoteDraftTerm[];
}

/** Whom the quote goes to: a case participant, a CRM person or contact point, or a typed address. */
export interface QuoteRecipientPick {
  participant_id?: string | null;
  person_id?: string | null;
  contact_point_id?: string | null;
  email?: string | null;
  name?: string | null;
}

export interface QuoteDraft {
  items: QuoteDraftItem[];
  manual_expenses: QuoteDraftExpense[];
  expense_method: "profile" | "dhl_calc" | "manual";
  allocation_mode: "principal" | "proportional_cost";
  principal_rule: "highest_cost_imported_equipment" | "first_eur_equipment_over_threshold" | "manual";
  fx_overrides: { currency: "EUR" | "USD"; clp_per_unit: Decimal; reason: string }[];
  terms: QuoteDraftTerms;
  recipient?: QuoteRecipientPick | null;
}

/* ── what the API computed ────────────────────────────────────────────────── */

export interface ComputedLine {
  line_no: number;
  item_type: string;
  product_id: string | null;
  unit_cost: Decimal | null;
  cost_currency: string | null;
  supplier_organization_id?: string | null;
  cost_observation_id?: string | null;
  imported: boolean;
  margin_mode: string;
  margin_pct: Decimal | null;
  cost_clp_unit: Decimal | null;
  expense_to_line: Decimal;
  unit_base: Decimal | null;
  unit_price: Decimal | null;
  line_total: Decimal | null;
  real_markup: Decimal | null;
  gross_margin: Decimal | null;
  margin_state: string;
  is_principal: boolean;
}

export interface Computed {
  lines: ComputedLine[];
  expenses: {
    line_no: number;
    label: string;
    amount: Decimal;
    currency: string;
    method: string;
    allocated_to_line_no: number | null;
    calc_inputs?: unknown;
  }[];
  totals: {
    net: Decimal;
    iva: Decimal;
    total: Decimal;
    cost_base: Decimal;
    expenses_clp: Decimal;
    markup: Decimal | null;
    margin: Decimal | null;
    margin_state: string;
  };
  principal_line_no: number | null;
  fx: { currency: string; clp_per_unit: Decimal; as_of: string; source: string }[];
  warnings: string[];
}

/* ── GET /v2/quotes/{id} ──────────────────────────────────────────────────── */

export interface QuoteWorkspaceParticipant {
  id: string;
  name: string | null;
  email: string | null;
  primary: boolean;
  role: string;
}

/** Someone the quote can go to: a case participant first, then people on the case's emails. */
export interface QuoteWorkspaceContact {
  participant_id: string | null;
  person_id: string | null;
  contact_point_id: string | null;
  name: string | null;
  email: string | null;
  source: string;
}

export interface QuoteWorkspaceRevision {
  id: string;
  revision_no: number;
  /** The printed label, or null for a historical number outside the series' form. */
  label: string | null;
  status: string;
  origin: string;
  version: number;
  grand_total: Decimal | null;
  approved_at: string | null;
  sent_at: string | null;
  superseded_by_revision_no: number | null;
}

/** A line stored on an issued revision (what approval froze). */
export interface QuoteStoredLine {
  line_no: number;
  line_kind: string;
  description: string | null;
  product_id: string | null;
  quantity: Decimal | null;
  item_type: string | null;
  cost_currency: string | null;
  unit_cost: Decimal | null;
  fx_rate: Decimal | null;
  fx_as_of: string | null;
  fx_source: string | null;
  margin_mode: string | null;
  margin_pct: Decimal | null;
  unit_price_qc: Decimal | null;
  line_total_qc: Decimal | null;
  is_principal: boolean | null;
  allocated_to_line_no: number | null;
  expense_method: string | null;
  supplier_organization_id: string | null;
  cost_observation_id: string | null;
  line_note_es: string | null;
}

/**
 * The revision the page shows. An authored draft carries `draft` / `computed` / `computed_error` /
 * `saved` / `needs_saving`; any other revision (approved, sent, void, imported) carries its stored
 * `lines` and the frozen `party_snapshot` instead. The API still answers 200 when a draft cannot
 * be priced: `computed` is then null and `computed_error` says why.
 */
export interface QuoteWorkspaceCurrent {
  id: string;
  revision_no: number;
  status: string;
  origin: string;
  version: number;
  quote_currency: string | null;
  tax_rate: Decimal | null;
  subtotal: Decimal | null;
  tax_total: Decimal | null;
  grand_total: Decimal | null;
  totals_computed_at: string | null;
  validity_days: number | null;
  valid_until: string | null;
  approved_at: string | null;
  sent_at: string | null;
  superseded_by_revision_no: number | null;
  recipient_participant_id: string | null;
  expense_method: string | null;
  expense_allocation_mode: string | null;
  principal_rule: string | null;
  updated_at: string | null;
  label: string | null;
  // authored draft only
  draft?: QuoteDraft & { recipient_participant_id?: string | null };
  computed?: Computed | null;
  computed_error?: { code: string; message: string } | null;
  saved?: {
    totals: { net: Decimal | null; iva: Decimal | null; total: Decimal | null };
    lines: { line_no: number; unit_price_qc: Decimal | null; line_total_qc: Decimal | null }[];
  };
  needs_saving?: boolean;
  // any other revision
  lines?: QuoteStoredLine[];
  party_snapshot?: unknown | null;
}

export interface QuoteWorkspace {
  quote: { id: string; quote_number: string; opportunity_id: string };
  case: {
    id: string;
    title: string | null;
    stage: string;
    closed: boolean;
    institution: { id: string; name: string; rut: string | null } | null;
    participants: QuoteWorkspaceParticipant[];
    contacts: QuoteWorkspaceContact[];
    lo_que_pidio: string | null;
  };
  revisions: QuoteWorkspaceRevision[];
  current: QuoteWorkspaceCurrent | null;
  suggestions: {
    mentioned: { product_id: string | null; model: string | null; name: string; model_key: string }[];
    kits: { model_key: string; product_id: string | null; name: string; share: Decimal; documents: number }[];
  };
  /** Null when the principal line has no product with a model key. */
  price_history: PriceHistory | null;
  /** The company's preferences as the rail applies them (`inputs.quote_preferences`); absent from an older API. */
  params?: QuotePreferences;
}

/**
 * `params` of the workspace: each the current cost parameter, else the engine's fallback (40 %
 * equipment, 45 % the rest; a 25–45 % band). Ratios as decimal strings ("0.25" = 25 %). The
 * deviation warning has no fallback: null, nothing warns.
 */
export interface QuotePreferences {
  markups?: Partial<Record<QuoteItemType, Decimal>>;
  margin_target_min?: Decimal | null;
  margin_target_max?: Decimal | null;
  price_deviation_warn_pct?: Decimal | null;
}

/** `GET /v2/quotes/numbering`: what the next number would be, without reserving it. */
export interface QuoteNumbering {
  configured: boolean;
  seed: number | null;
  next_number: string | null;
  highest_known: number | null;
  ignored_mail_numbers: string[];
}

/* ── refusals ─────────────────────────────────────────────────────────────── */

/**
 * The API or Worker refused a quote request. `code` is the command's own machine code: the API
 * answers `{error:{code:<generic: forbidden, conflict, validation_error…>, message, details:{code,
 * message, …}, request_id}}` (errors.py), so it is `error.details.code`, else the generic
 * `error.code` (a body validation error, the Worker's `{error:{code,message}}`), else an older
 * `{detail:{code,message}}`; null when the body is not an envelope (`parseRefusalEnvelope`). It is
 * an `OperatorApiError`, so `classifyError` (and `ResourceGate`) treats it like any other API
 * refusal. A viewer is 403 with `role_may_not_view_costs`.
 */
export class QuoteRefusal extends OperatorApiError {
  constructor(
    status: number,
    public readonly code: string | null,
    message: string,
    /** The envelope's `details`, e.g. `{validation_errors}` on a 422; null when there are none. */
    public readonly details: Record<string, unknown> | null = null,
  ) {
    super(message, status);
    this.name = "QuoteRefusal";
  }

  /** What FastAPI said was wrong with the body (English, with the field's `loc`); empty when none. */
  get validationErrors(): QuoteValidationError[] {
    const raw = this.details?.validation_errors;
    return Array.isArray(raw) ? (raw as QuoteValidationError[]) : [];
  }
}

/** One entry of a 422's `details.validation_errors`. */
export interface QuoteValidationError {
  loc: string[];
  msg: string;
  type: string;
}

async function refusalOf(res: Response): Promise<QuoteRefusal> {
  notifyIfSessionRefused(res.status);
  const text = await res.text().catch(() => "");
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(text);
  } catch {
    /* not JSON */
  }
  const { code, message, details } = parseRefusalEnvelope(parsed);
  return new QuoteRefusal(res.status, code, message || text || res.statusText || `HTTP ${res.status}`, details);
}

/* ── transport ────────────────────────────────────────────────────────────── */

export const QUOTE_COMMAND_PATHS = {
  create: "/v2/commands/create-quote",
  save: "/v2/commands/save-quote-draft",
  approve: "/v2/commands/approve-quote-revision",
  void: "/v2/commands/void-quote-revision",
  revise: "/v2/commands/create-quote-revision",
  setParameter: "/v2/commands/set-cost-parameter",
} as const;

const PREVIEW_PATH = "/v2/quotes/preview";
const NUMBERING_PATH = "/v2/quotes/numbering";

export function newQuoteCommandKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `quote-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(operatorApiUrl(path), {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) throw await refusalOf(res);
  return (await res.json()) as T;
}

/** The only POST site: a command carries its `Idempotency-Key`; the preview passes `null`. */
async function post<T>(path: string, body: unknown, idempotencyKey: string | null, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  if (idempotencyKey !== null) headers["Idempotency-Key"] = idempotencyKey;
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await refusalOf(res);
  return (await res.json()) as T;
}

/* ── GET /v2/quotes (the module's list) ───────────────────────────────────── */

export type QuoteListStatus = "draft" | "approved" | "sent" | "all";

/** One quote at its current revision. `status` is its bucket: an in-review revision is a draft. */
export interface QuoteListRow {
  quote_id: string;
  quote_number: string;
  /** The printed label («00042A-26»), or null for a historical number outside the series' form. */
  label: string | null;
  status: "draft" | "approved" | "sent" | "void";
  /** `authored` here, `historical_import` for a quote brought in from before the app. */
  origin: string;
  opportunity: { id: string; title: string | null; closed: boolean };
  client: { organization_id: string; name: string } | null;
  /** The first item descriptions and how many there are: the revision's own, or its read PDF's (`amount_source`). */
  items: { first: string[]; count: number };
  /** The revision's own total, IVA included; null without one (an imported quote). */
  grand_total: Decimal | null;
  /** The neto its PDF said, when the revision has no totals or lines of its own and the PDF was read. */
  net_total?: Decimal | null;
  /** Where `items` and the amount come from: the revision itself, its read PDF, or nowhere (null). Absent from an older API. */
  amount_source?: "revision" | "pdf_read" | null;
  currency: string | null;
  updated_at: string;
}

export interface QuoteListPage {
  rows: QuoteListRow[];
  /** How many quotes each status has under the same search (the filter does not narrow them). */
  counts: { draft: number; approved: number; sent: number; void: number; all: number };
  next_cursor: string | null;
}

/** One page of the list, newest first. A cursor the API did not give answers 422 `bad_cursor`. */
export function fetchQuoteList(
  { status, q, cursor, limit = 50 }: { status: QuoteListStatus; q?: string; cursor?: string | null; limit?: number },
  signal?: AbortSignal,
): Promise<QuoteListPage> {
  const params = new URLSearchParams({ status, limit: String(limit) });
  if (q) params.set("q", q);
  if (cursor) params.set("cursor", cursor);
  return get<QuoteListPage>(`/v2/quotes?${params.toString()}`, signal);
}

/* ── GET /v2/quotes/clients (the new-quote dialog's suggestions) ───────────── */

/** An open case of a suggested client. `requested_at`: when its email came in, else when it was opened. */
export interface QuoteClientCase {
  id: string;
  title: string | null;
  stage: string;
  requested_at: string | null;
}

export interface QuoteClientOrganization {
  id: string;
  name: string;
  kind: string | null;
  domains: string[];
  open_cases: QuoteClientCase[];
}

/** A person (`person_id`), or an address with no person (`contact_point_id` only). */
export interface QuoteClientPerson {
  person_id: string | null;
  contact_point_id: string | null;
  name: string | null;
  email: string | null;
  organization: { id: string; name: string } | null;
  open_cases: QuoteClientCase[];
}

export interface QuoteClientSuggestions {
  organizations: QuoteClientOrganization[];
  people: QuoteClientPerson[];
}

/** Up to eight institutions and eight people for `q` (two characters at least; shorter is 422 `query_too_short`). */
export const fetchQuoteClients = (q: string, signal?: AbortSignal): Promise<QuoteClientSuggestions> =>
  get<QuoteClientSuggestions>(`/v2/quotes/clients?${new URLSearchParams({ q }).toString()}`, signal);

/* ── reads ────────────────────────────────────────────────────────────────── */

export const fetchQuoteWorkspace = (quoteId: string): Promise<QuoteWorkspace> =>
  get<QuoteWorkspace>(`/v2/quotes/${encodeURIComponent(quoteId)}`);

export const fetchQuoteNumbering = (): Promise<QuoteNumbering> => get<QuoteNumbering>(NUMBERING_PATH);

/** Prices a draft without saving it. `revisionId` lets the API use the revision's own tax rate. */
export const previewQuote = (revisionId: string, draft: QuoteDraft, signal?: AbortSignal): Promise<{ computed: Computed }> =>
  post(PREVIEW_PATH, { revision_id: revisionId, draft }, null, signal);

/* ── commands ─────────────────────────────────────────────────────────────── */

/** The kinds a new institution may be (`NewOrganizationKind` upstream). */
export type NewClientKind = "company" | "university" | "public_institution" | "hospital" | "laboratory" | "other";

/** Whom a quote is for when no case is named: exactly one of these. */
export type QuoteClientPick =
  | { organization_id: string }
  | { person_id: string }
  | { contact_point_id: string }
  | { email: string; name?: string }
  | { new_organization: { name: string; kind?: NewClientKind; email?: string } };

/**
 * A quote for a client: the API finds the client's open case (`opportunity_id` names the one
 * chosen) or opens a Solicitud (`new_case`, or when the client has none). Several open cases and
 * none chosen is a 409 `case_choice_needed` with `details.candidates`.
 */
export interface CreateQuoteForClient {
  client: QuoteClientPick;
  opportunity_id?: string;
  new_case?: true;
}

export interface CreatedQuote {
  quote_id: string;
  quote_number: string;
  revision_id: string;
  version: number;
  /** For a client: the case used or opened, and whether it (or the institution) was made now. */
  opportunity_id?: string;
  case_created?: boolean;
  organization_id?: string | null;
  /** The institution's name, as the CRM has it (null without one). */
  organization_name?: string | null;
  organization_created?: boolean;
}

/** A quote on a case (`opportunityId`), or for a client (`CreateQuoteForClient`). */
export const createQuote = (target: string | CreateQuoteForClient, key: string = newQuoteCommandKey()): Promise<CreatedQuote> =>
  post(QUOTE_COMMAND_PATHS.create, typeof target === "string" ? { opportunity_id: target } : target, key);

export const saveQuoteDraft = (
  revisionId: string,
  expectedVersion: number,
  draft: QuoteDraft,
  key: string = newQuoteCommandKey(),
): Promise<{ revision_id: string; version: number; computed: Computed }> =>
  post(QUOTE_COMMAND_PATHS.save, { revision_id: revisionId, expected_version: expectedVersion, draft }, key);

export const approveQuoteRevision = (
  revisionId: string,
  expectedVersion: number,
  key: string = newQuoteCommandKey(),
): Promise<{ version: number; quote_number: string; revision_label: string; negotiated_costs_skipped: unknown[] }> =>
  post(QUOTE_COMMAND_PATHS.approve, { revision_id: revisionId, expected_version: expectedVersion }, key);

export const voidQuoteRevision = (
  revisionId: string,
  expectedVersion: number,
  reason: string,
  key: string = newQuoteCommandKey(),
): Promise<{ version: number }> =>
  post(QUOTE_COMMAND_PATHS.void, { revision_id: revisionId, expected_version: expectedVersion, reason }, key);

export const createQuoteRevision = (
  quoteId: string,
  key: string = newQuoteCommandKey(),
): Promise<{ revision_id: string; revision_no: number; revision_label?: string }> =>
  post(QUOTE_COMMAND_PATHS.revise, { quote_id: quoteId }, key);

/* ── «Preferencias»: one cost parameter ───────────────────────────────────── */

/**
 * `set-cost-parameter`: a new current value with its reason; the previous one stays as history.
 * Numbers go in `value` (a decimal string), the printed `quote_default_*` texts in `value_text`.
 * `expected_current_id` is the current row the operator saw (null when the parameter is unset);
 * another one current since is a 409 `parameter_changed`. Only administration sets
 * `quote_number_seed` (403 `role_may_not_set_numbering`). Mounted with the catalog
 * (`ORIGENLAB_V2_QUOTING_ENABLED`), sales and admin.
 */
export interface SetCostParameterBody {
  key: string;
  value?: Decimal;
  value_text?: string;
  reason: string;
  expected_current_id: string | null;
}

export const setCostParameter = (
  body: SetCostParameterBody,
  key: string = newQuoteCommandKey(),
): Promise<{ ok: true; cost_parameter_id: string; key: string }> => post(QUOTE_COMMAND_PATHS.setParameter, body, key);
