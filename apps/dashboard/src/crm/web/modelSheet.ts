/**
 * The model sheet of the public site (`apps/web/src/content/modelos.schema.json`, Draft 7), as the
 * «Catálogo web» form edits it. The enums, maximum lengths and required fields below are pinned to
 * that file by `modelSheet.test.ts`. The schema allows no price, stock, warranty or lead time, so
 * none is ever read, shown or sent: a field the schema does not name is dropped on load.
 */

export interface SpecRow {
  label: string;
  value: string;
  source: string;
}
export interface FaqRow {
  q: string;
  a: string;
}

export interface ModelSheet {
  slug: string;
  family: string;
  brand: string;
  name: string;
  headline: string;
  lead: string;
  applications: string[];
  specs: SpecRow[];
  accessories: string[];
  related: string[];
  faqs: FaqRow[];
  official_url: string;
  official_pdf_url?: string;
  image_id?: string;
  showroom_id?: string;
  verified_on: string;
}

export const FAMILIES = [
  { value: "sonicacion", label: "Sonicación" },
  { value: "dispersion", label: "Dispersión" },
  { value: "pesaje-humedad", label: "Pesaje y humedad" },
  { value: "osmometria", label: "Osmometría" },
  { value: "electroforesis", label: "Electroforesis" },
  { value: "centrifugacion", label: "Centrifugación" },
] as const;

export const BRANDS = [
  { value: "hielscher", label: "Hielscher" },
  { value: "ika", label: "IKA" },
  { value: "ortoalresa", label: "Ortoalresa" },
  { value: "adam-equipment", label: "Adam Equipment" },
  { value: "ohaus", label: "OHAUS" },
  { value: "loeser", label: "Loeser" },
  { value: "serva", label: "Serva" },
] as const;

export const REQUIRED_FIELDS = [
  "slug", "family", "brand", "name", "headline", "lead", "applications", "specs", "accessories",
  "related", "faqs", "official_url", "verified_on",
] as const;

export const LIMITS = { name: 80, headline: 120, lead: 300, application: 80, accessory: 120 } as const;
/** The schema sets no maximum; the editor keeps the list short as the page does. */
export const MAX_APPLICATIONS = 6;
/** The proxy accepts a model id of 1 to 60 slug characters. */
export const SLUG_PATTERN = /^[a-z0-9-]{1,60}$/;
/** The API refuses a list of more than 50 items. */
export const MAX_ROWS = 50;
export const SPEC_KEYS = ["label", "value", "source"] as const;
export const FAQ_KEYS = ["q", "a"] as const;

export function emptySheet(modelId: string, today: string): ModelSheet {
  return {
    slug: modelId, family: "", brand: "", name: "", headline: "", lead: "", applications: [], specs: [],
    accessories: [], related: [], faqs: [], official_url: "", verified_on: today,
  };
}

const text = (v: unknown): string => (typeof v === "string" ? v : "");
const textList = (v: unknown): string[] => (Array.isArray(v) ? v.map(text) : []);

export const KNOWN_KEYS = new Set<string>([
  ...REQUIRED_FIELDS, "official_pdf_url", "image_id", "showroom_id",
]);

/** The sheet as the form edits it, plus the keys of the stored file that the schema does not name. */
export function fromStored(raw: unknown, modelId: string, today: string): { sheet: ModelSheet; dropped: string[] } {
  const o = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const sheet: ModelSheet = {
    ...emptySheet(modelId, today),
    slug: text(o.slug) || modelId,
    family: text(o.family),
    brand: text(o.brand),
    name: text(o.name),
    headline: text(o.headline),
    lead: text(o.lead),
    applications: textList(o.applications),
    specs: (Array.isArray(o.specs) ? o.specs : []).map((s) => {
      const r = (s ?? {}) as Record<string, unknown>;
      return { label: text(r.label), value: text(r.value), source: text(r.source) };
    }),
    accessories: textList(o.accessories),
    related: textList(o.related),
    faqs: (Array.isArray(o.faqs) ? o.faqs : []).map((f) => {
      const r = (f ?? {}) as Record<string, unknown>;
      return { q: text(r.q), a: text(r.a) };
    }),
    official_url: text(o.official_url),
    verified_on: text(o.verified_on) || today,
  };
  for (const key of ["official_pdf_url", "image_id", "showroom_id"] as const) {
    if (text(o[key])) sheet[key] = text(o[key]);
  }
  return { sheet, dropped: Object.keys(o).filter((k) => !KNOWN_KEYS.has(k)) };
}

/** What the publish request carries as `sheet`: trimmed, blank rows and empty optionals left out. */
export function toPayload(sheet: ModelSheet): Record<string, unknown> {
  const clean = (list: string[]) => list.map((s) => s.trim()).filter(Boolean);
  const out: Record<string, unknown> = {
    slug: sheet.slug.trim(),
    family: sheet.family,
    brand: sheet.brand,
    name: sheet.name.trim(),
    headline: sheet.headline.trim(),
    lead: sheet.lead.trim(),
    applications: clean(sheet.applications),
    specs: sheet.specs
      .map((s) => ({ label: s.label.trim(), value: s.value.trim(), source: s.source.trim() }))
      .filter((s) => s.label || s.value || s.source),
    accessories: clean(sheet.accessories),
    related: clean(sheet.related),
    faqs: sheet.faqs
      .map((f) => ({ q: f.q.trim(), a: f.a.trim() }))
      .filter((f) => f.q || f.a),
    official_url: sheet.official_url.trim(),
    verified_on: sheet.verified_on.trim(),
  };
  for (const key of ["official_pdf_url", "image_id", "showroom_id"] as const) {
    const v = (sheet[key] ?? "").trim();
    if (v) out[key] = v;
  }
  return out;
}

/* ── the validator's errors ──────────────────────────────────────────────── */

export interface SchemaError {
  path: string;
  message: string;
}

/**
 * Paths come from the API as `specs/0/source`; `$.specs[0].source` and `/specs/0/source` are read the
 * same. A missing property is reported at the object's path with its name in the message
 * (`'headline' is a required property`): it is moved onto the property.
 */
export function normalizeErrors(errors: readonly SchemaError[]): SchemaError[] {
  return errors.map((e) => {
    let path = e.path
      .replace(/^\$\.?/, "")
      .replace(/\[(\d+)\]/g, "/$1")
      .replace(/\./g, "/")
      .replace(/^\/+|\/+$/g, "");
    const missing = /^'([^']+)' is a required property/.exec(e.message);
    if (missing) path = path ? `${path}/${missing[1]}` : missing[1];
    return { path, message: e.message };
  });
}

export function errorsAt(errors: readonly SchemaError[], path: string): string[] {
  return errors.filter((e) => e.path === path).map((e) => translateMessage(e.message));
}

/** The validator speaks English; the page does not. The original stays in a tooltip. */
export function translateMessage(message: string): string {
  if (/is a required property/.test(message)) return "Falta este campo.";
  if (/at most \d+ items/.test(message)) return `Demasiados elementos (máximo ${MAX_ROWS}).`;
  if (/is too long/.test(message)) return "Demasiado largo.";
  if (/is too short|should be non-empty/.test(message)) return "Demasiado corto.";
  if (/does not match '\^https\?:\/\/'/.test(message)) return "Debe empezar por http:// o https://.";
  if (/does not match.*d\{4\}/.test(message)) return "Fecha no válida (AAAA-MM-DD).";
  if (/does not match/.test(message)) return "Formato no válido (solo minúsculas, números y guiones).";
  if (/Additional properties are not allowed/.test(message)) return "Campo no permitido en la ficha.";
  if (/is not one of/.test(message)) return "Valor no permitido: elige una opción de la lista.";
  if (/is not of type/.test(message)) return "Tipo de dato no válido.";
  return "No cumple el esquema de la ficha.";
}

const FIELD_LABEL: Record<string, string> = {
  slug: "Identificador", family: "Familia", brand: "Marca", name: "Nombre del fabricante", headline: "Titular",
  lead: "Texto de apertura", applications: "Aplicaciones", specs: "Especificaciones", accessories: "Accesorios",
  related: "Modelos relacionados", faqs: "Preguntas frecuentes", official_url: "Página oficial",
  official_pdf_url: "Página PDF oficial", image_id: "Imagen", showroom_id: "Sala 3D", verified_on: "Verificado el",
};

/** «Especificaciones, fila 1» for `specs/0/source`; the whole sheet for an empty path. */
export function pathLabel(path: string): string {
  if (!path) return "Ficha";
  const [head, index] = path.split("/");
  const label = FIELD_LABEL[head] ?? head;
  return /^\d+$/.test(index ?? "") ? `${label}, fila ${Number(index) + 1}` : label;
}
