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
  case_id: string | null;
  case_title: string | null;
  quote_number: string | null;
  reasons: string[];
  undone: boolean;
}

export interface OperationsPayload {
  generated_at: string;
  cards: OpsCard[];
  actions: OpsAction[];
  actions_today: number;
  counts: { cases_without_institution: number; institutions_por_confirmar: number };
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
    cuando: "Un correo nombra un número de cotización (en el PDF o en el asunto) que pertenece a un solo caso abierto.",
    hace: "Vincula el correo a ese caso.",
    automatica: true,
    ejemplo: "Un cliente escribe «Consulta por la cotización 09876-26» → el correo queda en el caso de esa cotización.",
  },
  R3: {
    cuando: "OrigenLab envía una cotización nueva a una dirección de una institución que ya está en el CRM.",
    hace: "Abre un caso para esa institución, en «Enviada», con la cotización.",
    automatica: false,
    ejemplo: "Se envía «CN09876.pdf» a compras@institucion-ficticia.cl → caso nuevo de Institución Ficticia.",
  },
  R4: {
    cuando: "Como R3, pero el dominio del destinatario no corresponde a ninguna institución del CRM.",
    hace: "Crea la institución «por confirmar» con ese dominio, su caso en «Enviada» y la cotización.",
    automatica: false,
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
    cuando: "Llega una solicitud de cotización clara que no pertenece a ningún hilo conocido.",
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
  } else {
    verb = `Vinculó un correo a${a.case_title ? `l caso «${a.case_title}»` : " un caso"}`;
  }
  const why = RULE_WHY[rule] ? ` — ${RULE_WHY[rule]} (${rule})` : rule ? ` (${rule})` : "";
  return `${verb}${why}${a.undone ? " · deshecho" : ""}`;
}
