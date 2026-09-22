/**
 * What an institution *is to OrigenLab commercially* — which is not what it is.
 *
 * Three facts get confused constantly and this module exists to keep them apart:
 *
 * 1. **Identity** — «Hielscher Ultrasonics» is an organization. That is what
 *    `attribute_sender_organization` records, and all it records.
 * 2. **Commercial role** — Hielscher is a supplier; a university that asks for a quote is a
 *    counterparty. That lives in `crm.organization_relationship` (`docs/DOMAIN.md` §2.3,
 *    worked example #5), it is durable, and **there is no command for it yet**.
 * 3. **Person** — who wrote the message. Nothing here creates or names one.
 *
 * Nothing in this file writes anything. It annotates the review surface so that an operator
 * choosing which institution the sender belongs to can see, before they choose, that their
 * choice settles (1) and says nothing about (2) or (3).
 *
 * **Why a supplier is known and a counterparty is inferred.** The six approved brands are a
 * closed list the business fixed and the public site validates; matching one is a lookup.
 * Everything else is not on that list, which is the absence of evidence and not evidence of
 * absence — so the counterparty reading is drawn from the *message*, not from the name, and
 * it is labelled as drawn.
 */

/**
 * The six brands OrigenLab works with, exactly as `apps/web/src/data/brands.ts` spells them.
 *
 * **This is a copy, and a test keeps it honest.** `commercialRole.test.ts` reads
 * `apps/web/src/data/brands.ts` from disk and fails if the two lists diverge, the same way
 * `apps/web/scripts/validate-brands.mjs` does for the public site. A copy that cannot drift
 * silently is preferable here to importing across two apps with separate builds — the
 * operator dashboard does not depend on the marketing site to render.
 *
 * Provenance of the list: the brand and home-page review of 2026-09-06, in which the
 * business fixed the six approved brands.
 */
export const SUPPLIER_BRAND_NAMES: readonly string[] = [
  "Hielscher Ultrasonics",
  "Ortoalresa",
  "IKA",
  "Adam Equipment",
  "Löser Messtechnik",
  "SERVA Electrophoresis",
];

export type CommercialRole = "supplier" | "requesting" | "unknown";

/** The same fold `evidenceCommands` uses: trimmed, whitespace-collapsed, lower-cased. */
function fold(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

const SUPPLIER_KEYS: ReadonlySet<string> = new Set(SUPPLIER_BRAND_NAMES.map(fold));

/** Whether this asserted name is one of the six approved supplier brands. */
export function isSupplierBrand(name: string): boolean {
  return SUPPLIER_KEYS.has(fold(name));
}

/**
 * The commercial role this asserted name reads as, given every name the same message asserts.
 *
 * - `supplier` — an exact match against the approved-brand list. A lookup, not a guess.
 * - `requesting` — **inferred**: this message names a supplier brand and this name is not
 *   it, so this is the other side of that conversation. It is the reading an operator would
 *   make anyway; naming it here means the surface can also say it is a reading.
 * - `unknown` — no supplier brand is named at all, so the message gives nothing to read
 *   from. Silence is the honest answer and the surface says nothing rather than guessing.
 */
export function commercialRoleOf(name: string, allAssertedNames: readonly string[]): CommercialRole {
  if (isSupplierBrand(name)) {
    return "supplier";
  }
  return allAssertedNames.some((candidate) => isSupplierBrand(candidate))
    ? "requesting"
    : "unknown";
}

export const COMMERCIAL_ROLE_LABELS: Record<CommercialRole, string> = {
  supplier: "Proveedor / fabricante",
  requesting: "Solicita / cotiza",
  unknown: "Rol comercial sin registrar",
};

/** Where the reading comes from, so the operator can weigh it instead of trusting it. */
export function commercialRoleProvenance(role: CommercialRole): string {
  switch (role) {
    case "supplier":
      return "Marca aprobada: es una de las seis con las que OrigenLab trabaja. Dato del negocio, no una inferencia de este correo.";
    case "requesting":
      return "Inferido: el correo nombra una marca proveedora y ésta no lo es. Es una lectura del mensaje, no un dato registrado.";
    case "unknown":
      return "El correo no nombra ninguna marca proveedora, así que no hay nada de dónde leer un rol.";
  }
}

/**
 * What annotating a role does **not** do, stated next to the annotation rather than implied.
 *
 * These are the three things an operator might reasonably assume follow from "this is the
 * institution that asks for quotes", and none of them does. `crm.organization_relationship`
 * has no command, so not one of these rows can be written from this workspace at all.
 *
 * The person refusal is deliberately **not** here: every preview that uses this list already
 * states it, and states it more precisely than a generic line could — what a decision does
 * and does not claim about a human being deserves the specific sentence, not a shared one.
 */
export const COMMERCIAL_ROLE_WRITES_NOTHING: readonly string[] = [
  "No escribe el rol comercial: crm.organization_relationship no tiene comando todavía.",
  "No abre prospecto ni oportunidad, y cotizar no se deduce de un correo.",
  "No otorga permiso de marketing: recibir un correo no es autorización para enviar.",
];
