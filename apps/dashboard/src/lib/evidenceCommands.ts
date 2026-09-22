/**
 * What each review decision *would* record — computed, never sent.
 *
 * The API now has a command boundary (`POST /v2/commands/*`). This module is the dashboard's
 * half of it and it is deliberately only half: it turns a record and an assertion into a
 * plain-language account of the durable rows a decision would write, which preconditions are
 * satisfied, and which are not. It builds no request, imports no client and knows no URL.
 *
 * **Why preview first.** Every address in the real queue belongs to somebody who wrote to
 * OrigenLab, and a promotion is durable and audited. The cheapest moment to discover that a
 * command says something different from what the operator believed is before it is wired to
 * anything — so the preview exists, is unit-tested against fixtures, and the button that
 * would send it stays disabled until a person decides otherwise.
 *
 * **The preview never decides.** `availability` reports what the *data* allows. It never
 * chooses a command, never orders them by preference and never marks one recommended. Where
 * a heuristic has an opinion — this address does not look like a desk, several records share
 * this domain — it is attached as a `caution`, in words, next to the action rather than
 * instead of it.
 */

import type { V2AttachableUsage, V2EvidenceRecord, V2RecordAssertion } from "../api/v2Types";
import { isConsumerDomain, isRoleMailbox, organizationNamesOf } from "./evidenceReview";
import {
  COMMERCIAL_ROLE_LABELS,
  COMMERCIAL_ROLE_WRITES_NOTHING,
  commercialRoleOf,
  commercialRoleProvenance,
} from "./commercialRole";

/** The five commands, named exactly as the API names them. */
export type EvidenceCommandId =
  | "keep_evidence_pending"
  | "confirm_organization"
  | "create_organization"
  | "attach_contact_address"
  | "attribute_sender_organization";

export type CommandAvailability = "available" | "blocked";

export interface CommandPreview {
  id: EvidenceCommandId;
  /** The button's words. */
  label: string;
  /** One sentence: what durable fact this records. */
  intent: string;
  availability: CommandAvailability;
  /**
   * Why it is blocked, or what it still needs. Empty when available.
   *
   * These are the dashboard's own preconditions and they are a *subset* of the API's — the
   * boundary re-checks every one of them inside the transaction, because a precondition
   * checked in a browser is a suggestion, not a guarantee.
   */
  blockers: string[];
  /** Things true about the data that an operator should weigh. Never a recommendation. */
  cautions: string[];
  /** The durable rows this would write, in the order the API writes them. */
  writes: string[];
  /** What it explicitly does not do — the boundary of the command, stated to the operator. */
  doesNot: string[];
  /**
   * The request body this decision would send, field for field.
   *
   * Not a summary of it — the object itself, rendered as JSON on screen. A prose preview can
   * agree with the operator\'s intention while the request disagrees with both, and the
   * mismatch only shows up in the durable row afterwards. `null` when the decision is not
   * fully specified yet, because there is then no request to show.
   */
  request: Record<string, unknown> | null;
  /**
   * What this decision deliberately leaves open, named one by one.
   *
   * Only `attribute_sender_organization` has anything to say here: choosing one of the
   * institutions a message names is only safe if the operator can see that the others stay
   * unresolved and that the record stays in the queue carrying them.
   */
  leavesUnresolved: string[];
}

/** Everything the preview needs. `selectedOrganizationId` is null until an operator picks one. */
export interface CommandContext {
  record: V2EvidenceRecord;
  /** How many other pending records share this record's sender domain. */
  domainShareCount: number;
  /** The organization an operator has explicitly selected, if any. */
  selectedOrganizationId: string | null;
  /**
   * The *asserted institution name* an operator picked, when the message names more than one.
   *
   * This is the choice the four single commands had no way to express. It is an assertion
   * id and not a name: two assertions may carry the same words, and the decision is about
   * the one the operator was looking at.
   */
  selectedOrganizationAssertionId?: string | null;
  /**
   * What the operator says this address *is* to the institution. **Null until they say.**
   *
   * There is no default and there must not be one. The two values make opposite claims
   * about a human being — `shared_mailbox` says several people read this desk,
   * `individual_owner_unknown` says one person owns it and nobody has recorded who — and
   * until this change the surface asserted the first on every address because it was the
   * only shape the schema could hold. A default would put that back.
   */
  addressRelationship?: V2AttachableUsage | null;
  /**
   * How the operator knows a named address really is a shared desk. Blank until they say.
   *
   * Required only for the combination the evidence argues against: `shared_mailbox` on an
   * address without a recognised role local part. It is a sentence and not a checkbox
   * because it is read later by somebody weighing the claim.
   */
  sharedMailboxOverrideNote?: string;
  /** The operator's reason. Blank until they type one. */
  note: string;
}

const NOTE_REQUIRED = "Escribe el motivo de la decisión: queda en el registro de auditoría.";
const RECORD_NOT_PENDING = "Este registro ya fue revisado; no admite una decisión nueva.";
const RECORD_QUARANTINED = "Registro en cuarentena: su evidencia se contradice.";

function assertionsOfKind(record: V2EvidenceRecord, kind: string): V2RecordAssertion[] {
  return record.assertions.filter(
    (assertion) => assertion.kind === kind && assertion.resolution === "unresolved",
  );
}

/** Blockers that apply to every command, because they are facts about the record itself. */
function recordBlockers(context: CommandContext): string[] {
  const blockers: string[] = [];
  if (context.record.is_quarantined) {
    blockers.push(RECORD_QUARANTINED);
  }
  if (context.record.review_status !== "pending") {
    blockers.push(RECORD_NOT_PENDING);
  }
  if (!context.note.trim()) {
    blockers.push(NOTE_REQUIRED);
  }
  return blockers;
}

const RELATIONSHIP_REQUIRED =
  "Elige qué es esta dirección para la institución: no hay opción por omisión.";

/**
 * The blockers that govern calling an address one thing or the other.
 *
 * Mirrors the API's rule and is deliberately the *stricter* half of it: the boundary asks
 * for an override wherever its own role-mailbox list does not recognise a local part, and
 * that list is a superset of this one. So a preview that says "available" is never refused
 * for this reason, and a preview that asks for a sentence may occasionally ask for one the
 * boundary would not have needed. That is the right direction to be wrong in.
 */
function relationshipBlockers(
  context: CommandContext,
  address: string | null,
): { blockers: string[]; relationship: V2AttachableUsage | null; override: string } {
  const relationship = context.addressRelationship ?? null;
  const override = (context.sharedMailboxOverrideNote ?? "").trim();
  const blockers: string[] = [];

  if (!relationship) {
    blockers.push(RELATIONSHIP_REQUIRED);
  } else if (relationship === "shared_mailbox" && address && !isRoleMailbox(address)) {
    if (!override) {
      blockers.push(
        `«${address}» no tiene un nombre de función reconocido, así que llamarla buzón ` +
          "compartido afirma que varias personas la leen. Elige «de una persona, sin " +
          "identificar» o escribe cómo sabes que es una mesa.",
      );
    }
  } else if (relationship !== "shared_mailbox" && override) {
    blockers.push(
      "Escribiste una justificación de buzón compartido para una relación que no lo afirma: " +
        "bórrala o cambia la relación.",
    );
  }
  return { blockers, relationship, override };
}

/**
 * The organization the operator selected, but only when the evidence actually names it.
 *
 * An exact folded-name match is the API's rule, so the preview applies the same one rather
 * than showing an action the boundary would refuse. Two names that merely resemble each
 * other are two organizations here, as they are everywhere else in this system.
 */
function exactNameMatch(context: CommandContext) {
  const { record, selectedOrganizationId } = context;
  if (!selectedOrganizationId) {
    return null;
  }
  const asserted = new Set(organizationNamesOf(record).map((name) => fold(name)));
  return (
    record.organization_matches.find(
      (match) => match.organization_id === selectedOrganizationId && asserted.has(fold(match.name)),
    ) ?? null
  );
}

export function fold(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function keepPending(context: CommandContext): CommandPreview {
  const blockers = recordBlockers(context).filter((blocker) => blocker !== RECORD_NOT_PENDING);
  return {
    id: "keep_evidence_pending",
    label: "Dejar pendiente",
    intent: "Deja constancia de que leíste este correo y de que la evidencia no alcanza.",
    availability: blockers.length === 0 ? "available" : "blocked",
    blockers,
    cautions: [],
    writes: [
      "Un evento crm.domain_event (source_record.review_noted) con tu nombre y tu motivo.",
    ],
    doesNot: [
      "No cambia el estado del registro: sigue pendiente, porque lo sigue estando.",
      "No crea ni modifica ninguna fila de crm.*.",
      "No es una cuarentena: la evidencia está intacta, sólo es insuficiente.",
    ],
    request: blockers.length === 0
      ? { source_record_id: context.record.source_record_id, note: context.note.trim() }
      : null,
    leavesUnresolved: [],
  };
}

function confirmOrganization(context: CommandContext): CommandPreview {
  const blockers = recordBlockers(context);
  const named = assertionsOfKind(context.record, "organization_name");
  const match = exactNameMatch(context);

  if (named.length === 0) {
    blockers.push("Este correo no nombra ninguna institución sin resolver.");
  }
  if (!context.selectedOrganizationId) {
    blockers.push("Selecciona la institución existente que corresponde exactamente.");
  } else if (!match) {
    blockers.push(
      "La institución elegida no se llama exactamente igual que lo que afirma el correo.",
    );
  }
  if (named.length > 1) {
    blockers.push("El correo nombra más de una institución: confirma una afirmación a la vez.");
  }

  const cautions: string[] = [];
  if (match && match.confirmation === "machine_proposed") {
    cautions.push(
      "Esa institución fue propuesta por la máquina; confirmarla la convierte en verdad humana.",
    );
  }
  return {
    id: "confirm_organization",
    label: "Confirmar institución existente",
    intent: "Afirma que el nombre del correo es exactamente esta institución ya registrada.",
    availability: blockers.length === 0 ? "available" : "blocked",
    blockers,
    cautions,
    writes: [
      "La afirmación queda resuelta como 'linked' hacia esa institución.",
      "Si estaba propuesta por la máquina, la institución pasa a 'confirmed' y sube de versión.",
      "Un evento por cada cambio, con tu nombre, tu motivo y el recibo del comando.",
    ],
    doesNot: [
      "No fusiona instituciones parecidas.",
      "No crea personas ni afiliaciones.",
      "No atribuye el dominio del remitente a la institución.",
    ],
    request:
      blockers.length === 0 && match
        ? {
            source_record_id: context.record.source_record_id,
            note: context.note.trim(),
            assertion_id: named[0].assertion_id,
            organization_id: match.organization_id,
          }
        : null,
    leavesUnresolved: [],
  };
}

function createOrganization(context: CommandContext): CommandPreview {
  const blockers = recordBlockers(context);
  const named = assertionsOfKind(context.record, "organization_name");

  if (named.length === 0) {
    // The single most important refusal in this module: no name in the message, no
    // organization. A sender domain is not a name.
    blockers.push(
      "El correo no afirma ningún nombre de institución. Una pista de dominio no basta.",
    );
  }
  if (named.length > 1) {
    blockers.push("El correo nombra más de una institución: promueve una afirmación a la vez.");
  }
  if (context.record.organization_matches.length > 0) {
    blockers.push("Ya existe una institución con ese nombre exacto: confírmala en vez de crearla.");
  }

  return {
    id: "create_organization",
    label: "Crear institución nueva",
    intent: "Registra como institución el nombre que el propio correo escribe.",
    availability: blockers.length === 0 ? "available" : "blocked",
    blockers,
    cautions: named.length === 1 ? [`Se creará con el nombre afirmado: «${named[0].value_norm}».`] : [],
    writes: [
      "Una fila crm.organization con ese nombre, 'confirmed' y tu identidad.",
      "Su origen apunta a este registro de evidencia.",
      "La afirmación queda resuelta como 'promoted' y se registran los eventos.",
    ],
    doesNot: [
      "No toma el nombre de tu escritura ni del dominio: sólo de la afirmación del correo.",
      "No crea persona, afiliación, prospecto ni permiso de marketing.",
      "No registra el dominio del remitente como dominio de la institución.",
    ],
    request:
      blockers.length === 0 && named.length === 1
        ? {
            source_record_id: context.record.source_record_id,
            note: context.note.trim(),
            assertion_id: named[0].assertion_id,
            kind: "unknown",
          }
        : null,
    leavesUnresolved: [],
  };
}

function attachContactAddress(context: CommandContext): CommandPreview {
  const blockers = recordBlockers(context);
  const addresses = assertionsOfKind(context.record, "contact_address");

  if (addresses.length === 0) {
    blockers.push("Este correo no afirma ninguna dirección sin resolver.");
  }
  if (!context.selectedOrganizationId) {
    blockers.push("Selecciona primero la institución a la que pertenece el buzón.");
  }
  if (addresses.length > 1) {
    blockers.push("Hay más de una dirección sin resolver: decide una a la vez.");
  }

  const cautions: string[] = [];
  const address = addresses.length === 1 ? addresses[0].value_norm : null;
  const { blockers: relationshipIssues, relationship, override } = relationshipBlockers(
    context,
    address,
  );
  blockers.push(...relationshipIssues);
  if (address && !isRoleMailbox(address)) {
    // A heuristic with an opinion. It no longer has to carry the whole weight of the
    // decision: the operator now has a truthful value to choose, so this only says what the
    // spelling suggests and leaves the claim to them.
    cautions.push(
      `«${address}» no tiene un nombre de función reconocido, así que parece de una persona ` +
        "y no de una mesa. Un nombre local es una escritura, no evidencia: decídelo tú.",
    );
  }
  if (isConsumerDomain(context.record.from_domain)) {
    cautions.push("El dominio es de correo personal: no dice nada sobre el empleador.");
  }
  if (context.domainShareCount > 1) {
    cautions.push(
      `Otros ${context.domainShareCount - 1} registros pendientes comparten este dominio.`,
    );
  }
  const existing = address
    ? context.record.contact_matches.find((match) => match.value_norm === address)
    : undefined;
  if (existing?.person_id) {
    blockers.push("Esa dirección ya está atribuida a una persona; el comando la rechazaría.");
  } else if (existing?.organization_id && existing.organization_id !== context.selectedOrganizationId) {
    blockers.push("Esa dirección ya pertenece a otra institución.");
  }

  return {
    id: "attach_contact_address",
    label: "Adjuntar dirección a la institución",
    intent: "Afirma que esta dirección es un buzón que la institución opera.",
    availability: blockers.length === 0 ? "available" : "blocked",
    blockers,
    cautions,
    writes: existing
      ? [
          `El canal ya existe: se le asigna la institución y pasa a '${relationship ?? "?"}'/'confirmed'.`,
          "La afirmación queda resuelta como 'linked' y se registra el evento.",
        ]
      : [
          `Una fila crm.contact_point nueva, '${relationship ?? "?"}' y 'confirmed'.`,
          "La afirmación queda resuelta como 'promoted' y se registra el evento.",
        ],
    doesNot: [
      relationship === "individual_owner_unknown"
        ? "No crea persona ni afirma de quién es la dirección: sólo registra qué institución la opera."
        : "No crea persona: un buzón de mesa no tiene dueño conocido.",
      "No otorga permiso de marketing: recibir un correo no es autorización para enviar.",
      "No abre prospecto, oportunidad ni cotización.",
    ],
    request:
      blockers.length === 0 && address && relationship
        ? {
            source_record_id: context.record.source_record_id,
            note: context.note.trim(),
            assertion_id: addresses[0].assertion_id,
            organization_id: context.selectedOrganizationId,
            usage: relationship,
            ...(relationship === "shared_mailbox" && override
              ? { shared_mailbox_override_note: override }
              : {}),
          }
        : null,
    leavesUnresolved: [],
  };
}

/**
 * The decision the four single commands could not express: *this* institution is the
 * sender\'s, and this address is its mailbox.
 *
 * A message that names a supplier and the end customer names two institutions and has one
 * sender. `confirm_organization` and `create_organization` both refuse such a record — "one
 * assertion at a time" — which is correct as a rule and unhelpful as an outcome: the
 * operator can see perfectly well which name belongs to the sender and has no way to say so.
 *
 * Here they say so by picking the assertion. Three consequences the preview makes visible:
 *
 * 1. **The route is not a choice.** Whether the institution is confirmed or created follows
 *    from whether an organization already carries that exact name. Offering it as an option
 *    would only offer a way to be refused.
 * 2. **The other names stay open.** `leavesUnresolved` lists them, and the record stays
 *    `pending`. Choosing is not discarding, and the operator should be able to see that
 *    before they choose rather than infer it afterwards.
 * 3. **The address moves with the institution or not at all.** One transaction; a refusal on
 *    either half leaves neither behind.
 */
function attributeSenderOrganization(context: CommandContext): CommandPreview {
  const blockers = recordBlockers(context);
  const named = assertionsOfKind(context.record, "organization_name");
  const addresses = assertionsOfKind(context.record, "contact_address");
  const chosen =
    named.find(
      (assertion) => assertion.assertion_id === context.selectedOrganizationAssertionId,
    ) ?? null;

  if (named.length === 0) {
    blockers.push(
      "El correo no afirma ningún nombre de institución. Una pista de dominio no basta.",
    );
  } else if (!chosen) {
    blockers.push(
      named.length === 1
        ? "Elige la institución afirmada que corresponde al remitente."
        : `El correo nombra ${named.length} instituciones: elige cuál es la del remitente.`,
    );
  }
  if (addresses.length === 0) {
    blockers.push("Este correo no afirma ninguna dirección sin resolver.");
  }
  if (addresses.length > 1) {
    blockers.push(
      "Hay más de una dirección sin resolver: esta decisión atribuye una sola, la del remitente.",
    );
  }

  const match = chosen
    ? (context.record.organization_matches.find(
        (candidate) => fold(candidate.name) === fold(chosen.value_norm),
      ) ?? null)
    : null;
  const route = match ? "confirm" : "create";

  const cautions: string[] = [];
  if (match && match.confirmation === "machine_proposed") {
    cautions.push(
      `«${match.name}» fue propuesta por la máquina; esta decisión la convierte en verdad humana.`,
    );
  }
  if (chosen && !match) {
    cautions.push(`Se creará «${chosen.value_norm}», con el texto exacto que afirma el correo.`);
  }
  // The commercial role of the institution being chosen, and of the ones being left behind.
  // Identity is what this command records; the role is a different fact with no command, so
  // it is shown next to the decision and never folded into it.
  const assertedNames = named.map((assertion) => assertion.value_norm);
  if (chosen) {
    const role = commercialRoleOf(chosen.value_norm, assertedNames);
    if (role !== "unknown") {
      cautions.push(
        `«${chosen.value_norm}» — ${COMMERCIAL_ROLE_LABELS[role]}. ` +
          `${commercialRoleProvenance(role)} Esta decisión registra su identidad, no su rol.`,
      );
    }
  }
  const address = addresses.length === 1 ? addresses[0] : null;
  const { blockers: relationshipIssues, relationship, override } = relationshipBlockers(
    context,
    address?.value_norm ?? null,
  );
  blockers.push(...relationshipIssues);
  if (address && !isRoleMailbox(address.value_norm)) {
    cautions.push(
      `«${address.value_norm}» no tiene un nombre de función reconocido, así que parece de ` +
        "una persona y no de una mesa. Un nombre local es una escritura, no evidencia: " +
        "decídelo tú.",
    );
  }
  if (isConsumerDomain(context.record.from_domain)) {
    cautions.push("El dominio es de correo personal: no dice nada sobre el empleador.");
  }
  if (context.domainShareCount > 1) {
    cautions.push(
      `Otros ${context.domainShareCount - 1} registros pendientes comparten este dominio.`,
    );
  }
  const existing = address
    ? context.record.contact_matches.find((row) => row.value_norm === address.value_norm)
    : undefined;
  if (existing?.person_id) {
    blockers.push("Esa dirección ya está atribuida a una persona; el comando la rechazaría.");
  } else if (
    existing?.organization_id &&
    match &&
    existing.organization_id !== match.organization_id
  ) {
    blockers.push("Esa dirección ya pertenece a otra institución.");
  }

  const leavesUnresolved = named
    .filter((assertion) => assertion.assertion_id !== context.selectedOrganizationAssertionId)
    .map((assertion) => `«${assertion.value_norm}» queda sin resolver, y el registro pendiente.`);

  const ready =
    blockers.length === 0 && chosen !== null && address !== null && relationship !== null;
  const attached = `La dirección del remitente queda atribuida a ella como '${
    relationship ?? "?"
  }'/'confirmed'.`;
  return {
    id: "attribute_sender_organization",
    label: "Atribuir el remitente a una institución",
    intent:
      route === "confirm"
        ? "Afirma que el remitente es de esta institución ya registrada, y que esta dirección es su buzón."
        : "Registra la institución que el correo nombra y le adjunta la dirección del remitente, en una sola transacción.",
    availability: ready ? "available" : "blocked",
    blockers,
    cautions,
    writes:
      route === "confirm"
        ? [
            "La institución elegida pasa a 'confirmed' y sube de versión, si estaba propuesta.",
            attached,
            "Se resuelven exactamente dos afirmaciones: la institución elegida y la dirección.",
            "Un recibo de comando y un evento por cada cambio, con tu nombre y tu motivo.",
          ]
        : [
            "Una fila crm.organization con el nombre afirmado, 'confirmed' y tu identidad.",
            attached,
            "Se resuelven exactamente dos afirmaciones: la institución elegida y la dirección.",
            "Un recibo de comando y un evento por cada cambio, con tu nombre y tu motivo.",
          ],
    doesNot: [
      "No resuelve las demás instituciones que el correo nombra: quedan sin resolver.",
      "No infiere nada del dominio del remitente ni lo registra como dominio de la institución.",
      relationship === "individual_owner_unknown"
        ? "No crea persona ni afiliación, y no afirma de quién es la dirección: sólo qué institución la opera."
        : "No crea persona ni afiliación: un buzón de mesa no tiene dueño conocido.",
      ...COMMERCIAL_ROLE_WRITES_NOTHING,
      "No abre campaña, cotización ni tarea.",
      "Si falla cualquier mitad, no queda ninguna: es una sola transacción.",
    ],
    request:
      ready && chosen && address && relationship
        ? {
            source_record_id: context.record.source_record_id,
            note: context.note.trim(),
            organization_assertion_id: chosen.assertion_id,
            address_assertion_id: address.assertion_id,
            usage: relationship,
            ...(relationship === "shared_mailbox" && override
              ? { shared_mailbox_override_note: override }
              : {}),
            ...(match
              ? {
                  target: "existing",
                  organization_id: match.organization_id,
                }
              : { target: "new", kind: "unknown" }),
          }
        : null,
    leavesUnresolved,
  };
}

/**
 * The five previews for one record, always in the same order and always all five.
 *
 * A blocked command is shown, not hidden. An operator who cannot see why an action is
 * unavailable has to guess, and guessing is what this queue exists to replace.
 */
export function commandPreviews(context: CommandContext): CommandPreview[] {
  return [
    keepPending(context),
    attributeSenderOrganization(context),
    confirmOrganization(context),
    createOrganization(context),
    attachContactAddress(context),
  ];
}

/**
 * Whether anything at all can be decided right now.
 *
 * Used for the page's own banner. It counts availability; it does not pick a command.
 */
export function availableCount(previews: readonly CommandPreview[]): number {
  return previews.filter((preview) => preview.availability === "available").length;
}

/**
 * The reason the buttons do not send anything yet, in the words the page shows.
 *
 * This is one string in one place so the page cannot drift from the truth: the command
 * boundary exists in `apps/api`, and the browser's route to it does not.
 */
export const PREVIEW_ONLY_REASON =
  "Vista previa. El comando existe en la API (POST /v2/commands/*), pero el proxy no permite " +
  "todavía ningún POST bajo /v2, así que desde el navegador no se ejecuta ninguna decisión.";
