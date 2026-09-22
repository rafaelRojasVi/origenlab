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

import type { V2EvidenceRecord, V2RecordAssertion } from "../api/v2Types";
import { isConsumerDomain, isRoleMailbox, organizationNamesOf } from "./evidenceReview";

/** The four commands, named exactly as the API names them. */
export type EvidenceCommandId =
  | "keep_evidence_pending"
  | "confirm_organization"
  | "create_organization"
  | "attach_contact_address";

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
}

/** Everything the preview needs. `selectedOrganizationId` is null until an operator picks one. */
export interface CommandContext {
  record: V2EvidenceRecord;
  /** How many other pending records share this record's sender domain. */
  domainShareCount: number;
  /** The organization an operator has explicitly selected, if any. */
  selectedOrganizationId: string | null;
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
  if (address && !isRoleMailbox(address)) {
    // A heuristic with an opinion, stated as a caution and never as a refusal. The operator
    // may know perfectly well that this is a desk; a local part is a spelling, not evidence.
    cautions.push(
      "Esta dirección no parece un buzón de mesa. Adjuntarla como 'shared_mailbox' afirma " +
        "que lo es; si pertenece a una persona, todavía no hay comando para eso.",
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
          "El canal ya existe: se le asigna la institución y pasa a 'shared_mailbox'/'confirmed'.",
          "La afirmación queda resuelta como 'linked' y se registra el evento.",
        ]
      : [
          "Una fila crm.contact_point nueva, 'shared_mailbox' y 'confirmed'.",
          "La afirmación queda resuelta como 'promoted' y se registra el evento.",
        ],
    doesNot: [
      "No crea persona: un buzón de mesa no tiene dueño conocido.",
      "No otorga permiso de marketing: recibir un correo no es autorización para enviar.",
      "No abre prospecto, oportunidad ni cotización.",
    ],
  };
}

/**
 * The four previews for one record, always in the same order and always all four.
 *
 * A blocked command is shown, not hidden. An operator who cannot see why an action is
 * unavailable has to guess, and guessing is what this queue exists to replace.
 */
export function commandPreviews(context: CommandContext): CommandPreview[] {
  return [
    keepPending(context),
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
