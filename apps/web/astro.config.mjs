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
        !page.includes('/privacidad/') &&
        !page.includes('/aviso-legal/'),
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
  },
});
