/**
 * OrigenLab campaign templates, built from the website catalogue.
 *
 * Every product name, brand, link and image comes from the taxonomy the API exports from
 * `apps/web/src/data` — only images the site marks VERIFIED, served from origenlab.cl. The copy
 * states nothing about a product beyond its catalogue name and family: no specification, price,
 * stock or delivery claim, which an operator adds (and owns) when editing.
 *
 * Email-client HTML: tables, inline styles, a 600 px column that stacks below 480 px, and web
 * fonts only as a first preference over system fallbacks.
 */

import type { EquipmentTaxonomy, TaxonomyBrand, TaxonomyModel } from "./marketingTypes";

export const EMAIL_LOGO_URL = "https://origenlab.cl/email/origenlab-signature-lockup.png";

const INK = "#1d2022";
const MUTED = "#54595d";
const PAPER = "#fafaf7";
const HAIRLINE = "#e3e3de";
const TEAL = "#0f766e";
const FONT = "'Plus Jakarta Sans', 'Segoe UI', Arial, Helvetica, sans-serif";

export type TemplateId = "producto" | "familia" | "marcas" | "blanco";

export interface TemplateDescriptor {
  id: TemplateId;
  label: string;
  description: string;
}

export const TEMPLATES: TemplateDescriptor[] = [
  { id: "producto", label: "Producto destacado", description: "Un modelo con imagen, familia y enlace a su ficha." },
  { id: "familia", label: "Línea de una marca", description: "Hasta cuatro modelos de una misma marca en cuadrícula." },
  { id: "marcas", label: "Resumen por marcas", description: "Un modelo por marca, para una comunicación general." },
  { id: "blanco", label: "En blanco", description: "Encabezado y pie de OrigenLab, cuerpo libre." },
];

export interface TemplateInput {
  template: TemplateId;
  brandId?: string;
  modelId?: string;
}

function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function familyOf(t: EquipmentTaxonomy, familyId: string) {
  return t.families.find((f) => f.id === familyId);
}

function accentOf(t: EquipmentTaxonomy, familyId: string): string {
  return familyOf(t, familyId)?.color ?? TEAL;
}

function brandOf(t: EquipmentTaxonomy, brandId: string): TaxonomyBrand | undefined {
  return t.brands.find((b) => b.id === brandId);
}

function button(href: string, label: string, color: string): string {
  return (
    `<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>` +
    `<td bgcolor="${color}" style="border-radius:2px;">` +
    `<a href="${esc(href)}" style="display:inline-block;padding:10px 18px;font-family:${FONT};font-size:14px;` +
    `font-weight:600;color:#ffffff;text-decoration:none;">${esc(label)}</a></td></tr></table>`
  );
}

function image(model: TaxonomyModel, width: number): string {
  if (!model.image) return "";
  return (
    `<img src="${esc(model.image.url)}" alt="${esc(model.image.alt)}" width="${width}" ` +
    `style="display:block;width:${width}px;max-width:100%;height:auto;border:0;margin:0 auto;">`
  );
}

function modelCard(t: EquipmentTaxonomy, model: TaxonomyModel, width: number): string {
  const brand = brandOf(t, model.brand_id);
  const accent = accentOf(t, model.family_id);
  return (
    `<td class="col" width="${width}" valign="top" style="padding:8px;">` +
    `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" ` +
    `style="border:1px solid ${HAIRLINE};background:#ffffff;"><tr><td style="padding:16px;text-align:center;">` +
    `<div style="height:150px;line-height:150px;">${image(model, 120)}</div>` +
    `<p style="margin:12px 0 2px;font-family:${FONT};font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:${accent};">` +
    `${esc(brand?.name ?? "")}</p>` +
    `<p style="margin:0 0 10px;font-family:${FONT};font-size:15px;font-weight:600;color:${INK};">${esc(model.name)}</p>` +
    `<a href="${esc(model.page_url)}" style="font-family:${FONT};font-size:13px;color:${accent};">Ver en origenlab.cl</a>` +
    `</td></tr></table></td>`
  );
}

function shell(inner: string, accent: string): string {
  return `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OrigenLab</title>
<style>
  @media (max-width: 480px) {
    .container { width: 100% !important; }
    .col { display: block !important; width: 100% !important; }
    .pad { padding-left: 20px !important; padding-right: 20px !important; }
  }
</style>
</head>
<body style="margin:0;padding:0;background:${PAPER};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:${PAPER};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;background:#ffffff;border:1px solid ${HAIRLINE};">
<tr><td style="height:4px;background:${accent};font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td class="pad" style="padding:24px 32px 8px;">
<img src="${EMAIL_LOGO_URL}" alt="OrigenLab" width="150" style="display:block;width:150px;height:auto;border:0;">
</td></tr>
${inner}
<tr><td class="pad" style="padding:24px 32px;border-top:1px solid ${HAIRLINE};">
<p style="margin:0 0 6px;font-family:${FONT};font-size:12px;line-height:18px;color:${MUTED};">
OrigenLab · Equipamiento de laboratorio · <a href="https://origenlab.cl/" style="color:${TEAL};">origenlab.cl</a></p>
<p style="margin:0;font-family:${FONT};font-size:12px;line-height:18px;color:${MUTED};">
Si no desea recibir más correos de OrigenLab, responda a este mensaje con la palabra «BAJA».</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
`;
}

function intro(title: string, body: string): string {
  return (
    `<tr><td class="pad" style="padding:16px 32px 8px;">` +
    `<h1 style="margin:0 0 12px;font-family:${FONT};font-size:22px;line-height:28px;font-weight:700;color:${INK};">${esc(title)}</h1>` +
    `<p style="margin:0;font-family:${FONT};font-size:15px;line-height:23px;color:${MUTED};">${esc(body)}</p>` +
    `</td></tr>`
  );
}

/** Full HTML for a template, or throws when the chosen brand/model does not exist. */
export function renderTemplate(t: EquipmentTaxonomy, input: TemplateInput): string {
  if (input.template === "blanco") {
    return shell(
      intro("Título de la comunicación", "Escriba aquí el mensaje.") +
        `<tr><td class="pad" style="padding:8px 32px 24px;">${button("https://origenlab.cl/", "Visitar origenlab.cl", TEAL)}</td></tr>`,
      TEAL,
    );
  }

  if (input.template === "producto") {
    const model = t.models.find((m) => m.id === input.modelId) ?? t.models.find((m) => m.brand_id === input.brandId) ?? t.models[0];
    if (!model) throw new Error("el catálogo no tiene modelos");
    const brand = brandOf(t, model.brand_id);
    const family = familyOf(t, model.family_id);
    const accent = accentOf(t, model.family_id);
    return shell(
      intro(`${model.name} de ${brand?.name ?? ""}`, `Le presentamos el ${model.name}, de la línea de ${family?.name.toLowerCase() ?? "equipos"} que OrigenLab ofrece en Chile.`) +
        `<tr><td class="pad" align="center" style="padding:16px 32px;">${image(model, 260)}</td></tr>` +
        `<tr><td class="pad" style="padding:8px 32px 8px;">` +
        `<p style="margin:0 0 16px;font-family:${FONT};font-size:15px;line-height:23px;color:${MUTED};">` +
        `Escriba aquí por qué este equipo puede interesarle al destinatario.</p>` +
        button(model.page_url, "Ver ficha del equipo", accent) +
        `</td></tr><tr><td style="height:16px;font-size:0;line-height:0;">&nbsp;</td></tr>`,
      accent,
    );
  }

  if (input.template === "familia") {
    const brand = brandOf(t, input.brandId ?? "") ?? t.brands[0];
    if (!brand) throw new Error("el catálogo no tiene marcas");
    const models = t.models.filter((m) => m.brand_id === brand.id).slice(0, 4);
    const accent = accentOf(t, brand.family_id);
    const rows: string[] = [];
    for (let i = 0; i < models.length; i += 2) {
      rows.push(`<tr>${models.slice(i, i + 2).map((m) => modelCard(t, m, 268)).join("")}</tr>`);
    }
    return shell(
      intro(`Equipos ${brand.name}`, `Una selección de la línea ${brand.name} disponible a través de OrigenLab.`) +
        `<tr><td class="pad" style="padding:8px 24px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">${rows.join("")}</table></td></tr>` +
        `<tr><td class="pad" style="padding:8px 32px 24px;">${button(brand.page_url, `Ver ${brand.name} en origenlab.cl`, accent)}</td></tr>`,
      accent,
    );
  }

  // marcas: one model per brand, in taxonomy order
  const picks = t.brands
    .map((b) => t.models.find((m) => m.brand_id === b.id && m.image))
    .filter((m): m is TaxonomyModel => Boolean(m));
  const rows: string[] = [];
  for (let i = 0; i < picks.length; i += 2) {
    rows.push(`<tr>${picks.slice(i, i + 2).map((m) => modelCard(t, m, 268)).join("")}</tr>`);
  }
  return shell(
    intro("Marcas que representamos", "Equipos de laboratorio de fabricantes con los que trabaja OrigenLab.") +
      `<tr><td class="pad" style="padding:8px 24px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">${rows.join("")}</table></td></tr>` +
      `<tr><td class="pad" style="padding:8px 32px 24px;">${button("https://origenlab.cl/", "Ver catálogo completo", TEAL)}</td></tr>`,
    TEAL,
  );
}

/** Every image URL a rendered template references (for tests and the preview notice). */
export function imageUrlsIn(html: string): string[] {
  return [...html.matchAll(/<img[^>]+src="([^"]+)"/g)].map((m) => m[1]);
}
