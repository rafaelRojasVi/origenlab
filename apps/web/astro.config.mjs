// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

/**
 * Vista previa del boletín.
 *
 * `ORIGENLAB_NEWSLETTER_PREVIEW=1` dibuja el formulario y escribe el resultado
 * en `dist-preview/`, nunca en `dist/`. La bandera se inyecta como constante de
 * compilación para que el código que la lee sea tipado y para que una
 * compilación normal la tenga en `false` sin depender de ninguna variable.
 */
const newsletterPreview = process.env.ORIGENLAB_NEWSLETTER_PREVIEW === '1';

// https://astro.build/config
export default defineConfig({
  site: 'https://origenlab.cl',
  outDir: newsletterPreview ? './dist-preview' : './dist',
  /** La canónica del sitio siempre lleva barra final; el build debe coincidir. */
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  integrations: [
    sitemap({
      // Superficies internas, borradores legales y boletín: no se indexan y no
      // entran al sitemap. Las rutas legales vuelven al índice cuando
      // `legal.ts` registre la revisión profesional; las del boletín, cuando
      // `newsletter.ts` tenga sus ocho puertas abiertas. Las dos listas se
      // repiten en public/robots.txt, en public/.htaccess y en
      // scripts/validate-dist.mjs, que comprueba que las cuatro coincidan.
      filter: (page) =>
        !page.includes('/logo-lab/') &&
        !page.includes('/privacidad/') &&
        !page.includes('/cookies/') &&
        !page.includes('/aviso-legal/') &&
        !page.includes('/newsletter/'),
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
    define: {
      __ORIGENLAB_NEWSLETTER_PREVIEW__: JSON.stringify(newsletterPreview),
    },
  },
});
