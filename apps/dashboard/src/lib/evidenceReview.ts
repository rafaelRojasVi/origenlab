/**
 * Presentation logic for the evidence review workspace.
 *
 * The workspace exists to answer one question per staged message — *what am I being asked
 * to decide?* — without answering it for the operator. So everything here is a **reading of
 * facts the API returned**, never a proposal:
 *
 * * a matched `crm.contact_point` means the **address exists**, and nothing about a person;
 * * a sender domain is a **hint**, and only `crm.organization_domain` makes it evidence;
 * * an identically named organization is a **coincidence of spelling** until a human says
 *   otherwise.
 *
 * Keeping it out of the component is what lets those three distinctions be tested without a
 * DOM, and keeping it free of any promotion, scoring or ranking is what keeps this a
 * reading surface. There is no write path in this file and none under `/v2` to call.
 */

import type { V2EvidenceRecord, V2RecordContactMatch } from "../api/v2Types";

/** Mailbox names that name a desk rather than a person. */
const ROLE_LOCAL_PARTS: ReadonlySet<string> = new Set([
  "contacto",
  "contact",
  "ventas",
  "sales",
  "info",
  "informacion",
  "compras",
  "adquisiciones",
  "abastecimiento",
  "administracion",
  "secretaria",
  "recepcion",
  "gerencia",
  "distribucion",
  "despacho",
  "produccion",
  "operaciones",
  "calidad",
  "laboratorio",
  "cobranza",
  "facturacion",
  "finanzas",
  "soporte",
  "contabilidad",
  "direcciontecnica",
  "molecular",
  "no-reply",
  "noreply",
]);

/**
 * Consumer mail providers.
 *
 * The domain of one of these says where someone keeps their mail, not who they work for, so
 * the domain hint is worth nothing on these addresses and the surface must say so rather
 * than offer a hint that looks like the others.
 */
const CONSUMER_DOMAINS: ReadonlySet<string> = new Set([
  "gmail.com",
  "googlemail.com",
  "hotmail.com",
  "hotmail.cl",
  "hotmail.es",
  "outlook.com",
  "outlook.cl",
  "outlook.es",
  "live.cl",
  "live.com",
  "yahoo.com",
  "yahoo.es",
  "icloud.com",
  "me.com",
  "msn.com",
]);

export type ReviewFlagKind =
  | "address_is_new"
  | "address_exists_without_person"
  | "address_exists_without_organization"
  | "role_mailbox"
  | "consumer_domain"
  | "domain_shared_in_queue"
  | "domain_not_registered"
  | "organization_named_matches_existing"
  | "organization_named_without_match"
  | "several_organizations_named"
  | "no_organization_named"
  | "quarantined";

export interface ReviewFlag {
  kind: ReviewFlagKind;
  /** What the operator has to decide, in one sentence. Never an instruction to promote. */
  text: string;
}

export function localPart(address: string): string {
  const at = address.indexOf("@");
  return at > 0 ? address.slice(0, at).toLowerCase() : address.toLowerCase();
}

export function isRoleMailbox(address: string): boolean {
  const local = localPart(address);
  if (ROLE_LOCAL_PARTS.has(local)) {
    return true;
  }
  // `contacto+algo@` and `ventas.sur@` are the same desk under a suffix. The base is what
  // decides, and only when the separator is explicit — `pmorales` must not read as a role.
  const base = local.split(/[+._-]/)[0];
  return base !== local && ROLE_LOCAL_PARTS.has(base);
}

export function isConsumerDomain(domain: string | null): boolean {
  return domain !== null && CONSUMER_DOMAINS.has(domain.toLowerCase());
}

export function addressesOf(record: V2EvidenceRecord): string[] {
  return record.assertions
    .filter((assertion) => assertion.kind === "contact_address")
    .map((assertion) => assertion.value_norm);
}

export function organizationNamesOf(record: V2EvidenceRecord): string[] {
  return record.assertions
    .filter((assertion) => assertion.kind === "organization_name")
    .map((assertion) => assertion.value_norm);
}

export function matchForAddress(
  record: V2EvidenceRecord,
  address: string,
): V2RecordContactMatch | null {
  return record.contact_matches.find((match) => match.value_norm === address) ?? null;
}

/**
 * How many records in the same queue page share each sender domain.
 *
 * Two messages from one domain are not a duplicate — they are usually two real people at
 * one institution — but they are the case where a reviewer most often creates the same
 * organization twice, so the surface counts them instead of leaving it to be noticed.
 */
export function domainCounts(records: readonly V2EvidenceRecord[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const record of records) {
    const domain = record.from_domain;
    if (domain) {
      counts.set(domain, (counts.get(domain) ?? 0) + 1);
    }
  }
  return counts;
}

/**
 * Everything unresolved about one record, as sentences.
 *
 * Order is deliberate: identity first (does this address exist, is anyone known to own it),
 * then the two weak signals that get mistaken for evidence — the role mailbox and the
 * domain — then the organization question. A reviewer reads down the list in the order the
 * decisions actually depend on each other.
 */
export function reviewFlags(
  record: V2EvidenceRecord,
  counts: Map<string, number>,
): ReviewFlag[] {
  const flags: ReviewFlag[] = [];

  if (record.is_quarantined) {
    flags.push({
      kind: "quarantined",
      text: "Registro en cuarentena: no debe usarse para ninguna decisión comercial.",
    });
  }

  for (const address of addressesOf(record)) {
    const match = matchForAddress(record, address);
    if (match === null) {
      flags.push({
        kind: "address_is_new",
        text: `La dirección ${address} no existe todavía en el CRM durable.`,
      });
    } else {
      if (match.person_id === null) {
        flags.push({
          kind: "address_exists_without_person",
          text: `La dirección ${address} ya existe como canal, pero nadie está registrado como su dueño. Dirección conocida no es persona confirmada.`,
        });
      }
      if (match.organization_id === null) {
        flags.push({
          kind: "address_exists_without_organization",
          text: `El canal ${address} no está atribuido a ninguna institución.`,
        });
      }
    }
    if (isRoleMailbox(address)) {
      flags.push({
        kind: "role_mailbox",
        text: `${address} parece un buzón de función, no de una persona. Quién firma el correo y de quién es la casilla pueden no coincidir.`,
      });
    }
  }

  const domain = record.from_domain;
  if (isConsumerDomain(domain)) {
    flags.push({
      kind: "consumer_domain",
      text: `${domain} es un proveedor de correo personal: el dominio no dice para qué institución trabaja quien escribe.`,
    });
  } else if (domain && record.domain_organization === null) {
    flags.push({
      kind: "domain_not_registered",
      text: `${domain} no está registrado en ninguna institución del CRM. El dominio es una pista, no evidencia.`,
    });
  }
  if (domain && (counts.get(domain) ?? 0) > 1) {
    flags.push({
      kind: "domain_shared_in_queue",
      text: `${counts.get(domain)} registros pendientes comparten el dominio ${domain}: revisarlos por separado puede crear la misma institución dos veces.`,
    });
  }

  const names = organizationNamesOf(record);
  if (names.length === 0) {
    flags.push({
      kind: "no_organization_named",
      text: "El mensaje no nombra ninguna institución. No hay nada que confirmar salvo la pista del dominio.",
    });
  } else {
    if (names.length > 1) {
      flags.push({
        kind: "several_organizations_named",
        text: `El mensaje nombra ${names.length} instituciones (por ejemplo proveedor y cliente final): cuál corresponde al remitente es una decisión humana.`,
      });
    }
    for (const name of names) {
      const match = record.organization_matches.find((row) => row.value_norm === name);
      if (match) {
        flags.push({
          kind: "organization_named_matches_existing",
          text: `«${match.name}» coincide exactamente con una institución existente. Coincidir de nombre no es ser la misma institución.`,
        });
      } else {
        flags.push({
          kind: "organization_named_without_match",
          text: `«${name}» no coincide con ninguna institución registrada.`,
        });
      }
    }
  }

  return flags;
}

/** The one-line verdict shown in the queue row, derived from the same facts. */
export function identityHeadline(record: V2EvidenceRecord): string {
  const addresses = addressesOf(record);
  if (addresses.length === 0) {
    return "Sin dirección observada";
  }
  const matched = addresses.filter((address) => matchForAddress(record, address) !== null);
  const withPerson = addresses.filter(
    (address) => matchForAddress(record, address)?.person_id != null,
  );
  if (withPerson.length > 0) {
    return "Persona confirmada";
  }
  if (matched.length === addresses.length) {
    return "Dirección conocida, sin persona";
  }
  if (matched.length === 0) {
    return "Dirección nueva";
  }
  return "Parcialmente conocida";
}

export function organizationHeadline(record: V2EvidenceRecord): string {
  if (record.domain_organization) {
    return `Institución por dominio: ${record.domain_organization.name}`;
  }
  const names = organizationNamesOf(record);
  if (names.length === 0) {
    return record.from_domain ? `Solo pista de dominio: ${record.from_domain}` : "Sin señal";
  }
  const matched = record.organization_matches.length;
  return matched > 0
    ? `${names.length} nombre(s) en el mensaje, ${matched} con homónimo en el CRM`
    : `${names.length} nombre(s) en el mensaje, sin homónimo`;
}

/**
 * The flow this workspace sits inside, and why each step is or is not available.
 *
 * It is written down because the order is the product decision: nothing downstream of a
 * human review may start from machine evidence. A step is `available` only when the data
 * already permits it — never because a button exists.
 */
export interface FlowStep {
  id: "evidence" | "review" | "prospect" | "marketing" | "quote";
  label: string;
  detail: string;
}

export const REVIEW_FLOW: readonly FlowStep[] = [
  {
    id: "evidence",
    label: "1 · Evidencia",
    detail: "Un correo entra como registro de evidencia pendiente. No crea nada por sí solo.",
  },
  {
    id: "review",
    label: "2 · Contacto / institución revisados",
    detail: "Una persona decide quién es el remitente y a qué institución pertenece.",
  },
  {
    id: "prospect",
    label: "3 · Prospecto (opcional)",
    detail: "Solo si hay intención comercial. Prospecto es una etapa de oportunidad, no una entidad.",
  },
  {
    id: "marketing",
    label: "4 · Marketing (con permiso explícito)",
    detail: "Requiere permiso explícito registrado. Sin él no hay envío, ni siquiera manual.",
  },
  {
    id: "quote",
    label: "5 · Cotización",
    detail: "Se adjunta a un contacto e institución ya revisados, nunca a evidencia cruda.",
  },
];

export function reviewStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "Pendiente de revisión",
    reviewed: "Revisado",
    promoted: "Promovido",
    rejected: "Rechazado",
  };
  return labels[status] ?? status;
}

/** `es-CL`, date only: the hour a message arrived is noise in a queue of decisions. */
export function reviewDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString("es-CL", { year: "numeric", month: "2-digit", day: "2-digit" });
}
