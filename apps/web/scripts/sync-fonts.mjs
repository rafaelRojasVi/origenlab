#!/usr/bin/env node
/**
 * Copia los woff2 auto-hospedados desde los paquetes de Fontsource a public/fonts/.
 *
 * El sitio no carga tipografías de Google: cada visita enviaría la IP del
 * visitante a un tercero sin aviso ni base legal, y el CSS remoto bloquea el
 * primer pintado. Ambas familias están bajo SIL Open Font License 1.1, que
 * permite el auto-hospedaje (ver docs/design/DESIGN_SYSTEM.md).
 *
 * Ejecutar tras actualizar los paquetes: npm run sync:fonts
 */
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'public', 'fonts');
mkdirSync(out, { recursive: true });

const FILES = [
  ['@fontsource-variable/plus-jakarta-sans', 'plus-jakarta-sans-latin-wght-normal.woff2'],
  ['@fontsource-variable/plus-jakarta-sans', 'plus-jakarta-sans-latin-ext-wght-normal.woff2'],
  ['@fontsource/ibm-plex-mono', 'ibm-plex-mono-latin-400-normal.woff2'],
  ['@fontsource/ibm-plex-mono', 'ibm-plex-mono-latin-500-normal.woff2'],
];

for (const [pkg, file] of FILES) {
  copyFileSync(join(root, 'node_modules', pkg, 'files', file), join(out, file));
  console.log(`fonts/${file}`);
}
