#!/usr/bin/env node
// Export the public website's equipment catalogue as the CRM's equipment-interest taxonomy.
//
//   node apps/api/scripts/export_equipment_taxonomy.mjs            # rewrite the JSON
//   node apps/api/scripts/export_equipment_taxonomy.mjs --check    # exit 1 if it drifted
//
// The website (`apps/web/src/data/*.ts`) is the single source of brands, families, models and
// verified product images. This script only reshapes it; it adds no brand, model or image the
// site does not publish. Aliases are the one hand-written part: the spellings an email or a
// quotation uses for a brand ("Loeser", "Adam") that the site spells once.
//
// Node >= 22.6 imports the .ts data files directly (type stripping); none of them imports
// anything but types.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../../..");
const data = (name) => import(resolve(repo, "apps/web/src/data", name));
const OUT = resolve(repo, "apps/api/src/origenlab_api/v2/equipment_taxonomy.json");
const SITE = "https://origenlab.cl";

const { brands, APPROVED_BRAND_IDS } = await data("brands.ts");
const { equipmentScope } = await data("equipmentScope.ts");
const { brandModels } = await data("brandModels.ts");
const { products } = await data("products.ts");
const { productImages } = await data("productImages.ts");

// Other spellings of the brand name seen in correspondence. Matched on word boundaries.
const BRAND_ALIASES = {
  hielscher: ["Hielscher"],
  ortoalresa: ["Ortoalresa", "Orto Alresa"],
  ika: ["IKA", "Ultra-Turrax", "Ultraturrax"],
  "adam-equipment": ["Adam Equipment"],  // not bare "Adam": it is also a first name
  loeser: ["Löser", "Loeser", "Loser Messtechnik"],
  serva: ["SERVA"],
};

// Family colours from apps/web/src/styles/global.css (--color-family-*), ≥ 5.6:1 on paper.
const FAMILY_COLOR = {
  sonicacion: "#1d4f7c",
  "dispersion-homogeneizacion": "#6b3f96",
  centrifugacion: "#115e59",
  "pesaje-humedad": "#7d5310",
  osmometria: "#15697f",
  electroforesis: "#8f2f4f",
};

const brandById = new Map(brands.map((b) => [b.id, b]));
const familyOfBrand = new Map(brands.map((b) => [b.id, b.familyId]));

/** An image fit for email: JPG/PNG master when there is one, else the 480px WebP derivative. */
function emailImage(record) {
  if (!record || record.status !== "VERIFIED" || !record.masterPath) return null;
  const master = record.masterPath;
  const isRaster = /\.(jpe?g|png)$/i.test(master);
  const path = isRaster ? master : `${master.replace(/\.[a-z0-9]+$/i, "")}-480.webp`;
  return {
    url: `${SITE}${path}`,
    alt: record.alt,
    scope: record.scope,
    format: path.split(".").pop().toLowerCase(),
  };
}

function modelAliases(name, code) {
  const out = new Set([name]);
  // "T 25 digital ULTRA-TURRAX" → "T 25 digital", "T25"; "Biocen 22 R" → "Biocen 22R".
  const head = name.replace(/\s+ULTRA-TURRAX$/i, "");
  out.add(head);
  const compact = head.match(/^([A-Z]+)\s+(\d+)/);
  if (compact) out.add(`${compact[1]}${compact[2]}`);
  if (/\s[A-Z]$/.test(head)) out.add(head.replace(/\s([A-Z])$/, "$1"));
  if (code) out.add(code);
  return [...out].filter((a) => a.trim().length >= 3);
}

const models = [];
for (const m of brandModels) {
  const image = emailImage(productImages.find((i) => i.modelId === m.id));
  models.push({
    id: m.id,
    brand_id: m.brandId,
    family_id: m.familyId,
    name: m.name,
    code: m.code ?? null,
    kind: m.scope === "familia" ? "serie" : "modelo",
    page_url: `${SITE}/marcas/${brandById.get(m.brandId).slug}/`,
    image,
    aliases: modelAliases(m.name, m.code),
  });
}
for (const p of products) {
  const image = emailImage(productImages.find((i) => i.productId === p.id));
  models.push({
    id: p.id,
    brand_id: p.brandId,
    family_id: familyOfBrand.get(p.brandId),
    name: p.name,
    code: p.sku ?? null,
    kind: "modelo",
    page_url: `${SITE}/productos/${p.productFamilySlug}/${p.slug}/`,
    image,
    aliases: modelAliases(p.name, p.sku),
  });
}
models.sort((a, b) => a.brand_id.localeCompare(b.brand_id) || a.name.localeCompare(b.name));

const taxonomy = {
  source:
    "apps/web/src/data/{brands,equipmentScope,brandModels,products,productImages}.ts — regenerate with apps/api/scripts/export_equipment_taxonomy.mjs",
  site_base_url: SITE,
  families: equipmentScope
    .filter((f) => brands.some((b) => b.familyId === f.id))
    .map((f) => ({ id: f.id, name: f.name, color: FAMILY_COLOR[f.id] ?? null })),
  brands: APPROVED_BRAND_IDS.map((id) => {
    const b = brandById.get(id);
    return {
      id,
      name: b.name,
      family_id: b.familyId,
      page_url: `${SITE}/marcas/${b.slug}/`,
      aliases: BRAND_ALIASES[id] ?? [b.name],
    };
  }),
  models,
};

const text = `${JSON.stringify(taxonomy, null, 2)}\n`;
if (process.argv.includes("--check")) {
  const current = readFileSync(OUT, "utf8");
  if (current !== text) {
    console.error(`${OUT} is out of date with apps/web/src/data; rerun without --check`);
    process.exit(1);
  }
  console.log("equipment taxonomy matches the website catalogue");
} else {
  writeFileSync(OUT, text);
  console.log(`wrote ${models.length} models, ${taxonomy.brands.length} brands to ${OUT}`);
}
