/**
 * What a case card shows, read from data the card already carries — never stored, never sent
 * back: a readable institution name when the CRM only knows a domain or a slug, the equipment
 * model printed in the quote's file name, what the quote is for, the days since the quote went out and where the
 * conversation stands («respondió», «seguimiento», «sin respuesta»).
 */
import type { OpportunityCardData } from "./crmTypes";
import type { Tone } from "./ui";

/** «CN01238- Juan Germany G - AGAR DEL PACIFICO S.A-MB120.pdf» → company and model. */
export function parseQuoteFilename(filename: string | null | undefined): { company: string | null; model: string | null } {
  if (!filename) return { company: null, model: null };
  let s = filename.replace(/\.pdf$/i, "").trim();
  let model: string | null = null;
  const m = s.match(/[-–]\s*([A-Z][A-Za-z]{0,3}\d{2,5}[A-Za-z]{0,4})$/);
  if (m && m.index !== undefined) {
    model = m[1];
    s = s.slice(0, m.index).trim();
  }
  s = s.replace(/^(?:CN|Cotizaci[oó]n)\s*\d+[A-Z]*\s*[-–]?\s*/i, "");
  const parts = s
    .split(/\s+[-–]\s+|\s*–\s*|\s+-\s*/)
    .map((p) => p.trim())
    .filter(Boolean);
  return { company: parts.length > 1 ? parts[parts.length - 1] : null, model };
}

/** A subject that names a campaign or a process, not what the client asked for. */
const NOT_A_PRODUCT =
  /\||campaña|cyber|origenlab|labdelivery|presentaci[oó]n|inscripci[oó]n|proveedor|documentos|factura|orden de compra|^oc\b|^consulta$|^informaci[oó]n$|^quote$|^cotizar$|equipos?\s*(?:\/|e)?\s*(?:insumos\s+)?para\s+(?:su|el|el\s+su)?\s*laboratorio|suministros?\s+de\s+equipos|especial\b|newsletter/i;

/**
 * What the quote is for, as a short label: «Balanzas Ohaus», «Pipetas pasteur», «UP400St».
 * The quote email's subject names the product more often than the PDF does («Cotización
 * Balanzas Ohaus»); the model the file name ends with is added when the subject leaves it out.
 * A subject that is a campaign, a reply chain or a supplier form is not read as a product.
 */
export function quoteProduct(card: OpportunityCardData): string | null {
  const latest = card.latest_revision;
  const model = parseQuoteFilename(latest?.document?.filename ?? latest?.drive?.original_filename).model;
  let subject = latest?.gmail?.subject?.trim() ?? "";
  for (let i = 0; i < 4; i += 1) subject = subject.replace(/^\s*(?:re|rv|fw|fwd|reenviar|respuesta)\s*:\s*/i, "");
  subject = subject.replace(/^\s*urgente\s*[:!-]?\s*/i, "").replace(/\s*[-–]\s*(?:origenlab|labdelivery).*$/i, "");
  // «Corteva Solicitud de cotización de insumos» → «insumos»: what follows the word is the product.
  const after = subject.match(/cotizaci[oó]n(?:es)?\s*(?:(?:de|por|para|del)\s+)?[:-]?\s*(.*)$/i);
  if (after) subject = after[1];
  subject = subject
    .replace(/^[\s—–\-·:|]+/, "")
    .replace(/^\s*(?:solicitud|contacto|consulta)(?:\s+(?:de|por))?\s+/i, "")
    .trim();
  const fromSubject = subject.length >= 3 && !NOT_A_PRODUCT.test(subject) && /[a-záéíóúñ]/i.test(subject) ? subject : null;
  const label = fromSubject ? fromSubject.charAt(0).toUpperCase() + fromSubject.slice(1) : null;
  if (label && model && !label.toLowerCase().includes(model.toLowerCase())) return `${label} · ${model}`;
  return label ?? model;
}

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];

const DOMAIN = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/i;

/**
 * The name to lead a card with. The CRM's own name wins when it reads like one; an institution
 * the import named after a domain («exactachile.cl») or a slug («Labclinicoarauco») is shown by
 * the name the quote's title or file prints, with the CRM name kept as `sub`.
 */
export function displayName(card: OpportunityCardData): { name: string; sub: string | null } {
  const org = card.organization?.name?.trim() || null;
  const fromTitle = card.title.match(/^Cotizaci[oó]n .*? — (.+)$/)?.[1]?.trim() ?? null;
  const fromFile = parseQuoteFilename(card.latest_revision?.document?.filename).company;
  const better = fromTitle ?? fromFile;
  if (!org) return {
    name: better ?? (card.last_contact?.inbound?.subject?.trim() || card.title.trim() || "Sin institución"),
    sub: card.last_contact?.inbound?.sender_name || "Institución por confirmar",
  };
  const looksLikeDomain = DOMAIN.test(org);
  const looksLikeSlug = !/\s/.test(org) && org.length > 6 && better != null && /\s/.test(better);
  if ((looksLikeDomain || looksLikeSlug) && better && better.toLowerCase() !== org.toLowerCase()) return { name: better, sub: org };
  if (looksLikeDomain) {
    const word = org.split(".")[0];
    return { name: word.charAt(0).toUpperCase() + word.slice(1), sub: org };
  }
  return { name: org, sub: null };
}

/** The contact to show: a CRM person's name, else the recipient's name or address. */
export function contactLine(card: OpportunityCardData): string | null {
  const c = card.contact;
  if (!c) return null;
  const who = c.name?.trim() || c.address || null;
  if (!who) return null;
  return c.others > 0 ? `${who} +${c.others}` : who;
}

export function daysSince(iso: string | null | undefined, now: Date = new Date()): number | null {
  const t = iso ? Date.parse(iso) : Number.NaN;
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((now.getTime() - t) / 86_400_000));
}

/** The follow-up rhythm (day 3, 14, 30) as a tone for the «N d» badge. */
export function ageTone(days: number | null): Tone {
  if (days == null) return "neutral";
  if (days <= 3) return "brand";
  if (days <= 14) return "info";
  if (days <= 30) return "warn";
  return "bad";
}

/** Where the email conversation stands since the latest quote went out. */
export function conversation(
  card: OpportunityCardData,
  now: Date = new Date(),
): { kind: "replied" | "followed_up" | "silent" | "none"; text: string; at: string | null; url: string | null } {
  const sent = card.latest_revision?.sent_at ?? null;
  const inbound = card.last_contact?.inbound ?? null;
  const outbound = card.last_contact?.outbound ?? null;
  const after = (at: string | null | undefined) => !!at && (!sent || Date.parse(at) > Date.parse(sent) + 60_000);
  const short = (iso: string) => {
    const d = new Date(iso);
    return `${String(d.getDate()).padStart(2, "0")} ${MONTHS[d.getMonth()]}`;
  };
  if (inbound && after(inbound.at) && (!outbound || Date.parse(inbound.at) >= Date.parse(outbound.at))) {
    return { kind: "replied", text: `Respondió ${short(inbound.at)} · te toca`, at: inbound.at, url: inbound.url };
  }
  if (outbound && after(outbound.at)) {
    return { kind: "followed_up", text: `Seguimiento ${short(outbound.at)}`, at: outbound.at, url: outbound.url };
  }
  if (!sent) return { kind: "none", text: "Sin cotización", at: null, url: null };
  const d = daysSince(sent, now) ?? 0;
  return { kind: "silent", text: d === 0 ? "Enviada hoy" : `Sin respuesta · ${d} d`, at: null, url: null };
}
