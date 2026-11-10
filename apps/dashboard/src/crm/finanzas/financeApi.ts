/**
 * Client for the «Finanzas» module: the Mes KPI cards, expenses, supplier orders, settings,
 * and four commands (`upsert-expense`, `upsert-supplier-order`, `set-monthly-target`,
 * `set-partner-rule`).
 *
 * Everything is served by `GET /v2/workspace/finance/*` (read) and `POST /v2/commands/*`
 * (commands). Both are listed in the proxy allowlist; the commands require Deciding + Idempotency-Key.
 */

import { OperatorApiError, fetchJsonGet, notifyIfSessionRefused, operatorApiUrl } from "../../api/operatorClient";
import { refusalOfError } from "../../api/refusalEnvelope";
import { newCaseCommandKey } from "../caseCommands";

export const FINANCE_NOTE = "Desde «Finanzas».";

export const financeRefusal = refusalOfError;

// ── types ─────────────────────────────────────────────────────────────────────

export interface PipelineStage {
  weight: number;
  count: number;
  total_clp: number;
  weighted_clp: number;
}

export type AgeBucket = "0-30" | "31-60" | "61-90" | "90+";

export interface MesData {
  mes: string;        // "2026-11-01"
  today: string;      // "2026-11-09"
  vendido: { net: number; meta_net: number; pct_meta: number };
  por_cobrar: { total: number; count: number; by_age: Record<AgeBucket, number> };
  pipeline: { ponderado_net: number; by_stage: Record<string, PipelineStage> };
  capital_atado: { total: number; count: number };
  gastos: { total: number; count: number };
  monthly_history: Array<{ month: string; neto: number }>;
}

export interface Expense {
  expense_id: string;
  month: string;
  category: string;
  amount_clp: number;
  paid_by: string;
  notes: string | null;
  source: string;
  external_ref: string | null;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface ExpenseList {
  month: string;
  items: Expense[];
  total_clp: number;
}

export interface SupplierOrder {
  order_id: string;
  supplier_name: string;
  ordered_on: string;
  paid_on: string | null;
  amount_original: number;
  currency: string;
  amount_clp: number | null;
  expected_arrival_on: string | null;
  received_on: string | null;
  notes: string | null;
  created_at: string;
  opportunity_id: string | null;
  quote_id: string | null;
  opportunity_title: string | null;
}

export interface SupplierOrderList {
  items: SupplierOrder[];
  in_flight_total: number;
  in_flight_count: number;
}

export interface FinanceSettings {
  monthly_target: { net: number; notes: string | null; month: string | null; is_default: boolean };
  partner_rule: {
    rule_name: string;
    arriendo_clp: number;
    tasa_sebastian: number | null;
    sueldo_tatiana: number;
    arriendo_pct_empresa: number;
    split_pct: number;
    valid_from: string;
    notes: string | null;
  } | null;
}

// ── reads ─────────────────────────────────────────────────────────────────────

// ── Resumen v3 types ──────────────────────────────────────────────────────────

export interface CajaHorizon {
  horizon: 30 | 60 | 90;
  until: string;      // ISO date
  entra: number;
  sale: number;
  /** Confirmed unreceived supplier orders (amount_clp). */
  sale_confirmado: number;
  /** Estimated cost: Σ pedidos_estimados + current-month expenses. */
  sale_estimado: number;
  neto: number;
}

export interface ResumenMeta {
  facturado_net: number;
  ganado_sin_facturar_net: number;
  ganado_sin_facturar_count: number;
  en_negociacion_net: number;
  en_negociacion_count: number;
  meta_net: number;
  dias_habiles_restantes: number;
  ritmo_necesario: number;
  mes: string;        // "2026-11-01"
}

export interface ClienteLento {
  org: string;
  days: number;
  hint: string | null;
}

export interface ResumenIndicadores {
  dias_promedio_cobro: number;
  dias_promedio_cobro_count: number;
  margen_mes_pct: number;
  margen_mes_costo: number;
  margen_mes_subtotal: number;
  clientes_lentos: ClienteLento[];
}

export interface CobrarHoyItem {
  invoice_id: string;
  folio: number;
  issued_on: string;
  client_name: string;
  balance_iva: number;
  avg_days_client: number;
  expected_pay_date: string;
  age_days: number;
  opportunity_id: string | null;
  opportunity_title: string | null;
}

export interface PedidoEstimado {
  opportunity_id: string;
  title: string;
  supplier_name: string;
  supplier_org_id: string | null;
  estimated_cost_clp: number;
}

export interface ResumenData {
  caja: CajaHorizon[];
  meta: ResumenMeta;
  indicadores: ResumenIndicadores;
  cobrar_hoy: CobrarHoyItem[];
  pedidos_estimados: PedidoEstimado[];
}

export const fetchFinanceResumen = () =>
  fetchJsonGet<ResumenData>(operatorApiUrl("/v2/workspace/finance/resumen"));

export const fetchFinanceMes = () =>
  fetchJsonGet<MesData>(operatorApiUrl("/v2/workspace/finance/mes"));

export const fetchFinanceExpenses = (month?: string) =>
  fetchJsonGet<ExpenseList>(operatorApiUrl("/v2/workspace/finance/expenses", month ? { month } : undefined));

export const fetchFinanceSupplierOrders = (opportunityId?: string) =>
  fetchJsonGet<SupplierOrderList>(
    operatorApiUrl("/v2/workspace/finance/supplier-orders", opportunityId ? { opportunity_id: opportunityId } : undefined)
  );

export const fetchFinanceSettings = () =>
  fetchJsonGet<FinanceSettings>(operatorApiUrl("/v2/workspace/finance/settings"));

// ── commands ──────────────────────────────────────────────────────────────────

async function post(path: string, body: Record<string, unknown>, idempotencyKey: string): Promise<Record<string, unknown>> {
  const res = await fetch(operatorApiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ ...body, note: FINANCE_NOTE }),
  });
  if (!res.ok) {
    notifyIfSessionRefused(res.status);
    const text = await res.text().catch(() => "");
    throw new OperatorApiError(text || res.statusText || `HTTP ${res.status}`, res.status);
  }
  return (await res.json()) as Record<string, unknown>;
}

export interface UpsertExpenseArgs {
  expense_id?: string;
  month: string;    // "2026-11-01"
  category: string;
  amount_clp: number;
  paid_by?: string;
  expense_notes?: string;
}

export async function upsertExpense(args: UpsertExpenseArgs, idempotencyKey = newCaseCommandKey()) {
  return post("/v2/commands/upsert-expense", args as unknown as Record<string, unknown>, idempotencyKey);
}

export interface UpsertSupplierOrderArgs {
  order_id?: string;
  opportunity_id?: string;
  quote_id?: string;
  supplier_name: string;
  ordered_on: string;
  paid_on?: string;
  amount_original: number;
  currency?: string;
  amount_clp?: number;
  expected_arrival_on?: string;
  received_on?: string;
  order_notes?: string;
}

export async function upsertSupplierOrder(args: UpsertSupplierOrderArgs, idempotencyKey = newCaseCommandKey()) {
  return post("/v2/commands/upsert-supplier-order", args as unknown as Record<string, unknown>, idempotencyKey);
}

export interface SetMonthlyTargetArgs {
  month: string;              // "2026-11-01"
  sales_target_net: number;
  target_notes?: string;
}

export async function setMonthlyTarget(args: SetMonthlyTargetArgs, idempotencyKey = newCaseCommandKey()) {
  return post("/v2/commands/set-monthly-target", args as unknown as Record<string, unknown>, idempotencyKey);
}

export interface SetPartnerRuleArgs {
  valid_from: string;
  rule_name: string;
  arriendo_clp?: number;
  tasa_sebastian?: number;
  sueldo_tatiana?: number;
  arriendo_pct_empresa?: number;
  split_pct?: number;
  rule_notes?: string;
}

export async function setPartnerRule(args: SetPartnerRuleArgs, idempotencyKey = newCaseCommandKey()) {
  return post("/v2/commands/set-partner-rule", args as unknown as Record<string, unknown>, idempotencyKey);
}
