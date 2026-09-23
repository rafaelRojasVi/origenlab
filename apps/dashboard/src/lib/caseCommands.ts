/**
 * What each commercial-case decision *would* record — computed, never sent.
 *
 * The six case commands exist upstream as `POST /v2/commands/*`. This module is the
 * dashboard's half of them and it is deliberately only half: it turns a case and an
 * operator's partial intention into a plain-language account of the durable rows a decision
 * would write, which preconditions are satisfied, and which are not. It builds no request,
 * imports no client and knows no URL.
 *
 * It is the same shape as `evidenceCommands.ts` and for the same reason. A case is the
 * expensive half of this system — it is what a quote, a campaign and a won deal all hang
 * off — and the cheapest moment to discover that a command says something different from
 * what the operator believed is before it is wired to anything.
 *
 * **The preview never decides.** `availability` reports what the *data* allows. It never
 * chooses a command, never orders them by preference and never marks one recommended.
 *
 * **Every precondition here is a subset of the API's.** The boundary re-checks all of them
 * inside the transaction, and some of them cannot be checked here at all — whether an
 * institution holds a current supplier relationship with OrigenLab is a row in
 * `crm.organization_relationship` that this surface does not read, so the supplier
 * exception is described and never predicted.
 */

import type {
  V2CaseOrganization,
  V2CaseOrganizationRole,
  V2CaseEvidenceRelation,
  V2CaseStage,
  V2CommercialCaseCard,
} from "../api/v2Types";
import {
  caseRoleLabel,
  caseStageLabel,
  requestingInstitution,
  stageRequirements,
} from "./commercialCase";

/** The six commands, named exactly as the API names them. */
export type CaseCommandId =
  | "open_commercial_case"
  | "link_case_evidence"
  | "add_case_organization"
  | "set_case_organization_role"
  | "record_case_interest"
  | "advance_case_stage";

export type CaseCommandAvailability = "available" | "blocked";

export interface CaseCommandPreview {
  id: CaseCommandId;
  /** The button's words. */
  label: string;
  /** One sentence: what durable fact this records. */
  intent: string;
  availability: CaseCommandAvailability;
  /** Why it is blocked, or what it still needs. Empty when available. */
  blockers: string[];
  /** Things true about the case an operator should weigh. Never a recommendation. */
  cautions: string[];
  /** The durable rows this would write, in the order the API writes them. */
  writes: string[];
  /** What it explicitly does not do — the boundary of the command, stated out loud. */
  doesNot: string[];
  /**
   * The request body this decision would send, field for field, or null when the decision
   * is not fully specified yet. The object itself, not a summary of it: a prose preview can
   * agree with the operator's intention while the request disagrees with both.
   */
  request: Record<string, unknown> | null;
}

/**
 * Everything the previews need. Every field an operator has not filled in is null or blank,
 * and stays that way until they say — nothing here has a default, because every default
 * would be this module making a commercial decision on their behalf.
 */
export interface CaseCommandContext {
  /** The case being looked at, or null on the page header where no case is open yet. */
  card: V2CommercialCaseCard | null;
  /** The evidence record an operator picked as the reason a new case would exist. */
  originSourceRecordId: string | null;
  /** The title an operator typed for a new case. */
  title: string;
  /** The institution an operator picked, and the version of it they were shown. */
  selectedOrganizationId: string | null;
  selectedOrganizationVersion: number | null;
  /** The part they say that institution holds. Null until they say. */
  selectedRole: V2CaseOrganizationRole | null;
  /** The existing part row they are re-reading, for `set_case_organization_role`. */
  selectedCaseOrganizationId: string | null;
  /** Their justification for making a supplier the requesting institution. Blank until typed. */
  supplierExceptionReason: string;
  /** The reading they give a document they are linking. Null until they say. */
  selectedRelation: V2CaseEvidenceRelation | null;
  /** The document being linked, as exactly one typed subject. */
  evidenceSubject: { kind: CaseEvidenceSubjectKind; id: string } | null;
  /** What they say the case is seeking. At least one of the three is required. */
  interestProductId: string | null;
  interestManufacturerOrganizationId: string | null;
  interestModelText: string;
  interestQuantity: string;
  interestQuantityUnit: string;
  /** The stage they want to move to. Null until they pick one. */
  targetStage: V2CaseStage | null;
  /** The motive a closing stage requires. Blank until typed. */
  closeReason: string;
  /** The operator's reason. Blank until they type one. */
  note: string;
}

export type CaseEvidenceSubjectKind =
  | "source_record_id"
  | "assertion_id"
  | "message_id"
  | "notice_id";

const NOTE_REQUIRED = "Escribe el motivo de la decisión: queda en el registro de auditoría.";
const NO_CASE_OPEN = "Abre o elige un caso primero: este comando actúa sobre uno existente.";
const CASE_CLOSED =
  "El caso está cerrado. Las etapas terminales no se reviven; un caso nuevo se abre aparte.";

/**
 * The blockers that apply to every command *about an existing case*, because they are facts
 * about the case rather than about the decision.
 *
 * The closed check is the dashboard restating what the stage machine already enforces, and
 * deliberately so: the six commands other than `advance_case_stage` are not themselves
 * refused by the stage trigger, and an operator editing a closed case would get a refusal
 * from a constraint rather than a sentence.
 */
function caseBlockers(context: CaseCommandContext): string[] {
  const blockers: string[] = [];
  if (!context.card) {
    blockers.push(NO_CASE_OPEN);
  } else if (context.card.closed_at !== null) {
    blockers.push(CASE_CLOSED);
  }
  if (!context.note.trim()) {
    blockers.push(NOTE_REQUIRED);
  }
  return blockers;
}

/** The compare-and-set every case command carries: which case, and which version of it. */
function caseIdentity(card: V2CommercialCaseCard): Record<string, unknown> {
  return { opportunity_id: card.opportunity_id, opportunity_version: card.version };
}

/**
 * Opening a case, which is the only command that does not take one.
 *
 * `origin_source_record_id` is required by the API and that is the whole design of §W2: a
 * case is never an empty shell, and since an inbound message routinely names no institution
 * and no command creates a participant, the rule survives as *a case is opened from a
 * document*. The preview says so where the operator can read it, because a required field
 * whose reason is invisible reads as bureaucracy.
 */
function openCommercialCase(context: CaseCommandContext): CaseCommandPreview {
  const blockers: string[] = [];
  if (!context.note.trim()) {
    blockers.push(NOTE_REQUIRED);
  }
  if (!context.title.trim()) {
    blockers.push("Escribe un título: es cómo el caso se reconoce después.");
  }
  if (!context.originSourceRecordId) {
    blockers.push(
      "Elige el documento que causó el caso. Un caso sin nada detrás no se rechaza: no se " +
        "puede ni pedir.",
    );
  }
  const ready = blockers.length === 0;
  return {
    id: "open_commercial_case",
    label: "Abrir caso comercial",
    intent: "Abre un caso y deja constancia del documento por el que existe.",
    availability: ready ? "available" : "blocked",
    blockers,
    cautions: [
      "El caso nace en «Contacto inicial» y en ninguna otra etapa: no es una opción.",
      "Abrir un caso no dice quién pide. Eso es «Agregar institución», otra frase.",
    ],
    writes: [
      "Una fila crm.opportunity en etapa 'lead', contigo como dueño.",
      "Un vínculo crm.opportunity_evidence 'origin' hacia el documento elegido.",
      "Un recibo de comando y los eventos, todo en una sola transacción.",
    ],
    doesNot: [
      "No crea institución, persona ni contacto.",
      "No fija la institución solicitante: ese campo queda nulo a propósito.",
      "No abre cotización, tarea ni campaña.",
      "No permite elegir la etapa inicial.",
    ],
    request: ready
      ? {
          title: context.title.trim(),
          origin_source_record_id: context.originSourceRecordId,
          note: context.note.trim(),
        }
      : null,
  };
}

function linkCaseEvidence(context: CaseCommandContext): CaseCommandPreview {
  const blockers = caseBlockers(context);
  if (!context.evidenceSubject) {
    blockers.push("Elige el documento a vincular: exactamente uno, de un tipo nombrado.");
  }
  if (!context.selectedRelation) {
    blockers.push("Di qué significa ese documento para el caso. No hay lectura por omisión.");
  }
  const ready = blockers.length === 0 && context.card !== null;
  return {
    id: "link_case_evidence",
    label: "Vincular evidencia",
    intent: "Deja constancia de que leíste un documento y de qué significa para este caso.",
    availability: ready ? "available" : "blocked",
    blockers,
    cautions: [
      "«Lo contradice» es una lectura tan legítima como «Origen del caso»: un caso que " +
        "reunió la evidencia en su contra es un caso que se puede cerrar con honestidad.",
    ],
    writes: [
      "Una fila crm.opportunity_evidence con el sujeto, la relación, tu identidad y la fecha.",
      "Un recibo de comando y un evento.",
    ],
    doesNot: [
      "No crea el documento: el registro, la afirmación, el mensaje o el aviso ya deben existir.",
      "No resuelve ninguna afirmación de evidencia: eso es la cola de revisión.",
      "No cambia la etapa ni la institución del caso.",
    ],
    request:
      ready && context.card && context.evidenceSubject && context.selectedRelation
        ? {
            ...caseIdentity(context.card),
            relation: context.selectedRelation,
            [context.evidenceSubject.kind]: context.evidenceSubject.id,
            note: context.note.trim(),
          }
        : null,
  };
}

/**
 * Whether making this institution the requesting one might need the §3.6.1 override.
 *
 * It cannot be answered here and the preview does not pretend otherwise. Whether an
 * institution holds a *current* supplier or manufacturer relationship with OrigenLab is a
 * row in `crm.organization_relationship`, which this surface does not read; the command
 * checks it inside its own transaction. So the caution names the condition and leaves the
 * judgement to the boundary — the alternative, guessing from a name, is the mistake §3.6.1
 * was written to prevent.
 */
function supplierExceptionCaution(role: V2CaseOrganizationRole | null): string[] {
  if (role !== "requesting_institution") {
    return [];
  }
  return [
    "Si esta institución es proveedor o fabricante de OrigenLab, hacerla la que pide exige " +
      "una justificación escrita. El comando lo comprueba contra crm.organization_relationship; " +
      "esta pantalla no lee esa tabla y no lo adivina.",
  ];
}

function addCaseOrganization(context: CaseCommandContext): CaseCommandPreview {
  const blockers = caseBlockers(context);
  if (!context.selectedOrganizationId || context.selectedOrganizationVersion === null) {
    blockers.push("Elige la institución, y con ella la versión que estás viendo.");
  }
  if (!context.selectedRole) {
    blockers.push(
      "Di qué es esta institución para el caso. No hay rol por omisión: si aún no lo " +
        "sabes, «Mencionada» es un valor, no un encogimiento de hombros.",
    );
  }
  const existing = context.card
    ? context.card.organizations.find(
        (row) =>
          row.is_current &&
          row.organization_id === context.selectedOrganizationId &&
          row.role === context.selectedRole,
      )
    : undefined;
  if (existing) {
    blockers.push(
      "Esa institución ya tiene ese rol vigente en el caso: cambia el rol en la fila " +
        "existente en vez de abrir otra.",
    );
  }
  const current = context.card ? requestingInstitution(context.card.organizations) : null;
  const cautions = supplierExceptionCaution(context.selectedRole);
  if (context.selectedRole === "requesting_institution" && current) {
    blockers.push(
      `El caso ya tiene institución solicitante vigente («${current.name}»). Cierra ese rol ` +
        "primero: sólo puede haber una a la vez.",
    );
  }
  if (context.selectedRole === "requesting_institution") {
    cautions.push(
      "Este rol hace dos cosas a la vez: abre la fila y fija crm.opportunity.organization_id. " +
        "O se mueven las dos o no se mueve ninguna.",
    );
  }
  const ready = blockers.length === 0 && context.card !== null;
  return {
    id: "add_case_organization",
    label: "Agregar institución al caso",
    intent: "Pone una institución en el caso, en un papel nombrado.",
    availability: ready ? "available" : "blocked",
    blockers,
    cautions,
    writes: [
      "Una fila crm.opportunity_organization vigente desde hoy, 'confirmed' y con tu identidad.",
      "Si el rol es «Institución que pide», también crm.opportunity.organization_id.",
      "Un recibo de comando y un evento por cada cambio.",
    ],
    doesNot: [
      "No crea la institución: debe existir ya en crm.organization.",
      "No registra un rol comercial permanente: eso es crm.organization_relationship, que " +
        "no tiene comando.",
      "No crea persona, afiliación ni dominio.",
      "No otorga permiso de marketing.",
    ],
    request:
      ready &&
      context.card &&
      context.selectedOrganizationId &&
      context.selectedOrganizationVersion !== null &&
      context.selectedRole
        ? {
            ...caseIdentity(context.card),
            organization_id: context.selectedOrganizationId,
            organization_version: context.selectedOrganizationVersion,
            role: context.selectedRole,
            ...(context.supplierExceptionReason.trim()
              ? { supplier_exception_reason: context.supplierExceptionReason.trim() }
              : {}),
            note: context.note.trim(),
          }
        : null,
  };
}

/**
 * Confirming or changing what an institution is to this case.
 *
 * Nothing is rewritten. `role` is not in `origenlab_api`'s UPDATE grant on
 * `crm.opportunity_organization`, so changing a part closes the current row and opens a new
 * one — both readable forever. The single in-place move is confirming a proposal the
 * machine made: the reading did not change, only who vouches for it.
 */
function setCaseOrganizationRole(context: CaseCommandContext): CaseCommandPreview {
  const blockers = caseBlockers(context);
  const row: V2CaseOrganization | undefined = context.card
    ? context.card.organizations.find(
        (candidate) =>
          candidate.opportunity_organization_id === context.selectedCaseOrganizationId,
      )
    : undefined;

  if (!context.selectedCaseOrganizationId) {
    blockers.push(
      "Elige la fila vigente, no la institución: una institución puede tener varios papeles " +
        "en un mismo caso.",
    );
  } else if (!row) {
    blockers.push("Esa fila no pertenece a este caso.");
  } else if (!row.is_current) {
    blockers.push("Esa fila ya está cerrada: es historia, y la historia no se edita.");
  }
  if (!context.selectedRole) {
    blockers.push("Di cuál es el papel ahora.");
  }

  const confirmingInPlace =
    row !== undefined &&
    row.is_current &&
    row.confirmation === "machine_proposed" &&
    context.selectedRole === row.role;
  const cautions = supplierExceptionCaution(context.selectedRole);
  if (confirmingInPlace) {
    cautions.push(
      "Esto confirma en el sitio una propuesta de la máquina: la lectura no cambia, sólo " +
        "quién la respalda.",
    );
  } else if (row && context.selectedRole && context.selectedRole !== row.role) {
    cautions.push(
      `Cambiar «${caseRoleLabel(row.role)}» por «${caseRoleLabel(context.selectedRole)}» ` +
        "cierra la fila actual y abre otra. Las dos quedan legibles para siempre.",
    );
  }
  const ready = blockers.length === 0 && context.card !== null;
  return {
    id: "set_case_organization_role",
    label: "Fijar el papel de la institución",
    intent: "Confirma lo que una institución es para este caso, o dice que ahora es otra cosa.",
    availability: ready ? "available" : "blocked",
    blockers,
    cautions,
    writes: confirmingInPlace
      ? [
          "La fila pasa a 'confirmed' con tu identidad. Nada más cambia.",
          "Un recibo de comando y un evento.",
        ]
      : [
          "La fila actual se cierra con valid_to = hoy.",
          "Una fila nueva se abre con el papel nuevo, 'confirmed' y tu identidad.",
          "Si alguno de los dos papeles es «Institución que pide», también se mueve " +
            "crm.opportunity.organization_id.",
          "Un recibo de comando y un evento por cada cambio, en una sola transacción.",
        ],
    doesNot: [
      "No borra ni reescribe la fila anterior.",
      "No cambia la identidad de la institución.",
      "No cambia la etapa del caso.",
    ],
    request:
      ready && context.card && context.selectedCaseOrganizationId && context.selectedRole
        ? {
            ...caseIdentity(context.card),
            opportunity_organization_id: context.selectedCaseOrganizationId,
            role: context.selectedRole,
            ...(context.supplierExceptionReason.trim()
              ? { supplier_exception_reason: context.supplierExceptionReason.trim() }
              : {}),
            note: context.note.trim(),
          }
        : null,
  };
}

function recordCaseInterest(context: CaseCommandContext): CaseCommandPreview {
  const blockers = caseBlockers(context);
  const model = context.interestModelText.trim();
  const hasSubject =
    Boolean(context.interestProductId) ||
    Boolean(context.interestManufacturerOrganizationId) ||
    model.length > 0;
  if (!hasSubject) {
    blockers.push(
      "Di al menos una de tres cosas: el producto del catálogo, el fabricante, o el modelo " +
        "tal como lo escribe el correo.",
    );
  }
  const quantityRaw = context.interestQuantity.trim();
  const quantity = quantityRaw === "" ? null : Number(quantityRaw);
  if (quantity !== null && (!Number.isFinite(quantity) || quantity <= 0)) {
    blockers.push("La cantidad tiene que ser un número mayor que cero.");
  }
  if (context.interestQuantityUnit.trim() && quantity === null) {
    blockers.push("La unidad describe una cantidad: nombra también la cantidad.");
  }
  const ready = blockers.length === 0 && context.card !== null;
  return {
    id: "record_case_interest",
    label: "Registrar interés",
    intent: "Deja constancia de qué busca el caso.",
    availability: ready ? "available" : "blocked",
    blockers,
    cautions: [
      "Un interés puede empezar como un modelo leído de un correo y ganar un producto del " +
        "catálogo después, en la misma fila.",
      "No hay campo de precio ni de monto, y no hay sitio para uno: el dinero vive sólo en " +
        "crm.quote_revision y crm.quote_line.",
    ],
    writes: [
      "Una fila crm.opportunity_interest con el asunto que nombraste, 'confirmed' y tu identidad.",
      "Un recibo de comando y un evento.",
    ],
    doesNot: [
      "No es un compromiso ni un precio.",
      "No crea producto de catálogo ni institución.",
      "Nombrar un fabricante no le crea relación, prospecto, permiso ni papel en el caso.",
      "No abre cotización.",
    ],
    request:
      ready && context.card
        ? {
            ...caseIdentity(context.card),
            ...(context.interestProductId ? { product_id: context.interestProductId } : {}),
            ...(context.interestManufacturerOrganizationId
              ? {
                  manufacturer_organization_id:
                    context.interestManufacturerOrganizationId,
                }
              : {}),
            ...(model ? { model_text: model } : {}),
            ...(quantity !== null ? { quantity } : {}),
            ...(context.interestQuantityUnit.trim()
              ? { quantity_unit: context.interestQuantityUnit.trim() }
              : {}),
            note: context.note.trim(),
          }
        : null,
  };
}

/**
 * Moving the case.
 *
 * `allowed_next_stages` is the API's, served from the command boundary's own table. `won`
 * is refused by name rather than by a constraint violation later: `crm.opportunity` may
 * only be `won` together with a quote revision, no V2 command creates a quote, and
 * `crm.quote` is empty — so accepting it could only ever end in a 500.
 */
function advanceCaseStage(context: CaseCommandContext): CaseCommandPreview {
  const blockers = caseBlockers(context);
  const card = context.card;
  const target = context.targetStage;

  if (!target) {
    blockers.push("Elige la etapa a la que quieres mover el caso.");
  } else if (card) {
    if (!card.stage_machine.allowed_next_stages.includes(target)) {
      blockers.push(
        `Desde «${caseStageLabel(card.stage)}» no se puede ir a «${caseStageLabel(target)}».`,
      );
    }
    if (target === "won") {
      blockers.push(
        "«Ganado» no se alcanza desde aquí: exige una revisión de cotización ganadora, y " +
          "ningún comando V2 crea cotizaciones todavía.",
      );
    }
    // Only what is still missing. `stageRequirements` reports the requesting institution
    // and the motive, and is silent about either once it is satisfied, so this is the whole
    // of the stage's own preconditions rather than half of them plus a duplicate.
    blockers.push(...stageRequirements(card, target, context.closeReason));
    const needsReason = card.stage_machine.stages_requiring_a_close_reason.includes(target);
    if (!needsReason && context.closeReason.trim()) {
      blockers.push(
        "Escribiste un motivo de cierre para una etapa que no cierra el caso: bórralo o " +
          "elige otra etapa.",
      );
    }
  }

  const cautions: string[] = [];
  if (card?.stage_machine.is_terminal) {
    cautions.push("El caso está en una etapa terminal, y una etapa terminal no se revive.");
  }
  const ready = blockers.length === 0 && card !== null;
  return {
    id: "advance_case_stage",
    label: "Avanzar etapa",
    intent: "Mueve el caso, cuando se cumplen las reglas para moverlo.",
    availability: ready ? "available" : "blocked",
    blockers,
    cautions,
    writes: [
      "crm.opportunity.stage, y la versión sube.",
      "Si la etapa cierra el caso, también closed_at y close_reason.",
      "Un recibo de comando y un evento.",
    ],
    doesNot: [
      "No crea cotización, tarea ni campaña.",
      "Ningún temporizador, importador ni clasificador puede escribir una etapa: este " +
        "boundary es el único que escribe, y siempre con un operador con nombre.",
    ],
    request:
      ready && card && target
        ? {
            ...caseIdentity(card),
            stage: target,
            ...(context.closeReason.trim()
              ? { close_reason: context.closeReason.trim() }
              : {}),
            note: context.note.trim(),
          }
        : null,
  };
}

/**
 * The six previews, always in the same order and always all six.
 *
 * A blocked command is shown, not hidden. An operator who cannot see why an action is
 * unavailable has to guess, and guessing is what a case exists to replace.
 */
export function caseCommandPreviews(context: CaseCommandContext): CaseCommandPreview[] {
  return [
    openCommercialCase(context),
    linkCaseEvidence(context),
    addCaseOrganization(context),
    setCaseOrganizationRole(context),
    recordCaseInterest(context),
    advanceCaseStage(context),
  ];
}

/** An empty context: nothing chosen, nothing typed, nothing assumed. */
export function emptyCaseCommandContext(
  card: V2CommercialCaseCard | null,
): CaseCommandContext {
  return {
    card,
    originSourceRecordId: null,
    title: "",
    selectedOrganizationId: null,
    selectedOrganizationVersion: null,
    selectedRole: null,
    selectedCaseOrganizationId: null,
    supplierExceptionReason: "",
    selectedRelation: null,
    evidenceSubject: null,
    interestProductId: null,
    interestManufacturerOrganizationId: null,
    interestModelText: "",
    interestQuantity: "",
    interestQuantityUnit: "",
    targetStage: null,
    closeReason: "",
    note: "",
  };
}

/**
 * The reason the buttons do not send anything, in the words the page shows.
 *
 * One string in one place so the page cannot drift from the truth: the six commands exist
 * in `apps/api`, and the browser's route to them does not — the proxy allowlist permits no
 * POST under `/v2` at all.
 */
export const CASE_PREVIEW_ONLY_REASON =
  "Vista previa. Los seis comandos existen en la API (POST /v2/commands/*), pero el proxy " +
  "no permite ningún POST bajo /v2, así que desde el navegador no se ejecuta ninguna decisión.";
