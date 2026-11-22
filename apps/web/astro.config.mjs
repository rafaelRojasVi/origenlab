// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { TURNSTILE_ORIGIN, turnstileSiteKey } from './src/data/turnstile.mjs';

/**
 * Con clave de Turnstile (`src/data/turnstile.mjs`), la CSP del `.htaccess`
 * construido admite el script y el iframe del widget; sin clave queda intacta.
 * @returns {import('astro').AstroIntegration}
 */
function turnstileCsp() {
  return {
    name: 'origenlab-turnstile-csp',
    hooks: {
      'astro:build:done': ({ dir }) => {
        if (!turnstileSiteKey(process.env.PUBLIC_TURNSTILE_SITE_KEY)) return;
        const file = fileURLToPath(new URL('.htaccess', dir));
        const before = readFileSync(file, 'utf8');
        const after = before.replace(
          "script-src 'self' 'unsafe-inline';",
          `script-src 'self' 'unsafe-inline' ${TURNSTILE_ORIGIN}; frame-src ${TURNSTILE_ORIGIN};`,
        );
        if (after === before) throw new Error('turnstileCsp: no se encontró script-src en la CSP de .htaccess');
        writeFileSync(file, after);
      },
    },
  };
}

// https://astro.build/config
export default defineConfig({
  site: 'https://origenlab.cl',
  /** La canónica del sitio siempre lleva barra final; el build debe coincidir. */
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  integrations: [
    turnstileCsp(),
    sitemap({
      // Superficies internas y borradores legales: no se indexan y no entran
      // al sitemap. Las rutas legales vuelven al índice cuando `legal.ts`
      // registre la revisión profesional y dejen de servirse con noindex.
      filter: (page) =>
        !page.includes('/logo-lab/') &&
        !page.includes('/sala-lab/') &&
        !page.includes('/privacidad/') &&
        !page.includes('/cookies/') &&
        !page.includes('/aviso-legal/') &&
        !page.includes('/contacto/enviado/'),
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
    build: {
      rolldownOptions: {
        output: {
          // La sala 3D se pide bajo demanda: three.js (con las piezas comunes de
          // los modelos), la escena y cada modelo van en fragmentos `scene-*`,
          // que validate:dist mide aparte del presupuesto de página y que ningún
          // HTML puede enlazar en su carga inicial. Las páginas de producto
          // suman el mapa de scroll (`scene-scroll`), su escenario y arranque
          // (`scene-story`) y la historia de cada equipo (`scene-story-<id>`).
          // `loaders.ts` y el ayudante de precarga de Vite los importan de
          // entrada los scripts de la portada y de la historia: van en su propio
          // fragmento pequeño (`showroom-loaders`, dentro del presupuesto), no en
          // un `scene-*`, que entonces se cargaría con la página.
          codeSplitting: {
            groups: [
              {
                name(id) {
                  if (/[\\/]showroom[\\/]loaders\.ts$/.test(id) || /vite[\\/]preload-helper/.test(id)) return 'showroom-loaders';
                  if (/[\\/]node_modules[\\/]three[\\/]/.test(id) || /[\\/]showroom[\\/]models[\\/]kit\.ts$/.test(id)) return 'scene-three';
                  if (/[\\/]showroom[\\/]scene\.ts$/.test(id)) return 'scene-stage';
                  if (/[\\/]showroom[\\/]scrollStory\.ts$/.test(id)) return 'scene-scroll';
                  if (/[\\/]showroom[\\/](productStory|mountStory)\.ts$/.test(id)) return 'scene-story';
                  const story = id.match(/[\\/]showroom[\\/]stories[\\/]([\w-]+)\.ts$/);
                  if (story) return `scene-story-${story[1]}`;
                  const model = id.match(/[\\/]showroom[\\/]models[\\/]([\w-]+)\.ts$/);
                  return model ? `scene-${model[1]}` : null;
                },
              },
            ],
          },
        },
      },
    },
  },
});
