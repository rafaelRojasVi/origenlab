// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  site: 'https://origenlab.cl',
  /** La canónica del sitio siempre lleva barra final; el build debe coincidir. */
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  integrations: [
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
