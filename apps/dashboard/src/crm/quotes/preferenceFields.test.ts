/**
 * The Cotizador's preferences: how each parameter's value is shown, edited and read back. All on
 * the string (no float): a percentage moves the decimal point, an amount drops its thousands dots.
 */
import { describe, expect, it } from "vitest";
import {
  PREFERENCE_GROUPS,
  bandProblem,
  compareDecimal,
  displayPreference,
  editTextOf,
  parsePreference,
  sameValue,
  type PreferenceKind,
} from "./preferenceFields";

describe("percentages — the API's ratio as a decimal string", () => {
  it.each([
    ["0.40", "40 %", "40"],
    ["0.4", "40 %", "40"],
    ["0.125", "12,5 %", "12,5"],
    ["0.0125", "1,25 %", "1,25"],
    ["0.005", "0,5 %", "0,5"],
    ["1", "100 %", "100"],
    ["1.5", "150 %", "150"],
    ["0", "0 %", "0"],
    ["0.333333", "33,3333 %", "33,3333"],
  ])("%s shows as «%s» and edits as «%s»", (ratio, shown, edit) => {
    expect(displayPreference("percent", ratio)).toBe(shown);
    expect(editTextOf("percent", ratio)).toBe(edit);
  });

  it.each([
    ["40", "0.4"],
    ["40 %", "0.4"],
    ["40%", "0.4"],
    ["12,5", "0.125"],
    ["12.5", "0.125"],
    [" 1,25 % ", "0.0125"],
    ["0,5", "0.005"],
    ["100", "1"],
    ["0", "0"],
    ["33,3333", "0.333333"],
  ])("«%s» is sent as %s", (text, ratio) => {
    expect(parsePreference("percent", text)).toEqual({ ok: true, value: ratio });
  });

  it("round-trips every edge decimal exactly", () => {
    for (const ratio of ["0.40", "0.125", "0.0125", "0.005", "0.999", "0.333333", "2.25"]) {
      const back = parsePreference("percent", editTextOf("percent", ratio));
      expect(back.ok && sameValue("percent", back.value, ratio)).toBe(true);
    }
  });

  it("takes at most four decimals (six on the ratio: numeric(18,6)), never rounding silently", () => {
    expect(parsePreference("percent", "12,3456")).toEqual({ ok: true, value: "0.123456" });
    expect(parsePreference("percent", "12,34560")).toEqual({ ok: true, value: "0.123456" });
    for (const text of ["12,34567", "0,00001", "1.23456"]) {
      expect(parsePreference("percent", text)).toEqual({ ok: false, problem: "Usa como máximo 4 decimales en un porcentaje." });
    }
  });

  it("goes up to 999 %", () => {
    expect(parsePreference("percent", "999")).toEqual({ ok: true, value: "9.99" });
    expect(parsePreference("percent", "0999")).toEqual({ ok: true, value: "9.99" });
    for (const text of ["999,5", "1000", "123456789"]) {
      expect(parsePreference("percent", text)).toEqual({ ok: false, problem: "Un porcentaje va de 0 a 999 %." });
    }
  });

  it("refuses what is not a percentage, in Spanish", () => {
    for (const text of ["", "abc", "-5", "1,2,3", "12,5,", "40 %%x"]) {
      const out = parsePreference("percent", text);
      expect(out.ok).toBe(false);
      if (!out.ok) expect(out.problem).toMatch(/porcentaje/);
    }
  });
});

describe("peso amounts", () => {
  it("shows the API's amount with the CRM's formatter", () => {
    expect(displayPreference("clp", "1234567")).toBe("$ 1.234.567");
    expect(displayPreference("clp", "45000.000000")).toBe("$ 45.000");
  });

  it("edits with thousands dots and reads «1.234.567» back as 1234567", () => {
    expect(editTextOf("clp", "1234567")).toBe("1.234.567");
    expect(editTextOf("clp", "45000.000000")).toBe("45.000");
    expect(editTextOf("clp", "999")).toBe("999");
    expect(parsePreference("clp", "1.234.567")).toEqual({ ok: true, value: "1234567" });
    expect(parsePreference("clp", "$ 1.234.567")).toEqual({ ok: true, value: "1234567" });
    expect(parsePreference("clp", "1234567")).toEqual({ ok: true, value: "1234567" });
    expect(parsePreference("clp", "1.234.567,00")).toEqual({ ok: true, value: "1234567" });
    expect(parsePreference("clp", "0")).toEqual({ ok: true, value: "0" });
  });

  it("takes at most twelve integer digits, so nothing overflows numeric(18,6)", () => {
    expect(parsePreference("clp", "999.999.999.999")).toEqual({ ok: true, value: "999999999999" });
    expect(parsePreference("clp", "1.000.000.000.000")).toEqual({ ok: false, problem: "El monto es demasiado grande: como máximo 12 dígitos." });
    expect(parsePreference("clp", "0001234")).toEqual({ ok: true, value: "1234" });
  });

  it("asks for whole pesos", () => {
    for (const text of ["", "12,5", "1.5", "doce", "-100"]) {
      const out = parsePreference("clp", text);
      expect(out.ok).toBe(false);
      if (!out.ok) expect(out.problem).toMatch(/pesos/);
    }
  });
});

describe("days and the numbering seed: whole numbers in the API's range", () => {
  it("days are 1–365", () => {
    expect(displayPreference("days", "30")).toBe("30 días");
    expect(displayPreference("days", "1")).toBe("1 día");
    expect(editTextOf("days", "30")).toBe("30");
    expect(parsePreference("days", "45")).toEqual({ ok: true, value: "45" });
    expect(parsePreference("days", "45 días")).toEqual({ ok: true, value: "45" });
    for (const text of ["0", "366", "2,5", "treinta", ""]) expect(parsePreference("days", text).ok).toBe(false);
  });

  it("the seed is 1–99999", () => {
    expect(displayPreference("seed", "42")).toBe("42");
    expect(parsePreference("seed", "00042")).toEqual({ ok: true, value: "42" });
    for (const text of ["0", "100000", "4,2", ""]) expect(parsePreference("seed", text).ok).toBe(false);
  });
});

describe("printed texts", () => {
  it("are sent trimmed, and must say something", () => {
    expect(parsePreference("text", "  30 días hábiles [PLAZO]  ")).toEqual({ ok: true, value: "30 días hábiles [PLAZO]" });
    expect(parsePreference("text", "   ").ok).toBe(false);
    expect(parsePreference("text", "x".repeat(1001)).ok).toBe(false);
  });
});

describe("sameValue: an unchanged field has nothing to save", () => {
  it.each<[PreferenceKind, string, unknown, boolean]>([
    ["percent", "0.4", "0.40", true],
    ["percent", "0.45", "0.4", false],
    ["clp", "45000", "45000.000000", true],
    ["days", "30", "30", true],
    ["text", "Contado", "Contado", true],
    ["text", "Contado", undefined, false],
    ["percent", "0.4", undefined, false],
  ])("%s %s vs %s → %s", (kind, a, b, same) => {
    expect(sameValue(kind, a, b)).toBe(same);
  });
});

describe("compareDecimal: on the digits, no float", () => {
  it.each<[string, string, -1 | 0 | 1]>([
    ["0.25", "0.45", -1],
    ["0.45", "0.25", 1],
    ["0.4", "0.40", 0],
    ["1", "0.999999", 1],
    ["0.1", "0.099999", 1],
    ["10", "9.5", 1],
    ["0.000001", "0", 1],
  ])("%s vs %s → %s", (a, b, out) => expect(compareDecimal(a, b)).toBe(out));

  it("is null for what is not a plain decimal", () => expect(compareDecimal("abc", "1")).toBeNull());
});

describe("bandProblem: the margin band's ends never cross", () => {
  const current = { margin_target_max: { value: "0.40" } };
  it("refuses a minimum above the current maximum, or the fallback 45 % when it is unset", () => {
    expect(bandProblem("margin_target_min", "0.5", current)).toBe("El mínimo no puede ser mayor que el máximo (40 %).");
    expect(bandProblem("margin_target_min", "0.5", {})).toBe("El mínimo no puede ser mayor que el máximo (45 %).");
    expect(bandProblem("margin_target_min", "0.4", current)).toBeNull();
    expect(bandProblem("margin_target_min", "0.125", current)).toBeNull();
  });

  it("refuses a maximum below the current minimum, or the fallback 25 % when it is unset", () => {
    expect(bandProblem("margin_target_max", "0.2", { margin_target_min: { value: "0.30" } })).toBe("El máximo no puede ser menor que el mínimo (30 %).");
    expect(bandProblem("margin_target_max", "0.2", {})).toBe("El máximo no puede ser menor que el mínimo (25 %).");
    expect(bandProblem("margin_target_max", "0.25", {})).toBeNull();
  });

  it("says nothing for any other key", () => expect(bandProblem("default_markup_equipment", "9", {})).toBeNull());
});

describe("the groups", () => {
  it("cover the spec's fields with the API's keys, and only the seed is admin-only", () => {
    const keys = PREFERENCE_GROUPS.flatMap((g) => g.fields.map((f) => f.key));
    expect(keys).toEqual([
      "default_markup_equipment",
      "default_markup_accessory",
      "default_markup_consumable",
      "default_markup_spare_part",
      "default_markup_service",
      "margin_target_min",
      "margin_target_max",
      "price_deviation_warn_pct",
      "profile_freight_clp",
      "profile_agent_clp",
      "profile_customs_clp",
      "profile_transport_clp",
      "quote_default_delivery_terms",
      "quote_default_validity_days",
      "quote_default_payment_terms",
      "quote_default_warranty",
      "quote_default_delivery_place",
      "quote_default_installation",
      "quote_number_seed",
    ]);
    const adminOnly = PREFERENCE_GROUPS.flatMap((g) => g.fields).filter((f) => f.adminOnly).map((f) => f.key);
    expect(adminOnly).toEqual(["quote_number_seed"]);
    // numbering.py: max(seed, highest known + 1); a lower seed has no effect.
    const seedNote = PREFERENCE_GROUPS.find((g) => g.title === "Numeración")!.note!;
    expect(seedNote).toMatch(/mayor entre este número y el más alto ya usado \+ 1/);
    expect(seedNote).toMatch(/un valor menor que ése no tiene efecto/);
    expect(seedNote).toMatch(/sólo puede adelantar el inicio/);
  });
});
