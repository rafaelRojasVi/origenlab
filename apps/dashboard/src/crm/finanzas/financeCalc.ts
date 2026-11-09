/**
 * Pure calculation functions for the «Finanzas» module.
 *
 * All functions are side-effect-free: they take plain numbers/strings and return
 * plain values. This makes them trivially unit-testable without mocking anything.
 */

// ── aging ─────────────────────────────────────────────────────────────────────

export type AgeBucket = "0-30" | "31-60" | "61-90" | "90+";

/** Map days outstanding → bucket label. */
export function ageBucket(daysOld: number): AgeBucket {
  if (daysOld <= 30) return "0-30";
  if (daysOld <= 60) return "31-60";
  if (daysOld <= 90) return "61-90";
  return "90+";
}

export const AGE_BUCKET_LABELS: Record<AgeBucket, string> = {
  "0-30": "0–30 d",
  "31-60": "31–60 d",
  "61-90": "61–90 d",
  "90+": "+90 d",
};

// ── Chilean IVA ───────────────────────────────────────────────────────────────

export const IVA_RATE = 0.19;

/** Net amount → con IVA (round to integer CLP). */
export function withIva(net: number): number {
  return Math.round(net * (1 + IVA_RATE));
}

/** IVA-inclusive amount → net (round to integer CLP). */
export function withoutIva(total: number): number {
  return Math.round(total / (1 + IVA_RATE));
}

// ── pace ──────────────────────────────────────────────────────────────────────

/**
 * Business days (Mon–Fri, no holidays) remaining in the month of `today`,
 * including `today` itself.
 */
export function businessDaysLeft(today: string): number {
  const [y, m, d] = today.split("-").map(Number);
  const start = new Date(Date.UTC(y, m - 1, d, 12));
  const month = start.getUTCMonth();
  let n = 0;
  const cur = new Date(start);
  while (cur.getUTCMonth() === month) {
    const w = cur.getUTCDay();
    if (w !== 0 && w !== 6) n += 1;
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return n;
}

/** Remaining amount to reach `target` and the daily pace required. */
export function requiredPaceForTarget(
  sold: number,
  target: number,
  today: string,
): { remaining: number; days: number; perDay: number | null } {
  const remaining = Math.max(0, target - sold);
  const days = businessDaysLeft(today);
  return { remaining, days, perDay: days === 0 ? null : Math.ceil(remaining / days) };
}

// ── factoring simulator ───────────────────────────────────────────────────────

/**
 * Factoring cost estimate.
 * @param amount   Total invoice amount (IVA-inclusive CLP)
 * @param days     Days until expected payment (1–90)
 * @param monthlyRate  Factor's monthly rate as a decimal (e.g. 0.015 = 1.5 %)
 * @param fixedFee Fixed administrative fee in CLP (default 0)
 */
export function factoringCost(
  amount: number,
  days: number,
  monthlyRate: number,
  fixedFee = 0,
): { cost: number; netReceived: number; effectiveMonthlyRate: number } {
  const months = days / 30;
  const variableCost = Math.round(amount * monthlyRate * months);
  const cost = variableCost + fixedFee;
  return {
    cost,
    netReceived: amount - cost,
    effectiveMonthlyRate: months > 0 ? variableCost / amount / months : 0,
  };
}

// ── socios ────────────────────────────────────────────────────────────────────

export type PartnerRule = {
  rule_name: string;
  arriendo_clp: number;
  tasa_sebastian: number | null;
  sueldo_tatiana: number;
  arriendo_pct_empresa: number;
  split_pct: number;
};

export type SociosResult = {
  utilidad_bruta: number;        // margen - gastos_fijos
  tatiana: number;
  sebastian: number;
  rule_name: string;
};

/**
 * Compute the «a repartir» split between partners for a closed month.
 *
 * Rules (from finance_model.md):
 * - actual_50_50: `a_repartir / 2` each
 * - opcion_a: Tatiana pays arriendo; company pays `arriendo_pct_empresa * arriendo_clp`
 * - opcion_b: `sueldo_tatiana` to Tatiana first, then 50/50 of the residual
 * - opcion_c: same as B with explicit sueldo_tatiana parameter
 * - opcion_d: Sebastián provides capital at `tasa_sebastian` per month; residual split 50/50
 */
export function computeApartir(
  margen_bruto: number,
  gastos_fijos: number,
  rule: PartnerRule,
): SociosResult {
  const utilidad = margen_bruto - gastos_fijos;

  switch (rule.rule_name) {
    case "actual_50_50": {
      const half = Math.round(utilidad * rule.split_pct);
      return { utilidad_bruta: utilidad, tatiana: half, sebastian: utilidad - half, rule_name: rule.rule_name };
    }
    case "opcion_a": {
      // Company absorbs arriendo_pct_empresa fraction of arriendo before split
      const empresa_arriendo = Math.round(rule.arriendo_clp * rule.arriendo_pct_empresa);
      const residual = utilidad - empresa_arriendo;
      const tatiana = Math.round(residual * rule.split_pct) + (rule.arriendo_clp - empresa_arriendo);
      const sebastian = residual - Math.round(residual * rule.split_pct);
      return { utilidad_bruta: utilidad, tatiana, sebastian, rule_name: rule.rule_name };
    }
    case "opcion_b":
    case "opcion_c": {
      const sueldo = rule.sueldo_tatiana;
      const residual = utilidad - sueldo;
      const tat_residual = Math.round(residual * rule.split_pct);
      return {
        utilidad_bruta: utilidad,
        tatiana: sueldo + tat_residual,
        sebastian: residual - tat_residual,
        rule_name: rule.rule_name,
      };
    }
    case "opcion_d": {
      // Sebastián lends capital at tasa_sebastian monthly rate; Tatiana gets the residual 50/50
      const tasa = rule.tasa_sebastian ?? 0;
      // capital at risk ≈ utilidad (simplified) — in a real model this would be the investment amount
      const interes_sebastian = Math.round(margen_bruto * tasa);
      const residual = utilidad - interes_sebastian;
      const half = Math.round(residual * rule.split_pct);
      return {
        utilidad_bruta: utilidad,
        tatiana: half,
        sebastian: interes_sebastian + (residual - half),
        rule_name: rule.rule_name,
      };
    }
    default: {
      const half = Math.round(utilidad * 0.5);
      return { utilidad_bruta: utilidad, tatiana: half, sebastian: utilidad - half, rule_name: rule.rule_name };
    }
  }
}

// ── bar chart helpers ─────────────────────────────────────────────────────────

/** Format a month string "2026-11-01" as a 3-letter abbreviation ("nov"). */
export function monthAbbr(month: string): string {
  const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const m = Number(month.slice(5, 7)) - 1;
  return MONTHS[m] ?? month;
}

/**
 * Build a 6-entry bar chart series from the API's monthly_history array.
 * Fills in zero-amount months for a continuous 6-month window ending at `currentMonth`.
 */
export function buildBarSeries(
  history: Array<{ month: string; neto: number }>,
  currentMonth: string,
): Array<{ month: string; neto: number; isCurrent: boolean }> {
  const map = new Map(history.map((h) => [h.month, h.neto]));
  const [y, m] = currentMonth.split("-").map(Number);
  return Array.from({ length: 6 }, (_, i) => {
    const offset = i - 5; // -5, -4, -3, -2, -1, 0
    let month = m + offset;
    let year = y;
    while (month <= 0) { month += 12; year -= 1; }
    while (month > 12) { month -= 12; year += 1; }
    const key = `${year}-${String(month).padStart(2, "0")}-01`;
    return { month: key, neto: map.get(key) ?? 0, isCurrent: key === currentMonth };
  });
}
