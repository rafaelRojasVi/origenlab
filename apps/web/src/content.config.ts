/**
 * Colecciones de contenido de origenlab.cl (Astro 7: Content Layer API, así
 * que este archivo vive en `src/content.config.ts`, no en `src/content/`).
 *
 * - `modelos`: un JSON por equipo en `src/content/modelos/`. Su forma es
 *   `src/content/modelos.schema.json` (la que lee también el formulario del
 *   tablero); aquí se traduce a zod, y `npm run validate:content` comprueba
 *   los archivos contra ese mismo esquema.
 * - `aplicaciones`: una página de Markdown por tarea de laboratorio.
 *
 * Nada de precio, stock, plazo ni garantía: `.strict()` rechaza cualquier
 * clave que el esquema no declare.
 */
import { readdirSync } from 'node:fs';
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';
import { APPROVED_BRAND_IDS } from './data/brands';
import schema from './content/modelos.schema.json';

/**
 * La forma del modelo se lee de `modelos.schema.json` y no se vuelve a
 * escribir aquí: el esquema es la única fuente de verdad (el tablero lo lee
 * para su formulario y `validate:content` lo aplica a cada archivo), así que
 * zod y el esquema no pueden desviarse. Traduce sólo el subconjunto que el
 * esquema usa; una palabra que no conozca falla el build en lugar de ignorarse.
 */
type Node = {
  type: 'string' | 'array' | 'object';
  enum?: string[];
  maxLength?: number;
  pattern?: string;
  items?: Node;
  properties?: Record<string, Node>;
  required?: string[];
  additionalProperties?: boolean;
};

function toZod(node: Node): z.ZodType {
  if (node.type === 'string') {
    if (node.enum) return z.enum(node.enum as [string, ...string[]]);
    let str = z.string();
    if (node.maxLength !== undefined) str = str.max(node.maxLength);
    if (node.pattern) str = str.regex(new RegExp(node.pattern));
    return str;
  }
  if (node.type === 'array') return z.array(toZod(node.items!));
  if (node.type === 'object') {
    const shape: Record<string, z.ZodType> = {};
    for (const [key, child] of Object.entries(node.properties ?? {})) {
      shape[key] = node.required?.includes(key) ? toZod(child) : toZod(child).optional();
    }
    return node.additionalProperties === false ? z.strictObject(shape) : z.object(shape);
  }
  throw new Error(`modelos.schema.json: tipo no soportado ${(node as Node).type}`);
}

/** Lo que las páginas leen de `getCollection('modelos')`; sigue a `modelos.schema.json`. */
export interface Modelo {
  slug: string;
  family: 'sonicacion' | 'dispersion' | 'pesaje-humedad' | 'osmometria' | 'electroforesis' | 'centrifugacion';
  brand: string;
  name: string;
  headline: string;
  lead: string;
  applications: string[];
  specs: { label: string; value: string; source: string }[];
  accessories: string[];
  related: string[];
  faqs: { q: string; a: string }[];
  official_url: string;
  official_pdf_url?: string;
  image_id?: string;
  showroom_id?: string;
  verified_on: string;
}

const modeloSchema = (toZod(schema as Node) as z.ZodType<Modelo>).superRefine((model, ctx) => {
  if (!(APPROVED_BRAND_IDS as readonly string[]).includes(model.brand)) {
    ctx.addIssue({ code: 'custom', path: ['brand'], message: 'La marca no está en APPROVED_BRAND_IDS' });
  }
});

const modelos = defineCollection({
  loader: glob({ pattern: '*.json', base: './src/content/modelos' }),
  schema: modeloSchema,
});

/**
 * Mientras no haya ninguna página, el cargador `glob` avisa en cada build de
 * que no encontró archivos; con un cargador vacío la colección existe y el
 * build sigue limpio. Deja de hacer falta con la primera página `.md`.
 */
const hasAplicaciones = readdirSync('./src/content/aplicaciones').some((file) => file.endsWith('.md'));

const aplicaciones = defineCollection({
  loader: hasAplicaciones ? glob({ pattern: '*.md', base: './src/content/aplicaciones' }) : () => [],
  schema: z.object({ title: z.string() }),
});

export const collections = { modelos, aplicaciones };
