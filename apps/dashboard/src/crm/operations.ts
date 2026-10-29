/**
 * «Operaciones» (spec 2026-10-13): the state of each part of the system, what the email rules did,
 * and the rules explained in Spanish. One read, `GET /v2/workspace/operations`, open to every
 * operator; it carries no address, subject or message body.
 */
import { fetchJsonGet, operatorApiUrl } from "../api/operatorClient";

export const OPERATIONS_PATH = "/v2/workspace/operations";

export type OpsStatus = "ok" | "warn" | "bad" | "info";

export interface OpsFact {
  label: string;
  value: number | string;
}

export interface OpsEvent {
  at: string | null;
  text: string;
}

export interface OpsCard {
  key: string;
  title: string;
  status: OpsStatus;
  summary: string;
  at: string | null;
  facts: OpsFact[];
  events: OpsEvent[];
}

export interface OpsAction {
  receipt_id: string;
  at: string | null;
  rule_id: string | null;
  automatic: boolean;
  /** Applied on today's Santiago day (the API decides; a date string would be the UTC day). */
  today: boolean;
  case_id: string | null;
  case_title: string | null;
  quote_number: string | null;
  undone: boolean;
  /** R15 acts on an institution: its id and name (as confirmed). Absent from an older API. */
  organization_id?: string | null;
  organization_name?: string | null;
}

/** A quote contacto@ sent that no case holds: by number only (the PDF name may hold a person's name). */
export interface SentQuoteWithoutCase {
  source_record_id: string;
  quote_number: string;
  sent_at: string | null;
  thread_id: string | null;
  /** The institution part of the PDF name (legal form / kind / «(SIGLA)»), or null; never a person. */
  suggested_institution: string | null;
}

/** A quote contacto@ sent on an email already linked to one open case, whose number no case records. */
export interface SentQuoteUnrecordedOnCase {
  source_record_id: string;
  quote_number: string;
  sent_at: string | null;
  thread_id: string | null;
  case_id: string;
  case_title: string;
  case_version: number;
  /** The case's requesting institution, or null when it has none yet. */
  organization_name: string | null;
}

export interface OperationsPayload {
  generated_at: string;
  cards: OpsCard[];
  actions: OpsAction[];
  actions_today: number;
  sent_quotes_without_case: SentQuoteWithoutCase[];
  sent_quotes_unrecorded_on_case?: SentQuoteUnrecordedOnCase[];
  counts: {
    cases_without_institution: number;
    institutions_por_confirmar: number;
    sent_quotes_without_case: number;
    sent_quotes_unrecorded_on_case?: number;
  };
}

export const fetchOperations = () => fetchJsonGet<OperationsPayload>(operatorApiUrl(OPERATIONS_PATH));

export interface RuleGuide {
  /** When it applies, in one sentence. */
  cuando: string;
  /** What it does. */
  hace: string;
  /** True when the API applies it on its own timer; false when it waits for «Aplicar». */
  automatica: boolean;
  /** An invented example. */
  ejemplo: string;
}

/** The email → cases rules in the operator's words. Keep in step with `mail_rules.RULE_IDS`. */
export const RULE_GUIDE: Record<string, RuleGuide> = {
  R1: {
    cuando: "Llega o sale un correo en el mismo hilo de Gmail que un correo ya vinculado a un caso abierto.",
    hace: "Vincula el correo a ese caso. Si OrigenLab envió en ese hilo un PDF con un número de cotización nuevo, también registra la cotización y deja el caso en «Enviada».",
    automatica: true,
    ejemplo: "Tatiana responde en el hilo de un caso con «CN09876-Empresa Ficticia.pdf» → el caso pasa a «Enviada» con la cotización 09876.",
  },
  R2: {
    cuando: "Un correo nombra un número de cotización (en el PDF, en el asunto o en el texto del correo) que pertenece a un solo caso abierto o ganado.",
    hace: "Vincula el correo a ese caso. Un caso ganado recibe sus correos posteriores (pago, orden, despacho) sin reabrirse.",
    automatica: true,
    ejemplo: "Un cliente escribe «Consulta por la cotización 09876-26» → el correo queda en el caso de esa cotización.",
  },
  R3: {
    cuando: "OrigenLab envía una cotización nueva a una dirección de una institución que ya está en el CRM.",
    hace: "Abre un caso para esa institución, en «Enviada», con la cotización.",
    automatica: true,
    ejemplo: "Se envía «CN09876.pdf» a compras@institucion-ficticia.cl → caso nuevo de Institución Ficticia.",
  },
  R4: {
    cuando: "Como R3, pero el dominio del destinatario no corresponde a ninguna institución del CRM.",
    hace: "Crea la institución «por confirmar» con ese dominio, su caso en «Enviada» y la cotización.",
    automatica: true,
    ejemplo: "Se envía una cotización a lab@nuevo-cliente.cl → institución «nuevo-cliente.cl» por confirmar.",
  },
  R5: {
    cuando: "Llega una orden de compra en el hilo de un caso con una cotización vigente.",
    hace: "Vincula el correo y marca el caso como ganado.",
    automatica: false,
    ejemplo: "«Envío OC 4500012345» en el hilo de la cotización → caso ganado.",
  },
  R6: {
    cuando: "El cliente avisa en el hilo de un caso que eligió a otro proveedor.",
    hace: "Vincula el correo y marca el caso como perdido.",
    automatica: false,
    ejemplo: "«Gracias, nos quedamos con otro proveedor» → caso perdido.",
  },
  R7: {
    cuando: "Llega una solicitud de cotización clara que no pertenece a ningún hilo conocido: palabras de cotización en el asunto, o la lectura del correo (IA) dice que pide precio. Nunca una respuesta («Re:») ni la oferta de un proveedor.",
    hace: "Abre un caso en «Solicitada». Si el remitente usa un correo gratuito, el caso queda sin institución hasta que una persona la confirme.",
    automatica: true,
    ejemplo: "«Solicito cotización de una balanza» desde un correo nuevo → caso «Solicitada».",
  },
  R8: {
    cuando: "Ninguna otra regla aplica: proveedores, campañas masivas, notificaciones, menciones a Labdelivery…",
    hace: "No hace nada; el correo queda a la vista por si una persona quiere actuar.",
    automatica: false,
    ejemplo: "Un aviso de licitación o un boletín → queda sin acción.",
  },
  R9: {
    cuando: "OrigenLab envía una cotización nueva a un correo gratuito (Gmail, Hotmail…) y el nombre del PDF nombra una institución del CRM que tiene un solo caso abierto.",
    hace: "Vincula el correo a ese caso, lo deja en «Enviada» y registra la cotización.",
    automatica: true,
    ejemplo: "«CN09876-Persona Ficticia-Empresa Ficticia SA.pdf» enviado a un Gmail → el caso abierto de Empresa Ficticia SA pasa a «Enviada».",
  },
  R10: {
    cuando: "Como R9, pero la institución que nombra el PDF no está en el CRM.",
    hace: "Crea la institución «por confirmar» con ese nombre, su caso en «Enviada» y la cotización.",
    automatica: false,
    ejemplo: "«CN09876-Instituto Ficticio (IFI).pdf» enviado a un Gmail → institución «Instituto Ficticio (IFI)» por confirmar.",
  },
  R11: {
    cuando: "Llega a contacto@ una solicitud reenviada desde otro correo (un marketplace, el correo de Labdelivery, un proveedor que pasa un contacto).",
    hace: "Si el cliente ya tiene un caso abierto, la suma a ese caso; si no, abre una «Solicitada» con sus datos. Nunca vincula un correo del buzón de Labdelivery.",
    automatica: true,
    ejemplo: "«Fwd: Solicitud cotización Gradillas» de un cliente con caso abierto → queda en ese caso.",
  },
  R12: {
    cuando: "Llega un correo que ni el hilo ni un número de cotización ubican, y el sistema reconoce su caso con seguridad: la IA lo elige con confianza alta (por la persona, la institución, el producto) o las señales del correo coinciden exactamente. También el correo de un proveedor sobre el negocio de un cliente.",
    hace: "Lo vincula a ese caso en vez de abrir otra solicitud. Si la confianza es media o baja, queda como sugerencia «Parece del caso…» en Correos sin caso. Cada «Quitar» le enseña a la IA que ese no era el caso.",
    automatica: true,
    ejemplo: "Un cliente escribe desde un hilo nuevo «¿plazo de entrega de la centrífuga?» → queda en su caso abierto de la centrífuga.",
  },
  R13: {
    cuando: "OrigenLab envía una cotización nueva en un hilo nuevo, y el cliente ya tiene una solicitud abierta sin cotización (escribió él, la pidió por un reenvío, su dominio es el de la institución o el PDF nombra la institución).",
    hace: "Vincula el correo a esa solicitud, la pasa a «Enviada» y registra la cotización, en vez de abrir un caso nuevo.",
    automatica: true,
    ejemplo: "Se envía «CN00042 – Laboratorio Ejemplo» a la persona que pidió el equipo por un reenvío → queda en su solicitud.",
  },
  R14: {
    cuando: "OrigenLab envía una cotización nueva en un correo que ya está en un caso abierto (lo vinculó otra regla o una persona), y ningún caso tiene ese número.",
    hace: "Registra la cotización en ese caso y lo deja en «Enviada», con las mismas condiciones que R1. Si el caso no tiene institución o el correlativo existe en otro año, queda en «Por decidir».",
    automatica: true,
    ejemplo: "Un correo ya vinculado por el número 09875 lleva además «CN09876-Empresa Ficticia.pdf» → el caso registra la cotización 09876.",
  },
  R15: {
    cuando: "Una institución «por confirmar» que creó el sistema desde un correo (R4, R10 o «Completar con la cotización») tiene una cotización enviada hace 3 días o más, sin deshacer; no es proveedor ni un correo gratuito.",
    hace: "La confirma. Si se llamaba como su dominio, antes toma el nombre de institución que dan los PDF de sus cotizaciones (si dan uno solo; si no dan ninguno o dan varios, queda para una persona).",
    automatica: true,
    ejemplo: "«acme-ejemplo.example», con «CN09876-Persona Ficticia - Acme Ejemplo SpA.pdf» enviada hace 4 días → «Acme Ejemplo SpA», confirmada.",
  },
  R16: {
    cuando: "OrigenLab envía, en un correo que ya está en el caso, el PDF de una cotización hecha y aprobada en el Cotizador para ese caso (el número y la letra de revisión del nombre del archivo coinciden).",
    hace: "Marca esa cotización como enviada y deja el caso en «Enviada». No lee el PDF: lo que dice es lo que se aprobó en el Cotizador. Si el correo lleva varias, marca cada una.",
    automatica: true,
    ejemplo: "Se envía «CN09876A-Empresa Ficticia.pdf» (revisión A aprobada en el Cotizador) → la cotización 09876A queda enviada y el caso en «Enviada».",
  },
  R17: {
    cuando: "El cliente responde en un caso que está en «Enviada», después de recibir la cotización y en los últimos 14 días. No cuenta un correo nuestro, de un proveedor ni una respuesta automática (fuera de la oficina).",
    hace: "Pasa el caso a «Conversación». «Quitar» lo devuelve a «Enviada».",
    automatica: true,
    ejemplo: "«Estamos evaluando la oferta, les aviso» en el hilo de la cotización 09876 → el caso pasa a «Conversación».",
  },
  R18: {
    cuando: "El cliente escribe en un caso abierto o ganado, en los últimos 14 días, y la lectura de la IA dice que nadie necesita responderle: agradece, avisa que está evaluando, acusa recibo. No cuenta un correo nuestro, de un proveedor, un rebote ni una respuesta automática.",
    hace: "Saca el correo de «Responder»; el seguimiento (3 · 14 · 30 días) cuenta desde ese correo. No cambia nada en el caso. «Quitar» lo devuelve a «Responder».",
    automatica: true,
    ejemplo: "«Gracias, estamos analizando varias ofertas, te aviso» → sale de «Responder» y el seguimiento vuelve en 3 días.",
  },
  R19: {
    cuando: "El correo más reciente del cliente en un caso abierto (que no está «En pausa») dice, según la lectura de la IA del texto, que no sigue: compró a otro proveedor, se canceló o lo verán el próximo año. El remitente debe ser de la institución del caso o escribir desde su propio hilo.",
    hace: "Marca el caso «Perdida». Si el remitente no es de la institución del caso, queda como propuesta. «Quitar» reabre el caso donde estaba.",
    automatica: true,
    ejemplo: "«La compra ya se efectuó a otro proveedor» en el hilo de la cotización 09876 → el caso pasa a «Perdida».",
  },
};

/** Why, in a few words, for each rule (the end of each sentence). */
const RULE_WHY: Record<string, string> = {
  R1: "mismo hilo de Gmail",
  R2: "mismo número de cotización",
  R3: "institución conocida del destinatario",
  R4: "dominio nuevo «por confirmar»",
  R5: "orden de compra en el hilo",
  R6: "otro proveedor elegido",
  R7: "solicitud de cotización",
  R9: "institución que nombra el PDF",
  R10: "institución del PDF «por confirmar»",
  R11: "solicitud reenviada a contacto@",
  R12: "el sistema reconoce el caso",
  R13: "responde la solicitud del cliente",
  R14: "el correo ya estaba en el caso",
  R15: "cotización enviada hace 3 días o más",
  R16: "se envió el PDF del Cotizador",
  R17: "el cliente respondió a la cotización",
  R18: "el cliente cerró la conversación",
  R19: "el correo del cliente dice que no sigue",
};

/** One Spanish sentence for what the system did. */
export function actionSentence(a: OpsAction): string {
  const rule = a.rule_id ?? "";
  const target = a.case_title ? `el caso «${a.case_title}»` : "un caso";
  let verb: string;
  if (rule === "R3" || rule === "R4" || rule === "R10") {
    verb = `Abrió ${target}${a.quote_number ? ` con la cotización ${a.quote_number}` : ""}`;
  } else if (rule === "R5") {
    verb = `Marcó ganado ${target}`;
  } else if (rule === "R6") {
    verb = `Marcó perdido ${target}`;
  } else if (rule === "R7") {
    verb = `Abrió una solicitud: ${target}`;
  } else if (rule === "R11") {
    verb = `Atendió una solicitud reenviada: ${target}`;
  } else if (rule === "R14") {
    verb = `Registró ${a.quote_number ? `la cotización ${a.quote_number}` : "una cotización"} en ${target}`;
  } else if (rule === "R16") {
    verb = `Marcó enviada ${a.quote_number ? `la cotización ${a.quote_number}` : "una cotización"} en ${target}`;
  } else if (rule === "R17") {
    verb = `Pasó a «Conversación» ${target}`;
  } else if (rule === "R18") {
    verb = `No requiere respuesta el correo de${a.case_title ? `l caso «${a.case_title}»` : " un caso"}`;
  } else if (rule === "R19") {
    verb = `Marcó perdido ${target}`;
  } else if (rule === "R15") {
    verb = `Confirmó la institución${a.organization_name ? ` «${a.organization_name}»` : ""}`;
  } else {
    verb = `Vinculó un correo a${a.case_title ? `l caso «${a.case_title}»` : " un caso"}`;
  }
  const why = RULE_WHY[rule] ? ` — ${RULE_WHY[rule]} (${rule})` : rule ? ` (${rule})` : "";
  return `${verb}${why}${a.undone ? " · deshecho" : ""}`;
}
