#!/usr/bin/env node
/** Clasificación del origen de la visita (`src/lib/visit.ts`). Ejecutar: npm run test:visit */
import assert from 'node:assert/strict';
import { join, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const { classifyOrigin } = await import(pathToFileURL(join(root, 'src/lib/visit.ts')).href);
const host = 'origenlab.cl';
const cases = [
  ['?utm_source=gbp', '', 'perfil-google'],
  ['?utm_source=google&utm_medium=gbp', 'https://www.google.cl/', 'perfil-google'],
  ['', 'https://www.google.cl/', 'google'],
  ['', 'https://google.com/search?q=x', 'google'],
  ['', 'https://www.linkedin.com/feed/', 'linkedin'],
  ['', 'https://notlinkedin.com/', 'otro'],
  ['', 'https://linkedin.com.evil.example/', 'otro'],
  ['', 'https://wa.me/56900000000', 'whatsapp'],
  ['', 'https://api.whatsapp.com/', 'whatsapp'],
  ['?utm_source=whatsapp', '', 'whatsapp'],
  ['', '', 'directo'],
  ['', 'https://origenlab.cl/productos/', 'interno'],
  ['', 'https://www.origenlab.cl/', 'interno'],
  ['', 'http://localhost:4321/', 'otro'],
  ['', 'android-app://com.google.android.googlequicksearchbox', 'google'],
  ['', 'android-app://com.google.android.googlequicksearchbox/https/www.google.com', 'google'],
  ['', 'android-app://com.linkedin.android', 'linkedin'],
  ['', 'android-app://com.whatsapp/', 'whatsapp'],
  ['', 'android-app://com.example.app', 'otro'],
  ['?utm_source=gbp', 'https://origenlab.cl/', 'perfil-google'],
  ['', 'https://example.org/', 'otro'],
  ['', 'no es una url', 'directo'],
];
for (const [search, referrer, want] of cases) {
  assert.equal(classifyOrigin(search, referrer, host), want, `${JSON.stringify([search, referrer])} -> ${want}`);
}
console.log(`  ok  classifyOrigin (${cases.length} casos)`);
