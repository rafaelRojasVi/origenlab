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
          // HTML puede enlazar en su carga inicial.
          codeSplitting: {
            groups: [
              {
                name(id) {
                  if (/[\\/]node_modules[\\/]three[\\/]/.test(id) || /[\\/]showroom[\\/]models[\\/]kit\.ts$/.test(id)) return 'scene-three';
                  if (/[\\/]showroom[\\/]scene\.ts$/.test(id)) return 'scene-stage';
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
