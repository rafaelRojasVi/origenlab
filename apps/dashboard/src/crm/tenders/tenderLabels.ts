/**
 * Every code a tender's page can meet, in Spanish. An unknown code is one generic line — the server's
 * own text is never shown (it may be English, technical, or the model's).
 */
import { tenderRefusal } from "./tenderApi";

export const GENERIC = "No se pudo completar. Intenta de nuevo.";

/** The monthly cap line; `cap` is the spend summary's `cap_usd`. */
export function capText(cap?: string | null): string {
  const n = Number(cap);
  const amount = Number.isFinite(n) && n > 0 ? n.toLocaleString("es-CL") : "60";
  return `Se alcanzó el tope mensual de US$ ${amount} para licitaciones; vuelve a intentarlo el 1 del próximo mes.`;
}

/** A preparation's `failure_code` (worker: read and fill). */
const FAILURE_ES: Record<string, string> = {
  model_refused: "El modelo no pudo leer estos anexos.",
  annex_set_too_large: "Los anexos son demasiado largos para leerlos juntos.",
  no_readable_annex: "Ningún anexo tiene texto legible.",
  model_truncated: "La respuesta del modelo quedó incompleta. Vuelve a intentarlo.",
  request_failed: "No se pudo conectar con el modelo. Vuelve a intentarlo.",
  model_answer_invalid: "El modelo respondió algo que no se pudo usar. Vuelve a intentarlo.",
  annex_missing: "Falta un anexo; súbelo de nuevo.",
  annex_unavailable: "No se pudo abrir un anexo guardado. Vuelve a intentarlo.",
  annex_hash_mismatch: "Un anexo guardado no coincide con el original; súbelo de nuevo.",
  model_unpriced: "El modelo no tiene precio registrado; avisa a administración.",
  interrupted: "La preparación se interrumpió. Vuelve a intentarlo.",
  internal_error: "Algo falló al preparar. Vuelve a intentarlo.",
  connection_lost: "Se perdió la conexión con la base de datos. Vuelve a intentarlo.",
  read_not_ready: "La lectura de anexos ya no está lista; vuelve a leerlos.",
  offer_quote_missing: "Falta la cotización de la oferta.",
  offer_mismatch: "La cotización de la oferta no es de esta licitación.",
  offer_not_approved: "La cotización de la oferta no está aprobada.",
  offer_currency_not_clp: "La cotización de la oferta no está en pesos (CLP).",
  bidder_profile_missing: "Faltan los datos del oferente.",
  output_store_failed: "No se pudo guardar un archivo generado.",
  package_too_large: "El paquete es demasiado grande (más de 45 MB).",
};
export const FAILURE_GENERIC = "La preparación no se pudo completar. Vuelve a intentarlo.";

export function failureText(code: string | null, status: string, cap?: string | null): string {
  if (status === "budget_stopped" || code === "budget_reached" || code === "budget_stopped") return capText(cap);
  return (code && FAILURE_ES[code]) || FAILURE_GENERIC;
}

/** Refusals of the page's commands: prepare, the offer, the estado, the upload and the shared ones. */
const REFUSAL_ES: Record<string, string> = {
  // prepare-tender-bid
  no_annexes: "Sube los anexos antes de preparar la oferta.",
  read_not_ready: "Primero hay que leer los anexos con «Preparar oferta».",
  offer_quote_missing: "Crea la oferta económica primero.",
  offer_not_approved: "Aprueba la oferta económica en el Cotizador antes de llenar los formularios.",
  bidder_profile_missing: "Completa los Datos para licitaciones en Preferencias.",
  preparation_running: "Ya hay una preparación en curso; espera a que termine.",
  // create-tender-offer-quote
  offer_quote_exists: "Esta licitación ya tiene una oferta aprobada; ábrela en el Cotizador.",
  too_many_items: "La lectura trae más de 60 ítems; el Cotizador admite hasta 60 líneas.",
  no_items: "La lectura no encontró ítems para cotizar.",
  preparation_not_a_read: "La oferta se arma desde la lectura de los anexos.",
  preparation_not_found: "Esa lectura ya no existe; recarga la página.",
  quote_numbering_not_configured: "La numeración de cotizaciones no está configurada; avisa a administración.",
  // set-tender-status
  award_is_phase_3: "Se registra al publicarse la adjudicación (próxima fase).",
  reason_required: "Escribe el motivo.",
  tender_not_found: "No existe esa licitación.",
  case_version_conflict: "La licitación cambió desde que la abriste; se volvió a cargar. Revisa y vuelve a intentarlo.",
  tender_already_at_status: "La licitación ya estaba en ese estado.",
  tender_status_is_final: "Una licitación adjudicada no cambia de estado.",
  // attach-tender-annexes
  not_a_tender_case: "Los anexos se suben a una licitación.",
  case_not_found: "No existe esa licitación.",
  storage_unavailable: "El almacenamiento de documentos no está disponible. Intenta más tarde.",
  payload_too_large: "Los archivos pesan más de 60 MB en total; súbelos en partes.",
  document_too_large: "Los archivos pesan más de 60 MB en total; súbelos en partes.",
  http_413: "Los archivos pesan más de 60 MB en total; súbelos en partes.",
  too_many_files: "Sube hasta 20 archivos a la vez (o un .zip).",
  // remove-tender-annex
  annex_not_found: "Ese anexo ya no está en esta licitación; recarga la página.",
  document_already_removed: "Ese anexo ya se quitó; recarga la página.",
  // shared
  role_may_not_view_bidder: "Solo ventas y administración pueden ver esta licitación.",
  forbidden: "Solo ventas y administración pueden hacer esto.",
  path_not_allowed: "Esta función no está habilitada en este entorno.",
  command_in_progress: "La acción anterior aún se está procesando; espera un momento y vuelve a intentarlo.",
  file_not_found: "Ese archivo ya no existe; recarga la página.",
};

export function refusalText(err: unknown, cap?: string | null): string {
  const r = tenderRefusal(err);
  if (!r) return GENERIC;
  if (r.code === "model_budget_reached") return capText(cap);
  return REFUSAL_ES[r.code] ?? GENERIC;
}

/** The same command may still be running under this key: a retry must keep it. */
const STILL_RUNNING = new Set(["command_in_progress", "idempotency_conflict"]);

/** A 4xx with the API's own code is final, so the next attempt takes a fresh key; anything else may have run. */
export function definitive(err: unknown): boolean {
  const r = tenderRefusal(err);
  return !!r && r.status >= 400 && r.status < 500 && !r.code.startsWith("http_") && !STILL_RUNNING.has(r.code);
}

/** Why an anexo was not kept (`attach-tender-annexes`). `not_listed` is the summary row, shown on its own. */
export const SKIP_REASON_ES: Record<string, string> = {
  unsupported_type: "Tipo de archivo no aceptado",
  too_large: "Pesa más de 20 MB",
  unsafe_path: "Ruta no segura dentro del zip",
  encrypted: "Tiene contraseña",
  archive_rejected: "El zip no se pudo abrir de forma segura",
  already_attached: "Ya estaba subido",
  request_too_large: "No cupo: el envío supera 60 MB en total",
};
export const SKIP_GENERIC = "No se pudo subir";

/** Resumen fields, in the order the worker writes them. */
export const RESUMEN_FIELDS: readonly { key: string; label: string }[] = [
  { key: "objeto", label: "Objeto" },
  { key: "cantidades", label: "Cantidades" },
  { key: "cierre", label: "Cierre" },
  { key: "consultas", label: "Consultas" },
  { key: "adjudicacion", label: "Adjudicación" },
  { key: "entrega_plazo", label: "Plazo de entrega" },
  { key: "entrega_lugar", label: "Lugar de entrega" },
  { key: "garantia_producto", label: "Garantía del producto" },
  { key: "validez_oferta", label: "Validez de la oferta" },
  { key: "garantia_seriedad", label: "Garantía de seriedad" },
  { key: "garantia_fiel_cumplimiento", label: "Garantía de fiel cumplimiento" },
  { key: "criterios_evaluacion", label: "Criterios de evaluación" },
  { key: "causales_inadmisibilidad", label: "Causales de inadmisibilidad" },
];

export const CHECKLIST_ESTADO_ES: Record<string, string> = { listo: "Listo", falta: "Falta", firmar: "Firmar" };

export const FALTA_ES: Record<string, string> = {
  convertir_a_word: "Convertir a Word (.docx)",
  no_se_pudo_llenar: "No se pudo llenar; complétalo a mano",
  sin_documento: "Falta el documento",
  completar: "Tiene campos «[completar]»",
  archivo_sin_texto: "El archivo no tiene texto",
  no_se_pudo_leer: "No se pudo leer el archivo",
};
export const FALTA_GENERIC = "Falta revisar esta línea";

export const OUTPUT_KIND_ES: Record<string, string> = {
  formulario: "Formulario llenado",
  formulario_generado: "Formulario (documento generado)",
  oferta_economica: "Oferta económica",
  ficha_tecnica: "Ficha técnica",
  documento_oferente: "Documento del oferente",
  paquete: "Paquete (zip)",
};

export const FICHA_ESTADO_ES: Record<string, string> = {
  adjunta: "Adjunta",
  sin_ficha: "Sin ficha en el catálogo",
  sin_drive: "Drive no configurado",
  no_disponible: "No disponible en Drive",
  formato_no_soportado: "Formato no soportado",
  demasiado_grande: "Demasiado grande",
};

export const DRIVE_ES: Record<string, string> = {
  archivado: "Guardado en Drive",
  sin_carpeta: "El caso no tiene carpeta en Drive",
  apagado: "Drive no configurado",
  error: "No se pudo guardar en Drive",
  sin_paquete: "Sin paquete",
};

export const MATCH_ES: Record<string, string> = {
  hecho: "Productos sugeridos",
  sin_candidatos: "Sin coincidencias en el catálogo",
  tope_alcanzado: "Tope mensual alcanzado; sin sugerencias",
  fallo: "No se pudieron sugerir productos",
};

export const QUOTE_STATUS_ES: Record<string, string> = {
  draft: "Borrador",
  in_review: "En revisión",
  approved: "Aprobada",
  sent: "Enviada",
  void: "Anulada",
};

/** A known label, else the fallback — never the raw code. */
export function labelOf(map: Record<string, string>, code: string | null | undefined, fallback: string): string {
  return (code && map[code]) || fallback;
}
