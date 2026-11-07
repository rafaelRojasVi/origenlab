/**
 * Colecciones de contenido de origenlab.cl (Astro 7: Content Layer API, así
 * que este archivo vive en `src/content.config.ts`, no en `src/content/`).
 *
 * - `modelos`: un JSON por equipo en `src/content/modelos/`. La forma está
 *   también en `src/content/modelos.schema.json`, que es la que lee el
 *   formulario del tablero; los dos se mantienen iguales y
 *   `npm run validate:content` comprueba los archivos contra el JSON Schema.
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

const slug = z.string().regex(/^[a-z0-9-]+$/);

const modelos = defineCollection({
  loader: glob({ pattern: '*.json', base: './src/content/modelos' }),
  schema: z
    .object({
      slug,
      family: z.enum([
        'sonicacion',
        'dispersion',
        'pesaje-humedad',
        'osmometria',
        'electroforesis',
        'centrifugacion',
      ]),
      brand: z.string().refine((id) => (APPROVED_BRAND_IDS as readonly string[]).includes(id), {
        message: 'La marca no está en APPROVED_BRAND_IDS',
      }),
      name: z.string().max(80),
      headline: z.string().max(120),
      lead: z.string().max(300),
      applications: z.array(z.string().max(80)),
      specs: z.array(
        z.strictObject({ label: z.string(), value: z.string(), source: z.url() }),
      ),
      accessories: z.array(z.string().max(120)),
      related: z.array(slug),
      faqs: z.array(z.strictObject({ q: z.string(), a: z.string() })),
      official_url: z.url(),
      official_pdf_url: z.url().optional(),
      image_id: z.string().optional(),
      showroom_id: z.string().optional(),
      verified_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    })
    .strict(),
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
